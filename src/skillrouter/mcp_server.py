"""stdio MCP server. Exactly three tools, regardless of how many Skills are installed."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from skillrouter.engine import describe_capability, invoke, list_capabilities
from skillrouter.utf8 import force_utf8
from skillrouter.version import __version__

INSTRUCTIONS = (
    "Ask for a capability such as video.resolve@1, video.download@1, image.download@1, or video.transcript@1. "
    "The router picks an installed Skill, falls back when the error code says to, "
    "and returns one JSON result. Pass one user-supplied URL at a time. "
    "Do not bulk-download, re-upload, or host media. "
    "A new Skill returns NEED_CONFIRM until the user confirms it (options.yes or the skillrouter CLI). "
    "Paid Skills run only when options.allow_paid is true."
)

server = MCPServer(
    name="skill-router",
    title="skill-router",
    instructions=INSTRUCTIONS,
    version=__version__,
    log_level="WARNING",
)


@server.tool(
    name="list_capabilities",
    description=(
        "List capabilities the registry publishes and the installed Skills that provide them, "
        "including platform, health, cost, and which credential names are required."
    ),
    structured_output=False,
)
def list_capabilities_tool() -> dict[str, Any]:
    return list_capabilities()


@server.tool(
    name="describe_capability",
    description="Return the input schema, output schema, summary, and examples for one capability.",
    structured_output=False,
)
def describe_capability_tool(capability: str) -> dict[str, Any]:
    return describe_capability(capability)


@server.tool(
    name="invoke",
    description=(
        "Invoke a capability. input matches that capability's input schema. "
        "options may include prefer_skill, allow_paid, max_attempts, save_to, and yes."
    ),
    structured_output=False,
)
def invoke_tool(
    capability: str,
    input: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    opts = dict(options or {})
    yes = bool(opts.pop("yes", False))
    return invoke(capability, input or {}, options=opts, yes=yes)


def serve() -> None:
    force_utf8()
    server.run(transport="stdio")


if __name__ == "__main__":
    serve()
