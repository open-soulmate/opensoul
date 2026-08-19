"""OpenGene Adapter Template System — one-click integration configuration.

Provides a library of pre-built templates for common software integrations,
template instantiation (template → running adapter), a capability registry
so agents can discover what each integration can do, and a dashboard endpoint
for integration stats.

Templates are stored in memory with optional JSON persistence.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()
@router.get("/health")
async def gene_templates_health():
    """GeneTemplates health check."""
    return {"status": "ok", "component": "GeneTemplates"}
logger = logging.getLogger(__name__)

# ── Persistence ──────────────────────────────────────────────────────────

_PERSIST_DIR = Path(os.path.expanduser("~/.openmate"))
_PERSIST_FILE = _PERSIST_DIR / "gene_templates.json"


def _load_persisted() -> dict[str, Any]:
    """Load custom templates + instances from disk."""
    if _PERSIST_FILE.exists():
        try:
            return json.loads(_PERSIST_FILE.read_text())
        except Exception as e:
            logger.warning("Failed to load gene_templates persistence: %s", e)
    return {}


def _save_persisted() -> None:
    """Persist custom templates + instances to disk."""
    _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "custom_templates": {k: v for k, v in _templates.items() if not v.get("builtin")},
        "instances": _instances,
        "capabilities": _capabilities,
    }
    try:
        _PERSIST_FILE.write_text(json.dumps(data, indent=2, default=str))
    except Exception as e:
        logger.warning("Failed to persist gene_templates: %s", e)


# ── Template Data Model ──────────────────────────────────────────────────


class TemplateParam(BaseModel):
    """A single configurable parameter in a template."""
    name: str
    label: str
    type: str = "string"          # string, int, float, bool, select, password
    description: str = ""
    default: Any = None
    required: bool = False
    options: list[str] = []       # for type="select"
    placeholder: str = ""


class AdapterTemplate(BaseModel):
    """Definition of an integration template."""
    template_id: str
    name: str
    description: str
    adapter_type: str             # rest, database, cli, rpa, filesystem
    icon: str = "🔌"
    builtin: bool = True
    params: list[TemplateParam] = []
    capabilities: list[str] = []  # natural-language capability descriptions
    adapter_class: str = ""       # internal: which adapter class to use
    configure_action: str = ""    # internal: the 'configure' params template
    tags: list[str] = []


class AdapterInstance(BaseModel):
    """A running adapter created from a template."""
    instance_id: str
    template_id: str
    name: str
    adapter_type: str
    status: str = "active"        # active, error, stopped
    config: dict[str, Any] = {}
    capabilities: list[str] = []
    created_at: float = 0.0
    last_activity: float = 0.0
    activity_count: int = 0


class CapabilityEntry(BaseModel):
    """A registered capability — what an integration can do."""
    capability_id: str
    adapter_instance_id: str
    adapter_name: str
    adapter_type: str
    description: str              # natural language, e.g. "Can query PostgreSQL databases"
    actions: list[str] = []       # specific actions available
    tags: list[str] = []


# ── In-Memory Stores ────────────────────────────────────────────────────

_templates: dict[str, dict[str, Any]] = {}
_instances: dict[str, dict[str, Any]] = {}
_capabilities: dict[str, dict[str, Any]] = {}

# Reference to soma_discovery adapter registry (lazy import)
_adapter_registry = None


def _get_adapter_registry():
    """Get the soma_discovery adapter registry (lazy)."""
    global _adapter_registry
    if _adapter_registry is None:
        try:
            from src.api.soma_discovery import _adapter_registry as reg
            _adapter_registry = reg
        except ImportError:
            _adapter_registry = {}
    return _adapter_registry


# ── Preset Templates ────────────────────────────────────────────────────


def _build_preset_templates() -> dict[str, dict[str, Any]]:
    """Build the library of pre-built templates."""
    presets: list[dict[str, Any]] = [
        # ── REST API Adapters ───────────────────────────────────────
        {
            "template_id": "github-api",
            "name": "GitHub API",
            "description": "Connect to GitHub REST API v3. Manage repositories, issues, pull requests, and more.",
            "adapter_type": "rest",
            "icon": "🐙",
            "builtin": True,
            "adapter_class": "rest",
            "tags": ["git", "api", "devops", "ci-cd"],
            "params": [
                {"name": "base_url", "label": "API Base URL", "type": "string",
                 "default": "https://api.github.com", "required": True,
                 "description": "GitHub API endpoint"},
                {"name": "auth_token", "label": "Personal Access Token", "type": "password",
                 "required": False, "placeholder": "ghp_...",
                 "description": "GitHub PAT for authenticated requests"},
                {"name": "headers", "label": "Extra Headers", "type": "string",
                 "default": "", "description": "Additional headers (JSON)"},
            ],
            "capabilities": [
                "List and search GitHub repositories",
                "Create and manage issues and pull requests",
                "Read file contents from repositories",
                "Trigger and monitor GitHub Actions workflows",
                "Manage repository collaborators and permissions",
            ],
            "capability_actions": ["request", "probe"],
        },
        {
            "template_id": "web-monitor",
            "name": "Web URL Monitor",
            "description": "Monitor website health by sending HTTP requests and checking response status, latency, and content.",
            "adapter_type": "rest",
            "icon": "🌐",
            "builtin": True,
            "adapter_class": "rest",
            "tags": ["monitoring", "health", "uptime"],
            "params": [
                {"name": "base_url", "label": "Target URL", "type": "string",
                 "required": True, "placeholder": "https://example.com",
                 "description": "URL to monitor"},
                {"name": "timeout", "label": "Timeout (seconds)", "type": "float",
                 "default": 10.0, "description": "Request timeout"},
                {"name": "headers", "label": "Custom Headers", "type": "string",
                 "default": "", "description": "Request headers (JSON)"},
            ],
            "capabilities": [
                "Check if a website is reachable and responding",
                "Measure HTTP response time and latency",
                "Verify expected HTTP status codes",
                "Inspect response headers and body content",
            ],
            "capability_actions": ["request", "probe"],
        },
        {
            "template_id": "generic-rest",
            "name": "Generic REST API",
            "description": "Connect to any REST API. Configure base URL, authentication, and custom headers.",
            "adapter_type": "rest",
            "icon": "🔗",
            "builtin": True,
            "adapter_class": "rest",
            "tags": ["api", "generic"],
            "params": [
                {"name": "base_url", "label": "Base URL", "type": "string",
                 "required": True, "description": "API base URL"},
                {"name": "auth_token", "label": "Auth Token", "type": "password",
                 "required": False, "description": "Bearer token"},
                {"name": "timeout", "label": "Timeout", "type": "float",
                 "default": 30.0},
            ],
            "capabilities": [
                "Send HTTP requests (GET, POST, PUT, DELETE) to the configured API",
                "Auto-discover API schema via OpenAPI/Swagger",
                "Handle authentication with bearer tokens",
            ],
            "capability_actions": ["request", "probe", "configure"],
        },
        # ── Database Adapters ───────────────────────────────────────
        {
            "template_id": "postgresql",
            "name": "PostgreSQL",
            "description": "Connect to a PostgreSQL database. Query tables, inspect schemas, run read-only SQL.",
            "adapter_type": "database",
            "icon": "🐘",
            "builtin": True,
            "adapter_class": "database",
            "tags": ["database", "sql", "postgres"],
            "params": [
                {"name": "connection_string", "label": "Connection String", "type": "string",
                 "required": True, "placeholder": "postgresql://user:pass@localhost:5432/dbname",
                 "description": "PostgreSQL connection DSN"},
                {"name": "max_queries", "label": "Max Rows", "type": "int",
                 "default": 100, "description": "Maximum rows returned per query"},
            ],
            "capabilities": [
                "Query PostgreSQL tables with read-only SQL SELECT statements",
                "List all tables and their column schemas",
                "Describe table structure (columns, types, constraints)",
                "Execute EXPLAIN to analyze query plans",
            ],
            "capability_actions": ["configure", "query", "tables", "describe"],
            "configure_params": {"db_type": "postgresql"},
        },
        {
            "template_id": "sqlite",
            "name": "SQLite",
            "description": "Connect to a local SQLite database file. Lightweight, serverless database for quick queries.",
            "adapter_type": "database",
            "icon": "📦",
            "builtin": True,
            "adapter_class": "database",
            "tags": ["database", "sql", "sqlite", "local"],
            "params": [
                {"name": "connection_string", "label": "Database File Path", "type": "string",
                 "required": True, "placeholder": "/path/to/database.db",
                 "description": "Path to the SQLite database file"},
                {"name": "max_queries", "label": "Max Rows", "type": "int",
                 "default": 100},
            ],
            "capabilities": [
                "Query SQLite database with read-only SQL SELECT statements",
                "List all tables and their schemas",
                "Describe table columns and types using PRAGMA",
                "Read and inspect any local SQLite database file",
            ],
            "capability_actions": ["configure", "query", "tables", "describe"],
            "configure_params": {"db_type": "sqlite"},
        },
        # ── CLI Adapters ────────────────────────────────────────────
        {
            "template_id": "docker",
            "name": "Docker",
            "description": "Manage Docker containers, images, volumes, and networks via the Docker CLI.",
            "adapter_type": "cli",
            "icon": "🐳",
            "builtin": True,
            "adapter_class": "cli-tools",
            "tags": ["containers", "devops", "docker"],
            "params": [
                {"name": "docker_host", "label": "Docker Host", "type": "string",
                 "default": "unix:///var/run/docker.sock",
                 "description": "Docker daemon endpoint"},
                {"name": "compose_dir", "label": "Compose Directory", "type": "string",
                 "default": "", "placeholder": "/path/to/project",
                 "description": "Default docker-compose project directory"},
            ],
            "capabilities": [
                "List, start, stop, and remove Docker containers",
                "Pull, build, and manage Docker images",
                "Inspect container logs and resource usage",
                "Manage Docker volumes and networks",
                "Run docker-compose up/down for multi-container apps",
            ],
            "capability_actions": ["help", "which", "version"],
        },
        {
            "template_id": "git",
            "name": "Git",
            "description": "Interact with Git repositories — clone, commit, push, pull, and inspect history.",
            "adapter_type": "cli",
            "icon": "📂",
            "builtin": True,
            "adapter_class": "cli-tools",
            "tags": ["version-control", "git", "devops"],
            "params": [
                {"name": "repo_path", "label": "Repository Path", "type": "string",
                 "required": False, "placeholder": "/path/to/repo",
                 "description": "Default repository directory"},
                {"name": "remote", "label": "Default Remote", "type": "string",
                 "default": "origin", "description": "Default remote name"},
            ],
            "capabilities": [
                "Clone Git repositories from remote URLs",
                "View commit history and diffs",
                "Create branches, switch, and merge",
                "Stage, commit, and push changes",
                "Inspect repository status and file changes",
            ],
            "capability_actions": ["help", "which", "version"],
        },
        {
            "template_id": "generic-cli",
            "name": "Generic CLI Tool",
            "description": "Wrap any command-line tool for programmatic access via the adapter system.",
            "adapter_type": "cli",
            "icon": "⌨️",
            "builtin": True,
            "adapter_class": "cli-tools",
            "tags": ["cli", "generic"],
            "params": [
                {"name": "tool_name", "label": "Tool Name", "type": "string",
                 "required": True, "placeholder": "kubectl",
                 "description": "Name of the CLI tool"},
            ],
            "capabilities": [
                "Check if the CLI tool is installed and available",
                "Get tool version information",
                "Access tool help documentation",
            ],
            "capability_actions": ["help", "which", "version"],
        },
        # ── RPA Adapters ────────────────────────────────────────────
        {
            "template_id": "browser-automation",
            "name": "Browser Automation",
            "description": "Automate browser interactions — take screenshots, click elements, type text, read screen content via OCR.",
            "adapter_type": "rpa",
            "icon": "🖥️",
            "builtin": True,
            "adapter_class": "rpa",
            "tags": ["browser", "automation", "rpa", "gui"],
            "params": [
                {"name": "display_server", "label": "Display Server", "type": "select",
                 "options": ["auto", "wayland", "x11"], "default": "auto",
                 "description": "Display server type (auto-detected by default)"},
                {"name": "ocr_lang", "label": "OCR Language", "type": "string",
                 "default": "eng+chi_sim", "description": "Tesseract language codes"},
            ],
            "capabilities": [
                "Take screenshots of the current screen",
                "Perform OCR (optical character recognition) on screen content",
                "Simulate keyboard typing and key presses",
                "Click at specific screen coordinates",
                "Move and drag the mouse cursor",
                "Find and click on text visible on screen",
                "Wait for specific text to appear on screen",
                "List and manage application windows",
            ],
            "capability_actions": ["screenshot", "ocr", "type", "key", "click", "mousemove", "clicktext"],
        },
        # ── Filesystem Adapters ─────────────────────────────────────
        {
            "template_id": "file-backup",
            "name": "File Backup / Sync",
            "description": "Monitor and sync directories. Track file changes, list files, read content, and watch for modifications.",
            "adapter_type": "filesystem",
            "icon": "💾",
            "builtin": True,
            "adapter_class": "filesystem",
            "tags": ["backup", "sync", "files", "monitoring"],
            "params": [
                {"name": "directory", "label": "Directory Path", "type": "string",
                 "required": True, "placeholder": "~/Documents",
                 "description": "Directory to monitor"},
                {"name": "watch", "label": "Enable Watch", "type": "bool",
                 "default": True, "description": "Start background file watcher"},
                {"name": "max_depth", "label": "Max Depth", "type": "int",
                 "default": 5, "description": "Maximum directory recursion depth"},
            ],
            "capabilities": [
                "List files and directories with glob pattern filtering",
                "Read file contents (up to 1MB)",
                "Search for files by name or content",
                "Monitor directory for real-time file changes (create, modify, delete)",
                "Get detailed file information (size, timestamps, permissions)",
                "Track change history over time",
            ],
            "capability_actions": ["configure", "list", "read", "search", "info", "changes"],
        },
    ]

    result: dict[str, dict[str, Any]] = {}
    for t in presets:
        template_id = t["template_id"]
        # Convert params to dicts
        params_list = []
        for p in t.get("params", []):
            if isinstance(p, dict):
                params_list.append(p)
            else:
                params_list.append(p if isinstance(p, dict) else p.dict())
        t["params"] = params_list
        t["created_at"] = time.time()
        result[template_id] = t

    return result


# ── Request / Response Schemas ───────────────────────────────────────────


class InstantiateRequest(BaseModel):
    """Request to instantiate a template."""
    name: str = ""                    # friendly name for the instance
    params: dict[str, Any] = {}       # user-provided parameter values
    auto_configure: bool = True       # automatically call adapter.configure()


class CreateTemplateRequest(BaseModel):
    """Request to create a custom template."""
    template_id: str = ""
    name: str
    description: str = ""
    adapter_type: str                 # rest, database, cli, rpa, filesystem
    icon: str = "🔌"
    params: list[dict[str, Any]] = []
    capabilities: list[str] = []
    tags: list[str] = []
    adapter_class: str = ""           # maps to soma_discovery adapter name
    configure_params: dict[str, Any] = {}


# ── Initialization ───────────────────────────────────────────────────────


def _init():
    """Load presets and persisted data."""
    global _templates, _instances, _capabilities

    # 1. Load presets
    _templates.update(_build_preset_templates())

    # 2. Overlay persisted custom data
    persisted = _load_persisted()
    for tid, tdata in persisted.get("custom_templates", {}).items():
        _templates[tid] = tdata
    _instances.update(persisted.get("instances", {}))
    _capabilities.update(persisted.get("capabilities", {}))

    logger.info("Gene Templates: loaded %d templates, %d instances, %d capabilities",
                len(_templates), len(_instances), len(_capabilities))


_init()


# ── Helper: Instantiate Adapter ──────────────────────────────────────────


async def _instantiate_adapter(
    template: dict[str, Any],
    user_params: dict[str, Any],
) -> dict[str, Any]:
    """Create a running adapter from a template.

    Merges default params with user params, finds the matching soma_discovery
    adapter, and calls its 'configure' action.
    """
    adapter_class = template.get("adapter_class", "")
    if not adapter_class:
        return {"error": "Template has no adapter_class defined"}

    registry = _get_adapter_registry()
    adapter = registry.get(adapter_class)
    if not adapter:
        return {"error": f"Adapter '{adapter_class}' not found in registry"}

    # Build configure params: template defaults + user overrides
    config_params: dict[str, Any] = {}
    for p in template.get("params", []):
        pname = p["name"]
        default = p.get("default")
        if pname in user_params:
            config_params[pname] = user_params[pname]
        elif default is not None:
            config_params[pname] = default

    # Merge any template-level configure_params (e.g. db_type for PostgreSQL)
    template_configure = template.get("configure_params", {})
    config_params.update(template_configure)

    # For adapters that need specific 'configure' action params, remap
    adapter_type = template.get("adapter_type", "")

    if adapter_type == "rest":
        configure_payload = {
            "base_url": config_params.get("base_url", ""),
            "auth_token": config_params.get("auth_token"),
            "headers": {},
        }
        # Parse headers if string
        h = config_params.get("headers", "")
        if isinstance(h, str) and h.strip():
            try:
                configure_payload["headers"] = json.loads(h)
            except json.JSONDecodeError:
                pass
        elif isinstance(h, dict):
            configure_payload["headers"] = h
        if config_params.get("timeout"):
            configure_payload["timeout"] = config_params["timeout"]

    elif adapter_type == "database":
        configure_payload = {
            "db_type": config_params.get("db_type", "sqlite"),
            "connection_string": config_params.get("connection_string", ""),
            "max_queries": config_params.get("max_queries", 100),
        }

    elif adapter_type == "filesystem":
        configure_payload = {
            "directory": config_params.get("directory", ""),
            "watch": config_params.get("watch", False),
            "max_depth": config_params.get("max_depth", 5),
        }

    elif adapter_type == "cli":
        # CLI adapters don't have a 'configure' action — just verify they exist
        tool_name = config_params.get("tool_name") or template.get("params", [{}])[0].get("name", "")
        # Try to get version as a health check
        try:
            result = await adapter.execute("which", {"name": tool_name})
            return {"status": "configured", "tool_check": result, "config": config_params}
        except Exception as e:
            return {"status": "configured_with_warning", "warning": str(e), "config": config_params}

    elif adapter_type == "rpa":
        # RPA doesn't need a 'configure' action — it auto-detects
        return {"status": "configured", "config": config_params}

    else:
        configure_payload = config_params

    # Call adapter's configure action
    try:
        result = await adapter.execute("configure", configure_payload)
        if isinstance(result, dict) and result.get("error"):
            return {"error": result["error"], "config": configure_payload}
        return {"status": "configured", "result": result, "config": configure_payload}
    except Exception as e:
        logger.exception("Adapter instantiation failed for %s", adapter_class)
        return {"error": str(e), "config": configure_payload}


def _register_capabilities(
    instance_id: str,
    template: dict[str, Any],
    instance_name: str,
) -> list[str]:
    """Register capabilities for a new adapter instance."""
    cap_ids: list[str] = []
    cap_descriptions = template.get("capabilities", [])
    adapter_type = template.get("adapter_type", "unknown")

    for desc in cap_descriptions:
        cap_id = f"cap-{uuid.uuid4().hex[:8]}"
        entry = {
            "capability_id": cap_id,
            "adapter_instance_id": instance_id,
            "adapter_name": instance_name,
            "adapter_type": adapter_type,
            "description": desc,
            "actions": template.get("capability_actions", []),
            "tags": template.get("tags", []),
            "registered_at": time.time(),
        }
        _capabilities[cap_id] = entry
        cap_ids.append(cap_id)

    return cap_ids


# ── API Endpoints ────────────────────────────────────────────────────────


@router.get("/adapter-templates")
async def list_templates(
    adapter_type: str | None = None,
    tag: str | None = None,
):
    """List all available templates (built-in + custom).

    Filter by adapter_type (rest/database/cli/rpa/filesystem) or tag.
    """
    results = []
    for tid, t in _templates.items():
        if adapter_type and t.get("adapter_type") != adapter_type:
            continue
        if tag and tag not in t.get("tags", []):
            continue
        # Strip internal fields
        display = {k: v for k, v in t.items()
                   if k not in ("configure_params", "capability_actions", "adapter_class")}
        results.append(display)

    return {
        "templates": results,
        "count": len(results),
        "adapter_types": list(set(t.get("adapter_type", "") for t in _templates.values())),
    }


@router.get("/adapter-templates/{template_id}")
async def get_template(template_id: str):
    """Get full template details including parameters and capabilities."""
    t = _templates.get(template_id)
    if not t:
        raise HTTPException(404, f"Template '{template_id}' not found")
    return t


@router.post("/adapter-templates")
async def create_custom_template(req: CreateTemplateRequest):
    """Create a custom integration template.

    Custom templates can be deleted (unlike built-in ones).
    """
    tid = req.template_id or f"custom-{uuid.uuid4().hex[:8]}"

    if tid in _templates:
        raise HTTPException(409, f"Template '{tid}' already exists")

    template = {
        "template_id": tid,
        "name": req.name,
        "description": req.description,
        "adapter_type": req.adapter_type,
        "icon": req.icon,
        "builtin": False,
        "params": req.params,
        "capabilities": req.capabilities,
        "tags": req.tags,
        "adapter_class": req.adapter_class or req.adapter_type,
        "configure_params": req.configure_params,
        "capability_actions": [],
        "created_at": time.time(),
    }

    _templates[tid] = template
    _save_persisted()

    logger.info("Created custom template: %s (%s)", tid, req.name)
    return {"template_id": tid, "name": req.name, "status": "created"}


@router.delete("/adapter-templates/{template_id}")
async def delete_template(template_id: str):
    """Delete a custom template. Built-in templates cannot be deleted."""
    t = _templates.get(template_id)
    if not t:
        raise HTTPException(404, f"Template '{template_id}' not found")
    if t.get("builtin"):
        raise HTTPException(403, "Built-in templates cannot be deleted")

    del _templates[template_id]
    _save_persisted()

    return {"template_id": template_id, "status": "deleted"}


@router.post("/adapter-templates/{template_id}/instantiate")
async def instantiate_template(template_id: str, req: InstantiateRequest):
    """Create a running adapter instance from a template.

    1. Merge user params with template defaults
    2. Configure the underlying adapter
    3. Register capabilities
    4. Return the instance ID
    """
    template = _templates.get(template_id)
    if not template:
        raise HTTPException(404, f"Template '{template_id}' not found")

    # Validate required params
    for p in template.get("params", []):
        if p.get("required") and p["name"] not in req.params and p.get("default") is None:
            raise HTTPException(
                400,
                f"Required parameter '{p['name']}' ({p.get('label', '')}) is missing"
            )

    # Instantiate adapter
    result = await _instantiate_adapter(template, req.params)

    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(
            500,
            f"Adapter instantiation failed: {result['error']}"
        )

    # Create instance record
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    instance_name = req.name or f"{template['name']} ({instance_id[-4:]})"

    # Merge final config (template defaults + user params)
    final_config: dict[str, Any] = {}
    for p in template.get("params", []):
        pname = p["name"]
        if pname in req.params:
            final_config[pname] = req.params[pname]
        elif p.get("default") is not None:
            final_config[pname] = p["default"]
    final_config.update(template.get("configure_params", {}))

    instance = {
        "instance_id": instance_id,
        "template_id": template_id,
        "name": instance_name,
        "adapter_type": template.get("adapter_type", "unknown"),
        "status": "active",
        "config": final_config,
        "capabilities": [],
        "created_at": time.time(),
        "last_activity": time.time(),
        "activity_count": 0,
    }

    # Register capabilities
    if req.auto_configure:
        cap_ids = _register_capabilities(instance_id, template, instance_name)
        instance["capabilities"] = cap_ids

    _instances[instance_id] = instance
    _save_persisted()

    logger.info("Instantiated template %s → instance %s (%s)",
                template_id, instance_id, instance_name)

    return {
        "instance_id": instance_id,
        "name": instance_name,
        "template_id": template_id,
        "adapter_type": template.get("adapter_type"),
        "status": "active",
        "capabilities_count": len(instance["capabilities"]),
        "configure_result": result,
    }


@router.get("/instances")
async def list_instances():
    """List all running adapter instances."""
    return {
        "instances": list(_instances.values()),
        "count": len(_instances),
    }


@router.delete("/instances/{instance_id}")
async def delete_instance(instance_id: str):
    """Remove an adapter instance and its capabilities."""
    inst = _instances.get(instance_id)
    if not inst:
        raise HTTPException(404, f"Instance '{instance_id}' not found")

    # Remove associated capabilities
    cap_ids_to_remove = [
        cid for cid, c in _capabilities.items()
        if c.get("adapter_instance_id") == instance_id
    ]
    for cid in cap_ids_to_remove:
        del _capabilities[cid]

    del _instances[instance_id]
    _save_persisted()

    return {
        "instance_id": instance_id,
        "status": "deleted",
        "capabilities_removed": len(cap_ids_to_remove),
    }


@router.get("/capabilities")
async def list_capabilities(
    adapter_type: str | None = None,
    search: str | None = None,
):
    """List all registered capabilities.

    Agents query this to discover what integrations are available and
    what they can do. Filter by adapter_type or free-text search.
    """
    results = []
    for cid, cap in _capabilities.items():
        if adapter_type and cap.get("adapter_type") != adapter_type:
            continue
        if search and search.lower() not in cap.get("description", "").lower():
            continue
        results.append(cap)

    # Deduplicate by description for cleaner output
    seen_desc: set[str] = set()
    unique_results = []
    for r in results:
        desc = r["description"]
        if desc not in seen_desc:
            seen_desc.add(desc)
            unique_results.append(r)

    return {
        "capabilities": unique_results,
        "count": len(unique_results),
        "adapter_types": list(set(c.get("adapter_type", "") for c in _capabilities.values())),
    }


@router.get("/dashboard")
async def get_dashboard():
    """Integration dashboard — statistics and recent activity."""
    # Count by adapter type
    type_counts: dict[str, int] = {}
    for inst in _instances.values():
        atype = inst.get("adapter_type", "unknown")
        type_counts[atype] = type_counts.get(atype, 0) + 1

    # Count templates (built-in vs custom)
    builtin_count = sum(1 for t in _templates.values() if t.get("builtin"))
    custom_count = sum(1 for t in _templates.values() if not t.get("builtin"))

    # Recent activity (last 10 instances by creation time)
    recent = sorted(
        _instances.values(),
        key=lambda i: i.get("created_at", 0),
        reverse=True,
    )[:10]

    # Status breakdown
    status_counts: dict[str, int] = {}
    for inst in _instances.values():
        s = inst.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    return {
        "summary": {
            "total_templates": len(_templates),
            "builtin_templates": builtin_count,
            "custom_templates": custom_count,
            "total_instances": len(_instances),
            "total_capabilities": len(_capabilities),
        },
        "instances_by_type": type_counts,
        "instances_by_status": status_counts,
        "recent_instances": [
            {
                "instance_id": i["instance_id"],
                "name": i["name"],
                "template_id": i["template_id"],
                "adapter_type": i.get("adapter_type"),
                "status": i.get("status"),
                "created_at": i.get("created_at"),
            }
            for i in recent
        ],
        "available_adapter_types": sorted(set(
            t.get("adapter_type", "") for t in _templates.values()
        )),
    }


@router.get("/adapter-health")
async def health():
    """Gene Templates subsystem health check."""
    return {
        "status": "ok",
        "templates": len(_templates),
        "instances": len(_instances),
        "capabilities": len(_capabilities),
    }
