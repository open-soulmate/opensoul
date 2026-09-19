import os
import json
import httpx
from pathlib import Path
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
    provider: str | None = None
    variant: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


_PROFILES_PATH = Path(__file__).parent.parent.parent / "data" / "llm_profiles.json"


def _load_profiles() -> dict:
    if _PROFILES_PATH.exists():
        try:
            return json.loads(_PROFILES_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_profiles(profiles: dict):
    _PROFILES_PATH.parent.mkdir(exist_ok=True)
    _PROFILES_PATH.write_text(json.dumps(profiles, ensure_ascii=False, indent=2))


def _mask_key(key: str) -> str:
    return ("***" + key[-4:]) if key and len(key) > 4 else ("***" if key else "")


def _migrate_env_to_profiles() -> dict:
    """首次：把env现有双槽位配置迁入per-provider profiles（归到mimo名下）"""
    if not _llm_overrides:
        _load_overrides_from_env()
    profiles = _load_profiles()
    if not profiles:
        profiles = {
            "mimo": {
                "standard": {
                    "url": _llm_overrides.get("standard_base_url", "") or _llm_overrides.get("base_url", ""),
                    "api_key": _llm_overrides.get("standard_api_key", "") or _llm_overrides.get("api_key", ""),
                    "model": _llm_overrides.get("standard_model", "") or _llm_overrides.get("model", ""),
                },
                "subscription": {
                    "url": _llm_overrides.get("subscription_base_url", ""),
                    "api_key": _llm_overrides.get("subscription_api_key", ""),
                    "model": _llm_overrides.get("subscription_model", ""),
                },
            }
        }
        _save_profiles(profiles)
    return profiles


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
    profiles = _migrate_env_to_profiles()
    masked_profiles = {}
    for pid, variants in profiles.items():
        masked_profiles[pid] = {}
        for vid, prof in variants.items():
            key = prof.get("api_key", "")
            masked_profiles[pid][vid] = {
                "url": prof.get("url", ""),
                "api_key": _mask_key(key),
                "has_key": bool(key),
                "model": prof.get("model", ""),
            }
    active_variant = _active_variant()
    main_key = _llm_overrides.get("api_key", settings.llm_api_key)
    return {
        "profiles": masked_profiles,
        "active_variant": active_variant,
        "base_url": _llm_overrides.get("base_url", settings.llm_base_url),
        "api_key": _mask(main_key, masked),
        "model": _llm_overrides.get("model", settings.llm_model),
    }


@router.get("/config")
async def get_config(masked: bool = False):
    """Get current LLM configuration. Default: real key for admin UI."""
    return _get_config(masked=masked)


@router.post("/config")
async def save_config(data: LLMConfigUpdate):
    """Save per-provider×variant config to profiles JSON + sync active to .env."""
    profiles = _migrate_env_to_profiles()
    provider = (data.provider or "mimo").strip() or "mimo"
    variant = (data.variant or "standard").strip() or "standard"
    if variant not in ("standard", "subscription"):
        variant = "standard"
    if provider not in profiles:
        profiles[provider] = {}
    prof = dict(profiles[provider].get(variant, {}))
    if data.base_url is not None:
        prof["url"] = data.base_url
    if data.api_key is not None and data.api_key and not data.api_key.startswith("***"):
        prof["api_key"] = data.api_key
    if data.model is not None:
        prof["model"] = data.model
    if not prof.get("url"):
        for v in PRESET_VARIANTS.get(provider, []):
            if v["id"] == variant and v.get("base_url"):
                prof["url"] = v["base_url"]
    profiles[provider][variant] = prof
    _save_profiles(profiles)
    _llm_overrides[f"{variant}_base_url"] = prof.get("url", "")
    _llm_overrides[f"{variant}_api_key"] = prof.get("api_key", "")
    _llm_overrides[f"{variant}_model"] = prof.get("model", "")
    _llm_overrides["base_url"] = prof.get("url", "")
    _llm_overrides["api_key"] = prof.get("api_key", "")
    _llm_overrides["model"] = prof.get("model", "")
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
    """每个provider×每个API体系独立探测：绿=可用/黄=余额不足/红=Key无效/灰=未配置"""
    profiles = _migrate_env_to_profiles()
    presets: dict[str, dict] = {}
    for pid, variants in PRESET_VARIANTS.items():
        presets[pid] = {}
        for v in variants:
            prof = profiles.get(pid, {}).get(v["id"], {})
            url = prof.get("url", "")
            key = prof.get("api_key", "")
            model = prof.get("model", "")
            status = "not_configured"
            if key and url:
                status = await _probe_variant(url, key, model)
            presets[pid][v["id"]] = {
                "status": status,
                "label": v["label"],
                "base_url": url,
                "key_prefix": v.get("key_prefix", ""),
            }
    return {
        "active_variant": _active_variant(),
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
    # 双体系槽位解析：variant槽位优先，回退legacy槽位，再回退settings
    api_key = _active_slot("api_key") or settings.llm_api_key
    if not api_key:
        raise HTTPException(status_code=400, detail="LLM API key not configured")

    base_url = _active_slot("base_url") or settings.llm_base_url
    model = req.model or _active_slot("model") or settings.llm_model

    # 失败必须typed可见（mem0 §1.1）：上游错误→502，超时→504，不可达→502；
    # 禁止裸raise导致ASGI 500纯文本（此前connect失败直接500，客户端无法区分原因）
    try:
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
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"LLM API error: {e.response.status_code} — {e.response.text[:200]}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="LLM request timed out (120s)")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"LLM upstream unreachable: {type(e).__name__}: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM completion failed: {type(e).__name__}: {e}")
