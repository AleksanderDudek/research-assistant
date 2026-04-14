"""Budget tracking - raises BudgetExceeded when the USD limit is crossed."""

from __future__ import annotations

import threading

import structlog

log = structlog.get_logger(__name__)


class BudgetExceeded(Exception):
    """Raised when accumulated LLM cost exceeds the configured budget."""

    def __init__(self, limit_usd: float, spent_usd: float) -> None:
        self.limit_usd = limit_usd
        self.spent_usd = spent_usd
        super().__init__(
            f"Budget exceeded: spent ${spent_usd:.4f} of ${limit_usd:.4f} limit"
        )


class Budget:
    """Thread-safe USD budget with atomic charge().

    Usage::

        budget = Budget(limit_usd=2.00)
        budget.charge(0.05)   # deduct $0.05
        budget.remaining()    # → 1.95
    """

    def __init__(self, limit_usd: float) -> None:
        if limit_usd <= 0:
            raise ValueError("Budget limit must be positive")
        self._limit = limit_usd
        self._spent: float = 0.0
        self._lock = threading.Lock()

    @property
    def limit_usd(self) -> float:
        return self._limit

    def spent(self) -> float:
        with self._lock:
            return self._spent

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self._limit - self._spent)

    def charge(self, cost_usd: float) -> None:
        """Deduct cost_usd from the budget. Raises BudgetExceeded if over."""
        if cost_usd < 0:
            raise ValueError("Cost must be non-negative")
        with self._lock:
            self._spent += cost_usd
            if self._spent > self._limit:
                log.warning(
                    "budget.exceeded",
                    limit_usd=self._limit,
                    spent_usd=self._spent,
                )
                raise BudgetExceeded(self._limit, self._spent)
        log.debug(
            "budget.charged",
            cost_usd=cost_usd,
            spent_usd=self._spent,
            remaining=self.remaining(),
        )
