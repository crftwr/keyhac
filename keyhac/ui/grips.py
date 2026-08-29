"""The two ways a frameless window is grabbed: the handle it is moved by, and
its own edge, which resizes it (issue #117).

Frameless is not decoration on the chooser - it is what makes the window
genuinely non-activating, since the panel style mask that separates "key"
from "active" forces a title bar and only frameless hides it.  So the window
cannot have a title bar back to be dragged by, nor a resize edge the window
manager offers: both have to be found in the content and handled there.

None of it shapes the **pointer**.  macOS gives the pointer to the key
window, and this one deliberately never becomes key - that is what leaves the
target application its focus, its caret and its selection - so a shape it
asked for reached the screen only after a click, and not before.  An
affordance that turns up once you have already found the thing is worse than
none, so the window says it where it can always say it: `Frame.hot` draws the
whole border in the accent colour while the pointer is somewhere the window
can be grabbed.

Deliberately not asked of the toolkit.  macOS has `movableByWindowBackground`
and Windows the `WM_NCHITTEST` -> `HTCAPTION` reply, and both mean "drag from
anywhere the content is not a control" - which, in a window that is mostly a
list, is a gesture arguing with the list.  A handle is a *place*, and which
place is the application's to choose.

Both work in **screen coordinates**, not in the window's own: a press
remembers the window frame and where the pointer was on screen, and every
drag applies the whole travel since then to that remembered frame.  Summing
per-event deltas instead would be wrong twice over - what was grabbed slides
out from under the pointer as the window follows it, and a size held at its
minimum would go on accumulating travel the window never made, so it would
not start growing again where the pointer turned round.

That screen position comes from the **OS**, not from the event, and the
difference is not academic.  A mouse event carries its position relative to a
window, frozen when the event was posted; these gestures *move that window*
while they run, so adding the window's current origin to a location taken
against its previous one overstates the travel by exactly the move - and the
correction feeds the next frame.  Dragging the top edge oscillated, while the
bottom-right corner, which is the one gesture that never moves the window,
was fine.  `Backend.pointer_position_px()` never mentions a window and cannot
have the problem; the event is the fallback for a backend that cannot say.
"""

from puikit import Style
from puikit.event import EventType
from puikit.layout import SizeRequest
from puikit.widgets.base import Widget

#: Chrome, not content: the same quiet grey the badges use, so the handle
#: does not compete with the rows for attention.
_GRIP_STYLE = Style(fg=(130, 130, 140))

#: The smallest the window may be dragged to, in base units.  Below this the
#: filter field has no room to show what was typed into it, which is the one
#: thing the window cannot do without.
MIN_UNITS = (24.0, 6.0)


def _pointer_on_screen(backend, fallback):
    """Where the pointer is now, in the coordinates `frame_px` reports.

    `fallback()` derives it from the event instead, for a backend with no
    answer - which is right for every gesture that leaves the window where it
    is, and the best available for the ones that do not.
    """
    if backend is not None:
        pointer = backend.pointer_position_px()
        if pointer is not None:
            return pointer
    return fallback()


def _pixel_span(start, length):
    """One axis of a frame, rounded to whole pixels **by its two edges**.

    A window frame is a rectangle of whole pixels, and fractions reach here
    honestly: a minimum size is six rows of whatever the font measures, and a
    chooser centred on another window starts at a half.  Asking for those is
    asking the platform to round them, which it does per frame and not always
    the same way - that was the jitter at the minimum height.

    By the edges, not by the origin and the length, because rounding a length
    that starts at a half would move the far edge - and the far edge is
    exactly what a resize from the near side is holding still.
    """
    near = round(start)
    return near, round(start + length) - near


def _scale(window):
    """Pixels per base unit, both axes, or None when the window cannot say.
    Derived rather than asked for: a frameless window's frame *is* its
    drawable area, so the two numbers the backend already reports divide."""
    frame = window.frame_px()
    units = window.size_units
    if frame is None or not units[0] or not units[1]:
        return None
    return (frame[2] / units[0], frame[3] / units[1])


