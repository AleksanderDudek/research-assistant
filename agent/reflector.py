"""Reflector – reviews step results and decides if we have enough to answer."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog

from agent.config import settings
from agent.llm_client import LLMClient
from agent.models import Plan, PlanStep, ReflectionOutput, StepResult

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_reflector.txt"


def _load_system_prompt() -> str:
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()
    # Locate the outermost JSON object in case there is surrounding prose
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _remove_trailing_commas(text: str) -> str:
    """Remove trailing commas before } or ] — a frequent LLM JSON mistake."""
    return re.sub(r",\s*([}\]])", r"\1", text)


def _sanitize_json_strings(text: str) -> str:
    """Escape bare control characters inside JSON string tokens.

    LLMs sometimes emit literal newlines/tabs inside JSON strings, which
    makes the output technically invalid JSON.  This function replaces them
    with their proper JSON escape sequences before parsing.
    """

    def _fix(m: re.Match[str]) -> str:
        s = m.group(0)
        return s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

    return re.sub(r'"(?:[^"\\]|\\.)*"', _fix, text, flags=re.DOTALL)


def _parse_reflection(raw: str) -> ReflectionOutput:
    extracted = _extract_json(raw)
    sanitized = _sanitize_json_strings(extracted)
    attempts = [
        extracted,
        sanitized,
        _remove_trailing_commas(sanitized),
    ]
    data: dict[str, Any] | None = None
    for attempt in attempts:
        try:
            data = json.loads(attempt)
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        raise ValueError(f"Unparseable JSON from reflector: {extracted[:300]}")
    additional = [PlanStep(**s) for s in data.get("additional_steps", [])]
    return ReflectionOutput(
        sufficient=bool(data["sufficient"]),
        additional_steps=additional,
        final_answer=data.get("final_answer", ""),
        reasoning=data.get("reasoning", ""),
    )


def _summarise_results(context: dict[str, StepResult]) -> str:
    """Render step results as a compact text block for the reflector prompt."""
    parts: list[str] = []
    for step_id, sr in context.items():
        if sr.error:
            parts.append(f"[{step_id}] FAILED: {sr.error}")
        else:
            result_str = json.dumps(sr.result, ensure_ascii=False)[:3000]
            parts.append(f"[{step_id}] tool={sr.tool_name}\n{result_str}")
    return "\n\n".join(parts)


class Reflector:
    """Reviews execution results and either produces the final answer or
    requests additional investigation steps.

    Args:
        llm: LLMClient instance (with budget attached).
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._system = _load_system_prompt()

    async def reflect(
        self,
        question: str,
        plan: Plan,
        context: dict[str, StepResult],
    ) -> ReflectionOutput:
        """Evaluate whether the gathered evidence is sufficient to answer.

        Args:
            question: The original research question.
            plan: The plan that was executed.
            context: Dict of step_id -> StepResult from the executor.

        Returns:
            ReflectionOutput with sufficient flag, optional extra steps, and
            final answer when sufficient=True.
        """
        log.info("reflector.reflect", question=question[:80], n_steps=len(context))

        summary = _summarise_results(context)
        plan_json = json.dumps([s.model_dump() for s in plan.steps], indent=2, ensure_ascii=False)

        user_content = (
            f"Research question: {question}\n\n"
            f"Plan executed:\n{plan_json}\n\n"
            f"Step results:\n{summary}"
        )

        messages = [{"role": "user", "content": user_content}]
        response = await self._llm.call(
            messages=messages,
            model=settings.reflector_model,
            system=self._system,
            max_tokens=3000,
        )

        try:
            reflection = _parse_reflection(response.content)
            log.info(
                "reflector.done",
                sufficient=reflection.sufficient,
                n_additional=len(reflection.additional_steps),
            )
            return reflection
        except (KeyError, ValueError) as exc:
            log.warning("reflector.parse_error", error=str(exc))
            # Assume sufficient and return raw content as final answer
            return ReflectionOutput(
                sufficient=True,
                final_answer=response.content,
                reasoning=f"Could not parse structured reflection: {exc}",
            )
