"""Portable traversal and search over the platform element trees.

`keyhac/platform/mac/uielement.py` and `keyhac/platform/win/uielement.py` each
expose one element in their own OS's vocabulary - AX attribute names on macOS,
UI Automation property names on Windows - and deliberately do not pretend to
share one (see the Windows module's docstring).  This module is the portable
layer that sits on top: it walks whatever those elements say their children
are, and projects each one onto the handful of facts that *do* mean the same
thing on both systems.

    role        AXRole            / ControlType
    name        AXTitle           / Name              (the label, not content)
    value       AXValue           / Value pattern     (the content)
    identifier  AXDOMIdentifier   / AutomationId      (stable id, when any)
    rect        AXPosition+AXSize / BoundingRectangle

Anything outside that projection is still reachable: every UINode carries the
platform element as `.element`, so a config can ask it for
"AXVisibleCharacterRange" or "FrameworkId" directly.

WHAT WALKING REAL TREES TAUGHT (measured 2026-08-06, macOS 15, Safari 18 on a
page with a search form, a 3-row result table and a modal):

- **It is a DAG, not a tree.** A table's cells are children of their AXRow
  *and* of their AXColumn - the same element, CFEqual-identical, reached twice.
  A naive recursion therefore reports every cell twice and doubles every
  extracted table.  `seen` below is not cycle paranoia; it is load bearing on
  any page with a table.
- **Depth alone is a bad budget.** The trivial page above needs depth 10 to
  reach a table cell, while VS Code emits 1543 nodes by depth 30.  So the
  default depth is generous and `max_nodes` is the real guard.
- **Truncation must be visible.** A tree that silently stopped at the cap reads
  exactly like a complete one, and the caller writes an action against half a
  page.  Hence UINode.truncated and a warning in format_tree().
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


class StaleElement(Exception):
    """The element this node was read from no longer exists.

    **A `UINode` is a snapshot.** It records what an element was when the tree
    was walked; the screen moves on and the node does not notice. That is the
    contract on purpose - a node that quietly re-read itself would hide exactly
    the change an action's preconditions exist to catch.

    So the node has to say when it has gone stale, and say it in a way an
    action can act on. The distinction this exists for is the one §3.7 turns
    on:

    - `StaleElement` - *the screen moved*. Re-find the element and carry on, or
      stop and hand back to a human. The action is not wrong.
    - `FillFailed` / an empty search - *the selector is wrong*. The action was
      written against a screen that is not this one, and running it again will
      fail the same way.

    Before this existed both arrived as "element supports no press action",
    because a dead element reports no actions - true, and the least useful true
    thing to say.
    """

#: Depth bound.  Deep enough to reach table cells in web content (which sit at
#: about 10) without following an Electron tree to its bottom.
DEFAULT_MAX_DEPTH = 14

#: Node budget.  The guard that actually binds - see the module docstring.
DEFAULT_MAX_NODES = 1000


@dataclass
class UINode:
    """One element, projected onto the facts both platforms agree on.

    Every member is one of two kinds.  `find`, `find_all`, `reread`, the
    waits and the text layer read the live UI each time they are called,
    dispatching to the event-loop thread themselves; `text`, `all_text`,
    `children`, `walk` and `dump` are free reads of this snapshot, showing
    the screen as it was when the node was read.

    Attributes:
        role: Control role - "AXTextField" (macOS) or "Edit" (Windows).  The
            OS's own name; match it with `role=` patterns, which accept the
            macOS names with or without their "AX" prefix.
        name: The element's label, not its content ("Query" for a field
            labelled Query).
        value: The element's content (what is typed into the field, "0"/"1" for
            a checkbox).
        name_source: Which attribute `name` came from - "label",
            "description", "help", or None when the element has no name.
            An icon-only button typically has no label and answers one of the
            other two; nothing at all means it can be addressed only by role
            and position.
        identifier: A stable identifier where the platform has one - the DOM id
            in web content, AXIdentifier in native macOS UI, AutomationId on
            Windows.  The best thing to address an element by when present,
            since it survives relabelling and localisation.
        rect: (x, y, w, h) in screen coordinates, or None.
        depth: Distance below the root the walk started from.
        element: The platform UIElement, for anything outside this projection.
        children: Child nodes, in the platform's own order.
        truncated: True when this node's children were cut off by max_depth or
            max_nodes - so a caller can tell "leaf" from "gave up here".
    """

    role: str | None = None
    name: str | None = None
    #: Where `name` came from - "label" (AXTitle / UIA Name), "description"
    #: (AXDescription, what an unlabelled control offers instead), "help"
    #: (AXHelp / UIA HelpText, the tooltip), or None when there is no name.
    #: An icon-only button usually answers "description" or "help", and the
    #: difference matters: only "label" is words the user can actually see.
    name_source: str | None = None
    value: str | None = None
    identifier: str | None = None
    rect: tuple | None = None
    depth: int = 0
    element: Any = None
    children: list["UINode"] = field(default_factory=list)
    truncated: bool = False

    # Structural parent within the walk that built this node - private
    # plumbing for the MCP ancestor path (issue #55), not public shape, and
    # deliberately not a dataclass field: a back-edge in __eq__ would recurse
    # forever through the cycle, and the semantics that would freeze on
    # publication are awkward (under the DAG dedupe a shared cell's parent is
    # whichever of row/column the walk reached first; under a roles= filter
    # it is the nearest *reported* ancestor). None on a walk's root and on
    # the shallow nodes ui.window()/ui.node() return. Promotable later.
    _parent = None

    @property
    def text(self) -> str:
        """This element's own label and content, as one string.

        Note `is not None`, not truthiness: an unchecked checkbox's value is 0
        and an empty field's is "", and both are facts an action needs.  This
        is the same trap "read before toggling" exists to avoid.
        """
        parts = [p for p in (self.name, self.value) if p is not None and p != ""]
        return " ".join(str(p) for p in parts)

    @property
    def all_text(self) -> str:
        """The text of this element and everything under it.

        What a table cell needs: web content puts the visible string in a child
        AXStaticText, so the cell's own `text` is empty and reading a results
        table off `.text` silently yields blank columns.

        Two kinds of repetition are dropped, both of them WebKit's doing: a
        child that merely restates its parent (a label's AXStaticText carrying
        the label again, a heading's child carrying the heading again), and an
        immediate repeat of the piece just emitted.  Repeats that are *not*
        adjacent survive on purpose - two cells of a row legitimately holding
        "37" are data, not noise.
        """
        out: list[str] = []

        def collect(node, parent_pieces):
            pieces = [str(p) for p in (node.name, node.value)
                      if p is not None and p != ""]
            for piece in pieces:
                if piece in parent_pieces:
                    continue          # the child only echoes its parent
                if out and out[-1] == piece:
                    continue
                out.append(piece)
            for child in node.children:
                collect(child, pieces or parent_pieces)

        collect(self, [])
        return " ".join(out)

    def __repr__(self):
        bits = [self.role or "?"]
        if self.name:
            bits.append(f"name={self.name!r}")
        if self.identifier:
            bits.append(f"id={self.identifier!r}")
        return f"UINode({' '.join(bits)})"

    # -- searching -----------------------------------------------------------
    #
    # Every method below reads the live UI, so every one dispatches to the
    # event-loop thread itself.  That is not a convenience: element access is
    # main-thread-only, an action's pipeline runs on a worker, and making each
    # call site remember that produced a wrapper around nearly every line of
    # the first six actions written against this API.

    def find(self, max_depth: int = DEFAULT_MAX_DEPTH,
             max_nodes: int = DEFAULT_MAX_NODES, **criteria) -> "UINode | None":
        """The first element below this one matching `criteria`, or None.

        Reads the live UI at call time - this node's captured `children`
        play no part, so an old window node finds what is on screen *now*.

        What comes back is a node of *that* walk, not a bare handle, so its
        `children`, `walk()` and `all_text` are already filled in - reading a
        cell you just found needs no second call.  The subtree it carries is
        bounded by the same `max_depth`, counted from this node: something
        matched near the bottom of a default walk has little or nothing below
        it, and web content keeps its string one level down.  A table that
        reads as blank columns is that, and `table.reread()` is the fix rather
        than a deeper search for cells.
        None rather than an exception, because only the caller knows whether
        a missing element is a failed precondition or an expected absence -
        `wait_for` is the one that insists.

        Args:
            max_depth: Depth bound for the underlying walk.  Web content can
                nest controls deeper than the default; raise this before
                concluding an element is not there.
            max_nodes: Node budget for the underlying walk.
            **criteria: `role`, `name`, `value`, `identifier`, `text` and
                `predicate`; patterns are case-insensitive fnmatch with "|"
                alternation.
        """
        from keyhac.core.wait import evaluate_on_main_thread
        return evaluate_on_main_thread(lambda: find_element(
            self, max_depth=max_depth, max_nodes=max_nodes, **criteria))

    def find_all(self, max_depth: int = DEFAULT_MAX_DEPTH,
                 max_nodes: int = DEFAULT_MAX_NODES,
                 **criteria) -> list["UINode"]:
        """Every element below this one matching `criteria`, in tree order.

        The same live read as `find`, and the same snapshot rule for what it
        hands back - each match carries its own subtree, bounded by this call's
        `max_depth`.

        A table's rows come back once each.  The tree is a DAG - a cell is a
        child of its row and of its column both - but the walk dedupes on
        element identity, so this is already handled; it only bites code that
        walks `children` by hand.

        Args:
            max_depth: Depth bound for the underlying walk.
            max_nodes: Node budget for the underlying walk.
            **criteria: As `find`.
        """
        from keyhac.core.wait import evaluate_on_main_thread
        return evaluate_on_main_thread(lambda: find_elements(
            self, max_depth=max_depth, max_nodes=max_nodes, **criteria))

    def reread(self, max_depth: int = DEFAULT_MAX_DEPTH,
               max_nodes: int = DEFAULT_MAX_NODES,
               roles: str | None = None, prune=None) -> "UINode":
        """Read this subtree again, returning a fresh node.

        A UINode is a snapshot: the screen moves on, and nothing here notices.
        """
        from keyhac.core.wait import evaluate_on_main_thread
        return evaluate_on_main_thread(lambda: get_ui_tree(
            self, max_depth=max_depth, max_nodes=max_nodes, roles=roles,
            prune=prune))

    def dump(self, max_value: int = 60) -> str:
        """This subtree as indented text - to read, and to hand to an AI agent.

        Prints the snapshot as held: a node from `ui.window()` or `ui.node()`
        has read nothing below itself yet, so `reread()` first.
        """
        return format_tree(self, max_value=max_value)

    # -- the text layer ------------------------------------------------------

    def read_text(self) -> str | None:
        """The whole text content, descending into child text nodes.

        Distinct from the `text` / `all_text` properties, which are free reads
        of the snapshot: this asks the application, and is what a terminal
        buffer or a document body needs.
        """
        return self._on_element("get_text")

    def line_at_caret(self) -> str | None:
        """The line the caret is on - no selection, no pointer."""
        return self._on_element("get_line_at_caret")

    def selection(self) -> str | None:
        """The selected text ("" is a real answer, meaning a bare caret)."""
        return self._on_element("get_selection")

    # -- acting --------------------------------------------------------------

    def press(self) -> None:
        """Press this element, by whichever action name the platform uses."""
        from keyhac.core.fill import press
        press(self)

    def focus(self, timeout: float = None) -> bool:
        """Give this element keyboard focus; True when it actually landed.

        Landing is not instant, so the ask is repeated for `timeout` seconds
        (`keyhac.core.fill.FOCUS_TIMEOUT` by default) before the answer is
        False.  Pass 0 to ask exactly once.
        """
        from keyhac.core.fill import focus
        return focus(self, timeout)

    def set_text(self, text: str, **options) -> str:
        """Write `text` into this field and prove it arrived.

        Returns the mechanism that worked; raises `FillFailed` when none did.
        Takes the same options as `keyhac.core.fill.set_text`.
        """
        from keyhac.core.fill import set_text
        return set_text(self, text, **options)

    def set_checked(self, checked: bool) -> bool:
        """Set a checkbox, reading it first. True when it pressed."""
        from keyhac.core.fill import set_checked
        return set_checked(self, checked)

    # -- waiting, scoped to this subtree -------------------------------------

    def wait_for(self, timeout: float = 10.0, message: str | None = None,
                 max_depth: int = DEFAULT_MAX_DEPTH,
                 max_nodes: int = DEFAULT_MAX_NODES, **criteria) -> "UINode":
        """Wait until an element matching `criteria` exists below this one.

        Args:
            timeout: Seconds before giving up.
            message: What was being waited for, for the timeout error.
            max_depth: Depth bound for the walk.  Every poll walks the tree
                again, so this is a cost bound as much as a reach bound.
            max_nodes: Node budget for the walk.
            **criteria: As `find`.
        """
        from keyhac.core.wait import wait_for_element
        return wait_for_element(self, timeout=timeout, message=message,
                                max_depth=max_depth, max_nodes=max_nodes,
                                **criteria)

    def wait_until_gone(self, timeout: float = 10.0,
                        message: str | None = None,
                        max_depth: int = DEFAULT_MAX_DEPTH,
                        max_nodes: int = DEFAULT_MAX_NODES,
                        **criteria) -> None:
        """Wait until nothing below this one matches `criteria`.

        A bound makes "gone" mean "not found within the bounds": an element
        deeper than `max_depth` counts as gone.

        Args:
            timeout: Seconds before giving up.
            message: What was being waited for, for the timeout error.
            max_depth: Depth bound for the walk.
            max_nodes: Node budget for the walk.
            **criteria: As `find`.
        """
        from keyhac.core.wait import wait_until_gone
        wait_until_gone(self, timeout=timeout, message=message,
                        max_depth=max_depth, max_nodes=max_nodes, **criteria)

    def wait_until_stable(self, quiet: float = 0.3, timeout: float = 10.0,
                          **bounds) -> None:
        """Wait until this subtree stops changing."""
        from keyhac.core.wait import wait_for_stable
        wait_for_stable(self, quiet=quiet, timeout=timeout, **bounds)

    def _on_element(self, method: str):
        from keyhac.core.wait import evaluate_on_main_thread
        call = getattr(self.element, method, None)
        if call is None:
            return None
        return evaluate_on_main_thread(call)


    def walk(self) -> Iterator["UINode"]:
        """This node and every descendant in the snapshot, depth first.

        A walk over what was captured, not what is on screen: it yields the
        nodes already held, asking the OS nothing.  On a node read with
        `max_depth=0` - which is what `ui.window()` and `ui.node()` return -
        that is this node alone.  `find_all()` is the one that searches the
        live tree; `reread().walk()` traverses a fresh capture.
        """
        yield self
        for child in self.children:
            yield from child.walk()


def _describe(element) -> dict:
    """The projection for one element, from whichever platform it belongs to."""
    describe = getattr(element, "describe", None)
    return describe() if describe is not None else {}


def _children(element) -> list:
    children = getattr(element, "children", None)
    return children() if children is not None else []


def _identity(element):
    """A hashable identity for dedupe, or None when the platform has none.

    macOS AX element refs are CFEqual-comparable and hash accordingly, so the
    row/column double-listing collapses.  UI Automation's control view is a
    real tree and needs no dedupe, so its elements return None here rather
    than paying for GetRuntimeId on every node.
    """
    key = getattr(element, "identity_key", None)
    return key() if key is not None else None


def get_ui_tree(root, max_depth: int = DEFAULT_MAX_DEPTH,
                max_nodes: int = DEFAULT_MAX_NODES,
                roles: str | None = None,
                prune: Callable[[UINode], bool] | None = None) -> UINode:
    """Read `root` and its descendants into UINodes.

    Args:
        root: A platform UIElement (`keymap.focus.element`, or an application
            element) - or a UINode, whose `.element` is used.
        max_depth: How far below `root` to descend.
        max_nodes: Stop after this many nodes.  The node where it stopped is
            marked `truncated`.
        roles: Role pattern; when given, only matching elements are *reported*
            (the walk still descends through the others, since a table's cells
            live under rows that may not match).
        prune: Called with each node; return True to skip its subtree.  The
            cheap way to keep a walk out of an area known to be huge.

    Returns:
        The root UINode, with `.children` populated.
    """
    root_element = root.element if isinstance(root, UINode) else root
    budget = [max_nodes]
    seen = set()

    def build(element, depth: int) -> UINode | None:
        identity = _identity(element)
        if identity is not None:
            if identity in seen:
                return None
            seen.add(identity)
        node = UINode(depth=depth, element=element, **_describe(element))
        budget[0] -= 1
        if prune is not None and prune(node):
            node.truncated = True
            return node
        if depth >= max_depth:
            # Only a real cut, not a leaf that happens to sit at the bound.
            node.truncated = bool(_children(element))
            return node
        for child in _children(element):
            if budget[0] <= 0:
                node.truncated = True
                break
            built = build(child, depth + 1)
            if built is None:
                continue
            if roles is None or match_role(built.role, roles):
                built._parent = node
                node.children.append(built)
            else:
                # Not reported, but its matching descendants still are, so a
                # roles= filter cannot lose a cell by way of its row.  They
                # are re-pointed too: the parent chain describes the reported
                # tree, never a node the caller cannot see.
                for hoisted in built.children:
                    hoisted._parent = node
                node.children.extend(built.children)
        return node

    return build(root_element, 0) or UINode(element=root_element)


def find_elements(root, role: str | None = None, name: str | None = None,
                  value: str | None = None, identifier: str | None = None,
                  text: str | None = None,
                  predicate: Callable[[UINode], bool] | None = None,
                  max_depth: int = DEFAULT_MAX_DEPTH,
                  max_nodes: int = DEFAULT_MAX_NODES) -> list[UINode]:
    """Every element under `root` matching all the given patterns.

    Patterns are case-insensitive fnmatch with "|" alternation, the same
    matching `define_keytable(app=...)` uses.  `text` matches against label and
    content together, which is usually what "find the Search button" means when
    you do not know whether the app puts its caption in the name or the value.

    Args:
        root: Platform UIElement or UINode to search below.
        role: Role pattern ("Button", "AXTextField", "Edit|Text").
        name: Label pattern.
        value: Content pattern.
        identifier: DOM id / AutomationId pattern - the most stable when the
            application offers one.
        text: Pattern matched against label and content together.
        predicate: Extra filter called with the candidate UINode.
        max_depth: Depth bound for the underlying walk.
        max_nodes: Node budget for the underlying walk.

    A match is a node of the walk this call made, not a bare handle: see
    `UINode.find` for what that means for reading it.

    Returns:
        Matching nodes in tree order.  Empty when nothing matched - callers
        that need an element should say so themselves, since "the UI changed"
        must stop the action rather than proceed on a guess.
    """
    tree = get_ui_tree(root, max_depth=max_depth, max_nodes=max_nodes)
    out = []
    for node in tree.walk():
        if role is not None and not match_role(node.role, role):
            continue
        if name is not None and not match_pattern(node.name, name):
            continue
        if value is not None and not match_pattern(node.value, value):
            continue
        if identifier is not None and not match_pattern(node.identifier, identifier):
            continue
        if text is not None and not match_pattern(node.text, text):
            continue
        if predicate is not None and not predicate(node):
            continue
        out.append(node)
    return out


def find_element(root, **kwargs) -> UINode | None:
    """The first match of `find_elements`, or None.

    Takes the same arguments.  Returning None rather than raising is
    deliberate: the caller is the one that knows whether a missing element is a
    failed precondition or an expected absence.
    """
    matches = find_elements(root, **kwargs)
    return matches[0] if matches else None


def format_tree(node: UINode, indent: int = 0, max_value: int = 60) -> str:
    """The tree as indented text - for reading, and for handing to an AI agent.

    Deliberately terse: this is what gets pasted into a conversation while an
    action is being written, and a page's worth of it has to stay readable.
    """
    lines = []
    for current in node.walk():
        prefix = "  " * (current.depth - node.depth + indent)
        bits = [current.role or "?"]
        if current.identifier:
            bits.append(f"#{current.identifier}")
        if current.name:
            bits.append(repr(current.name))
        if current.value is not None and current.value != "":
            value = str(current.value).replace("\n", "\\n")
            if len(value) > max_value:
                value = value[:max_value] + "…"
            bits.append(f"= {value!r}")
        if current.truncated:
            bits.append("… (truncated)")
        lines.append(prefix + " ".join(bits))
    return "\n".join(lines)


# -- matching ---------------------------------------------------------------

def match_pattern(value: str | None, pattern: str) -> bool:
    """Case-insensitive fnmatch with "|" alternation, against a maybe-None."""
    if value is None:
        return False
    value = str(value).lower()
    return any(fnmatch.fnmatch(value, p.strip().lower()) for p in pattern.split("|"))


def match_role(role: str | None, pattern: str) -> bool:
    """Role matching, with macOS's "AX" prefix optional.

    So `role="Button"` finds an AXButton on macOS and a Button on Windows,
    while `role="AXButton"` still means what it says.  This is as far as role
    unification honestly goes - a macOS AXTextField really is a Windows Edit,
    and no amount of aliasing makes those two names one vocabulary.
    """
    if role is None:
        return False
    if match_pattern(role, pattern):
        return True
    stripped = role[2:] if role.startswith("AX") else role
    return match_pattern(stripped, pattern)


def _first_name(*candidates) -> tuple[str | None, str | None]:
    """The first non-empty name among ``(source, text)`` pairs, with the source
    that answered.  ``(None, None)`` when an element offers no name at all -
    which is a fact worth carrying, not a blank to paper over: such an element
    cannot be addressed by name, only by role and position.
    """
    for source, text in candidates:
        if text:
            return str(text), source
    return None, None
