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
from src.hippo.dream_distiller import DreamDistiller, DreamAction, DreamResult
from src.hippo.memory_pipeline import (
    ConsolidationDecision,
    MemoryPipeline,
    Phase1Candidate,
    Phase1Result,
    PipelineResult,
    parse_phase1,
    parse_phase2,
)
from src.hippo.decision_log import DecisionEntry, DecisionLog, get_decision_log
from src.hippo.nl_filters import NLFilterResult, parse_nl_query, apply_post_filters
from src.hippo.extractors.deermem_tags import (
    SafetyTags,
    TagDecision,
    infer_tags,
    tags_vocab,
    validate_delete_tags,
    validate_write_tags,
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
    "DreamDistiller",
    "DreamAction",
    "DreamResult",
    "MemoryPipeline",
    "Phase1Candidate",
    "Phase1Result",
    "PipelineResult",
    "ConsolidationDecision",
    "parse_phase1",
    "parse_phase2",
    "DecisionEntry",
    "DecisionLog",
    "get_decision_log",
    "NLFilterResult",
    "parse_nl_query",
    "apply_post_filters",
    "SafetyTags",
    "TagDecision",
    "infer_tags",
    "tags_vocab",
    "validate_delete_tags",
    "validate_write_tags",
]
