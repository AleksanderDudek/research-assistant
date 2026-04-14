"""Async persistence layer for runs, steps, and tool calls."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.models import RunRecord, RunStatus, StepKind
from db.models import Message, Run, Step, ToolCall
from db.session import db_session

log = structlog.get_logger(__name__)


async def create_run(question: str) -> RunRecord:
    """Persist a new run and return its record."""
    run_id = uuid.uuid4()
    async with db_session() as session:
        run = Run(id=run_id, question=question)
        session.add(run)
    log.info("run.created", run_id=str(run_id))
    return RunRecord(id=run_id, question=question)


async def load_run(run_id: uuid.UUID) -> RunRecord:
    """Load a run by ID. Raises KeyError if not found."""
    async with db_session() as session:
        result = await session.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            raise KeyError(f"Run {run_id} not found")
        return RunRecord(
            id=run.id,
            question=run.question,
            status=RunStatus(run.status),
            started_at=run.started_at,
            ended_at=run.ended_at,
            total_cost_usd=run.total_cost_usd,
            final_answer=run.final_answer,
            replan_count=run.replan_count,
        )


async def append_step(
    session: AsyncSession,
    run_id: uuid.UUID,
    ordinal: int,
    kind: StepKind,
    content: dict[str, Any],
    cost_usd: float = 0.0,
) -> uuid.UUID:
    """Append a step to a run within an existing session. Returns step id."""
    step_id = uuid.uuid4()
    step = Step(
        id=step_id,
        run_id=run_id,
        ordinal=ordinal,
        kind=kind.value,
        content_json=content,
        cost_usd=cost_usd,
        started_at=datetime.utcnow(),
        ended_at=datetime.utcnow(),
    )
    session.add(step)
    return step_id


async def append_tool_call(
    session: AsyncSession,
    step_id: uuid.UUID,
    tool_name: str,
    arguments: dict[str, Any],
    result: Any,
    error: str | None = None,
    latency_ms: int = 0,
) -> None:
    """Record a tool invocation."""
    tc = ToolCall(
        id=uuid.uuid4(),
        step_id=step_id,
        tool_name=tool_name,
        arguments_json=arguments,
        result_json=result if isinstance(result, dict) else {"value": result},
        error=error,
        latency_ms=latency_ms,
    )
    session.add(tc)


async def append_message(
    session: AsyncSession,
    run_id: uuid.UUID,
    role: str,
    content: str,
) -> None:
    """Record a raw LLM message."""
    msg = Message(id=uuid.uuid4(), run_id=run_id, role=role, content=content)
    session.add(msg)


async def mark_run_complete(
    run_id: uuid.UUID,
    status: RunStatus,
    final_answer: str,
    total_cost_usd: float,
    replan_count: int,
) -> None:
    """Update run status and write final answer."""
    async with db_session() as session:
        result = await session.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one()
        run.status = status.value
        run.final_answer = final_answer
        run.total_cost_usd = total_cost_usd
        run.replan_count = replan_count
        run.ended_at = datetime.utcnow()
    log.info("run.complete", run_id=str(run_id), status=status.value)


async def get_completed_steps(run_id: uuid.UUID) -> list[dict[str, Any]]:
    """Return step content dicts for a run (used on resume)."""
    async with db_session() as session:
        result = await session.execute(
            select(Step)
            .where(Step.run_id == run_id)
            .where(Step.kind == StepKind.EXECUTE.value)
            .order_by(Step.ordinal)
        )
        steps = result.scalars().all()
        return [s.content_json for s in steps]
