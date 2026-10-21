"""Clipboard history, ported from keyhac-mac keyhac_clipboard.py.

Differences from the original:
- backed by the portable ClipboardProvider instead of keyhac_core.Clipboard
- saving is debounced (dirty flag + flush()) instead of rewriting the JSON
  file on every copy (the upstream FIXME); main() flushes periodically and
  on exit.
- an entry is a ClipboardSnapshot, not only its text: rich text, an image or
  files survive being chosen from the history (keyhac-mac kept every
  pasteboard type in-session; this keeps a fixed set - see ClipboardSnapshot
  for why).  An image-only copy, which keyhac-mac dropped for having no
  string, is an entry of its own.
- what the clipboard holds *now* is tracked apart from the entries, so that
  content the history does not keep (concealed, or nothing it can read) can
  still be offered - and pasted untouched.
"""

import collections
import hashlib
import json
import os
import re
import time

from keyhac.platform.base import ClipboardProvider, ClipboardSnapshot
from keyhac.core import log, permissions

logger = log.getLogger("Clipboard")

#: What `current()` answers when the clipboard holds something the history
#: did not record: marked concealed by the copying app, or in no format the
#: history keeps.
CONCEALED = "concealed"
UNSUPPORTED = "unsupported"


class ClipboardEntry:
    """One history entry: the snapshot and the label it is listed under.

    lazydocs: ignore
    """

    __slots__ = ("key", "snapshot", "label", "time")

    def __init__(self, key, snapshot: ClipboardSnapshot, label: str,
                 when: float = None):
        self.key = key
        self.snapshot = snapshot
        self.label = label
        #: When it was copied (time.time()); None for one loaded from disk,
        #: which records no time.
        self.time = when

    @property
    def text(self) -> str | None:
        return self.snapshot.text


