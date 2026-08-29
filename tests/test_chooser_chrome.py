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


def _sync_pointer(chooser, x, y) -> None:
    """Put the OS pointer where an event at these window coordinates says it
    is - which is what is true at the moment an event is posted, and stops
    being true as soon as the gesture moves the window. One pixel per cell
    here, so the window coordinate needs no scaling."""
    frame = chooser.window.frame_px()
    chooser._backend.pointer_px = (frame[0] + x, frame[1] + y)


def _press(chooser, x, y):
    _sync_pointer(chooser, x, y)
    chooser._on_event(Event(type=EventType.MOUSE_DOWN, x=x, y=y, button="left"))


def _drag(chooser, x, y):
    _sync_pointer(chooser, x, y)
    chooser._on_event(Event(type=EventType.MOUSE_DRAG, x=x, y=y, button="left"))


def _release(chooser, x, y):
    _sync_pointer(chooser, x, y)
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
        # With no pointer from the OS the event answers instead - and the
        # Panel clamps its x/y to the widget once the pointer leaves it,
        # keeping the true position alongside. Reading the clamped one would
        # pin the window a character from where it started.
        chooser = _chooser(ui_backend)
        _press(chooser, *HANDLE)
        ui_backend.pointer_px = None
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
    """macOS clips a window to a rounded rectangle (15 pt for anything with a
    frame under it; a borderless one is square) and Windows 11 rounds a popup
    too, so a square border drawn at the window's extent loses exactly its
    four corners to that clip - which is what it did. The radius is the
    window's own: `WindowHandle.corner_radius_px` is the platform's fact."""

    def _rounded_backend(self, monkeypatch, radius=15.0):
        # MemoryBackend masks vector_shapes off - it is a character grid - so a
        # test that wants the vector path re-enables it, the way puikit's own
        # rounded-face tests do, and says its windows have rounded corners.
        from puikit import PROFILE_GUI_DESKTOP
        from puikit.backends import memory_backend as mb
        from puikit.capability import CapabilityProfile

        class VectorBackend(mb.MemoryBackend):
            @property
            def capabilities(self):
                return CapabilityProfile({**PROFILE_GUI_DESKTOP,
                                          "vector_shapes": True})

        monkeypatch.setattr(mb._MemoryWindowHandle, "corner_radius_px",
                            property(lambda self: radius), raising=False)
        backend = VectorBackend(width=100, height=30,
                                capabilities=PROFILE_GUI_DESKTOP)
        backend.open()
        runtime.backend = backend
        return backend

    def test_the_line_is_round_and_just_inside_the_corner(self, ui_backend,
                                                          monkeypatch):
        backend = self._rounded_backend(monkeypatch)
        try:
            _chooser(backend)
            # first: the page frame draws before anything nested in it
            x, y, w, h, radius = backend.round_rect_calls[0][:5]
        finally:
            runtime.backend = ui_backend
            backend.close()
        # half a pixel in on every side - the outermost thing the window
        # draws, not a second edge inside a rim of background
        assert (x, y) == (0.5, 0.5)
        assert (w, h) == (72.0 - 1.0, 20.0 - 1.0)
        # ... and concentric with the corner it sits in
        assert radius == 14.5

    def test_a_square_cornered_window_keeps_its_box(self, ui_backend):
        # Nothing there to round: the memory backend's corners are square, as
        # a borderless macOS window's are.
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