class DragHandle(Widget):
    """Drag the window by its magnifier.

    The glyph is dead space and sits where a title bar would have put the
    drag handle, which is the whole argument for it being this one.

    Not focusable, and it never selects anything: like the scope arrows, it
    is for the moment the pointer is already in hand, and taking the focus
    off the filter field would break the thing the window is for.
    """

    def __init__(self, window, glyph: str, style: Style = _GRIP_STYLE,
                 backend=None):
        self._window = window
        self._backend = backend
        self.glyph = glyph
        self.style = style
        #: Where this widget sits in the window, in base units, read off the
        #: last draw - a drag can only follow one.
        self._origin = (0.0, 0.0)
        #: (pointer on screen, window frame) at the press, or None.
        self._press = None

    def measure(self, ctx, axis, available):
        """lazydocs: ignore"""
        if axis != "x":
            return SizeRequest(min=1, preferred=1)
        width = ctx.measure_text(self.glyph, self.style)
        return SizeRequest(min=width, preferred=width)

    def draw(self, ctx) -> None:
        """lazydocs: ignore"""
        self._origin = ctx.screen_rect[:2]
        ctx.draw_text(0, 0, self.glyph, self.style)

    def handle_event(self, event) -> bool:
        """lazydocs: ignore"""
        if event.type is EventType.MOUSE_DOWN:
            pointer = self._pointer_px(event)
            frame = self._window.frame_px()
            self._press = (pointer, frame) if pointer and frame else None
            return True
        if event.type is EventType.MOUSE_DRAG and self._press is not None:
            pointer = self._pointer_px(event)
            if pointer is not None:
                (px, py), frame = self._press
                x, _w = _pixel_span(frame[0] + pointer[0] - px, 0)
                y, _h = _pixel_span(frame[1] + pointer[1] - py, 0)
                self._window.move_to_px(x, y)
            return True
        if event.type in (EventType.MOUSE_UP, EventType.MOUSE_CLICK):
            # The click a release synthesizes is swallowed with it: a handle
            # that has just moved the window must not also read as a click on
            # whatever it is drawn over.
            self._press = None
            return True
        return False

    def _pointer_px(self, event):
        """Where the pointer is on screen, in the coordinates `frame_px`
        reports - from the OS, or worked out from the event where the backend
        has no answer."""
        return _pointer_on_screen(self._backend,
                                  lambda: self._from_event(event))

    def _from_event(self, event):
        """The event's own position, put back into screen coordinates.  Its
        x/y are clamped to this widget once a drag leaves it, so the unclamped
        position the Panel keeps alongside them is what a gesture that has
        wandered across the screen is measured by."""
        if event.x is None:
            return None
        frame = self._window.frame_px()
        scale = _scale(self._window)
        if frame is None or scale is None:
            return None
        x = event.hints.get("pointer_x", event.x)
        y = event.hints.get("pointer_y", event.y)
        return (frame[0] + (self._origin[0] + x) * scale[0],
                frame[1] + (self._origin[1] + y) * scale[1])


