from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field

import httpx

from src.gland.key_manager import KeyManager
from src.gland.token_meter import TokenMeter

logger = logging.getLogger(__name__)


def _outbound_redactor():
    """Lazy singleton ContentModerator for outbound LLM redaction.

    Fail-safe: any import/compile failure disables redaction (logged once)
    instead of breaking every LLM call.
    """
    global _REDACTOR, _REDACTOR_INIT
    if not _REDACTOR_INIT:
        _REDACTOR_INIT = True
        try:
            from src.immune.moderator import ContentModerator

            _REDACTOR = ContentModerator()
        except Exception as exc:
            logger.warning("Outbound secret redaction disabled: %s", exc)
            _REDACTOR = None
    return _REDACTOR


_REDACTOR = None
_REDACTOR_INIT = False
# Minimum risk level redacted before text leaves the machine toward an LLM
# provider. "critical" = API keys/tokens/passwords only; set to "low" to also
# redact PII (phone/id/email/IP). Configurable per-deployment.
OUTBOUND_REDACT_MIN_RISK = "critical"


def _is_rate_limited(exc: BaseException) -> bool:
    """HTTP 429 — the provider is overloaded, not broken."""
    import httpx

    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


def _is_transient_error(exc: BaseException) -> bool:
    """Error class that plausibly recovers on its own (429/5xx/transport).

    Used to decide whether the CowAgent wrap-around pass is worthwhile:
    permanent errors (401/404/400) will still be permanent a second later.
    """
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        from src.cortex.llm_retry import is_retryable_status

        return is_retryable_status(exc.response.status_code)
    return isinstance(exc, httpx.TransportError)


