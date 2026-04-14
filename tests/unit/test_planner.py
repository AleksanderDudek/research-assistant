"""Unit tests for the Planner."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent.planner import Planner
from tests.conftest import SAMPLE_PLAN_JSON, make_llm_response


@pytest.fixture
def planner(mock_llm: AsyncMock) -> Planner:
    return Planner(llm=mock_llm)


@pytest.mark.asyncio
async def test_planner_returns_plan(planner: Planner, mock_llm: AsyncMock) -> None:
    mock_llm.call.return_value = make_llm_response(content=SAMPLE_PLAN_JSON)
    plan = await planner.plan("What is the current market cap of Apple?")
    assert plan.question == "What is the current market cap of Apple?"
    assert len(plan.steps) == 2


@pytest.mark.asyncio
async def test_planner_has_web_search_step(planner: Planner, mock_llm: AsyncMock) -> None:
    """For an Apple market-cap question the plan must include a web_search step."""
    mock_llm.call.return_value = make_llm_response(content=SAMPLE_PLAN_JSON)
    plan = await planner.plan("What is the current market cap of Apple?")
    tools_used = {s.tool for s in plan.steps}
    assert "web_search" in tools_used


@pytest.mark.asyncio
async def test_planner_has_fetch_or_search(planner: Planner, mock_llm: AsyncMock) -> None:
    """Plan should include a fetch_url or search step after web_search."""
    mock_llm.call.return_value = make_llm_response(content=SAMPLE_PLAN_JSON)
    plan = await planner.plan("What is the current market cap of Apple?")
    tools_used = {s.tool for s in plan.steps}
    assert tools_used & {"fetch_url", "search_knowledge_base"}


@pytest.mark.asyncio
async def test_planner_retries_on_invalid_json(planner: Planner, mock_llm: AsyncMock) -> None:
    """On bad JSON, planner retries once and succeeds on the second call."""
    good_response = make_llm_response(content=SAMPLE_PLAN_JSON)
    mock_llm.call.side_effect = [
        make_llm_response(content="this is not json {{{{"),
        good_response,
    ]
    plan = await planner.plan("What is Apple's market cap?")
    assert len(plan.steps) >= 1
    assert mock_llm.call.call_count == 2


@pytest.mark.asyncio
async def test_planner_depends_on_respected(planner: Planner, mock_llm: AsyncMock) -> None:
    mock_llm.call.return_value = make_llm_response(content=SAMPLE_PLAN_JSON)
    plan = await planner.plan("Question?")
    step_map = {s.step_id: s for s in plan.steps}
    # step_2 depends on step_1
    step_2 = step_map.get("step_2")
    if step_2:
        assert "step_1" in step_2.depends_on
