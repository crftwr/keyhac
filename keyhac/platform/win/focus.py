"""Windows focus provider - Win32 via ctypes.

Reimplements the subset of pyauto.Window that keyhac-win's WindowKeymap
matching used: exe name, window class, window text of the focused window.

The focus path string is a provisional synthesized format
"/{app_name}/{class_name}({title})" so that focus_path_pattern="*" (the
global key table) works portably; app/title/class_name matching is preferred
on Windows.

STATUS: run on Windows - get_focus() returns correct app/title/class_name for
the foreground window.
"""

import ctypes
import os
import sys

from keyhac.platform.base import FocusProvider, Focus
from keyhac.core.focus import FOCUS_PATH_TRANS_TABLE
from keyhac.core import log

logger = log.getLogger("WinFocus")

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]

    # Mandatory on 64-bit: the default c_int restype truncates HWND/HANDLE.
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
    user32.GetGUIThreadInfo.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


class NativeWindow:
    """Minimal Win32 window wrapper exposed as Focus.native."""

    def __init__(self, hwnd):
        self.hwnd = hwnd

    def get_text(self) -> str:
        length = user32.GetWindowTextLengthW(self.hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(self.hwnd, buf, length + 1)
        return buf.value

    def get_class_name(self) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(self.hwnd, buf, 256)
        return buf.value

    def get_process_name(self) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(self.hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
            return ""
        finally:
            kernel32.CloseHandle(handle)

    def get_pid(self) -> int:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(self.hwnd, ctypes.byref(pid))
        return pid.value


class WinFocusProvider(FocusProvider):

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WinFocusProvider requires Windows")

    def get_focus(self) -> Focus | None:

        foreground = user32.GetForegroundWindow()
        if not foreground:
            return None

        # The actually-focused child window (GUITHREADINFO.hwndFocus) gives
        # the class name that keyhac-win configs match against (e.g. "Edit").
        focus_hwnd = foreground
        thread_id = user32.GetWindowThreadProcessId(foreground, None)
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)) and info.hwndFocus:
            focus_hwnd = info.hwndFocus

        top = NativeWindow(foreground)
        focused = NativeWindow(focus_hwnd)

        exe = top.get_process_name()
        app_name = exe.removesuffix(".exe").removesuffix(".EXE") if exe else None
        title = top.get_text()
        class_name = focused.get_class_name()

        safe_title = (title or "").translate(FOCUS_PATH_TRANS_TABLE)
        path = f"/{app_name or ''}/{class_name or ''}({safe_title})"

        return Focus(
            app_name=app_name,
            pid=top.get_pid(),
            window_title=title,
            class_name=class_name,
            path=path,
            native=focused,
        )
