"""Failure mode: MCP tool times out.

The executor should:
1. Catch asyncio.TimeoutError from the MCP client.
2. Retry once with exponential backoff.
3. On second failure, return a StepResult with error set.
4. Allow the reflector to replan rather than crashing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.executor import Executor
from agent.mcp_client import MCPClient
from agent.models import Plan, PlanStep


def _make_single_step_plan() -> Plan:
    return Plan(
        question="Test question",
        steps=[
            PlanStep(
                step_id="step_1",
                action="Search the web",
                tool="web_search",
                arguments={"query": "test"},
                depends_on=[],
            )
        ],
    )


@pytest.mark.asyncio
async def test_tool_timeout_retries_and_marks_failed() -> None:
    """When a tool times out on every attempt, the step is marked failed."""
    mock_mcp = AsyncMock(spec=MCPClient)
    mock_mcp.call_tool.side_effect = TimeoutError("tool timed out")

    executor = Executor(mcp=mock_mcp, retry_attempts=2)
    plan = _make_single_step_plan()
    context = await executor.execute_plan(plan)

    step = context["step_1"]
    assert step.error is not None
    assert "timed out" in step.error.lower() or step.result is None
    # Should have been called exactly retry_attempts times
    assert mock_mcp.call_tool.call_count == 2


@pytest.mark.asyncio
async def test_tool_timeout_then_success_on_retry() -> None:
    """When the first call times out but the second succeeds, result is captured."""
    mock_mcp = AsyncMock(spec=MCPClient)
    mock_mcp.call_tool.side_effect = [
        TimeoutError("timeout"),
        {"results": [{"title": "ok", "url": "https://example.com", "snippet": "good"}]},
    ]

    executor = Executor(mcp=mock_mcp, retry_attempts=2)
    plan = _make_single_step_plan()

    # Patch sleep so the test does not actually wait
    with patch("agent.executor.asyncio.sleep"):
        context = await executor.execute_plan(plan)

    step = context["step_1"]
    assert step.error is None
    assert step.result is not None
    assert mock_mcp.call_tool.call_count == 2


@pytest.mark.asyncio
async def test_plan_continues_after_failed_step() -> None:
    """Execution continues to subsequent steps even if one step fails."""
    mock_mcp = AsyncMock(spec=MCPClient)
    # step_1 times out, step_2 succeeds
    mock_mcp.call_tool.side_effect = [
        TimeoutError(),
        TimeoutError(),
        {"text": "fallback content"},
    ]

    plan = Plan(
        question="Q",
        steps=[
            PlanStep(step_id="step_1", action="search", tool="web_search", arguments={"query": "x"}, depends_on=[]),
            PlanStep(step_id="step_2", action="fetch", tool="fetch_url", arguments={"url": "https://example.com"}, depends_on=[]),
        ],
    )
    executor = Executor(mcp=mock_mcp, retry_attempts=2)
    with patch("agent.executor.asyncio.sleep"):
        context = await executor.execute_plan(plan)

    assert "step_1" in context
    assert "step_2" in context
    assert context["step_1"].error is not None
    assert context["step_2"].error is None
