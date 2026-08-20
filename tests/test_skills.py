"""Tests for OpenSkills — unified shared skills for all AI agents."""


class TestSkillsHealth:
    def test_health(self, client):
        resp = client.get("/api/skills/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenSkills"


class TestSkillsList:
    def test_list_skills(self, client):
        resp = client.get("/api/skills/", follow_redirects=True)
        assert resp.status_code in (200, 401, 403)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (list, dict))

    def test_list_skills_returns_list(self, client):
        resp = client.get("/api/skills/", follow_redirects=True)
        assert resp.status_code in (200, 401, 403)
        if resp.status_code == 200:
            data = resp.json()
            # Should be a list of skill objects
            if isinstance(data, list):
                for skill in data:
                    assert "name" in skill


class TestSkillsInstall:
    def test_install_nonexistent_skill(self, client):
        resp = client.post("/api/skills/nonexistent-skill-xyz/install")
        # Should fail gracefully
        assert resp.status_code in (404, 400, 401, 403, 500)

    def test_delete_nonexistent_skill(self, client):
        resp = client.delete("/api/skills/nonexistent-skill-xyz")
        assert resp.status_code in (404, 400, 401, 403, 200)
