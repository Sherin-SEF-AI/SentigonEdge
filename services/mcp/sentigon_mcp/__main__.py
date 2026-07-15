"""Run the Sentigon MCP server (streamable-http): python -m sentigon_mcp"""

from __future__ import annotations

from .server import mcp


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
