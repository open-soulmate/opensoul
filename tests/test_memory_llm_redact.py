"""kilocode MemoryRedact收口测试 — 记忆LLM提取边界源端脱敏（supplement3 #6收口）。

背景：MemoryRedact此前只在store()落库前脱敏——库是干净的，但Phase1提取/Dream
蒸馏的**LLM prompt**里凭据原文仍在出境（MEMORY_MODEL可能是第三方小模型）。
kilocode ports.ts text() 的语义是脱敏发生在进入记忆处理的源端：先过redact再进
digest/提取。本套件锁三条契约：

1. **凭据绝不进记忆蒸馏prompt**：Phase1提取/Phase2整合/Dream蒸馏三条LLM路径的
   prompt里不含凭据原文（只有掩码/标记）。
2. **脱敏必须可见**：redacted_spans在Phase1Result/PipelineResult/DreamResult及
   phase2 meta里显式透出（mem0 §1.1：处理结果不静默）。
3. **脱敏≠洗白**：gatekeeper凭据笔记整体拒绝语义不变——携带凭据类[REDACTED:<type>]
   标记的笔记照旧按secret_detected拒绝（记忆库不是密钥库）。

无live server依赖：tmp_path SQLite + 注入式Fake LLM捕获prompt。
"""

import asyncio
import sqlite3
import time

import pytest

from src.hippo.dream_distiller import DreamDistiller
from src.hippo.extractors.gatekeeper import MemoryGatekeeper
from src.hippo.long_term_memory import LongTermMemoryStore
from src.hippo.memory_pipeline import MemoryPipeline, Phase1Candidate
from src.hippo.memory_redact import redact_message_bodies

# 测试凭据样本（命中immune/moderator凭据类规则，risk>=high）
SECRET_PASSWORD = "SuperSecret12345"  # password_leak: "password: ..." 形态
SECRET_NOTE = f"我的系统配置是 password: {SECRET_PASSWORD}，请记住"
SECRET_SK = "sk-" + "a1b2c3d4e5" * 4 + "f5g6h7"  # openai_api_key: sk-+48位


def run(coro):
    return asyncio.run(coro)


class CaptureLLM:
    """捕获prompt的Fake LLM：返回预置响应（str）。"""

    def __init__(self, response_text: str = "[]"):
        self.response_text = response_text
        self.prompts: list[tuple[str, str]] = []

    async def __call__(self, system_prompt, user_prompt):
        self.prompts.append((system_prompt, user_prompt))
        return self.response_text

    @property
    def all_text(self) -> str:
        return "\n".join(s + "\n" + u for s, u in self.prompts)


@pytest.fixture
def store(tmp_path):
    return LongTermMemoryStore(db_path=str(tmp_path / "ltm.db"), gatekeeper_enabled=True)


# ── 契约2辅助：redact_message_bodies本身 ─────────────────────────


class TestRedactMessageBodies:
    def test_redacts_and_counts(self):
        msgs = [
            {"role": "user", "content": SECRET_NOTE},
            {"role": "assistant", "content": "好的，已记录"},
        ]
        out, n = redact_message_bodies(msgs)
        assert n >= 1
        assert SECRET_PASSWORD not in out[0]["content"]
        assert out[1]["content"] == "好的，已记录"
        # 非破坏性：输入不被就地修改（kilocode text()纯函数语义）
        assert msgs[0]["content"] == SECRET_NOTE

    def test_clean_and_odd_shapes_passthrough(self):
        msgs = [
            {"role": "user", "content": "今天天气不错"},
            {"role": "assistant", "content": [{"type": "text", "text": "parts形态"}]},
            "not-a-dict",
            {"role": "user"},  # 无content
        ]
        out, n = redact_message_bodies(msgs)
        assert n == 0
        assert out[0] is msgs[0]
        assert out[1] is msgs[1]
        assert out[2] == "not-a-dict"
        assert out[3] is msgs[3]

    def test_empty_input(self):
        assert redact_message_bodies(None) == ([], 0)
        assert redact_message_bodies([]) == ([], 0)


# ── 契约1+2：三条LLM路径prompt里无凭据原文 ──────────────────────


