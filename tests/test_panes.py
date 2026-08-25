"""Pane finding and direction (keyhac/core/panes.py).

Hermetic: the fake element implements the same small protocol the real ones
do, so the geometry is tested without a screen.  The layouts below are the
ones that were actually measured - a VS Code-shaped three-pane window, and
Finder's file list reported twice one pixel apart.
"""

import pytest

from keyhac.core import panes
from keyhac.core.panes import (
    centre_of, clamp_point, contains_rect, find_panes, focus_target, is_pane,
    pane_holding, panes_towards, same_rect,
)
from keyhac.core.uitree import UINode


class FakeElement:
    def __init__(self, role, rect, children=(), focusable=False,
                 identifier=None, name=None):
        self.role = role
        self.rect = rect
        self._children = list(children)
        self._focusable = focusable
        self.identifier = identifier
        self.name = name
        self.child_reads = 0

    def describe(self):
        return {"role": self.role, "name": self.name, "value": None,
                "identifier": self.identifier, "rect": self.rect}

    def children(self):
        self.child_reads += 1
        return list(self._children)

    def identity_key(self):
        return id(self)

    def accepts_focus(self):
        return self._focusable


def node_for(element):
    return UINode(element=element, **element.describe())


def leaf(role="AXTextArea", rect=(0, 0, 200, 60), focusable=True, **kw):
    """A control, deliberately smaller than a pane on one axis."""
    return FakeElement(role, rect, focusable=focusable, **kw)


def window_of(*children, rect=(0, 0, 1000, 1000)):
    return FakeElement("AXWindow", rect, children=children)


# -- the rectangle predicates ------------------------------------------------

def test_same_rect_tolerates_wrapper_drift():
    assert same_rect((10, 10, 100, 100), (11, 11, 99, 98))
    assert not same_rect((10, 10, 100, 100), (10, 10, 100, 200))


def test_contains_rect_is_strict():
    assert contains_rect((0, 0, 500, 500), (10, 10, 100, 100))
    assert not contains_rect((0, 0, 500, 500), (1, 1, 499, 499))   # same rect
    assert not contains_rect((0, 0, 100, 100), (200, 200, 50, 50))


# -- find_panes --------------------------------------------------------------

def test_finds_the_panes_a_person_would_name():
    """The VS Code shape: sidebar, editor, panel."""
    sidebar = FakeElement("AXGroup", (0, 0, 250, 1000), [leaf(rect=(0, 0, 250, 60))])
    editor = FakeElement("AXGroup", (250, 0, 450, 1000), [leaf(rect=(250, 0, 450, 60))])
    panel = FakeElement("AXGroup", (700, 0, 300, 1000), [leaf(rect=(700, 0, 300, 60))])
    found = find_panes(node_for(window_of(sidebar, editor, panel)))
    assert [p.rect[0] for p in found] == [250, 700, 0]      # largest area first
    assert len(found) == 3


def test_a_wrapper_chain_is_one_pane():
    """Finder reports its file list as an AXOutline and an AXScrollArea one
    point apart; without the tolerance both survive."""
    inner = FakeElement("AXOutline", (301, 1, 398, 998), [leaf(rect=(301, 1, 398, 60))],
                        focusable=True)
    outer = FakeElement("AXScrollArea", (300, 0, 400, 1000), [inner])
    other = FakeElement("AXGroup", (0, 0, 250, 1000), [leaf(rect=(0, 0, 250, 60))])
    found = find_panes(node_for(window_of(outer, other)))
    assert len(found) == 2
    assert found[0].role == "AXScrollArea"                  # the outermost kept


def test_a_container_of_panes_is_not_a_pane():
    left = FakeElement("AXGroup", (0, 0, 300, 900), [leaf(rect=(0, 0, 300, 60))])
    right = FakeElement("AXGroup", (300, 0, 300, 900), [leaf(rect=(300, 0, 300, 60))])
    both = FakeElement("AXGroup", (0, 0, 600, 900), [left, right])
    found = find_panes(node_for(window_of(both)))
    assert [p.rect for p in found] == [(0, 0, 300, 900), (300, 0, 300, 900)]


def test_a_pane_nothing_can_focus_has_no_target():
    """Finder's sidebar: big, present, and unreachable by the keyboard.

    A pane with nothing focusable in it is not a destination, so it is not a
    pane: `is_pane` asks for the target and there is none.
    """
    reachable = FakeElement("AXGroup", (0, 0, 300, 900), [leaf(rect=(0, 0, 300, 60))])
    dead = FakeElement("AXGroup", (300, 0, 300, 900),
                       [leaf(rect=(300, 0, 300, 60), focusable=False)])
    found = find_panes(node_for(window_of(reachable, dead)))
    assert [p.rect for p in found] == [(0, 0, 300, 900)]
    assert focus_target(node_for(dead)) is None


