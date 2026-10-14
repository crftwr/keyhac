"""SnapWindow - snap geometry against fake windows and screens (no OS).

The live macOS counterpart (real AX frame writes) is in test_mac_window.py;
this file pins the arithmetic: work-area use, ratio, best-screen choice,
leaving minimized and full screen first, and the degraded paths.
"""

import pytest

from keyhac.actions import SnapWindow
from keyhac.platform.base import Window, WindowProvider


class FakeWindow(Window):

    def __init__(self, frame, minimized=False, maximized=False,
                 full_screen=False, accepts_frame=True):
        self._frame = frame
        self._minimized = minimized
        self._maximized = maximized
        self._full_screen = full_screen
        self._accepts_frame = accepts_frame
        self.set_frame_calls = []
        self.restored = False
        self.events = []

    @property
    def title(self):
        return "fake"

    @property
    def app_name(self):
        return "FakeApp"

    @property
    def pid(self):
        return 1

    def get_frame(self):
        return self._frame

    def set_frame(self, x, y, w=None, h=None):
        self.set_frame_calls.append((x, y, w, h))
        self.events.append(("set_frame", self._full_screen))
        return self._accepts_frame

    def activate(self):
        return True

    def is_minimized(self):
        return self._minimized

    def restore(self):
        self._minimized = False
        self.restored = True
        return True

    def is_maximized(self):
        return self._maximized

    def is_full_screen(self):
        return self._full_screen

    def set_full_screen(self, full_screen=True):
        self._full_screen = full_screen
        self.events.append(("set_full_screen", full_screen))
        return True


class FakeProvider(WindowProvider):

    def __init__(self, window, screens, work_frames):
        self._window = window
        self._screens = screens
        self._work = work_frames

    def get_active_window(self):
        return self._window

    def list_windows(self):
        return [self._window] if self._window else []

    def screen_frames(self):
        return self._screens

    def screen_work_frames(self):
        return self._work

    def window_frames(self):
        return []


SCREEN = (0.0, 0.0, 1920.0, 1080.0)
WORK = (0.0, 25.0, 1920.0, 1055.0)      # 25 px menu bar


@pytest.fixture
def snapped(engine):
    """snapped(position, ..., frame=..., screens=, work=) -> FakeWindow after
    the snap ran on a Keymap wired to the fakes."""
    def run(position, ratio=None, frame=(100.0, 100.0, 500.0, 400.0),
            screens=(SCREEN,), work=(WORK,), **state):
        fixture = engine(lambda keymap: None)
        window = FakeWindow(frame, **state)
        fixture.keymap.window_provider = FakeProvider(
            window, list(screens), list(work))
        action = (SnapWindow(position) if ratio is None
                  else SnapWindow(position, ratio=ratio))
        action()
        return window
    return run


class TestSnapGeometry:

    def test_left_half_of_work_area(self, snapped):
        window = snapped("left")
        assert window.set_frame_calls == [(0.0, 25.0, 960.0, 1055.0)]

    def test_right_half_of_work_area(self, snapped):
        window = snapped("right")
        assert window.set_frame_calls == [(960.0, 25.0, 960.0, 1055.0)]

    def test_top_and_bottom_split_the_height(self, snapped):
        top = snapped("top")
        assert top.set_frame_calls == [(0.0, 25.0, 1920.0, 527.5)]
        bottom = snapped("bottom")
        assert bottom.set_frame_calls == [(0.0, 552.5, 1920.0, 527.5)]

    def test_full_covers_the_work_area(self, snapped):
        window = snapped("full")
        assert window.set_frame_calls == [(0.0, 25.0, 1920.0, 1055.0)]

    def test_ratio_picks_the_split(self, snapped):
        window = snapped("right", ratio=1 / 3)
        assert window.set_frame_calls[-1] == pytest.approx(
            (1280.0, 25.0, 640.0, 1055.0))

    def test_snaps_on_the_screen_the_window_is_mostly_on(self, snapped):
        second_screen = (1920.0, 0.0, 1920.0, 1080.0)
        second_work = (1920.0, 0.0, 1920.0, 1055.0)
        window = snapped("left", frame=(2000.0, 50.0, 600.0, 400.0),
                         screens=(SCREEN, second_screen),
                         work=(WORK, second_work))
        assert window.set_frame_calls == [(1920.0, 0.0, 960.0, 1055.0)]

    def test_falls_back_to_screen_frames_without_work_area(self, snapped):
        window = snapped("left", work=())
        assert window.set_frame_calls == [(0.0, 0.0, 960.0, 1080.0)]

    def test_minimized_window_is_restored_before_the_snap(self, snapped):
        window = snapped("full", minimized=True)
        assert window.restored
        assert window.set_frame_calls  # restored *and* placed

    def test_full_screen_window_leaves_full_screen_before_the_snap(
            self, snapped):
        """macOS refuses a frame write to a full-screen window outright."""
        window = snapped("left", full_screen=True)
        assert window.events == [("set_full_screen", False),
                                 ("set_frame", False)]
        assert window.set_frame_calls == [(0.0, 25.0, 960.0, 1055.0)]

    def test_maximized_window_is_placed_without_restoring_first(
            self, snapped):
        """set_frame un-maximizes; restoring first would race on macOS,
        where Return to Previous Size animates over the frame written
        after it."""
        window = snapped("left", maximized=True)
        assert not window.restored
        assert window.set_frame_calls == [(0.0, 25.0, 960.0, 1055.0)]

    def test_a_refused_frame_is_logged_not_raised(self, snapped, caplog):
        snapped("left", accepts_frame=False)
        assert "did not accept the frame" in caplog.text


class TestSnapValidation:

    def test_unknown_position_raises_at_config_time(self):
        with pytest.raises(ValueError):
            SnapWindow("center")

    def test_out_of_range_ratio_raises_at_config_time(self):
        with pytest.raises(ValueError):
            SnapWindow("left", ratio=0.0)
        with pytest.raises(ValueError):
            SnapWindow("left", ratio=1.5)

    def test_no_window_provider_is_a_noop(self, engine):
        fixture = engine(lambda keymap: None)
        fixture.keymap.window_provider = None
        SnapWindow("left")()  # must not raise

    def test_repr_names_the_position(self):
        assert repr(SnapWindow("left")) == 'SnapWindow("left")'
