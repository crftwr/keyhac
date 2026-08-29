"""ActivateApplication / ActivateWindow against fake windows (no OS).

The rotation is the part worth pinning here, because the obvious
implementation of it is wrong: activating a window moves it to the front of
the z-order, so a walk over that order swaps the top two forever and never
reaches a third window.  What is walked is the windows' position on screen,
and the z-order is asked only which window is current.

The live counterparts (real AX / Win32 activation) are in test_mac_window.py
and test_win_window.py.
"""

import pytest

from keyhac.actions import ActivateApplication, ActivateWindow
from keyhac.platform.base import Window, WindowProvider


class FakeWindow(Window):

    def __init__(self, app_name, frame, minimized=False, activates=True):
        self._app_name = app_name
        self._frame = frame
        self._minimized = minimized
        self._activates = activates
        self.activated = False
        self.restored = False

    def __repr__(self):
        return f"FakeWindow({self._app_name}, {self._frame})"

    @property
    def title(self):
        # Every window of one application with the same title, which is what
        # three open Terminal windows actually report.
        return self._app_name

    @property
    def app_name(self):
        return self._app_name

    @property
    def pid(self):
        return 1

    def get_frame(self):
        return self._frame

    def set_frame(self, x, y, w=None, h=None):
        return True

    def activate(self):
        self.activated = True
        return self._activates

    def is_minimized(self):
        return self._minimized

    def restore(self):
        self._minimized = False
        self.restored = True
        return True


class FakeProvider(WindowProvider):
    """Windows in z-order, front-most first - what both platforms report."""

    def __init__(self, windows, active=None):
        self.windows = list(windows)
        self.active = active

    def get_active_window(self):
        return self.active

    def list_windows(self):
        return list(self.windows)

    def screen_frames(self):
        return [(0.0, 0.0, 1920.0, 1080.0)]

    def screen_work_frames(self):
        return [(0.0, 0.0, 1920.0, 1080.0)]

    def window_frames(self):
        return []


class FakeAppControl:

    def __init__(self):
        self.launched = []
        self.activated_pids = []

    def launch(self, app_name):
        self.launched.append(app_name)

    def activate_pid(self, pid):
        self.activated_pids.append(pid)


@pytest.fixture
def wired(engine):
    """wired(windows, active=, running=) -> (keymap, app_control)."""
    def make(windows, active=None, running=(), platform="windows"):
        fixture = engine(lambda keymap: None, platform=platform)
        keymap = fixture.keymap
        keymap.window_provider = FakeProvider(windows, active=active)
        keymap.app_control = FakeAppControl()
        keymap.app_control_running_apps = lambda: list(running)
        return keymap, keymap.app_control
    return make


# Three windows of one application, listed in z-order (front-most first) and
# deliberately not in screen order: the middle one of the column is in front.
def _three_terminals():
    top = FakeWindow("Terminal", (0.0, 0.0, 800.0, 300.0))
    middle = FakeWindow("Terminal", (0.0, 300.0, 800.0, 300.0))
    bottom = FakeWindow("Terminal", (0.0, 600.0, 800.0, 300.0))
    return top, middle, bottom


class TestFirstPress:

    def test_activates_the_front_most_window_of_the_application(self, wired):
        top, middle, bottom = _three_terminals()
        other = FakeWindow("Code", (100.0, 100.0, 400.0, 400.0))
        wired([other, middle, top, bottom], active=other)
        ActivateApplication(app="Terminal").starting()
        assert middle.activated          # windows[0] of the matching ones
        assert not top.activated and not bottom.activated

    def test_direction_does_not_apply_while_the_application_is_behind(self, wired):
        """Both bindings do the same thing on a first press, on purpose."""
        top, middle, bottom = _three_terminals()
        other = FakeWindow("Code", (0.0, 0.0, 100.0, 100.0))
        wired([middle, top, bottom, other], active=other)
        ActivateApplication(app="Terminal", reverse=True).starting()
        assert middle.activated

    def test_a_minimized_window_is_restored_first(self, wired):
        window = FakeWindow("Terminal", (0.0, 0.0, 800.0, 300.0), minimized=True)
        wired([window], active=None)
        ActivateApplication(app="Terminal").starting()
        assert window.restored and window.activated

    def test_the_application_pattern_matches_like_a_focus_condition(self, wired):
        window = FakeWindow("Code", (0.0, 0.0, 800.0, 300.0))
        wired([window], active=None)
        ActivateApplication(app="chrome|code.exe").starting()
        assert window.activated


