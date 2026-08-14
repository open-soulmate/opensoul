"""OpenEcho API — 回声系统：多渠道消息推送。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.echo.dispatcher import MessageDispatcher, Channel

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
dispatcher = MessageDispatcher()


# ── Request Schemas ────────────────────────────────────────

class SendRequest(BaseModel):
    channel: str
    title: str
    content: str
    target: str = ""
    priority: int = 5


class BroadcastRequest(BaseModel):
    title: str
    content: str
    priority: int = 5


class ChannelConfigRequest(BaseModel):
    channel: str
    endpoint: str = ""
    token: str = ""
    enabled: bool = True
    extra: dict = {}


# ── Message Endpoints ──────────────────────────────────────

@router.post("/send")
async def send_message(req: SendRequest):
    """Send a message via specified channel."""
    try:
        channel = Channel(req.channel)
    except ValueError:
        raise HTTPException(400, f"Invalid channel. Valid: {[c.value for c in Channel]}")

    result = dispatcher.send(
        channel=channel,
        title=req.title,
        content=req.content,
        target=req.target,
        priority=req.priority,
    )
    return {
        "success": result.success,
        "msg_id": result.msg_id,
        "channel": result.channel,
        "error": result.error,
    }


@router.post("/broadcast")
async def broadcast_message(req: BroadcastRequest):
    """Broadcast a message to all enabled channels."""
    results = dispatcher.send_all(title=req.title, content=req.content, priority=req.priority)
    return {
        "results": [
            {"success": r.success, "msg_id": r.msg_id, "channel": r.channel, "error": r.error}
            for r in results
        ],
        "total": len(results),
        "sent": sum(1 for r in results if r.success),
    }


# ── Channel Configuration ──────────────────────────────────

@router.post("/channels/configure")
async def configure_channel(req: ChannelConfigRequest):
    """Configure a message channel."""
    try:
        channel = Channel(req.channel)
    except ValueError:
        raise HTTPException(400, f"Invalid channel. Valid: {[c.value for c in Channel]}")

    dispatcher.configure_channel(
        channel=channel,
        endpoint=req.endpoint,
        token=req.token,
        enabled=req.enabled,
        extra=req.extra,
    )
    return {"message": f"Channel '{req.channel}' configured", "enabled": req.enabled}


@router.get("/channels")
async def list_channels():
    """List configured channels."""
    return {"channels": dispatcher.list_channels()}


# ── History ────────────────────────────────────────────────

@router.get("/history")
async def message_history(
    limit: int = Query(default=50, ge=1, le=500),
    channel: str = Query(default=None),
):
    """Get message sending history."""
    ch = None
    if channel:
        try:
            ch = Channel(channel)
        except ValueError:
            raise HTTPException(400, f"Invalid channel: {channel}")
    return {"messages": dispatcher.history(limit=limit, channel=ch)}


# ── Health ─────────────────────────────────────────────────

@router.get("/health")
async def echo_health():
    """OpenEcho health check."""
    return {
        "status": "ok",
        "component": "OpenEcho",
        **dispatcher.stats(),
    }
