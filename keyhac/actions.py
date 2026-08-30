"""Chooser-based actions (ported from keyhac-mac keyhac_action.py).

These live above both keyhac.core (engine) and keyhac.ui (PuiKit windows):
they are the user-facing glue between the two.
"""

import datetime
import os
import traceback

from keyhac.core.const import MODKEY_SHIFT
from keyhac.core.focus import match_app_name, match_window_fields
from keyhac.core.action import ThreadedAction
from keyhac.core.keymap import Keymap
from keyhac.core import log

logger = log.getLogger("Action")

# Delay between re-activating the target app and sending the paste keystroke,
# so the activation has settled (keyhac-win used a comparable settle wait).
# Only the activating path pays it - see ChooserAction.activates.
_PASTE_DELAY = 0.15

#: Seconds between an open chooser's "did the world move" checks.  The same
#: read runs on every key down, so a user typing pays it faster than this
#: does, and it only runs while a chooser is open.
_DISMISS_POLL = 0.25


class _DismissWatch:
    """Closes the open chooser when the user moves away from it.

    A chooser is transient: opened over one window, for one decision.
    Nothing used to close it when that context went away, so one could
    survive on another virtual desktop - and the hotkey would then toggle
    closed a window the user could not see, which looked like the chooser
    refusing to open (discussion #112).

    Two triggers, which are one observation and one more:

    - **The frontmost window changed.**  A window belongs to exactly one
      desktop, so switching desktops necessarily changes which window is
      frontmost: the same check catches a desktop switch, another
      application coming forward, and another window of the same
      application.  Keyed on `(pid, window title)` and deliberately not on
      the focus path - on macOS that path runs down to the focused
      *element*, so it changes when the user Tabs between fields inside one
      window and would pull the chooser out from under them.
    - **A click landed outside the chooser.**  Separate, because a click on
      the current window's own background moves no focus at all.  The mouse
      hook is already installed for one-shot cancellation; this rides it.

    Polled rather than pushed: the native notifications differ per OS
    (`NSWorkspaceActiveSpaceDidChange`, `SetWinEventHook`) and would be two
    platform-layer implementations, while `FocusProvider.get_focus()` is
    already called on every keystroke - so the cost is known, and both OSes
    behave identically.

    Dismissal never gives the focus back to anyone.  The user moved away on
    purpose; yanking them somewhere would be the opposite of what they did.
    """

    def __init__(self, chooser, on_dismiss):
        self._chooser = chooser
        self._on_dismiss = on_dismiss
        self._stopped = False
        self._cancel = None
        self._origin = self._frontmost()
        # One bound-method object, kept: `self._clicked` builds a fresh one on
        # every access, so stop() could never recognise its own registration.
        self._observer = self._clicked
        keymap = Keymap.get_instance()
        if keymap is not None:
            keymap.on_mouse_button = self._observer
        self._arm()

    @staticmethod
    def _frontmost():
        """Which window the user is in, or None for "no usable reading".

        The *active window*, deliberately, and not the keyboard focus.  A
        `Focus` mixes its sources - its pid is the frontmost application
        (which our popup never becomes) while its window title comes from
        the AX-focused application (which our popup *can* become on a
        click).  Watching that mixture is how clicking the chooser closed
        it: the pid check passed, and the title had turned into ours.
        `get_active_window()` reads the frontmost application's own focused
        window throughout, so nothing about our popup can move it - and it
        skips the up-to-64-level AX path walk `get_focus()` pays for, which
        makes it the cheaper read as well.

        Keyhac's own process is still no reading at all, for the activating
        path (`activates = True`), which puts the focus on us on purpose.
        """
        keymap = Keymap.get_instance()
        if keymap is None:
            return None
        try:
            window = keymap.get_active_window()
        except Exception:
            return None
        if window is None or window.pid == os.getpid():
            return None
        return (window.pid, window.title)

    def _arm(self) -> None:
        from keyhac.ui import runtime
        if runtime.backend is not None:
            self._cancel = runtime.backend.call_later(_DISMISS_POLL, self._tick)

    def _tick(self) -> None:
        if self._stopped:
            return
        where = self._frontmost()
        # None is "could not read it", not "nothing is focused": a transient
        # AX failure must not close the window the user is typing into.
        if where is not None and where != self._origin:
            logger.debug(f"Chooser dismissed: focus moved to {where}.")
            self._fire()
            return
        self._arm()

    def _clicked(self) -> None:
        if self._stopped or self._inside():
            return
        # Coordinates in the message on purpose: if this ever fires for a
        # click that was visibly on the popup, the two numbers say whether
        # the geometry or the trigger is at fault.
        keymap = Keymap.get_instance()
        logger.debug(f"Chooser dismissed: click at "
                     f"{keymap.cursor_pos() if keymap else None} is outside "
                     f"{self._chooser.window.frame_px()}.")
        self._fire()

    def _inside(self) -> bool:
        """Whether the pointer is over the chooser.  True when it cannot be
        told - same rule as above, an unreadable answer closes nothing."""
        keymap = Keymap.get_instance()
        pos = keymap.cursor_pos() if keymap is not None else None
        frame = self._chooser.window.frame_px()
        if pos is None or frame is None:
            return True
        x, y = pos
        fx, fy, fw, fh = frame
        return fx <= x < fx + fw and fy <= y < fy + fh

    def _fire(self) -> None:
        self.stop()
        self._on_dismiss()

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        keymap = Keymap.get_instance()
        if keymap is not None and keymap.on_mouse_button is self._observer:
            keymap.on_mouse_button = None


