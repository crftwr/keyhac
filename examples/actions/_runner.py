"""Run one of these example actions without the whole application.

The thread architecture matters to what is being demonstrated, so this is not
a shortcut around it: the event loop turns on the main thread, the action's
run() executes on a worker, and every UI read is dispatched back - exactly the
arrangement `keyhac.core.wait` is written against, and the one that makes
waiting legal from a worker at all.

    python examples/actions/mac/extract_records.py

Both platforms are wired here, because the *framework* is portable and an
action is not. Each example targets one OS and says which in its docstring:
five under `mac/`, and `win/snapshot_settings.py` the counterpart of
`mac/snapshot_settings.py`. Nothing is gained by making a generated action carry
selectors for a tree it will never meet - role names are not a shared
vocabulary (see the Windows entries in the authoring skill's
`references/quirks.md`).
"""

import pathlib
import sys
import threading
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))


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

    config = pathlib.Path(__file__).with_name("_runner_config.py")
    config.write_text("def configure(keymap):\n    pass\n")

    init_key_names(platform, "ansi")
    loop = EventLoop()
    hook = InputHook()
    keymap = Keymap(hook, FakeFocusProvider(), platform,
                    config_path=str(config), template_path=str(config))
    # configure() is what fills in the modifier map, and without it send_key()
    # emits the key without its modifiers - "Cmd-V" arrives as a bare "v".
    # Silent, and it cost an afternoon of blaming the OS.
    keymap.configure()
    keymap.set_main_thread_dispatcher(loop.call_on_main_thread)
    # main() wires this; a bare Keymap has none, and without it keymap.ui's
    # window lookups return None - which reads exactly like "the application
    # has no window" rather than "this harness is incomplete".
    keymap.window_provider = window_provider()

    clipboard = Clipboard()
    history = ClipboardHistory(clipboard, str(config.with_name("_runner_clip.json")))
    history.persist = False
    keymap._clipboard_history = history

    status = {"code": 0}

    def worker():
        try:
            result = action.run()
            action.finished(result)
        except Exception:
            traceback.print_exc()
            status["code"] = 1
        finally:
            loop.stop()

    # Typing needs the hook: send() posts plain key events and the tap's
    # callback is what puts the modifier flags on them.  It is uninstalled in
    # the finally below, and dies with the process in any case, but an example
    # that types really is holding a keyboard tap while it runs.
    hook.install(keymap.on_key_event, keymap.on_hook_restored)
    try:
        action.starting()
        threading.Thread(target=worker, daemon=True).start()
        loop.run()
    finally:
        hook.uninstall()
        config.unlink(missing_ok=True)
        config.with_name("_runner_clip.json").unlink(missing_ok=True)
    return status["code"]
