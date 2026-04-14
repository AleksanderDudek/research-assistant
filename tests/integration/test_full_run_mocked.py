"""Integration test: a full agent run with all external calls mocked.

Tests the complete plan → execute → reflect flow without hitting real APIs
or a real database.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.budget import Budget
from agent.models import Plan, PlanStep, ReflectionOutput, RunStatus
from tests.conftest import SAMPLE_PLAN_JSON


@pytest.fixture
def mock_plan() -> Plan:
    data = json.loads(SAMPLE_PLAN_JSON)
    steps = [PlanStep(**s) for s in data["steps"]]
    return Plan(question=data["question"], steps=steps, rationale=data.get("rationale", ""))


@pytest.mark.asyncio
async def test_full_run_completes(mock_plan: Plan) -> None:
    """A full run with mocked LLM and MCP should reach COMPLETED status."""
    from agent.core import Agent

    with (
        patch("agent.core.Planner") as MockPlanner,
        patch("agent.core.Executor") as MockExecutor,
        patch("agent.core.Reflector") as MockReflector,
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

        planner_inst = AsyncMock()
        planner_inst.plan.return_value = mock_plan
        MockPlanner.return_value = planner_inst

        executor_inst = AsyncMock()
        from agent.models import StepResult

        executor_inst.execute_plan.return_value = {
            "step_1": StepResult(
                step_id="step_1",
                tool_name="web_search",
                result={
                    "results": [
                        {
                            "title": "Apple",
                            "url": "https://apple.com",
                            "snippet": "AAPL market cap is $3T",
                        }
                    ]
                },
            ),
            "step_2": StepResult(
                step_id="step_2",
                tool_name="fetch_url",
                result={"text": "Apple's market cap is approximately $3 trillion."},
            ),
        }
        MockExecutor.return_value = executor_inst

        reflector_inst = AsyncMock()
        reflector_inst.reflect.return_value = ReflectionOutput(
            sufficient=True,
            final_answer="Apple's market cap is approximately $3 trillion [1].\n\nReferences:\n[1] https://apple.com",
            reasoning="We have clear data from the search result.",
        )
        MockReflector.return_value = reflector_inst

        agent = Agent()
        result = await agent.run("What is Apple's market cap?", budget=Budget(limit_usd=5.0))

    mock_mark_complete.assert_called_once()
    call_kwargs = mock_mark_complete.call_args.kwargs
    assert call_kwargs["status"] == RunStatus.COMPLETED
    assert "Apple" in call_kwargs["final_answer"]


@pytest.mark.asyncio
async def test_full_run_includes_citations(mock_plan: Plan) -> None:
    """Final answer should include citation markers when sufficient."""
    from agent.core import Agent

    with (
        patch("agent.core.Planner") as MockPlanner,
        patch("agent.core.Executor") as MockExecutor,
        patch("agent.core.Reflector") as MockReflector,
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

        planner_inst = AsyncMock()
        planner_inst.plan.return_value = mock_plan
        MockPlanner.return_value = planner_inst

        executor_inst = AsyncMock()
        executor_inst.execute_plan.return_value = {}
        MockExecutor.return_value = executor_inst

        reflector_inst = AsyncMock()
        answer_with_citations = "The answer is X [1].\n\nReferences:\n[1] https://source.com"
        reflector_inst.reflect.return_value = ReflectionOutput(
            sufficient=True,
            final_answer=answer_with_citations,
        )
        MockReflector.return_value = reflector_inst

        agent = Agent()
        await agent.run("Question?", budget=Budget(limit_usd=5.0))

    call_kwargs = mock_mark_complete.call_args.kwargs
    assert "[1]" in call_kwargs["final_answer"]
    assert "References" in call_kwargs["final_answer"]
