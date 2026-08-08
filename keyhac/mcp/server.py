"""The MCP endpoint Keyhac serves on localhost.

Claude reaches Keyhac's tools through this: it inspects the screen an action
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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from keyhac.core import log

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

    def _authorised(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        # Constant-time: the token is a bearer credential and a timing oracle
        # on a localhost socket is still an oracle.
        return secrets.compare_digest(header[len(prefix):], self.server.token)

    def _send(self, status: int, payload) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
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
        return {"content": [{"type": "text", "text": text}], "isError": is_error}


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
        a window in which the credential is world-readable.
        """
        directory = os.path.dirname(self.endpoint_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        descriptor = os.open(self.endpoint_path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"port": self.port, "token": self.token,
                       "pid": os.getpid()}, handle)