class TestTheGestureAsksTheOSWhereThePointerIs:
    """An event's position is measured against a window and frozen when the
    event was posted. The top and left edges *move* that window as they
    resize it, so adding its current origin to a location taken against its
    previous one overstates the travel by exactly the move - and the
    correction feeds the next frame. Dragging the top edge oscillated; the
    bottom-right corner, the one gesture that never moves the window, did
    not."""

    def test_a_stale_event_does_not_move_the_top_edge_again(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *TOP)
        _drag(chooser, TOP[0], TOP[1] - 4)             # window top now at 156
        settled = chooser.window.frame_px()
        assert settled[1:2] + settled[3:] == (156.0, 24.0)

        # The pointer has not moved. This event was posted before the window
        # was, so it still measures against the old top - the shape of event
        # that made the window oscillate.
        chooser._on_event(Event(type=EventType.MOUSE_DRAG, x=TOP[0],
                                y=TOP[1] - 4, button="left"))
        assert chooser.window.frame_px() == settled

    def test_the_same_is_true_of_the_left_edge(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *LEFT)
        _drag(chooser, LEFT[0] - 6, LEFT[1])
        settled = chooser.window.frame_px()
        chooser._on_event(Event(type=EventType.MOUSE_DRAG, x=LEFT[0] - 6,
                                y=LEFT[1], button="left"))
        assert chooser.window.frame_px() == settled

    def test_the_os_pointer_wins_over_the_event(self, ui_backend):
        chooser = _chooser(ui_backend)
        _press(chooser, *BOTTOM_RIGHT)
        ui_backend.pointer_px = (160 + BOTTOM_RIGHT[0] + 10,
                                 160 + BOTTOM_RIGHT[1] + 3)
        chooser._on_event(Event(type=EventType.MOUSE_DRAG, x=BOTTOM_RIGHT[0],
                                y=BOTTOM_RIGHT[1], button="left"))
        assert chooser.window.frame_px()[2:] == (82.0, 23.0)

    def test_a_near_edge_sets_its_whole_frame_at_once(self, ui_backend):
        # Not move-then-resize: in between, the far edge - the one being held
        # still - would be somewhere else, and it shows as a twitch.
        chooser = _chooser(ui_backend)
        calls = []
        set_frame = chooser.window.set_frame_px
        chooser.window.move_to_px = lambda *a: calls.append("move")
        chooser.window.set_frame_px = lambda *a: (calls.append("frame"),
                                                 set_frame(*a))[1]
        _press(chooser, *TOP)
        _drag(chooser, TOP[0], TOP[1] - 4)
        assert calls == ["frame"]
        assert chooser.window.frame_px() == (160.0, 156.0, 72.0, 24.0)


class TestAResizeOnlyTouchesTheAxisItIsOn:
    """A window frame is a rectangle of whole pixels, and the chooser's is
    often asked for at a half - it centres itself on another window. Re-sending
    that half every step of a drag argues with the platform's own snapping,
    one pixel at a time, and the window shivers sideways while its top edge is
    being dragged."""

    def _offset_chooser(self, ui_backend, x=160.5, y=160.0):
        chooser = _chooser(ui_backend)
        chooser.window.x, chooser.window.y = x, y
        return chooser

    def test_the_top_edge_leaves_the_horizontal_axis_alone(self, ui_backend):
        chooser = self._offset_chooser(ui_backend)
        _press(chooser, *TOP)
        for step in (1, 2, 3):
            _drag(chooser, TOP[0], TOP[1] - step)
            x, _y, w, _h = chooser.window.frame_px()
            assert (x, w) == (160.5, 72.0), "the untouched axis moved"

    def test_the_frame_it_asks_for_is_whole_pixels(self, ui_backend):
        chooser = _chooser(ui_backend)
        asked = []
        chooser.window.set_frame_px = lambda *a: asked.append(a)
        _press(chooser, *TOP)
        _drag(chooser, TOP[0], TOP[1] - 3)
        assert asked and all(v == int(v) for v in asked[0])

    def test_at_the_minimum_it_stops_asking_at_all(self, ui_backend):
        # Every further step computes the same rectangle; setting it again is
        # a window-server update and a redisplay for a window that is not
        # moving, which is what jittered at the limit.
        chooser = _chooser(ui_backend)
        _press(chooser, *TOP)
        _drag(chooser, TOP[0], TOP[1] + 40)            # well past the minimum
        settled = chooser.window.frame_px()
        assert settled[3] == 6.0
        calls = []
        chooser.window.set_frame_px = lambda *a: calls.append(a)
        chooser.window.resize_to_px = lambda *a: calls.append(a)
        for step in (50, 60, 70):
            _drag(chooser, TOP[0], TOP[1] + step)
        assert calls == []
        assert chooser.window.frame_px() == settled

    def test_the_bottom_settles_on_a_pixel_and_stays_there(self, ui_backend):
        # The far edge is what a near-edge drag holds still. A window that
        # began on a half pixel has to land on one at the first step - it is
        # being rounded, once - and must not move again after that, however
        # far the drag goes, minimum included.
        chooser = self._offset_chooser(ui_backend, y=160.5)
        _press(chooser, *TOP)
        _drag(chooser, TOP[0], TOP[1] + 2.5)
        _x, y, _w, h = chooser.window.frame_px()
        bottom = y + h
        assert bottom == round(bottom)
        for step in (7.5, 12, 40):
            _drag(chooser, TOP[0], TOP[1] + step)
            _x, y, _w, h = chooser.window.frame_px()
            assert y + h == bottom


