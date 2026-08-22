"""Knowledge Quality Scorer.

Multi-dimensional quality assessment of knowledge entries:
- Completeness: content length, structure, metadata
- Specificity: concrete data points, numbers, specifics
- Freshness: recency of creation and last update
- Connectivity: links to entities, tags, other knowledge
- Readability: sentence structure, language mix
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class QualityScorer:
    """Score knowledge entries on multiple quality dimensions."""

    WEIGHTS = {
        "completeness": 0.25,
        "specificity": 0.25,
        "freshness": 0.15,
        "connectivity": 0.20,
        "readability": 0.15,
    }

    GRADE_THRESHOLDS = [
        (0.9, "A+"),
        (0.8, "A"),
        (0.7, "B+"),
        (0.6, "B"),
        (0.5, "C+"),
        (0.4, "C"),
        (0.0, "D"),
    ]

    # ── Public API ──────────────────────────────────────────────────

    async def score_knowledge(
        self, db_pool: Any, user_id: str, knowledge_id: str
    ) -> dict[str, Any]:
        """Score a single knowledge entry."""
        row = await db_pool.fetchrow(
            "SELECT id, title, content, metadata, created_at, updated_at "
            "FROM knowledge WHERE id = $1 AND user_id = $2",
            knowledge_id,
            user_id,
        )
        if not row:
            return {"error": "Knowledge entry not found"}

        title = row["title"] or ""
        content = row["content"] or ""
        text = f"{title} {content}"
        meta = self._parse_meta(row["metadata"])

        scores = {
            "completeness": self._score_completeness(text, title, meta),
            "specificity": self._score_specificity(text),
            "freshness": self._score_freshness(row["created_at"], row["updated_at"]),
            "connectivity": await self._score_connectivity(db_pool, knowledge_id),
            "readability": self._score_readability(text),
        }

        total = sum(scores[k] * self.WEIGHTS[k] for k in scores)

        return {
            "knowledge_id": str(row["id"]),
            "title": title,
            "total_score": round(total, 2),
            "scores": {k: round(v, 2) for k, v in scores.items()},
            "grade": self._get_grade(total),
        }

    async def score_all(self, db_pool: Any, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Batch score knowledge entries."""
        rows = await db_pool.fetch(
            "SELECT id FROM knowledge WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
            user_id,
            limit,
        )
        results = []
        for row in rows:
            result = await self.score_knowledge(db_pool, user_id, str(row["id"]))
            if "error" not in result:
                results.append(result)

        results.sort(key=lambda x: x["total_score"], reverse=True)
        return results

    async def get_quality_report(self, db_pool: Any, user_id: str) -> dict[str, Any]:
        """Aggregate quality report for all user knowledge."""
        scores = await self.score_all(db_pool, user_id, limit=1000)

        if not scores:
            return {"total": 0, "avg_score": 0, "distribution": {}, "top_5": [], "bottom_5": []}

        avg = sum(s["total_score"] for s in scores) / len(scores)
        distribution: dict[str, int] = {}
        for s in scores:
            g = s["grade"]
            distribution[g] = distribution.get(g, 0) + 1

        # Dimension averages
        dim_avgs: dict[str, float] = {}
        for dim in self.WEIGHTS:
            dim_sum = sum(s["scores"].get(dim, 0) for s in scores)
            dim_avgs[dim] = round(dim_sum / len(scores), 2)

        return {
            "total": len(scores),
            "avg_score": round(avg, 2),
            "grade": self._get_grade(avg),
            "distribution": distribution,
            "dimension_averages": dim_avgs,
            "top_5": scores[:5],
            "bottom_5": scores[-5:],
        }

    # ── Dimension Scorers ───────────────────────────────────────────

    def _score_completeness(self, text: str, title: str, meta: dict) -> float:
        score = 0.0
        length = len(text)

        # Content length
        if length > 2000:
            score += 0.30
        elif length > 500:
            score += 0.25
        elif length > 200:
            score += 0.20
        elif length > 50:
            score += 0.10
        elif length > 10:
            score += 0.05

        # Structure indicators
        if "{" in text and "}" in text:
            score += 0.10  # JSON/structured data
        if any(c.isdigit() for c in text):
            score += 0.05  # Contains numbers
        if len(text.split(",")) > 3:
            score += 0.05  # Multiple data points
        if re.search(r"^#{1,3}\s", text, re.MULTILINE):
            score += 0.10  # Has markdown headers
        if re.search(r"^[-*]\s", text, re.MULTILINE):
            score += 0.05  # Has bullet lists

        # Title quality
        if title and len(title) > 5:
            score += 0.10
        if title and len(title) > 15:
            score += 0.05

        # Metadata richness
        if meta.get("tags"):
            score += 0.05
        if meta.get("source"):
            score += 0.05
        if meta.get("description"):
            score += 0.05

        return min(1.0, score)

    def _score_specificity(self, text: str) -> float:
        score = 0.0

        # Amounts/numbers
        if re.search(r"\d+(?:\.\d+)?(?:万元|亿元|元|万|亿|美元|USD|EUR)", text):
            score += 0.15
        if re.search(r"\d+(?:\.\d+)?%", text):
            score += 0.10

        # Dates
        if re.search(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", text):
            score += 0.10

        # Version numbers / IDs
        if re.search(r"v\d+\.\d+", text):
            score += 0.10
        if re.search(r"[A-Z]{2,}[-_]?\d+", text):
            score += 0.05

        # Technical terms
        tech_terms = [
            "API",
            "SDK",
            "REST",
            "WebSocket",
            "HTTP",
            "JSON",
            "SQL",
            "Docker",
            "Kubernetes",
            "Linux",
            "Python",
            "JavaScript",
            "React",
            "FastAPI",
            "PostgreSQL",
            "Redis",
            "Qdrant",
        ]
        tech_count = sum(1 for t in tech_terms if t in text)
        score += min(0.20, tech_count * 0.04)

        # Concrete numbers
        numbers = re.findall(r"\b\d+\b", text)
        if len(numbers) > 10:
            score += 0.15
        elif len(numbers) > 5:
            score += 0.10
        elif len(numbers) > 2:
            score += 0.05

        # URLs / references
        if re.search(r"https?://", text):
            score += 0.05
        if re.search(r"\[\d+\]", text):
            score += 0.05  # Reference citations

        return min(1.0, score)

    def _score_freshness(self, created_at: Any, updated_at: Any) -> float:
        now = datetime.now(UTC)

        try:
            updated = (
                updated_at
                if isinstance(updated_at, datetime)
                else datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            )
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            age_days = (now - updated).days
        except Exception:
            age_days = 999

        if age_days <= 7:
            return 1.0
        elif age_days <= 30:
            return 0.85
        elif age_days <= 90:
            return 0.65
        elif age_days <= 180:
            return 0.45
        elif age_days <= 365:
            return 0.30
        else:
            return 0.15

    async def _score_connectivity(self, db_pool: Any, knowledge_id: str) -> float:
        score = 0.0

        # Entity links
        try:
            entity_count = await db_pool.fetchval(
                "SELECT COUNT(*) FROM knowledge_entity_link WHERE knowledge_id = $1",
                knowledge_id,
            )
            if entity_count >= 5:
                score += 0.40
            elif entity_count >= 3:
                score += 0.30
            elif entity_count >= 1:
                score += 0.20
            else:
                score += 0.05
        except Exception:
            score += 0.05  # Table might not exist

        # Tag links
        try:
            tag_count = await db_pool.fetchval(
                "SELECT COUNT(*) FROM knowledge_tags WHERE knowledge_id = $1",
                knowledge_id,
            )
            if tag_count >= 3:
                score += 0.30
            elif tag_count >= 1:
                score += 0.20
            else:
                score += 0.05
        except Exception:
            score += 0.05

        # Chunk count (indicates depth)
        try:
            chunk_count = await db_pool.fetchval(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE knowledge_id = $1",
                knowledge_id,
            )
            if chunk_count >= 5:
                score += 0.30
            elif chunk_count >= 2:
                score += 0.20
            elif chunk_count >= 1:
                score += 0.10
        except Exception as exc:
            logging.getLogger(__name__).debug("probe skipped: %s", exc)
        return min(1.0, score)

    def _score_readability(self, text: str) -> float:
        score = 0.5  # Base score

        # Sentence count (indicates structure)
        sentences = re.split(r"[。！？.!?\n]+", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 5]

        if len(sentences) > 10:
            score += 0.15
        elif len(sentences) > 5:
            score += 0.10
        elif len(sentences) > 2:
            score += 0.05

        # Average sentence length (not too short, not too long)
        if sentences:
            avg_len = sum(len(s) for s in sentences) / len(sentences)
            if 20 <= avg_len <= 100:
                score += 0.15
            elif 10 <= avg_len <= 200:
                score += 0.10

        # Paragraph breaks
        paragraphs = text.split("\n\n")
        if len(paragraphs) > 3:
            score += 0.10
        elif len(paragraphs) > 1:
            score += 0.05

        # Mixed content (code + text)
        if re.search(r"```", text):
            score += 0.10  # Has code blocks

        return min(1.0, score)

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _get_grade(score: float) -> str:
        for threshold, grade in QualityScorer.GRADE_THRESHOLDS:
            if score >= threshold:
                return grade
        return "D"

    @staticmethod
    def _parse_meta(metadata: Any) -> dict:
        if isinstance(metadata, dict):
            return metadata
        if isinstance(metadata, str):
            try:
                return json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}
