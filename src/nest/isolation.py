"""Isolation engine — enforce namespace isolation across shared resources.

Ensures tenants cannot access each other's:
- Knowledge bases and documents
- Vector collections (logical partitioning)
- API keys and secrets
- Agents and workflows
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from enum import Enum


class ResourceType(str, Enum):
    KNOWLEDGE_BASE = "knowledge_base"
    DOCUMENT = "document"
    VECTOR_COLLECTION = "vector_collection"
    AGENT = "agent"
    WORKFLOW = "workflow"
    API_KEY = "api_key"
    FILE = "file"
    SESSION = "session"


@dataclass
class IsolationPolicy:
    """Defines isolation rules for a resource type."""
    resource_type: ResourceType
    namespace_scoped: bool = True      # Prefix all IDs with namespace
    cross_tenant_allowed: bool = False  # Can tenants share this resource?
    encryption_enabled: bool = False    # Encrypt at rest per tenant
    audit_access: bool = True           # Log all access attempts


@dataclass
class AccessLog:
    """Audit log entry for cross-boundary access attempts."""
    timestamp: float
    tenant_id: str
    resource_type: str
    resource_id: str
    action: str           # "read", "write", "delete", "list"
    allowed: bool
    reason: str = ""
    source_ip: str = ""


class IsolationEngine:
    """Enforce tenant isolation across all shared resources."""

    # Default policies per resource type
    DEFAULT_POLICIES = {
        ResourceType.KNOWLEDGE_BASE: IsolationPolicy(
            ResourceType.KNOWLEDGE_BASE, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.DOCUMENT: IsolationPolicy(
            ResourceType.DOCUMENT, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.VECTOR_COLLECTION: IsolationPolicy(
            ResourceType.VECTOR_COLLECTION, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.AGENT: IsolationPolicy(
            ResourceType.AGENT, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.WORKFLOW: IsolationPolicy(
            ResourceType.WORKFLOW, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.API_KEY: IsolationPolicy(
            ResourceType.API_KEY, namespace_scoped=True, cross_tenant_allowed=False, encryption_enabled=True
        ),
        ResourceType.FILE: IsolationPolicy(
            ResourceType.FILE, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.SESSION: IsolationPolicy(
            ResourceType.SESSION, namespace_scoped=True, cross_tenant_allowed=True  # Shared sessions allowed
        ),
    }

    def __init__(self):
        self._policies: dict[ResourceType, IsolationPolicy] = dict(self.DEFAULT_POLICIES)
        self._access_log: list[AccessLog] = []
        self._lock = threading.Lock()
        self._max_log = 10000
        self._blocked_attempts = 0

    def namespaced_id(self, namespace: str, resource_id: str) -> str:
        """Create a namespaced resource ID."""
        return f"{namespace}:{resource_id}"

    def extract_namespace(self, namespaced_id: str) -> tuple[str, str]:
        """Extract namespace and original ID from a namespaced ID."""
        parts = namespaced_id.split(":", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return "", namespaced_id

    def check_access(
        self,
        tenant_namespace: str,
        resource_type: ResourceType,
        resource_id: str,
        action: str = "read",
        source_ip: str = "",
    ) -> dict:
        """Check if a tenant can access a resource.

        Returns:
            {"allowed": bool, "reason": str, "namespaced_id": str}
        """
        policy = self._policies.get(resource_type)
        if not policy:
            self._log_access(tenant_namespace, resource_type, resource_id, action, True, "no_policy")
            return {"allowed": True, "reason": "no_policy", "namespaced_id": resource_id}

        # If resource has a namespace prefix, check it matches
        if policy.namespace_scoped and ":" in resource_id:
            resource_ns, _ = self.extract_namespace(resource_id)
            if resource_ns and resource_ns != tenant_namespace:
                self._log_access(
                    tenant_namespace, resource_type, resource_id, action, False,
                    f"namespace mismatch: {resource_ns} != {tenant_namespace}", source_ip
                )
                self._blocked_attempts += 1
                return {
                    "allowed": False,
                    "reason": f"Resource belongs to different tenant (namespace: {resource_ns})",
                    "namespaced_id": resource_id,
                }

        # Build namespaced ID if needed
        namespaced_id = resource_id
        if policy.namespace_scoped and ":" not in resource_id:
            namespaced_id = self.namespaced_id(tenant_namespace, resource_id)

        self._log_access(tenant_namespace, resource_type, resource_id, action, True, "ok", source_ip)
        return {"allowed": True, "reason": "ok", "namespaced_id": namespaced_id}

    def set_policy(self, resource_type: str, **kwargs) -> bool:
        """Update isolation policy for a resource type."""
        try:
            rt = ResourceType(resource_type)
        except ValueError:
            return False

        with self._lock:
            policy = self._policies.get(rt)
            if not policy:
                policy = IsolationPolicy(rt)
                self._policies[rt] = policy
            for key in ("namespace_scoped", "cross_tenant_allowed", "encryption_enabled", "audit_access"):
                if key in kwargs:
                    setattr(policy, key, kwargs[key])
        return True

    def get_policy(self, resource_type: str) -> dict | None:
        try:
            rt = ResourceType(resource_type)
        except ValueError:
            return None
        policy = self._policies.get(rt)
        if not policy:
            return None
        return {
            "resource_type": policy.resource_type.value,
            "namespace_scoped": policy.namespace_scoped,
            "cross_tenant_allowed": policy.cross_tenant_allowed,
            "encryption_enabled": policy.encryption_enabled,
            "audit_access": policy.audit_access,
        }

    def list_policies(self) -> list[dict]:
        return [
            {
                "resource_type": p.resource_type.value,
                "namespace_scoped": p.namespace_scoped,
                "cross_tenant_allowed": p.cross_tenant_allowed,
                "encryption_enabled": p.encryption_enabled,
                "audit_access": p.audit_access,
            }
            for p in self._policies.values()
        ]

    def get_access_log(
        self,
        tenant_id: str | None = None,
        action: str | None = None,
        allowed: bool | None = None,
        limit: int = 100,
    ) -> list[dict]:
        with self._lock:
            entries = list(self._access_log)
        if tenant_id:
            entries = [e for e in entries if e.tenant_id == tenant_id]
        if action:
            entries = [e for e in entries if e.action == action]
        if allowed is not None:
            entries = [e for e in entries if e.allowed == allowed]
        return [
            {
                "timestamp": e.timestamp,
                "tenant_id": e.tenant_id,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "action": e.action,
                "allowed": e.allowed,
                "reason": e.reason,
                "source_ip": e.source_ip,
            }
            for e in sorted(entries, key=lambda x: x.timestamp, reverse=True)[:limit]
        ]

    def stats(self) -> dict:
        with self._lock:
            total = len(self._access_log)
            blocked = sum(1 for e in self._access_log if not e.allowed)
            by_type = {}
            for e in self._access_log:
                by_type[e.resource_type] = by_type.get(e.resource_type, 0) + 1
            return {
                "total_access_checks": total,
                "blocked_attempts": blocked,
                "block_rate": round(blocked / total * 100, 2) if total else 0,
                "by_resource_type": by_type,
                "policies_count": len(self._policies),
            }

    def _log_access(
        self,
        tenant_id: str,
        resource_type: ResourceType,
        resource_id: str,
        action: str,
        allowed: bool,
        reason: str = "",
        source_ip: str = "",
    ):
        entry = AccessLog(
            timestamp=time.time(),
            tenant_id=tenant_id,
            resource_type=resource_type.value,
            resource_id=resource_id,
            action=action,
            allowed=allowed,
            reason=reason,
            source_ip=source_ip,
        )
        with self._lock:
            self._access_log.append(entry)
            if len(self._access_log) > self._max_log:
                self._access_log = self._access_log[-self._max_log:]
