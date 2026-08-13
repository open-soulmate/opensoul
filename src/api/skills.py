"""Skills API - unified shared skills for all AI agents.

Auto-detects skills from individual agent directories and migrates them
to a shared location so all agents can use them without duplication.
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends

from src.api.user import get_current_user

router = APIRouter()
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


def _parse_skill_md(skill_md: Path) -> dict:
    """Parse SKILL.md frontmatter"""
    content = skill_md.read_text(errors="replace")
    name = skill_md.parent.name
    description = ""
    category = ""
    version = ""
    in_fm = False
    for line in content.split("\n"):
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm:
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip().strip('"\'')
            elif line.startswith("description:"):
                description = line.split(":", 1)[1].strip().strip('"\'')
            elif line.startswith("category:"):
                category = line.split(":", 1)[1].strip().strip('"\'')
            elif line.startswith("version:"):
                version = line.split(":", 1)[1].strip().strip('"\'')
    return {"name": name, "description": description[:200], "category": category or "general", "version": version}


def _scan_shared_skills() -> list[dict]:
    """Scan skills from shared directory"""
    skills = []
    if not SHARED_SKILLS_DIR.exists():
        return skills
    for d in sorted(SHARED_SKILLS_DIR.iterdir()):
        if d.is_dir():
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
    """Copy a skill from agent dir to shared dir"""
    dest = SHARED_SKILLS_DIR / skill_name
    if dest.exists():
        # Already in shared, skip
        return False
    SHARED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_path, dest)
    return True


@router.get("")
async def list_skills(user_id: UUID = Depends(get_current_user)):
    """List all skills - shared + detected from agents"""
    shared = _scan_shared_skills()
    shared_names = {s["name"] for s in shared}

    # Scan agent dirs for skills not yet in shared
    agent_skills = []
    for s in _scan_agent_skills():
        if s["name"] not in shared_names:
            agent_skills.append(s)
            shared_names.add(s["name"])  # dedupe

    return {
        "skills": shared + agent_skills,
        "installed_count": len(shared),
        "shared_dir": str(SHARED_SKILLS_DIR),
    }


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
    """Install a skill using hermes CLI into shared directory"""
    SHARED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        # Try hermes skill install first
        env = os.environ.copy()
        env["HERMES_SKILLS_DIR"] = str(SHARED_SKILLS_DIR)
        proc = subprocess.run(
            ["hermes", "skill", "install", skill_name],
            capture_output=True, text=True, timeout=60, env=env,
        )
        if proc.returncode == 0:
            return {"success": True, "output": proc.stdout[-500:]}
        
        # Fallback: try pip/npm if it looks like a package
        if "/" in skill_name or "@" in skill_name:
            # GitHub repo
            proc = subprocess.run(
                ["git", "clone", f"https://github.com/{skill_name}", str(SHARED_SKILLS_DIR / skill_name.split("/")[-1])],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0:
                return {"success": True, "output": "Cloned from GitHub"}

        return {"success": False, "error": proc.stderr[-500:] or "Install failed"}
    except FileNotFoundError:
        return {"success": False, "error": "hermes CLI not found"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Install timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/{skill_name}")
async def uninstall_skill(skill_name: str, user_id: UUID = Depends(get_current_user)):
    """Uninstall a skill from shared directory"""
    skill_path = SHARED_SKILLS_DIR / skill_name
    if skill_path.exists():
        shutil.rmtree(skill_path)
        return {"success": True}
    return {"success": False, "error": "Skill not found in shared directory"}
