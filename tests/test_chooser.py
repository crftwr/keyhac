"""Chooser single-instance behavior (issue #3: a second invocation must not
stack another chooser window). UI is tested against puikit's MemoryBackend."""

import pytest

from keyhac.actions import ChooserAction
from keyhac.core.keymap import Keymap
from keyhac.platform.base import Window, WindowProvider
from keyhac.ui import runtime


class _Items(ChooserAction):

    def __init__(self):
        self.chosen = []

    def list_items(self):
        return [("*", "alpha", "a"), ("*", "beta", "b")]

    def on_chosen(self, item, modifier_flags):
        self.chosen.append(item)


@pytest.fixture
def keyhac_engine(engine):
    """The Keymap the actions look up, plus its fake hook and focus provider.
    A separate fixture from ui_backend so a test can drive both; pytest hands
    the same instance to each."""
    def configure(keymap):
        keymap.define_keytable(focus_path_pattern="*")

    return engine(configure)


@pytest.fixture
def ui_backend(keyhac_engine):
    from puikit.backends.memory_backend import MemoryBackend
    backend = MemoryBackend(width=100, height=30)
    backend.open()
    runtime.backend = backend
    yield backend
    runtime.backend = None
    ChooserAction._open = None
    ChooserAction._pending = None
    ChooserAction._stop_watch()
    backend.close()


class TestOpeningLeavesTheHookCallback:
    """A key hook's callback has a deadline, and overrunning it does not just
    make things slow: Windows drops the hook and hands the event that overran
    to the application anyway, so the key that opened the chooser turns up in
    whatever the user was typing in (macOS disables a slow tap the same way).
    Building the window is well past that budget - the chooser module's import
    alone is 390 ms on the first press - so the callback returns first and the
    window is built on the next turn of the loop.

    These tests wire a dispatcher, which is what production has; without one
    the callback runs inline, which is what every other test here relies on.
    """

    def _queue(self, keyhac_engine):
        queued = []
        keyhac_engine.keymap.set_main_thread_dispatcher(queued.append)
        return queued

    def test_nothing_is_built_until_the_loop_turns(self, ui_backend,
                                                   keyhac_engine):
        queued = self._queue(keyhac_engine)
        action = _Items()
        action()
        assert ChooserAction._open is None, "built inside the hook callback"
        assert len(queued) == 1
        queued[0]()
        assert ChooserAction._open is not None
        assert ChooserAction._pending is None

    def test_a_second_press_before_it_opens_is_still_the_toggle(
            self, ui_backend, keyhac_engine):
        """The window has not appeared, so there is nothing on screen to
        close - dropping the queued open is what closing means here."""
        queued = self._queue(keyhac_engine)
        action = _Items()
        action()
        action()
        for callback in queued:
            callback()
        assert ChooserAction._open is None

    def test_another_action_pressed_meanwhile_takes_over(
            self, ui_backend, keyhac_engine):
        queued = self._queue(keyhac_engine)
        first, second = _Items(), _Items()
        first()
        second()
        for callback in queued:
            callback()
        assert ChooserAction._open is not None
        assert ChooserAction._open[0] is second

    def test_the_press_and_its_release_open_exactly_one_window(
            self, ui_backend, keyhac_engine):
        """The whole flow a key performs, not just the action call: the down
        runs the action and the up fires the one-shot condition behind it.
        Neither may cancel the open the other queued - a queued open that a
        release drops is a chooser that never appears."""
        queued = self._queue(keyhac_engine)
        action = _Items()
        table = keyhac_engine.keymap.define_keytable(focus_path_pattern="*")
        table["A"] = action

        assert keyhac_engine.down("A") is True
        keyhac_engine.up("A")
        for callback in queued:
            callback()

        assert ChooserAction._open is not None
        assert ChooserAction._open[0] is action
        assert not ChooserAction._open[1]._done

    def test_a_failure_while_opening_no_longer_leaks_the_key(
            self, ui_backend, keyhac_engine):
        """What the leak actually looked like. The engine passes a key through
        when handling it *raises* - deliberately, so a broken config cannot
        swallow the keyboard - so while the window was built inside the
        callback, any failure in building it (a source that could not be read,
        a window that could not be created) sent the key that opened the
        chooser to the application underneath: the P of a `User0-P` arriving
        in whatever the user was typing in. Out of the callback, the key is
        consumed either way and the failure is reported as the action's."""
        class _Broken(ChooserAction):
            def list_items(self):
                raise RuntimeError("a source that could not be read")

        keymap = keyhac_engine.keymap
        table = keymap.define_keytable(focus_path_pattern="*")
        table["A"] = _Broken()
        assert keyhac_engine.down("A") is True, "the key must not reach the app"
        assert ChooserAction._open is None
        keyhac_engine.up("A")

    def test_an_open_window_is_closed_in_the_callback_not_deferred(
            self, ui_backend, keyhac_engine):
        """Only *building* one is expensive; dismissing is a window close, and
        leaving it queued would let a second chooser be built over the first."""
        queued = self._queue(keyhac_engine)
        action = _Items()
        action()
        queued.pop()()
        chooser = ChooserAction._open[1]
        action()
        assert chooser._done
        assert ChooserAction._open is None
        assert not queued, "closing needs no turn of the loop"