class ChooserAction:
    """Base class for actions that open the chooser window.

    Derive from it to build your own popup: implement list_items() and
    on_chosen(), and inherit the whole open / filter / refocus flow.  Only one
    chooser is open at a time - pressing the same action's key again closes
    it, and a different chooser action replaces it.

    ```python
    class PickBranch(ChooserAction):
        def list_items(self):
            return [("🌱", name) for name in git_branches()]
        def on_chosen(self, item, modifier_flags):
            checkout(item[1])
    ```
    """


    #: The one chooser currently open: (action, window, original_pid).
    _open = None

    #: The _DismissWatch guarding it, cleared with it.
    _watch = None

    #: The open that has been asked for but not built yet, as (action, token).
    #: A chooser is never built inside the key hook's callback - see
    #: __call__ - so between the key and the window there is one turn of the
    #: loop in which a second press has to find something to toggle.
    _pending = None

    #: How the filter text is matched against the rows.  None means the
    #: default: case-insensitive substring, unioned with Migemo so romaji
    #: finds Japanese.  Set it per action to pick something else -
    #: ``WildcardMatcher()`` for 1.x's ``*`` / ``?``.
    matcher = None

    #: Whether the chooser takes OS keyboard focus.  False (the default)
    #: leaves the application underneath focused and routes the keystrokes
    #: through the key hook, which is what keeps the console where it is,
    #: keeps the current Space, and lets a paste go out with no settle
    #: delay.  Set it True only for a source whose filter field genuinely
    #: needs an input method: composition cannot reach an unfocused window,
    #: so that is the one thing the default gives up.
    activates = False

    #: Where the window opens.  ``"caret"`` puts it under the text insertion
    #: point, falling through to the focused control and then to the focused
    #: window's centre when the caret cannot be read *or cannot be believed*
    #: (`keyhac.core.anchor`).  ``"window"`` is that centre alone, which is
    #: what a chooser acting on the window rather than on a place in it wants
    #: - a window switcher has no business opening beside your caret.
    anchor = "caret"

    def __repr__(self):
        return f"{type(self).__name__}()"

    def __call__(self):
        from keyhac.ui import runtime

        if runtime.backend is None:
            logger.error(f"{self!r} requires the UI (running with --no-ui?).")
            return

        keymap = Keymap.get_instance()

        # A second press while the first one's window is still queued is that
        # same press twice - there is no window on screen for it to toggle
        # yet. Drop the queued open; for this action that *is* the toggle, and
        # nothing is left to close.
        pending, ChooserAction._pending = ChooserAction._pending, None
        if pending is not None and pending[0] is self:
            return

        # Only one chooser at a time (issue #3): pressing the same action's
        # key again toggles its chooser closed; a different chooser action
        # replaces the open one. Either way the replacement inherits the app
        # to refocus - which matters only for an activating chooser, where
        # the focus at this point is the chooser itself rather than the
        # window the user was working in. A non-activating one never took
        # the focus, so there is nothing to give back.
        open_entry, ChooserAction._open = ChooserAction._open, None
        ChooserAction._stop_watch()
        if open_entry is not None:
            prev_action, prev_chooser, original_pid = open_entry
            prev_chooser.dismiss()
            if prev_action is self:
                self._refocus(original_pid)
                return
        else:
            original_pid = None
            if self.activates:
                focus = keymap.focus
                original_pid = focus.pid if focus else None

        # Center the chooser on the focused window (issue #4). Both frames are
        # portable top-left screen coordinates on both OSes; clamp to the
        # screen the window mostly lives on. UI thread here, so the window
        # accessors are allowed.
        #
        # The *caret* is not read here. This runs on the hook's clock (see the
        # deadline note below), and reading it is two more cross-process
        # round trips into an application that may not be answering. It is
        # read in _open_window instead, one turn of the loop later, where the
        # window is built - and where the answer is just as good, the chooser
        # taking no focus and the caret therefore not having moved.
        center_on = clamp_to = None
        active = keymap.get_active_window()
        if active is not None:
            center_on = active.get_frame()
        if center_on is not None and keymap.window_provider is not None:
            clamp_to = MoveWindow._get_best_screen(
                center_on, keymap.window_provider.screen_frames())

        # **The window is not built here**, because everything above runs
        # inside the key hook's callback and building it is neither quick nor
        # certain to succeed.
        #
        # Certain first, because that is the reported bug: the engine passes
        # the key through to the application when handling it *raises* (a
        # deliberate rule - a broken config must not swallow the keyboard), so
        # a source that could not be read, or a window that could not be
        # created, sent the "P" of the key that opened the chooser into
        # whatever the user was typing in. When the failure came after the
        # window existed - the dismissal watch, the activation - the chooser
        # was on screen *and* the key had leaked, which is exactly what was
        # reported.
        #
        # Quick, second: a low-level hook has a deadline. Windows drops a hook
        # whose callback overruns LowLevelHooksTimeout (300 ms unless the
        # registry says otherwise) and delivers the event that overran to the
        # application anyway; macOS disables a slow event tap in the same
        # spirit. Building a chooser is past that budget - 390 ms for this
        # module's import of the chooser alone on the first press, before any
        # source is read (84 ms for a small menu bar, 590 ms for a heavy
        # window's controls). Measured on one Windows 11 build a 1.5 s stall
        # was still honoured, so this is the risk rather than the proven
        # cause; it is a real one on any machine that has that timeout set.
        #
        # So the key is consumed and the callback returns now; the window is
        # built on the next turn of the loop, a millisecond later and none of
        # it on the hook's clock or in the reach of its pass-through.
        token = object()
        ChooserAction._pending = (self, token)

        def _open_now():
            if ChooserAction._pending is None \
                    or ChooserAction._pending[1] is not token:
                return                  # a later press superseded this one
            ChooserAction._pending = None
            try:
                self._open_window(original_pid, center_on, clamp_to)
            except Exception:
                # A source that could not be read is this action's failure
                # to report. Nothing catches for us out here - the loop's
                # queue drain does not - and the engine's own catch, which
                # would have logged it, is no longer on the stack: it
                # passes the key through to the application when handling
                # raises, which is the leak this whole detour avoids.
                logger.error(f"{self!r} could not open:\n"
                             f"{traceback.format_exc()}")

        if keymap is not None:
            # Queued, never inline: both backends post to the loop rather than
            # calling back on the spot. With no loop wired (a library use, or
            # a test) it does run inline, which is the behaviour those callers
            # already had everywhere else.
            keymap.call_on_main_thread(_open_now)
        else:
            _open_now()

    def _below(self, keymap, clamp_to):
        """The caret or the focused control to open under, or None.

        None means the window's centre, which is where `center_on` already
        points - so every fall-through in the chain ends at the behaviour
        this replaced rather than at nothing.

        lazydocs: ignore
        """
        if self.anchor != "caret" or keymap is None:
            return None
        from keyhac.core.anchor import popup_anchor
        provider = getattr(keymap, "_focus_provider", None)
        if provider is None:
            return None
        try:
            element = provider.get_focused_element()
        except Exception:
            return None
        found = popup_anchor(element)
        if found is None:
            return None
        rect, kind = found
        logger.debug(f"Chooser anchored on the {kind}: {rect}")
        return rect

    def _open_window(self, original_pid, center_on, clamp_to) -> None:
        """Build and show the window, one turn of the loop after the key that
        asked for it (see __call__).

        lazydocs: ignore
        """
        from keyhac.ui import runtime
        from keyhac.ui.chooser import ChooserWindow

        if runtime.backend is None:
            return
        keymap = Keymap.get_instance()

        def _refocus_original_app():
            self._refocus(original_pid)

        def _on_selected(item, modifier_flags):
            ChooserAction._open = None
            ChooserAction._stop_watch()
            _refocus_original_app()
            self._choose(item, modifier_flags)

        def _on_canceled():
            ChooserAction._open = None
            ChooserAction._stop_watch()
            _refocus_original_app()

        # Always the same page, so the key means the same thing on every
        # press - and the *middle* one, because that is where the page you
        # reach for most belongs. Landing on an end would put the common
        # case a keystroke away and leave one side of the row unused.
        self._page = self._home_page()
        self._owners = {}
        #: What each source has read so far, for this window only:
        #: id(source) -> [rows, unfinished generator or None].
        self._read = {}
        rows, pending, badge_of, background = self._collect()
        chooser = ChooserWindow(runtime.backend, rows, pending=pending,
                                on_selected=_on_selected, on_canceled=_on_canceled,
                                center_on=center_on, clamp_to=clamp_to,
                                below=self._below(keymap, clamp_to),
                                matcher=self.matcher, activates=self.activates,
                                badge_of=badge_of,
                                pages=self.page_names(),
                                on_page=self._page_rows,
                                background=background,
                                source_of=self._source_name)
        ChooserAction._open = (self, chooser, original_pid)

        def _dismiss():
            # No refocus: the user moved away deliberately.
            ChooserAction._open = None
            chooser.dismiss()

        ChooserAction._watch = _DismissWatch(chooser, _dismiss)

        if self.activates and keymap is not None and keymap.app_control is not None:
            # Keyhac runs as an accessory (agent) app, so an activating
            # chooser has to activate our own process to be typed into; the
            # original app is re-activated on selection/cancel above. This
            # is app-scoped, so it also brings the console forward and can
            # follow the app to another Space - the reason the default is
            # not to do it (discussion #112).
            keymap.app_control.activate_pid(os.getpid())

    def sources(self) -> list:
        """The sources this action shows.  Override this, or `list_items`.

        The default wraps `list_items` / `on_chosen` as one unnamed source, so
        an action written before sources existed keeps working unchanged.

        lazydocs: ignore
        """
        from keyhac.core.source import CallableSource
        return [CallableSource(self.list_items, on_chosen=self._chosen_legacy)]

    def _chosen_legacy(self, candidate, modifier_flags: int) -> None:
        """Hand `on_chosen` what it has always been handed: the tuple
        `list_items` produced, for an action written before sources existed."""
        payload = candidate.payload
        self.on_chosen(payload if isinstance(payload, tuple) else candidate,
                       modifier_flags)

    def _collect(self):
        """The current page's rows, whatever of them is still coming, and the
        badge lookup the window draws beside each one.

        **A source is read once per window, not once per page.** The same
        `MenuItemsSource` can sit in an everything-page and in a page of its
        own, and walking the menu bar twice for one press of one key is work
        nobody asked for. So what is remembered is keyed on the *source
        object*: share an instance between pages and it is read once; build
        two, and they are two sources that happen to be alike, which is also
        the right answer - two `SnippetsSource` with different snippets are
        not interchangeable.

        Safe for the life of the window because the dismissal watch closes it
        the moment the front window changes, so nothing a source read can
        have gone stale while the window is up.
        """
        from keyhac.core.candidate import Candidate
        from keyhac.core.source import CandidateSource

        sources = self.sources()
        rows, unfinished = [], []
        for source in sources:
            state = self._read.get(id(source))
            if state is None:
                produced = source.candidates()
                if isinstance(produced, (list, tuple)):
                    state = [[self._adopt(source, item) for item in produced],
                             None]
                else:
                    state = [[], iter(produced)]
                self._read[id(source)] = state
            rows.extend(state[0])
            if state[1] is not None:
                unfinished.append((source, state))

        def remainder(group):
            """Continue each unfinished source in `group`, recording as it
            goes.

            A row read here lands in the source's own list as well as in this
            window, so a *different* page sharing the source starts from
            where this one got to rather than from nothing.
            """
            for source, state in group:
                generator = state[1]
                if generator is None:
                    continue
                for item in generator:
                    candidate = self._adopt(source, item)
                    state[0].append(candidate)
                    yield candidate
                state[1] = None

        # Two streams, because they are drained in two different places. A
        # source that touches nothing of Keyhac's says so with `background`
        # and is walked on a worker; everything else keeps the main-thread
        # slices, which is where a source touching the UI has to be. They are
        # separate generators rather than one, because merging them would put
        # every row of both on whichever thread drained first.
        main = [pair for pair in unfinished if not getattr(pair[0], "background", False)]
        worker = [pair for pair in unfinished if getattr(pair[0], "background", False)]
        pending = remainder(main) if main else None
        background = remainder(worker) if worker else None
        if len(sources) < 2:
            # No "which source" question to answer, so the slot belongs to
            # the source itself - the menu source puts the shortcut there.
            single = sources[0] if sources else None
            if single is None or type(single).badge is CandidateSource.badge:
                return rows, pending, None, background
            return rows, pending, lambda c: single.badge(c), background
        return rows, pending, lambda c: getattr(
            self._owners.get(id(c)), "name", ""), background

    def _source_name(self, candidate) -> str:
        """Which source produced a row, for the window's `@` narrowing.

        Separate from the badge lookup on purpose: with one source the badge
        slot belongs to the source itself - the menu source puts a keyboard
        shortcut there - so the two questions have different answers and only
        this one is always a source's name.

        lazydocs: ignore
        """
        return getattr(self._owners.get(id(candidate)), "name", "")

    def _adopt(self, source, item):
        from keyhac.core.candidate import Candidate
        candidate = Candidate.from_item(item)
        self._owners[id(candidate)] = source
        return candidate

    def _page_rows(self, index: int):
        """The rows of the page the window is moving to.

        lazydocs: ignore
        """
        self._page = index
        return self._collect()

    def page_names(self):
        """ChooserPage names for the window's row; empty when there is only one.

        lazydocs: ignore
        """
        return []

    def _home_page(self) -> int:
        """The page a press of the key opens on.  Zero here, because a
        chooser with no pages has only one thing to open on; `ShowCandidates`
        overrides it with the middle of the row.

        lazydocs: ignore
        """
        return 0

    def _choose(self, candidate, modifier_flags: int) -> None:
        """Route a chosen row to whatever owns it."""
        source = self._owners.get(id(candidate))
        if source is not None:
            source.choose(candidate, modifier_flags)
        else:
            # A candidate this invocation did not produce.
            self.on_chosen(candidate, modifier_flags)

    #: Filled by _collect for the lifetime of one open window.
    _owners: dict = {}
    _read: dict = {}

    @staticmethod
    def _stop_watch() -> None:
        """Tear down the watch guarding the chooser that is going away."""
        watch, ChooserAction._watch = ChooserAction._watch, None
        if watch is not None:
            watch.stop()

    @staticmethod
    def _refocus(pid) -> None:
        """Give the focus back to the application the chooser took it from.
        `pid` is None for a non-activating chooser, which never took it."""
        if pid is None:
            return
        keymap = Keymap.get_instance()
        if keymap.app_control is not None:
            keymap.app_control.activate_pid(pid)

    def list_items(self):
        """Build the list the chooser shows.  Override this.

        Returns:
            A list of (icon, label) or (icon, label, ...) tuples.  Anything
            after the label is yours; on_chosen() receives the whole tuple.
        """
        return []

    def on_chosen(self, item, modifier_flags: int) -> None:
        """Handle the chosen item.  Override this.

        Args:
            item: The tuple list_items() produced for the chosen row.
            modifier_flags: Modifiers held at selection time, as a bit mask -
                the clipboard choosers read it to tell Enter from Shift-Enter.
        """


