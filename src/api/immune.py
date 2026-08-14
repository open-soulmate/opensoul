"""OpenImmune API — 免疫系统：内容风控、限流、IP管控、安全审计。"""

import time
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from src.immune.rate_limiter import RateLimiter, RateLimitConfig
from src.immune.moderator import ContentModerator
from src.immune.access_control import IPAccessControl
from src.immune.audit import AuditLogger, AuditAction

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
rate_limiter = RateLimiter()
moderator = ContentModerator()
ip_control = IPAccessControl()
audit = AuditLogger()


# ── Request Schemas ────────────────────────────────────────

class ModerateRequest(BaseModel):
    text: str


class IPActionRequest(BaseModel):
    ip: str
    reason: str = ""
    ttl_seconds: int | None = None


class RateLimitCheckRequest(BaseModel):
    key: str


# ── Content Moderation ─────────────────────────────────────

@router.post("/moderate")
async def moderate_text(req: ModerateRequest):
    """Scan text for sensitive data (PII, secrets, etc.)."""
    result = moderator.moderate(req.text)

    if not result.is_safe:
        audit.log(
            AuditAction.CONTENT_BLOCKED,
            detail=f"risk={result.risk_level}, findings={len(result.findings)}",
            risk_level=result.risk_level,
        )

    return {
        "is_safe": result.is_safe,
        "risk_level": result.risk_level,
        "findings": [
            {"type": f["type"], "label": f["label"], "risk": f["risk"]}
            for f in result.findings
        ],
        "redacted_text": result.redacted_text,
        "original_length": result.original_length,
    }


# ── Rate Limiting ──────────────────────────────────────────

@router.post("/rate-limit/check")
async def check_rate_limit(req: RateLimitCheckRequest, request: Request):
    """Check rate limit for a given key."""
    result = rate_limiter.check(req.key)

    if not result["allowed"]:
        audit.log(
            AuditAction.RATE_LIMITED,
            client_ip=request.client.host if request.client else "",
            endpoint=req.key,
            detail=f"minute={result['minute_count']}, hour={result['hour_count']}",
            risk_level="medium",
        )

    return result


@router.get("/rate-limit/stats")
async def rate_limit_stats():
    """Get rate limiter statistics."""
    return rate_limiter.stats()


@router.post("/rate-limit/reset")
async def reset_rate_limit(key: str = Query(default=None)):
    """Reset rate limit counters."""
    rate_limiter.reset(key)
    return {"message": "reset", "key": key or "all"}


@router.put("/rate-limit/config")
async def update_rate_limit_config(
    requests_per_minute: int = Query(default=60),
    requests_per_hour: int = Query(default=1000),
    burst_size: int = Query(default=20),
):
    """Update rate limit configuration."""
    rate_limiter.config = RateLimitConfig(
        requests_per_minute=requests_per_minute,
        requests_per_hour=requests_per_hour,
        burst_size=burst_size,
    )
    return {"message": "config updated", "config": rate_limiter.config.__dict__}


# ── IP Access Control ──────────────────────────────────────

@router.post("/ip/blacklist")
async def blacklist_ip(req: IPActionRequest, request: Request):
    """Add IP to blacklist."""
    ip_control.blacklist_add(req.ip, req.reason, req.ttl_seconds)
    audit.log(
        AuditAction.CONFIG_CHANGE,
        client_ip=request.client.host if request.client else "",
        detail=f"blacklisted {req.ip}: {req.reason}",
        risk_level="medium",
    )
    return {"message": f"IP {req.ip} blacklisted", "reason": req.reason}


@router.delete("/ip/blacklist/{ip}")
async def unblacklist_ip(ip: str):
    """Remove IP from blacklist."""
    ip_control.blacklist_remove(ip)
    return {"message": f"IP {ip} removed from blacklist"}


@router.post("/ip/whitelist")
async def whitelist_ip(req: IPActionRequest):
    """Add IP to whitelist."""
    ip_control.whitelist_add(req.ip, req.reason)
    return {"message": f"IP {req.ip} whitelisted"}


@router.delete("/ip/whitelist/{ip}")
async def unwhitelist_ip(ip: str):
    """Remove IP from whitelist."""
    ip_control.whitelist_remove(ip)
    return {"message": f"IP {ip} removed from whitelist"}


@router.get("/ip/lists")
async def list_ip_lists():
    """Get blacklist and whitelist."""
    return {
        "blacklist": ip_control.list_blacklist(),
        "whitelist": ip_control.list_whitelist(),
    }


@router.get("/ip/check/{ip}")
async def check_ip(ip: str):
    """Check if an IP is allowed."""
    result = ip_control.is_allowed(ip)
    if not result["allowed"]:
        audit.log(AuditAction.IP_BLOCKED, client_ip=ip, detail=result["reason"], risk_level="high")
    return result


# ── Audit Log ──────────────────────────────────────────────

@router.get("/audit/log")
async def get_audit_log(
    action: str = Query(default=None),
    risk_level: str = Query(default=None),
    client_ip: str = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    since: float = Query(default=None),
):
    """Query security audit log."""
    action_enum = None
    if action:
        try:
            action_enum = AuditAction(action)
        except ValueError:
            raise HTTPException(400, f"Invalid action. Valid: {[a.value for a in AuditAction]}")

    return {"entries": audit.query(
        action=action_enum,
        risk_level=risk_level,
        client_ip=client_ip,
        limit=limit,
        since=since,
    )}


@router.get("/audit/stats")
async def audit_stats():
    """Get audit log statistics."""
    return audit.stats()


# ── Health ─────────────────────────────────────────────────

@router.get("/health")
async def immune_health():
    """OpenImmune health check."""
    return {
        "status": "ok",
        "component": "OpenImmune",
        "modules": {
            "rate_limiter": rate_limiter.stats(),
            "moderator": {"patterns": len(moderator.patterns)},
            "access_control": ip_control.stats(),
            "audit": audit.stats(),
        },
    }
