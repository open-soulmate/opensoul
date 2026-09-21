"""消息树parentId+fork单元测试 — open-webui build_fork_history + pi追加树移植（P0-10）

纯离线：不依赖live server。覆盖：
- ensure_parent_column幂等迁移 + 历史回填（pi追加树线性主干）
- build_branch：open-webui语义（环检测/消息不存在/空会话 fail-visible）
- fork_session：分支复制/parent重链接/确定性主键幂等（agno idempotency_key）
- import_to_db：导入transcript写parentId链
- sessions_api endpoint离线直调：状态码映射 + 读路径parent_id字段
"""

import asyncio
import sqlite3
import sys
import uuid

import pytest

sys.path.insert(0, "/home/climbing/opensoul")

import src.api.sessions_api as sessions_api  # noqa: E402
from src.trajectory.import_formats import (  # noqa: E402
    ConvertedSession,
    convert,
    import_to_db,
)
from src.trajectory.message_tree import (  # noqa: E402
    CycleError,
    EmptyChatError,
    MessageNotFoundError,
    MessageTreeError,
    SessionNotFoundError,
    backfill_linear_parents,
    build_branch,
    ensure_parent_column,
    fork_session,
)

CLAUDE_BASIC = (
    '{"type":"user","sessionId":"s","uuid":"u1","timestamp":"2026-01-01T00:00:00.000Z",'
    '"cwd":"/tmp","message":{"role":"user","content":"do it"}}\n'
    '{"type":"assistant","sessionId":"s","uuid":"u2","timestamp":"2026-01-01T00:00:01.000Z",'
    '"cwd":"/tmp","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_1",'
    '"name":"bash","input":{"command":"ls"}}]}}\n'
    '{"type":"user","sessionId":"s","uuid":"u3","timestamp":"2026-01-01T00:00:02.000Z",'
    '"cwd":"/tmp","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_1",'
    '"content":[{"type":"text","text":"file.txt"}]}]}}'
)

