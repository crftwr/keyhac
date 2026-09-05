"""stdio -> HTTP shim, for MCP clients that can only spawn a subprocess.

Claude Desktop starts a local MCP server as a child process and talks JSON-RPC
over its stdin/stdout. Keyhac cannot be that child: it is a resident daemon
holding a keyboard hook and the origin session, and starting a second copy per
conversation would be a second hook, a second accessibility prompt, and no
access to the running one's state. So this runs as the child instead and
forwards every message to the daemon already running.

**It contains no tool definitions and no logic on purpose.** Everything the
model sees is served by the daemon, so the two cannot drift apart into a bridge
that advertises a tool the daemon no longer has. If you find yourself adding a
branch here for a specific method, it belongs on the other side.

**When the daemon is not answering, the bridge answers for it.** A client
that meets a JSON-RPC error at `initialize` writes the server off as broken and
stops calling it - so the state a user meets most often, Keyhac running with its
MCP switch off, arrives as "keyhac failed to connect" with the reason on a line
nobody reads, and the way out is a client restart rather than a tick in Keyhac.
Instead the handshake succeeds, `tools/list` serves one tool whose name and
description say the switch is off and how to turn it on, and calling that tool
re-checks. It cannot drift from the daemon either: what it serves exists only
while the daemon serves nothing, and `notifications/tools/list_changed` hands
the list back the moment one answers.

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
from keyhac.mcp.server import ENDPOINT_FILE, PROTOCOL_VERSION

#: How long to wait on the daemon before answering the client ourselves. Long
#: enough for a deep tree walk on a slow application; short enough that a wedged
#: daemon does not hang the conversation with no explanation.
TIMEOUT = 60.0

#: How long the placeholder tool waits for the daemon before reporting it
#: still off. A `ping` answered from the daemon's own thread pool, so this is
#: a localhost round trip and not a tree walk.
PROBE_TIMEOUT = 5.0

#: The tool served while the daemon is silent. Named as a sentence because the
#: name is the half of a tool listing every client shows.
OFF_TOOL = "keyhac_mcp_server_is_off"


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

    `listChanged` is declared because the whole point of answering is that what
    is served next is provisional: the client has to accept being told to ask
    again once the switch goes on. The version is this bridge's own, which is
    the only one it can know - the daemon reports its own when it is up.
    """
    from keyhac import __version__
    return {"protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "keyhac", "version": __version__}}


def _off_tool(path: str) -> dict:
    """The one tool served while the daemon is silent.

    A tool rather than an empty list: a server that connects and offers nothing
    reads as a broken Keyhac and says nothing about the switch, while a listed
    tool carries the reason in the two places a model and a person both look -
    the name and the description. Calling it is also the cheapest way to find
    out whether the switch has since been ticked.
    """
    return {"name": OFF_TOOL,
            "description": (f"{_no_daemon(path)} Calling this tool checks "
                            f"again: once Keyhac answers, its own tools "
                            f"replace this one."),
            "inputSchema": {"type": "object", "properties": {}}}


def _tool_called(message: dict) -> str | None:
    params = message.get("params")
    return params.get("name") if isinstance(params, dict) else None


def _answering(endpoint: dict) -> bool:
    """Whether the daemon behind a published endpoint is really there.

    The file outliving the daemon is an ordinary state - a Keyhac killed rather
    than quit leaves one - so its presence is not the answer. `ping` is, and it
    is the cheapest question the protocol has. A rejected token counts as no:
    a stale credential and a stale port are the same stale file.
    """
    body = json.dumps({"jsonrpc": "2.0", "id": 0,
                       "method": "ping"}).encode("utf-8")
    try:
        with urllib.request.urlopen(_post(endpoint, body),
                                    timeout=PROBE_TIMEOUT):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


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

    # Whether the tool list the client is holding is ours. Only then is there
    # anything to announce when the daemon starts answering: the notification
    # means "what I gave you is stale", and a client that never saw the
    # placeholder is already holding the daemon's own list.
    placeholder_served = False

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

        # Ours to answer whichever way the daemon is: it has never heard of
        # this tool and would refuse the call.
        if method == "tools/call" and _tool_called(message) == OFF_TOOL:
            back = endpoint is not None and _answering(endpoint)
            if request_id is not None:
                text = ("Keyhac's MCP server is answering again - its own "
                        "tools are available now." if back else _no_daemon(path))
                _respond(message, _result(request_id, {
                    "content": [{"type": "text", "text": text}],
                    "isError": not back}))
            if back and placeholder_served:
                placeholder_served = False
                _notify("notifications/tools/list_changed")
            continue

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
            except (urllib.error.URLError, OSError):
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
                placeholder_served = True
                _respond(message, _result(request_id,
                                          {"tools": [_off_tool(path)]}))
            else:
                _respond(message, _error(request_id, _no_daemon(path)))
            continue

        # A notification gets 202 and an empty body; there is nothing to write.
        if body:
            sys.stdout.write(body.decode("utf-8") + "\n")
            sys.stdout.flush()

        if placeholder_served:
            placeholder_served = False
            _notify("notifications/tools/list_changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
