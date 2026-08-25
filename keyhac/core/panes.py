"""Finding the panes inside a window, and which one is in a given direction.

The problem this exists for: "move focus to the pane on the left" has to mean
what is on the screen, not what a menu once called a direction.  Translating
the binding into whatever command an application already has for switching
panes is the cheap version and it is wrong - those commands are *logical* (an
editor group index, a named sidebar, a panel assumed to be at the bottom),
while pane layout is something the user rearranges.  A mapping written against
a default layout becomes a lie the first time somebody drags a panel, and the
failure is silent: focus goes somewhere, just not where the arrow pointed.

So the direction is read off the screen, and this module is that reading.  It
is pure - rectangles in, rectangles out - and it is the OS-independent half of
`keyhac.actions.MoveFocus`.

WHAT MEASURING REAL WINDOWS TAUGHT (2026-08-23; VS Code, Finder, System
Settings, Chrome):

- **The walk must be pruned, and that is a correctness requirement rather than
  an optimisation.**  An unpruned descent of a VS Code window is 4116 nodes and
  519 ms, and it runs on the thread that services the keyboard hook - where
  Windows' `LowLevelHooksTimeout` is ~300 ms and exceeding it *silently
  unhooks Keyhac permanently*.  A subtree whose own rectangle is already
  smaller than a pane cannot contain one, so it is not entered: 403 nodes and
  33 ms for the same window.
- **A generic candidate rule works.**  Big rectangle, holds something
  focusable, is not merely a container of other candidates - that alone found
  exactly the three panes a person would name in VS Code (source control,
  editor, panel) with no per-application knowledge at all.
- **Wrapper chains share one rectangle.**  Several nested elements report the
  same frame, so panes are deduplicated by rectangle *with tolerance* - Finder
  reports its file list twice, one pixel apart, and without the tolerance both
  survive as separate panes.
- **Ancestry is the wrong way to ask which pane holds focus.**  Because of
  those chains, "is the focused element a descendant of this pane" depends on
  which link of the chain was kept, and the first implementation lost track of
  its own starting pane.  Geometry answers it, and geometry is what the
  feature is about anyway.
"""

from __future__ import annotations

from typing import Any, Callable

from keyhac.core import log
from keyhac.core.uitree import UINode, get_ui_tree, match_role

logger = log.getLogger("Panes")

#: Smallest fraction of the window a pane may cover.
DEFAULT_MIN_AREA = 0.04

#: Smallest a pane may be on either axis, in points.  Also the prune bound:
#: nothing smaller than this can contain a pane.
DEFAULT_MIN_SIDE = 120.0

#: Largest fraction of the window a pane may cover.  Above this it is the
#: content area or the window itself, not a pane within it.
MAX_AREA = 0.95

#: How far apart two rectangles may be and still be the same one.  Sized from
#: Finder, which reports its file list as an AXOutline and an AXScrollArea one
#: point and two points apart.
RECT_TOLERANCE = 12.0

#: Depth bound for the pane walk.  Deep, because Electron puts its panes far
#: down - VS Code's editor area sits at 23 and its focused element at 26 - and
#: the prune, not the depth, is what actually bounds the cost.
DEFAULT_MAX_DEPTH = 40

#: Node budget, the backstop if a tree defeats the prune.
DEFAULT_MAX_NODES = 3000

#: Bounds for the search *inside* one pane for the element to focus.  A
#: separate walk on purpose: the prune that makes pane discovery cheap drops
#: every subtree smaller than a pane, which is most of what a pane hands the
#: keyboard to - a field is 200x30.  Running it per pane instead of per
#: candidate is what keeps that affordable; only the one or two panes actually
#: tried are ever searched.
TARGET_MAX_DEPTH = 20
TARGET_MAX_NODES = 800

#: Node budget while *deciding* whether a candidate is a pane, as opposed to
#: finding what to focus in one.  Much smaller, because this runs per
#: candidate rather than per destination: a pane announces what it hands the
#: keyboard to near its top, and a candidate whose only focusable element is
#: buried under hundreds of nodes of content is content itself.  Measured on a
#: VS Code window, the full budget spent 318 ms on one chat transcript alone.
QUALIFY_MAX_NODES = 250

