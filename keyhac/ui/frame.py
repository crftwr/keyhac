"""A bordered LayoutView shared by the Keyhac windows (console, chooser)."""

import math

from puikit import Style
from puikit.widgets import LayoutView

_BORDER_STYLE = Style(fg=(120, 120, 132))

#: The border where the pointer is standing on something it can drag.  Bright
#: enough to read as a state change out of the corner of the eye, since it is
#: the whole affordance: macOS gives the pointer to the key window, and a
#: window that never becomes one cannot shape it at all (issue #117).
_HOT = (210, 210, 225)

#: How thick the lit stretch is, in device pixels.  Thicker than the line it
#: sits on, or the difference is something the eye has to go looking for.
_HOT_PX = 3.0

#: How much of each free end dissolves back into the border, and in how many
#: steps.  A lit stretch that stops dead reads as a separate object lying on
#: the window; one that fades out reads as the border itself being warm.
_FADE_PX = 40.0
_FADE_STEPS = 10


def _blend(under, over, t: float):
    """`over` at strength t against `under`, both opaque.

    Mixed here rather than asked for as an alpha: a fill carrying one is
    composited by the backends that can and flattened by the backends that
    cannot, and this wants the same soft end on both."""
    return tuple(round(u + (o - u) * t) for u, o in zip(under, over))


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
        #: Which of this frame's own edges the pointer is standing on, as
        #: (ex, ey) each -1 / 0 / +1, or None.  Set by whoever owns the
        #: window, since the edge is not a widget and nothing hovers it - and
        #: drawn lit, because the pointer belongs to the key window and a
        #: window that deliberately never becomes one has to say "you can grab
        #: this here" in the only place it owns: the border it draws itself.
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

    # --- the lit edge -------------------------------------------------------

    def _draw_hot_edge(self, ctx) -> None:
        """Light the side(s) the pointer is standing on, the corner included.

        Sides rather than a second outline, because a corner is two of them at
        once and has to read as *the corner*: both its sides light up and the
        curve between them with them, so the eye is told which two directions
        the drag will go in.
        """
        bw, bh = ctx.base_pixel_size
        if not bw or not bh:
            return
        w, h = ctx.size_units
        ex, ey = self.hot_edge
        under = ctx.background or (0, 0, 0)
        rx, ry = self.radius_px / bw, self.radius_px / bh
        tx, ty = _HOT_PX / bw, _HOT_PX / bh
        ix, iy = self.inset_px / bw, self.inset_px / bh

        if ex:
            self._bar(ctx, under, vertical=True,
                      across=ix if ex < 0 else w - ix - tx, thickness=tx,
                      start=ry, end=h - ry,
                      # An end that runs into the lit corner is not an end.
                      fade_start=ey >= 0, fade_end=ey <= 0,
                      fade=_FADE_PX / bh)
        if ey:
            self._bar(ctx, under, vertical=False,
                      across=iy if ey < 0 else h - iy - ty, thickness=ty,
                      start=rx, end=w - rx,
                      fade_start=ex >= 0, fade_end=ex <= 0,
                      fade=_FADE_PX / bw)
        if ex and ey and self.radius_px:
            self._arc(ctx, ex, ey, w, h, ix, iy, rx, ry, tx, ty)

    def _bar(self, ctx, under, *, vertical, across, thickness, start, end,
             fade_start, fade_end, fade) -> None:
        """One lit side: solid in the middle, dissolving at a free end."""
        length = end - start
        if length <= 0:
            return
        fade = max(0.0, min(fade, length / 2))
        head = fade if fade_start else 0.0
        tail = fade if fade_end else 0.0

        def fill(at, extent, colour):
            style = Style(fg=colour, bg=colour)
            if vertical:
                ctx.fill_rect(across, at, thickness, extent, style)
            else:
                ctx.fill_rect(at, across, extent, thickness, style)

        if length - head - tail > 0:
            fill(start + head, length - head - tail, _HOT)
        if not fade:
            return
        step = fade / _FADE_STEPS
        for i in range(_FADE_STEPS):
            # t: 0 at the tip, 1 where the solid stretch takes over.
            colour = _blend(under, _HOT, (i + 0.5) / _FADE_STEPS)
            if fade_start:
                fill(start + i * step, step, colour)
            if fade_end:
                fill(end - (i + 1) * step, step, colour)

    def _arc(self, ctx, ex, ey, w, h, ix, iy, rx, ry, tx, ty) -> None:
        """The curve between two lit sides, walked as overlapping squares.

        There is no arc primitive to ask for and this is not enough reason to
        add one: at three pixels thick against a fifteen point radius the
        samples overlap into a smooth corner, and it degrades to rectangles on
        a backend that has nothing else."""
        cx = ix + rx if ex < 0 else w - ix - rx
        cy = iy + ry if ey < 0 else h - iy - ry
        quarter = math.pi / 2
        base = {(-1, -1): math.pi, (1, -1): 3 * quarter,
                (1, 1): 0.0, (-1, 1): quarter}[(ex, ey)]
        style = Style(fg=_HOT, bg=_HOT)
        steps = max(6, int(self.radius_px))
        for i in range(steps + 1):
            angle = base + quarter * i / steps
            ctx.fill_rect(cx + rx * math.cos(angle) - tx / 2,
                          cy + ry * math.sin(angle) - ty / 2,
                          tx, ty, style)
