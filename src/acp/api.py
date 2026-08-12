"""ACP (Agent Client Protocol) API endpoints for OpenSoul."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.user import get_current_user
from src.acp.proxy import get_acp_process

logger = logging.getLogger(__name__)
router = APIRouter()

# Cache default session ID
_default_session_id: str | None = None


async def _get_session_id(session_id: str | None) -> str:
    """Get or create a session ID."""
    global _default_session_id
    if session_id:
        return session_id
    if _default_session_id:
        return _default_session_id

    acp = get_acp_process()
    # Create a new session
    result = await acp.new_session()
    _default_session_id = result.get("sessionId") or result.get("session_id") or "default"
    return _default_session_id


class ACPMessage(BaseModel):
    text: str
    session_id: str | None = None


class ACPImageMessage(BaseModel):
    text: str = ""
    image_data: str
    mime_type: str = "image/png"
    session_id: str | None = None


@router.post("/send")
async def send_message(data: ACPMessage, user_id: UUID = Depends(get_current_user)):
    """Send a message to Hermes via ACP."""
    acp = get_acp_process()
    try:
        sid = await _get_session_id(data.session_id)
        result = await acp.send_message(data.text, sid)

        # Extract text from PromptResponse
        content = _extract_response_text(result)
        return {"ok": True, "content": content, "session_id": sid, "raw": result}
    except TimeoutError:
        raise HTTPException(status_code=408, detail="ACP timeout")
    except Exception as e:
        logger.error(f"ACP send error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send-image")
async def send_image(data: ACPImageMessage, user_id: UUID = Depends(get_current_user)):
    """Send a message with image to Hermes via ACP."""
    acp = get_acp_process()
    try:
        sid = await _get_session_id(data.session_id)
        result = await acp.send_message_with_image(data.text, data.image_data, data.mime_type, sid)
        content = _extract_response_text(result)
        return {"ok": True, "content": content, "session_id": sid, "raw": result}
    except TimeoutError:
        raise HTTPException(status_code=408, detail="ACP timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions")
async def list_sessions(user_id: UUID = Depends(get_current_user)):
    """List ACP sessions."""
    acp = get_acp_process()
    try:
        sessions = await acp.list_sessions()
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/new")
async def new_session(user_id: UUID = Depends(get_current_user)):
    """Create a new ACP session."""
    acp = get_acp_process()
    try:
        session = await acp.new_session()
        return session
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def acp_status():
    """Check ACP process status."""
    acp = get_acp_process()
    return {"running": acp.is_running}


@router.post("/start")
async def start_acp(user_id: UUID = Depends(get_current_user)):
    """Start the ACP process."""
    acp = get_acp_process()
    try:
        result = await acp.start()
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_acp(user_id: UUID = Depends(get_current_user)):
    """Stop the ACP process."""
    acp = get_acp_process()
    await acp.stop()
    return {"ok": True}


def _extract_response_text(result: dict) -> str:
    """Extract readable text from ACP PromptResponse."""
    # PromptResponse has: stopReason, parts (list of content blocks)
    parts = result.get("parts", [])
    if not parts:
        # Maybe wrapped differently
        parts = result.get("content", [])

    texts = []
    for part in parts:
        if isinstance(part, dict):
            if part.get("type") == "text":
                texts.append(part.get("text", ""))
            elif part.get("type") == "image":
                texts.append(f"[图片: {part.get('source', {}).get('mediaType', 'image')}]")
        elif isinstance(part, str):
            texts.append(part)

    return "\n".join(texts) if texts else str(result)