class ShowCandidates(ChooserAction):
    """Open the candidate window over one or more sources.

    The hotkey is the scarce resource, not the code: an action class per kind
    of row means a key per kind of row, and there are only so many a person
    can hold.  This takes sources as *values*, so several kinds share one key
    and one incremental search - and each row is labelled with where it came
    from, so a mixed list stays readable.

    ```python
    kt["Fn-V"] = ShowCandidates([ClipboardHistorySource(), SnippetsSource(mine)])
    kt["Fn-B"] = ShowCandidates(git_branches, on_chosen=checkout)
    kt["Fn-P"] = ShowCandidates([ChooserPage("All", every), ChooserPage("Clipboard", clip)])
    ```

    Enter runs whatever the chosen row's source says to do, so rows from
    different sources can mean different things in the same window - paste
    this, activate that, press the other.
    """

    def __init__(self, sources, on_chosen=None, matcher=None, activates=None,
                 anchor=None):
        """Build the action.

        Args:
            sources: A `CandidateSource`, a plain callable returning candidates, or a
                list of either.  A callable is wrapped, so anything that can
                produce a list can be a source without subclassing.  A list
                of `ChooserPage` objects instead gives the window a row of
                pages that Left and Right move between, keeping the query as
                they go.  Aim for three, with the one you reach for most in
                the middle; `@` narrows within a page and is what makes three
                enough.
            on_chosen: Called as `on_chosen(candidate, modifier_flags)` for
                rows whose source does not say what to do itself - which is
                every row when the source is a bare callable.
            matcher: How the filter text is matched; the default is
                case-insensitive substring unioned with Migemo.
            activates: Whether the window takes OS keyboard focus.  Leave it
                alone unless the filter field genuinely needs an input method
                - see `ChooserAction.activates`.
            anchor: Where the window opens - "caret" (the default) under the
                text insertion point, or "window" in the focused window's
                middle.  See `ChooserAction.anchor`.
        """
        from keyhac.core.source import ChooserPage, as_source

        self._on_chosen = on_chosen
        listed = sources if isinstance(sources, (list, tuple)) else [sources]
        if listed and all(isinstance(s, ChooserPage) for s in listed):
            self._pages = list(listed)
        else:
            # A bare callable has no opinion about what choosing does, so it
            # inherits this action's; a real CandidateSource keeps its own.
            self._pages = [ChooserPage("", [as_source(s, on_chosen=self._chosen_here)
                                       for s in listed])]
        if matcher is not None:
            self.matcher = matcher
        if activates is not None:
            self.activates = activates
        if anchor is not None:
            self.anchor = anchor

    def __repr__(self):
        if len(self._pages) > 1:
            return f"ShowCandidates({len(self._pages)} pages)"
        names = ", ".join(s.name or type(s).__name__
                          for s in self._pages[0].sources)
        return f"ShowCandidates({names})"

    def sources(self):
        """lazydocs: ignore"""
        return self._pages[self._page].sources

    def page_names(self):
        """lazydocs: ignore"""
        return [s.name for s in self._pages] if len(self._pages) > 1 else []

    #: Index into `_pages` while a window is open.  Starts at `_home_page()`.
    _page = 0

    def _home_page(self) -> int:
        """The middle of the row (overriding `ChooserAction._home_page`).

        The arrangement this window recommends is three pages with the one
        you reach for most in the middle, and opening at an end would give
        that up - the common case would cost a keystroke and half the row
        would be two away.

        Rounds **left** on an even count, which is what makes two pages open
        on the first: there is no middle of two, and the alternative reads as
        the window opening on the wrong one.

        lazydocs: ignore
        """
        return (len(self._pages) - 1) // 2 if self._pages else 0

    def on_chosen(self, candidate, modifier_flags: int) -> None:
        """lazydocs: ignore"""
        self._chosen_here(candidate, modifier_flags)

    def _chosen_here(self, candidate, modifier_flags: int) -> None:
        if self._on_chosen is not None:
            self._on_chosen(candidate, modifier_flags)