#: How much of a candidate its keyboard target must cover for the candidate to
#: be a pane, when the target's role does not already say so.
#:
#: This is the rule that separates a pane from a *piece of content* inside one,
#: and both halves of it are needed - measured on VS Code and Finder,
#: 2026-08-23.  A chat transcript's message blocks and a table's columns are
#: big rectangles that hold something focusable, so without a qualifier they
#: are read as panes, and the real pane around them is then discarded for
#: "containing panes": Finder lost its file list to its own columns.
#:
#: Coverage alone: editor 0.99, source control 1.00, message blocks 0.01 - a
#: clean split, except a terminal's target is a 7x14 hidden input covering
#: 0.00 of it.  Role alone: the terminal and the tree qualify, the blocks do
#: not, but an *empty* editor group offers only an AXGroup.  Either one, and
#: all four real panes qualify while nothing else does.
MIN_TARGET_COVER = 0.5

#: Roles that mean "this is what a pane hands the keyboard to", preferred over
#: whatever else inside the pane will accept focus.  Without this a pane is
#: represented by whichever toolbar button its subtree happens to list first -
#: observed on VS Code, where the source control pane offered "Views and More
#: Actions" ahead of the tree.
PREFERRED_ROLES = ("AXTextArea", "AXWebArea", "AXTextField", "AXOutline",
                   "AXList", "AXTable", "AXBrowser", "AXScrollArea",
                   "Edit", "Document", "Tree", "List", "DataGrid", "Pane")

#: Gaps within this many points of each other count as the same distance, so
#: that a splitter's width is not mistaken for travel.
#:
#: It was 64 and had to shrink twice, both times because it was sized for one
#: scale and used at another. A column of controls is 43 points apart, so
#: several rows shared a bucket and moving up skipped one; entering a list,
#: the candidates sat 8, 10 and 51 points in and all three counted as equally
#: near, which put the keyboard on the one column with a single element in it.
#:
#: What it was originally for - keeping a few points of edge position from
#: outvoting an overlap two and a half times larger - is now done by the
#: reference position, which sorts ahead of it. Measured: every pane-level
#: test passes with this at 1. So it is left only wide enough to absorb a
#: splitter, and the work it used to do is done by something that is right at
#: every scale rather than tuned for one.
GAP_BUCKET = 8.0

#: Roles that are a pane's *furniture* rather than anywhere to put the
#: keyboard.  They take focus perfectly well and are never what a person means
#: by moving into a pane.
#:
#: Without this, System Settings' detail pane resolved to its scroll bar: the
#: pane holds 33 checkboxes and popup buttons, none of them a preferred role,
#: so the largest-focusable fallback picked the one element 647 points tall
#: (2026-08-24).  Landing there does nothing a keystroke can follow up on.
CHROME_ROLES = ("AXScrollBar", "AXSplitter", "AXValueIndicator", "AXGrowArea",
                "ScrollBar", "Thumb", "Separator")

DIRECTIONS = ("left", "right", "up", "down")

def _accepts_focus(node: UINode) -> bool:
    """Whether the application says this element can take focus.

    The guard against the failure that looks like success: both platforms
    accept a focus request on an element that will never take it and report
    no error.  Finder's sidebar and System Settings' detail pane do exactly
    that, and both answer False here.
    """
    ask = getattr(node.element, "accepts_focus", None)
    if ask is None:
        return False
    try:
        return bool(ask())
    except Exception:                       # noqa: BLE001 - a dead element
        return False


def same_rect(a, b, tolerance: float = RECT_TOLERANCE) -> bool:
    """Whether two rectangles are the same one, allowing for wrapper drift."""
    if a is None or b is None:
        return False
    return all(abs(a[i] - b[i]) <= tolerance for i in range(4))


def contains_rect(outer, inner, tolerance: float = RECT_TOLERANCE) -> bool:
    """Whether `outer` strictly contains `inner` - same rectangle is not."""
    if outer is None or inner is None or same_rect(outer, inner, tolerance):
        return False
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return (ox - tolerance <= ix and oy - tolerance <= iy
            and ox + ow + tolerance >= ix + iw
            and oy + oh + tolerance >= iy + ih)


def centre_of(rect):
    """The middle of a rectangle."""
    return (rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0)


def clamp_point(point, rect):
    """`point` moved the shortest distance needed to lie inside `rect`.

    Seeding the reference position needs this: a scrolling pane reports the
    height of its contents, so the focused element's centre can be well
    outside the pane and even off the screen.  Microsoft To Do's list of lists
    ran 47 points past the bottom of its own window.
    """
    x, y = point
    return (min(max(x, rect[0]), rect[0] + rect[2]),
            min(max(y, rect[1]), rect[1] + rect[3]))