class TestRotation:

    def test_the_next_window_is_the_next_one_down_the_screen(self, wired):
        """Not the next in z-order, which activation has just reordered."""
        top, middle, bottom = _three_terminals()
        wired([middle, top, bottom], active=middle)
        ActivateApplication(app="Terminal").starting()
        assert bottom.activated
        assert not top.activated

    def test_reverse_walks_back_up(self, wired):
        top, middle, bottom = _three_terminals()
        wired([middle, top, bottom], active=middle)
        ActivateApplication(app="Terminal", reverse=True).starting()
        assert top.activated
        assert not bottom.activated

    def test_the_walk_wraps_around(self, wired):
        top, middle, bottom = _three_terminals()
        wired([bottom, top, middle], active=bottom)
        ActivateApplication(app="Terminal").starting()
        assert top.activated

    def test_a_single_window_is_just_activated_again(self, wired):
        only = FakeWindow("Terminal", (0.0, 0.0, 800.0, 300.0))
        wired([only], active=only)
        ActivateApplication(app="Terminal").starting()
        assert only.activated

    def test_windows_with_no_frame_sort_last_without_failing(self, wired):
        top, middle, _bottom = _three_terminals()
        nowhere = FakeWindow("Terminal", None)
        wired([top, middle, nowhere], active=top)
        ActivateApplication(app="Terminal").starting()
        assert middle.activated

    def test_cycle_off_stays_on_the_front_most_window(self, wired):
        top, middle, bottom = _three_terminals()
        wired([middle, top, bottom], active=middle)
        ActivateApplication(app="Terminal", cycle=False).starting()
        assert middle.activated
        assert not top.activated and not bottom.activated


class TestLaunch:

    def test_nothing_matches_and_launch_starts_it(self, wired):
        _keymap, app_control = wired([FakeWindow("Code", (0.0, 0.0, 10.0, 10.0))])
        action = ActivateApplication(app="Terminal", launch="wt.exe")
        action.starting()
        assert action.run() == "Launched wt.exe"
        assert app_control.launched == ["wt.exe"]

    def test_without_launch_nothing_is_started(self, wired):
        _keymap, app_control = wired([])
        action = ActivateApplication(app="Terminal")
        action.starting()
        action.run()
        assert app_control.launched == []

    def test_a_window_that_will_not_activate_falls_back_to_launching(self, wired):
        stuck = FakeWindow("Terminal", (0.0, 0.0, 800.0, 300.0), activates=False)
        _keymap, app_control = wired([stuck])
        action = ActivateApplication(app="Terminal", launch="wt.exe")
        action.starting()
        action.run()
        assert app_control.launched == ["wt.exe"]

    def test_a_running_application_with_no_window_is_activated_by_pid(self, wired):
        _keymap, app_control = wired([], running=[("Terminal", 4321)])
        ActivateApplication(app="Terminal").starting()
        assert app_control.activated_pids == [4321]

    def test_launching_wins_over_the_pid_fallback(self, wired):
        """A running application showing no window has nowhere to put the
        user; the OS's own launch activates it *and* opens one."""
        _keymap, app_control = wired([], running=[("Terminal", 4321)])
        action = ActivateApplication(app="Terminal", launch="Terminal.app")
        action.starting()
        action.run()
        assert app_control.activated_pids == []
        assert app_control.launched == ["Terminal.app"]

    def test_nothing_at_all_is_a_warning(self, wired, caplog):
        wired([])
        with caplog.at_level("WARNING"):
            ActivateApplication(app="Terminal").starting()
        assert "no window or running app" in caplog.text


class TestActivateWindow:
    """The released action, now the no-cycle no-launch case of the new one."""

    def test_it_never_rotates(self, wired):
        top, middle, bottom = _three_terminals()
        wired([middle, top, bottom], active=middle)
        ActivateWindow(app="Terminal").starting()
        assert middle.activated
        assert not top.activated and not bottom.activated

    def test_it_never_launches(self, wired):
        _keymap, app_control = wired([])
        action = ActivateWindow(app="Terminal")
        action.starting()
        action.run()
        assert app_control.launched == []

    def test_it_still_falls_back_to_a_running_application(self, wired):
        _keymap, app_control = wired([], running=[("Code", 99)])
        ActivateWindow(app="code").starting()
        assert app_control.activated_pids == [99]

    def test_repr_is_unchanged(self):
        assert repr(ActivateWindow(app="code|Visual Studio Code")) == \
            'ActivateWindow(app="code|Visual Studio Code")'


class TestValidation:

    def test_an_empty_pattern_raises_at_config_time(self):
        with pytest.raises(ValueError):
            ActivateApplication(app="")

    def test_repr_names_what_was_asked_for(self):
        assert repr(ActivateApplication(app="Terminal", launch="Terminal.app",
                                        reverse=True)) == \
            'ActivateApplication(app="Terminal", launch="Terminal.app", reverse=True)'

    def test_no_window_provider_is_a_noop(self, engine):
        fixture = engine(lambda keymap: None)
        fixture.keymap.window_provider = None
        fixture.keymap.app_control = FakeAppControl()
        fixture.keymap.app_control_running_apps = list
        ActivateApplication(app="Terminal").starting()
        assert fixture.keymap.app_control.launched == []
