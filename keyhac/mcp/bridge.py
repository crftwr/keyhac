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

`--tools` is the one exception, and it holds to the same rule: it prints what
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
from keyhac.mcp.server import ENDPOINT_FILE

#: How long to wait on the daemon before answering the client ourselves. Long
#: enough for a deep tree walk on a slow application; short enough that a wedged
#: daemon does not hang the conversation with no explanation.
TIMEOUT = 60.0


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
    """The likeliest failure by far, so it names the cause rather than leaving
    the reader with 'connection refused'. Shared by the pump and by --tools:
    the two reach the same daemon and fail to find it the same way."""
    return (f"Keyhac's MCP endpoint is not available ({path}). Is Keyhac "
            f"running, and is its MCP server switch on - the 'AI Integration: "
            f"MCP Server' checkbox in the console window, or 'AI Integration > "
            f"MCP Server' in the tray menu?")


def _post(endpoint: dict, body: bytes) -> urllib.request.Request:
    return urllib.request.Request(
        f"http://127.0.0.1:{endpoint['port']}/",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {endpoint['token']}"},
        method="POST")


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

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue                      # not addressable: no id to reply to

        request_id = message.get("id") if isinstance(message, dict) else None

        # Read the endpoint per request rather than once at startup: Keyhac may
        # be restarted - or started for the first time - while the client holds
        # this process open, and the port changes when it is.
        try:
            endpoint = read_endpoint(path)
        except (OSError, ValueError):
            if request_id is None:
                continue
            _respond(message, _error(request_id, _no_daemon(path)))
            continue

        request = _post(endpoint, line.encode("utf-8"))
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as reply:
                body = reply.read()
        except urllib.error.HTTPError as error:
            if request_id is not None:
                _respond(message, _error(
                    request_id, f"Keyhac returned HTTP {error.code}"))
            continue
        except (urllib.error.URLError, OSError) as error:
            if request_id is not None:
                _respond(message, _error(
                    request_id, f"could not reach Keyhac: {error}"))
            continue

        # A notification gets 202 and an empty body; there is nothing to write.
        if body:
            sys.stdout.write(body.decode("utf-8") + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
