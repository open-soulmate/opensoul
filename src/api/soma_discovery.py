"""Soma Discovery Engine — Local software and service discovery.

Scans the local system for:
- Running processes (via ps aux)
- CLI tools installed on PATH (via which)
- Listening network services (via ss -tlnp)

Provides a unified SoftwareAdapter interface for extending discovery
with custom scanners, and exposes all results via REST API.

Results are cached for 60 seconds to avoid repeated system scans.
"""

import asyncio
import logging
import os
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Cache ────────────────────────────────────────────────────────────────

CACHE_TTL = 60  # seconds


class _Cache:
    """Simple time-based cache for scan results."""

    def __init__(self, ttl: int = CACHE_TTL):
        self._store: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.time() - ts > self._ttl:
            del self._store[key]
            return None
        return data

    def set(self, key: str, data: Any) -> None:
        self._store[key] = (time.time(), data)

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._store.clear()
        else:
            self._store.pop(key, None)


_cache = _Cache()
# Common CLI tool descriptions (static, no probing needed)
_TOOL_DESCRIPTIONS: dict[str, str] = {
    "python3": "Python 3 interpreter",
    "python": "Python interpreter",
    "node": "Node.js JavaScript runtime",
    "npm": "Node.js package manager",
    "yarn": "JavaScript package manager",
    "pnpm": "Fast, disk-space-efficient package manager",
    "git": "Distributed version control system",
    "docker": "Container runtime and orchestration",
    "podman": "Daemonless container engine",
    "kubectl": "Kubernetes cluster management",
    "cargo": "Rust package manager and build tool",
    "rustc": "Rust compiler",
    "gcc": "GNU C compiler",
    "g++": "GNU C++ compiler",
    "clang": "LLVM C/C++ compiler",
    "make": "GNU build automation tool",
    "cmake": "Cross-platform build system generator",
    "go": "Go programming language toolchain",
    "java": "Java application launcher",
    "javac": "Java compiler",
    "curl": "Command-line HTTP client",
    "wget": "Network file retriever",
    "ssh": "OpenSSH remote login client",
    "rsync": "Fast file synchronization tool",
    "tar": "Tape archive utility",
    "unzip": "Extraction utility for .zip archives",
    "jq": "Command-line JSON processor",
    "sed": "Stream editor for text transformation",
    "awk": "Pattern-directed text processing",
    "grep": "Pattern search utility",
    "rg": "ripgrep — fast recursive grep",
    "fd": "fd-find — fast alternative to find",
    "htop": "Interactive process viewer",
    "vim": "Vi IMproved text editor",
    "nvim": "Neovim text editor",
    "code": "Visual Studio Code editor",
    "nano": "Simple terminal text editor",
    "tmux": "Terminal multiplexer",
    "screen": "Terminal session manager",
    "uv": "Fast Python package installer",
    "pip": "Python package installer",
    "pip3": "Python 3 package installer",
    "conda": "Package and environment manager",
    "ffmpeg": "Multimedia framework for audio/video",
    "sqlite3": "SQLite database CLI",
    "psql": "PostgreSQL interactive terminal",
    "mysql": "MySQL command-line client",
    "redis-cli": "Redis command-line interface",
    "mongosh": "MongoDB shell",
    "nginx": "High-performance HTTP server",
    "systemctl": "Systemd service manager",
    "journalctl": "Systemd journal viewer",
    "flatpak": "Linux application sandboxing framework",
    "snap": "Universal Linux package manager",
    "paru": "AUR helper for Arch Linux",
    "pacman": "Arch Linux package manager",
    "yay": "Yet Another AUR helper",
    "top": "System process monitor",
    "df": "Disk space report",
    "du": "Disk usage estimator",
    "free": "Memory usage report",
    "lsof": "List open files and sockets",
    "strace": "System call tracer",
    "ltrace": "Library call tracer",
    "gdb": "GNU debugger",
    "valgrind": "Memory debugging and profiling",
    "nmap": "Network exploration and security scanner",
    "dig": "DNS lookup utility",
    "ip": "Network interface configuration",
    "ss": "Socket statistics",
    "ping": "Network reachability test",
    "traceroute": "Network route tracer",
    "hermes": "Hermes AI agent CLI",
}


# ── Data Models ──────────────────────────────────────────────────────────

class ProcessInfo(BaseModel):
    pid: int
    user: str
    cpu: float
    mem: float
    command: str
    executable: str | None = None
    description: str = ""


