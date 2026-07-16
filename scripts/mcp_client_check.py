#!/usr/bin/env python3
"""Real MCP client: connect to the Sentigon MCP server over streamable-http,
list its tools, and call each one, printing the real results. Proves the MCP
surface works end to end with the official MCP client SDK (the same protocol
MCP hosts use).

    uv run python scripts/mcp_client_check.py
"""

from __future__ import annotations

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "http://localhost:8065/mcp"


def _short(result) -> str:
    try:
        content = result.content[0].text if result.content else ""
    except Exception:  # noqa: BLE001
        content = str(result)
    return content[:220]


async def main() -> int:
    async with (
        streamablehttp_client(URL) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        tools = (await session.list_tools()).tools
        print(f"connected. server advertises {len(tools)} tools:")
        for t in tools:
            print(f"  - {t.name}: {t.description or ''[:60]}")
        print()

        calls = [
            ("incident_summary", {}),
            ("search_incidents", {"severity": "critical", "limit": 3}),
            ("semantic_search", {"query": "person near restricted area", "limit": 3}),
            ("verify_evidence", {}),
        ]
        for name, args in calls:
            try:
                res = await session.call_tool(name, args)
                print(f"call {name}({json.dumps(args)}) ->")
                print(f"    {_short(res)}")
            except Exception as exc:  # noqa: BLE001
                print(f"call {name} FAILED: {exc}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
