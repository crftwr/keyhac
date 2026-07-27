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

from keyhac.core import log
from keyhac.core.keymap import Keymap

logger = log.getLogger("Main")


def main() -> int:
    parser = argparse.ArgumentParser(prog="keyhac", description="Keyhac 2")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="enable debug logging (key events, dispatch)")
    parser.add_argument("-c", "--config", metavar="PATH", default=None,
                        help="config file path (default: ~/.keyhac/config.py; "
                             "created from the template if missing)")
    parser.add_argument("--no-ui", action="store_true",
                        help="run without the console window (headless, logs to stderr)")
    args = parser.parse_args()

    log.set_debug(args.debug)

    if not args.no_ui:
        # print() from user configs must reach the console window; do this
        # before the first configure() so config-load output is captured too.
        log.redirect_std_streams()

    if sys.platform == "darwin":
        platform_name = "mac"
        import keyhac.platform.mac as platform_module

        if not platform_module.check_accessibility(prompt=True):
            logger.error(
                "Accessibility permission is required. Grant it in "
                "System Settings > Privacy & Security > Accessibility, then restart Keyhac.")
            return 1

    elif sys.platform == "win32":
        platform_name = "windows"
        import keyhac.platform.win as platform_module

    else:
        logger.error(f"Unsupported platform: {sys.platform}")
        return 1

    hook, focus_provider, native_loop = platform_module.create_platform()

    keymap = Keymap(hook, focus_provider, platform_name, config_path=args.config)

    # Clipboard history + app control (platform services above the hook)
    from keyhac.core.clipboard_history import ClipboardHistory
    if platform_name == "mac":
        from keyhac.platform.mac.clipboard import MacClipboardProvider
        from keyhac.platform.mac.apps import MacAppControl
        from keyhac.platform.mac.window import MacWindowProvider
        clipboard_provider = MacClipboardProvider()
        keymap.app_control = MacAppControl()
        keymap.window_provider = MacWindowProvider()
    else:
        from keyhac.platform.win.clipboard import WinClipboardProvider
        from keyhac.platform.win.apps import WinAppControl
        from keyhac.platform.win.window import WinWindowProvider
        clipboard_provider = WinClipboardProvider()
        keymap.app_control = WinAppControl()
        keymap.window_provider = WinWindowProvider()
    # With an explicit --config, keep the history beside it (sandbox testing
    # must not touch the real ~/.keyhac/clipboard.json).
    import os
    history_path = (os.path.join(os.path.dirname(os.path.abspath(args.config)),
                                 "clipboard.json")
                    if args.config else None)
    keymap._clipboard_history = ClipboardHistory(clipboard_provider, history_path)

    keymap.configure()

    if args.no_ui:
        return _run_headless(keymap, hook, native_loop, platform_name,
                             clipboard_provider)
    return _run_with_console(keymap, hook, platform_name, clipboard_provider)


def _run_with_console(keymap, hook, platform_name: str, clipboard_provider) -> int:
    from keyhac.ui.console import ConsoleWindow
    from keyhac.ui import runtime

    console = ConsoleWindow(keymap, hook)
    console.open()
    runtime.backend = console.backend
    console.attach_clipboard(clipboard_provider, keymap.clipboard_history)

    # Tray icon (reopen console / reload / quit) + balloons (multi-stroke help)
    from keyhac.ui.tray import install_tray
    from keyhac.ui.balloon import BalloonManager
    balloon = BalloonManager(console.backend)
    keymap.pop_balloon = balloon.pop
    keymap.close_balloon = balloon.close
    keymap.on_enter_multi_stroke = (
        lambda name: balloon.pop("MultiStroke", f"Multi-stroke: {name or '...'}"))
    keymap.on_leave_multi_stroke = lambda: balloon.close("MultiStroke")
    install_tray(console, keymap, hook)

    hook.install(keymap.on_key_event, keymap.on_hook_restored)
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
    hook.install(keymap.on_key_event, keymap.on_hook_restored)

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
