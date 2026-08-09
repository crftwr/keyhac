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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", help="Path to config.py, when Keyhac runs "
                                         "with a non-default one.")
    args = parser.parse_args(argv)

    path = endpoint_path(args.config)

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
            _respond(message, _error(
                request_id,
                f"Keyhac's MCP endpoint is not available ({path}). Is Keyhac "
                f"running, and is its MCP server switch on - the 'AI "
                f"Integration: MCP Server' checkbox in the console window, or "
                f"'AI Integration > MCP Server' in the tray menu?"))
            continue

        request = urllib.request.Request(
            f"http://127.0.0.1:{endpoint['port']}/",
            data=line.encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {endpoint['token']}"},
            method="POST")
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