class TestOnlyOneGesturePerPress:
    """`frameless` hides the title bar the panel mask forces; it does not
    remove it, and AppKit goes on dragging the window by it - which is the
    top edge, where the resize gesture is. One press was moving the window
    and resizing it at once."""

    def test_the_window_does_not_offer_itself_to_the_window_manager(
            self, ui_backend):
        assert _chooser(ui_backend).window.window_style.movable is False

    def test_a_chooser_that_takes_focus_is_an_ordinary_window_again(
            self, ui_backend):
        # It has a real title bar and no drag handle of its own, so dragging
        # it is the window manager's job, as it always was.
        chooser = _chooser(ui_backend, activates=True)
        assert chooser.window.window_style.movable is True

    def test_the_app_can_still_move_it(self, ui_backend):
        # movable=False is about the user's drag, not the app's: the handle
        # moves the window through move_to_px either way.
        chooser = _chooser(ui_backend)
        _press(chooser, *HANDLE)
        _drag(chooser, HANDLE[0] + 14, HANDLE[1] + 6)
        assert chooser.window.frame_px()[:2] == (174.0, 166.0)


class TestACornerIsATargetYouCanHit:
    """An edge is aimed at with one coordinate and a corner with two, so a
    corner sized like the edge is a six-pixel square the pointer crosses
    without landing in - which reads as "the corner does not resize"."""

    def _pixel_chooser(self, ui_backend):
        # A backend whose pixels mean something: one cell per pixel here, so
        # the strip depths are in cells and exact.
        from puikit import PROFILE_GUI_DESKTOP
        from puikit.backends.memory_backend import MemoryBackend
        backend = MemoryBackend(width=200, height=90,
                                capabilities=PROFILE_GUI_DESKTOP)
        backend.open()
        runtime.backend = backend
        chooser = _chooser(backend)
        chooser.window.resize_to_px(120, 60)
        return chooser

    def test_a_point_near_two_edges_is_the_corner(self, ui_backend):
        chooser = self._pixel_chooser(ui_backend)
        try:
            # 10 px from the right and 12 from the bottom: outside both
            # 6 px edge strips, inside the corner's reach.
            assert chooser._resizer.edge_at(110, 48) == (1, 1)
            assert chooser._resizer.edge_at(10, 12) == (-1, -1)
            assert chooser._resizer.edge_at(110, 12) == (1, -1)
        finally:
            runtime.backend = ui_backend
            chooser.window.close()

    def test_the_middle_of_an_edge_stays_one_axis(self, ui_backend):
        chooser = self._pixel_chooser(ui_backend)
        try:
            assert chooser._resizer.edge_at(118, 30) == (1, 0)
            assert chooser._resizer.edge_at(60, 58) == (0, 1)
        finally:
            runtime.backend = ui_backend
            chooser.window.close()

    def test_a_corner_drag_moves_both_axes(self, ui_backend):
        chooser = self._pixel_chooser(ui_backend)
        try:
            _press(chooser, 110, 48)
            _drag(chooser, 130, 60)
            assert chooser.window.frame_px()[2:] == (140.0, 72.0)
        finally:
            runtime.backend = ui_backend
            chooser.window.close()

    def test_the_corner_never_swallows_a_third_of_the_window(self, ui_backend):
        # A window dragged down to its minimum must not become all edge and
        # no content.
        chooser = self._pixel_chooser(ui_backend)
        try:
            chooser.window.resize_to_px(30, 24)
            assert chooser._resizer.edge_at(15, 12) is None
        finally:
            runtime.backend = ui_backend
            chooser.window.close()