class TestDreamPromptRedaction:
    def test_secret_never_reaches_dream_prompt(self, store):
        llm = CaptureLLM("[]")
        d = DreamDistiller(ltm_store=store, llm_call=llm)
        result = run(d.dream(messages=[{"role": "user", "content": SECRET_NOTE}], force=True))
        assert llm.prompts, "Dream LLM必须被调用（否则测试空转）"
        assert SECRET_PASSWORD not in llm.all_text
        assert result.redacted_spans >= 1
        assert result.to_dict()["redacted_spans"] >= 1  # 可见性（mem0 §1.1）

    def test_clean_conversation_prompt_unchanged(self, store):
        llm = CaptureLLM("[]")
        d = DreamDistiller(ltm_store=store, llm_call=llm)
        result = run(
            d.dream(messages=[{"role": "user", "content": "用户偏好深色主题"}], force=True)
        )
        assert "用户偏好深色主题" in llm.all_text
        assert result.redacted_spans == 0

    def test_redaction_happens_before_truncation(self, store):
        """先脱敏后截断：密钥不因token预算截断拦腰漏出半个。"""
        long_note = "x" * 400 + f" password: {SECRET_PASSWORD}"
        llm = CaptureLLM("[]")
        d = DreamDistiller(ltm_store=store, llm_call=llm)
        result = run(d.dream(messages=[{"role": "user", "content": long_note}], force=True))
        assert SECRET_PASSWORD not in llm.all_text
        assert result.redacted_spans >= 1


class TestPipelinePromptRedaction:
    def test_secret_never_reaches_phase1_prompt(self, store):
        llm = CaptureLLM("[]")
        p = MemoryPipeline(ltm_store=store, llm_call=llm)
        res = run(p.extract(messages=[{"role": "user", "content": SECRET_NOTE}]))
        assert llm.prompts, "Phase1 LLM必须被调用（否则测试空转）"
        assert SECRET_PASSWORD not in llm.all_text
        assert res.redacted_spans >= 1
        assert res.to_dict()["redacted_spans"] >= 1

    def test_secret_never_reaches_phase2_prompt_import_mode(self, store):
        """import模式直供候选（可含原始会话摘录/evidence）同样先脱敏再进Phase2。"""
        llm = CaptureLLM("[]")
        p = MemoryPipeline(ltm_store=store, llm_call=llm)
        cands = [
            Phase1Candidate(
                content=f"用户的API key是 {SECRET_SK}",
                evidence=f"原文: {SECRET_SK}",
            )
        ]
        _decisions, meta = run(p.consolidate(cands, use_llm=True))
        assert llm.prompts, "Phase2 LLM必须被调用（否则测试空转）"
        assert SECRET_SK not in llm.all_text
        assert meta.get("redacted_spans", 0) >= 1  # fallback路径meta不丢计数

    def test_run_propagates_redacted_spans(self, store):
        llm = CaptureLLM("[]")
        p = MemoryPipeline(ltm_store=store, llm_call=llm)
        result = run(
            p.run(
                messages=[{"role": "user", "content": SECRET_NOTE}],
                use_llm_phase2=False,
            )
        )
        d = result.to_dict()
        assert d["redacted_spans"] >= 1
        assert SECRET_PASSWORD not in llm.all_text


# ── 契约3：脱敏≠洗白（gatekeeper凭据笔记拒绝语义不变）─────────────


class TestCredentialNoteStillRejected:
    def test_raw_credential_note_rejected(self, store):
        """原始凭据笔记照旧整体拒绝（存量行为回归锁）。"""
        mem = store.store(content=SECRET_NOTE, memory_type="semantic")
        assert mem is None
        assert store.gatekeeper.last_decision.rule == "secret_detected"

    def test_redacted_marker_note_rejected(self, store):
        """携带凭据类[REDACTED:<type>]标记的笔记=凭据笔记，照旧整体拒绝。"""
        mem = store.store(
            content="用户配置了 [REDACTED:password_leak] 用于登录",
            memory_type="semantic",
        )
        assert mem is None
        assert store.gatekeeper.last_decision.rule == "secret_detected"

    def test_benign_note_and_partial_mask_admitted(self, store):
        """非凭据内容不被新规则误伤（email部分掩码无标记→可正常入库）。"""
        mem = store.store(content="用户偏好深色主题", memory_type="semantic")
        assert mem is not None
        mem2 = store.store(content="用户邮箱 a***@example.com 用于通知", memory_type="semantic")
        assert mem2 is not None

    def test_dream_credential_note_end_to_end_skipped(self, store):
        """端到端：脱敏prompt→LLM产出含凭据标记的ADD→gatekeeper拒绝→skipped可见，
        记忆库无凭据笔记（'凭据笔记整体拒绝'穿过新脱敏路径仍成立）。"""
        add_echo = (
            '[{"action": "ADD", "content": "用户配置了 [REDACTED:password_leak] '
            '用于登录", "memory_type": "semantic", "importance": 0.6, '
            '"scope": "user", "durability": "durable", "authority": "descriptive", '
            '"reason": "用户设置了登录密码"}]'
        )
        llm = CaptureLLM(add_echo)
        d = DreamDistiller(ltm_store=store, llm_call=llm)
        result = run(d.dream(messages=[{"role": "user", "content": SECRET_NOTE}], force=True))
        assert result.redacted_spans >= 1
        assert result.applied == 0
        assert result.skipped == 1  # gatekeeper拒绝=skipped可见，不当成功
        assert store.list_memories(include_deleted=False, limit=10) == []
        assert SECRET_PASSWORD not in llm.all_text


