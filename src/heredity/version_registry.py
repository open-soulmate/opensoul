"""Version Registry — Track component versions, dependencies, and compatibility."""

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class VersionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class MigrationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class ComponentVersion:
    component_id: str
    component_name: str
    version: str  # semver: MAJOR.MINOR.PATCH
    status: VersionStatus = VersionStatus.ACTIVE
    release_notes: str = ""
    dependencies: dict[str, str] = field(default_factory=dict)  # component_id -> version_range
    config_schema: dict = field(default_factory=dict)
    breaking_changes: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    retired_at: float | None = None


@dataclass
class MigrationPlan:
    migration_id: str
    component_id: str
    from_version: str
    to_version: str
    steps: list[dict]  # ordered list of migration steps
    status: MigrationStatus = MigrationStatus.PENDING
    dry_run: bool = False
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    rollback_plan: list[dict] = field(default_factory=list)


@dataclass
class ChangelogEntry:
    entry_id: str
    component_id: str
    version: str
    change_type: str  # feature, fix, breaking, deprecation, security
    description: str
    author: str = "system"
    timestamp: float = field(default_factory=time.time)


class VersionRegistry:
    """Central registry for all component versions."""

    def __init__(self):
        self._components: dict[str, list[ComponentVersion]] = {}
        self._migrations: list[MigrationPlan] = []
        self._changelog: list[ChangelogEntry] = []
        self._platform_version = "1.0.0"

    # ── Component Registration ────────────────────────────

    def register(
        self,
        component_id: str,
        component_name: str,
        version: str,
        dependencies: dict[str, str] = None,
        config_schema: dict = None,
        release_notes: str = "",
        breaking_changes: list[str] = None,
    ) -> ComponentVersion:
        """Register a new version for a component."""
        cv = ComponentVersion(
            component_id=component_id,
            component_name=component_name,
            version=version,
            dependencies=dependencies or {},
            config_schema=config_schema or {},
            release_notes=release_notes,
            breaking_changes=breaking_changes or [],
        )
        if component_id not in self._components:
            self._components[component_id] = []
        # Mark previous versions as deprecated
        for existing in self._components[component_id]:
            if existing.status == VersionStatus.ACTIVE:
                existing.status = VersionStatus.DEPRECATED
        self._components[component_id].append(cv)

        # Auto-add changelog
        self._add_changelog(
            component_id,
            version,
            "feature",
            release_notes or f"Registered {component_name} v{version}",
        )
        return cv

    def get_current(self, component_id: str) -> ComponentVersion | None:
        """Get the current active version of a component."""
        versions = self._components.get(component_id, [])
        for v in reversed(versions):
            if v.status in (VersionStatus.ACTIVE, VersionStatus.DRAFT):
                return v
        return versions[-1] if versions else None

    def get_all_versions(self, component_id: str) -> list[ComponentVersion]:
        """Get all versions for a component."""
        return self._components.get(component_id, [])

    def list_components(self) -> list[dict]:
        """List all registered components with their current version."""
        result = []
        for cid, versions in self._components.items():
            current = self.get_current(cid)
            if current:
                result.append(
                    {
                        "component_id": cid,
                        "component_name": current.component_name,
                        "current_version": current.version,
                        "status": current.status.value,
                        "total_versions": len(versions),
                        "dependencies": current.dependencies,
                        "created_at": current.created_at,
                    }
                )
        return result

    # ── Dependency Checking ───────────────────────────────

    def check_compatibility(self, component_id: str) -> dict:
        """Check if a component's dependencies are satisfied."""
        current = self.get_current(component_id)
        if not current:
            return {"compatible": False, "error": "Component not registered"}

        issues = []
        for dep_id, version_range in current.dependencies.items():
            dep_current = self.get_current(dep_id)
            if not dep_current:
                issues.append(
                    {"dependency": dep_id, "issue": "not_registered", "required": version_range}
                )
            elif not self._version_matches(dep_current.version, version_range):
                issues.append(
                    {
                        "dependency": dep_id,
                        "issue": "version_mismatch",
                        "required": version_range,
                        "actual": dep_current.version,
                    }
                )

        return {
            "compatible": len(issues) == 0,
            "component_id": component_id,
            "version": current.version,
            "issues": issues,
        }

    def get_dependency_graph(self) -> dict:
        """Get the full dependency graph of all components."""
        graph = {}
        for cid, versions in self._components.items():
            current = self.get_current(cid)
            if current:
                graph[cid] = {
                    "version": current.version,
                    "dependencies": current.dependencies,
                }
        return graph

    # ── Migration Management ──────────────────────────────

    def create_migration(
        self,
        component_id: str,
        from_version: str,
        to_version: str,
        steps: list[dict] = None,
        dry_run: bool = False,
    ) -> MigrationPlan:
        """Create a migration plan for upgrading a component."""
        migration = MigrationPlan(
            migration_id=f"mig_{uuid.uuid4().hex[:12]}",
            component_id=component_id,
            from_version=from_version,
            to_version=to_version,
            steps=steps
            or [
                {"action": "backup", "description": "Create backup before migration"},
                {"action": "validate", "description": "Validate pre-conditions"},
                {
                    "action": "migrate",
                    "description": f"Apply migration {from_version} → {to_version}",
                },
                {"action": "verify", "description": "Verify post-migration integrity"},
            ],
            dry_run=dry_run,
            rollback_plan=[
                {"action": "restore_backup", "description": "Restore from backup"},
                {"action": "verify_rollback", "description": "Verify rollback integrity"},
            ],
        )
        self._migrations.append(migration)
        return migration

    def execute_migration(self, migration_id: str) -> MigrationPlan:
        """Execute a migration plan."""
        migration = None
        for m in self._migrations:
            if m.migration_id == migration_id:
                migration = m
                break
        if not migration:
            raise ValueError(f"Migration {migration_id} not found")

        migration.status = MigrationStatus.RUNNING
        migration.started_at = time.time()

        # Simulate migration steps
        try:
            for step in migration.steps:
                step["executed_at"] = time.time()
                step["status"] = "completed"

            migration.status = MigrationStatus.COMPLETED
            migration.completed_at = time.time()

            # Update component version
            current = self.get_current(migration.component_id)
            if current:
                self.register(
                    migration.component_id,
                    current.component_name,
                    migration.to_version,
                    dependencies=current.dependencies,
                    release_notes=f"Migrated from {migration.from_version}",
                )

            self._add_changelog(
                migration.component_id,
                migration.to_version,
                "feature",
                f"Migration completed: {migration.from_version} → {migration.to_version}",
            )
        except Exception as e:
            migration.status = MigrationStatus.FAILED
            migration.error = str(e)

        return migration

    def rollback_migration(self, migration_id: str) -> MigrationPlan:
        """Rollback a migration."""
        migration = None
        for m in self._migrations:
            if m.migration_id == migration_id:
                migration = m
                break
        if not migration:
            raise ValueError(f"Migration {migration_id} not found")

        migration.status = MigrationStatus.ROLLED_BACK
        self._add_changelog(
            migration.component_id,
            migration.from_version,
            "fix",
            f"Migration rolled back: {migration.to_version} → {migration.from_version}",
        )
        return migration

    def list_migrations(self, component_id: str = None) -> list[MigrationPlan]:
        """List migrations, optionally filtered by component."""
        if component_id:
            return [m for m in self._migrations if m.component_id == component_id]
        return self._migrations

    # ── Changelog ─────────────────────────────────────────

    def _add_changelog(self, component_id: str, version: str, change_type: str, description: str):
        entry = ChangelogEntry(
            entry_id=f"cl_{uuid.uuid4().hex[:12]}",
            component_id=component_id,
            version=version,
            change_type=change_type,
            description=description,
        )
        self._changelog.append(entry)

    def get_changelog(self, component_id: str = None, limit: int = 50) -> list[dict]:
        """Get changelog entries."""
        entries = self._changelog
        if component_id:
            entries = [e for e in entries if e.component_id == component_id]
        entries = sorted(entries, key=lambda e: e.timestamp, reverse=True)[:limit]
        return [
            {
                "entry_id": e.entry_id,
                "component_id": e.component_id,
                "version": e.version,
                "change_type": e.change_type,
                "description": e.description,
                "author": e.author,
                "timestamp": e.timestamp,
            }
            for e in entries
        ]

    # ── Platform Version ──────────────────────────────────

    def get_platform_version(self) -> dict:
        """Get overall platform version info."""
        return {
            "platform_version": self._platform_version,
            "total_components": len(self._components),
            "total_migrations": len(self._migrations),
            "total_changelog_entries": len(self._changelog),
            "components": self.list_components(),
        }

    def bump_platform_version(self, bump_type: str = "patch") -> str:
        """Bump platform version (major/minor/patch)."""
        parts = self._platform_version.split(".")
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
        if bump_type == "major":
            major += 1
            minor = 0
            patch = 0
        elif bump_type == "minor":
            minor += 1
            patch = 0
        else:
            patch += 1
        self._platform_version = f"{major}.{minor}.{patch}"
        self._add_changelog(
            "platform",
            self._platform_version,
            "feature",
            f"Platform bumped to {self._platform_version}",
        )
        return self._platform_version

    # ── Stats ─────────────────────────────────────────────

    def get_stats(self) -> dict:
        statuses = {}
        for versions in self._components.values():
            for v in versions:
                statuses[v.status.value] = statuses.get(v.status.value, 0) + 1
        migration_statuses = {}
        for m in self._migrations:
            migration_statuses[m.status.value] = migration_statuses.get(m.status.value, 0) + 1
        return {
            "total_components": len(self._components),
            "total_versions": sum(len(v) for v in self._components.values()),
            "version_statuses": statuses,
            "total_migrations": len(self._migrations),
            "migration_statuses": migration_statuses,
            "total_changelog_entries": len(self._changelog),
            "platform_version": self._platform_version,
        }

    # ── Helpers ───────────────────────────────────────────

    @staticmethod
    def _version_matches(actual: str, constraint: str) -> bool:
        """Simple semver constraint matching. Supports: >=1.0.0, ~1.0, ^1.0.0, =1.0.0."""
        if constraint.startswith(">="):
            return VersionRegistry._parse_ver(actual) >= VersionRegistry._parse_ver(constraint[2:])
        if constraint.startswith("=") or constraint.startswith("=="):
            return actual == constraint.lstrip("=")
        if constraint.startswith("^"):
            # Compatible: same major
            return actual.split(".")[0] == constraint[1:].split(".")[0]
        if constraint.startswith("~"):
            # Same minor
            a_parts = actual.split(".")
            c_parts = constraint[1:].split(".")
            return a_parts[0] == c_parts[0] and a_parts[1] == c_parts[1]
        # Default: exact
        return actual == constraint

    @staticmethod
    def _parse_ver(v: str) -> tuple:
        parts = v.strip().split(".")
        return tuple(int(p) for p in parts) + (0,) * (3 - len(parts))
