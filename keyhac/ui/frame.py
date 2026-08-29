"""A bordered LayoutView shared by the Keyhac windows (console, chooser)."""

from puikit import Style
from puikit.widgets import LayoutView

_BORDER_STYLE = Style(fg=(120, 120, 132))

#: The border while the pointer is standing somewhere the window can be
#: grabbed.  The theme's accent when there is a theme to ask, and this when
#: there is not - it is the same blue puikit's own focus ring uses, since
#: this is the same statement: what the pointer is on is live.
_HOT_FALLBACK = (0, 122, 204)

#: How thick that border is drawn while it is hot, in device pixels, against
#: the one pixel it is drawn at otherwise.  It costs the content nothing: the
#: stroke is inset by half its width, so it grows *inward* from the same outer
#: edge, and four pixels is still inside the page margin the content starts
#: after - the layout does not move, and neither does anything under it.
_HOT_PX = 4.0


class Frame(LayoutView):
    """A LayoutView that draws a clear border line around its own extent.
    draw_border() also clips the hosted content to the interior, so children
    can fill up to the line without painting over it.

    `radius_px` rounds that line, for the frame that is a *window's* own edge:
    a macOS window is clipped to a rounded rectangle - 15 pt, measured off
    `NSThemeFrame`, and Windows 11 rounds a popup too - so a square border
    drawn at the window's extent loses its four corners to the clip. Rounding
    it to the same shape, `inset_px` inside, keeps the line whole and
    concentric with the corner it sits in. The rounded line does not clip its
    content, so a frame using it wants a margin wider than the inset.

    Both are ignored on a character grid, which has square corners and a
    one-cell stroke: there is nothing there to round or to inset into."""

    def __init__(self, layout, margin_px: float = 6.0,
                 line_style: Style = _BORDER_STYLE,
                 radius_px: float = 0.0, inset_px: float = 0.0):
        super().__init__(layout, margin_px=margin_px)
        self.line_style = line_style
        self.radius_px = radius_px
        self.inset_px = inset_px
        #: Whether the pointer is standing somewhere the window can be
        #: grabbed.  Set by whoever owns the window, since the edge is not a
        #: widget and nothing hovers it, and drawn as the whole border in the
        #: accent colour: the pointer belongs to the key window, and a window
        #: that deliberately never becomes one cannot shape it at all, so the
        #: only place it can say "this is live" is the border it draws itself.
        #: The whole border rather than the side under the pointer, because
        #: which side is already where the pointer is - the thing worth saying
        #: is that the window is grabbable at all.
        self.hot = False

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

    def line(self, ctx) -> tuple:
        """The border's style and stroke width now: the accent, drawn thicker,
        while the pointer is somewhere the window can be grabbed - its own
        quiet line at one pixel otherwise.

        lazydocs: ignore
        """
        if not self.hot:
            return self.line_style, None
        accent = getattr(ctx.theme, "accent", None) if ctx.theme else None
        return Style(fg=accent or _HOT_FALLBACK), {"line_width": _HOT_PX}

    def draw(self, ctx) -> None:
        self._reserve_stroke(ctx.layout_context())
        line, hints = self.line(ctx)
        if self.radius_px and ctx.pixel_layout:
            bw, bh = ctx.base_pixel_size
            ix = self.inset_px / bw if bw else 0.0
            iy = self.inset_px / bh if bh else 0.0
            w, h = ctx.size_units
            ctx.round_rect(ix, iy, w - 2 * ix, h - 2 * iy,
                           line, radius=self.radius_px, hints=hints)
        else:
            # A character grid's stroke is one cell and has no other width to
            # be drawn at; the colour is the whole of what changes there.
            ctx.draw_border(line)
        super().draw(ctx)
