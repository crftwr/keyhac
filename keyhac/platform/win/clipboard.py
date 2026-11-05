"""Windows clipboard provider - sequence-number polling + CF_UNICODETEXT,
and snapshots of the formats the history keeps.

STATUS: verified live on Windows (tests/test_win_clipboard.py): get/set
round-trip incl. Japanese and non-BMP emoji, sequence-number poll, empty
clipboard. The non-BMP case caught a real bug (see set_text).
"""

import ctypes
import struct
import sys

from keyhac.platform.base import (
    ClipboardProvider, ClipboardSnapshot, main_thread_only,
)

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
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
    user32.EnumClipboardFormats.restype = ctypes.c_uint
    user32.GetClipboardFormatNameW.argtypes = [ctypes.c_uint, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClipboardFormatNameW.restype = ctypes.c_int


# -- snapshot formats ----------------------------------------------------------
#
# Formats are named, not numbered, in a snapshot: a registered format's number
# is only good for the session that registered it, and the standard ones get a
# name here so that every key in a snapshot reads the same way.

STANDARD_FORMATS = {"CF_UNICODETEXT": 13, "CF_DIB": 8, "CF_HDROP": 15}
RICH_FORMATS = ("HTML Format", "Rich Text Format")
IMAGE_FORMATS = ("PNG", "CF_DIB")
DROP_EFFECT = "Preferred DropEffect"
KEPT_FORMATS = frozenset({*STANDARD_FORMATS, *RICH_FORMATS, *IMAGE_FORMATS,
                          DROP_EFFECT})

#: Formats that restate what is kept or describe it - the text and bitmap
#: forms Windows synthesizes from the ones kept, the shell's own views of a
#: file drop, OLE plumbing, the monitor markers.  Leaving one behind is not a
#: loss.  Metafiles are not here: those are content.
_RESTATING_STANDARD = frozenset({1, 2, 7, 16, 17})   # TEXT BITMAP OEMTEXT LOCALE DIBV5
_RESTATING_NAMES = frozenset({
    "Ole Private Data", "DataObject", "Object Descriptor", "Link Source Descriptor",
    "Shell IDList Array", "Shell Object Offsets", "FileName", "FileNameW",
    "FileGroupDescriptorW", "DropDescription", "UsingDefaultDragImage",
    "DragImageBits", "DragContext", "IsShowingLayered", "IsComputingImage",
    "DisableDragText", "IsShowingText", "Locale",
    "ExcludeClipboardContentFromMonitorProcessing",
    "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard",
    "Chromium internal source URL", "Chromium internal source RFH token",
    "text/x-moz-url-priv", "msSourceUrl", "Link Preview Format",
})

DROPEFFECT_COPY = 1
DROPEFFECT_MOVE = 2


def decode_unicode_text(data: bytes) -> str:
    """CF_UNICODETEXT's bytes as a string, up to its terminator."""
    return data.decode("utf-16-le", "replace").split("\x00", 1)[0]


def parse_hdrop(data: bytes) -> tuple:
    """The paths in a DROPFILES block."""
    if len(data) < 20:
        return ()
    offset, = struct.unpack_from("<I", data, 0)
    wide, = struct.unpack_from("<i", data, 16)
    body = data[offset:]
    text = body.decode("utf-16-le" if wide else "mbcs" if sys.platform == "win32" else "latin-1",
                       "replace")
    return tuple(p for p in text.split("\x00\x00", 1)[0].split("\x00") if p)


def build_hdrop(paths) -> bytes:
    """A DROPFILES block naming `paths`, wide-character."""
    header = struct.pack("<IiiiI", 20, 0, 0, 0, 1)
    return header + ("\x00".join(paths) + "\x00\x00").encode("utf-16-le")


def image_size(name: str, data: bytes) -> tuple | None:
    """Pixel size of a PNG or a packed DIB, from its header."""
    if name == "PNG" and data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if name == "CF_DIB" and len(data) >= 12:
        width, height = struct.unpack_from("<ii", data, 4)
        return (abs(width), abs(height))
    return None


def as_copy_effect(data: bytes) -> bytes:
    """A Preferred DropEffect that copies.  Explorer marks a cut as MOVE; put
    back from the history, that would move the files a second time."""
    if len(data) >= 4:
        effect, = struct.unpack_from("<I", data, 0)
        if effect & DROPEFFECT_MOVE:
            return struct.pack("<I", DROPEFFECT_COPY) + data[4:]
    return data


def dib_to_bmp(dib: bytes) -> bytes:
    """A packed DIB with the BITMAPFILEHEADER that makes it a .bmp file."""
    header_size, = struct.unpack_from("<I", dib, 0)
    bit_count, = struct.unpack_from("<H", dib, 14)
    compression, = struct.unpack_from("<I", dib, 16)
    colors_used, = struct.unpack_from("<I", dib, 32)
    palette = colors_used or (1 << bit_count if bit_count <= 8 else 0)
    # BI_BITFIELDS with a plain BITMAPINFOHEADER carries three masks after it.
    masks = 12 if compression == 3 and header_size == 40 else 0
    offset = 14 + header_size + masks + palette * 4
    return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, offset) + dib