# 迁移前的老schema（无parent_message_id列，与schema_doctor v1一致）
OLD_DDL = [
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
        FOREIGN KEY (session_id) REFERENCES agent_sessions(id)
    )""",
]


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _make_old_db(tmp_path, name="tree.db") -> str:
    db_path = str(tmp_path / name)
    conn = sqlite3.connect(db_path)
    for ddl in OLD_DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    return db_path


def _seed_session(db_path, sid, n, agent_id="soulmate", title="原始会话"):
    """写入session行+n条消息（老schema无parent列），返回消息id列表。"""
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO agent_sessions (id, agent_id, title, created_at, last_activity_at, message_count)"
        " VALUES (?, ?, ?, 1000.0, 1000.0, ?)",
        (sid, agent_id, title, n),
    )
    ids = []
    for i in range(n):
        cur = conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (sid, "user" if i % 2 == 0 else "assistant", f"msg-{sid}-{i}", 1000.0 + i),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return ids


class TestEnsureParentColumn:
    def test_migration_adds_column_and_backfills_chain(self, tmp_path):
        db = _make_old_db(tmp_path)
        ids = _seed_session(db, "s1", 3)
        conn = _connect(db)
        assert ensure_parent_column(conn) is True
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(agent_messages)")]
        assert "parent_message_id" in cols
        rows = conn.execute(
            "SELECT id, parent_message_id FROM agent_messages WHERE session_id='s1' ORDER BY id"
        ).fetchall()
        assert [r["parent_message_id"] for r in rows] == [None, ids[0], ids[1]]
        conn.close()

    def test_migration_idempotent(self, tmp_path):
        db = _make_old_db(tmp_path)
        _seed_session(db, "s1", 2)
        conn = _connect(db)
        assert ensure_parent_column(conn) is True
        assert ensure_parent_column(conn) is False  # 第二次无操作
        conn.close()

    def test_fresh_schema_noop(self, tmp_path):
        db = str(tmp_path / "fresh.db")
        conn = _connect(db)
        for ddl in OLD_DDL:
            conn.execute(ddl)
        conn.execute("ALTER TABLE agent_messages ADD COLUMN parent_message_id TEXT")
        conn.commit()
        assert ensure_parent_column(conn) is False
        conn.close()

    def test_backfill_multi_session_independent(self, tmp_path):
        db = _make_old_db(tmp_path)
        ids_a = _seed_session(db, "sa", 2)
        ids_b = _seed_session(db, "sb", 2)
        conn = _connect(db)
        ensure_parent_column(conn)
        got = {
            r["session_id"]: r["parent_message_id"]
            for r in conn.execute(
                "SELECT session_id, parent_message_id FROM agent_messages WHERE id IN (?, ?)",
                (ids_a[1], ids_b[1]),
            )
        }
        assert got["sa"] == ids_a[0]
        assert got["sb"] == ids_b[0]
        conn.close()

    def test_backfill_does_not_touch_non_null(self, tmp_path):
        db = _make_old_db(tmp_path)
        ids = _seed_session(db, "s1", 2)
        conn = _connect(db)
        ensure_parent_column(conn)
        # 手工给第2条改成指向自己会话外的显式值，backfill不动
        conn.execute("UPDATE agent_messages SET parent_message_id = 99999 WHERE id = ?", (ids[1],))
        conn.commit()
        backfill_linear_parents(conn)
        row = conn.execute(
            "SELECT parent_message_id FROM agent_messages WHERE id = ?", (ids[1],)
        ).fetchone()
        assert row["parent_message_id"] == 99999
        conn.close()


class TestBuildBranch:
    def _msgs(self, specs):
        return [{"id": i, "parent_id": p, "role": "user", "content": str(i)} for i, p in specs]

    def test_linear_chain_root_to_source(self):
        msgs = self._msgs([(1, None), (2, 1), (3, 2)])
        branch = build_branch(msgs, 3)
        assert [m["id"] for m in branch] == [1, 2, 3]

    def test_branch_point_excludes_siblings(self):
        # 树：1根；2、3是1的两个孩子；fork在3只带1→3，不带兄弟2
        msgs = self._msgs([(1, None), (2, 1), (3, 1)])
        branch = build_branch(msgs, 3)
        assert [m["id"] for m in branch] == [1, 3]

    def test_fork_at_root_copies_one(self):
        msgs = self._msgs([(1, None), (2, 1)])
        assert [m["id"] for m in build_branch(msgs, 1)] == [1]

    def test_cycle_raises(self):
        msgs = self._msgs([(1, 2), (2, 1)])
        with pytest.raises(CycleError):
            build_branch(msgs, 1)

    def test_self_loop_raises(self):
        msgs = self._msgs([(1, 1)])
        with pytest.raises(CycleError):
            build_branch(msgs, 1)

    def test_missing_message_raises(self):
        msgs = self._msgs([(1, None), (2, 1)])
        with pytest.raises(MessageNotFoundError):
            build_branch(msgs, 99)

    def test_empty_chat_raises(self):
        with pytest.raises(EmptyChatError):
            build_branch([], 1)

    def test_parent_message_id_alias(self):
        # sqlite Row SELECT alias parent_id 与原始列名 parent_message_id 都能识别
        msgs = [
            {"id": 1, "parent_message_id": None},
            {"id": 2, "parent_message_id": 1},
        ]
        assert [m["id"] for m in build_branch(msgs, 2)] == [1, 2]


class TestForkSession:
    def _seed_tree(self, tmp_path):
        """老schema库：session s1 4条消息（线性）。返回(ids, db_path)"""
        db = _make_old_db(tmp_path)
        ids = _seed_session(db, "s1", 4, agent_id="soulmate", title="测试会话")
        return ids, db

    def test_fork_copies_branch_and_relinks_parents(self, tmp_path):
        ids, db = self._seed_tree(tmp_path)
        stats = fork_session(db, "s1", ids[1])
        assert stats["status"] == "forked"
        assert stats["messages_copied"] == 2
        fork_id = stats["fork_session_id"]
        assert fork_id == f"fork:s1:{ids[1]}"
        conn = _connect(db)
        rows = conn.execute(
            "SELECT id, role, content, parent_message_id FROM agent_messages "
            "WHERE session_id = ? ORDER BY id",
            (fork_id,),
        ).fetchall()
        assert len(rows) == 2
        assert [r["content"] for r in rows] == ["msg-s1-0", "msg-s1-1"]
        # 新会话内parent重新链接：首条NULL，第二条指向新行id
        assert rows[0]["parent_message_id"] is None
        assert rows[1]["parent_message_id"] == rows[0]["id"]
        conn.close()

    def test_fork_session_row_metadata(self, tmp_path):
        ids, db = self._seed_tree(tmp_path)
        stats = fork_session(db, "s1", ids[2])
        conn = _connect(db)
        row = conn.execute(
            "SELECT agent_id, title, message_count FROM agent_sessions WHERE id = ?",
            (stats["fork_session_id"],),
        ).fetchone()
        assert row["agent_id"] == "soulmate"  # 继承源会话agent
        assert row["title"] == "Fork: 测试会话"
        assert row["message_count"] == 3
        conn.close()

    def test_duplicate_fork_idempotent_zero_write(self, tmp_path):
        ids, db = self._seed_tree(tmp_path)
        first = fork_session(db, "s1", ids[1])
        second = fork_session(db, "s1", ids[1])
        assert second["status"] == "duplicate"
        assert second["fork_session_id"] == first["fork_session_id"]
        assert second["messages_copied"] == first["messages_copied"]
        conn = _connect(db)
        n = conn.execute(
            "SELECT COUNT(*) c FROM agent_messages WHERE session_id = ?",
            (first["fork_session_id"],),
        ).fetchone()["c"]
        assert n == first["messages_copied"]  # 没有第二份副本
        conn.close()

    def test_fork_at_root_copies_single_message(self, tmp_path):
        ids, db = self._seed_tree(tmp_path)
        stats = fork_session(db, "s1", ids[0])
        assert stats["messages_copied"] == 1
        conn = _connect(db)
        row = conn.execute(
            "SELECT parent_message_id FROM agent_messages WHERE session_id = ?",
            (stats["fork_session_id"],),
        ).fetchone()
        assert row["parent_message_id"] is None
        conn.close()

    def test_unknown_session_raises(self, tmp_path):
        db = _make_old_db(tmp_path)
        with pytest.raises(SessionNotFoundError):
            fork_session(db, "nope", 1)

    def test_unknown_message_raises(self, tmp_path):
        ids, db = self._seed_tree(tmp_path)
        with pytest.raises(MessageNotFoundError):
            fork_session(db, "s1", 99999)

    def test_cycle_in_db_raises(self, tmp_path):
        ids, db = self._seed_tree(tmp_path)
        conn = _connect(db)
        ensure_parent_column(conn)
        conn.execute(
            "UPDATE agent_messages SET parent_message_id = ? WHERE id = ?", (ids[1], ids[0])
        )
        conn.execute(
            "UPDATE agent_messages SET parent_message_id = ? WHERE id = ?", (ids[0], ids[1])
        )
        conn.commit()
        conn.close()
        with pytest.raises(CycleError):
            fork_session(db, "s1", ids[0])

    def test_old_schema_without_attachments_column(self, tmp_path):
        """fork INSERT引用attachments列——老schema缺列时probe+ALTER自愈"""
        ids, db = self._seed_tree(tmp_path)
        stats = fork_session(db, "s1", ids[1])
        assert stats["status"] == "forked"
        conn = _connect(db)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(agent_messages)")]
        assert "attachments" in cols and "parent_message_id" in cols
        conn.close()

    def test_forked_chain_forkable_again(self, tmp_path):
        """fork产物是独立追加树：可从fork会话再次fork（树语义闭环）"""
        ids, db = self._seed_tree(tmp_path)
        stats = fork_session(db, "s1", ids[2])
        fork_id = stats["fork_session_id"]
        conn = _connect(db)
        fork_rows = conn.execute(
            "SELECT id FROM agent_messages WHERE session_id = ? ORDER BY id", (fork_id,)
        ).fetchall()
        mid = fork_rows[1]["id"]
        conn.close()
        stats2 = fork_session(db, fork_id, mid)
        assert stats2["status"] == "forked"
        assert stats2["messages_copied"] == 2
        assert stats2["fork_session_id"] == f"fork:{fork_id}:{mid}"


class TestImportChain:
    def test_import_writes_parent_chain(self, tmp_path):
        conv = convert(CLAUDE_BASIC)
        assert len(conv.messages) == 3
        db = str(tmp_path / "imp.db")
        stats = import_to_db(conv, db_path=db, agent_id="imported")
        assert stats["status"] == "imported"
        conn = _connect(db)
        rows = conn.execute(
            "SELECT id, parent_message_id FROM agent_messages WHERE session_id = ? ORDER BY id",
            (stats["session_id"],),
        ).fetchall()
        assert rows[0]["parent_message_id"] is None
        assert rows[1]["parent_message_id"] == rows[0]["id"]
        assert rows[2]["parent_message_id"] == rows[1]["id"]
        conn.close()

    def test_import_on_old_schema_db_migrates(self, tmp_path):
        """生产库先于迁移存在：import_to_db必须自愈加列"""
        db = _make_old_db(tmp_path, "old_prod.db")
        conv = convert(CLAUDE_BASIC)
        stats = import_to_db(conv, db_path=db)
        assert stats["status"] == "imported"
        conn = _connect(db)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(agent_messages)")]
        assert "parent_message_id" in cols
        conn.close()


class TestEndpointDirect:
    """sessions_api fork端点+读路径离线直调（monkeypatch DB路径，无live server）"""

    def _setup(self, tmp_path, monkeypatch):
        ids = _seed_session(_make_old_db(tmp_path), "s1", 3)
        monkeypatch.setattr(sessions_api, "_OPENSOUL_DB", str(tmp_path / "tree.db"))
        monkeypatch.setattr(sessions_api, "_get_db", lambda: None)
        return ids

    def _fork(self, sid, message_id):
        return asyncio.run(
            sessions_api.fork_session_at_message(
                sid,
                sessions_api.SessionForkRequest(message_id=message_id),
                user_id=uuid.uuid4(),
            )
        )

    def test_fork_ok(self, tmp_path, monkeypatch):
        ids = self._setup(tmp_path, monkeypatch)
        resp = self._fork("s1", ids[1])
        assert resp["ok"] is True
        assert resp["status"] == "forked"
        assert resp["messages_copied"] == 2

    def test_fork_duplicate(self, tmp_path, monkeypatch):
        ids = self._setup(tmp_path, monkeypatch)
        self._fork("s1", ids[1])
        resp = self._fork("s1", ids[1])
        assert resp["status"] == "duplicate"

    def test_missing_message_id_400(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(sessions_api.HTTPException) as ei:
            self._fork("s1", None)
        assert ei.value.status_code == 400

    def test_unknown_session_404(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(sessions_api.HTTPException) as ei:
            self._fork("nope", 1)
        assert ei.value.status_code == 404

    def test_unknown_message_404(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(sessions_api.HTTPException) as ei:
            self._fork("s1", 99999)
        assert ei.value.status_code == 404

    def test_cycle_409(self, tmp_path, monkeypatch):
        ids = self._setup(tmp_path, monkeypatch)
        db = str(tmp_path / "tree.db")
        conn = _connect(db)
        ensure_parent_column(conn)
        conn.execute(
            "UPDATE agent_messages SET parent_message_id = ? WHERE id = ?", (ids[1], ids[0])
        )
        conn.execute(
            "UPDATE agent_messages SET parent_message_id = ? WHERE id = ?", (ids[0], ids[1])
        )
        conn.commit()
        conn.close()
        with pytest.raises(sessions_api.HTTPException) as ei:
            self._fork("s1", ids[0])
        assert ei.value.status_code == 409

    def test_read_path_carries_parent_id(self, tmp_path, monkeypatch):
        ids = self._setup(tmp_path, monkeypatch)
        resp = asyncio.run(sessions_api.get_session_messages("s1", user_id=uuid.uuid4()))
        msgs = resp["messages"]
        assert len(msgs) == 3
        assert msgs[0]["parent_id"] is None
        assert msgs[1]["parent_id"] == str(ids[0])
        assert msgs[2]["parent_id"] == str(ids[1])
