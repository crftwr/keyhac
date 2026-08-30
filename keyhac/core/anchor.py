"""Where a popup goes: the caret, the focused control, or the window.

The chooser has centred on the focused window's frame since issue #4, which
is right when the *window* is what you are acting on and wrong when a place
in it is - completing a word at the caret, acting on the control you just
tabbed to. The eye is already somewhere; the window arrives somewhere else.

Two rules live here rather than in either caller, and both for the same
reason: they have to be one rule.

**Whether a caret rectangle can be believed.** Asking is easy on both OSes -
macOS answers `AXBoundsForRange` over `AXSelectedTextRange`, Windows answers
`GetBoundingRectangles` over the TextPattern selection - and the answer is
not always true. Measured in VS Code: the call succeeds and returns
`(0, 1112, 0, 0)` for a text area whose own frame is `(1275, 981, 409, 40)`.
No height, x at the screen edge, y outside the element. A popup placed there
lands in a corner of the screen nobody was looking at, which is worse than
the window centre it replaced, and *nothing in the return value says so* -
the call did not fail. So a reported caret is checked against the element it
is supposed to be inside, and a caret that fails is no caret.

**Where the box goes once there is a rectangle.** Under it, flipped above
when there is no room below, clamped to the screen - what an IME does with
its candidate window, and the caret's own reason for existing: the text you
are typing has to stay visible, so the popup goes where the text is not.

Both rules judge sizes, and a size on screen is not a number until you know
what the platform counts in. macOS answers in points, so a line of text is a
line of text on any display. Windows answers a per-monitor-DPI-aware process
in physical pixels, so a 200% display doubles every number that arrives here:
Chrome's New Tab search field, one line of text, measures 112. Every measured
constant below is therefore written in *logical* units and multiplied by the
scale the element's own display reports - `get_coordinate_scale()`, asked of
the element and 1.0 when it cannot answer, which is every platform whose
coordinates were already logical.
"""

from keyhac.core import log

logger = log.getLogger("Anchor")

#: A caret is a line: no width is ordinary, no height is not. Not scaled -
#: this one says "some" rather than a measured amount.
_MIN_HEIGHT = 1.0

#: How far outside its element a caret may sit and still be believed. Web
#: content reports a line box a little proud of the field's own bounds, and a
#: caret at the very end of a line sits on the boundary rather than inside it.
_SLACK = 8.0

#: Between the anchor and the popup, so the two do not touch.
_GAP_PX = 4.0

#: Tall enough to be a document rather than a field, and therefore not a
#: place. Under a single-line field is within a line of where the caret is;
#: under a code editor is the bottom of the editor, which the caret may be
#: nowhere near. Three lines of ordinary text is the generous end of "field" -
#: a two-line box with padding still fits, and nothing that holds a document
#: comes close.
_MAX_PLACE_HEIGHT = 72.0


def display_scale(element) -> float:
    """How many screen units the element's display packs into a logical one.

    1.0 wherever the platform already answers in logical units - all of
    macOS, and any element that does not offer the question. Windows reports
    physical pixels, so this is the monitor's own scale factor: 2.0 at 200%.

    Args:
        element: the focused element (a platform UIElement), or None.

    Returns:
        A positive float; 1.0 when there is no answer to be had.
    """
    scale = _ask(element, "get_coordinate_scale")
    if isinstance(scale, (int, float)) and not isinstance(scale, bool) and scale > 0:
        return float(scale)
    return 1.0


