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


#: The magnifier, just inside the top-left corner of a 72x20 window - and the
#: points on the window's own edge that resize it, one base unit deep.
HANDLE = (1, 1)
BOTTOM_RIGHT = (71.5, 19.5)
RIGHT = (71.5, 10)
BOTTOM = (36, 19.5)
LEFT = (0.5, 10)
TOP = (36, 0.5)


class TestTheWindowHasAnEdge:

    def test_a_border_is_drawn_around_the_whole_window(self, ui_backend):
        chooser = _chooser(ui_backend)
        rows = ["".join(row) for row in chooser.window.snapshot()]
        assert rows[0].startswith("┌") and rows[0].endswith("┐")
        assert rows[-1].startswith("└") and rows[-1].endswith("┘")
        assert all(row[0] in "│┌└" for row in rows)

    def test_the_edge_costs_the_list_no_row(self, ui_backend):
        # The first version of this drew a grip on a row of its own; the row
        # is a candidate the window then could not show.
        chooser = _chooser(ui_backend)
        rows = ["".join(row) for row in chooser.window.snapshot()]
        assert "◢" not in "".join(rows)
        assert sum("entry" in row for row in rows) == 6


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


class TestDraggingTheEdgeResizesTheWindow:
    """Every edge and corner, since a window opens out in whichever direction
    there is room. The ones holding the far side have to move the window as
    they resize it: `resize_to_px` always keeps the top-left corner."""

    def test_the_bottom_right_corner_follows_the_pointer(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *BOTTOM_RIGHT)
        _drag(chooser, BOTTOM_RIGHT[0] + 16, BOTTOM_RIGHT[1] + 6)
        assert chooser.window.frame_px() == (160.0, 160.0, 88.0, 26.0)

    def test_the_right_edge_moves_one_side_only(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *RIGHT)
        _drag(chooser, RIGHT[0] + 10, RIGHT[1])
        assert chooser.window.frame_px() == (160.0, 160.0, 82.0, 20.0)

    def test_the_bottom_edge_moves_one_side_only(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *BOTTOM)
        _drag(chooser, BOTTOM[0], BOTTOM[1] + 5)
        assert chooser.window.frame_px() == (160.0, 160.0, 72.0, 25.0)

    def test_the_left_edge_holds_the_right_one_still(self, ui_backend):
        # Dragging left grows the window leftwards: the origin moves by what
        # the width gains, so the right edge stays on screen where it was.
        chooser = _chooser(ui_backend)
        _press(chooser, *LEFT)
        _drag(chooser, LEFT[0] - 12, LEFT[1])
        x, _y, w, _h = chooser.window.frame_px()
        assert (x, w) == (148.0, 84.0)
        assert x + w == 232.0                      # 160 + 72, unmoved

    def test_the_top_edge_holds_the_bottom_one_still(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *TOP)
        _drag(chooser, TOP[0], TOP[1] - 4)
        _x, y, _w, h = chooser.window.frame_px()
        assert (y, h) == (156.0, 24.0)
        assert y + h == 180.0                      # 160 + 20, unmoved

    def test_a_press_inside_the_edge_is_not_a_resize(self, ui_backend):
        # One base unit deep, and the row below it belongs to the list.
        chooser = _chooser(ui_backend)
        _press(chooser, 36, 10)
        _drag(chooser, 60, 18)
        assert chooser.window.frame_px() == (160.0, 160.0, 72.0, 20.0)

    def test_it_stops_at_a_size_the_field_still_fits_in(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *BOTTOM_RIGHT)
        _drag(chooser, BOTTOM_RIGHT[0] - 60, BOTTOM_RIGHT[1] - 30)
        assert chooser.window.frame_px()[2:] == (24.0, 6.0)

    def test_the_minimum_does_not_let_a_near_edge_keep_sliding(self, ui_backend):
        # The left edge moves the origin by whatever the width gives up; once
        # the width stops giving, the origin has to stop too.
        chooser = _chooser(ui_backend)
        _press(chooser, *LEFT)
        _drag(chooser, LEFT[0] + 60, LEFT[1])
        assert chooser.window.frame_px()[:3] == (208.0, 160.0, 24.0)

    def test_it_grows_again_from_where_the_pointer_turned_round(
            self, ui_backend):
        # Travel is measured from the press, so the size the window would have
        # had goes on being computed while it sits at the minimum - and coming
        # back out resumes at the pointer rather than 48 columns later.
        chooser = _chooser(ui_backend)
        _press(chooser, *BOTTOM_RIGHT)
        _drag(chooser, BOTTOM_RIGHT[0] - 60, BOTTOM_RIGHT[1])
        _drag(chooser, BOTTOM_RIGHT[0] - 10, BOTTOM_RIGHT[1])
        assert chooser.window.frame_px()[2] == 62.0

    def test_the_layout_follows_the_new_size(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *BOTTOM_RIGHT)
        _drag(chooser, BOTTOM_RIGHT[0] - 20, BOTTOM_RIGHT[1] - 4)
        rows = ["".join(row) for row in chooser.window.snapshot()]
        assert len(rows) == 16 and len(rows[0]) == 52
        assert rows[0].startswith("┌") and rows[0].endswith("┐")
        assert rows[-1].startswith("└") and rows[-1].endswith("┘")


