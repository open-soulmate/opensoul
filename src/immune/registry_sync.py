"""Registry同步管线 — kilocode skill/discovery.ts完整移植（P1）

调研来源：feature-matrix/kilocode-source-supplement.md #7：
"index.json→逐skill安全计划（SKILL.md必须存在/name安全段校验/路径逃逸contained()检查/
文件下载origin钉死在index源）→staging目录下载+版本文件比对+原子rename交换"

上轮skill_guard.py落了安装/迁移/卸载路径的防御，但marketplace registry同步
（src/api/marketplace.py sync_skill_source）仍是"Simulate sync"占位——
远程registry index.json真实拉取+逐skill安全计划全流程未接。本模块补齐：

- fetch_registry_index：真实拉取index.json（本地目录/file://=气隙内网registry，
  一等公民——用户"文件不出本地"约束下的企业级模式；http(s)远程registry支持
  github repo URL→raw index.json main/master回退）
- plan_registry_entries：逐skill安全计划（index层）——name安全段校验/registry内
  相对路径..逃逸拒绝/download_url origin钉死在index源（kilocode原文）
- download_skill_payload：origin钉死下载到staging（本地=containment校验后copy，
  远程=逐文件按index解析基准URL拉取），产物必须含SKILL.md

失败语义（mem0 §1.1失败必须可见）：拉取失败/非法index→RegistrySyncError带
typed reason，调用方落last_sync_error，禁止静默假成功；逐条entry校验失败→
rejected列表随响应返回，不阻断其他合法skill（fail-closed per-entry）。
"""

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from src.immune.skill_guard import SkillSecurityError, contained, validate_skill_name

HTTP_TIMEOUT = 15  # 单次拉取上限（秒）


class RegistrySyncError(Exception):
    """registry同步失败 — typed reason（fetch_failed/invalid_index/download_failed）"""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


@dataclass
class RegistryEntry:
    name: str
    description: str = ""
    category: str = "general"
    version: str = ""
    path: str = ""  # registry内skill目录（相对路径，默认=skill名）
    files: list = field(default_factory=list)  # registry内文件清单（相对路径，空=整目录）
    download_url: str = ""  # 可选完整下载URL（必须与index同origin）


@dataclass
class RegistryIndex:
    entries: list  # raw index条目（list[dict]）
    base_url: str  # index解析后的位置（远程origin钉死基准）
    base_dir: str = ""  # 本地registry基目录（非空=本地registry，相对路径解析基准）


@dataclass
class PlannedEntry:
    entry: RegistryEntry
    origin: str  # 供应链origin（registry:<origin基准>，promote_staging写入.origin.json）


# ── index拉取 ──────────────────────────────────────────────────


def _local_base(url: str) -> Path | None:
    """本地registry判定：file://或存在的本地目录 / 无scheme本地路径"""
    if url.startswith("file://"):
        return Path(url[7:])
    if "://" not in url:
        return Path(url)
    return None


def _candidate_index_urls(url: str, source_type: str = "") -> list[str]:
    """远程registry的index候选URL序列。

    github repo URL → raw.githubusercontent.com main/master index.json回退，
    最后直接尝试源URL本身（API型registry如 clawhub.com/api/v1/skills）。
    """
    parts = urlsplit(url)
    if parts.netloc == "github.com":
        segs = [s for s in parts.path.split("/") if s]
        if len(segs) >= 2:
            org, repo = segs[0], segs[1].removesuffix(".git")
            return [
                f"https://raw.githubusercontent.com/{org}/{repo}/main/index.json",
                f"https://raw.githubusercontent.com/{org}/{repo}/master/index.json",
                url,
            ]
    return [url]


def _http_get(url: str) -> bytes:
    """HTTP(S)拉取 — 失败抛RegistrySyncError(fetch_failed)，不静默"""
    req = Request(url, headers={"User-Agent": "OpenSoul-RegistrySync/1.0"})
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.read()
    except Exception as e:  # URLError/HTTPError/timeout/SSL 全部落typed error
        raise RegistrySyncError("fetch_failed", f"{url}: {e}") from e


