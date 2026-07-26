"""Hook echo tool - the M0 spike, kept for platform bring-up.

Installs the keyboard hook and prints every event with its consume decision
(always pass-through), plus the current focus on each key-down.  No keymap,
no config - this isolates the platform layer.

Run:  python tools/hook_echo.py
Quit: Ctrl+C

This must be the FIRST thing run in any new platform bring-up (especially
the first Windows session - the win backend has not been executed yet).
"""

import sys
import time
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from keyhac.core import log
from keyhac.core.vk import init_key_names
from keyhac.platform.base import KeyEvent

log.set_debug(True)
logger = log.getLogger("Echo")


def main():
    if sys.platform == "darwin":
        import keyhac.platform.mac as platform_module
        if not platform_module.check_accessibility(prompt=True):
            logger.error("Accessibility permission required; grant and re-run.")
            return 1
        platform_name = "mac"
    elif sys.platform == "win32":
        import keyhac.platform.win as platform_module
        platform_name = "windows"
    else:
        logger.error(f"Unsupported platform: {sys.platform}")
        return 1

    hook, focus_provider, loop = platform_module.create_platform()
    names = init_key_names(platform_name, hook.keyboard_layout())
    logger.info(f"Keyboard layout: {hook.keyboard_layout()}")

    def on_key(event: KeyEvent) -> bool:
        t0 = time.perf_counter()
        name = names.vk_to_str(event.vk)
        focus = focus_provider.get_focus() if event.down else None
        dt = (time.perf_counter() - t0) * 1000
        direction = "D" if event.down else "U"
        line = f"{direction}-{name:12s} vk={event.vk:<4d} kind={event.kind:6s} focus_query={dt:5.2f}ms"
        if focus is not None:
            line += f"  app={focus.app_name!r} title={focus.window_title!r}"
        logger.info(line)
        return False  # never consume

    def on_restored():
        logger.warning("Hook was disabled by the OS and has been restored.")

    hook.install(on_key, on_restored)

    def health_tick():
        hook.check_health()
        loop.call_later(0.1, health_tick)
    loop.call_later(0.1, health_tick)

    import signal
    signal.signal(signal.SIGINT, lambda sig, frame: loop.stop())

    logger.info("Echoing key events. Ctrl+C to quit.")
    try:
        loop.run()
    finally:
        hook.uninstall()
    return 0


if __name__ == "__main__":
    sys.exit(main())
