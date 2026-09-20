"""P1 skill供应链防御测试 — kilocode discovery.ts移植（origin钉死+staging+原子swap+路径逃逸）"""

import os

import pytest

from src.immune.skill_guard import (
    ORIGIN_MANIFEST,
    OriginRecord,
    SkillSecurityError,
    atomic_swap,
    contained,
    inventory,
    make_staging_dir,
    promote_staging,
    read_origin,
    safe_remove,
    safe_skill_path,
    security_plan,
    skill_fingerprint,
    validate_registry_name,
    validate_skill_name,
    verify_origin,
    write_origin,
)


def make_skill_dir(base, name, description="test skill", with_md=True):
    """构造一个合法skill目录"""
    d = base / name
    d.mkdir(parents=True)
    if with_md:
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\nbody\n",
            encoding="utf-8",
        )
    (d / "scripts").mkdir(exist_ok=True)
    (d / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")
    return d


# ── 名称安全段校验（kilocode name安全段） ──────────────────────


class TestValidateSkillName:
    def test_valid_names(self):
        for name in ["abc", "my-skill", "skill_v2", "a.b.c", "X123"]:
            assert validate_skill_name(name) == name

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            None,
            "..",
            "../evil",
            "..\\evil",
            "foo/bar",
            "foo\\bar",
            ".hidden",
            ".staging-x-1",
            "./x",
            "a/../../b",
            "skill name",
            "技能",
        ],
    )
    def test_rejected_names(self, bad):
        with pytest.raises(SkillSecurityError) as e:
            validate_skill_name(bad)
        assert e.value.reason == "unsafe_name"


class TestValidateRegistryName:
    def test_registry_forms(self):
        assert validate_registry_name("plain-skill") == "plain-skill"
        assert validate_registry_name("org/repo-name") == "repo-name"
        assert validate_registry_name("@scope/pkg") == "pkg"
        assert validate_registry_name("a/b/c") == "c"

    @pytest.mark.parametrize(
        "bad",
        [
            "../evil-name",
            "org/../evil",
            "org/..",
            "org/.hidden",
            "org\\evil",
            "..",
            "./x",
            "org/repo name",
            "",
            "org/",
            "org/bad$name",
            "技能/pkg",
            "org/@pkg",
        ],
    )
    def test_registry_traversal_rejected(self, bad):
        with pytest.raises(SkillSecurityError) as e:
            validate_registry_name(bad)
        assert e.value.reason == "unsafe_name"


# ── 路径逃逸contained（kilocode contained()） ──────────────────


