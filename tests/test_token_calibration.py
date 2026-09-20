"""d439f163遗留#3：estimate_tokens公式校准闭环 — OpenSoul侧测试（镜像）。

镜像契约：acp-proxy/tests/test_token_calibration.py以相同的字面量断言同一公式/同一
校准值（两侧compute_calibration与build_context_usage(calibration_factor=...)输出必须
一致——镜像关系见src/cortex/token_attribution.py模块docstring）。
覆盖：compute_calibration镜像值/fail-safe/剔除/夹限 + build_context_usage校准字段 +
ContextAttributor.calibration()（记录样本→校准状态）+ summary()携带calibration。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cortex.token_attribution import (  # noqa: E402
    ContextAttributor,
    ContextItem,
    build_context_usage,
    compute_calibration,
    reset_attributor,
)

# 镜像值（与acp-proxy侧测试同组数字，两侧必须一致）
MIRROR_PAIRS = [
    {"estimated": 3528, "actual": 3787},
    {"estimated": 1000, "actual": 1200},
    {"estimated": 2000, "actual": 2100},
]
MIRROR_FACTOR = 1.0856
MIRROR_AVG_GAP = 186


class TestComputeCalibrationMirror:
    def test_insufficient_samples_fail_safe(self):
        cal = compute_calibration(MIRROR_PAIRS[:2])
        assert cal["sample_count"] == 2
        assert cal["calibrated"] is False
        assert cal["factor"] == 1.0

    def test_no_samples(self):
        cal = compute_calibration([])
        assert cal == {
            "sample_count": 0,
            "calibrated": False,
            "factor": 1.0,
            "avg_estimate_gap": None,
        }

    def test_mirror_values_three_samples(self):
        cal = compute_calibration(MIRROR_PAIRS)
        assert cal["sample_count"] == 3
        assert cal["calibrated"] is True
        assert cal["factor"] == MIRROR_FACTOR
        assert cal["raw_factor"] == MIRROR_FACTOR
        assert cal["avg_estimate_gap"] == MIRROR_AVG_GAP
        assert cal["sum_estimated"] == 6528
        assert cal["sum_actual"] == 7087

    def test_invalid_pairs_excluded(self):
        pairs: list = MIRROR_PAIRS + [
            {"estimated": 0, "actual": 100},
            {"estimated": 500, "actual": None},
            None,
        ]
        cal = compute_calibration(pairs)
        assert cal["sample_count"] == 3
        assert cal["factor"] == MIRROR_FACTOR

    def test_factor_clamped_bounds(self):
        cal = compute_calibration([{"estimated": 1000, "actual": 10}] * 3)
        assert cal["factor"] == 0.5
        cal2 = compute_calibration([{"estimated": 100, "actual": 10000}] * 3)
        assert cal2["factor"] == 4.0


def _items():
    return [
        ContextItem(kind="system_prompt", name="sys", source="s", tokens=3000),
        ContextItem(kind="message", name="conv", source="session", tokens=2400),
    ]


class TestBuildContextUsageCalibration:
    def test_raw_fields_untouched_calibrated_parallel(self):
        u = build_context_usage(
            _items(), max_tokens=10000, compaction_tokens=6000, calibration_factor=1.2
        )
        assert u["total_tokens"] == 5400
        assert u["percentage"] == 54.0
        assert u["over_limit"] is None
        assert u["calibration_factor"] == 1.2
        assert u["calibrated_total_tokens"] == 6480
        assert u["calibrated_percentage"] == 64.8
        assert u["calibrated_over_limit"] == {"tokens_over": 480, "kind": "compaction_window"}

    def test_calibrated_hard_limit(self):
        u = build_context_usage(
            _items(), max_tokens=10000, compaction_tokens=6000, calibration_factor=2.0
        )
        assert u["over_limit"] is None
        assert u["calibrated_total_tokens"] == 10800
        assert u["calibrated_over_limit"] == {"tokens_over": 800, "kind": "hard_limit"}

    def test_default_factor_schema_stable(self):
        u = build_context_usage(_items(), max_tokens=10000, compaction_tokens=6000)
        assert u["calibration_factor"] == 1.0
        assert u["calibrated_total_tokens"] == u["total_tokens"]
        assert u["calibrated_over_limit"] == u["over_limit"]


class TestContextAttributorCalibration:
    def test_empty_records_fail_safe(self):
        a = ContextAttributor()
        cal = a.calibration()
        assert cal["calibrated"] is False and cal["factor"] == 1.0
        assert cal["sample_count"] == 0

    def test_backfill_records_produce_calibration(self):
        a = ContextAttributor()
        for i, (est, act) in enumerate([(3528, 3787), (1000, 1200), (2000, 2100)]):
            a.record({"total_tokens": est, "over_limit": None}, session_id=f"s{i}", model="m")
            a.backfill_actual(f"s{i}", act)  # provider usage回填→样本对
        cal = a.calibration()
        assert cal["calibrated"] is True
        assert cal["sample_count"] == 3
        assert cal["factor"] == MIRROR_FACTOR
        assert cal["avg_estimate_gap"] == MIRROR_AVG_GAP

    def test_record_with_actual_inline_counts_as_sample(self):
        a = ContextAttributor()
        for i, (est, act) in enumerate([(3528, 3787), (1000, 1200), (2000, 2100)]):
            a.record(
                {"total_tokens": est}, session_id=f"i{i}", actual_prompt_tokens=act
            )  # record时直接携带actual
        assert a.calibration()["factor"] == MIRROR_FACTOR

    def test_summary_contains_calibration(self):
        a = ContextAttributor()
        a.record({"total_tokens": 1000}, session_id="s0", actual_prompt_tokens=1200)
        s = a.summary()
        assert "calibration" in s
        assert s["calibration"]["sample_count"] == 1
        assert s["calibration"]["calibrated"] is False

    def test_build_usage_consumes_attributor_calibration(self):
        """chat.py接线语义：record里的usage带calibrated_*字段（factor来自样本）。"""
        reset_attributor()
        from src.cortex.token_attribution import get_attributor

        a = get_attributor()
        for i, (est, act) in enumerate([(3528, 3787), (1000, 1200), (2000, 2100)]):
            a.record({"total_tokens": est}, session_id=f"c{i}")
            a.backfill_actual(f"c{i}", act)
        factor = a.calibration().get("factor")
        usage = build_context_usage(
            _items(), max_tokens=10000, compaction_tokens=6000, calibration_factor=factor
        )
        assert usage["calibration_factor"] == MIRROR_FACTOR
        assert usage["calibrated_total_tokens"] == round(5400 * MIRROR_FACTOR)
        reset_attributor()
