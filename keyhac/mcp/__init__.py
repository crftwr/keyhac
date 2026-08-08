"""Keyhac's MCP endpoint: the tools Claude uses to author actions.

`server.py` serves them over localhost HTTP from inside the daemon, `tools.py`
defines them, and `bridge.py` is the stdio shim for clients that spawn a
subprocess. See doc/mcp.md, and doc/dev/ai-integration.md §4 for why the split
is shaped this way.
"""
