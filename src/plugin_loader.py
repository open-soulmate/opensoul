"""Plugin Loader — auto-discovers and loads plugins from ~/.openmate/plugins/."""

import json
import importlib.util
import logging
import os
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path.home() / ".openmate" / "plugins"

loaded_plugins: list[dict] = []


def discover_plugins() -> list[dict]:
    """Discover all plugins with valid manifest.json."""
    plugins = []
    if not PLUGIN_DIR.exists():
        return plugins

    for plugin_path in sorted(PLUGIN_DIR.iterdir()):
        manifest_path = plugin_path / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            manifest["_path"] = str(plugin_path)
            plugins.append(manifest)
        except Exception as e:
            logger.warning(f"Failed to load plugin manifest at {manifest_path}: {e}")

    return plugins


def load_plugin_backend(plugin_path: str) -> APIRouter | None:
    """Load a plugin's backend.py as a FastAPI router."""
    backend_file = os.path.join(plugin_path, "backend.py")
    if not os.path.exists(backend_file):
        return None

    try:
        spec = importlib.util.spec_from_file_location("plugin_backend", backend_file)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, "router"):
            return module.router
        else:
            logger.warning(f"Plugin backend at {backend_file} has no 'router' attribute")
            return None
    except Exception as e:
        logger.error(f"Failed to load plugin backend from {backend_file}: {e}")
        return None


def load_all_plugins(app):
    """Discover and mount all plugin routers."""
    global loaded_plugins
    plugins = discover_plugins()
    loaded_plugins = []

    for manifest in plugins:
        plugin_id = manifest.get("id", "unknown")
        plugin_path = manifest.get("_path", "")
        api_prefix = manifest.get("api_prefix", f"/api/plugins/{plugin_id}")

        router = load_plugin_backend(plugin_path)
        if router:
            app.include_router(router, prefix=api_prefix, tags=[f"plugin-{plugin_id}"])
            logger.info(f"Loaded plugin: {plugin_id} at {api_prefix}")
        else:
            logger.info(f"Plugin {plugin_id}: no backend (frontend-only)")

        loaded_plugins.append({
            "id": plugin_id,
            "name": manifest.get("name", plugin_id),
            "version": manifest.get("version", "0.0.0"),
            "description": manifest.get("description", ""),
            "type": manifest.get("type", "general"),
            "sidebar": manifest.get("sidebar", []),
            "api_prefix": api_prefix,
            "has_backend": router is not None,
            "config": manifest.get("config", {}),
        })

    logger.info(f"Loaded {len(loaded_plugins)} plugins")
    return loaded_plugins
