"""Run one action file directly under the interpreter (macOS and Windows).

Neither Keyhac nor the MCP server is involved: this imports the file, finds the
`ThreadedAction` in it, and drives it to completion.

    python tools/run_action_file.py examples/actions/mac/extract_records.py
    python tools/run_action_file.py examples/actions/mac/jump_to_error.py dry_run=true
    python tools/run_action_file.py examples/actions/win/snapshot_settings.py output_path=s.json

Arguments are `key=value` and become **constructor** keyword arguments, because
that is where a real action takes them - `keymap.register_action("snapshot",
SnapshotSettings(output_path=...))` in `configure()` is the call site this is
standing in for.  `true`/`false` and integers are converted; everything else
stays a string.  If a file defines more than one action, pick with `class=Name`.

The thread architecture matters to what is being demonstrated, so this is not a
shortcut around it: the event loop turns on the main thread, the action's run()
executes on a worker, and every UI read is dispatched back - exactly the
arrangement `keyhac.core.wait` is written against, and the one that makes
waiting legal from a worker at all.

## Why this is a tool and not a shipped entry point

It would be a small step from here to `python -m keyhac.run_action <file>`, and
it is the wrong one.  macOS grants the Accessibility permission per binary, and
`doc/dev/packaging.md` is explicit that a generic interpreter "would grant the
permission to that interpreter, not to Keyhac" - which is why Keyhac.app has a
real bundle executable in the first place.  So this path cannot use the grant
the user already gave Keyhac.  What it uses instead is the grant held by the
process responsible for the shell: Terminal, or the IDE, or whatever the agent
happens to live in.  That is a far wider authorisation than Keyhac's own, since
it covers every process that shell will ever spawn, and for an application whose
standing trust problem is proving it is not a keylogger it is the wrong
direction to push a user in.

It also walks around `doc/dev/ai-integration.md` §4.4 rather than through it.
The MCP endpoint is off unless the config asks, bound to loopback, gated by a
per-start token, and deliberately offers no tool that writes Python to disk.
A runner that any shell can invoke has none of those: write a file, spawn an
interpreter, read the UI and inject keystrokes.  The tap installed below is the
strongest of the capabilities being routed around, not the weakest.

The asymmetry that makes it fine *here* is who pays.  Running the repository's
examples is something a Keyhac developer does, and they have already granted
their terminal that access deliberately - `tests/test_mac_window.py` skips
without it.  An operator has not, and an agent should be reaching the daemon
over MCP, where the permission already lives (§4.3).
"""

import importlib.util
import pathlib
import sys
import tempfile
import threading
import traceback

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def _platform_parts():
    """(name, EventLoop, InputHook, Clipboard) for this OS, or None."""
    if sys.platform == "darwin":
        from keyhac.platform.mac.clipboard import MacClipboardProvider
        from keyhac.platform.mac.hook import MacInputHook
        from keyhac.platform.mac.loop import MacEventLoop
        return "mac", MacEventLoop, MacInputHook, MacClipboardProvider
    if sys.platform == "win32":
        from keyhac.platform.win.clipboard import WinClipboardProvider
        from keyhac.platform.win.hook import WinInputHook
        from keyhac.platform.win.loop import WinEventLoop
        return "windows", WinEventLoop, WinInputHook, WinClipboardProvider
    return None


def window_provider():
    """The platform WindowProvider, which main() wires and a bare Keymap lacks."""
    if sys.platform == "darwin":
        from keyhac.platform.mac.window import MacWindowProvider
        return MacWindowProvider()
    from keyhac.platform.win.window import WinWindowProvider
    return WinWindowProvider()


