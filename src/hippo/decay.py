"""Decay strategies for memory lifecycle management.

Implements multiple forgetting curves inspired by Ebbinghaus:
- Exponential decay: rapid initial forgetting, then plateau
- Linear decay: steady decline
- Importance-gated: high-importance memories resist decay
- Access-reinforced: frequently accessed memories get refreshed
"""

import math
import time
from dataclasses import dataclass
from enum import StrEnum


class DecayStrategy(StrEnum):
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    IMPORTANCE_GATED = "importance_gated"
    ACCESS_REINFORCED = "access_reinforced"


@dataclass
class DecayResult:
    """Result of a decay calculation."""

    retention: float  # 0.0 (forgotten) to 1.0 (fresh)
    should_archive: bool  # True if memory should be moved to long-term
    should_forget: bool  # True if memory should be deleted
    strategy: str


class DecayEngine:
    """Calculates memory retention based on different decay strategies."""

    def __init__(
        self,
        strategy: DecayStrategy = DecayStrategy.EXPONENTIAL,
        half_life_hours: float = 24.0,
        archive_threshold: float = 0.3,
        forget_threshold: float = 0.05,
    ):
        self.strategy = strategy
        self.half_life_hours = half_life_hours
        self.archive_threshold = archive_threshold
        self.forget_threshold = forget_threshold

    def calculate(
        self,
        created_at: float,
        last_accessed_at: float,
        access_count: int = 0,
        importance: float = 0.5,
        now: float | None = None,
    ) -> DecayResult:
        """Calculate current retention for a memory."""
        now = now or time.time()
        age_hours = (now - created_at) / 3600.0
        idle_hours = (now - last_accessed_at) / 3600.0

        if self.strategy == DecayStrategy.EXPONENTIAL:
            retention = self._exponential(age_hours)
        elif self.strategy == DecayStrategy.LINEAR:
            retention = self._linear(age_hours)
        elif self.strategy == DecayStrategy.IMPORTANCE_GATED:
            retention = self._importance_gated(age_hours, importance)
        elif self.strategy == DecayStrategy.ACCESS_REINFORCED:
            retention = self._access_reinforced(age_hours, idle_hours, access_count)
        else:
            retention = self._exponential(age_hours)

        # Clamp
        retention = max(0.0, min(1.0, retention))

        return DecayResult(
            retention=round(retention, 4),
            should_archive=retention <= self.archive_threshold
            and retention > self.forget_threshold,
            should_forget=retention <= self.forget_threshold,
            strategy=self.strategy.value,
        )

    def _exponential(self, age_hours: float) -> float:
        """Ebbinghaus-inspired exponential decay: R = e^(-t/S) where S = half_life / ln(2)."""
        stability = self.half_life_hours / math.log(2)
        return math.exp(-age_hours / stability)

    def _linear(self, age_hours: float) -> float:
        """Linear decay over 2x half-life period."""
        return max(0.0, 1.0 - age_hours / (2 * self.half_life_hours))

    def _importance_gated(self, age_hours: float, importance: float) -> float:
        """High-importance memories decay slower. importance in [0,1]."""
        # Effective half-life scales with importance: 0→0.5x, 0.5→1x, 1→4x
        effective_half_life = self.half_life_hours * (0.5 + 3.5 * importance)
        stability = effective_half_life / math.log(2)
        return math.exp(-age_hours / stability)

    def _access_reinforced(self, age_hours: float, idle_hours: float, access_count: int) -> float:
        """Each access reinforces memory. Uses combined age + idle decay with access bonus."""
        # Base decay on idle time (time since last access)
        base_retention = math.exp(-idle_hours / (self.half_life_hours / math.log(2)))
        # Access reinforcement: log scale bonus
        reinforcement = min(1.0, 1.0 + 0.1 * math.log1p(access_count))
        # Age penalty (mild)
        age_penalty = math.exp(-age_hours / (10 * self.half_life_hours / math.log(2)))
        return base_retention * reinforcement * age_penalty
