"""A bordered LayoutView shared by the Keyhac windows (console, chooser)."""

from puikit import Style
from puikit.widgets import LayoutView

_BORDER_STYLE = Style(fg=(120, 120, 132))

#: The border where the pointer is standing on something it can drag.  Bright
#: enough to read as a state change out of the corner of the eye, since it is
#: standing in for the pointer shape macOS will not let a window that is not
#: key ask for (issue #117).
_HOT_STYLE = Style(fg=(210, 210, 225), bg=(210, 210, 225))

#: How thick the lit segment is, in device pixels.  Thicker than the line it
#: sits on: the difference has to be visible without the eye going looking.
_HOT_PX = 3.0


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
        #: Pointer shape over this frame, or None.  Set by whoever owns the
        #: window, because the edge a rounded frame draws is not a widget:
        #: nothing hovers it, so nothing else would ask.  A child asks later
        #: in the frame and wins wherever it covers this one.
        self.cursor = None
        #: Which of this frame's own edges the pointer is standing on, as
        #: (ex, ey) each -1 / 0 / +1, or None.  Set by the same owner, and
        #: drawn lit: macOS gives the pointer to the key window, and a window
        #: that deliberately never becomes key (the chooser is one) cannot
        #: shape it at all until it is clicked - so the affordance has to be
        #: something the window draws for itself.
        self.hot_edge = None

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
        if self.cursor:
            ctx.set_cursor(self.cursor)
        if self.radius_px and ctx.pixel_layout:
            bw, bh = ctx.base_pixel_size
            ix = self.inset_px / bw if bw else 0.0
            iy = self.inset_px / bh if bh else 0.0
            w, h = ctx.size_units
            ctx.round_rect(ix, iy, w - 2 * ix, h - 2 * iy,
                           self.line_style, radius=self.radius_px)
        else:
            ctx.draw_border(self.line_style)
        if self.hot_edge and ctx.pixel_layout:
            # Pixels only, like the rounding: on a character grid a bar three
            # pixels thick is three whole columns of tinted cells across the
            # list, which is not a lit edge - it is a stripe.
            self._draw_hot_edge(ctx)
        super().draw(ctx)

    def _draw_hot_edge(self, ctx) -> None:
        """Light the side(s) the pointer is standing on.

        Drawn as bars rather than as a second outline, because a corner is
        two sides at once and has to read as *the corner* - both of its sides
        light up, and the eye is told which two directions the drag will go
        in. They stop short of the rounded corners, where a straight bar
        would cross the curve it is meant to sit inside.
        """
        bw, bh = ctx.base_pixel_size
        if not bw or not bh:
            return
        w, h = ctx.size_units
        thick_x, thick_y = _HOT_PX / bw, _HOT_PX / bh
        inset_x, inset_y = self.inset_px / bw, self.inset_px / bh
        # The curve eats the last stretch of every side, so the bars end where
        # it begins.
        gap_x, gap_y = self.radius_px / bw, self.radius_px / bh
        ex, ey = self.hot_edge
        if ex:
            x = inset_x if ex < 0 else w - inset_x - thick_x
            ctx.fill_rect(x, gap_y, thick_x, max(0.0, h - 2 * gap_y),
                          _HOT_STYLE)
        if ey:
            y = inset_y if ey < 0 else h - inset_y - thick_y
            ctx.fill_rect(gap_x, y, max(0.0, w - 2 * gap_x), thick_y,
                          _HOT_STYLE)