def load_action(path: pathlib.Path, kwargs: dict):
    """Import `path` and construct the ThreadedAction it defines."""
    from keyhac.core.action import ThreadedAction

    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so a module that imports itself by name - or that
    # a traceback has to render - finds it where Python expects.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    defined = [value for value in vars(module).values()
               if isinstance(value, type) and issubclass(value, ThreadedAction)
               and value.__module__ == spec.name]
    wanted = kwargs.pop("class", None)
    if wanted:
        defined = [cls for cls in defined if cls.__name__ == wanted]
    if not defined:
        raise SystemExit(f"{path}: no ThreadedAction subclass"
                         + (f" named {wanted}" if wanted else ""))
    if len(defined) > 1:
        names = ", ".join(cls.__name__ for cls in defined)
        raise SystemExit(f"{path} defines {names}; pick one with class=Name")
    return defined[0](**kwargs)


def parse_value(text: str):
    """`key=value` carries no types, so recover the two that actions take."""
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        return text


def run_action(action) -> int:
    """Drive one ThreadedAction to completion, then stop the loop."""
    parts = _platform_parts()
    if parts is None:
        print(f"These examples run on macOS and Windows; this is {sys.platform}.")
        return 1
    platform, EventLoop, InputHook, Clipboard = parts

    from keyhac.core.clipboard_history import ClipboardHistory
    from keyhac.core.keymap import Keymap
    from keyhac.core.vk import init_key_names
    from keyhac.platform.fake import FakeFocusProvider

    with tempfile.TemporaryDirectory() as scratch:
        config = pathlib.Path(scratch) / "config.py"
        config.write_text("def configure(keymap):\n    pass\n")

        init_key_names(platform, "ansi")
        loop = EventLoop()
        hook = InputHook()
        keymap = Keymap(hook, FakeFocusProvider(), platform,
                        config_path=str(config), template_path=str(config))
        # configure() is what fills in the modifier map, and without it
        # send_key() emits the key without its modifiers - "Cmd-V" arrives as a
        # bare "v".  Silent, and it cost an afternoon of blaming the OS.
        keymap.configure()
        keymap.set_main_thread_dispatcher(loop.call_on_main_thread)
        # main() wires this; a bare Keymap has none, and without it keymap.ui's
        # window lookups return None - which reads exactly like "the application
        # has no window" rather than "this harness is incomplete".
        keymap.window_provider = window_provider()

        clipboard = Clipboard()
        history = ClipboardHistory(clipboard, str(config.with_name("clip.json")))
        history.persist = False
        keymap._clipboard_history = history

        status = {"code": 0}

        def complete(result):
            # finished() belongs on the loop thread and under the engine lock,
            # because that is where ThreadedAction._done_callback puts it and
            # it is the half of the lifecycle allowed to touch UI.  The harness
            # this replaced called it straight from the worker, so
            # handle_queue's closing window read ran on the wrong thread and
            # passed anyway - a harness that is lenient where the application
            # is not teaches an action to be wrong.
            try:
                with keymap._lock:
                    action.finished(result)
            except Exception:
                traceback.print_exc()
                status["code"] = 1
            finally:
                loop.stop()

        def worker():
            try:
                result = action.run()
            except Exception:
                traceback.print_exc()
                status["code"] = 1
                loop.stop()
                return
            loop.call_on_main_thread(lambda: complete(result))

        # Typing needs the hook: send() posts plain key events and the tap's
        # callback is what puts the modifier flags on them.  It is uninstalled
        # in the finally below, and dies with the process in any case, but a
        # run that types really is holding a keyboard tap while it lasts.
        hook.install(keymap.on_key_event, keymap.on_hook_restored)
        try:
            with keymap._lock:
                action.starting()
            threading.Thread(target=worker, daemon=True).start()
            loop.run()
        finally:
            hook.uninstall()
        return status["code"]


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.split("## Why")[0].strip())
        return 0 if argv else 2

    path = pathlib.Path(argv[0]).resolve()
    if not path.is_file():
        raise SystemExit(f"no such file: {argv[0]}")

    kwargs = {}
    for argument in argv[1:]:
        if "=" not in argument:
            raise SystemExit(f"arguments are key=value; got {argument!r}")
        key, _, value = argument.partition("=")
        kwargs[key] = parse_value(value)

    return run_action(load_action(path, kwargs))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