class TestChooserSingleInstance:

    def test_same_action_toggles(self, ui_backend):
        action = _Items()
        action()
        assert ChooserAction._open is not None
        first = ChooserAction._open[1]
        assert not first._done

        action()  # same action again: close, do not reopen
        assert ChooserAction._open is None
        assert first._done

    def test_different_action_replaces(self, ui_backend):
        first_action, second_action = _Items(), _Items()
        first_action()
        first = ChooserAction._open[1]

        second_action()
        assert ChooserAction._open is not None
        assert ChooserAction._open[0] is second_action
        assert ChooserAction._open[1] is not first
        assert first._done

    def test_selection_clears_registry(self, ui_backend):
        action = _Items()
        action()
        chooser = ChooserAction._open[1]
        # The rows are Candidates internally (discussion #112); a tuple
        # source still gets its own tuple back in on_chosen.
        chooser._finish(chooser._filtered[0], 0)
        assert ChooserAction._open is None
        assert action.chosen == [("*", "alpha", "a")]

    def test_cancel_clears_registry(self, ui_backend):
        action = _Items()
        action()
        chooser = ChooserAction._open[1]
        chooser._finish(None, 0)
        assert ChooserAction._open is None
        assert action.chosen == []


class TestChooserScrolling:
    """Issue #27: the chooser routes keys itself and assigns the list's
    `selected` directly, so the scroll has to come with the assignment -
    puikit scrolls the selection into view on assignment."""

    def test_selection_moved_past_the_viewport_stays_visible(self, ui_backend):
        from puikit.event import Event, EventType
        from keyhac.ui.chooser import ChooserWindow

        items = [("*", f"entry {i:02}", i) for i in range(40)]
        chooser = ChooserWindow(ui_backend, items)
        # The first Down steps out of the filter field onto row 0, so 31
        # presses reach row 30.
        for _ in range(31):
            chooser._on_event(Event(type=EventType.KEY, key="down"))
        assert chooser._list.selected == 30
        rows = ["".join(row) for row in chooser.window.snapshot()]
        assert any("entry 30" in row for row in rows), \
            "the selected row scrolled out of view (issue #27)"


class TestChooserCentering:
    """Where the window opens: the focused window's centre (issue #4), or
    under the caret when there is one to sit under (issue #118).

    The memory backend creates 72x20 windows at (160, 160) with 1 px per
    cell, so the geometry below is exact."""

    def _window(self, ui_backend, **kwargs):
        from keyhac.ui.chooser import ChooserWindow
        return ChooserWindow(ui_backend, [("*", "a", 1)], **kwargs)

    def test_default_position_untouched(self, ui_backend):
        chooser = self._window(ui_backend)
        assert chooser.window.frame_px() == (160.0, 160.0, 72.0, 20.0)

    def test_centers_on_rect(self, ui_backend):
        chooser = self._window(ui_backend, center_on=(100, 100, 400, 300))
        # center (300, 250) minus half of 72x20
        assert chooser.window.frame_px() == (264.0, 240.0, 72.0, 20.0)

    def test_clamped_to_screen(self, ui_backend):
        chooser = self._window(ui_backend, center_on=(100, 100, 400, 300),
                               clamp_to=(0, 0, 300, 250))
        assert chooser.window.frame_px() == (228.0, 230.0, 72.0, 20.0)

    def test_clamped_at_origin(self, ui_backend):
        chooser = self._window(ui_backend, center_on=(-500, -500, 100, 100),
                               clamp_to=(0, 0, 800, 600))
        assert chooser.window.frame_px() == (0.0, 0.0, 72.0, 20.0)

    def test_below_sits_under_the_caret(self, ui_backend):
        chooser = self._window(ui_backend, below=(300, 400, 0, 18),
                               clamp_to=(0, 0, 1000, 800))
        assert chooser.window.frame_px() == (300.0, 422.0, 72.0, 20.0)

    def test_below_wins_over_the_window_s_centre(self, ui_backend):
        """Both are answers to "where does it open"; one of them knows where
        the eye already is."""
        chooser = self._window(ui_backend, below=(300, 400, 0, 18),
                               center_on=(100, 100, 400, 300),
                               clamp_to=(0, 0, 1000, 800))
        assert chooser.window.frame_px()[:2] == (300.0, 422.0)

    def test_below_flips_above_a_caret_near_the_bottom(self, ui_backend):
        chooser = self._window(ui_backend, below=(300, 780, 0, 18),
                               clamp_to=(0, 0, 1000, 800))
        assert chooser.window.frame_px()[:2] == (300.0, 756.0)


class TestChooserFiltering:
    """Filtering goes through a pluggable Matcher (discussion #112); the
    default reproduces the multi-word substring behaviour shipped in 2.0."""

    def _chooser(self, ui_backend, **kwargs):
        from keyhac.ui.chooser import ChooserWindow
        items = [("*", "alpha", 1), ("*", "beta", 2), ("*", "alpine", 3)]
        return ChooserWindow(ui_backend, items, **kwargs)

    def _labels(self, chooser):
        return [c.display for c in chooser._filtered]

    def test_default_is_multi_word_substring(self, ui_backend):
        chooser = self._chooser(ui_backend)
        chooser._on_filter_change("al")
        assert self._labels(chooser) == ["alpha", "alpine"]
        chooser._on_filter_change("al ne")
        assert self._labels(chooser) == ["alpine"]

    def test_empty_query_restores_every_row(self, ui_backend):
        chooser = self._chooser(ui_backend)
        chooser._on_filter_change("al")
        chooser._on_filter_change("")
        assert self._labels(chooser) == ["alpha", "beta", "alpine"]

    def test_wildcards_are_literal_under_the_default_matcher(self, ui_backend):
        chooser = self._chooser(ui_backend)
        chooser._on_filter_change("al*a")
        assert self._labels(chooser) == []

    def test_wildcard_matcher_can_be_selected(self, ui_backend):
        from keyhac.core.matcher import WildcardMatcher
        chooser = self._chooser(ui_backend, matcher=WildcardMatcher())
        chooser._on_filter_change("al*a")
        assert self._labels(chooser) == ["alpha"]

    def test_candidate_rows_are_accepted_directly(self, ui_backend):
        from keyhac.core.candidate import Candidate
        from keyhac.ui.chooser import ChooserWindow

        chosen = []
        target = Candidate(display="notes.txt", match_text="/home/u/notes.txt",
                           payload=object())
        chooser = ChooserWindow(
            ui_backend, [target, Candidate(display="other")],
            on_selected=lambda c, mod: chosen.append(c))
        # Matched on the full path, displayed as the basename.
        chooser._on_filter_change("home")
        assert self._labels(chooser) == ["notes.txt"]
        chooser._finish(chooser._filtered[0], 0)
        assert chosen == [target]

    def test_action_can_choose_its_matcher(self, ui_backend):
        from keyhac.core.matcher import WildcardMatcher

        class _Wild(_Items):
            matcher = WildcardMatcher()

        action = _Wild()
        action()
        chooser = ChooserAction._open[1]
        assert chooser._matcher is _Wild.matcher