class ClipboardChooserAction(ChooserAction):
    """Base of the clipboard presets below.

    Kept because it is documented and subclassed in the wild; the behaviour
    itself now lives in `keyhac.core.sources`, so a clipboard row means the
    same thing whether it is reached through its own hotkey or through a
    unified window shared with other sources.
    """

    def _paste(self):
        """lazydocs: ignore"""
        from keyhac.core.sources import _send_paste
        _send_paste()

    def _on_chosen_common(self, text: str, modifier_flags: int):
        """lazydocs: ignore"""
        from keyhac.core.sources import _PastingSource
        _PastingSource().paste(text, modifier_flags)


class ShowClipboardHistory(ShowCandidates):
    """Show the clipboard history in the chooser window.

    Type to filter, Enter pastes into the application you came from,
    Shift-Enter only sets the clipboard, Escape cancels.

    A preset: `ShowCandidates(ClipboardHistorySource())`.  Reach for
    `ShowCandidates` directly to put the history in one window alongside
    other sources rather than on a hotkey of its own.
    """

    def __init__(self):
        """lazydocs: ignore"""
        from keyhac.core.sources import ClipboardHistorySource
        super().__init__(ClipboardHistorySource())


class ShowClipboardSnippets(ShowCandidates):
    """Show fixed snippets in the chooser window.

    Choosing one pastes it, exactly like the clipboard history.

    ```python
    ShowClipboardSnippets([
        ("📧", "me@example.com"),                          # (icon, text)
        ("📮", "Mailing address", "400 Broad St, ..."),    # (icon, label, text)
        ("🕒", "Date", DateTimeSnippet("%Y-%m-%d")),       # (icon, label, callable)
    ])
    ```

    A preset over `SnippetsSource`.
    """

    def __init__(self, snippets):
        """Build the action.

        Args:
            snippets: Sequence of (icon, text), (icon, label, text) or
                (icon, label, callable) tuples.  A callable is invoked when
                the snippet is chosen and its return value is pasted;
                returning None pastes nothing.
        """
        from keyhac.core.sources import SnippetsSource
        self.snippets = list(snippets)
        super().__init__(SnippetsSource(self.snippets))


