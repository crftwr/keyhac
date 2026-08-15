"""The MCP endpoint (keyhac/mcp/).

Hermetic on every platform: the protocol layer needs no UI, and the tools are
exercised against a fake keymap. What is pinned here is the protocol contract
(a wrong reply shape is invisible until a client refuses to start), the two
security properties, and the rule that a failing tool reports itself *inside*
the result so the model can read it.
"""

import json
import logging
import pathlib
import sys
import os
import shutil
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

import keyhac.core.keymap as keymap_module
from keyhac.core import capture
import keyhac.mcp.server as server_module
import keyhac.mcp.tools as tools_module
from keyhac.mcp.server import Dispatcher, MCPServer, PROTOCOL_VERSION
from keyhac.mcp.tools import ToolRegistry


class FakeNode:
    def __init__(self, name="Main", children=(), depth=0):
        self.name = name
        self.role = "AXWindow"
        self.value = None
        self.identifier = None
        self.rect = (0, 0, 100, 100)
        self.depth = depth
        self.truncated = False
        self.children = list(children)
        self.searched = None             # what find_all was last asked

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def reread(self, **kwargs):
        return self

    def dump(self):
        return f"{self.role} {self.name!r}"

    def find_all(self, **criteria):
        self.searched = criteria
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
    def __init__(self, extensions_dir=""):
        self.node = FakeNode()
        self.ui = FakeUI(self.node)
        self.focus = type("F", (), {"app_name": "TestApp", "window_title": "Main",
                                    "path": "/App/Window"})()
        self.reloaded = 0
        self.extensions_dir = extensions_dir

        self.config_path = os.path.join(extensions_dir or "", "..", "config.py")

    def describe_keymap(self, limit=300):
        return "focus now: TestApp - 'Main'\nfocus path: /App/Window"

    def list_windows(self):
        return [FakeWindow("TestApp", "Main")]

    def configure(self):
        self.reloaded += 1


@pytest.fixture
def registry():
    return ToolRegistry(FakeKeymap())


@pytest.fixture
def writable(tmp_path):
    """A registry whose keymap has an extensions/ directory to work in."""
    return ToolRegistry(FakeKeymap(extensions_dir=str(tmp_path / "extensions")))


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


# -- the console trail -------------------------------------------------------

def _rpc(dispatcher, name, arguments=None):
    return dispatcher.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                              "params": {"name": name,
                                         "arguments": arguments or {}}})


def _info(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]


def _debug(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]


def test_every_call_is_one_info_line(dispatcher, caplog):
    """The console is where an operator watches an open endpoint, and until
    this it reported only the two writes - a session could read every window on
    the screen and leave nothing behind."""
    with caplog.at_level(logging.INFO, logger="keyhac.MCP"):
        _rpc(dispatcher, "describe_screen", {"app": "TestApp"})
    assert len(_info(caplog)) == 1
    assert _info(caplog)[0].startswith("describe_screen(app='TestApp')")


def test_the_line_carries_the_outcome(dispatcher, caplog):
    """Half of what the line is for: a call that was refused and one that
    handed over a window are not the same event."""
    with caplog.at_level(logging.INFO, logger="keyhac.MCP"):
        _rpc(dispatcher, "list_windows")
        _rpc(dispatcher, "start_action", {"name": "absent"})
    served, refused = _info(caplog)
    assert served.endswith("chars")
    assert "absent" in refused


def test_a_failure_stays_on_one_line(dispatcher, caplog):
    """A tool reports failure as text and some of that text is a traceback.
    One call is one line, or the trail is unreadable in the window it is in."""
    def explode(**_):
        raise RuntimeError("first line\nsecond line\nthird line")

    dispatcher.registry.tools["list_windows"].run = explode
    with caplog.at_level(logging.INFO, logger="keyhac.MCP"):
        _rpc(dispatcher, "list_windows")
    assert "\n" not in _info(caplog)[0]
    assert "first line second line" in _info(caplog)[0]


def test_a_long_argument_is_elided_not_dropped(writable, caplog):
    """`write_extension` carries a whole module. Logging it whole would push
    the rest of the session out of the console's ring buffer; logging only the
    tool name would lose which module was written."""
    source = "# " + "x" * 5000 + "\n"
    with caplog.at_level(logging.INFO, logger="keyhac.MCP"):
        _rpc(Dispatcher(writable), "write_extension",
             {"name": "thing", "source": source})
    # Two lines here, saying different things: the tool announces the file it
    # replaced, and the call line is the one under test.
    line = next(m for m in _info(caplog) if m.startswith("write_extension("))
    assert "name='thing'" in line
    assert source not in line and len(line) < 300
    assert "source='# xxx" in line, "and enough of it to recognise"


def test_the_handshake_is_not_an_info_line(dispatcher, caplog):
    """A client pings for as long as it stays connected. An audit trail nobody
    can read is not one."""
    with caplog.at_level(logging.INFO, logger="keyhac.MCP"):
        for method in ("initialize", "tools/list", "ping"):
            dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": method})
    assert _info(caplog) == []


def test_debug_carries_the_whole_exchange(dispatcher, caplog):
    with caplog.at_level(logging.DEBUG, logger="keyhac.MCP"):
        _rpc(dispatcher, "describe_keymap")
    sent = [m for m in _debug(caplog) if m.startswith("-> ")]
    received = [m for m in _debug(caplog) if m.startswith("<- ")]
    assert any("describe_keymap" in m for m in sent)
    assert any("focus path" in m for m in received), "the reply, not just a shape"


def test_the_debug_line_spends_no_characters_on_nothing(registry, caplog):
    """Both of json.dumps's defaults inflate it: padded separators cost a
    character per field, and ensure_ascii turns one Japanese label into six
    characters each. `DETAIL_CHARS` is the budget they were spending."""
    registry.keymap.node.name = "セカイブラウザ"
    with caplog.at_level(logging.DEBUG, logger="keyhac.MCP"):
        _rpc(Dispatcher(registry), "describe_screen")
    reply = next(m for m in _debug(caplog) if m.startswith("<- "))
    assert '{"jsonrpc":"2.0","id":9' in reply
    assert "セカイブラウザ" in reply and "\\u30bb" not in reply


def test_the_payload_reads_as_itself_not_as_an_escaped_ribbon(registry, caplog):
    """A UI tree inside JSON is one line with `\\n` written out along it, which
    is the one thing the console could have shown properly and did not. The
    envelope stays on its line; the tree goes underneath as a tree."""
    registry.keymap.node.dump = lambda: ("AXWindow 'Root'\n"
                                         "  AXGroup 'Sidebar'\n"
                                         "    AXButton 'Explorer'")
    with caplog.at_level(logging.DEBUG, logger="keyhac.MCP"):
        _rpc(Dispatcher(registry), "describe_screen")
    envelope, *body = next(
        m for m in _debug(caplog) if m.startswith("<- ")).splitlines()

    assert "\\n" not in envelope, "the tree is lifted out, not written along it"
    assert envelope.endswith('"isError":false}}'), "and the shape still shows"
    assert "<result.content[0].text>" in envelope, "naming where it went"
    assert body[:3] == ["AXWindow 'Root'",
                        "  AXGroup 'Sidebar'",
                        "    AXButton 'Explorer'"], "indentation is the tree"


def test_a_debug_block_is_bounded(writable, caplog):
    """The bound is per block rather than per record, because the block is what
    would evict the ring buffer: `describe_screen` answers with a whole UI tree
    and `write_extension` is handed a whole module."""
    with caplog.at_level(logging.DEBUG, logger="keyhac.MCP"):
        _rpc(Dispatcher(writable), "write_extension",
             {"name": "thing", "source": "# " + "x" * 50_000 + "\n"})
    envelope, block = next(
        m for m in _debug(caplog) if m.startswith("-> ")).splitlines()
    assert len(envelope) < 300, "the source is not in it"
    assert len(block) < server_module.DETAIL_CHARS + 100
    assert "more characters" in block


def test_a_notification_logs_no_reply(dispatcher, caplog):
    with caplog.at_level(logging.DEBUG, logger="keyhac.MCP"):
        dispatcher.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert not [m for m in _debug(caplog) if m.startswith("<- ")]


# -- tools ------------------------------------------------------------------

def test_list_windows_marks_the_focused_one(registry):
    assert "* TestApp: Main" in registry.call("list_windows", {})


