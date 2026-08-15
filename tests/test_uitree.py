"""Portable tree traversal and search (keyhac/core/uitree.py).

Runs on every platform: the element protocol get_ui_tree() consumes is
children() / describe() / identity_key(), so a fake element exercises all of
it.  The shapes below are the ones real trees actually produced - the
row/column DAG and the nested duplicate label are copied from a live Safari
page, not invented.
"""

import pytest

from keyhac.core.uitree import (
    UINode, find_element, find_elements, format_tree, get_ui_tree,
    match_pattern, match_role,
)


class FakeElement:
    """An element in the shape the platform UIElements present."""

    def __init__(self, role=None, name=None, value=None, identifier=None,
                 rect=None, children=(), key=None):
        self._describe = {"role": role, "name": name, "value": value,
                          "identifier": identifier, "rect": rect}
        self._children = list(children)
        #: Identity for the dedupe; None means "platform has no cheap identity"
        #: (what the Windows element returns).
        self._key = key

    def describe(self):
        return dict(self._describe)

    def children(self):
        return list(self._children)

    def identity_key(self):
        return self._key


def cell(text, key):
    """A table cell in web shape: the string lives in a child, not the cell."""
    return FakeElement("AXCell", key=key,
                       children=[FakeElement("AXStaticText", value=text,
                                             key=f"{key}-t")])


def table_with_shared_cells():
    """A table whose cells are children of a row *and* of a column.

    This is what WebKit really hands back, and the reason the walk dedupes.
    """
    cells = [[cell(f"r{r}c{c}", f"{r}:{c}") for c in range(2)] for r in range(2)]
    rows = [FakeElement("AXRow", key=f"row{r}", children=cells[r]) for r in range(2)]
    columns = [FakeElement("AXColumn", key=f"col{c}",
                           children=[cells[r][c] for r in range(2)])
               for c in range(2)]
    return FakeElement("AXTable", identifier="results", key="table",
                       children=rows + columns)


# -- traversal --------------------------------------------------------------

def test_shared_cells_are_reported_once():
    tree = get_ui_tree(table_with_shared_cells())
    texts = [n.value for n in tree.walk() if n.role == "AXStaticText"]
    assert sorted(texts) == ["r0c0", "r0c1", "r1c0", "r1c1"]


def test_columns_keep_their_place_when_cells_are_deduped():
    """Dedupe must not drop the columns themselves, only the repeated cells."""
    tree = get_ui_tree(table_with_shared_cells())
    assert [n.role for n in tree.children] == \
        ["AXRow", "AXRow", "AXColumn", "AXColumn"]
    rows = [n for n in tree.children if n.role == "AXRow"]
    assert all(len(row.children) == 2 for row in rows)


def test_without_identity_nothing_is_deduped():
    """A platform reporting no identity (Windows) still walks correctly."""
    shared = FakeElement("Text", value="x")
    root = FakeElement("Pane", children=[
        FakeElement("Group", children=[shared]),
        FakeElement("Group", children=[shared]),
    ])
    tree = get_ui_tree(root)
    assert len([n for n in tree.walk() if n.value == "x"]) == 2


def test_max_depth_marks_truncation_but_a_leaf_is_not_truncated():
    deep = FakeElement("A", key="a", children=[
        FakeElement("B", key="b", children=[FakeElement("C", key="c")])])
    tree = get_ui_tree(deep, max_depth=1)
    assert tree.truncated is False           # root has children, but was walked
    assert tree.children[0].role == "B"
    assert tree.children[0].truncated is True     # cut here
    leaf = get_ui_tree(FakeElement("A", key="a"), max_depth=0)
    assert leaf.truncated is False                # nothing to cut


def test_max_nodes_stops_and_says_so():
    wide = FakeElement("Root", key="root", children=[
        FakeElement("Item", key=f"i{i}") for i in range(50)])
    tree = get_ui_tree(wide, max_nodes=10)
    assert len(list(tree.walk())) <= 10
    assert any(n.truncated for n in tree.walk())


def test_prune_skips_a_subtree():
    root = FakeElement("Root", key="root", children=[
        FakeElement("Keep", key="k", children=[FakeElement("Kid", key="kk")]),
        FakeElement("Skip", key="s", children=[FakeElement("Hidden", key="h")]),
    ])
    tree = get_ui_tree(root, prune=lambda n: n.role == "Skip")
    assert "Hidden" not in [n.role for n in tree.walk()]
    assert "Kid" in [n.role for n in tree.walk()]


def test_roles_filter_keeps_descendants_of_unmatched_parents():
    """A cell must survive a roles= filter that its row does not match."""
    tree = get_ui_tree(table_with_shared_cells(), roles="AXStaticText")
    assert sorted(n.value for n in tree.children) == \
        ["r0c0", "r0c1", "r1c0", "r1c1"]


