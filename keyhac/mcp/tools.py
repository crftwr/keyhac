"""The tools the agent gets, and why these ones.

They follow the escalation ladder in doc/dev/ai-integration.md §8.1: read the
screen that is already on it, ask the operator to open the state that is not,
and only then anything more expensive. `describe_screen` is rung 1 and the tool
everything else depends on - an action written without looking at the tree is
written against a remembered screen.

`run_action` is the other load-bearing one (§8.3). Without it the human is the
transport for every error message: they run the action, copy the traceback,
paste it back. With it the generate-verify loop closes, and loop iteration rate
is what the whole authoring approach lives or dies on.

`write_extension` is the only tool that writes, and it is fenced rather than
free: inside `extensions/` only, and over a module-shaped name only. It exists
because the operator was otherwise the transport for every iteration of every
fix - and because the manual step it replaces was never the review it
resembled; nobody reads what they paste.

`delete_extension` is that same fence, run in reverse, and it is a **rename**
rather than an unlink: a module leaves `extensions/` as a timestamped `.bak-`
beside itself. So the one tool here that sounds destructive destroys nothing,
and the directory a fix loop leaves cluttered can be tidied without the operator
being the transport for that either.

`keyhac/mcp/extensions.py` is the other half of that: an action class in
`extensions/` is runnable by `module.Class` without any `config.py` edit, and
those classes are the whole of the action surface. There is no registry of named
actions to consult first - `register_action` existed to add a `config.py` line
that nothing needs any more.

**Nothing here checks a permission**, and that is deliberate rather than
missing. The endpoint being reachable *is* the permission: it listens only
while the operator's switch is on, and that switch closes itself an hour after
they tick it (`Keymap.start_mcp_server`). Splitting it into a second, finer
switch was tried and undone - it left reading every open window as the
long-lived half and writing as the short-lived one, which is backwards, since
`describe_screen` is by far the larger exposure. So the statement of what a
running endpoint grants is short and whole: while it is open, code the operator
has not read can be written, run and taken back out again, and every window they
have open can be read. Nothing types text or presses a key.

THREADS. Element access is main-thread-only and these run on the MCP server's
threads, so every tool that touches the UI goes through `ui.on_main_thread` -
directly, or through the node methods, which dispatch themselves.
"""

from __future__ import annotations

import difflib
import keyword
import logging
import os
import re
import shutil
import sys
import threading
import time

from keyhac.core import capture, log, uitree
from keyhac.core.focus import FOCUS_PATH_TRANS_TABLE
from keyhac.mcp import extensions

logger = log.getLogger("MCP")

#: Node budget for a screen dump.  Smaller than the library default: this text
#: goes into a model's context, and a 1000-node tree is mostly furniture.
#: Measured against real windows, 400 nodes of Finder costs ~1,700 tokens - a
#: whole window for the price of a short file, which is the budget this is set
#: to keep.
DEFAULT_MAX_NODES = 400

#: Below this many elements under a web area, the page is not exposed - the
#: shell is there and the document is not.
#:
#: Set from measurement rather than instinct, after two guesses missed. With
#: content access off on this machine, VS Code's largest web area holds 30
#: elements and Claude's 14; with it on, Chrome went from 59 elements to 119
#: for a trivial page, and a real application's page runs to hundreds. So the
#: gap this sits in is wide, and 40 sits in it with room on both sides.
EMPTY_WEB_AREA = 40

#: A window with fewer elements than this has nothing in it at all.
EMPTY_WINDOW = 5

#: Ceiling on what `read_extension` returns, in bytes.  Generous: the
#: largest action written so far is about 15 KB, so this is four times the real
#: range and trips only on something that is not a hand-written action.
MAX_SOURCE = 64 * 1024


def _listdir(directory: str) -> list[str]:
    try:
        return os.listdir(directory)
    except OSError:
        return []


def _truncation_shape(nodes: list, max_depth: int, max_nodes: int) -> str:
    """The shape of what a bounded walk cut off, so the next call can be
    sized instead of guessed (issue #54).

    Which bound did the cutting is readable off each cut point: a node
    truncated at `max_depth` was cut by the depth bound, anywhere shallower
    by the node budget.  The walker already knew all of these numbers; this
    just stops discarding them.
    """
    cut = [node for node in nodes if node.truncated]
    by_depth = sum(1 for node in cut if node.depth >= max_depth)
    by_budget = len(cut) - by_depth
    bounds = []
    if by_depth:
        bounds.append(f"{by_depth} by max_depth={max_depth}")
    if by_budget:
        bounds.append(f"{by_budget} by the max_nodes={max_nodes} budget")
    return (f"reported {len(nodes)} node(s), cut off at {len(cut)} point(s) "
            f"({', '.join(bounds)}); deepest level reached: "
            f"{max(node.depth for node in nodes)}")


def _portable_role_spelling(pattern: str) -> str | None:
    """The unprefixed spelling of an "AX"-prefixed role pattern, or None.

    "AX" is macOS vocabulary: an AX-prefixed pattern matches only macOS
    roles, while the unprefixed spelling matches the AX name there too, so
    it is the portable one (issue #69).  Purely syntactic - this reads the
    pattern, never the tree, so the no-match path stays a single walk
    (issue #76).  A wildcard-led remainder ("AX*") is left alone: stripping
    it would turn "every AX-prefixed role" into "everything".
    """
    branches = [b.strip() for b in pattern.split("|")]
    stripped = [b[2:] if (b[:2].lower() == "ax" and len(b) > 2
                          and b[2] not in "*?[") else b
                for b in branches]
    if stripped == branches:
        return None
    return "|".join(stripped)


#: Ceiling on a name used as a path qualifier - one line per match has to
#: stay readable, and the qualifier exists to split twins, not to quote them.
PATH_NAME_CHARS = 30


