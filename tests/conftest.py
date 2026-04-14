"""Shared fixtures for all test modules."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agent.budget import Budget
from agent.llm_client import LLMClient
from agent.mcp_client import MCPClient
from agent.models import LLMResponse

# ---------------------------------------------------------------------------
# Event loop
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop_policy():  # type: ignore[override]
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------------
# Budget fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def generous_budget() -> Budget:
    return Budget(limit_usd=100.0)


@pytest.fixture
def tight_budget() -> Budget:
    return Budget(limit_usd=0.001)


# ---------------------------------------------------------------------------
# Mock LLM response builder
# ---------------------------------------------------------------------------


def make_llm_response(
    content: str = "",
    input_tokens: int = 100,
    output_tokens: int = 200,
    cost_usd: float = 0.001,
    model: str = "claude-sonnet-4-6",
    stop_reason: str = "end_turn",
) -> LLMResponse:
    return LLMResponse(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        model=model,
        stop_reason=stop_reason,
    )


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm(generous_budget: Budget) -> AsyncMock:
    """A mock LLMClient whose .call() returns a configurable LLMResponse."""
    client = AsyncMock(spec=LLMClient)
    client._budget = generous_budget
    return client


# ---------------------------------------------------------------------------
# Mock MCP client
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_mcp() -> AsyncMock:
    """A mock MCPClient whose .call_tool() returns an empty dict by default."""
    client = AsyncMock(spec=MCPClient)
    client.call_tool.return_value = {}
    return client


# ---------------------------------------------------------------------------
# Sample plan JSON (for planner tests)
# ---------------------------------------------------------------------------


SAMPLE_PLAN_JSON = """{
  "question": "What is the current market cap of Apple?",
  "rationale": "Search for current data then fetch the detail page.",
  "steps": [
    {
      "step_id": "step_1",
      "action": "Search for Apple market cap",
      "tool": "web_search",
      "arguments": {"query": "Apple Inc market cap 2026"},
      "depends_on": []
    },
    {
      "step_id": "step_2",
      "action": "Fetch detail from first result",
      "tool": "fetch_url",
      "arguments": {"url": "${step_1.result}"},
      "depends_on": ["step_1"]
    }
  ]
}"""
