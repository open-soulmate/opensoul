"""Notification Center API — aggregates notifications from all organs.

Pulls recent events from the event stream, vital alerts, echo messages,
and nerve events into a unified notification feed.
"""

import time
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

# ── In-memory notification store ──────────────────────────────
_notifications: list[dict] = []
_MAX_NOTIFICATIONS = 500
_read_ids: set[str] = set()
# ── Echo Forwarding Rules ────────────────────────────────────
# Maps notification levels to echo channels for auto-forwarding.
# e.g. {"error": ["dingtalk", "telegram"], "warning": ["webhook"]}
_forward_rules: dict[str, list[str]] = {}
_forward_min_priority: int = 3  # Only forward notifications with priority <= this
_forward_enabled: bool = True



def _add_notification(
    source: str,
    title: str,
    body: str,
    level: str = "info",
    organ: str = "",
    emoji: str = "🔔",
    action_url: str = "",
    metadata: dict | None = None,
) -> dict:
    """Add a notification to the store."""
    notif = {
        "id": f"notif_{int(time.time() * 1000)}_{len(_notifications)}",
        "source": source,
        "title": title,
        "body": body,
        "level": level,  # info | warning | error | success
        "organ": organ,
        "emoji": emoji,
        "action_url": action_url,
        "metadata": metadata or {},
        "timestamp": time.time(),
        "read": False,
    }
    _notifications.insert(0, notif)
    # Trim to max
    while len(_notifications) > _MAX_NOTIFICATIONS:
        _notifications.pop()

    # Auto-forward to Echo channels if rules match
    _auto_forward_to_echo(notif)
    return notif


def _auto_forward_to_echo(notif: dict) -> None:
    """Forward notification to Echo channels based on forwarding rules."""
    if not _forward_enabled:
        return
    level = notif.get("level", "info")
    channels = _forward_rules.get(level, [])
    if not channels:
        return
    try:
        from src.echo.dispatcher import MessageDispatcher, Channel
        from src.api.echo import dispatcher
        emoji = notif.get("emoji", "🔔")
        title = f"{emoji} [{level.upper()}] {notif.get('title', '')}"
        body = notif.get("body", "")
        source = notif.get("source", "unknown")
        content = f"{body}\n\nSource: {source} | Time: {time.strftime('%H:%M:%S')}"
        for ch_name in channels:
            try:
                channel = Channel(ch_name)
                dispatcher.send(
                    channel=channel,
                    title=title,
                    content=content,
                    priority=notif.get("metadata", {}).get("priority", 5),
                )
            except (ValueError, Exception):
                pass  # Skip invalid or failed channels
    except Exception:
        pass  # Non-fatal — don't break notifications if Echo is down


def push_notification(
    source: str,
    title: str,
    body: str,
    level: str = "info",
    organ: str = "",
    emoji: str = "🔔",
    action_url: str = "",
    metadata: dict | None = None,
) -> None:
    """Public API for other modules to push notifications."""
    _add_notification(source, title, body, level, organ, emoji, action_url, metadata)


# ── Seed some initial notifications from event stream ─────────

def _seed_from_events():
    """Pull recent events from the event buffer and convert to notifications."""
    try:
        from src.api.event_stream import _event_buffer
        for evt in list(_event_buffer)[:30]:
            organ = evt.get("organ", "system")
            emoji = evt.get("emoji", "🔔")
            summary = evt.get("summary", "")
            evt_type = evt.get("type", "event")
            if not summary:
                continue
            # Avoid duplicates by checking if we already have this event
            existing_ids = {n.get("metadata", {}).get("event_id") for n in _notifications}
            evt_id = evt.get("id", "")
            if evt_id in existing_ids:
                continue
            # Map event types to notification levels
            level = "info"
            if evt_type in ("error", "alert", "content_blocked", "ip_blacklisted", "component_error"):
                level = "error"
            elif evt_type in ("warning", "rate_limited"):
                level = "warning"
            elif evt_type in ("backup_created", "file_uploaded", "component_registered"):
                level = "success"
            _add_notification(
                source="event_stream",
                title=f"{emoji} {organ.upper()}",
                body=summary,
                level=level,
                organ=organ,
                emoji=emoji,
                action_url=f"/{organ}",
                metadata={"event_id": evt_id, "event_type": evt_type},
            )
    except Exception:
        pass


