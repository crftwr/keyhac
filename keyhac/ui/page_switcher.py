"""The page indicator at the right of the filter field: `‹ Name ›`.

The arrows carry two jobs. A key-driven switch (Left / Right) has no
visible affordance of its own, so they are what says one exists at all - the
discoverability cost of preferring a key over a typed prefix, paid back. And
they are clickable, for the moment the pointer is already in hand: the
chooser reaches its window through `overlay_input="mouse"` on macOS and
`WS_EX_NOACTIVATE` on Windows, so a click arrives without the application
underneath losing anything.

Deliberately **not focusable**. A click here must not take the focus off the
filter field - the point of the whole two-pane arrangement is that the field
is where typing goes, and nothing about switching page changes that.
"""

from puikit import DEFAULT_STYLE, Style
from puikit.event import EventType
from puikit.layout import SizeRequest
from puikit.widgets.base import Widget

_PREV, _NEXT = "‹", "›"

#: The name is context rather than content, and reads a shade quieter than
#: the query beside it - but the arrows stay at full strength, since they are
#: the part being offered as a control.
_NAME_STYLE = Style(fg=(130, 130, 140))


class PageSwitcher(Widget):
    """`‹ Name ›`, where clicking an arrow moves one page.

    **An arrow is drawn only where there is somewhere to go.** The pages stop
    at the ends rather than wrapping, so a chevron at the last page would be
    offering a move that does not happen - and the row's whole job is to say
    where you are in it. At an end it also says *which* end.

    The space it occupied stays occupied. Dropping the glyph outright would
    narrow the widget, and the field beside it grows to fill what it gave up,
    so the name would jump sideways every time you reached an edge - a row
    that moves as you page is worse than one with a gap in it.
    """

    def __init__(self, name: str = "", on_switch=None,
                 style: Style = DEFAULT_STYLE,
                 name_style: Style = _NAME_STYLE):
        self.name = name
        self.on_switch = on_switch          # called with -1 or +1
        #: Whether there is a page that way. False blanks the arrow.
        self.can_prev = True
        self.can_next = True
        self.style = style
        self.name_style = name_style
        self._origin = 0.0
        self._width = 0.0

    def text(self) -> str:
        """What the widget occupies, arrows included - and it occupies the
        same width whether they are drawn or not."""
        return f"{_PREV} {self.name} {_NEXT}" if self.name else ""

    def measure(self, ctx, axis, available):
        """lazydocs: ignore"""
        if axis != "x":
            return SizeRequest(min=1, preferred=1)
        width = ctx.measure_text(self.text(), self.style) if self.name else 0
        return SizeRequest(min=width, preferred=width)

    def draw(self, ctx) -> None:
        """lazydocs: ignore"""
        # The widget's own place and width, remembered for the click test:
        # there is no layout hook to read them from, and a click can only
        # follow a draw.
        self._origin = ctx.screen_rect[0]
        self._width = ctx.size_units[0]
        if not self.name:
            return
        x = 0.0
        prev = _PREV if self.can_prev else " "
        nxt = _NEXT if self.can_next else " "
        for piece, style in ((f"{prev} ", self.style),
                             (self.name, self.name_style),
                             (f" {nxt}", self.style)):
            # Measured on the drawn piece so a blanked arrow still costs its
            # own width, which is what keeps the name from moving.
            ctx.draw_text(x, 0, piece, style)
            x += ctx.measure_text(piece, self.style)

    def handle_event(self, event) -> bool:
        """lazydocs: ignore"""
        if event.type is not EventType.MOUSE_CLICK or self.on_switch is None:
            return False
        if not self.name or event.x is None:
            return False
        # Which half was clicked, not which glyph: the arrows are one
        # character wide and nobody aims that precisely at a chevron.
        delta = -1 if event.x < self._half() else 1
        # A blank half is not a control. Paging would clamp anyway, but a
        # click that visibly does nothing is better than one that silently
        # does nothing somewhere else.
        if (delta < 0 and not self.can_prev) or (delta > 0 and not self.can_next):
            return True
        self.on_switch(delta)
        return True

    def _half(self) -> float:
        return getattr(self, "_width", 0.0) / 2 or len(self.text()) / 2