def test_gatekeeper_pattern_unit():
    """单元锁：gatekeeper新增的凭据标记规则（无需store）。"""
    g = MemoryGatekeeper()
    hit = g._check_secret("用户配置了 [REDACTED:openai_api_key]")
    assert hit is not None and hit[0] == "secret_detected"
    assert g._check_secret("用户喜欢 [REDACTED_email_掩码] 无此标记形态") is None


# ── 契约1+2扩展：存量记忆块（[[EXISTING]]/Dream现有记忆）prompt脱敏 ──


def seed_raw_memory(store, content: str, memory_id: str = "hist_raw_1") -> None:
    """模拟历史存量行：脱敏功能上线前写入/外部直写的原文记忆（绕过store()的
    落库前脱敏直接写SQLite）——复现"库里可能有原文"的真实威胁模型。"""
    now = time.time()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO memories (memory_id, content, memory_type, importance, tags, "
            "metadata, created_at, last_accessed_at) VALUES (?,?,?,?,?,?,?,?)",
            (memory_id, content, "semantic", 0.5, "[]", "{}", now, now),
        )


class TestExistingMemoryPromptRedaction:
    """22:57轮遗留#2收口：Phase2 [[EXISTING]]/Dream「现有记忆」块出蒸馏prompt前
    逐条先脱敏后截断——历史存量行的凭据原文绝不抵达记忆蒸馏LLM（MEMORY_MODEL
    可能是第三方小模型），span计数显式透出（mem0 §1.1处理必须可见）。"""

    def test_secret_in_existing_memory_never_reaches_phase2_prompt(self, store):
        seed_raw_memory(store, SECRET_NOTE)
        llm = CaptureLLM("[]")
        p = MemoryPipeline(ltm_store=store, llm_call=llm)
        cands = [Phase1Candidate(content="用户偏好深色主题")]
        _decisions, meta = run(p.consolidate(cands, use_llm=True))
        assert llm.prompts, "Phase2 LLM必须被调用（否则测试空转）"
        assert SECRET_PASSWORD not in llm.all_text
        assert meta.get("existing_redacted_spans", 0) >= 1
        assert meta.get("redacted_spans", 0) >= 1

    def test_secret_in_existing_memory_never_reaches_dream_prompt(self, store):
        seed_raw_memory(store, SECRET_NOTE)
        llm = CaptureLLM("[]")
        d = DreamDistiller(ltm_store=store, llm_call=llm)
        result = run(
            d.dream(messages=[{"role": "user", "content": "用户偏好深色主题"}], force=True)
        )
        assert llm.prompts, "Dream LLM必须被调用（否则测试空转）"
        assert SECRET_PASSWORD not in llm.all_text
        assert result.redacted_spans >= 1
        assert result.to_dict()["redacted_spans"] >= 1  # 可见性（mem0 §1.1）

    def test_existing_redaction_before_truncation(self, store):
        """先脱敏后截断：存量记忆的100字token预算截断不能把密钥拦腰漏出半个
        （密钥横跨截断点，修复前截断片段'SuperSec…'会原样进prompt）。"""
        content = "x" * 80 + " password: " + SECRET_PASSWORD  # 密钥起点≈char 91
        seed_raw_memory(store, content)
        llm = CaptureLLM("[]")
        d = DreamDistiller(ltm_store=store, llm_call=llm)
        result = run(
            d.dream(messages=[{"role": "user", "content": "用户偏好深色主题"}], force=True)
        )
        assert SECRET_PASSWORD not in llm.all_text
        assert "SuperSec" not in llm.all_text  # 截断半个密钥也不许漏
        assert result.redacted_spans >= 1

    def test_clean_existing_memory_passes_unchanged(self, store):
        """干净存量记忆原样进prompt（脱敏不误伤），计数为0。"""
        seed_raw_memory(store, "用户偏好深色主题")
        llm = CaptureLLM("[]")
        d = DreamDistiller(ltm_store=store, llm_call=llm)
        result = run(d.dream(messages=[{"role": "user", "content": "今天天气不错"}], force=True))
        assert "用户偏好深色主题" in llm.all_text
        assert result.redacted_spans == 0

    def test_phase2_meta_counts_visible_on_fallback(self, store):
        """fallback路径meta不丢计数：LLM响应不可解析→deterministic降级，
        存量脱敏计数仍显式透出（既有契约同族回归锁）。"""
        seed_raw_memory(store, SECRET_NOTE)
        llm = CaptureLLM("不是JSON")
        p = MemoryPipeline(ltm_store=store, llm_call=llm)
        cands = [Phase1Candidate(content="用户偏好深色主题")]
        _decisions, meta = run(p.consolidate(cands, use_llm=True))
        assert meta.get("phase2") == "deterministic"
        assert meta.get("existing_redacted_spans", 0) >= 1
        assert SECRET_PASSWORD not in llm.all_text