class TestTheLitEdgeFollowsThePointer:
    """Nothing here shapes the pointer. macOS gives the pointer to the key
    window, and this one deliberately never becomes key - measured: before it
    is clicked the panel reports key=False with the application inactive, and
    every shape it asked for was recorded and never reached the screen; after
    a click it is key and the same request works. An affordance that appears
    only once you have already found the thing is worse than none, so the
    window lights its own border instead."""

    def _lit(self, chooser, x, y):
        chooser._on_event(Event(type=EventType.MOUSE_MOVE, x=x, y=y))
        return chooser._page.hot_edge

    def test_each_edge_and_corner_lights_its_own(self, ui_backend):
        chooser = _chooser(ui_backend)
        resizer = chooser._resizer
        assert resizer.edge_at(*RIGHT) == (1, 0)
        assert resizer.edge_at(*TOP) == (0, -1)
        assert resizer.edge_at(*BOTTOM_RIGHT) == (1, 1)
        assert resizer.edge_at(0.5, 19.5) == (-1, 1)
        assert resizer.edge_at(36, 10) is None

    def test_hovering_hands_the_edge_to_the_frame_that_draws_it(
            self, ui_backend):
        # The edge is not a widget, so nothing hovers it and nothing would
        # light it on its own.
        chooser = _chooser(ui_backend)
        assert self._lit(chooser, *RIGHT) == (1, 0)
        assert self._lit(chooser, 36, 10) is None

    def test_a_pointer_that_has_left_is_not_on_an_edge(self, ui_backend):
        # The pointer leaving arrives as a move to (-1, -1), and "near the
        # start of the axis" is true of every negative number - so the window
        # lit its top-left corner on the way out, and kept it lit.
        chooser = _chooser(ui_backend)
        self._lit(chooser, *RIGHT)
        assert chooser._resizer.edge_at(-1.0, -1.0) is None
        assert self._lit(chooser, -1.0, -1.0) is None

    def test_arriving_on_an_edge_lights_it_at_once(self, ui_backend):
        # Crossing into the window and stopping on its edge is an entry and no
        # move (puikit 1.4.2 reports the arrival as a move at that point), so
        # this is the whole of what the user does approaching from outside.
        chooser = _chooser(ui_backend)
        self._lit(chooser, -1.0, -1.0)
        assert self._lit(chooser, *BOTTOM_RIGHT) == (1, 1)

    def test_it_follows_the_live_pointer_not_the_event(self, ui_backend):
        # A hand moving quickly crosses the boundary and comes to rest on the
        # edge; the event reporting the arrival says where the boundary was
        # crossed, which by then is a point the pointer has left.
        chooser = _chooser(ui_backend)
        frame = chooser.window.frame_px()
        ui_backend.pointer_px = (frame[0] + BOTTOM_RIGHT[0],
                                 frame[1] + BOTTOM_RIGHT[1])
        assert self._lit(chooser, 36.0, 4.0) == (1, 1)

    def test_it_still_reads_the_event_when_the_backend_cannot_say(
            self, ui_backend):
        chooser = _chooser(ui_backend)
        ui_backend.pointer_px = None
        assert self._lit(chooser, *TOP) == (0, -1)

    def test_a_pointer_resting_outside_lights_nothing(self, ui_backend):
        chooser = _chooser(ui_backend)
        frame = chooser.window.frame_px()
        ui_backend.pointer_px = (frame[0] - 40, frame[1] + 5)
        assert self._lit(chooser, *RIGHT) is None


