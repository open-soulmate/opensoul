"""OpenHeredity API — 遗传链：版本演化中心、插件版本管理、平滑升级、知识库结构迁移。"""

import time
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.heredity.version_registry import VersionRegistry
from src.heredity.migration import MigrationEngine

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
registry = VersionRegistry()
engine = MigrationEngine()

# ── Seed with all known components ─────────────────────────
SEED_COMPONENTS = [
    ("opensoul", "OpenSoul", "0.1.0", {}),
    ("openmate", "OpenMate", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opensoma", "OpenSoma", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opencortex", "OpenCortex", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opennerve", "OpenNerve", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openvein", "OpenVein", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opensense", "OpenSense", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openwill", "OpenWill", "0.1.0", {"opensoul": ">=0.1.0", "opennerve": ">=0.1.0"}),
    ("openvital", "OpenVital", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opengland", "OpenGland", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openimmune", "OpenImmune", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openmarrow", "OpenMarrow", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opengene", "OpenGene", "0.1.0", {}),
    ("openecho", "OpenEcho", "0.1.0", {"opennerve": ">=0.1.0"}),
    ("openmirror", "OpenMirror", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openlink", "OpenLink", "0.1.0", {"opensoul": ">=0.1.0", "opennerve": ">=0.1.0"}),
    ("openhippo", "OpenHippo", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openreflex", "OpenReflex", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openheredity", "OpenHeredity", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openpulse", "OpenPulse", "0.1.0", {}),
]

for cid, cname, ver, deps in SEED_COMPONENTS:
    registry.register(cid, cname, ver, dependencies=deps)


# ── Request Schemas ────────────────────────────────────────

class RegisterRequest(BaseModel):
    component_id: str
    component_name: str
    version: str
    dependencies: dict[str, str] = {}
    config_schema: dict = {}
    release_notes: str = ""
    breaking_changes: list[str] = []


class MigrationCreateRequest(BaseModel):
    component_id: str
    from_version: str
    to_version: str
    steps: list[dict] = []
    dry_run: bool = False


class SchemaRegisterRequest(BaseModel):
    component_id: str
    version: str
    fields: list[dict]


class MigrationScriptRequest(BaseModel):
    migration_type: str
    component_id: str
    from_version: str
    to_version: str
    up_sql: str = ""
    down_sql: str = ""
    transform: str = ""


# ── Health ─────────────────────────────────────────────────

@router.get("/health")
async def health():
    """OpenHeredity health check."""
    return {
        "status": "ok",
        "component": "OpenHeredity",
        "registry": registry.get_stats(),
        "migrations": engine.get_stats(),
    }


# ── Version Registry ──────────────────────────────────────

@router.get("/components")
async def list_components():
    """List all registered components with their current version."""
    return {"components": registry.list_components(), "total": len(registry.list_components())}


@router.post("/components")
async def register_component(req: RegisterRequest):
    """Register a new component version."""
    cv = registry.register(
        req.component_id, req.component_name, req.version,
        dependencies=req.dependencies, config_schema=req.config_schema,
        release_notes=req.release_notes, breaking_changes=req.breaking_changes,
    )
    return {
        "component_id": cv.component_id,
        "version": cv.version,
        "status": cv.status.value,
        "created_at": cv.created_at,
    }


@router.get("/components/{component_id}")
async def get_component(component_id: str):
    """Get component details with all versions."""
    current = registry.get_current(component_id)
    if not current:
        raise HTTPException(404, f"Component {component_id} not found")
    all_versions = registry.get_all_versions(component_id)
    compatibility = registry.check_compatibility(component_id)
    return {
        "component_id": component_id,
        "current": {
            "version": current.version,
            "status": current.status.value,
            "dependencies": current.dependencies,
            "release_notes": current.release_notes,
            "breaking_changes": current.breaking_changes,
            "created_at": current.created_at,
        },
        "versions": [
            {"version": v.version, "status": v.status.value, "created_at": v.created_at}
            for v in all_versions
        ],
        "compatibility": compatibility,
    }


@router.get("/dependencies")
async def get_dependency_graph():
    """Get the full dependency graph."""
    return {"graph": registry.get_dependency_graph()}


@router.get("/compatibility/{component_id}")
async def check_compatibility(component_id: str):
    """Check compatibility for a component."""
    result = registry.check_compatibility(component_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


# ── Migrations ────────────────────────────────────────────

@router.post("/migrations")
async def create_migration(req: MigrationCreateRequest):
    """Create a migration plan."""
    migration = registry.create_migration(
        req.component_id, req.from_version, req.to_version,
        steps=req.steps or None, dry_run=req.dry_run,
    )
    return {
        "migration_id": migration.migration_id,
        "component_id": migration.component_id,
        "from_version": migration.from_version,
        "to_version": migration.to_version,
        "status": migration.status.value,
        "dry_run": migration.dry_run,
        "steps": migration.steps,
    }


@router.get("/migrations")
async def list_migrations(component_id: str = Query(default=None)):
    """List migrations."""
    migrations = registry.list_migrations(component_id=component_id)
    return {
        "migrations": [
            {
                "migration_id": m.migration_id,
                "component_id": m.component_id,
                "from_version": m.from_version,
                "to_version": m.to_version,
                "status": m.status.value,
                "dry_run": m.dry_run,
                "started_at": m.started_at,
                "completed_at": m.completed_at,
                "error": m.error,
            }
            for m in migrations
        ],
        "total": len(migrations),
    }


@router.post("/migrations/{migration_id}/execute")
async def execute_migration(migration_id: str):
    """Execute a migration."""
    try:
        migration = registry.execute_migration(migration_id)
        return {
            "migration_id": migration.migration_id,
            "status": migration.status.value,
            "started_at": migration.started_at,
            "completed_at": migration.completed_at,
        }
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/migrations/{migration_id}/rollback")
async def rollback_migration(migration_id: str):
    """Rollback a migration."""
    try:
        migration = registry.rollback_migration(migration_id)
        return {"migration_id": migration.migration_id, "status": migration.status.value}
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── Schema Migrations ─────────────────────────────────────

@router.post("/schemas")
async def register_schema(req: SchemaRegisterRequest):
    """Register a schema version."""
    sv = engine.register_schema(req.component_id, req.version, req.fields)
    return {"schema_id": sv.schema_id, "component_id": sv.component_id, "version": sv.version}


@router.get("/schemas/{component_id}")
async def get_schema(component_id: str, version: str = Query(default=None)):
    """Get schema for a component."""
    schema = engine.get_schema(component_id, version)
    if not schema:
        raise HTTPException(404, "Schema not found")
    return {
        "schema_id": schema.schema_id,
        "component_id": schema.component_id,
        "version": schema.version,
        "fields": [{"name": f.name, "field_type": f.field_type, "required": f.required, "default": f.default} for f in schema.fields],
    }


@router.get("/schemas/{component_id}/diff")
async def diff_schemas(component_id: str, from_version: str = Query(...), to_version: str = Query(...)):
    """Get diff between two schema versions."""
    diff = engine.diff_schemas(component_id, from_version, to_version)
    return {
        "component_id": component_id,
        "from_version": from_version,
        "to_version": to_version,
        "added": [{"name": f.name, "field_type": f.field_type} for f in diff.added],
        "removed": diff.removed,
        "modified": diff.modified,
    }


@router.post("/scripts")
async def create_script(req: MigrationScriptRequest):
    """Create a migration script."""
    script = engine.create_migration_script(
        req.migration_type, req.component_id, req.from_version, req.to_version,
        up_sql=req.up_sql, down_sql=req.down_sql, transform=req.transform,
    )
    return {"script_id": script.script_id, "applied": script.applied}


@router.get("/scripts")
async def list_scripts(component_id: str = Query(default=None), applied: bool = Query(default=None)):
    """List migration scripts."""
    return {"scripts": engine.list_scripts(component_id=component_id, applied=applied)}


@router.post("/scripts/{script_id}/apply")
async def apply_script(script_id: str):
    """Mark a migration script as applied."""
    try:
        script = engine.apply_script(script_id)
        return {"script_id": script.script_id, "applied": True, "applied_at": script.applied_at}
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── Changelog ─────────────────────────────────────────────

@router.get("/changelog")
async def get_changelog(component_id: str = Query(default=None), limit: int = Query(default=50)):
    """Get changelog."""
    return {"changelog": registry.get_changelog(component_id=component_id, limit=limit)}


# ── Platform Version ──────────────────────────────────────

@router.get("/platform")
async def get_platform():
    """Get platform version info."""
    return registry.get_platform_version()


@router.post("/platform/bump")
async def bump_platform(bump_type: str = Query(default="patch")):
    """Bump platform version."""
    if bump_type not in ("major", "minor", "patch"):
        raise HTTPException(400, "bump_type must be major, minor, or patch")
    new_version = registry.bump_platform_version(bump_type)
    return {"platform_version": new_version, "bump_type": bump_type}
