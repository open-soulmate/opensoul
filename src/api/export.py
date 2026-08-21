"""Export API — Export user knowledge, entities, and tags in multiple formats.

Supports: JSON, Markdown (real .md), CSV, and JSON-with-markdown.
"""

import csv
import io
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from src.database.postgres import db_pool

router = APIRouter()


@router.get("/health")
async def export_health():
    """Export system health check."""
    return {"status": "ok", "component": "ExportSystem"}


@router.get("/json")
async def export_json(user_id: UUID):
    """Export all user data as JSON."""
    knowledge = await db_pool.fetch(
        "SELECT * FROM knowledge WHERE user_id = $1 ORDER BY created_at", user_id
    )
    entities = await db_pool.fetch(
        "SELECT * FROM entities WHERE user_id = $1 ORDER BY name", user_id
    )
    tags = await db_pool.fetch("SELECT * FROM tags WHERE user_id = $1 ORDER BY name", user_id)

    return JSONResponse(
        {
            "knowledge": [dict(r) for r in knowledge],
            "entities": [dict(r) for r in entities],
            "tags": [dict(r) for r in tags],
            "exported_at": datetime.utcnow().isoformat() + "Z",
        }
    )


@router.get("/markdown")
async def export_markdown(user_id: UUID):
    """Export all knowledge as a real Markdown document."""
    knowledge = await db_pool.fetch(
        "SELECT k.*, COALESCE(array_agg(t.name) FILTER (WHERE t.name IS NOT NULL), '{}') as tags "
        "FROM knowledge k "
        "LEFT JOIN knowledge_tags kt ON k.id = kt.knowledge_id "
        "LEFT JOIN tags t ON kt.tag_id = t.id "
        "WHERE k.user_id = $1 GROUP BY k.id ORDER BY k.created_at",
        user_id,
    )
    entities = await db_pool.fetch(
        "SELECT * FROM entities WHERE user_id = $1 ORDER BY name", user_id
    )
    tags = await db_pool.fetch("SELECT * FROM tags WHERE user_id = $1 ORDER BY name", user_id)

    lines: list[str] = []
    lines.append("# Knowledge Export")
    lines.append("")
    lines.append(f"Exported at: {datetime.utcnow().isoformat()}Z")
    lines.append(f"Total entries: {len(knowledge)}")
    lines.append("")

    # Table of contents
    if knowledge:
        lines.append("## Table of Contents")
        lines.append("")
        for i, row in enumerate(knowledge, 1):
            title = row.get("title", f"Entry {i}")
            anchor = title.lower().replace(" ", "-").replace("/", "-")[:50]
            lines.append(f"{i}. [{title}](#{anchor})")
        lines.append("")

    # Knowledge entries
    lines.append("## Knowledge Entries")
    lines.append("")
    for i, row in enumerate(knowledge, 1):
        title = row.get("title", f"Entry {i}")
        content = row.get("content", "")
        entry_tags = row.get("tags", [])
        created = row.get("created_at", "")
        source = row.get("source", "")

        lines.append(f"### {title}")
        lines.append("")
        if created:
            lines.append(f"**Created:** {created}")
        if source:
            lines.append(f"**Source:** {source}")
        if entry_tags and entry_tags[0]:
            tag_str = ", ".join(f"`{t}`" for t in entry_tags if t)
            lines.append(f"**Tags:** {tag_str}")
        lines.append("")
        lines.append(content if content else "*No content*")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Entities section
    if entities:
        lines.append("## Entities")
        lines.append("")
        lines.append("| Name | Type | Description |")
        lines.append("|------|------|-------------|")
        for row in entities:
            name = row.get("name", "")
            etype = row.get("type", "")
            desc = row.get("description", "")
            lines.append(f"| {name} | {etype} | {desc} |")
        lines.append("")

    # Tags section
    if tags:
        lines.append("## Tags")
        lines.append("")
        for row in tags:
            tname = row.get("name", "")
            tdesc = row.get("description", "")
            lines.append(f"- **{tname}**: {tdesc}" if tdesc else f"- **{tname}**")
        lines.append("")

    md_content = "\n".join(lines)
    return PlainTextResponse(
        content=md_content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="knowledge-export-{user_id}.md"'
        },
    )


@router.get("/markdown-json")
async def export_markdown_json(user_id: UUID):
    """Export all knowledge as JSON with markdown-formatted content (legacy)."""
    knowledge = await db_pool.fetch(
        "SELECT k.*, COALESCE(array_agg(t.name) FILTER (WHERE t.name IS NOT NULL), '{}') as tags "
        "FROM knowledge k "
        "LEFT JOIN knowledge_tags kt ON k.id = kt.knowledge_id "
        "LEFT JOIN tags t ON kt.tag_id = t.id "
        "WHERE k.user_id = $1 GROUP BY k.id ORDER BY k.created_at",
        user_id,
    )
    entities = await db_pool.fetch(
        "SELECT * FROM entities WHERE user_id = $1 ORDER BY name", user_id
    )
    tags = await db_pool.fetch("SELECT * FROM tags WHERE user_id = $1 ORDER BY name", user_id)

    return JSONResponse(
        {
            "knowledge": [dict(r) for r in knowledge],
            "entities": [dict(r) for r in entities],
            "tags": [dict(r) for r in tags],
            "format": "markdown",
            "exported_at": datetime.utcnow().isoformat() + "Z",
        }
    )


@router.get("/csv")
async def export_csv(
    user_id: UUID,
    table: str = Query("knowledge", description="Table to export: knowledge, entities, or tags"),
):
    """Export data as CSV file."""
    valid_tables = {"knowledge", "entities", "tags"}
    if table not in valid_tables:
        return JSONResponse(
            {"error": f"Invalid table '{table}'. Must be one of: {', '.join(valid_tables)}"},
            status_code=400,
        )

    if table == "knowledge":
        rows = await db_pool.fetch(
            "SELECT k.id, k.title, k.content, k.source, k.created_at, "
            "COALESCE(array_agg(t.name) FILTER (WHERE t.name IS NOT NULL), '{}') as tags "
            "FROM knowledge k "
            "LEFT JOIN knowledge_tags kt ON k.id = kt.knowledge_id "
            "LEFT JOIN tags t ON kt.tag_id = t.id "
            "WHERE k.user_id = $1 GROUP BY k.id ORDER BY k.created_at",
            user_id,
        )
    else:
        rows = await db_pool.fetch(
            f"SELECT * FROM {table} WHERE user_id = $1 ORDER BY name", user_id
        )

    output = io.StringIO()
    if rows:
        # Write header
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for row in rows:
            d = dict(row)
            # Convert non-string types
            for k, v in d.items():
                if isinstance(v, (list, dict)):
                    d[k] = str(v)
                elif v is None:
                    d[k] = ""
            writer.writerow(d)

    csv_content = output.getvalue()
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{table}-export-{user_id}.csv"'
        },
    )
