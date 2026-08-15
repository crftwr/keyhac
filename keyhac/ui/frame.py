"""A bordered LayoutView shared by the Keyhac windows (console, chooser)."""

from puikit import Style
from puikit.widgets import LayoutView

_BORDER_STYLE = Style(fg=(120, 120, 132))


class Frame(LayoutView):
    """A LayoutView that draws a clear border line around its own extent.
    draw_border() also clips the hosted content to the interior, so children
    can fill up to the line without painting over it."""

    def __init__(self, layout, margin_px: float = 6.0,
                 line_style: Style = _BORDER_STYLE):
        super().__init__(layout, margin_px=margin_px)
        self.line_style = line_style

    def draw(self, ctx) -> None:
        ctx.draw_border(self.line_style)
        super().draw(ctx)
