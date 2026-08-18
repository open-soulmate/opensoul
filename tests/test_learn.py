"""Integration tests for OpenLearn — course management, chapters, quizzes."""

import pytest


class TestLearnHealth:
    def test_health(self, client):
        resp = client.get("/api/learn/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenLearn"
        assert "stats" in data

    def test_stats(self, client):
        resp = client.get("/api/learn/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_courses" in data


class TestLearnCourses:
    def test_list_courses(self, client):
        resp = client.get("/api/learn/courses")
        assert resp.status_code == 200
        data = resp.json()
        assert "courses" in data
        assert "count" in data

    def test_create_course(self, client):
        resp = client.post(
            "/api/learn/courses",
            json={
                "title": "Integration Test Course",
                "description": "Created by automated test",
                "tags": ["test", "integration"],
                "topics": ["testing"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Integration Test Course"
        assert data["status"] == "not_started"
        course_id = data["id"]

        # Get course detail
        resp = client.get(f"/api/learn/courses/{course_id}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["id"] == course_id
        assert "chapters" in detail

        # Update course
        resp = client.put(
            f"/api/learn/courses/{course_id}",
            json={
                "title": "Updated Test Course",
                "status": "in_progress",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated Test Course"

        # Delete course
        resp = client.delete(f"/api/learn/courses/{course_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_get_nonexistent_course(self, client):
        resp = client.get("/api/learn/courses/nonexistent-id")
        assert resp.status_code == 404


class TestLearnChapters:
    @pytest.fixture()
    def course_id(self, client):
        resp = client.post(
            "/api/learn/courses",
            json={
                "title": "Chapter Test Course",
                "description": "For testing chapters",
            },
        )
        yield resp.json()["id"]
        client.delete(f"/api/learn/courses/{resp.json()['id']}")

    def test_add_chapter(self, client, course_id):
        resp = client.post(
            f"/api/learn/courses/{course_id}/chapters",
            json={
                "title": "Chapter 1: Introduction",
                "content": "This is the first chapter content.",
                "quiz": [
                    {
                        "question": "What is testing?",
                        "options": ["A process", "A tool", "A language", "A framework"],
                        "correct_index": 0,
                        "explanation": "Testing is the process of evaluating software.",
                    }
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Chapter 1: Introduction"
        assert data["completed"] is False
        assert len(data["quiz"]) == 1
        chapter_id = data["id"]

        # Mark chapter as completed
        resp = client.post(
            f"/api/learn/courses/{course_id}/chapters/{chapter_id}/mark",
            json={"completed": True},
        )
        assert resp.status_code == 200
        assert resp.json()["completed"] is True

        # Update chapter
        resp = client.put(
            f"/api/learn/courses/{course_id}/chapters/{chapter_id}",
            json={"title": "Updated Chapter 1"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated Chapter 1"

        # Delete chapter
        resp = client.delete(f"/api/learn/courses/{course_id}/chapters/{chapter_id}")
        assert resp.status_code == 200

    def test_chapter_not_found(self, client, course_id):
        resp = client.post(
            f"/api/learn/courses/{course_id}/chapters/fake-id/mark",
            json={"completed": True},
        )
        assert resp.status_code == 404


class TestLearnQuiz:
    @pytest.fixture()
    def course_and_chapter(self, client):
        resp = client.post("/api/learn/courses", json={"title": "Quiz Test Course"})
        cid = resp.json()["id"]
        resp = client.post(
            f"/api/learn/courses/{cid}/chapters",
            json={
                "title": "Quiz Chapter",
                "content": "Quiz content",
            },
        )
        chid = resp.json()["id"]
        yield cid, chid
        client.delete(f"/api/learn/courses/{cid}")

    def test_set_and_get_quiz(self, client, course_and_chapter):
        cid, chid = course_and_chapter
        questions = [
            {
                "question": "What is 2+2?",
                "options": ["3", "4", "5", "6"],
                "correct_index": 1,
                "explanation": "2+2 = 4",
            },
            {
                "question": "Capital of France?",
                "options": ["London", "Berlin", "Paris", "Madrid"],
                "correct_index": 2,
                "explanation": "Paris is the capital of France.",
            },
        ]
        resp = client.post(
            f"/api/learn/courses/{cid}/chapters/{chid}/quiz",
            json={"questions": questions},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

        # Get quiz
        resp = client.get(f"/api/learn/courses/{cid}/chapters/{chid}/quiz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["questions"][0]["question"] == "What is 2+2?"
