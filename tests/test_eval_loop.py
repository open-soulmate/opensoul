"""Tests for the P0-5 evaluation loop (benchmark/eval_loop.py).

All tests are offline: runner/judge are stub callables, store uses a
temporary SQLite file. agno/langfuse semantics under test:
deterministic sampling, env/policy fingerprint separation, error-storm
breaker, unscored≠failed pass_rate, learning_zone, baseline diff.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.benchmark.eval_loop import (
    JUDGE_SYSTEM_PROMPT,
    CodeScorer,
    EnvMismatchError,
    EvalCase,
    EvalStore,
    ExperimentRunner,
    JudgeScorer,
    _parse_judge_verdict,
    diff_against_baseline,
)


@pytest.fixture
def store(tmp_path):
    return EvalStore(db_path=str(tmp_path / "eval_loop.db"))


def _mk_dataset(store, n=3, expected="42"):
    ds = store.create_dataset("test-ds", "unit test dataset")
    cases = [
        EvalCase(
            case_id=f"c{i}",
            prompt=f"Solve task {i}",
            expected=expected,
            task_type="math" if i % 2 == 0 else "qa",
        )
        for i in range(n)
    ]
    store.add_cases(ds, cases)
    return ds


def run(coro):
    """Run *coro* to completion on a fresh event loop.

    Uses asyncio.run() instead of get_event_loop().run_until_complete():
    pytest-asyncio closes and unsets the thread-local loop after async
    tests in earlier modules, so get_event_loop() raises RuntimeError in
    combined test runs (module-order-dependent isolation bug — this helper
    broke when run after any @pytest.mark.asyncio module). asyncio.run()
    creates a fresh loop per call, immune to prior-module loop state.
    """
    return asyncio.run(coro)


# ── EvalStore: datasets / cases / deterministic sampling ──────────────


class TestEvalStore:
    def test_create_and_get_dataset(self, store):
        ds = store.create_dataset("name", "desc")
        data = store.get_dataset(ds)
        assert data["name"] == "name"
        assert data["description"] == "desc"
        assert data["case_count"] == 0

    def test_add_cases_assigns_ids(self, store):
        ds = store.create_dataset("ds")
        n = store.add_cases(ds, [EvalCase(case_id="", prompt="p1", expected="x")])
        assert n == 1
        cases = store.list_cases(ds)
        assert len(cases) == 1
        assert cases[0].case_id.startswith("case_")

    def test_add_cases_unknown_dataset(self, store):
        with pytest.raises(ValueError):
            store.add_cases("ds_missing", [EvalCase(case_id="a", prompt="p")])

    def test_add_cases_replaces_by_id(self, store):
        ds = _mk_dataset(store, n=1)
        store.add_cases(ds, [EvalCase(case_id="c0", prompt="updated", expected="99")])
        cases = store.list_cases(ds)
        assert len(cases) == 1
        assert cases[0].prompt == "updated"
        assert cases[0].expected == "99"

    def test_list_cases_tags_metadata_roundtrip(self, store):
        ds = store.create_dataset("ds")
        store.add_cases(
            ds,
            [EvalCase(case_id="a", prompt="p", tags=["t1"], metadata={"h": 1})],
        )
        case = store.list_cases(ds)[0]
        assert case.tags == ["t1"]
        assert case.metadata == {"h": 1}

    def test_deterministic_sampling_same_seed(self, store):
        ds = _mk_dataset(store, n=8, expected="e")
        order1 = [c.case_id for c in store.list_cases(ds, seed=7)]
        order2 = [c.case_id for c in store.list_cases(ds, seed=7)]
        assert order1 == order2
        # full set preserved
        assert sorted(order1) == sorted([f"c{i}" for i in range(8)])

    def test_deterministic_sampling_limit(self, store):
        ds = _mk_dataset(store, n=8)
        sampled = store.list_cases(ds, seed=3, limit=5)
        assert len(sampled) == 5
        again = store.list_cases(ds, seed=3, limit=5)
        assert [c.case_id for c in sampled] == [c.case_id for c in again]

    def test_env_fingerprint_stable_and_sensitive(self, store):
        ds = _mk_dataset(store, n=3)
        fp1 = store.env_fingerprint(ds, ["s1"], k=2)
        fp2 = store.env_fingerprint(ds, ["s1"], k=2)
        assert fp1 == fp2
        # k changes env
        assert store.env_fingerprint(ds, ["s1"], k=3) != fp1
        # scorer set changes env
        assert store.env_fingerprint(ds, ["s2"], k=2) != fp1
        # dataset content changes env
        store.add_cases(ds, [EvalCase(case_id="new", prompt="p", expected="z")])
        assert store.env_fingerprint(ds, ["s1"], k=2) != fp1

    def test_experiment_roundtrip(self, store):
        results = {
            "experiment_id": "exp_x",
            "dataset_id": "ds_x",
            "policy_fingerprint": "p1",
            "env_fingerprint": "e1",
            "k": 1,
            "cases": {},
            "circuit_broken": False,
            "created_at": 1.0,
        }
        store.save_experiment(results)
        loaded = store.get_experiment("exp_x")
        assert loaded["experiment_id"] == "exp_x"
        assert loaded["env_fingerprint"] == "e1"
        listed = store.list_experiments(dataset_id="ds_x")
        assert len(listed) == 1
        assert store.get_experiment("nope") is None


# ── CodeScorer ────────────────────────────────────────────────────────


class TestCodeScorer:
    def test_contains_pass_and_fail(self, store):
        sc = CodeScorer()
        case = EvalCase(case_id="a", prompt="p", expected="42")
        ok = run(sc.score(case, "the answer is 42"))
        assert ok.scored and ok.passed and ok.score == 1.0
        bad = run(sc.score(case, "the answer is 41"))
        assert bad.scored and not bad.passed and bad.score == 0.0
        assert bad.source == "code_eval"

    def test_abstains_without_expected(self, store):
        sc = CodeScorer()
        res = run(sc.score(EvalCase(case_id="a", prompt="p"), "anything"))
        assert not res.scored
        assert "abstain" in res.reason

    def test_not_contains(self):
        sc = CodeScorer(check="not_contains")
        case = EvalCase(case_id="a", prompt="p", expected="error")
        assert run(sc.score(case, "all good")).passed
        assert not run(sc.score(case, "an error occurred")).passed

    def test_regex_mode(self):
        sc = CodeScorer(check="regex")
        case = EvalCase(case_id="a", prompt="p", expected=r"\d{3}-\d{4}")
        assert run(sc.score(case, "call 555-1234 now")).passed
        assert not run(sc.score(case, "call 5551234 now")).passed

    def test_equals_mode(self):
        sc = CodeScorer(check="equals")
        case = EvalCase(case_id="a", prompt="p", expected="ok")
        assert run(sc.score(case, "ok")).passed
        assert not run(sc.score(case, "ok!")).passed

    def test_custom_case_fn(self):
        sc = CodeScorer(case_fn=lambda case, output: len(output) < 10)
        case = EvalCase(case_id="a", prompt="p", expected="ignored")
        assert run(sc.score(case, "short")).passed
        assert not run(sc.score(case, "way too long output")).passed

    def test_invalid_check_mode(self):
        with pytest.raises(ValueError):
            CodeScorer(check="vibes")

    def test_digest_changes_with_config(self):
        assert CodeScorer().digest != CodeScorer(check="regex").digest
        assert CodeScorer().digest == CodeScorer().digest


# ── JudgeScorer + verdict parsing ─────────────────────────────────────


class TestJudgeScorer:
    def test_parses_json_verdict(self):
        async def judge(messages):
            return '{"score": 8, "pass": true, "reason": "solid"}'

        sc = JudgeScorer(judge, model_identity={"model": "m1"})
        res = run(sc.score(EvalCase(case_id="a", prompt="p", rubric="r"), "out"))
        assert res.scored and res.passed and res.score == 0.8
        assert res.source == "llm_judge"
        assert res.reason == "solid"

    def test_fence_and_untrusted_markers(self):
        async def judge(messages):
            return "{}"

        sc = JudgeScorer(judge)
        msgs = sc.build_messages(
            EvalCase(case_id="a", prompt="task-p", rubric="be right"), "OUTPUT_HERE"
        )
        system, user = msgs[0]["content"], msgs[1]["content"]
        assert system == JUDGE_SYSTEM_PROMPT
        assert "UNTRUSTED" in system
        assert "<candidate_output>" in user and "OUTPUT_HERE" in user
        assert "task-p" in user and "be right" in user

    def test_fenced_json_parsed(self):
        parsed = _parse_judge_verdict('blah ```json\n{"score": 6.5, "pass": true}\n``` end', 5.0)
        assert parsed == (6.5, True, "")

    def test_pass_inferred_from_threshold(self):
        parsed = _parse_judge_verdict('{"score": 3}', 5.0)
        assert parsed is not None
        assert parsed[0] == 3.0 and parsed[1] is False

    def test_regex_fallback(self):
        parsed = _parse_judge_verdict("I think score: 7/10 overall", 5.0)
        assert parsed == (7.0, True, "regex fallback parse")

    def test_unparseable_abstains(self):
        async def judge(messages):
            return "the model refused to answer properly"

        sc = JudgeScorer(judge)
        res = run(sc.score(EvalCase(case_id="a", prompt="p"), "out"))
        assert not res.scored
        assert "unparseable" in res.reason

    def test_judge_error_abstains(self):
        async def judge(messages):
            raise RuntimeError("provider down")

        sc = JudgeScorer(judge)
        res = run(sc.score(EvalCase(case_id="a", prompt="p"), "out"))
        assert not res.scored
        assert "judge error" in res.reason

    def test_score_clamped(self):
        parsed = _parse_judge_verdict('{"score": 99, "pass": true}', 5.0)
        assert parsed is not None
        assert parsed[0] == 10.0

    def test_digest_includes_identity(self):
        async def judge(messages):
            return "{}"

        s1 = JudgeScorer(judge, model_identity={"model": "judge-a"})
        s2 = JudgeScorer(judge, model_identity={"model": "judge-b"})
        assert s1.digest != s2.digest


# ── ExperimentRunner ──────────────────────────────────────────────────


def _make_easy_runner():
    """Runner stub: passes cases mentioning task 0/2 (expected '42'), fails others."""

    async def fn(case: EvalCase):
        return "42 done" if "task 0" in case.prompt or "task 2" in case.prompt else "no idea"

    return fn


class TestExperimentRunner:
    def test_basic_pass_rate(self, store):
        ds = _mk_dataset(store, n=3, expected="42")
        runner = ExperimentRunner(store, ds, _make_easy_runner(), [CodeScorer()], k=1)
        results = run(runner.run())
        assert results["cases"]["c0"]["pass_rate"] == 1.0  # "42 done"
        assert results["cases"]["c1"]["pass_rate"] == 0.0  # "no idea"
        assert results["cases"]["c2"]["pass_rate"] == 1.0
        assert results["summary"]["cases_run"] == 3
        assert results["summary"]["mean_pass_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_unknown_dataset(self, store):
        runner = ExperimentRunner(store, "ds_nope", _make_easy_runner(), [CodeScorer()])
        with pytest.raises(ValueError):
            run(runner.run())

    def test_errored_attempts_excluded_from_pass_rate(self, store):
        """agno: 超时≠答错 — unscored attempts leave the denominator."""
        ds = store.create_dataset("ds")
        store.add_cases(ds, [EvalCase(case_id="only", prompt="task 0", expected="42")])
        calls = {"n": 0}

        async def flaky(case):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise TimeoutError("slow")
            return "42"

        runner = ExperimentRunner(store, ds, flaky, [CodeScorer()], k=3)
        results = run(runner.run())
        rec = results["cases"]["only"]
        assert rec["scored_count"] == 1
        assert rec["pass_rate"] == 1.0  # the ONE scored attempt passed
        assert results["circuit_broken"] is False  # streak broke after 2 errors
        assert results["summary"]["errored_attempts"] == 2

    def test_error_storm_breaker_trips(self, store):
        ds = store.create_dataset("ds")
        store.add_cases(
            ds,
            [EvalCase(case_id=f"c{i}", prompt=f"p{i}", expected="x") for i in range(4)],
        )

        async def dead(case):
            raise ConnectionError("provider dead")

        runner = ExperimentRunner(store, ds, dead, [CodeScorer()], k=2, storm_window=3)
        results = run(runner.run())
        assert results["circuit_broken"] is True
        assert "ConnectionError" in results["circuit_reason"]
        # aborted early: fewer cases recorded than exist
        assert len(results["cases"]) < 4

    def test_error_storm_requires_same_type(self, store):
        ds = store.create_dataset("ds")
        store.add_cases(
            ds,
            [EvalCase(case_id=f"c{i}", prompt=f"p{i}", expected="x") for i in range(4)],
        )
        errs = [ValueError("a"), TimeoutError("b"), KeyError("c"), ValueError("d")]
        idx = {"i": 0}

        async def mixed(case):
            i = idx["i"]
            idx["i"] += 1
            raise errs[i % len(errs)]

        runner = ExperimentRunner(store, ds, mixed, [CodeScorer()], k=1, storm_window=3)
        results = run(runner.run())
        assert results["circuit_broken"] is False
        assert len(results["cases"]) == 4

    def test_learning_zone_mixed_outcomes(self, store):
        ds = store.create_dataset("ds")
        store.add_cases(
            ds,
            [
                EvalCase(case_id="always", prompt="task 0", expected="42"),
                EvalCase(case_id="never", prompt="hard", expected="42"),
                EvalCase(case_id="mixed", prompt="mixed", expected="42"),
            ],
        )
        flip = {"n": 0}

        async def runner_fn(case):
            if case.case_id == "always":
                return "42"
            if case.case_id == "never":
                return "no"
            flip["n"] += 1
            return "42" if flip["n"] % 2 == 0 else "no"

        runner = ExperimentRunner(store, ds, runner_fn, [CodeScorer()], k=4)
        results = run(runner.run())
        assert results["learning_zone"] == ["mixed"]
        assert results["summary"]["learning_zone_size"] == 1

    def test_baseline_diff_improved_regressed(self, store):
        ds = _mk_dataset(store, n=2, expected="42")

        async def old_policy(case):
            return "no"  # fails everything

        async def new_policy(case):
            return "42" if case.case_id == "c0" else "no"

        base = ExperimentRunner(
            store,
            ds,
            old_policy,
            [CodeScorer()],
            k=2,
            policy_name="v1",
            policy_meta={"model": "a"},
        ).run()
        base_results = run(base)

        cur = ExperimentRunner(
            store,
            ds,
            new_policy,
            [CodeScorer()],
            k=2,
            policy_name="v2",
            policy_meta={"model": "b"},
            baseline_id=base_results["experiment_id"],
        )
        results = run(cur.run())
        diff = results["baseline_diff"]
        assert diff["policy_changed"] is True
        assert [d["case_id"] for d in diff["improved"]] == ["c0"]
        assert diff["regressed"] == []
        assert diff["unchanged"] == ["c1"]

    def test_baseline_env_mismatch_refused(self, store):
        ds = _mk_dataset(store, n=2, expected="42")

        async def ok(case):
            return "42"

        base_results = run(ExperimentRunner(store, ds, ok, [CodeScorer()], k=2).run())
        # env changes: different k
        runner = ExperimentRunner(
            store,
            ds,
            ok,
            [CodeScorer()],
            k=3,
            baseline_id=base_results["experiment_id"],
        )
        with pytest.raises(EnvMismatchError):
            run(runner.run())

    def test_policy_fingerprint_sensitivity(self, store):
        ds = _mk_dataset(store, n=1)

        async def ok(case):
            return "42"

        r1 = ExperimentRunner(store, ds, ok, [CodeScorer()], policy_meta={"model": "m1"})
        r2 = ExperimentRunner(store, ds, ok, [CodeScorer()], policy_meta={"model": "m2"})
        r3 = ExperimentRunner(store, ds, ok, [CodeScorer()], policy_meta={"model": "m1"})
        assert r1.policy_fingerprint != r2.policy_fingerprint
        assert r1.policy_fingerprint == r3.policy_fingerprint
        # same policy + same env → fingerprints equal (diff shows policy_changed=False)
        assert r1.env_fingerprint() == r3.env_fingerprint()

    def test_results_persisted(self, store):
        ds = _mk_dataset(store, n=1)

        async def ok(case):
            return "42"

        results = run(ExperimentRunner(store, ds, ok, [CodeScorer()]).run())
        loaded = store.get_experiment(results["experiment_id"])
        assert loaded is not None
        assert loaded["cases"]["c0"]["pass_rate"] == 1.0
        assert loaded["summary"]["cases_scored"] == 1


# ── Scorer chain + trajectory integration ─────────────────────────────


class TestScorerChainAndTrajectory:
    def test_code_first_judge_fallback(self, store):
        """Code scorer takes cases with expected values; judge handles the rest."""
        ds = store.create_dataset("ds")
        store.add_cases(
            ds,
            [
                EvalCase(case_id="with_expected", prompt="p1", expected="42"),
                EvalCase(case_id="judge_only", prompt="p2", rubric="must be polite"),
            ],
        )
        judge_calls = {"n": 0}

        async def judge(messages):
            judge_calls["n"] += 1
            return '{"score": 9, "pass": true, "reason": "polite"}'

        scorers = [CodeScorer(), JudgeScorer(judge, model_identity={"model": "j"})]

        async def runner_fn(case):
            return "42"

        results = run(ExperimentRunner(store, ds, runner_fn, scorers, k=1).run())
        a1 = results["cases"]["with_expected"]["attempts"][0]
        a2 = results["cases"]["judge_only"]["attempts"][0]
        assert a1["scorer"] == "code_eval"
        assert a2["scorer"] == "llm_judge"
        assert judge_calls["n"] == 1  # judge only invoked for the abstained case

    def test_all_scorers_abstain_unscored(self, store):
        ds = store.create_dataset("ds")
        store.add_cases(ds, [EvalCase(case_id="a", prompt="p")])  # no expected, no rubric check

        async def judge(messages):
            return "garbage verdict"

        async def runner_fn(case):
            return "out"

        runner = ExperimentRunner(store, ds, runner_fn, [CodeScorer(), JudgeScorer(judge)], k=1)
        results = run(runner.run())
        rec = results["cases"]["a"]
        assert rec["scored_count"] == 0
        assert rec["pass_rate"] is None  # unscored is NOT zero
        assert "abstain" in rec["attempts"][0]["reason"]

    def test_trajectory_scores_written(self, store):
        ds = _mk_dataset(store, n=1, expected="42")

        class FakeTrajectoryStore:
            def __init__(self):
                self.scores = []

            async def add_score(self, score):
                self.scores.append(score)

        fake_ts = FakeTrajectoryStore()

        async def ok(case):
            return "42"

        runner = ExperimentRunner(
            store,
            ds,
            ok,
            [CodeScorer()],
            k=2,
            session_prefix="evaltest",
            trajectory_store=fake_ts,
        )
        results = run(runner.run())
        assert len(fake_ts.scores) == 2
        score = fake_ts.scores[0]
        assert score.source == "code_eval"
        assert score.value == 1.0
        assert score.trace_id == results["experiment_id"]
        assert score.session_id == "evaltest_c0"
        assert score.name == f"eval/{ds}/c0"


# ── diff_against_baseline standalone ──────────────────────────────────


class TestDiffStandalone:
    def test_new_case_and_unscored_buckets(self):
        baseline = {
            "experiment_id": "b",
            "env_fingerprint": "e",
            "policy_fingerprint": "p",
            "cases": {"a": {"pass_rate": None, "pass_count": 0, "scored_count": 0}},
        }
        current = {
            "experiment_id": "c",
            "env_fingerprint": "e",
            "policy_fingerprint": "p",
            "cases": {
                "a": {"pass_rate": 1.0, "pass_count": 1, "scored_count": 1},
                "fresh": {"pass_rate": 0.0, "pass_count": 0, "scored_count": 1},
            },
        }
        diff = diff_against_baseline(baseline, current)
        assert diff["unscored"] == ["a"]
        assert diff["new_cases"] == ["fresh"]
        assert diff["policy_changed"] is False

    def test_unknown_baseline_raises(self, store):
        ds = _mk_dataset(store, n=1)

        async def ok(case):
            return "42"

        runner = ExperimentRunner(store, ds, ok, [CodeScorer()], baseline_id="exp_missing")
        with pytest.raises(ValueError):
            run(runner.run())
