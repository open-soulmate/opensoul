"""In-memory LRU cache with TTL for file access hot path.

Future: swap backend to Redis for distributed caching.
"""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class CacheEntry:
    key: str
    data: bytes
    size: int
    created_at: float
    ttl: float  # seconds
    hits: int = 0

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > self.ttl


class CacheManager:
    """LRU cache with TTL eviction and size limits."""

    def __init__(self, max_size_mb: int = 256, default_ttl: int = 3600):
        self._max_size = max_size_mb * 1024 * 1024  # bytes
        self._default_ttl = default_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._current_size = 0
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> bytes | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.expired:
                self._remove(key)
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            entry.hits += 1
            self._hits += 1
            return entry.data

    def put(self, key: str, data: bytes, ttl: float | None = None) -> None:
        with self._lock:
            # Remove existing if present
            if key in self._cache:
                self._remove(key)

            entry_size = len(data)

            # Evict LRU entries until we have space
            while self._current_size + entry_size > self._max_size and self._cache:
                self._evict_lru()

            self._cache[key] = CacheEntry(
                key=key,
                data=data,
                size=entry_size,
                created_at=time.time(),
                ttl=ttl or self._default_ttl,
            )
            self._current_size += entry_size

    def invalidate(self, key: str) -> bool:
        with self._lock:
            return self._remove(key)

    def clear(self) -> int:
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._current_size = 0
            return count

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        with self._lock:
            expired_keys = [k for k, v in self._cache.items() if v.expired]
            for k in expired_keys:
                self._remove(k)
            return len(expired_keys)

    @property
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._cache),
                "current_size_bytes": self._current_size,
                "max_size_bytes": self._max_size,
                "usage_percent": round(self._current_size / self._max_size * 100, 2) if self._max_size else 0,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total * 100, 2) if total else 0,
                "default_ttl_seconds": self._default_ttl,
            }

    def _remove(self, key: str) -> bool:
        entry = self._cache.pop(key, None)
        if entry:
            self._current_size -= entry.size
            return True
        return False

    def _evict_lru(self):
        """Evict the least recently used entry."""
        if self._cache:
            _, entry = self._cache.popitem(last=False)
            self._current_size -= entry.size
