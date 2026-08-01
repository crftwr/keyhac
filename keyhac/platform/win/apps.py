"""Windows application control.

STATUS: activate_pid verified live on Windows (chooser-focus session: a
two-process probe armed the foreground lock with synthesized input and
confirmed plain SetForegroundWindow is refused while this path wins
foreground + keyboard focus). launch() and edit_file() written to spec, not
yet run.
"""

import ctypes
import os
import sys

from keyhac.core import log
from keyhac.platform.base import AppControl

logger = log.getLogger("WinApps")

if sys.platform == "win32":
    from ctypes import wintypes

    # Foregrounding delegates to WinWindow.activate(), which carries the
    # AttachThreadInput foreground-lock workaround (a background process --
    # Keyhac at chooser-open time -- is refused a plain SetForegroundWindow).
    from keyhac.platform.win.window import WinWindow

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    ENUMWINDOWSPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [ENUMWINDOWSPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    shell32.ShellExecuteW.restype = wintypes.HINSTANCE

    SW_SHOWNORMAL = 1


class WinAppControl(AppControl):

    def activate_pid(self, pid: int) -> bool:
        """Foreground the process's first visible top-level window in Z-order
        (for our own pid that is the topmost chooser, not the console).
        WinWindow.activate() restores a minimized window first."""
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
        return WinWindow(found[0]).activate()

    def launch(self, app_name: str) -> None:
        os.startfile(app_name)

    def edit_file(self, path: str, editor: str | None = None) -> None:
        """ShellExecute the editor with the quoted path (the keyhac-win
        editTextFile call: PATH and App Paths both resolve the name, so
        "notepad.exe" and registered editors like "Code.exe" work)."""
        if sys.platform != "win32":
            return
        editor = editor or "notepad.exe"
        result = shell32.ShellExecuteW(
            None, None, editor, f'"{path}"', None, SW_SHOWNORMAL)
        # Documented contract: the pseudo-HINSTANCE is > 32 on success.
        if (result or 0) <= 32:
            logger.warning(f"Could not open {path} with {editor} "
                           f"(ShellExecute code {result}).")
