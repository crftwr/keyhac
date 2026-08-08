"""The MCP endpoint (keyhac/mcp/).

Hermetic on every platform: the protocol layer needs no UI, and the tools are
exercised against a fake keymap. What is pinned here is the protocol contract
(a wrong reply shape is invisible until a client refuses to start), the two
security properties, and the rule that a failing tool reports itself *inside*
the result so the model can read it.
"""

import json
import sys
import os
import stat
import time
import urllib.error
import urllib.request

import pytest

from keyhac.mcp.server import Dispatcher, MCPServer, PROTOCOL_VERSION
from keyhac.mcp.tools import ToolRegistry


class FakeNode:
    def __init__(self, name="Main", children=()):
        self.name = name
        self.role = "AXWindow"
        self.value = None
        self.identifier = None
        self.rect = (0, 0, 100, 100)
        self.truncated = False
        self.children = list(children)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def reread(self, **kwargs):
        return self

    def dump(self):
        return f"{self.role} {self.name!r}"

    def find_all(self, **criteria):
        return [self]

    def find(self, **criteria):
        return self

    def read_text(self):
        return "buffer contents"


class FakeUI:
    def __init__(self, node):
        self.node = node
        self.content_access = []

    def focused(self):
        return self.node

    def window(self, app=None, title=None):
        return self.node if app in (None, "TestApp") else None

    def on_main_thread(self, func):
        return func()

    def enable_content_access(self, target=None, enable=True):
        self.content_access.append(enable)
        return True


class FakeWindow:
    def __init__(self, app_name, title):
        self.app_name = app_name
        self.title = title


class FakeKeymap:
    def __init__(self):
        self.node = FakeNode()
        self.ui = FakeUI(self.node)
        self.registered_actions = {}
        self.focus = type("F", (), {"app_name": "TestApp", "window_title": "Main",
                                    "path": "/App/Window"})()
        self.reloaded = 0

    def list_windows(self):
        return [FakeWindow("TestApp", "Main")]

    def configure(self):
        self.reloaded += 1


@pytest.fixture
def registry():
    return ToolRegistry(FakeKeymap())


@pytest.fixture
def dispatcher(registry):
    return Dispatcher(registry)


# -- protocol ---------------------------------------------------------------

def test_initialize_reports_a_protocol_and_a_name(dispatcher):
    reply = dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {}})
    assert reply["jsonrpc"] == "2.0" and reply["id"] == 1
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert reply["result"]["serverInfo"]["name"] == "keyhac"
    assert "tools" in reply["result"]["capabilities"]


def test_notifications_get_no_reply(dispatcher):
    """A reply to a notification is a protocol violation, and clients differ in
    how loudly they complain about it."""
    assert dispatcher.handle({"jsonrpc": "2.0",
                              "method": "notifications/initialized"}) is None


def test_unknown_method_is_a_jsonrpc_error(dispatcher):
    reply = dispatcher.handle({"jsonrpc": "2.0", "id": 2, "method": "nope"})
    assert reply["error"]["code"] == -32601


def test_a_non_jsonrpc_body_is_rejected(dispatcher):
    assert dispatcher.handle({"hello": "world"})["error"]["code"] == -32600


def test_tools_list_shape(dispatcher):
    tools = dispatcher.handle({"jsonrpc": "2.0", "id": 3,
                               "method": "tools/list"})["result"]["tools"]
    assert {"describe_screen", "start_action", "get_action_result",
            "cancel_action", "list_windows"} <= {t["name"] for t in tools}
    for tool in tools:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"


def test_a_failing_tool_reports_inside_the_result(dispatcher):
    """Not as a JSON-RPC error: the model is meant to read the failure and try
    again, which it cannot do if the transport swallows it."""
    reply = dispatcher.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                               "params": {"name": "start_action",
                                          "arguments": {"name": "absent"}}})
    assert "error" not in reply
    assert reply["result"]["isError"] is True
    assert "absent" in reply["result"]["content"][0]["text"]


# -- tools ------------------------------------------------------------------

def test_list_windows_marks_the_focused_one(registry):
    assert "* TestApp: Main" in registry.call("list_windows", {})


def test_describe_screen_dumps_the_tree(registry):
    assert "AXWindow" in registry.call("describe_screen", {})


def test_a_missing_window_says_what_to_do_next(registry):
    with pytest.raises(RuntimeError, match="list_windows"):
        registry.call("describe_screen", {"app": "Nope"})


def test_find_elements_requires_a_criterion(registry):
    with pytest.raises(ValueError, match="at least one"):
        registry.call("find_elements", {})


