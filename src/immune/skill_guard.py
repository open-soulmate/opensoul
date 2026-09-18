# -*- coding: utf-8 -*-
"""Skill供应链防御 — kilocode skill/discovery.ts移植（P1）

调研来源：feature-matrix/kilocode-source-supplement.md #7
"远程skill注册表拉取+供应链防御"：index.json→逐skill安全计划（SKILL.md必须存在/
name安全段校验/路径逃逸contained()检查/文件下载origin钉死在index源）→staging目录
下载+版本文件比对+**原子rename交换（backup→失败回滚）**"

对应本系统的skill安装/迁移/卸载路径（src/api/skills.py）：
- install_skill：此前git clone直接落live共享目录，失败=半成品污染live路径
- uninstall_skill：此前shutil.rmtree(用户输入)无containment检查 → 路径穿越删除
- _sync_to_shared：此前copytree直接写live dest，非原子

设计原则（同源铁律）：
- fail-closed：校验不过=拒绝安装/拒绝删除，不降级放行
- origin钉死：每个已安装skill带.origin清单，更新必须来自同源（或显式force）
- 原子swap：staging→live只用os.rename；失败回滚backup，live永不出现半成品
"""

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

# 安全skill名：单段、字母数字开头、允许._- （kilocode name安全段校验）
# 显式拒绝：路径分隔符 / \\ 、.. 、隐藏段开头、空名
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

ORIGIN_MANIFEST = ".origin.json"


class SkillSecurityError(Exception):
    """供应链防御拒绝 — fail-closed，携带typed reason（对齐agno typed-error模式）"""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason  # unsafe_name / path_escape / origin_mismatch / invalid_skill / swap_failed
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


# ── 名称与路径安全 ─────────────────────────────────────────────


def validate_skill_name(name: str) -> str:
    """kilocode name安全段校验：单安全段才放行，否则fail-closed拒绝"""
    if not name or not isinstance(name, str):
        raise SkillSecurityError("unsafe_name", "空skill名")
    if "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise SkillSecurityError("unsafe_name", f"含路径语义: {name!r}")
    if not _SAFE_NAME_RE.match(name):
        raise SkillSecurityError("unsafe_name", f"不匹配安全段正则: {name!r}")
    return name


def contained(base: Path, target: Path) -> bool:
    """kilocode contained()：target解析后必须在base之内（防路径逃逸）"""
    try:
        base_r = base.resolve()
        target_r = target.resolve()
    except (OSError, RuntimeError):
        return False
    return target_r == base_r or base_r in target_r.parents


def safe_skill_path(base_dir: Path, skill_name: str) -> Path:
    """校验名+拼路径+containment三合一 — base_dir/skill_name必须contained"""
    validate_skill_name(skill_name)
    target = base_dir / skill_name
    if not contained(base_dir, target):
        raise SkillSecurityError("path_escape", f"{target} 逃逸出 {base_dir}")
    return target


# registry安装名（org/repo、@scope/pkg）：每段都须安全——".."段/反斜杠/隐藏段/非法字符全拒
# kilocode discovery.ts："name安全段校验"在触网下载之前执行
_REGISTRY_SEGMENT_RE = re.compile(r"^@?[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_registry_name(skill_name: str) -> str:
    """校验registry形态skill名（可含org/repo多段），返回最后一段（=skill目录名）"""
    if not skill_name or not isinstance(skill_name, str):
        raise SkillSecurityError("unsafe_name", "空skill名")
    if "\\" in skill_name:
        raise SkillSecurityError("unsafe_name", f"含反斜杠: {skill_name!r}")
    segments = [s for s in skill_name.split("/") if s]
    if not segments or "/".join(segments) != skill_name:
        # 空段（"org/"、"a//b"、尾斜杠）= malformed，fail-closed拒绝而非静默归一化
        raise SkillSecurityError("unsafe_name", f"registry名含空段/畸形分隔: {skill_name!r}")
    for seg in segments:
        if not _REGISTRY_SEGMENT_RE.match(seg):
            raise SkillSecurityError("unsafe_name", f"registry名含非法段: {seg!r} (in {skill_name!r})")
    # 最后段=skill目录名，须过更严格的单段校验（不允许@开头的scope只出现在中间段）
    return validate_skill_name(segments[-1])


# ── origin钉死（.origin.json清单） ─────────────────────────────


@dataclass
class OriginRecord:
    origin: str  # 来源URL/目录（钉死源）
    source_type: str  # git / registry / agent-dir / manual
    version: str = ""
    installed_at: str = ""
    content_hash: str = ""  # SKILL.md内容指纹（版本文件比对的轻量替身）

    def to_json(self) -> str:
        return json.dumps(
            {
                "origin": self.origin,
                "source_type": self.source_type,
                "version": self.version,
                "installed_at": self.installed_at,
                "content_hash": self.content_hash,
            },
            ensure_ascii=False,
            indent=2,
        )


def write_origin(skill_dir: Path, record: OriginRecord) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / ORIGIN_MANIFEST).write_text(record.to_json(), encoding="utf-8")