def _seed_from_vital():
    """Pull recent vital alerts and convert to notifications."""
    try:
        from src.vital.alert import AlertManager
        # Try to get alerts from the alert manager
        from src.vital.collector import MetricsCollector
        collector = MetricsCollector()
        checker = __import__("src.vital.health", fromlist=["HealthChecker"]).HealthChecker()
        # Check component health
        results = checker.check_all()
        for comp in results:
            if comp.get("status") == "error":
                name = comp.get("name", "unknown")
                existing_titles = {n.get("title") for n in _notifications}
                title = f"⚠️ VITAL: {name} 健康检查失败"
                if title not in existing_titles:
                    _add_notification(
                        source="vital",
                        title=title,
                        body=f"组件 {name} 健康检查返回错误状态",
                        level="error",
                        organ="vital",
                        emoji="📊",
                        action_url="/vital",
                        metadata={"component": name, "check_type": "health"},
                    )
    except Exception:
        pass


def _seed_all():
    """Seed notifications from all sources."""
    _seed_from_events()
    _seed_from_vital()


# ── API Endpoints ─────────────────────────────────────────────

@router.get("/recent")
async def get_recent_notifications(
    limit: int = Query(50, ge=1, le=200),
    unread_only: bool = Query(False),
    source: str = Query("", description="Filter by source"),
    level: str = Query("", description="Filter by level: info|warning|error|success"),
):
    """Get recent notifications, optionally filtered."""
    # Seed from events on first call
    if not _notifications:
        _seed_all()

    results = _notifications
    if unread_only:
        results = [n for n in results if not n.get("read")]
    if source:
        results = [n for n in results if n.get("source") == source]
    if level:
        results = [n for n in results if n.get("level") == level]

    unread_count = sum(1 for n in _notifications if not n.get("read"))

    return {
        "notifications": results[:limit],
        "total": len(results),
        "unread_count": unread_count,
    }


@router.get("/unread-count")
async def get_unread_count():
    """Get count of unread notifications."""
    return {"unread_count": sum(1 for n in _notifications if not n.get("read"))}


@router.post("/{notif_id}/read")
async def mark_read(notif_id: str):
    """Mark a notification as read."""
    for n in _notifications:
        if n["id"] == notif_id:
            n["read"] = True
            _read_ids.add(notif_id)
            return {"success": True}
    return {"success": False, "error": "not found"}


@router.post("/read-all")
async def mark_all_read():
    """Mark all notifications as read."""
    for n in _notifications:
        n["read"] = True
        _read_ids.add(n["id"])
    return {"success": True, "marked": len(_notifications)}


@router.delete("/{notif_id}")
async def dismiss_notification(notif_id: str):
    """Dismiss (delete) a notification."""
    global _notifications
    before = len(_notifications)
    _notifications = [n for n in _notifications if n["id"] != notif_id]
    return {"success": len(_notifications) < before}


class ForwardRuleRequest(BaseModel):
    level: str  # "error", "warning", "info", "success"
    channels: list[str]  # e.g. ["dingtalk", "telegram"]


class ForwardNotificationRequest(BaseModel):
    channel: str  # echo channel to forward to


@router.delete("/")
async def clear_all():
    """Clear all notifications."""
    global _notifications
    count = len(_notifications)
    _notifications = []
    return {"success": True, "cleared": count}


@router.post("/test")
async def push_test_notification():
    """Push a test notification (for development)."""
    notif = _add_notification(
        source="test",
        title="🧪 Test Notification",
        body="This is a test notification to verify the notification center is working.",
        level="info",
        organ="system",
        emoji="🧪",
    )
    return notif