def usable_caret(caret, element_rect, scale: float = 1.0) -> bool:
    """Whether a reported caret rectangle is worth placing anything against.

    **An element of no size is not a place to check a caret against.**
    Measured in VS Code on Windows: the focused element is Monaco's input
    proxy, and UIA reports its frame as `(0, 0, 0, 0)` - the answer for
    something moved off screen, which that proxy is. The selection over it is
    a *true* caret, `(900, 1116, 1, 32)`, and checking it against a rectangle
    at the origin rejects it for being nowhere near an element that is
    nowhere. An empty frame is the same answer as no frame at all, and both
    mean there is nothing to check against rather than "check against this".

    Args:
        caret: (x, y, w, h) as the platform reported it, or None.
        element_rect: the focused element's own rectangle, or None when it
            could not be read - in which case the caret is taken on trust,
            there being nothing to check it against.
        scale: screen units per logical unit, from `display_scale()`. The
            slack a caret is allowed outside its element is a measured
            amount, so it grows with the display like everything it is
            being compared against.

    Returns:
        True when the rectangle has height and sits within the element.
    """
    if not isinstance(caret, (tuple, list)) or len(caret) != 4:
        return False
    x, y, _w, h = caret
    if h < _MIN_HEIGHT:
        return False
    if not _is_box(element_rect):
        return True
    if _is_the_element_itself(caret, element_rect):
        return False
    slack = _SLACK * scale
    ex, ey, ew, eh = element_rect
    return (ex - slack <= x <= ex + ew + slack
            and ey - slack <= y <= ey + eh + slack)


def _is_the_element_itself(rect, own) -> bool:
    """Whether a reported caret is just the element's own frame.

    **A caret is not the size of the thing it is in.**  Two different roads
    answer this way and they are both a way of saying "nothing":

    - `AXBoundsForRange` in Excel, with no cell being edited. The grid is an
      `AXLayoutArea` of no characters, and every spelling - the character,
      the insertion point, even the caret's line - comes back as
      `(482, 293, 945, 624)`, which is the grid.
    - the marker API for an empty range. VS Code's editor is that case: what
      holds the focus is Monaco's input proxy, which carries no text, so the
      selection covers nothing and the bounds of nothing are the whole
      element.

    Believing either is worse than having no caret at all, because a caret
    bypasses the height limit that keeps a popup from opening under a
    document - which is exactly what those two elements are.

    Within a point: these are screen coordinates that have been through a
    coordinate flip.
    """
    if not isinstance(rect, (tuple, list)) or not isinstance(own, (tuple, list)):
        return False
    return len(rect) == len(own) == 4 and all(
        abs(a - b) < 1.0 for a, b in zip(rect, own))


def caret_anchor(element, scale: float | None = None):
    """The caret alone, believed or not at all.

    For a popup with no second-best place to be. The balloon is that: under
    the caret is where multi-stroke help belongs, and its fall-back is a
    corner of the screen rather than another rectangle - the focused control
    is not a better corner, it is a worse caret.

    Args:
        element: the focused element (a platform UIElement), or None.
        scale: screen units per logical unit, when the caller has already
            asked; None to ask the element itself.

    Returns:
        (x, y, w, h), or None.
    """
    if scale is None:
        scale = display_scale(element)
    caret = _ask(element, "get_caret_rect")
    element_rect = _ask(element, "get_rect")
    if usable_caret(caret, element_rect, scale):
        return _clear_the_field(tuple(caret), element_rect, scale)
    # The one line that says *why* a popup went where it went. Without it a
    # refused caret and an application that has none look identical from the
    # outside - both are simply a popup that did not move - and those two
    # want opposite things done about them.
    logger.debug(f"No caret to place against: reported {caret} for an "
                 f"element at {element_rect}.")
    return None


def _clear_the_field(caret, element_rect, scale: float = 1.0):
    """Extend a caret down to the bottom of the field it is typed in.

    A caret is the text; a field is the text plus its padding and its border,
    and the two do not end in the same place. Measured in Finder's search
    field: the caret is `(924.5, 202, 0, 16)` inside a field at
    `(891, 207, 242, 38)` - it *starts* five points above the field and ends
    twenty-seven points above the field's bottom. A popup placed under that
    caret opens inside the box it was typed into, covering half of it.

    Only for a field. Under a document's bottom edge is nowhere near the
    caret, which is the whole reason a tall element is not a place.

    The x is left alone: the column is what the caret is for, and it is the
    one thing the field cannot say.
    """
    if not _is_place(element_rect, scale):
        return caret
    bottom = max(caret[1] + caret[3], element_rect[1] + element_rect[3])
    return (caret[0], caret[1], caret[2], bottom - caret[1])


