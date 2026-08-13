"""Hermes session bridge - lists sessions from ALL platforms (WeChat, Telegram, etc.)
and allows sending messages to specific sessions."""

import asyncio
import json
import logging
import os
import re
import subprocess
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.user import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


class SendMessageRequest(BaseModel):
    text: str
    session_id: str | None = None


def _list_sessions_sync(limit: int, source: str | None = None) -> dict:
    """Synchronous hermes sessions list (runs in thread executor)."""
    env = {**os.environ, "COLUMNS": "200"}
    cmd = ["hermes", "sessions", "list", "--limit", str(limit)]
    if source:
        cmd.extend(["--source", source])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, env=env,
        )
        output = result.stdout.strip()

        sessions = []
        for line in output.split("\n"):
            # Skip header/separator lines
            if ("Preview" in line or "Title" in line) and "Workspace" in line:
                continue
            if line.startswith("─") or line.startswith("═") or not line.strip():
                continue

            # Find session ID at end of line
            m = re.search(r'(\d{8}_\d{6}_[a-f0-9]+|cron_\w+)$', line)
            if not m:
                continue

            sid = m.group(1)
            rest = line[:m.start()].rstrip()

            # Split rest by 2+ spaces (column separator)
            cols = re.split(r'\s{2,}', rest)

            if len(cols) >= 4:
                name_or_title, workspace, last_active, src = cols[0].strip(), cols[1].strip(), cols[2].strip(), cols[3].strip()
            elif len(cols) >= 3:
                name_or_title, workspace, last_active = cols[0].strip(), cols[1].strip(), cols[2].strip()
                src = "cli"
            elif len(cols) >= 2:
                name_or_title, workspace = cols[0].strip(), cols[1].strip()
                last_active, src = "", "cli"
            else:
                name_or_title = cols[0].strip() if cols else ""
                workspace, last_active, src = "", "", "cli"

            # Map source to platform
            platform_map = {"cli": "hermes", "wx": "wechat", "wxentry": "wechat", "tg": "telegram", "dc": "discord"}
            platform = platform_map.get(src.lower(), src.lower() or "hermes")

            # Use session ID as fallback name when title is "—"
            display_name = name_or_title if name_or_title and name_or_title != "—" else sid

            sessions.append({
                "id": sid,
                "name": display_name,
                "platform": platform,
                "chat_id": "",
                "workspace": workspace,
                "last_active": last_active,
                "last_message": name_or_title[:80] if name_or_title else "",
            })

        return {"sessions": sessions, "total": len(sessions)}
    except Exception as e:
        logger.error("list_sessions error: %s", e)
        return {"sessions": [], "total": 0, "error": str(e)}


@router.get("/sessions")
async def list_sessions(
    source: str | None = None,
    limit: int = 50,
    user_id: UUID = Depends(get_current_user),
):
    """列出Hermes所有平台的会话（微信、Telegram等）"""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _list_sessions_sync, limit, source)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, user_id: UUID = Depends(get_current_user)):
    """获取指定会话的历史消息"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "hermes", "sessions", "export", "--session-id", session_id, "--format", "jsonl", "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace").strip()

        messages = []
        for line in output.split("\n"):
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
                messages.append({
                    "id": msg.get("id", ""),
                    "role": msg.get("role", "unknown"),
                    "content": msg.get("content", ""),
                    "timestamp": msg.get("timestamp", ""),
                    "source": msg.get("source", ""),
                })
            except json.JSONDecodeError:
                continue

        return {"messages": messages, "total": len(messages)}
    except Exception as e:
        logger.error("get_session_messages error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/send")
async def send_message(
    req: SendMessageRequest,
    user_id: UUID = Depends(get_current_user),
):
    """向指定会话发送消息"""
    try:
        if req.session_id:
            cmd = ["hermes", "send", "--session", req.session_id, "--", req.text]
        else:
            cmd = ["hermes", "-z", req.text]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode("utf-8", errors="replace").strip()

        return {"ok": True, "content": output, "source": "hermes-cli"}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Hermes response timeout")
    except Exception as e:
        logger.error("send_message error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
