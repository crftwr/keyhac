"""Windows main event loop - GetMessage pump.

A blocking GetMessage pump on the hook's thread is exactly what
WH_KEYBOARD_LL needs (callbacks are delivered during message retrieval) and
uses no idle CPU.  call_later uses a thread-owned SetTimer (null hWnd);
call_on_main_thread posts WM_APP+1 to the same thread and drains a queue.

STATUS: run on Windows - the pump, WM_TIMER dispatch and stop() are validated.
call_on_main_thread has unit coverage but has not been exercised live yet.
"""

import ctypes
import sys
import threading
from typing import Callable

from keyhac.platform.base import EventLoop

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    WM_QUIT = 0x0012
    WM_TIMER = 0x0113
    HWND_MESSAGE = -3
    # WM_APP+n is the range reserved for an application's own messages, so it
    # cannot collide with a system message or with a window class's private
    # WM_USER range.
    WM_CALL_ON_MAIN_THREAD = 0x8000 + 1  # WM_APP + 1

    # 64-bit correctness: ctypes defaults restype to c_int, which truncates
    # the pointer-sized timer id returned by SetTimer.
    UINT_PTR = ctypes.c_size_t
    TIMERPROC = ctypes.WINFUNCTYPE(None, wintypes.HWND, wintypes.UINT,
                                   UINT_PTR, wintypes.DWORD)
    # A declared function-pointer argtype rejects a bare None, so NULL - which
    # is what makes SetTimer post WM_TIMER instead of calling back - needs to
    # be an explicitly cast instance.
    NULL_TIMERPROC = ctypes.cast(None, TIMERPROC)

    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = wintypes.LPARAM
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.SetTimer.argtypes = [wintypes.HWND, UINT_PTR, wintypes.UINT, TIMERPROC]
    user32.SetTimer.restype = UINT_PTR
    user32.KillTimer.argtypes = [wintypes.HWND, UINT_PTR]
    user32.KillTimer.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class WinEventLoop(EventLoop):

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WinEventLoop requires Windows")
        self._thread_id = kernel32.GetCurrentThreadId()
        self._timers: dict[int, Callable[[], None]] = {}
        self._next_timer_id = 1
        self._pending_lock = threading.Lock()
        self._pending: list[Callable[[], None]] = []

    def run(self) -> None:
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            # A thread message (PostThreadMessageW) arrives with a null hWnd and
            # DispatchMessageW would drop it, so it has to be handled here.
            if msg.message == WM_CALL_ON_MAIN_THREAD and not msg.hWnd:
                self._drain_pending()
                continue
            if msg.message == WM_TIMER and not msg.hWnd:
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
        timer_id = user32.SetTimer(None, 0, int(delay_seconds * 1000), NULL_TIMERPROC)
        if not timer_id:
            error = ctypes.get_last_error()
            from keyhac.core import log
            log.getLogger("WinLoop").error(
                f"SetTimer failed: {error} ({ctypes.FormatError(error)})")
            return
        self._timers[timer_id] = func

    def call_on_main_thread(self, callback: Callable[[], None]) -> None:
        with self._pending_lock:
            self._pending.append(callback)
        # PostThreadMessageW needs the target thread to own a message queue.
        # The loop thread has one once it has called a message function; the
        # error is logged rather than raised so a late/early call degrades to a
        # dropped callback instead of killing the worker.
        if not user32.PostThreadMessageW(
                self._thread_id, WM_CALL_ON_MAIN_THREAD, 0, 0):
            error = ctypes.get_last_error()
            from keyhac.core import log
            log.getLogger("WinLoop").error(
                f"PostThreadMessageW failed: {error} ({ctypes.FormatError(error)})")

    def _drain_pending(self) -> None:
        # Swap the whole list out: a callback that posts another one must not
        # extend the batch being walked (and must not deadlock on the lock).
        with self._pending_lock:
            callbacks, self._pending = self._pending, []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                from keyhac.core import log
                log.getLogger("WinLoop").error("Main-thread callback raised.")
