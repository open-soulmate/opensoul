"""Skills API - unified shared skills for all AI agents.

Auto-detects skills from individual agent directories and migrates them
to a shared location so all agents can use them without duplication.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends

from src.api.user import get_current_user
from src.immune.skill_guard import (
    OriginRecord,
    SkillSecurityError,
    inventory,
    make_staging_dir,
    promote_staging,
    safe_remove,
    skill_fingerprint,
    validate_registry_name,
    validate_skill_name,
    write_origin,
)

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "component": "OpenSkills"}


logger = logging.getLogger(__name__)

# Shared skills directory - all agents read from here
SHARED_SKILLS_DIR = Path.home() / ".openmate" / "shared-skills"

# Known agent skill directories to scan
AGENT_SKILL_DIRS = [
    ("hermes", Path.home() / ".hermes" / "skills"),
    ("mimo", Path.home() / ".config" / "mimo" / "skills"),
    ("opencode", Path.home() / ".config" / "opencode" / "skills"),
    ("claude", Path.home() / ".claude" / "skills"),
    ("aider", Path.home() / ".aider" / "skills"),
    ("continue", Path.home() / ".continue" / "skills"),
]

# .agents/skills 跨agent技能目录标准（五方定案：goose/ChatDev2.0/FastGPT/OpenHands/Warp）
# 全局 ~/.agents/skills/ + 项目级 <repo>/.agents/skills/ 双层结构
AGENTS_STANDARD_DIRS = [
    ("agents-global", Path.home() / ".agents" / "skills"),
    ("agents-openmate", Path.home() / "openmate" / ".agents" / "skills"),
    ("agents-opensoul", Path.home() / "opensoul" / ".agents" / "skills"),
]

# SKILL.md frontmatter必填字段（对齐OpenHands扩展规范）
REQUIRED_SKILL_FIELDS = ("name", "description")


def _validate_skill_dir(skill_dir: Path) -> list[dict]:
    """校验skill目录结构（对齐agno SkillLoader.validate_skill_directory）

    返回typed validation errors列表，空列表=合法
    error types: missing_skill_md / missing_field / empty_description / not_directory
    """
    errors = []
    if not skill_dir.is_dir():
        return [{"type": "not_directory", "detail": str(skill_dir)}]
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [{"type": "missing_skill_md", "detail": f"{skill_dir}/SKILL.md不存在"}]
    try:
        info = _parse_skill_md(skill_md)
    except Exception as e:
        return [{"type": "parse_error", "detail": str(e)}]
    for field in REQUIRED_SKILL_FIELDS:
        if not info.get(field):
            errors.append(
                {"type": "missing_field", "detail": f"{skill_dir.name}: frontmatter缺{field}"}
            )
    return errors


def _scan_standard_skills() -> tuple[list[dict], list[dict]]:
    """扫描.agents/skills标准目录（五方定案）

    返回 (skills, validation_report)
    """
    skills = []
    validation = []
    for source, base_dir in AGENTS_STANDARD_DIRS:
        if not base_dir.exists():
            continue
        for d in sorted(base_dir.iterdir()):
            if not d.is_dir():
                continue
            errs = _validate_skill_dir(d)
            if errs:
                validation.append(
                    {"skill": d.name, "path": str(d), "source": source, "errors": errs}
                )
                continue  # 不合法的不进skills列表，但记录在校验报告
            info = _parse_skill_md(d / "SKILL.md")
            info["installed"] = True  # .agents/skills内即为标准安装位
            info["path"] = str(d)
            info["source"] = source
            info["standard"] = "agents"  # 标记五方定案标准
            skills.append(info)
    return skills, validation


def _parse_skill_md(skill_md: Path) -> dict:
    """Parse SKILL.md frontmatter"""
    content = skill_md.read_text(errors="replace")
    name = skill_md.parent.name
    description = ""
    category = ""
    version = ""
    in_fm = False
    triggers = ""
    for line in content.split("\n"):
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm:
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("description:"):
                description = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("category:"):
                category = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("version:"):
                version = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("triggers:"):
                triggers = line.split(":", 1)[1].strip().strip("\"'[]")
    return {
        "name": name,
        "description": description[:200],
        "category": category or "general",
        "version": version,
        "triggers": triggers,
    }


def _scan_shared_skills() -> list[dict]:
    """Scan skills from shared directory"""
    skills = []
    if not SHARED_SKILLS_DIR.exists():
        return skills
    for d in sorted(SHARED_SKILLS_DIR.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            skill_md = d / "SKILL.md"
            if skill_md.exists():
                info = _parse_skill_md(skill_md)
                info["installed"] = True
                info["path"] = str(d)
                info["source"] = "shared"
                skills.append(info)
    return skills


def _scan_agent_skills() -> list[dict]:
    """Scan skills from individual agent directories"""
    found = []
    for agent_name, agent_dir in AGENT_SKILL_DIRS:
        if not agent_dir.exists():
            continue
        for d in agent_dir.iterdir():
            if d.is_dir():
                skill_md = d / "SKILL.md"
                if skill_md.exists():
                    info = _parse_skill_md(skill_md)
                    info["installed"] = False  # Not yet in shared dir
                    info["path"] = str(d)
                    info["source"] = agent_name
                    info["agent_dir"] = str(agent_dir)
                    found.append(info)
    return found


def _sync_to_shared(skill_path: Path, skill_name: str) -> bool:
    """Copy a skill from agent dir to shared dir — staging+原子swap（skill_guard）

    origin钉死：agent目录路径即origin，后续同名skill更新源不一致会被拒绝。
    """
    dest = SHARED_SKILLS_DIR / skill_name
    if dest.exists():
        # Already in shared, skip
        return False
    SHARED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        validate_skill_name(skill_name)
    except SkillSecurityError:
        logger.warning("skill_guard拒绝迁移不安全skill名: %r", skill_name)
        return False
    staging = make_staging_dir(SHARED_SKILLS_DIR, skill_name)
    payload = staging / skill_name  # kilocode式：staging容器内放skill负载，安全计划按负载目录名校验
    try:
        shutil.copytree(skill_path, payload)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        logger.warning("skill迁移copy失败已清理staging: %s (%s)", skill_name, e)
        return False
    result = promote_staging(
        payload, SHARED_SKILLS_DIR, origin=str(skill_path), source_type="agent-dir"
    )
    shutil.rmtree(staging, ignore_errors=True)  # 容器清理（负载已被rename走或失败被清）
    if not result.success:
        logger.warning("skill_guard拒绝迁移%s: %s", skill_name, result.errors)
        return False
    return result.swapped


@router.get("")
async def list_skills(user_id: UUID = Depends(get_current_user)):
    """List all skills - .agents/skills标准层 + shared + detected from agents"""
    standard_skills, validation = _scan_standard_skills()
    shared = _scan_shared_skills()
    seen = {s["name"] for s in standard_skills}

    # shared层：排除与标准层重名的
    shared_dedup = []
    for s in shared:
        if s["name"] not in seen:
            shared_dedup.append(s)
            seen.add(s["name"])

    # agent目录层：未在前两层的
    agent_skills = []
    for s in _scan_agent_skills():
        if s["name"] not in seen:
            agent_skills.append(s)
            seen.add(s["name"])  # dedupe

    return {
        "skills": standard_skills + shared_dedup + agent_skills,
        "installed_count": len(standard_skills) + len(shared_dedup),
        "standard_count": len(standard_skills),
        "shared_dir": str(SHARED_SKILLS_DIR),
        "standard_dirs": [str(d) for _, d in AGENTS_STANDARD_DIRS],
        "validation_errors": validation,
    }


@router.get("/validate")
async def validate_skills():
    """校验所有skill目录（agno typed-error模式）— 免登录，供健康检查/监控用"""
    report = {"valid": [], "invalid": [], "stats": {}}

    # .agents/skills标准层
    standard_skills, validation = _scan_standard_skills()
    for s in standard_skills:
        report["valid"].append({"name": s["name"], "source": s["source"], "standard": "agents"})
    report["invalid"].extend(validation)

    # shared + agent层也跑校验
    for base_label, base_dirs in [
        ("shared", [SHARED_SKILLS_DIR]),
        ("agent", [d for _, d in AGENT_SKILL_DIRS]),
    ]:
        for base_dir in base_dirs:
            if not base_dir.exists():
                continue
            for d in sorted(base_dir.iterdir()):
                if not d.is_dir() or d.name.startswith("."):  # 跳过.staging/.backup供应链临时目录
                    continue
                errs = _validate_skill_dir(d)
                if errs:
                    report["invalid"].append(
                        {"skill": d.name, "path": str(d), "source": base_label, "errors": errs}
                    )
                else:
                    info = _parse_skill_md(d / "SKILL.md")
                    report["valid"].append(
                        {"name": info["name"], "source": base_label, "standard": ""}
                    )

    report["stats"] = {
        "valid_count": len(report["valid"]),
        "invalid_count": len(report["invalid"]),
        "agents_standard_count": len(standard_skills),
    }
    return report


@router.post("/migrate")
async def migrate_all_skills(user_id: UUID = Depends(get_current_user)):
    """Auto-migrate all agent skills to shared directory"""
    migrated = []
    shared_names = {s["name"] for s in _scan_shared_skills()}

    for s in _scan_agent_skills():
        if s["name"] not in shared_names:
            src = Path(s["path"])
            if _sync_to_shared(src, s["name"]):
                migrated.append(s["name"])
                shared_names.add(s["name"])

    return {"migrated": migrated, "count": len(migrated)}


@router.post("/{skill_name}/install")
async def install_skill(skill_name: str, user_id: UUID = Depends(get_current_user)):
    """Install a skill using hermes CLI into shared directory — skill_guard供应链防御

    kilocode discovery.ts管线：name安全段校验→staging下载→安全计划→origin钉死→原子swap。
    失败任何一关=staging清理，live共享目录永不出现半成品。
    """
    SHARED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    # 安全段校验在先：registry名每段都须安全段，不合法名在触网之前就被拒
    # （runtime proof抓到的缺陷："../evil-name"只查尾段时".."段漏进git URL）
    try:
        repo_name = validate_registry_name(skill_name)
    except SkillSecurityError as e:
        return {"success": False, "error": f"skill_guard: {e.reason} — {e.detail}"}
    try:
        # Try hermes skill install first（安装到隔离staging而非live目录）
        env = os.environ.copy()
        staging = make_staging_dir(SHARED_SKILLS_DIR, repo_name)
        env["HERMES_SKILLS_DIR"] = str(staging)
        proc = subprocess.run(
            ["hermes", "skill", "install", skill_name],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        installed_dir = staging / repo_name
        if proc.returncode == 0 and installed_dir.is_dir():
            result = promote_staging(
                installed_dir,
                SHARED_SKILLS_DIR,
                origin=f"hermes:{skill_name}",
                source_type="registry",
            )
            shutil.rmtree(staging, ignore_errors=True)
            if not result.success:
                return {"success": False, "error": f"skill_guard拒绝: {result.errors}"}
            return {
                "success": True,
                "output": proc.stdout[-500:],
                "origin": result.origin,
                "swapped": result.swapped,
                "guard": "skill_guard",
            }
        shutil.rmtree(staging, ignore_errors=True)

        # Fallback: try pip/npm if it looks like a package — git clone进staging，不直接落live
        if "/" in skill_name or "@" in skill_name:
            # GitHub repo
            repo_url = f"https://github.com/{skill_name}"
            staging = make_staging_dir(SHARED_SKILLS_DIR, repo_name)
            clone_target = staging / repo_name
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(clone_target)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0:
                result = promote_staging(
                    clone_target, SHARED_SKILLS_DIR, origin=repo_url, source_type="git"
                )
                shutil.rmtree(staging, ignore_errors=True)
                if not result.success:
                    return {"success": False, "error": f"skill_guard拒绝: {result.errors}"}
                return {
                    "success": True,
                    "output": "Cloned from GitHub",
                    "origin": repo_url,
                    "swapped": result.swapped,
                    "guard": "skill_guard",
                }
            shutil.rmtree(staging, ignore_errors=True)

        return {"success": False, "error": proc.stderr[-500:] or "Install failed"}
    except FileNotFoundError:
        return {"success": False, "error": "hermes CLI not found"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Install timed out"}
    except SkillSecurityError as e:
        return {"success": False, "error": f"skill_guard: {e.reason} — {e.detail}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/{skill_name}")
async def uninstall_skill(skill_name: str, user_id: UUID = Depends(get_current_user)):
    """Uninstall a skill from shared directory — safe_remove防路径穿越

    此前shutil.rmtree(skill_path)直接吃用户输入：DELETE /skills/..%2F..%2Fxxx
    会rmtree到共享目录之外。skill_guard：name安全段+containment双校验，fail-closed。
    """
    try:
        removed = safe_remove(SHARED_SKILLS_DIR, skill_name)
    except SkillSecurityError as e:
        return {"success": False, "error": f"skill_guard: {e.reason} — {e.detail}"}
    if removed:
        return {"success": True, "guard": "skill_guard"}
    return {"success": False, "error": "Skill not found in shared directory"}


@router.get("/security")
async def skills_security_report():
    """供应链安全报告 — 已装skill的origin/版本/指纹清单（免登录，监控用）

    可观测性：每个skill"从哪来、什么时候装的、内容指纹是什么"一目了然；
    has_origin_manifest=false = 防御接线前的老安装，更新时会补签origin。
    """
    shared = inventory(SHARED_SKILLS_DIR)
    standard = []
    for source, base_dir in AGENTS_STANDARD_DIRS:
        for item in inventory(base_dir):
            item["layer"] = source
            standard.append(item)
    all_items = shared + standard
    return {
        "shared_dir": str(SHARED_SKILLS_DIR),
        "skills": all_items,
        "stats": {
            "total": len(all_items),
            "with_origin_manifest": sum(1 for i in all_items if i["has_origin_manifest"]),
            "legacy_no_manifest": sum(1 for i in all_items if not i["has_origin_manifest"]),
        },
        "guard": "src.immune.skill_guard (kilocode discovery.ts: origin钉死+staging+原子swap)",
    }