def test_the_mark_survives_a_title_the_focus_path_escaped():
    # On macOS Focus.window_title is transliterated by FOCUS_PATH_TRANS_TABLE
    # ("(" -> "<", ":" -> "-", ...) while enumerated titles stay raw; the
    # focused window was never marked when its title contained any of
    # ( ) [ ] / * ? : - which is most editor and browser titles.  Issue #73.
    from keyhac.core.focus import FOCUS_PATH_TRANS_TABLE
    raw = "ai-integration.md (Working Tree) — keyhac"
    keymap = FakeKeymap()
    keymap.focus.app_name = "Code"
    keymap.focus.window_title = raw.translate(FOCUS_PATH_TRANS_TABLE)
    keymap.list_windows = lambda: [FakeWindow("Finder", "(untitled)"),
                                   FakeWindow("Code", raw)]
    out = ToolRegistry(keymap).call("list_windows", {})
    assert f"* Code: {raw}" in out
    assert "* Finder" not in out


def test_describe_screen_dumps_the_tree(registry):
    assert "AXWindow" in registry.call("describe_screen", {})


def test_a_missing_window_says_what_to_do_next(registry):
    with pytest.raises(RuntimeError, match="list_windows"):
        registry.call("describe_screen", {"app": "Nope"})


def test_find_elements_requires_a_criterion(registry):
    with pytest.raises(ValueError, match="at least one"):
        registry.call("find_elements", {})


def test_find_elements_passes_the_walk_bounds_through(registry):
    """Issue #68: controls in web content sit 30+ levels down, and the tool
    offered no way to reach past the default depth - find_all had the
    parameters all along."""
    registry.call("find_elements",
                  {"role": "PopUpButton", "max_depth": 45, "max_nodes": 6000})
    assert registry.keymap.node.searched == {
        "role": "PopUpButton", "max_depth": 45, "max_nodes": 6000}


def test_the_bounds_alone_are_not_a_criterion(registry):
    with pytest.raises(ValueError, match="at least one"):
        registry.call("find_elements", {"max_depth": 45})


def test_a_no_match_stays_plain_even_when_the_walk_was_cut(registry):
    """Deliberate (#68 second thoughts, issue #76): the runtime diagnostic
    that re-walked the tree on an empty result - to say whether "no match"
    meant "absent" or "not within the bounds" - was reverted. It cost a
    second live accessibility walk, and it described a second snapshot the
    search never saw. The ambiguity is accepted for now: taught statically
    in the max_depth schema description, with the candidate real fix (a
    stats out-parameter on find_all) recorded in the issue."""
    root = registry.keymap.node
    root.find_all = lambda **criteria: []
    root.truncated = True
    reread = []
    root.reread = lambda **kw: reread.append(kw) or root
    text = registry.call("find_elements", {"role": "PopUpButton"})
    assert text == "no element matching {'role': 'PopUpButton'}"
    assert reread == [], "and no second walk happened to say more"


def test_an_ax_prefixed_no_match_names_the_portable_spelling(registry):
    """Issue #69: "AX" is macOS vocabulary - an AX-prefixed pattern matches
    only macOS roles, and rediscovering that cost a session several calls.
    The hint is syntactic, read off the pattern alone, so the no-match path
    stays a single walk (issue #76)."""
    root = registry.keymap.node
    root.find_all = lambda **criteria: []
    reread = []
    root.reread = lambda **kw: reread.append(kw) or root
    text = registry.call("find_elements", {"role": "AXPopUpButton|Cell"})
    assert text.startswith(
        "no element matching {'role': 'AXPopUpButton|Cell'}")
    assert 'role="PopUpButton|Cell"' in text
    assert reread == [], "the hint is syntactic - no second walk"


def test_a_wildcard_ax_pattern_gets_no_suggestion(registry):
    """Stripping "AX*" would leave "*" - every role - so it is left alone
    and the no-match reply stays plain."""
    root = registry.keymap.node
    root.find_all = lambda **criteria: []
    text = registry.call("find_elements", {"role": "AX*"})
    assert text == "no element matching {'role': 'AX*'}"


def test_a_match_found_with_an_ax_pattern_gets_no_hint(registry):
    """The spelling advice is for the no-match path only."""
    text = registry.call("find_elements", {"role": "AXWindow"})
    assert "portable" not in text


def test_find_elements_names_each_matchs_ancestors(registry):
    """Issue #55: two same-role matches are told apart by their ancestor
    paths in one call, instead of by three dumps with different filters."""
    from keyhac.core.uitree import UINode

    root = UINode(role="AXWindow", name="Translate")
    left = UINode(role="AXTabGroup", identifier="source")
    right = UINode(role="AXTabGroup", name="Target")
    button_l = UINode(role="AXButton", name="English")
    button_r = UINode(role="AXButton", name="English")
    left._parent = root
    right._parent = root
    button_l._parent = left
    button_r._parent = right
    registry.keymap.node.find_all = lambda **criteria: [button_l, button_r]
    text = registry.call("find_elements", {"role": "Button"})
    assert "path=AXWindow(Translate)/AXTabGroup(#source)" in text
    assert "path=AXWindow(Translate)/AXTabGroup(Target)" in text


def test_a_match_that_is_the_searched_root_carries_no_path(registry):
    """A parentless match - the searched root itself - gets no empty
    path= fragment, and the line keeps its established shape."""
    from keyhac.core.uitree import UINode

    window = UINode(role="AXWindow", name="Main", rect=(0, 0, 100, 100))
    registry.keymap.node.find_all = lambda **criteria: [window]
    text = registry.call("find_elements", {"role": "Window"})
    assert text == ("1 match(es):\n"
                    "  UINode(AXWindow name='Main') value=None "
                    "rect=(0, 0, 100, 100)")


def test_an_action_reports_what_it_logged(writable):
    from keyhac.core import log

    def run():
        log.getLogger("Probe").info("did the thing")
        return "ok"

    output = _register(writable, run)
    assert "did the thing" in output
    assert "finished" in output


def test_a_failure_comes_back_as_a_traceback_rather_than_raising(writable):
    """The whole point of the tool: the model reads the failure itself."""
    def run():
        raise ValueError("selector matched nothing")

    output = _register(writable, run, name="bad")
    assert "ValueError: selector matched nothing" in output
    assert "Traceback" in output
    assert "failed" in output


def test_reload_config_reloads(registry):
    registry.call("reload_config", {})
    assert registry.keymap.reloaded == 1


def test_enable_content_access_is_reversible(registry):
    registry.call("enable_content_access", {"enable": False})
    assert registry.keymap.ui.content_access == [False]


# -- write_extension --------------------------------------------------------
#
# The fence, not the happy path, is what these pin: this is the one tool that
# puts bytes on disk, and every refusal below is a property the design argument
# in doc/dev/ai-integration.md depends on holding.

def extensions(registry):
    return pathlib.Path(registry.keymap.extensions_dir)


@pytest.mark.parametrize("name", [
    "../config", "sub/thing", "thing.py", "", "2legit", "import", "a b",
    "/etc/passwd", "..",
])
def test_only_a_module_name_gets_through(writable, name):
    result = writable.call("write_extension", {"name": name, "source": "x = 1\n"})
    assert "cannot be a module name" in result
    assert not extensions(writable).exists()


def test_source_that_does_not_parse_never_reaches_the_disk(writable):
    """A truncated transfer must not replace a working action."""
    writable.call("write_extension", {"name": "thing", "source": "x = 1\n"})
    good = extensions(writable) / "thing.py"

    result = writable.call("write_extension",
                           {"name": "thing", "source": "def broken(:\n"})
    assert "does not parse" in result and "line 1" in result
    assert good.read_text() == "x = 1\n"


# -- config.py --------------------------------------------------------------
#
# A separate pair from the extension one, because the blast radius differs: a
# module in extensions/ is inert until something names it, while this file runs
# at every start and takes the key bindings down with it if it is wrong.

@pytest.fixture
def configurable(tmp_path):
    keymap = FakeKeymap(extensions_dir=str(tmp_path / "extensions"))
    keymap.config_path = str(tmp_path / "config.py")
    return ToolRegistry(keymap)


def test_it_reads_and_replaces_config_py(configurable, tmp_path):
    (tmp_path / "config.py").write_text("def configure(keymap):\n    pass\n")
    assert "def configure" in configurable.call("read_config", {})

    result = configurable.call(
        "write_config", {"source": "def configure(keymap):\n    x = 1\n"})
    assert "wrote config.py" in result
    assert "x = 1" in (tmp_path / "config.py").read_text()


def test_the_previous_config_survives_a_write(configurable, tmp_path):
    """This is the file that stops Keyhac working, so the undo has to exist."""
    (tmp_path / "config.py").write_text("original = 1\n")
    configurable.call("write_config", {"source": "replacement = 2\n"})

    backups = list(tmp_path.glob("config.py.bak-*"))
    assert [b.read_text() for b in backups] == ["original = 1\n"]


def test_a_config_that_does_not_parse_never_reaches_the_disk(configurable,
                                                             tmp_path):
    (tmp_path / "config.py").write_text("good = 1\n")
    result = configurable.call("write_config", {"source": "def broken(:\n"})
    assert "does not parse" in result
    assert (tmp_path / "config.py").read_text() == "good = 1\n"