class TestContained:
    def test_inside(self, tmp_path):
        assert contained(tmp_path, tmp_path / "a" / "b") is True
        assert contained(tmp_path, tmp_path) is True

    def test_escape(self, tmp_path):
        outside = tmp_path.parent / "outside-target"
        assert contained(tmp_path, outside) is False
        assert contained(tmp_path, tmp_path.parent) is False

    def test_symlink_escape_rejected(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        secret = tmp_path / "secret-outside"
        secret.mkdir()
        link = base / "innocent"
        os.symlink(secret, link)
        assert contained(base, link) is False

    def test_safe_skill_path_rejects_escape(self, tmp_path):
        with pytest.raises(SkillSecurityError):
            safe_skill_path(tmp_path, "../outside")


# ── origin钉死（kilocode origin钉死在index源） ────────────────


class TestOriginPinning:
    def test_fresh_install_no_origin_ok(self, tmp_path):
        skill = make_skill_dir(tmp_path, "fresh-skill")
        assert verify_origin(skill, "https://example.com/registry") is None

    def test_same_origin_update_allowed(self, tmp_path):
        skill = make_skill_dir(tmp_path, "pinned-skill")
        origin = "https://github.com/org/pinned-skill"
        write_origin(skill, OriginRecord(origin=origin, source_type="git"))
        rec = verify_origin(skill, origin)
        assert rec is not None and rec.origin == origin

    def test_origin_mismatch_rejected(self, tmp_path):
        skill = make_skill_dir(tmp_path, "pinned-skill")
        write_origin(skill, OriginRecord(origin="https://trusted/registry", source_type="registry"))
        with pytest.raises(SkillSecurityError) as e:
            verify_origin(skill, "https://evil.example.com/skill")
        assert e.value.reason == "origin_mismatch"

    def test_force_allows_origin_change(self, tmp_path):
        skill = make_skill_dir(tmp_path, "pinned-skill")
        write_origin(skill, OriginRecord(origin="https://old-source", source_type="git"))
        rec = verify_origin(skill, "https://new-source", force=True)
        assert rec.origin == "https://old-source"  # 返回旧记录供审计

    def test_origin_manifest_roundtrip(self, tmp_path):
        skill = make_skill_dir(tmp_path, "manifest-skill")
        write_origin(
            skill,
            OriginRecord(
                origin="https://src", source_type="git", version="1.2.0", content_hash="abc123"
            ),
        )
        assert (skill / ORIGIN_MANIFEST).exists()
        rec = read_origin(skill)
        assert rec.origin == "https://src"
        assert rec.version == "1.2.0"
        assert rec.content_hash == "abc123"


# ── 安全计划（kilocode逐skill安全计划） ───────────────────────


class TestSecurityPlan:
    def test_valid_staging_passes(self, tmp_path):
        staging = make_skill_dir(tmp_path, "good-skill")
        check = security_plan(staging, expected_origin="https://src")
        assert check.ok is True
        assert check.errors == []
        assert check.fingerprint == skill_fingerprint(staging)

    def test_missing_skill_md_fails(self, tmp_path):
        staging = tmp_path / "md-less"
        staging.mkdir()
        check = security_plan(staging)
        assert check.ok is False
        assert any(e["type"] == "invalid_skill" for e in check.errors)

    def test_unsafe_name_fails(self, tmp_path):
        tmp_path / ".."
        check = security_plan(tmp_path / "nonexistent-but-safe-name")
        assert check.ok is False  # 不存在的目录名合法但无SKILL.md

    def test_symlink_escape_fails_plan(self, tmp_path):
        staging = make_skill_dir(tmp_path, "escape-skill")
        outside = tmp_path / "outside-dir"
        outside.mkdir()
        os.symlink(outside, staging / "exfiltrate")
        check = security_plan(staging)
        assert check.ok is False
        assert any(e["type"] == "path_escape" for e in check.errors)

    def test_origin_mismatch_fails_plan(self, tmp_path):
        live = make_skill_dir(tmp_path, "live-skill")
        write_origin(live, OriginRecord(origin="https://trusted", source_type="registry"))
        make_skill_dir(tmp_path / "staging-area" if False else tmp_path, "live-skill-x")
        # staging与live同名场景在promote管线测，这里直接验证plan的origin比对路径
        staging2 = tmp_path / "staging2" / "live-skill"
        staging2.parent.mkdir()
        make_skill_dir(staging2.parent, "live-skill")
        check = security_plan(staging2, expected_origin="https://evil", live_dir=live)
        assert check.ok is False
        assert any(e["type"] == "origin_mismatch" for e in check.errors)


# ── 原子swap（backup→失败回滚） ────────────────────────────────


class TestAtomicSwap:
    def test_fresh_promote(self, tmp_path):
        live_parent = tmp_path / "live"
        live_parent.mkdir()
        container = make_staging_dir(live_parent, "new-skill")
        staging = make_skill_dir(container, "new-skill")
        report = atomic_swap(staging, live_parent / "new-skill")
        assert report.swapped is True
        assert report.replaced is False
        assert (live_parent / "new-skill" / "SKILL.md").exists()
        assert not staging.exists()  # staging已被rename走

    def test_replace_old_version(self, tmp_path):
        live_parent = tmp_path / "live"
        live_parent.mkdir()
        old = make_skill_dir(live_parent, "up-skill", description="v1")
        staging_container = make_staging_dir(live_parent, "up-skill")
        new = make_skill_dir(staging_container, "up-skill", description="v2 upgraded")
        report = atomic_swap(new, old)
        assert report.swapped is True
        assert report.replaced is True
        content = (live_parent / "up-skill" / "SKILL.md").read_text(encoding="utf-8")
        assert "v2 upgraded" in content
        # backup已清理：live目录下无.backup残留
        leftovers = [d for d in live_parent.iterdir() if ".backup-" in d.name]
        assert leftovers == []

    def test_identical_version_skipped(self, tmp_path):
        live_parent = tmp_path / "live"
        live_parent.mkdir()
        make_skill_dir(live_parent, "same-skill")
        staging_container = make_staging_dir(live_parent, "same-skill")
        staging = make_skill_dir(staging_container, "same-skill")  # 同内容
        report = atomic_swap(staging, live_parent / "same-skill")
        assert report.skipped is True
        assert report.swapped is False
        assert not staging.exists()  # staging被清理

    def test_failure_rolls_back_live(self, tmp_path, monkeypatch):
        live_parent = tmp_path / "live"
        live_parent.mkdir()
        make_skill_dir(live_parent, "rollback-skill", description="original")
        staging_container = make_staging_dir(live_parent, "rollback-skill")
        staging = make_skill_dir(staging_container, "rollback-skill", description="new-broken")

        real_rename = os.rename
        calls = {"n": 0}

        def failing_rename(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:  # 第一次=dest→backup成功，第二次=staging→dest失败
                raise OSError("simulated rename failure")
            return real_rename(src, dst)

        monkeypatch.setattr(os, "rename", failing_rename)
        with pytest.raises(SkillSecurityError) as e:
            atomic_swap(staging, live_parent / "rollback-skill")
        assert e.value.reason == "swap_failed"
        monkeypatch.undo()
        # 回滚验证：live原版完好，内容还是original
        content = (live_parent / "rollback-skill" / "SKILL.md").read_text(encoding="utf-8")
        assert "original" in content

    def test_cross_filesystem_rejected(self, tmp_path, monkeypatch):
        live_parent = tmp_path / "live"
        live_parent.mkdir()
        staging_area = tmp_path / "staging-area"  # 模拟另一个filesystem上的staging
        staging = make_skill_dir(staging_area, "fs-skill")
        # 伪造staging_area的st_dev不同
        real_stat = os.stat

        class FakeStat:
            st_dev = 999

        def fake_stat(path, *a, **kw):
            st = real_stat(path, *a, **kw)
            if str(path) == str(staging_area):
                return FakeStat()
            return st

        monkeypatch.setattr(os, "stat", fake_stat)
        with pytest.raises(SkillSecurityError) as e:
            atomic_swap(staging, live_parent / "fs-skill")
        assert e.value.reason == "swap_failed"


# ── 安全删除（fix路径穿越） ────────────────────────────────────


class TestSafeRemove:
    def test_remove_existing(self, tmp_path):
        make_skill_dir(tmp_path, "doomed-skill")
        assert safe_remove(tmp_path, "doomed-skill") is True
        assert not (tmp_path / "doomed-skill").exists()

    def test_remove_nonexistent_valid_name(self, tmp_path):
        assert safe_remove(tmp_path, "never-existed") is False

    @pytest.mark.parametrize("evil", ["../victim", "..", "a/b", ".hidden", ""])
    def test_traversal_rejected_and_target_preserved(self, tmp_path, evil):
        victim = tmp_path.parent / "victim"
        victim.mkdir(exist_ok=True)
        marker = victim / "precious.txt"
        marker.write_text("must survive", encoding="utf-8")
        base = tmp_path / "skills-base"
        base.mkdir()
        with pytest.raises(SkillSecurityError):
            safe_remove(base, evil)
        assert marker.exists()  # 目标文件必须原封不动
        assert marker.read_text(encoding="utf-8") == "must survive"


# ── 完整晋升管线（staging→校验→origin→原子swap） ──────────────


class TestPromoteStaging:
    def test_happy_path_writes_origin(self, tmp_path):
        live_parent = tmp_path / "shared"
        live_parent.mkdir()
        container = make_staging_dir(live_parent, "promoted-skill")
        payload = make_skill_dir(container, "promoted-skill")
        result = promote_staging(
            payload, live_parent, origin="https://github.com/org/promoted-skill", source_type="git"
        )
        assert result.success is True
        assert result.swapped is True
        dest = live_parent / "promoted-skill"
        assert dest.exists()
        rec = read_origin(dest)
        assert rec is not None
        assert rec.origin == "https://github.com/org/promoted-skill"
        assert rec.source_type == "git"
        assert rec.content_hash == result.fingerprint

    def test_invalid_skill_rejected_live_untouched(self, tmp_path):
        live_parent = tmp_path / "shared"
        live_parent.mkdir()
        container = live_parent / ".staging-bad-1"
        payload = container / "bad-skill"
        payload.mkdir(parents=True)  # 无SKILL.md
        result = promote_staging(payload, live_parent, origin="https://src", source_type="git")
        assert result.success is False
        assert any(e["type"] == "invalid_skill" for e in result.errors)
        assert not (live_parent / "bad-skill").exists()
        assert not payload.exists()  # 负载被清理（容器由调用方清理：install_skill/_sync_to_shared）

    def test_origin_mismatch_rejected_on_update(self, tmp_path):
        live_parent = tmp_path / "shared"
        live_parent.mkdir()
        # v1 from trusted origin
        c1 = make_staging_dir(live_parent, "hot-skill")
        p1 = make_skill_dir(c1, "hot-skill", description="v1")
        r1 = promote_staging(
            p1, live_parent, origin="https://trusted/registry", source_type="registry"
        )
        assert r1.success is True
        # v2 from a DIFFERENT origin → 供应链攻击信号 → fail-closed
        c2 = make_staging_dir(live_parent, "hot-skill")
        p2 = make_skill_dir(c2, "hot-skill", description="v2 backdoored")
        r2 = promote_staging(
            p2, live_parent, origin="https://evil.example.com/hot-skill", source_type="git"
        )
        assert r2.success is False
        assert any(e["type"] == "origin_mismatch" for e in r2.errors)
        content = (live_parent / "hot-skill" / "SKILL.md").read_text(encoding="utf-8")
        assert "v1" in content and "backdoored" not in content  # live保持v1

    def test_same_origin_update_succeeds(self, tmp_path):
        live_parent = tmp_path / "shared"
        live_parent.mkdir()
        c1 = make_staging_dir(live_parent, "legit-skill")
        p1 = make_skill_dir(c1, "legit-skill", description="v1")
        assert promote_staging(
            p1, live_parent, origin="https://trusted/src", source_type="git"
        ).success
        c2 = make_staging_dir(live_parent, "legit-skill")
        p2 = make_skill_dir(c2, "legit-skill", description="v2 upgraded legit")
        r2 = promote_staging(p2, live_parent, origin="https://trusted/src", source_type="git")
        assert r2.success is True and r2.swapped is True
        content = (live_parent / "legit-skill" / "SKILL.md").read_text(encoding="utf-8")
        assert "v2 upgraded legit" in content
        assert read_origin(live_parent / "legit-skill").origin == "https://trusted/src"

    def test_inventory_reports_provenance(self, tmp_path):
        live_parent = tmp_path / "shared"
        live_parent.mkdir()
        c = make_staging_dir(live_parent, "audited-skill")
        p = make_skill_dir(c, "audited-skill")
        promote_staging(
            p, live_parent, origin="https://src/audited", source_type="git", version="3.0"
        )
        make_skill_dir(live_parent, "legacy-no-origin")  # 防御接线前的老安装
        items = inventory(live_parent)
        by_name = {i["skill"]: i for i in items}
        assert by_name["audited-skill"]["has_origin_manifest"] is True
        assert by_name["audited-skill"]["origin"] == "https://src/audited"
        assert by_name["audited-skill"]["version"] == "3.0"
        assert by_name["legacy-no-origin"]["has_origin_manifest"] is False
        # staging容器/backup不进清单
        assert all(not i["skill"].startswith(".") for i in items)