def _path_segment(node) -> str:
    """One ancestor as role(qualifier) - at most one qualifier.

    The identifier when there is one, else the name, else the bare role:
    the path exists to tell two same-role containers apart (issue #55),
    not to re-dump the tree, so one discriminating fact per level is the
    budget.
    """
    role = node.role or "?"
    if node.identifier:
        return f"{role}(#{node.identifier})"
    if node.name:
        name = str(node.name)
        if len(name) > PATH_NAME_CHARS:
            name = name[:PATH_NAME_CHARS] + "…"
        return f"{role}({name})"
    return role


def _ancestor_path(node) -> str:
    """A match's ancestors, root-first, slash-joined; "" on the root itself.

    Read off the same walk that found the match - the `_parent` back-edges
    `get_ui_tree` records - so it describes the searched snapshot, not a
    second one.  Where the DAG dedupe applies, a shared node's chain is the
    side the walk reached first, which is the honest description of the
    reported tree.
    """
    segments = []
    current = getattr(node, "_parent", None)
    while current is not None:
        segments.append(_path_segment(current))
        current = getattr(current, "_parent", None)
    return "/".join(reversed(segments))


def _running_action(name: str):
    """A running action filed under `name`, whoever started it.

    `ThreadedAction._running` is the set Esc reaches, so it holds key-started
    actions the loader has never seen. Matched on the same name the run record
    was filed under, which is what makes the two ways of starting an action
    answer to one name at all.
    """
    from keyhac.core.action import ThreadedAction

    with ThreadedAction._running_lock:
        running = tuple(ThreadedAction._running)
    for action in running:
        if getattr(action, "_run_record", None) is not None \
                and action._run_record.name == name:
            return action
    return None