class CLITool(BaseModel):
    name: str
    path: str
    version: str | None = None
    description: str = ""


class ServiceInfo(BaseModel):
    protocol: str
    local_address: str
    local_port: int
    state: str
    process_name: str | None = None
    pid: int | None = None
    description: str = ""


class ScanResult(BaseModel):
    scan_time: float
    processes: list[ProcessInfo]
    cli_tools: list[CLITool]
    services: list[ServiceInfo]
    adapters: list[str]


class AdapterInfo(BaseModel):
    name: str
    description: str
    registered_at: float


class AdapterExecuteRequest(BaseModel):
    action: str
    params: dict[str, Any] = {}


class AdapterExecuteResponse(BaseModel):
    adapter: str
    action: str
    success: bool
    result: Any = None
    error: str | None = None


# ── Software Adapter Base ────────────────────────────────────────────────

class SoftwareAdapter(ABC):
    """Base class for software discovery adapters.

    Subclass this to add custom discovery logic for specific
    software categories (databases, containers, IDEs, etc.).
    """

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.registered_at = time.time()

    @abstractmethod
    async def scan(self) -> list[dict[str, Any]]:
        """Run the adapter's discovery scan and return results."""
        ...

    @abstractmethod
    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        """Execute an action through this adapter."""
        ...

    def to_info(self) -> AdapterInfo:
        return AdapterInfo(
            name=self.name,
            description=self.description,
            registered_at=self.registered_at,
        )


# ── Adapter Registry ────────────────────────────────────────────────────

_adapter_registry: dict[str, SoftwareAdapter] = {}


def register_adapter(adapter: SoftwareAdapter) -> None:
    """Register a SoftwareAdapter instance."""
    _adapter_registry[adapter.name] = adapter
    logger.info("Registered software adapter: %s", adapter.name)


def get_adapter(name: str) -> SoftwareAdapter | None:
    return _adapter_registry.get(name)


# ── System Scanners ──────────────────────────────────────────────────────


