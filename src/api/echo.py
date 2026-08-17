"""OpenEcho API — 回声系统：多渠道消息推送、消息模板。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.echo.dispatcher import MessageDispatcher, Channel
from src.echo.templates import TemplateEngine
from src.nerve.event_bridge import push_event

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
dispatcher = MessageDispatcher()
template_engine = TemplateEngine()


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

    # Emit event
    if result.success:
        push_event({
            "organ": "echo", "emoji": "🔊", "type": "message_sent",
            "summary": f"📨 [{req.channel}] {req.title}",
            "detail": {"channel": req.channel, "title": req.title, "msg_id": result.msg_id},
        })

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


# ── Stats ──────────────────────────────────────────────────

@router.get("/stats")
async def echo_stats():
    """Get OpenEcho statistics."""
    return {
        "status": "ok",
        "component": "OpenEcho",
        **dispatcher.stats(),
        "templates": template_engine.stats(),
    }


# ── Health ─────────────────────────────────────────────────

@router.get("/health")
async def echo_health():
    """OpenEcho health check."""
    return {
        "status": "ok",
        "component": "OpenEcho",
        **dispatcher.stats(),
        "templates": template_engine.stats(),
    }


# ── Template Schemas ───────────────────────────────────────

class TemplateCreateRequest(BaseModel):
    name: str
    title_template: str
    content_template: str
    description: str = ""
    channel: str = "any"
    category: str = "custom"
    icon: str = "📨"


class TemplateUpdateRequest(BaseModel):
    name: str | None = None
    title_template: str | None = None
    content_template: str | None = None
    description: str | None = None
    channel: str | None = None
    category: str | None = None
    icon: str | None = None


class TemplateSendRequest(BaseModel):
    variables: dict = {}
    channel: str | None = None  # override template's preferred channel
    target: str = ""
    priority: int = 5


# ── Template Endpoints ─────────────────────────────────────

@router.get("/templates")
async def list_templates(category: str = Query(default=None)):
    """List all message templates."""
    return {"templates": template_engine.list_templates(category=category)}


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    """Get a specific template."""
    tpl = template_engine.get(template_id)
    if not tpl:
        raise HTTPException(404, "Template not found")
    return {
        "template_id": tpl.template_id,
        "name": tpl.name,
        "description": tpl.description,
        "channel": tpl.channel,
        "title_template": tpl.title_template,
        "content_template": tpl.content_template,
        "variables": tpl.variables,
        "category": tpl.category,
        "icon": tpl.icon,
        "usage_count": tpl.usage_count,
        "last_used": tpl.last_used,
        "created_at": tpl.created_at,
    }


@router.post("/templates")
async def create_template(req: TemplateCreateRequest):
    """Create a new message template."""
    tpl = template_engine.create(
        name=req.name,
        title_template=req.title_template,
        content_template=req.content_template,
        description=req.description,
        channel=req.channel,
        category=req.category,
        icon=req.icon,
    )
    push_event({
        "organ": "echo", "emoji": "🔊", "type": "template_created",
        "summary": f"📝 Template created: {tpl.name}",
        "detail": {"template_id": tpl.template_id, "name": tpl.name},
    })
    return {
        "template_id": tpl.template_id,
        "name": tpl.name,
        "variables": tpl.variables,
    }


@router.patch("/templates/{template_id}")
async def update_template(template_id: str, req: TemplateUpdateRequest):
    """Update an existing template."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not template_engine.update(template_id, **updates):
        raise HTTPException(404, "Template not found or no changes")
    return {"message": "updated", "template_id": template_id}


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    """Delete a template."""
    if not template_engine.delete(template_id):
        raise HTTPException(404, "Template not found")
    return {"message": "deleted", "template_id": template_id}


@router.post("/templates/{template_id}/send")
async def send_from_template(template_id: str, req: TemplateSendRequest):
    """Render a template and send the message immediately."""
    rendered = template_engine.render_template(template_id, req.variables)
    if not rendered:
        raise HTTPException(404, "Template not found")

    # Determine channel
    channel_name = req.channel or rendered["channel"]
    if channel_name == "any":
        channel_name = "console"  # default fallback

    try:
        channel = Channel(channel_name)
    except ValueError:
        raise HTTPException(400, f"Invalid channel: {channel_name}")

    result = dispatcher.send(
        channel=channel,
        title=rendered["title"],
        content=rendered["content"],
        target=req.target,
        priority=req.priority,
    )

    push_event({
        "organ": "echo", "emoji": "🔊", "type": "template_sent",
        "summary": f"📨 [{channel_name}] {rendered['title']}",
        "detail": {
            "template_id": template_id,
            "channel": channel_name,
            "msg_id": result.msg_id,
        },
    })

    return {
        "success": result.success,
        "msg_id": result.msg_id,
        "channel": result.channel,
        "error": result.error,
        "rendered_title": rendered["title"],
        "rendered_content": rendered["content"],
    }


@router.post("/templates/{template_id}/preview")
async def preview_template(template_id: str, req: TemplateSendRequest):
    """Preview a rendered template without sending."""
    rendered = template_engine.render_template(template_id, req.variables)
    if not rendered:
        raise HTTPException(404, "Template not found")
    return rendered
