import os
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config import settings

router = APIRouter()

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")


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
    variant: str | None = None


class LLMModelsRequest(BaseModel):
    base_url: str
    api_key: str | None = None


@router.post("/models")
async def list_models(body: LLMModelsRequest):
    """List available models from an OpenAI-compatible API endpoint."""
    base_url = body.base_url.rstrip("/")
    api_key = body.api_key or _active_slot("api_key") or settings.llm_api_key

    headers = {"Content-Type": "application/json"}
    if api_key:
        if api_key.startswith("tp-"):
            headers["api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{base_url}/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            models = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
            models.sort()
            return {"models": models}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Models API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch models: {str(e)}")


# In-memory config override, synced to .env on save.
_llm_overrides: dict[str, str] = {}


def _load_overrides_from_env():
    """Load LLM overrides from .env on startup."""
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                _SLOT_MAP = {
                    "LLM_API_KEY": "api_key", "LLM_BASE_URL": "base_url", "LLM_MODEL": "model",
                    "LLM_ACTIVE_VARIANT": "active_variant",
                    "LLM_STANDARD_API_KEY": "standard_api_key", "LLM_STANDARD_BASE_URL": "standard_base_url", "LLM_STANDARD_MODEL": "standard_model",
                    "LLM_SUBSCRIPTION_API_KEY": "subscription_api_key", "LLM_SUBSCRIPTION_BASE_URL": "subscription_base_url", "LLM_SUBSCRIPTION_MODEL": "subscription_model",
                }
                slot = _SLOT_MAP.get(k)
                if slot and v:
                    _llm_overrides[slot] = v


def _save_overrides_to_env():
    """Persist LLM overrides to .env file."""
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        lines = f.readlines()

    keys_to_save = {
        "LLM_API_KEY": "api_key", "LLM_BASE_URL": "base_url", "LLM_MODEL": "model",
        "LLM_ACTIVE_VARIANT": "active_variant",
        "LLM_STANDARD_API_KEY": "standard_api_key", "LLM_STANDARD_BASE_URL": "standard_base_url", "LLM_STANDARD_MODEL": "standard_model",
        "LLM_SUBSCRIPTION_API_KEY": "subscription_api_key", "LLM_SUBSCRIPTION_BASE_URL": "subscription_base_url", "LLM_SUBSCRIPTION_MODEL": "subscription_model",
    }
    updated_keys = set()

    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in keys_to_save:
                override_key = keys_to_save[k]
                val = _llm_overrides.get(override_key, "")
                new_lines.append(f"{k}={val}\n")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    # Append any keys not already in .env
    for env_key, override_key in keys_to_save.items():
        if env_key not in updated_keys and override_key in _llm_overrides:
            new_lines.append(f"{env_key}={_llm_overrides[override_key]}\n")

    with open(ENV_PATH, "w") as f:
        f.writelines(new_lines)


# Load on module import
_load_overrides_from_env()


def _active_variant() -> str:
    return _llm_overrides.get("active_variant", "standard") or "standard"


def _active_slot(field: str) -> str:
    prefix = f"{_active_variant()}_"
    return _llm_overrides.get(prefix + field) or _llm_overrides.get(field, "")


def _mask(k: str, masked: bool) -> str:
    return ("***" if masked and k else k) if k else ""


def _get_config(masked: bool = False) -> dict:
    std_key = _llm_overrides.get("standard_api_key", "") or _llm_overrides.get("api_key", settings.llm_api_key)
    sub_key = _llm_overrides.get("subscription_api_key", "")
    main_key = _llm_overrides.get("api_key", settings.llm_api_key)
    return {
        "base_url": _llm_overrides.get("base_url", settings.llm_base_url),
        "api_key": _mask(main_key, masked),
        "model": _llm_overrides.get("model", settings.llm_model),
        "active_variant": _active_variant(),
        "standard": {
            "base_url": _llm_overrides.get("standard_base_url", "") or _llm_overrides.get("base_url", settings.llm_base_url),
            "api_key": _mask(std_key, masked),
            "model": _llm_overrides.get("standard_model", "") or _llm_overrides.get("model", settings.llm_model),
        },
        "subscription": {
            "base_url": _llm_overrides.get("subscription_base_url", ""),
            "api_key": _mask(sub_key, masked),
            "model": _llm_overrides.get("subscription_model", ""),
        },
    }


@router.get("/config")
async def get_config(masked: bool = False):
    """Get current LLM configuration. Default: real key for admin UI."""
    return _get_config(masked=masked)


@router.post("/config")
async def save_config(data: LLMConfigUpdate):
    """Save LLM configuration overrides and persist to .env."""
    variant = (data.variant or "standard").strip() or "standard"
    if variant not in ("standard", "subscription"):
        variant = "standard"
    if data.base_url is not None:
        _llm_overrides[f"{variant}_base_url"] = data.base_url
        _llm_overrides["base_url"] = data.base_url
    if data.api_key is not None:
        _llm_overrides[f"{variant}_api_key"] = data.api_key
        _llm_overrides["api_key"] = data.api_key
    if data.model is not None:
        _llm_overrides[f"{variant}_model"] = data.model
        _llm_overrides["model"] = data.model
    _llm_overrides["active_variant"] = variant
    _save_overrides_to_env()
    return _get_config()


class LLMTestRequest(BaseModel):
    """Optional overrides for test — lets the frontend test unsaved settings."""
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


# ── 大模型预设双体系（标准API / Token Plan）状态指示灯 ──────────
def _std(url: str, prefix: str = "sk-") -> dict:
    return {"id": "standard", "label": "标准API", "base_url": url, "key_prefix": prefix}


def _sub(url: str = "", prefix: str = "") -> dict:
    return {"id": "subscription", "label": "订阅制", "base_url": url, "key_prefix": prefix}


# 所有云端大模型统一双体系：标准API + 订阅制（订阅地址为空=用户自填；小米订阅制=官方Token Plan地址）
PRESET_VARIANTS: dict[str, list[dict]] = {
    "mimo": [_std("https://api.xiaomimimo.com/v1"), _sub("https://token-plan-cn.xiaomimimo.com/v1", "tp-")],
    "openai": [_std("https://api.openai.com/v1"), _sub()],
    "claude": [_std("https://api.anthropic.com/v1", "sk-ant-"), _sub()],
    "gemini": [_std("https://generativelanguage.googleapis.com/v1beta", "AIza"), _sub()],
    "deepseek": [_std("https://api.deepseek.com/v1"), _sub()],
    "qwen": [_std("https://dashscope.aliyuncs.com/compatible-mode/v1"), _sub()],
    "zhipu": [_std("https://open.bigmodel.cn/api/paas/v4", ""), _sub()],
    "moonshot": [_std("https://api.moonshot.cn/v1"), _sub()],
    "baichuan": [_std("https://api.baichuan-ai.com/v1"), _sub()],
    "yi": [_std("https://api.lingyiwanwu.com/v1"), _sub()],
    "minimax": [_std("https://api.minimax.chat/v1", "eyJ"), _sub()],
    "stepfun": [_std("https://api.stepfun.com/v1"), _sub()],
    "doubao": [_std("https://ark.cn-beijing.volces.com/api/v3", ""), _sub()],
    "ollama": [{"id": "standard", "label": "本地", "base_url": "http://localhost:11434/v1", "key_prefix": ""}],
    "lmstudio": [{"id": "standard", "label": "本地", "base_url": "http://localhost:1234/v1", "key_prefix": ""}],
    "vllm": [{"id": "standard", "label": "本地", "base_url": "http://localhost:8000/v1", "key_prefix": ""}],
    "custom": [_std("", ""), _sub("", "")],
}


async def _probe_variant(base_url: str, api_key: str, model: str = "") -> str:
    """探测API端点可用性→状态: ok/invalid_key/low_balance/unreachable/not_configured。"""
    if not api_key or not base_url:
        return "not_configured"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code in (401, 403):
            return "invalid_key"
        if resp.status_code == 402:
            return "low_balance"
        if resp.status_code == 200:
            # models可达≠余额充足：做一次最小chat调用探测真实可用性（max_tokens=1，成本可忽略）
            try:
                pass
                if model:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        probe = await client.post(
                            f"{base_url.rstrip('/')}/chat/completions",
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json={"model": model, "messages": [{"role": "user", "content": "."}], "max_tokens": 1},
                        )
                    if probe.status_code == 402:
                        return "low_balance"
            except Exception:
                pass
            return "ok"
        return f"error_{resp.status_code}"
    except Exception:
        return "unreachable"


@router.get("/presets/status")
async def presets_status():
    """每个大模型×每个API体系(标准/订阅制)的状态指示灯。
    standard槽位和subscription槽位各自独立探测：各自用自己的base_url+key。"""
    if not _llm_overrides:
        _load_overrides_from_env()
    std_base = (_llm_overrides.get("standard_base_url") or _llm_overrides.get("base_url") or "").rstrip("/")
    std_key = _llm_overrides.get("standard_api_key") or _llm_overrides.get("api_key") or ""
    std_model = _llm_overrides.get("standard_model") or _llm_overrides.get("model") or ""
    sub_base = (_llm_overrides.get("subscription_base_url") or "").rstrip("/")
    sub_key = _llm_overrides.get("subscription_api_key") or ""
    sub_model = _llm_overrides.get("subscription_model") or ""
    presets: dict[str, dict] = {}
    for pid, variants in PRESET_VARIANTS.items():
        presets[pid] = {}
        for v in variants:
            vbase = v["base_url"].rstrip("/")
            status = "not_configured"
            if v["id"] == "subscription":
                if sub_key and vbase and vbase == sub_base:
                    status = await _probe_variant(v["base_url"], sub_key, sub_model)
            else:
                if std_key and vbase and vbase == std_base:
                    status = await _probe_variant(v["base_url"], std_key, std_model)
            presets[pid][v["id"]] = {
                "status": status,
                "label": v["label"],
                "base_url": v["base_url"],
                "key_prefix": v.get("key_prefix", ""),
            }
    return {
        "active_variant": _active_variant(),
        "standard": {"base_url": std_base, "model": std_model, "configured": bool(std_key)},
        "subscription": {"base_url": sub_base, "model": sub_model, "configured": bool(sub_key)},
        "presets": presets,
    }


@router.post("/test")
async def test_connection(body: LLMTestRequest | None = None):
    """Test LLM connection with a simple prompt.
    Accepts optional overrides so the frontend can test settings
    the user has edited but not yet saved.
    """
    api_key = (body.api_key if body and body.api_key else None) or _active_slot("api_key") or settings.llm_api_key
    base_url = (body.base_url if body and body.base_url else None) or _active_slot("base_url") or settings.llm_base_url
    model = (body.model if body and body.model else None) or _active_slot("model") or settings.llm_model

    import logging
    logging.getLogger("llm-test").warning(
        "TEST REQUEST: body=%s, resolved: base_url=%s, model=%s, key_len=%d, key_source=%s",
        body.model_dump() if body else None,
        base_url, model, len(api_key) if api_key else 0,
        "body" if (body and body.api_key) else "override/env"
    )

    if not api_key:
        raise HTTPException(status_code=400, detail="LLM API key not configured")

    # Mask key for display
    masked_key = api_key[:8] + "***" + api_key[-4:] if len(api_key) > 12 else "***"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    **({"api-key": api_key} if api_key.startswith("tp-") else {"Authorization": f"Bearer {api_key}"}),
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Say 'pong' and nothing else."}],
                    "max_tokens": 16,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]
            return {
                "status": "ok",
                "model": model,
                "base_url": base_url,
                "key_preview": masked_key,
                "reply": reply.strip(),
            }
    except httpx.HTTPStatusError as e:
        import logging
        logging.getLogger("llm-test").warning("LLM API error %d: %s", e.response.status_code, e.response.text[:300])
        raise HTTPException(status_code=502, detail=f"LLM API error: {e.response.status_code} — {e.response.text[:200]}")
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
            headers={
                "Content-Type": "application/json",
                **({"api-key": api_key} if api_key.startswith("tp-") else {"Authorization": f"Bearer {api_key}"}),
            },
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