def test_writing_the_config_is_announced_on_the_console(configurable, tmp_path,
                                                        caplog):
    (tmp_path / "config.py").write_text("x = 1\n")
    with caplog.at_level(logging.INFO, logger="keyhac.MCP"):
        configurable.call("write_config", {"source": "x = 1\ny = 2\n"})
    assert any("config.py (+1/-0 lines)" in r.getMessage() for r in caplog.records)


def test_list_extensions_shows_the_files_list_actions_hides(writable):
    """The two lists answer different questions, and the file view is the one
    that can see a helper split out beside an action."""
    write_action(writable, name="act")
    write_action(writable, name="shared", source="VALUE = 1\n")
    write_action(writable, name="_private", source="VALUE = 1\n")

    files = writable.call("list_extensions", {})
    assert "act.py" in files and "OpenIssues" in files
    assert "shared.py" in files and "no ThreadedAction subclass" in files
    assert "_private.py" in files and "helper" in files

    runnable = writable.call("list_actions", {})
    assert "shared" not in runnable and "_private" not in runnable


def test_read_extension_reads_a_module_back(writable):
    """The counterpart write_extension needed: it replaces whole files, so an
    action changed without being read is reconstructed from a guess."""
    writable.call("write_extension", {"name": "thing", "source": "x = 1\n"})
    assert writable.call("read_extension", {"name": "thing"}) == "x = 1\n"


def test_reading_a_helper_the_listing_does_not_show(writable):
    """list_actions only reports action classes; a module split out beside one
    still has to be readable to be maintained."""
    write_action(writable, name="shared", source="VALUE = 1\n")
    assert "VALUE = 1" in writable.call("read_extension", {"name": "shared"})


def test_a_missing_module_says_what_is_there(writable):
    write_action(writable, name="present", source="x = 1\n")
    result = writable.call("read_extension", {"name": "absent"})
    assert "no absent.py" in result and "present" in result


@pytest.mark.parametrize("name", ["../config", "sub/thing", "thing.py", ".."])
def test_reading_is_fenced_by_the_same_module_name_rule(writable, name):
    with pytest.raises(ValueError, match="cannot be a module name"):
        writable.call("read_extension", {"name": name})


def test_an_oversized_module_is_refused_rather_than_truncated(writable,
                                                              monkeypatch):
    """Half a read feeding a whole-file write is how you lose the other half."""
    monkeypatch.setattr(tools_module, "MAX_SOURCE", 10)
    writable.call("write_extension", {"name": "big", "source": "x = 1\n" * 20})
    result = writable.call("read_extension", {"name": "big"})
    assert "Refusing rather than truncating" in result
    assert "x = 1" not in result, "it must not leak a partial file"


def test_it_writes_and_says_what_to_do_next(writable):
    result = writable.call("write_extension",
                           {"name": "open_issues", "source": "x = 1\n"})
    assert (extensions(writable) / "open_issues.py").read_text() == "x = 1\n"
    # It must not send the model to reload_config: the file is picked up by
    # being named, and a reload between rounds rebuilds live key bindings for
    # nothing.
    assert "start_action" in result and "no reload needed" in result


def test_replacing_keeps_the_previous_version(writable):
    writable.call("write_extension", {"name": "thing", "source": "x = 1\n"})
    writable.call("write_extension", {"name": "thing", "source": "x = 2\n"})

    assert (extensions(writable) / "thing.py").read_text() == "x = 2\n"
    backups = list(extensions(writable).glob("thing.py.bak-*"))
    assert [b.read_text() for b in backups] == ["x = 1\n"]


def test_backups_are_bounded(writable, monkeypatch):
    monkeypatch.setattr(tools_module, "_BACKUPS_KEPT", 2)
    # Distinct timestamps: the suffix has one-second resolution, and the prune
    # sorts by it.
    for revision in range(5):
        monkeypatch.setattr(tools_module.time, "strftime",
                            lambda fmt, r=revision: f".bak-2026080{r}-000000")
        writable.call("write_extension",
                      {"name": "thing", "source": f"x = {revision}\n"})

    backups = sorted(p.read_text() for p in extensions(writable).glob("thing.py.bak-*"))
    assert backups == ["x = 2\n", "x = 3\n"]


# -- delete_extension --------------------------------------------------------
#
# The property under all of these is that nothing leaves the disk: a delete is a
# rename into the same `.bak-` scheme a replace already uses, so the tool that
# sounds destructive is the one that destroys nothing.

def test_deleting_renames_rather_than_unlinking(writable):
    writable.call("write_extension", {"name": "thing", "source": "x = 1\n"})
    result = writable.call("delete_extension", {"name": "thing"})

    assert not (extensions(writable) / "thing.py").exists()
    backups = list(extensions(writable).glob("thing.py.bak-*"))
    assert [b.read_text() for b in backups] == ["x = 1\n"]
    assert backups[0].name in result, "and the reply names what to rename back"


def test_deleting_prunes_the_backups_like_a_write_does(writable, monkeypatch):
    monkeypatch.setattr(tools_module, "_BACKUPS_KEPT", 2)
    for revision in range(4):
        monkeypatch.setattr(tools_module.time, "strftime",
                            lambda fmt, r=revision: f".bak-2026080{r}-000000")
        writable.call("write_extension",
                      {"name": "thing", "source": f"x = {revision}\n"})
    monkeypatch.setattr(tools_module.time, "strftime",
                        lambda fmt: ".bak-20260809-000000")
    writable.call("delete_extension", {"name": "thing"})

    kept = sorted(p.read_text()
                  for p in extensions(writable).glob("thing.py.bak-*"))
    assert kept == ["x = 2\n", "x = 3\n"], "the deleted version is the newest"


@pytest.mark.parametrize("name", ["../config", "sub/thing", "thing.py", ".."])
def test_deleting_is_fenced_by_the_same_module_name_rule(writable, name):
    write_action(writable, name="present", source="x = 1\n")
    result = writable.call("delete_extension", {"name": name})
    assert "cannot be a module name" in result
    assert (extensions(writable) / "present.py").exists()


def test_deleting_something_that_is_not_there_says_what_is(writable):
    write_action(writable, name="present", source="x = 1\n")
    result = writable.call("delete_extension", {"name": "absent"})
    assert "no absent.py" in result and "present" in result


def test_deleting_a_module_the_config_imports_says_so(configurable, tmp_path):
    """The one way a delete can still hurt: config.py is not touched, so a
    module bound to a key takes the operator's whole file down at the next
    load - hours later, nowhere near this call."""
    (tmp_path / "config.py").write_text(
        "def configure(keymap):\n    import thing\n")
    write_action(configurable, name="thing", source="x = 1\n")
    write_action(configurable, name="other", source="x = 1\n")

    assert "config.py mentions thing" in \
        configurable.call("delete_extension", {"name": "thing"})
    assert "config.py" not in \
        configurable.call("delete_extension", {"name": "other"})


def test_the_delete_is_announced_on_the_console(writable, caplog):
    """A file can leave the directory without appearing in the conversation."""
    write_action(writable, name="thing", source="x = 1\n")
    with caplog.at_level(logging.INFO, logger="keyhac.MCP"):
        writable.call("delete_extension", {"name": "thing"})
    assert any("Deleted" in r.getMessage() and "thing.py" in r.getMessage()
               for r in caplog.records)


def test_a_deleted_module_stops_being_listed(writable):
    write_action(writable, name="thing")
    assert "thing.OpenIssues" in writable.call("list_actions", {})

    writable.call("delete_extension", {"name": "thing"})
    assert "thing.OpenIssues" not in writable.call("list_actions", {})
    assert "thing.py" not in writable.call("list_extensions", {}), \
        "and the backup beside it is not a .py, so nothing importable is left"


def test_the_write_is_announced_on_the_console(writable, caplog):
    with caplog.at_level(logging.INFO, logger="keyhac.MCP"):
        writable.call("write_extension", {"name": "thing", "source": "x = 1\n"})
        writable.call("write_extension", {"name": "thing", "source": "x = 1\ny = 2\n"})

    messages = [record.getMessage() for record in caplog.records]
    assert any("thing.py (new, 1 lines)" in m for m in messages)
    assert any("thing.py (+1/-0 lines)" in m for m in messages)


# -- the action classes in extensions/ ---------------------------------------
#
# The property worth guarding hardest is that listing does not execute: the
# whole point of the AST scan is that a directory of half-finished actions
# stays inert until something names one.

SAMPLE = '''\
from keyhac import ThreadedAction

RAN_AT_IMPORT.append("yes")          # NameError unless someone injected it


class OpenIssues(ThreadedAction):
    """Opens the issue list."""

    def run(self):
        return "ran"


class NeedsArgs(ThreadedAction):
    def __init__(self, target, limit=10):
        self.target = target


class NotAnAction:
    pass
'''


