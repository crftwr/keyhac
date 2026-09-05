"""The MCP endpoint Keyhac serves on localhost.

The agent reaches Keyhac's tools through this: it inspects the screen an action
will run against, runs the action, reads what went wrong, and tries again.
That loop - and not runtime inference - is the whole of the AI integration
(doc/dev/ai-integration.md §3.1).

WHY THE WORK LIVES HERE AND NOT IN A SEPARATE PROCESS (§4.3). Three reasons,
and each on its own would be enough: accessibility permission is granted per
binary on macOS, so a second executable is a second scary authorisation prompt
for an application whose main trust problem is proving it is not a keylogger;
the origin session - window identifiers, focus history, active keytable - lives
in this process and nothing else can refer to it; and Windows UI Automation is
COM apartment-bound, which makes a cross-process split awkward. So Keyhac
speaks HTTP here, and `keyhac/mcp/bridge.py` is a dumb stdio shim for clients
that can only spawn a subprocess.

DISABLED BY DEFAULT, AND AUTHENTICATED WHEN ON (§4.4). Listening on localhost
means every process on the machine can reach an API that reads the UI tree and
injects keystrokes. An application arguing it is not a keylogger cannot ship an
unauthenticated local endpoint offering key injection - so the config has to
ask for this, the socket binds to 127.0.0.1 only, and every request carries a
token generated at startup and readable only by this user.

OFF THE ACTION EXECUTOR (§4.5). This runs on its own threads. Routing tool
calls through ThreadedAction's pool would mean one long-running action stalls
every incoming call, and a burst of calls starves actions - `max_workers=1`
makes both certain rather than likely.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from keyhac.core import log, permissions

logger = log.getLogger("MCP")

#: The MCP revision this server implements.  Reported in `initialize`; a client
#: asking for a different one still gets ours, which is what the spec's
#: version-negotiation expects.
PROTOCOL_VERSION = "2025-06-18"

#: Where the port and token are published for the bridge to read.  Beside the
#: config rather than in a fixed home directory, so portable mode keeps its
#: state together with everything else.
ENDPOINT_FILE = "mcp.json"

#: Largest request body accepted.  Tool arguments are small; a body this size
#: is a mistake or an attack, and reading it would be the damage.
MAX_BODY = 1 << 20

#: How much of one argument value the INFO line for a call shows.  Long enough
#: to tell two calls on the same tool apart, short enough that
#: `write_extension`'s source cannot push the rest of the console out of the
#: ring buffer - the file it lands in is announced with a line count anyway.
VALUE_CHARS = 60

#: How much of one JSON-RPC message the DEBUG traffic lines show.  A
#: `describe_screen` reply is a whole UI tree: the point of the traffic log is
#: what was asked and roughly what came back, not a transcript that scrolls
#: everything else out of the window.
DETAIL_CHARS = 2000


#: APPMODEL_ERROR_NO_PACKAGE - what GetCurrentPackageFullName answers when the
#: calling process has no package identity, i.e. is not running from an MSIX
#: install.  Any other result means it does.
_NO_PACKAGE = 15700


def running_packaged() -> bool:
    """Whether this process has MSIX package identity (Store or sideloaded).

    Asked of Windows rather than inferred from the path, because it is exactly
    the condition that matters: a process *with* identity may run the binaries
    inside its package, and one without may not.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    length = ctypes.c_uint32(0)
    result = ctypes.windll.kernel32.GetCurrentPackageFullName(
        ctypes.byref(length), None)
    return result != _NO_PACKAGE


