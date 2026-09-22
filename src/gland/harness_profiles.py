"""Per-model Harness Profiles + Model Roles (deepagents + continue Model Roles).

Research (SUMMARY §五 cortex P1 "按模型Harness profiles"; 51-continue-source #1
"Model Roles六角色"; 38-CowAgent #11 "model-derived context budget";
93-97-98 #4 PromptConstructor): **"OpenSoul多模型共用一套prompt/工具面是'小模型
效果差'的结构性原因"**. Four-way corroboration (deepagents harness profiles +
continue model roles + CowAgent per-model budget + TradingAgents/STORM slots).

This module gives each (role, model-tier) a :class:`HarnessProfile` that tunes the
four knobs a harness must vary per model, instead of one global prompt/tool-face:

* **model routing** — each role resolves to its own model (continue: chat /
  summarize / embedding / rerank each配独立模型), so the summarizer can run on a
  cheap model while reasoning runs on a strong one.
* **context budget** — small models blow up on huge prompts → clamp the message
  window to what the tier tolerates (kilocode "不丢用户最后的话" tail-preserving).
* **generation defaults** — temperature / max_tokens per role (summarize≈0.2 stable,
  rerank≈0.0 deterministic, code larger window).
* **tool-face (工具可见性)** — small/summarize roles see fewer tools ("看不见>拦截",
  goose "总工具数<25" guidance).

Everything here is pure + offline-testable (no I/O); :class:`~src.gland.router.
ModelRouter` consumes it on the real dispatch path. This is a *default* layer:
callers that pass explicit ``temperature``/``max_tokens``/``model`` always win.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

# ── Model Roles (continue six-role → OpenSoul five) ───────────────────────────
# Each role routes to a TaskType AND looks up its own model key in a provider's
# ``models`` map (e.g. {"chat": "gpt-4o", "summarize": "gpt-4o-mini"}), so a
# single provider can expose a different model per role.


class ModelRole(enum.StrEnum):
    REASONING = "reasoning"  # main chat / agent reasoning
    SUMMARIZE = "summarize"  # compression / distillation / branch summaries
    EMBEDDING = "embedding"  # vectorisation
    RERANK = "rerank"  # retrieval re-ranking (feature/scoring model)
    CODE = "code"  # code generation / apply model


class ModelTier(enum.StrEnum):
    SMALL = "small"  # ≤~8B — concise prompt, shrunk tool-face, tight budget
    MEDIUM = "medium"
    LARGE = "large"  # frontier — detailed prompt, full tool-face, wide budget


# role → TaskType value (used for provider candidate selection + model fallback).
ROLE_TO_TASK: dict[str, str] = {
    ModelRole.REASONING.value: "chat",
    ModelRole.SUMMARIZE.value: "completion",
    ModelRole.EMBEDDING.value: "embedding",
    ModelRole.RERANK.value: "completion",
    ModelRole.CODE.value: "code",
}

# Explicit model-tier specs (deepagents "内置5个模型spec"). Exact/substring match
# wins over the heuristic below — extend as deployments pin known models.
MODEL_SPECS: dict[str, ModelTier] = {
    "deepseek-r1": ModelTier.LARGE,
    "deepseek-v3": ModelTier.LARGE,
    "mimo-v2.5-pro": ModelTier.LARGE,
    "mimo-v2.6-pro": ModelTier.LARGE,
    "gpt-4o": ModelTier.LARGE,
    "gpt-4-turbo": ModelTier.LARGE,
    "claude-3-7": ModelTier.LARGE,
    "gpt-4o-mini": ModelTier.SMALL,
}

# Heuristic fallback when the model is not in MODEL_SPECS (substring, checked in
# order: LARGE first — a "large" marker is unambiguous — then SMALL).
_LARGE_MARKERS = (
    "opus",
    "-70b",
    "-72b",
    "-405b",
    "deepseek-r1",
    "deepseek-v3",
    "gpt-4o",
    "gpt-4-turbo",
    "o1",
    "o3",
    "claude-4",
    "gemini-1.5-pro",
    "gemini-2",
    "-pro",
    "large",
    "max",
    "ultra",
)
_SMALL_MARKERS = (
    "mini",
    "lite",
    "tiny",
    "nano",
    "small",
    "-7b",
    "-8b",
    "-3b",
    "-2b",
    "-4b",
    "-6b",
    "-1.5",
    "7b",
    "8b",
    "distil",
    "turbo-mini",
)


def model_tier(model: str | None) -> ModelTier:
    """Classify a model name into a capability tier (heuristic + explicit specs)."""
    if not model:
        return ModelTier.MEDIUM
    name = model.lower()
    # Most-specific spec first (e.g. "gpt-4o-mini" before "gpt-4o") so a substring
    # of a longer model name can't steal its tier.
    for spec in sorted(MODEL_SPECS, key=len, reverse=True):
        if spec in name:
            return MODEL_SPECS[spec]
    if any(m in name for m in _LARGE_MARKERS):
        return ModelTier.LARGE
    if any(m in name for m in _SMALL_MARKERS):
        return ModelTier.SMALL
    return ModelTier.MEDIUM


# ── knobs per role / tier ─────────────────────────────────────────────────────
# Context budget (chars) the harness lets into the prompt window for this tier.
TIER_CONTEXT_CHARS: dict[ModelTier, int] = {
    ModelTier.SMALL: 16_000,
    ModelTier.MEDIUM: 48_000,
    ModelTier.LARGE: 120_000,
}
# Role multiplier — a summarizer must *read* long transcripts (bigger budget), a
# reranker scores one passage (smaller). Reasoning/code sit in between.
ROLE_BUDGET_FACTOR: dict[ModelRole, float] = {
    ModelRole.REASONING: 1.0,
    ModelRole.SUMMARIZE: 1.5,
    ModelRole.EMBEDDING: 1.0,
    ModelRole.RERANK: 0.5,
    ModelRole.CODE: 1.2,
}
ROLE_MAX_TOKENS: dict[ModelRole, int] = {
    ModelRole.REASONING: 2048,
    ModelRole.SUMMARIZE: 2048,
    ModelRole.EMBEDDING: 0,
    ModelRole.RERANK: 512,
    ModelRole.CODE: 4096,
}
TIER_MAX_TOKENS_CAP: dict[ModelTier, int] = {
    ModelTier.SMALL: 2048,
    ModelTier.MEDIUM: 4096,
    ModelTier.LARGE: 8192,
}
ROLE_TEMPERATURE: dict[ModelRole, float] = {
    ModelRole.REASONING: 0.7,
    ModelRole.SUMMARIZE: 0.2,
    ModelRole.EMBEDDING: 0.0,
    ModelRole.RERANK: 0.0,
    ModelRole.CODE: 0.2,
}
# Tool-face (工具可见性): how many tools this (role, tier) may see — goose "总工具数
# <25" guidance + "工具面收缩>运行时拦截". "none" = the role never calls tools.
ROLE_TOOL_FACE: dict[ModelRole, str] = {
    ModelRole.REASONING: "full",
    ModelRole.SUMMARIZE: "none",
    ModelRole.EMBEDDING: "none",
    ModelRole.RERANK: "none",
    ModelRole.CODE: "standard",
}
FACE_MAX_TOOLS: dict[str, int] = {"none": 0, "minimal": 8, "standard": 25, "full": 40}

_PROMPT_STYLE = {
    ModelTier.SMALL: "concise",
    ModelTier.MEDIUM: "balanced",
    ModelTier.LARGE: "detailed",
}


@dataclass(frozen=True)
class HarnessProfile:
    """Everything the harness must vary for one (role, model-tier) pair."""

    role: ModelRole
    tier: ModelTier
    context_budget_chars: int
    max_tokens: int
    temperature: float
    tool_face: str
    prompt_style: str = "balanced"

    def clamp_messages(self, messages: list[dict]) -> tuple[list[dict], bool]:
        """Tail-preserving clamp of *messages* to :attr:`context_budget_chars`.

        System prompts are kept verbatim (they carry the contract); the
        conversation is kept from the **tail** so the user's last words never
        vanish (kilocode "不丢用户最后的话"). A single oversized message has its
        head trimmed. Returns ``(clamped_messages, truncated)``.
        """
        budget = self.context_budget_chars
        system = [m for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        sys_chars = sum(len(str(m.get("content") or "")) for m in system)
        avail = max(500, budget - sys_chars)

        kept: list[dict] = []
        used = 0
        trimmed = False
        for m in reversed(rest):
            content = str(m.get("content") or "")
            if used + len(content) > avail and kept:
                trimmed = True
                break
            if len(content) > avail:
                content = content[-avail:]
                m = dict(m)
                m["content"] = content
                trimmed = True
            kept.append(m)
            used += len(content)
        kept.reverse()
        out = system + kept
        return out, trimmed or len(out) != len(messages)

    def describe(self) -> dict:
        return {
            "role": self.role.value,
            "tier": self.tier.value,
            "context_budget_chars": self.context_budget_chars,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "tool_face": self.tool_face,
            "prompt_style": self.prompt_style,
        }


def resolve_task(role: ModelRole | str) -> str:
    """Map a role to its TaskType value (provider candidate selection)."""
    r = ModelRole(role)
    return ROLE_TO_TASK[r.value]


def model_key_for(role: ModelRole | str) -> str:
    """Provider ``models`` lookup key for a role (role-specific model first)."""
    return ModelRole(role).value


def profile_for(
    model: str | None, role: ModelRole | str, tier: ModelTier | None = None
) -> HarnessProfile:
    """Build the HarnessProfile for *(model, role)* (tier inferred unless forced)."""
    r = ModelRole(role)
    t = tier or model_tier(model)
    budget = int(TIER_CONTEXT_CHARS[t] * ROLE_BUDGET_FACTOR[r])
    max_tokens = min(ROLE_MAX_TOKENS[r], TIER_MAX_TOKENS_CAP[t]) if ROLE_MAX_TOKENS[r] else 0
    # Small tiers see a shrunk tool-face (one step down); large tiers keep the
    # role's face ("看不见>拦截").
    face = ROLE_TOOL_FACE[r]
    if t is ModelTier.SMALL:
        if face == "full":
            face = "standard"
        elif face == "standard":
            face = "minimal"
    return HarnessProfile(
        role=r,
        tier=t,
        context_budget_chars=budget,
        max_tokens=max_tokens,
        temperature=ROLE_TEMPERATURE[r],
        tool_face=face,
        prompt_style=_PROMPT_STYLE[t],
    )


def filter_tools_for(
    model: str | None,
    role: ModelRole | str,
    tool_names: list[str],
    *,
    preferred: list[str] | None = None,
    tier: ModelTier | None = None,
) -> list[str]:
    """Apply the model's tool-face: cap the tool list to what this (role, tier)
    should see ("看不见>拦截"). ``preferred`` names are kept first so critical
    tools survive the shrink; order is otherwise preserved. "none" → ``[]``.
    """
    prof = profile_for(model, role, tier=tier)
    limit = FACE_MAX_TOOLS.get(prof.tool_face, FACE_MAX_TOOLS["full"])
    if limit <= 0:
        return []
    if preferred:
        # Preferred names surface first, in the caller's priority order, so
        # critical tools survive the shrink.
        pref = [t for t in preferred if t in tool_names]
        rest = [t for t in tool_names if t not in preferred]
        ordered = pref + rest
    else:
        ordered = list(tool_names)
    return ordered[:limit]
