"""Clipboard history (text), ported from keyhac-mac keyhac_clipboard.py.

Differences from the original:
- backed by the portable ClipboardProvider instead of keyhac_core.Clipboard
- saving is debounced (dirty flag + flush()) instead of rewriting the JSON
  file on every copy (the upstream FIXME); main() flushes periodically and
  on exit.
"""

import collections
import json
import os
import re

from keyhac.platform.base import ClipboardProvider
from keyhac.core import log

logger = log.getLogger("Clipboard")


class ClipboardHistory:
    """Automatically captures historical clipboard text.

    Class variables configure limits:
    - max_items: maximum entries kept (default 1000)
    - max_label_length: maximum length of item labels (default 4096)
    - max_data_size: maximum size of a single item (default 10 MB)
    - max_persist_data_size: maximum size persisted per item (default 64 KB)
    """

    max_items = 1000
    max_label_length = 4096
    max_data_size = 10 * 1024 * 1024
    max_persist_data_size = 64 * 1024

    def __init__(self, provider: ClipboardProvider, filename: str = None):
        self._provider = provider
        self.filename = filename or os.path.expanduser("~/.keyhac/clipboard.json")
        self.persist = True
        self._items: collections.OrderedDict[str, str] = collections.OrderedDict()
        self.dirty = False
        self._load()

    # -- capture ------------------------------------------------------------

    def on_clipboard_changed(self) -> None:
        """Called (on the UI thread) when the provider detected a change."""
        s = self._provider.get_text()
        if s:
            self.add_item(s)

    def items(self):
        """Iterate (text, label) pairs, latest first."""
        for s, label in reversed(self._items.items()):
            yield s, label

    def add_item(self, s: str) -> None:
        """Add text to the history (duplicates move to the front)."""
        if not s or len(s) > self.max_data_size:
            return
        if s in self._items:
            del self._items[s]
        self._items[s] = self._shorten(s)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
        self.dirty = True

    def set_current(self, s: str) -> None:
        """Set text to the OS clipboard and the front of the history."""
        self.add_item(s)
        self._provider.set_text(s)

    def get_current(self) -> str | None:
        for s, _label in self.items():
            return s
        return None

    # -- persistence ----------------------------------------------------------

    def flush(self) -> None:
        """Save if there are unsaved changes (call periodically and on exit)."""
        if self.dirty and self.persist:
            self._save()

    def _shorten(self, s: str) -> str:
        return re.sub(r"\s+", " ", s[: self.max_label_length]).strip()

    def _save(self) -> None:
        d = {"clipboard_history": [
            {"type": "string", "data": s}
            for s, _label in self.items()
            if len(s) <= self.max_persist_data_size
        ]}
        try:
            os.makedirs(os.path.dirname(self.filename), exist_ok=True)
            with open(self.filename, "w", encoding="utf-8") as fd:
                json.dump(d, fd)
            self.dirty = False
        except OSError as e:
            logger.error(f"Saving clipboard history failed: {e}")

    def _load(self) -> None:
        self._items.clear()
        if os.path.exists(self.filename):
            try:
                with open(self.filename, encoding="utf-8") as fd:
                    d = json.load(fd)
                for item in reversed(d.get("clipboard_history", [])):
                    if item.get("type") == "string":
                        self.add_item(item["data"])
            except (OSError, ValueError) as e:
                logger.error(f"Loading clipboard history failed: {e}")
        self.dirty = False
