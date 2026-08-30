"""Keyhac 2 bootstrap.

Default mode opens the PuiKit console window; its backend runs the process's
native event loop and the keyboard hook shares it (CGEventTap source on the
same run loop on macOS; the GetMessage pump services WH_KEYBOARD_LL on
Windows).  --no-ui keeps the M1 headless mode (bare native loop + stderr).

Usage:
    keyhac [-d] [-c PATH] [--no-ui]       (or: python -m keyhac ...)
"""

import argparse
import signal
import sys

from keyhac import __version__
from keyhac.core import log, paths
from keyhac.core.keymap import Keymap

logger = log.getLogger("Main")

WEBSITE_URL = "https://crftwr.github.io/keyhac/"


def main() -> int:
    parser = argparse.ArgumentParser(prog="keyhac", description="Keyhac 2")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="enable debug logging (key events, dispatch)")
    parser.add_argument("-c", "--config", metavar="PATH", default=None,
                        help="config file path (default: ~/.keyhac/config.py, "
                             "or the Keyhac.exe directory in Windows portable "
                             "mode; created from the template if missing)")
    parser.add_argument("--no-ui", action="store_true",
                        help="run without the console window (headless, logs to stderr)")
    args = parser.parse_args()

    log.set_debug(args.debug)

    # First line of every run, before anything that can fail: which build this
    # is, and where its documentation is. The console window backfills the
    # lines logged before it opened, so it heads that too - and a run that
    # stops at the single-instance check or the accessibility prompt has still
    # said which version stopped.
    #
    # A banner, not a record: no level and no source name in front of it. That
    # is what print() writes here, except that print() only becomes console
    # output at redirect_std_streams() below, which is after the point where
    # this has to be said - so it goes to the same place by hand.
    log.Console.get_instance().write(f"Keyhac {__version__} - {WEBSITE_URL}\n")

    if sys.platform == "darwin":
        platform_name = "mac"
        import keyhac.platform.mac as platform_module
    elif sys.platform == "win32":
        platform_name = "windows"
        import keyhac.platform.win as platform_module
    else:
        logger.error(f"Unsupported platform: {sys.platform}")
        return 1

    # Single instance: two Keyhacs would both install low-level hooks and
    # fight over every key. Checked before the std-stream redirect so the
    # error still lands on stderr (this process's console window never
    # opens). The lock object must stay referenced until the process ends.
    instance_lock = platform_module.acquire_instance_lock()
    if instance_lock is None:
        logger.error("Keyhac is already running.")
        if not args.no_ui:
            # Feedback for a double-click launch: surface the running
            # instance (its console may be hidden to the tray).
            platform_module.notify_already_running()
        return 1

    if not args.no_ui:
        # print() from user configs must reach the console window; do this
        # before the first configure() so config-load output is captured too.
        log.redirect_std_streams()

    if platform_name == "mac":
        if not platform_module.check_accessibility(prompt=True):
            logger.error(
                "Accessibility permission is required. Grant it in "
                "System Settings > Privacy & Security > Accessibility, then restart Keyhac.")
            return 1

    # Where config.py and the state files beside it live: --config, else
    # Windows portable mode (a config.py next to Keyhac.exe), else ~/.keyhac.
    app_paths = paths.resolve(args.config)
    if app_paths.portable:
        logger.info(f"Portable mode: using {app_paths.data_dir}")
    elif platform_name == "windows" and not args.no_ui and not args.config:
        # First run on a machine upgrading from Keyhac 1.x: offer to bring the
        # old %APPDATA%\Keyhac config across before anything reads (or
        # template-creates) the new one.  Needs a message box, so not in
        # --no-ui runs; and an explicit --config asked for a specific setup,
        # which a sandbox expects to start from the template.
        platform_module.offer_config_migration(app_paths.config_path)

    hook, focus_provider, native_loop = platform_module.create_platform()

    keymap = Keymap(hook, focus_provider, platform_name,
                    config_path=app_paths.config_path)

    # Clipboard history + app control (platform services above the hook)
    from keyhac.core.clipboard_history import ClipboardHistory
    if platform_name == "mac":
        from keyhac.platform.mac.clipboard import MacClipboardProvider
        from keyhac.platform.mac.apps import MacAppControl
        from keyhac.platform.mac.window import MacWindowProvider
        from keyhac.platform.mac.ime import MacImeProvider
        clipboard_provider = MacClipboardProvider()
        keymap.app_control = MacAppControl()
        keymap.window_provider = MacWindowProvider()
        keymap.ime_provider = MacImeProvider()
    else:
        from keyhac.platform.win.clipboard import WinClipboardProvider
        from keyhac.platform.win.apps import WinAppControl
        from keyhac.platform.win.window import WinWindowProvider
        from keyhac.platform.win.ime import WinImeProvider
        clipboard_provider = WinClipboardProvider()
        keymap.app_control = WinAppControl()
        keymap.window_provider = WinWindowProvider()
        keymap.ime_provider = WinImeProvider()
    # The state files always sit beside the config, however it resolved: a
    # --config sandbox must not touch the real ~/.keyhac/clipboard.json, and a
    # portable install keeps its history on the stick with its config.
    keymap._clipboard_history = ClipboardHistory(
        clipboard_provider, app_paths.state_file("clipboard.json"))

    keymap.configure()

    # The MCP endpoint is deliberately *not* restored here. It closes itself an
    # hour after it is switched on, and a start-up that reopened it would be the
    # one way back to an endpoint nobody remembers arming - which is the state
    # the timeout exists to end. It is ticked per session, in the console window
    # or the tray menu.
    #
    # Which does leave --no-ui unable to open it, having no menu to tick. That
    # is the honest shape rather than a gap: this is an authoring-time feature,
    # and authoring happens where the operator can see the switch.
    from keyhac.core.settings import Settings
    settings = Settings(app_paths.state_file("settings.json"))

    if args.no_ui:
        return _run_headless(keymap, hook, native_loop, platform_name,
                             clipboard_provider)

    return _run_with_console(keymap, hook, platform_name, clipboard_provider,
                             settings)


