"""pi切分支摘要LLM summarizer接线测试 — 15:27轮遗留#1销账。

纯离线：LLM调用以monkeypatch _call_llm_router桩替代，不触网。覆盖：
1. resolve_summarizer模式解析：llm/extractive/auto × provider配置 × env覆盖
   × 未知模式fail-closed
2. llm_branch_summarizer：成功路径 / 格式门禁（空输出+缺段落）/ 超时降级
3. generate_branch_summary：summarizer_kind="llm"标注 / 失败降级extractive-
   fallback+error可见（既有失败可见契约在llm路径上的延续）
4. sync SummarizerFn契约在running event loop内的线程桥安全性
5. sessions_api端点接线：request字段/env两条解析路径 + fork端点resolve接线
   + 未知模式400 + 落库summarizer列
"""

import asyncio
import sqlite3
import sys
import uuid
from types import SimpleNamespace

import pytest

sys.path.insert(0, "/home/climbing/opensoul")

import src.api.sessions_api as sessions_api  # noqa: E402
import src.config as src_config  # noqa: E402
import src.trajectory.branch_summary as branch_summary  # noqa: E402
from src.trajectory.branch_summary import (  # noqa: E402
    BRANCH_SUMMARY_PREAMBLE,
    generate_branch_summary,
    get_branch_summaries,
    llm_branch_summarizer,
    resolve_summarizer,
)

VALID_BODY = """## Goal
测试目标

## Constraints & Preferences
- 必须保留原有字段

## Progress
### Done
- [x] read_file

### In Progress
- (none)

### Blocked
- (none)

## Key Decisions
- (none)

## Next Steps
1. 继续
"""

DDL = [
    """CREATE TABLE IF NOT EXISTS agent_sessions (
        id TEXT PRIMARY KEY,
        agent_id TEXT,
        title TEXT,
        created_at REAL,
        last_activity_at REAL,
        message_count INTEGER DEFAULT 0,
        archived INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS agent_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        role TEXT,
        content TEXT,
        timestamp REAL,
        parent_message_id INTEGER
    )""",
]


def _make_db(tmp_path, name="branch_llm.db") -> str:
    db_path = str(tmp_path / name)
    conn = sqlite3.connect(db_path)
    for ddl in DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    return db_path


def _seed(db_path, session_id, contents):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT OR IGNORE INTO agent_sessions "
        "(id, agent_id, title, created_at, last_activity_at, message_count, archived) "
        "VALUES (?, 'a', 't', 1.0, 1.0, 0, 0)",
        (session_id,),
    )
    ids = []
    prev = None
    for i, (role, content) in enumerate(contents):
        cur = conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp, parent_message_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, float(i), prev),
        )
        prev = cur.lastrowid
        ids.append(prev)
    conn.commit()
    conn.close()
    return ids


def _stub_router(monkeypatch, body=VALID_BODY):
    """桩掉gland router调用：记录system/user prompt供断言。"""
    calls: dict[str, str] = {}

    async def fake_call(system_prompt, user_prompt):
        calls["system"] = system_prompt
        calls["user"] = user_prompt
        return body

    monkeypatch.setattr(branch_summary, "_call_llm_router", fake_call)
    return calls


def _no_provider_ns():
    return SimpleNamespace(llm_api_key="", llm_base_url="", llm_model="m", ollama_base_url="")


def _provider_ns():
    return SimpleNamespace(
        llm_api_key="k", llm_base_url="https://llm.example/v1", llm_model="m", ollama_base_url=""
    )