def _as_entries(data, src: str) -> list:
    """宽容解析：index必须是skill数组或{"skills":[...]}（kilocode index.json形态）"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("skills"), list):
        return data["skills"]
    raise RegistrySyncError("invalid_index", f"{src}: index必须是skill数组或{{'skills':[...]}}")


def fetch_registry_index(url: str, source_type: str = "") -> RegistryIndex:
    """真实拉取registry index — 本地目录/file://走文件系统，远程走HTTP候选序列"""
    base_dir = _local_base(url)
    if base_dir is not None:
        index_file = base_dir / "index.json"
        if not index_file.exists():
            raise RegistrySyncError("fetch_failed", f"本地registry无index.json: {index_file}")
        try:
            data = json.loads(index_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise RegistrySyncError("invalid_index", f"{index_file}: {e}") from e
        return RegistryIndex(
            entries=_as_entries(data, str(index_file)),
            base_url=str(index_file),
            base_dir=str(base_dir),
        )

    errors = []
    for cand in _candidate_index_urls(url, source_type):
        try:
            raw = _http_get(cand)
        except RegistrySyncError as e:
            errors.append(e.detail)
            continue
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as e:
            errors.append(f"{cand}: JSON解析失败 {e}")
            continue
        return RegistryIndex(entries=_as_entries(data, cand), base_url=cand, base_dir="")
    raise RegistrySyncError("fetch_failed", "; ".join(errors)[:500] or f"无可用index候选: {url}")


# ── 逐skill安全计划（index层） ─────────────────────────────────


def _origin_of(url: str, base_dir: str = "") -> str:
    """origin基准：本地registry=local:<dir>；远程=<scheme>://<netloc>"""
    if base_dir:
        return f"local:{base_dir}"
    if url.startswith("file://"):
        return f"local:{url[7:]}"
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    if "://" not in url:
        return f"local:{url}"
    return url


def _unsafe_relative(rel: str) -> bool:
    """registry内相对路径逃逸判定：绝对路径/..段/反斜杠/空路径"""
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel:
        return True
    return ".." in Path(rel).parts


def plan_registry_entries(index: RegistryIndex) -> tuple[list[PlannedEntry], list[dict]]:
    """kilocode逐skill安全计划（index层，per-entry fail-closed）：

    1. name必须存在且过skill_guard安全段校验
    2. path/files相对路径禁止..逃逸/绝对路径
    3. download_url必须与index同origin（"文件下载origin钉死在index源"）
    """
    accepted: list[PlannedEntry] = []
    rejected: list[dict] = []
    index_origin = _origin_of(index.base_url, index.base_dir)

    for raw in index.entries:
        if not isinstance(raw, dict):
            rejected.append(
                {"skill": str(raw)[:50], "reason": "invalid_entry", "detail": "非对象条目"}
            )
            continue
        name = str(raw.get("name", "") or "").strip()
        try:
            validate_skill_name(name)
        except SkillSecurityError as e:
            rejected.append({"skill": name[:50], "reason": e.reason, "detail": e.detail})
            continue

        path = str(raw.get("path", "") or name)
        files = [str(f) for f in (raw.get("files") or [])]
        download_url = str(raw.get("download_url", "") or "")

        bad_rel = next((rel for rel in [path] + files if _unsafe_relative(rel)), None)
        if bad_rel is not None:
            rejected.append(
                {
                    "skill": name,
                    "reason": "path_escape",
                    "detail": f"registry内相对路径含逃逸语义: {bad_rel!r}",
                }
            )
            continue

        if download_url and _origin_of(download_url) != index_origin:
            rejected.append(
                {
                    "skill": name,
                    "reason": "origin_mismatch",
                    "detail": (
                        f"download_url origin={_origin_of(download_url)!r} ≠ "
                        f"index origin={index_origin!r} — kilocode:文件下载origin钉死在index源"
                    ),
                }
            )
            continue

        entry = RegistryEntry(
            name=name,
            description=str(raw.get("description", "") or "")[:200],
            category=str(raw.get("category", "") or "general") or "general",
            version=str(raw.get("version", "") or ""),
            path=path,
            files=files,
            download_url=download_url,
        )
        accepted.append(PlannedEntry(entry=entry, origin=f"registry:{index_origin}"))

    return accepted, rejected


# ── origin钉死下载（staging落盘） ──────────────────────────────


def download_skill_payload(
    planned: PlannedEntry, index: RegistryIndex, staging_parent: Path
) -> Path:
    """把skill负载下载到staging容器内 <staging_parent>/<name>。

    - 本地registry：containment校验后copytree/逐文件copy（path逃逸在此拦）
    - 远程registry：逐文件按index解析基准URL拉取（download_url已过origin钉死校验）
    - 产物必须含SKILL.md（security plan前置检查，缺失=下载失败）
    之后调用方走skill_guard.promote_staging（security_plan→origin→原子swap）。
    """
    e = planned.entry
    payload = staging_parent / e.name

    if index.base_dir:
        base_dir = Path(index.base_dir)
        src = base_dir / e.path
        if not contained(base_dir, src):
            raise RegistrySyncError("download_failed", f"{src} 逃逸出registry {base_dir}")
        if not src.is_dir():
            raise RegistrySyncError("download_failed", f"registry内无skill目录: {src}")
        if e.files:
            payload.mkdir(parents=True, exist_ok=True)
            for rel in e.files:
                f = src / rel
                if not contained(base_dir, f) or not f.is_file():
                    raise RegistrySyncError("download_failed", f"registry文件缺失或逃逸: {rel}")
                dest = payload / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
        else:
            shutil.copytree(src, payload)
    else:
        base_url = index.base_url.rsplit("/", 1)[0]  # index所在目录=相对路径解析基准
        payload.mkdir(parents=True, exist_ok=True)
        file_list = e.files or ["SKILL.md"]
        for rel in file_list:
            if e.download_url:
                file_url = e.download_url  # 单文件下载URL（origin已钉死校验）
            else:
                prefix = f"{base_url}/{e.path}" if e.path else base_url
                file_url = f"{prefix}/{rel}"
            dest = payload / rel
            if not contained(payload, dest):
                raise RegistrySyncError("download_failed", f"目标路径逃逸: {rel}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_http_get(file_url))

    if not (payload / "SKILL.md").exists():
        raise RegistrySyncError("download_failed", f"{e.name}: 下载产物无SKILL.md")
    return payload