class TestChooserActivation:
    """The chooser does not take OS keyboard focus by default (discussion
    #112).  What that removes is the app-scoped activation - which brought
    the console forward, could follow the app to another Space, and forced
    a settle delay before pasting."""

    class _Recorder:
        """Stands in for the platform AppControl, recording activations."""

        def __init__(self):
            self.activated = []

        def activate_pid(self, pid):
            self.activated.append(pid)
            return True

    def _wire(self, ui_backend):
        from keyhac.core.keymap import Keymap
        keymap = Keymap.get_instance()
        # The focus snapshot is normally taken on the first key event; the
        # activating path reads it to know whom to give the focus back to.
        keymap._check_focus_change()
        recorder = self._Recorder()
        keymap.app_control = recorder
        return keymap, recorder

    def test_default_activates_nothing(self, ui_backend):
        _keymap, recorder = self._wire(ui_backend)
        action = _Items()
        action()
        assert recorder.activated == []

    def test_selection_does_not_refocus(self, ui_backend):
        _keymap, recorder = self._wire(ui_backend)
        action = _Items()
        action()
        chooser = ChooserAction._open[1]
        chooser._finish(chooser._filtered[0], 0)
        assert recorder.activated == []
        assert action.chosen == [("*", "alpha", "a")]

    def test_toggling_closed_does_not_refocus(self, ui_backend):
        _keymap, recorder = self._wire(ui_backend)
        action = _Items()
        action()
        action()  # same action again: closes it
        assert ChooserAction._open is None
        assert recorder.activated == []

    def test_opting_in_restores_the_old_behaviour(self, ui_backend):
        import os

        _keymap, recorder = self._wire(ui_backend)

        class _Focused(_Items):
            activates = True

        action = _Focused()
        action()
        assert recorder.activated == [os.getpid()]

        chooser = ChooserAction._open[1]
        chooser._finish(chooser._filtered[0], 0)
        # ... and hands the focus back to the application it took it from.
        original_pid = _keymap.focus.pid
        assert recorder.activated == [os.getpid(), original_pid]


class TestClipboardPaste:
    """The settle delay existed only to let a re-activated application catch
    up; with no activation there is nothing to wait for.  The behaviour lives
    on the source now (`keyhac.core.sources`), so it is tested there - a
    clipboard row means the same thing whether it is reached through its own
    hotkey or through a window shared with other sources."""

    def _wire(self, ui_backend, tmp_path, monkeypatch, took_focus):
        from keyhac.core import sources
        from keyhac.core.clipboard_history import ClipboardHistory
        from keyhac.core.keymap import Keymap
        from keyhac.platform.base import ClipboardProvider

        class _Board(ClipboardProvider):
            def __init__(self):
                self.text = None

            def get_text(self):
                return self.text

            def set_text(self, s):
                self.text = s

            def poll(self):
                return False

        keymap = Keymap.get_instance()
        keymap._clipboard_history = ClipboardHistory(
            _Board(), str(tmp_path / "clipboard.json"))
        pasted, deferred = [], []
        monkeypatch.setattr(sources, "_send_paste", lambda: pasted.append(True))
        monkeypatch.setattr(sources, "_chooser_took_focus", lambda: took_focus)
        ui_backend.call_later = lambda delay, fn: deferred.append((delay, fn))
        return keymap, sources._PastingSource(), pasted, deferred

    def test_paste_is_immediate_without_activation(self, ui_backend, tmp_path,
                                                   monkeypatch):
        keymap, source, pasted, deferred = self._wire(
            ui_backend, tmp_path, monkeypatch, took_focus=False)
        source.paste("hello", 0)
        assert pasted == [True]
        assert deferred == []
        assert keymap.clipboard_history.get_current() == "hello"

    def test_paste_still_waits_when_the_chooser_took_focus(
            self, ui_backend, tmp_path, monkeypatch):
        _keymap, source, pasted, deferred = self._wire(
            ui_backend, tmp_path, monkeypatch, took_focus=True)
        source.paste("hello", 0)
        assert pasted == []
        assert len(deferred) == 1 and deferred[0][0] > 0

    def test_shift_select_never_pastes(self, ui_backend, tmp_path, monkeypatch):
        from keyhac.core.const import MODKEY_SHIFT
        keymap, source, pasted, deferred = self._wire(
            ui_backend, tmp_path, monkeypatch, took_focus=False)
        source.paste("hello", MODKEY_SHIFT)
        assert pasted == [] and deferred == []
        assert keymap.clipboard_history.get_current() == "hello", \
            "Shift-Enter still sets the clipboard; it only skips the paste"


class _ActiveWindow(Window):
    """The frontmost window, as the dismissal watch reads it."""

    def __init__(self, pid, title):
        self._pid = pid
        self._title = title

    @property
    def title(self):
        return self._title

    @property
    def app_name(self):
        return "FakeApp"

    @property
    def pid(self):
        return self._pid

    def get_frame(self):
        return (0, 0, 800, 600)

    def set_frame(self, x, y, w=None, h=None):
        return True

    def activate(self):
        return True

    def is_minimized(self):
        return False

    def restore(self):
        return True


