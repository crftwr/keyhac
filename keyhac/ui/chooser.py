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

import time

from puikit import Panel, Style, WindowStyle
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

#: How long one streaming slice may hold the main thread.  Small enough that a
#: keystroke lands between slices; large enough that a cheap source finishes in
#: one.
_SLICE_SECONDS = 0.002

#: The progress note is context, not content: quieter than the query.
_PROGRESS_STYLE = Style(fg=(130, 130, 140))

_EVENT_MODKEYS = {
    "shift": MODKEY_SHIFT, "ctrl": MODKEY_CTRL, "alt": MODKEY_ALT,
    "cmd": MODKEY_CMD, "win": MODKEY_WIN,
}


class ChooserWindow:
    """items are (icon, label, *payload) tuples, or Candidate objects.

    matcher: how the filter text is matched against the candidates.

    badge_of: optional `candidate -> str`, drawn quietly at the right of its
    row.  What the unified window uses to say which source a row came from,
    since with clipboard entries, windows and on-screen controls in one list
    a row without its provenance is a guess.  None (or a function returning
    "" for everything) draws plain rows.

    pending: an iterator of further rows, drained a slice at a time between
    renders.  None for a window whose rows are all already known.

    scopes: names of the scopes Tab / Shift-Tab move between, or None for a
    window with only one.  `on_scope(index)` is asked for the rows of the
    scope being moved to, as `(candidates, badge_of)`.  **The query survives
    the move** - that is the whole reason the switch is a key rather than a
    typed prefix, and a prefix could not do it without the user editing the
    front of what they had already typed.

    activates: whether the window takes OS keyboard focus.  The default is
    not to - see the module docstring.  A non-activating window asks PuiKit
    for ``overlay_input="mouse"``: clicks reach it, but the application
    underneath keeps its focus, its caret and its selection, and Keyhac is
    never brought forward.

    center_on: a screen rect (x, y, w, h) to center the window on - the
    focused window's frame (issue #4); clamp_to keeps the result on the
    given screen rect. Both are in puikit's portable screen coordinates
    (top-left origin), the same space keyhac's Window/WindowProvider report."""

    def __init__(self, backend, items, on_selected=None, on_canceled=None,
                 title="Keyhac", center_on=None, clamp_to=None, matcher=None,
                 activates=False, badge_of=None,
                 scopes=None, on_scope=None, pending=None):
        self._items = [Candidate.from_item(item) for item in items]
        # Rows still being produced, drained in slices between renders.  None
        # is the ordinary case: a source that returns a list has nothing left
        # to give, and nothing about it should become asynchronous.
        self._pending = pending
        self._matcher = matcher if matcher is not None else DEFAULT_MATCHER
        self._match = self._matcher.compile("")
        self._filtered = list(self._items)
        self._on_selected = on_selected
        self._on_canceled = on_canceled
        self._done = False
        self._grabbed = False
        self._streaming = False

        self.window = backend.create_window(
            72, 20, title=title,
            # tool: a transient picker gets no taskbar button (no-op on macOS)
            style=WindowStyle(topmost=True, resizable=False, tool=True,
                              activates=activates,
                              # The panel's mask forces a title bar; frameless
                              # hides it and restores content == frame.
                              frameless=not activates,
                              # Clicks reach the popup without activating us
                              # and without taking the target's keyboard, so
                              # the paste still has somewhere to land (puikit
                              # PR #126).  Inert on Windows, where
                              # WS_EX_NOACTIVATE already refuses both.
                              overlay_input="mouse" if not activates else "none"))
        if center_on is not None:
            self._center_on(center_on, clamp_to)
        # Install the event handler BEFORE binding the Panel so it stays ours.
        self.window.on_event = self._on_event
        self.window.on_close = self._on_user_close

        # width is a cap, not a request: TextEdit draws at most `width` base
        # units of its flex slot, so pass the full window width to let the
        # field fill whatever the layout resolves (the window is not resizable).
        self._edit = TextEdit(text="", on_change=self._on_filter_change, width=72)
        self._scopes = list(scopes) if scopes else []
        self._on_scope = on_scope
        self._scope = 0
        # What each scope had read, kept for the life of this window.  Safe
        # because the dismissal watch closes the window the moment the front
        # window changes, so nothing a scope read can have gone stale while
        # this window is still up - and a scope left half-read keeps its
        # generator, so tabbing back resumes rather than starting again.
        self._scope_cache = {}
        self._badge_of = badge_of
        # With scopes, the row widget is used throughout even where the
        # current scope draws no badge: switching would otherwise have to
        # swap the list widget itself, and the badge is the first thing a
        # merged scope needs anyway.
        if badge_of is None and not self._scopes:
            self._list = ListView(self._labels(), ellipsis="…",
                                  elide_where="end", allow_no_selection=True,
                                  on_select=self._on_row_clicked)
        else:
            # Rows become widgets so each can carry its source beside it; the
            # widget does its own eliding, which plain string rows get from
            # ListView (see keyhac.ui.candidate_row).
            from keyhac.ui.candidate_row import CandidateRow
            self._list = ListView(
                self._rows(), allow_no_selection=True,
                on_select=self._on_row_clicked,
                row_factory=lambda row: CandidateRow(*row))

        # Kept, not inlined: the list is nested inside it, and focus is marked
        # one level at a time - see _focus_list.
        self._frame = Frame(VSplit(Item(self._list, weight=1)),
                            margin_px=_LIST_PAD_PX)

        self.panel = Panel(backend, window=self.window)
        # align="center" sits the magnifier on the field's text line (the field
        # box is taller than one text line on pixel backends); the page margin
        # and the search-row/list gap collapse to nothing on a character grid.
        from keyhac.ui.scope_switcher import ScopeSwitcher
        self._scope_label = ScopeSwitcher(
            self._scope_name(), on_switch=self._switch_clicked)
        # "Still reading", and how much of it so far.  Without this an
        # unfinished list is indistinguishable from a finished one, so a query
        # that has not matched *yet* reads as one that never will - which is
        # the whole of what makes a streaming source feel slow, rather than
        # the milliseconds.
        self._progress = Label("", style=_PROGRESS_STYLE)
        search_row = [
            Item(Label("🔍"), size="content", align="center"),
            Item(Label(""), size_px=_MARGIN_PX),
            Item(self._edit, weight=1),
            Item(Label(""), size_px=_MARGIN_PX),
            Item(self._progress, size="content", align="center"),
        ]
        if self._scopes:
            search_row.append(Item(Label(""), size_px=_MARGIN_PX))
            search_row.append(
                Item(self._scope_label, size="content", align="center"))
        page = VSplit(
            Item(HSplit(*search_row, gap=0), size="content"),
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

        # After the window is on screen, so streamed rows land in something
        # the user is already looking at rather than delaying its first paint.
        self._start_streaming()

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

    def _rows(self):
        badge = self._badge_of
        return [(c.label, (badge(c) or "") if badge else "")
                for c in self._filtered]

    def _items_for_list(self):
        plain = self._badge_of is None and not self._scopes
        return self._labels() if plain else self._rows()

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

    # --- streaming --------------------------------------------------------

    def _start_streaming(self) -> None:
        """Pump the pending rows in slices until there are none left."""
        if self._pending is None or self._streaming:
            return
        self._streaming = True
        if not self.panel.request_animation_ticks(self._drain):
            # A still backend registers nothing, so drain in one go rather
            # than leaving the rows on the floor.  Tests take this path.
            while self._drain():
                pass
            self._streaming = False

    def _drain(self) -> bool:
        """One slice.  Returns whether there is more to come.

        Time-boxed rather than counted: an accessibility call's cost varies by
        orders of magnitude between a menu item and a node inside a web area,
        so "twenty rows" is a different amount of frozen keyboard every time
        and "two milliseconds" is not.
        """
        if self._done or self._pending is None:
            self._streaming = False
            return False
        deadline = time.monotonic() + _SLICE_SECONDS
        arrived = []
        for candidate in self._pending:
            arrived.append(Candidate.from_item(candidate))
            if time.monotonic() >= deadline:
                break
        else:
            self._pending = None
        if arrived:
            self._append(arrived)
        self._show_progress()
        if self._pending is None:
            self._streaming = False
            return False
        return True

    def _show_progress(self) -> None:
        """Say whether the list is still filling, and how far it has got."""
        text = f"… {len(self._items)}" if self._pending is not None else ""
        if self._progress.text != text:
            self._progress.text = text
            self.panel.render()

    def _ranked(self, candidates) -> list:
        """Best first, and stable - so rows the query cannot tell apart keep
        the order their sources produced them in."""
        return sorted(candidates, key=lambda c: self._match.rank(c.match_text))

    def _append(self, arrived) -> None:
        """Add rows to a window that is still filling, keeping the filter
        applied and the best matches on top.

        **The row under the selection does not move.** Ranking wants to
        reorder and a list being chosen from must not shift, so what is kept
        is the *candidate*, not its index - it is found again wherever the
        new order puts it. The two rules only appear to conflict: while the
        filter field holds the focus nothing is selected at all (the list
        shows no row until Down steps into it), and that is exactly the
        window during which rows are still arriving. A changed query is
        different again and resets the selection deliberately.
        """
        self._items.extend(arrived)
        matched = [c for c in arrived if self._match.hit(c.match_text)]
        if not matched:
            return
        index = self._list.selected
        pinned = self._filtered[index] if 0 <= index < len(self._filtered) \
            else None
        offset = self._list.offset
        self._filtered = self._ranked(self._filtered + matched)
        self._list.set_items(self._items_for_list())
        # set_items drops the viewport, so put it back first; then move the
        # selection, which scrolls itself into view if the row actually went
        # somewhere. Restoring in the other order would leave the selection
        # off-screen whenever ranking moved it.
        self._list.offset = offset
        if pinned is not None:
            self._list.selected = self._filtered.index(pinned)
        self.panel.render()

    def _on_filter_change(self, text: str) -> None:
        # The query is compiled once here, not once per candidate: Migemo's
        # whole cost is in building its alternation regex (discussion #112).
        # Kept, too, so an arriving slice re-uses it rather than paying that
        # cost again on every frame of a streaming source.
        self._match = self._matcher.compile(text)
        self._filtered = self._ranked(
            c for c in self._items if self._match.hit(c.match_text))
        self._list.set_items(self._items_for_list())
        # A changed query re-proposes nothing: the focus is in the field, so
        # the list goes back to showing no selection.
        self._list.selected = -1

    # --- scopes -----------------------------------------------------------

    def _scope_name(self) -> str:
        return self._scopes[self._scope] if self._scopes else ""

    def _switch_clicked(self, delta: int) -> None:
        self.switch_scope(delta)
        self.panel.render()

    def switch_scope(self, delta: int) -> None:
        """Move `delta` steps along the scope cycle, keeping the query.

        lazydocs: ignore
        """
        if len(self._scopes) < 2 or self._on_scope is None:
            return
        self._scope_cache[self._scope] = (self._items, self._pending,
                                          self._badge_of)
        self._scope = (self._scope + delta) % len(self._scopes)
        cached = self._scope_cache.get(self._scope)
        if cached is not None:
            # Including a generator that was mid-walk: it resumes where it
            # stopped, so cycling through the scopes does not restart the
            # expensive ones.
            self._items, self._pending, self._badge_of = cached
            self._show_progress()
        else:
            rows, pending, badge_of = self._on_scope(self._scope)
            self._items = [Candidate.from_item(row) for row in rows]
            self._pending = pending
            self._badge_of = badge_of
        self._scope_label.name = self._scope_name()
        # The rows are different ones, so nothing is proposed and the focus
        # goes back to the field - the same rule a changed query follows.
        self._focus_edit()
        self._on_filter_change(self._edit.text)
        self._start_streaming()

    def _on_event(self, event) -> None:
        if event.type is EventType.KEY:
            if event.key == "tab":
                # Intercepted before the Panel, which would otherwise spend it
                # on focus traversal between the field and the list.
                self.switch_scope(-1 if "shift" in event.modifiers else 1)
                self.panel.render()
                return
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
            # Always the Candidate. Handing a tuple-derived row back as its
            # tuple - which this used to do, for the pre-Candidate API - meant
            # the window decided how the row would be routed, and a source
            # that legitimately yields tuples had its own on_chosen skipped.
            # Unwrapping is the caller's business; see ChooserAction._choose.
            self._on_selected(candidate, modifier_flags)
