"""The MCP endpoint (keyhac/mcp/).

Hermetic on every platform: the protocol layer needs no UI, and the tools are
exercised against a fake keymap. What is pinned here is the protocol contract
(a wrong reply shape is invisible until a client refuses to start), the two
security properties, and the rule that a failing tool reports itself *inside*
the result so the model can read it.
"""

import json
import os
import stat
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
    assert {"describe_screen", "run_action", "list_windows"} <= {t["name"] for t in tools}
    for tool in tools:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"


def test_a_failing_tool_reports_inside_the_result(dispatcher):
    """Not as a JSON-RPC error: the model is meant to read the failure and try
    again, which it cannot do if the transport swallows it."""
    reply = dispatcher.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                               "params": {"name": "run_action",
                                          "arguments": {"name": "absent"}}})
    assert "error" not in reply
    assert reply["result"]["isError"] is True
    assert "absent" in reply["result"]["content"][0]["text"]


# -- tools ------------------------------------------------------------------

def test_list_windows_marks_the_focused_one(registry):
    assert "* TestApp: Main" in registry.call("list_windows", {})


def test_describe_screen_dumps_the_tree(registry):
    assert "AXWindow" in registry.call("describe_screen", {})


def test_describe_screen_says_when_it_truncated(registry):
    registry.keymap.node.truncated = True
    assert "truncated" in registry.call("describe_screen", {})


def test_a_missing_window_says_what_to_do_next(registry):
    with pytest.raises(RuntimeError, match="list_windows"):
        registry.call("describe_screen", {"app": "Nope"})


def test_find_elements_requires_a_criterion(registry):
    with pytest.raises(ValueError, match="at least one"):
        registry.call("find_elements", {})


def test_run_action_returns_what_the_action_logged(registry):
    from keyhac.core import log

    class Action:
        def starting(self): pass
        def run(self):
            log.getLogger("Probe").info("did the thing")
            return "ok"
        def finished(self, result): pass

    registry.keymap.registered_actions["probe"] = Action()
    assert "did the thing" in registry.call("run_action", {"name": "probe"})


def test_run_action_returns_the_traceback_rather_than_raising(registry):
    """The whole point of the tool: the model reads the failure itself."""
    class Action:
        def starting(self): pass
        def run(self):
            raise ValueError("selector matched nothing")
        def finished(self, result): pass

    registry.keymap.registered_actions["bad"] = Action()
    output = registry.call("run_action", {"name": "bad"})
    assert "ValueError: selector matched nothing" in output
    assert "Traceback" in output


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
    assert "enable_mcp_server" in reply["error"]["message"]