class TestResolveSummarizer:
    def test_explicit_extractive_returns_none(self, monkeypatch):
        monkeypatch.setattr(src_config, "settings", _provider_ns())
        assert resolve_summarizer("extractive") is None

    def test_explicit_llm_returns_kinded_callable(self, monkeypatch):
        monkeypatch.setattr(src_config, "settings", _no_provider_ns())  # 即使无provider
        fn = resolve_summarizer("llm")
        assert fn is llm_branch_summarizer
        assert getattr(fn, "summarizer_kind") == "llm"

    def test_auto_with_provider_selects_llm(self, monkeypatch):
        monkeypatch.setattr(src_config, "settings", _provider_ns())
        monkeypatch.delenv("BRANCH_SUMMARY_SUMMARIZER", raising=False)
        assert resolve_summarizer(None) is llm_branch_summarizer
        assert resolve_summarizer("auto") is llm_branch_summarizer

    def test_auto_without_provider_selects_extractive(self, monkeypatch):
        monkeypatch.setattr(src_config, "settings", _no_provider_ns())
        monkeypatch.delenv("BRANCH_SUMMARY_SUMMARIZER", raising=False)
        assert resolve_summarizer(None) is None
        assert resolve_summarizer("auto") is None

    def test_auto_ollama_only_provider_selects_llm(self, monkeypatch):
        ns = SimpleNamespace(
            llm_api_key="",
            llm_base_url="",
            llm_model="",
            ollama_base_url="http://localhost:11434/v1",
        )
        monkeypatch.setattr(src_config, "settings", ns)
        monkeypatch.delenv("BRANCH_SUMMARY_SUMMARIZER", raising=False)
        assert resolve_summarizer("auto") is llm_branch_summarizer

    def test_env_overrides_auto(self, monkeypatch):
        """env=extractive：provider已配置仍强制离线路径（测试/离线部署通道）"""
        monkeypatch.setattr(src_config, "settings", _provider_ns())
        monkeypatch.setenv("BRANCH_SUMMARY_SUMMARIZER", "extractive")
        assert resolve_summarizer(None) is None

    def test_env_forces_llm_without_provider(self, monkeypatch):
        monkeypatch.setattr(src_config, "settings", _no_provider_ns())
        monkeypatch.setenv("BRANCH_SUMMARY_SUMMARIZER", "llm")
        assert resolve_summarizer(None) is llm_branch_summarizer

    def test_explicit_mode_beats_env(self, monkeypatch):
        monkeypatch.setattr(src_config, "settings", _provider_ns())
        monkeypatch.setenv("BRANCH_SUMMARY_SUMMARIZER", "llm")
        assert resolve_summarizer("extractive") is None  # 显式extractive赢env=llm

    def test_unknown_mode_fail_closed(self, monkeypatch):
        monkeypatch.setattr(src_config, "settings", _provider_ns())
        monkeypatch.delenv("BRANCH_SUMMARY_SUMMARIZER", raising=False)
        with pytest.raises(ValueError, match="unknown summarizer mode"):
            resolve_summarizer("bogus")


class TestLlmBranchSummarizer:
    def test_success_returns_body_and_drives_prompt(self, monkeypatch):
        calls = _stub_router(monkeypatch)
        body = llm_branch_summarizer("[User]: hi", [{"role": "user", "content": "hi"}])
        assert body == VALID_BODY.strip()  # 格式门禁strip首尾空白
        # pi BRANCH_SUMMARY_PROMPT原文是system prompt；序列化文本是user prompt
        assert "Use this EXACT format" in calls["system"]
        assert calls["user"] == "[User]: hi"
        assert llm_branch_summarizer.summarizer_kind == "llm"

    def test_empty_body_rejected(self, monkeypatch):
        _stub_router(monkeypatch, body="   ")
        with pytest.raises(ValueError, match="empty body"):
            llm_branch_summarizer("x", [])

    def test_missing_sections_rejected(self, monkeypatch):
        _stub_router(monkeypatch, body="## Goal\nonly goal")
        with pytest.raises(ValueError, match="missing required sections: ## Progress"):
            llm_branch_summarizer("x", [])

    def test_timeout_raises(self, monkeypatch):
        async def slow(system_prompt, user_prompt):
            await asyncio.sleep(30)
            return VALID_BODY

        monkeypatch.setattr(branch_summary, "_call_llm_router", slow)
        monkeypatch.setattr(branch_summary, "LLM_SUMMARIZER_TIMEOUT", 0.05)
        with pytest.raises(TimeoutError):
            llm_branch_summarizer("x", [])

    def test_provider_error_propagates(self, monkeypatch):
        async def boom(system_prompt, user_prompt):
            raise RuntimeError("AllProvidersFailedError: chain exhausted")

        monkeypatch.setattr(branch_summary, "_call_llm_router", boom)
        with pytest.raises(RuntimeError, match="chain exhausted"):
            llm_branch_summarizer("x", [])

    def test_safe_inside_running_event_loop(self, monkeypatch):
        """sync SummarizerFn线程桥：在running loop内调用不抛'already running'"""
        _stub_router(monkeypatch)

        async def main():
            # 直接在协程体内调用同步summarizer——正是FastAPI端点的真实调用形态
            return llm_branch_summarizer("[User]: hi", [{"role": "user", "content": "hi"}])

        body = asyncio.run(main())
        assert body == VALID_BODY.strip()  # running loop内线程桥路径同样过门禁


