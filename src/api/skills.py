"""Skills API - browse, install, uninstall Hermes skills."""

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

SKILLS_DIR = Path.home() / ".hermes" / "skills"


def _scan_skills() -> list[dict]:
    """Scan installed skills from ~/.hermes/skills/"""
    skills = []
    if not SKILLS_DIR.exists():
        return skills
    for d in sorted(SKILLS_DIR.iterdir()):
        if d.is_dir():
            skill_md = d / "SKILL.md"
            if skill_md.exists():
                # Parse frontmatter
                content = skill_md.read_text(errors="replace")
                name = d.name
                description = ""
                category = ""
                version = ""
                lines = content.split("\n")
                in_frontmatter = False
                for line in lines:
                    if line.strip() == "---":
                        in_frontmatter = not in_frontmatter
                        continue
                    if in_frontmatter:
                        if line.startswith("name:"):
                            name = line.split(":", 1)[1].strip().strip('"\'')
                        elif line.startswith("description:"):
                            description = line.split(":", 1)[1].strip().strip('"\'')
                        elif line.startswith("category:"):
                            category = line.split(":", 1)[1].strip().strip('"\'')
                        elif line.startswith("version:"):
                            version = line.split(":", 1)[1].strip().strip('"\'')
                skills.append({
                    "name": name,
                    "description": description[:200],
                    "category": category or "general",
                    "installed": True,
                    "version": version,
                    "path": str(d),
                })
    return skills


# Known available skills from the repository
KNOWN_SKILLS = [
    {"name": "web-quality", "description": "Analyze web pages for quality, accessibility, and performance", "category": "web"},
    {"name": "code-review", "description": "Automated code review with best practices", "category": "development"},
    {"name": "api-design", "description": "API design patterns and best practices", "category": "development"},
    {"name": "debugging", "description": "Systematic debugging methodology", "category": "development"},
    {"name": "testing", "description": "Test-driven development workflow", "category": "development"},
    {"name": "deployment", "description": "Deployment automation and CI/CD", "category": "devops"},
    {"name": "database", "description": "Database design and optimization", "category": "data"},
    {"name": "security-audit", "description": "Security vulnerability scanning", "category": "security"},
    {"name": "performance", "description": "Performance optimization analysis", "category": "development"},
    {"name": "documentation", "description": "Auto-generate documentation from code", "category": "development"},
    {"name": "refactoring", "description": "Code refactoring with patterns", "category": "development"},
    {"name": "architecture", "description": "System architecture design patterns", "category": "development"},
]


@router.get("")
async def list_skills(user_id: UUID = Depends(get_current_user)):
    """List all skills - installed + available"""
    installed = _scan_skills()
    installed_names = {s["name"] for s in installed}

    # Add available (not installed) skills
    available = []
    for skill in KNOWN_SKILLS:
        if skill["name"] not in installed_names:
            available.append({**skill, "installed": False, "version": None, "path": None})

    return {"skills": installed + available, "installed_count": len(installed)}


@router.post("/{skill_name}/install")
async def install_skill(skill_name: str, user_id: UUID = Depends(get_current_user)):
    """Install a skill using hermes CLI"""
    try:
        proc = subprocess.run(
            ["hermes", "skill", "install", skill_name],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode == 0:
            return {"success": True, "output": proc.stdout[-500:]}
        return {"success": False, "error": proc.stderr[-500:] or "Install failed"}
    except FileNotFoundError:
        return {"success": False, "error": "hermes CLI not found"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Install timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/{skill_name}")
async def uninstall_skill(skill_name: str, user_id: UUID = Depends(get_current_user)):
    """Uninstall a skill"""
    skill_path = SKILLS_DIR / skill_name
    if skill_path.exists():
        shutil.rmtree(skill_path)
        return {"success": True}
    return {"success": False, "error": "Skill not found"}
