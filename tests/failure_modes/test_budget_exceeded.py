"""Failure mode: budget is exceeded mid-run.

The agent should:
1. Raise BudgetExceeded from LLMClient.call() when the limit is crossed.
2. Mark the run status as HALTED_OVER_BUDGET.
3. Retain any partial results gathered before the budget ran out.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.budget import Budget, BudgetExceeded
from agent.models import LLMResponse, RunStatus
from tests.conftest import make_llm_response


@pytest.mark.asyncio()
async def test_budget_exceeded_raises_immediately() -> None:
    """BudgetExceeded is raised as soon as Budget.charge() is called over limit."""
    b = Budget(limit_usd=0.001)
    with pytest.raises(BudgetExceeded):
        b.charge(1.00)


@pytest.mark.asyncio()
async def test_budget_status_after_exceeded() -> None:
    """After exceeding, spent() reflects the overage, remaining() is 0."""
    b = Budget(limit_usd=0.10)
    try:
        b.charge(0.20)
    except BudgetExceeded:
        pass
    assert b.spent() > 0.10
    assert b.remaining() == 0.0


@pytest.mark.asyncio()
async def test_llm_client_raises_budget_exceeded() -> None:
    """LLMClient.call() propagates BudgetExceeded from the budget."""
    from agent.llm_client import LLMClient

    tight = Budget(limit_usd=0.00001)

    with patch("agent.llm_client.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client

        # Simulate an API response that costs more than the budget
        mock_usage = MagicMock()
        mock_usage.input_tokens = 1000
        mock_usage.output_tokens = 2000
        mock_response = MagicMock()
        mock_response.usage = mock_usage
        mock_response.content = [MagicMock(type="text", text="response")]
        mock_response.stop_reason = "end_turn"
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        llm = LLMClient(budget=tight, api_key="test-key")
        with pytest.raises(BudgetExceeded):
            await llm.call(
                messages=[{"role": "user", "content": "hello"}],
                model="claude-sonnet-4-6",
            )


@pytest.mark.asyncio()
async def test_agent_marks_run_halted_over_budget() -> None:
    """When BudgetExceeded propagates to the agent, it sets HALTED_OVER_BUDGET."""
    from agent.core import Agent
    from agent.budget import Budget

    # Use a tiny budget that will be exceeded by the first LLM call
    tiny_budget = Budget(limit_usd=0.00001)

    with (
        patch("agent.core.Planner") as MockPlanner,
        patch("agent.core.Executor"),
        patch("agent.core.Reflector"),
        patch("agent.core.LLMClient"),
        patch("agent.core.create_run") as mock_create_run,
        patch("agent.core.mark_run_complete") as mock_mark_complete,
        patch("agent.core.db_session"),
        patch("agent.core.MCPClient"),
    ):
        run_id = uuid.uuid4()
        mock_record = MagicMock()
        mock_record.id = run_id
        mock_record.status = RunStatus.RUNNING
        mock_record.replan_count = 0
        mock_record.total_cost_usd = 0.0
        mock_create_run.return_value = mock_record

        mock_planner_instance = AsyncMock()
        mock_planner_instance.plan.side_effect = BudgetExceeded(0.00001, 0.01)
        MockPlanner.return_value = mock_planner_instance

        agent = Agent()
        result = await agent.run("test question", budget=tiny_budget)

    # Verify mark_run_complete was called with halted status
    mock_mark_complete.assert_called_once()
    call_kwargs = mock_mark_complete.call_args.kwargs
    assert call_kwargs["status"] == RunStatus.HALTED_OVER_BUDGET