class TestGenerateWithLlm:
    def test_llm_kind_recorded(self, monkeypatch):
        _stub_router(monkeypatch)
        result = generate_branch_summary(
            [{"role": "user", "content": "检查配置"}], summarizer=llm_branch_summarizer
        )
        assert result["status"] == "created"
        assert result["summarizer"] == "llm"
        assert result["summary"].startswith(BRANCH_SUMMARY_PREAMBLE)
        assert "测试目标" in result["summary"]  # LLM正文进入摘要
        assert "error" not in result

    def test_llm_failure_degrades_visible(self, monkeypatch):
        async def boom(system_prompt, user_prompt):
            raise RuntimeError("provider down")

        monkeypatch.setattr(branch_summary, "_call_llm_router", boom)
        result = generate_branch_summary(
            [{"role": "user", "content": "任务X"}], summarizer=llm_branch_summarizer
        )
        assert result["summarizer"] == "extractive-fallback"
        assert "provider down" in result["error"]  # 失败可见，绝不静默
        assert result["summary"].startswith(BRANCH_SUMMARY_PREAMBLE)
        assert "任务X" in result["summary"]  # extractive兜底仍产出内容

    def test_format_gate_failure_degrades_visible(self, monkeypatch):
        _stub_router(monkeypatch, body="随便写的没有标准段落")
        result = generate_branch_summary(
            [{"role": "user", "content": "q"}], summarizer=llm_branch_summarizer
        )
        assert result["summarizer"] == "extractive-fallback"
        assert "missing required sections" in result["error"]

    def test_plain_callable_still_labeled_custom(self, monkeypatch):
        """既有契约回归：无summarizer_kind属性的callable标注custom"""
        result = generate_branch_summary(
            [{"role": "user", "content": "x"}], summarizer=lambda t, e: "custom-body"
        )
        assert result["summarizer"] == "custom"