def popup_anchor(element, window_rect=None):
    """What to place a popup against, and what it turned out to be.

    The chain issue #118 presumed: the caret, then the focused control, then
    the window. Each falls through when it cannot be read *or cannot be
    believed*, which is the same thing from the caller's side.

    Args:
        element: the focused element (a platform UIElement), or None.
        window_rect: the focused window's frame, used when the element
            offers nothing.

    Returns:
        `(rect, kind)` where kind is "caret", "element" or "window", or None
        when there is nowhere to point at. A "window" anchor is the whole
        frame and means *centre on this*; the other two are small and mean
        *sit under this*.
    """
    scale = display_scale(element)
    caret = caret_anchor(element, scale)
    if caret is not None:
        return caret, "caret"
    element_rect = _ask(element, "get_rect")
    if _is_place(element_rect, scale):
        return tuple(element_rect), "element"
    # The other silent refusal, and the one that sends a balloon to the title
    # bar of a window whose search field was right there: say what was
    # measured and what it was measured against, in the units it arrived in.
    if _is_box(element_rect):
        logger.debug(f"The focused element at {_fmt(element_rect)} is no place "
                     f"to put a popup: {element_rect[3]:.0f} tall against a "
                     f"limit of {_MAX_PLACE_HEIGHT * scale:.0f} "
                     f"({_MAX_PLACE_HEIGHT:.0f} x {scale:g}).")
    if _is_box(window_rect):
        return tuple(window_rect), "window"
    return None


def place_below(size, anchor, screen=None, gap: float = _GAP_PX):
    """Top-left for a box of `size` placed under `anchor`.

    Left edges aligned, because the caret is where the text is going and the
    eye is already at that column - centring on a caret puts half the popup
    where the user just came from.

    Args:
        size: (w, h) of the box being placed.
        anchor: (x, y, w, h) to sit under.
        screen: (x, y, w, h) to stay inside, or None for no clamping.
        gap: distance between the anchor and the box.

    Returns:
        (x, y) for the box.
    """
    w, h = size
    ax, ay, _aw, ah = anchor
    x, y = ax, ay + ah + gap
    # Each step records what it decided and the comparison it decided on.
    # "the popup did not move" is the only symptom this arithmetic has, so
    # the numbers behind it are worth a line of debug rather than a
    # re-derivation by hand from four rectangles.
    steps = [f"under {ay:.0f}+{ah:.0f}+{gap:.0f} -> y={y:.0f}"]
    if not _is_box(screen):
        steps.append("no screen: neither flipped nor clamped")
        _trace("place_below", anchor, size, screen, steps, x, y)
        return x, y
    sx, sy, sw, sh = screen
    bottom, right = sy + sh, sx + sw
    if y + h > bottom:
        # Above instead - but only when it fits there, or a tall popup over a
        # caret near the bottom would be flipped into an equally bad place and
        # then clamped anyway. Clamping alone at least keeps the caret's line
        # at an edge of the popup rather than in the middle of it.
        above = ay - gap - h
        if above >= sy:
            steps.append(f"y+h={y + h:.0f} past bottom {bottom:.0f}"
                         f" -> flipped above to y={above:.0f}")
            y = above
        else:
            steps.append(f"y+h={y + h:.0f} past bottom {bottom:.0f}, but"
                         f" above ({above:.0f}) is off the top {sy:.0f}: not flipped")
    x, y = _clamp((x, y), size, screen, steps)
    _trace("place_below", anchor, size, screen, steps, x, y)
    return x, y