def write_action(registry, name="thing", source=SAMPLE):
    directory = extensions(registry)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(source)
    return directory / f"{name}.py"


def test_listing_does_not_import_anything(writable):
    """The module raises at import; listing it must still work."""
    write_action(writable)
    result = writable.call("list_actions", {})

    assert "thing.OpenIssues: Opens the issue list." in result
    assert "thing" not in sys.modules


def test_a_class_that_is_not_an_action_is_not_listed(writable):
    write_action(writable)
    assert "NotAnAction" not in writable.call("list_actions", {})


def test_one_needing_arguments_says_so_instead_of_offering_itself(writable):
    write_action(writable)
    result = writable.call("list_actions", {})
    assert "thing.NeedsArgs: needs constructor arguments (target)" in result


def test_starting_one_that_needs_arguments_is_refused(writable):
    write_action(writable)
    with pytest.raises(KeyError, match="constructor arguments.*target"):
        writable.call("start_action", {"name": "thing.NeedsArgs"})


def test_a_file_that_does_not_parse_is_skipped_not_reported(writable):
    write_action(writable, name="broken", source="def nope(:\n")
    write_action(writable, name="good")
    result = writable.call("list_actions", {})
    assert "good.OpenIssues" in result and "broken" not in result


def test_underscore_files_are_helpers_not_actions(writable):
    write_action(writable, name="_helpers")
    assert "OpenIssues" not in writable.call("list_actions", {})


# -- inheritance, without importing (issue #43) ------------------------------
#
# Reusing an action by subclassing it is the natural thing to write, and it was
# the one way to write one this could not see: matching a *direct* base spelled
# ThreadedAction meant `class B(A)` vanished while `class B(A, ThreadedAction)`
# - identical MRO, the base named twice - appeared. The workaround was a line
# of code written to satisfy a scanner.

INHERITED = '''\
from keyhac import ThreadedAction


class TranslateClipboard(ThreadedAction):
    """Translate the clipboard."""

    def run(self):
        return "clipboard"


class TranslateSelection(TranslateClipboard):
    """Translate the selection."""

    def run(self):
        return "selection"
'''


def test_a_subclass_of_an_action_is_an_action(writable):
    write_action(writable, source=INHERITED)
    result = writable.call("list_actions", {})
    assert "thing.TranslateSelection: Translate the selection." in result


def test_it_follows_a_base_class_into_another_module(writable):
    """An action's base often lives in the helper file beside it."""
    write_action(writable, name="_base", source=(
        "from keyhac import ThreadedAction\n\n\n"
        "class Base(ThreadedAction):\n"
        "    def run(self): ...\n"))
    write_action(writable, name="derived", source=(
        "from _base import Base\n\n\n"
        "class Derived(Base):\n"
        '    """Built on the helper."""\n'
        "    def run(self): ...\n"))

    result = writable.call("list_actions", {})
    assert "derived.Derived: Built on the helper." in result
    assert "_base.Base" not in result, "a helper file is still not offered"


def test_it_follows_a_base_reached_through_a_module(writable):
    write_action(writable, name="_base", source=(
        "from keyhac import ThreadedAction\n\n\n"
        "class Base(ThreadedAction):\n"
        "    def run(self): ...\n"))
    write_action(writable, name="dotted", source=(
        "import _base\n\n\n"
        "class Dotted(_base.Base):\n"
        '    """Reached through the module."""\n'
        "    def run(self): ...\n"))

    assert "dotted.Dotted: Reached through the module." in \
        writable.call("list_actions", {})


def test_a_class_inheriting_something_else_is_still_not_an_action(writable):
    """The walk must widen what is found, not stop discriminating."""
    write_action(writable, source=(
        "class Helper:\n    pass\n\n\n"
        "class Plain(Helper):\n    pass\n"))
    assert "Plain" not in writable.call("list_actions", {})


def test_a_base_cycle_does_not_hang_the_listing(writable):
    """Half a file is not a finding, and a file being edited can say this.

    Reaching the assertion at all is most of the test: an unguarded walk
    recurses until the interpreter stops it.
    """
    write_action(writable, source=(
        "class A(B):\n    pass\n\n\n"
        "class B(A):\n    pass\n"))
    result = writable.call("list_actions", {})
    assert "thing.A" not in result and "thing.B" not in result


def test_inheritance_is_resolved_without_importing(writable):
    """The property the whole AST scan exists for still holds."""
    write_action(writable, name="explodes", source=(
        "from keyhac import ThreadedAction\n"
        "raise AssertionError('imported')\n\n\n"
        "class Base(ThreadedAction):\n    pass\n\n\n"
        "class Derived(Base):\n    pass\n"))

    assert "explodes.Derived" in writable.call("list_actions", {})
    assert "explodes" not in sys.modules


# -- loading one -------------------------------------------------------------

RUNNABLE = '''\
from keyhac import ThreadedAction

VERSION = {version}


class Thing(ThreadedAction):
    def run(self):
        return VERSION
'''


def test_it_runs_without_any_config_edit(writable):
    write_action(writable, source=RUNNABLE.format(version=1))
    writable.call("start_action", {"name": "thing.Thing"})
    result = writable.call("get_action_result", {"name": "thing.Thing", "wait": 5})
    assert "finished" in result
    assert writable.keymap.reloaded == 0        # no reload_config needed


def test_an_edited_file_is_reimported_without_a_reload(writable):
    """Round two of a fix loop must not run round one's code."""
    path = write_action(writable, source=RUNNABLE.format(version=1))
    first = writable._startable("thing.Thing")

    # A distinct mtime: the cache is keyed on it, and a fast test can land
    # inside one timestamp tick.
    path.write_text(RUNNABLE.format(version=2))
    os.utime(path, (0, 0))

    second = writable._startable("thing.Thing")
    assert second is not first
    assert sys.modules["thing"].VERSION == 2


def test_an_edited_helper_is_reimported_too(writable):
    """An action split across two files must not run half of round two.

    The action's own module comes back by path; anything it imports by name
    would answer out of sys.modules, so the helper is where staleness hides -
    and it hides silently, reporting success against the previous version.
    """
    directory = extensions(writable)
    write_action(writable, name="shared", source="VALUE = 1\n")
    write_action(writable, name="act", source=(
        "from keyhac import ThreadedAction\n"
        "import shared\n\n\n"
        "class Act(ThreadedAction):\n"
        "    def run(self):\n"
        "        return shared.VALUE\n"))
    assert writable._startable("act.Act").run() == 1

    (directory / "shared.py").write_text("VALUE = 2\n")
    os.utime(directory / "act.py", (0, 0))
    assert writable._startable("act.Act").run() == 2


def test_an_unchanged_file_keeps_its_instance(writable):
    """cancel_action reaches the running object by looking it up again."""
    write_action(writable, source=RUNNABLE.format(version=1))
    assert writable._startable("thing.Thing") is writable._startable("thing.Thing")


# -- one module, not two (issue #40) -----------------------------------------

STATEFUL = '''\
import itertools

from keyhac import ThreadedAction

RUNS = itertools.count(1)


class Counted(ThreadedAction):
    def run(self):
        return next(RUNS)
'''


@pytest.fixture
def clean_modules():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]


def test_it_runs_the_module_the_config_imported(writable, clean_modules):
    """Issue #40: start_action used to re-import, unconditionally.

    So a class on the operator's key and the same class started from here came
    from two different module objects. Anything the module held existed twice
    and neither copy saw the other's writes - which showed up as a run counter
    going 3, then 2, with the 2 twenty-five seconds later.
    """
    write_action(writable, name="counted", source=STATEFUL)
    directory = str(extensions(writable))

    keymap_module.Keymap._prepare_extensions(directory)
    import counted                                    # what config.py does
    keymap_module.Keymap._stamp_extensions(directory)  # end of configure()

    started = writable._startable("counted.Counted")

    assert type(started) is counted.Counted, "a second copy of the class"
    assert sys.modules["counted"] is counted

    # The counter is the visible half: two modules would restart it.
    assert counted.Counted().run() == 1
    assert started.run() == 2


def test_an_edited_helper_wins_over_the_loaded_module(writable, clean_modules):
    """Sharing must not become the staleness it replaced.

    An action reaches its helper through sys.modules, so reusing a module
    whose *helper* moved would run the edited half against the previous
    version of the other and report success - the failure the directory-wide
    eviction exists to prevent, walked back in through the door #40's fix
    opened.
    """
    directory = extensions(writable)
    write_action(writable, name="_lib", source="VALUE = 1\n")
    write_action(writable, name="uses_lib", source=(
        "from keyhac import ThreadedAction\n"
        "import _lib\n\n\n"
        "class Uses(ThreadedAction):\n"
        "    def run(self):\n"
        "        return _lib.VALUE\n"))

    keymap_module.Keymap._prepare_extensions(str(directory))
    import uses_lib                                    # noqa: F401
    keymap_module.Keymap._stamp_extensions(str(directory))

    (directory / "_lib.py").write_text("VALUE = 2\n")   # only the helper moved

    assert writable._startable("uses_lib.Uses").run() == 2


