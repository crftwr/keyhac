"""Windows clipboard provider - sequence-number polling + CF_UNICODETEXT.

STATUS: verified live on Windows (tests/test_win_clipboard.py): get/set
round-trip incl. Japanese and non-BMP emoji, sequence-number poll, empty
clipboard. The non-BMP case caught a real bug (see set_text).
"""

import ctypes
import sys

from keyhac.platform.base import ClipboardProvider, main_thread_only

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    from ctypes import wintypes

    user32.GetClipboardSequenceNumber.argtypes = []
    user32.GetClipboardSequenceNumber.restype = ctypes.c_uint32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p


class WinClipboardProvider(ClipboardProvider):

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WinClipboardProvider requires Windows")
        self._last_sequence = user32.GetClipboardSequenceNumber()

    @main_thread_only
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

    @main_thread_only
    def set_text(self, s: str) -> None:
        if not user32.OpenClipboard(None):
            return
        try:
            user32.EmptyClipboard()
            # Explicit UTF-16-LE: len(s) counts code points, but non-BMP
            # characters (emoji) take two UTF-16 units, so a
            # create_unicode_buffer(s)-sized allocation truncates them.
            data = s.encode("utf-16-le") + b"\x00\x00"
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                return
            ptr = kernel32.GlobalLock(handle)
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(handle)
            # On success the system owns the handle; on failure it is ours
            # to free (the documented SetClipboardData contract).
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
        finally:
            user32.CloseClipboard()

    @main_thread_only
    def poll(self) -> bool:
        sequence = user32.GetClipboardSequenceNumber()
        if sequence != self._last_sequence:
            self._last_sequence = sequence
            return True
        return False
