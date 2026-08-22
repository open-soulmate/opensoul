"""Learn engine — manage courses, chapters, quizzes and progress tracking."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class QuizQuestion:
    question: str
    options: list[str]
    correct_index: int
    explanation: str = ""


@dataclass
class Chapter:
    chapter_id: str
    title: str
    content: str  # Markdown content
    order: int = 0
    completed: bool = False
    completed_at: float | None = None
    quiz: list[QuizQuestion] = field(default_factory=list)


@dataclass
class Course:
    course_id: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    domain: str = ""
    knowledge_ids: list[str] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    status: str = "not_started"  # not_started | in_progress | reviewing | completed
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    generated_by: str = "manual"  # manual | ai


class CourseEngine:
    """Course management with persistence."""

    def __init__(self, storage_dir: str | Path | None = None):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.opensoul/learn"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._courses: dict[str, Course] = {}
        self._lock = threading.Lock()
        self._load_courses()

    def _load_courses(self):
        for f in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                chapters = []
                for ch in data.get("chapters", []):
                    quiz = [QuizQuestion(**q) for q in ch.pop("quiz", [])]
                    chapters.append(Chapter(quiz=quiz, **ch))
                data["chapters"] = chapters
                c = Course(**data)
                self._courses[c.course_id] = c
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
    def _save_course(self, course: Course):
        path = self.storage_dir / f"{course.course_id}.json"
        data = asdict(course)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def _update_status(self, course: Course):
        if not course.chapters:
            course.status = "not_started"
            return
        completed = sum(1 for ch in course.chapters if ch.completed)
        if completed == 0:
            course.status = "not_started"
        elif completed == len(course.chapters):
            course.status = "completed"
        else:
            course.status = "in_progress"

    # ── Course CRUD ─────────────────────────────────────────

    def create_course(
        self,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        topics: list[str] | None = None,
        domain: str = "",
        knowledge_ids: list[str] | None = None,
        generated_by: str = "manual",
    ) -> Course:
        course = Course(
            course_id=f"course_{uuid.uuid4().hex[:12]}",
            title=title,
            description=description,
            tags=tags or [],
            topics=topics or [],
            domain=domain,
            knowledge_ids=knowledge_ids or [],
            generated_by=generated_by,
        )
        with self._lock:
            self._courses[course.course_id] = course
            self._save_course(course)
        return course

    def get_course(self, course_id: str) -> Course | None:
        return self._courses.get(course_id)

    def list_courses(self, status: str | None = None) -> list[Course]:
        courses = list(self._courses.values())
        if status:
            courses = [c for c in courses if c.status == status]
        courses.sort(key=lambda c: c.updated_at, reverse=True)
        return courses

    def update_course(self, course_id: str, **kwargs) -> Course | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(course, k) and k not in ("course_id", "created_at", "chapters"):
                    setattr(course, k, v)
            course.updated_at = time.time()
            self._save_course(course)
        return course

    def delete_course(self, course_id: str) -> bool:
        with self._lock:
            if course_id in self._courses:
                del self._courses[course_id]
                path = self.storage_dir / f"{course_id}.json"
                path.unlink(missing_ok=True)
                return True
        return False

    # ── Chapter CRUD ────────────────────────────────────────

    def add_chapter(
        self,
        course_id: str,
        title: str,
        content: str = "",
        quiz: list[dict] | None = None,
    ) -> Chapter | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        chapter = Chapter(
            chapter_id=f"ch_{uuid.uuid4().hex[:12]}",
            title=title,
            content=content,
            order=len(course.chapters),
            quiz=[QuizQuestion(**q) for q in (quiz or [])],
        )
        with self._lock:
            course.chapters.append(chapter)
            course.updated_at = time.time()
            self._save_course(course)
        return chapter

    def update_chapter(self, course_id: str, chapter_id: str, **kwargs) -> Chapter | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        with self._lock:
            for ch in course.chapters:
                if ch.chapter_id == chapter_id:
                    for k, v in kwargs.items():
                        if hasattr(ch, k) and k not in ("chapter_id",):
                            setattr(ch, k, v)
                    course.updated_at = time.time()
                    self._update_status(course)
                    self._save_course(course)
                    return ch
        return None

    def delete_chapter(self, course_id: str, chapter_id: str) -> bool:
        course = self._courses.get(course_id)
        if not course:
            return False
        with self._lock:
            before = len(course.chapters)
            course.chapters = [ch for ch in course.chapters if ch.chapter_id != chapter_id]
            if len(course.chapters) < before:
                # Re-order
                for i, ch in enumerate(course.chapters):
                    ch.order = i
                course.updated_at = time.time()
                self._update_status(course)
                self._save_course(course)
                return True
        return False

    def mark_chapter(self, course_id: str, chapter_id: str, completed: bool) -> Chapter | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        with self._lock:
            for ch in course.chapters:
                if ch.chapter_id == chapter_id:
                    ch.completed = completed
                    ch.completed_at = time.time() if completed else None
                    course.updated_at = time.time()
                    self._update_status(course)
                    self._save_course(course)
                    return ch
        return None

    # ── Quiz ────────────────────────────────────────────────

    def add_quiz(
        self,
        course_id: str,
        chapter_id: str,
        questions: list[dict],
    ) -> list[QuizQuestion] | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        with self._lock:
            for ch in course.chapters:
                if ch.chapter_id == chapter_id:
                    ch.quiz = [QuizQuestion(**q) for q in questions]
                    course.updated_at = time.time()
                    self._save_course(course)
                    return ch.quiz
        return None

    def get_quiz(self, course_id: str, chapter_id: str) -> list[QuizQuestion] | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        for ch in course.chapters:
            if ch.chapter_id == chapter_id:
                return ch.quiz
        return None

    # ── Stats ───────────────────────────────────────────────

    def stats(self) -> dict:
        courses = list(self._courses.values())
        total_chapters = sum(len(c.chapters) for c in courses)
        completed_chapters = sum(sum(1 for ch in c.chapters if ch.completed) for c in courses)
        reviewing = sum(1 for c in courses if c.status == "reviewing")
        return {
            "total_courses": len(courses),
            "total_chapters": total_chapters,
            "completed_chapters": completed_chapters,
            "pending_chapters": total_chapters - completed_chapters,
            "reviewing_courses": reviewing,
            "by_status": {
                s: sum(1 for c in courses if c.status == s)
                for s in ("not_started", "in_progress", "reviewing", "completed")
            },
        }
