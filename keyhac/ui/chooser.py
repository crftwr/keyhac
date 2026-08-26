"""The chooser (candidate) window - a secondary PuiKit window.

Replaces keyhac-mac's SwiftUI ChooserWindowView / keyhac-win's ListWindow:
a search field + filtered list. Up/Down navigate, Enter chooses, Escape
cancels.

Filtering goes through a pluggable ``Matcher`` (discussion #112), defaulting
to the multi-word AND substring behaviour inherited from keyhac-mac, so
nothing shipped changes unless a caller asks for something else - Migemo
(``matcher.with_migemo()``, issue #106) or 1.x's wildcards
(``WildcardMatcher``). Rows are ``Candidate`` objects internally; the
``(icon, label, *payload)`` tuples ``ChooserAction.list_items`` returns are
adapted on the way in and handed back untouched on selection.
"""

from puikit import Panel, WindowStyle
from puikit.event import EventType
from puikit.layout import HSplit, Item, VSplit
from puikit.widgets import Label, LayoutView, ListView, TextEdit

from keyhac.core.const import (
    MODKEY_ALT, MODKEY_CMD, MODKEY_CTRL, MODKEY_SHIFT, MODKEY_WIN,
)
from keyhac.core import log
from keyhac.core.candidate import Candidate
from keyhac.core.matcher import DEFAULT_MATCHER
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
    """items are (icon, label, *payload) tuples, or Candidate objects.

    matcher: how the filter text is matched against the candidates; the
    keyhac-mac substring behaviour by default.

    center_on: a screen rect (x, y, w, h) to center the window on - the
    focused window's frame (issue #4); clamp_to keeps the result on the
    given screen rect. Both are in puikit's portable screen coordinates
    (top-left origin), the same space keyhac's Window/WindowProvider report."""

    def __init__(self, backend, items, on_selected=None, on_canceled=None,
                 title="Keyhac", center_on=None, clamp_to=None, matcher=None,
                 activates=True):
        self._items = [Candidate.from_item(item) for item in items]
        self._matcher = matcher if matcher is not None else DEFAULT_MATCHER
        self._filtered = list(self._items)
        self._on_selected = on_selected
        self._on_canceled = on_canceled
        self._done = False
        self._grabbed = False

        self.window = backend.create_window(
            72, 20, title=title,
            # tool: a transient picker gets no taskbar button (no-op on macOS)
            style=WindowStyle(topmost=True, resizable=False, tool=True,
                              activates=activates))
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

        if not activates:
            self._grab_keys()

    # --- non-activating input (spike - discussion #112) -------------------

    def _grab_keys(self) -> None:
        """Take the hook's keystrokes, since the window has no OS focus.

        See ``keyhac.ui.keyroute`` for what that route can carry - ASCII and
        the named keys, and no input method at all.
        """
        from keyhac.core.keymap import Keymap
        from keyhac.ui.keyroute import to_event

        keymap = Keymap.get_instance()
        if keymap is None:
            logger.error("No Keymap: a non-activating chooser has no key route.")
            return

        def handler(key):
            event = to_event(key)
            if event is not None:
                self._on_event(event)

        keymap.push_modal_input(handler)
        self._grabbed = True

    def _release_keys(self) -> None:
        if not self._grabbed:
            return
        self._grabbed = False
        from keyhac.core.keymap import Keymap
        keymap = Keymap.get_instance()
        if keymap is not None:
            keymap.pop_modal_input()

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
        return [candidate.label for candidate in self._filtered]

    def _on_filter_change(self, text: str) -> None:
        # The query is compiled once here, not once per candidate: Migemo's
        # whole cost is in building its alternation regex (discussion #112).
        match = self._matcher.compile(text)
        self._filtered = [c for c in self._items if match.hit(c.match_text)]
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
            self._release_keys()
            if self._on_canceled is not None:
                self._on_canceled()

    def dismiss(self) -> None:
        """Close without invoking the callbacks (the owner is replacing it)."""
        if not self._done:
            self._done = True
            self._release_keys()
            self.window.close()

    def _finish(self, candidate, modifier_flags: int) -> None:
        if self._done:
            return
        self._done = True
        self._release_keys()
        self.window.close()
        if candidate is None:
            if self._on_canceled is not None:
                self._on_canceled()
        elif self._on_selected is not None:
            # Tuple sources get their own tuple back, so every ChooserAction
            # written against the pre-Candidate API keeps working.
            payload = candidate.payload
            self._on_selected(
                payload if isinstance(payload, tuple) else candidate,
                modifier_flags)
