"""Hermes session bridge - lists sessions from ALL platforms (WeChat, Telegram, etc.)
and allows sending messages to specific sessions.
"""

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.user import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


class SessionInfo(BaseModel):
    id: str
    title: str
    preview: str
    last_active: str
    source: str  # weixin, telegram, discord, local, etc.


class SendMessage(BaseModel):
    session_id: str
    message: str


@router.get("/list")
async def list_sessions(
    source: str | None = None,
    limit: int = 20,
    user_id: UUID = Depends(get_current_user),
):
    """列出Hermes所有平台的会话（微信、Telegram等）"""
    cmd = ["hermes", "sessions", "list", "--limit", str(limit)]
    if source:
        cmd.extend(["--source", source])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode("utf-8", errors="replace").strip()

        sessions = []
        for line in output.split("\n"):
            if line.startswith("Title") or line.startswith("─") or not line.strip():
                continue
            # Parse: Title  Preview  LastActive  ID
            parts = line.rsplit(maxsplit=3)
            if len(parts) >= 2:
                sid = parts[-1] if len(parts) >= 4 else parts[-1]
                # Find the session ID (format: YYYYMMDD_HHMMSS_xxxxxxxx or cron_xxx)
                if len(sid) >= 8 and ("_" in sid):
                    title = " ".join(parts[:-3]) if len(parts) >= 4 else " ".join(parts[:-2])
                    preview = parts[-3] if len(parts) >= 4 else ""
                    last_active = parts[-2] if len(parts) >= 4 else parts[-1]
                    sessions.append({
                        "id": sid,
                        "title": title.strip() or "—",
                        "preview": preview,
                        "last_active": last_active,
                        "source": source or "all",
                    })

        return {"sessions": sessions, "total": len(sessions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send")
async def send_to_session(data: SendMessage, user_id: UUID = Depends(get_current_user)):
    """向指定会话发送消息（继续对话）"""
    try:
        # Use hermes send or hermes -z with session context
        cmd = ["hermes", "send", "--to", "weixin", data.message]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip()

        return {
            "ok": proc.returncode == 0,
            "output": output,
            "error": error if proc.returncode != 0 else None,
        }
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="Timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/platforms")
async def list_platforms(user_id: UUID = Depends(get_current_user)):
    """列出可用的消息平台"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "hermes", "send", "--list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode("utf-8", errors="replace").strip()

        platforms = []
        for line in output.split("\n"):
            line = line.strip()
            if line and not line.startswith("Usage") and not line.startswith("Examples"):
                platforms.append(line)

        return {"platforms": platforms}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
