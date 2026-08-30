"""Feedback service — extract knowledge from conversations and store in knowledge table.

Uses the existing knowledge table with metadata.feedback_type to distinguish
feedback entries from regular knowledge entries.
"""

import json
import uuid
import logging

from src.database.postgres import db_pool
from src.models.feedback import FeedbackEntry, FeedbackExtractRequest

logger = logging.getLogger(__name__)


async def extract_knowledge(data: FeedbackExtractRequest, llm_call=None) -> list[FeedbackEntry]:
    """Extract knowledge entries from a conversation using LLM.

    Args:
        data: The extraction request with conversation text
        llm_call: Optional async function(prompt) -> str for LLM calls.
                  If None, uses rule-based extraction only.

    Returns:
        List of extracted feedback entries
    """
    entries = []

    # Rule-based extraction for common patterns
    entries.extend(_rule_based_extract(data.conversation))

    # LLM-based extraction if available
    if llm_call:
        try:
            llm_entries = await _llm_extract(data.conversation, llm_call)
            entries.extend(llm_entries)
        except Exception as e:
            logger.warning(f"LLM extraction failed: {e}")

    # Deduplicate
    entries = _deduplicate(entries)

    return entries


def _rule_based_extract(text: str) -> list[FeedbackEntry]:
    """Extract knowledge using pattern matching."""
    entries = []
    lines = text.split('\n')

    for i, line in enumerate(lines):
        lower = line.lower().strip()

        # Pattern: user correction (highest priority)
        if any(kw in lower for kw in ['不对', '错了', '不是这样', '应该是', 'wrong', 'incorrect']):
            context = _get_context(lines, i, 3)
            entries.append(FeedbackEntry(
                type="pattern",
                title=f"用户纠正: {line.strip()[:60]}",
                content=context,
                confidence="high",
                evidence=line.strip(),
                tags=["user-correction", "high-priority"],
            ))

        # Pattern: architecture decision
        if any(kw in lower for kw in ['决定', '选择', '用这个方案', '采用', 'decided', 'chosen']):
            context = _get_context(lines, i, 2)
            entries.append(FeedbackEntry(
                type="decision",
                title=f"决策: {line.strip()[:60]}",
                content=context,
                confidence="medium",
                evidence=line.strip(),
                tags=["decision"],
            ))

        # Pattern: new knowledge (discovery)
        if any(kw in lower for kw in ['发现', '原来', '其实', '注意', 'found out', 'turns out']):
            context = _get_context(lines, i, 2)
            entries.append(FeedbackEntry(
                type="knowledge",
                title=f"发现: {line.strip()[:60]}",
                content=context,
                confidence="medium",
                evidence=line.strip(),
                tags=["discovery"],
            ))

    return entries


async def _llm_extract(text: str, llm_call) -> list[FeedbackEntry]:
    """Extract knowledge using LLM."""
    prompt = f"""分析以下AI对话，提取有价值的知识条目。

每条知识输出JSON格式，包含以下字段：
- type: "knowledge"(事实/原理) | "pattern"(问题→方案) | "decision"(架构/技术决策)
- title: 一句话标题
- content: 详细描述
- confidence: "high" | "medium" | "low"
- evidence: 对话中的关键证据（原文摘录）
- tags: 关键词数组

只输出JSON数组，不要其他文字。如果没有值得提取的知识，输出空数组 []。

对话内容：
{text[:4000]}
"""
    try:
        result = await llm_call(prompt)
        # Parse JSON from response
        result = result.strip()
        if result.startswith('```'):
            result = result.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        items = json.loads(result)
        return [FeedbackEntry(**item) for item in items if isinstance(item, dict)]
    except Exception as e:
        logger.warning(f"Failed to parse LLM extraction: {e}")
        return []


def _get_context(lines: list[str], idx: int, window: int) -> str:
    """Get surrounding context for a line."""
    start = max(0, idx - window)
    end = min(len(lines), idx + window + 1)
    return '\n'.join(lines[start:end]).strip()


def _deduplicate(entries: list[FeedbackEntry]) -> list[FeedbackEntry]:
    """Remove duplicate entries based on title similarity."""
    seen_titles = set()
    unique = []
    for entry in entries:
        # Normalize title for comparison
        norm = entry.title.lower().strip()[:40]
        if norm not in seen_titles:
            seen_titles.add(norm)
            unique.append(entry)
    return unique


async def store_entries(
    entries: list[FeedbackEntry],
    user_id: str,
    source: str = "hermes-feedback",
    session_id: str | None = None,
) -> tuple[int, int]:
    """Store extracted entries into the knowledge table.

    Returns:
        (stored_count, skipped_count)
    """
    stored = 0
    skipped = 0

    for entry in entries:
        # Check for duplicates
        existing = await db_pool.fetchval(
            "SELECT id FROM knowledge WHERE user_id = ? AND title = ? AND metadata LIKE ?",
            user_id,
            entry.title,
            f'%"feedback_type"%',
        )
        if existing:
            skipped += 1
            continue

        entry_id = str(uuid.uuid4())
        metadata = json.dumps({
            "feedback_type": entry.type,
            "confidence": entry.confidence,
            "evidence": entry.evidence,
            "session_id": session_id,
            "extracted_at": __import__("datetime").datetime.utcnow().isoformat(),
        })

        await db_pool.execute(
            "INSERT INTO knowledge (id, user_id, title, content, source, content_type, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            entry_id,
            user_id,
            entry.title,
            entry.content,
            source,
            "feedback",
            metadata,
        )

        # Add tags
        for tag_name in entry.tags + ["auto-extracted", entry.type]:
            tag_id = str(uuid.uuid4())
            await db_pool.execute(
                "INSERT INTO tags (id, name, user_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                tag_id,
                tag_name,
                user_id,
            )
            tag = await db_pool.fetchrow(
                "SELECT id FROM tags WHERE name = ? AND user_id = ?", tag_name, user_id
            )
            if tag:
                await db_pool.execute(
                    "INSERT INTO knowledge_tags (knowledge_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    entry_id,
                    tag["id"],
                )

        stored += 1

    return stored, skipped


async def list_entries(
    user_id: str,
    feedback_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List feedback entries for a user."""
    query = (
        "SELECT k.id, k.user_id, k.title, k.content, k.source, k.metadata, k.created_at "
        "FROM knowledge k "
        "WHERE k.user_id = ? AND k.content_type = 'feedback' "
    )
    params: list = [user_id]

    if feedback_type:
        query += " AND json_extract(k.metadata, '$.feedback_type') = ? "
        params.append(feedback_type)

    query += " ORDER BY k.created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = await db_pool.fetch(query, *params)
    results = []
    for row in rows:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        tags_row = await db_pool.fetch(
            "SELECT t.name FROM tags t JOIN knowledge_tags kt ON t.id = kt.tag_id WHERE kt.knowledge_id = ?",
            row["id"],
        )
        results.append({
            "id": row["id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "content": row["content"],
            "feedback_type": meta.get("feedback_type", ""),
            "confidence": meta.get("confidence", ""),
            "evidence": meta.get("evidence", ""),
            "source": row["source"],
            "tags": [t["name"] for t in tags_row],
            "created_at": row["created_at"],
        })

    return results
