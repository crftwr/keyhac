"""The chooser (candidate) window - a secondary PuiKit window.

Replaces keyhac-mac's SwiftUI ChooserWindowView / keyhac-win's ListWindow:
a search field + filtered list.

**Two panes, one focus.** The filter field holds it to begin with, and while
it does the list shows no selection at all - it is a preview of what matches,
not a proposal. Down steps into the list; Up off its first row steps back
out, as does typing any character, so the field is never more than one
keystroke away. Enter chooses the selected row, or the top match when the
field still has the focus - typing a few letters and pressing Enter is the
flow this window exists for. Escape cancels. A click picks a row without
choosing it: the payload can be a destructive action.

Filtering goes through a pluggable ``Matcher`` (discussion #112): the
multi-word AND substring behaviour inherited from keyhac-mac, unioned with
Migemo so romaji finds Japanese (issue #106), and replaceable per source -
``WildcardMatcher`` restores 1.x's ``*`` / ``?``. Rows are ``Candidate``
objects internally; the ``(icon, label, *payload)`` tuples
``ChooserAction.list_items`` returns are adapted on the way in and handed
back untouched on selection.

**The window does not take OS keyboard focus** (discussion #112). It used to,
and that was never a decision anybody made: it is what a secondary PuiKit
window does by default. Three things followed from it, all of which go away
here - the console window came to the front alongside the chooser, because
activation is app-scoped and there is no way to activate without it (macOS 26
refuses ``activateWithOptions:`` for self-activation); reopening the chooser
could jump to another Space, because the OS follows the app's frontmost
window; and pasting needed a settle delay, because the target application had
to be deactivated and reactivated around it.

Not taking focus is not the same as not being clickable, and on macOS it takes
a specific window kind to be both. A borderless window cannot become key, but
a *click* on it still activates the application - which deactivated the window
underneath and left the paste with nowhere to go. The window is therefore a
non-activating panel that takes key status only on demand (puikit PR #126):
clicks reach it, the application is never activated, and the target keeps its
focus, its caret and its selection. On Windows ``WS_EX_NOACTIVATE`` already
refuses both, so the flags are inert there.

The keystrokes arrive through the key hook instead - see
``keyhac.ui.keyroute`` for that route and, importantly, for what it cannot
carry. The one real loss is IME composition, which is why Migemo is now part
of the default matcher rather than an option: for a localised list, romaji
matching is what makes the filter reach the rows at all.

A source that genuinely needs an input method in the filter field asks for
``activates=True`` and gets the old behaviour back, with the old costs.
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

    matcher: how the filter text is matched against the candidates.

    activates: whether the window takes OS keyboard focus.  The default is
    not to - see the module docstring.  A non-activating window is built as
    a PuiKit non-activating panel that only takes key status on demand:
    clicks reach it, but the application underneath keeps its focus, its
    caret and its selection, and its application is never brought forward.

    center_on: a screen rect (x, y, w, h) to center the window on - the
    focused window's frame (issue #4); clamp_to keeps the result on the
    given screen rect. Both are in puikit's portable screen coordinates
    (top-left origin), the same space keyhac's Window/WindowProvider report."""

    def __init__(self, backend, items, on_selected=None, on_canceled=None,
                 title="Keyhac", center_on=None, clamp_to=None, matcher=None,
                 activates=False):
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
                              activates=activates,
                              # The panel's mask forces a title bar; frameless
                              # hides it and restores content == frame.
                              frameless=not activates,
                              # macOS: an NSPanel that clicks reach without
                              # activating us or taking the target's keyboard
                              # (puikit PR #126).  No-op on Windows, where
                              # WS_EX_NOACTIVATE already refuses both.
                              nonactivating_panel=not activates,
                              becomes_key_on_demand=not activates))
        if center_on is not None:
            self._center_on(center_on, clamp_to)
        # Install the event handler BEFORE binding the Panel so it stays ours.
        self.window.on_event = self._on_event
        self.window.on_close = self._on_user_close

        # width is a cap, not a request: TextEdit draws at most `width` base
        # units of its flex slot, so pass the full window width to let the
        # field fill whatever the layout resolves (the window is not resizable).
        self._edit = TextEdit(text="", on_change=self._on_filter_change, width=72)
        self._list = ListView(self._labels(), ellipsis="…", elide_where="end",
                              allow_no_selection=True,
                              on_select=self._on_row_clicked)

        # Kept, not inlined: the list is nested inside it, and focus is marked
        # one level at a time - see _focus_list.
        self._frame = Frame(VSplit(Item(self._list, weight=1)),
                            margin_px=_LIST_PAD_PX)

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
            Item(self._frame, weight=1),
            gap=0.3,
        )
        self._page = LayoutView(page, margin_px=_MARGIN_PX)
        self.panel.set_layout(VSplit(Item(self._page, weight=1)))
        self.panel.focus(self._page)
        self._focus_edit()
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

    # --- focus ------------------------------------------------------------
    #
    # Two panes, one at a time.  While the field has the focus the list shows
    # *no* selection - it is a preview of what matches, not a proposal - and
    # Down steps into it.  Up off the first row steps back out, as does
    # typing, so the field is never more than one keystroke away.

    @property
    def in_list(self) -> bool:
        """Whether the list has the focus (rather than the filter field)."""
        return self._list.selected >= 0

    def _focus_edit(self) -> None:
        self._page.set_focused(self._edit)
        self._list.selected = -1

    def _focus_list(self, index: int = 0) -> None:
        if not self._filtered:
            return
        # A container marks *its own* child as focused, and a child is focused
        # only if every container above it is too.  The list sits inside the
        # Frame, so the page has to focus the frame and the frame the list -
        # naming the list to the page marks nothing, and the selection then
        # draws in the muted unfocused colour (grey) instead of the accent.
        self._page.set_focused(self._frame)
        self._frame.set_focused(self._list)
        self._list.selected = index

    def _on_row_clicked(self, index: int, _label) -> None:
        """A click on a row moves the focus into the list along with the
        selection.  It deliberately does not confirm: the payload can be a
        destructive action, so choosing stays an explicit Enter."""
        self._focus_list(index)

    def _on_filter_change(self, text: str) -> None:
        # The query is compiled once here, not once per candidate: Migemo's
        # whole cost is in building its alternation regex (discussion #112).
        match = self._matcher.compile(text)
        self._filtered = [c for c in self._items if match.hit(c.match_text)]
        self._list.set_items(self._labels())
        # A changed query re-proposes nothing: the focus is in the field, so
        # the list goes back to showing no selection.
        self._list.selected = -1

    def _on_event(self, event) -> None:
        if event.type is EventType.KEY:
            if event.key == "escape":
                self._finish(None, 0)
                return
            if event.key == "enter":
                # With the focus in the field nothing is selected, and Enter
                # still takes the top match - typing a few letters and
                # pressing Enter is the flow this window exists for.
                index = self._list.selected if self.in_list else 0
                if 0 <= index < len(self._filtered):
                    mod = 0
                    for name in event.modifiers:
                        mod |= _EVENT_MODKEYS.get(name, 0)
                    self._finish(self._filtered[index], mod)
                return
            if event.key in ("up", "down", "pageup", "pagedown"):
                self._navigate(event.key)
                self.panel.render()
                return
            if self.in_list and event.char:
                # Typing anywhere goes to the field, which means leaving the
                # list first - then the character is dispatched as usual.
                self._focus_edit()
        self.panel.dispatch_event(event)
        self.panel.render()

    def _navigate(self, key: str) -> None:
        if not self._filtered:
            return
        if not self.in_list:
            # Only forward keys step into the list; Up in the field does
            # nothing, so a stray Up cannot jump to the bottom of the list.
            if key in ("down", "pagedown"):
                self._focus_list(0)
            return
        delta = {"up": -1, "down": 1, "pageup": -10, "pagedown": 10}[key]
        index = self._list.selected + delta
        if index < 0:
            # Off the top of the list is back to the field, whether it was one
            # row up or a whole page.
            self._focus_edit()
            return
        self._list.selected = min(len(self._filtered) - 1, index)

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
