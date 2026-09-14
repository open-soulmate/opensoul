"""Project Memory — 代码库文件索引、依赖图谱、核心组件识别，支持增量刷新。"""

import ast
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models.cognitive import FileInfo, ImpactAnalysis

logger = logging.getLogger(__name__)

LANG_MAP = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".json": "json",
    ".yaml": "yaml", ".yml": "yaml", ".md": "markdown",
    ".css": "css", ".html": "html", ".sh": "shell",
}

IGNORE_DIRS = {
    "node_modules", ".next", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".hermes", ".cache", "data",
}

CORE_FILE_PERCENTILE = 0.9


class ProjectMemory:
    """项目记忆：文件索引、依赖图谱、核心组件识别"""

    def __init__(self, repo_root: str, cache_path: str = ""):
        self.repo_root = Path(repo_root)
        self.cache_path = Path(cache_path) if cache_path else Path(os.path.expanduser("~/.opensoul/project_memory.json"))
        self.file_index: dict[str, FileInfo] = {}
        self.dependency_graph: dict[str, list[str]] = {}
        self.import_graph: dict[str, list[str]] = {}
        self.core_files: list[str] = []
        self._last_scan: Optional[datetime] = None
        self._build_index()

    def _build_index(self):
        logger.info("扫描项目: %s", self.repo_root)
        start = datetime.now()
        for root, dirs, files in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for fname in files:
                fpath = Path(root) / fname
                ext = fpath.suffix.lower()
                if ext in LANG_MAP:
                    rel_path = str(fpath.relative_to(self.repo_root))
                    try:
                        stat = fpath.stat()
                        self.file_index[rel_path] = FileInfo(
                            path=rel_path, language=LANG_MAP[ext],
                            lines=sum(1 for _ in open(fpath, "r", errors="replace")),
                            size_bytes=stat.st_size,
                            last_modified=datetime.fromtimestamp(stat.st_mtime),
                        )
                    except Exception:
                        pass
        self._build_dependency_graph()
        self._identify_core_files()
        self._last_scan = start
        logger.info("扫描完成: %d文件, %d核心, %.1fs",
                     len(self.file_index), len(self.core_files), (datetime.now()-start).total_seconds())

    def _build_dependency_graph(self):
        for rel_path, info in self.file_index.items():
            full_path = self.repo_root / rel_path
            imports = self._extract_imports(full_path, info.language)
            self.import_graph[rel_path] = imports
            for imp in imports:
                self.dependency_graph.setdefault(imp, [])
                if rel_path not in self.dependency_graph[imp]:
                    self.dependency_graph[imp].append(rel_path)

    def _extract_imports(self, path: Path, language: str) -> list[str]:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []
        if language == "python":
            return self._extract_python_imports(path, content)
        elif language in ("typescript", "javascript"):
            return self._extract_js_imports(path, content)
        return []

    def _extract_python_imports(self, path: Path, content: str) -> list[str]:
        imports = []
        try:
            tree = ast.parse(content, filename=str(path))
        except SyntaxError:
            return imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolved = self._resolve_python_import(alias.name, path)
                    if resolved:
                        imports.append(resolved)
            elif isinstance(node, ast.ImportFrom) and node.module:
                resolved = self._resolve_python_import(node.module, path)
                if resolved:
                    imports.append(resolved)
        return imports

    def _resolve_python_import(self, module_name: str, from_path: Path) -> Optional[str]:
        parts = module_name.split(".")
        candidate = from_path.parent / "/".join(parts)
        for suffix in [".py", "/__init__.py"]:
            try:
                resolved = candidate.with_suffix(suffix) if suffix.startswith(".") else candidate / suffix.lstrip("/")
                rel = resolved.relative_to(self.repo_root)
                if str(rel) in self.file_index:
                    return str(rel)
            except (ValueError, OSError):
                pass
        candidate = self.repo_root / "/".join(parts)
        for suffix in [".py", "/__init__.py"]:
            try:
                resolved = candidate.with_suffix(suffix) if suffix.startswith(".") else candidate / suffix.lstrip("/")
                rel = resolved.relative_to(self.repo_root)
                if str(rel) in self.file_index:
                    return str(rel)
            except (ValueError, OSError):
                pass
        return None

    def _extract_js_imports(self, path: Path, content: str) -> list[str]:
        imports = []
        for m in re.finditer(r'''(?:import|from)\s+.*?['"]([^'"]+)['"]''', content):
            resolved = self._resolve_js_import(m.group(1), path)
            if resolved:
                imports.append(resolved)
        for m in re.finditer(r'''require\s*\(\s*['"]([^'"]+)['"]\s*\)''', content):
            resolved = self._resolve_js_import(m.group(1), path)
            if resolved:
                imports.append(resolved)
        return imports

    def _resolve_js_import(self, import_path: str, from_path: Path) -> Optional[str]:
        if not import_path.startswith("."):
            return None
        candidate = (from_path.parent / import_path).resolve()
        for suffix in [".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js"]:
            test = candidate.with_suffix(suffix) if "." in suffix and "/" not in suffix else candidate.parent / (candidate.name + suffix)
            try:
                rel = test.relative_to(self.repo_root)
                if str(rel) in self.file_index:
                    return str(rel)
            except ValueError:
                continue
        return None

    def _identify_core_files(self):
        dep_counts = [(f, len(deps)) for f, deps in self.dependency_graph.items()]
        dep_counts.sort(key=lambda x: x[1], reverse=True)
        cutoff = max(1, int(len(dep_counts) * (1 - CORE_FILE_PERCENTILE)))
        self.core_files = [f for f, cnt in dep_counts[:cutoff] if cnt > 0]
        for p in self.core_files:
            if p in self.file_index:
                self.file_index[p].is_core = True

    async def refresh_incremental(self):
        if not self._last_scan:
            self._build_index()
            return
        changed = []
        for rel_path, info in self.file_index.items():
            full_path = self.repo_root / rel_path
            try:
                mod_time = datetime.fromtimestamp(full_path.stat().st_mtime)
                if mod_time > info.last_modified:
                    info.lines = sum(1 for _ in open(full_path, "r", errors="replace"))
                    info.size_bytes = full_path.stat().st_size
                    info.last_modified = mod_time
                    changed.append(rel_path)
            except FileNotFoundError:
                del self.file_index[rel_path]
                changed.append(rel_path)
        if changed:
            self._identify_core_files()
        self._last_scan = datetime.now()

    def is_core_file(self, path: str) -> bool:
        return path in self.core_files

    def get_dependents(self, path: str) -> list[str]:
        return self.dependency_graph.get(path, [])

    def get_impact(self, path: str) -> ImpactAnalysis:
        direct = self.get_dependents(path)
        indirect = self._transitive_deps(path)
        level = "critical" if len(direct) > 10 else "high" if len(direct) > 5 else "medium" if len(direct) > 2 else "low"
        return ImpactAnalysis(direct_impact=direct, indirect_impact=indirect, risk_level=level)

    def _transitive_deps(self, path: str) -> list[str]:
        visited, queue = set(), self.get_dependents(path).copy()
        while queue:
            f = queue.pop(0)
            if f not in visited:
                visited.add(f)
                queue.extend(self.get_dependents(f))
        return list(visited)

    def get_stats(self) -> dict:
        return {
            "total_files": len(self.file_index),
            "core_files": len(self.core_files),
            "total_dependencies": sum(len(d) for d in self.dependency_graph.values()),
            "last_scan": self._last_scan.isoformat() if self._last_scan else None,
        }

    def save_cache(self):
        data = {
            "files": {k: {"language": v.language, "lines": v.lines, "is_core": v.is_core} for k, v in self.file_index.items()},
            "dependency_graph": self.dependency_graph,
            "import_graph": self.import_graph,
            "core_files": self.core_files,
            "last_scan": self._last_scan.isoformat() if self._last_scan else None,
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
