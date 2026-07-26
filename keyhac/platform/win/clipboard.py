"""Windows clipboard provider - sequence-number polling + CF_UNICODETEXT.

STATUS: written to spec, NOT yet run on Windows.
"""

import ctypes
import sys

from keyhac.platform.base import ClipboardProvider

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    user32.GetClipboardSequenceNumber.restype = ctypes.c_uint32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p


class WinClipboardProvider(ClipboardProvider):

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WinClipboardProvider requires Windows")
        self._last_sequence = user32.GetClipboardSequenceNumber()

    def get_text(self) -> str | None:
        if not user32.OpenClipboard(None):
            return None
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def set_text(self, s: str) -> None:
        if not user32.OpenClipboard(None):
            return
        try:
            user32.EmptyClipboard()
            size = (len(s) + 1) * ctypes.sizeof(ctypes.c_wchar)
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            ptr = kernel32.GlobalLock(handle)
            ctypes.memmove(ptr, ctypes.create_unicode_buffer(s), size)
            kernel32.GlobalUnlock(handle)
            user32.SetClipboardData(CF_UNICODETEXT, handle)
        finally:
            user32.CloseClipboard()

    def poll(self) -> bool:
        sequence = user32.GetClipboardSequenceNumber()
        if sequence != self._last_sequence:
            self._last_sequence = sequence
            return True
        return False
