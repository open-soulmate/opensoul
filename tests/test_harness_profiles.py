"""Offline tests for Harness Profiles + Model Roles (deepagents + continue).

Pure + stubbed — no network, no live server. Verifies the per-(role, tier) harness
knobs (model routing, context budget, generation defaults, tool-face) and their
consumption inside ``ModelRouter.chat`` on the real dispatch path.
"""

from __future__ import annotations

import asyncio

from src.gland.harness_profiles import (
    FACE_MAX_TOOLS,
    ROLE_TO_TASK,
    HarnessProfile,
    ModelRole,
    ModelTier,
    filter_tools_for,
    model_key_for,
    model_tier,
    profile_for,
    resolve_task,
)
from src.gland.router import ModelRouter


def run(coro):
    return asyncio.run(coro)


def _router(models: dict[str, str], monkeypatch):
    """Build a ModelRouter whose HTTP layer is a recording stub (offline).

    The stub is a plain function (a descriptor) so it binds like the real
    ``_call_chat`` and receives the router instance as its first argument.
    """
    r = ModelRouter()
    r.add_provider(name="p", base_url="http://x", models=models, priority=0)
    calls: list[dict] = []

    def stub(
        self_router,
        provider,
        api_key,
        model,
        messages,
        temperature=None,
        max_tokens=None,
        stream=False,
        **kw,
    ):
        calls.append(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    async def async_stub(
        self_router,
        provider,
        api_key,
        model,
        messages,
        temperature=None,
        max_tokens=None,
        stream=False,
        **kw,
    ):
        return stub(
            self_router,
            provider,
            api_key,
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )

    monkeypatch.setattr(ModelRouter, "_call_chat", async_stub)
    return r, calls


class TestRoleModelMapping:
    def test_role_to_task(self):
        assert resolve_task(ModelRole.REASONING) == "chat"
        assert resolve_task(ModelRole.SUMMARIZE) == "completion"
        assert resolve_task(ModelRole.EMBEDDING) == "embedding"
        assert resolve_task(ModelRole.RERANK) == "completion"
        assert resolve_task(ModelRole.CODE) == "code"

    def test_role_str_value_resolves(self):
        assert ModelRole("summarize") is ModelRole.SUMMARIZE
        assert resolve_task("code") == "code"

    def test_model_key_per_role(self):
        assert model_key_for(ModelRole.SUMMARIZE) == "summarize"
        assert model_key_for("rerank") == "rerank"

    def test_all_roles_have_task(self):
        for role in ModelRole:
            assert role.value in ROLE_TO_TASK


class TestModelTier:
    def test_explicit_spec_wins(self):
        assert model_tier("deepseek-r1:latest") is ModelTier.LARGE
        assert model_tier("gpt-4o-mini") is ModelTier.SMALL  # longest spec first

    def test_substring_spec_not_steal_longer_name(self):
        # "gpt-4o" is a spec, but "gpt-4o-mini" must classify SMALL.
        assert model_tier("gpt-4o") is ModelTier.LARGE
        assert model_tier("gpt-4o-mini") is ModelTier.SMALL

    def test_heuristic_markers(self):
        assert model_tier("llama-7b-chat") is ModelTier.SMALL
        assert model_tier("claude-4-opus") is ModelTier.LARGE
        assert model_tier("mystery-model") is ModelTier.MEDIUM

    def test_none_model_medium(self):
        assert model_tier(None) is ModelTier.MEDIUM


class TestHarnessProfile:
    def test_generation_defaults_per_role(self):
        assert profile_for("m", ModelRole.SUMMARIZE).temperature == 0.2
        assert profile_for("m", ModelRole.RERANK).temperature == 0.0
        assert profile_for("m", ModelRole.REASONING).temperature == 0.7

    def test_context_budget_scales_with_tier_and_role(self):
        small = profile_for("gpt-4o-mini", ModelRole.REASONING)
        large = profile_for("gpt-4o", ModelRole.REASONING)
        assert small.context_budget_chars < large.context_budget_chars
        summ = profile_for("gpt-4o", ModelRole.SUMMARIZE)
        rerank = profile_for("gpt-4o", ModelRole.RERANK)
        assert summ.context_budget_chars > rerank.context_budget_chars

    def test_max_tokens_capped_by_tier(self):
        assert profile_for("gpt-4o-mini", ModelRole.CODE).max_tokens == 2048
        assert profile_for("gpt-4o", ModelRole.CODE).max_tokens == 4096

    def test_tool_face_shrinks_for_small_tier(self):
        assert profile_for("gpt-4o", ModelRole.REASONING).tool_face == "full"
        assert profile_for("gpt-4o-mini", ModelRole.REASONING).tool_face == "standard"
        assert profile_for("gpt-4o-mini", ModelRole.CODE).tool_face == "minimal"
        assert profile_for("gpt-4o", ModelRole.SUMMARIZE).tool_face == "none"

    def test_prompt_style_tracks_tier(self):
        assert profile_for("gpt-4o-mini", ModelRole.REASONING).prompt_style == "concise"
        assert profile_for("gpt-4o", ModelRole.REASONING).prompt_style == "detailed"


class TestClampMessages:
    def _prof(self, budget: int) -> HarnessProfile:
        return HarnessProfile(
            role=ModelRole.REASONING,
            tier=ModelTier.MEDIUM,
            context_budget_chars=budget,
            max_tokens=2048,
            temperature=0.7,
            tool_face="full",
        )

    def test_tail_preserving(self):
        msgs = [
            {"role": "user", "content": "a" * 600},
            {"role": "assistant", "content": "b" * 600},
            {"role": "user", "content": "c" * 600},
        ]
        out, truncated = self._prof(1000).clamp_messages(msgs)
        assert truncated is True
        assert len(out) == 1
        assert out[0]["content"] == "c" * 600  # tail kept (user's last words)

    def test_system_preserved_verbatim(self):
        msgs = [
            {"role": "system", "content": "S" * 50},
            {"role": "user", "content": "u" * 5000},
        ]
        out, truncated = self._prof(1000).clamp_messages(msgs)
        assert truncated is True
        assert out[0]["role"] == "system"
        assert out[0]["content"] == "S" * 50

    def test_single_huge_message_head_trimmed(self):
        body = "".join(str(i % 10) for i in range(3000))
        msgs = [{"role": "user", "content": body}]
        out, truncated = self._prof(1000).clamp_messages(msgs)
        assert truncated is True
        assert len(out[0]["content"]) <= 1000
        assert out[0]["content"] == body[-len(out[0]["content"]) :]  # kept the tail

    def test_no_truncation_when_short(self):
        msgs = [{"role": "user", "content": "hi"}]
        out, truncated = self._prof(1000).clamp_messages(msgs)
        assert truncated is False
        assert out == msgs


class TestFilterTools:
    def test_none_role_returns_empty(self):
        tools = ["a", "b", "c"]
        assert filter_tools_for("gpt-4o", ModelRole.SUMMARIZE, tools) == []
        assert FACE_MAX_TOOLS["none"] == 0

    def test_full_face_keeps_all_within_cap(self):
        tools = ["t0", "t1", "t2"]
        assert filter_tools_for("gpt-4o", ModelRole.REASONING, tools) == tools

    def test_caps_and_preferred_order(self):
        tools = [f"t{i}" for i in range(30)]
        preferred = ["t29", "t0"]
        out = filter_tools_for("gpt-4o-mini", ModelRole.REASONING, tools, preferred=preferred)
        assert len(out) == FACE_MAX_TOOLS["standard"]
        assert out[0] == "t29"
        assert out[1] == "t0"

    def test_minimal_face_for_small_code(self):
        tools = [f"t{i}" for i in range(30)]
        out = filter_tools_for("gpt-4o-mini", ModelRole.CODE, tools)
        assert len(out) == FACE_MAX_TOOLS["minimal"]


class TestRouterRoleDispatch:
    def test_role_routes_to_role_model(self, monkeypatch):
        r, calls = _router({"chat": "chat-m", "summarize": "summ-m"}, monkeypatch)
        run(r.chat([{"role": "user", "content": "x"}], role="summarize"))
        assert calls[0]["model"] == "summ-m"

    def test_role_falls_back_to_chat_model(self, monkeypatch):
        r, calls = _router({"chat": "chat-m"}, monkeypatch)
        run(r.chat([{"role": "user", "content": "x"}], role="summarize"))
        assert calls[0]["model"] == "chat-m"

    def test_profile_defaults_applied_when_none(self, monkeypatch):
        r, calls = _router({"chat": "chat-m"}, monkeypatch)
        run(r.chat([{"role": "user", "content": "x"}], role="summarize"))
        assert calls[0]["temperature"] == 0.2  # summarize default
        assert calls[0]["max_tokens"] == 2048

    def test_explicit_params_win_over_profile(self, monkeypatch):
        r, calls = _router({"chat": "chat-m"}, monkeypatch)
        run(
            r.chat(
                [{"role": "user", "content": "x"}], role="summarize", temperature=0.9, max_tokens=99
            )
        )
        assert calls[0]["temperature"] == 0.9
        assert calls[0]["max_tokens"] == 99

    def test_context_clamp_applied(self, monkeypatch):
        r, calls = _router({"chat": "gpt-4o-mini"}, monkeypatch)
        big = "z" * 30000
        run(r.chat([{"role": "user", "content": big}], role="summarize", model="gpt-4o-mini"))
        sent = calls[0]["messages"][0]["content"]
        assert len(sent) < len(big)  # clamped to the SMALL-tier summarize budget
        assert big.endswith(sent)  # tail preserved

    def test_no_role_unchanged_defaults(self, monkeypatch):
        r, calls = _router({"chat": "chat-m"}, monkeypatch)
        run(r.chat([{"role": "user", "content": "x"}]))
        assert calls[0]["temperature"] == 0.7  # legacy default preserved
        assert not any("harness_profile" in t for t in r.get_chain_trace())

    def test_trace_carries_harness_profile(self, monkeypatch):
        r, calls = _router({"chat": "chat-m"}, monkeypatch)
        run(r.chat([{"role": "user", "content": "x"}], role="reasoning"))
        assert any("harness_profile" in t for t in r.get_chain_trace())

    def test_model_key_routing_supercedes_task(self, monkeypatch):
        r, calls = _router({"chat": "chat-m", "rerank": "rr-m"}, monkeypatch)
        run(r.chat([{"role": "user", "content": "x"}], role="rerank"))
        assert calls[0]["model"] == "rr-m"
