"""The chooser (candidate) window - a secondary PuiKit window.

Replaces keyhac-mac's SwiftUI ChooserWindowView / keyhac-win's ListWindow:
a search field + filtered list.

**Two panes, one focus.** The filter field holds it to begin with, and while
it does the list shows no selection at all - it is a preview of what matches,
not a proposal. Down steps into the list; Up off its first row steps back
out, as does anything addressed to the query - typing a character, editing it
(Backspace, Delete) or moving its caret (Left/Right and their modifier forms)
- so the field is never more than one keystroke away, and that keystroke is
never spent getting there. Enter chooses the selected row, or the top match
when the field still has the focus - typing a few letters and pressing Enter
is the flow this window exists for. Escape cancels. A click picks a row without
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

import threading
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
from keyhac.ui import runtime
from keyhac.ui.frame import Frame, Separator
from keyhac.ui.grips import MIN_UNITS, EdgeResizer

logger = log.getLogger("Chooser")

# The window's inner margin, which collapses on a character grid.
_MARGIN_PX = 5

#: How far the two ornaments in the search row - the magnifier and the scope
#: switcher - stand off the window's *edge*, the page margin included.  The
#: gap to the field beside them stays `_MARGIN_PX`, and the difference is the
#: point: the field draws its own box with its own padding inside it, and the
#: border is drawn in the margin, so two sides that measure the same do not
#: look the same.  Both glyphs read as pushed against the frame until the
#: outer side is about twice the inner one.
_EDGE_GUTTER_PX = 10

#: The window's own edge, drawn a half pixel inside the frame - far enough in
#: that the stroke clears the corner the platform clips the window to, close
#: enough that the line *is* the outermost thing the window draws rather than
#: a second edge inside a rim of background.  The radius is the window's own,
#: less the inset, so the line stays concentric with that corner; how round
#: the corner is is the platform's fact and `WindowHandle.corner_radius_px`
#: is where it is kept.
_EDGE_INSET_PX = 0.5

#: The window's size before anything has been remembered, in base units, and
#: the ceiling a remembered one is clamped to - a settings file written by
#: hand, or on a much larger screen, must not open a window that does not fit
#: on this one.
_DEFAULT_SIZE = (72, 20)
_MAX_UNITS = (300, 100)

#: Where a resize is kept between invocations.  The window is rebuilt every
#: time it opens, so without this every resize is undone by the next press of
#: the key that opened it.  Size only: *where* the window goes is decided per
#: invocation from the window it opens over (issue #4).
_SIZE_KEY = "chooser_size"

#: How long one streaming slice may hold the main thread.  Small enough that a
#: keystroke lands between slices; large enough that a cheap source finishes in
#: one.
_SLICE_SECONDS = 0.002

#: How much a background worker accumulates before handing rows to the main
#: thread.  Whichever comes first: a full batch keeps a fast source from
#: crossing threads per row, the deadline keeps a slow one from looking
#: stalled.  30 ms is under a frame, so the list still fills visibly rather
#: than in one jump - which is what says the window is working.
_BATCH_MAX = 64
_BATCH_SECONDS = 0.030

#: The progress note is context, not content: quieter than the query.
_PROGRESS_STYLE = Style(fg=(130, 130, 140))

#: The outline drawn over the control a highlighted row stands for.  Loud on
#: purpose: it is answering "is this the one you meant?" across the whole
#: screen, over content it cannot predict.
_POINTER_STYLE = Style(fg=(255, 90, 90))
_POINTER_WIDTH = 3.0
_POINTER_RADIUS = 4.0

#: Keys that address the *query* rather than the list: they edit it or move
#: its caret, so pressing one in the list means the field, exactly as typing
#: does. Modifier forms arrive under these same names - Ctrl-Left is word-wise,
#: Shift-Left selects, Cmd-Left is the line start on macOS - so naming the bare
#: keys covers the derivatives with them. Home and End are deliberately absent:
#: the list uses those for its first and last row, which is what a list long
#: enough to need them wants them for.
_FIELD_KEYS = frozenset({"backspace", "delete", "left", "right"})

_EVENT_MODKEYS = {
    "shift": MODKEY_SHIFT, "ctrl": MODKEY_CTRL, "alt": MODKEY_ALT,
    "cmd": MODKEY_CMD, "win": MODKEY_WIN,
}


def _remembered_size() -> tuple:
    """The size the last resize left, or the default - clamped, because this
    comes off disk and a window too small to read or too large for the screen
    is one the user cannot undo from inside the window."""
    store = getattr(runtime, "settings", None)
    size = store.get(_SIZE_KEY) if store is not None else None
    try:
        width, height = int(size[0]), int(size[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return _DEFAULT_SIZE
    return (max(int(MIN_UNITS[0]), min(width, _MAX_UNITS[0])),
            max(int(MIN_UNITS[1]), min(height, _MAX_UNITS[1])))


def _remember_size(width: float, height: float) -> None:
    """Keep the size a resize ended on, for the next window (this one is
    thrown away when it closes).  Silently nothing while running headless,
    where there is no settings store to write to."""
    store = getattr(runtime, "settings", None)
    if store is not None:
        store.set(_SIZE_KEY, [round(width), round(height)])


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
                 scopes=None, on_scope=None, pending=None, background=None):
        self._items = [Candidate.from_item(item) for item in items]
        # Rows still being produced, drained in slices between renders.  None
        # is the ordinary case: a source that returns a list has nothing left
        # to give, and nothing about it should become asynchronous.
        self._pending = pending
        # Rows from sources that declared themselves safe off the main thread
        # (CandidateSource.background), walked on a worker and handed back in
        # batches. None is the ordinary case.
        self._background = background
        self._bg_stop = None
        self._matcher = matcher if matcher is not None else DEFAULT_MATCHER
        self._match = self._matcher.compile("")
        self._filtered = list(self._items)
        self._on_selected = on_selected
        self._on_canceled = on_canceled
        self._done = False
        self._grabbed = False
        self._streaming = False
        self._backend = backend
        #: The mark drawn over whatever the selection stands for, and the
        #: rectangle it is drawn at, so an unchanged selection redraws nothing.
        self._pointer = None
        self._pointing_at = None

        self.window = backend.create_window(
            *_remembered_size(), title=title,
            # tool: a transient picker gets no taskbar button (no-op on macOS)
            style=WindowStyle(topmost=True, resizable=False, tool=True,
                              activates=activates,
                              # The drag is the window's own (the magnifier),
                              # so the OS must not run one too: `frameless`
                              # only *hides* the title bar the panel mask
                              # forces, and AppKit goes on dragging the window
                              # by it - which is the top edge, where the
                              # resize gesture is. One press was moving the
                              # window and resizing it at the same time.
                              movable=activates,
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
        # units of its flex slot, so the cap is the widest window there can be
        # rather than the one this is - the window resizes, and a field still
        # capped at the width it opened with would stop short of its own box.
        self._edit = TextEdit(text="", on_change=self._on_filter_change,
                              width=_MAX_UNITS[0])
        # Dragging the window's own edge resizes it, since a frameless window
        # gets nothing from the window manager to drag (issue #117).
        self._resizer = EdgeResizer(self.window, on_resized=_remember_size,
                                    backend=backend)
        self._scopes = list(scopes) if scopes else []
        self._on_scope = on_scope
        self._scope = 0
        self._badge_of = badge_of
        # With scopes, the row widget is used throughout even where the
        # current scope draws no badge: switching would otherwise have to
        # swap the list widget itself, and the badge is the first thing a
        # merged scope needs anyway.
        # Every row is a widget, badge or no badge: it carries the source name
        # beside it where there is one, and the air before the icon where
        # there is not - and the list runs to the page's edge now, so a plain
        # string row would start against the window.  It does its own eliding
        # in exchange, which plain string rows got from ListView (see
        # keyhac.ui.candidate_row).
        from keyhac.ui.candidate_row import CandidateRow
        self._list = ListView(
            self._rows(), allow_no_selection=True,
            on_select=self._on_row_clicked,
            row_factory=lambda row: CandidateRow(*row))

        # A plain pane, not a frame: the list needs no box of its own.  Kept
        # rather than inlined, because focus is marked one level at a time -
        # see _focus_list - and because the list runs to the page's edges,
        # which is where its scrollbar then sits.
        self._pane = LayoutView(VSplit(Item(self._list, weight=1)),
                                margin_px=0)

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
        # The magnifier is where a title bar would have put the drag handle,
        # and a frameless window has no title bar to put one in (issue #117).
        from keyhac.ui.grips import DragHandle
        self._handle = DragHandle(self.window, "🔍", backend=backend)
        search_row = [
            Item(Label(""), size_px=_EDGE_GUTTER_PX - _MARGIN_PX),
            Item(self._handle, size="content", align="center"),
            Item(Label(""), size_px=_MARGIN_PX),
            Item(self._edit, weight=1),
            # No spacer of its own: the progress note is usually empty, and a
            # spacer that stayed behind it would widen the gap the switcher
            # keeps on its left.  It carries its own leading space instead.
            Item(self._progress, size="content", align="center"),
            Item(Label(""), size_px=_EDGE_GUTTER_PX - _MARGIN_PX),
        ]
        if self._scopes:
            search_row.insert(-1, Item(Label(""), size_px=_MARGIN_PX))
            search_row.insert(
                -1, Item(self._scope_label, size="content", align="center"))
        page = VSplit(
            Item(HSplit(*search_row, gap=0), size="content"),
            # The one line worth drawing between the two: what the list's own
            # frame was for, without the three sides of it that only repeated
            # the window's border a few pixels further in.
            Item(Separator(), size="content"),
            Item(self._pane, weight=1),
            gap=0.3,
        )
        # Frame, not LayoutView: the window is frameless, so the only edge it
        # can have is one it draws.  Without it the popup reads as a floating
        # rectangle of text over a light background rather than as a window.
        # Rounded to whatever the platform clips this window's corners to,
        # because a square line drawn at the window's extent loses exactly
        # those four corners to the clip - which is what it did.
        self._page = Frame(
            page, margin_px=_MARGIN_PX, inset_px=_EDGE_INSET_PX,
            radius_px=max(0.0, self.window.corner_radius_px - _EDGE_INSET_PX))
        self.panel.set_layout(VSplit(Item(self._page, weight=1)))
        self.panel.focus(self._page)
        self._focus_edit()
        self.panel.render()

        if not activates:
            self._grab_keys()

        # After the window is on screen, so streamed rows land in something
        # the user is already looking at rather than delaying its first paint.
        self._start_streaming()
        self._start_background()

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

    def _rows(self):
        badge = self._badge_of
        return [(c.label, (badge(c) or "") if badge else "")
                for c in self._filtered]

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
        self._point_at_selection()

    def _focus_list(self, index: int = 0) -> None:
        if not self._filtered:
            return
        # A container marks *its own* child as focused, and a child is focused
        # only if every container above it is too.  The list sits inside the
        # Frame, so the page has to focus the frame and the frame the list -
        # naming the list to the page marks nothing, and the selection then
        # draws in the muted unfocused colour (grey) instead of the accent.
        self._page.set_focused(self._pane)
        self._pane.set_focused(self._list)
        self._list.selected = index
        self._point_at_selection()

    def _on_row_clicked(self, index: int, _label) -> None:
        """A click on a row moves the focus into the list along with the
        selection.  It deliberately does not confirm: the payload can be a
        destructive action, so choosing stays an explicit Enter."""
        self._focus_list(index)

    # --- pointing at the real thing ---------------------------------------

    def _point_at_selection(self) -> None:
        """Outline the thing the highlighted row stands for, on the screen.

        Discussion #112's argument for this is the row that cannot describe
        itself: an icon-only control listed as its tooltip, or by its role and
        position, is a row whose text does not settle *which* one it is. The
        only way to confirm the target is to light the real one up - and that
        is also the difference between a list of controls and a list of names
        that happen to be controls.

        Only rows that carry a screen rectangle can be pointed at, which is
        the accessibility ones; a clipboard entry has no place on screen and
        simply clears the mark.
        """
        rect = None
        index = self._list.selected
        if 0 <= index < len(self._filtered):
            rect = self._filtered[index].rect
        if rect == self._pointing_at:
            return
        self._pointing_at = rect
        if self._pointer is not None:
            self._pointer.close()
            self._pointer = None
        if rect is None:
            return
        x, y, w, h = rect
        try:
            self._pointer = self._backend.mark_screen(
                x, y, w, h, style=_POINTER_STYLE,
                line_width=_POINTER_WIDTH, radius=_POINTER_RADIUS,
                # The outline lands wherever the control is, which is not
                # where the eye is - it is on the row that was just
                # highlighted, on the other side of the screen.
                flash=True)
        except Exception:
            logger.debug("This platform cannot point at a control.")

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

    # --- the worker ------------------------------------------------------

    def _start_background(self) -> None:
        """Walk the background sources on a worker, delivering in batches.

        The tick drain above exists because a main-thread generator must hand
        the thread back; a worker has nothing to hand back, so it simply runs
        and the window fills in the time the walk actually takes. Measured on
        a live menu bar: the same walk that reached the screen over ten
        seconds arrives in a fifth of one.

        What makes this safe is not a guess. An accessibility read into
        *another* process releases the GIL while it waits, so a target that
        has stopped answering blocks this thread and nothing else - measured
        against a deliberately frozen application, the main thread's
        throughput did not move while a call sat in it for 1.5 s. And the
        contention runs the right way round: a main thread busy with Python
        starves the worker rather than the other way about, so a keystroke
        does not queue behind the walk. What it costs the main thread,
        measured with the walk running flat out, is 0.13 ms on a hook-sized
        piece of work.

        One worker, not several. The cost of a walk is the marshalling in
        *this* process rather than the wait in the other one, so six walks on
        six threads took as long as six in a row - the GIL is the ceiling,
        and more threads only divide it.
        """
        if self._background is None or self._bg_stop is not None:
            return
        if not self.panel.dispatches_to_main_thread:
            # No other thread to hand rows back to, so nothing is gained by
            # putting them on one - and a worker would have nowhere to land.
            # Drain inline, which is what the tick drain does on a still
            # backend for the same reason. Tests take this path.
            generator, self._background = self._background, None
            rows = [Candidate.from_item(c) for c in generator]
            if rows:
                self._append(rows)
            self._show_progress()
            return
        stop = self._bg_stop = threading.Event()
        generator = self._background

        def run():
            batch, deadline = [], time.monotonic() + _BATCH_SECONDS
            try:
                for candidate in generator:
                    if stop.is_set():
                        return
                    batch.append(candidate)
                    now = time.monotonic()
                    if len(batch) >= _BATCH_MAX or now >= deadline:
                        self._deliver(batch, stop)
                        batch, deadline = [], now + _BATCH_SECONDS
            except Exception:
                logger.exception("A background candidate source failed.")
            finally:
                # The last batch and the "no more coming" both land here, so
                # an abandoned walk still takes the progress note down.
                self._deliver(batch, stop, done=True)

        threading.Thread(target=run, name="keyhac-candidates",
                         daemon=True).start()

    def _deliver(self, batch, stop, done: bool = False) -> None:
        """Hand one batch to the main thread, which is the only place a row
        may be added to the window.

        Batched rather than row by row for the reason the tick drain is
        time-boxed rather than counted: `_append` re-ranks and redraws
        everything it has, so paying that per row makes filling the window
        cost more than reading it did.
        """
        def land():
            if self._done or stop.is_set():
                return
            if batch:
                self._append([Candidate.from_item(c) for c in batch])
            if done:
                self._background = None
            # Every batch, not only the last: the count is what says the
            # window is still filling, and a note that only appears once the
            # filling is over says nothing at all.
            self._show_progress()

        self._backend.call_on_main_thread(land)

    def _stop_background(self) -> None:
        """Abandon the walk. The worker notices at its next row; nothing it
        delivers after this lands, because `land()` checks the same flag."""
        if self._bg_stop is not None:
            self._bg_stop.set()
            self._bg_stop = None

    def _show_progress(self) -> None:
        """Say whether the list is still filling, and how far it has got."""
        # The leading space is the note's own gap from the field: an empty
        # note then takes no width at all, and the switcher beside it keeps
        # the gutter it is supposed to have.
        filling = self._pending is not None or self._background is not None
        text = f"  … {len(self._items)}" if filling else ""
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
        self._list.set_items(self._rows())
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
        self._list.set_items(self._rows())
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
        self._scope = (self._scope + delta) % len(self._scopes)
        # Asked afresh, but not *read* afresh: the caller remembers what each
        # source produced, keyed on the source itself, so returning to a scope
        # re-concatenates rather than re-walks - and a source shared with
        # another scope is not read a second time at all.
        # Before anything else: the worker is walking a generator this is
        # about to hand to someone else, and two consumers of one generator
        # is not a race that can be tidied up afterwards.
        self._stop_background()
        rows, pending, badge_of, background = self._on_scope(self._scope)
        self._items = [Candidate.from_item(row) for row in rows]
        self._pending = pending
        self._background = background
        self._badge_of = badge_of
        self._show_progress()
        self._scope_label.name = self._scope_name()
        # The rows are different ones, so nothing is proposed and the focus
        # goes back to the field - the same rule a changed query follows.
        self._focus_edit()
        self._on_filter_change(self._edit.text)
        self._start_streaming()
        self._start_background()

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
            if event.key in ("up", "down", "pageup", "pagedown") or (
                    self.in_list and event.key in ("home", "end")):
                # Home/End only once the list has the focus - in the field
                # they are the caret's, and the field keeps them. Routing them
                # here rather than letting the ListView answer them itself is
                # what keeps the outline on screen in step: every move of the
                # selection has to go through _navigate, or the mark stays on
                # the row the selection just left.
                self._navigate(event.key)
                self.panel.render()
                return
            if self.in_list and (event.char or event.key in _FIELD_KEYS):
                # Anything addressed to the query goes to the field, which
                # means leaving the list first - then the key is dispatched as
                # usual and lands there. Backspace with the focus still in the
                # list did nothing at all, which reads as the window ignoring
                # you: the query is right there on screen with a caret in it.
                self._focus_edit()
        elif self._resizer.handle(event):
            # A press on the window's edge is a resize, so the Panel never
            # sees it - which is also what keeps it from landing on the row
            # or the field the edge is drawn beside.
            self.panel.render()
            return
        elif event.type is EventType.MOUSE_MOVE:
            # The edge is not a widget, so nothing hovers it and nothing would
            # ask for its cursor; the frame that draws it asks on its behalf.
            # The border, and nothing about the pointer's shape: macOS gives
            # that to the key window, so a window that never becomes one
            # could only change it after a click - an affordance that appears
            # once you no longer need it is worse than none.
            self._page.hot = self._resizer.edge_now(event.x, event.y) is not None
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
        if key == "home":
            index = 0
        elif key == "end":
            index = len(self._filtered) - 1
        else:
            index = self._list.selected + {
                "up": -1, "down": 1, "pageup": -10, "pagedown": 10}[key]
        if index < 0:
            # Off the top of the list is back to the field, whether it was one
            # row up or a whole page.
            self._focus_edit()
            return
        self._list.selected = min(len(self._filtered) - 1, index)
        self._point_at_selection()

    def _on_user_close(self) -> None:
        if not self._done:
            self._done = True
            self._release_keys()
            self._stop_background()
            self._stop_pointing()
            if self._on_canceled is not None:
                self._on_canceled()

    def _stop_pointing(self) -> None:
        if self._pointer is not None:
            self._pointer.close()
            self._pointer = None
        self._pointing_at = None

    def dismiss(self) -> None:
        """Close without invoking the callbacks (the owner is replacing it)."""
        if not self._done:
            self._done = True
            self._release_keys()
            self._stop_background()
            self._stop_pointing()
            self.window.close()

    def _finish(self, candidate, modifier_flags: int) -> None:
        if self._done:
            return
        self._done = True
        self._release_keys()
        self._stop_background()
        self._stop_pointing()
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
