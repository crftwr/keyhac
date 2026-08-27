"""The scope indicator at the right of the filter field: `‹ Name ›`.

The arrows carry two jobs. A key-driven switch (Tab / Shift-Tab) has no
visible affordance of its own, so they are what says one exists at all - the
discoverability cost of preferring a key over a typed prefix, paid back. And
they are clickable, for the moment the pointer is already in hand: the
chooser reaches its window through `overlay_input="mouse"` on macOS and
`WS_EX_NOACTIVATE` on Windows, so a click arrives without the application
underneath losing anything.

Deliberately **not focusable**. A click here must not take the focus off the
filter field - the point of the whole two-pane arrangement is that the field
is where typing goes, and nothing about switching scope changes that.
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


class ScopeSwitcher(Widget):
    """`‹ Name ›`, where clicking an arrow moves along the cycle."""

    def __init__(self, name: str = "", on_switch=None,
                 style: Style = DEFAULT_STYLE,
                 name_style: Style = _NAME_STYLE):
        self.name = name
        self.on_switch = on_switch          # called with -1 or +1
        self.style = style
        self.name_style = name_style
        self._width = 0.0

    def text(self) -> str:
        """What the widget occupies, arrows included."""
        return f"{_PREV} {self.name} {_NEXT}" if self.name else ""

    def measure(self, ctx, axis, available):
        """lazydocs: ignore"""
        if axis != "x":
            return SizeRequest(min=1, preferred=1)
        width = ctx.measure_text(self.text(), self.style) if self.name else 0
        return SizeRequest(min=width, preferred=width)

    def draw(self, ctx) -> None:
        """lazydocs: ignore"""
        # The widget's own width, remembered for the click test: there is no
        # layout hook to read it from, and a click can only follow a draw.
        self._width = ctx.size_units[0]
        if not self.name:
            return
        x = 0.0
        for piece, style in ((f"{_PREV} ", self.style),
                             (self.name, self.name_style),
                             (f" {_NEXT}", self.style)):
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
        self.on_switch(-1 if event.x < self._half() else 1)
        return True

    def _half(self) -> float:
        return getattr(self, "_width", 0.0) / 2 or len(self.text()) / 2
