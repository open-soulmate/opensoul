"""IP access control — blacklist, whitelist, geo-blocking."""

from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import dataclass, field


@dataclass
class IPEntry:
    ip: str
    reason: str = ""
    added_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    hit_count: int = 0


class IPAccessControl:
    """IP-based access control with blacklist/whitelist."""

    def __init__(self):
        self._blacklist: dict[str, IPEntry] = {}
        self._whitelist: dict[str, IPEntry] = {}
        self._lock = threading.Lock()

    def is_allowed(self, ip: str) -> dict:
        """Check if an IP is allowed."""
        with self._lock:
            # Check whitelist first — if whitelist exists, only whitelisted IPs allowed
            if self._whitelist and not self._is_in_list(ip, self._whitelist):
                return {"allowed": False, "reason": "IP not in whitelist", "action": "block"}

            # Check blacklist
            if self._is_in_list(ip, self._blacklist):
                entry = self._blacklist.get(self._normalize(ip)) or self._blacklist.get(ip)
                if entry:
                    entry.hit_count += 1
                    if entry.expires_at and time.time() > entry.expires_at:
                        del self._blacklist[self._normalize(ip)]
                        return {"allowed": True, "reason": "blacklist expired", "action": "allow"}
                return {"allowed": False, "reason": entry.reason if entry else "blacklisted", "action": "block"}

            return {"allowed": True, "reason": "", "action": "allow"}

    def blacklist_add(self, ip: str, reason: str = "", ttl_seconds: int | None = None):
        with self._lock:
            expires = time.time() + ttl_seconds if ttl_seconds else None
            self._blacklist[self._normalize(ip)] = IPEntry(ip=ip, reason=reason, expires_at=expires)

    def blacklist_remove(self, ip: str):
        with self._lock:
            self._blacklist.pop(self._normalize(ip), None)

    def whitelist_add(self, ip: str, reason: str = ""):
        with self._lock:
            self._whitelist[self._normalize(ip)] = IPEntry(ip=ip, reason=reason)

    def whitelist_remove(self, ip: str):
        with self._lock:
            self._whitelist.pop(self._normalize(ip), None)

    def list_blacklist(self) -> list[dict]:
        with self._lock:
            return [
                {"ip": e.ip, "reason": e.reason, "added_at": e.added_at, "expires_at": e.expires_at, "hit_count": e.hit_count}
                for e in self._blacklist.values()
            ]

    def list_whitelist(self) -> list[dict]:
        with self._lock:
            return [
                {"ip": e.ip, "reason": e.reason, "added_at": e.added_at}
                for e in self._whitelist.values()
            ]

    def stats(self) -> dict:
        with self._lock:
            return {
                "blacklist_count": len(self._blacklist),
                "whitelist_count": len(self._whitelist),
            }

    @staticmethod
    def _normalize(ip: str) -> str:
        try:
            return str(ipaddress.ip_address(ip))
        except ValueError:
            return ip

    def _is_in_list(self, ip: str, ip_dict: dict) -> bool:
        normalized = self._normalize(ip)
        if normalized in ip_dict:
            return True
        # Check if IP is in any CIDR range
        for key in ip_dict:
            if "/" in key:
                try:
                    if ipaddress.ip_address(normalized) in ipaddress.ip_network(key, strict=False):
                        return True
                except ValueError:
                    pass
        return False
