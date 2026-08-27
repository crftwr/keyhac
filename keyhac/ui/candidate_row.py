"""One row of the candidate list: label on the left, source on the right.

Built as a `row_factory` widget rather than as a PuiKit addition, deliberately.
A trailing badge is a reasonable thing for a list widget to offer, but this
particular badge - "which of the merged sources did this row come from" -
belongs to the unified candidate window and not to lists in general, and
building it here is also the honest test of whether `ListView`'s
`row_factory` is flexible enough to be used this way.

What the toolkit does *not* hand over with it is the text abbreviation
`ListView` applies to plain string rows, so this does its own: the two texts
are elided independently, and the label yields the width the badge needs
rather than the other way round - a clipboard entry is long and a source name
is short, so clipping the short one to fit the long one would lose the label
that makes the row legible.
"""

from puikit import DEFAULT_STYLE, Style
from puikit.text import elide
from puikit.widgets.base import Widget

#: Blank columns kept between the label and its badge, so a full-width label
#: does not run into it.
_GAP = 2

#: The badge is quieter than the row it annotates: it is context, not content.
_BADGE_STYLE = Style(fg=(130, 130, 140))


class CandidateRow(Widget):
    """`label` on the left, `badge` (may be empty) right-aligned."""

    def __init__(self, label: str, badge: str = "",
                 style: Style = DEFAULT_STYLE,
                 badge_style: Style = _BADGE_STYLE):
        self.label = label
        self.badge = badge
        self.style = style
        self.badge_style = badge_style

    def draw(self, ctx) -> None:
        """lazydocs: ignore"""
        width = ctx.size_units[0]
        measure = lambda t: ctx.measure_text(t, self.style)
        badge_w = 0.0
        badge = ""
        if self.badge:
            # The badge gets at most a third of the row; past that it is the
            # label that is being annotated, not the other way round.
            badge = elide(self.badge, int(max(0, width / 3)), "…",
                          where="end", measure=measure)
            badge_w = measure(badge) if badge else 0.0
        label_w = max(0, width - badge_w - (_GAP if badge else 0))
        ctx.draw_text(0, 0, elide(self.label, int(label_w), "…",
                                  where="end", measure=measure), self.style)
        if badge:
            # A selected row's own foreground wins: the muted badge colour
            # would disappear into the accent fill.
            style = self.style if ctx.focused else self.badge_style
            ctx.draw_text(width - badge_w, 0, badge, style)