def read_origin(skill_dir: Path) -> OriginRecord | None:
    manifest = skill_dir / ORIGIN_MANIFEST
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return OriginRecord(
            origin=data.get("origin", ""),
            source_type=data.get("source_type", ""),
            version=data.get("version", ""),
            installed_at=data.get("installed_at", ""),
            content_hash=data.get("content_hash", ""),
        )
    except (json.JSONDecodeError, OSError):
        return None


def verify_origin(skill_dir: Path, incoming_origin: str, force: bool = False) -> OriginRecord | None:
    """origin钉死：更新源必须与已安装origin一致，否则fail-closed拒绝。

    kilocode："文件下载origin钉死在index源"——同skill换源=供应链攻击信号，
    必须显式force才允许（调用方决策，防御层不静默放行）。
    返回已存在的origin记录（供审计），全新安装返回None。
    """
    existing = read_origin(skill_dir)
    if existing is None:
        return None  # 全新安装，无历史origin可比对
    if force:
        return existing
    if existing.origin != incoming_origin:
        raise SkillSecurityError(
            "origin_mismatch",
            f"skill {skill_dir.name} 已钉死origin={existing.origin!r}，"
            f"本次更新来自 {incoming_origin!r} — 拒绝（如需换源请显式force）",
        )
    return existing


def skill_fingerprint(skill_dir: Path) -> str:
    """SKILL.md内容指纹 — 版本文件比对（staging vs live）"""
    import hashlib

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return ""
    return hashlib.sha256(skill_md.read_bytes()).hexdigest()


# ── 逐skill安全计划（kilocode security plan） ───────────────────


@dataclass
class SecurityPlanCheck:
    skill_name: str
    ok: bool
    errors: list = field(default_factory=list)
    origin: str = ""
    fingerprint: str = ""


def security_plan(staging_dir: Path, expected_origin: str = "", live_dir: Path | None = None) -> SecurityPlanCheck:
    """kilocode逐skill安全计划：staging目录在晋升live前的整体校验。

    1. SKILL.md必须存在
    2. name安全段校验（目录名）
    3. 所有文件路径逃逸contained()检查（symlink逃逸也在此拦）
    4. origin比对（live已有同名skill时，来源必须一致，除非调用方force绕过）
    5. 版本指纹提取（供调用方决定skip/升级）
    """
    name = staging_dir.name
    check = SecurityPlanCheck(skill_name=name, ok=True)
    try:
        validate_skill_name(name)
    except SkillSecurityError as e:
        check.ok = False
        check.errors.append({"type": e.reason, "detail": e.detail})
        return check

    if not (staging_dir / "SKILL.md").exists():
        check.ok = False
        check.errors.append({"type": "invalid_skill", "detail": f"{name}: staging内无SKILL.md"})
        return check

    # 路径逃逸检查：staging内每个entry解析后必须contained
    for root, dirs, files in os.walk(staging_dir, followlinks=False):
        root_p = Path(root)
        for entry in list(dirs) + list(files):
            p = root_p / entry
            if not contained(staging_dir, p):
                check.ok = False
                check.errors.append({"type": "path_escape", "detail": f"{p} 逃逸出staging {staging_dir}"})
                return check
        # symlink目标单独检查（os.walk不入symlink目录，但symlink文件会被followlinks=False排除在walk外，需显式查）
        for entry in list(root_p.iterdir()):
            if entry.is_symlink():
                if not contained(staging_dir, entry):
                    check.ok = False
                    check.errors.append({"type": "path_escape", "detail": f"symlink {entry} 指向staging外"})
                    return check

    check.fingerprint = skill_fingerprint(staging_dir)
    if live_dir is not None and expected_origin:
        try:
            verify_origin(live_dir, expected_origin)
        except SkillSecurityError as e:
            check.ok = False
            check.errors.append({"type": e.reason, "detail": e.detail})
    check.origin = expected_origin
    return check


# ── 原子swap（staging→live，backup→失败回滚） ──────────────────


@dataclass
class SwapReport:
    swapped: bool
    dest: str
    replaced: bool  # 是否替换了已有版本
    skipped: bool = False  # 版本指纹一致，无需替换
    reason: str = ""


