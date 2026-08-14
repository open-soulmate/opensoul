"""Tenant management — create, configure, and manage isolated tenants.

Each tenant gets:
- Isolated resource quotas (storage, API calls, tokens)
- Namespace isolation for knowledge bases and vector collections
- Usage tracking and enforcement
- Tier-based defaults (free, pro, enterprise)
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class TenantTier(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    CUSTOM = "custom"


class TenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"
    EXPIRED = "expired"


@dataclass
class ResourceQuota:
    """Resource limits for a tenant."""
    max_storage_bytes: int = 1_073_741_824       # 1 GB
    max_documents: int = 10_000
    max_vector_collections: int = 10
    max_api_calls_per_day: int = 10_000
    max_tokens_per_day: int = 1_000_000
    max_agents: int = 5
    max_workflows: int = 10
    max_users: int = 10
    max_file_size_bytes: int = 104_857_600        # 100 MB

    def to_dict(self) -> dict:
        return {
            "max_storage_bytes": self.max_storage_bytes,
            "max_documents": self.max_documents,
            "max_vector_collections": self.max_vector_collections,
            "max_api_calls_per_day": self.max_api_calls_per_day,
            "max_tokens_per_day": self.max_tokens_per_day,
            "max_agents": self.max_agents,
            "max_workflows": self.max_workflows,
            "max_users": self.max_users,
            "max_file_size_bytes": self.max_file_size_bytes,
        }

    @classmethod
    def for_tier(cls, tier: TenantTier) -> ResourceQuota:
        if tier == TenantTier.FREE:
            return cls()
        elif tier == TenantTier.PRO:
            return cls(
                max_storage_bytes=10_737_418_240,       # 10 GB
                max_documents=100_000,
                max_vector_collections=50,
                max_api_calls_per_day=100_000,
                max_tokens_per_day=10_000_000,
                max_agents=20,
                max_workflows=50,
                max_users=50,
                max_file_size_bytes=524_288_000,         # 500 MB
            )
        elif tier == TenantTier.ENTERPRISE:
            return cls(
                max_storage_bytes=107_374_182_400,      # 100 GB
                max_documents=1_000_000,
                max_vector_collections=200,
                max_api_calls_per_day=1_000_000,
                max_tokens_per_day=100_000_000,
                max_agents=100,
                max_workflows=500,
                max_users=500,
                max_file_size_bytes=1_073_741_824,       # 1 GB
            )
        return cls()  # CUSTOM defaults to free, then override


@dataclass
class ResourceUsage:
    """Current resource usage for a tenant."""
    storage_bytes: int = 0
    documents: int = 0
    vector_collections: int = 0
    api_calls_today: int = 0
    tokens_today: int = 0
    agents: int = 0
    workflows: int = 0
    users: int = 0

    # Daily reset tracking
    _day_key: str = ""

    def to_dict(self) -> dict:
        return {
            "storage_bytes": self.storage_bytes,
            "documents": self.documents,
            "vector_collections": self.vector_collections,
            "api_calls_today": self.api_calls_today,
            "tokens_today": self.tokens_today,
            "agents": self.agents,
            "workflows": self.workflows,
            "users": self.users,
        }

    def reset_daily(self, today: str):
        """Reset daily counters if day changed."""
        if self._day_key != today:
            self.api_calls_today = 0
            self.tokens_today = 0
            self._day_key = today


@dataclass
class Tenant:
    """A tenant in the multi-tenant system."""
    tenant_id: str
    name: str
    tier: TenantTier = TenantTier.FREE
    status: TenantStatus = TenantStatus.ACTIVE
    namespace: str = ""          # Isolated namespace prefix
    owner_user_id: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # Resource management
    quota: ResourceQuota = field(default_factory=ResourceQuota)
    usage: ResourceUsage = field(default_factory=ResourceUsage)

    # Custom metadata
    metadata: dict = field(default_factory=dict)

    def to_dict(self, include_usage: bool = True) -> dict:
        d = {
            "tenant_id": self.tenant_id,
            "name": self.name,
            "tier": self.tier.value,
            "status": self.status.value,
            "namespace": self.namespace,
            "owner_user_id": self.owner_user_id,
            "description": self.description,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "quota": self.quota.to_dict(),
        }
        if include_usage:
            d["usage"] = self.usage.to_dict()
            d["usage_percent"] = self._usage_percent()
        return d

    def _usage_percent(self) -> dict:
        """Calculate usage as percentage of quota."""
        q = self.quota
        u = self.usage
        return {
            "storage": round(u.storage_bytes / q.max_storage_bytes * 100, 1) if q.max_storage_bytes else 0,
            "documents": round(u.documents / q.max_documents * 100, 1) if q.max_documents else 0,
            "api_calls": round(u.api_calls_today / q.max_api_calls_per_day * 100, 1) if q.max_api_calls_per_day else 0,
            "tokens": round(u.tokens_today / q.max_tokens_per_day * 100, 1) if q.max_tokens_per_day else 0,
        }


class TenantManager:
    """Manage tenants with resource quotas and namespace isolation."""

    def __init__(self):
        self._tenants: dict[str, Tenant] = {}
        self._lock = threading.Lock()
        self._namespace_index: dict[str, str] = {}  # namespace -> tenant_id

    def create(
        self,
        name: str,
        tier: str = "free",
        owner_user_id: str = "",
        description: str = "",
        tags: list[str] | None = None,
        config: dict | None = None,
        custom_quota: dict | None = None,
    ) -> Tenant:
        """Create a new tenant with isolated namespace."""
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        namespace = f"ns-{tenant_id}"

        tier_enum = TenantTier(tier)
        quota = ResourceQuota.for_tier(tier_enum)

        # Apply custom quota overrides
        if custom_quota and tier_enum == TenantTier.CUSTOM:
            for key, value in custom_quota.items():
                if hasattr(quota, key):
                    setattr(quota, key, value)

        tenant = Tenant(
            tenant_id=tenant_id,
            name=name,
            tier=tier_enum,
            namespace=namespace,
            owner_user_id=owner_user_id,
            description=description,
            tags=tags or [],
            config=config or {},
            quota=quota,
        )

        with self._lock:
            self._tenants[tenant_id] = tenant
            self._namespace_index[namespace] = tenant_id

        return tenant

    def get(self, tenant_id: str) -> Tenant | None:
        with self._lock:
            return self._tenants.get(tenant_id)

    def get_by_namespace(self, namespace: str) -> Tenant | None:
        with self._lock:
            tid = self._namespace_index.get(namespace)
            return self._tenants.get(tid) if tid else None

    def list_tenants(
        self,
        tier: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        with self._lock:
            tenants = list(self._tenants.values())
        if tier:
            tenants = [t for t in tenants if t.tier.value == tier]
        if status:
            tenants = [t for t in tenants if t.status.value == status]
        tenants.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tenants[offset:offset + limit]]

    def update(self, tenant_id: str, **kwargs) -> bool:
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return False
            for key in ("name", "description", "owner_user_id"):
                if key in kwargs:
                    setattr(tenant, key, kwargs[key])
            if "tier" in kwargs:
                new_tier = TenantTier(kwargs["tier"])
                tenant.tier = new_tier
                if new_tier != TenantTier.CUSTOM:
                    tenant.quota = ResourceQuota.for_tier(new_tier)
            if "status" in kwargs:
                tenant.status = TenantStatus(kwargs["status"])
            if "tags" in kwargs:
                tenant.tags = kwargs["tags"]
            if "config" in kwargs:
                tenant.config.update(kwargs["config"])
            if "custom_quota" in kwargs and tenant.tier == TenantTier.CUSTOM:
                for key, value in kwargs["custom_quota"].items():
                    if hasattr(tenant.quota, key):
                        setattr(tenant.quota, key, value)
            tenant.updated_at = time.time()
        return True

    def delete(self, tenant_id: str) -> bool:
        with self._lock:
            tenant = self._tenants.pop(tenant_id, None)
            if tenant:
                self._namespace_index.pop(tenant.namespace, None)
                return True
        return False

    def suspend(self, tenant_id: str, reason: str = "") -> bool:
        return self.update(tenant_id, status="suspended")

    def reactivate(self, tenant_id: str) -> bool:
        return self.update(tenant_id, status="active")

    # ── Resource Tracking ──────────────────────────────────

    def record_usage(
        self,
        tenant_id: str,
        storage_delta: int = 0,
        document_delta: int = 0,
        api_calls: int = 0,
        tokens: int = 0,
    ) -> bool:
        """Record resource usage for a tenant."""
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return False
            today = time.strftime("%Y-%m-%d")
            tenant.usage.reset_daily(today)
            tenant.usage.storage_bytes = max(0, tenant.usage.storage_bytes + storage_delta)
            tenant.usage.documents = max(0, tenant.usage.documents + document_delta)
            tenant.usage.api_calls_today += api_calls
            tenant.usage.tokens_today += tokens
        return True

    def check_quota(self, tenant_id: str, resource: str, amount: int = 1) -> dict:
        """Check if a tenant has quota for a resource."""
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return {"allowed": False, "reason": "tenant_not_found"}
            if tenant.status != TenantStatus.ACTIVE:
                return {"allowed": False, "reason": f"tenant is {tenant.status.value}"}

            today = time.strftime("%Y-%m-%d")
            tenant.usage.reset_daily(today)

            checks = {
                "storage": (tenant.usage.storage_bytes + amount, tenant.quota.max_storage_bytes, "storage"),
                "documents": (tenant.usage.documents + amount, tenant.quota.max_documents, "documents"),
                "api_calls": (tenant.usage.api_calls_today + amount, tenant.quota.max_api_calls_per_day, "api_calls"),
                "tokens": (tenant.usage.tokens_today + amount, tenant.quota.max_tokens_per_day, "tokens"),
                "agents": (tenant.usage.agents + amount, tenant.quota.max_agents, "agents"),
                "workflows": (tenant.usage.workflows + amount, tenant.quota.max_workflows, "workflows"),
                "users": (tenant.usage.users + amount, tenant.quota.max_users, "users"),
            }

            if resource not in checks:
                return {"allowed": True, "reason": "no_limit"}

            current, limit, name = checks[resource]
            return {
                "allowed": current <= limit,
                "current": current - amount,
                "limit": limit,
                "would_be": current,
                "percent": round(current / limit * 100, 1) if limit else 0,
                "reason": f"{name} quota exceeded" if current > limit else "ok",
            }

    def stats(self) -> dict:
        with self._lock:
            tenants = list(self._tenants.values())
            by_tier = {}
            by_status = {}
            total_storage = 0
            total_docs = 0
            for t in tenants:
                by_tier[t.tier.value] = by_tier.get(t.tier.value, 0) + 1
                by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
                total_storage += t.usage.storage_bytes
                total_docs += t.usage.documents
            return {
                "total_tenants": len(tenants),
                "by_tier": by_tier,
                "by_status": by_status,
                "total_storage_bytes": total_storage,
                "total_documents": total_docs,
            }
