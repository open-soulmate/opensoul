from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config import settings
from src.gland.router import ModelRouter, TaskType

router = APIRouter()

# ── singleton gateway ───────────────────────────────────────────
# Bootstraps with env-based config; providers can be added/removed at runtime.
gateway = ModelRouter()


def _ensure_bootstrapped() -> None:
    """Register default providers from settings on first use."""
    if gateway.providers:
        return

    # OpenAI-compatible (covers OpenAI, custom endpoints, MiMo, etc.)
    if settings.llm_base_url:
        gateway.add_provider(
            name="openai",
            base_url=settings.llm_base_url,
            models={
                "chat": settings.llm_model,
                "embedding": settings.embedding_model,
            },
            priority=0,
        )
        if settings.llm_api_key:
            gateway.key_manager.add_key("openai", settings.llm_api_key)
        if settings.embedding_api_key and settings.embedding_api_key != settings.llm_api_key:
            gateway.key_manager.add_key("openai", settings.embedding_api_key)

    # Ollama (local, no key needed)
    ollama_url = getattr(settings, "ollama_base_url", "http://localhost:11434/v1")
    gateway.add_provider(
        name="ollama",
        base_url=ollama_url,
        models={"chat": "llama3.2", "embedding": "nomic-embed-text"},
        priority=10,
    )


# ── request / response schemas ──────────────────────────────────

class ChatRequest(BaseModel):
    messages: list[dict]
    model: str | None = None
    provider: str | None = None
    task: str = "chat"
    temperature: float = 0.7
    max_tokens: int = 2048
    user_id: str | None = None


class EmbedRequest(BaseModel):
    texts: list[str]
    model: str | None = None
    provider: str | None = None
    user_id: str | None = None


class ProviderCreate(BaseModel):
    name: str
    base_url: str
    models: dict[str, str] | None = None
    api_key: str | None = None
    priority: int = 0


class KeyAdd(BaseModel):
    provider: str
    api_key: str


class BudgetUpdate(BaseModel):
    limit: int


# ── endpoints ───────────────────────────────────────────────────

@router.get("/health")
async def gland_health():
    """OpenGland health check."""
    _ensure_bootstrapped()
    providers = gateway.list_providers()
    total_keys = sum(
        len(gateway.key_manager._slots.get(p["name"], []))
        for p in providers
    )
    return {
        "status": "ok",
        "component": "OpenGland",
        "providers": {
            "total": len(providers),
            "enabled": sum(1 for p in providers if p["enabled"]),
            "unhealthy": sum(1 for p in providers if not p["enabled"]),
        },
        "keys": {"total": total_keys},
        "token_meter": gateway.token_meter.summary(),
    }


@router.get("/models")
async def list_models():
    """List all available models grouped by provider."""
    _ensure_bootstrapped()
    result = {}
    for name, p in gateway.providers.items():
        result[name] = {
            "models": p.models,
            "enabled": p.enabled,
            "priority": p.priority,
        }
    return {"providers": result}


@router.post("/test")
async def test_connection(body: dict | None = None):
    """Test a provider connection. Defaults to the highest-priority provider."""
    _ensure_bootstrapped()
    provider_name = (body or {}).get("provider")
    if not provider_name:
        # Pick the first enabled provider
        candidates = [p for p in gateway.providers.values() if p.enabled]
        if not candidates:
            raise HTTPException(status_code=404, detail="No providers configured")
        provider_name = min(candidates, key=lambda p: p.priority).name

    result = await gateway.test_provider(provider_name)
    if result["status"] == "error":
        raise HTTPException(status_code=502, detail=result["detail"])
    return result


@router.get("/usage")
async def get_usage():
    """Return token usage statistics."""
    _ensure_bootstrapped()
    return gateway.token_meter.summary()


@router.get("/usage/recent")
async def get_recent_usage(limit: int = 50):
    """Return recent token usage records."""
    _ensure_bootstrapped()
    return {"records": gateway.token_meter.recent_records(limit)}


@router.post("/chat")
async def chat(req: ChatRequest):
    """Route a chat request through the gateway with automatic failover."""
    _ensure_bootstrapped()
    try:
        task = TaskType(req.task)
    except ValueError:
        task = TaskType.CHAT

    result = await gateway.chat(
        messages=req.messages,
        model=req.model,
        task=task,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        user_id=req.user_id,
    )
    return result


@router.post("/embed")
async def embed(req: EmbedRequest):
    """Route an embedding request through the gateway."""
    _ensure_bootstrapped()
    try:
        embeddings = await gateway.embed(
            texts=req.texts,
            model=req.model,
            user_id=req.user_id,
        )
        return {"embeddings": embeddings, "count": len(embeddings)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/providers")
async def list_providers():
    """List all registered providers with health status."""
    _ensure_bootstrapped()
    return {"providers": gateway.list_providers()}


@router.post("/providers")
async def add_provider(body: ProviderCreate):
    """Register a new provider at runtime."""
    _ensure_bootstrapped()
    gateway.add_provider(
        name=body.name,
        base_url=body.base_url,
        models=body.models,
        priority=body.priority,
    )
    if body.api_key:
        gateway.key_manager.add_key(body.name, body.api_key)
    return {"status": "ok", "provider": body.name}


@router.delete("/providers/{name}")
async def remove_provider(name: str):
    """Remove a provider."""
    _ensure_bootstrapped()
    if not gateway.remove_provider(name):
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return {"status": "ok"}


@router.post("/keys")
async def add_key(body: KeyAdd):
    """Add an API key for a provider."""
    _ensure_bootstrapped()
    slot = gateway.key_manager.add_key(body.provider, body.api_key)
    return {"status": "ok", "masked": slot.key[:4] + "..." + slot.key[-4:]}


@router.get("/keys")
async def list_keys():
    """List all keys (secrets masked)."""
    _ensure_bootstrapped()
    return gateway.key_manager.status()


@router.post("/budget")
async def set_budget(body: BudgetUpdate):
    """Set the token budget limit (0 = unlimited)."""
    _ensure_bootstrapped()
    gateway.token_meter.set_budget(body.limit)
    return {"status": "ok", "budget_limit": body.limit or "unlimited"}


# ── Stats ──────────────────────────────────────────────────

@router.get("/stats")
async def gland_stats():
    """Get OpenGland statistics."""
    _ensure_bootstrapped()
    return {
        "status": "ok",
        "component": "OpenGland",
        **gateway.token_meter.summary(),
        "providers": gateway.list_providers(),
        "keys": gateway.key_manager.status(),
    }
