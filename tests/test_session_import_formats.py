"""跨agent会话导入单元测试 — goose import_formats移植（P0-10）

纯离线：不依赖live server（conftest的client fixture不使用）。
用例移植自goose源码测试（claude_code.rs/codex.rs/pi.rs/mod.rs #[cfg(test)]）+
OpenSoul canonical schema写入契约 + API endpoint离线直调。
"""

import asyncio
import sqlite3
import sys
import uuid

import pytest

sys.path.insert(0, "/home/climbing/opensoul")

from src.trajectory.import_formats import (  # noqa: E402
    ConvertedSession,
    ImportFormatError,
    convert,
    convert_claude_code,
    convert_codex,
    convert_pi,
    detect_format,
    import_to_db,
    sanitize_unicode_tags,
    summarize_first_line,
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

CODEX_BASIC = (
    '{"timestamp":"2026-05-22T13:37:22Z","type":"session_meta","payload":{"id":"s","cwd":"/w"}}\n'
    '{"timestamp":"2026-05-22T13:37:23Z","type":"response_item","payload":{"type":"message",'
    '"role":"user","content":[{"type":"input_text","text":"run ls"}]}}\n'
    '{"timestamp":"2026-05-22T13:37:24Z","type":"response_item","payload":{"type":"function_call",'
    '"name":"exec_command","arguments":"{\\"cmd\\":\\"ls\\"}","call_id":"call_1"}}\n'
    '{"timestamp":"2026-05-22T13:37:25Z","type":"response_item","payload":{"type":"function_call_output",'
    '"call_id":"call_1","output":"file.txt\\n"}}'
)

PI_BASIC = (
    '{"type":"session","version":3,"cwd":"/p","id":"pi-1","timestamp":"2026-03-01T10:00:00Z"}\n'
    '{"type":"message","timestamp":"2026-03-01T10:00:01Z","message":{"role":"user","content":"hello pi"}}\n'
    '{"type":"message","timestamp":"2026-03-01T10:00:02Z","message":{"role":"assistant",'
    '"content":[{"type":"text","text":"hi there"}],"usage":{"input":10,"output":5,'
    '"cacheRead":100,"cacheWrite":20,"cost":{"total":0.01}}}}\n'
    '{"type":"message","timestamp":"2026-03-01T10:00:03Z","message":{"role":"bashExecution",'
    '"command":"pwd","output":"/p","exitCode":0}}'
)


class TestSanitizeAndSummarize:
    def test_sanitize_strips_tags_block(self):
        # goose utils.rs测试同款：不可见"ABC"标签字符剥除
        assert sanitize_unicode_tags("Hello\U000e0041\U000e0042\U000e0043world") == "Helloworld"

    def test_sanitize_preserves_legitimate_unicode(self):
        s = "Hello world 世界 🌍"
        assert sanitize_unicode_tags(s) == s

    def test_sanitize_only_malicious(self):
        assert sanitize_unicode_tags("\U000e0041\U000e0042") == ""

    def test_summarize_first_line_short(self):
        assert summarize_first_line("first line\nsecond") == "first line"

    def test_summarize_first_line_cjk_char_boundary(self):
        long = "问" * 100
        out = summarize_first_line(long)
        assert len(out) == 80  # 字符级截断，CJK不劈开
        assert out.endswith("...")

    def test_summarize_skips_blank_first_line(self):
        assert summarize_first_line("\n\n  real content  ") == "real content"


class TestDetectFormat:
    def test_codex(self):
        assert detect_format(CODEX_BASIC) == "codex"

    def test_pi_with_version(self):
        assert detect_format(PI_BASIC) == "pi"

    def test_pi_legacy_no_version_has_cwd_id(self):
        legacy = '{"type":"session","cwd":"/p","id":"x"}\n{}'
        assert detect_format(legacy) == "pi"

    def test_claude_first_line(self):
        assert detect_format(CLAUDE_BASIC) == "claude_code"

    def test_claude_fallback_scan_5_lines(self):
        # 首行是噪声，前5行内出现sessionId → Claude Code（mod.rs fallback同款）
        content = "not json\n\n" + CLAUDE_BASIC
        assert detect_format(content) == "claude_code"

    def test_unknown(self):
        assert detect_format("random text\nmore text") == "unknown"

    def test_convert_unknown_raises_visible(self):
        with pytest.raises(ImportFormatError):
            convert("totally not a session file")

    def test_blank_lines_first_skipped(self):
        assert detect_format("\n\n" + CODEX_BASIC) == "codex"


class TestClaudeCodeConverter:
    def test_tool_roundtrip(self):
        # goose claude_code.rs::converts_tool_use_and_result移植
        conv = convert_claude_code(CLAUDE_BASIC)
        assert conv.source_format == "claude_code"
        assert conv.source_id == "s"
        assert len(conv.messages) == 3
        assert conv.messages[0]["role"] == "user"
        assert conv.messages[0]["content"] == "do it"
        assert "[tool_call bash id=toolu_1]" in conv.messages[1]["content"]
        assert '"command": "ls"' in conv.messages[1]["content"]
        assert conv.messages[1]["role"] == "assistant"
        assert conv.messages[2]["role"] == "user"
        assert "[tool_result id=toolu_1]" in conv.messages[2]["content"]
        assert "file.txt" in conv.messages[2]["content"]

    def test_unicode_sanitized_in_tool_result(self):
        # goose测试原文语义：真实Tags Block字符（不可见"ABC"）导入时必须剥除
        import json as _json

        jsonl = _json.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "uuid": "u1",
                "timestamp": "2026-01-01T00:00:00Z",
                "cwd": "/tmp",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": [{"type": "text", "text": "visible\U000e0041世界"}],
                        }
                    ],
                },
            },
            ensure_ascii=False,
        )
        conv = convert_claude_code(jsonl)
        assert "visible世界" in conv.messages[0]["content"]
        assert "\U000e0041" not in conv.messages[0]["content"]

    def test_error_tool_result_marked(self):
        import json as _json

        jsonl = _json.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "uuid": "u1",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "is_error": True,
                            "content": "failed\U000e0041café",
                        }
                    ],
                },
            },
            ensure_ascii=False,
        )
        conv = convert_claude_code(jsonl)
        assert "[tool_result id=t1 error=1]" in conv.messages[0]["content"]
        assert "failedcafé" in conv.messages[0]["content"]

    def test_cache_token_merge(self):
        # goose emits_cache_token_breakdown：input = 7 + 1000 + 5000 = 6007
        jsonl = (
            '{"type":"user","sessionId":"s","uuid":"u1","timestamp":"2026-01-01T00:00:01Z",'
            '"message":{"role":"user","content":"hi"}}\n'
            '{"type":"assistant","sessionId":"s","uuid":"u2","timestamp":"2026-01-01T00:00:02Z",'
            '"message":{"role":"assistant","content":[{"type":"text","text":"hello"}],'
            '"usage":{"input_tokens":7,"cache_creation_input_tokens":1000,'
            '"cache_read_input_tokens":5000,"output_tokens":50}}}'
        )
        conv = convert_claude_code(jsonl)
        assert conv.usage["input_tokens"] == 6007
        assert conv.usage["output_tokens"] == 50
        assert conv.usage["cache_read_input_tokens"] == 5000
        assert conv.usage["cache_write_input_tokens"] == 1000

    def test_skips_unknown_lines(self):
        jsonl = (
            '{"type":"attachment","sessionId":"s","uuid":"u0","timestamp":"2026-01-01T00:00:00Z"}\n'
            '{"type":"queue-operation","sessionId":"s","timestamp":"2026-01-01T00:00:00Z"}\n'
            '{"type":"user","sessionId":"s","uuid":"u1","timestamp":"2026-01-01T00:00:01Z",'
            '"cwd":"/tmp","message":{"role":"user","content":"hi"}}'
        )
        conv = convert_claude_code(jsonl)
        assert len(conv.messages) == 1
        assert conv.skipped_lines == 2  # 噪声行显式计数（mem0失败可见）

    def test_malformed_lines_counted(self):
        jsonl = "garbage line\n" + CLAUDE_BASIC
        conv = convert_claude_code(jsonl)
        assert conv.malformed_lines == 1
        assert len(conv.messages) == 3

    def test_ai_title_wins(self):
        jsonl = (
            '{"type":"user","sessionId":"s","uuid":"u1","timestamp":"2026-01-01T00:00:00Z",'
            '"message":{"role":"user","content":"the prompt"}}\n'
            '{"type":"ai-title","sessionId":"s","aiTitle":"My Session"}'
        )
        conv = convert_claude_code(jsonl)
        assert conv.title == "My Session"

    def test_title_from_first_user_line(self):
        conv = convert_claude_code(CLAUDE_BASIC)
        assert conv.title == "do it"

    def test_thinking_block_serialized(self):
        jsonl = (
            '{"type":"assistant","sessionId":"s","uuid":"a1","timestamp":"2026-01-01T00:00:00Z",'
            '"message":{"role":"assistant","content":[{"type":"thinking","thinking":"hmm",'
            '"signature":"sig"},{"type":"text","text":"answer"}]}}'
        )
        conv = convert_claude_code(jsonl)
        assert "[thinking]\nhmm\n[/thinking]" in conv.messages[0]["content"]
        assert "answer" in conv.messages[0]["content"]

    def test_image_placeholder_and_counted(self):
        jsonl = (
            '{"type":"user","sessionId":"s","uuid":"u1","timestamp":"2026-01-01T00:00:00Z",'
            '"message":{"role":"user","content":[{"type":"image","source":{"data":"AAAA",'
            '"media_type":"image/png"}}]}}'
        )
        conv = convert_claude_code(jsonl)
        assert conv.images_skipped == 1
        assert "[image: image/png]" in conv.messages[0]["content"]

    def test_empty_no_parseable_raises(self):
        with pytest.raises(ImportFormatError):
            convert_claude_code("not json at all")

    def test_timestamps_from_transcript(self):
        conv = convert_claude_code(CLAUDE_BASIC)
        assert conv.created_at == pytest.approx(1767225600.0)  # 2026-01-01T00:00:00Z
        assert conv.updated_at == pytest.approx(1767225602.0)


