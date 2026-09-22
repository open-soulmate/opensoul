"""Memory intake redaction tests — kilocode MemoryRedact (#6 采集前脱敏).

Covers: redact_for_memory unit behavior + real write paths
(LongTermMemoryStore.store/update_memory, MemoryStore.add/update):
credentials must never persist in any memory table (memories / memories_fts /
memory_audit), observability metadata is recorded without leaking the secret,
force=True does NOT bypass redaction, and redactor failure is fail-safe
but visible.
"""

import json
import sqlite3

from src.hippo import memory_redact
from src.hippo.long_term_memory import LongTermMemoryStore
from src.hippo.memory_store import MemoryStore

OPENAI_KEY = "sk-" + "a1" * 24  # 48 chars -> openai_api_key + generic_sk overlap
GITHUB_PAT = "ghp_" + "B" * 36
ID_CARD = "110105199001011234"


def make_ltm(tmp_path, enabled: bool = False) -> LongTermMemoryStore:
    return LongTermMemoryStore(db_path=str(tmp_path / "ltm.db"), gatekeeper_enabled=enabled)


class TestRedactForMemory:
    def test_openai_key_masked_and_summary_safe(self):
        text = f"deploy with {OPENAI_KEY} to prod"
        redacted, findings = memory_redact.redact_for_memory(text)
        assert OPENAI_KEY not in redacted
        assert "[REDACTED:" in redacted
        assert "to prod" in redacted  # surrounding text intact
        assert findings
        types = {f["type"] for f in findings}
        assert "openai_api_key" in types
        # summary must never carry the matched secret (safe for logs/metadata)
        for f in findings:
            assert set(f.keys()) == {"type", "risk", "label"}
            assert OPENAI_KEY not in json.dumps(f)

    def test_overlapping_rules_merged_single_mask(self):
        # openai_api_key and generic_sk_api_key both match — overlap merge must
        # yield exactly one mask token and preserve the tail.
        redacted, _ = memory_redact.redact_for_memory(f"key={OPENAI_KEY} end")
        assert redacted.count("[REDACTED:") == 1
        assert redacted.endswith(" end")

    def test_github_pat_masked(self):
        redacted, findings = memory_redact.redact_for_memory(f"github {GITHUB_PAT}")
        assert GITHUB_PAT not in redacted
        assert any(f["type"].startswith("github") for f in findings)

    def test_default_threshold_masks_high_risk_pii(self):
        redacted, findings = memory_redact.redact_for_memory(f"id is {ID_CARD}")
        assert ID_CARD not in redacted
        assert any(f["type"] == "id_card_cn" for f in findings)

    def test_default_threshold_keeps_email_and_phone(self):
        # "记住我的邮箱"是合法记忆：low/medium PII 在默认 high 阈值下保留
        text = "mail me at foo@example.com or call 13800138000"
        redacted, findings = memory_redact.redact_for_memory(text)
        assert redacted == text
        assert findings == []

    def test_min_risk_low_masks_email(self):
        text = "mail me at foo@example.com"
        redacted, findings = memory_redact.redact_for_memory(text, min_risk="low")
        assert "foo@example.com" not in redacted
        assert any(f["type"] == "email" for f in findings)

    def test_clean_text_untouched(self):
        redacted, findings = memory_redact.redact_for_memory("user likes pizza")
        assert redacted == "user likes pizza"
        assert findings == []

    def test_empty_and_non_string_safe(self):
        assert memory_redact.redact_for_memory("") == ("", [])
        assert memory_redact.redact_for_memory(None) == ("", [])


