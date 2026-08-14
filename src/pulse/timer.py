"""Pulse Timer — High-precision timer with sub-second polling and signal dispatch."""

import time
import uuid
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class SignalType(str, Enum):
    TICK = "tick"           # Regular heartbeat tick
    INTERVAL = "interval"   # Custom interval signal
    CRON = "cron"           # Cron-style scheduled signal
    ONCE = "once"           # One-shot delayed signal


class SignalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class PulseSignal:
    signal_id: str
    signal_type: SignalType
    name: str
    interval_ms: float  # milliseconds
    status: SignalStatus = SignalStatus.ACTIVE
    callback_url: str = ""  # webhook URL to notify
    payload: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_fired_at: float | None = None
    next_fire_at: float | None = None
    fire_count: int = 0
    max_fires: int = 0  # 0 = unlimited
    precision_ms: float = 1.0  # actual achieved precision
    drift_correction: float = 0.0  # accumulated drift in ms


@dataclass
class TickRecord:
    tick_id: str
    signal_id: str
    timestamp: float
    expected_at: float
    drift_ms: float  # actual - expected
    latency_ms: float  # processing latency


class PulseEngine:
    """High-precision timer engine with drift correction."""

    def __init__(self):
        self._signals: dict[str, PulseSignal] = {}
        self._tick_history: list[TickRecord] = []
        self._start_time = time.time()
        self._total_ticks = 0
        self._running = False

    def create_signal(self, name: str, signal_type: str, interval_ms: float,
                      callback_url: str = "", payload: dict = None,
                      max_fires: int = 0) -> PulseSignal:
        """Create a new pulse signal."""
        sig = PulseSignal(
            signal_id=f"pulse_{uuid.uuid4().hex[:12]}",
            signal_type=SignalType(signal_type),
            name=name,
            interval_ms=interval_ms,
            callback_url=callback_url,
            payload=payload or {},
            max_fires=max_fires,
            next_fire_at=time.time() + interval_ms / 1000.0,
        )
        self._signals[sig.signal_id] = sig
        return sig

    def tick(self, signal_id: str) -> TickRecord | None:
        """Process a tick for a signal. Returns tick record."""
        sig = self._signals.get(signal_id)
        if not sig or sig.status != SignalStatus.ACTIVE:
            return None

        now = time.time()
        expected_at = sig.next_fire_at or now
        drift_ms = (now - expected_at) * 1000

        record = TickRecord(
            tick_id=f"tick_{uuid.uuid4().hex[:12]}",
            signal_id=signal_id,
            timestamp=now,
            expected_at=expected_at,
            drift_ms=drift_ms,
            latency_ms=0,  # measured externally
        )
        self._tick_history.append(record)
        if len(self._tick_history) > 10000:
            self._tick_history = self._tick_history[-5000:]

        # Update signal state
        sig.last_fired_at = now
        sig.fire_count += 1
        sig.drift_correction = drift_ms * 0.1  # EMA correction

        # Calculate next fire with drift correction
        corrected_interval = sig.interval_ms - sig.drift_correction
        sig.next_fire_at = now + max(1.0, corrected_interval) / 1000.0

        # Auto-complete one-shot signals
        if sig.signal_type == SignalType.ONCE:
            sig.status = SignalStatus.COMPLETED

        # Check max fires
        if sig.max_fires > 0 and sig.fire_count >= sig.max_fires:
            sig.status = SignalStatus.COMPLETED

        self._total_ticks += 1
        return record

    def get_signal(self, signal_id: str) -> PulseSignal | None:
        return self._signals.get(signal_id)

    def list_signals(self, status: str = None) -> list[PulseSignal]:
        signals = list(self._signals.values())
        if status:
            signals = [s for s in signals if s.status.value == status]
        return sorted(signals, key=lambda s: s.created_at, reverse=True)

    def pause_signal(self, signal_id: str) -> bool:
        sig = self._signals.get(signal_id)
        if sig and sig.status == SignalStatus.ACTIVE:
            sig.status = SignalStatus.PAUSED
            return True
        return False

    def resume_signal(self, signal_id: str) -> bool:
        sig = self._signals.get(signal_id)
        if sig and sig.status == SignalStatus.PAUSED:
            sig.status = SignalStatus.ACTIVE
            sig.next_fire_at = time.time() + sig.interval_ms / 1000.0
            return True
        return False

    def cancel_signal(self, signal_id: str) -> bool:
        sig = self._signals.get(signal_id)
        if sig and sig.status in (SignalStatus.ACTIVE, SignalStatus.PAUSED):
            sig.status = SignalStatus.CANCELLED
            return True
        return False

    def delete_signal(self, signal_id: str) -> bool:
        if signal_id in self._signals:
            del self._signals[signal_id]
            return True
        return False

    def get_tick_history(self, signal_id: str = None, limit: int = 100) -> list[dict]:
        history = self._tick_history
        if signal_id:
            history = [t for t in history if t.signal_id == signal_id]
        history = history[-limit:]
        return [
            {
                "tick_id": t.tick_id,
                "signal_id": t.signal_id,
                "timestamp": t.timestamp,
                "expected_at": t.expected_at,
                "drift_ms": round(t.drift_ms, 3),
                "latency_ms": round(t.latency_ms, 3),
            }
            for t in history
        ]

    def get_stats(self) -> dict:
        uptime = time.time() - self._start_time
        signals = list(self._signals.values())
        by_type = {}
        for s in signals:
            by_type[s.signal_type.value] = by_type.get(s.signal_type.value, 0) + 1
        by_status = {}
        for s in signals:
            by_status[s.status.value] = by_status.get(s.status.value, 0) + 1

        # Precision stats from recent ticks
        recent = self._tick_history[-100:] if self._tick_history else []
        avg_drift = sum(abs(t.drift_ms) for t in recent) / len(recent) if recent else 0
        max_drift = max((abs(t.drift_ms) for t in recent), default=0)

        return {
            "uptime_seconds": round(uptime, 1),
            "total_ticks": self._total_ticks,
            "total_signals": len(signals),
            "by_type": by_type,
            "by_status": by_status,
            "precision": {
                "avg_drift_ms": round(avg_drift, 3),
                "max_drift_ms": round(max_drift, 3),
                "sample_size": len(recent),
            },
        }
