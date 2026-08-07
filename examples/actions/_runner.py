"""Run one of these example actions without the whole application.

The thread architecture matters to what is being demonstrated, so this is not
a shortcut around it: the event loop turns on the main thread, the action's
run() executes on a worker, and every UI read is dispatched back - exactly the
arrangement `keyhac.core.wait` is written against, and the one that makes
waiting legal from a worker at all.

    python examples/actions/extract_records.py
"""

import pathlib
import sys
import threading
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))


def run_action(action) -> int:
    """Drive one ThreadedAction to completion, then stop the loop."""
    if sys.platform != "darwin":
        print("These examples have only been run on macOS so far.")
        return 1

    from keyhac.core.clipboard_history import ClipboardHistory
    from keyhac.core.keymap import Keymap
    from keyhac.core.vk import init_key_names
    from keyhac.platform.fake import FakeFocusProvider
    from keyhac.platform.mac.clipboard import MacClipboardProvider
    from keyhac.platform.mac.hook import MacInputHook
    from keyhac.platform.mac.loop import MacEventLoop

    config = pathlib.Path(__file__).with_name("_runner_config.py")
    config.write_text("def configure(keymap):\n    pass\n")

    init_key_names("mac", "ansi")
    loop = MacEventLoop()
    hook = MacInputHook()
    keymap = Keymap(hook, FakeFocusProvider(), "mac",
                    config_path=str(config), template_path=str(config))
    # configure() is what fills in the modifier map, and without it send_key()
    # emits the key without its modifiers - "Cmd-V" arrives as a bare "v".
    # Silent, and it cost an afternoon of blaming the OS.
    keymap.configure()
    keymap.set_main_thread_dispatcher(loop.call_on_main_thread)

    clipboard = MacClipboardProvider()
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


def front_window(app_name: str):
    """The frontmost window of a running application, as a UIElement.

    Main-thread only, like every other element read.
    """
    import ApplicationServices as AS
    from AppKit import NSWorkspace
    from keyhac.platform.mac.uielement import UIElement

    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if str(app.localizedName()) == app_name:
            element = UIElement(AS.AXUIElementCreateApplication(
                app.processIdentifier()))
            AS.AXUIElementSetMessagingTimeout(element._ref, 5.0)
            windows = element.get_attribute_value("AXWindows") or []
            return (windows[0] if windows else None), element
    return None, None
