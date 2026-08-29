"""The chooser's own window chrome (issue #117): a drawn edge, a handle to
move it by and a corner to resize it from.

Frameless is what makes the window genuinely non-activating, and frameless is
what took all three away, so each is drawn in the content and driven from
mouse events rather than by the window manager.

The memory backend puts a 72x20 window at (160, 160) with one pixel per cell,
so every coordinate below is exact and the px/unit scale the grips derive is
1.0.
"""

import pytest

from puikit.event import Event, EventType

from keyhac.ui import runtime


@pytest.fixture
def ui_backend():
    from puikit.backends.memory_backend import MemoryBackend
    backend = MemoryBackend(width=100, height=30)
    backend.open()
    runtime.backend = backend
    yield backend
    runtime.backend = None
    backend.close()


def _chooser(backend, **kwargs):
    from keyhac.ui.chooser import ChooserWindow
    items = [("*", f"entry {i:02}", i) for i in range(6)]
    return ChooserWindow(backend, items, **kwargs)


def _press(chooser, x, y):
    chooser._on_event(Event(type=EventType.MOUSE_DOWN, x=x, y=y, button="left"))


def _drag(chooser, x, y):
    chooser._on_event(Event(type=EventType.MOUSE_DRAG, x=x, y=y, button="left"))


def _release(chooser, x, y):
    chooser._on_event(Event(type=EventType.MOUSE_UP, x=x, y=y, button="left"))


#: Where the two grips are drawn in a 72x20 window: the magnifier just inside
#: the top-left corner, the grip on the last row at the right edge.
HANDLE = (1, 1)
GRIP = (70, 18)


class TestTheWindowHasAnEdge:

    def test_a_border_is_drawn_around_the_whole_window(self, ui_backend):
        chooser = _chooser(ui_backend)
        rows = ["".join(row) for row in chooser.window.snapshot()]
        assert rows[0].startswith("┌") and rows[0].endswith("┐")
        assert rows[-1].startswith("└") and rows[-1].endswith("┘")
        assert all(row[0] in "│┌└" for row in rows)

    def test_the_grip_sits_in_the_bottom_right_corner(self, ui_backend):
        chooser = _chooser(ui_backend)
        rows = ["".join(row) for row in chooser.window.snapshot()]
        assert rows[GRIP[1]][GRIP[0]] == "◢"


class TestDraggingTheHandleMovesTheWindow:

    def test_the_window_follows_the_pointer(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *HANDLE)
        _drag(chooser, HANDLE[0] + 30, HANDLE[1] + 12)
        assert chooser.window.frame_px() == (190.0, 172.0, 72.0, 20.0)

    def test_a_drag_is_measured_from_the_press_not_from_the_last_event(
            self, ui_backend):
        # The window slides out from under the pointer as it follows it, so
        # the second event reports the *same* pointer position again once the
        # move has caught up. Summing per-event deltas would move it twice.
        chooser = _chooser(ui_backend)
        _press(chooser, *HANDLE)
        _drag(chooser, HANDLE[0] + 20, HANDLE[1])
        _drag(chooser, *HANDLE)
        assert chooser.window.frame_px()[:2] == (180.0, 160.0)

    def test_a_gesture_that_leaves_the_handle_still_moves_it(self, ui_backend):
        # The Panel clamps x/y to the widget once the pointer leaves it and
        # keeps the true position alongside; reading the clamped one would
        # pin the window a character from where it started.
        chooser = _chooser(ui_backend)
        _press(chooser, *HANDLE)
        chooser._on_event(Event(
            type=EventType.MOUSE_DRAG, x=HANDLE[0], y=HANDLE[1], button="left",
            hints={"pointer_x": HANDLE[0] + 40.0,
                   "pointer_y": HANDLE[1] + 5.0}))
        assert chooser.window.frame_px()[:2] == (200.0, 165.0)

    def test_releasing_ends_the_drag(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *HANDLE)
        _drag(chooser, HANDLE[0] + 10, HANDLE[1])
        _release(chooser, HANDLE[0] + 10, HANDLE[1])
        _drag(chooser, HANDLE[0] + 50, HANDLE[1])
        assert chooser.window.frame_px()[:2] == (170.0, 160.0)

    def test_the_window_is_not_resized_by_being_moved(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *HANDLE)
        _drag(chooser, HANDLE[0] + 9, HANDLE[1] + 9)
        assert chooser.window.frame_px()[2:] == (72.0, 20.0)


class TestDraggingTheGripResizesTheWindow:

    def test_the_corner_follows_the_pointer(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *GRIP)
        _drag(chooser, GRIP[0] + 16, GRIP[1] + 6)
        assert chooser.window.frame_px() == (160.0, 160.0, 88.0, 26.0)

    def test_the_top_left_corner_stays_where_it_was(self, ui_backend):
        # The whole reason the grip is in the bottom-right: resize_to_px holds
        # the top-left, so the window opens out under the pointer instead of
        # walking away from it.
        chooser = _chooser(ui_backend)
        _press(chooser, *GRIP)
        _drag(chooser, GRIP[0] + 20, GRIP[1] + 20)
        assert chooser.window.frame_px()[:2] == (160.0, 160.0)

    def test_it_stops_at_a_size_the_field_still_fits_in(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *GRIP)
        _drag(chooser, GRIP[0] - 60, GRIP[1] - 30)
        assert chooser.window.frame_px()[2:] == (24.0, 6.0)

    def test_it_grows_again_from_where_the_pointer_turned_round(
            self, ui_backend):
        # Travel is measured from the press, so the size the window would have
        # had goes on being computed while it sits at the minimum - and coming
        # back out resumes at the pointer rather than 48 columns later.
        chooser = _chooser(ui_backend)
        _press(chooser, *GRIP)
        _drag(chooser, GRIP[0] - 60, GRIP[1])
        _drag(chooser, GRIP[0] - 10, GRIP[1])
        assert chooser.window.frame_px()[2] == 62.0

    def test_the_layout_follows_the_new_size(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *GRIP)
        _drag(chooser, GRIP[0] - 20, GRIP[1] - 4)
        rows = ["".join(row) for row in chooser.window.snapshot()]
        assert len(rows) == 16 and len(rows[0]) == 52
        # redrawn at the new size, edge and grip included
        assert rows[0].startswith("┌") and rows[0].endswith("┐")
        assert rows[-2][-2] == "◢"


class TestAGripIsNotAControlTheKeyboardKnowsAbout:
    """The same rule the scope arrows follow: a click here is for the moment
    the pointer is already in hand, and it must not take the focus off the
    filter field - typing is what the window is for."""

    def test_dragging_leaves_the_filter_field_focused(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *HANDLE)
        _drag(chooser, HANDLE[0] + 12, HANDLE[1] + 3)
        _release(chooser, HANDLE[0] + 12, HANDLE[1] + 3)
        assert chooser._page.get_focused() is chooser._edit
        assert not chooser.in_list

    def test_the_grip_does_not_take_the_focus_either(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *GRIP)
        _release(chooser, *GRIP)
        assert chooser._page.get_focused() is chooser._edit
