"""OpenLearn API — 学习系统：课程管理、章节学习、测验、进度追踪。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.learn.course_engine import CourseEngine

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
engine = CourseEngine()


# ── Request Schemas ─────────────────────────────────────────

class CourseCreateRequest(BaseModel):
    title: str
    description: str = ""
    tags: list[str] = []
    topics: list[str] = []
    domain: str = ""
    knowledge_ids: list[str] = []
    generated_by: str = "manual"


class CourseUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    topics: list[str] | None = None
    domain: str | None = None
    status: str | None = None


class ChapterCreateRequest(BaseModel):
    title: str
    content: str = ""
    quiz: list[dict] = []


class ChapterUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    order: int | None = None


class MarkChapterRequest(BaseModel):
    completed: bool = True


class QuizSubmitRequest(BaseModel):
    questions: list[dict]


# ── Helpers ─────────────────────────────────────────────────

def _course_dict(c) -> dict:
    return {
        "id": c.course_id,
        "title": c.title,
        "description": c.description,
        "tags": c.tags,
        "topics": c.topics,
        "domain": c.domain,
        "knowledge_ids": c.knowledge_ids,
        "totalChapters": len(c.chapters),
        "completedChapters": sum(1 for ch in c.chapters if ch.completed),
        "status": c.status,
        "generated_by": c.generated_by,
        "createdAt": c.created_at,
        "updatedAt": c.updated_at,
    }


def _chapter_dict(ch) -> dict:
    return {
        "id": ch.chapter_id,
        "title": ch.title,
        "content": ch.content,
        "order": ch.order,
        "completed": ch.completed,
        "completedAt": ch.completed_at,
        "quiz": [
            {
                "question": q.question,
                "options": q.options,
                "correctIndex": q.correct_index,
                "explanation": q.explanation,
            }
            for q in ch.quiz
        ],
    }


# ── Course Endpoints ────────────────────────────────────────

@router.get("/courses")
async def list_courses(status: str = Query(default=None)):
    """List all courses, optionally filtered by status."""
    courses = engine.list_courses(status=status)
    return {"courses": [_course_dict(c) for c in courses], "count": len(courses)}


@router.post("/courses")
async def create_course(req: CourseCreateRequest):
    """Create a new course."""
    course = engine.create_course(
        title=req.title,
        description=req.description,
        tags=req.tags,
        topics=req.topics,
        domain=req.domain,
        knowledge_ids=req.knowledge_ids,
        generated_by=req.generated_by,
    )
    return _course_dict(course)


@router.get("/courses/{course_id}")
async def get_course(course_id: str):
    """Get full course detail including chapters."""
    course = engine.get_course(course_id)
    if not course:
        raise HTTPException(404, "Course not found")
    data = _course_dict(course)
    data["chapters"] = [_chapter_dict(ch) for ch in course.chapters]
    return data


@router.put("/courses/{course_id}")
async def update_course(course_id: str, req: CourseUpdateRequest):
    """Update course metadata."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    course = engine.update_course(course_id, **updates)
    if not course:
        raise HTTPException(404, "Course not found")
    return _course_dict(course)


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str):
    """Delete a course and all its chapters."""
    if not engine.delete_course(course_id):
        raise HTTPException(404, "Course not found")
    return {"status": "deleted"}


# ── Chapter Endpoints ───────────────────────────────────────

@router.post("/courses/{course_id}/chapters")
async def add_chapter(course_id: str, req: ChapterCreateRequest):
    """Add a chapter to a course."""
    chapter = engine.add_chapter(
        course_id, title=req.title, content=req.content, quiz=req.quiz
    )
    if not chapter:
        raise HTTPException(404, "Course not found")
    return _chapter_dict(chapter)


@router.put("/courses/{course_id}/chapters/{chapter_id}")
async def update_chapter(course_id: str, chapter_id: str, req: ChapterUpdateRequest):
    """Update a chapter's content."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    chapter = engine.update_chapter(course_id, chapter_id, **updates)
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    return _chapter_dict(chapter)


@router.delete("/courses/{course_id}/chapters/{chapter_id}")
async def delete_chapter(course_id: str, chapter_id: str):
    """Delete a chapter."""
    if not engine.delete_chapter(course_id, chapter_id):
        raise HTTPException(404, "Chapter not found")
    return {"status": "deleted"}


@router.post("/courses/{course_id}/chapters/{chapter_id}/mark")
async def mark_chapter(course_id: str, chapter_id: str, req: MarkChapterRequest):
    """Mark a chapter as completed/incomplete."""
    chapter = engine.mark_chapter(course_id, chapter_id, req.completed)
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    return _chapter_dict(chapter)


# ── Quiz Endpoints ──────────────────────────────────────────

@router.get("/courses/{course_id}/chapters/{chapter_id}/quiz")
async def get_quiz(course_id: str, chapter_id: str):
    """Get quiz questions for a chapter."""
    quiz = engine.get_quiz(course_id, chapter_id)
    if quiz is None:
        raise HTTPException(404, "Chapter not found")
    return {
        "questions": [
            {
                "question": q.question,
                "options": q.options,
                "correctIndex": q.correct_index,
                "explanation": q.explanation,
            }
            for q in quiz
        ],
        "count": len(quiz),
    }


@router.post("/courses/{course_id}/chapters/{chapter_id}/quiz")
async def set_quiz(course_id: str, chapter_id: str, req: QuizSubmitRequest):
    """Set/replace quiz questions for a chapter."""
    quiz = engine.add_quiz(course_id, chapter_id, req.questions)
    if quiz is None:
        raise HTTPException(404, "Chapter not found")
    return {
        "questions": [
            {
                "question": q.question,
                "options": q.options,
                "correctIndex": q.correct_index,
                "explanation": q.explanation,
            }
            for q in quiz
        ],
        "count": len(quiz),
    }


# ── Stats & Health ──────────────────────────────────────────

@router.get("/stats")
async def learn_stats():
    """Get learning statistics."""
    return engine.stats()


@router.get("/health")
async def learn_health():
    """Health check for OpenLearn."""
    stats = engine.stats()
    return {
        "status": "ok",
        "component": "OpenLearn",
        "stats": stats,
    }
