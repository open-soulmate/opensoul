"""Pomodoro Timer Plugin Backend — 番茄工作法计时器。"""

import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

# ── State ────────────────────────────────────────────────────
sessions: list[dict] = []
current_timer: dict | None = None

CONFIG = {
    "work_minutes": 25,
    "short_break_minutes": 5,
    "long_break_minutes": 15,
    "long_break_interval": 4,
}


class TimerStartRequest(BaseModel):
    task: str = ""
    duration_minutes: int | None = None  # override default


class TimerStopRequest(BaseModel):
    completed: bool = True  # True = finished, False = cancelled


# ── Endpoints ────────────────────────────────────────────────

@router.post("/start")
async def start_timer(req: TimerStartRequest):
    """Start a pomodoro timer."""
    global current_timer
    if current_timer and current_timer.get("active"):
        raise HTTPException(400, "Timer already running. Stop it first.")

    duration = req.duration_minutes or CONFIG["work_minutes"]
    current_timer = {
        "active": True,
        "task": req.task,
        "type": "work",
        "duration_minutes": duration,
        "started_at": time.time(),
        "ends_at": time.time() + duration * 60,
    }
    return current_timer


@router.post("/stop")
async def stop_timer(req: TimerStopRequest):
    """Stop the current timer."""
    global current_timer
    if not current_timer or not current_timer.get("active"):
        raise HTTPException(400, "No active timer")

    elapsed = time.time() - current_timer["started_at"]
    session = {
        "task": current_timer["task"],
        "type": current_timer["type"],
        "duration_minutes": current_timer["duration_minutes"],
        "elapsed_seconds": round(elapsed, 1),
        "completed": req.completed,
        "started_at": current_timer["started_at"],
        "ended_at": time.time(),
    }
    sessions.insert(0, session)
    current_timer = None
    return {"session": session, "total_sessions": len(sessions)}


@router.get("/status")
async def timer_status():
    """Get current timer status."""
    if not current_timer or not current_timer.get("active"):
        return {"active": False, "completed_sessions": len(sessions)}

    remaining = max(0, current_timer["ends_at"] - time.time())
    return {
        "active": True,
        "task": current_timer["task"],
        "type": current_timer["type"],
        "duration_minutes": current_timer["duration_minutes"],
        "remaining_seconds": round(remaining, 1),
        "elapsed_seconds": round(time.time() - current_timer["started_at"], 1),
        "progress": round(min(1.0, (time.time() - current_timer["started_at"]) / (current_timer["duration_minutes"] * 60)) * 100, 1),
        "completed_sessions": len(sessions),
    }


@router.get("/sessions")
async def get_sessions(limit: int = 50):
    """Get pomodoro session history."""
    return {"sessions": sessions[:limit], "total": len(sessions)}


@router.delete("/sessions")
async def clear_sessions():
    """Clear session history."""
    sessions.clear()
    return {"message": "Sessions cleared"}


@router.get("/stats")
async def get_stats():
    """Get pomodoro statistics."""
    today_start = time.time() - (time.time() % 86400)
    today_sessions = [s for s in sessions if s["started_at"] >= today_start and s["completed"]]
    total_focus = sum(s["elapsed_seconds"] for s in today_sessions)

    all_completed = [s for s in sessions if s["completed"]]
    all_focus = sum(s["elapsed_seconds"] for s in all_completed)

    return {
        "today": {
            "sessions": len(today_sessions),
            "focus_minutes": round(total_focus / 60, 1),
        },
        "all_time": {
            "sessions": len(all_completed),
            "focus_hours": round(all_focus / 3600, 1),
            "cancelled": len([s for s in sessions if not s["completed"]]),
        },
        "config": CONFIG,
    }


@router.put("/config")
async def update_config(
    work_minutes: int | None = None,
    short_break_minutes: int | None = None,
    long_break_minutes: int | None = None,
    long_break_interval: int | None = None,
):
    """Update pomodoro configuration."""
    if work_minutes is not None:
        CONFIG["work_minutes"] = max(1, min(120, work_minutes))
    if short_break_minutes is not None:
        CONFIG["short_break_minutes"] = max(1, min(30, short_break_minutes))
    if long_break_minutes is not None:
        CONFIG["long_break_minutes"] = max(1, min(60, long_break_minutes))
    if long_break_interval is not None:
        CONFIG["long_break_interval"] = max(1, min(10, long_break_interval))
    return {"config": CONFIG}


@router.get("/health")
async def plugin_health():
    """Pomodoro plugin health."""
    return {
        "status": "ok",
        "component": "Pomodoro",
        "active_timer": current_timer is not None and current_timer.get("active", False),
        "total_sessions": len(sessions),
        "config": CONFIG,
    }
