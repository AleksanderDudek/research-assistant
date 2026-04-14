"""Unit tests for MCPClient argument resolution and error handling."""

from __future__ import annotations

from agent.executor import _resolve_arguments
from agent.models import StepResult


def make_result(step_id: str, result: object) -> StepResult:
    return StepResult(step_id=step_id, tool_name="web_search", result=result)


def test_resolve_no_templates() -> None:
    args = {"query": "Apple market cap", "max_results": 5}
    context: dict[str, StepResult] = {}
    resolved = _resolve_arguments(args, context)
    assert resolved == args


def test_resolve_string_template() -> None:
    context = {"step_1": make_result("step_1", "https://example.com")}
    args = {"url": "${step_1.result}"}
    resolved = _resolve_arguments(args, context)
    assert resolved["url"] == "https://example.com"


def test_resolve_dict_result_serialised_as_json() -> None:
    context = {"step_1": make_result("step_1", {"key": "value"})}
    args = {"code": "data = ${step_1.result}"}
    resolved = _resolve_arguments(args, context)
    assert '"key"' in resolved["code"]


def test_resolve_unknown_ref_left_as_is() -> None:
    context: dict[str, StepResult] = {}
    args = {"url": "${step_99.result}"}
    resolved = _resolve_arguments(args, context)
    assert resolved["url"] == "${step_99.result}"


def test_resolve_non_string_values_passthrough() -> None:
    context = {"step_1": make_result("step_1", [1, 2, 3])}
    args = {"max_results": 5, "url": "${step_1.result}"}
    resolved = _resolve_arguments(args, context)
    assert resolved["max_results"] == 5