def bridge_command() -> str | None:
    """Absolute path to the stdio bridge, or None when this install has none.

    Published in the endpoint file so a client that can only spawn a subprocess
    is configurable from the same place as one that speaks HTTP.  Without it the
    path has to be found by hand - it is four levels inside an application
    bundle - or guessed, and a GUI client needs it absolute because it inherits
    no shell PATH.

    Derived from this file rather than from a platform check, so it is right for
    however Keyhac is actually running:

    - macOS bundle: ``Contents/Resources/keyhac`` -> ``Resources/bin/...``
    - Windows bundle: ``<root>/app/keyhac`` -> ``<root>/keyhac-mcp-bridge.exe``
    - Windows MSIX install: the app execution alias, never the copy inside the
      package - see below
    - venv or pip install: the console script pip generated from
      ``[project.scripts]``, which lands beside the interpreter running us
      (``bin/`` on POSIX, ``Scripts/`` with an ``.exe`` suffix on Windows)
    - anything else: whatever is on PATH, or None.

    The order is "this install first, PATH last", deliberately. A checkout run
    as ``.venv/bin/python -m keyhac`` never has its own ``bin/`` on PATH, so a
    PATH-first search finds some *other* Keyhac's bridge while ignoring the one
    that belongs to the daemon doing the publishing - which is how this was
    found. PATH remains the last resort because a bridge from an unrelated
    install still forwards correctly; it just resolves the config directory with
    its own copy of ``keyhac.core.paths``, so the two can disagree about where
    ``mcp.json`` lives.

    **The MSIX install is the exception, and existence is not the test there.**
    The bridge inside ``C:\\Program Files\\WindowsApps\\...`` is perfectly
    readable and cannot be *executed* by anybody: every launch attempt from an
    ordinary process comes back "Access is denied", because a packaged binary
    may only be started with its package's identity. Publishing that path
    yielded an MCP client that failed at startup with no output at all. So a
    packaged Keyhac publishes the app execution alias Windows registers on its
    behalf (``windows.appExecutionAlias`` in the manifest), which is the
    supported way in - and if that stub is absent, because the user turned the
    alias off in Settings, publishes nothing rather than a path that cannot
    work.
    """
    if running_packaged():
        # lexists, not isfile: the alias is a 0-byte reparse point carrying an
        # app-exec tag, and asking about the link itself avoids depending on
        # how a given Python resolves a tag it does not know.
        alias = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft",
                             "WindowsApps", "keyhac-mcp-bridge.exe")
        return alias if os.path.lexists(alias) else None

    package = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parent = os.path.dirname(package)
    root = os.path.dirname(parent)
    candidates = [os.path.join(parent, "bin", "keyhac-mcp-bridge"),
                  os.path.join(root, "keyhac-mcp-bridge.exe"),
                  # 2.2.0-2.2.2 shipped only the .cmd; it now forwards to the
                  # .exe, but a bundle from one of those still has just this.
                  os.path.join(root, "keyhac-mcp-bridge.cmd")]
    # sys.executable is empty when the interpreter cannot identify itself (an
    # embedded host); there is simply no scripts directory to look in then.
    if sys.executable:
        scripts = os.path.dirname(sys.executable)
        candidates += [os.path.join(scripts, "keyhac-mcp-bridge"),
                       os.path.join(scripts, "keyhac-mcp-bridge.exe")]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("keyhac-mcp-bridge")


