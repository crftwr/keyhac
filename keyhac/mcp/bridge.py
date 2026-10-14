"""stdio -> HTTP shim, for MCP clients that can only spawn a subprocess.

Claude Desktop starts a local MCP server as a child process and talks JSON-RPC
over its stdin/stdout. Keyhac cannot be that child: it is a resident daemon
holding a keyboard hook and the origin session, and starting a second copy per
conversation would be a second hook, a second accessibility prompt, and no
access to the running one's state. So this runs as the child instead and
forwards every message to the daemon already running.

**It writes no tool definitions and no logic on purpose.** Everything the
model sees is served by the daemon, so the two cannot drift apart into a bridge
that advertises a tool the daemon no longer has. If you find yourself adding a
branch here for a specific method, it belongs on the other side.

**When the daemon is not answering, the bridge answers for it.** A client
that meets a JSON-RPC error at `initialize` writes the server off as broken and
stops calling it - so the state a user meets most often, Keyhac running with its
MCP switch off, arrives as "keyhac failed to connect" with the reason on a line
nobody reads, and the way out is a client restart rather than a tick in Keyhac.
Instead the handshake succeeds and `tools/list` serves Keyhac's own tools, built
by importing the same `ToolRegistry` the daemon serves them from - generated,
not written twice, so it cannot drift either. Calling one while the switch is
off answers with which box to tick.

**The list does not change when the switch does** (issue #156). It used to:
the bridge served one placeholder tool while the daemon was silent and sent
`notifications/tools/list_changed` when it answered. Honouring that
notification is optional in MCP, and a client that ignores it - Amazon Quick -
kept the placeholder until it was restarted, which is the very cost the offline
answers exist to remove. The notification is now sent only when the daemon's
list really differs from the one served here, which takes two installs at
different versions: a bridge process older than the Keyhac it reaches, or a
development checkout's bridge in front of the bundled daemon.

`--tools` is the other exception, and it holds to the same rule: it prints what
the daemon answers to `tools/list`, verbatim. It knows no tool names, no
argument shapes, and formats nothing the daemon did not serve, so it cannot
drift from it either. It exists for a client that has a shell but no MCP
transport, which would otherwise have to hand-type a JSON-RPC line to find out
what the tools are. Do not grow it into a per-tool branch.

Register it with Claude Desktop as:

    {"mcpServers": {"keyhac": {"command": "keyhac-mcp-bridge"}}}

It takes no arguments: the port and token come from the endpoint file the
daemon publishes beside config.py, so a restarted daemon on a new port needs no
configuration change.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from keyhac.core import paths
from keyhac.mcp.server import ENDPOINT_FILE, PROTOCOL_VERSION, REQUEST_TIMEOUT

#: How long to wait on the daemon before answering the client ourselves. Half
#: of a contract with the tools, which cap what they will deliberately block
#: for at MAX_TOOL_WAIT - so the value lives beside the rest of the endpoint
#: contract and is imported here rather than written twice.
TIMEOUT = REQUEST_TIMEOUT

#: How long to wait on the daemon's own tool list, asked for once after it
#: starts answering. Answered from a table, so this is a localhost round trip
#: and not a tree walk.
PROBE_TIMEOUT = 5.0


def endpoint_path(config: str | None = None) -> str:
    """Where the daemon publishes its port and token."""
    resolved = paths.resolve(config)
    return os.path.join(os.path.dirname(resolved.config_path), ENDPOINT_FILE)


def read_endpoint(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _respond(message: dict, payload: dict) -> None:
    """Write one JSON-RPC message. MCP's stdio transport is newline-delimited,
    so the JSON must not contain a raw newline - json.dumps guarantees that."""
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _error(request_id, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32603, "message": message}}


def _no_daemon(path: str) -> str:
    """Why nothing answered, in the terms whoever reads it can act on.

    The switch being off is far and away the likeliest, and its expiry is the
    one that surprises people: it is on until it is not, and nothing is said at
    the time. Both are named here rather than left as 'connection refused'.
    Shared by the offline answers, the pump's errors and --tools, which all
    reach the same daemon and fail to find it the same way.
    """
    return (f"Keyhac's MCP server is not answering, so none of its tools can "
            f"run. Either Keyhac is not running, or its MCP server switch is "
            f"off: it starts off every time, and it also closes itself an hour "
            f"after being switched on. Ask the operator to tick 'AI "
            f"Integration: MCP Server' in Keyhac's console window, or 'AI "
            f"Integration > MCP Server' in its tray menu, then try again. "
            f"(endpoint: {path})")


def _too_slow() -> str:
    """Why nothing came back, when something is plainly there.

    A daemon that is listening and working is not a daemon that is absent, and
    the two took the same answer until issue #70: a call that outlived the
    timeout was reported with `_no_daemon`'s prose, which sends the operator to
    tick a switch that is already on. The tools cap what they will block for
    (MAX_TOOL_WAIT), so what reaches here is a call that is genuinely slow -
    a deep tree walk on a large application - rather than one that was asked
    to wait.
    """
    return (f"Keyhac is running and did not answer within {TIMEOUT:.0f}s. The "
            f"call is still going on inside it - nothing was cancelled, and "
            f"the MCP server is on, so this is a slow call rather than a "
            f"missing one. Ask for less of the screen at once (a narrower "
            f"window, fewer nodes, a shallower walk), or try again.")


def _timed_out(error: BaseException) -> bool:
    """Whether a failed request was slow rather than unreachable.

    Both spellings, because urllib raises a bare TimeoutError when the read
    runs out and wraps one in URLError when the connection does.
    """
    return isinstance(error, TimeoutError) or \
        isinstance(getattr(error, "reason", None), TimeoutError)


def _post(endpoint: dict, body: bytes) -> urllib.request.Request:
    return urllib.request.Request(
        f"http://127.0.0.1:{endpoint['port']}/",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {endpoint['token']}"},
        method="POST")


def _result(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _notify(method: str) -> None:
    """A JSON-RPC notification: no id, and no reply is coming."""
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
    sys.stdout.flush()


def _handshake() -> dict:
    """The `initialize` result, for the daemon that is not there to give one.

    `listChanged` is declared, as the daemon declares it, for the one case in
    which this bridge's list and the daemon's differ. The version is this
    bridge's own, which is the only one it can know - the daemon reports its
    own when it is up.
    """
    from keyhac import __version__
    return {"protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "keyhac", "version": __version__}}


def own_tools() -> list[dict]:
    """The tool list this install's daemon serves, without asking it.

    Built from the registry rather than copied from it: `describe()` reads
    only the definitions, and the keymap a registry is bound to is touched by
    the tools when they run, which none do here.
    """
    from keyhac.mcp.tools import ToolRegistry
    return ToolRegistry(None).describe()


def _daemon_tools(endpoint: dict) -> list[dict] | None:
    """What the daemon serves to `tools/list`, or None if it did not say."""
    body = json.dumps({"jsonrpc": "2.0", "id": 0,
                       "method": "tools/list"}).encode("utf-8")
    try:
        with urllib.request.urlopen(_post(endpoint, body),
                                    timeout=PROBE_TIMEOUT) as reply:
            served = json.loads(reply.read().decode("utf-8"))
        return served["result"]["tools"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return None


def print_tools(path: str) -> int:
    """Print the daemon's tool list as JSON. Diagnostics go to stderr so the
    stdout of a successful run is nothing but the JSON, and a caller can pipe
    it straight into a parser."""
    try:
        endpoint = read_endpoint(path)
    except (OSError, ValueError):
        sys.stderr.write(_no_daemon(path) + "\n")
        return 1

    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "method": "tools/list"}).encode("utf-8")
    try:
        with urllib.request.urlopen(_post(endpoint, body), timeout=TIMEOUT) as reply:
            served = json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        sys.stderr.write(f"Keyhac returned HTTP {error.code}\n")
        return 1
    except (urllib.error.URLError, OSError, ValueError) as error:
        sys.stderr.write(f"could not reach Keyhac: {error}\n")
        return 1

    if "error" in served:
        sys.stderr.write(f"Keyhac returned an error: "
                         f"{served['error'].get('message')}\n")
        return 1

    tools = served.get("result", {}).get("tools", [])
    sys.stdout.write(json.dumps(tools, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        epilog="With no options it reads JSON-RPC on stdin and answers on "
               "stdout, which is what an MCP client wants. --tools is for a "
               "client that has a shell but no MCP transport, and unlike the "
               "rest of --help it needs Keyhac running with its MCP server "
               "switch on, since the daemon is what holds the tool list.")
    parser.add_argument("--config", help="Path to config.py, when Keyhac runs "
                                         "with a non-default one.")
    parser.add_argument("--tools", action="store_true",
                        help="Print the daemon's tool schemas as JSON and "
                             "exit, instead of forwarding stdin.")
    args = parser.parse_args(argv)

    # MCP's stdio transport is UTF-8. Windows decides a pipe's encoding from the
    # ANSI code page instead - cp1252 on a US install, cp932 on a Japanese one -
    # so every non-ASCII argument a client sends (a window name, a path) arrived
    # as mojibake, and any non-ASCII reply would have failed to encode on the way
    # back. Guarded rather than assumed: a caller can hand main() a stdin that is
    # not a TextIOWrapper.
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

    path = endpoint_path(args.config)

    if args.tools:
        return print_tools(path)

    # Whether the tool list the client is holding is ours rather than the
    # daemon's. Only then is there anything to check when the daemon starts
    # answering - and only a difference between the two is worth announcing,
    # since a client is free to ignore the announcement.
    own_served = False

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue                      # not addressable: no id to reply to

        addressable = isinstance(message, dict)
        request_id = message.get("id") if addressable else None
        method = message.get("method") if addressable else None

        # Read the endpoint per request rather than once at startup: Keyhac may
        # be restarted - or started for the first time - while the client holds
        # this process open, and the port changes when it is.
        try:
            endpoint = read_endpoint(path)
        except (OSError, ValueError):
            endpoint = None

        body = None
        if endpoint is not None:
            request = _post(endpoint, line.encode("utf-8"))
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as reply:
                    body = reply.read()
            except urllib.error.HTTPError as error:
                # An answer, and from the daemon: a rejected token is not a
                # switch that is off, and saying so would send the operator to
                # tick one that is already ticked.
                if request_id is not None:
                    _respond(message, _error(
                        request_id, f"Keyhac returned HTTP {error.code}"))
                continue
            except (urllib.error.URLError, OSError) as error:
                if _timed_out(error):
                    # An answer that is late is not an answer that is not
                    # coming, and the offline path below would say the daemon
                    # is gone - about a daemon that is working (issue #70).
                    if request_id is not None:
                        _respond(message, _error(request_id, _too_slow()))
                    continue
                # Published but not listening - a Keyhac killed rather than
                # quit leaves the file behind. The same state as never
                # published, and it takes the same answer.
                endpoint = None

        if endpoint is None:
            if request_id is None:
                continue                  # a notification: nothing to answer
            if method == "initialize":
                _respond(message, _result(request_id, _handshake()))
            elif method == "ping":
                _respond(message, _result(request_id, {}))
            elif method == "tools/list":
                own_served = True
                _respond(message, _result(request_id, {"tools": own_tools()}))
            elif method == "tools/call":
                # A tool result rather than a JSON-RPC error: with no
                # placeholder in the list, this answer is the one place the
                # switch is named, and MCP routes tool failures to the model
                # this way - a client may show a protocol error as a bare
                # "tool failed".
                _respond(message, _result(request_id, {
                    "content": [{"type": "text", "text": _no_daemon(path)}],
                    "isError": True}))
            else:
                _respond(message, _error(request_id, _no_daemon(path)))
            continue

        # A notification gets 202 and an empty body; there is nothing to write.
        if body:
            sys.stdout.write(body.decode("utf-8") + "\n")
            sys.stdout.flush()

        if own_served:
            if method == "tools/list":
                own_served = False        # the daemon's list just went out
            else:
                served = _daemon_tools(endpoint)
                if served is not None:
                    own_served = False
                    if served != own_tools():
                        _notify("notifications/tools/list_changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
