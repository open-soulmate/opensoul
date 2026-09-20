"""Registry同步管线测试 — kilocode discovery.ts移植（fetch/安全计划/origin钉死下载/端到端）

纯函数级+monkeypatch隔离端点测试（marketplace.DB_PATH/SHARED_SKILLS_DIR指向tmp），
不依赖live server、不触网。
"""

import asyncio
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

import src.api.marketplace as marketplace
from src.immune.registry_sync import (
    RegistryEntry,
    RegistryIndex,
    RegistrySyncError,
    _candidate_index_urls,
    download_skill_payload,
    fetch_registry_index,
    plan_registry_entries,
)
from src.immune.skill_guard import read_origin

# ── fixtures ──────────────────────────────────────────────────


def make_registry(base: Path, skills: dict, index: dict | None = None) -> Path:
    """建本地registry：<base>/index.json + 各skill目录"""
    base.mkdir(parents=True, exist_ok=True)
    for name, content in skills.items():
        d = base / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(content, encoding="utf-8")
    idx = (
        index
        if index is not None
        else {
            "skills": [
                {"name": n, "description": f"desc {n}", "version": "1.0.0", "category": "demo"}
                for n in skills
            ]
        }
    )
    (base / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    return base


VALID_MD = "---\nname: demo-echo-skill\ndescription: Echo demo skill\n---\n# demo\n"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """marketplace端点测试用隔离DB + 隔离shared目录"""
    db_path = tmp_path / "marketplace_test.db"
    shared = tmp_path / "shared-skills"
    monkeypatch.setattr(marketplace, "DB_PATH", db_path)
    monkeypatch.setattr(marketplace, "SHARED_SKILLS_DIR", shared)
    return db_path


def add_source(db_path: Path, source_id: str, url: str, type_: str = "custom") -> str:
    db = sqlite3.connect(db_path)
    marketplace.init_marketplace_tables(db)
    db.execute(
        "INSERT INTO skill_sources (id, name, type, url, description, enabled, builtin, auto_sync) "
        "VALUES (?, ?, ?, ?, '', 1, 0, 0)",
        (source_id, source_id, type_, url),
    )
    db.commit()
    db.close()
    return source_id


# ── fetch_registry_index ─────────────────────────────────────


class TestFetchIndex:
    def test_local_registry_list_form(self, tmp_path):
        base = make_registry(tmp_path / "reg", {"skill-a": VALID_MD})
        index = fetch_registry_index(str(base))
        assert index.base_dir == str(base)
        assert len(index.entries) == 1
        assert index.entries[0]["name"] == "skill-a"

    def test_local_registry_dict_form(self, tmp_path):
        base = tmp_path / "reg"
        base.mkdir()
        (base / "index.json").write_text(json.dumps({"skills": [{"name": "x"}]}))
        index = fetch_registry_index(str(base))
        assert index.entries == [{"name": "x"}]

    def test_file_url_form(self, tmp_path):
        base = make_registry(tmp_path / "reg", {"skill-a": VALID_MD})
        index = fetch_registry_index(f"file://{base}")
        assert index.base_dir == str(base)

    def test_missing_index_fetch_failed(self, tmp_path):
        with pytest.raises(RegistrySyncError) as ei:
            fetch_registry_index(str(tmp_path / "no-such-dir"))
        assert ei.value.reason == "fetch_failed"

    def test_invalid_json_invalid_index(self, tmp_path):
        base = tmp_path / "reg"
        base.mkdir()
        (base / "index.json").write_text("not json {")
        with pytest.raises(RegistrySyncError) as ei:
            fetch_registry_index(str(base))
        assert ei.value.reason == "invalid_index"

    def test_invalid_shape_invalid_index(self, tmp_path):
        base = tmp_path / "reg"
        base.mkdir()
        (base / "index.json").write_text(json.dumps({"foo": 1}))
        with pytest.raises(RegistrySyncError) as ei:
            fetch_registry_index(str(base))
        assert ei.value.reason == "invalid_index"

    def test_github_candidates_raw_main_master(self):
        cands = _candidate_index_urls("https://github.com/org/repo", "github")
        assert cands[0] == "https://raw.githubusercontent.com/org/repo/main/index.json"
        assert cands[1] == "https://raw.githubusercontent.com/org/repo/master/index.json"
        assert cands[2] == "https://github.com/org/repo"

    def test_nongithub_single_candidate(self):
        assert _candidate_index_urls("https://clawhub.com/api/v1/skills", "openclaw") == [
            "https://clawhub.com/api/v1/skills"
        ]

    def test_http_failure_fetch_failed(self, monkeypatch):
        def boom(url):
            raise RegistrySyncError("fetch_failed", f"{url}: conn refused")

        monkeypatch.setattr("src.immune.registry_sync._http_get", boom)
        with pytest.raises(RegistrySyncError) as ei:
            fetch_registry_index("https://example.invalid/skills", "custom")
        assert ei.value.reason == "fetch_failed"


# ── plan_registry_entries（逐skill安全计划） ──────────────────


class TestPlanEntries:
    def _plan(self, entries, base_dir="/tmp/reg"):
        index = RegistryIndex(entries=entries, base_url="", base_dir=base_dir)
        return plan_registry_entries(index)

    def test_valid_accepted_with_origin(self):
        accepted, rejected = self._plan([{"name": "good-skill", "description": "d"}])
        assert len(accepted) == 1 and rejected == []
        assert accepted[0].origin == "registry:local:/tmp/reg"
        assert accepted[0].entry.name == "good-skill"

    def test_missing_name_rejected(self):
        accepted, rejected = self._plan([{"description": "no name"}])
        assert accepted == [] and rejected[0]["reason"] == "unsafe_name"

    def test_unsafe_name_rejected(self):
        accepted, rejected = self._plan([{"name": "../evil"}])
        assert accepted == [] and rejected[0]["reason"] == "unsafe_name"

    def test_hidden_name_rejected(self):
        accepted, rejected = self._plan([{"name": ".hidden"}])
        assert accepted == [] and rejected[0]["reason"] == "unsafe_name"

    def test_path_escape_rejected(self):
        accepted, rejected = self._plan([{"name": "sneaky", "path": "a/../../etc"}])
        assert accepted == [] and rejected[0]["reason"] == "path_escape"

    def test_absolute_path_rejected(self):
        accepted, rejected = self._plan([{"name": "sneaky", "files": ["/etc/passwd"]}])
        assert accepted == [] and rejected[0]["reason"] == "path_escape"

    def test_foreign_download_url_origin_mismatch(self):
        accepted, rejected = self._plan(
            [{"name": "thief", "download_url": "https://evil.example.com/skill.zip"}]
        )
        assert accepted == [] and rejected[0]["reason"] == "origin_mismatch"

    def test_same_origin_download_url_accepted(self):
        index = RegistryIndex(
            entries=[{"name": "ok", "download_url": "https://reg.example.com/ok/SKILL.md"}],
            base_url="https://reg.example.com/index.json",
            base_dir="",
        )
        accepted, rejected = plan_registry_entries(index)
        assert len(accepted) == 1 and rejected == []
        assert accepted[0].origin == "registry:https://reg.example.com"

    def test_non_dict_entry_rejected(self):
        accepted, rejected = self._plan(["just-a-string"])
        assert accepted == [] and rejected[0]["reason"] == "invalid_entry"

    def test_partial_accept_mixed_index(self):
        accepted, rejected = self._plan(
            [
                {"name": "good-one"},
                {"name": "../bad"},
                {"name": "good-two", "version": "2.0"},
                {"name": "origin-thief", "download_url": "https://evil.example.com/x"},
            ]
        )
        assert [p.entry.name for p in accepted] == ["good-one", "good-two"]
        assert {r["reason"] for r in rejected} == {"unsafe_name", "origin_mismatch"}


# ── download_skill_payload ────────────────────────────────────


class TestDownloadPayload:
    def test_local_full_dir_copy(self, tmp_path):
        base = make_registry(tmp_path / "reg", {"skill-a": VALID_MD})
        (base / "skill-a" / "refs").mkdir()
        (base / "skill-a" / "refs" / "note.txt").write_text("hello")
        index = fetch_registry_index(str(base))
        accepted, _ = plan_registry_entries(index)
        staging = tmp_path / "staging"
        staging.mkdir()
        payload = download_skill_payload(accepted[0], index, staging)
        assert (payload / "SKILL.md").exists()
        assert (payload / "refs" / "note.txt").read_text() == "hello"

    def test_local_files_subset_copy(self, tmp_path):
        base = make_registry(tmp_path / "reg", {"skill-a": VALID_MD})
        (base / "skill-a" / "extra.bin").write_text("not-in-list")
        (base / "index.json").write_text(
            json.dumps({"skills": [{"name": "skill-a", "files": ["SKILL.md"]}]})
        )
        index = fetch_registry_index(str(base))
        accepted, _ = plan_registry_entries(index)
        staging = tmp_path / "staging"
        staging.mkdir()
        payload = download_skill_payload(accepted[0], index, staging)
        assert (payload / "SKILL.md").exists()
        assert not (payload / "extra.bin").exists()

    def test_missing_registry_dir_download_failed(self, tmp_path):
        index = RegistryIndex(entries=[], base_url="", base_dir=str(tmp_path / "reg"))
        planned_entry = RegistryEntry(name="ghost", path="ghost")
        staging = tmp_path / "staging"
        staging.mkdir()
        from src.immune.registry_sync import PlannedEntry

        with pytest.raises(RegistrySyncError) as ei:
            download_skill_payload(PlannedEntry(entry=planned_entry, origin="x"), index, staging)
        assert ei.value.reason == "download_failed"

    def test_missing_skill_md_download_failed(self, tmp_path):
        base = tmp_path / "reg" / "no-md"
        base.mkdir(parents=True)
        (tmp_path / "reg" / "index.json").write_text(json.dumps({"skills": [{"name": "no-md"}]}))
        index = fetch_registry_index(str(tmp_path / "reg"))
        accepted, _ = plan_registry_entries(index)
        staging = tmp_path / "staging"
        staging.mkdir()
        with pytest.raises(RegistrySyncError) as ei:
            download_skill_payload(accepted[0], index, staging)
        assert ei.value.reason == "download_failed"

    def test_remote_download_pinned_files(self, tmp_path, monkeypatch):
        fetched = {}

        def fake_get(url):
            fetched[url] = True
            return b"---\nname: remote-skill\ndescription: r\n---\n# r\n"

        monkeypatch.setattr("src.immune.registry_sync._http_get", fake_get)
        index = RegistryIndex(
            entries=[{"name": "remote-skill", "files": ["SKILL.md"]}],
            base_url="https://reg.example.com/org/repo/main/index.json",
            base_dir="",
        )
        accepted, _ = plan_registry_entries(index)
        staging = tmp_path / "staging"
        staging.mkdir()
        payload = download_skill_payload(accepted[0], index, staging)
        assert (payload / "SKILL.md").exists()
        assert list(fetched) == ["https://reg.example.com/org/repo/main/remote-skill/SKILL.md"]


# ── 端到端：registry→plan→download→promote_staging ────────────


class TestEndToEndPipeline:
    def test_full_pipeline_installs_with_origin_manifest(self, tmp_path):
        reg = make_registry(tmp_path / "reg", {"demo-echo-skill": VALID_MD})
        live = tmp_path / "live"
        index = fetch_registry_index(str(reg))
        accepted, rejected = plan_registry_entries(index)
        assert rejected == []
        staging = tmp_path / "staging"
        staging.mkdir()
        payload = download_skill_payload(accepted[0], index, staging)
        from src.immune.skill_guard import promote_staging

        result = promote_staging(
            payload, live, origin=accepted[0].origin, source_type="registry:custom"
        )
        assert result.success and result.swapped
        origin_rec = read_origin(live / "demo-echo-skill")
        assert origin_rec is not None
        assert origin_rec.origin == f"registry:local:{reg}"
        assert origin_rec.source_type == "registry:custom"

    def test_second_registry_same_skill_origin_mismatch(self, tmp_path):
        """换源拒绝：registry-B安装同名skill→promote origin_mismatch，live不被污染"""
        reg_a = make_registry(tmp_path / "rega", {"demo-echo-skill": VALID_MD})
        reg_b = make_registry(tmp_path / "regb", {"demo-echo-skill": VALID_MD + "\nB version\n"})
        live = tmp_path / "live"
        from src.immune.skill_guard import promote_staging

        for reg, expect_ok in [(reg_a, True), (reg_b, False)]:
            index = fetch_registry_index(str(reg))
            accepted, _ = plan_registry_entries(index)
            staging = tmp_path / f"staging-{reg.name}"
            staging.mkdir()
            payload = download_skill_payload(accepted[0], index, staging)
            result = promote_staging(
                payload, live, origin=accepted[0].origin, source_type="registry:custom"
            )
            assert result.success is expect_ok, result.errors
        # live仍是registry-A版本
        assert "B version" not in (live / "demo-echo-skill" / "SKILL.md").read_text()

    def test_force_allows_origin_change(self, tmp_path):
        reg_a = make_registry(tmp_path / "rega", {"demo-echo-skill": VALID_MD})
        reg_b = make_registry(tmp_path / "regb", {"demo-echo-skill": VALID_MD + "\nB version\n"})
        live = tmp_path / "live"
        from src.immune.skill_guard import promote_staging

        for reg, force in [(reg_a, False), (reg_b, True)]:
            index = fetch_registry_index(str(reg))
            accepted, _ = plan_registry_entries(index)
            staging = tmp_path / f"staging-{reg.name}"
            staging.mkdir()
            payload = download_skill_payload(accepted[0], index, staging)
            result = promote_staging(
                payload, live, origin=accepted[0].origin, source_type="registry:custom", force=force
            )
            assert result.success, result.errors
        assert "B version" in (live / "demo-echo-skill" / "SKILL.md").read_text()
        assert read_origin(live / "demo-echo-skill").origin == f"registry:local:{reg_b}"


# ── marketplace端点级（monkeypatch隔离DB） ────────────────────


class TestMarketplaceSyncEndpoint:
    def test_sync_happy_path(self, tmp_db, tmp_path):
        reg = make_registry(tmp_path / "reg", {"skill-a": VALID_MD})
        sid = add_source(tmp_db, "src-test-1", str(reg))
        resp = asyncio.run(marketplace.sync_skill_source(sid, user_id=uuid.uuid4()))
        assert resp["success"] is True
        assert resp["accepted"] == 1 and resp["rejected"] == []
        assert resp["skills"] == ["skill-a"]
        db = sqlite3.connect(tmp_db)
        row = db.execute(
            "SELECT skill_count, last_sync_error FROM skill_sources WHERE id=?", (sid,)
        ).fetchone()
        assert row == (1, None)
        skill_row = db.execute(
            "SELECT name, origin, security_status FROM marketplace_skills WHERE source_id=?", (sid,)
        ).fetchone()
        assert skill_row[0] == "skill-a"
        assert skill_row[1] == f"registry:local:{reg}"
        assert skill_row[2] == "accepted"
        db.close()

    def test_sync_rejects_malicious_entries_visible(self, tmp_db, tmp_path):
        idx = {
            "skills": [
                {"name": "good-skill"},
                {"name": "../evil"},
                {"name": "thief", "download_url": "https://evil.example.com/x"},
            ]
        }
        reg = make_registry(tmp_path / "reg", {"good-skill": VALID_MD}, index=idx)
        sid = add_source(tmp_db, "src-test-2", str(reg))
        resp = asyncio.run(marketplace.sync_skill_source(sid, user_id=uuid.uuid4()))
        assert resp["success"] is True
        assert resp["accepted"] == 1
        assert {r["reason"] for r in resp["rejected"]} == {"unsafe_name", "origin_mismatch"}
        db = sqlite3.connect(tmp_db)
        names = [
            r[0]
            for r in db.execute(
                "SELECT name FROM marketplace_skills WHERE source_id=?", (sid,)
            ).fetchall()
        ]
        db.close()
        assert names == ["good-skill"]  # 恶意条目未入库

    def test_sync_failure_visible_not_silent(self, tmp_db, tmp_path):
        sid = add_source(tmp_db, "src-test-3", str(tmp_path / "does-not-exist"))
        resp = asyncio.run(marketplace.sync_skill_source(sid, user_id=uuid.uuid4()))
        assert resp["success"] is False
        assert resp["error"]["reason"] == "fetch_failed"
        db = sqlite3.connect(tmp_db)
        err = db.execute("SELECT last_sync_error FROM skill_sources WHERE id=?", (sid,)).fetchone()[
            0
        ]
        db.close()
        assert err and err.startswith("fetch_failed")  # 失败落库可见，非静默假成功

    def test_sync_updates_do_not_reset_installed_flag(self, tmp_db, tmp_path):
        reg = make_registry(tmp_path / "reg", {"skill-a": VALID_MD})
        sid = add_source(tmp_db, "src-test-4", str(reg))
        uid = uuid.uuid4()
        asyncio.run(marketplace.sync_skill_source(sid, user_id=uid))
        db = sqlite3.connect(tmp_db)
        db.execute("UPDATE marketplace_skills SET installed=1 WHERE source_id=?", (sid,))
        db.commit()
        db.close()
        asyncio.run(marketplace.sync_skill_source(sid, user_id=uid))  # 二次同步
        db = sqlite3.connect(tmp_db)
        installed = db.execute(
            "SELECT installed FROM marketplace_skills WHERE source_id=?", (sid,)
        ).fetchone()[0]
        db.close()
        assert installed == 1  # ON CONFLICT DO UPDATE保留installed

    def test_get_synced_skills_mapping_correct(self, tmp_db, tmp_path):
        """回归上轮映射bug：name≠description，且不再IndexError"""
        reg = make_registry(tmp_path / "reg", {"skill-a": VALID_MD})
        sid = add_source(tmp_db, "src-test-5", str(reg))
        uid = uuid.uuid4()
        asyncio.run(marketplace.sync_skill_source(sid, user_id=uid))
        resp = asyncio.run(marketplace.get_synced_skills(user_id=uid))
        assert resp["total"] == 1
        s = resp["skills"][0]
        assert s["name"] == "skill-a"
        assert s["description"] == "desc skill-a"
        assert s["version"] == "1.0.0"
        assert s["category"] == "demo"
        assert s["source_id"] == sid
        assert s["origin"] == f"registry:local:{reg}"
        assert s["security_status"] == "accepted"
        assert s["installed"] is False


class TestMarketplaceInstallEndpoint:
    def _sync(self, reg: Path, sid: str) -> dict:
        add_source(marketplace.DB_PATH, sid, str(reg))
        return asyncio.run(marketplace.sync_skill_source(sid, user_id=uuid.uuid4()))

    def test_install_full_pipeline(self, tmp_db, tmp_path):
        reg = make_registry(tmp_path / "reg", {"demo-echo-skill": VALID_MD})
        self._sync(reg, "src-inst-1")
        resp = asyncio.run(
            marketplace.marketplace_install_skill(
                "src-inst-1", "demo-echo-skill", user_id=uuid.uuid4()
            )
        )
        assert resp["success"] is True
        assert resp["swapped"] is True
        assert resp["origin"] == f"registry:local:{reg}"
        live_skill = marketplace.SHARED_SKILLS_DIR / "demo-echo-skill"
        assert (live_skill / "SKILL.md").exists()
        origin_rec = read_origin(live_skill)
        assert origin_rec.origin == f"registry:local:{reg}"
        assert origin_rec.source_type == "registry:custom"
        # marketplace_skills installed标志+origin回写
        db = sqlite3.connect(marketplace.DB_PATH)
        row = db.execute(
            "SELECT installed, origin FROM marketplace_skills WHERE source_id='src-inst-1'"
        ).fetchone()
        db.close()
        assert row == (1, f"registry:local:{reg}")

    def test_install_skipped_when_identical(self, tmp_db, tmp_path):
        reg = make_registry(tmp_path / "reg", {"demo-echo-skill": VALID_MD})
        self._sync(reg, "src-inst-2")
        uid = uuid.uuid4()
        first = asyncio.run(
            marketplace.marketplace_install_skill("src-inst-2", "demo-echo-skill", user_id=uid)
        )
        second = asyncio.run(
            marketplace.marketplace_install_skill("src-inst-2", "demo-echo-skill", user_id=uid)
        )
        assert first["swapped"] is True
        assert second["success"] is True and second["skipped"] is True  # 指纹一致跳过

    def test_install_swap_on_registry_update(self, tmp_db, tmp_path):
        reg = make_registry(tmp_path / "reg", {"demo-echo-skill": VALID_MD})
        self._sync(reg, "src-inst-3")
        uid = uuid.uuid4()
        asyncio.run(
            marketplace.marketplace_install_skill("src-inst-3", "demo-echo-skill", user_id=uid)
        )
        # registry内容更新→重新安装=真实swap
        (reg / "demo-echo-skill" / "SKILL.md").write_text(VALID_MD + "\nupdated v2\n")
        resp = asyncio.run(
            marketplace.marketplace_install_skill("src-inst-3", "demo-echo-skill", user_id=uid)
        )
        assert resp["success"] is True and resp["swapped"] is True
        assert (
            "updated v2"
            in (marketplace.SHARED_SKILLS_DIR / "demo-echo-skill" / "SKILL.md").read_text()
        )

    def test_install_cross_registry_origin_mismatch_rejected(self, tmp_db, tmp_path):
        """换源拒绝：registry-B同名skill安装→origin_mismatch，live不被污染；force=true放行"""
        reg_a = make_registry(tmp_path / "rega", {"demo-echo-skill": VALID_MD})
        reg_b = make_registry(tmp_path / "regb", {"demo-echo-skill": VALID_MD + "\nB evil\n"})
        self._sync(reg_a, "src-inst-4a")
        self._sync(reg_b, "src-inst-4b")
        uid = uuid.uuid4()
        ok = asyncio.run(
            marketplace.marketplace_install_skill("src-inst-4a", "demo-echo-skill", user_id=uid)
        )
        assert ok["success"] is True
        rejected = asyncio.run(
            marketplace.marketplace_install_skill("src-inst-4b", "demo-echo-skill", user_id=uid)
        )
        assert rejected["success"] is False
        assert "origin_mismatch" in rejected["error"]
        assert (
            "B evil"
            not in (marketplace.SHARED_SKILLS_DIR / "demo-echo-skill" / "SKILL.md").read_text()
        )  # live未被换源污染
        forced = asyncio.run(
            marketplace.marketplace_install_skill(
                "src-inst-4b",
                "demo-echo-skill",
                body=marketplace.MarketplaceInstallRequest(force=True),
                user_id=uid,
            )
        )
        assert forced["success"] is True
        assert (
            "B evil" in (marketplace.SHARED_SKILLS_DIR / "demo-echo-skill" / "SKILL.md").read_text()
        )

    def test_install_registry_tamper_detected(self, tmp_db, tmp_path):
        """入库后registry里skill被移除→安装时安全计划重跑拒绝（不信入库快照）"""
        reg = make_registry(tmp_path / "reg", {"demo-echo-skill": VALID_MD})
        self._sync(reg, "src-inst-5")
        import shutil as _sh

        _sh.rmtree(reg / "demo-echo-skill")  # 篡改：registry内容消失
        resp = asyncio.run(
            marketplace.marketplace_install_skill(
                "src-inst-5", "demo-echo-skill", user_id=uuid.uuid4()
            )
        )
        assert resp["success"] is False
        assert "安全计划" in resp["error"] or "download" in resp["error"]

    def test_install_unknown_skill_404(self, tmp_db, tmp_path):
        from fastapi import HTTPException

        reg = make_registry(tmp_path / "reg", {"skill-a": VALID_MD})
        self._sync(reg, "src-inst-6")
        with pytest.raises(HTTPException) as ei:
            asyncio.run(
                marketplace.marketplace_install_skill(
                    "src-inst-6", "no-such-skill", user_id=uuid.uuid4()
                )
            )
        assert ei.value.status_code == 404