def test_an_action_reports_what_it_logged(registry):
    from keyhac.core import log

    def run():
        log.getLogger("Probe").info("did the thing")
        return "ok"

    output = _register(registry, run)
    assert "did the thing" in output
    assert "finished" in output


def test_a_failure_comes_back_as_a_traceback_rather_than_raising(registry):
    """The whole point of the tool: the model reads the failure itself."""
    def run():
        raise ValueError("selector matched nothing")

    output = _register(registry, run, name="bad")
    assert "ValueError: selector matched nothing" in output
    assert "Traceback" in output
    assert "failed" in output


def test_reload_config_reloads(registry):
    registry.call("reload_config", {})
    assert registry.keymap.reloaded == 1


def test_enable_content_access_is_reversible(registry):
    registry.call("enable_content_access", {"enable": False})
    assert registry.keymap.ui.content_access == [False]


# -- the two security properties --------------------------------------------

@pytest.fixture
def server(tmp_path, registry):
    server = MCPServer(registry, str(tmp_path / "mcp.json"))
    server.start()
    yield server
    server.stop()


def post(server, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"http://127.0.0.1:{server.port}/",
                                     data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as reply:
        return json.loads(reply.read() or b"null")


def test_a_request_without_the_token_is_refused(server):
    """An application arguing it is not a keylogger cannot serve an
    unauthenticated local endpoint offering key injection (§4.4)."""
    with pytest.raises(urllib.error.HTTPError) as caught:
        post(server, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert caught.value.code == 401

    with pytest.raises(urllib.error.HTTPError):
        post(server, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
             token="wrong")


def test_with_the_token_it_serves(server):
    reply = post(server, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                 token=server.token)
    assert reply["result"]["tools"]


def test_the_endpoint_file_is_private_and_complete(server):
    path = server.endpoint_path
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, "token is world-readable"
    published = json.loads(open(path).read())
    assert published["port"] == server.port
    assert published["token"] == server.token


def test_stopping_removes_the_published_token(tmp_path, registry):
    server = MCPServer(registry, str(tmp_path / "mcp.json"))
    server.start()
    path = server.endpoint_path
    assert os.path.exists(path)
    server.stop()
    assert not os.path.exists(path), "a stale token file points at a dead port"


def test_it_binds_loopback_only(server):
    assert server._server.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_the_declined_verbs_answer_405_and_not_501(server, method):
    """Streamable HTTP lets a server offer no SSE stream and no sessions, but
    the refusal has to be 405: a client that opens the optional server stream
    before its first tool call reads the stdlib's default 501 as "this server
    does not implement HTTP" rather than "no stream here". Only clients other
    than the bridge ever send these, which is exactly who this is for."""
    request = urllib.request.Request(f"http://127.0.0.1:{server.port}/",
                                     method=method)
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=10)
    assert caught.value.code == 405
    assert caught.value.headers.get("Allow") == "POST"


# -- the switch -------------------------------------------------------------

def _real_keymap(tmp_path):
    from keyhac.core.keymap import Keymap
    from keyhac.platform.fake import FakeFocusProvider
    from tests.conftest import FakeInputHook

    config = tmp_path / "config.py"
    config.write_text("def configure(keymap):\n    pass\n")
    keymap = Keymap(FakeInputHook("ansi"), FakeFocusProvider(), "mac",
                    config_path=str(config), template_path=str(config))
    keymap.configure()
    return keymap


def test_the_switch_reports_and_moves(tmp_path):
    """`mcp_server_running` is what both faces of the switch read to draw
    themselves, so it has to follow the socket rather than the request."""
    keymap = _real_keymap(tmp_path)
    assert keymap.mcp_server_running is False
    keymap.start_mcp_server()
    try:
        assert keymap.mcp_server_running is True
        keymap.start_mcp_server()          # idempotent: the menu can double-fire
        assert keymap.mcp_server_running is True
    finally:
        keymap.stop_mcp_server()
    assert keymap.mcp_server_running is False


# -- the bridge -------------------------------------------------------------

def test_the_bridge_explains_a_missing_daemon(tmp_path, capsys, monkeypatch):
    """The most likely failure a user meets, so the message has to name the
    cause rather than 'connection refused'."""
    import io
    from keyhac.mcp import bridge

    monkeypatch.setattr(bridge, "endpoint_path",
                        lambda config=None: str(tmp_path / "absent.json"))
    monkeypatch.setattr("sys.stdin",
                        io.StringIO('{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'))
    bridge.main([])
    reply = json.loads(capsys.readouterr().out)
    assert "MCP server" in reply["error"]["message"]


def test_a_hollow_web_area_points_at_content_access(registry):
    """The shape a Chromium/Electron window really has with content off: the
    web area is present, nearly empty, and its nodes are marked truncated. A
    model reading only the truncation note raises max_nodes, gets the same tree
    back, and concludes the application has no accessible UI."""
    web = FakeNode("page")
    web.role = "AXWebArea"
    root = registry.keymap.node
    root.children = [web]
    root.truncated = True
    text = registry.call("describe_screen", {})
    assert "enable_content_access" in text
    assert "Raising max_nodes will not help" in text
    assert "raise max_nodes/max_depth" not in text, "the misleading advice won"


def test_a_populated_web_area_does_not(registry):
    """A loaded page must not be told to enable what is already enabled."""
    from keyhac.mcp.tools import EMPTY_WEB_AREA

    web = FakeNode("page", children=[FakeNode(f"n{i}")
                                     for i in range(EMPTY_WEB_AREA + 1)])
    web.role = "AXWebArea"
    registry.keymap.node.children = [web]
    assert "enable_content_access" not in registry.call("describe_screen", {})


def test_a_native_window_keeps_the_truncation_note(registry):
    """Finder: 235 elements, no web area - the budget note is the right one."""
    from keyhac.mcp.tools import EMPTY_WINDOW

    root = registry.keymap.node
    root.children = [FakeNode(f"child{i}") for i in range(EMPTY_WINDOW + 5)]
    root.truncated = True
    text = registry.call("describe_screen", {})
    assert "raise max_nodes/max_depth" in text
    assert "enable_content_access" not in text


# -- what an action hands back (§15.3) --------------------------------------
#
# Three things reached the console window and not the model. Each of these
# fails silently if it regresses: the tool still returns *something*, just
# without the line that says what went wrong.

def _register(registry, run, name="probe"):
    """Start an action and collect it, which is now two calls rather than one."""
    class Action:
        def starting(self): pass
        def finished(self, result): pass
    Action.run = staticmethod(run)
    registry.keymap.registered_actions[name] = Action()
    registry.call("start_action", {"name": name})
    return registry.call("get_action_result", {"name": name, "wait": 20})


def test_print_reaches_the_model(registry):
    """The shipped config.py template teaches print() on the same line as the
    logger. It reached the console window and stopped there."""
    assert "printed this" in _register(registry, lambda: print("printed this"))


def test_print_still_reaches_the_console(registry):
    """Teed, not redirected: the operator watching the console window is not
    who this change was for, and must not lose anything."""
    import sys
    seen = []

    class Console:
        def write(self, text): seen.append(text); return len(text)
        def flush(self): pass

    original = sys.stdout
    sys.stdout = Console()
    try:
        _register(registry, lambda: print("both places"))
    finally:
        sys.stdout = original
    assert any("both places" in text for text in seen)


def test_a_logger_outside_the_keyhac_tree_is_captured(registry):
    """What getLogger(__name__) produces in a module under extensions/."""
    import logging as stdlib_logging

    def run():
        stdlib_logging.getLogger("my_extension").info("from an extension")

    assert "from an extension" in _register(registry, run)


def test_the_documented_logger_is_still_captured(registry):
    """`keyhac` is configured with propagate=False, so a root-only handler sees
    none of it - the regression this pins."""
    from keyhac.core import log

    def run():
        log.getLogger("Probe").info("through keyhac's own logger")

    assert "through keyhac's own logger" in _register(registry, run)


def test_nothing_is_captured_twice(registry):
    from keyhac.core import log

    def run():
        log.getLogger("Probe").info("once please")

    assert _register(registry, run).count("once please") == 1


def test_a_failed_subprocess_hands_over_its_stderr(registry):
    """No stream wrapper can see this: the child writes to the real file
    descriptor, not to Python's sys.stderr. It survives only on the exception,
    and only when the action asked for it."""
    import subprocess

    def run():
        subprocess.run([sys.executable, "-c", "import sys; sys.stderr.write('the child complained'); sys.exit(3)"],
                       check=True, capture_output=True, text=True)

    output = _register(registry, run)
    assert "the child complained" in output


def test_a_subprocess_run_without_capture_says_so(registry):
    """Better than silence: "returned 1" with no reason is where the loop
    stalls, and the fix is a line in the action rather than a mystery."""
    import subprocess

    def run():
        subprocess.run([sys.executable, "-c", "import sys; sys.exit(4)"], check=True)

    output = _register(registry, run)
    assert "capture_output=True" in output


def test_a_long_run_is_bounded_and_says_it_was_truncated(registry):
    """A run logging a line per row over hundreds of rows would otherwise fill
    a context window with the middle of its own progress."""
    from keyhac.mcp.tools import MAX_CAPTURE

    def run():
        for index in range(2000):        # ~100k characters, fixed
            print(f"row {index} " + "x" * 40)
        print("THE LAST LINE")

    output = _register(registry, run)
    assert len(output) < MAX_CAPTURE * 1.5
    assert "characters dropped" in output
    assert "THE LAST LINE" in output, "the tail is where the failure is"


# -- the asynchronous shape --------------------------------------------------
#
# Starting and collecting are separate because the transport answers one
# message per request and §2's actions run for minutes. These pin the parts
# that only exist because of that.

def _slow_action(registry, name="slow"):
    import threading as t
    gate = t.Event()

    class Action:
        def starting(self): pass
        def finished(self, result): pass
        def run(self):
            print("started working")
            gate.wait(20)
            print("done working")

    registry.keymap.registered_actions[name] = Action()
    return gate


def test_start_action_returns_before_the_action_finishes(registry):
    """The property the whole shape exists for: a call that waited for the end
    is a call that times out for exactly the workload this serves."""
    gate = _slow_action(registry)
    reply = registry.call("start_action", {"name": "slow"})
    assert "started" in reply
    assert "slow" in registry.call("list_actions", {})
    assert "RUNNING" in registry.call("list_actions", {})
    gate.set()
    registry.call("get_action_result", {"name": "slow", "wait": 20})


def test_still_running_is_an_answer_not_a_timeout(registry):
    gate = _slow_action(registry)
    registry.call("start_action", {"name": "slow"})
    reply = registry.call("get_action_result", {"name": "slow", "wait": 0})
    assert "still running" in reply
    assert "started working" in reply, "output so far comes back too"
    assert "again" in reply, "and it says what to do about it"
    gate.set()
    registry.call("get_action_result", {"name": "slow", "wait": 20})


def test_a_waiting_collect_returns_as_soon_as_it_ends(registry):
    """Waiting rather than polling is what keeps a fast action fast: two round
    trips, no added latency."""
    import threading as t
    import time as clock

    gate = _slow_action(registry)
    registry.call("start_action", {"name": "slow"})
    t.Timer(0.2, gate.set).start()
    began = clock.monotonic()
    reply = registry.call("get_action_result", {"name": "slow", "wait": 20})
    assert clock.monotonic() - began < 5, "it waited out the full timeout"
    assert "done working" in reply


def test_cancel_action_stops_it(registry):
    """The model can stop what it started - refusing that while allowing
    starting would be the odd asymmetry."""
    from keyhac.core.action import ActionCancelled, ThreadedAction

    class Slow(ThreadedAction):
        def __init__(self):
            self.entered = __import__("threading").Event()
        def run(self):
            self.entered.set()
            from keyhac.core.wait import wait_for
            wait_for(lambda: False, timeout=20, message="never", interval=0.01)

    action = Slow()
    registry.keymap.registered_actions["slow2"] = action
    registry.call("start_action", {"name": "slow2"})
    assert action.entered.wait(5)
    assert "asked" in registry.call("cancel_action", {"name": "slow2"})
    assert "cancelled" in registry.call("get_action_result",
                                        {"name": "slow2", "wait": 20})


def test_collecting_an_action_that_never_ran(registry):
    class Action:
        def starting(self): pass
        def run(self): pass
        def finished(self, result): pass

    registry.keymap.registered_actions["idle"] = Action()
    assert "has not been run" in registry.call("get_action_result",
                                               {"name": "idle", "wait": 0})
    assert "not run yet" in registry.call("list_actions", {})


def test_starting_one_that_is_already_running_says_so(registry):
    gate = _slow_action(registry)
    registry.call("start_action", {"name": "slow"})
    assert "already running" in registry.call("start_action", {"name": "slow"})
    gate.set()
    registry.call("get_action_result", {"name": "slow", "wait": 20})


def test_an_unrelated_print_does_not_land_in_a_running_action(registry):
    """A run lasts minutes. A global stdout tee spent all of them absorbing
    every unrelated print in the process into whichever action happened to be
    running - which the first end-to-end run showed as an action's record
    quoting the script that started it."""
    import threading as t

    gate = _slow_action(registry)
    registry.call("start_action", {"name": "slow"})
    time.sleep(0.1)

    elsewhere = t.Thread(target=lambda: print("NOT THE ACTION'S OUTPUT"))
    elsewhere.start()
    elsewhere.join(5)

    peek = registry.call("get_action_result", {"name": "slow", "wait": 0})
    assert "started working" in peek, "the action's own print is captured"
    assert "NOT THE ACTION'S OUTPUT" not in peek
    gate.set()
    registry.call("get_action_result", {"name": "slow", "wait": 20})
