"""The chooser (candidate) window - a secondary PuiKit window.

Replaces keyhac-mac's SwiftUI ChooserWindowView / keyhac-win's ListWindow:
a search field + filtered list. Multi-word AND substring filtering (the
keyhac-mac behavior). Up/Down navigate, Enter chooses, Escape cancels.
"""

from puikit import Panel, WindowStyle
from puikit.event import EventType
from puikit.layout import HSplit, Item, VSplit
from puikit.widgets import Label, ListView, TextEdit

from keyhac.core.const import (
    MODKEY_ALT, MODKEY_CMD, MODKEY_CTRL, MODKEY_SHIFT, MODKEY_WIN,
)
from keyhac.core import log

logger = log.getLogger("Chooser")

_EVENT_MODKEYS = {
    "shift": MODKEY_SHIFT, "ctrl": MODKEY_CTRL, "alt": MODKEY_ALT,
    "cmd": MODKEY_CMD, "win": MODKEY_WIN,
}


class ChooserWindow:
    """items are tuples: (icon, label, *payload)."""

    def __init__(self, backend, items, on_selected=None, on_canceled=None,
                 title="Keyhac"):
        self._items = list(items)
        self._filtered = list(self._items)
        self._on_selected = on_selected
        self._on_canceled = on_canceled
        self._done = False

        self.window = backend.create_window(
            72, 20, title=title, style=WindowStyle(topmost=True, resizable=False))
        # Install the event handler BEFORE binding the Panel so it stays ours.
        self.window.on_event = self._on_event
        self.window.on_close = self._on_user_close

        self._edit = TextEdit(text="", on_change=self._on_filter_change, width=60)
        self._list = ListView(self._labels(), ellipsis="…", elide_where="end")

        self.panel = Panel(backend, window=self.window)
        self.panel.set_layout(VSplit(
            Item(HSplit(
                Item(Label("🔍"), size="content"),
                Item(self._edit, weight=1),
                gap=1,
            ), size="content"),
            Item(self._list, weight=1),
            gap=0,
        ))
        self.panel.focus(self._edit)
        self.panel.render()

    def _labels(self):
        return [f"{item[0]} {item[1]}" if item[0] else item[1]
                for item in self._filtered]

    def _on_filter_change(self, text: str) -> None:
        words = [w for w in text.lower().split() if w]
        self._filtered = [
            item for item in self._items
            if all(w in item[1].lower() for w in words)
        ]
        self._list.set_items(self._labels())
        self._list.selected = 0

    def _on_event(self, event) -> None:
        if event.type is EventType.KEY:
            if event.key == "escape":
                self._finish(None, 0)
                return
            if event.key == "enter":
                index = self._list.selected
                if 0 <= index < len(self._filtered):
                    mod = 0
                    for name in event.modifiers:
                        mod |= _EVENT_MODKEYS.get(name, 0)
                    self._finish(self._filtered[index], mod)
                return
            if event.key in ("up", "down", "pageup", "pagedown"):
                delta = {"up": -1, "down": 1, "pageup": -10, "pagedown": 10}[event.key]
                if self._filtered:
                    self._list.selected = max(
                        0, min(len(self._filtered) - 1, self._list.selected + delta))
                self.panel.render()
                return
        self.panel.dispatch_event(event)
        self.panel.render()

    def _on_user_close(self) -> None:
        if not self._done:
            self._done = True
            if self._on_canceled is not None:
                self._on_canceled()

    def _finish(self, item, modifier_flags: int) -> None:
        if self._done:
            return
        self._done = True
        self.window.close()
        if item is None:
            if self._on_canceled is not None:
                self._on_canceled()
        elif self._on_selected is not None:
            self._on_selected(item, modifier_flags)
