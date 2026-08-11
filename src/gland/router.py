from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field

import httpx

from src.gland.key_manager import KeyManager
from src.gland.token_meter import TokenMeter

logger = logging.getLogger(__name__)


class TaskType(str, enum.Enum):
    CHAT = "chat"
    COMPLETION = "completion"
    EMBEDDING = "embedding"
    VISION = "vision"
    CODE = "code"


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    models: dict[str, str] = field(default_factory=dict)
    # models maps TaskType -> model name, e.g. {"chat": "gpt-4o", "embedding": "text-embedding-3-small"}
    enabled: bool = True
    priority: int = 0  # lower = higher priority
    _consecutive_failures: int = field(default=0, repr=False)
    _last_failure: float = field(default=0.0, repr=False)
    _cooldown_until: float = field(default=0.0, repr=False)


class ModelRouter:
    """Multi-provider LLM gateway with smart routing, load-balancing, and failover."""

    COOLDOWN_SECONDS = 60  # disable a provider for this long after repeated failures
    MAX_FAILURES = 3

    def __init__(self) -> None:
        self.providers: dict[str, ProviderConfig] = {}
        self.key_manager = KeyManager()
        self.token_meter = TokenMeter()
        self._http_client: httpx.AsyncClient | None = None

    # ── lifecycle ────────────────────────────────────────────────

    async def startup(self) -> None:
        self._http_client = httpx.AsyncClient(timeout=60)
        self.key_manager.load_from_env()

    async def shutdown(self) -> None:
        if self._http_client:
            await self._http_client.aclose()

    # ── provider management ──────────────────────────────────────

    def add_provider(
        self,
        name: str,
        base_url: str,
        models: dict[str, str] | None = None,
        priority: int = 0,
    ) -> ProviderConfig:
        cfg = ProviderConfig(
            name=name,
            base_url=base_url.rstrip("/"),
            models=models or {},
            priority=priority,
        )
        self.providers[name] = cfg
        logger.info("Registered provider=%s base_url=%s priority=%d", name, base_url, priority)
        return cfg

    def remove_provider(self, name: str) -> bool:
        return self.providers.pop(name, None) is not None

    def list_providers(self) -> list[dict]:
        out = []
        for p in self.providers.values():
            out.append({
                "name": p.name,
                "base_url": p.base_url,
                "models": p.models,
                "enabled": p.enabled and not self._is_cooling_down(p),
                "priority": p.priority,
                "consecutive_failures": p._consecutive_failures,
            })
        return out

    # ── smart routing ────────────────────────────────────────────

    def _resolve_model(self, provider: ProviderConfig, task: TaskType, model: str | None) -> str | None:
        """Determine the concrete model name for a request."""
        if model:
            return model
        return provider.models.get(task.value) or provider.models.get("chat")

    def _candidate_providers(self, task: TaskType) -> list[ProviderConfig]:
        """Return providers that can handle *task*, sorted by priority, excluding unhealthy ones."""
        candidates = []
        for p in self.providers.values():
            if not p.enabled:
                continue
            if self._is_cooling_down(p):
                continue
            # Provider must have at least a model mapping for this task or a generic "chat" fallback
            if task.value in p.models or "chat" in p.models:
                candidates.append(p)
        candidates.sort(key=lambda c: c.priority)
        return candidates

    def _is_cooling_down(self, p: ProviderConfig) -> bool:
        return p._cooldown_until > time.time()

    def _mark_failure(self, p: ProviderConfig) -> None:
        p._consecutive_failures += 1
        p._last_failure = time.time()
        if p._consecutive_failures >= self.MAX_FAILURES:
            p._cooldown_until = time.time() + self.COOLDOWN_SECONDS
            logger.warning(
                "Provider=%s cooling down for %ds after %d failures",
                p.name, self.COOLDOWN_SECONDS, p._consecutive_failures,
            )

    def _mark_success(self, p: ProviderConfig) -> None:
        p._consecutive_failures = 0
        p._cooldown_until = 0.0

    # ── API calls ────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        task: TaskType = TaskType.CHAT,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        user_id: str | None = None,
        stream: bool = False,
    ) -> dict:
        """Route a chat/completion request through the best available provider with failover."""
        candidates = self._candidate_providers(task)
        if not candidates:
            raise NoProviderError(f"No provider available for task={task.value}")

        last_error: Exception | None = None
        for provider in candidates:
            resolved_model = self._resolve_model(provider, task, model)
            if not resolved_model:
                logger.debug("Skipping provider=%s: no model for task=%s", provider.name, task.value)
                continue

            api_key = self.key_manager.next_key(provider.name)
            if not api_key:
                logger.debug("Skipping provider=%s: no API key", provider.name)
                continue

            try:
                result = await self._call_chat(
                    provider, api_key, resolved_model, messages,
                    temperature=temperature, max_tokens=max_tokens, stream=stream,
                )
                self._mark_success(provider)

                # Record token usage
                usage = result.get("usage", {})
                self.token_meter.record(
                    provider=provider.name,
                    model=resolved_model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    user_id=user_id,
                )
                return result
            except Exception as exc:
                last_error = exc
                self._mark_failure(provider)
                logger.warning("Provider=%s failed: %s, trying next...", provider.name, exc)

        raise AllProvidersFailedError(f"All providers failed for task={task.value}") from last_error

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        user_id: str | None = None,
    ) -> list[list[float]]:
        """Route an embedding request through the best available provider."""
        candidates = self._candidate_providers(TaskType.EMBEDDING)
        if not candidates:
            raise NoProviderError("No provider available for embedding")

        last_error: Exception | None = None
        for provider in candidates:
            resolved_model = model or provider.models.get("embedding")
            if not resolved_model:
                continue

            api_key = self.key_manager.next_key(provider.name)
            if not api_key:
                continue

            try:
                result = await self._call_embedding(provider, api_key, resolved_model, texts)
                self._mark_success(provider)

                # Rough token estimate for embeddings: 1 token per 4 chars
                est_tokens = sum(len(t) // 4 for t in texts)
                self.token_meter.record(
                    provider=provider.name,
                    model=resolved_model,
                    prompt_tokens=est_tokens,
                    completion_tokens=0,
                    user_id=user_id,
                )
                return result
            except Exception as exc:
                last_error = exc
                self._mark_failure(provider)
                logger.warning("Embedding provider=%s failed: %s", provider.name, exc)

        raise AllProvidersFailedError("All providers failed for embedding") from last_error

    # ── HTTP layer ───────────────────────────────────────────────

    async def _call_chat(
        self,
        provider: ProviderConfig,
        api_key: str,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> dict:
        client = self._http_client or httpx.AsyncClient(timeout=60)
        resp = await client.post(
            f"{provider.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": stream,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def _call_embedding(
        self,
        provider: ProviderConfig,
        api_key: str,
        model: str,
        texts: list[str],
    ) -> list[list[float]]:
        client = self._http_client or httpx.AsyncClient(timeout=60)
        resp = await client.post(
            f"{provider.base_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]

    # ── testing ──────────────────────────────────────────────────

    async def test_provider(self, provider_name: str) -> dict:
        """Send a minimal request to verify a provider is reachable."""
        provider = self.providers.get(provider_name)
        if not provider:
            return {"status": "error", "detail": f"Provider '{provider_name}' not found"}

        api_key = self.key_manager.next_key(provider_name)
        model = provider.models.get("chat") or next(iter(provider.models.values()), "")
        if not model:
            return {"status": "error", "detail": "No model configured for provider"}

        try:
            client = self._http_client or httpx.AsyncClient(timeout=30)
            resp = await client.post(
                f"{provider.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Say 'pong'."}],
                    "max_tokens": 16,
                },
            )
            resp.raise_for_status()
            reply = resp.json()["choices"][0]["message"]["content"]
            self._mark_success(provider)
            return {"status": "ok", "provider": provider_name, "model": model, "reply": reply.strip()}
        except Exception as exc:
            self._mark_failure(provider)
            return {"status": "error", "provider": provider_name, "detail": str(exc)}


class NoProviderError(Exception):
    """No provider is available for the requested task type."""


class AllProvidersFailedError(Exception):
    """Every candidate provider failed."""
