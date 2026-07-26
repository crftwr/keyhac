"""Windows application control.

STATUS: written to spec, NOT yet run on Windows.
"""

import ctypes
import os
import sys

from keyhac.platform.base import AppControl

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ENUMWINDOWSPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [ENUMWINDOWSPROC, wintypes.LPARAM]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    SW_RESTORE = 9


class WinAppControl(AppControl):

    def activate_pid(self, pid: int) -> bool:
        """Foreground the first visible top-level window of the process
        (keyhac-win's ActivateWindowCommand behavior: restore if minimized)."""
        if sys.platform != "win32":
            return False
        found = []

        def _cb(hwnd, lparam):
            wnd_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wnd_pid))
            if wnd_pid.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(ENUMWINDOWSPROC(_cb), 0)
        if not found:
            return False
        hwnd = found[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        return bool(user32.SetForegroundWindow(hwnd))

    def launch(self, app_name: str) -> None:
        os.startfile(app_name)
