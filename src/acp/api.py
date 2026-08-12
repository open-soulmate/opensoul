"""ACP API endpoints with hermes -z fallback."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.user import get_current_user
from src.acp.proxy import get_acp_process

logger = logging.getLogger(__name__)
router = APIRouter()


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
    acp = get_acp_process()
    try:
        result = await acp.send_message(data.text, data.session_id)
        return {
            "ok": True,
            "content": result.get("response_text", ""),
            "source": result.get("source", "acp"),
            "session_id": data.session_id,
        }
    except TimeoutError:
        raise HTTPException(status_code=408, detail="ACP timeout")
    except Exception as e:
        logger.error(f"ACP send error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send-image")
async def send_image(data: ACPImageMessage, user_id: UUID = Depends(get_current_user)):
    acp = get_acp_process()
    try:
        result = await acp.send_message_with_image(data.text, data.image_data, data.mime_type, data.session_id)
        return {
            "ok": True,
            "content": result.get("response_text", ""),
            "source": result.get("source", "acp"),
        }
    except TimeoutError:
        raise HTTPException(status_code=408, detail="ACP timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions")
async def list_sessions(user_id: UUID = Depends(get_current_user)):
    acp = get_acp_process()
    try:
        sessions = await acp.list_sessions()
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/new")
async def new_session(user_id: UUID = Depends(get_current_user)):
    acp = get_acp_process()
    try:
        return await acp.new_session()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def acp_status():
    acp = get_acp_process()
    return {"running": acp.is_running}


@router.post("/start")
async def start_acp(user_id: UUID = Depends(get_current_user)):
    acp = get_acp_process()
    try:
        result = await acp.start()
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_acp(user_id: UUID = Depends(get_current_user)):
    acp = get_acp_process()
    await acp.stop()
    return {"ok": True}
