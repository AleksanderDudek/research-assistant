"""Unit tests for the Budget class."""

from __future__ import annotations

import threading

import pytest

from agent.budget import Budget, BudgetExceeded


def test_budget_initial_state() -> None:
    b = Budget(limit_usd=5.00)
    assert b.limit_usd == 5.00
    assert b.spent() == pytest.approx(0.0)
    assert b.remaining() == pytest.approx(5.00)


def test_budget_charge_deducts() -> None:
    b = Budget(limit_usd=1.00)
    b.charge(0.25)
    assert b.spent() == pytest.approx(0.25)
    assert b.remaining() == pytest.approx(0.75)


def test_budget_charge_exact_limit_ok() -> None:
    b = Budget(limit_usd=1.00)
    b.charge(1.00)
    assert b.spent() == pytest.approx(1.00)
    assert b.remaining() == pytest.approx(0.0)


def test_budget_exceeded_raises() -> None:
    b = Budget(limit_usd=0.10)
    with pytest.raises(BudgetExceeded) as exc_info:
        b.charge(0.11)
    assert exc_info.value.limit_usd == pytest.approx(0.10)
    assert exc_info.value.spent_usd > 0.10


def test_budget_exceeded_message_contains_amounts() -> None:
    b = Budget(limit_usd=0.50)
    with pytest.raises(BudgetExceeded, match="0.5"):
        b.charge(1.00)


def test_budget_negative_limit_raises() -> None:
    with pytest.raises(ValueError):
        Budget(limit_usd=-1.0)


def test_budget_negative_charge_raises() -> None:
    b = Budget(limit_usd=5.0)
    with pytest.raises(ValueError):
        b.charge(-0.01)


def test_budget_thread_safety() -> None:
    """Multiple threads charging simultaneously should not under-charge."""
    b = Budget(limit_usd=1000.00)
    errors: list[Exception] = []

    def charge_many() -> None:
        for _ in range(100):
            try:
                b.charge(0.01)
            except Exception as e:
                errors.append(e)

    threads = [threading.Thread(target=charge_many) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert b.spent() == pytest.approx(10.00, abs=0.001)


def test_budget_remaining_never_negative() -> None:
    b = Budget(limit_usd=0.10)
    try:
        b.charge(1.00)
    except BudgetExceeded:
        pass
    assert b.remaining() == 0.0