class TestNeitherGrabIsAControlTheKeyboardKnowsAbout:
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

    def test_resizing_does_not_take_the_focus_either(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *BOTTOM_RIGHT)
        _drag(chooser, BOTTOM_RIGHT[0] + 8, BOTTOM_RIGHT[1] + 2)
        _release(chooser, BOTTOM_RIGHT[0] + 8, BOTTOM_RIGHT[1] + 2)
        assert chooser._page.get_focused() is chooser._edit


class TestTheEdgeIsRoundedWhereTheWindowIs:
    """macOS clips a window to a 15 pt rounded rectangle (measured off
    `NSThemeFrame`; a borderless window is 0) and Windows 11 rounds a popup
    too, so a square border drawn at the window's extent loses its four
    corners to that clip - which is what it did."""

    def _gui_backend(self):
        # MemoryBackend masks vector_shapes off - it is a character grid - so a
        # test that wants the vector path re-enables it, the way puikit's own
        # rounded-face tests do.
        from puikit import PROFILE_GUI_DESKTOP
        from puikit.backends.memory_backend import MemoryBackend
        from puikit.capability import CapabilityProfile

        class VectorBackend(MemoryBackend):
            @property
            def capabilities(self):
                return CapabilityProfile({**PROFILE_GUI_DESKTOP,
                                          "vector_shapes": True})

        backend = VectorBackend(width=100, height=30,
                                capabilities=PROFILE_GUI_DESKTOP)
        backend.open()
        runtime.backend = backend
        return backend

    def test_the_line_is_round_and_inside_the_corner(self, ui_backend):
        backend = self._gui_backend()
        try:
            _chooser(backend)
            # first: the page frame draws before anything nested in it
            outer = backend.round_rect_calls[0]
        finally:
            runtime.backend = ui_backend
            backend.close()
        x, y, w, h, radius = outer[:5]
        # inset on every side, and rounded concentrically with the window
        assert (x, y) == (2.0, 2.0)
        assert (w, h) == (72.0 - 4.0, 20.0 - 4.0)
        assert radius == 13.0

    def test_a_character_grid_keeps_its_square_box(self, ui_backend):
        # Nothing there to round: the corner is a cell and the stroke is a
        # box-drawing glyph.
        chooser = _chooser(ui_backend)
        assert ui_backend.round_rect_calls == []
        assert "".join(chooser.window.snapshot()[0]).startswith("┌")


class TestTheSizeSurvivesTheWindow:
    """The window is rebuilt every invocation, so a resize that is not
    remembered is undone by the next press of the key that opened it."""

    @pytest.fixture
    def settings(self, tmp_path):
        from keyhac.core.settings import Settings
        store = Settings(str(tmp_path / "settings.json"))
        runtime.settings = store
        yield store
        runtime.settings = None

    def test_a_resize_is_written_down_when_it_ends(self, ui_backend, settings):
        chooser = _chooser(ui_backend)
        _press(chooser, *BOTTOM_RIGHT)
        _drag(chooser, BOTTOM_RIGHT[0] + 8, BOTTOM_RIGHT[1] + 4)
        assert settings.get("chooser_size") is None      # not until it ends
        _release(chooser, BOTTOM_RIGHT[0] + 8, BOTTOM_RIGHT[1] + 4)
        assert settings.get("chooser_size") == [80, 24]

    def test_the_next_window_opens_at_that_size(self, ui_backend, settings):
        settings.set("chooser_size", [90, 12])
        assert _chooser(ui_backend).window.frame_px()[2:] == (90.0, 12.0)

    def test_a_size_nobody_could_undo_is_clamped(self, ui_backend, settings):
        # It comes off disk, and a window too small to read or larger than the
        # screen cannot be resized back from inside itself.
        settings.set("chooser_size", [4, 4000])
        assert _chooser(ui_backend).window.frame_px()[2:] == (24.0, 100.0)

    def test_nonsense_falls_back_to_the_default(self, ui_backend, settings):
        settings.set("chooser_size", "wide please")
        assert _chooser(ui_backend).window.frame_px()[2:] == (72.0, 20.0)

    def test_headless_has_nowhere_to_write_and_does_not_mind(self, ui_backend):
        assert runtime.settings is None
        chooser = _chooser(ui_backend)
        _press(chooser, *BOTTOM_RIGHT)
        _drag(chooser, BOTTOM_RIGHT[0] + 8, BOTTOM_RIGHT[1])
        _release(chooser, BOTTOM_RIGHT[0] + 8, BOTTOM_RIGHT[1])
        assert chooser.window.frame_px()[2] == 80.0