def test_an_unrelated_edit_does_not_split_the_module(writable, clean_modules):
    """The reason freshness follows the import graph and not the directory.

    In a fix loop some file is nearly always newer than the last configuration
    load. If any of them forced a re-import, every action but the one being
    edited would go back to running its own private copy - which is issue #40
    with extra steps.
    """
    directory = extensions(writable)
    write_action(writable, name="quiet", source=STATEFUL)
    write_action(writable, name="noisy", source=RUNNABLE.format(version=1))

    keymap_module.Keymap._prepare_extensions(str(directory))
    import quiet
    keymap_module.Keymap._stamp_extensions(str(directory))

    (directory / "noisy.py").write_text(RUNNABLE.format(version=2))

    assert type(writable._startable("quiet.Counted")) is quiet.Counted


def test_an_edit_still_wins_over_the_loaded_module(writable, clean_modules):
    """Sharing must not become staleness.

    Until the operator reloads, the class on their key genuinely *is* the
    previous version - so the moment the file moves, this has to go back to
    importing its own.
    """
    path = write_action(writable, name="moved", source=RUNNABLE.format(version=1))
    directory = str(extensions(writable))

    keymap_module.Keymap._prepare_extensions(directory)
    import moved                                       # noqa: F401
    keymap_module.Keymap._stamp_extensions(directory)

    path.write_text(RUNNABLE.format(version=2))
    os.utime(path, (0, 0))

    assert writable._startable("moved.Thing").run() == 2


# -- both ways of starting one answer to one name (issue #42) ----------------
#
# get_action_result returned only what start_action had run. Press the key the
# action is bound to and it handed back the *previous* MCP run, unchanged -
# same text, same counter - while list_actions said "not run yet" about an
# action the operator had just run. The two paths filed their records under
# different keys: repr(self) for a key press, module.Class for the tool. It
# cost one wrong conclusion ("the key press is not reaching the hook") about a
# hook that was working.

def _run_as_a_key_press(action):
    """What ThreadedAction._run_tracked does: cancellable() with no name.

    The cancellation is swallowed the way the pool swallows it - it lands in
    the future, and _finish logs it - rather than being left to surface out of
    a bare thread.
    """
    from keyhac.core.action import ActionCancelled

    try:
        with action.cancellable():
            return action.run()
    except ActionCancelled:
        return None


def test_a_key_press_run_is_readable_by_name(engine, tmp_path, clean_modules):
    keymap = engine(lambda keymap: None).keymap
    directory = keymap.extensions_dir
    os.makedirs(directory, exist_ok=True)
    pathlib.Path(directory, "pressed.py").write_text(RUNNABLE.format(version=7))

    keymap_module.Keymap._prepare_extensions(directory)
    import pressed
    _run_as_a_key_press(pressed.Thing())          # the operator's own binding

    registry = ToolRegistry(keymap)
    result = registry.call("get_action_result",
                           {"name": "pressed.Thing", "wait": 0})
    assert "finished" in result, result
    assert "has not been run" not in result
    assert "last run: finished" in registry.call("list_actions", {})


def test_a_key_press_run_is_cancellable_by_name(engine, tmp_path, clean_modules):
    """The other half: cancel_action reaches instances it did not create."""
    keymap = engine(lambda keymap: None).keymap
    directory = keymap.extensions_dir
    os.makedirs(directory, exist_ok=True)
    pathlib.Path(directory, "held.py").write_text(SLOW.replace("Slow", "Held"))

    keymap_module.Keymap._prepare_extensions(directory)
    import held
    action = held.Held()
    thread = threading.Thread(target=_run_as_a_key_press, args=(action,),
                              daemon=True)
    thread.start()

    registry = ToolRegistry(keymap)
    deadline = time.monotonic() + 5
    while capture.get_run("held.Held") is None and time.monotonic() < deadline:
        time.sleep(0.01)

    registry.call("cancel_action", {"name": "held.Held"})
    thread.join(timeout=5)
    assert "cancelled" in registry.call("get_action_result",
                                        {"name": "held.Held", "wait": 5})


def test_an_action_outside_extensions_keeps_its_repr(engine):
    """`LaunchApplication("Terminal.app")` says which application.

    `keyhac.core.action.LaunchApplication` says neither that nor which of two
    bindings ran, so the class-derived name is for `extensions/` only.
    """
    engine(lambda keymap: None)
    from keyhac.core.action import LaunchApplication

    action = LaunchApplication("Terminal.app")
    assert action._run_name() == 'LaunchApplication("Terminal.app")'


SLOW = '''\
import time

from keyhac import ThreadedAction, getLogger

logger = getLogger("Slow")


class Slow(ThreadedAction):
    def run(self):
        logger.info("started")
        for _ in range(200):
            self.check_cancelled()
            time.sleep(0.01)
        return "finished the whole loop"
'''


def test_an_action_is_asynchronous_and_reports_what_it_logged(writable):
    """The authoring loop's three calls: start, collect, read."""
    write_action(writable, source=SLOW)

    started = writable.call("start_action", {"name": "thing.Slow"})
    assert "started" in started

    # Returned before the action finished - that is the contract that lets
    # these drive real applications for minutes.
    looking = writable.call("get_action_result", {"name": "thing.Slow", "wait": 0})
    assert "RUNNING" in looking or "running" in looking

    writable.call("cancel_action", {"name": "thing.Slow"})
    result = writable.call("get_action_result", {"name": "thing.Slow", "wait": 5})
    assert "cancelled" in result
    assert "started" in result           # what it logged comes back too


def test_one_that_raises_hands_back_its_traceback(writable):
    write_action(writable, source=(
        "from keyhac import ThreadedAction\n\n\n"
        "class Boom(ThreadedAction):\n"
        "    def run(self):\n"
        "        raise ValueError('the selector matched nothing')\n"))

    writable.call("start_action", {"name": "thing.Boom"})
    result = writable.call("get_action_result", {"name": "thing.Boom", "wait": 5})
    assert "failed" in result
    assert "the selector matched nothing" in result
    assert "Traceback" in result


def test_a_started_run_stays_readable_after_the_window_shuts(writable):
    """The run already happened; its traceback must not become unreachable."""
    write_action(writable, source=RUNNABLE.format(version=1))
    writable.call("start_action", {"name": "thing.Thing"})
    writable.keymap.action_authoring_allowed = False

    result = writable.call("get_action_result", {"name": "thing.Thing", "wait": 5})
    assert "finished" in result


def test_reloading_the_config_keeps_a_run_cancellable(writable):
    """A reload must not cost the operator the stop button.

    cancel_action reaches a running action by looking its name up again,
    so dropping the cache here would strand whatever is mid-run. Staleness
    is handled by mtime instead, which covers a hand edit too.
    """
    write_action(writable, source=RUNNABLE.format(version=1))
    first = writable._startable("thing.Thing")
    writable.call("reload_config", {})
    assert writable._loader.cached("thing.Thing") is first


# -- the endpoint closes itself ---------------------------------------------
#
# One switch with a deadline, rather than two switches one of which nobody
# turns off. What lapses is the whole endpoint, so these drive a real Keymap.

def test_the_endpoint_is_off_until_asked(engine):
    assert engine(lambda keymap: None).keymap.mcp_server_running is False


def test_it_closes_itself(engine, monkeypatch):
    monkeypatch.setattr(keymap_module, "_AUTHORING_WINDOW", 0.1)
    keymap = engine(lambda keymap: None).keymap
    keymap.start_mcp_server()
    assert keymap.mcp_server_running is True

    deadline = time.monotonic() + 5
    while keymap.mcp_server_running and time.monotonic() < deadline:
        time.sleep(0.02)
    assert keymap.mcp_server_running is False, "the window never closed"


def test_reopening_after_a_manual_stop_gets_a_whole_window(engine, monkeypatch):
    """A cancelled timer must not close the *next* window.

    The failure this guards is quiet and confusing: the operator turns the
    endpoint off and straight back on, and it dies seconds later because the
    first window's timer was still counting.
    """
    monkeypatch.setattr(keymap_module, "_AUTHORING_WINDOW", 0.2)
    keymap = engine(lambda keymap: None).keymap
    keymap.start_mcp_server()
    keymap.stop_mcp_server()

    monkeypatch.setattr(keymap_module, "_AUTHORING_WINDOW", 30)
    keymap.start_mcp_server()
    try:
        time.sleep(0.5)                   # the first timer would have fired
        assert keymap.mcp_server_running is True
    finally:
        keymap.stop_mcp_server()


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


