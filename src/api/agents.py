"""Agent detection API - checks which AI agents are installed on the system."""

import asyncio
import logging
import shutil
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.user import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

# Known agents and their commands
AGENT_COMMANDS = {
    "hermes": "hermes",
    "mimo": "mimo",
    "opencode": "opencode",
    "claude": "claude",
    "codex": "codex",
    "cursor": "cursor",
    "aider": "aider",
    "continue": "continue",
}


@router.get("/detect")
async def detect_agents(user_id: UUID = Depends(get_current_user)):
    """检测本机安装的AI Agent"""
    result = {}
    for name, cmd in AGENT_COMMANDS.items():
        path = shutil.which(cmd)
        if path:
            result[name] = path
    return result


class InstallRequest(BaseModel):
    command: str


@router.post("/install")
async def install_agent(req: InstallRequest, user_id: UUID = Depends(get_current_user)):
    """执行Agent安装命令"""
    cmd = req.command.strip()
    if not cmd:
        return {"success": False, "error": "Empty command"}

    # Safety: only allow known install commands
    allowed_prefixes = [
        "npm install -g", "pip install", "pip3 install",
        "cargo install", "go install", "brew install",
        "gh extension install",
    ]
    if not any(cmd.startswith(p) for p in allowed_prefixes):
        return {"success": False, "error": f"Command not allowed: {cmd[:50]}"}

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode == 0:
            return {"success": True, "output": stdout.decode()[-500:]}
        else:
            return {"success": False, "error": stderr.decode()[-500:]}
    except asyncio.TimeoutError:
        return {"success": False, "error": "Installation timed out (5 min)"}
    except Exception as e:
        return {"success": False, "error": str(e)}