def describe(item: dict, text: str | None, dropped: bool) -> ClipboardSnapshot:
    """The portable description of one Windows clipboard item."""
    files = parse_hdrop(item["CF_HDROP"]) if "CF_HDROP" in item else ()
    size = None
    for name in IMAGE_FORMATS:
        if name in item:
            size = image_size(name, item[name])
            if size:
                break
    has_image = any(name in item for name in IMAGE_FORMATS)
    if files:
        kind = "files"
    elif text:
        # Text first: an Excel range offers a picture of itself too, and is
        # still the text the user copied.
        rich = has_image or any(name in item for name in RICH_FORMATS)
        kind = "rich" if rich else "text"
    elif has_image:
        kind = "image"
    else:
        kind = None
    return ClipboardSnapshot(kind=kind, text=text or None, image_size=size,
                             files=files, lossy=dropped,
                             items=[item] if item else [])


class WinClipboardProvider(ClipboardProvider):

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WinClipboardProvider requires Windows")
        self._last_sequence = user32.GetClipboardSequenceNumber()
        # Registered formats: the name is the contract, the number is per
        # session, so it is looked up once here.
        self._cf_exclude = user32.RegisterClipboardFormatW(
            "ExcludeClipboardContentFromMonitorProcessing")
        self._cf_can_include = user32.RegisterClipboardFormatW(
            "CanIncludeInClipboardHistory")

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
    def set_text(self, s: str, concealed: bool = False) -> None:
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
            if concealed:
                self._set_marker(self._cf_exclude)
        finally:
            user32.CloseClipboard()

    @staticmethod
    def _set_marker(cf: int) -> None:
        """Put an empty marker format on the already-open clipboard."""
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, 1)
        if handle and not user32.SetClipboardData(cf, handle):
            kernel32.GlobalFree(handle)

    @main_thread_only
    def is_concealed(self) -> bool:
        # Presence alone says "leave this alone"; no open needed to ask.
        if self._cf_exclude and user32.IsClipboardFormatAvailable(self._cf_exclude):
            return True
        # CanIncludeInClipboardHistory carries a DWORD: 0 excludes, 1 allows.
        if not (self._cf_can_include
                and user32.IsClipboardFormatAvailable(self._cf_can_include)):
            return False
        if not user32.OpenClipboard(None):
            # Marked but unreadable: err on the side of not recording it.
            return True
        try:
            handle = user32.GetClipboardData(self._cf_can_include)
            if not handle or kernel32.GlobalSize(handle) < 4:
                return True
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return True
            try:
                return ctypes.c_uint32.from_address(ptr).value == 0
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def _format_name(self, fmt: int) -> str | None:
        for name, number in STANDARD_FORMATS.items():
            if number == fmt:
                return name
        if fmt >= 0xC000:
            buf = ctypes.create_unicode_buffer(256)
            if user32.GetClipboardFormatNameW(fmt, buf, len(buf)):
                return buf.value
        return None

    def _format_number(self, name: str) -> int:
        return STANDARD_FORMATS.get(name) or user32.RegisterClipboardFormatW(name)

    @staticmethod
    def _read_global(handle, limit: int = None) -> bytes | None:
        size = kernel32.GlobalSize(handle)
        if limit is not None and size > limit:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.string_at(ptr, size)
        finally:
            kernel32.GlobalUnlock(handle)

    @main_thread_only
    def get_snapshot(self, max_format_size: int = None) -> ClipboardSnapshot | None:
        if not user32.OpenClipboard(None):
            return None
        try:
            item, dropped, any_format = {}, False, False
            fmt = user32.EnumClipboardFormats(0)
            while fmt:
                any_format = True
                name = self._format_name(fmt)
                if name in KEPT_FORMATS:
                    # Only these are asked for: GetClipboardData on a
                    # delay-rendered format makes the source application
                    # render it, synchronously, on this thread.
                    handle = user32.GetClipboardData(fmt)
                    data = self._read_global(handle, max_format_size) if handle else None
                    if data is None:
                        dropped = True
                    else:
                        item[name] = data
                elif not (fmt in _RESTATING_STANDARD or name in _RESTATING_NAMES):
                    dropped = True
                fmt = user32.EnumClipboardFormats(fmt)
            if not any_format:
                return None
            text = (decode_unicode_text(item["CF_UNICODETEXT"])
                    if "CF_UNICODETEXT" in item else None)
            return describe(item, text, dropped)
        finally:
            user32.CloseClipboard()

    def image_data(self, snapshot: ClipboardSnapshot) -> bytes | None:
        item = snapshot.items[0] if snapshot.items else {}
        if "PNG" in item:
            return item["PNG"]
        if "CF_DIB" in item and len(item["CF_DIB"]) >= 40:
            return dib_to_bmp(item["CF_DIB"])
        return None

    @main_thread_only
    def set_snapshot(self, snapshot: ClipboardSnapshot) -> None:
        item = dict(snapshot.items[0]) if snapshot.items else {}
        if not item and snapshot.files:
            # Back from disk, where only the paths were kept.
            item["CF_HDROP"] = build_hdrop(snapshot.files)
            item[DROP_EFFECT] = struct.pack("<I", DROPEFFECT_COPY)
        if not item:
            if snapshot.text:
                self.set_text(snapshot.text)
            return
        if DROP_EFFECT in item:
            item[DROP_EFFECT] = as_copy_effect(item[DROP_EFFECT])
        if not user32.OpenClipboard(None):
            return
        try:
            user32.EmptyClipboard()
            for name, data in item.items():
                handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(len(data), 1))
                if not handle:
                    continue
                ptr = kernel32.GlobalLock(handle)
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(handle)
                if not user32.SetClipboardData(self._format_number(name), handle):
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