def test_the_walk_does_not_enter_a_subtree_too_small_to_hold_a_pane():
    """The prune is a correctness requirement, not an optimisation: an
    unpruned VS Code window is 519 ms on the thread that services the
    keyboard hook, where Windows unhooks silently past ~300 ms."""
    buried = leaf(rect=(0, 0, 300, 900))
    tiny = FakeElement("AXGroup", (0, 0, 20, 20), [buried])
    pane_a = FakeElement("AXGroup", (0, 0, 300, 900), [leaf(rect=(0, 0, 300, 60))])
    pane_b = FakeElement("AXGroup", (300, 0, 300, 900), [leaf(rect=(300, 0, 300, 60))])
    find_panes(node_for(window_of(tiny, pane_a, pane_b)))
    assert tiny.child_reads == 0


def test_roles_narrows_the_candidates():
    """The recipe declares what counts as a pane, never which key to send."""
    group = FakeElement("AXGroup", (0, 0, 300, 900), [leaf(rect=(0, 0, 300, 60))])
    scroll = FakeElement("AXScrollArea", (300, 0, 300, 900), [leaf(rect=(300, 0, 300, 60))])
    window = node_for(window_of(group, scroll))
    assert len(find_panes(window)) == 2
    assert [p.role for p in find_panes(window, roles="AXScrollArea")] == ["AXScrollArea"]


def test_a_window_without_a_rectangle_has_no_panes():
    assert find_panes(UINode(element=FakeElement("AXWindow", None))) == []


# -- focus_target ------------------------------------------------------------

def test_focus_target_prefers_a_role_over_a_bigger_element():
    """VS Code offered "Views and More Actions" ahead of the tree."""
    button = leaf(role="AXButton", rect=(0, 0, 300, 800))
    tree = leaf(role="AXOutline", rect=(0, 0, 100, 100))
    pane = FakeElement("AXGroup", (0, 0, 300, 900), [button, tree])
    assert focus_target(node_for(pane)).role == "AXOutline"


def test_focus_target_falls_back_to_the_largest_focusable():
    small = leaf(role="AXButton", rect=(0, 0, 50, 50))
    big = leaf(role="AXButton", rect=(0, 0, 300, 800))   # neither is preferred
    pane = FakeElement("AXGroup", (0, 0, 300, 900), [small, big])
    assert focus_target(node_for(pane)).rect == (0, 0, 300, 800)


def test_a_scroll_bar_is_not_somewhere_to_put_the_keyboard():
    """System Settings' detail pane resolved to its scroll bar: 33 controls
    inside, none of them a preferred role, and the largest-focusable fallback
    picked the one element 647 points tall."""
    bar = leaf(role="AXScrollBar", rect=(280, 0, 19, 640))
    control = leaf(role="AXCheckBox", rect=(0, 100, 36, 16))
    pane = FakeElement("AXScrollArea", (0, 0, 300, 900), [bar, control])
    assert focus_target(node_for(pane)).role == "AXCheckBox"


def test_a_pane_of_small_controls_qualifies_by_its_own_role():
    """The System Settings shape: no single target names the pane and none
    covers a fortieth of it, but a scroll area is a viewport onto content,
    which is what a pane is."""
    controls = [leaf(role="AXCheckBox", rect=(0, 40 * i, 36, 16)) for i in range(8)]
    pane = FakeElement("AXScrollArea", (0, 0, 300, 900), controls)
    assert is_pane(node_for(pane)) is True


def test_a_content_block_of_the_same_size_still_does_not():
    """A chat transcript's message block: one small button, and an AXGroup."""
    block = FakeElement("AXGroup", (0, 0, 300, 900),
                        [leaf(role="AXButton", rect=(0, 0, 24, 24))])
    assert is_pane(node_for(block)) is False


def test_a_region_reachable_only_through_its_scroll_bar_is_not_a_pane():
    """Finder's sidebar is an AXScrollArea too - the own-role arm must not
    readmit the panes nothing can actually reach."""
    sidebar = FakeElement("AXScrollArea", (0, 0, 300, 900),
                          [leaf(role="AXScrollBar", rect=(280, 0, 19, 890))])
    assert focus_target(node_for(sidebar)) is None
    assert is_pane(node_for(sidebar)) is False


# -- pane_holding ------------------------------------------------------------

def test_pane_holding_picks_the_smallest_container():
    panes_list = [UINode(rect=(0, 0, 1000, 1000)), UINode(rect=(0, 0, 300, 900))]
    assert pane_holding(panes_list, (10, 10, 50, 50)).rect == (0, 0, 300, 900)


