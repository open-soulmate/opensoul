"""OpenHippo — 海马体：记忆生命周期管理。

Modules:
- MemoryStore: Short-term in-memory store with decay-based lifecycle
- DecayEngine: Ebbinghaus-inspired forgetting curves
- SessionManager: Session lifecycle with idle detection
- LongTermMemoryStore: Persistent cross-session memory with FTS5 search
"""

from src.hippo.decay import DecayEngine, DecayStrategy
from src.hippo.memory_store import Memory, MemoryStore
from src.hippo.session import Session, SessionManager, SessionStatus
from src.hippo.long_term_memory import (
    LongTermMemory,
    LongTermMemoryStore,
    MemoryAuditEntry,
    _normalize_dict_floats,
)

__all__ = [
    "DecayEngine",
    "DecayStrategy",
    "Memory",
    "MemoryStore",
    "MemoryAuditEntry",
    "Session",
    "SessionManager",
    "SessionStatus",
    "LongTermMemory",
    "LongTermMemoryStore",
    "_normalize_dict_floats",
]
