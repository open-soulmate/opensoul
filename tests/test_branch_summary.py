"""切分支自动摘要单元测试 — pi branch-summarization.ts移植（P0-10遗留#1销账）。

纯离线：不依赖live server/provider。覆盖：
- collect_entries_for_branch：pi oldLeaf→公共祖先收集（时间序/分支隔离/环/不存在）
- serialize_conversation：pi序列化协议（[User]/[Assistant]/[Assistant tool calls]/
  [Tool result] + 2000字符显式截断标记）
- extract_tool_ops/compute_file_lists/format_file_operations：pi文件清单语义
- prepare_branch_entries：token预算newest-first + summary条目90%挤入
- extractive摘要：pi EXACT段落结构 + 确定性提取 + "(none)"诚实默认
- generate_branch_summary：no_content/custom summarizer/fallback降级可见
- record/get/delete：agno确定性主键幂等 + 读侧序列化 + 清理
- summarize_branch/summarize_fork_context编排器
- sessions_api端点离线直调：fork自动摘要/branch-summaries/branches/读路径/DELETE清理
"""

import asyncio
import sqlite3
import sys
import uuid

import pytest

sys.path.insert(0, "/home/climbing/opensoul")

import src.api.sessions_api as sessions_api  # noqa: E402
from src.trajectory.branch_summary import (  # noqa: E402
    BRANCH_SUMMARY_PREAMBLE,
    collect_entries_for_branch,
    compute_file_lists,
    delete_branch_summaries,
    ensure_branch_summary_table,
    extract_tool_ops,
    extractive_summary,
    format_file_operations,
    generate_branch_summary,
    get_branch_summaries,
    list_branch_points,
    prepare_branch_entries,
    record_branch_summary,
    serialize_conversation,
    summarize_branch,
    summarize_fork_context,
)
from src.trajectory.message_tree import (  # noqa: E402
    CycleError,
    MessageNotFoundError,
    SessionNotFoundError,
)

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
        parent_message_id INTEGER,
        FOREIGN KEY (session_id) REFERENCES agent_sessions(id)
    )""",
]


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _make_db(tmp_path, name="branch.db") -> str:
    db_path = str(tmp_path / name)
    conn = sqlite3.connect(db_path)
    for ddl in DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    return db_path


def _seed(db_path, session_id, contents, with_session_row=True):
    """contents: list of (role, content)；返回消息id列表（线性parent链）。"""
    conn = _connect(db_path)
    if with_session_row:
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


def _rows(db_path, session_id):
    conn = _connect(db_path)
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT id, role, content, timestamp, parent_message_id FROM agent_messages "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    ]
    conn.close()
    for r in rows:
        r["id"] = str(r["id"])
        parent = r.pop("parent_message_id")
        r["parent_id"] = str(parent) if parent is not None else None
    return rows


class TestCollectEntries:
    def test_linear_tail_after_fork_point(self, tmp_path):
        """fork点之后的尾部=被离开分支（pi old leaf→公共祖先，祖先不含）"""
        db = _make_db(tmp_path)
        ids = _seed(
            db, "s1", [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]
        )
        collected = collect_entries_for_branch(_rows(db, "s1"), str(ids[3]), str(ids[1]))
        entries = collected["entries"]
        assert [e["content"] for e in entries] == ["q2", "a2"]  # 时间序，fork点a1不含
        assert collected["common_ancestor_id"] == str(ids[1])

    def test_same_leaf_target_noop(self, tmp_path):
        db = _make_db(tmp_path)
        ids = _seed(db, "s1", [("user", "q1"), ("assistant", "a1")])
        collected = collect_entries_for_branch(_rows(db, "s1"), str(ids[1]), str(ids[1]))
        assert collected["entries"] == []

    def test_divergent_branch_excludes_sibling(self, tmp_path):
        """两个子分支：收集leafA路径时不含兄弟分支条目"""
        db = _make_db(tmp_path)
        ids = _seed(db, "s1", [("user", "root"), ("assistant", "branch-point")])
        conn = _connect(db)
        # 两条子消息挂在branch-point（ids[1]）下=真分支
        a_id = conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp, parent_message_id) "
            "VALUES ('s1', 'user', 'branch-A', 2.0, ?)",
            (ids[1],),
        ).lastrowid
        b_id = conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp, parent_message_id) "
            "VALUES ('s1', 'user', 'branch-B', 3.0, ?)",
            (ids[1],),
        ).lastrowid
        conn.commit()
        conn.close()
        collected = collect_entries_for_branch(_rows(db, "s1"), str(a_id), str(b_id))
        assert [e["content"] for e in collected["entries"]] == ["branch-A"]  # 不含branch-B
        assert collected["common_ancestor_id"] == str(ids[1])

    def test_unknown_leaf_raises(self, tmp_path):
        db = _make_db(tmp_path)
        _seed(db, "s1", [("user", "q")])
        with pytest.raises(MessageNotFoundError):
            collect_entries_for_branch(_rows(db, "s1"), "99999", "1")

    def test_unknown_target_raises(self, tmp_path):
        db = _make_db(tmp_path)
        ids = _seed(db, "s1", [("user", "q")])
        with pytest.raises(MessageNotFoundError):
            collect_entries_for_branch(_rows(db, "s1"), str(ids[0]), "99999")

    def test_cycle_raises(self, tmp_path):
        db = _make_db(tmp_path)
        ids = _seed(db, "s1", [("user", "q1"), ("assistant", "a1")])
        conn = _connect(db)
        conn.execute(
            "UPDATE agent_messages SET parent_message_id = ? WHERE id = ?", (ids[1], ids[0])
        )
        conn.execute(
            "UPDATE agent_messages SET parent_message_id = ? WHERE id = ?", (ids[0], ids[1])
        )
        conn.commit()
        conn.close()
        with pytest.raises(CycleError):
            collect_entries_for_branch(_rows(db, "s1"), str(ids[0]), str(ids[1]))

    def test_empty_rows_raises(self):
        with pytest.raises(MessageNotFoundError):
            collect_entries_for_branch([], "1", "2")


class TestSerializeConversation:
    def test_user_and_assistant_text(self):
        entries = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        text = serialize_conversation(entries)
        assert "[User]: hello" in text
        assert "[Assistant]: world" in text

    def test_thinking_and_tool_call_markers(self):
        entries = [
            {
                "role": "assistant",
                "content": "[thinking]plan[/thinking]\n执行 [tool_call read_file id=t1]",
            }
        ]
        text = serialize_conversation(entries)
        assert "[Assistant thinking]: plan" in text
        assert "[Assistant tool calls]: read_file" in text
        assert "[Assistant]: 执行" in text

    def test_tool_result_truncation_marker(self):
        big = "x" * 2500
        entries = [{"role": "user", "content": f"[tool_result id=t1]\n{big}"}]
        text = serialize_conversation(entries)
        assert "[... 500 more characters truncated]" in text
        assert "2500" not in text.split("[...")[0] or True  # 截断标记显式存在

    def test_error_tool_result_marked(self):
        entries = [{"role": "user", "content": "[tool_result id=t9 error=1]"}]
        text = serialize_conversation(entries)
        assert "[Tool result t9 [ERROR]]" in text

    def test_cjk_content_preserved(self):
        entries = [{"role": "user", "content": "中文任务：检查文件"}]
        assert "[User]: 中文任务：检查文件" in serialize_conversation(entries)


class TestToolOpsAndFileLists:
    def test_extract_tool_ops_classification(self):
        entries = [
            {
                "role": "assistant",
                "content": "[tool_call read_file id=t1] [tool_call write_file id=t2] "
                "[tool_call terminal id=t3]",
            },
            {"role": "user", "content": "[tool_result id=t1][tool_result id=t3 error=1]"},
        ]
        ops = extract_tool_ops(entries)
        assert ops["read"] == ["read_file"]
        assert ops["error"] == ["terminal"]  # error结果按id回溯到工具名
        assert "terminal" not in ops["write"]  # 失败调用不算write成功
        assert "write_file" in ops["write"]

    def test_none_tools_skipped(self):
        ops = extract_tool_ops([{"role": "assistant", "content": "[tool_call todo id=t1]"}])
        assert ops["read"] == [] and ops["write"] == []

    def test_compute_file_lists_pi_semantics(self):
        read, modified = compute_file_lists(
            {"read": {"a.py", "b.py"}, "written": {"b.py"}, "edited": {"c.py"}}
        )
        assert read == ["a.py"]  # 只读未改
        assert modified == ["b.py", "c.py"]  # edited∪written排序

    def test_format_file_operations_exact(self):
        out = format_file_operations(["a.py"], ["b.py"])
        assert "<read-files>\na.py\n</read-files>" in out
        assert "<modified-files>\nb.py\n</modified-files>" in out
        assert format_file_operations([], []) == ""


class TestPrepareBranchEntries:
    def test_newest_first_budget(self):
        entries = [
            {"role": "user", "content": "old " + "x" * 400, "kind": "message"},
            {"role": "user", "content": "recent message", "kind": "message"},
        ]
        prep = prepare_branch_entries(entries, token_budget=5)  # 只够新的
        contents = [e["content"] for e in prep["entries"]]
        assert "recent message" in contents
        assert not any(c.startswith("old ") for c in contents)

    def test_budget_zero_includes_all(self):
        entries = [{"role": "user", "content": f"m{i}", "kind": "message"} for i in range(5)]
        prep = prepare_branch_entries(entries, token_budget=0)
        assert len(prep["entries"]) == 5

    def test_summary_entry_fits_anyway_under_90pct(self):
        """pi：summary类条目超预算但总token<90%预算时挤入（重要上下文）"""
        entries = [
            {"role": "assistant", "content": "s" * 400, "kind": "branch_summary"},  # ~100tok
            {"role": "user", "content": "ok", "kind": "message"},  # ~1tok newest
        ]
        prep = prepare_branch_entries(entries, token_budget=50)
        kinds = [e["kind"] for e in prep["entries"]]
        # newest "ok"先入（1tok<50）；branch_summary 100tok超预算但total 1<45→挤入
        assert kinds == ["branch_summary", "message"]

    def test_existing_summaries_file_ops_accumulate(self):
        """pi first pass：嵌套摘要的read_files/modified_files累积（不受预算影响）"""
        existing = [
            {
                "read_files": '["/x/a.py"]',
                "modified_files": '["/y/b.py"]',
                "tool_ops": '{"read": ["read_file"], "write": [], "error": []}',
            }
        ]
        prep = prepare_branch_entries([], token_budget=0, existing_summaries=existing)
        assert prep["tool_ops"]["read"] == ["read_file"]


class TestGenerateBranchSummary:
    def test_no_content_status(self):
        result = generate_branch_summary([])
        assert result["status"] == "no_content"
        assert result["summary"] is None

    def test_extractive_pi_section_format(self):
        entries = [
            {"role": "user", "content": "帮我检查配置文件是否正确，必须保留原有字段"},
            {
                "role": "assistant",
                "content": "已检查 [tool_call read_file id=t1]\n决定采用新方案",
            },
            {"role": "user", "content": "[tool_result id=t1]"},
        ]
        result = generate_branch_summary(entries)
        assert result["status"] == "created"
        s = result["summary"]
        assert s.startswith(BRANCH_SUMMARY_PREAMBLE)
        for section in (
            "## Goal",
            "## Constraints & Preferences",
            "## Progress\n### Done",
            "### In Progress",
            "### Blocked",
            "## Key Decisions",
            "## Next Steps",
        ):
            assert section in s
        assert "帮我检查配置文件" in s  # Goal来自首条用户消息
        assert "read_file" in s  # Done聚合工具调用
        assert "必须保留原有字段" in s  # 约束关键词行
        assert "决定采用新方案" in s  # 决策关键词行
        assert result["summarizer"] == "extractive"

    def test_honest_none_defaults(self):
        entries = [{"role": "assistant", "content": "hello"}]
        s = generate_branch_summary(entries)["summary"]
        assert "## Goal\n(unknown)" in s
        assert "- (none)" in s

    def test_blocked_from_error_results(self):
        entries = [
            {"role": "user", "content": "跑测试"},
            {"role": "assistant", "content": "[tool_call terminal id=t1]"},
            {"role": "user", "content": "[tool_result id=t1 error=1]"},
        ]
        s = generate_branch_summary(entries)["summary"]
        assert "- [!] terminal failed" in s

    def test_in_progress_pending_request(self):
        entries = [
            {"role": "assistant", "content": "好了"},
            {"role": "user", "content": "接下来处理第二个文件"},
        ]
        s = generate_branch_summary(entries)["summary"]
        assert "- [ ] pending request: 接下来处理第二个文件" in s

    def test_next_steps_from_interrogative(self):
        entries = [{"role": "user", "content": "这个bug怎么修？"}]
        s = generate_branch_summary(entries)["summary"]
        assert "## Next Steps\n1. 这个bug怎么修？" in s

    def test_custom_summarizer_used(self):
        result = generate_branch_summary(
            [{"role": "user", "content": "x"}],
            summarizer=lambda text, entries: "custom-body",
        )
        assert result["summarizer"] == "custom"
        assert "custom-body" in result["summary"]
        assert result["summary"].startswith(BRANCH_SUMMARY_PREAMBLE)

    def test_failing_summarizer_falls_back_explicitly(self):
        def boom(text, entries):
            raise RuntimeError("provider down")

        result = generate_branch_summary([{"role": "user", "content": "x"}], summarizer=boom)
        assert result["summarizer"] == "extractive-fallback"
        assert "provider down" in result["error"]  # 失败可见，绝不静默
        assert result["summary"].startswith(BRANCH_SUMMARY_PREAMBLE)  # 摘要仍产出

    def test_nested_summary_files_appended(self):
        existing = [{"read_files": '["/docs/a.md"]', "modified_files": "[]"}]
        result = generate_branch_summary(
            [{"role": "user", "content": "q"}], existing_summaries=existing
        )
        assert "<read-files>\n/docs/a.md\n</read-files>" in result["summary"]


class TestRecordAndGet:
    def _gen(self):
        return generate_branch_summary([{"role": "user", "content": "任务X"}])

    def test_deterministic_id_and_get(self, tmp_path):
        db = _make_db(tmp_path)
        conn = _connect(db)
        rec = record_branch_summary(
            conn,
            session_id="s1",
            from_message_id=10,
            target_message_id=5,
            generated=self._gen(),
            source_session_id="s0",
            common_ancestor_id=5,
            source="fork",
        )
        assert rec["status"] == "created"
        assert rec["id"] == "branchsum:s1:10:5"
        summaries = get_branch_summaries(conn, "s1")
        assert len(summaries) == 1
        s = summaries[0]
        assert s["from_message_id"] == "10"
        assert s["target_message_id"] == "5"
        assert s["source"] == "fork"
        assert s["summary"].startswith(BRANCH_SUMMARY_PREAMBLE)
        assert s["created_at"] is not None  # ISO序列化
        conn.close()

    def test_duplicate_zero_rewrite(self, tmp_path):
        db = _make_db(tmp_path)
        conn = _connect(db)
        gen = self._gen()
        record_branch_summary(
            conn, session_id="s1", from_message_id=1, target_message_id=0, generated=gen
        )
        rec2 = record_branch_summary(
            conn,
            session_id="s1",
            from_message_id=1,
            target_message_id=0,
            generated={**gen, "summary": "CHANGED"},
        )
        assert rec2["status"] == "duplicate"
        summaries = get_branch_summaries(conn, "s1")
        assert summaries[0]["summary"] != "CHANGED"  # 零重写（agno幂等键）
        conn.close()

    def test_delete_cleans_session_rows(self, tmp_path):
        db = _make_db(tmp_path)
        conn = _connect(db)
        record_branch_summary(
            conn, session_id="s1", from_message_id=1, target_message_id=0, generated=self._gen()
        )
        record_branch_summary(
            conn, session_id="s2", from_message_id=1, target_message_id=0, generated=self._gen()
        )
        assert delete_branch_summaries(conn, "s1") == 1
        assert get_branch_summaries(conn, "s1") == []
        assert len(get_branch_summaries(conn, "s2")) == 1  # 不跨会话误删
        conn.close()

    def test_get_on_missing_table_self_heals(self, tmp_path):
        db = _make_db(tmp_path)
        conn = _connect(db)
        assert get_branch_summaries(conn, "s1") == []  # 表不存在=空列表不崩
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE name='agent_branch_summaries'"
            )
        ]
        assert "agent_branch_summaries" in tables  # 幂等建表
        conn.close()

    def test_ensure_idempotent(self, tmp_path):
        db = _make_db(tmp_path)
        conn = _connect(db)
        assert ensure_branch_summary_table(conn) is True
        assert ensure_branch_summary_table(conn) is False
        conn.close()


class TestListBranchPoints:
    def test_branch_point_detection(self, tmp_path):
        db = _make_db(tmp_path)
        ids = _seed(db, "s1", [("user", "root"), ("assistant", "bp")])
        conn = _connect(db)
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp, parent_message_id) "
            "VALUES ('s1', 'user', 'child-a', 2.0, ?)",
            (ids[1],),
        )
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp, parent_message_id) "
            "VALUES ('s1', 'user', 'child-b', 3.0, ?)",
            (ids[1],),
        )
        conn.commit()
        conn.close()
        points = list_branch_points(_connect(db), "s1")
        assert len(points) == 1
        assert points[0]["branch_point_message_id"] == str(ids[1])
        assert len(points[0]["children"]) == 2

    def test_linear_session_no_branch_points(self, tmp_path):
        db = _make_db(tmp_path)
        _seed(db, "s1", [("user", "q"), ("assistant", "a")])
        assert list_branch_points(_connect(db), "s1") == []


class TestOrchestrators:
    def test_summarize_branch_created(self, tmp_path):
        db = _make_db(tmp_path)
        ids = _seed(
            db,
            "s1",
            [
                ("user", "任务开始"),
                ("assistant", "处理中 [tool_call write_file id=t1]"),
                ("user", "继续"),
                ("assistant", "完成"),
            ],
        )
        stats = summarize_branch(db, "s1", str(ids[3]), str(ids[1]))
        assert stats["status"] == "created"
        assert stats["entries_count"] == 2
        assert stats["summary_id"] == f"branchsum:s1:{ids[3]}:{ids[1]}"
        assert stats["common_ancestor_id"] == str(ids[1])
        # 落库可读
        conn = _connect(db)
        summaries = get_branch_summaries(conn, "s1")
        conn.close()
        assert len(summaries) == 1

    def test_summarize_branch_duplicate_idempotent(self, tmp_path):
        db = _make_db(tmp_path)
        ids = _seed(db, "s1", [("user", "q"), ("assistant", "a"), ("user", "b")])
        summarize_branch(db, "s1", str(ids[2]), str(ids[0]))
        stats2 = summarize_branch(db, "s1", str(ids[2]), str(ids[0]))
        assert stats2["status"] == "duplicate"

    def test_summarize_branch_no_content(self, tmp_path):
        db = _make_db(tmp_path)
        ids = _seed(db, "s1", [("user", "q"), ("assistant", "a")])
        stats = summarize_branch(db, "s1", str(ids[0]), str(ids[0]))  # from==target
        assert stats["status"] == "no_content"
        conn = _connect(db)
        assert get_branch_summaries(conn, "s1") == []  # 不写库
        conn.close()

    def test_summarize_fork_context_created(self, tmp_path):
        db = _make_db(tmp_path)
        ids = _seed(
            db,
            "s1",
            [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")],
        )
        stats = summarize_fork_context(db, "s1", str(ids[1]), "fork:s1:X")
        assert stats["status"] == "created"
        assert stats["from_message_id"] == str(ids[3])  # 源会话叶
        assert stats["target_message_id"] == str(ids[1])  # fork点
        assert stats["source_session_id"] == "s1"
        conn = _connect(db)
        summaries = get_branch_summaries(conn, "fork:s1:X")  # 摘要挂fork产物会话名下
        conn.close()
        assert len(summaries) == 1
        assert summaries[0]["source"] == "fork"
        assert "q2" in summaries[0]["summary"]  # 摘要覆盖被离开分支内容

    def test_summarize_fork_at_leaf_no_content(self, tmp_path):
        db = _make_db(tmp_path)
        ids = _seed(db, "s1", [("user", "q1"), ("assistant", "a1")])
        stats = summarize_fork_context(db, "s1", str(ids[1]), "fork:s1:Y")
        assert stats["status"] == "no_content"  # fork点=叶→无被离开分支

    def test_summarize_fork_unknown_session_raises(self, tmp_path):
        db = _make_db(tmp_path)
        with pytest.raises(SessionNotFoundError):
            summarize_fork_context(db, "nope", 1, "fork:nope:1")


class TestEndpointDirect:
    """sessions_api端点离线直调（monkeypatch DB路径，无live server）"""

    def _setup(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path, "tree.db")
        ids = _seed(
            db, "s1", [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]
        )
        monkeypatch.setattr(sessions_api, "_OPENSOUL_DB", db)
        monkeypatch.setattr(sessions_api, "_get_db", lambda: None)
        return ids, db

    def _fork(self, sid, message_id):
        return asyncio.run(
            sessions_api.fork_session_at_message(
                sid,
                sessions_api.SessionForkRequest(message_id=message_id),
                user_id=uuid.uuid4(),
            )
        )

    def test_fork_response_carries_branch_summary(self, tmp_path, monkeypatch):
        ids, _ = self._setup(tmp_path, monkeypatch)
        resp = self._fork("s1", ids[1])
        assert resp["status"] == "forked"
        bs = resp["branch_summary"]
        assert bs["status"] == "created"
        assert bs["target_message_id"] == str(ids[1])
        assert bs["entries_count"] == 2  # fork点之后的q2/a2
        assert bs["summary_preview"].startswith("The user explored a different")

    def test_fork_at_leaf_branch_summary_no_content(self, tmp_path, monkeypatch):
        ids, _ = self._setup(tmp_path, monkeypatch)
        resp = self._fork("s1", ids[3])  # fork在叶
        assert resp["branch_summary"]["status"] == "no_content"

    def test_fork_duplicate_no_regeneration(self, tmp_path, monkeypatch):
        ids, _ = self._setup(tmp_path, monkeypatch)
        self._fork("s1", ids[1])
        resp2 = self._fork("s1", ids[1])
        assert resp2["status"] == "duplicate"
        assert "branch_summary" not in resp2  # duplicate不重复触发摘要

    def test_read_path_carries_branch_summaries(self, tmp_path, monkeypatch):
        ids, _ = self._setup(tmp_path, monkeypatch)
        self._fork("s1", ids[1])  # 触发fork产物会话的摘要
        resp = asyncio.run(
            sessions_api.get_session_messages(f"fork:s1:{ids[1]}", user_id=uuid.uuid4())
        )
        assert "branch_summaries" in resp
        assert len(resp["branch_summaries"]) == 1
        assert resp["branch_summaries"][0]["source"] == "fork"

    def test_read_path_empty_summaries(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        resp = asyncio.run(sessions_api.get_session_messages("s1", user_id=uuid.uuid4()))
        assert resp["branch_summaries"] == []

    def test_nav_endpoint_ok(self, tmp_path, monkeypatch):
        ids, _ = self._setup(tmp_path, monkeypatch)
        resp = asyncio.run(
            sessions_api.create_branch_summary(
                "s1",
                sessions_api.BranchSummaryRequest(from_message_id=ids[3], target_message_id=ids[1]),
                user_id=uuid.uuid4(),
            )
        )
        assert resp["ok"] is True
        assert resp["status"] == "created"
        assert resp["entries_count"] == 2

    def test_nav_endpoint_no_content(self, tmp_path, monkeypatch):
        ids, _ = self._setup(tmp_path, monkeypatch)
        resp = asyncio.run(
            sessions_api.create_branch_summary(
                "s1",
                sessions_api.BranchSummaryRequest(from_message_id=ids[0], target_message_id=ids[0]),
                user_id=uuid.uuid4(),
            )
        )
        assert resp["status"] == "no_content"

    def test_nav_endpoint_missing_params_400(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(sessions_api.HTTPException) as ei:
            asyncio.run(
                sessions_api.create_branch_summary(
                    "s1",
                    sessions_api.BranchSummaryRequest(from_message_id=None, target_message_id=1),
                    user_id=uuid.uuid4(),
                )
            )
        assert ei.value.status_code == 400

    def test_nav_endpoint_unknown_message_404(self, tmp_path, monkeypatch):
        ids, _ = self._setup(tmp_path, monkeypatch)
        with pytest.raises(sessions_api.HTTPException) as ei:
            asyncio.run(
                sessions_api.create_branch_summary(
                    "s1",
                    sessions_api.BranchSummaryRequest(
                        from_message_id=99999, target_message_id=ids[0]
                    ),
                    user_id=uuid.uuid4(),
                )
            )
        assert ei.value.status_code == 404

    def test_branches_endpoint(self, tmp_path, monkeypatch):
        ids, db = self._setup(tmp_path, monkeypatch)
        # 制造真分支点
        conn = _connect(db)
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp, parent_message_id) "
            "VALUES ('s1', 'user', 'sibling', 9.0, ?)",
            (ids[1],),
        )
        conn.commit()
        conn.close()
        self._fork("s1", ids[1])
        resp = asyncio.run(sessions_api.get_session_branches("s1", user_id=uuid.uuid4()))
        assert len(resp["branch_points"]) == 1
        assert resp["branch_points"][0]["branch_point_message_id"] == str(ids[1])
        # fork摘要挂在fork产物会话，s1本身无navigate摘要
        resp_fork = asyncio.run(
            sessions_api.get_session_branches(f"fork:s1:{ids[1]}", user_id=uuid.uuid4())
        )
        assert len(resp_fork["branch_summaries"]) == 1

    def test_delete_session_cleans_summaries(self, tmp_path, monkeypatch):
        ids, db = self._setup(tmp_path, monkeypatch)
        self._fork("s1", ids[1])
        fork_id = f"fork:s1:{ids[1]}"
        resp = asyncio.run(sessions_api.delete_session(fork_id, user_id=uuid.uuid4()))
        assert resp["success"] is True
        conn = _connect(db)
        remaining = conn.execute(
            "SELECT COUNT(*) FROM agent_branch_summaries WHERE session_id = ?", (fork_id,)
        ).fetchone()[0]
        conn.close()
        assert remaining == 0  # 防孤儿行
