"""Thin MCP client – calls the MCP server over stdio subprocess or HTTP SSE."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, cast

import httpx
import structlog

from agent.config import settings
from agent.telemetry import get_tracer

log = structlog.get_logger(__name__)


class MCPError(Exception):
    """Raised when the MCP tool call fails."""


class MCPClient:
    """Client for the MCP research-tools server.

    In production (docker-compose) the MCP server runs as a separate process
    reachable via HTTP + SSE. In tests, we can swap this with a mock.

    The client supports two modes:
    - HTTP (default): POSTs to MCP_SERVER_URL/call_tool
    - stdio subprocess: spawns the MCP server process inline (for local dev)
    """

    def __init__(self, server_url: str | None = None, timeout: float | None = None) -> None:
        self._server_url = (server_url or settings.mcp_server_url).rstrip("/")
        self._timeout = timeout or settings.tool_timeout_seconds
        self._tracer = get_tracer("mcp_client")

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Invoke an MCP tool and return the parsed result.

        Args:
            tool_name: Name of the MCP tool (e.g. 'web_search').
            arguments: Tool arguments as a dict.

        Returns:
            Parsed JSON result from the tool.

        Raises:
            MCPError: On transport or tool errors.
            asyncio.TimeoutError: If the tool exceeds the configured timeout.
        """
        with self._tracer.start_as_current_span(f"tool.{tool_name}") as span:
            span.set_attribute("tool.name", tool_name)
            span.set_attribute("tool.args", json.dumps(arguments)[:256])

            t0 = time.monotonic()
            log.info("mcp.call", tool=tool_name, args=arguments)

            try:
                result = await asyncio.wait_for(
                    self._http_call(tool_name, arguments),
                    timeout=self._timeout,
                )
            except TimeoutError:
                span.set_attribute("tool.timed_out", True)
                raise
            except Exception as exc:
                span.set_attribute("tool.error", str(exc))
                raise MCPError(f"MCP tool '{tool_name}' failed: {exc}") from exc

            latency_ms = int((time.monotonic() - t0) * 1000)
            span.set_attribute("tool.latency_ms", latency_ms)
            log.info("mcp.done", tool=tool_name, latency_ms=latency_ms)
            return result

    async def _http_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call the MCP HTTP endpoint directly."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._server_url}",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

        if "error" in data:
            raise MCPError(f"MCP error: {data['error']}")

        # MCP result is a list of content items; we expect a single TextContent
        result_items = data.get("result", {}).get("content", [])
        if not result_items:
            return {}

        text = result_items[0].get("text", "{}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the list of available tools from the MCP server."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self._server_url, json=payload)
            response.raise_for_status()
            data = response.json()
        return cast("list[dict[str, Any]]", data.get("result", {}).get("tools", []))
