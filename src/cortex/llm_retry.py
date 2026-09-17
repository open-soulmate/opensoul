"""LLM call retry policy — ported from kilocode retry.ts (179 lines).

P0 gap (SUMMARY.md: "cortex | LLM重试策略（Retry-After三格式+5xx重试+离线三态）
| kilocode retry.ts | P0"). Before this module, a single transient 429/5xx from
one provider immediately burned a failure mark and started failover; three
such blips parked a healthy provider in a 60s cooldown.

Core principles carried over from kilocode's comments:

1. **Honor Retry-After headers in three formats** — ``retry-after-ms``
   (milliseconds, vendor extension), ``retry-after`` as delta-seconds, or
   ``retry-after`` as an HTTP-date. Only fall back to exponential backoff
   when the server gave no hint ("有头听头" — listening to the header is
   far kinder to rate-limited APIs than blind backoff).
2. **Retry 5xx unconditionally** — kilocode retries server errors even when
   the SDK didn't mark them retryable ("transient server failures").
3. **Never retry what retrying cannot fix** — 400/401/403/404/422 fail fast
   so the router can fail over to the next provider immediately.
4. **Hard attempt limit** — kilocode added the `limit` parameter on top of
   upstream opencode precisely to stop infinite retry loops.

Deviation from kilocode: the header-derived delay is capped at
RETRY_AFTER_CAP_SECONDS (120s) rather than i32-max, because a multi-hour
Retry-After is better surfaced to the caller as a failover than as a frozen
request. Cap is configurable per deployment.
"""

from __future__ import annotations

import email.utils
import logging
import random
import time

logger = logging.getLogger(__name__)

# Attempts per provider before giving up and letting the router fail over.
DEFAULT_MAX_ATTEMPTS = 3
# Exponential backoff base/cap when the server sent no Retry-After hint
# (kilocode: 2s x 2^n, capped at 30s).
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 30.0
# Upper bound for server-provided Retry-After values.
RETRY_AFTER_CAP_SECONDS = 120.0

# Always-retry status codes: request timeout, too-early, rate limited, and
# the common transient server errors.
_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})


def is_retryable_status(status: int) -> bool:
    """5xx unconditional (kilocode), plus the explicit transient 4xx set."""
    if status >= 500:
        return True
    return status in _RETRYABLE_STATUSES


def parse_retry_after(headers, now: float | None = None) -> float | None:
    """Parse Retry-After hints from response headers.

    Supports the three formats kilocode handles:
      1. ``retry-after-ms`` — integer milliseconds
      2. ``retry-after``    — delta-seconds (integer or float)
      3. ``retry-after``    — HTTP-date (``email.utils`` parsed, minus now)

    Returns seconds (capped at RETRY_AFTER_CAP_SECONDS), or None when no
    usable hint is present.
    """
    if not headers:
        return None
    now = time.time() if now is None else now

    raw = headers.get("retry-after-ms")
    if raw is not None:
        try:
            return min(max(int(raw), 0) / 1000.0, RETRY_AFTER_CAP_SECONDS)
        except (TypeError, ValueError):
            pass

    raw = headers.get("retry-after")
    if raw is None:
        return None
    raw = raw.strip()
    try:
        return min(max(float(raw), 0.0), RETRY_AFTER_CAP_SECONDS)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        return min(max(dt.timestamp() - now, 0.0), RETRY_AFTER_CAP_SECONDS)
    except (TypeError, ValueError):
        return None


def backoff_delay(
    attempt: int, headers=None, jitter: bool = True
) -> float:
    """Seconds to wait before retry attempt *attempt* (0-based).

    Server hint wins; otherwise exponential backoff 2s x 2^attempt capped
    at 30s. Half-jitter (50–100% of the delay) avoids thundering-herd
    retries when many requests hit the same rate limit — the same
    motivation as langgraph's RetryPolicy jitter.
    """
    hint = parse_retry_after(headers)
    if hint is not None:
        return hint
    delay = min(BACKOFF_BASE_SECONDS * (2**attempt), BACKOFF_CAP_SECONDS)
    if jitter:
        delay *= 0.5 + random.random() * 0.5
    return delay


def retry_delay_for(exc: BaseException, attempt: int) -> float | None:
    """Classify an exception from an LLM HTTP call.

    Returns the delay in seconds before the next attempt, or None when the
    error is not retryable (caller should fail over / surface immediately).
    """
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if is_retryable_status(status):
            return backoff_delay(attempt, exc.response.headers)
        return None
    # Transport-level failures (connect/read timeouts, resets, DNS) are
    # transient by nature — same errno family kilocode's SessionNetwork
    # treats as "wait for network" rather than "task failed".
    if isinstance(exc, httpx.TransportError):
        return backoff_delay(attempt, None)
    return None