def test_pane_holding_survives_a_pane_that_scrolls():
    """Measured in Microsoft To Do: the focused list of lists is 637 points
    tall inside a 548-point scroll area, running past the bottom of the
    window.  A containment test called that "focus is not inside any pane"
    and the arrow keys did nothing."""
    panes_list = [UINode(rect=(1406, 337, 260, 704), name="detail"),
                  UINode(rect=(1021, 417, 375, 361), name="tasks"),
                  UINode(rect=(836, 451, 176, 548), name="sidebar")]
    assert pane_holding(panes_list, (836.0, 451.0, 176.5, 637.0)).name == "sidebar"


def test_pane_holding_prefers_the_pane_it_overlaps_most():
    panes_list = [UINode(rect=(0, 0, 300, 900), name="left"),
                  UINode(rect=(300, 0, 300, 900), name="right")]
    # Straddling the splitter, mostly on the right.
    assert pane_holding(panes_list, (250, 100, 200, 100)).name == "right"


def test_pane_holding_returns_none_outside_every_pane():
    assert pane_holding([UINode(rect=(0, 0, 100, 100))], (500, 500, 10, 10)) is None
    assert pane_holding([UINode(rect=(0, 0, 100, 100))], None) is None


# -- panes_towards -----------------------------------------------------------

@pytest.fixture
def row():
    """Three panes side by side: A | B | C."""
    return [UINode(rect=(0, 0, 300, 900), name="A"),
            UINode(rect=(300, 0, 300, 900), name="B"),
            UINode(rect=(600, 0, 300, 900), name="C")]


def test_nearest_first_in_the_direction_of_travel(row):
    assert [p.name for p in panes_towards(row, (600, 0, 300, 900), "left")] == ["B", "A"]
    assert [p.name for p in panes_towards(row, (0, 0, 300, 900), "right")] == ["B", "C"]


def test_nothing_that_way_at_the_edge(row):
    """Scoped to the window: at the last pane in a direction, nothing."""
    assert panes_towards(row, (0, 0, 300, 900), "left") == []
    assert panes_towards(row, (600, 0, 300, 900), "right") == []


def test_a_pane_that_does_not_overlap_perpendicularly_is_not_that_way():
    here = (300, 0, 300, 300)
    elsewhere = [UINode(rect=(0, 500, 300, 300), name="below-left")]
    assert panes_towards(elsewhere, here, "left") == []


def test_more_overlap_wins_at_the_same_distance():
    origin = (300, 0, 300, 400)
    options = [UINode(rect=(0, 0, 300, 50), name="sliver"),
               UINode(rect=(0, 0, 300, 400), name="full")]
    assert [p.name for p in panes_towards(options, origin, "left")][0] == "full"


def test_direction_must_be_one_of_the_four():
    with pytest.raises(ValueError, match="direction must be"):
        panes_towards([], (0, 0, 10, 10), "sideways")


# -- the layout that was actually measured -----------------------------------

#: A VS Code window split three ways: explorer, a middle column split top and
#: bottom, a third editor beside it, a terminal across the bottom and a
#: full-height panel on the right.  Read off the screen on 2026-08-23, and
#: kept because a nested split is where flat geometric adjacency was expected
#: to pick something surprising.
MEASURED = {
    "Claude":   (1088, 145, 612, 925),
    "Terminal": (69, 811, 1001, 238),
    "EditorR":  (756, 104, 295, 661),
    "Explorer": (49, 132, 261, 572),
    "EditorMB": (317, 450, 382, 315),
    "EditorMT": (317, 104, 382, 314),
}


@pytest.fixture
def measured():
    return [UINode(rect=r, name=n) for n, r in MEASURED.items()]


@pytest.mark.parametrize("origin,direction,expected", [
    # A pane full height on the right, with an editor above a terminal to its
    # left: the editor covers 620 points of it and the terminal 238, but the
    # terminal's edge sits 19 points nearer.  Ordering by raw gap chose the
    # terminal.
    ("Claude", "left", "EditorR"),
    # Down the middle column, then out of it - the nested split resolves the
    # way the layout reads.
    ("EditorMT", "down", "EditorMB"),
    ("EditorMB", "down", "Terminal"),
    ("EditorMB", "up", "EditorMT"),
    ("EditorMT", "right", "EditorR"),
    ("EditorR", "right", "Claude"),
    ("EditorMT", "left", "Explorer"),
    ("EditorMB", "left", "Explorer"),
    ("Explorer", "down", "Terminal"),
    ("Terminal", "right", "Claude"),
    ("EditorR", "down", "Terminal"),
    # Window edges: scoped to the window, so nothing beyond them.
    ("EditorMT", "up", None),
    ("Claude", "right", None),
    ("Terminal", "down", None),
    ("Explorer", "left", None),
])
def test_the_measured_layout_resolves_as_the_screen_reads(measured, origin,
                                                          direction, expected):
    order = panes_towards(measured, MEASURED[origin], direction)
    assert (order[0].name if order else None) == expected


