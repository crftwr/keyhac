"""The tools Claude gets, and why these ones.

They follow the escalation ladder in doc/dev/ai-integration.md §8.1: read the
screen that is already on it, ask the operator to open the state that is not,
and only then anything more expensive. `describe_screen` is rung 1 and the tool
everything else depends on - an action written without looking at the tree is
written against a remembered screen.

`run_action` is the other load-bearing one (§8.3). Without it the human is the
transport for every error message: they run the action, copy the traceback,
paste it back. With it the generate-verify loop closes, and loop iteration rate
is what the whole authoring approach lives or dies on.

There is deliberately no tool that writes files or types text. Reading a tree
and pressing a button in an application the operator is looking at is one thing;
a tool that lets a remote model put arbitrary Python on disk is another, and it
wants its own decision rather than arriving as a side effect of this one.
Generated actions are pasted in by the operator until Layer 5 exists.

THREADS. Element access is main-thread-only and these run on the MCP server's
threads, so every tool that touches the UI goes through `ui.on_main_thread` -
directly, or through the node methods, which dispatch themselves.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import traceback

from keyhac.core import log
from keyhac.core.action import ActionCancelled

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


class Tool:
    """One callable, its JSON Schema, and the description Claude reads."""

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
                 "identifier / text, and report what each one is. Use when a "
                 "full tree is more than you need, or to check that a "
                 "selector you are about to write actually matches.",
                 {"type": "object", "properties": {
                     **window_args,
                     "role": {**string, "description": "Role pattern. macOS "
                              "names may drop the AX prefix."},
                     "name": {**string, "description": "Label pattern."},
                     "value": {**string, "description": "Content pattern."},
                     "identifier": {**string, "description":
                                    "DOM id / AutomationId pattern."},
                     "text": {**string, "description":
                              "Matches label and content together."},
                     "limit": {**integer, "description": "Maximum matches "
                               "to report (default 20)."}}},
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
                 "The actions this configuration has registered by name, and "
                 "which of them run_action can run.",
                 {"type": "object", "properties": {}},
                 self.list_actions),
            Tool("run_action",
                 "Run a registered action and return everything it logged, "
                 "including the traceback if it raised. This is how you check "
                 "your own work: write the action, have the operator save and "
                 "reload it, run it, read what happened, fix it.",
                 {"type": "object", "properties": {
                     "name": {**string, "description":
                              "Name from list_actions."}},
                  "required": ["name"]},
                 self.run_action),
            Tool("reload_config",
                 "Reload ~/.keyhac/config.py, so an edited or newly added "
                 "action is picked up without restarting Keyhac. Reports the "
                 "error instead of applying it if the config fails to load.",
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
        def read():
            focus = self.keymap.focus
            focused = (focus.app_name, focus.window_title) if focus else (None, None)
            lines = []
            for window in self.keymap.list_windows():
                mark = "*" if (window.app_name, window.title) == focused else " "
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
        window = self._window(app, title)
        tree = window.reread(max_depth=int(max_depth), max_nodes=int(max_nodes),
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
            text += ("\n\n[truncated: raise max_nodes/max_depth, or narrow "
                     "with roles=, before concluding this is the whole screen]")
        return text

    def find_elements(self, app=None, title=None, limit=20, **criteria) -> str:
        criteria = {k: v for k, v in criteria.items() if v is not None}
        if not criteria:
            raise ValueError("give at least one of role/name/value/"
                             "identifier/text")
        window = self._window(app, title)
        matches = window.find_all(**criteria)
        if not matches:
            return f"no element matching {criteria}"
        lines = [f"{len(matches)} match(es):"]
        for node in matches[:int(limit)]:
            lines.append(f"  {node!r} value={node.value!r} rect={node.rect}")
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

    def list_actions(self) -> str:
        actions = self.keymap.registered_actions
        if not actions:
            return ("no actions registered. A configuration registers one "
                    "with keymap.register_action(\"name\", TheAction()).")
        return "\n".join(f"{name}: {action!r}" for name, action in
                         sorted(actions.items()))

    def run_action(self, name: str) -> str:
        action = self.keymap.registered_actions.get(name)
        if action is None:
            raise KeyError(f"no action named {name!r}; call list_actions")

        # Still not through ThreadedAction.__call__, but for one reason now
        # rather than two. The pool no longer serializes everything behind a
        # single worker, so queueing is no longer the problem; what remains is
        # that __call__ returns as soon as it has submitted, and this tool has
        # to hand back what the action logged. Registering with _running is
        # what matters, and is done explicitly below, so Esc reaches an action
        # started from here exactly as it reaches one started from a key.
        with _captured_log() as captured:
            try:
                if hasattr(action, "run"):
                    self.ui.on_main_thread(action.starting)
                    # register_action is duck-typed - anything with run() is
                    # accepted - so an action that is not a ThreadedAction
                    # still runs here, just without being cancellable.
                    track = getattr(action, "cancellable", contextlib.nullcontext)
                    with track():
                        result = action.run()
                    self.ui.on_main_thread(lambda: action.finished(result))
                else:
                    self.ui.on_main_thread(action)
            except ActionCancelled:
                # Its own arm because it is a BaseException: the handler below
                # would not see it, and it would leave through the HTTP layer
                # as a server error rather than as the answer it is.
                return (captured.getvalue()
                        + f"\n{name} was cancelled with Esc. Whatever it "
                          f"logged above is how far it got.")
            except Exception as error:                    # noqa: BLE001
                return (captured.getvalue()
                        + "\nthe action raised:\n" + traceback.format_exc()
                        + _subprocess_detail(error))
        output = captured.getvalue().strip()
        return output or f"{name} finished and logged nothing"

    def reload_config(self) -> str:
        def reload():
            self.keymap.configure()
            return "reloaded"

        with _captured_log() as captured:
            self.ui.on_main_thread(reload)
        output = captured.getvalue().strip()
        return output or "config reloaded"


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