class _Handler(BaseHTTPRequestHandler):
    """One JSON-RPC request per POST.  No GET, no SSE, no sessions."""

    protocol_version = "HTTP/1.1"
    server_version = "Keyhac-MCP"

    def log_message(self, fmt, *args):
        # BaseHTTPRequestHandler logs every request to stderr, which on a
        # desktop app means the console window fills with noise.
        logger.debug(f"{self.address_string()} {fmt % args}")

    def do_POST(self):                                   # noqa: N802 - stdlib
        if not self._authorised():
            self._send(401, {"error": "unauthorised"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "bad Content-Length"})
            return
        if length > MAX_BODY:
            self._send(413, {"error": "request too large"})
            return

        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError) as error:
            self._send(400, {"error": f"bad JSON: {error}"})
            return

        response = self.server.dispatcher.handle(request)
        if response is None:
            # A JSON-RPC notification has no reply, but HTTP needs a status.
            self._send(202, None)
            return
        self._send(200, response)

    def do_GET(self):                                    # noqa: N802 - stdlib
        self._decline()

    def do_DELETE(self):                                 # noqa: N802 - stdlib
        self._decline()

    def _decline(self) -> None:
        """405, not the stdlib's 501, for the verbs this transport declines.

        Streamable HTTP lets a server offer no server-to-client SSE stream and
        no sessions - but it has to decline in the spec's words: GET without a
        stream "MUST return HTTP 405", and DELETE is how a client ends a
        session it was never given. The stdlib's default 501 says something
        else entirely - *this server does not implement HTTP* - and a client
        that opens the optional stream before its first tool call can read that
        as fatal rather than as "no stream here". The distinction only matters
        for clients other than the bridge, which is exactly who this is for.
        """
        self.send_response(405)
        self.send_header("Allow", "POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorised(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        # Constant-time: the token is a bearer credential and a timing oracle
        # on a localhost socket is still an oracle.
        return secrets.compare_digest(header[len(prefix):], self.server.token)

    def _send(self, status: int, payload) -> None:
        # UTF-8 on the wire rather than `\uXXXX` escapes, which is what JSON is
        # specified to be (RFC 8259 §8.1) and what the bridge already decodes
        # the body as. A tool reply is a UI tree, so on a Japanese system it is
        # mostly non-ASCII: escaping sent every one of those characters as six
        # bytes instead of three, and the CJK case measured 1.77x. The compact
        # separators beside it save a constant handful of bytes per reply - the
        # padding is per field, not per character, so it is the escaping that
        # was worth the change.
        body = b"" if payload is None else json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


class Dispatcher:
    """JSON-RPC 2.0 over the handful of MCP methods a tool server needs.

    Deliberately hand-rolled: the surface is `initialize`, `tools/list`,
    `tools/call` and `ping`, and a dependency that has to be installed before
    Keyhac starts is a worse trade than sixty lines.
    """

    def __init__(self, registry):
        self.registry = registry

    def handle(self, request):
        """One reply, with the whole exchange logged around it at DEBUG.

        A wrapper so that every reply is logged from one place rather than at
        each of `_handle`'s returns - and so a request that is not JSON-RPC at
        all, which the dispatch below refuses before reading it, is still
        visible as something that arrived. The INFO half of the console trail
        is one line per *tool* call, in `_call`.
        """
        logger.debug(f"-> {_detail(request)}")
        response = self._handle(request)
        # A notification has no reply to log; the 202 is the whole answer.
        if response is not None:
            logger.debug(f"<- {_detail(response)}")
        return response

    def _handle(self, request):
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return _error(None, -32600, "not a JSON-RPC 2.0 request")

        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}

        # Notifications carry no id and take no reply - including
        # "notifications/initialized", which every client sends.
        if request_id is None:
            return None

        try:
            if method == "initialize":
                return _result(request_id, self._initialize())
            if method == "ping":
                return _result(request_id, {})
            if method == "tools/list":
                return _result(request_id, {"tools": self.registry.describe()})
            if method == "tools/call":
                return _result(request_id, self._call(params))
            return _error(request_id, -32601, f"unknown method {method!r}")
        except Exception as error:                        # noqa: BLE001
            logger.error(f"MCP {method} failed: {error}", exc_info=True)
            return _error(request_id, -32603, str(error))

    def _initialize(self) -> dict:
        from keyhac import __version__
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "keyhac", "version": __version__},
        }

    def _call(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            text = self.registry.call(name, arguments)
            is_error = False
        except Exception as error:                        # noqa: BLE001
            # A failed tool is reported *inside* the result, not as a protocol
            # error: the model is supposed to read it and try something else,
            # which it cannot do if the transport swallows it.
            logger.debug(f"tool {name} failed", exc_info=True)
            text = f"{type(error).__name__}: {error}"
            is_error = True

        # One INFO line per tool call, and only for tool calls: `initialize`,
        # `tools/list` and the ping a client repeats while it is connected are
        # handshake, and an audit trail nobody can read is not one. Logged on
        # the way out rather than the way in, because the outcome is half of
        # what the line is for - the access-log shape, not a progress display.
        # A call that blocks by design (`get_action_result` waits) therefore
        # reports when it returns, and what it was waiting for is already on
        # the console: the action's own output goes there as it happens.
        logger.info(f"{name}({_call_arguments(arguments)}) -> "
                    f"{_elide(text, VALUE_CHARS) if is_error else f'{len(text)} chars'}")
        return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _call_arguments(arguments) -> str:
    """`name='thing', wait=20` - what tells two calls on one tool apart.

    Values are elided rather than dropped. Which module was written and which
    window was read is the whole content of the audit line, and a tool whose
    arguments are all long (`write_extension`) is exactly the one where seeing
    the first words of them matters.
    """
    if not isinstance(arguments, dict):
        return _elide(repr(arguments), VALUE_CHARS)
    return ", ".join(f"{key}={_value(value)}" for key, value in arguments.items())


def _value(value) -> str:
    # Elided *inside* the quotes, so a shortened string still reads as one.
    if isinstance(value, str) and len(value) > VALUE_CHARS:
        return repr(_elide(value, VALUE_CHARS))
    return repr(value)


def _elide(text: str, limit: int) -> str:
    """One console line, at most `limit` characters of it.

    Newlines collapse rather than wrap: a traceback from a failing tool would
    otherwise turn one audit line into thirty, and the whole of it is a DEBUG
    line away.
    """
    line = " ".join(text.split())
    return line if len(line) <= limit else line[:limit] + "..."


def _detail(message) -> str:
    """A JSON-RPC message as something a person can read, bounded.

    **The envelope stays one compact line.** `jsonrpc`, `id` and the shape of
    the result say almost nothing to a reader, and folding them over ten
    indented lines would bury the part that does.

    **The payload becomes a block.** That part is a `describe_screen` reply or
    the module `write_extension` was handed, and inside JSON either arrives as
    one line with `\\n` written out in it - a UI tree rendered as an unreadable
    ribbon in a window that would have shown it perfectly well. So every string
    with a newline in it is lifted out, leaving `<its.path>` where it sat, and
    printed underneath as itself. Blocks follow in the order their placeholders
    appear.

    The console splits a record on its newlines and prefixes only the first
    (`core/log.py`), so this lands as a header line and an indented body - the
    shape a logged traceback already has there. `ensure_ascii` is off for the
    same reason: a Japanese menu label is worth more as itself than as six
    characters of `\\u30bb`.
    """
    blocks: list[str] = []

    def lift(node, path):
        if isinstance(node, str) and "\n" in node:
            blocks.append(node)
            return f"<{path}>"
        if isinstance(node, dict):
            return {key: lift(value, f"{path}.{key}" if path else str(key))
                    for key, value in node.items()}
        if isinstance(node, list):
            return [lift(value, f"{path}[{index}]")
                    for index, value in enumerate(node)]
        return node

    try:
        envelope = json.dumps(lift(message, ""), default=repr,
                              ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        envelope, blocks = repr(message), []
    return "\n".join([_bounded(envelope)] + [_bounded(block) for block in blocks])


def _bounded(text: str) -> str:
    """`DETAIL_CHARS` of it, and how much was left. Per block rather than per
    record: the cap is there to keep one reply from evicting the console's ring
    buffer, and a reply's tree is the block."""
    if len(text) <= DETAIL_CHARS:
        return text
    return f"{text[:DETAIL_CHARS]}... [{len(text) - DETAIL_CHARS} more characters]"


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


class MCPServer:
    """The listening socket, its thread, and the published endpoint file."""

    def __init__(self, registry, endpoint_path: str, port: int = 0):
        self.registry = registry
        self.endpoint_path = endpoint_path
        self.token = secrets.token_urlsafe(32)
        self.bridge = bridge_command()
        self._server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self._server.dispatcher = Dispatcher(registry)
        self._server.token = self.token
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._publish()
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="keyhac-mcp", daemon=True)
        self._thread.start()
        logger.info(f"MCP server listening on 127.0.0.1:{self.port} "
                    f"({len(self.registry.describe())} tools)")
        # The console is where a user looks when wiring up a client, and the
        # bundled path is long enough that nobody should be retyping it.
        if self.bridge:
            logger.info(f"stdio bridge: {self.bridge}")

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            os.unlink(self.endpoint_path)
        except OSError:
            pass

    def _publish(self) -> None:
        """Write port + token where the bridge can find them, and nobody else.

        Created 0600 *before* the token is written - a chmod afterwards leaves
        a window in which the credential is world-readable.  That is what
        ``permissions.open_private`` does for every file Keyhac writes; this
        one had it first.
        """
        permissions.ensure_private_dir(os.path.dirname(self.endpoint_path))
        published = {"port": self.port, "token": self.token, "pid": os.getpid()}
        # Omitted rather than null when this install generated no console
        # script: a reader can then treat the key's presence as "a stdio client
        # is configurable", with no separate existence check.
        if self.bridge:
            published["bridge"] = self.bridge

        with permissions.open_private(self.endpoint_path) as handle:
            json.dump(published, handle)