def test_the_reply_goes_out_as_utf8_not_escapes(server):
    """A tool reply is a UI tree, so on a Japanese system it is mostly
    non-ASCII, and `\\uXXXX` spends six bytes on each character UTF-8 carries in
    three. Asserted on the bytes: a client would decode either form to the same
    string, so only the wire shows it."""
    server.registry.keymap.node.name = "セカイブラウザ"
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "describe_screen"}}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {server.token}"},
        method="POST")
    with urllib.request.urlopen(request, timeout=10) as reply:
        body = reply.read()
        # A Content-Length counted off a different string than the one written
        # is a hang, not a wrong answer - urlopen would wait for bytes that
        # never come. Reading the whole body under a timeout is that assertion.
        assert int(reply.headers["Content-Length"]) == len(body)

    assert "セカイブラウザ".encode() in body
    assert b"\\u30bb" not in body
    assert json.loads(body)["result"]["content"][0]["text"], "and it still parses"


def _no_other_user_can_read(path):
    """Windows' answer to the 0600 question, asked of the mechanism it uses.

    `os.open(..., 0o600)` is not a lie on Windows so much as a no-op: only the
    read-only bit is honoured, so `S_IMODE` reads 0o666 for a file no other
    user can open. What actually protects the token is the ACL, so that is
    what gets asserted - no ACE for Everyone or for BUILTIN\\Users. icacls
    ships with Windows, which pywin32 does not, and keyhac does not depend on
    it.
    """
    out = subprocess.run(["icacls", path], capture_output=True, text=True,
                         check=True).stdout
    # icacls prints the *first* ACE on the same line as the path, and the path
    # has a colon of its own ("C:\..."), so neither "skip line one" nor "split
    # on :" survives contact. Drop the path, then read one "PRINCIPAL:(FLAGS)"
    # per line. Getting this wrong is how the first version of this helper
    # passed a file it had just granted Everyone:(R) to.
    grantees = []
    for line in out.replace(path, "", 1).splitlines():
        line = line.strip()
        if not line:
            break                       # the summary line follows the ACEs
        grantees.append(line.split(":(")[0].strip())
    broad = ("everyone", "authenticated users", "users")
    return [g for g in grantees if g.split("\\")[-1].lower() in broad]


def test_the_endpoint_file_is_private_and_complete(server):
    path = server.endpoint_path
    if sys.platform == "win32":
        # Same property, different mechanism - see _no_other_user_can_read.
        assert not _no_other_user_can_read(path), "token is readable by other users"
    else:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, "token is world-readable"
    published = json.loads(open(path).read())
    assert published["port"] == server.port
    assert published["token"] == server.token


def test_the_bridge_path_is_published_when_there_is_one(tmp_path, registry,
                                                        monkeypatch):
    """A stdio-only client needs an absolute path, and inside an app bundle that
    path is four levels deep. Publishing it beside the port is what lets a client
    be configured from the endpoint file alone rather than by hand."""
    fake = tmp_path / "bin" / "keyhac-mcp-bridge"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(server_module, "bridge_command", lambda: str(fake))

    served = MCPServer(registry, str(tmp_path / "mcp.json"))
    served.start()
    try:
        assert json.loads(open(served.endpoint_path).read())["bridge"] == str(fake)
    finally:
        served.stop()


def test_no_bridge_key_when_the_install_generated_no_script(tmp_path, registry,
                                                            monkeypatch):
    """Absent, not null: a reader treats the key's presence as "a stdio client
    is configurable", so a null would have to be special-cased everywhere."""
    monkeypatch.setattr(server_module, "bridge_command", lambda: None)

    served = MCPServer(registry, str(tmp_path / "mcp.json"))
    served.start()
    try:
        assert "bridge" not in json.loads(open(served.endpoint_path).read())
    finally:
        served.stop()


def test_the_bundled_bridge_is_found_beside_the_package(tmp_path, monkeypatch):
    """The macOS bundle layout: Contents/Resources/keyhac, with the console
    script build.sh writes at Contents/Resources/bin. Resolved from the module's
    own location, so it is right however Keyhac is running."""
    resources = tmp_path / "Contents" / "Resources"
    (resources / "keyhac" / "mcp").mkdir(parents=True)
    (resources / "bin").mkdir()
    script = resources / "bin" / "keyhac-mcp-bridge"
    script.write_text("#!/bin/sh\n")

    monkeypatch.setattr(server_module, "__file__",
                        str(resources / "keyhac" / "mcp" / "server.py"))
    assert server_module.bridge_command() == str(script)


def test_the_venv_script_is_found_without_being_on_path(tmp_path, monkeypatch):
    """`make run` is `.venv/bin/python -m keyhac`, which never puts .venv/bin on
    PATH - so a PATH-only search misses the console script pip generated for the
    very install doing the publishing, and reports either nothing or some other
    Keyhac's bridge. Found the hard way: deleting an unrelated bridge from
    ~/.local/bin left a running checkout with no bridge at all."""
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    script = venv_bin / "keyhac-mcp-bridge"
    script.write_text("#!/bin/sh\n")

    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    monkeypatch.setattr(shutil, "which", lambda _: None)   # nothing on PATH
    assert server_module.bridge_command() == str(script)


def test_the_install_we_belong_to_wins_over_path(tmp_path, monkeypatch):
    """A bridge from an unrelated install forwards fine but resolves the config
    directory with its own keyhac.core.paths, so the two can disagree about
    where mcp.json lives. Prefer ours whenever there is one."""
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    ours = venv_bin / "keyhac-mcp-bridge"
    ours.write_text("#!/bin/sh\n")

    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    monkeypatch.setattr(shutil, "which", lambda _: "/somewhere/else/keyhac-mcp-bridge")
    assert server_module.bridge_command() == str(ours)


def test_the_windows_bundle_publishes_the_exe_and_not_the_cmd(tmp_path,
                                                              monkeypatch):
    """Both sit at the bundle root - the .cmd only forwards to the .exe now,
    and is kept for configs written when it was the whole thing. Publishing the
    real one keeps a freshly-configured client one process shorter."""
    root = tmp_path / "Keyhac"
    (root / "app" / "keyhac" / "mcp").mkdir(parents=True)
    for name in ("keyhac-mcp-bridge.exe", "keyhac-mcp-bridge.cmd"):
        (root / name).write_text("")

    monkeypatch.setattr(server_module, "__file__",
                        str(root / "app" / "keyhac" / "mcp" / "server.py"))
    monkeypatch.setattr(server_module, "running_packaged", lambda: False)
    assert server_module.bridge_command() == str(root / "keyhac-mcp-bridge.exe")


def test_a_packaged_install_publishes_the_alias_not_the_package_copy(tmp_path,
                                                                     monkeypatch):
    """The bridge inside C:\\Program Files\\WindowsApps is readable and cannot
    be executed by anyone - "Access is denied", whatever the ACL says, because
    a packaged binary starts only with its package's identity. Publishing it
    gave Claude Desktop a server that died at startup with no output. The app
    execution alias is the supported way in."""
    root = tmp_path / "WindowsApps" / "craftware.Keyhac_2.2.3.0_x64__abc"
    (root / "app" / "keyhac" / "mcp").mkdir(parents=True)
    (root / "keyhac-mcp-bridge.exe").write_text("")     # present, unusable
    alias = tmp_path / "Local" / "Microsoft" / "WindowsApps"
    alias.mkdir(parents=True)
    (alias / "keyhac-mcp-bridge.exe").write_text("")

    monkeypatch.setattr(server_module, "__file__",
                        str(root / "app" / "keyhac" / "mcp" / "server.py"))
    monkeypatch.setattr(server_module, "running_packaged", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert server_module.bridge_command() == str(alias / "keyhac-mcp-bridge.exe")


def test_a_packaged_install_with_no_alias_publishes_nothing(tmp_path,
                                                            monkeypatch):
    """Turning the alias off in Settings > Apps > Advanced app settings is the
    user's to do. Nothing is then configurable, and saying so beats publishing
    the package copy - the key's absence already means "no stdio client here"."""
    root = tmp_path / "WindowsApps" / "craftware.Keyhac_2.2.3.0_x64__abc"
    (root / "app" / "keyhac" / "mcp").mkdir(parents=True)
    (root / "keyhac-mcp-bridge.exe").write_text("")
    (tmp_path / "Local" / "Microsoft" / "WindowsApps").mkdir(parents=True)

    monkeypatch.setattr(server_module, "__file__",
                        str(root / "app" / "keyhac" / "mcp" / "server.py"))
    monkeypatch.setattr(server_module, "running_packaged", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert server_module.bridge_command() is None


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


def test_the_bridge_reads_its_pipe_as_utf8(tmp_path, monkeypatch):
    """MCP's stdio transport is UTF-8; Windows gives a pipe the ANSI code page
    instead - cp1252 on a US install, cp932 on a Japanese one. Every non-ASCII
    argument a client sent (a window name, a path) reached the daemon as
    mojibake, and it looked like a Keyhac bug rather than an encoding one."""
    import io
    from keyhac.mcp import bridge

    endpoint = tmp_path / "mcp.json"
    endpoint.write_text(json.dumps({"port": 1, "token": "t"}))
    monkeypatch.setattr(bridge, "endpoint_path", lambda config=None: str(endpoint))

    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"arguments": {"name": "メモ帳"}}},
                         ensure_ascii=False) + "\n"
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(
        io.BytesIO(request.encode("utf-8")), encoding="cp1252"))

    forwarded = []

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def read(self): return b""

    def capture(prepared, timeout=None):
        forwarded.append(prepared.data)
        return Reply()

    monkeypatch.setattr(bridge.urllib.request, "urlopen", capture)
    bridge.main([])

    sent = json.loads(forwarded[0].decode("utf-8"))
    assert sent["params"]["arguments"]["name"] == "メモ帳"


