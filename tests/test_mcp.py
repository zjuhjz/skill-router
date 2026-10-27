import asyncio
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from skillrouter.mcp_server import server

REPO = Path(__file__).resolve().parents[1]
EXPECTED = ["describe_capability", "invoke", "list_capabilities"]


def test_server_lists_exactly_three_tools():
    tools = asyncio.run(server.list_tools())
    assert sorted(tool.name for tool in tools) == EXPECTED


def test_stdio_server_lists_exactly_three_tools():
    async def go() -> list[str]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "skillrouter.mcp_server"],
            env={**dict(**__import__("os").environ), "PYTHONPATH": str(REPO / "src")},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                return sorted(tool.name for tool in listed.tools)

    names = asyncio.run(asyncio.wait_for(go(), timeout=20))
    assert names == EXPECTED
