"""P1 记忆回声阻断（kilocode recalledMemory）——API级集成测试。

调研来源：kilocode-source-supplement3.md #5："本轮若跑过kilo_memory_recall且
count>0→跳过digest"——"答案来自记忆的回合不能再蒸馏回记忆"（记忆自我污染闭环
的阻断器）。此前DreamDistiller已有mark_recall/should_skip_digest但两条真实
写入路径（/ltm/add回合digest、memory_pipeline整合）不查echo状态，真实消息路径
也不mark——本轮补齐（echo_guard闸+memory_ids暴露+echo_check整合入口+acp-proxy接线）。

运行方式与tests/conftest.py一致：打live :8090（systemctl --user restart opensoul后跑）。
所有用例自带reset-turn+删除种子记忆，零残留。
"""

import uuid

import pytest


def _token() -> str:
    return f"echo_probe_{uuid.uuid4().hex[:10]}"


@pytest.fixture
def echo_reset(client):
    """每个用例前后清回声状态（全局turn状态，防用例间串扰）"""
    client.post("/api/hippo/ltm/dream/reset-turn")
    yield
    client.post("/api/hippo/ltm/dream/reset-turn")


def _add(client, content, **kw):
    payload = {"content": content, "memory_type": "episodic", "importance": 0.6}
    payload.update(kw)
    return client.post("/api/hippo/ltm/add", json=payload)


def _delete(client, memory_id):
    client.request(
        "DELETE",
        f"/api/hippo/ltm/{memory_id}",
        json={"reason": "test_cleanup", "hard_delete": True},
    )


class TestEchoBlockOnLTMAdd:
    """kilocode #5：召回过的回合digest必须跳过（/ltm/add回声闸）"""

    def test_add_without_recall_stores(self, client, echo_reset):
        tok = _token()
        resp = _add(client, f"{tok} 用户提到喜欢攀岩运动")
        data = resp.json()
        assert data.get("added") is True, data
        assert data.get("outcome") != "echo_blocked"
        _delete(client, data["memory_id"])

    def test_digest_blocked_after_recall(self, client, echo_reset):
        tok_seed, tok_digest = _token(), _token()
        seed = _add(client, f"{tok_seed} 用户最喜欢的数字是42424242").json()
        assert seed.get("added") is True, seed
        try:
            # 本回合召回了seed记忆→mark
            mark = client.post(
                "/api/hippo/ltm/dream/recall-mark", json={"memory_ids": [seed["memory_id"]]}
            ).json()
            assert mark["marked"] == 1
            assert mark["echo_stats"]["digest_blocked"] is True
            # 回合digest写入必须被回声闸拦截（显式reason非静默）
            digest = _add(client, f"{tok_digest} 用户: 问题\n助手: 答案来自记忆").json()
            assert digest.get("added") is False, digest
            assert digest.get("outcome") == "echo_blocked"
            assert "echo blocker" in digest.get("reason", "")
            assert digest.get("memory_id") == ""
            # 确认真的没写进去
            listed = client.get("/api/hippo/ltm/list", params={"limit": 200}).json()
            contents = str(listed)
            assert tok_digest not in contents
        finally:
            _delete(client, seed["memory_id"])

    def test_echo_guard_false_override(self, client, echo_reset):
        """显式写入（echo_guard=False）不受回声闸限制"""
        tok = _token()
        mark = client.post("/api/hippo/ltm/dream/recall-mark", json={"memory_ids": ["manual_x"]})
        assert mark.status_code == 200
        data = {}
        try:
            resp = _add(client, f"{tok} 人工显式记录的事实", echo_guard=False)
            data = resp.json()
            assert data.get("added") is True, data
        finally:
            if data.get("memory_id"):
                _delete(client, data["memory_id"])

    def test_reset_turn_clears_echo_state(self, client, echo_reset):
        tok = _token()
        mark = client.post("/api/hippo/ltm/dream/recall-mark", json={"memory_ids": ["m_z"]})
        assert mark.json()["echo_stats"]["digest_blocked"] is True
        reset = client.post("/api/hippo/ltm/dream/reset-turn").json()
        assert reset["echo_stats"]["digest_blocked"] is False
        try:
            data = _add(client, f"{tok} reset之后的新回合内容").json()
            assert data.get("added") is True, data
        finally:
            if data.get("memory_id"):
                _delete(client, data["memory_id"])