class TestLongTermStoreRedaction:
    def test_store_redacts_all_tables(self, tmp_path):
        store = make_ltm(tmp_path)
        mem = store.store(f"api key is {OPENAI_KEY} for prod", memory_type="semantic")
        assert mem is not None
        assert OPENAI_KEY not in mem.content
        assert "[REDACTED:" in mem.content
        # observability metadata without leaking the secret
        mr = mem.metadata.get("memory_redact")
        assert mr and mr["count"] >= 1 and "openai_api_key" in mr["types"]
        assert OPENAI_KEY not in json.dumps(mem.metadata)

        # every persisted surface is key-free: memories / FTS / audit
        with sqlite3.connect(store.db_path) as conn:
            rows = (
                conn.execute("SELECT content, metadata FROM memories").fetchall()
                + conn.execute("SELECT content, tags FROM memories_fts").fetchall()
                + conn.execute("SELECT new_value FROM memory_audit").fetchall()
            )
        joined = json.dumps(rows, ensure_ascii=False, default=str)
        assert OPENAI_KEY not in joined

    def test_store_force_still_redacts(self, tmp_path):
        store = make_ltm(tmp_path)
        mem = store.store(f"remember {GITHUB_PAT}", force=True)
        assert mem is not None
        assert GITHUB_PAT not in mem.content

    def test_store_clean_text_no_metadata_key(self, tmp_path):
        store = make_ltm(tmp_path)
        mem = store.store("user prefers dark mode", memory_type="semantic")
        assert mem is not None
        assert mem.content == "user prefers dark mode"
        assert "memory_redact" not in mem.metadata

    def test_gatekeeper_sees_raw_but_storage_redacted(self, tmp_path):
        # 语义分工：拒绝规则（secret_detected）必须看到原文才拦得住凭据笔记；
        # 但一旦入库，任何表里都不能有凭据原文。
        store = make_ltm(tmp_path, enabled=True)
        seen = []
        orig = store.gatekeeper.evaluate

        def spy(content, *args, **kwargs):
            seen.append(content)
            return orig(content, *args, **kwargs)

        store.gatekeeper.evaluate = spy
        mem = store.store(f"note {OPENAI_KEY} rotated", memory_type="semantic")
        assert seen, "gatekeeper.evaluate must be called on the write path"
        assert any(OPENAI_KEY in c for c in seen)  # rule sees the truth
        if mem is not None:
            assert OPENAI_KEY not in mem.content  # storage never sees it

    def test_update_memory_redacts(self, tmp_path):
        store = make_ltm(tmp_path)
        mem = store.store("initial note", memory_type="semantic")
        assert mem is not None
        updated = store.update_memory(mem.memory_id, content=f"now with {OPENAI_KEY}")
        assert updated is not None
        assert OPENAI_KEY not in updated["content"]
        with sqlite3.connect(store.db_path) as conn:
            all_text = json.dumps(
                conn.execute("SELECT * FROM memories").fetchall()
                + conn.execute("SELECT * FROM memory_audit").fetchall(),
                default=str,
            )
        assert OPENAI_KEY not in all_text

    def test_redactor_failure_fail_safe_visible(self, tmp_path, monkeypatch, caplog):
        store = make_ltm(tmp_path)

        def boom():
            raise RuntimeError("moderator exploded")

        monkeypatch.setattr(memory_redact, "_get_redactor", boom)
        with caplog.at_level("WARNING", logger="opensoul.hippo.memory_redact"):
            mem = store.store("must not be lost", memory_type="semantic")
        # fail-safe: write proceeds with original text, degradation is logged
        assert mem is not None
        assert mem.content == "must not be lost"
        assert any("MemoryRedact failed" in r.message for r in caplog.records)


class TestShortTermStoreRedaction:
    def test_add_redacts(self):
        sm = MemoryStore()
        mem = sm.add("sess-1", f"token {OPENAI_KEY} leaked")
        assert OPENAI_KEY not in mem.content
        mr = mem.metadata.get("memory_redact")
        assert mr and mr["count"] >= 1

    def test_add_clean_text_no_metadata(self):
        sm = MemoryStore()
        mem = sm.add("sess-1", "hello")
        assert mem.content == "hello"
        assert "memory_redact" not in mem.metadata

    def test_update_redacts(self):
        sm = MemoryStore()
        mem = sm.add("sess-1", "hello")
        sm.update(mem.memory_id, content=f"updated {GITHUB_PAT}")
        assert GITHUB_PAT not in mem.content