def test_the_walk_records_each_nodes_parent():
    """Private back-edges for the MCP ancestor path (issue #55): each
    reported node points at the node it hangs from, the root at nothing, and
    a deduped shared cell at whichever side the walk reached first."""
    tree = get_ui_tree(table_with_shared_cells())
    assert tree._parent is None
    row = tree.children[0]
    assert row._parent is tree
    a_cell = row.children[0]
    assert a_cell._parent is row            # rows walk first; columns lose
    assert a_cell.children[0]._parent is a_cell


def test_the_roles_filter_reparents_hoisted_children():
    """A hoisted child's parent is the nearest *reported* ancestor - the
    chain never names a node the caller cannot see."""
    tree = get_ui_tree(table_with_shared_cells(), roles="AXStaticText")
    assert all(n._parent is tree for n in tree.children)


def test_find_all_matches_reach_the_searched_root_through_parents():
    matches = find_elements(table_with_shared_cells(), role="AXStaticText")
    node = matches[0]
    while node._parent is not None:
        node = node._parent
    assert node.role == "AXTable"


# -- the node projection ----------------------------------------------------

def test_text_keeps_falsy_values():
    """An unchecked checkbox is 0 and an empty field is "" - both are facts."""
    assert UINode(role="AXCheckBox", name="Archived", value=0).text == "Archived 0"
    assert UINode(role="AXTextField", name="Query", value="").text == "Query"


def test_all_text_reaches_into_children():
    tree = get_ui_tree(cell("REC-001", "c"))
    assert tree.text == ""              # the cell itself carries nothing
    assert tree.all_text == "REC-001"   # its child does


def test_all_text_collapses_the_nested_duplicate_label():
    """WebKit nests an identical AXStaticText inside a label's AXStaticText."""
    label = FakeElement("AXStaticText", value="Query", key="l", children=[
        FakeElement("AXStaticText", value="Query", key="l2")])
    assert get_ui_tree(label).all_text == "Query"


# -- search -----------------------------------------------------------------

@pytest.fixture
def form():
    return FakeElement("AXGroup", identifier="search", key="form", children=[
        FakeElement("AXTextField", name="Query", identifier="q", value="", key="q"),
        FakeElement("AXCheckBox", name="Include archived", identifier="archived",
                    value=0, key="a"),
        FakeElement("AXButton", name="Search", identifier="go", key="go"),
    ])


def test_find_by_identifier_name_and_role(form):
    assert find_element(form, identifier="q").name == "Query"
    assert find_element(form, name="Include archived").identifier == "archived"
    assert find_element(form, role="Button").identifier == "go"


def test_role_matches_with_or_without_the_ax_prefix(form):
    assert find_element(form, role="AXButton") is not None
    assert find_element(form, role="Button") is not None
    assert match_role("Edit", "Edit") and not match_role("Edit", "TextField")


def test_patterns_are_case_insensitive_with_alternation(form):
    assert find_element(form, name="query").identifier == "q"
    assert find_element(form, role="AXButton|AXCheckBox").identifier == "archived"
    assert len(find_elements(form, role="AXTextField|AXButton")) == 2


def test_text_matches_label_or_content(form):
    assert find_element(form, text="*Search*").identifier == "go"
    assert find_element(form, text="*archived*").identifier == "archived"


def test_missing_element_is_none_not_an_error(form):
    assert find_element(form, identifier="nope") is None
    assert find_elements(form, identifier="nope") == []


def test_predicate_filters(form):
    found = find_elements(form, predicate=lambda n: n.value == 0)
    assert [n.identifier for n in found] == ["archived"]


def test_match_pattern_on_none_is_false():
    assert match_pattern(None, "*") is False
    assert match_role(None, "*") is False


# -- formatting -------------------------------------------------------------

def test_format_tree_shows_a_zero_value(form):
    """The checkbox case: `if value:` would hide exactly the 0 that matters."""
    text = format_tree(get_ui_tree(form))
    assert "= '0'" in text
    assert "#archived" in text


def test_format_tree_marks_truncation():
    deep = FakeElement("A", key="a", children=[FakeElement("B", key="b")])
    assert "(truncated)" in format_tree(get_ui_tree(deep, max_depth=0))


def test_all_text_drops_a_child_that_only_echoes_its_parent():
    """WebKit nests an AXStaticText carrying the same string as its parent's
    name; a heading does it too, so a dialog title read back doubled."""
    heading = FakeElement("AXHeading", name="Approve this item?", key="h",
                          children=[FakeElement("AXStaticText",
                                                value="Approve this item?",
                                                key="h2")])
    assert get_ui_tree(heading).all_text == "Approve this item?"


def test_all_text_keeps_non_adjacent_repeats():
    """Two cells of a row really can both say 37 - that is data."""
    row = FakeElement("AXRow", key="r", children=[
        cell("37", "c1"), cell("x", "c2"), cell("37", "c3")])
    assert get_ui_tree(row).all_text == "37 x 37"
