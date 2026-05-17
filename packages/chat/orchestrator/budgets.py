"""Per-request token + wall-clock budget.

Single source of truth for "have we used too much" checks. Stages call
.add(usage) after every LLM response, .check() before every new call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


class BudgetExceededError(RuntimeError):
    """Raised when a request blows past its token budget or wall-clock deadline."""


@dataclass
class Budget:
    """Token + deadline tracker scoped to a single chat turn."""

    token_budget: int
    deadline: float  # monotonic timestamp (time.monotonic() value)
    tokens_used: int = 0

    @classmethod
    def from_now(cls, token_budget: int, wall_clock_s: float) -> Budget:
        return cls(
            token_budget=token_budget,
            deadline=time.monotonic() + wall_clock_s,
        )

    def add(self, usage: dict[str, int] | None) -> None:
        """Accumulate token usage from an LLM response. No-op if usage is None."""
        if usage:
            self.tokens_used += int(usage.get("total_tokens", 0) or 0)

    def remaining_s(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def check(self) -> None:
        if self.tokens_used > self.token_budget:
            raise BudgetExceededError(
                f"token budget {self.token_budget} exceeded (used {self.tokens_used})"
            )
        if time.monotonic() > self.deadline:
            raise BudgetExceededError("wall-clock deadline exceeded")