class _ActiveWindowProvider(WindowProvider):

    def __init__(self):
        self.active = _ActiveWindow(1, "Original Window")

    def get_active_window(self):
        return self.active

    def list_windows(self):
        return [self.active] if self.active else []

    def screen_frames(self):
        return [(0, 0, 1920, 1080)]

    def screen_work_frames(self):
        return [(0, 0, 1920, 1040)]

    def window_frames(self):
        return [w.get_frame() for w in self.list_windows()]


class TestAutoDismiss:
    """A chooser is transient: it closes when the world it was opened over
    moves (discussion #112).  Without this, one could survive on another
    virtual desktop and the hotkey would toggle closed a window the user
    could not see - which read as the chooser refusing to open."""

    def _open(self, ui_backend):
        action = _Items()
        action()
        assert ChooserAction._open is not None
        return action, ChooserAction._open[1]

    def _move_focus_to(self, _unused, *, pid, title):
        """Put a different window in front.

        The watch reads the *active window*, not the keyboard focus - a Focus
        mixes its sources and our own popup can appear in half of it - so the
        test drives the window provider.
        """
        provider = Keymap.get_instance().window_provider
        provider.active = _ActiveWindow(pid, title)

    def _tick(self, ui_backend):
        """Run the watch's pending timer once."""
        ChooserAction._watch._tick()

    @pytest.fixture(autouse=True)
    def _windows(self, keyhac_engine):
        keyhac_engine.keymap.window_provider = _ActiveWindowProvider()

    def test_survives_while_nothing_moves(self, ui_backend):
        _action, chooser = self._open(ui_backend)
        self._tick(ui_backend)
        assert ChooserAction._open is not None
        assert not chooser._done

    def test_closes_when_another_window_comes_forward(self, keyhac_engine,
                                                     ui_backend):
        _action, chooser = self._open(ui_backend)
        self._move_focus_to(keyhac_engine.focus_provider, pid=99, title="Other Window")
        self._tick(ui_backend)
        assert ChooserAction._open is None
        assert chooser._done

    def test_closes_when_the_same_app_shows_a_different_window(
            self, keyhac_engine, ui_backend):
        """A desktop switch reads as this: same application, other window.
        Keying on the pid alone would miss it."""
        original = keyhac_engine.focus_provider.focus
        _action, chooser = self._open(ui_backend)
        self._move_focus_to(keyhac_engine.focus_provider, pid=original.pid,
                            title="Window On The Other Desktop")
        self._tick(ui_backend)
        assert ChooserAction._open is None

    def test_in_window_focus_moves_do_not_close_it(self, keyhac_engine,
                                                   ui_backend):
        """Tabbing between fields moves the keyboard focus without changing
        the window.  Reading the active window makes that a non-event by
        construction; the macOS focus *path* would have changed."""
        _action, chooser = self._open(ui_backend)
        keyhac_engine.focus_provider.focus = None   # the focus route moved
        self._tick(ui_backend)
        assert ChooserAction._open is not None

    def test_an_unreadable_active_window_closes_nothing(self, keyhac_engine,
                                                        ui_backend):
        _action, chooser = self._open(ui_backend)
        Keymap.get_instance().window_provider.active = None
        self._tick(ui_backend)
        assert ChooserAction._open is not None

    def test_a_click_on_the_popup_cannot_look_like_a_move(self, keyhac_engine,
                                                          ui_backend):
        """The bug this read replaced: a Focus carries the frontmost app's pid
        but the AX-focused app's window title, and clicking the popup turns
        the title into ours while the pid still says the target.  The active
        window never reports our popup at all."""
        _action, chooser = self._open(ui_backend)
        keyhac_engine.focus_provider.focus = None
        self._tick(ui_backend)
        assert ChooserAction._open is not None
        assert not chooser._done

    def test_a_click_outside_closes_it(self, keyhac_engine, ui_backend):
        _action, chooser = self._open(ui_backend)
        x, y, w, h = chooser.window.frame_px()
        keyhac_engine.hook._cursor = (int(x + w + 50), int(y + h + 50))
        keyhac_engine.hook.mouse()
        assert ChooserAction._open is None
        assert chooser._done

    def test_a_click_on_the_chooser_does_not(self, keyhac_engine, ui_backend):
        _action, chooser = self._open(ui_backend)
        x, y, w, h = chooser.window.frame_px()
        keyhac_engine.hook._cursor = (int(x + w / 2), int(y + h / 2))
        keyhac_engine.hook.mouse()
        assert ChooserAction._open is not None
        assert not chooser._done

    def test_dismissal_never_refocuses(self, keyhac_engine, ui_backend):
        """The user moved away on purpose; pulling them back would undo it."""
        from keyhac.core.keymap import Keymap

        class _Recorder:
            def __init__(self):
                self.activated = []

            def activate_pid(self, pid):
                self.activated.append(pid)
                return True

        recorder = _Recorder()
        Keymap.get_instance().app_control = recorder

        class _Focused(_Items):
            activates = True

        action = _Focused()
        action()
        recorder.activated.clear()          # drop the open-time self-activation
        self._move_focus_to(keyhac_engine.focus_provider, pid=99, title="Elsewhere")
        ChooserAction._watch._tick()
        assert ChooserAction._open is None
        assert recorder.activated == []

    def test_dismissal_releases_the_key_grab(self, keyhac_engine, ui_backend):
        _action, _chooser = self._open(ui_backend)
        assert keyhac_engine.keymap.modal_input_active()
        self._move_focus_to(keyhac_engine.focus_provider, pid=99, title="Elsewhere")
        ChooserAction._watch._tick()
        assert not keyhac_engine.keymap.modal_input_active()

    def test_the_watch_is_torn_down_with_the_chooser(self, ui_backend):
        from keyhac.core.keymap import Keymap
        _action, chooser = self._open(ui_backend)
        assert Keymap.get_instance().on_mouse_button is not None
        chooser._finish(chooser._filtered[0], 0)
        assert ChooserAction._watch is None
        assert Keymap.get_instance().on_mouse_button is None

    def test_focus_landing_on_keyhac_itself_closes_nothing(
            self, keyhac_engine, ui_backend):
        """Clicking the popup can make Keyhac the AX-focused application on
        macOS even though a borderless window cannot take key status. That is
        the chooser's own doing, not the user leaving."""
        import os
        _action, chooser = self._open(ui_backend)
        self._move_focus_to(keyhac_engine.focus_provider,
                            pid=os.getpid(), title="Keyhac")
        ChooserAction._watch._tick()
        assert ChooserAction._open is not None
        assert not chooser._done

    def test_an_activating_chooser_survives_its_own_activation(
            self, keyhac_engine, ui_backend):
        """activates=True focuses us on purpose; the watch is armed before
        that happens, so without the self-check the first tick would close
        the chooser the user just opened."""
        import os

        class _Focused(_Items):
            activates = True

        action = _Focused()
        action()
        self._move_focus_to(keyhac_engine.focus_provider,
                            pid=os.getpid(), title="Keyhac")
        ChooserAction._watch._tick()
        assert ChooserAction._open is not None

    def test_moving_on_from_keyhac_still_closes_it(
            self, keyhac_engine, ui_backend):
        """The self-check is "no reading", not "never close": once the focus
        lands somewhere that is not us, the watch works again."""
        import os
        _action, chooser = self._open(ui_backend)
        self._move_focus_to(keyhac_engine.focus_provider,
                            pid=os.getpid(), title="Keyhac")
        ChooserAction._watch._tick()
        self._move_focus_to(keyhac_engine.focus_provider,
                            pid=99, title="Somewhere Else")
        ChooserAction._watch._tick()
        assert ChooserAction._open is None

    def test_a_wheel_turn_outside_does_not_close_it(self, keyhac_engine,
                                                    ui_backend):
        """macOS scrolls the window under the pointer without focusing it, so
        a wheel turn over a background window is not the user leaving.
        Spotlight survives it too."""
        _action, chooser = self._open(ui_backend)
        x, y, w, h = chooser.window.frame_px()
        keyhac_engine.hook._cursor = (int(x + w + 50), int(y + h + 50))
        keyhac_engine.hook.mouse("wheel")
        assert ChooserAction._open is not None
        assert not chooser._done

    def test_a_wheel_turn_still_cancels_a_one_shot(self, keyhac_engine,
                                                   ui_backend):
        """Only the dismissal is button-only; the one-shot cancellation this
        signal was originally for still fires on either."""
        keyhac_engine.keymap._last_keydown = 1
        keyhac_engine.hook.mouse("wheel")
        assert keyhac_engine.keymap._last_keydown is None


