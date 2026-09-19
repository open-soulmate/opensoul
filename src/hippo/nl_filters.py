"""Khoj-style natural language memory search filters for OpenHippo.

P0-6 gap (SUMMARY.md): "Khoj DateFilter/FileFilter/WordFilter自然语言检索过滤"
— user types natural language, filters are extracted, remaining text is the
search query. Reference: Khoj khoj/processor/filter/queries.py.

Supported filter categories:
  DateFilter:   "last week", "yesterday", "today", "last month", "recent",
                "N days/weeks/months ago", "after YYYY-MM-DD", "before YYYY-MM-DD",
                "YYYY-MM" (month), "YYYY" (year)
  TypeFilter:   "episodic", "semantic", "procedural", "working" memories
  ImportanceFilter: "important", "high importance", "critical"
  WordFilter:   "mentioning X", "containing X", "about X", "related to X"

Design decisions:
  - All parsing is regex-based (deterministic, no LLM call — CAMEL philosophy:
    "能程序化验证的绝不靠LLM")
  - Filters that fail to parse are silently ignored (the text stays in the
    query — fail-open for search, fail-closed only for security)
  - Date computation uses local time (user-facing semantics)
  - Returned clean_query has filter phrases stripped, preserving remaining text
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class NLFilterResult:
    """Parsed result of a natural language memory search query."""

    clean_query: str = ""
    date_from: float = 0.0  # epoch timestamp, 0 = no lower bound
    date_to: float = 0.0  # epoch timestamp, 0 = no upper bound
    memory_types: list[str] = field(default_factory=list)
    min_importance: float = 0.0
    word_filters: list[str] = field(default_factory=list)  # must appear in content
    parsed_filters: list[str] = field(default_factory=list)  # human-readable log

    @property
    def has_filters(self) -> bool:
        return bool(
            self.date_from
            or self.date_to
            or self.memory_types
            or self.min_importance > 0
            or self.word_filters
        )

    def to_dict(self) -> dict:
        return {
            "clean_query": self.clean_query,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "memory_types": self.memory_types,
            "min_importance": self.min_importance,
            "word_filters": self.word_filters,
            "parsed_filters": self.parsed_filters,
            "has_filters": self.has_filters,
        }


# ── Date parsing patterns ──────────────────────────────────────────

_DAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Relative time patterns: ("regex", unit, count, direction)
# direction: "past" = [now - delta, now], "recent" = same
_RELATIVE_PATTERNS = [
    (r"\btoday\b", "day", 1, "past"),
    (r"\byesterday\b", "yesterday", 1, "past"),
    (r"\bthis\s+morning\b", "day", 1, "past"),
    (r"\blast\s+(\d+)\s+days?\b", "day", None, "past"),
    (r"\blast\s+(\d+)\s+weeks?\b", "week", None, "past"),
    (r"\blast\s+(\d+)\s+months?\b", "month", None, "past"),
    (r"\blast\s+week\b", "week", 1, "past"),
    (r"\blast\s+month\b", "month", 1, "past"),
    (r"\blast\s+year\b", "year", 1, "past"),
    (r"\brecent(?:ly)?\b", "day", 7, "past"),
    (r"\bpast\s+week\b", "week", 1, "past"),
    (r"\bpast\s+month\b", "month", 1, "past"),
    (r"\b(\d+)\s+days?\s+ago\b", "day", None, "past"),
    (r"\b(\d+)\s+weeks?\s+ago\b", "week", None, "past"),
]

# Absolute date patterns
_ABS_DATE_PATTERNS = [
    # "after 2024-01-15" / "since 2024-01-15"
    (r"\b(?:after|since)\s+(\d{4}-\d{2}-\d{2})\b", "after_date"),
    # "before 2024-02-01" / "until 2024-02-01"
    (r"\b(?:before|until|prior to)\s+(\d{4}-\d{2}-\d{2})\b", "before_date"),
    # "in 2024-01" (month)
    (r"\bin\s+(\d{4}-\d{2})\b(?!-)", "month"),
    # "in 2024" (year)
    (r"\bin\s+(\d{4})\b(?!\d)", "year"),
]

# Memory type keywords
_TYPE_KEYWORDS = {
    "episodic": "episodic",
    "episodes": "episodic",
    "semantic": "semantic",
    "procedural": "procedural",
    "procedures": "procedural",
    "working": "working",
    "work memory": "working",
}

# Importance keywords → minimum importance threshold
_IMPORTANCE_KEYWORDS = {
    "important": 0.6,
    "high importance": 0.6,
    "highly important": 0.7,
    "critical": 0.8,
    "crucial": 0.8,
    "significant": 0.5,
    "key": 0.5,
}

# Word filter patterns: extract the word/phrase after these keywords
_WORD_FILTER_PATTERNS = [
    r"\bmentioning\s+(.+?)(?:\s+(?:and|from|in|with|that|which)\b|$)",
    r"\bcontaining\s+(.+?)(?:\s+(?:and|from|in|with|that|which)\b|$)",
    r"\babout\s+(.+?)(?:\s+(?:from|in|during|that|which)\b|$)",
    r"\brelated\s+to\s+(.+?)(?:\s+(?:from|in|during|that|which)\b|$)",
    r"\breferring\s+to\s+(.+?)(?:\s+(?:from|in|during|that|which)\b|$)",
    r"\bwith\s+the\s+word\s+(.+?)(?:\s+(?:and|from|in)\b|$)",
]


def _compute_date_range(
    unit: str, count: int, now: Optional[float] = None
) -> tuple[float, float]:
    """Compute (date_from, date_to) for a relative time expression."""
    if now is None:
        now = time.time()
    dt_now = datetime.fromtimestamp(now)

    if unit == "yesterday":
        # "yesterday" = the calendar day before today
        start_of_today = dt_now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_yesterday = start_of_today - timedelta(days=1)
        return start_of_yesterday.timestamp(), start_of_today.timestamp()
    elif unit == "day":
        # For "today"/"yesterday"/"N days ago": start of the period
        if count == 1:
            start = dt_now.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start = dt_now - timedelta(days=count)
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif unit == "week":
        start = dt_now - timedelta(weeks=count)
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif unit == "month":
        start = dt_now - timedelta(days=30 * count)
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif unit == "year":
        start = dt_now.replace(year=dt_now.year - count, month=1, day=1,
                              hour=0, minute=0, second=0, microsecond=0)
    else:
        return 0.0, 0.0

    return start.timestamp(), now


def parse_nl_query(query: str, now: Optional[float] = None) -> NLFilterResult:
    """Parse natural language filters from a memory search query.

    Returns NLFilterResult with:
      - clean_query: query text with filter phrases removed
      - Structured filter fields (date_from/date_to/memory_types/etc.)
      - parsed_filters: human-readable log of what was extracted

    The clean_query may be empty if the entire query was filter expressions
    (in that case, the caller should list memories matching the filters
    without text search).
    """
    if not query or not query.strip():
        return NLFilterResult(clean_query="")

    if now is None:
        now = time.time()

    result = NLFilterResult(clean_query=query)
    text = query
    removed_phrases: list[str] = []

    # ── 1. Date filters ────────────────────────────────────────

    # Relative dates
    for pattern, unit, count, direction in _RELATIVE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            actual_count = count
            if actual_count is None:
                # Extract number from regex group
                try:
                    actual_count = int(m.group(1))
                except (IndexError, ValueError):
                    continue
            date_from, date_to = _compute_date_range(unit, actual_count, now)
            if date_from > 0:
                result.date_from = date_from
                result.date_to = date_to
                result.parsed_filters.append(
                    f"date: {m.group(0).strip()} → [{_fmt_ts(date_from)}, now]"
                )
                removed_phrases.append(m.group(0))
            break  # Use first matching relative date only

    # Absolute dates
    for pattern, kind in _ABS_DATE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            date_str = m.group(1)
            try:
                if kind == "after_date":
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    result.date_from = dt.timestamp()
                    result.parsed_filters.append(f"date_from: after {date_str}")
                elif kind == "before_date":
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    result.date_to = dt.timestamp()
                    result.parsed_filters.append(f"date_to: before {date_str}")
                elif kind == "month":
                    dt = datetime.strptime(date_str, "%Y-%m")
                    result.date_from = dt.timestamp()
                    next_month = dt.replace(
                        day=28
                    ) + timedelta(days=4)  # safe way to get next month
                    next_month = next_month.replace(
                        day=1, hour=0, minute=0, second=0, microsecond=0
                    )
                    result.date_to = next_month.timestamp()
                    result.parsed_filters.append(f"date: in {date_str}")
                elif kind == "year":
                    dt = datetime(int(date_str), 1, 1)
                    result.date_from = dt.timestamp()
                    result.date_to = datetime(int(date_str) + 1, 1, 1).timestamp()
                    result.parsed_filters.append(f"date: in {date_str}")
                removed_phrases.append(m.group(0))
            except ValueError:
                pass  # Invalid date string — keep text in query
            break  # Use first matching absolute date only

    # ── 2. Memory type filters ─────────────────────────────────

    found_types = []
    for keyword, mem_type in _TYPE_KEYWORDS.items():
        pattern = rf"\b{re.escape(keyword)}\s+memor(?:y|ies)\b|\b{re.escape(keyword)}\b(?!\s+(?:and|or|from|in|with))"
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            if mem_type not in found_types:
                found_types.append(mem_type)
            removed_phrases.append(m.group(0))
    if found_types:
        result.memory_types = found_types
        result.parsed_filters.append(f"memory_types: {found_types}")

    # ── 3. Importance filters ──────────────────────────────────

    best_importance = 0.0
    for keyword, threshold in _IMPORTANCE_KEYWORDS.items():
        pattern = rf"\b{re.escape(keyword)}\b"
        if re.search(pattern, text, re.IGNORECASE):
            best_importance = max(best_importance, threshold)
            removed_phrases.append(keyword)
    if best_importance > 0:
        result.min_importance = best_importance
        result.parsed_filters.append(f"min_importance: {best_importance}")

    # ── 4. Word filters ────────────────────────────────────────

    for pattern in _WORD_FILTER_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            word = m.group(1).strip().strip("\"'""''")
            if word and len(word) >= 2:
                result.word_filters.append(word)
                result.parsed_filters.append(f"word_filter: '{word}'")
                removed_phrases.append(m.group(0))

    # ── 5. Build clean query ───────────────────────────────────

    clean = text
    for phrase in removed_phrases:
        # Remove the phrase, then collapse whitespace
        clean = clean.replace(phrase, " ", 1)

    # Also remove common filter-introduction words left behind
    leftover_patterns = [
        r"\bshow\s+me\b", r"\bfind\b", r"\bsearch\s+for\b",
        r"\bmemories?\b", r"\bfrom\b", r"\bin\b(?!\d)",
        r"\bthat\s+are\b", r"\bwhich\s+are\b", r"\bare\b",
        r"\bthe\b", r"\bof\b", r"\bwith\b", r"\band\b",
    ]
    for pat in leftover_patterns:
        clean = re.sub(pat, " ", clean, flags=re.IGNORECASE)

    clean = re.sub(r"\s+", " ", clean).strip()
    # Remove leading/trailing prepositions and articles
    clean = re.sub(r"^(?:from|in|about|with|and|or|the|a|an|on|at|to)\s+", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+(?:from|in|about|with|and|or|on|at|to)$", "", clean, flags=re.IGNORECASE)
    clean = clean.strip()

    result.clean_query = clean
    return result


def apply_post_filters(
    memories: list[dict], nl_result: NLFilterResult
) -> list[dict]:
    """Apply NL filter predicates to a list of memory dicts post-retrieval.

    Used when the SQL query doesn't support all filter types natively
    (e.g., word_filters on content).
    """
    if not nl_result.has_filters:
        return memories

    filtered = []
    for mem in memories:
        # Date filter
        if nl_result.date_from and mem.get("created_at", 0) < nl_result.date_from:
            continue
        if nl_result.date_to and mem.get("created_at", 0) > nl_result.date_to:
            continue

        # Importance filter
        if (
            nl_result.min_importance > 0
            and mem.get("importance", 0) < nl_result.min_importance
        ):
            continue

        # Word filters — all words must appear in content or tags
        if nl_result.word_filters:
            content = (mem.get("content", "") or "").lower()
            tags_str = json.dumps(mem.get("tags", [])).lower() if isinstance(mem.get("tags"), list) else str(mem.get("tags", "")).lower()
            combined = content + " " + tags_str
            if not all(wf.lower() in combined for wf in nl_result.word_filters):
                continue

        filtered.append(mem)

    return filtered


def _fmt_ts(ts: float) -> str:
    """Format epoch timestamp for display."""
    if ts <= 0:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