class Tool:
    """One callable, its JSON Schema, and the description the agent reads."""

    def __init__(self, name, description, schema, run):
        self.name = name
        self.description = description
        self.schema = schema
        self.run = run

    def describe(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": self.schema}


class ToolRegistry:
    """The tool set, bound to one Keymap."""

    def __init__(self, keymap):
        self.keymap = keymap
        self._loader = extensions.Loader()
        self.tools = {t.name: t for t in self._build()}

    def describe(self) -> list[dict]:
        return [tool.describe() for tool in self.tools.values()]

    def call(self, name: str, arguments: dict) -> str:
        tool = self.tools.get(name)
        if tool is None:
            raise KeyError(f"unknown tool {name!r} "
                           f"(have: {', '.join(sorted(self.tools))})")
        return tool.run(**arguments)

    # -- the tools -----------------------------------------------------------

    def _build(self) -> list[Tool]:
        string = {"type": "string"}
        integer = {"type": "integer"}
        window_args = {
            "app": {**string, "description": "Application name pattern "
                    "(fnmatch, '|' alternation, case-insensitive). Omitted "
                    "means the focused application."},
            "title": {**string, "description": "Window title pattern."},
        }
        return [
            Tool("list_windows",
                 "List the open windows: application, title, and whether each "
                 "is the focused one. Start here when you do not know what to "
                 "address.",
                 {"type": "object", "properties": {}},
                 self.list_windows),
            Tool("get_focus",
                 "What currently has keyboard focus: application, window "
                 "title, and the focus path. Cheap, and usually tells you "
                 "which window the operator means.",
                 {"type": "object", "properties": {}},
                 self.get_focus),
            Tool("describe_screen",
                 "Read a window's element tree as indented text - roles, "
                 "names, identifiers and values. **Do this before writing any "
                 "action**: selectors written from memory address a screen "
                 "that may not exist. If the state you need is not on screen "
                 "(a modal, a later wizard step), ask the operator to open it "
                 "and call this again.",
                 {"type": "object", "properties": {
                     **window_args,
                     "max_depth": {**integer, "description": "How deep to "
                                   "walk (default 14)."},
                     "max_nodes": {**integer, "description":
                                   f"Node budget (default {DEFAULT_MAX_NODES})."},
                     "roles": {**string, "description": "Only report elements "
                               "whose role matches this pattern."}}},
                 self.describe_screen),
            Tool("find_elements",
                 "Search a window for elements matching role / name / value / "
                 "identifier / text, and report what each one is, with its "
                 "ancestor path - which is how to tell two same-role "
                 "containers apart. Use when a full tree is more than you "
                 "need, or to check that a selector you are about to write "
                 "actually matches.",
                 {"type": "object", "properties": {
                     **window_args,
                     "role": {**string, "description": "Role pattern. The AX "
                              "prefix may be dropped in the pattern ('Row' "
                              "matches AXRow); an AX-prefixed pattern matches "
                              "only macOS roles."},
                     "name": {**string, "description": "Label pattern."},
                     "value": {**string, "description": "Content pattern."},
                     "identifier": {**string, "description":
                                    "DOM id / AutomationId pattern."},
                     "text": {**string, "description":
                              "Matches label and content together."},
                     "limit": {**integer, "description": "Maximum matches "
                               "to report (default 20)."},
                     "max_depth": {**integer, "description": "How deep to "
                                   f"search (default {uitree.DEFAULT_MAX_DEPTH}"
                                   "). Web content can nest controls 30+ "
                                   "levels down, and a search cut short by "
                                   "this bound reports the same 'no element "
                                   "matching' as a genuinely absent element - "
                                   "raise it before concluding one is not "
                                   "there."},
                     "max_nodes": {**integer, "description": "Node budget for "
                                   "the search (default "
                                   f"{uitree.DEFAULT_MAX_NODES})."}}},
                 self.find_elements),
            Tool("read_text",
                 "Read an element's whole text content - a terminal's "
                 "scrollback, an editor buffer, a log block. The control tree "
                 "does not reach into text, so this is the only way to get at "
                 "it; extract what you need from the result with a regex.",
                 {"type": "object", "properties": {
                     **window_args,
                     "identifier": {**string, "description":
                                    "Identifier of the element to read."},
                     "role": {**string, "description": "Role of the element "
                              "to read, if it has no identifier."},
                     "name": {**string, "description": "Label of the element "
                              "to read."}}},
                 self.read_text),
            Tool("enable_content_access",
                 "Ask a Chromium or Electron application (Chrome, Edge, VS "
                 "Code, Slack) to expose its content. On macOS they build no "
                 "accessibility tree until asked - a loaded page reads as "
                 "browser chrome with no document in it. Call this when "
                 "describe_screen shows a browser with no page in it. Windows "
                 "needs nothing equivalent and reports that it did nothing.",
                 {"type": "object", "properties": {
                     **window_args,
                     "enable": {"type": "boolean", "description":
                                "False to turn it back off when done."}}},
                 self.enable_content_access),
            Tool("list_actions",
                 "The action classes in ~/.keyhac/extensions/ - addressed as "
                 "module.Class - which are running now, and how the last run "
                 "of each ended. They need no config.py entry to be listed or "
                 "run; the operator's edit comes later, to put a working one "
                 "on a key. Found by reading the files, so listing never "
                 "executes them.",
                 {"type": "object", "properties": {}},
                 self.list_actions),
            Tool("start_action",
                 "Start an action class and return immediately. **It is not "
                 "finished when this returns** - call get_action_result for "
                 "what it did. Actions here drive real applications and can "
                 "take minutes, which is why starting and collecting are "
                 "separate steps. Takes a module.Class from list_actions, and "
                 "re-imports the file when it has changed - so write_extension "
                 "then start_action needs no reload_config in between.",
                 {"type": "object", "properties": {
                     "name": {**string, "description":
                              "module.Class, from list_actions."}},
                  "required": ["name"]},
                 self.start_action),
            Tool("get_action_result",
                 "Wait for an action to finish and return everything it "
                 "logged or printed, with the traceback if it raised. Blocks "
                 "up to `wait` seconds; if it is still running when that is "
                 "up, that is the answer - call again. This is how you check "
                 "your own work: write it, start it, read what happened, fix "
                 "it, write it again.",
                 {"type": "object", "properties": {
                     "name": {**string, "description":
                              "module.Class, from list_actions."},
                     "wait": {**integer, "description":
                              "Seconds to wait for it to finish (default 30, "
                              "0 to look without waiting)."},
                     "level": {**string, "description":
                               "Lowest log severity to return: DEBUG, INFO, "
                               "WARNING or ERROR (default INFO, which hides "
                               "the per-keystroke DEBUG stream; print() "
                               "output always comes through)."},
                     "tail": {**integer, "description":
                              "Only the last N lines of the output (default: "
                              "all of it)."}},
                  "required": ["name"]},
                 self.get_action_result),
            Tool("cancel_action",
                 "Stop a running action - the same thing the operator's Esc "
                 "does. It unwinds rather than being killed, so progress it "
                 "has already recorded stays recorded.",
                 {"type": "object", "properties": {
                     "name": {**string, "description":
                              "module.Class, from list_actions."}},
                  "required": ["name"]},
                 self.cancel_action),
            Tool("describe_keymap",
                 "The key tables this configuration defined: their conditions, "
                 "which ones match the current focus, and what each binds. "
                 "**This is how you check a key binding**, because nothing here "
                 "can press a key - it tells you the binding landed in the "
                 "table you meant and that the table applies where the operator "
                 "is; only whether pressing it feels right is left to them. It "
                 "also reports the live focus path, which is what a "
                 "focus_path_pattern is written against.",
                 {"type": "object", "properties": {}},
                 self.describe_keymap),
            Tool("read_config",
                 "Read ~/.keyhac/config.py - the operator's own configuration: "
                 "key tables, bindings, and the lines that wire up actions. "
                 "Read it before proposing any change to it.",
                 {"type": "object", "properties": {}},
                 self.read_config),
            Tool("write_config",
                 "Replace ~/.keyhac/config.py, keeping a backup. Separate from "
                 "write_extension because the stakes differ: a module in "
                 "extensions/ is inert until something names it, while this "
                 "file runs at every start and takes the operator's key "
                 "bindings down with it if it is wrong. **Read it first** - "
                 "this replaces the whole file, and it is theirs. It is also "
                 "the only thing you can do that outlives the endpoint's hour, "
                 "so say what you changed.",
                 {"type": "object", "properties": {
                     "source": {**string, "description": "The complete file."}},
                  "required": ["source"]},
                 self.write_config),
            Tool("list_extensions",
                 "The files in ~/.keyhac/extensions/, with what each one "
                 "holds. list_actions shows what can be *run*; this shows what "
                 "is on disk, including helper modules that define no action "
                 "class and so never appear there.",
                 {"type": "object", "properties": {}},
                 self.list_extensions),
            Tool("read_extension",
                 "Read a module in ~/.keyhac/extensions/ - the whole file, as "
                 "it is on disk right now. **Call this before changing an "
                 "action you did not write in this conversation**: "
                 "write_extension replaces the entire file, so editing one you "
                 "have not read means reconstructing it from a guess and "
                 "silently dropping whatever you did not know was in it. Also "
                 "reads helper modules the action imports, which list_actions "
                 "does not show.",
                 {"type": "object", "properties": {
                     "name": {**string, "description": "Module name, with no "
                              "path and no .py suffix: \"open_issues\"."}},
                  "required": ["name"]},
                 self.read_extension),
            Tool("write_extension",
                 "Save an action module into ~/.keyhac/extensions/ - the whole "
                 "file, replacing whatever is there and keeping a backup. This "
                 "is how you close the loop yourself: write, start_action, "
                 "get_action_result, write again - no config.py edit and no "
                 "reload in between. It replaces rather than patches, so call "
                 "read_extension first if you did not write the current "
                 "contents.",
                 {"type": "object", "properties": {
                     "name": {**string, "description": "Module name, with no "
                              "path and no .py suffix: \"open_issues\"."},
                     "source": {**string, "description": "The complete file. "
                                "Partial content replaces the whole module, "
                                "so send all of it every time."}},
                  "required": ["name", "source"]},
                 self.write_extension),
            Tool("delete_extension",
                 "Remove a module from ~/.keyhac/extensions/ - **renamed to a "
                 "timestamped .bak- beside it rather than unlinked**, so the "
                 "operator can put it back. For what an authoring session "
                 "leaves behind: a module written under the wrong name, a "
                 "helper that got folded back into the action. It does not "
                 "touch config.py - so if that file imports the module, the "
                 "reply says so, and taking those lines out is the next thing "
                 "to offer.",
                 {"type": "object", "properties": {
                     "name": {**string, "description": "Module name, with no "
                              "path and no .py suffix: \"open_issues\"."}},
                  "required": ["name"]},
                 self.delete_extension),
            Tool("reload_config",
                 "Reload ~/.keyhac/config.py and report what it said - use it "
                 "**after the operator edits that file**, to check their paste "
                 "still loads and to hand back the error if it does not. "
                 "**Not part of the authoring loop**: an action class in "
                 "extensions/ is re-read whenever its file changes, so calling "
                 "this between rounds only rebuilds the operator's live key "
                 "bindings for nothing.",
                 {"type": "object", "properties": {}},
                 self.reload_config),
        ]

    # -- implementations -----------------------------------------------------

    @property
    def ui(self):
        return self.keymap.ui

    def _window(self, app=None, title=None):
        """The window a tool should act on, or a clear error saying why not."""
        if app is None and title is None:
            node = self.ui.focused()
            if node is None:
                raise RuntimeError(
                    "nothing has keyboard focus; pass app= or title=, or call "
                    "list_windows")
            return node
        window = self.ui.window(app=app, title=title)
        if window is None:
            raise RuntimeError(
                f"no window matching app={app!r} title={title!r}; "
                f"call list_windows to see what is open")
        return window

    def list_windows(self) -> str:
        # On macOS Focus.window_title is captured off the focus-path walk and
        # arrives transliterated (FOCUS_PATH_TRANS_TABLE: "(" -> "<", ":" ->
        # "-", ...); enumerated window titles are raw.  Normalizing both sides
        # makes the comparison mean "same window" on both OSes - the table
        # maps onto characters it never maps from, so re-applying it to an
        # already-escaped title changes nothing.  Issue #73.
        def normalize(title):
            return title.translate(FOCUS_PATH_TRANS_TABLE) if title else title

        def read():
            focus = self.keymap.focus
            focused = ((focus.app_name, normalize(focus.window_title))
                       if focus else (None, None))
            lines = []
            for window in self.keymap.list_windows():
                mark = ("*" if (window.app_name, normalize(window.title)) == focused
                        else " ")
                lines.append(f"{mark} {window.app_name or '?'}: "
                             f"{window.title or '(untitled)'}")
            return lines

        lines = self.ui.on_main_thread(read)
        if not lines:
            return "no windows"
        return "\n".join(["('*' marks the focused window)"] + lines)

    def get_focus(self) -> str:
        def read():
            focus = self.keymap.focus
            if focus is None:
                return None
            return (f"application: {focus.app_name}\n"
                    f"window: {focus.window_title}\n"
                    f"path: {focus.path}")

        return self.ui.on_main_thread(read) or "nothing focused"

    def describe_screen(self, app=None, title=None, max_depth=14,
                        max_nodes=DEFAULT_MAX_NODES, roles=None) -> str:
        max_depth, max_nodes = int(max_depth), int(max_nodes)
        window = self._window(app, title)
        tree = window.reread(max_depth=max_depth, max_nodes=max_nodes,
                             roles=roles)
        nodes = list(tree.walk())
        text = tree.dump()

        # Order matters, and the first case is the one that would otherwise
        # send a model down a dead end. A Chromium or Electron window with its
        # content switched off still reports its *web area* - the shell is
        # there, the document is not - and marks nodes truncated. A model
        # reading only the truncation note raises max_nodes, gets the identical
        # tree back, and concludes the application has no accessible UI. The
        # discriminator is not how small the window is (measured: 43 elements
        # for all of VS Code) but how little hangs off the web area.
        hollow = [area for area in nodes
                  if (area.role or "").endswith("WebArea")
                  and len(list(area.walk())) < EMPTY_WEB_AREA]
        if hollow:
            text += (f"\n\n[this window has a web area with almost nothing in "
                     f"it, which is what a Chromium or Electron application "
                     f"(Chrome, Edge, VS Code, Slack, Claude) looks like before "
                     f"it is asked to expose its content: call "
                     f"enable_content_access and read it again. Raising "
                     f"max_nodes will not help.]")
        elif len(nodes) < EMPTY_WINDOW:
            text += (f"\n\n[only {len(nodes)} element(s) - this window exposes "
                     f"essentially nothing. Check list_windows for a better "
                     f"target.]")
        elif any(node.truncated for node in nodes):
            # With the shape of the cut, not just the fact of one (issue #54):
            # "raise max_nodes" without saying from what left the next value a
            # guess, and a measured session guessed twice.
            text += (f"\n\n[truncated: "
                     f"{_truncation_shape(nodes, max_depth, max_nodes)}. "
                     f"Raise the bound that did the cutting, or narrow with "
                     f"roles=, before concluding this is the whole screen]")
        return text

    def find_elements(self, app=None, title=None, limit=20, max_depth=None,
                      max_nodes=None, **criteria) -> str:
        criteria = {k: v for k, v in criteria.items() if v is not None}
        if not criteria:
            raise ValueError("give at least one of role/name/value/"
                             "identifier/text")
        window = self._window(app, title)
        bounds = {}
        if max_depth is not None:
            bounds["max_depth"] = int(max_depth)
        if max_nodes is not None:
            bounds["max_nodes"] = int(max_nodes)
        matches = window.find_all(**criteria, **bounds)
        if not matches:
            # Deliberately plain. This cannot tell "absent" from "not within
            # the bounds" - the walked tree and its truncation marks die
            # inside find_all, and the runtime diagnostic that re-walked the
            # tree here to recover them was reverted: a second live walk,
            # describing a second snapshot the search never saw. The
            # ambiguity is accepted and taught in the max_depth description
            # instead; issue #76 holds the candidate real fix.
            text = f"no element matching {criteria}"
            role = criteria.get("role")
            suggested = (_portable_role_spelling(role)
                         if isinstance(role, str) else None)
            if suggested:
                text += (f'\n\n[an "AX"-prefixed role pattern only matches '
                         f'macOS roles - the unprefixed spelling matches the '
                         f'AX name too and is the portable one: try '
                         f'role="{suggested}". On macOS, also raise max_depth '
                         f'before concluding the element is not there]')
            return text
        lines = [f"{len(matches)} match(es):"]
        for node in matches[:int(limit)]:
            line = f"  {node!r} value={node.value!r} rect={node.rect}"
            path = _ancestor_path(node)
            if path:
                line += f" path={path}"
            lines.append(line)
        if len(matches) > int(limit):
            lines.append(f"  ... and {len(matches) - int(limit)} more")
        return "\n".join(lines)

    def read_text(self, app=None, title=None, **criteria) -> str:
        criteria = {k: v for k, v in criteria.items() if v is not None}
        window = self._window(app, title)
        node = window.find(**criteria) if criteria else window
        if node is None:
            raise RuntimeError(f"no element matching {criteria}")
        text = node.read_text()
        if not text:
            return (f"{node!r} has no text content "
                    f"(its value is {node.value!r})")
        return text

    def enable_content_access(self, app=None, title=None, enable=True) -> str:
        window = self._window(app, title)
        did = self.ui.enable_content_access(window, bool(enable))
        if not did:
            return ("this platform needs no accessibility switch "
                    "(Windows enables the renderer tree when a UIA client "
                    "attaches); call describe_screen again and retry if the "
                    "content is not there yet")
        return (f"content access {'enabled' if enable else 'disabled'}; "
                f"call describe_screen again to see the difference")

    def _state(self, name: str, running) -> str:
        run = capture.get_run(name)
        if name in running:
            return f"RUNNING for {run.seconds:.0f}s"
        if run is not None:
            return f"last run: {run.status}"
        return "not run yet"

    def list_actions(self) -> str:
        """What is in `extensions/`."""
        # Read out of the files, never imported, so this costs nothing and
        # executes nothing - see keyhac/mcp/extensions.py.
        found = extensions.discover(self.keymap.extensions_dir)
        if not found:
            return (f"no action classes in {self.keymap.extensions_dir}. "
                    f"write_extension puts one there; it needs to subclass "
                    f"ThreadedAction to be found.")
        running = capture.running_names()
        lines = ["action classes in extensions/:"]
        for action in found:
            lines.append(f"{action.describe()} - "
                         f"{self._state(action.name, running)}")
        return "\n".join(lines)

    def _action(self, name: str):
        """Resolve a name for *reading* - results, cancellation.

        Deliberately ungated and import-free: a run that started while the
        window was open must stay readable after it closes, or the model loses
        the traceback for the thing it just ran.

        Two places to look, because there are two ways to start one. The
        loader's cache holds what `start_action` made; a run the operator
        triggered with a key was made by their `config.py` and is reachable
        only while it is running, through the set the Esc key uses. Without the
        second, `cancel_action` could not stop an action the operator started -
        which is the half of issue #42 that survived naming the runs alike.
        """
        action = self._loader.cached(name)
        if action is None:
            action = _running_action(name)
        if action is None:
            raise KeyError(f"{name!r} has not been started; call list_actions")
        return action

    def _startable(self, name: str):
        """Resolve a name for *running*.

        No permission check: the endpoint being reachable at all is the
        permission. It listens only while the operator's switch is on, and the
        switch closes itself - see `Keymap.start_mcp_server`.
        """
        found = {action.name: action
                 for action in extensions.discover(self.keymap.extensions_dir)}
        action = found.get(name)
        if action is None:
            raise KeyError(f"no action class named {name!r}; call list_actions")
        if action.required:
            raise KeyError(
                f"{name} takes constructor arguments with no default "
                f"({', '.join(action.required)}), so it cannot be started from "
                f"here. Give them defaults - the operator can still pass other "
                f"values where they bind it to a key.")
        # Re-imports when the file has moved, so write_extension followed by
        # start_action runs what was just written, with no reload_config
        # between them.
        return self._loader.instantiate(action)

    def start_action(self, name: str) -> str:
        """Start it and get out of the way.

        Asynchronous by design rather than because the transport forced it.
        The endpoint answers one JSON message per request with no stream to
        push progress over, and §2's actions run for minutes - so a call that
        waited for the end would be a call that times out for exactly the
        class of work this exists to serve. Two shapes of reply depending on
        how fast the action happened to be would be worse still: the branch
        would hinge on the least predictable thing there is.
        """
        action = self._startable(name)
        if capture.get_run(name) is not None and capture.get_run(name).running:
            return (f"{name} is already running - get_action_result to watch "
                    f"it, or cancel_action to stop it.")

        # Always a ThreadedAction: that is what the scan matches on, and what
        # instantiate() confirms. So `cancellable` opens the run record, files
        # the traceback and closes it - the same path a key press takes, which
        # is what makes a run started from here and one started by a key report
        # identically.
        def body():
            try:
                with action.cancellable(name):
                    self.ui.on_main_thread(action.starting)
                    result = action.run()
                    self.ui.on_main_thread(lambda: action.finished(result))
            except BaseException:                         # noqa: BLE001
                # Recorded by cancellable() on the way out; re-raising here
                # would only reach a daemon thread nobody is watching.
                pass

        threading.Thread(target=body, name=f"mcp-{name}", daemon=True).start()
        return (f"{name} started. Call get_action_result to see what it did.")

    def get_action_result(self, name: str, wait: int = 30,
                          level: str = "INFO", tail=None) -> str:
        # No name resolution first: the run record is the whole answer, and a
        # class that has been listed but never started should read as "not run"
        # rather than as an unknown name. It also keeps a result readable after
        # the authoring window has closed under a run that already happened.
        run = capture.wait_for_run(name, float(wait))
        if run is None:
            return (f"{name} has not been run since Keyhac started. "
                    f"start_action runs it.")
        # INFO by default (issue #71): an action typing its way through a form
        # leaves thousands of keymap DEBUG lines around the two INFO lines
        # that carry the result, and the model reading this pays for all of
        # them. The cut announces itself, so the DEBUG stream is one call away.
        return run.report(level=level,
                          tail=int(tail) if tail is not None else None)

    def cancel_action(self, name: str) -> str:
        action = self._action(name)
        run = capture.get_run(name)
        if run is None or not run.running:
            return f"{name} is not running."
        flag = getattr(action, "_cancel_flag", None)
        if flag is None:
            return (f"{name} is running but cannot be cancelled - only a "
                    f"ThreadedAction can be, and this is not one.")
        flag.set()
        return (f"asked {name} to stop. It unwinds at its next wait, so "
                f"get_action_result for how far it got.")

    def describe_keymap(self) -> str:
        return self.keymap.describe_keymap()

    def read_config(self) -> str:
        """The operator's whole `config.py`."""
        return self._read_file(self.keymap.config_path)

    def write_config(self, source: str) -> str:
        """Replace `config.py`, having been asked to.

        **A separate tool from `write_extension`, deliberately.** The two have
        different blast radii and the tool list is where that should be
        visible: a module in `extensions/` is inert until something names it,
        while `config.py` runs at every start and is what stops working if it
        is wrong - taking the operator's key bindings with it. Folding both
        into one call would hide that behind an argument.

        It is also the one thing here that **outlives the endpoint's hour**.
        Everything else an agent does expires with the window; a key binding
        written here keeps working, which is the point of writing it and worth
        saying out loud.
        """
        return self._write_file(self.keymap.config_path, source, "config.py")

    def list_extensions(self) -> str:
        """Every `.py` in `extensions/`, and what each one holds.

        The file view, where `list_actions` is the runnable view. They differ
        on purpose: a helper module split out beside an action has no class to
        run and never appears in the other list, which makes it invisible to
        anything trying to maintain the pair.
        """
        directory = self.keymap.extensions_dir
        files = sorted(e for e in _listdir(directory) if e.endswith(".py"))
        if not files:
            return f"no .py files in {directory}"

        classes = {}
        for action in extensions.discover(directory):
            classes.setdefault(action.module, []).append(action.class_name)

        lines = [f"files in {directory}:"]
        for entry in files:
            module = entry[:-3]
            try:
                size = os.path.getsize(os.path.join(directory, entry))
            except OSError:
                size = 0
            if module in classes:
                note = ", ".join(classes[module])
            elif entry.startswith("_"):
                note = "helper - not offered as an action"
            else:
                note = "no ThreadedAction subclass"
            lines.append(f"{entry}  {size} bytes - {note}")
        return "\n".join(lines)

    def _module_path(self, name: str) -> str:
        """`<extensions>/<name>.py`, or raise if `name` is not a module name.

        The same fence both ways: an importable name has no separator and no
        `..` in it, so validating it as an identifier confines reads and writes
        to the directory by construction.
        """
        if not isinstance(name, str) or not name.isidentifier() \
                or keyword.iskeyword(name):
            raise ValueError(
                f"{name!r} cannot be a module name. Pass the bare name - no "
                f"directory, no .py - and make it importable: letters, digits "
                f"and underscores, not starting with a digit.")
        return os.path.join(self.keymap.extensions_dir, name + ".py")

    def _read_file(self, path: str) -> str:
        try:
            size = os.path.getsize(path)
        except OSError as error:
            return f"could not read {path}: {error}"
        if size > MAX_SOURCE:
            # Refused rather than truncated, because the caller's next move is
            # a whole-file write: half a file read is how you lose the other
            # half.
            return (f"{path} is {size} bytes, over the {MAX_SOURCE} this "
                    f"returns. Refusing rather than truncating: the write "
                    f"tools replace whole files, so acting on half of one "
                    f"would drop the rest. Ask the operator to edit it by "
                    f"hand.")
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except OSError as error:
            return f"could not read {path}: {error}"

    def _write_file(self, path: str, source: str, what: str) -> str:
        try:
            compile(source, path, "exec")
        except SyntaxError as error:
            return (f"nothing written: that source does not parse - "
                    f"{error.msg}, line {error.lineno}. The file on disk is "
                    f"untouched; send the whole file again.")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            previous = None
            if os.path.exists(path):
                with open(path, encoding="utf-8") as handle:
                    previous = handle.read()
                shutil.copyfile(path, path + time.strftime(".bak-%Y%m%d-%H%M%S"))
                _prune_backups(path)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(source)
        except OSError as error:
            return f"nothing written: {error}"

        summary = _change_summary(previous, source)
        logger.info(f"Wrote {path} ({summary}).")
        return f"wrote {what} ({summary})."

    def read_extension(self, name: str) -> str:
        """The whole of one module in `extensions/`.

        The counterpart `write_extension` needed and did not have. That tool
        replaces a file rather than patching it, so an agent asked to change an
        action it has not read has to reconstruct the module from its own
        guess - and what it did not guess is gone, quietly, with only the
        backup to show for it. Read-then-write is the only safe shape, and this
        is the read.
        """
        path = self._module_path(name)
        if not os.path.exists(path):
            return self._no_module(name)
        return self._read_file(path)

    def _no_module(self, name: str) -> str:
        """What a tool says about a module that is not there.

        With the directory listed, because the near-misses are what this is
        usually hit by: a module addressed by its class name, or by the name it
        had before it was rewritten.
        """
        modules = sorted(
            entry[:-3] for entry in _listdir(self.keymap.extensions_dir)
            if entry.endswith(".py"))
        return (f"no {name}.py in {self.keymap.extensions_dir}"
                + (f". There is: {', '.join(modules)}" if modules
                   else " - the directory is empty."))

    def write_extension(self, name: str, source: str) -> str:
        """Save a module into ``extensions/``, while the window is open.

        **The module name is the fence.** An importable name has no separator
        and no ``..`` in it, so validating it as a Python identifier confines
        the write by construction - there is no list of dangerous characters to
        keep complete, and no path to normalise and re-check.

        **Syntax is checked before the file is touched.** A truncated transfer
        would otherwise replace a working action with one that cannot be
        imported, and the operator would meet it as a config that stopped
        loading. Refusing costs the model one retry and costs them nothing.
        """
        try:
            path = self._module_path(name)
        except ValueError as error:
            return f"nothing written: {error}"

        # The console line _write_file logs is the audit trail, and it answers
        # the one thing these tools add that pasting did not: a file can arrive
        # without appearing in the conversation. An operator who sees a module
        # they did not ask for scroll past has been told.
        result = self._write_file(path, source, path)
        if result.startswith("wrote "):
            return result[:-1] + " - start_action picks it up as soon as it "\
                                 "is named, no reload needed."
        return result

    def delete_extension(self, name: str) -> str:
        """Retire a module from `extensions/` - by renaming, never unlinking.

        **The same move `write_extension` already makes on every replace**,
        applied to the last version instead of a superseded one: the file goes
        to a timestamped `.bak-` beside itself, under the same five-deep bound.
        The argument for keeping backups there holds harder here - a replaced
        module at least leaves its successor to read the intent off, and a
        deleted one leaves nothing - so this must not be the single operation
        that cannot be walked back.

        **`config.py` is untouched, and that is where a delete can still
        hurt.** A module the operator has bound to a key is imported by their
        file at every load, so removing it stops that file loading - and the
        failure surfaces as key bindings that quietly stopped working, well
        after this call and nowhere near it. Doing it anyway and *saying so* is
        the right shape: their file is edited by `write_config`, with them in
        the conversation, not as a side effect of tidying `extensions/`.

        **Live state is deliberately left alone.** A class already imported
        keeps running out of memory, so an action started before this stays
        readable through `get_action_result` and stoppable through
        `cancel_action`, and the operator's key goes on working until they
        reload. Nothing here is trying to make the deletion take effect faster
        than the file system says it did.
        """
        try:
            path = self._module_path(name)
        except ValueError as error:
            return f"nothing deleted: {error}"
        if not os.path.exists(path):
            return self._no_module(name)

        backup = path + time.strftime(".bak-%Y%m%d-%H%M%S")
        try:
            # replace() rather than rename(): the suffix has one-second
            # resolution, so writing a module and deleting it inside the same
            # second collides - and on Windows rename() would raise there,
            # turning a bounded loss (one backup of two, holding versions a
            # second apart) into a refusal.
            os.replace(path, backup)
        except OSError as error:
            return f"nothing deleted: {error}"
        _prune_backups(path)

        # Same reason write_extension logs: a file can leave the directory
        # without appearing in the conversation, and the console line is what
        # tells the operator watching it that one did.
        logger.info(f"Deleted {path} (kept {os.path.basename(backup)}).")
        return (f"deleted {name}.py - kept as {os.path.basename(backup)} "
                f"beside it, so renaming that back undoes this."
                + self._config_mentions(name))

    def _config_mentions(self, name: str) -> str:
        """A warning to hang off a delete when `config.py` names the module.

        Searched rather than parsed, and deliberately loose: `import thing`,
        `from thing import Action` and a `thing.Action()` on a binding line are
        all the same signal, and the two errors are not symmetric. A false
        positive costs a sentence the model reads and drops; a false negative
        costs the operator a `config.py` that stops loading, hours later.
        """
        try:
            with open(self.keymap.config_path, encoding="utf-8") as handle:
                source = handle.read()
        except OSError:
            return ""
        if not re.search(rf"\b{re.escape(name)}\b", source):
            return ""
        return (f" NOTE: config.py mentions {name}, so it is probably importing "
                f"what is no longer there - the operator's next reload would "
                f"fail and take their key bindings with it. Read it and offer "
                f"to take those lines out.")

    def reload_config(self) -> str:
        # Cached instances are deliberately *not* dropped here. Staleness is
        # already handled a better way - the loader re-imports whenever the
        # file's mtime moves, which covers a hand edit as well as a
        # write_extension - and dropping them would take a *running* action out
        # of cancel_action's reach, since that reaches it by looking the name up
        # again. A reload should not cost the operator the stop button.
        def reload():
            self.keymap.configure()
            return "reloaded"

        with _captured_log() as captured:
            self.ui.on_main_thread(reload)
        output = captured.getvalue().strip()
        return output or "config reloaded"


#: Timestamped backups kept per module.  Enough to walk back through a fix loop
#: that went wrong, few enough that `extensions/` stays readable - they land in
#: the directory the operator opens to read their own actions.  Not a `.py`, so
#: nothing importable is being left behind.
_BACKUPS_KEPT = 5


def _prune_backups(path: str) -> None:
    """Drop all but the newest :data:`_BACKUPS_KEPT` backups of one module.

    Sorted by name, which is chronological: the suffix is a fixed-width
    timestamp.  Deleting our own backups only - the glob is anchored to this
    module's path.
    """
    prefix = os.path.basename(path) + ".bak-"
    directory = os.path.dirname(path)
    try:
        backups = sorted(entry for entry in os.listdir(directory)
                         if entry.startswith(prefix))
        for stale in backups[:-_BACKUPS_KEPT]:
            os.remove(os.path.join(directory, stale))
    except OSError:
        # Housekeeping: a backup that will not delete is not a reason to fail
        # the write that has already succeeded.
        pass


def _change_summary(previous: str | None, source: str) -> str:
    """What the console reports about a write: new, unchanged, or +N/-M."""
    if previous is None:
        return f"new, {len(source.splitlines())} lines"
    if previous == source:
        return "unchanged"
    added = removed = 0
    for line in difflib.unified_diff(previous.splitlines(), source.splitlines(),
                                     n=0, lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return f"+{added}/-{removed} lines"


def _subprocess_detail(error: BaseException) -> str:
    """What a failed subprocess said, which the stream capture cannot see.

    A child process writes to the real file descriptor, not to Python's
    `sys.stderr`, so no wrapper installed here observes it. The only place it
    survives is on the exception - and only when the action asked for it, which
    is why the skill says to shell out with `capture_output=True`. Saying so
    when it did not is worth more than silence: "returned 1" with no reason is
    where the loop stalls.
    """
    output = getattr(error, "stderr", None) or getattr(error, "output", None)
    if isinstance(output, bytes):
        output = output.decode("utf-8", "replace")
    if output:
        return f"\nthe subprocess wrote to stderr:\n{output.strip()}\n"
    if getattr(error, "returncode", None) is not None:
        return ("\n(the subprocess left no stderr here - it was run without "
                "capture_output=True, so what it said went to the terminal "
                "Keyhac was started from and nowhere this can reach)\n")
    return ""


#: Ceiling on what one run hands back. A run that logs a line per row over
#: hundreds of rows would otherwise fill a context window with the middle of
#: its own progress bar; the tail is where the failure is, so that is the end
#: that is kept.
MAX_CAPTURE = 20_000

#: Captures currently collecting, and the lock around installing the stream
#: tee. Global rather than per-thread on purpose: an action's `starting()` and
#: `finished()` run on the loop thread while `run()` runs on this one, so a
#: thread filter would drop exactly the two halves that report what happened.
#: The cost is that two runs overlapping - possible now that the action pool
#: has more than one worker - each see the other's lines. Interleaved context
#: beats the previous behaviour, which was the model seeing none of it.
_captures: list = []

#: Guards the stream swap only. The list itself needs no lock - append and
#: remove are atomic - but two runs starting together could both believe they
#: were the first and install a tee over a tee.
_captures_lock = threading.Lock()


class _Bounded:
    """A buffer that keeps the last MAX_CAPTURE characters and says so."""

    def __init__(self):
        self._parts: list[str] = []
        self._size = 0
        self.dropped = 0

    def write(self, text: str) -> int:
        self._parts.append(text)
        self._size += len(text)
        while self._size > MAX_CAPTURE and len(self._parts) > 1:
            gone = self._parts.pop(0)
            self._size -= len(gone)
            self.dropped += len(gone)
        return len(text)

    def getvalue(self) -> str:
        body = "".join(self._parts)
        if self.dropped:
            return (f"[{self.dropped} earlier characters dropped - this is the "
                    f"tail of the run]\n{body}")
        return body


class _Capture(logging.Handler):
    def __init__(self, buffer):
        super().__init__()
        self.buffer = buffer

    def emit(self, record):
        self.buffer.write(self.format(record) + "\n")


class _Tee:
    """`sys.stdout`, plus every active capture.

    Wraps rather than replaces, because print() must keep reaching the console
    window - the operator watching it is not who this change is for.
    """

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def write(self, text) -> int:
        for buffer in tuple(_captures):
            buffer.write(str(text))
        return self._wrapped.write(text)

    def flush(self) -> None:
        self._wrapped.flush()

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


class _captured_log:
    """Collect what an action produced, to hand back to the model.

    The point of run_action is that the model reads its own failure; a tool
    returning "ok"/"failed" leaves the operator copying tracebacks, which is
    the manual step it exists to remove. Three things used to reach the
    console window and not the model, and the first two now do:

    - **print()**, which the shipped config.py template teaches on the same
      line as the logger. Collected by teeing sys.stdout/sys.stderr rather
      than redirecting them, so it still reaches the console as well.
    - **Loggers outside the `keyhac` tree** - what `getLogger(__name__)` in a
      module under `extensions/` produces. The handler sits on the root logger
      now. That does sweep in every library the action imports, which is the
      intended trade: a urllib retry storm is part of what went wrong.
    - **Subprocess stderr** is the one this cannot reach. A child process
      writes to the real file descriptor, not to Python's `sys.stderr`, so no
      stream wrapper sees it. `run_action` surfaces it from
      CalledProcessError instead, and the skill tells actions to shell out
      with capture_output=True so there is something to surface.
    """

    def __enter__(self) -> "_Bounded":
        self.buffer = _Bounded()
        self.handler = _Capture(self.buffer)
        self.handler.setFormatter(
            logging.Formatter("%(levelname)s [%(name)s] %(message)s"))

        # Our own access log is not part of what the reload said, and a call
        # arriving on another connection while this one runs would otherwise
        # come back as if it were.
        self.handler.addFilter(lambda record: record.name != logger.name)

        # Both, and it is not belt-and-braces. `keyhac` is configured with
        # propagate=False (core/log.py), so a record from the documented
        # getLogger() never reaches root - attaching only there captured
        # nothing, which is what the tests caught. Because it does not
        # propagate, nothing is emitted twice either: keyhac records stop at
        # the keyhac handler, everything else - an extensions/ module's
        # getLogger(__name__), a library - arrives at root.
        self.loggers = (logging.getLogger(), logging.getLogger("keyhac"))
        for target in self.loggers:
            target.addHandler(self.handler)

        # A root handler only sees what root's level admits, and the default is
        # WARNING - which would have dropped every logger.info() an action
        # writes. INFO rather than DEBUG on purpose: DEBUG here would pull in
        # the debug chatter of every library the action imports, and the model
        # is reading this.
        self._level = self.loggers[0].level
        if self._level == logging.NOTSET or self._level > logging.INFO:
            self.loggers[0].setLevel(logging.INFO)

        with _captures_lock:
            _captures.append(self.buffer)
            if len(_captures) == 1 and not isinstance(sys.stdout, _Tee):
                self._streams = (sys.stdout, sys.stderr)
                sys.stdout, sys.stderr = _Tee(sys.stdout), _Tee(sys.stderr)
            else:
                self._streams = None
        return self.buffer

    def __exit__(self, *exc) -> None:
        for target in self.loggers:
            target.removeHandler(self.handler)
        self.loggers[0].setLevel(self._level)
        with _captures_lock:
            try:
                _captures.remove(self.buffer)
            except ValueError:
                pass
            if self._streams is not None and not _captures:
                sys.stdout, sys.stderr = self._streams
