"""Executor – walks a Plan, calls MCP tools, stores results."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Any

import structlog

from agent.config import settings
from agent.mcp_client import MCPClient, MCPError
from agent.models import Plan, PlanStep, StepKind, StepResult

log = structlog.get_logger(__name__)


def _resolve_arguments(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Replace ${step_N.result} template tokens with actual step results.

    Args:
        arguments: Raw argument dict, may contain template strings.
        context: Maps step_id -> StepResult for completed steps.

    Returns:
        Arguments dict with all templates resolved.
    """
    def _resolve_value(v: Any) -> Any:
        if not isinstance(v, str):
            return v
        # Replace all ${step_N.result} occurrences
        def replacer(m: re.Match) -> str:  # type: ignore[type-arg]
            step_id = m.group(1)
            if step_id not in context:
                return m.group(0)  # leave unresolved if step not done
            result = context[step_id].result
            if isinstance(result, str):
                return result
            import json
            return json.dumps(result)

        return re.sub(r"\$\{(step_\w+)\.result\}", replacer, v)

    return {k: _resolve_value(v) for k, v in arguments.items()}


class Executor:
    """Executes a plan step-by-step, calling MCP tools and storing results.

    Args:
        mcp: MCPClient for tool dispatch.
        retry_attempts: How many times to retry a failing tool call.
    """

    def __init__(
        self,
        mcp: MCPClient,
        retry_attempts: int = settings.tool_retry_attempts,
    ) -> None:
        self._mcp = mcp
        self._retry_attempts = retry_attempts

    async def execute_plan(
        self,
        plan: Plan,
        context: dict[str, StepResult] | None = None,
    ) -> dict[str, StepResult]:
        """Execute all steps in a plan, respecting depends_on ordering.

        Args:
            plan: The Plan to execute.
            context: Pre-populated context from a prior run (for resume).

        Returns:
            Dict mapping step_id -> StepResult for all executed steps.
        """
        if context is None:
            context = {}

        # Build a simple topological ordering – resolve depends_on
        ordered = _topological_sort(plan.steps)

        for step in ordered:
            if step.step_id in context:
                log.info("executor.skip_done", step_id=step.step_id)
                continue

            result = await self._execute_step(step, context)
            context[step.step_id] = result

        return context

    async def _execute_step(
        self,
        step: PlanStep,
        context: dict[str, StepResult],
    ) -> StepResult:
        """Execute a single plan step with retry logic.

        On the first failure, waits briefly and retries. On second failure,
        returns a StepResult with error set (so the reflector can replan).
        """
        resolved_args = _resolve_arguments(step.arguments, context)
        log.info("executor.step_start", step_id=step.step_id, tool=step.tool)

        last_error: str | None = None
        for attempt in range(1, self._retry_attempts + 1):
            try:
                t0 = time.monotonic()
                result = await self._mcp.call_tool(step.tool, resolved_args)
                latency_ms = int((time.monotonic() - t0) * 1000)
                log.info(
                    "executor.step_done",
                    step_id=step.step_id,
                    tool=step.tool,
                    latency_ms=latency_ms,
                    attempt=attempt,
                )
                return StepResult(
                    step_id=step.step_id,
                    tool_name=step.tool,
                    result=result,
                )
            except (MCPError, asyncio.TimeoutError, Exception) as exc:
                last_error = str(exc)
                log.warning(
                    "executor.step_error",
                    step_id=step.step_id,
                    tool=step.tool,
                    attempt=attempt,
                    error=last_error,
                )
                if attempt < self._retry_attempts:
                    await asyncio.sleep(2**attempt)  # exponential backoff

        # All retries exhausted – return a failed result so reflector can replan
        log.error(
            "executor.step_failed",
            step_id=step.step_id,
            tool=step.tool,
            error=last_error,
        )
        return StepResult(
            step_id=step.step_id,
            tool_name=step.tool,
            result=None,
            error=last_error,
        )


def _topological_sort(steps: list[PlanStep]) -> list[PlanStep]:
    """Return steps in an order that respects depends_on constraints.

    Uses Kahn's algorithm. Circular dependencies raise ValueError.
    """
    by_id = {s.step_id: s for s in steps}
    in_degree: dict[str, int] = {s.step_id: 0 for s in steps}
    dependents: dict[str, list[str]] = {s.step_id: [] for s in steps}

    for step in steps:
        for dep in step.depends_on:
            if dep not in by_id:
                continue  # unknown dep – skip gracefully
            in_degree[step.step_id] += 1
            dependents[dep].append(step.step_id)

    queue = [sid for sid, deg in in_degree.items() if deg == 0]
    result: list[PlanStep] = []

    while queue:
        sid = queue.pop(0)
        result.append(by_id[sid])
        for dep_sid in dependents[sid]:
            in_degree[dep_sid] -= 1
            if in_degree[dep_sid] == 0:
                queue.append(dep_sid)

    if len(result) != len(steps):
        raise ValueError("Circular dependency detected in plan steps")

    return result