class TestTheEdgeLightsUpUnderThePointer:
    """macOS gives the pointer to the key window, and the chooser deliberately
    never becomes one - measured: before it is clicked the panel reports
    key=False and the application inactive, and the shape it asks for never
    reaches the screen; after a click it is key and it does. So the affordance
    cannot be the pointer alone. The window draws it on the border it already
    draws, which needs nobody's permission."""

    def _lit(self, chooser, x, y):
        chooser._on_event(Event(type=EventType.MOUSE_MOVE, x=x, y=y))
        return chooser._page.hot_edge

    def test_each_side_lights_its_own(self, ui_backend):
        chooser = _chooser(ui_backend)
        assert self._lit(chooser, *RIGHT) == (1, 0)
        assert self._lit(chooser, *TOP) == (0, -1)
        assert self._lit(chooser, *LEFT) == (-1, 0)

    def test_a_corner_lights_both_of_its_sides(self, ui_backend):
        # Which is the whole reason it is drawn as bars and not as a second
        # outline: the corner has to say which two directions it drags in.
        chooser = _chooser(ui_backend)
        assert self._lit(chooser, *BOTTOM_RIGHT) == (1, 1)

    def test_the_middle_lights_nothing(self, ui_backend):
        chooser = _chooser(ui_backend)
        assert self._lit(chooser, 36, 10) is None

    def test_it_goes_out_when_the_pointer_leaves(self, ui_backend):
        chooser = _chooser(ui_backend)
        self._lit(chooser, *RIGHT)
        assert self._lit(chooser, -1.0, -1.0) is None

    def test_nothing_asks_for_a_pointer_shape(self, ui_backend):
        # Consistency over a hint that only works after a click: hovering
        # changes no cursor at all now.
        chooser = _chooser(ui_backend)
        shapes = []
        ui_backend.set_pointer_shape = shapes.append
        self._lit(chooser, *BOTTOM_RIGHT)
        assert not [s for s in shapes if s]

    def _fills(self, ui_backend, x, y, radius=0.0):
        """What the lit edge filled, on a backend whose pixels mean something.

        Only its own fills: the field box, the selection and the scrollbar
        fill rectangles too, so these are picked out by their colour, which is
        the hot colour or a step of its fade towards the background."""
        from puikit import PROFILE_GUI_DESKTOP
        from puikit.backends.memory_backend import MemoryBackend
        from puikit.backends import memory_backend as mb
        from keyhac.ui.frame import _HOT

        backend = MemoryBackend(width=300, height=200,
                                capabilities=PROFILE_GUI_DESKTOP)
        backend.open()
        runtime.backend = backend
        saved = mb._MemoryWindowHandle.corner_radius_px
        mb._MemoryWindowHandle.corner_radius_px = property(lambda self: radius)
        try:
            chooser = _chooser(backend)
            chooser.window.resize_to_px(200, 120)
            fills = []
            native = backend.fill_rect
            backend.fill_rect = lambda *a: fills.append(a)
            chooser._on_event(Event(type=EventType.MOUSE_MOVE, x=x, y=y))
            backend.fill_rect = native
            # on the ramp from the background to the hot colour, and nothing
            # else in this window is on it
            hot = [f for f in fills if f[4].fg and f[4].fg[0] == f[4].fg[1]
                   and f[4].fg[2] > f[4].fg[0] and f[4].fg[0] <= _HOT[0]]
            return hot, chooser
        finally:
            mb._MemoryWindowHandle.corner_radius_px = saved
            runtime.backend = ui_backend
            backend.close()

    def test_the_lit_side_runs_down_the_side_it_is_on(self, ui_backend):
        fills, _c = self._fills(ui_backend, 199.0, 60.0)
        assert fills, "the lit edge drew nothing"
        assert all(f[0] > 190 for f in fills), "it strayed off its own side"
        assert sum(f[3] for f in fills) > 60, "it did not run down the side"

    def test_its_ends_dissolve_into_the_border(self, ui_backend):
        fills, _c = self._fills(ui_backend, 199.0, 60.0)
        by_y = sorted(fills, key=lambda f: f[1])

        def brightness(fill):
            return sum(fill[4].fg) / 3

        middle = max(brightness(f) for f in fills)
        assert brightness(by_y[0]) < middle, "the top end stopped dead"
        assert brightness(by_y[-1]) < middle, "the bottom end stopped dead"

    def test_a_corner_lights_both_sides_and_the_curve_between_them(
            self, ui_backend):
        fills, _c = self._fills(ui_backend, 199.0, 119.0, radius=15.0)
        assert any(f[3] > f[2] for f in fills), "no vertical side"
        assert any(f[2] > f[3] for f in fills), "no horizontal side"
        # the curve itself, walked as overlapping squares
        corner = [f for f in fills if f[0] > 200 - 19 and f[1] > 120 - 19
                  and f[2] < 6 and f[3] < 6]
        assert len(corner) > 4, "the corner between them stayed dark"

    def test_a_square_cornered_window_needs_no_curve(self, ui_backend):
        # Its two sides already meet there, so nothing walks between them.
        fills, _c = self._fills(ui_backend, 199.0, 119.0, radius=0.0)
        walked = [f for f in fills if f[0] > 200 - 19 and f[1] > 120 - 19
                  and f[2] < 6 and f[3] < 6]
        assert walked == []
