"""Chooser single-instance behavior (issue #3: a second invocation must not
stack another chooser window). UI is tested against puikit's MemoryBackend."""

import pytest

from keyhac.actions import ChooserAction
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
    ChooserAction._stop_watch()
    backend.close()


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
    puikit >= 1.0.12 scrolls the selection into view on assignment."""

    def test_selection_moved_past_the_viewport_stays_visible(self, ui_backend):
        from puikit.event import Event, EventType
        from keyhac.ui.chooser import ChooserWindow

        items = [("*", f"entry {i:02}", i) for i in range(40)]
        chooser = ChooserWindow(ui_backend, items)
        for _ in range(30):
            chooser._on_event(Event(type=EventType.KEY, key="down"))
        assert chooser._list.selected == 30
        rows = ["".join(row) for row in chooser.window.snapshot()]
        assert any("entry 30" in row for row in rows), \
            "the selected row scrolled out of view (issue #27)"


class TestChooserCentering:
    """Issue #4: the chooser centers on the focused window's frame.

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
    up; with no activation there is nothing to wait for."""

    def _wire(self, ui_backend, tmp_path):
        from keyhac.actions import ClipboardChooserAction
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

        class _Paste(ClipboardChooserAction):
            def _paste(self):
                pasted.append(True)

        ui_backend.call_later = lambda delay, fn: deferred.append((delay, fn))
        return keymap, _Paste(), pasted, deferred

    def test_paste_is_immediate_without_activation(self, ui_backend, tmp_path):
        keymap, action, pasted, deferred = self._wire(ui_backend, tmp_path)
        action._on_chosen_common("hello", 0)
        assert pasted == [True]
        assert deferred == []
        assert keymap.clipboard_history.get_current() == "hello"

    def test_paste_still_waits_when_the_chooser_took_focus(self, ui_backend,
                                                           tmp_path):
        _keymap, action, pasted, deferred = self._wire(ui_backend, tmp_path)
        type(action).activates = True
        try:
            action._on_chosen_common("hello", 0)
        finally:
            type(action).activates = False
        assert pasted == []
        assert len(deferred) == 1 and deferred[0][0] > 0

    def test_shift_select_never_pastes(self, ui_backend, tmp_path):
        from keyhac.core.const import MODKEY_SHIFT
        _keymap, action, pasted, deferred = self._wire(ui_backend, tmp_path)
        action._on_chosen_common("hello", MODKEY_SHIFT)
        assert pasted == [] and deferred == []


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

    def _move_focus_to(self, provider, *, pid, title):
        from keyhac.platform.base import Focus
        provider.focus = Focus(app_name="other", pid=pid, window_title=title,
                               class_name=None, path=f"/other/X({title})")

    def _tick(self, ui_backend):
        """Run the watch's pending timer once."""
        ChooserAction._watch._tick()

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
        """The macOS focus path runs down to the focused element, so Tabbing
        between fields changes it.  The watch must not react to that."""
        from keyhac.platform.base import Focus
        original = keyhac_engine.focus_provider.focus
        _action, chooser = self._open(ui_backend)
        keyhac_engine.focus_provider.focus = Focus(
            app_name=original.app_name, pid=original.pid,
            window_title=original.window_title, class_name=original.class_name,
            path=original.path + "/AXTextField(Other Field)")
        self._tick(ui_backend)
        assert ChooserAction._open is not None

    def test_an_unreadable_focus_closes_nothing(self, keyhac_engine, ui_backend):
        _action, chooser = self._open(ui_backend)
        keyhac_engine.focus_provider.focus = None
        self._tick(ui_backend)
        assert ChooserAction._open is not None

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
