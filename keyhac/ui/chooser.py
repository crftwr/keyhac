"""The chooser (candidate) window - a secondary PuiKit window.

Replaces keyhac-mac's SwiftUI ChooserWindowView / keyhac-win's ListWindow:
a search field + filtered list. Multi-word AND substring filtering (the
keyhac-mac behavior). Up/Down navigate, Enter chooses, Escape cancels.
"""

from puikit import Panel, WindowStyle
from puikit.event import EventType
from puikit.layout import HSplit, Item, VSplit
from puikit.widgets import Label, LayoutView, ListView, TextEdit

from keyhac.core.const import (
    MODKEY_ALT, MODKEY_CMD, MODKEY_CTRL, MODKEY_SHIFT, MODKEY_WIN,
)
from keyhac.core import log
from keyhac.ui.frame import Frame

logger = log.getLogger("Chooser")

# The window's inner margin, shared by the magnifier<->field spacer so the
# magnifier sits the same distance from the window edge and the field; and
# the list frame's interior inset. Both collapse on a character grid.
_MARGIN_PX = 5
_LIST_PAD_PX = 3

_EVENT_MODKEYS = {
    "shift": MODKEY_SHIFT, "ctrl": MODKEY_CTRL, "alt": MODKEY_ALT,
    "cmd": MODKEY_CMD, "win": MODKEY_WIN,
}


class ChooserWindow:
    """items are tuples: (icon, label, *payload).

    center_on: a screen rect (x, y, w, h) to center the window on - the
    focused window's frame (issue #4); clamp_to keeps the result on the
    given screen rect. Both are in puikit's portable screen coordinates
    (top-left origin), the same space keyhac's Window/WindowProvider report."""

    def __init__(self, backend, items, on_selected=None, on_canceled=None,
                 title="Keyhac", center_on=None, clamp_to=None):
        self._items = list(items)
        self._filtered = list(self._items)
        self._on_selected = on_selected
        self._on_canceled = on_canceled
        self._done = False

        self.window = backend.create_window(
            72, 20, title=title,
            # tool: a transient picker gets no taskbar button (no-op on macOS)
            style=WindowStyle(topmost=True, resizable=False, tool=True))
        if center_on is not None:
            self._center_on(center_on, clamp_to)
        # Install the event handler BEFORE binding the Panel so it stays ours.
        self.window.on_event = self._on_event
        self.window.on_close = self._on_user_close

        # width is a cap, not a request: TextEdit draws at most `width` base
        # units of its flex slot, so pass the full window width to let the
        # field fill whatever the layout resolves (the window is not resizable).
        self._edit = TextEdit(text="", on_change=self._on_filter_change, width=72)
        self._list = ListView(self._labels(), ellipsis="…", elide_where="end")

        self.panel = Panel(backend, window=self.window)
        # align="center" sits the magnifier on the field's text line (the field
        # box is taller than one text line on pixel backends); the page margin
        # and the search-row/list gap collapse to nothing on a character grid.
        page = VSplit(
            Item(HSplit(
                Item(Label("🔍"), size="content", align="center"),
                Item(Label(""), size_px=_MARGIN_PX),
                Item(self._edit, weight=1),
                gap=0,
            ), size="content"),
            Item(Frame(VSplit(Item(self._list, weight=1)), margin_px=_LIST_PAD_PX),
                 weight=1),
            gap=0.3,
        )
        self._page = LayoutView(page, margin_px=_MARGIN_PX)
        self.panel.set_layout(VSplit(Item(self._page, weight=1)))
        self.panel.focus(self._page)
        self._page.set_focused(self._edit)
        self.panel.render()

    def _center_on(self, rect, clamp_to) -> None:
        frame = self.window.frame_px()
        if frame is None:
            return
        _x, _y, w, h = frame
        x = rect[0] + (rect[2] - w) / 2
        y = rect[1] + (rect[3] - h) / 2
        if clamp_to is not None:
            sx, sy, sw, sh = clamp_to
            x = max(sx, min(x, sx + sw - w))
            y = max(sy, min(y, sy + sh - h))
        self.window.move_to_px(x, y)

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

    def dismiss(self) -> None:
        """Close without invoking the callbacks (the owner is replacing it)."""
        if not self._done:
            self._done = True
            self.window.close()

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
