"""kilocode #9记忆marker读侧测试。

覆盖：sessions_api._decode_memory_marker（marker-meta.ts fromParts语义含sources
回退）+ GET /{session_id}/messages 携带memory_marker字段（"本回复用了记忆"
badge数据源）+ 读路径metadata列自愈迁移 + fork保留metadata（审计标记跟消息走）。
"""

import asyncio
import json
import sqlite3
import uuid

from src.api import sessions_api
from src.trajectory.message_tree import fork_session

# 迁移前老schema（无attachments/parent_message_id/metadata列）
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
        timestamp REAL
    )""",
]

MARKER_JSON = json.dumps(
    {
        "kiloMemory": {
            "type": "recall",
            "bytes": 33,
            "tokens": 12,
            "count": 2,
            "files": ["ltm_1", "mem_2"],
            "items": ["片段A"],
        }
    },
    ensure_ascii=False,
)


def _make_db(tmp_path) -> str:
    db_path = str(tmp_path / "marker_read.db")
    conn = sqlite3.connect(db_path)
    for ddl in OLD_DDL:
        conn.execute(ddl)
    # 模拟acp-proxy写侧probe+ALTER：metadata列由_save_message自愈迁移加上
    conn.execute("ALTER TABLE agent_messages ADD COLUMN metadata TEXT")
    conn.execute(
        "INSERT INTO agent_sessions (id, agent_id, title, created_at, last_activity_at,"
        " message_count, archived) VALUES ('s1', 'a1', 'New Chat', 1000.0, 1000.0, 3, 0)"
    )
    rows = [
        ("s1", "user", "你好", 1000.0, None),
        ("s1", "assistant", "回复", 1001.0, MARKER_JSON),
        ("s1", "user", "继续", 1002.0, None),
    ]
    for sid, role, content, ts, meta in rows:
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp, metadata)"
            " VALUES (?, ?, ?, ?, ?)",
            (sid, role, content, ts, meta),
        )
    conn.commit()
    conn.close()
    return db_path


class TestDecodeMemoryMarker:
    def test_valid_marker_decoded(self):
        out = sessions_api._decode_memory_marker(MARKER_JSON)
        assert out["type"] == "recall"
        assert out["tokens"] == 12
        assert out["count"] == 2
        assert out["files"] == ["ltm_1", "mem_2"]
        assert out["items"] == ["片段A"]

    def test_sources_fallback_when_files_missing(self):
        # kilocode fromParts：sources回退兼容dropped之前的老格式
        raw = json.dumps({"kiloMemory": {"type": "recall", "sources": ["old_1"], "count": 1}})
        out = sessions_api._decode_memory_marker(raw)
        assert out["files"] == ["old_1"]
        assert out["items"] == []

    def test_count_falls_back_to_files_len(self):
        raw = json.dumps({"kiloMemory": {"files": ["a", "b"]}})
        out = sessions_api._decode_memory_marker(raw)
        assert out["count"] == 2
        assert out["type"] == "recall"  # 非startup一律recall

    def test_startup_type_mapped(self):
        raw = json.dumps({"kiloMemory": {"type": "startup", "files": ["f"]}})
        assert sessions_api._decode_memory_marker(raw)["type"] == "startup"

    def test_invalid_json_returns_none(self):
        assert sessions_api._decode_memory_marker("{broken") is None
        assert sessions_api._decode_memory_marker("") is None
        assert sessions_api._decode_memory_marker(None) is None

    def test_non_dict_shapes_return_none(self):
        assert sessions_api._decode_memory_marker("[1,2]") is None
        assert sessions_api._decode_memory_marker('{"kiloMemory": "x"}') is None
        assert sessions_api._decode_memory_marker('{"other": {}}') is None

    def test_non_str_entries_filtered(self):
        raw = json.dumps(
            {"kiloMemory": {"files": ["ok", 5, None], "items": ["i", 7], "tokens": "bad"}}
        )
        out = sessions_api._decode_memory_marker(raw)
        assert out["files"] == ["ok"]
        assert out["items"] == ["i"]
        assert out["tokens"] == 0


class TestReadPathMemoryMarker:
    def _setup(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path)
        monkeypatch.setattr(sessions_api, "_OPENSOUL_DB", db)
        monkeypatch.setattr(sessions_api, "_get_db", lambda: None)
        return db

    def test_messages_carry_memory_marker(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        resp = asyncio.run(sessions_api.get_session_messages("s1", user_id=uuid.uuid4()))
        msgs = resp["messages"]
        assert len(msgs) == 3
        assert msgs[0]["memory_marker"] is None  # 无marker消息不打标
        assert msgs[1]["memory_marker"]["files"] == ["ltm_1", "mem_2"]  # "本回复用了记忆"
        assert msgs[2]["memory_marker"] is None

    def test_read_path_migrates_metadata_column(self, tmp_path, monkeypatch):
        # 老schema库：读路径probe+ALTER自愈（attachments迁移同款）
        db_path = str(tmp_path / "old_no_meta.db")
        conn = sqlite3.connect(db_path)
        for ddl in OLD_DDL:
            conn.execute(ddl)
        conn.execute(
            "INSERT INTO agent_sessions (id, agent_id, title, created_at,"
            " last_activity_at, message_count, archived)"
            " VALUES ('s1', 'a1', 't', 1.0, 1.0, 1, 0)"
        )
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp)"
            " VALUES ('s1', 'user', 'hi', 1.0)"
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(sessions_api, "_OPENSOUL_DB", db_path)
        monkeypatch.setattr(sessions_api, "_get_db", lambda: None)
        resp = asyncio.run(sessions_api.get_session_messages("s1", user_id=uuid.uuid4()))
        assert resp["messages"][0]["memory_marker"] is None
        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_messages)")]
        conn.close()
        assert "metadata" in cols


class TestForkPreservesMarker:
    def test_fork_copies_metadata(self, tmp_path):
        db = _make_db(tmp_path)
        stats = fork_session(db, "s1", 2)  # fork点=assistant消息（带marker）
        assert stats["status"] == "forked"
        assert stats["messages_copied"] == 2
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT metadata FROM agent_messages WHERE session_id = ? ORDER BY id",
            (stats["fork_session_id"],),
        ).fetchall()
        conn.close()
        assert rows[0]["metadata"] is None
        assert json.loads(rows[1]["metadata"])["kiloMemory"]["files"] == ["ltm_1", "mem_2"]

    def test_fork_on_old_schema_migrates(self, tmp_path):
        # fork前库无metadata列：fork路径probe+ALTER自愈
        db = _make_db(tmp_path)
        conn = sqlite3.connect(db)
        conn.execute("ALTER TABLE agent_messages DROP COLUMN metadata")
        conn.commit()
        conn.close()
        stats = fork_session(db, "s1", 1)
        assert stats["status"] == "forked"
        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_messages)")]
        conn.close()
        assert "metadata" in cols