def _run_with_console(keymap, hook, platform_name: str, clipboard_provider,
                      settings) -> int:
    from keyhac.ui.console import ConsoleWindow
    from keyhac.ui import runtime

    console = ConsoleWindow(keymap, hook, settings=settings)
    console.open()
    runtime.backend = console.backend
    # The chooser is a new window every invocation, so what it remembers
    # between them (its size) has to live somewhere that outlives it.
    runtime.settings = settings
    # PuiKit's loop is the one turning in this mode, so it owns the hand-back
    # to the main thread (ThreadedAction.finished, keymap.call_on_main_thread).
    if console.backend.capabilities.supports("main_thread_dispatch"):
        keymap.set_main_thread_dispatcher(console.backend.call_on_main_thread)
    console.attach_clipboard(clipboard_provider, keymap.clipboard_history)

    # Tray icon (reopen console / reload / quit) + balloons (multi-stroke help)
    from keyhac.ui.tray import install_tray
    from keyhac.ui.balloon import BalloonManager
    balloon = BalloonManager(console.backend)
    keymap.pop_balloon = balloon.pop
    keymap.close_balloon = balloon.close
    def _multi_stroke_balloon(name):
        # keymap.focus is the snapshot this very keystroke was dispatched
        # against, so the element is already in hand: no second focus lookup,
        # and none of it on the hook's clock beyond two attribute reads.
        from keyhac.core.anchor import caret_anchor
        focus = keymap.focus
        balloon.pop("MultiStroke", f"Multi-stroke: {name or '...'}",
                    near=caret_anchor(getattr(focus, "element", None)))

    keymap.on_enter_multi_stroke = _multi_stroke_balloon
    keymap.on_leave_multi_stroke = lambda: balloon.close("MultiStroke")
    install_tray(console, keymap, hook)

    hook.install(keymap.on_key_event, keymap.on_hook_restored,
                 keymap.on_mouse_event)
    console._hook_checkbox.checked = True

    # Ctrl+C in the launching terminal stops the UI loop. The console's
    # ~10 Hz pump tick guarantees the Python-level handler runs promptly.
    signal.signal(signal.SIGINT, lambda sig, frame: console.quit())

    logger.info(f"Keyhac 2 running ({platform_name}, config: {keymap._config_path}).")

    try:
        console.run()
    finally:
        hook.uninstall()
        keymap.clipboard_history.flush()
        console.close()

    logger.info("Keyhac 2 stopped.")
    return 0


def _run_headless(keymap, hook, loop, platform_name: str, clipboard_provider) -> int:
    # No PuiKit backend here; the bare native loop is the main thread.
    keymap.set_main_thread_dispatcher(loop.call_on_main_thread)

    hook.install(keymap.on_key_event, keymap.on_hook_restored,
                 keymap.on_mouse_event)

    # Periodic hook health check; the 100 ms Python tick also guarantees the
    # SIGINT handler below runs promptly while blocked in the native loop.
    def health_tick():
        hook.check_health()
        if clipboard_provider.poll():
            keymap.clipboard_history.on_clipboard_changed()
        loop.call_later(0.1, health_tick)

    loop.call_later(0.1, health_tick)
    signal.signal(signal.SIGINT, lambda sig, frame: loop.stop())

    logger.info(f"Keyhac 2 running headless ({platform_name}, "
                f"config: {keymap._config_path}). Press Ctrl+C to quit.")

    try:
        loop.run()
    finally:
        hook.uninstall()
        keymap.clipboard_history.flush()

    logger.info("Keyhac 2 stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