class TestEndpointWiring:
    def _setup(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path)
        ids = _seed(
            db, "s1", [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]
        )
        monkeypatch.setattr(sessions_api, "_OPENSOUL_DB", db)
        monkeypatch.setattr(sessions_api, "_get_db", lambda: None)
        return ids, db

    def test_endpoint_env_extractive_path(self, tmp_path, monkeypatch):
        """env=extractive经端点resolve→summarizer=extractive（离线契约通道）"""
        ids, db = self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("BRANCH_SUMMARY_SUMMARIZER", "extractive")
        resp = asyncio.run(
            sessions_api.create_branch_summary(
                "s1",
                sessions_api.BranchSummaryRequest(from_message_id=ids[3], target_message_id=ids[1]),
                user_id=uuid.uuid4(),
            )
        )
        assert resp["ok"] is True
        assert resp["summarizer"] == "extractive"

    def test_endpoint_explicit_llm_wired_to_stub(self, tmp_path, monkeypatch):
        """request.summarizer="llm"经端点→llm_branch_summarizer→桩router→summarizer=llm"""
        ids, db = self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("BRANCH_SUMMARY_SUMMARIZER", "extractive")  # env在，显式字段应赢
        calls = _stub_router(monkeypatch)
        resp = asyncio.run(
            sessions_api.create_branch_summary(
                "s1",
                sessions_api.BranchSummaryRequest(
                    from_message_id=ids[3], target_message_id=ids[1], summarizer="llm"
                ),
                user_id=uuid.uuid4(),
            )
        )
        assert resp["ok"] is True
        assert resp["summarizer"] == "llm"
        assert "Use this EXACT format" in calls["system"]  # pi prompt真实进入LLM调用
        # 落库summarizer列带llm标注
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = get_branch_summaries(conn, "s1")
        conn.close()
        assert len(rows) == 1
        assert rows[0]["summarizer"] == "llm"
        assert "测试目标" in rows[0]["summary"]

    def test_endpoint_auto_uses_llm_when_provider_configured(self, tmp_path, monkeypatch):
        """默认auto + provider已配置（生产形态）→LLM summarizer接线live于端点路径"""
        ids, db = self._setup(tmp_path, monkeypatch)
        monkeypatch.delenv("BRANCH_SUMMARY_SUMMARIZER", raising=False)
        monkeypatch.setattr(src_config, "settings", _provider_ns())
        _stub_router(monkeypatch)
        resp = asyncio.run(
            sessions_api.create_branch_summary(
                "s1",
                sessions_api.BranchSummaryRequest(from_message_id=ids[3], target_message_id=ids[1]),
                user_id=uuid.uuid4(),
            )
        )
        assert resp["summarizer"] == "llm"  # auto→provider ready→llm

    def test_endpoint_unknown_mode_400(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.delenv("BRANCH_SUMMARY_SUMMARIZER", raising=False)
        with pytest.raises(sessions_api.HTTPException) as ei:
            asyncio.run(
                sessions_api.create_branch_summary(
                    "s1",
                    sessions_api.BranchSummaryRequest(
                        from_message_id=1, target_message_id=0, summarizer="bogus"
                    ),
                    user_id=uuid.uuid4(),
                )
            )
        assert ei.value.status_code == 400
        assert "unknown summarizer mode" in ei.value.detail

    def test_fork_endpoint_resolve_wired(self, tmp_path, monkeypatch):
        """fork端点summarize_fork_context经resolve_summarizer接线（env=llm+桩）"""
        ids, db = self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("BRANCH_SUMMARY_SUMMARIZER", "llm")
        _stub_router(monkeypatch)
        resp = asyncio.run(
            sessions_api.fork_session_at_message(
                "s1",
                sessions_api.SessionForkRequest(message_id=ids[1]),
                user_id=uuid.uuid4(),
            )
        )
        assert resp["status"] == "forked"
        assert resp["branch_summary"]["status"] == "created"
        assert resp["branch_summary"]["summarizer"] == "llm"
        assert resp["branch_summary"]["summary_preview"].startswith(
            "The user explored a different"
        )  # pi preamble契约在llm路径同样成立

    def test_fork_endpoint_llm_failure_fork_not_rolled_back(self, tmp_path, monkeypatch):
        """LLM失败不回滚fork：branch_summary=extractive-fallback+error可见"""
        ids, db = self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("BRANCH_SUMMARY_SUMMARIZER", "llm")

        async def boom(system_prompt, user_prompt):
            raise RuntimeError("provider down")

        monkeypatch.setattr(branch_summary, "_call_llm_router", boom)
        resp = asyncio.run(
            sessions_api.fork_session_at_message(
                "s1",
                sessions_api.SessionForkRequest(message_id=ids[1]),
                user_id=uuid.uuid4(),
            )
        )
        assert resp["status"] == "forked"  # fork已commit不回滚
        bs = resp["branch_summary"]
        assert bs["status"] == "created"
        assert bs["summarizer"] == "extractive-fallback"
        assert "provider down" in bs.get("error", "")
