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

    def _reserve_stroke(self, lctx) -> None:
        # On snap backends the pixel margin collapses to zero while the border
        # stroke still consumes a whole base unit per edge, so the hosted
        # layout must be inset by that unit itself — otherwise its first and
        # last rows land under the border's content clip and silently vanish
        # (a ListView scrolled its selection into a row that was never shown).
        # On pixel backends the stroke is one device pixel, already inside the
        # pixel margin.
        if lctx.snap:
            self.margin_units = max(self.margin_units, 1.0)

    def measure(self, ctx, axis, available):
        self._reserve_stroke(ctx)
        return super().measure(ctx, axis, available)

    def draw(self, ctx) -> None:
        self._reserve_stroke(ctx.layout_context())
        ctx.draw_border(self.line_style)
        super().draw(ctx)