class ShowClipboardTools(ShowCandidates):
    """Show clipboard conversion tools in the chooser window.

    Each tool takes the current clipboard text and returns its replacement.

    ```python
    ShowClipboardTools([
        ("🔄", "Quote", ShowClipboardTools.quote),
        ("🔄", "Upper case", str.upper),
    ])
    ```

    A preset over `ClipboardToolsSource`.
    """

    def __init__(self, tools):
        """Build the action.

        Args:
            tools: Sequence of (icon, label, callable) tuples; the callable
                takes the current clipboard text and returns the replacement.
        """
        from keyhac.core.sources import ClipboardToolsSource
        self.tools = list(tools)
        super().__init__(ClipboardToolsSource(self.tools))

    @staticmethod
    def to_plain(s):
        """Return the text unchanged (the identity converter).

        Args:
            s: Current clipboard text.

        Returns:
            The same text.
        """
        return s

    @staticmethod
    def quote(s):
        """Prefix every line with quote_mark.

        Args:
            s: Current clipboard text.

        Returns:
            The quoted text.
        """
        return "\n".join(ShowClipboardTools.quote_mark + line
                         for line in s.splitlines())

    @staticmethod
    def unindent(s):
        """Remove the common leading whitespace from every line.

        Args:
            s: Current clipboard text.

        Returns:
            The dedented text.
        """
        import textwrap
        return textwrap.dedent(s)

    @staticmethod
    def to_half_width(s):
        """Convert full-width characters to their half-width forms.

        Args:
            s: Current clipboard text.

        Returns:
            The converted text.
        """
        return s.translate(str.maketrans(
            {chr(c): chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)}) | {0x3000: " "})

    @staticmethod
    def to_full_width(s):
        """Convert half-width characters to their full-width forms.

        Args:
            s: Current clipboard text.

        Returns:
            The converted text.
        """
        return s.translate(str.maketrans(
            {chr(c): chr(c + 0xFEE0) for c in range(0x21, 0x7F)}) | {0x20: "　"})


class DateTimeSnippet:
    """A ShowClipboardSnippets value that produces the current date and time.

    ```python
    ShowClipboardSnippets([("🕒", "Date", DateTimeSnippet("%Y-%m-%d"))])
    ```
    """

    def __init__(self, fmt: str):
        """Build the snippet.

        Args:
            fmt: A strftime format string, e.g. "%Y-%m-%d".
        """
        self.fmt = fmt

    def __repr__(self):
        return f"DateTimeSnippet({self.fmt!r})"

    def __call__(self):
        return datetime.datetime.now().strftime(self.fmt)


class MouseMove:
    """Move the mouse cursor by a relative offset.

    Held modifiers stay held, unlike the button and wheel actions.  The move
    is injected acceleration-proof, so the distance is exactly what you ask
    for (keyhac-win MouseMoveCommand).
    """

    def __init__(self, dx: int, dy: int):
        """Build the action.

        Args:
            dx: Horizontal offset in pixels, positive = right.
            dy: Vertical offset in pixels, positive = down.
        """
        self.dx = dx
        self.dy = dy

    def __call__(self):
        with Keymap.get_instance().get_input_context() as ctx:
            ctx.send_mouse_move(self.dx, self.dy)

    def __repr__(self):
        return f"MouseMove({self.dx}, {self.dy})"


class _MouseButtonAction:
    """Common shape of the button actions: validate at config-load time,
    send through the input context (which releases held modifiers around
    the button and restores them after - keyhac-win behavior)."""

    _down: bool | None = None

    def __init__(self, button: str = "left"):
        """Build the action.

        Args:
            button: "left", "right" or "middle".

        Raises:
            ValueError: Unknown button name - reported when the configuration
                loads, not when the key is pressed.
        """
        if button not in ("left", "right", "middle"):
            raise ValueError(f'{type(self).__name__} button must be "left", '
                             f'"right" or "middle", not {button!r}')
        self.button = button

    def __call__(self):
        with Keymap.get_instance().get_input_context() as ctx:
            ctx.send_mouse_button(self.button, self._down)

    def __repr__(self):
        return f'{type(self).__name__}("{self.button}")'


class MouseButtonDown(_MouseButtonAction):
    """Press a mouse button and hold it.

    Held modifiers are released first, so a modifier-bound press does not
    become a modified one (keyhac-win MouseButtonDownCommand).
    """
    _down = True


class MouseButtonUp(_MouseButtonAction):
    """Release a held mouse button (keyhac-win MouseButtonUpCommand)."""
    _down = False


class MouseButtonClick(_MouseButtonAction):
    """Click a mouse button.

    Held modifiers are released first, and rapid synthetic clicks register as
    double-clicks (keyhac-win MouseButtonClickCommand).
    """
    _down = None


class MouseWheel:
    """Turn the vertical mouse wheel (keyhac-win MouseWheelCommand)."""

    _kind = "vertical"

    def __init__(self, wheel: float):
        """Build the action.

        Args:
            wheel: Wheel notches; positive = away from you, 1.0 = one notch.
        """
        self.wheel = wheel

    def __call__(self):
        with Keymap.get_instance().get_input_context() as ctx:
            if self._kind == "vertical":
                ctx.send_mouse_wheel(self.wheel)
            else:
                ctx.send_mouse_horizontal_wheel(self.wheel)

    def __repr__(self):
        return f"{type(self).__name__}({self.wheel})"


class MouseHorizontalWheel(MouseWheel):
    """Turn the horizontal mouse wheel (keyhac-win
    MouseHorizontalWheelCommand)."""

    _kind = "horizontal"

    def __init__(self, wheel: float):
        """Build the action.

        Args:
            wheel: Wheel notches; positive = right, 1.0 = one notch.
        """
        super().__init__(wheel)


