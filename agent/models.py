"""Pydantic domain models for the agent runtime."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    HALTED_OVER_BUDGET = "halted_over_budget"
    HALTED_REPLAN_LIMIT = "halted_replan_limit"


class StepKind(StrEnum):
    PLAN = "plan"
    EXECUTE = "execute"
    REFLECT = "reflect"


# ---------------------------------------------------------------------------
# Plan structures
# ---------------------------------------------------------------------------


class PlanStep(BaseModel):
    """A single step inside an agent plan."""

    step_id: str = Field(..., description="Unique identifier within this plan, e.g. 'step_1'")
    action: str = Field(..., description="Human-readable description of what to do")
    tool: str = Field(..., description="MCP tool to invoke, e.g. 'web_search'")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    depends_on: list[str] = Field(
        default_factory=list,
        description="Step IDs that must complete before this step",
    )


class Plan(BaseModel):
    """Structured plan produced by the planner."""

    question: str = Field(..., description="The original research question")
    steps: list[PlanStep] = Field(..., description="Ordered list of investigation steps")
    rationale: str = Field(
        default="", description="Brief explanation of why this plan addresses the question"
    )


# ---------------------------------------------------------------------------
# LLM / tool call records
# ---------------------------------------------------------------------------


class LLMResponse(BaseModel):
    """Normalised response from the LLM client."""

    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    raw_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    stop_reason: str = ""


class ToolCallRecord(BaseModel):
    """Record of a single MCP tool invocation."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    step_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None
    latency_ms: int = 0


class StepResult(BaseModel):
    """Result of executing a single plan step."""

    step_id: str
    tool_name: str
    result: Any
    error: str | None = None
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Run / reflection
# ---------------------------------------------------------------------------


class ReflectionOutput(BaseModel):
    """Output from the reflector LLM call."""

    sufficient: bool = Field(..., description="True if there is enough info to answer the question")
    additional_steps: list[PlanStep] = Field(
        default_factory=list,
        description="New steps to execute if not sufficient",
    )
    final_answer: str = Field(default="", description="Final cited answer when sufficient=True")
    reasoning: str = Field(default="", description="Why more steps are / are not needed")


class RunRecord(BaseModel):
    """In-memory representation of a run (mirrors the DB row)."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    question: str
    status: RunStatus = RunStatus.RUNNING
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    total_cost_usd: float = 0.0
    final_answer: str = ""
    replan_count: int = 0