def test_a_nearer_edge_does_not_outvote_a_much_larger_overlap():
    """What GAP_BUCKET is for, reduced to its bones."""
    origin = (1000, 0, 300, 900)
    tall = UINode(rect=(600, 0, 350, 900), name="tall")     # gap 50, overlap 900
    near = UINode(rect=(600, 0, 390, 100), name="near")     # gap 10, overlap 100
    assert panes_towards([tall, near], origin, "left")[0].name == "tall"


def test_a_pane_a_column_further_off_is_still_ordered_behind():
    """The bucket must not flatten "next to me" and "two panes over"."""
    origin = (700, 0, 300, 900)
    next_to = UINode(rect=(400, 0, 290, 900), name="next")   # gap 10
    beyond = UINode(rect=(0, 0, 390, 900), name="beyond")    # gap 310
    assert [p.name for p in panes_towards([next_to, beyond], origin, "left")] \
        == ["next", "beyond"]


# -- the reference position --------------------------------------------------

def test_centre_and_clamp():
    assert centre_of((10, 20, 100, 200)) == (60.0, 120.0)
    pane = (0, 0, 100, 100)
    assert clamp_point((50, 50), pane) == (50, 50)
    # A scrolling pane's focused element runs past it, so its centre does too.
    assert clamp_point((50, 900), pane) == (50, 100)
    assert clamp_point((-40, -40), pane) == (0, 0)


def _step(panes_list, origin_rect, direction, reference):
    """One MoveFocus press: choose, then move only this axis of the reference.

    The same two lines the action runs, kept here so the property below is
    tested against the geometry rather than against the action's plumbing.
    """
    order = panes_towards(panes_list, origin_rect, direction,
                          reference=reference)
    if not order:
        return None, reference
    x, y = reference
    cx, cy = centre_of(order[0].rect)
    moved = (cx, y) if direction in ("left", "right") else (x, cy)
    return order[0], moved


@pytest.mark.parametrize("start", sorted(MEASURED))
@pytest.mark.parametrize("out_,back_", [("left", "right"), ("right", "left"),
                                        ("up", "down"), ("down", "up")])
def test_every_round_trip_comes_back(measured, start, out_, back_):
    """Overshooting has to be undoable, and this is the layout where it was
    not: steering by the pane being left behind, five of these landed
    somewhere other than where they started.  A pane is wide enough to lead
    to different answers from either end of it; a reference position that
    only the moving axis changes asks the same question on the way back."""
    seed = clamp_point(centre_of(MEASURED[start]), MEASURED[start])
    away, reference = _step(measured, MEASURED[start], out_, seed)
    if away is None:
        pytest.skip(f"nothing to the {out_} of {start}")
    home, _ = _step(measured, away.rect, back_, reference)
    assert home is not None and home.name == start


def test_the_reference_survives_a_pane_it_passes_through():
    """The point of preserving the off-axis coordinate: two moves out and two
    back, through a pane whose own centre would have answered differently."""
    panes_list = [UINode(rect=(0, 0, 300, 900), name="far"),
                  UINode(rect=(300, 0, 300, 900), name="middle"),
                  UINode(rect=(600, 0, 300, 400), name="top"),
                  UINode(rect=(600, 400, 300, 500), name="bottom")]
    start = (600, 400, 300, 500)                 # bottom
    reference = clamp_point(centre_of(start), start)
    a, reference = _step(panes_list, start, "left", reference)
    b, reference = _step(panes_list, a.rect, "left", reference)
    assert [a.name, b.name] == ["middle", "far"]
    c, reference = _step(panes_list, b.rect, "right", reference)
    d, _ = _step(panes_list, c.rect, "right", reference)
    assert [c.name, d.name] == ["middle", "bottom"]


def test_without_a_reference_the_origin_centre_is_used():
    """A first press has nothing remembered, and must still behave."""
    row = [UINode(rect=(0, 0, 300, 900), name="A"),
           UINode(rect=(300, 0, 300, 900), name="B")]
    assert panes_towards(row, (300, 0, 300, 900), "left")[0].name == "A"


def test_a_pane_covering_the_reference_is_preferred_over_a_nearer_one():
    origin = (600, 0, 300, 200)
    options = [UINode(rect=(300, 0, 250, 200), name="near-but-elsewhere"),
               UINode(rect=(0, 0, 250, 200), name="on-the-reference")]
    # Reference sits inside the second one's band only.
    options[0].rect = (300, 500, 250, 200)
    assert panes_towards(options, origin, "left",
                         reference=(700, 100))[0].name == "on-the-reference"

