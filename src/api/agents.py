"""Agent detection API - checks which AI agents are installed on the system."""

import shutil
import logging
from uuid import UUID

from fastapi import APIRouter, Depends

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
