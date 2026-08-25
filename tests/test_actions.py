"""SnapWindow - snap geometry against fake windows and screens (no OS).

The live macOS counterpart (real AX frame writes) is in test_mac_window.py;
this file pins the arithmetic: work-area use, ratio, best-screen choice,
minimized-window restore, and the degraded paths.
"""

import pytest

from keyhac.actions import MoveFocus, SnapWindow
from keyhac.core.uitree import UINode
from keyhac.platform.base import Window, WindowProvider


class FakeWindow(Window):

    def __init__(self, frame, minimized=False):
        self._frame = frame
        self._minimized = minimized
        self.set_frame_calls = []
        self.restored = False

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
        return True

    def activate(self):
        return True

    def is_minimized(self):
        return self._minimized

    def restore(self):
        self._minimized = False
        self.restored = True
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
            screens=(SCREEN,), work=(WORK,), minimized=False):
        fixture = engine(lambda keymap: None)
        window = FakeWindow(frame, minimized=minimized)
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


class TestMoveFocusReference:
    """The hidden reference position that makes overshooting undoable.

    Class state on purpose - a configuration binds four MoveFocus objects and
    they have to steer by the same thing - so each test starts from clean.
    """

    LEFT_PANE = UINode(rect=(0, 0, 300, 900), name="left")
    RIGHT_PANE = UINode(rect=(300, 0, 300, 900), name="right")
    WINDOW = (42, (0, 0, 600, 900))

    @pytest.fixture(autouse=True)
    def clean_state(self):
        MoveFocus._reference = None
        MoveFocus._reference_pane = None
        MoveFocus._reference_window = None
        yield
        MoveFocus._reference = None
        MoveFocus._reference_pane = None
        MoveFocus._reference_window = None

    def _action(self, direction, focus_rect, window=None):
        action = MoveFocus(direction)
        action.window_key = self.WINDOW if window is None else window
        action.focus_rect = focus_rect
        return action

    def test_a_first_press_is_seeded_from_the_focused_element(self):
        """Not from the middle of the pane: the first move should go where
        the keyboard actually is."""
        action = self._action("right", (10, 100, 40, 40))
        assert action._steer_from(self.LEFT_PANE) == (30.0, 120.0)

    def test_the_seed_is_clamped_into_the_pane(self):
        """A scrolling pane reports the height of its contents, so the
        focused element's centre can be outside the pane entirely."""
        action = self._action("right", (0, 0, 300, 4000))
        assert action._steer_from(self.LEFT_PANE) == (150.0, 900)

    def test_a_horizontal_move_keeps_the_vertical_reference(self):
        action = self._action("right", (10, 100, 40, 40))
        action._remember((30.0, 120.0), self.RIGHT_PANE)
        assert MoveFocus._reference == (450.0, 120.0)     # x moved, y kept

    def test_a_vertical_move_keeps_the_horizontal_reference(self):
        action = self._action("down", (10, 100, 40, 40))
        action._remember((30.0, 120.0), self.RIGHT_PANE)
        assert MoveFocus._reference == (30.0, 450.0)      # y moved, x kept

    def test_the_reference_is_reused_by_the_opposite_binding(self):
        """The whole point: a left press must steer by what a right press
        left behind, and they are different objects."""
        self._action("right", (10, 100, 40, 40))._remember(
            (30.0, 120.0), self.RIGHT_PANE)
        going_back = self._action("left", (400, 800, 40, 40))
        assert going_back._steer_from(self.RIGHT_PANE) == (450.0, 120.0)

    def test_focus_moved_by_something_else_discards_it(self):
        """A click, or the application deciding for itself. Steering by a
        position with no relation to where the keyboard now is would send the
        next arrow key somewhere unrelated."""
        self._action("right", (10, 100, 40, 40))._remember(
            (30.0, 120.0), self.RIGHT_PANE)
        # Focus is now in the left pane, which is not where we put it.
        elsewhere = self._action("up", (10, 800, 40, 40))
        assert elsewhere._steer_from(self.LEFT_PANE) == (30.0, 820.0)

    def test_a_different_window_discards_it(self):
        self._action("right", (10, 100, 40, 40))._remember(
            (30.0, 120.0), self.RIGHT_PANE)
        other = self._action("left", (310, 400, 40, 40),
                             window=(99, (0, 0, 600, 900)))
        assert other._steer_from(self.RIGHT_PANE) == (330.0, 420.0)

    def test_a_moved_window_discards_it(self):
        """The reference is a screen coordinate, so a window that has moved
        no longer means what it meant."""
        self._action("right", (10, 100, 40, 40))._remember(
            (30.0, 120.0), self.RIGHT_PANE)
        moved = self._action("left", (310, 400, 40, 40),
                             window=(42, (100, 0, 600, 900)))
        assert moved._steer_from(self.RIGHT_PANE) == (330.0, 420.0)