class MoveWindow(ThreadedAction):
    """Move the focused window.

    It nudges the window by `distance` pixels, or - with `window_edge` /
    `screen_edge` - travels until it meets another window's edge or the edge
    of the screen.  A window already at the screen edge hops to the adjacent
    monitor instead.

    ```python
    MoveWindow(direction="left", distance=20)
    MoveWindow(direction="left", distance=9999, window_edge=True)
    ```
    """

    # Full port of keyhac-mac's MoveWindow. Runs on both OSes: the geometry
    # algorithm was already pure, so only the frame reads/writes and the
    # screen/window queries go through the portable Window / WindowProvider
    # API (keyhac.platform.base), which keeps macOS on AX + CoreGraphics and
    # Windows on Win32.

    ADJACENT_SCREEN_TOLERANCE = 50   # menu-bar gap etc.
    EDGE_TOLERANCE = 2

    def __init__(self, x: int = None, y: int = None, direction: str = "",
                 distance: float = 10, window_edge: bool = False,
                 screen_edge: bool = True):
        """Build the action.

        Args:
            x: Deprecated since keyhac-mac v1.64; use direction and distance.
            y: Deprecated since keyhac-mac v1.64; use direction and distance.
            direction: "left", "right", "up" or "down".
            distance: How far to move, in pixels (default 10).  Pass a large
                value together with window_edge / screen_edge to travel until
                something stops it.
            window_edge: Stop at the edges of other windows (default False).
            screen_edge: Stop at the edge of the screen (default True).
        """
        if x or y:
            logger.warning("MoveWindow's arguments x, y are deprecated. "
                           "Use direction and distance instead.")
            if x and x < 0:
                direction, distance = "left", abs(x)
            elif x and x > 0:
                direction, distance = "right", abs(x)
            elif y and y < 0:
                direction, distance = "up", abs(y)
            elif y and y > 0:
                direction, distance = "down", abs(y)
        self.direction = direction
        self.distance = distance
        self.window_edge = window_edge
        self.screen_edge = screen_edge
        self.wnd = None

    def starting(self):
        """lazydocs: ignore"""
        # ALL window reads happen here, on the main thread. On macOS these are
        # AX calls, and AX into our OWN process off the main thread SIGTRAPs
        # (they are in-process, not IPC) - and this action must also work on
        # Keyhac's own windows. Window's thread contract says the same.
        keymap = Keymap.get_instance()
        self.wnd = keymap.get_active_window()
        self.frame = self.wnd.get_frame() if self.wnd is not None else None

    @staticmethod
    def _get_best_screen(frame, screens):
        wx, wy, ww, wh = frame
        best, best_overlap = (screens[0] if screens else None), -1
        for sx, sy, sw, sh in screens:
            ox = max(0, min(wx + ww, sx + sw) - max(wx, sx))
            oy = max(0, min(wy + wh, sy + sh) - max(wy, sy))
            if ox * oy > best_overlap:
                best_overlap, best = ox * oy, (sx, sy, sw, sh)
        return best

    @classmethod
    def _find_adjacent_screen(cls, cur, screens, direction):
        cx, cy, cw, ch = cur
        tol = cls.ADJACENT_SCREEN_TOLERANCE
        best, best_overlap = None, 0
        for s in screens:
            if s == cur:
                continue
            sx, sy, sw, sh = s
            if direction == "left":
                touching = abs((sx + sw) - cx) <= tol
                overlap = min(cy + ch, sy + sh) - max(cy, sy)
            elif direction == "right":
                touching = abs(sx - (cx + cw)) <= tol
                overlap = min(cy + ch, sy + sh) - max(cy, sy)
            elif direction == "up":
                touching = abs((sy + sh) - cy) <= tol
                overlap = min(cx + cw, sx + sw) - max(cx, sx)
            else:
                touching = abs(sy - (cy + ch)) <= tol
                overlap = min(cx + cw, sx + sw) - max(cx, sx)
            if touching and overlap > best_overlap:
                best_overlap, best = overlap, s
        return best

    def run(self):
        """lazydocs: ignore"""
        if self.wnd is None or self.frame is None:
            logger.warning("MoveWindow: no focused window.")
            return None
        # Worker thread: pure math plus the WindowProvider's two thread-safe
        # geometry queries only - never a Window accessor (AX on macOS,
        # WM_GETTEXT-backed on Windows; both would block or crash here).
        provider = Keymap.get_instance().window_provider
        if provider is None:
            logger.warning("MoveWindow: no window provider on this platform.")
            return None
        frame = self.frame
        wx, wy, ww, wh = frame

        screens = provider.screen_frames()
        cur = self._get_best_screen(frame, screens)
        if cur is None:
            return None
        sx, sy, sw, sh = cur

        # Already at the screen edge? Jump to the adjacent monitor.
        at_edge = {
            "left": (wx - sx) <= self.EDGE_TOLERANCE,
            "right": ((sx + sw) - (wx + ww)) <= self.EDGE_TOLERANCE,
            "up": (wy - sy) <= self.ADJACENT_SCREEN_TOLERANCE,
            "down": ((sy + sh) - (wy + wh)) <= self.EDGE_TOLERANCE,
        }.get(self.direction, False)
        if at_edge:
            adj = self._find_adjacent_screen(cur, screens, self.direction)
            if adj is not None:
                ax, ay, aw, ah = adj
                if self.direction == "left":
                    wx = ax + aw - ww
                elif self.direction == "right":
                    wx = ax
                elif self.direction == "up":
                    wx = max(ax, min(wx, ax + aw - ww))
                    wy = ay + ah - wh
                elif self.direction == "down":
                    wx = max(ax, min(wx, ax + aw - ww))
                    wy = ay
                return (wx, wy)

        distance = self.distance

        # Leading edge of this window in the movement direction
        if self.direction == "left":
            front_pos, front_range, sign = wx, (wy, wy + wh), -1
        elif self.direction == "right":
            front_pos, front_range, sign = wx + ww, (wy, wy + wh), 1
        elif self.direction == "up":
            front_pos, front_range, sign = wy, (wx, wx + ww), -1
        elif self.direction == "down":
            front_pos, front_range, sign = wy + wh, (wx, wx + ww), 1
        else:
            return None

        if self.screen_edge:
            edge_dist = {
                "left": wx - sx, "right": (sx + sw) - (wx + ww),
                "up": wy - sy, "down": (sy + sh) - (wy + wh),
            }[self.direction]
            if edge_dist >= 0.1:
                distance = min(distance, edge_dist)

        if self.window_edge:
            gap = 1
            for ox, oy, ow, oh in provider.window_frames():
                if (ox, oy, ow, oh) == frame:
                    continue
                if self.direction in ("left", "right"):
                    edge_pos = (ox + ow) if self.direction == "left" else ox
                    edge_range = (oy, oy + oh)
                else:
                    edge_pos = (oy + oh) if self.direction == "up" else oy
                    edge_range = (ox, ox + ow)
                if not (front_range[1] <= edge_range[0]
                        or front_range[0] >= edge_range[1]):
                    if (edge_pos - front_pos) * sign - gap >= 0.1:
                        distance = min(distance, (edge_pos - front_pos) * sign - gap)

        if self.direction == "left":
            wx -= distance
        elif self.direction == "right":
            wx += distance
        elif self.direction == "up":
            wy -= distance
        elif self.direction == "down":
            wy += distance

        wx = max(sx, min(wx, sx + sw - ww))
        wy = max(sy, min(wy, sy + sh - wh))
        return (wx, wy)

    def finished(self, result):
        """lazydocs: ignore"""
        # finished() is on the event-loop thread, which is where the AX write
        # has to happen (own-process safety).
        if self.wnd is None or result is None:
            return
        self.wnd.set_frame(result[0], result[1])

    def __repr__(self):
        return f'MoveWindow(direction="{self.direction}")'


