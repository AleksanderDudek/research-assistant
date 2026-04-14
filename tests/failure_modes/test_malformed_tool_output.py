"""Failure mode: MCP tool returns malformed / unexpected output.

The executor should gracefully handle:
- Non-JSON responses from the MCP server.
- Missing required fields in the result dict.
- Empty results where a list is expected.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.executor import Executor
from agent.mcp_client import MCPClient, MCPError
from agent.models import Plan, PlanStep


def _single_step_plan() -> Plan:
    return Plan(
        question="Q",
        steps=[
            PlanStep(
                step_id="step_1",
                action="search",
                tool="web_search",
                arguments={"query": "test"},
                depends_on=[],
            )
        ],
    )


@pytest.mark.asyncio
async def test_mcp_error_on_malformed_response() -> None:
    """When the MCP client raises MCPError (from malformed JSON), the step is failed."""
    mock_mcp = AsyncMock(spec=MCPClient)
    mock_mcp.call_tool.side_effect = MCPError("Response was not valid JSON")

    executor = Executor(mcp=mock_mcp, retry_attempts=2)
    with patch("agent.executor.asyncio.sleep"):
        context = await executor.execute_plan(_single_step_plan())

    step = context["step_1"]
    assert step.error is not None
    assert step.result is None


@pytest.mark.asyncio
async def test_empty_results_still_stored() -> None:
    """An empty dict result is stored without error (tool returned nothing)."""
    mock_mcp = AsyncMock(spec=MCPClient)
    mock_mcp.call_tool.return_value = {}  # empty but valid

    executor = Executor(mcp=mock_mcp, retry_attempts=1)
    context = await executor.execute_plan(_single_step_plan())

    step = context["step_1"]
    assert step.error is None
    assert step.result == {}


@pytest.mark.asyncio
async def test_unexpected_exception_marks_step_failed() -> None:
    """Any unexpected exception from the MCP call marks the step as failed."""
    mock_mcp = AsyncMock(spec=MCPClient)
    mock_mcp.call_tool.side_effect = RuntimeError("Unexpected crash")

    executor = Executor(mcp=mock_mcp, retry_attempts=2)
    with patch("agent.executor.asyncio.sleep"):
        context = await executor.execute_plan(_single_step_plan())

    step = context["step_1"]
    assert step.error is not None


@pytest.mark.asyncio
async def test_execution_proceeds_to_next_step_after_error() -> None:
    """A failed step does not block subsequent independent steps."""
    mock_mcp = AsyncMock(spec=MCPClient)
    mock_mcp.call_tool.side_effect = [
        MCPError("bad"),
        MCPError("bad"),
        {"text": "success"},
    ]

    plan = Plan(
        question="Q",
        steps=[
            PlanStep(
                step_id="step_1",
                action="a",
                tool="web_search",
                arguments={"query": "x"},
                depends_on=[],
            ),
            PlanStep(
                step_id="step_2",
                action="b",
                tool="fetch_url",
                arguments={"url": "https://x.com"},
                depends_on=[],
            ),
        ],
    )
    executor = Executor(mcp=mock_mcp, retry_attempts=2)
    with patch("agent.executor.asyncio.sleep"):
        context = await executor.execute_plan(plan)

    assert context["step_2"].error is None
    assert context["step_2"].result == {"text": "success"}