def focus_target(pane: UINode, max_depth: int = TARGET_MAX_DEPTH,
                 max_nodes: int = TARGET_MAX_NODES,
                 cached: bool = True) -> UINode | None:
    """The element inside `pane` that should receive the keyboard.

    A pane container is usually not focusable itself - in Chromium and
    Electron it is a plain div with no tabindex, and reports as much - so what
    the direction actually resolves to is a control within it.

    Reads the pane's subtree live rather than reusing the walk `find_panes`
    did: that one prunes away everything smaller than a pane, which is most of
    what could take focus.  Main-thread work, like every live read here.

    **Breadth first**, which is not an implementation detail: a pane announces
    what it hands the keyboard to near its own top, while its *content* goes
    deep.  Depth first walked a chat transcript's several hundred message
    nodes before reaching the input underneath it, exhausted its budget, and
    settled for a container that would not take focus at all - 318 ms to
    return the wrong answer.  Breadth first reaches the same input in a few
    dozen nodes.

    Preferred roles win immediately; otherwise the largest element that will
    take focus does.  Returns None when nothing inside the pane will take
    focus at all - which is how Finder's sidebar and System Settings' detail
    pane disqualify themselves as destinations.
    """
    from collections import deque
    from keyhac.core.uitree import _children, _describe, _identity

    if cached:
        remembered = getattr(pane, "_focus_target", False)
        if remembered is not False:
            return remembered

    root = pane.element
    if root is None:
        return None
    queue = deque([(root, 0)])
    seen = set()
    best = None
    best_area = -1.0
    budget = max_nodes
    while queue and budget > 0:
        element, depth = queue.popleft()
        identity = _identity(element)
        if identity is not None:
            if identity in seen:
                continue
            seen.add(identity)
        budget -= 1
        node = UINode(depth=depth, element=element, **_describe(element))
        if _accepts_focus(node) and not (
                node.role and match_role(node.role, "|".join(CHROME_ROLES))):
            if node.role and match_role(node.role, "|".join(PREFERRED_ROLES)):
                return node
            rect = node.rect
            area = (rect[2] * rect[3]) if rect else 0.0
            if area > best_area:
                best, best_area = node, area
        if depth < max_depth:
            for child in _children(element):
                queue.append((child, depth + 1))
    return best


def is_pane(candidate: UINode, min_cover: float = MIN_TARGET_COVER,
            max_nodes: int = QUALIFY_MAX_NODES) -> bool:
    """Whether a candidate rectangle is a pane rather than content inside one.

    True on any of three counts, each of which one of the others misses:

    - the element the keyboard would go to says what it is by its role - a
      tree, a document, a terminal's input;
    - that element is large enough to *be* the candidate (MIN_TARGET_COVER
      carries what each of these first two is for);
    - or the candidate's **own** role says it is a region rather than a piece
      of content.  System Settings' detail pane needed this: it is an
      AXScrollArea holding 33 checkboxes and popup buttons, so no single
      target names it and none covers a fortieth of it, and the pane was
      dropped entirely while its sidebar survived.  A scroll area is a
      viewport onto content, which is what a pane is.

    Something focusable must still be found inside either way, which is what
    keeps this from readmitting the panes nothing can reach - Finder's sidebar
    is also an AXScrollArea, and its only focusable element is its scroll bar.
    """
    target = focus_target(candidate, max_nodes=max_nodes)
    # Kept on the node so the destination is not searched for twice.  Deciding
    # a candidate is a pane and deciding what to focus in it are the same
    # search, and the second one cost 398 ms on a pane whose target then
    # refused focus - paid on the thread that services the keyboard hook.
    # Private and non-dataclass, like UINode._parent.
    candidate._focus_target = target
    if target is None:
        return False
    if target.role and match_role(target.role, "|".join(PREFERRED_ROLES)):
        return True
    if not target.rect or not candidate.rect:
        return bool(candidate.role
                    and match_role(candidate.role, "|".join(PREFERRED_ROLES)))
    area = candidate.rect[2] * candidate.rect[3]
    if area > 0 and (target.rect[2] * target.rect[3]) / area >= min_cover:
        return True
    return bool(candidate.role
                and match_role(candidate.role, "|".join(PREFERRED_ROLES)))