class EdgeResizer:
    """Resize the window by dragging its own edge - the place a window manager
    would have offered if the window had a frame.

    Not a widget, and that is the point.  The first version of this drew a
    grip in the bottom-right corner, which needed a row of its own, and a row
    is a candidate the window stopped being able to show.  The edge costs the
    content nothing: it is the strip the border is drawn in, where no row can
    be anyway, so it is read straight off the window's event stream before
    the Panel sees it.

    Every edge and corner works, so the window opens out in whichever
    direction there is room - which means the ones that hold the *far* side
    still have to move the window as they resize it, since `resize_to_px`
    always keeps the top-left corner.
    """

    #: How deep the grab strip is, in device pixels.  A window manager gives
    #: a frame about this much *plus* a band outside the window, which a
    #: frameless window does not have to give - so this is the generous end
    #: of what fits: the page margin is five pixels and the list's own frame
    #: another four, so eight still lands on chrome rather than on a row.
    DEPTH_PX = 8.0
    DEPTH_UNITS = 1.0

    #: How far along each edge a *corner* reaches, in the same pixels.  Deeper
    #: than the edge on purpose: an edge is aimed at with one coordinate and a
    #: corner with two, so a corner sized like the edge is a six-pixel square
    #: that the pointer crosses without ever landing in - which reads as "the
    #: corner does not resize", not as "I missed it".  Every window manager
    #: gives the corner the larger target for the same reason.
    CORNER_PX = 16.0

    def __init__(self, window, min_units=MIN_UNITS, on_resized=None,
                 backend=None):
        self._window = window
        self._backend = backend
        self._min_units = min_units
        #: Called with the new size in base units once a resize ends, for a
        #: caller that remembers it.
        self.on_resized = on_resized
        #: ((ex, ey), pointer on screen, window frame) at the press, or None.
        self._press = None

    @property
    def resizing(self) -> bool:
        return self._press is not None

    def edge_at(self, x: float, y: float):
        """Which edge (ex, ey) the point (x, y) in window base units is on,
        each -1 / 0 / +1, or None for a point that is not on one.

        A corner is both at once, and it is looked for first, in a square
        that reaches `CORNER_PX` along each axis - deeper than the edges,
        because an edge is aimed at with one coordinate and a corner with two.
        """
        units = self._window.size_units
        scale = _scale(self._window)
        if scale is None:
            return None
        if not (0 <= x <= units[0] and 0 <= y <= units[1]):
            # Outside the window is not an edge of it.  Worth saying out
            # loud, because the arithmetic below would happily call it one:
            # "near the start of the axis" is true of every negative number,
            # and the pointer leaving arrives as a move to (-1, -1) - so the
            # window asked for a top-left corner cursor on the way out.
            return None
        # The corner square first, and it reaches further along both axes
        # than the edge strips do: inside it the answer is both axes, so the
        # region that resizes width and height together is a target rather
        # than the six-pixel overlap of two strips.
        cx = self._side(x, units[0], self.CORNER_PX, scale[0])
        cy = self._side(y, units[1], self.CORNER_PX, scale[1])
        if cx and cy:
            return (cx, cy)
        ex = self._side(x, units[0], self.DEPTH_PX, scale[0])
        ey = self._side(y, units[1], self.DEPTH_PX, scale[1])
        return (ex, ey) if (ex or ey) else None

    def _side(self, value: float, extent: float, depth_px: float,
              scale: float) -> int:
        """-1 near the start of an axis, +1 near its end, 0 in between.

        The depth is in device pixels where those mean something and one cell
        where they do not: a character grid's cell *is* about a pixel, so six
        of them would be six rows of the list.  And never more than a third
        of the axis, so a window dragged down to its minimum does not become
        all edge and no content.
        """
        if self._pixel_layout():
            depth = depth_px / scale
        else:
            depth = self.DEPTH_UNITS
        depth = min(depth, extent / 3.0)
        if value < depth:
            return -1
        return 1 if value > extent - depth else 0

    def _pixel_layout(self) -> bool:
        if self._backend is None:
            return False
        return self._backend.capabilities.supports("pixel_layout")

    def edge_now(self, x: float = None, y: float = None):
        """Which edge the pointer is on **now**, falling back to the window
        coordinates given.

        The live pointer, for the same reason the drag uses it: an event says
        where the pointer was when the event was posted.  The one that reports
        the pointer *arriving* says where it crossed the window's boundary,
        which for a hand moving quickly is not where it has stopped - the
        pointer came to rest on the edge and the window was told about a point
        it had already left.
        """
        here = self._pointer_in_window()
        if here is None:
            here = (x, y) if x is not None else None
        return self.edge_at(*here) if here else None

    def _pointer_in_window(self):
        """The live pointer in window base units, or None where the backend
        cannot say."""
        pointer = _pointer_on_screen(self._backend, lambda: None)
        frame = self._window.frame_px()
        scale = _scale(self._window)
        if pointer is None or frame is None or scale is None:
            return None
        return ((pointer[0] - frame[0]) / scale[0],
                (pointer[1] - frame[1]) / scale[1])

    def handle(self, event) -> bool:
        """Take the event if it belongs to a resize; leave it otherwise.

        lazydocs: ignore
        """
        if event.type is EventType.MOUSE_DOWN:
            return self._start(event)
        if self._press is None:
            return False
        if event.type is EventType.MOUSE_DRAG:
            self._apply(event)
            return True
        if event.type in (EventType.MOUSE_UP, EventType.MOUSE_CLICK):
            ending, self._press = self._press, None
            if ending is not None and event.type is EventType.MOUSE_UP:
                self._announce()
            return True
        return False

    # -- internals ----------------------------------------------------------

    def _start(self, event) -> bool:
        if event.x is None:
            return False
        edge = self.edge_at(event.x, event.y)
        frame = self._window.frame_px()
        pointer = self._pointer_px(event)
        if edge is None or frame is None or pointer is None:
            return False
        self._press = (edge, pointer, frame)
        return True

    def _apply(self, event) -> None:
        pointer = self._pointer_px(event)
        current = self._window.frame_px()
        scale = _scale(self._window)
        if pointer is None or current is None or scale is None:
            return
        (ex, ey), (px, py), (fx, fy, fw, fh) = self._press
        dx, dy = pointer[0] - px, pointer[1] - py
        # The axis this drag is not on is passed straight back through, from
        # the window's *current* frame and unrounded.  Re-sending the frame
        # from the press instead - a request the platform granted
        # approximately, since the chooser centres itself on another window
        # and can start on a half pixel - argued with its own snapping one
        # pixel at a time, which is the sideways shiver a top-edge drag had.
        x, w = (_pixel_span(*self._axis(fx, fw, dx, ex,
                                        self._min_units[0] * scale[0]))
                if ex else (current[0], current[2]))
        y, h = (_pixel_span(*self._axis(fy, fh, dy, ey,
                                        self._min_units[1] * scale[1]))
                if ey else (current[1], current[3]))
        target = (x, y, w, h)
        if target == tuple(current):
            # Nothing to ask for.  Worth the check: at the minimum size every
            # further step of the drag computes the same rectangle, and
            # setting it again is a window-server update and a redisplay for
            # a window that is not moving.
            return
        if target[:2] != tuple(current[:2]):
            # One call, because the near edges move the window as they resize
            # it: as move-then-resize the window passes through a frame with
            # the new origin and the old size, and the far edge - the one the
            # user is holding still - twitches once per step of the drag.
            self._window.set_frame_px(*target)
        else:
            self._window.resize_to_px(target[2], target[3])

    @staticmethod
    def _axis(origin, length, travel, edge, minimum):
        """One axis of the new frame: where the window starts and how long it
        is, given which side was grabbed."""
        if edge > 0:
            return origin, max(minimum, length + travel)
        if edge < 0:
            # The far side stays put, so everything the near side gives up in
            # length it takes back in origin - including when the minimum
            # stops it, or the window would go on sliding while not shrinking.
            new_length = max(minimum, length - travel)
            return origin + length - new_length, new_length
        return origin, length

    def _announce(self) -> None:
        if self.on_resized is None:
            return
        w, h = self._window.size_units
        self.on_resized(w, h)

    def _pointer_px(self, event):
        """The pointer on screen - from the OS, or from the event where the
        backend has no answer."""
        return _pointer_on_screen(self._backend,
                                  lambda: self._from_event(event))

    def _from_event(self, event):
        """The event's own position, put back into screen coordinates."""
        frame = self._window.frame_px()
        scale = _scale(self._window)
        if event.x is None or frame is None or scale is None:
            return None
        x = event.hints.get("pointer_x", event.x)
        y = event.hints.get("pointer_y", event.y)
        return (frame[0] + x * scale[0], frame[1] + y * scale[1])
