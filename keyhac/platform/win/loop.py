"""Windows main event loop - GetMessage pump.

A blocking GetMessage pump on the hook's thread is exactly what
WH_KEYBOARD_LL needs (callbacks are delivered during message retrieval) and
uses no idle CPU.  call_later uses SetTimer on a message-only window.

STATUS: written to spec, NOT yet run on Windows (M1 was developed on macOS).
"""

import ctypes
import sys
from typing import Callable

from keyhac.platform.base import EventLoop

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    WM_QUIT = 0x0012
    WM_TIMER = 0x0113
    HWND_MESSAGE = -3


class WinEventLoop(EventLoop):

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WinEventLoop requires Windows")
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self._timers: dict[int, Callable[[], None]] = {}
        self._next_timer_id = 1

    def run(self) -> None:
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_TIMER and msg.hwnd is None:
                callback = self._timers.pop(msg.wParam, None)
                if callback is not None:
                    user32.KillTimer(None, msg.wParam)
                    try:
                        callback()
                    except Exception:
                        from keyhac.core import log
                        log.getLogger("WinLoop").error("Timer callback raised.")
                    continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self) -> None:
        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def call_later(self, delay_seconds: float, func: Callable[[], None]) -> None:
        timer_id = user32.SetTimer(None, 0, int(delay_seconds * 1000), None)
        self._timers[timer_id] = func
