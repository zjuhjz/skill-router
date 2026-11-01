from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from .base import InvocationResult


def normalize_result(result: CallToolResult) -> dict[str, Any]:
    structured = result.structured_content
    if isinstance(structured, dict):
        if isinstance(structured.get("ok"), bool):
            return structured
        if result.is_error:
            return {"ok": False, "error": structured}
        return {"ok": True, "output": structured}
    text = next(
        (item.text for item in result.content if isinstance(item, TextContent) and item.text.strip()),
        "",
    )
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            if isinstance(parsed.get("ok"), bool):
                return parsed
            if result.is_error:
                return {"ok": False, "error": parsed}
            return {"ok": True, "output": parsed}
    if result.is_error:
        return {
            "ok": False,
            "error": {"code": "MCP_TOOL_ERROR", "message": text or "MCP tool returned an error"},
        }
    if text:
        return {"ok": True, "output": {"text": text}}
    return {
        "ok": False,
        "error": {"code": "INVALID_RESPONSE", "message": "MCP tool returned no structured or text content"},
    }


async def _call_remote(url: str, tool: str, arguments: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    async def call() -> dict[str, Any]:
        async with streamable_http_client(url) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timeout_s) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments, read_timeout_seconds=timeout_s)
                if not isinstance(result, CallToolResult):
                    raise RuntimeError(f"unexpected MCP result type: {type(result).__name__}")
                return normalize_result(result)

    return await asyncio.wait_for(call(), timeout=timeout_s)


def invoke(entrypoint: dict[str, Any], arguments: dict[str, Any], timeout_s: float) -> InvocationResult:
    url = str(entrypoint.get("url") or "").strip()
    tool = str(entrypoint.get("tool") or "").strip()
    if not url or not tool:
        return InvocationResult(None, 0, "INVALID_MCP_ENTRYPOINT: url and tool are required")
    started = time.perf_counter()
    try:
        response = asyncio.run(_call_remote(url, tool, arguments, timeout_s))
    except TimeoutError:
        return InvocationResult(None, (time.perf_counter() - started) * 1000, "TIMEOUT")
    except Exception as exc:
        return InvocationResult(
            None,
            (time.perf_counter() - started) * 1000,
            f"MCP_TRANSPORT_ERROR: {type(exc).__name__}: {exc}",
        )
    return InvocationResult(response, (time.perf_counter() - started) * 1000)