def find_panes(window: UINode,
               roles: str | None = None,
               min_area: float = DEFAULT_MIN_AREA,
               min_side: float = DEFAULT_MIN_SIDE,
               max_depth: int = DEFAULT_MAX_DEPTH,
               max_nodes: int = DEFAULT_MAX_NODES) -> list[UINode]:
    """The panes inside a window, largest first.

    Reads the live tree, so this is main-thread work; `MoveFocus` dispatches it.

    Args:
        window: The window, as a node.
        roles: Role pattern candidates must match - the per-application recipe,
            which declares *what counts as a pane* rather than which key to
            send.  A role set survives every rearrangement that breaks a
            command mapping, which is the whole reason the recipe takes this
            shape.  None accepts any role.
        min_area: Smallest fraction of the window a pane may cover.
        min_side: Smallest a pane may be on either axis.  Doubles as the prune
            bound - see the module docstring on why that matters.
        max_depth: Depth bound for the walk.
        max_nodes: Node budget for the walk.

    Returns:
        Panes in descending area order, each one a rectangle that is not
        merely a container of other panes.

    Whether a pane can actually be *reached* is not decided here - that is
    `focus_target()`, asked only of the panes a direction actually offers, so
    that the expensive question is asked once or twice per keypress rather
    than once per candidate.
    """
    wrect = window.rect
    if not wrect or wrect[2] <= 0 or wrect[3] <= 0:
        return []
    warea = wrect[2] * wrect[3]

    def prune(node: UINode) -> bool:
        if node.depth == 0:
            return False
        rect = node.rect
        return bool(rect) and (rect[2] < min_side or rect[3] < min_side)

    tree = get_ui_tree(window, max_depth=max_depth, max_nodes=max_nodes,
                       prune=prune)

    sized = []
    for node in tree.walk():
        rect = node.rect
        if not rect or rect[2] < min_side or rect[3] < min_side:
            continue
        fraction = (rect[2] * rect[3]) / warea
        if fraction < min_area or fraction > MAX_AREA:
            continue
        if roles is not None and not (node.role and match_role(node.role, roles)):
            continue
        sized.append(node)

    # One pane per rectangle, keeping the outermost of each wrapper chain: it
    # contains everything the inner links do, so the focusable-descendant test
    # below cannot be lost to whichever link happened to be kept.
    sized.sort(key=lambda n: n.depth)
    unique: list[UINode] = []
    for node in sized:
        if not any(same_rect(node.rect, kept.rect) for kept in unique):
            unique.append(node)

    # Innermost first, and a candidate is a pane only if nothing it contains
    # already is.  The order is what makes this affordable *and* correct:
    #
    # - Correct, because "a container of panes is not a pane" cannot be
    #   decided before knowing what is a pane.  Applying it first cost Finder
    #   its file list, discarded for containing its own table columns.
    # - Affordable, because qualifying is the expensive step and a container
    #   whose contents qualified is never qualified at all.  Deciding the
    #   containers too cost 1403 ms on a VS Code window; this way the nested
    #   wrapper chains and the window-sized groups are answered by geometry.
    unique.sort(key=lambda n: n.rect[2] * n.rect[3])
    panes: list[UINode] = []
    for candidate in unique:
        if any(contains_rect(candidate.rect, kept.rect) for kept in panes):
            continue                      # it contains a pane, so it is not one
        if is_pane(candidate):
            panes.append(candidate)
    panes.sort(key=lambda n: -(n.rect[2] * n.rect[3]))
    return panes


def pane_holding(panes: list[UINode], rect) -> UINode | None:
    """The pane `rect` mostly lies in, or None if it lies in none of them.

    Asked of the focused element's rectangle, this is "which pane has the
    keyboard".  Geometry rather than ancestry, for the reason in the module
    docstring.

    **Overlap, not containment.**  Containment looks like the obvious test and
    fails on any scrolling pane: a list reports the height of its *contents*,
    not of the viewport showing them, so the focused element is routinely
    larger than the pane it is inside and sometimes larger than the window.
    Microsoft To Do's list of lists was measured at 637 points tall inside a
    548-point scroll area, running 47 points past the bottom of the window
    itself - structurally inside its pane, geometrically not, and the arrow
    keys did nothing at all as a result.

    Largest intersection is also what `MoveWindow._get_best_screen` uses to
    decide which screen a window is on, which is the same question asked of
    bigger rectangles.  Ties go to the smaller pane.
    """
    if not rect:
        return None
    x, y, w, h = rect
    best, best_area = None, 0.0
    for pane in panes:
        if not pane.rect:
            continue
        px, py, pw, ph = pane.rect
        overlap = (max(0.0, min(x + w, px + pw) - max(x, px))
                   * max(0.0, min(y + h, py + ph) - max(y, py)))
        if overlap <= 0:
            continue
        if overlap > best_area or (overlap == best_area and best is not None
                                   and pw * ph < best.rect[2] * best.rect[3]):
            best, best_area = pane, overlap
    return best