class TestChooserFocus:
    """Two panes, one at a time (the filter field and the list).

    While the field has the focus the list shows *no* selection - it is a
    preview of what matches, not a proposal - and the field is never more
    than one keystroke away from wherever the user is.
    """

    def _chooser(self, ui_backend, **kwargs):
        from keyhac.ui.chooser import ChooserWindow
        items = [("*", "alpha", 1), ("*", "beta", 2), ("*", "alpine", 3)]
        return ChooserWindow(ui_backend, items, **kwargs)

    def _key(self, chooser, key, char=None, modifiers=()):
        from puikit.event import Event, EventType
        chooser._on_event(Event(type=EventType.KEY, key=key, char=char,
                                modifiers=frozenset(modifiers)))

    def test_nothing_is_selected_while_typing(self, ui_backend):
        chooser = self._chooser(ui_backend)
        assert chooser._list.selected == -1
        assert not chooser.in_list
        chooser._on_filter_change("al")
        assert chooser._list.selected == -1

    def test_down_steps_into_the_list_at_the_top(self, ui_backend):
        chooser = self._chooser(ui_backend)
        self._key(chooser, "down")
        assert chooser.in_list
        assert chooser._list.selected == 0

    def test_up_off_the_first_row_returns_to_the_field(self, ui_backend):
        chooser = self._chooser(ui_backend)
        self._key(chooser, "down")
        self._key(chooser, "down")
        assert chooser._list.selected == 1
        self._key(chooser, "up")
        assert chooser._list.selected == 0
        self._key(chooser, "up")
        assert not chooser.in_list
        assert chooser._list.selected == -1

    def test_a_page_off_the_top_also_returns_to_the_field(self, ui_backend):
        chooser = self._chooser(ui_backend)
        self._key(chooser, "down")
        self._key(chooser, "pageup")
        assert not chooser.in_list

    def test_up_in_the_field_does_not_jump_to_the_bottom(self, ui_backend):
        chooser = self._chooser(ui_backend)
        self._key(chooser, "up")
        assert not chooser.in_list

    def test_typing_in_the_list_returns_to_the_field(self, ui_backend):
        chooser = self._chooser(ui_backend)
        self._key(chooser, "down")
        assert chooser.in_list
        self._key(chooser, "b", char="b")
        assert not chooser.in_list
        assert chooser._edit.text == "b"
        assert [c.display for c in chooser._filtered] == ["beta"]

    def test_editing_the_query_from_the_list_returns_to_the_field(
            self, ui_backend):
        """Backspace with the focus still in the list did nothing at all,
        which reads as the window ignoring you - the query is on screen with
        a caret in it. Anything addressed to the query goes to the field, and
        the keystroke that got there is not spent on getting there."""
        chooser = self._chooser(ui_backend)
        self._key(chooser, "b", char="b")
        self._key(chooser, "e", char="e")
        self._key(chooser, "down")
        assert chooser.in_list
        self._key(chooser, "backspace")
        assert not chooser.in_list
        assert chooser._edit.text == "b", "the key edits, it does not only move"

    def test_moving_the_caret_from_the_list_returns_to_the_field(
            self, ui_backend):
        """Bare Left and Right belong to the pages now. Their modifier forms
        do not - Ctrl-Left is a word, Shift-Left a selection, Cmd-Left the
        line start - and all arrive under the same name, so the field has to
        get them back or it loses all three."""
        chooser = self._chooser(ui_backend)
        self._key(chooser, "a", char="a")
        for key, modifiers in (("right", ("ctrl",)), ("delete", ()),
                               ("left", ("shift",)), ("left", ("cmd",))):
            self._key(chooser, "down")
            assert chooser.in_list
            self._key(chooser, key, modifiers=modifiers)
            assert not chooser.in_list, f"{key} {modifiers} should leave the list"

    def test_a_bare_arrow_stays_with_the_pages(self, ui_backend):
        """The other half of the rule above."""
        chooser = self._chooser(ui_backend)
        self._key(chooser, "down")
        assert chooser.in_list
        self._key(chooser, "left")
        assert chooser.in_list, "a bare arrow is the pages', not the field's"

    def test_home_and_end_stay_with_the_list(self, ui_backend):
        """The one pair that is not the caret's: a list long enough to want a
        first and last row wants them for that. They move the selection
        through _navigate like every other move, so the outline on screen
        follows instead of staying on the row it just left."""
        chooser = self._chooser(ui_backend)
        self._key(chooser, "down")
        self._key(chooser, "end")
        assert chooser.in_list
        assert chooser._list.selected == len(chooser._filtered) - 1
        self._key(chooser, "home")
        assert chooser.in_list
        assert chooser._list.selected == 0

    def test_home_and_end_in_the_field_are_the_caret_s(self, ui_backend):
        """They must not step into the list from the field - there they are
        the line start and end of the query."""
        chooser = self._chooser(ui_backend)
        self._key(chooser, "end")
        assert not chooser.in_list
        self._key(chooser, "home")
        assert not chooser.in_list

    def test_changing_the_filter_deselects(self, ui_backend):
        chooser = self._chooser(ui_backend)
        self._key(chooser, "down")
        chooser._on_filter_change("al")
        assert not chooser.in_list

    def test_enter_while_typing_takes_the_top_match(self, ui_backend):
        from keyhac.ui.chooser import ChooserWindow
        chosen = []
        items = [("*", "alpha", 1), ("*", "beta", 2), ("*", "alpine", 3)]
        chooser = ChooserWindow(ui_backend, items,
                                on_selected=lambda item, mod: chosen.append(item))
        chooser._on_filter_change("al")
        self._key(chooser, "enter")
        # The window hands back the Candidate; unwrapping to the tuple is
        # ChooserAction's business, not the window's.
        assert [c.payload for c in chosen] == [("*", "alpha", 1)]

    def test_enter_in_the_list_takes_the_selected_row(self, ui_backend):
        from keyhac.ui.chooser import ChooserWindow
        chosen = []
        items = [("*", "alpha", 1), ("*", "beta", 2), ("*", "alpine", 3)]
        chooser = ChooserWindow(ui_backend, items,
                                on_selected=lambda item, mod: chosen.append(item))
        self._key(chooser, "down")
        self._key(chooser, "down")
        self._key(chooser, "enter")
        assert [c.payload for c in chosen] == [("*", "beta", 2)]

    def test_down_with_no_matches_stays_in_the_field(self, ui_backend):
        chooser = self._chooser(ui_backend)
        chooser._on_filter_change("zzz")
        self._key(chooser, "down")
        assert not chooser.in_list

    def test_a_click_selects_and_does_not_confirm(self, ui_backend):
        from puikit.event import Event, EventType
        from keyhac.ui.chooser import ChooserWindow
        chosen = []
        items = [("*", "alpha", 1), ("*", "beta", 2), ("*", "alpine", 3)]
        chooser = ChooserWindow(ui_backend, items,
                                on_selected=lambda item, mod: chosen.append(item))
        chooser._list.handle_event(
            Event(type=EventType.MOUSE_CLICK, x=0, y=1, button="left"))
        assert chooser._list.selected == 1
        assert chooser.in_list
        assert chosen == [], "a click picks the row; Enter chooses it"

    def test_the_selection_draws_as_active_while_the_list_has_focus(
            self, ui_backend):
        """Grey means "focus is elsewhere".  The list is nested inside the
        Frame, and a child draws focused only if every container above it is
        focused too - so naming the list to the page marks nothing and the
        accent never appears."""
        chooser = self._chooser(ui_backend)
        theme = chooser.panel.theme
        assert theme.selection_active_bg != theme.selection_inactive_bg

        self._key(chooser, "down")
        chooser.panel.render()
        row = self._selected_row_bg(chooser)
        assert row == theme.selection_active_bg, \
            f"selection drew {row}, expected the focused accent"

    def test_the_selection_is_not_drawn_at_all_from_the_field(self, ui_backend):
        chooser = self._chooser(ui_backend)
        theme = chooser.panel.theme
        chooser.panel.render()
        assert self._selected_row_bg(chooser) not in (
            theme.selection_active_bg, theme.selection_inactive_bg)

    def _selected_row_bg(self, chooser):
        """Background of the row the list currently points at, read off the
        rendered window."""
        rows = ["".join(r) for r in chooser.window.snapshot()]
        index = max(chooser._list.selected, 0)
        label = chooser._filtered[index].display
        for y, text in enumerate(rows):
            if label in text:
                return chooser.window.style_at(text.index(label), y).bg
        raise AssertionError(f"{label!r} not found in the rendered window")


