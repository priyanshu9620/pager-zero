"""MCP tool server (notes Phase 2, D-12).

`build_server(adapters, actions_enabled=False)` returns a FastMCP instance exposing the adapter
readers as tools. Mutating tools are *registered only when* `actions_enabled=True`, so a server
started for investigation physically has no way to change anything. The agent starts a second,
actions-enabled session only after the human approves (Phase 3).
"""

from pager_zero.mcp_server.server import build_server

__all__ = ["build_server"]