def panes_towards(panes: list[UINode], origin, direction: str,
                  reference=None,
                  tolerance: float = RECT_TOLERANCE) -> list[UINode]:
    """Panes lying in `direction` from `origin`, nearest first.

    A list rather than a single answer, because the nearest pane is not always
    a place focus can go: Finder's sidebar is exactly where "left" points from
    the file list and will not take focus at all.  The caller walks the list
    until one accepts, which is what makes the binding mean "the next thing
    that way" rather than "the next thing that way, unless it is unreachable,
    in which case nothing".

    Args:
        panes: Every pane in the window.
        origin: The rectangle being moved away from.
        direction: "left", "right", "up" or "down".
        reference: The hidden reference position (x, y) - see below.  None
            uses the middle of `origin`, which is what a first move does.
        tolerance: Slack for edge comparisons.

    ORDERED BY, in order: whether the pane's perpendicular extent covers the
    reference position, then the gap in the direction of travel *bucketed*
    (see GAP_BUCKET), then how far off the reference it is, then how much it
    overlaps `origin`, and finally the unbucketed gap.

    That last key is what makes the ordering work at more than one scale. The
    bucket is sized for panes, where a splitter is a few points and the next
    pane along is a hundred; a *column of controls* is 43 points apart, so
    several rows share a bucket and identically-wide buttons overlap the origin
    identically too - every key ties, and the answer falls out of enumeration
    order. Moving up from the third row of System Settings' accessibility list
    went to the first. Nearest-wins settles it, and only where overlap has
    already tied, so nothing coarser is affected.  The overlap is the measure `MoveWindow` uses to choose
    an adjacent screen, which is the same problem with screen rectangles
    swapped for element ones; the bucket keeps a few points of edge position
    from outvoting an overlap two and a half times larger.

    THE REFERENCE POSITION IS WHAT MAKES MOVEMENT REVERSIBLE, and that is
    worth more than every other consideration here.  Moving left and then
    right did not come back: on the six-pane layout in the tests, five of the
    round trips landed somewhere other than where they started, because each
    step chose against the *pane it was leaving* and a pane is wide enough to
    lead to different answers from either end of it.  Steering by a position
    that only the moving axis changes - the two-dimensional form of a text
    editor's goal column - makes the return journey ask the same question that
    led away, and all five come back.

    What it does not do is make the first move less arbitrary.  Two panes
    stacked beside one tall pane is genuinely ambiguous: the reference lands
    in the splitter between them, and by overlap it is 315 against 314, by
    centre distance 173.5 against 173.0.  No geometry resolves that.  It stops
    mattering once the wrong guess is undoable.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, not {direction!r}")
    if not origin:
        return []
    ox, oy, ow, oh = origin
    rx, ry = reference if reference is not None else centre_of(origin)
    out = []
    for pane in panes:
        rect = pane.rect
        if not rect or same_rect(rect, origin, tolerance):
            continue
        px, py, pw, ph = rect
        if direction == "left":
            gap, overlap = ox - (px + pw), min(oy + oh, py + ph) - max(oy, py)
            low, high, along = py, py + ph, ry
        elif direction == "right":
            gap, overlap = px - (ox + ow), min(oy + oh, py + ph) - max(oy, py)
            low, high, along = py, py + ph, ry
        elif direction == "up":
            gap, overlap = oy - (py + ph), min(ox + ow, px + pw) - max(ox, px)
            low, high, along = px, px + pw, rx
        else:
            gap, overlap = py - (oy + oh), min(ox + ow, px + pw) - max(ox, px)
            low, high, along = px, px + pw, rx
        # A pane merely nested inside the origin is not "that way".
        if gap < -tolerance or overlap <= 0:
            continue
        covers = low - tolerance <= along <= high + tolerance
        off = 0.0 if covers else min(abs(along - low), abs(along - high))
        out.append((0 if covers else 1, int(max(gap, 0.0) // GAP_BUCKET),
                    off, -overlap, max(gap, 0.0), pane))
    out.sort(key=lambda item: item[:5])
    return [item[-1] for item in out]