class TestBalloonIsAMark:
    """A balloon used to be a frameless topmost non-activating window with a
    Label in it - five window-style fields spelling out "a tooltip", and it
    could be clicked, which for a tooltip is simply wrong."""

    class _Backend:
        base_size = (8, 16)

        def __init__(self):
            self.marks = []

        def screen_frames(self):
            return [((0, 0, 1920, 1080), (0, 25, 1920, 1055))]

        def mark_screen(self, x, y, w=None, h=None, **kwargs):
            mark = _Mark(x, y, kwargs)
            self.marks.append(mark)
            return mark

    def _manager(self):
        from keyhac.ui.balloon import BalloonManager
        backend = self._Backend()
        return BalloonManager(backend), backend

    def test_a_balloon_is_one_mark(self):
        manager, backend = self._manager()
        manager.pop("help", "Multi-stroke: sub")
        assert len(backend.marks) == 1
        assert backend.marks[0].kwargs["text"] == "Multi-stroke: sub"

    def test_it_wraps_instead_of_being_squeezed_onto_one_line(self):
        """The old window sized itself with min(70, len(text) + 4), which was
        a wrap width with no name and no way for a long balloon to do
        anything but be cut short."""
        manager, backend = self._manager()
        manager.pop("help", "x" * 400)
        assert backend.marks[0].kwargs["max_width"] == 70 * 8

    def test_it_sits_in_the_work_area_s_top_right(self):
        manager, backend = self._manager()
        manager.pop("help", "hi")
        mark = backend.marks[0]
        assert mark.y == 25 + 24
        assert mark.x == 1920 - 70 * 8 - 24

    def test_a_caret_puts_it_under_the_caret(self):
        """Multi-stroke help arrives while the user is typing, and a corner of
        the screen is not where they are looking."""
        manager, backend = self._manager()
        manager.pop("help", "Multi-stroke: sub", near=(400.0, 300.0, 0.0, 18.0))
        mark = backend.marks[0]
        assert (mark.x, mark.y) == (400.0, 322.0)

    def test_a_short_balloon_near_the_right_edge_stays_at_the_caret(self):
        """The bug this replaced: measured against the wrap width instead of
        its own, an eighteen-character balloon at x=1406 on a 1710 screen
        decided it would not fit and clamped a quarter of the screen left of
        the caret it was meant to be under."""
        manager, backend = self._manager()
        manager.pop("help", "Multi-stroke: Fn-X", near=(1500.0, 300.0, 0.0, 18.0))
        assert backend.marks[0].x == 1500.0

    def test_a_short_balloon_is_clamped_by_its_own_width(self):
        manager, backend = self._manager()
        manager.pop("help", "hi", near=(1900.0, 300.0, 0.0, 18.0))
        assert backend.marks[0].x == 1920 - (2 * 8 + 24)

    def test_a_long_balloon_is_clamped_by_the_wrap_width(self):
        """It really is that wide once it wraps, so it really does have to
        move that far."""
        manager, backend = self._manager()
        manager.pop("help", "x" * 300, near=(1900.0, 300.0, 0.0, 18.0))
        assert backend.marks[0].x == 1920 - (70 * 8 + 24)

    def test_no_caret_is_still_the_corner(self):
        manager, backend = self._manager()
        manager.pop("help", "hi", near=None)
        assert backend.marks[0].y == 25 + 24

    def test_popping_the_same_name_replaces_it(self):
        manager, backend = self._manager()
        manager.pop("help", "first")
        manager.pop("help", "second")
        assert backend.marks[0].closed
        assert not backend.marks[1].closed

    def test_the_timeout_is_the_mark_s(self):
        """Rather than a call_later of the balloon's own: closing is what a
        mark already knows how to schedule."""
        manager, backend = self._manager()
        manager.pop("help", "hi", timeout=2.0)
        assert backend.marks[0].kwargs["timeout"] == 2.0

    def test_closing_by_name_and_closing_all(self):
        manager, backend = self._manager()
        manager.pop("one", "a")
        manager.pop("two", "b")
        manager.close("one")
        assert backend.marks[0].closed and not backend.marks[1].closed
        manager.close()
        assert backend.marks[1].closed

    def test_multi_stroke_help_reads_the_caret_where_it_is_now(self):
        """The provider is asked when the prefix is armed, rather than the
        snapshot being taken at its word."""
        from keyhac.ui.balloon import multi_stroke_help
        manager, backend = self._manager()
        multi_stroke_help(manager, _Keymap(_Focused(
            caret=(400.0, 300.0, 0.0, 18.0),
            rect=(390.0, 290.0, 300.0, 40.0))))("sub")
        mark = backend.marks[0]
        assert mark.kwargs["text"] == "Multi-stroke: sub"
        # 330 is the field's bottom edge, not the caret's - a balloon under
        # the caret would cover the box it was typed into.
        assert (mark.x, mark.y) == (400.0, 334.0)

    def test_a_snapshot_that_has_gone_stale_does_not_place_the_balloon(self):
        """Windows caches the focus on (foreground window, focused child
        window, title), none of which change when the focus moves inside a
        Chromium window - the whole UI being one HWND. The balloon opened at
        the field the user had left; it asks the provider now."""
        from keyhac.ui.balloon import multi_stroke_help
        manager, backend = self._manager()
        here = _Focused(caret=(400.0, 300.0, 0.0, 18.0),
                        rect=(390.0, 290.0, 300.0, 40.0))
        left_behind = _Focused(caret=(900.0, 700.0, 0.0, 18.0),
                               rect=(890.0, 690.0, 300.0, 40.0))
        multi_stroke_help(manager, _Keymap(here, snapshot=left_behind))("sub")
        assert (backend.marks[0].x, backend.marks[0].y) == (400.0, 334.0)

    def test_the_snapshot_is_what_a_provider_with_no_answer_leaves(self):
        """It says nothing rather than hand back a window pretending to be
        the focus (issue #44), and the snapshot has no such scruples."""
        from keyhac.ui.balloon import multi_stroke_help
        manager, backend = self._manager()
        remembered = _Focused(caret=(400.0, 300.0, 0.0, 18.0),
                              rect=(390.0, 290.0, 300.0, 40.0))
        multi_stroke_help(manager, _Keymap(None, snapshot=remembered))("sub")
        assert (backend.marks[0].x, backend.marks[0].y) == (400.0, 334.0)

    def test_a_provider_that_raises_is_not_a_balloon_that_fails(self):
        from keyhac.ui.balloon import multi_stroke_help

        class _Raises:
            def get_focused_element(self):
                raise RuntimeError("no focus for you")

        manager, backend = self._manager()
        keymap = _Keymap(_Focused(caret=(400.0, 300.0, 0.0, 18.0),
                                  rect=(390.0, 290.0, 300.0, 40.0)))
        keymap._focus_provider = _Raises()
        multi_stroke_help(manager, keymap)("sub")
        assert (backend.marks[0].x, backend.marks[0].y) == (400.0, 334.0)

    def test_a_caret_it_cannot_believe_falls_to_the_field(self):
        """The VS Code measurement, reaching the balloon: the call succeeds,
        the rectangle is CGRectZero flipped, and the field it came from is one
        line tall - so under the field is within a line of where the caret
        actually is, and a great deal better than the corner."""
        from keyhac.ui.balloon import multi_stroke_help
        manager, backend = self._manager()
        multi_stroke_help(manager, _Keymap(_Focused(
            caret=(0.0, 1112.0, 0.0, 0.0),
            rect=(1275.0, 981.0, 409.0, 40.0))))("sub")
        mark = backend.marks[0]
        assert (mark.x, mark.y) == (1275.0, 1025.0)

    def test_a_document_is_not_a_place_and_the_corner_is(self):
        """The objection the size rule answers: under a full-window text area
        is neither where you are looking nor out of the way."""
        from keyhac.ui.balloon import multi_stroke_help
        manager, backend = self._manager()
        multi_stroke_help(manager, _Keymap(_Focused(
            caret=(0.0, 1112.0, 0.0, 0.0),
            rect=(100.0, 100.0, 1200.0, 800.0))))("sub")
        assert backend.marks[0].y == 25 + 24

    def test_no_focus_at_all_is_the_corner_too(self):
        from keyhac.ui.balloon import multi_stroke_help
        manager, backend = self._manager()
        multi_stroke_help(manager, _Keymap(None))(None)
        assert backend.marks[0].kwargs["text"] == "Multi-stroke: ..."
        assert backend.marks[0].y == 25 + 24

    def test_no_caret_and_no_field_puts_it_on_the_title_bar(self):
        """Excel with no cell being edited: the grid answers every spelling
        with its own rectangle, so there is no caret and the grid is far too
        tall to be a place. The window's top edge is where that leaves it -
        on screen where the work is, rather than a corner of another
        monitor."""
        from keyhac.ui.balloon import multi_stroke_help
        manager, backend = self._manager()
        grid = (482.0, 293.0, 945.0, 624.0)
        multi_stroke_help(manager, _Keymap(_Focused(caret=grid, rect=grid),
                                           window=(400.0, 200.0, 1100.0, 800.0)))("sub")
        mark = backend.marks[0]
        # "Multi-stroke: sub" is 17 characters: 17 x 8 + 24 = 160 wide, and
        # it hangs two pixels below the top edge rather than flush with it.
        assert (mark.x, mark.y) == (400.0 + (1100.0 - 160.0) / 2, 202.0)

    def test_with_no_window_either_it_is_still_the_corner(self):
        from keyhac.ui.balloon import multi_stroke_help
        manager, backend = self._manager()
        grid = (482.0, 293.0, 945.0, 624.0)
        multi_stroke_help(manager, _Keymap(_Focused(caret=grid, rect=grid)))("sub")
        assert backend.marks[0].y == 25 + 24

    def test_a_platform_that_cannot_mark_is_not_an_error(self):
        from keyhac.ui.balloon import BalloonManager

        class _Refuses(self._Backend):
            def mark_screen(self, *a, **k):
                raise RuntimeError("no marks here")

        BalloonManager(_Refuses()).pop("help", "hi")   # must not raise


