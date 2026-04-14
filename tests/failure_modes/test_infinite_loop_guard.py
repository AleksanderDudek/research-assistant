"""Failure mode: reflector always requests more steps (infinite loop guard).

The agent must:
1. Hard-halt after settings.max_replan_cycles replan iterations.
2. Mark the run as HALTED_REPLAN_LIMIT.
3. NOT enter an infinite loop.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.config import settings
from agent.models import PlanStep, ReflectionOutput, RunStatus


def _always_insufficient_reflection() -> ReflectionOutput:
    """A reflection that always says more steps are needed."""
    return ReflectionOutput(
        sufficient=False,
        reasoning="I need more information",
        additional_steps=[
            PlanStep(
                step_id="extra_step",
                action="Search again",
                tool="web_search",
                arguments={"query": "more info"},
                depends_on=[],
            )
        ],
        final_answer="",
    )


@pytest.mark.asyncio
async def test_agent_halts_after_max_replan_cycles() -> None:
    """The agent stops replanning after max_replan_cycles and sets HALTED_REPLAN_LIMIT."""
    from agent.budget import Budget
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

        mock_plan = MagicMock()
        mock_plan.steps = []
        mock_plan.model_dump.return_value = {}
        mock_planner_instance = AsyncMock()
        mock_planner_instance.plan.return_value = mock_plan
        MockPlanner.return_value = mock_planner_instance

        mock_executor_instance = AsyncMock()
        mock_executor_instance.execute_plan.return_value = {}
        MockExecutor.return_value = mock_executor_instance

        # Reflector ALWAYS says more steps needed
        mock_reflector_instance = AsyncMock()
        mock_reflector_instance.reflect.return_value = _always_insufficient_reflection()
        MockReflector.return_value = mock_reflector_instance

        agent = Agent()
        await agent.run("Never satisfied question", budget=Budget(limit_usd=100.0))

    mock_mark_complete.assert_called_once()
    call_kwargs = mock_mark_complete.call_args.kwargs
    assert call_kwargs["status"] == RunStatus.HALTED_REPLAN_LIMIT


@pytest.mark.asyncio
async def test_reflector_called_at_most_max_replan_plus_one_times() -> None:
    """Reflector is called initial + max_replan_cycles times, then we stop."""
    from agent.budget import Budget
    from agent.core import Agent

    reflect_calls: list[int] = []

    with (
        patch("agent.core.Planner") as MockPlanner,
        patch("agent.core.Executor") as MockExecutor,
        patch("agent.core.Reflector") as MockReflector,
        patch("agent.core.LLMClient"),
        patch("agent.core.create_run") as mock_create_run,
        patch("agent.core.mark_run_complete"),
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

        mock_plan = MagicMock()
        mock_plan.steps = []
        mock_plan.model_dump.return_value = {}
        mock_planner_instance = AsyncMock()
        mock_planner_instance.plan.return_value = mock_plan
        MockPlanner.return_value = mock_planner_instance

        mock_executor_instance = AsyncMock()
        mock_executor_instance.execute_plan.return_value = {}
        MockExecutor.return_value = mock_executor_instance

        async def counting_reflect(*args, **kwargs) -> ReflectionOutput:  # type: ignore[no-untyped-def]
            reflect_calls.append(1)
            return _always_insufficient_reflection()

        mock_reflector_instance = AsyncMock()
        mock_reflector_instance.reflect.side_effect = counting_reflect
        MockReflector.return_value = mock_reflector_instance

        agent = Agent()
        await agent.run("Infinite question", budget=Budget(limit_usd=100.0))

    # Reflector is called: initial + up-to max_replan_cycles additional times
    # The loop exits when replan_count > max_replan_cycles, so reflector is called
    # max_replan_cycles + 1 times at most.
    assert len(reflect_calls) <= settings.max_replan_cycles + 1
    assert len(reflect_calls) >= 1  # at least called once