class TaskType(enum.StrEnum):
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
        # CowAgent chain trace: ordered {provider, model, pass, outcome/error}
        # log of the most recent chat()/embed() failover walk. Exposed via
        # get_chain_trace() and /api/gland endpoints — the chain walk must be
        # inspectable ("我都不知道他们在干嘛").
        self._last_chain_trace: list[dict] = []

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
            out.append(
                {
                    "name": p.name,
                    "base_url": p.base_url,
                    "models": p.models,
                    "enabled": p.enabled and not self._is_cooling_down(p),
                    "priority": p.priority,
                    "consecutive_failures": p._consecutive_failures,
                }
            )
        return out

    def get_chain_trace(self) -> list[dict]:
        """Return the failover-chain trace of the most recent request."""
        return list(self._last_chain_trace)

    # ── smart routing ────────────────────────────────────────────

    def _resolve_model(
        self, provider: ProviderConfig, task: TaskType, model: str | None
    ) -> str | None:
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
                p.name,
                self.COOLDOWN_SECONDS,
                p._consecutive_failures,
            )

    def _mark_success(self, p: ProviderConfig) -> None:
        p._consecutive_failures = 0
        p._cooldown_until = 0.0

    # ── per-provider retry (kilocode retry.ts policy) ────────────

    # Max attempts against ONE provider before giving up and letting the
    # router fail over to the next candidate. Annotated as int: tests and
    # deployments override per-instance (e.g. router.RETRY_MAX_ATTEMPTS = 2).
    RETRY_MAX_ATTEMPTS: int = 3

    async def _with_retry(self, provider: ProviderConfig, fn, *, has_backup: bool = False):
        """Run a single-provider HTTP call with the cortex retry policy.

        Retryable errors (429/5xx/timeouts/connection resets) are retried
        in place — honoring the server's Retry-After header when present —
        so one transient blip no longer burns a failure mark and triggers
        failover. Non-retryable errors (401/400/404/...) raise immediately
        so the next provider is tried at once.

        CowAgent rate-limit fast-switch (38-CowAgent-source-supplement3 #12):
        when the provider is rate-limited (429) AND a backup link remains in
        the chain, raise immediately instead of waiting — "限流有备胎立即切换".
        With no backup ("没备胎干等"), the normal in-place retry applies.
        """
        from src.cortex.llm_retry import retry_delay_for

        attempt = 0
        while True:
            try:
                return await fn()
            except Exception as exc:
                if has_backup and _is_rate_limited(exc):
                    logger.info(
                        "Provider=%s rate-limited with backup available — "
                        "switching immediately (CowAgent fast-switch)",
                        provider.name,
                    )
                    raise
                delay = retry_delay_for(exc, attempt)
                if delay is None or attempt + 1 >= self.RETRY_MAX_ATTEMPTS:
                    raise
                attempt += 1
                logger.warning(
                    "Provider=%s attempt %d/%d failed (%s), retrying in %.1fs",
                    provider.name,
                    attempt,
                    self.RETRY_MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

    # ── CowAgent ordered fallback chain ─────────────────────────

    def _build_links(
        self, candidates: list[ProviderConfig], task: TaskType, model: str | None
    ) -> list[tuple[ProviderConfig, str, str]]:
        """Build the ordered {provider, model, api_key} chain.

        Keyless providers (e.g. local Ollama) stay in the chain with an
        empty key — _call_chat/_call_embedding omit the Authorization
        header in that case. A provider that rejects keyless calls will
        401 and the chain moves on (fail-safe, no worse than skipping).
        """
        links: list[tuple[ProviderConfig, str, str]] = []
        for provider in candidates:
            resolved_model = self._resolve_model(provider, task, model)
            if not resolved_model:
                logger.debug(
                    "Skipping provider=%s: no model for task=%s", provider.name, task.value
                )
                continue
            api_key = self.key_manager.next_key(provider.name) or ""
            links.append((provider, resolved_model, api_key))
        return links

    async def _walk_chain(self, links, tried: list[dict], invoke):
        """Walk the CowAgent ordered fallback chain.

        Pass 1 — each link in priority order; within a link the kilocode
        retry policy applies, except rate-limits when a backup link remains
        (fast-switch). Each link failure is recorded in *tried*.

        Pass 2 (wrap-around) — only when the chain has ≥2 links and pass-1
        saw at least one transient error: "瞬时限流已恢复不该废掉整个turn".
        Single attempt per link, no in-place retry — a fast re-probe.

        On exhaustion raises AllProvidersFailedError listing every tried
        {provider, model, pass, error} (CowAgent: 链耗尽时报错列出所有试过的模型).

        Returns ``(result, provider, model)`` of the successful link.
        """
        last_error: Exception | None = None
        transient_seen = False

        # ── pass 1: ordered walk with per-link retry ─────────────
        for i, (provider, call_model, call_key) in enumerate(links):
            has_backup = i < len(links) - 1
            try:
                result = await self._with_retry(
                    provider,
                    lambda p=provider, k=call_key, m=call_model: invoke(p, k, m),
                    has_backup=has_backup,
                )
                self._mark_success(provider)
                tried.append(
                    {"provider": provider.name, "model": call_model, "pass": 1, "outcome": "success"}
                )
                return result, provider, call_model
            except Exception as exc:
                self._mark_failure(provider)
                tried.append(
                    {"provider": provider.name, "model": call_model, "pass": 1, "error": str(exc)}
                )
                last_error = exc
                if _is_transient_error(exc):
                    transient_seen = True
                logger.warning(
                    "Chain pass 1: provider=%s model=%s failed: %s", provider.name, call_model, exc
                )

        # ── pass 2: wrap-around re-probe (CowAgent) ──────────────
        if len(links) >= 2 and transient_seen:
            logger.info(
                "Chain exhausted with transient errors — wrap-around pass 2 (%d links)",
                len(links),
            )
            for provider, call_model, call_key in links:
                try:
                    result = await invoke(provider, call_key, call_model)
                    self._mark_success(provider)
                    tried.append(
                        {
                            "provider": provider.name,
                            "model": call_model,
                            "pass": 2,
                            "outcome": "success",
                        }
                    )
                    return result, provider, call_model
                except Exception as exc:
                    self._mark_failure(provider)
                    tried.append(
                        {"provider": provider.name, "model": call_model, "pass": 2, "error": str(exc)}
                    )
                    last_error = exc
                    logger.warning(
                        "Chain pass 2: provider=%s model=%s failed: %s",
                        provider.name,
                        call_model,
                        exc,
                    )

        # ── chain exhausted: enumerate everything tried ───────────
        tried_desc = "; ".join(
            f"{t['provider']}/{t['model']} (pass {t['pass']}): "
            f"{t.get('error', t.get('outcome', '?'))}"
            for t in tried
        )
        raise AllProvidersFailedError(
            f"All providers failed after {len(tried)} chain attempt(s). Tried: {tried_desc}",
            tried=tried,
        ) from last_error

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
        """Route a chat request through the CowAgent ordered fallback chain."""
        candidates = self._candidate_providers(task)
        if not candidates:
            raise NoProviderError(f"No provider available for task={task.value}")

        links = self._build_links(candidates, task, model)
        if not links:
            raise NoProviderError(f"No provider with a usable model for task={task.value}")

        async def _invoke(provider: ProviderConfig, api_key: str, model_name: str) -> dict:
            return await self._call_chat(
                provider,
                api_key,
                model_name,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
            )

        tried: list[dict] = []
        self._last_chain_trace = tried
        result, succ_provider, succ_model = await self._walk_chain(links, tried, _invoke)

        # Record token usage on the winning link.
        usage = result.get("usage", {})
        self.token_meter.record(
            provider=succ_provider.name,
            model=succ_model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            user_id=user_id,
        )
        return result

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        user_id: str | None = None,
    ) -> list[list[float]]:
        """Route an embedding request through the CowAgent ordered fallback chain."""
        candidates = self._candidate_providers(TaskType.EMBEDDING)
        if not candidates:
            raise NoProviderError("No provider available for embedding")

        # Embedding model resolution differs from chat: explicit model arg
        # wins, else the provider's "embedding" mapping.
        links: list[tuple[ProviderConfig, str, str]] = []
        for provider in candidates:
            resolved_model = model or provider.models.get("embedding")
            if not resolved_model:
                continue
            api_key = self.key_manager.next_key(provider.name) or ""
            links.append((provider, resolved_model, api_key))
        if not links:
            raise NoProviderError("No provider with a usable embedding model")

        async def _invoke(provider: ProviderConfig, api_key: str, model_name: str):
            return await self._call_embedding(provider, api_key, model_name, texts)

        tried: list[dict] = []
        self._last_chain_trace = tried
        result, succ_provider, succ_model = await self._walk_chain(links, tried, _invoke)

        # Rough token estimate for embeddings: 1 token per 4 chars
        est_tokens = sum(len(t) // 4 for t in texts)
        self.token_meter.record(
            provider=succ_provider.name,
            model=succ_model,
            prompt_tokens=est_tokens,
            completion_tokens=0,
            user_id=user_id,
        )
        return result

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
        # Outbound secret guard (Warp blocklist pattern): redact API keys /
        # tokens in message content before it leaves the machine toward the
        # provider. Fail-safe — redaction errors never block the LLM call.
        try:
            redactor = _outbound_redactor()
            if redactor is not None:
                messages, findings = redactor.redact_messages(
                    messages, min_risk=OUTBOUND_REDACT_MIN_RISK
                )
                if findings:
                    types = sorted({f["type"] for f in findings})
                    logger.warning(
                        "Redacted %d secret(s) before provider=%s: %s",
                        len(findings),
                        provider.name,
                        ", ".join(types),
                    )
        except Exception as exc:
            logger.debug("Outbound redaction skipped: %s", exc)

        client = self._http_client or httpx.AsyncClient(timeout=60)
        # Keyless providers (local Ollama) get no Authorization header.
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = await client.post(
            f"{provider.base_url}/chat/completions",
            headers=headers,
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
        # Keyless providers (local Ollama) get no Authorization header.
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = await client.post(
            f"{provider.base_url}/embeddings",
            headers=headers,
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
            return {
                "status": "ok",
                "provider": provider_name,
                "model": model,
                "reply": reply.strip(),
            }
        except Exception as exc:
            self._mark_failure(provider)
            return {"status": "error", "provider": provider_name, "detail": str(exc)}


class NoProviderError(Exception):
    """No provider is available for the requested task type."""


class AllProvidersFailedError(Exception):
    """Every candidate provider failed.

    ``tried`` carries the full CowAgent chain trace (provider/model/pass/
    error per attempt) so callers and logs can see exactly what was tried.
    """

    def __init__(self, message: str, tried: list[dict] | None = None) -> None:
        super().__init__(message)
        self.tried: list[dict] = tried or []
