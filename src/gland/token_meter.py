from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class UsageRecord:
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    user_id: str | None = None
    timestamp: float = field(default_factory=time.time)


class TokenMeter:
    """Tracks per-call token consumption, per-user/model aggregation, and budget enforcement."""

    def __init__(self, budget_limit: int = 0, alert_threshold: float = 0.8) -> None:
        """
        Args:
            budget_limit: Maximum total tokens allowed. 0 = unlimited.
            alert_threshold: Fraction of budget at which to log a warning (0.0–1.0).
        """
        self._budget_limit = budget_limit
        self._alert_threshold = alert_threshold
        self._records: list[UsageRecord] = []

        # Aggregated counters
        self._by_user: dict[str, int] = defaultdict(int)
        self._by_model: dict[str, int] = defaultdict(int)
        self._by_provider: dict[str, int] = defaultdict(int)
        self._total: int = 0

    # ── recording ────────────────────────────────────────────────

    def record(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        user_id: str | None = None,
    ) -> UsageRecord:
        """Record a single API call's token usage. Raises if budget exhausted."""
        total = prompt_tokens + completion_tokens
        self._check_budget(total)

        rec = UsageRecord(
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            user_id=user_id,
        )
        self._records.append(rec)

        self._total += total
        self._by_model[model] += total
        self._by_provider[provider] += total
        if user_id:
            self._by_user[user_id] += total

        self._maybe_alert()
        return rec

    # ── budget ───────────────────────────────────────────────────

    def _check_budget(self, incoming: int) -> None:
        if self._budget_limit <= 0:
            return
        if self._total + incoming > self._budget_limit:
            raise BudgetExceededError(
                f"Budget exhausted: {self._total}/{self._budget_limit} tokens used, "
                f"request needs {incoming} more."
            )

    def _maybe_alert(self) -> None:
        if self._budget_limit <= 0:
            return
        ratio = self._total / self._budget_limit
        if ratio >= self._alert_threshold:
            logger.warning(
                "Token budget alert: %.1f%% used (%d/%d tokens)",
                ratio * 100,
                self._total,
                self._budget_limit,
            )

    def set_budget(self, limit: int) -> None:
        self._budget_limit = limit

    @property
    def remaining_budget(self) -> int | None:
        if self._budget_limit <= 0:
            return None
        return max(0, self._budget_limit - self._total)

    # ── queries ──────────────────────────────────────────────────

    @property
    def total_tokens(self) -> int:
        return self._total

    def usage_by_model(self) -> dict[str, int]:
        return dict(self._by_model)

    def usage_by_user(self) -> dict[str, int]:
        return dict(self._by_user)

    def usage_by_provider(self) -> dict[str, int]:
        return dict(self._by_provider)

    def summary(self) -> dict:
        return {
            "total_tokens": self._total,
            "budget_limit": self._budget_limit or None,
            "remaining_budget": self.remaining_budget,
            "call_count": len(self._records),
            "by_model": self.usage_by_model(),
            "by_user": self.usage_by_user(),
            "by_provider": self.usage_by_provider(),
        }

    def recent_records(self, limit: int = 50) -> list[dict]:
        return [
            {
                "provider": r.provider,
                "model": r.model,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens,
                "user_id": r.user_id,
                "timestamp": r.timestamp,
            }
            for r in self._records[-limit:]
        ]


class BudgetExceededError(Exception):
    """Raised when a request would exceed the configured token budget."""