def atomic_swap(staging_dir: Path, dest_dir: Path, skip_if_identical: bool = True) -> SwapReport:
    """kilocode原子rename交换：staging→live，backup旧版本，失败回滚。

    - staging与dest必须同filesystem（tempfile建在dest同级，保证os.rename原子）
    - 版本文件比对：staging与live的SKILL.md指纹一致且skip_if_identical→skip（不换不删）
    - 失败路径：rename dest→backup成功后rename staging→dest失败 → 把backup移回去（回滚）
    """
    if not staging_dir.is_dir():
        raise SkillSecurityError("swap_failed", f"staging不存在: {staging_dir}")

    try:
        same_fs = os.stat(staging_dir.parent).st_dev == os.stat(dest_dir.parent).st_dev
    except OSError:
        same_fs = False
    if not same_fs:
        # rename跨filesystem非原子且会EXDEV失败 — staging必须与dest同filesystem
        # （make_staging_dir把staging容器建在live_parent内，天然同fs）
        raise SkillSecurityError(
            "swap_failed",
            f"staging {staging_dir} 与 dest {dest_dir} 不同filesystem，rename不原子",
        )

    replaced = dest_dir.exists()
    if replaced and skip_if_identical:
        live_fp = skill_fingerprint(dest_dir)
        stage_fp = skill_fingerprint(staging_dir)
        if live_fp and live_fp == stage_fp:
            shutil.rmtree(staging_dir, ignore_errors=True)
            return SwapReport(swapped=False, dest=str(dest_dir), replaced=False, skipped=True,
                              reason="版本指纹一致，跳过替换")

    backup = dest_dir.parent / f".{dest_dir.name}.backup-{int(time.time() * 1000)}"
    try:
        if replaced:
            os.rename(dest_dir, backup)
        os.rename(staging_dir, dest_dir)
    except OSError as e:
        # 回滚：backup移回原位，live保持原状
        if backup.exists() and not dest_dir.exists():
            try:
                os.rename(backup, dest_dir)
            except OSError as e2:
                raise SkillSecurityError("swap_failed", f"rename失败={e}，且回滚失败={e2}，backup保留在{backup}")
        raise SkillSecurityError("swap_failed", f"原子交换失败已回滚: {e}")
    # 成功：清理backup（保留一个失败不会污染live，成功后不再需要）
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    return SwapReport(swapped=True, dest=str(dest_dir), replaced=replaced, reason="ok")


def make_staging_dir(live_parent: Path, skill_name: str) -> Path:
    """在live同filesystem内创建staging容器（保证atomic_swap同filesystem）

    唯一性：uuid后缀——毫秒时间戳在并发/快速连装时会碰撞（FileExistsError实测）。
    """
    import uuid

    validate_skill_name(skill_name)
    staging = live_parent / f".staging-{skill_name}-{uuid.uuid4().hex[:10]}"
    staging.mkdir(parents=True, exist_ok=False)
    return staging


# ── 安全删除（fix路径穿越） ────────────────────────────────────


def safe_remove(base_dir: Path, skill_name: str) -> bool:
    """kilocode式安全删除：name校验+containment后才rmtree。

    返回True=已删除；False=目标不存在（合法名）。
    不合法名/逃逸 → SkillSecurityError（fail-closed，绝不rmtree）。
    """
    target = safe_skill_path(base_dir, skill_name)
    if not target.exists():
        return False
    if not contained(base_dir, target):
        raise SkillSecurityError("path_escape", f"拒绝删除{target}（不在{base_dir}内）")
    shutil.rmtree(target)
    return True


# ── 安装管线（staging→校验→origin→原子晋升） ──────────────────


@dataclass
class InstallResult:
    success: bool
    skill: str
    dest: str
    origin: str
    fingerprint: str
    swapped: bool
    skipped: bool
    errors: list = field(default_factory=list)


def promote_staging(
    staging_dir: Path,
    live_parent: Path,
    origin: str,
    source_type: str,
    force: bool = False,
    version: str = "",
) -> InstallResult:
    """完整安装管线：安全计划校验→origin比对→写origin→原子swap晋升live。

    任一校验失败=staging被清理+live不动（fail-closed）。
    """
    name = staging_dir.name
    dest = live_parent / name
    result = InstallResult(success=False, skill=name, dest=str(dest), origin=origin,
                           fingerprint="", swapped=False, skipped=False)
    try:
        check = security_plan(staging_dir, expected_origin=origin, live_dir=dest if dest.exists() else None)
        if not check.ok:
            result.errors = check.errors
            shutil.rmtree(staging_dir, ignore_errors=True)
            return result
        if dest.exists():
            verify_origin(dest, origin, force=force)  # origin钉死：二次确认（force由调用方决定）
        result.fingerprint = check.fingerprint
        # origin清单写进staging再swap — live内永远带provenance
        write_origin(staging_dir, OriginRecord(
            origin=origin,
            source_type=source_type,
            version=version,
            installed_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            content_hash=check.fingerprint,
        ))
        live_parent.mkdir(parents=True, exist_ok=True)
        swap = atomic_swap(staging_dir, dest, skip_if_identical=True)
        result.swapped = swap.swapped
        result.skipped = swap.skipped
        result.success = True
        return result
    except SkillSecurityError as e:
        result.errors.append({"type": e.reason, "detail": e.detail})
        shutil.rmtree(staging_dir, ignore_errors=True)
        return result


def inventory(live_parent: Path) -> list[dict]:
    """已安装skill的供应链清单 — origin/版本/指纹（可观测性报告源）"""
    items = []
    if not live_parent.exists():
        return items
    for d in sorted(live_parent.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        rec = read_origin(d)
        items.append({
            "skill": d.name,
            "path": str(d),
            "origin": rec.origin if rec else "",
            "source_type": rec.source_type if rec else "",
            "version": rec.version if rec else "",
            "installed_at": rec.installed_at if rec else "",
            "content_hash": rec.content_hash if rec else skill_fingerprint(d),
            "has_origin_manifest": rec is not None,
        })
    return items
