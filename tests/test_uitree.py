"""Portable tree traversal and search (keyhac/core/uitree.py).

Runs on every platform: the element protocol get_ui_tree() consumes is
children() / describe() / identity_key(), so a fake element exercises all of
it.  The shapes below are the ones real trees actually produced - the
row/column DAG and the nested duplicate label are copied from a live Safari
page, not invented.
"""

import pytest

from keyhac.core.uitree import (
    UINode, collapsed_tree, find_element, find_elements, format_tree, get_ui_tree,
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


def test_a_node_carries_no_back_edge_to_the_walk():
    """The walk's parent is a fact about one walk - which side of a DAG a
    shared cell hung from, which ancestor a roles= filter hoisted it to - so
    it is not kept on the node; the MCP tools index the tree they searched
    (discussion #92)."""
    tree = get_ui_tree(table_with_shared_cells())
    assert all(not hasattr(node, "_parent") for node in tree.walk())


# -- parent -----------------------------------------------------------------

class AXElement(FakeElement):
    """An element that reports its OS parent the way macOS does: in the same
    batched read as its description, and through parent()."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ax_parent = None
        for child in self._children:
            if getattr(child, "ax_parent", None) is None:
                child.ax_parent = self

    def describe_and_parent(self):
        return self.describe(), self.ax_parent

    def parent(self):
        return self.ax_parent


def ax_window():
    """application > window > group > field, the shape ui.focused() climbs."""
    field_ = AXElement("AXTextField", name="Query", key="field")
    group = AXElement("AXGroup", identifier="search", key="group",
                      children=[field_])
    window = AXElement("AXWindow", name="Main", key="window", children=[group])
    AXElement("AXApplication", key="app", children=[window])
    return window, group, field_


def test_a_shallow_node_carries_its_ancestors_up_to_the_window():
    """ui.focused() and ui.node() read with max_depth=0, and parent is still
    filled in: shallow ancestors, negative depths, the application left out
    so a top-level window's parent is None."""
    _window, _group, field_ = ax_window()
    node = get_ui_tree(field_, max_depth=0)
    group = node.parent
    assert (group.role, group.identifier, group.depth) == \
        ("AXGroup", "search", -1)
    assert group.children == [] and group.truncated
    window = group.parent
    assert (window.role, window.depth) == ("AXWindow", -2)
    assert window.parent is None


def test_a_walk_links_its_nodes_and_its_root_upwards():
    window, _group, _field = ax_window()
    tree = get_ui_tree(window)
    assert tree.parent is None
    group = tree.children[0]
    assert group.parent is tree
    assert group.children[0].parent is group

    inner = get_ui_tree(window._children[0])
    assert inner.parent.role == "AXWindow" and inner.parent.depth == -1


def test_a_shared_cell_has_its_row_as_parent_whichever_side_came_first():
    """children are the OS's, so the cell is in both lists - one node, read
    once - and its parent is the row even with the column listed first."""
    cells = [AXElement("AXCell", value=f"c{c}", key=f"c{c}") for c in range(2)]
    row = AXElement("AXRow", key="row", children=cells)
    column = AXElement("AXColumn", key="col", children=cells[:1])
    table = AXElement("AXTable", key="table", children=[column, row])
    tree = get_ui_tree(table)
    column_node, row_node = tree.children
    shared = column_node.children[0]
    assert shared.value == "c0"
    assert row_node.children[0] is shared
    assert shared.parent is row_node


def test_a_parent_the_os_never_lists_is_a_shallow_node():
    """Electron: the group lists the web area, whose AXParent is a scroll
    area the group never lists (measured, discussion #92)."""
    web = AXElement("AXWebArea", name="Page", key="web")
    scroll = AXElement("AXScrollArea", key="scroll", children=[web])
    group = AXElement("AXGroup", key="group", children=[web])
    scroll.ax_parent = group
    window = AXElement("AXWindow", key="window", children=[group])
    tree = get_ui_tree(window)
    group_node = tree.children[0]
    web_node = group_node.children[0]
    hidden = web_node.parent
    assert hidden.role == "AXScrollArea" and hidden.truncated
    assert hidden not in group_node.children
    assert hidden.parent is group_node


def test_under_a_roles_filter_the_parent_is_the_real_one():
    window, _group, _field = ax_window()
    tree = get_ui_tree(window, roles="AXTextField")
    field_node = tree.children[0]
    assert field_node.parent.role == "AXGroup"
    assert field_node.parent is not tree


def test_where_the_platform_reports_no_parent_the_walk_edge_is_used():
    """UI Automation's control view is a tree, so the walk's edge is the OS
    parent; above the root, parent() is climbed."""
    tree = get_ui_tree(table_with_shared_cells())
    assert tree.parent is None
    row = tree.children[0]
    assert row.parent is tree and row.children[0].parent is row

    root = FakeElement("Pane", key="pane")
    window = FakeElement("Window", key="win")
    desktop = FakeElement("Desktop", key="desk")
    root.parent = lambda: window
    window.parent = lambda: desktop
    desktop.parent = lambda: None
    node = get_ui_tree(root)
    assert node.parent.role == "Window" and node.parent.parent is None


def test_a_column_lists_its_cells_and_traversal_meets_each_once():
    """children are the OS's: the column holds its cells too.  walk(),
    find_all() and all_text still meet every cell once, under its row."""
    tree = get_ui_tree(table_with_shared_cells())
    columns = [n for n in tree.children if n.role == "AXColumn"]
    rows = [n for n in tree.children if n.role == "AXRow"]
    assert [len(c.children) for c in columns] == [2, 2]
    assert columns[0].children[0] is rows[0].children[0]
    cells = [n for n in tree.walk() if n.role == "AXCell"]
    assert len(cells) == 4 and len({id(c) for c in cells}) == 4
    assert len(find_elements(table_with_shared_cells(), role="AXCell")) == 4
    assert tree.all_text == "r0c0 r0c1 r1c0 r1c1"


def test_a_column_walked_on_its_own_yields_its_cells():
    """The cells' parent (a row) is not below the column, so a walk from the
    column shows them there - reading a column is now possible."""
    tree = get_ui_tree(table_with_shared_cells())
    column = next(n for n in tree.children if n.role == "AXColumn")
    assert [n.value for n in column.walk() if n.role == "AXStaticText"] == \
        ["r0c0", "r1c0"]
    assert column.all_text == "r0c0 r1c0"


def test_a_dump_shows_a_shared_cell_once_and_says_where_the_rest_went():
    tree = get_ui_tree(table_with_shared_cells())
    text = tree.dump()
    assert text.count("'r0c0'") == 1
    assert text.count("… 2 more children, shown under their own parent") == 2
    collapsed, hidden = collapsed_tree(tree)
    assert collapsed.count("'r0c0'") == 1 and hidden == 0
    assert "shown under their own parent" in collapsed


def test_a_node_whose_parent_never_lists_it_is_not_lost():
    """AXParent names a node that is in the snapshot but does not list this
    one - an application bug, or the screen moving mid-walk.  The node is
    shown under the node that does list it, rather than nowhere."""
    stray = AXElement("AXButton", name="Stray", key="stray")
    holder = AXElement("AXGroup", key="holder", children=[stray])
    claimed = AXElement("AXGroup", key="claimed")
    stray.ax_parent = claimed
    window = AXElement("AXWindow", key="win", children=[holder, claimed])
    tree = get_ui_tree(window)
    assert [n.name for n in tree.walk()].count("Stray") == 1
    stray_node = tree.children[0].children[0]
    assert stray_node.parent is tree.children[1]
    assert "'Stray'" in tree.dump()


def test_a_reported_cycle_ends_and_shows_each_node_once():
    """An application listing an ancestor among its children must not send
    a walk round forever."""
    inner = AXElement("AXGroup", key="inner")
    outer = AXElement("AXGroup", key="outer", children=[inner])
    inner._children = [outer]            # the cycle
    window = AXElement("AXWindow", key="win", children=[outer])
    tree = get_ui_tree(window)
    assert [n.role for n in tree.walk()] == ["AXWindow", "AXGroup", "AXGroup"]
    assert tree.dump().count("AXGroup") == 2
    assert collapsed_tree(tree)[0].count("AXGroup") == 2


def test_electron_web_content_is_walked_under_the_group_that_lists_it():
    web = AXElement("AXWebArea", name="Page", key="web")
    scroll = AXElement("AXScrollArea", key="scroll", children=[web])
    group = AXElement("AXGroup", key="group", children=[web])
    scroll.ax_parent = group
    tree = get_ui_tree(AXElement("AXWindow", key="w", children=[group]))
    assert [n.role for n in tree.walk()] == ["AXWindow", "AXGroup", "AXWebArea"]


def test_parent_stays_out_of_equality_and_repr():
    """parent and children make a cycle; __eq__ and repr must not follow it."""
    window, _group, _field = ax_window()
    first, second = get_ui_tree(window), get_ui_tree(window)
    assert first.children[0] == second.children[0]
    assert "parent" not in repr(first.children[0])


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


class TestNameProvenance:
    """`name_source` says which attribute answered - the difference between a
    label the user can read and a tooltip only assistive tech sees. An
    icon-only button has no label at all, and knowing that is what tells a
    caller it can be addressed only by role and position."""

    def test_a_label_wins(self):
        from keyhac.core.uitree import _first_name
        assert _first_name(("label", "Save"), ("description", "Save file"),
                           ("help", "Saves")) == ("Save", "label")

    def test_a_description_stands_in_for_a_missing_label(self):
        from keyhac.core.uitree import _first_name
        assert _first_name(("label", None), ("description", "Bold"),
                           ("help", "Make bold")) == ("Bold", "description")

    def test_a_tooltip_is_the_last_resort(self):
        from keyhac.core.uitree import _first_name
        assert _first_name(("label", ""), ("description", None),
                           ("help", "Toggle sidebar")) == ("Toggle sidebar",
                                                           "help")

    def test_no_name_at_all_is_reported_as_such(self):
        from keyhac.core.uitree import _first_name
        assert _first_name(("label", None), ("description", ""),
                           ("help", None)) == (None, None)

    def test_an_empty_string_is_not_a_name(self):
        # An unlabelled control frequently answers "" rather than nothing.
        from keyhac.core.uitree import _first_name
        assert _first_name(("label", ""), ("description", "Close"))[1] == \
            "description"

    def test_a_node_defaults_to_no_provenance(self):
        from keyhac.core.uitree import UINode
        assert UINode().name_source is None
        assert UINode(name="Save", name_source="label").name_source == "label"


def _rows(parent_depth, count, role="AXRow"):
    return [UINode(role=role, name=f"{role} {i}", depth=parent_depth + 1) for i in range(count)]


class TestCollapsedTree:

    def test_a_long_run_keeps_its_first_two_and_a_count(self):
        root = UINode(role="AXTable", depth=0)
        root.children = _rows(0, 10)
        text, hidden = collapsed_tree(root)
        assert text.splitlines() == ["AXTable", "  AXRow 'AXRow 0'", "  AXRow 'AXRow 1'",
                                     "  … 8 more AXRow"]
        assert hidden == 8

    def test_a_short_run_and_mixed_roles_are_left_alone(self):
        root = UINode(role="AXTable", depth=0)
        root.children = _rows(0, 5) + _rows(0, 1, "AXButton") + _rows(0, 5)
        text, hidden = collapsed_tree(root)
        assert hidden == 0 and text == format_tree(root)

    def test_what_is_inside_a_hidden_node_is_counted(self):
        root = UINode(role="AXTable", depth=0)
        root.children = _rows(0, 7)
        for row in root.children:
            row.children = _rows(1, 2, "AXCell")
        text, hidden = collapsed_tree(root)
        assert "… 5 more AXRow (15 nodes with what is inside them)" in text
        assert hidden == 15

    def test_runs_are_collapsed_inside_what_is_kept(self):
        root = UINode(role="AXTable", depth=0)
        root.children = _rows(0, 6)
        root.children[0].children = _rows(1, 9, "AXCell")
        text, hidden = collapsed_tree(root)
        assert "    … 7 more AXCell" in text
        assert hidden == 7 + 4

    def test_a_run_after_other_siblings_is_found(self):
        root = UINode(role="AXGroup", depth=0)
        root.children = _rows(0, 1, "AXButton") + _rows(0, 6) + _rows(0, 1, "AXButton")
        lines = collapsed_tree(root)[0].splitlines()
        assert lines[-2] == "  … 4 more AXRow" and lines[-1].startswith("  AXButton")
