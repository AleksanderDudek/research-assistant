"""Failure mode: Claude refuses to answer.

The reflector receives a refusal (stop_reason='end_turn' but content indicates
refusal). The agent should:
1. Record the refusal in the run state.
2. Mark the run as COMPLETED (refusals are a valid terminal state).
3. NOT crash or loop.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.models import Plan, PlanStep, ReflectionOutput, RunStatus

REFUSAL_TEXT = (
    "I'm unable to help with that request as it may violate usage policies. "
    "Please rephrase your question."
)


@pytest.mark.asyncio
async def test_reflector_handles_refusal_gracefully() -> None:
    """Reflector treats a refusal response as sufficient=True with refusal text."""
    from agent.llm_client import LLMClient
    from agent.reflector import Reflector
    from tests.conftest import make_llm_response

    mock_llm = AsyncMock(spec=LLMClient)
    # Simulate Claude refusing in a non-JSON response
    mock_llm.call.return_value = make_llm_response(content=REFUSAL_TEXT, stop_reason="end_turn")

    reflector = Reflector(llm=mock_llm)

    plan = Plan(
        question="Sensitive question",
        steps=[
            PlanStep(
                step_id="step_1",
                action="search",
                tool="web_search",
                arguments={"query": "x"},
                depends_on=[],
            )
        ],
    )
    from agent.models import StepResult

    context = {
        "step_1": StepResult(step_id="step_1", tool_name="web_search", result={"results": []})
    }

    # Should NOT raise even with non-JSON content
    reflection = await reflector.reflect("Sensitive question", plan, context)
    assert isinstance(reflection, ReflectionOutput)
    # When we can't parse JSON, we treat it as sufficient with raw content
    assert reflection.sufficient is True


@pytest.mark.asyncio
async def test_agent_ends_cleanly_on_refusal() -> None:
    """The agent writes the refusal to state and returns without crashing."""
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

        # Planner returns a simple plan
        mock_plan = MagicMock()
        mock_plan.steps = []
        mock_plan.model_dump.return_value = {}
        mock_planner_instance = AsyncMock()
        mock_planner_instance.plan.return_value = mock_plan
        MockPlanner.return_value = mock_planner_instance

        # Executor returns empty context
        mock_executor_instance = AsyncMock()
        mock_executor_instance.execute_plan.return_value = {}
        MockExecutor.return_value = mock_executor_instance

        # Reflector returns a refusal as the final answer
        mock_reflector_instance = AsyncMock()
        mock_reflector_instance.reflect.return_value = ReflectionOutput(
            sufficient=True,
            final_answer=REFUSAL_TEXT,
            reasoning="Model refused the request",
        )
        MockReflector.return_value = mock_reflector_instance

        from agent.budget import Budget

        agent = Agent()
        result = await agent.run("Sensitive question", budget=Budget(limit_usd=10.0))

    # Run should complete (not fail)
    mock_mark_complete.assert_called_once()
    call_kwargs = mock_mark_complete.call_args.kwargs
    assert call_kwargs["status"] == RunStatus.COMPLETED
    assert REFUSAL_TEXT in call_kwargs["final_answer"]