@router.get("/health")
async def health():
    """Health check for notification system."""
    return {
        "status": "ok",
        "total_notifications": len(_notifications),
        "unread_count": sum(1 for n in _notifications if not n.get("read")),
    }


@router.get("/stats")
async def notifications_stats():
    """Get notification statistics."""
    levels = {}
    organs = {}
    for n in _notifications:
        level = n.get("level", "info")
        levels[level] = levels.get(level, 0) + 1
        organ = n.get("organ", "system")
        organs[organ] = organs.get(organ, 0) + 1
    return {
        "status": "ok",
        "component": "OpenNotifications",
        "total": len(_notifications),
        "unread": sum(1 for n in _notifications if not n.get("read")),
        "by_level": levels,
        "by_organ": organs,
    }


# ── Echo Forwarding Endpoints ──────────────────────────────

@router.get("/forward/rules")
async def get_forward_rules():
    """Get current notification-to-Echo forwarding rules."""
    return {
        "enabled": _forward_enabled,
        "rules": _forward_rules,
        "min_priority": _forward_min_priority,
    }


@router.put("/forward/rules")
async def set_forward_rule(req: ForwardRuleRequest):
    """Set forwarding rule: which notification level → which Echo channels."""
    valid_levels = {"info", "warning", "error", "success"}
    if req.level not in valid_levels:
        raise HTTPException(400, f"Invalid level. Valid: {valid_levels}")
    from src.echo.dispatcher import Channel
    valid_channels = {c.value for c in Channel}
    for ch in req.channels:
        if ch not in valid_channels:
            raise HTTPException(400, f"Invalid channel '{ch}'. Valid: {valid_channels}")
    _forward_rules[req.level] = req.channels
    return {"success": True, "level": req.level, "channels": req.channels}


@router.delete("/forward/rules/{level}")
async def delete_forward_rule(level: str):
    """Remove forwarding rule for a notification level."""
    removed = _forward_rules.pop(level, None)
    return {"success": removed is not None, "level": level}


@router.put("/forward/enabled")
async def set_forward_enabled(enabled: bool = Query(...)):
    """Enable or disable Echo forwarding globally."""
    global _forward_enabled
    _forward_enabled = enabled
    return {"enabled": _forward_enabled}


@router.post("/{notif_id}/forward")
async def forward_notification(notif_id: str, req: ForwardNotificationRequest):
    """Manually forward a specific notification to an Echo channel."""
    notif = next((n for n in _notifications if n["id"] == notif_id), None)
    if not notif:
        raise HTTPException(404, "Notification not found")

    from src.echo.dispatcher import Channel
    try:
        channel = Channel(req.channel)
    except ValueError:
        raise HTTPException(400, f"Invalid channel. Valid: {[c.value for c in Channel]}")

    from src.api.echo import dispatcher
    emoji = notif.get("emoji", "🔔")
    level = notif.get("level", "info")
    title = f"{emoji} [{level.upper()}] {notif.get('title', '')}"
    body = notif.get("body", "")
    source = notif.get("source", "unknown")
    content = f"{body}\n\nSource: {source} | Time: {time.strftime('%H:%M:%S')}"

    result = dispatcher.send(channel=channel, title=title, content=content)

    return {
        "success": result.success,
        "msg_id": result.msg_id,
        "channel": result.channel,
        "error": result.error,
    }


@router.post("/forward/test")
async def test_forward():
    """Send a test notification that will be forwarded if rules are configured."""
    notif = _add_notification(
        source="forward_test",
        title="🔔 Echo Forward Test",
        body="This is a test of the notification → Echo forwarding bridge.",
        level="error",  # Use error level to trigger forwarding
        organ="system",
        emoji="🔔",
        metadata={"priority": 1},
    )
    return {
        "notification": notif,
        "forward_rules": _forward_rules,
        "forward_enabled": _forward_enabled,
    }