async def _run_cmd(args: list[str], timeout: float = 10.0) -> tuple[str, str, int]:
    """Run a command via asyncio subprocess with timeout.

    Returns (stdout, stderr, returncode).
    Returns empty strings if the command fails or times out.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # isolate so kill() gets all children
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return (
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            proc.returncode or 0,
        )
    except asyncio.TimeoutError:
        logger.warning("Command timed out: %s", " ".join(args))
        try:
            proc.kill()
        except (ProcessLookupError, UnboundLocalError):
            pass
        return ("", "timeout", -1)
    except PermissionError:
        logger.warning("Permission denied: %s", " ".join(args))
        return ("", "permission denied", -1)
    except FileNotFoundError:
        return ("", "command not found", -1)
    except Exception as e:
        logger.warning("Command failed [%s]: %s", " ".join(args), e)
        return ("", str(e), -1)


async def scan_processes() -> list[ProcessInfo]:
    """Scan running processes using 'ps aux'."""
    cached = _cache.get("processes")
    if cached is not None:
        return cached

    stdout, stderr, rc = await _run_cmd(["ps", "aux"])
    if rc != 0:
        logger.error("ps aux failed: %s", stderr)
        return []

    results: list[ProcessInfo] = []
    lines = stdout.strip().split("\n")
    for line in lines[1:]:  # Skip header
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        try:
            user = parts[0]
            pid = int(parts[1])
            cpu = float(parts[2])
            mem = float(parts[3])
            command_full = parts[10]
            executable = command_full.split()[0] if command_full else None

            # Extract a short name from the executable path
            exe_name = Path(executable).name if executable else command_full[:40]

            results.append(
                ProcessInfo(
                    pid=pid,
                    user=user,
                    cpu=cpu,
                    mem=mem,
                    command=command_full,
                    executable=executable,
                    description=f"PID {pid} ({user}): {command_full[:80]}",
                )
            )
        except (ValueError, IndexError):
            continue

    _cache.set("processes", results)
    return results


async def scan_cli_tools() -> list[CLITool]:
    """Discover CLI tools by scanning PATH directories."""
    cached = _cache.get("cli_tools")
    if cached is not None:
        return cached

    path_dirs = os.environ.get("PATH", "").split(":")
    seen: set[str] = set()
    results: list[CLITool] = []


    for dir_path in path_dirs:
        if not dir_path or not os.path.isdir(dir_path):
            continue
        try:
            entries = os.listdir(dir_path)
        except PermissionError:
            continue

        for entry in entries:
            if entry in seen:
                continue
            full_path = os.path.join(dir_path, entry)
            if not os.path.isfile(full_path):
                continue
            # Check if executable
            if not os.access(full_path, os.X_OK):
                continue
            # Skip common non-tool files
            if entry.endswith((".py", ".pyc", ".sh", ".conf", ".txt", ".md", ".so")):
                continue
            seen.add(entry)
            description = _TOOL_DESCRIPTIONS.get(entry, f"CLI tool: {entry}")
            results.append(
                CLITool(
                    name=entry,
                    path=full_path,
                    version=None,
                    description=description,
                )
            )

    # Sort alphabetically
    results.sort(key=lambda t: t.name)
    _cache.set("cli_tools", results)
    return results


async def scan_services() -> list[ServiceInfo]:
    """Scan listening TCP services using 'ss -tlnp'."""
    cached = _cache.get("services")
    if cached is not None:
        return cached

    stdout, stderr, rc = await _run_cmd(["ss", "-tlnp"])
    if rc != 0:
        logger.error("ss -tlnp failed: %s", stderr)
        return []

    results: list[ServiceInfo] = []
    lines = stdout.strip().split("\n")
    for line in lines[1:]:  # Skip header
        parts = line.split()
        if len(parts) < 5:
            continue
        # ss output: State  Recv-Q  Send-Q  Local Address:Port  Peer Address:Port  ...
        state = parts[0]
        local = parts[3]
        # Split address and port
        if ":" in local:
            # Handle IPv6 [::]:port format
            if local.startswith("["):
                bracket_end = local.find("]")
                addr = local[1:bracket_end]
                port_str = local[bracket_end + 2 :]  # skip ]:
            else:
                addr, port_str = local.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                continue
        else:
            continue

        # Extract process info from the last column (users:(("name",pid=N,fd=N)))
        process_name = None
        pid = None
        if len(parts) >= 6:
            info_str = " ".join(parts[5:])
            if 'users:(("' in info_str:
                try:
                    start = info_str.index('users:(("') + 9
                    end = info_str.index('"', start)
                    process_name = info_str[start:end]
                    # Extract PID
                    pid_start = info_str.index("pid=", end) + 4
                    pid_end = info_str.index(",", pid_start)
                    pid = int(info_str[pid_start:pid_end])
                except (ValueError, IndexError):
                    pass

        # Build a useful description
        addr_display = "0.0.0.0" if addr == "*" else addr
        desc = f"{process_name or 'unknown'} listening on {addr_display}:{port}"
        if process_name:
            desc = f"{process_name} (PID {pid}) — port {port}"

        results.append(
            ServiceInfo(
                protocol="tcp",
                local_address=addr,
                local_port=port,
                state=state,
                process_name=process_name,
                pid=pid,
                description=desc,
            )
        )

    results.sort(key=lambda s: s.local_port)
    _cache.set("services", results)
    return results


# ── Built-in Adapters ───────────────────────────────────────────────────


class ProcessAdapter(SoftwareAdapter):
    """Adapter that exposes the process scanner."""

    def __init__(self):
        super().__init__("process", "Discover and inspect running processes")

    async def scan(self) -> list[dict[str, Any]]:
        procs = await scan_processes()
        return [p.model_dump() for p in procs]

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        if action == "kill":
            pid = params.get("pid")
            if not pid:
                return {"error": "missing 'pid' parameter"}
            try:
                os.kill(int(pid), 15)
                return {"message": f"Sent SIGTERM to PID {pid}"}
            except ProcessLookupError:
                return {"error": f"Process {pid} not found"}
            except PermissionError:
                return {"error": f"Permission denied for PID {pid}"}
        elif action == "info":
            pid = params.get("pid")
            if not pid:
                return {"error": "missing 'pid' parameter"}
            procs = await scan_processes()
            for p in procs:
                if p.pid == int(pid):
                    return p.model_dump()
            return {"error": f"Process {pid} not found"}
        return {"error": f"Unknown action: {action}"}


class CLIToolAdapter(SoftwareAdapter):
    """Adapter that exposes CLI tool scanning."""

    def __init__(self):
        super().__init__("cli-tools", "Discover command-line tools on PATH")

    async def scan(self) -> list[dict[str, Any]]:
        tools = await scan_cli_tools()
        return [t.model_dump() for t in tools]

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        if action == "help":
            tool_name = params.get("name")
            if not tool_name:
                return {"error": "missing 'name' parameter"}
            tool_path = shutil.which(tool_name)
            if not tool_path:
                return {"error": f"Tool '{tool_name}' not found on PATH"}
            stdout, stderr, rc = await _run_cmd(
                [tool_path, "--help"], timeout=5.0
            )
            return {
                "tool": tool_name,
                "help": stdout[:2000] if rc == 0 else stderr[:2000],
            }
        elif action == "which":
            tool_name = params.get("name")
            if not tool_name:
                return {"error": "missing 'name' parameter"}
            tool_path = shutil.which(tool_name)
            return {"name": tool_name, "path": tool_path, "found": tool_path is not None}
        elif action == "version":
            tool_name = params.get("name")
            if not tool_name:
                return {"error": "missing 'name' parameter"}
            tool_path = shutil.which(tool_name)
            if not tool_path:
                return {"error": f"Tool '{tool_name}' not found on PATH"}
            for flag in ("--version", "-v"):
                stdout, stderr, rc = await _run_cmd([tool_path, flag], timeout=3.0)
                if rc == 0 and stdout.strip():
                    return {"tool": tool_name, "version": stdout.strip().split(chr(10))[0][:200]}
            return {"tool": tool_name, "version": None, "note": "No version output"}
        return {"error": f"Unknown action: {action}"}


class ServiceAdapter(SoftwareAdapter):
    """Adapter that exposes local service scanning."""

    def __init__(self):
        super().__init__("services", "Discover local listening services")

    async def scan(self) -> list[dict[str, Any]]:
        services = await scan_services()
        return [s.model_dump() for s in services]

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        if action == "connections":
            port = params.get("port")
            if port:
                stdout, stderr, rc = await _run_cmd(
                    ["ss", "-tnp", "sport", "=", str(port)], timeout=5.0
                )
            else:
                stdout, stderr, rc = await _run_cmd(
                    ["ss", "-tnp"], timeout=5.0
                )
            if rc == 0:
                return {"connections": stdout.strip()}
            return {"error": stderr.strip()}
        return {"error": f"Unknown action: {action}"}


# Auto-register built-in adapters
register_adapter(ProcessAdapter())
register_adapter(CLIToolAdapter())
register_adapter(ServiceAdapter())


# ── API Endpoints ────────────────────────────────────────────────────────


@router.get("/scan", response_model=ScanResult)
async def api_full_scan():
    """Run a full system scan — processes, CLI tools, and services."""
    start = time.time()
    procs, tools, services = await asyncio.gather(
        scan_processes(),
        scan_cli_tools(),
        scan_services(),
    )
    return ScanResult(
        scan_time=round(time.time() - start, 3),
        processes=procs,
        cli_tools=tools,
        services=services,
        adapters=list(_adapter_registry.keys()),
    )


@router.get("/processes", response_model=list[ProcessInfo])
async def api_list_processes():
    """List all running processes."""
    return await scan_processes()


@router.get("/cli-tools", response_model=list[CLITool])
async def api_list_cli_tools():
    """List all CLI tools found on PATH."""
    return await scan_cli_tools()


@router.get("/services", response_model=list[ServiceInfo])
async def api_list_services():
    """List all locally listening services."""
    return await scan_services()


@router.get("/adapters", response_model=list[AdapterInfo])
async def api_list_adapters():
    """List all registered software adapters."""
    return [a.to_info() for a in _adapter_registry.values()]


@router.post(
    "/adapters/{name}/execute",
    response_model=AdapterExecuteResponse,
)
async def api_execute_adapter(name: str, req: AdapterExecuteRequest):
    """Execute an action through a named adapter."""
    adapter = _adapter_registry.get(name)
    if not adapter:
        raise HTTPException(
            status_code=404,
            detail=f"Adapter '{name}' not found. Available: {list(_adapter_registry.keys())}",
        )
    try:
        result = await adapter.execute(req.action, req.params)
        error = None
        success = True
        if isinstance(result, dict) and "error" in result:
            error = result["error"]
            success = False
        return AdapterExecuteResponse(
            adapter=name,
            action=req.action,
            success=success,
            result=result,
            error=error,
        )
    except Exception as e:
        logger.exception("Adapter %s execute failed", name)
        return AdapterExecuteResponse(
            adapter=name,
            action=req.action,
            success=False,
            error=str(e),
        )


@router.get("/health")
async def api_discovery_health():
    """Discovery engine health check."""
    return {
        "status": "ok",
        "adapters_registered": len(_adapter_registry),
        "cache_ttl_seconds": CACHE_TTL,
    }
