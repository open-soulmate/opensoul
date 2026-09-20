"""Environment sensor for OpenSense.

Collects runtime environment information (system/network/filesystem)
for injection into agent context.

Borrowed from: AutoGPT environment sensing, Claude Code environment info
"""

import logging
import os
import platform
import shutil
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("opensoul.sense.environment")


@dataclass
class EnvironmentInfo:
    os_type: str = ""
    os_version: str = ""
    hostname: str = ""
    python_version: str = ""
    cpu_count: int = 0
    memory_total_gb: float = 0.0
    memory_available_gb: float = 0.0
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0
    working_directory: str = ""
    home_directory: str = ""
    platform_details: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class EnvironmentSensor:
    """Collects and caches environment information."""

    def __init__(self, refresh_interval: float = 300.0):
        self.refresh_interval = refresh_interval
        self._cached: EnvironmentInfo | None = None
        self._last_refresh: float = 0.0

    def collect(self, force: bool = False) -> EnvironmentInfo:
        """Collect environment info with caching."""
        if not force and self._cached:
            if time.time() - self._last_refresh < self.refresh_interval:
                return self._cached

        info = EnvironmentInfo(
            os_type=platform.system(),
            os_version=platform.version(),
            hostname=platform.node(),
            python_version=platform.python_version(),
            cpu_count=os.cpu_count() or 0,
            working_directory=os.getcwd(),
            home_directory=str(os.path.expanduser("~")),
            platform_details={
                "machine": platform.machine(),
                "processor": platform.processor(),
                "platform": platform.platform(),
            },
        )

        # Memory info
        try:
            import psutil

            mem = psutil.virtual_memory()
            info.memory_total_gb = round(mem.total / (1024**3), 1)
            info.memory_available_gb = round(mem.available / (1024**3), 1)
        except ImportError:
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if "MemTotal:" in line:
                            info.memory_total_gb = round(int(line.split()[1]) / (1024**2), 1)
                        elif "MemAvailable:" in line:
                            info.memory_available_gb = round(int(line.split()[1]) / (1024**2), 1)
            except Exception:
                pass

        # Disk info
        try:
            usage = shutil.disk_usage("/")
            info.disk_total_gb = round(usage.total / (1024**3), 1)
            info.disk_free_gb = round(usage.free / (1024**3), 1)
        except Exception:
            pass

        self._cached = info
        self._last_refresh = time.time()
        return info

    def get_context_prompt(self) -> str:
        """Generate environment context for system prompt injection."""
        info = self.collect()
        return "\n".join(
            [
                "## 运行环境",
                f"- 操作系统: {info.os_type} {info.os_version}",
                f"- Python: {info.python_version}",
                f"- CPU: {info.cpu_count}核",
                f"- 内存: {info.memory_available_gb}GB可用 / {info.memory_total_gb}GB总量",
                f"- 磁盘: {info.disk_free_gb}GB可用 / {info.disk_total_gb}GB总量",
                f"- 工作目录: {info.working_directory}",
            ]
        )

    def check_resource_constraints(self) -> list[str]:
        """Check for resource constraints."""
        issues = []
        info = self.collect(force=True)
        if info.memory_available_gb and info.memory_available_gb < 1.0:
            issues.append(f"⚠️ 可用内存不足: {info.memory_available_gb}GB")
        if info.disk_free_gb and info.disk_free_gb < 5.0:
            issues.append(f"⚠️ 磁盘空间不足: {info.disk_free_gb}GB")
        return issues

    def get_stats(self) -> dict:
        info = self.collect()
        return {
            "os": f"{info.os_type} {info.os_version}",
            "cpu_count": info.cpu_count,
            "memory_total_gb": info.memory_total_gb,
            "memory_available_gb": info.memory_available_gb,
            "disk_free_gb": info.disk_free_gb,
            "python_version": info.python_version,
        }
