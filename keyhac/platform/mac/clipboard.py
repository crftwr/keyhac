"""macOS clipboard provider - NSPasteboard with changeCount polling."""

from AppKit import NSPasteboard, NSPasteboardTypeString

from keyhac.platform.base import ClipboardProvider


class MacClipboardProvider(ClipboardProvider):

    def __init__(self):
        self._pasteboard = NSPasteboard.generalPasteboard()
        self._last_change_count = self._pasteboard.changeCount()

    def get_text(self) -> str | None:
        s = self._pasteboard.stringForType_(NSPasteboardTypeString)
        return str(s) if s is not None else None

    def set_text(self, s: str) -> None:
        self._pasteboard.clearContents()
        self._pasteboard.setString_forType_(s, NSPasteboardTypeString)

    def poll(self) -> bool:
        count = self._pasteboard.changeCount()
        if count != self._last_change_count:
            self._last_change_count = count
            return True
        return False
