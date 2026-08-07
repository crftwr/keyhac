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

    from keyhac.core.keymap import Keymap
    from keyhac.platform.fake import FakeInputHook, FakeFocusProvider
    from keyhac.platform.mac.loop import MacEventLoop

    config = pathlib.Path(__file__).with_name("_runner_config.py")
    config.write_text("def configure(keymap):\n    pass\n")

    loop = MacEventLoop()
    keymap = Keymap(FakeInputHook("ansi"), FakeFocusProvider(), "mac",
                    config_path=str(config), template_path=str(config))
    keymap.set_main_thread_dispatcher(loop.call_on_main_thread)

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

    action.starting()
    threading.Thread(target=worker, daemon=True).start()
    loop.run()
    config.unlink(missing_ok=True)
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
