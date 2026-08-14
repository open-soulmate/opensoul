"""Rate limiter — token bucket + sliding window."""

from __future__ import annotations

import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class RateLimitConfig:
    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    burst_size: int = 20


class SlidingWindowCounter:
    """Sliding window rate limiter using circular buffer."""

    def __init__(self, window_seconds: int = 60, max_requests: int = 60):
        self.window = window_seconds
        self.max_requests = max_requests
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.time()
        cutoff = now - self.window
        with self._lock:
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            if len(self._timestamps) < self.max_requests:
                self._timestamps.append(now)
                return True
            return False

    @property
    def current_count(self) -> int:
        now = time.time()
        cutoff = now - self.window
        return sum(1 for t in self._timestamps if t > cutoff)


class RateLimiter:
    """Multi-tier rate limiter keyed by (client_id, endpoint)."""

    def __init__(self, config: RateLimitConfig | None = None):
        self.config = config or RateLimitConfig()
        self._minute_windows: dict[str, SlidingWindowCounter] = {}
        self._hour_windows: dict[str, SlidingWindowCounter] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> dict:
        """Check if request is allowed. Returns {allowed, retry_after, ...}."""
        with self._lock:
            if key not in self._minute_windows:
                self._minute_windows[key] = SlidingWindowCounter(60, self.config.requests_per_minute)
                self._hour_windows[key] = SlidingWindowCounter(3600, self.config.requests_per_hour)

        minute_ok = self._minute_windows[key].allow()
        hour_ok = self._hour_windows[key].allow()

        allowed = minute_ok and hour_ok
        retry_after = 0

        if not minute_ok:
            retry_after = 60
        elif not hour_ok:
            retry_after = 3600

        return {
            "allowed": allowed,
            "retry_after": retry_after,
            "minute_count": self._minute_windows[key].current_count,
            "hour_count": self._hour_windows[key].current_count,
            "minute_limit": self.config.requests_per_minute,
            "hour_limit": self.config.requests_per_hour,
            "burst_count": min(self._minute_windows[key].current_count, self.config.burst_size),
            "burst_limit": self.config.burst_size,
        }

    def reset(self, key: str | None = None):
        """Reset counters for a key or all keys."""
        with self._lock:
            if key:
                self._minute_windows.pop(key, None)
                self._hour_windows.pop(key, None)
            else:
                self._minute_windows.clear()
                self._hour_windows.clear()

    def stats(self) -> dict:
        with self._lock:
            return {
                "tracked_keys": len(self._minute_windows),
                "config": {
                    "requests_per_minute": self.config.requests_per_minute,
                    "requests_per_hour": self.config.requests_per_hour,
                    "burst_size": self.config.burst_size,
                },
            }
