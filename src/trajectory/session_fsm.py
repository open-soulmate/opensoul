"""Session finite state machine for OpenTrajectory.

Explicit state transitions for conversation sessions.
Borrowed from: LangGraph StateGraph, XState finite state machine

States: idle → thinking → executing → waiting_input → responding → completed
Events: user_message, tool_call, tool_result, error, interrupt, timeout
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

logger = logging.getLogger("opensoul.trajectory.fsm")


class SessionState(StrEnum):
    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING = "executing"
    WAITING_INPUT = "waiting_input"
    RESPONDING = "responding"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class SessionEvent(StrEnum):
    USER_MESSAGE = "user_message"
    START_THINKING = "start_thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    RESPONSE_READY = "response_ready"
    RESPONSE_SENT = "response_sent"
    ERROR = "error"
    INTERRUPT = "interrupt"
    TIMEOUT = "timeout"
    RESUME = "resume"
    COMPLETE = "complete"
    TERMINATE = "terminate"


# Transition table: (current_state, event) → next_state
TRANSITIONS: dict[tuple[SessionState, SessionEvent], SessionState] = {
    (SessionState.IDLE, SessionEvent.USER_MESSAGE): SessionState.THINKING,
    (SessionState.THINKING, SessionEvent.TOOL_CALL): SessionState.EXECUTING,
    (SessionState.THINKING, SessionEvent.RESPONSE_READY): SessionState.RESPONDING,
    (SessionState.THINKING, SessionEvent.ERROR): SessionState.ERROR,
    (SessionState.THINKING, SessionEvent.INTERRUPT): SessionState.PAUSED,
    (SessionState.THINKING, SessionEvent.TIMEOUT): SessionState.ERROR,
    (SessionState.EXECUTING, SessionEvent.TOOL_RESULT): SessionState.THINKING,
    (SessionState.EXECUTING, SessionEvent.ERROR): SessionState.ERROR,
    (SessionState.EXECUTING, SessionEvent.INTERRUPT): SessionState.PAUSED,
    (SessionState.EXECUTING, SessionEvent.TIMEOUT): SessionState.ERROR,
    (SessionState.RESPONDING, SessionEvent.RESPONSE_SENT): SessionState.IDLE,
    (SessionState.RESPONDING, SessionEvent.COMPLETE): SessionState.COMPLETED,
    (SessionState.RESPONDING, SessionEvent.ERROR): SessionState.ERROR,
    (SessionState.PAUSED, SessionEvent.RESUME): SessionState.THINKING,
    (SessionState.PAUSED, SessionEvent.USER_MESSAGE): SessionState.THINKING,
    (SessionState.PAUSED, SessionEvent.TERMINATE): SessionState.TERMINATED,
    (SessionState.ERROR, SessionEvent.USER_MESSAGE): SessionState.THINKING,
    (SessionState.ERROR, SessionEvent.RESUME): SessionState.THINKING,
    (SessionState.ERROR, SessionEvent.TERMINATE): SessionState.TERMINATED,
    (SessionState.COMPLETED, SessionEvent.USER_MESSAGE): SessionState.THINKING,
    (SessionState.COMPLETED, SessionEvent.TERMINATE): SessionState.TERMINATED,
}


@dataclass
class StateTransition:
    from_state: SessionState
    event: SessionEvent
    to_state: SessionState
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class SessionFSM:
    """Finite state machine for a single session."""
    session_id: str
    state: SessionState = SessionState.IDLE
    history: list[StateTransition] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    state_entered_at: float = field(default_factory=time.time)

    @property
    def time_in_state(self) -> float:
        return time.time() - self.state_entered_at

    @property
    def total_transitions(self) -> int:
        return len(self.history)


class SessionStateMachine:
    """Manages session state machines with transition validation."""

    def __init__(self):
        self._machines: dict[str, SessionFSM] = {}
        self._stats = {"total_transitions": 0, "invalid_transitions": 0}

    def create_session(self, session_id: str = "") -> SessionFSM:
        """Create a new session state machine."""
        sid = session_id or f"fsm_{uuid.uuid4().hex[:12]}"
        fsm = SessionFSM(session_id=sid)
        self._machines[sid] = fsm
        return fsm

    def transition(
        self,
        session_id: str,
        event: SessionEvent,
        metadata: Optional[dict] = None,
    ) -> tuple[bool, SessionState]:
        """Attempt a state transition. Returns (success, current_state)."""
        fsm = self._machines.get(session_id)
        if not fsm:
            logger.warning(f"Unknown session: {session_id}")
            return False, SessionState.IDLE

        key = (fsm.state, event)
        next_state = TRANSITIONS.get(key)

        if next_state is None:
            self._stats["invalid_transitions"] += 1
            logger.warning(
                f"Invalid transition: {fsm.state.value} + {event.value} "
                f"(session: {session_id})"
            )
            return False, fsm.state

        # Record transition
        transition = StateTransition(
            from_state=fsm.state,
            event=event,
            to_state=next_state,
            metadata=metadata or {},
        )
        fsm.history.append(transition)
        fsm.state = next_state
        fsm.state_entered_at = time.time()
        self._stats["total_transitions"] += 1

        logger.debug(
            f"Session {session_id}: {transition.from_state.value} → "
            f"{next_state.value} (via {event.value})"
        )
        return True, next_state

    def get_state(self, session_id: str) -> Optional[SessionState]:
        fsm = self._machines.get(session_id)
        return fsm.state if fsm else None

    def get_history(self, session_id: str, limit: int = 20) -> list[dict]:
        fsm = self._machines.get(session_id)
        if not fsm:
            return []
        return [
            {
                "from": t.from_state.value,
                "event": t.event.value,
                "to": t.to_state.value,
                "timestamp": t.timestamp,
            }
            for t in fsm.history[-limit:]
        ]

    def can_transition(self, session_id: str, event: SessionEvent) -> bool:
        """Check if a transition is valid without executing it."""
        fsm = self._machines.get(session_id)
        if not fsm:
            return False
        return (fsm.state, event) in TRANSITIONS

    def get_stats(self) -> dict:
        states: dict[str, int] = {}
        for fsm in self._machines.values():
            states[fsm.state.value] = states.get(fsm.state.value, 0) + 1

        return {
            **self._stats,
            "active_sessions": len(self._machines),
            "states": states,
            "valid_transitions": len(TRANSITIONS),
        }
