from fastapi import APIRouter
from pydantic import BaseModel

import httpx

from src.config import settings

router = APIRouter()


class LLMRequest(BaseModel):
    messages: list[dict]
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int = 2048


@router.post("/completions")
async def completions(req: LLMRequest):
    api_key = settings.llm_api_key
    if not api_key:
        return {"error": "LLM API key not configured"}

    model = req.model or settings.llm_model
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": req.messages,
                "temperature": req.temperature,
                "max_tokens": req.max_tokens,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()
