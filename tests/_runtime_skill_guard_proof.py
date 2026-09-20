"""runtime wiring proof: 直接调用src.api.skills的真实端点函数（非仅import验证）"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/climbing/opensoul")

import src.api.skills as skills_mod
from src.immune.skill_guard import read_origin

tmp = Path(tempfile.mkdtemp(prefix="skillguard-live-"))
real_shared = skills_mod.SHARED_SKILLS_DIR
skills_mod.SHARED_SKILLS_DIR = tmp / "shared-skills"
(skills_mod.SHARED_SKILLS_DIR).mkdir(parents=True)

# 1) 路径穿越DELETE必须被safe_remove拒绝，且目录外目标文件原封不动
victim = tmp / "victim-dir"
victim.mkdir()
marker = victim / "important.txt"
marker.write_text("do not delete", encoding="utf-8")

r = asyncio.run(skills_mod.uninstall_skill("../victim-dir", user_id=None))
print("traversal DELETE ../victim-dir ->", r)
assert r["success"] is False and "skill_guard" in r["error"], r
assert marker.exists() and marker.read_text() == "do not delete"
print("victim file intact:", marker.exists())

r2 = asyncio.run(skills_mod.uninstall_skill("..", user_id=None))
print("traversal DELETE .. ->", r2)
assert r2["success"] is False and "skill_guard" in r2["error"], r2

# 2) 不安全skill名安装被拒（触网前）
r3 = asyncio.run(skills_mod.install_skill("../evil-name", user_id=None))
print("unsafe install ../evil-name ->", r3)
assert r3["success"] is False and "unsafe_name" in r3["error"], r3

# 3) _sync_to_shared真实迁移路径：staging→校验→origin→原子swap
src_skill = tmp / "agent-skills" / "demo-skill"
src_skill.mkdir(parents=True)
(src_skill / "SKILL.md").write_text(
    "---\nname: demo-skill\ndescription: runtime demo\n---\nbody\n", encoding="utf-8"
)
ok = skills_mod._sync_to_shared(src_skill, "demo-skill")
print("_sync_to_shared(demo-skill) ->", ok)
assert ok is True
dest = skills_mod.SHARED_SKILLS_DIR / "demo-skill"
assert dest.exists(), "atomic swap后live目录必须存在"
rec = read_origin(dest)
print("origin manifest ->", rec.origin, rec.source_type)
assert rec is not None and rec.origin == str(src_skill) and rec.source_type == "agent-dir"

# 4) origin钉死：同名skill换源迁移必须被拒（模拟供应链攻击）
evil_src = tmp / "evil-skills" / "demo-skill"
evil_src.mkdir(parents=True)
(evil_src / "SKILL.md").write_text(
    "---\nname: demo-skill\ndescription: backdoored\n---\nboom\n", encoding="utf-8"
)
from src.immune.skill_guard import make_staging_dir, promote_staging

container = make_staging_dir(skills_mod.SHARED_SKILLS_DIR, "demo-skill")
payload = container / "demo-skill"
import shutil

shutil.copytree(evil_src, payload)
res = promote_staging(
    payload, skills_mod.SHARED_SKILLS_DIR, origin=str(evil_src), source_type="git"
)
print("origin-mismatch promote ->", res.success, res.errors)
assert res.success is False
assert any(e["type"] == "origin_mismatch" for e in res.errors), res.errors
content = (dest / "SKILL.md").read_text(encoding="utf-8")
assert "backdoored" not in content, "live必须保持原版，未被换源污染"
print("live content unpolluted: OK")
shutil.rmtree(container, ignore_errors=True)

# 5) /security端点函数返回真实清单
report = asyncio.run(skills_mod.skills_security_report())
print("security report stats ->", report["stats"])
assert report["stats"]["total"] >= 1
assert any(i["skill"] == "demo-skill" and i["has_origin_manifest"] for i in report["skills"])

# 6) dot-staging目录不出现在list/validate输出里
listing = asyncio.run(skills_mod.list_skills(user_id=None))
names = [s["name"] for s in listing["skills"]]
print("list_skills names:", names)
assert not any(n.startswith(".") for n in names), names

skills_mod.SHARED_SKILLS_DIR = real_shared
shutil.rmtree(tmp, ignore_errors=True)
print("\nALL RUNTIME WIRING CHECKS PASSED")
