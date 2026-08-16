from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import httpx

from src.config import settings

router = APIRouter()


@router.get("/health")
async def llm_health():
    """LLM proxy health check."""
    return {"status": "ok", "component": "LLMProxy"}


class LLMRequest(BaseModel):
    messages: list[dict]
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int = 2048


class LLMConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class LLMConfigUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


# In-memory config override (persisted for the running instance).
# In production, store in DB or a config file.
_llm_overrides: dict[str, str] = {}


def _get_config() -> dict:
    return {
        "base_url": _llm_overrides.get("base_url", settings.llm_base_url),
        "api_key": "***" if _llm_overrides.get("api_key", settings.llm_api_key) else "",
        "model": _llm_overrides.get("model", settings.llm_model),
    }


@router.get("/config")
async def get_config():
    """Get current LLM configuration (API key masked)."""
    return _get_config()


@router.post("/config")
async def save_config(data: LLMConfigUpdate):
    """Save LLM configuration overrides."""
    if data.base_url is not None:
        _llm_overrides["base_url"] = data.base_url
    if data.api_key is not None:
        _llm_overrides["api_key"] = data.api_key
    if data.model is not None:
        _llm_overrides["model"] = data.model
    return _get_config()


@router.post("/test")
async def test_connection():
    """Test LLM connection with a simple prompt."""
    api_key = _llm_overrides.get("api_key", settings.llm_api_key)
    base_url = _llm_overrides.get("base_url", settings.llm_base_url)
    model = _llm_overrides.get("model", settings.llm_model)

    if not api_key:
        raise HTTPException(status_code=400, detail="LLM API key not configured")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Say 'pong' and nothing else."}],
                    "max_tokens": 16,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]
            return {"status": "ok", "model": model, "reply": reply.strip()}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"LLM API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM connection failed: {str(e)}")


@router.post("/completions")
async def completions(req: LLMRequest):
    api_key = _llm_overrides.get("api_key", settings.llm_api_key)
    if not api_key:
        raise HTTPException(status_code=400, detail="LLM API key not configured")

    base_url = _llm_overrides.get("base_url", settings.llm_base_url)
    model = req.model or _llm_overrides.get("model", settings.llm_model)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
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
