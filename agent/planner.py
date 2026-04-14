"""Planner – generates a structured JSON plan via Claude."""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

from agent.config import settings
from agent.llm_client import LLMClient
from agent.models import Plan, PlanStep

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_planner.txt"

_TOOL_DESCRIPTIONS = """
- web_search(query, max_results=5): Search the web. Returns [{title, url, snippet}].
- fetch_url(url): Download and extract text from a URL. Returns {url, text, char_count}.
- read_pdf(source): Extract text from a PDF (path or URL). Returns {text, page_count}.
- execute_python(code): Run Python in a Docker sandbox. Returns {stdout, stderr, exit_code}.
- search_knowledge_base(query, top_k=5): Search local KB. Returns [{source, text, score}].
""".strip()


def _load_system_prompt() -> str:
    template = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{tool_descriptions}", _TOOL_DESCRIPTIONS)


def _extract_json(text: str) -> str:
    """Strip markdown fences and leading/trailing whitespace."""
    text = text.strip()
    # Remove ```json ... ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    return text.strip()


def _parse_plan(raw: str) -> Plan:
    data = json.loads(_extract_json(raw))
    steps = [PlanStep(**s) for s in data.get("steps", [])]
    return Plan(
        question=data["question"],
        steps=steps,
        rationale=data.get("rationale", ""),
    )


class Planner:
    """Generates a Plan from a research question using the LLM.

    Args:
        llm: LLMClient instance (with budget attached).
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._system = _load_system_prompt()

    async def plan(self, question: str) -> Plan:
        """Call the LLM to produce a Plan for the given question.

        Retries once on JSON parse failure, appending the error to the context
        so the model can self-correct.

        Args:
            question: The research question.

        Returns:
            A validated Plan object.
        """
        log.info("planner.plan", question=question[:100])
        messages = [{"role": "user", "content": question}]

        response = await self._llm.call(
            messages=messages,
            model=settings.planner_model,
            system=self._system,
            max_tokens=2048,
        )

        parse_error: str | None = None
        try:
            plan = _parse_plan(response.content)
            log.info("planner.done", n_steps=len(plan.steps))
            return plan
        except (KeyError, ValueError) as exc:
            parse_error = str(exc)
            log.warning("planner.parse_error", error=parse_error, retry=True)

        # Self-correction retry
        messages.append({"role": "assistant", "content": response.content})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"Your response was not valid JSON. Error: {parse_error}. "
                    "Please respond with ONLY a valid JSON plan object, no other text."
                ),
            }
        )
        response2 = await self._llm.call(
            messages=messages,
            model=settings.planner_model,
            system=self._system,
            max_tokens=2048,
        )
        plan = _parse_plan(response2.content)
        log.info("planner.retry_success", n_steps=len(plan.steps))
        return plan
