"""LLM client – wraps the Anthropic SDK with cost tracking and OTel spans."""

from __future__ import annotations

from typing import Any

import anthropic
import structlog

from agent.budget import Budget
from agent.config import settings
from agent.models import LLMResponse
from agent.telemetry import get_tracer


class LLMClientError(Exception):
    """Raised when the Anthropic API returns a recognised error.

    Attributes:
        retryable: True when a retry is likely to succeed (rate limits,
                   transient connectivity); False for permanent errors
                   such as invalid API keys.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Pricing table (USD per 1M tokens) – update here when Anthropic changes prices
# ---------------------------------------------------------------------------
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    # Fallback for unknown models
    "__default__": {"input": 3.00, "output": 15.00},
}


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING.get(model, _PRICING["__default__"])
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


class LLMClient:
    """Async Anthropic client with per-call OTel spans and budget enforcement.

    Args:
        budget: Optional Budget instance. If provided, each call deducts its
                cost and raises BudgetExceeded if the limit is breached.
        api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var).
    """

    def __init__(self, budget: Budget | None = None, api_key: str | None = None) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key or settings.anthropic_api_key)
        self._budget = budget
        self._tracer = get_tracer("llm_client")

    async def call(
        self,
        messages: list[dict[str, Any]],
        model: str,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Call the Anthropic API and return a normalised LLMResponse.

        Args:
            messages: List of {role, content} dicts.
            model: Claude model identifier.
            system: Optional system prompt.
            tools: Optional list of tool definitions (Anthropic format).
            max_tokens: Maximum tokens in the response.

        Returns:
            LLMResponse with content, token counts, and cost.

        Raises:
            BudgetExceeded: If the accumulated cost exceeds the budget.
        """
        with self._tracer.start_as_current_span("llm.call") as span:
            span.set_attribute("llm.model", model)
            span.set_attribute("llm.max_tokens", max_tokens)
            span.set_attribute("llm.n_messages", len(messages))

            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
            }
            if system:
                kwargs["system"] = system
            if tools:
                kwargs["tools"] = tools

            log.info("llm.call", model=model, n_messages=len(messages))

            try:
                response = await self._client.messages.create(**kwargs)
            except anthropic.RateLimitError as exc:
                raise LLMClientError(
                    "The AI service is temporarily rate-limited. Please wait a minute and try again.",  # noqa: E501
                    retryable=True,
                ) from exc
            except anthropic.AuthenticationError as exc:
                raise LLMClientError(
                    "Invalid API key — please check the server configuration.",
                    retryable=False,
                ) from exc
            except anthropic.APIConnectionError as exc:
                raise LLMClientError(
                    "Could not connect to the AI service. Please try again.",
                    retryable=True,
                ) from exc
            except anthropic.InternalServerError as exc:
                raise LLMClientError(
                    "The AI service is experiencing issues. Please try again in a moment.",
                    retryable=True,
                ) from exc
            except anthropic.APIStatusError as exc:
                raise LLMClientError(
                    f"AI service returned an error (HTTP {exc.status_code}).",
                    retryable=exc.status_code >= 500,
                ) from exc

            input_tokens: int = response.usage.input_tokens
            output_tokens: int = response.usage.output_tokens
            cost_usd = _compute_cost(model, input_tokens, output_tokens)

            span.set_attribute("llm.input_tokens", input_tokens)
            span.set_attribute("llm.output_tokens", output_tokens)
            span.set_attribute("llm.cost_usd", cost_usd)
            span.set_attribute("llm.stop_reason", response.stop_reason or "")

            log.info(
                "llm.done",
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                stop_reason=response.stop_reason,
            )

            # Charge the budget *after* a successful API call so the cost is
            # always recorded even if we're about to raise.
            if self._budget is not None:
                self._budget.charge(cost_usd)

            # Extract text content
            content_parts: list[str] = []
            raw_tool_calls: list[dict[str, Any]] = []
            for block in response.content:
                if block.type == "text":
                    content_parts.append(block.text)
                elif block.type == "tool_use":
                    raw_tool_calls.append(
                        {
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )

            return LLMResponse(
                content="\n".join(content_parts),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                model=model,
                raw_tool_calls=raw_tool_calls,
                stop_reason=response.stop_reason or "",
            )