class ClipboardHistory:
    """Automatically captures historical clipboard content.

    Text is kept across restarts, and so are copied files (as their paths).
    Rich text and images are kept for the session: choosing such an entry
    puts the formatting or the image back.

    Content the copying app marked as concealed or transient - what password
    managers do with a copied password - is never recorded.

    Reached from a configuration as ``keymap.clipboard_history``, and shown by
    the ShowClipboardHistory action.

    Attributes:
        max_items: Maximum entries kept (default 1000).
        max_label_length: Maximum length of item labels (default 4096).
        max_data_size: Maximum size of a single captured format - larger
            ones are dropped, keeping the rest (default 10 MB).
        max_persist_data_size: Maximum size of an item written to disk
            (default 64 KB).
        max_session_data_size: Total size of the rich text and images kept
            in memory.  Past it, the oldest entries fall back to their text,
            and images without one are forgotten (default 64 MB).
        persist: Whether the history is saved across restarts (default True;
            set False to keep it in memory only).
        filename: Where it is saved (default ~/.keyhac/clipboard.json).
    """

    max_items = 1000
    max_label_length = 4096
    max_data_size = 10 * 1024 * 1024
    max_persist_data_size = 64 * 1024
    max_session_data_size = 64 * 1024 * 1024

    def __init__(self, provider: ClipboardProvider, filename: str = None):
        """Created by Keyhac's bootstrap; configurations use
        keymap.clipboard_history.

        lazydocs: ignore
        """
        self._provider = provider
        self.filename = filename or os.path.expanduser("~/.keyhac/clipboard.json")
        self.persist = True
        self._entries: collections.OrderedDict = collections.OrderedDict()
        #: The key of the entry the clipboard holds, CONCEALED, UNSUPPORTED,
        #: or None (empty, or not looked at yet).
        self._current = None
        self.dirty = False
        self._load()

    # -- capture ------------------------------------------------------------

    def on_clipboard_changed(self) -> None:
        """Called (on the UI thread) when the provider detected a change.

        lazydocs: ignore
        """
        # Asked before anything is read: a password manager's copy must not
        # reach the history, in memory or on disk.
        if self._call("is_concealed", lambda: False):
            self._current = CONCEALED
            return
        snapshot = self._call(
            "get_snapshot", lambda size: ClipboardProvider.get_snapshot(self._provider, size),
            self.max_data_size)
        if snapshot is None:
            self._current = None
            return
        if snapshot.kind is None:
            self._current = UNSUPPORTED
            return
        key = self._add(snapshot)
        self._current = key if key is not None else UNSUPPORTED

    def items(self):
        """Iterate the history's text.

        Entries without text - an image, files copied in Explorer - are not
        included; the chooser lists them.

        Yields:
            (text, label) pairs, latest first.  The label is the text
            collapsed onto one line for display.
        """
        for entry in self.entries():
            if entry.text:
                yield entry.text, self._shorten(entry.text)

    def entries(self):
        """Every entry, latest first, as ClipboardEntry objects.

        lazydocs: ignore
        """
        return reversed(list(self._entries.values()))

    def add_item(self, s: str) -> None:
        """Add text to the history without touching the OS clipboard.

        Args:
            s: The text to add.  A duplicate moves to the front; anything
                larger than max_data_size is dropped.
        """
        self._add(ClipboardSnapshot(text=s))

    def set_current(self, s: str) -> None:
        """Set text to the OS clipboard and the front of the history.

        Args:
            s: The text to put on the clipboard.
        """
        key = self._add(ClipboardSnapshot(text=s))
        self._provider.set_text(s)
        if key is not None:
            self._current = key

    def get_current(self) -> str | None:
        """Get the most recent clipboard text.

        Returns:
            The newest history entry with text, or None when there is none.
        """
        for s, _label in self.items():
            return s
        return None

    def current(self):
        """What the clipboard holds now: a ClipboardEntry, CONCEALED,
        UNSUPPORTED, or None.

        lazydocs: ignore
        """
        if self._current in (CONCEALED, UNSUPPORTED, None):
            return self._current
        return self._entries.get(self._current)

    def restore(self, entry: ClipboardEntry) -> None:
        """Put an entry back on the OS clipboard, every format it kept, and
        move it to the front.

        lazydocs: ignore
        """
        self._call("set_snapshot",
                   lambda s: ClipboardProvider.set_snapshot(self._provider, s),
                   entry.snapshot)
        key = self._add(entry.snapshot)
        if key is not None:
            self._current = key

    def _call(self, name: str, fallback, *args):
        """The provider's `name`, or the base class's default for a provider
        that predates it (anything with get_text/set_text is a provider)."""
        method = getattr(self._provider, name, None)
        return method(*args) if method is not None else fallback(*args)

    # -- entries --------------------------------------------------------------

    def _key(self, snapshot: ClipboardSnapshot):
        if snapshot.files:
            return ("files", snapshot.files)
        if snapshot.kind == "image":
            digest = hashlib.sha1()
            for item in snapshot.items:
                for name in sorted(item):
                    digest.update(item[name])
            return ("image", digest.hexdigest())
        # By text, as it always was: copying the same words again, formatted
        # or not, moves one entry to the front rather than adding a second.
        return ("text", snapshot.text)

    def _label(self, snapshot: ClipboardSnapshot) -> str:
        if snapshot.files:
            return ", ".join(os.path.basename(p.rstrip("/\\")) or p
                             for p in snapshot.files)
        if snapshot.kind == "image":
            if snapshot.image_size:
                return "Image {} × {}".format(*snapshot.image_size)
            return "Image"
        label = self._shorten(snapshot.text or "")
        if snapshot.lossy and snapshot.kind == "text":
            # What the copy offered beyond its text is gone from this entry;
            # choosing the entry pastes the text alone.
            label += " (text only)"
        return label

    def _add(self, snapshot: ClipboardSnapshot):
        """Add a snapshot; the key it went in under, or None if dropped."""
        if snapshot.kind == "text" or snapshot.kind == "rich":
            if not snapshot.text or len(snapshot.text) > self.max_data_size:
                return None
        key = self._key(snapshot)
        if key in self._entries:
            del self._entries[key]
        self._entries[key] = ClipboardEntry(key, snapshot, self._label(snapshot),
                                            None if self._loading else time.time())
        while len(self._entries) > self.max_items:
            self._entries.popitem(last=False)
        self._fit_session_budget()
        self.dirty = True
        return key if key in self._entries else None

    def _fit_session_budget(self) -> None:
        """Past max_session_data_size, the oldest entries give up their rich
        formats: text stays, an image without text goes."""
        total = 0
        for key in reversed(list(self._entries)):
            entry = self._entries[key]
            size = entry.snapshot.nbytes()
            if not size:
                continue
            total += size
            if total <= self.max_session_data_size:
                continue
            reduced = entry.snapshot.without_items()
            if reduced.text or reduced.files:
                self._entries[key] = ClipboardEntry(key, reduced, self._label(reduced),
                                                    entry.time)
            else:
                del self._entries[key]

    # -- persistence ----------------------------------------------------------

    def flush(self) -> None:
        """Save if there are unsaved changes (call periodically and on exit).

        lazydocs: ignore
        """
        if self.dirty and self.persist:
            self._save()

    def _shorten(self, s: str) -> str:
        return re.sub(r"\s+", " ", s[: self.max_label_length]).strip()

    def _save(self) -> None:
        saved = []
        for entry in self.entries():
            snapshot = entry.snapshot
            if snapshot.files:
                item = {"type": "files", "data": list(snapshot.files)}
                if snapshot.text:
                    item["text"] = snapshot.text
                saved.append(item)
            elif (snapshot.text and snapshot.kind in ("text", "rich")
                    and len(snapshot.text) <= self.max_persist_data_size):
                saved.append({"type": "string", "data": snapshot.text})
        d = {"clipboard_history": saved}
        try:
            permissions.ensure_private_dir(os.path.dirname(self.filename))
            with permissions.open_private(self.filename) as fd:
                json.dump(d, fd)
            self.dirty = False
        except OSError as e:
            logger.error(f"Saving clipboard history failed: {e}")

    #: True while the saved history is being read back, whose entries carry
    #: no time of their own.
    _loading = False

    def _load(self) -> None:
        self._entries.clear()
        self._loading = True
        try:
            self._load_file()
        finally:
            self._loading = False
        self.dirty = False

    def _load_file(self) -> None:
        if os.path.exists(self.filename):
            try:
                with open(self.filename, encoding="utf-8") as fd:
                    d = json.load(fd)
                for item in reversed(d.get("clipboard_history", [])):
                    if item.get("type") == "string":
                        self.add_item(item["data"])
                    elif item.get("type") == "files" and item.get("data"):
                        self._add(ClipboardSnapshot(
                            kind="files", text=item.get("text"),
                            files=tuple(item["data"])))
            except (OSError, ValueError, TypeError) as e:
                logger.error(f"Loading clipboard history failed: {e}")
