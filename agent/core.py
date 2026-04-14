"""Agent core - orchestrates plan -> execute -> reflect -> replan loop."""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agent.budget import Budget, BudgetExceeded
from agent.config import settings
from agent.executor import Executor
from agent.llm_client import LLMClient
from agent.mcp_client import MCPClient
from agent.models import Plan, RunRecord, RunStatus, StepKind, StepResult
from agent.planner import Planner
from agent.reflector import Reflector
from agent.state import (
    append_step,
    create_run,
    get_completed_steps,
    load_run,
    mark_run_complete,
)
from agent.telemetry import get_tracer
from db.session import db_session

log = structlog.get_logger(__name__)


class Agent:
    """Research agent that plans, executes MCP tools, and reflects.

    Args:
        mcp_url: Override the MCP server URL (useful in tests).
        api_key: Override the Anthropic API key (useful in tests).
    """

    def __init__(
        self,
        mcp_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._mcp_url = mcp_url
        self._api_key = api_key
        self._tracer = get_tracer("agent")

    async def run(
        self,
        question: str,
        budget: Budget | None = None,
        resume_run_id: uuid.UUID | None = None,
    ) -> RunRecord:
        """Execute a full research run.

        Args:
            question: The research question.
            budget: Optional Budget. Defaults to settings.default_budget_usd.
            resume_run_id: If set, resume a previously started run by ID.

        Returns:
            Completed RunRecord with final_answer and status.
        """
        if budget is None:
            budget = Budget(limit_usd=settings.default_budget_usd)

        llm = LLMClient(budget=budget, api_key=self._api_key)
        planner = Planner(llm=llm)
        executor = Executor(mcp=MCPClient(server_url=self._mcp_url))
        reflector = Reflector(llm=llm)

        with self._tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("agent.question", question[:200])
            span.set_attribute("agent.budget_usd", budget.limit_usd)

            context: dict[str, StepResult]
            if resume_run_id is not None:
                run_record = await load_run(resume_run_id)
                log.info("agent.resume", run_id=str(resume_run_id), status=run_record.status.value)
                # Only resume runs that are still running
                if run_record.status != RunStatus.RUNNING:
                    log.info("agent.already_done", status=run_record.status.value)
                    return run_record
                context = await self._restore_context(run_record.id)
            else:
                run_record = await create_run(question)
                context = {}

            run_id = run_record.id
            replan_count = run_record.replan_count
            total_cost = run_record.total_cost_usd
            ordinal = 0

            try:
                # --- Plan phase ---
                with self._tracer.start_as_current_span("agent.plan"):
                    plan = await planner.plan(question)

                async with db_session() as session:
                    await self._persist_plan(session, run_id, plan, ordinal)
                    ordinal += 1

                while True:
                    # --- Execute phase ---
                    with self._tracer.start_as_current_span(f"agent.execute.cycle_{replan_count}"):
                        context = await executor.execute_plan(plan, context)

                    async with db_session() as session:
                        await self._persist_execute_results(session, run_id, context, ordinal)
                        ordinal += 1

                    # --- Reflect phase ---
                    with self._tracer.start_as_current_span("agent.reflect"):
                        reflection = await reflector.reflect(question, plan, context)

                    async with db_session() as session:
                        await append_step(
                            session, run_id, ordinal, StepKind.REFLECT,
                            {"sufficient": reflection.sufficient, "reasoning": reflection.reasoning},  # noqa: E501
                        )
                        ordinal += 1

                    if reflection.sufficient:
                        log.info("agent.sufficient", run_id=str(run_id))
                        final_answer = reflection.final_answer
                        status = RunStatus.COMPLETED
                        break

                    replan_count += 1
                    if replan_count > settings.max_replan_cycles:
                        log.warning("agent.replan_limit", run_id=str(run_id))
                        final_answer = (
                            "Research halted: maximum replan cycles reached. "
                            "Partial results have been stored."
                        )
                        status = RunStatus.HALTED_REPLAN_LIMIT
                        break

                    log.info(
                        "agent.replan",
                        cycle=replan_count,
                        n_new_steps=len(reflection.additional_steps),
                    )
                    # Extend the plan with additional steps from the reflector
                    plan = Plan(
                        question=question,
                        steps=reflection.additional_steps,
                        rationale=f"Replan cycle {replan_count}",
                    )

            except BudgetExceeded as exc:
                log.warning("agent.budget_exceeded", error=str(exc))
                final_answer = f"Research halted: {exc}"
                status = RunStatus.HALTED_OVER_BUDGET

            except Exception as exc:
                log.exception("agent.fatal_error", error=str(exc))
                final_answer = f"Research failed with unexpected error: {exc}"
                status = RunStatus.FAILED

            total_cost = budget.spent()
            await mark_run_complete(
                run_id=run_id,
                status=status,
                final_answer=final_answer,
                total_cost_usd=total_cost,
                replan_count=replan_count,
            )

            span.set_attribute("agent.status", status.value)
            span.set_attribute("agent.total_cost_usd", total_cost)

            run_record.status = status
            run_record.final_answer = final_answer
            run_record.total_cost_usd = total_cost
            run_record.replan_count = replan_count
            run_record.ended_at = datetime.utcnow()

            return run_record

    async def _persist_plan(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        plan: Plan,
        ordinal: int,
    ) -> None:
        await append_step(
            session,
            run_id,
            ordinal,
            StepKind.PLAN,
            plan.model_dump(),
        )

    async def _persist_execute_results(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        context: dict[str, StepResult],
        ordinal: int,
    ) -> uuid.UUID:
        step_id = await append_step(
            session,
            run_id,
            ordinal,
            StepKind.EXECUTE,
            {k: v.model_dump() for k, v in context.items()},
        )
        return step_id

    async def _restore_context(self, run_id: uuid.UUID) -> dict[str, StepResult]:
        """Reconstruct execution context from DB steps for a resumed run."""
        completed = await get_completed_steps(run_id)
        context: dict[str, StepResult] = {}
        for step_data in completed:
            for step_id, step_dict in step_data.items():
                try:
                    context[step_id] = StepResult(**step_dict)
                except Exception:  # noqa: S110
                    pass
        log.info("agent.restored_context", n_steps=len(context))
        return context