class ReportCaretAnchor:
    """Say where a popup would open right now, and why.

    Bind it to a key and press it inside the application you are asking
    about.  The report goes to the console at INFO - no debug logging to turn
    on - and a balloon appears at the place it found, so the numbers and the
    result are visible together.

    ```python
    kt["Fn-Ctrl-C"] = ReportCaretAnchor()
    ```

    **Why a key rather than a command-line tool.**  `tools/caret_probe.py`
    asks about whatever is in front, and while it runs that is the terminal.
    Reaching the application under test means switching to it and racing a
    timer, and the Accessibility permission it needs belongs to the terminal
    rather than to Keyhac - a second grant, in a second place, easy to have
    given to a different terminal last time.  Pressed as a key, none of that
    applies: Keyhac is already trusted and the application under test already
    has the focus.

    What it reports is the chain `keyhac.core.anchor` walks - the caret, then
    the focused control if it is small enough to be a place, then the window -
    and, above it, what each way of asking the caret question answered.  That
    last part is what tells a control with no caret from one whose caret
    cannot be believed, and those want opposite things done about them.
    """

    def __repr__(self):
        return "ReportCaretAnchor()"

    def __call__(self):
        from keyhac.core.anchor import popup_anchor, place_below, usable_caret

        keymap = Keymap.get_instance()
        provider = getattr(keymap, "_focus_provider", None)
        asked = None
        if provider is not None:
            try:
                asked = provider.get_focused_element()
            except Exception:
                logger.error("Could not ask where the focus is.")
                return

        # The two callers do not read the focus from the same place, and the
        # answers can differ: the chooser asks the provider for the truth
        # *now*, the balloon takes the snapshot the keystroke was dispatched
        # against - and that snapshot falls back to the window, and then to
        # the application, where the provider would rather say nothing
        # (issue #44). A report that showed one of them would be describing
        # half of what is on screen.
        focus = getattr(keymap, "focus", None)
        snapshot = getattr(focus, "element", None)
        where = getattr(focus, "app_name", None) or "the front application"
        element = asked if asked is not None else snapshot
        if element is None:
            logger.info(f"Caret report - {where} reports no focused element. "
                        f"A Chromium application answers that until an "
                        f"assistive client asks it to build its accessibility "
                        f"tree (keymap.ui.enable_content_access()).")
            self._show(None, "no focused element")
            return

        rect = getattr(element, "get_rect", lambda: None)()
        caret = getattr(element, "get_caret_rect", lambda: None)()
        lines = [f"Caret report - {where} / {self._role(element)}",
                 f"  focused element : {rect}"]
        if asked is None:
            lines.append("  (from the keystroke's focus snapshot - asking for "
                         "the focus now returned nothing)")
        elif snapshot is not None and not self._same(asked, snapshot):
            lines.append(f"  (the balloon would use a different element: "
                         f"{self._role(snapshot)} "
                         f"{getattr(snapshot, 'get_rect', lambda: None)()})")
        for label, value in self._detail(element):
            lines.append(f"  {label:<32}: {value}")
        lines.append(f"  caret as we take it: {caret} "
                     f"-> {'believed' if usable_caret(caret, rect) else 'not believed'}")

        found = popup_anchor(element)
        if found is None:
            lines.append("  anchor          : none - a popup opens in the "
                         "window's centre, and a balloon in the corner")
        else:
            anchored, kind = found
            lines.append(f"  anchor          : {kind} {anchored}")
            lines.append(f"  a 400x200 popup would go to "
                         f"{place_below((400, 200), anchored)}")
        logger.info("\n".join(lines))
        self._show(found, "nothing to place against")

    @staticmethod
    def _same(one, other) -> bool:
        """Whether two reads landed on the same element.

        Each read builds a fresh Python proxy, so `is` never says yes;
        `identity_key()` is the platform reference underneath.
        """
        try:
            return one.identity_key() == other.identity_key()
        except Exception:
            return False

    @staticmethod
    def _role(element) -> str:
        for attribute in ("AXRole", "ControlType"):
            try:
                value = element.get_attribute_value(attribute)
            except Exception:
                continue
            if value:
                return str(value)
        return "?"

    @staticmethod
    def _detail(element):
        """Whatever the platform can say about how it looked for the caret."""
        describe = getattr(element, "describe_caret", None)
        if describe is None:
            return []
        try:
            return describe()
        except Exception:
            return [("describe_caret", "raised")]

    @staticmethod
    def _show(found, caption: str) -> None:
        """Put a balloon where the real one would go, by the same rules."""
        keymap = Keymap.get_instance()
        pop = getattr(keymap, "pop_balloon", None)
        if pop is None:
            return
        where = {}
        if found is not None:
            rect, caption = found[0], found[1]
            where = {"over": rect} if caption == "window" else {"near": rect}
        try:
            pop("CaretAnchor", f"anchor: {caption}", 4.0, **where)
        except Exception:
            logger.debug("No balloon to show the anchor with.")


class SnapWindow:
    """Snap the focused window to a region of its screen (tiling).

    The region is the screen's *work area*, so the menu bar and Dock (macOS)
    and the taskbar (Windows) stay uncovered.  "Its screen" is the one the
    window overlaps most, so repeated snaps keep a window on the monitor it is
    already on.

    This places the window itself; it does not enter the OS's own snap state.
    The window lands where Windows' snap would put it, but Snap Assist does
    not offer to fill the other half, no snap group is formed, dragging the
    window away does not restore its previous size, and dragging the shared
    edge does not resize the neighbour.

    ```python
    SnapWindow("left")               # left half
    SnapWindow("left", ratio=2/3)    # left two thirds
    SnapWindow("full")
    ```
    """

    # Deliberately a plain main-thread action, not a ThreadedAction: unlike
    # MoveWindow there is no window-edge scan to push off-thread, just
    # arithmetic - and both the Window accessors and the work-area query
    # (AppKit-backed on macOS) are UI-thread only anyway.

    POSITIONS = ("left", "right", "top", "bottom", "full")

    def __init__(self, position: str, ratio: float = 0.5):
        """Build the action.

        Args:
            position: "left", "right", "top", "bottom" or "full".
            ratio: Fraction of the work area the window covers along the snap
                axis, between 0.1 and 1.0 (default 0.5 = half the screen).
                Ignored for "full".

        Raises:
            ValueError: Unknown position, or a ratio outside [0.1, 1.0] -
                reported when the configuration loads, not when the key is
                pressed.
        """
        if position not in self.POSITIONS:
            raise ValueError(
                f"SnapWindow position must be one of {self.POSITIONS}, "
                f"not {position!r}")
        if not 0.1 <= ratio <= 1.0:
            raise ValueError(f"SnapWindow ratio must be in [0.1, 1.0], "
                             f"not {ratio}")
        self.position = position
        self.ratio = ratio

    def __call__(self):
        keymap = Keymap.get_instance()
        window = keymap.get_active_window()
        if window is None:
            logger.warning("SnapWindow: no focused window.")
            return
        frame = window.get_frame()
        if frame is None:
            logger.warning("SnapWindow: the focused window has no frame.")
            return
        screens = keymap.screen_work_frames() or keymap.screen_frames()
        screen = MoveWindow._get_best_screen(frame, screens)
        if screen is None:
            return
        sx, sy, sw, sh = screen
        r = self.ratio
        if self.position == "left":
            target = (sx, sy, sw * r, sh)
        elif self.position == "right":
            target = (sx + sw * (1 - r), sy, sw * r, sh)
        elif self.position == "top":
            target = (sx, sy, sw, sh * r)
        elif self.position == "bottom":
            target = (sx, sy + sh * (1 - r), sw, sh * r)
        else:
            target = (sx, sy, sw, sh)
        if window.is_minimized():
            window.restore()
        window.set_frame(*target)

    def __repr__(self):
        return f'SnapWindow("{self.position}")'


