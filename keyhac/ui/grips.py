"""The two places a frameless window can be grabbed: the handle it is moved
by, and the grip it is resized from (issue #117).

Frameless is not decoration on the chooser - it is what makes the window
genuinely non-activating, since the panel style mask that separates "key"
from "active" forces a title bar and only frameless hides it.  So the window
cannot have a title bar back to be dragged by, nor a resize edge back to be
pulled: both have to be drawn inside the content and handled there.

Deliberately not asked of the toolkit.  macOS has `movableByWindowBackground`
and Windows the `WM_NCHITTEST` -> `HTCAPTION` reply, and both mean "drag from
anywhere the content is not a control" - which, in a window that is mostly a
list, is a gesture arguing with the list.  A handle is a *place*, and which
place is the application's to choose.

Both work in **screen coordinates**, not in the widget's own: a press
remembers the window frame and where the pointer was on screen, and every
drag applies the whole travel since then to that remembered frame.  Summing
per-event deltas instead would be wrong twice over - the widget slides out
from under the pointer as the window follows it, and a size held at its
minimum would go on accumulating travel the window never made, so it would
not start growing again where the pointer turned round.
"""

from puikit import DEFAULT_STYLE, Style
from puikit.event import EventType
from puikit.layout import SizeRequest
from puikit.widgets.base import Widget

#: Chrome, not content: the same quiet grey the badges use, so neither grip
#: competes with the rows for attention.
_GRIP_STYLE = Style(fg=(130, 130, 140))

#: The smallest the window may be dragged to, in base units.  Below this the
#: filter field has no room to show what was typed into it, which is the one
#: thing the window cannot do without.
_MIN_UNITS = (24.0, 6.0)


class _Grip(Widget):
    """A widget that turns a drag into a change to its window's frame.

    Not focusable, and never selects anything: like the scope arrows, a grip
    is for the moment the pointer is already in hand, and taking the focus
    off the filter field would break the thing the window is for.
    """

    #: Pointer shape while hovering, and while dragging.
    cursor = None
    cursor_active = None

    def __init__(self, window, glyph: str, style: Style = _GRIP_STYLE):
        self._window = window
        self.glyph = glyph
        self.style = style
        #: Where this widget sits in the window, in base units, read off the
        #: last draw - a drag can only follow one, and the widget moves
        #: whenever the window it is anchored to changes size.
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
        if self._press is not None:
            ctx.set_cursor(self.cursor_active or self.cursor)
        elif ctx.hovered:
            ctx.set_cursor(self.cursor)
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
            if pointer is None:
                return True
            (px, py), frame = self._press
            self.apply(pointer[0] - px, pointer[1] - py, frame)
            return True
        if event.type in (EventType.MOUSE_UP, EventType.MOUSE_CLICK):
            # The click a release synthesizes is swallowed with it: a grip
            # that has just moved the window must not also read as a click on
            # whatever it is drawn over.
            self._press = None
            return True
        return False

    def apply(self, dx: float, dy: float, frame) -> None:
        """Act on a total travel of (dx, dy) px since the press, which found
        the window at `frame`.  Overridden by each grip."""

    # -- geometry -----------------------------------------------------------

    def _scale(self):
        """Pixels per base unit, both axes, or None when the window cannot
        say.  Derived rather than asked for: a frameless window's frame *is*
        its drawable area, so the two the backend already reports divide."""
        frame = self._window.frame_px()
        units = self._window.size_units
        if frame is None or not units[0] or not units[1]:
            return None
        return (frame[2] / units[0], frame[3] / units[1])

    def _pointer_px(self, event):
        """Where the pointer is on screen, in the coordinates `frame_px`
        reports.  The event's own x/y are clamped to this widget once a drag
        leaves it, so the unclamped position the Panel keeps alongside them
        is what a gesture that has wandered across the screen is measured by.
        """
        if event.x is None:
            return None
        frame = self._window.frame_px()
        scale = self._scale()
        if frame is None or scale is None:
            return None
        x = event.hints.get("pointer_x", event.x)
        y = event.hints.get("pointer_y", event.y)
        return (frame[0] + (self._origin[0] + x) * scale[0],
                frame[1] + (self._origin[1] + y) * scale[1])


class DragHandle(_Grip):
    """Drag the window by its magnifier.

    The glyph is dead space and sits where a title bar would have put the
    drag handle, which is the whole argument for it being this one.
    """

    cursor = "grab"
    cursor_active = "grabbing"

    def apply(self, dx: float, dy: float, frame) -> None:
        """lazydocs: ignore"""
        self._window.move_to_px(frame[0] + dx, frame[1] + dy)


class ResizeGrip(_Grip):
    """Resize the window from its bottom-right corner.

    Bottom-right because that is the corner `WindowHandle.resize_to_px` grows
    towards: it holds the top-left still, so the window opens out under the
    pointer instead of walking away from it.
    """

    cursor = "nwse-resize"

    def apply(self, dx: float, dy: float, frame) -> None:
        """lazydocs: ignore"""
        scale = self._scale()
        if scale is None:
            return
        self._window.resize_to_px(
            max(_MIN_UNITS[0] * scale[0], frame[2] + dx),
            max(_MIN_UNITS[1] * scale[1], frame[3] + dy))
