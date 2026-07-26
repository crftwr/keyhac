"""Keyhac 2 bootstrap.

M1: command-line app - installs the hook, loads ~/.keyhac/config.py, runs the
native event loop.  The PuiKit console window replaces stderr logging in M2.

Usage:
    keyhac [-d]          (or: python -m keyhac [-d])
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
    args = parser.parse_args()

    log.set_debug(args.debug)

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

    hook, focus_provider, loop = platform_module.create_platform()

    keymap = Keymap(hook, focus_provider, platform_name, config_path=args.config)
    keymap.configure()

    hook.install(keymap.on_key_event, keymap.on_hook_restored)

    # Periodic hook health check (no-op on platforms with internal timers).
    # The 100 ms Python tick also guarantees the SIGINT handler below runs
    # promptly even while blocked in the native loop.
    def health_tick():
        hook.check_health()
        loop.call_later(0.1, health_tick)

    loop.call_later(0.1, health_tick)

    # Ctrl+C: stop the native loop cleanly.  KeyboardInterrupt cannot
    # propagate out of native run-loop callbacks (PyObjC logs and swallows
    # it), so the handler stops the loop instead of raising.
    signal.signal(signal.SIGINT, lambda sig, frame: loop.stop())

    logger.info(f"Keyhac 2 running ({platform_name}, config: {keymap._config_path}). "
                "Press Ctrl+C to quit.")

    try:
        loop.run()
    finally:
        hook.uninstall()

    logger.info("Keyhac 2 stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