class TestCodexConverter:
    def test_basic_roundtrip(self):
        # goose codex.rs::converts_function_call_and_output移植
        conv = convert_codex(CODEX_BASIC)
        assert conv.source_format == "codex"
        assert conv.source_id == "s"
        assert conv.working_dir == "/w"
        assert len(conv.messages) == 3
        assert conv.messages[0]["content"] == "run ls"
        assert "[tool_call exec_command id=call_1]" in conv.messages[1]["content"]
        assert '"cmd": "ls"' in conv.messages[1]["content"]
        assert "[tool_result id=call_1]" in conv.messages[2]["content"]
        assert "file.txt" in conv.messages[2]["content"]

    def test_skips_developer_and_system(self):
        # goose skips_developer_and_system_messages + name来自真实提问
        jsonl = (
            '{"timestamp":"2026-05-22T13:37:22.526Z","type":"session_meta","payload":{"id":"abc","cwd":"/tmp"}}\n'
            '{"timestamp":"2026-05-22T13:37:23.000Z","type":"response_item","payload":{"type":"message",'
            '"role":"developer","content":[{"type":"input_text","text":"<huge system prompt>"}]}}\n'
            '{"timestamp":"2026-05-22T13:37:23.946Z","type":"response_item","payload":{"type":"message",'
            '"role":"user","content":[{"type":"input_text","text":"the real question"}]}}'
        )
        conv = convert_codex(jsonl)
        assert len(conv.messages) == 1
        assert conv.title == "the real question"
        assert conv.skipped_lines == 1

    def test_context_blob_not_used_for_title(self):
        # goose first_user_text_skips_context_blobs：blob保留进transcript但不配当名字
        jsonl = (
            '{"timestamp":"2026-05-22T13:37:22Z","type":"session_meta","payload":{"id":"s","cwd":"/w"}}\n'
            '{"timestamp":"2026-05-22T13:37:23Z","type":"response_item","payload":{"type":"message",'
            '"role":"user","content":[{"type":"input_text","text":"<environment_context>\\n'
            '  <cwd>/w</cwd>\\n</environment_context>"}]}}\n'
            '{"timestamp":"2026-05-22T13:37:24Z","type":"response_item","payload":{"type":"message",'
            '"role":"user","content":[{"type":"input_text","text":"actual prompt"}]}}'
        )
        conv = convert_codex(jsonl)
        assert conv.title == "actual prompt"
        assert len(conv.messages) == 2  # blob仍保留

    def test_event_msg_usage_harvest(self):
        jsonl = (
            '{"timestamp":"2026-05-22T13:37:22Z","type":"session_meta","payload":{"id":"s","cwd":"/w"}}\n'
            '{"timestamp":"2026-05-22T13:37:26Z","type":"event_msg","payload":{"type":"agent_message",'
            '"usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":20}}}\n'
            '{"timestamp":"2026-05-22T13:37:23Z","type":"response_item","payload":{"type":"message",'
            '"role":"user","content":[{"type":"input_text","text":"q"}]}}'
        )
        conv = convert_codex(jsonl)
        assert conv.usage["input_tokens"] == 100  # codex口径：input已含cache
        assert conv.usage["cache_read_input_tokens"] == 40
        assert conv.usage["output_tokens"] == 20
        assert len(conv.messages) == 1  # event_msg不进会话

    def test_web_search_pair(self):
        jsonl = (
            '{"timestamp":"2026-05-22T13:37:22Z","type":"session_meta","payload":{"id":"s","cwd":"/w"}}\n'
            '{"timestamp":"2026-05-22T13:37:23Z","type":"response_item","payload":{"type":"web_search_call",'
            '"action":{"query":"rust"},"status":"completed"}}'
        )
        conv = convert_codex(jsonl)
        assert len(conv.messages) == 2
        assert "[tool_call web_search id=codex_websearch_1]" in conv.messages[0]["content"]
        assert '"query": "rust"' in conv.messages[0]["content"]
        assert "[web_search completed]" in conv.messages[1]["content"]

    def test_unicode_sanitized_in_function_output(self):
        import json as _json

        jsonl = "\n".join(
            [
                _json.dumps(
                    {
                        "timestamp": "2026-05-22T13:37:22Z",
                        "type": "session_meta",
                        "payload": {"id": "s", "cwd": "/w"},
                    },
                    ensure_ascii=False,
                ),
                _json.dumps(
                    {
                        "timestamp": "2026-05-22T13:37:23Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "c1",
                            "output": "visible\U000e0041世界",
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        conv = convert_codex(jsonl)
        assert "visible世界" in conv.messages[0]["content"]

    def test_reasoning_preserved(self):
        jsonl = (
            '{"timestamp":"2026-05-22T13:37:22Z","type":"session_meta","payload":{"id":"s","cwd":"/w"}}\n'
            '{"timestamp":"2026-05-22T13:37:23Z","type":"response_item","payload":{"type":"reasoning",'
            '"summary":[{"type":"summary_text","text":"let me think"}]}}'
        )
        conv = convert_codex(jsonl)
        assert conv.messages[0]["role"] == "assistant"
        assert "[reasoning]\nlet me think" in conv.messages[0]["content"]

    def test_empty_raises(self):
        with pytest.raises(ImportFormatError):
            convert_codex("")


class TestPiConverter:
    def test_basic_roundtrip(self):
        conv = convert_pi(PI_BASIC)
        assert conv.source_format == "pi"
        assert conv.source_id == "pi-1"
        assert conv.working_dir == "/p"
        assert conv.messages[0]["content"] == "hello pi"
        assert conv.messages[1]["role"] == "assistant"
        assert conv.messages[1]["content"] == "hi there"

    def test_usage_with_cache_and_cost(self):
        conv = convert_pi(PI_BASIC)
        assert conv.usage["input_tokens"] == 10 + 100 + 20  # pi口径：input+=cacheRead+cacheWrite
        assert conv.usage["output_tokens"] == 5
        assert conv.usage["cache_read_input_tokens"] == 100
        assert conv.usage["cache_write_input_tokens"] == 20
        assert conv.usage["cost"] == pytest.approx(0.01)

    def test_bash_execution_synthesizes_roundtrip(self):
        conv = convert_pi(PI_BASIC)
        bash_req = conv.messages[2]
        bash_resp = conv.messages[3]
        assert "[tool_call bash id=pi_bash_2]" in bash_req["content"]
        assert '"command": "pwd"' in bash_req["content"]
        assert bash_resp["role"] == "user"
        assert "/p" in bash_resp["content"]

    def test_bash_nonzero_exit_prefixed(self):
        jsonl = (
            '{"type":"session","cwd":"/p","id":"x"}\n'
            '{"type":"message","timestamp":"2026-03-01T10:00:03Z","message":{"role":"bashExecution",'
            '"command":"bad","output":"boom","exitCode":2}}'
        )
        conv = convert_pi(jsonl)
        assert "exit 2\nboom" in conv.messages[1]["content"]

    def test_tool_result_error_marker(self):
        jsonl = (
            '{"type":"session","cwd":"/p","id":"x"}\n'
            '{"type":"message","timestamp":"2026-03-01T10:00:03Z","message":{"role":"toolResult",'
            '"toolCallId":"tc9","isError":true,"content":"oops"}}'
        )
        conv = convert_pi(jsonl)
        assert "[tool_result id=tc9 error=1]" in conv.messages[0]["content"]
        assert "oops" in conv.messages[0]["content"]

    def test_missing_header_raises_visible(self):
        with pytest.raises(ImportFormatError):
            convert_pi('{"type":"message","message":{"role":"user","content":"hi"}}')

    def test_empty_raises(self):
        with pytest.raises(ImportFormatError):
            convert_pi("   \n  ")

    def test_summary_roles_preserved(self):
        jsonl = (
            '{"type":"session","cwd":"/p","id":"x"}\n'
            '{"type":"message","timestamp":"2026-03-01T10:00:03Z","message":{"role":"compactionSummary",'
            '"summary":"compacted old turns"}}'
        )
        conv = convert_pi(jsonl)
        assert conv.messages[0]["role"] == "assistant"
        assert "[compactionSummary] compacted old turns" in conv.messages[0]["content"]


def _make_conv(n=2, fmt="claude_code", sid="unit-test"):
    return ConvertedSession(
        source_format=fmt,
        source_id=sid,
        title="unit session",
        working_dir="/tmp",
        created_at=1700000000.0,
        updated_at=1700000060.0,
        messages=[
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"m{i}",
                "timestamp": 1700000000.0 + i,
            }
            for i in range(n)
        ],
    )


class TestImportToDb:
    def test_writes_canonical_rows(self, tmp_path):
        db = str(tmp_path / "t.db")
        conv = convert_claude_code(CLAUDE_BASIC)
        stats = import_to_db(conv, db_path=db, agent_id="claude-code")
        assert stats["status"] == "imported"
        assert stats["messages_written"] == 3
        with sqlite3.connect(db) as c:
            row = c.execute(
                "SELECT agent_id, title, message_count, archived FROM agent_sessions WHERE id=?",
                (stats["session_id"],),
            ).fetchone()
            assert row == ("claude-code", "do it", 3, 0)
            roles = [
                r[0]
                for r in c.execute(
                    "SELECT role FROM agent_messages WHERE session_id=? ORDER BY id",
                    (stats["session_id"],),
                )
            ]
            assert roles == ["user", "assistant", "user"]
            ts_types = [
                type(r[0])
                for r in c.execute(
                    "SELECT timestamp FROM agent_messages WHERE session_id=? ORDER BY id",
                    (stats["session_id"],),
                )
            ]
            assert all(t is float for t in ts_types)

    def test_deterministic_id_and_dedupe(self, tmp_path):
        db = str(tmp_path / "t.db")
        conv = convert_claude_code(CLAUDE_BASIC)
        s1 = import_to_db(conv, db_path=db)
        assert s1["session_id"] == "import:claude_code:s"
        s2 = import_to_db(convert_claude_code(CLAUDE_BASIC), db_path=db)
        assert s2["status"] == "duplicate"
        assert s2["messages_written"] == 0
        with sqlite3.connect(db) as c:
            n_sess = c.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0]
            n_msg = c.execute("SELECT COUNT(*) FROM agent_messages").fetchone()[0]
        assert n_sess == 1
        assert n_msg == 3  # 重复导入零写入

    def test_empty_conv_visible_not_written(self, tmp_path):
        db = str(tmp_path / "t.db")
        conv = _make_conv(n=0)
        stats = import_to_db(conv, db_path=db)
        assert stats["status"] == "empty"
        assert stats["messages_written"] == 0
        with sqlite3.connect(db) as c:
            tbl = c.execute("SELECT name FROM sqlite_master WHERE name='agent_sessions'").fetchone()
        assert tbl is None  # 空会话连schema都不建，零写入

    def test_roles_collapse_to_canonical(self, tmp_path):
        # pi bashExecution → assistant+user对，无第三方role污染canonical表
        db = str(tmp_path / "t.db")
        conv = convert_pi(PI_BASIC)
        import_to_db(conv, db_path=db)
        with sqlite3.connect(db) as c:
            roles = {r[0] for r in c.execute("SELECT DISTINCT role FROM agent_messages")}
        assert roles <= {"user", "assistant"}

    def test_imported_via_all_three_formats(self, tmp_path):
        db = str(tmp_path / "t.db")
        r1 = import_to_db(convert(CLAUDE_BASIC), db_path=db)
        r2 = import_to_db(convert(CODEX_BASIC), db_path=db)
        r3 = import_to_db(convert(PI_BASIC), db_path=db)
        assert [r["status"] for r in (r1, r2, r3)] == ["imported"] * 3
        assert r2["session_id"] == "import:codex:s"
        assert r3["session_id"] == "import:pi:pi-1"


class TestApiEndpointOffline:
    """endpoint逻辑离线直调（FastAPI Depends只在路由层生效，直调走函数体）"""

    def _call(self, body):
        from src.api.sessions_api import SessionImportRequest, import_external_session

        req = SessionImportRequest(**body)
        return asyncio.run(import_external_session(req, user_id=uuid.uuid4()))

    def test_content_happy_path(self, tmp_path, monkeypatch):
        import src.api.sessions_api as sa

        monkeypatch.setattr(sa, "_OPENSOUL_DB", str(tmp_path / "api.db"))
        resp = self._call({"content": CLAUDE_BASIC, "agent_id": "claude-code"})
        assert resp["ok"] is True
        assert resp["status"] == "imported"
        assert resp["messages_written"] == 3
        with sqlite3.connect(str(tmp_path / "api.db")) as c:
            assert (
                c.execute(
                    "SELECT COUNT(*) FROM agent_messages WHERE session_id=?", (resp["session_id"],)
                ).fetchone()[0]
                == 3
            )

    def test_file_path_happy_path(self, tmp_path, monkeypatch):
        import src.api.sessions_api as sa

        monkeypatch.setattr(sa, "_OPENSOUL_DB", str(tmp_path / "api.db"))
        f = tmp_path / "rollout.jsonl"
        f.write_text(CODEX_BASIC, encoding="utf-8")
        resp = self._call({"file_path": str(f)})
        assert resp["source_format"] == "codex"
        assert resp["status"] == "imported"

    def test_dedupe_second_call(self, tmp_path, monkeypatch):
        import src.api.sessions_api as sa

        monkeypatch.setattr(sa, "_OPENSOUL_DB", str(tmp_path / "api.db"))
        self._call({"content": PI_BASIC})
        resp2 = self._call({"content": PI_BASIC})
        assert resp2["status"] == "duplicate"

    def test_missing_both_400(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            self._call({})
        assert ei.value.status_code == 400

    def test_missing_file_404(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            self._call({"file_path": "/nonexistent/nope.jsonl"})
        assert ei.value.status_code == 404

    def test_unsupported_format_400(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            self._call({"content": "hello world not a transcript"})
        assert ei.value.status_code == 400
        assert "unsupported" in ei.value.detail

    def test_pi_error_propagates_as_400(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            self._call({"content": '{"type":"message"}'})
        assert ei.value.status_code == 400