class _Focused:
    """A focus element that answers the two questions the anchor asks."""

    def __init__(self, caret=None, rect=None):
        self._caret, self._rect = caret, rect

    def get_caret_rect(self):
        return self._caret

    def get_rect(self):
        return self._rect


#: "the snapshot is whatever the provider says", which is the ordinary case.
_FRESH = object()


class _Keymap:
    """What multi_stroke_help reads: the provider it asks where the focus is,
    the snapshot it falls back to, and the focused window.

    `snapshot=` is what the last keystroke left behind, for the cases where
    Windows' focus cache and the truth have come apart."""

    def __init__(self, element, window=None, snapshot=_FRESH):
        self._focus_provider = _Provider(element)
        remembered = element if snapshot is _FRESH else snapshot
        self.focus = None if remembered is None else _Focus(remembered)
        self._window = window

    def get_active_window(self):
        return None if self._window is None else _Window(self._window)


class _Provider:
    def __init__(self, element):
        self._element = element

    def get_focused_element(self):
        return self._element


class _Window:
    def __init__(self, frame):
        self._frame = frame

    def get_frame(self):
        return self._frame


class _Focus:
    def __init__(self, element):
        self.element = element


class _Mark:
    def __init__(self, x, y, kwargs):
        self.x, self.y, self.kwargs = x, y, kwargs
        self.closed = False

    def close(self):
        self.closed = True