def place_over_top(size, anchor, screen=None, drop: float = 0.0):
    """Top-left for a box centred on the top edge of `anchor`, dropped by
    `drop`.

    Where a popup goes when there is nothing in the window to point at - no
    caret, and no control small enough to be a place. Over the window's own
    top edge, which on both OSes is its title bar: the window being talked
    about is named by the balloon sitting on it, and a title bar is the one
    strip of a window that holds nothing the user is reading.

    Centred, because with nothing to point at there is no side to prefer, and
    the middle of the top edge is the part of a window the eye passes over
    anyway.

    Args:
        size: (w, h) of the box being placed.
        anchor: (x, y, w, h) whose top edge to sit on.
        screen: (x, y, w, h) to stay inside, or None for no clamping.
        drop: how far below that edge to start, so the box hangs *on* the
            title bar rather than from the window's very corner.

    Returns:
        (x, y) for the box.
    """
    w, _h = size
    ax, ay, aw, _ah = anchor
    x, y = ax + (aw - w) / 2, ay + drop
    steps = [f"centred on {ax:.0f}+{aw:.0f}/2 -> x={x:.0f}, "
             f"its top {ay:.0f}+{drop:.0f} -> y={y:.0f}"]
    x, y = _clamp((x, y), size, screen, steps)
    _trace("place_over_top", anchor, size, screen, steps, x, y)
    return x, y


def _clamp(position, size, screen, steps):
    """Keep a box inside a screen, recording it when it had to move."""
    if not _is_box(screen):
        steps.append("no screen to clamp against")
        return position
    x, y = position
    w, h = size
    sx, sy, sw, sh = screen
    moved = (max(sx, min(x, sx + sw - w)), max(sy, min(y, sy + sh - h)))
    if moved != position:
        steps.append(f"clamped from ({x:.0f}, {y:.0f}) into "
                     f"{sx:.0f}..{sx + sw - w:.0f} x {sy:.0f}..{sy + sh - h:.0f}")
    return moved


def _trace(what, anchor, size, screen, steps, x, y) -> None:
    logger.debug(f"{what}: anchor={_fmt(anchor)} size={_fmt(size)} "
                 f"screen={_fmt(screen)} | " + "; ".join(steps)
                 + f" | -> ({x:.0f}, {y:.0f})")


def _fmt(rect) -> str:
    """Rectangles as integers: this is a screen, and a tenth of a pixel in the
    log is a tenth of a pixel nobody can act on."""
    if not isinstance(rect, (tuple, list)):
        return str(rect)
    return "(" + ", ".join(f"{v:.0f}" for v in rect) + ")"


def _ask(element, method):
    """Call an optional element method, treating anything at all as absence.

    Focus elements are duck-typed - an AX element on macOS, a UIA one on
    Windows, whatever a test supplies - so a missing method is an ordinary
    answer here, and so is one that raises: the element may have been
    destroyed between being handed over and being asked.
    """
    if element is None:
        return None
    call = getattr(element, method, None)
    if call is None:
        return None
    try:
        return call()
    except Exception:
        return None


def _is_box(rect) -> bool:
    return (isinstance(rect, (tuple, list)) and len(rect) == 4
            and rect[2] > 0 and rect[3] > 0)


def _is_place(rect, scale: float = 1.0) -> bool:
    """Whether an element is small enough that under it means anything.

    The objection this answers is a real one: a control is not a caret, and
    a popup under a full-window text area is neither where you are looking
    nor out of the way. It is only the *tall* ones that fail that way. Under
    a one-line field is within a line of the caret, which is the whole point,
    and is what Electron applications leave as the best available answer -
    they return CGRectZero for the caret and no amount of asking changes it.

    **Three lines of what, on whose display.** The limit is a count of text
    lines and the rectangle is in the platform's screen units, so the two
    only mean the same thing at 100%. Measured in Chrome's New Tab on a 200%
    display: the search field, one line high, is `(1180, 1086, 1179, 112)` -
    a field by every honest description and half as tall again as a limit
    meant for three lines. Scaled, it is 56 against 72 and a field again.
    """
    return _is_box(rect) and rect[3] <= _MAX_PLACE_HEIGHT * scale