def test_the_bridge_writes_its_pipe_as_utf8(tmp_path, monkeypatch):
    """The other half of the same reconfigure, and it only became load-bearing
    when the daemon stopped `\\uXXXX`-escaping its replies: until then every
    body was ASCII whatever the pipe's code page was, so a cp932 stdout could
    not fail on one. Now a reply carries the window titles it read."""
    import io
    from keyhac.mcp import bridge

    endpoint = tmp_path / "mcp.json"
    endpoint.write_text(json.dumps({"port": 1, "token": "t"}))
    monkeypatch.setattr(bridge, "endpoint_path", lambda config=None: str(endpoint))
    monkeypatch.setattr("sys.stdin", io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"))

    served = json.dumps({"jsonrpc": "2.0", "id": 1,
                         "result": {"text": "メモ帳 - 無題"}},
                        ensure_ascii=False).encode("utf-8")

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def read(self): return served

    monkeypatch.setattr(bridge.urllib.request, "urlopen",
                        lambda prepared, timeout=None: Reply())
    written = io.BytesIO()
    monkeypatch.setattr("sys.stdout", io.TextIOWrapper(written, encoding="cp1252"))
    bridge.main([])

    relayed = json.loads(written.getvalue().decode("utf-8"))
    assert relayed["result"]["text"] == "メモ帳 - 無題"


def test_the_bridge_prints_the_tool_list_the_daemon_serves(tmp_path, capsys,
                                                          monkeypatch):
    """--tools exists for a client with a shell and no MCP transport. It must
    print what the daemon answered and nothing of its own: a bridge that
    reformatted or filtered the schemas could drift from the tools that are
    actually there, which is the one thing this file may not do."""
    from keyhac.mcp import bridge

    endpoint = tmp_path / "mcp.json"
    endpoint.write_text(json.dumps({"port": 1, "token": "t"}))
    monkeypatch.setattr(bridge, "endpoint_path", lambda config=None: str(endpoint))

    tools = [{"name": "list_windows", "description": "メモ帳 windows",
              "inputSchema": {"type": "object", "properties": {}}}]
    served = json.dumps({"jsonrpc": "2.0", "id": 1,
                         "result": {"tools": tools}}).encode("utf-8")

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def read(self): return served

    monkeypatch.setattr(bridge.urllib.request, "urlopen",
                        lambda prepared, timeout=None: Reply())

    assert bridge.main(["--tools"]) == 0
    assert json.loads(capsys.readouterr().out) == tools


def test_the_tools_option_never_reads_stdin(tmp_path, monkeypatch):
    """It is a one-shot query, not a pump. Left reading stdin it would hang a
    shell that ran it without redirecting one."""
    import io
    from keyhac.mcp import bridge

    endpoint = tmp_path / "mcp.json"
    endpoint.write_text(json.dumps({"port": 1, "token": "t"}))
    monkeypatch.setattr(bridge, "endpoint_path", lambda config=None: str(endpoint))

    class Exploding(io.StringIO):
        def __iter__(self): raise AssertionError("--tools read stdin")

    monkeypatch.setattr("sys.stdin", Exploding(""))

    served = json.dumps({"jsonrpc": "2.0", "id": 1,
                         "result": {"tools": []}}).encode("utf-8")

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def read(self): return served

    monkeypatch.setattr(bridge.urllib.request, "urlopen",
                        lambda prepared, timeout=None: Reply())
    assert bridge.main(["--tools"]) == 0


def test_the_tools_option_fails_loudly_without_a_daemon(tmp_path, capsys,
                                                       monkeypatch):
    """The same missing daemon the pump explains, but a shell caller reads exit
    codes and pipes stdout: the explanation goes to stderr so a parser on the
    other end of the pipe is handed empty input rather than prose."""
    from keyhac.mcp import bridge

    monkeypatch.setattr(bridge, "endpoint_path",
                        lambda config=None: str(tmp_path / "absent.json"))

    assert bridge.main(["--tools"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "MCP server" in captured.err


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
    assert "[truncated:" not in text, "the misleading advice won"


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
    assert "[truncated:" in text
    assert "enable_content_access" not in text


def test_the_truncation_note_reports_the_shape_of_the_cut(registry):
    """Issue #54: "raise max_nodes/max_depth" without saying from what left
    the next value a guess, and a measured session guessed twice. The walker
    knew the numbers all along."""
    from keyhac.mcp.tools import EMPTY_WINDOW

    root = registry.keymap.node
    root.children = [FakeNode(f"child{i}", depth=1)
                     for i in range(EMPTY_WINDOW + 5)]
    root.children[0].truncated = True     # a budget cut, shallow
    root.children[0].depth = 3
    root.children[1].truncated = True     # a depth cut, at the bound
    root.children[1].depth = 14
    text = registry.call("describe_screen", {})
    assert f"reported {EMPTY_WINDOW + 6} node(s)" in text
    assert "cut off at 2 point(s)" in text
    assert "1 by max_depth=14" in text
    assert "1 by the max_nodes=400 budget" in text
    assert "deepest level reached: 14" in text


# -- what an action hands back ----------------------------------------------
#
# Three things reached the console window and not the model. Each of these
# fails silently if it regresses: the tool still returns *something*, just
# without the line that says what went wrong.

PROBE = '''\
from keyhac import ThreadedAction

RUN = None                       # the test installs its body after import


class Probe(ThreadedAction):
    def run(self):
        return RUN()
'''


def _register(writable, run, name="probe"):
    """Run the test's callable as an action class, and collect what it produced.

    Actions reach the endpoint only as classes in `extensions/` now, so the
    body has to arrive through a file. Importing it first and installing the
    callable afterwards keeps these tests written as closures over their own
    state - `_startable` hands back the cached instance the second time,
    because the file has not moved.
    """
    write_action(writable, name=name, source=PROBE)
    writable._startable(f"{name}.Probe")
    sys.modules[name].RUN = run
    writable.call("start_action", {"name": f"{name}.Probe"})
    return writable.call("get_action_result",
                         {"name": f"{name}.Probe", "wait": 20})


def test_print_reaches_the_model(writable):
    """The shipped config.py template teaches print() on the same line as the
    logger. It reached the console window and stopped there."""
    assert "printed this" in _register(writable, lambda: print("printed this"))


def test_print_still_reaches_the_console(writable):
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
        _register(writable, lambda: print("both places"))
    finally:
        sys.stdout = original
    assert any("both places" in text for text in seen)


def test_a_logger_outside_the_keyhac_tree_is_captured(writable):
    """What getLogger(__name__) produces in a module under extensions/."""
    import logging as stdlib_logging

    def run():
        stdlib_logging.getLogger("my_extension").info("from an extension")

    assert "from an extension" in _register(writable, run)


def test_the_documented_logger_is_still_captured(writable):
    """`keyhac` is configured with propagate=False, so a root-only handler sees
    none of it - the regression this pins."""
    from keyhac.core import log

    def run():
        log.getLogger("Probe").info("through keyhac's own logger")

    assert "through keyhac's own logger" in _register(writable, run)


def test_nothing_is_captured_twice(writable):
    from keyhac.core import log

    def run():
        log.getLogger("Probe").info("once please")

    assert _register(writable, run).count("once please") == 1


def test_a_failed_subprocess_hands_over_its_stderr(writable):
    """No stream wrapper can see this: the child writes to the real file
    descriptor, not to Python's sys.stderr. It survives only on the exception,
    and only when the action asked for it."""
    import subprocess

    def run():
        subprocess.run([sys.executable, "-c", "import sys; sys.stderr.write('the child complained'); sys.exit(3)"],
                       check=True, capture_output=True, text=True)

    output = _register(writable, run)
    assert "the child complained" in output


def test_a_subprocess_run_without_capture_says_so(writable):
    """Better than silence: "returned 1" with no reason is where the loop
    stalls, and the fix is a line in the action rather than a mystery."""
    import subprocess

    def run():
        subprocess.run([sys.executable, "-c", "import sys; sys.exit(4)"], check=True)

    output = _register(writable, run)
    assert "capture_output=True" in output


def test_a_long_run_is_bounded_and_says_it_was_truncated(writable):
    """A run logging a line per row over hundreds of rows would otherwise fill
    a context window with the middle of its own progress."""
    from keyhac.mcp.tools import MAX_CAPTURE

    def run():
        for index in range(2000):        # ~100k characters, fixed
            print(f"row {index} " + "x" * 40)
        print("THE LAST LINE")

    output = _register(writable, run)
    assert len(output) < MAX_CAPTURE * 1.5
    assert "characters dropped" in output
    assert "THE LAST LINE" in output, "the tail is where the failure is"


def _noisy_run(name):
    """A finished run whose output is the shape issue #71 measured: the two
    INFO lines that carry the result, buried in keymap DEBUG keystrokes."""
    run = capture.start_run(name)
    for index in range(50):
        run.output.write(f"DEBUG [keyhac.Keymap] D-Return {index}\n")
        run.output.write(f"DEBUG [keyhac.Keymap] PASSTHRU : U-Return {index}\n")
    run.output.write("INFO [keyhac.Probe] OK: 63 AZ x 2 row -> 126 rows\n")
    run.output.write("printed: DONE\n")
    run.finish("finished")
    return run


def test_the_keystroke_stream_is_hidden_by_default(registry):
    """Issue #71: thousands of keymap DEBUG lines buried the INFO lines that
    carried the result. INFO is the default, and the cut announces itself."""
    _noisy_run("noisy.Filtered")
    out = registry.call("get_action_result",
                        {"name": "noisy.Filtered", "wait": 0})
    assert "PASSTHRU" not in out
    assert "OK: 63 AZ x 2 row -> 126 rows" in out
    assert "printed: DONE" in out, "print() is not a log line to filter"
    assert "100 log line(s) below INFO hidden" in out


def test_the_keystroke_stream_is_one_call_away(registry):
    _noisy_run("noisy.Filtered")
    out = registry.call("get_action_result",
                        {"name": "noisy.Filtered", "wait": 0,
                         "level": "DEBUG"})
    assert "PASSTHRU : U-Return 49" in out
    assert "hidden" not in out


def test_tail_keeps_the_end_and_says_what_it_dropped(registry):
    _noisy_run("noisy.Tailed")
    out = registry.call("get_action_result",
                        {"name": "noisy.Tailed", "wait": 0,
                         "level": "DEBUG", "tail": 2})
    assert "showing the last 2 of 102 lines" in out
    assert "OK: 63" in out and "printed: DONE" in out
    assert "PASSTHRU" not in out, "the middle of the progress bar is gone"


def test_a_wrong_level_is_told_its_choices(registry):
    _noisy_run("noisy.Filtered")
    with pytest.raises(ValueError, match="DEBUG, INFO, WARNING"):
        registry.call("get_action_result",
                      {"name": "noisy.Filtered", "wait": 0,
                       "level": "VERBOSE"})


# -- the asynchronous shape --------------------------------------------------
#
# Starting and collecting are separate because the transport answers one
# message per request and §2's actions run for minutes. These pin the parts
# that only exist because of that.

GATED = '''\
import threading

from keyhac import ThreadedAction

GATE = threading.Event()         # the test releases it


class Slow(ThreadedAction):
    def run(self):
        print("started working")
        GATE.wait(20)
        print("done working")
'''


def _slow_action(writable, name="slow"):
    """An action class that blocks until the returned event is set."""
    write_action(writable, name=name, source=GATED)
    writable._startable(f"{name}.Slow")
    return sys.modules[name].GATE


def test_start_action_returns_before_the_action_finishes(writable):
    """The property the whole shape exists for: a call that waited for the end
    is a call that times out for exactly the workload this serves."""
    gate = _slow_action(writable)
    reply = writable.call("start_action", {"name": "slow.Slow"})
    assert "started" in reply
    assert "slow.Slow" in writable.call("list_actions", {})
    assert "RUNNING" in writable.call("list_actions", {})
    gate.set()
    writable.call("get_action_result", {"name": "slow.Slow", "wait": 20})


def test_still_running_is_an_answer_not_a_timeout(writable):
    gate = _slow_action(writable)
    writable.call("start_action", {"name": "slow.Slow"})
    reply = writable.call("get_action_result", {"name": "slow.Slow", "wait": 0})
    assert "still running" in reply
    assert "started working" in reply, "output so far comes back too"
    assert "again" in reply, "and it says what to do about it"
    gate.set()
    writable.call("get_action_result", {"name": "slow.Slow", "wait": 20})


def test_a_waiting_collect_returns_as_soon_as_it_ends(writable):
    """Waiting rather than polling is what keeps a fast action fast: two round
    trips, no added latency."""
    import threading as t
    import time as clock

    gate = _slow_action(writable)
    writable.call("start_action", {"name": "slow.Slow"})
    t.Timer(0.2, gate.set).start()
    began = clock.monotonic()
    reply = writable.call("get_action_result", {"name": "slow.Slow", "wait": 20})
    assert clock.monotonic() - began < 5, "it waited out the full timeout"
    assert "done working" in reply


CANCELLABLE = '''\
import threading

from keyhac import ThreadedAction
from keyhac.core.wait import wait_for

ENTERED = threading.Event()


class Slow(ThreadedAction):
    def run(self):
        ENTERED.set()
        wait_for(lambda: False, timeout=20, message="never", interval=0.01)
'''


def test_cancel_action_stops_it(writable):
    """The model can stop what it started - refusing that while allowing
    starting would be the odd asymmetry.

    Also pins that cancel reaches the *running* object: it looks the action
    up again, and a fresh instance per start would hand it a flag nobody is
    waiting on.
    """
    write_action(writable, name="slow2", source=CANCELLABLE)
    writable._startable("slow2.Slow")
    writable.call("start_action", {"name": "slow2.Slow"})

    assert sys.modules["slow2"].ENTERED.wait(5)
    assert "asked" in writable.call("cancel_action", {"name": "slow2.Slow"})
    assert "cancelled" in writable.call("get_action_result",
                                        {"name": "slow2.Slow", "wait": 20})


def test_collecting_an_action_that_never_ran(writable):
    write_action(writable, name="idle", source=RUNNABLE.format(version=1))
    assert "has not been run" in writable.call("get_action_result",
                                               {"name": "idle.Thing", "wait": 0})
    assert "not run yet" in writable.call("list_actions", {})


def test_starting_one_that_is_already_running_says_so(writable):
    gate = _slow_action(writable)
    writable.call("start_action", {"name": "slow.Slow"})
    assert "already running" in writable.call("start_action", {"name": "slow.Slow"})
    gate.set()
    writable.call("get_action_result", {"name": "slow.Slow", "wait": 20})


def test_an_unrelated_print_does_not_land_in_a_running_action(writable):
    """A run lasts minutes. A global stdout tee spent all of them absorbing
    every unrelated print in the process into whichever action happened to be
    running - which the first end-to-end run showed as an action's record
    quoting the script that started it."""
    import threading as t

    gate = _slow_action(writable)
    writable.call("start_action", {"name": "slow.Slow"})
    time.sleep(0.1)

    elsewhere = t.Thread(target=lambda: print("NOT THE ACTION'S OUTPUT"))
    elsewhere.start()
    elsewhere.join(5)

    peek = writable.call("get_action_result", {"name": "slow.Slow", "wait": 0})
    assert "started working" in peek, "the action's own print is captured"
    assert "NOT THE ACTION'S OUTPUT" not in peek
    gate.set()
    writable.call("get_action_result", {"name": "slow.Slow", "wait": 20})


def test_the_call_log_does_not_land_in_a_running_action(writable):
    """The same problem the print tee had, arriving by the other route: the log
    handler is global on purpose, so one line per call would mean a model
    polling a long run reading back the polls that read it."""
    dispatcher = Dispatcher(writable)
    gate = _slow_action(writable)
    _rpc(dispatcher, "start_action", {"name": "slow.Slow"})
    time.sleep(0.1)
    _rpc(dispatcher, "get_action_result", {"name": "slow.Slow", "wait": 0})

    peek = _rpc(dispatcher, "get_action_result",
                {"name": "slow.Slow", "wait": 0})["result"]["content"][0]["text"]
    assert "started working" in peek, "the action's own output is still captured"
    # The report quotes get_action_result itself - it says how to ask again -
    # so the marker is the log format, not the tool name.
    assert "[keyhac.MCP]" not in peek
    gate.set()
    writable.call("get_action_result", {"name": "slow.Slow", "wait": 20})