def _window_position(window):
    """Sort key for the rotation: top to bottom, then left to right.

    A frame survives an activation, which is exactly what the z-order does
    not.  A window with no readable frame sorts last, together with the others
    that have none, in the order the platform listed them - deterministic
    rather than correct, which is all there is for a window that will not say
    where it is.
    """
    frame = window.get_frame()
    if frame is None:
        return (float("inf"), float("inf"))
    x, y, _width, _height = frame
    return (y, x)


class ActivateApplication(ThreadedAction):
    """Go to an application: bring it forward, walk its windows, launch it.

    One key for the whole of "take me to my terminal".  What a press does
    depends on where the application already is:

    - **Behind** - its front-most window comes forward.
    - **Already in front** - its next window comes forward, so one key reaches
      all of them.  Bind a second key with `reverse=True` to walk back.
    - **Not running** - `launch=` starts it.  Without `launch=` nothing is
      started.

    ```python
    kt["O-RCmd"] = ActivateApplication(app="Terminal|ターミナル",
                                       launch="Terminal.app")
    ```

    The rotation keeps no state.  Which window is current is read from the
    z-order on each press, and the order walked is where the windows sit on
    screen, so it survives a configuration reload, windows opening and
    closing, and the user dragging one somewhere else.
    """

    # Three measurements shaped the rotation, and each one breaks an obvious
    # implementation:
    #
    # * Several windows of one application report the same title (three
    #   Terminal windows all called "Terminal"), so a title is not an identity
    #   and nothing may key on one.
    # * list_windows() is z-ordered and activating a window moves it to the
    #   front of that order, so walking the z-order swaps the top two back and
    #   forth forever and never reaches a third window.  The walk therefore
    #   runs over a stable order - position on screen - and the z-order is
    #   asked only which window is current.
    # * The z-order is not globally front-most-first on macOS: with Terminal
    #   active the list still began with a VS Code window, because it is
    #   grouped by application.  So windows[0] cannot answer "is this
    #   application in front"; get_active_window() can, and does.
    #
    # Thread contract: every window read and the activation itself happen in
    # starting(), on the event-loop thread - Window accessors are UI-thread
    # only - which leaves run() with nothing but the launch.

    def __init__(self, app: str, launch: str = None,
                 cycle: bool = True, reverse: bool = False):
        """Build the action.

        Args:
            app: Application name pattern, matched like define_keytable's
                app= - case-insensitive, fnmatch wildcards, "|" alternation,
                ".exe" optional.  Name every spelling you need: macOS reports
                the localized application name, so Terminal is "ターミナル" on
                a Japanese system.
            launch: What to hand the OS when nothing matches - "Terminal.app"
                on macOS, an executable name or path on Windows.  None never
                launches anything.
            cycle: Whether a press made while the application is already in
                front moves on to its next window.
            reverse: Walk the windows the other way.

        Raises:
            ValueError: No app pattern - reported when the configuration
                loads, not when the key is pressed.
        """
        if not app:
            raise ValueError("ActivateApplication needs an app= pattern")
        self.app = app
        self.launch = launch
        self.cycle = cycle
        self.reverse = reverse
        self._launch_wanted = False
        self._message = None

    def starting(self):
        """lazydocs: ignore"""
        self._launch_wanted = False
        self._message = None
        keymap = Keymap.get_instance()
        windows = []
        if keymap.window_provider is not None:
            windows = [w for w in keymap.list_windows()
                       if match_window_fields(w, app=self.app)]
        if not windows:
            self._nothing_matched(keymap)
            return

        active = keymap.get_active_window()
        in_front = active is not None and match_window_fields(active, app=self.app)
        if not self.cycle or len(windows) == 1 or not in_front:
            # windows[0] is the application's front-most window - that is what
            # the z-order means - and a press that finds the application
            # behind means "come here", not "next", so direction does not
            # apply and both bindings do the same thing.
            target = windows[0]
            self._message = f"Activated {self.app} ({len(windows)} window(s))"
        else:
            ordered = sorted(windows, key=_window_position)
            # sorted() hands back the same objects, so identity locates the
            # current window even though the titles cannot.
            index = next((i for i, w in enumerate(ordered) if w is windows[0]), 0)
            step = (index - 1 if self.reverse else index + 1) % len(ordered)
            target = ordered[step]
            self._message = (f"Moved to {self.app} window "
                             f"{step + 1}/{len(ordered)}")

        if target.is_minimized():
            target.restore()
        if target.activate():
            return
        self._message = None
        if self.launch is None:
            logger.warning(f"{type(self).__name__}: a window matching "
                           f"{self.app!r} would not activate.")
        else:
            self._launch_wanted = True

    def _nothing_matched(self, keymap):
        """Launch, or activate a running application that shows no window."""
        if self.launch is not None:
            self._launch_wanted = True
            return
        # An application can be running with no window the provider can see;
        # by pid is the only way to reach one.
        for name, pid in keymap.app_control_running_apps():
            if match_app_name(name, self.app):
                keymap.app_control.activate_pid(pid)
                self._message = f"Activated {name} by pid"
                return
        logger.warning(f"{type(self).__name__}: no window or running app "
                       f"matches {self.app!r}")

    def run(self):
        """lazydocs: ignore"""
        if not self._launch_wanted:
            return self._message
        # The OS's own launch is idempotent - `open -a` activates a running
        # application rather than starting a second copy - so racing the
        # window read in starting() costs nothing.
        Keymap.get_instance().app_control.launch(self.launch)
        return f"Launched {self.launch}"

    def finished(self, result):
        """lazydocs: ignore"""
        if result:
            logger.debug(result)

    def __repr__(self):
        extra = ""
        if self.launch is not None:
            extra += f', launch="{self.launch}"'
        if self.reverse:
            extra += ", reverse=True"
        return f'{type(self).__name__}(app="{self.app}"{extra})'


class ActivateWindow(ActivateApplication):
    """Bring an application's window to the front, by name pattern.

    The front-most window of the first application that matches, restored
    first if it was minimized.  An application that is running but shows no
    window the platform can enumerate is activated by pid instead.

    ```python
    ActivateWindow(app="code|Visual Studio Code")
    ```

    `ActivateApplication` is this with the rest of "go to that application"
    attached: it walks the windows on a second press, and launches what is not
    running.
    """

    # A portable subset of keyhac-win's ActivateWindowCommand, and now the
    # no-cycle, no-launch case of the action above.

    def __init__(self, app: str):
        """Build the action.

        Args:
            app: Application name pattern, matched like define_keytable's
                app= - case-insensitive, fnmatch wildcards, "|" alternation,
                ".exe" optional.
        """
        super().__init__(app=app, launch=None, cycle=False)

    def __repr__(self):
        return f'ActivateWindow(app="{self.app}")'
