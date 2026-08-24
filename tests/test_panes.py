"""Pane finding and direction (keyhac/core/panes.py).

Hermetic: the fake element implements the same small protocol the real ones
do, so the geometry is tested without a screen.  The layouts below are the
ones that were actually measured - a VS Code-shaped three-pane window, and
Finder's file list reported twice one pixel apart.
"""

import pytest

from keyhac.core import panes
from keyhac.core.panes import (
    contains_rect, find_panes, focus_target, pane_holding, panes_towards,
    same_rect,
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


# -- pane_holding ------------------------------------------------------------

def test_pane_holding_picks_the_smallest_container():
    panes_list = [UINode(rect=(0, 0, 1000, 1000)), UINode(rect=(0, 0, 300, 900))]
    assert pane_holding(panes_list, (10, 10, 50, 50)).rect == (0, 0, 300, 900)


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


# -- recipes -----------------------------------------------------------------

class FakeWindow:
    def __init__(self, app_name="Code", title="Main"):
        self.app_name = app_name
        self.title = title
        self.class_name = None


@pytest.fixture(autouse=True)
def no_leftover_recipes():
    panes.clear_recipes()
    yield
    panes.clear_recipes()


def test_the_most_recent_matching_recipe_wins():
    panes.define_recipe(app="Code", roles="AXGroup")
    panes.define_recipe(app="Code", roles="AXScrollArea")
    assert panes.settings_for(FakeWindow())["roles"] == "AXScrollArea"


def test_a_recipe_only_applies_to_its_application():
    panes.define_recipe(app="Finder", roles="AXOutline")
    assert panes.settings_for(FakeWindow(app_name="Code")) == {}
    assert panes.settings_for(FakeWindow(app_name="Finder"))["roles"] == "AXOutline"


def test_unset_recipe_fields_are_not_recorded():
    panes.define_recipe(app="Code", roles="AXGroup", min_area=None)
    assert panes.settings_for(FakeWindow()) == {"roles": "AXGroup"}


def test_reloading_the_config_does_not_accumulate_recipes():
    panes.define_recipe(app="Code", roles="AXGroup")
    panes.clear_recipes()
    panes.define_recipe(app="Code", roles="AXGroup")
    assert len(panes._recipes) == 1