class TestContextMemoryIds:
    """/ltm/context必须暴露实际注入的记忆id（mark_recall的输入）"""

    def test_context_returns_injected_ids(self, client, echo_reset):
        tok = _token()
        r1 = _add(client, f"{tok} 用户的猫叫芝麻").json()
        r2 = _add(client, f"{tok} 用户的狗叫汤圆").json()
        try:
            resp = client.post("/api/hippo/ltm/context", json={"query": tok, "limit": 3})
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("context"), data
            ids = data.get("memory_ids", [])
            assert set(ids) >= {r1["memory_id"], r2["memory_id"]}, ids
        finally:
            _delete(client, r1["memory_id"])
            _delete(client, r2["memory_id"])

    def test_context_no_match_empty_ids(self, client, echo_reset):
        data = client.post(
            "/api/hippo/ltm/context", json={"query": _token() + " 绝对不存在的记忆", "limit": 3}
        ).json()
        assert data.get("memory_ids") == []


class TestEchoBlockOnDigestPipelines:
    """kilocode"整合入口一行判断"：dream蒸馏+memory_pipeline整合同样受回声闸"""

    def test_dream_echo_blocked(self, client, echo_reset):
        mark = client.post("/api/hippo/ltm/dream/recall-mark", json={"memory_ids": ["m_d"]})
        assert mark.json()["echo_stats"]["digest_blocked"] is True
        result = client.post(
            "/api/hippo/ltm/dream",
            json={
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "yo"},
                ]
            },
        ).json()
        assert result.get("echo_blocked") is True, result
        assert "echo blocker" in result.get("error", "")
        # reset后不再被拦（空消息的error是"no messages"而非echo_blocked，证明闸已开）
        client.post("/api/hippo/ltm/dream/reset-turn")
        result2 = client.post("/api/hippo/ltm/dream", json={"messages": []}).json()
        assert result2.get("echo_blocked") is False
        assert "No messages" in result2.get("error", "")

    def test_pipeline_run_echo_blocked(self, client, echo_reset):
        tok = _token()
        mark = client.post("/api/hippo/ltm/dream/recall-mark", json={"memory_ids": ["m_p"]})
        assert mark.json()["echo_stats"]["digest_blocked"] is True
        result = client.post(
            "/api/hippo/ltm/pipeline/run",
            json={
                "candidates": [{"content": f"{tok} 候选事实", "memory_type": "semantic"}],
                "apply": True,
                "use_llm_phase2": False,
            },
        ).json()
        assert result.get("echo_blocked") is True, result
        assert result.get("diff") == [], result
        assert "echo_blocked" in result.get("error", "")
        # reset后管线恢复（apply=False dry-run不落库零残留）
        client.post("/api/hippo/ltm/dream/reset-turn")
        result2 = client.post(
            "/api/hippo/ltm/pipeline/run",
            json={
                "candidates": [{"content": f"{tok} 候选事实", "memory_type": "semantic"}],
                "apply": False,
                "use_llm_phase2": False,
            },
        ).json()
        assert result2.get("echo_blocked") is False, result2

    def test_pipeline_force_bypasses(self, client, echo_reset):
        """force=True手动触发绕过回声闸（dream force语义对齐）"""
        tok = _token()
        client.post("/api/hippo/ltm/dream/recall-mark", json={"memory_ids": ["m_f"]})
        result = client.post(
            "/api/hippo/ltm/pipeline/run",
            json={
                "candidates": [{"content": f"{tok} force候选", "memory_type": "semantic"}],
                "apply": False,
                "use_llm_phase2": False,
                "force": True,
            },
        ).json()
        assert result.get("echo_blocked") is False, result
