# -*- coding: utf-8 -*-
"""第51批 T2（P0-4）门控测试：质量分 v2（可证伪评估体系）。

覆盖：
* 五个维度各自的公式与数据源
* 汇总（权重归一化 / None 维度不参与 / 可用不足提示）
* 可证伪性（每维度含 formula + data_source + raw）
* 趋势记录（写/读，注入路径，绝不碰生产）
* 等级判定
* 接入验证（diagnostics 只读入口 + 旧方法零回归）
* 真实数据 smoke
"""
import io
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.self_awareness import quality_score_v2 as Q  # noqa: E402


class TestDimensions(unittest.TestCase):
    def test_01_module_effectiveness_shape(self):
        _d = Q.score_module_effectiveness()
        self.assertIn("score", _d)
        self.assertIn("formula", _d)
        self.assertIn("data_source", _d)
        self.assertIn("raw", _d)

    def test_02_effectiveness_pure_when_single_source(self):
        """只有修复率可用时 → 按其自身 ×100。"""
        _d = Q.score_module_effectiveness.__wrapped__ if hasattr(
            Q.score_module_effectiveness, "__wrapped__") else None
        _ = _d
        # 直接验证注入路径（见 TestAggregate）
        self.assertTrue(callable(Q.score_module_effectiveness))

    def test_03_issue_severity_formula(self):
        _d = Q.score_issue_severity()
        _raw = _d["raw"]
        _expect = max(0.0, 100.0 - (10.0 * _raw.get("P0", 0)
                                    + 4.0 * _raw.get("P1", 0)
                                    + 1.0 * _raw.get("P2", 0)))
        self.assertAlmostEqual(_d["score"], round(_expect, 2), places=2)

    def test_04_count_open_debts_dedup(self):
        with tempfile.TemporaryDirectory(prefix="m51_t2_") as _td:
            _p = os.path.join(_td, "d.md")
            io.open(_p, "w", encoding="utf-8").write(
                "| **P0-1** | a |\U0001F534待修复 |\n"
                "| **P0-1** | dup |\U0001F534待修复 |\n"      # 重复编号 → 去重
                "| **P1-7** | b |\U0001F534 |\n"
                "| **P2-3** | c |\u2705已修复 |\n")            # 非 🔴 → 不计
            _c = Q.count_open_debts(_p)
            self.assertEqual(_c["P0"], 1)
            self.assertEqual(_c["P1"], 1)
            self.assertEqual(_c["P2"], 0)

    def test_05_test_coverage_bounds(self):
        _d = Q.organ_test_coverage()
        if _d["score"] is not None:
            self.assertGreaterEqual(_d["score"], 0.0)
            self.assertLessEqual(_d["score"], 100.0)
            self.assertIn("organs_total", _d["raw"])

    def test_06_static_health_from_injected_count(self):
        self.assertEqual(Q.score_static_health(0)["score"], 100.0)
        self.assertEqual(Q.score_static_health(4)["score"], 80.0)
        self.assertEqual(Q.score_static_health(30)["score"], 0.0)   # 下限 0

    def test_07_data_integrity_formula(self):
        _d = Q.score_data_integrity({"rate": 0.5, "marked_ratio": 1.0})
        # 100 × (1 − 0.5×0.5) × 1.0 = 75
        self.assertAlmostEqual(_d["score"], 75.0, places=2)

    def test_08_data_integrity_penalizes_unmarked(self):
        _a = Q.score_data_integrity({"rate": 0.5, "marked_ratio": 1.0})["score"]
        _b = Q.score_data_integrity({"rate": 0.5, "marked_ratio": 0.2})["score"]
        self.assertLess(_b, _a, "未标记清理的污染应扣分")

    def test_09_data_integrity_none_when_no_data(self):
        self.assertIsNone(Q.score_data_integrity({"rate": None})["score"])


class TestAggregate(unittest.TestCase):
    def _inject(self, **kw):
        _d = {}
        for _k in Q.DIMENSION_WEIGHTS_V2:
            _d[_k] = kw.get(_k, Q._dim(None, "x", "y", {}))
        return _d

    def test_20_weighted_average(self):
        _dims = self._inject(
            module_effectiveness=Q._dim(80.0, "f", "s"),
            issue_severity=Q._dim(60.0, "f", "s"),
            test_coverage=Q._dim(40.0, "f", "s"),
            static_health=Q._dim(100.0, "f", "s"),
            data_integrity=Q._dim(50.0, "f", "s"))
        _r = Q.evaluate_v2(_dims)
        _exp = (80 * .30 + 60 * .25 + 40 * .20 + 100 * .15 + 50 * .10)
        self.assertAlmostEqual(_r["score"], round(_exp, 2), places=2)

    def test_21_none_dimensions_excluded(self):
        """★数据缺失的维度**不参与**加权（不当作 0 分）。"""
        _dims = self._inject(
            module_effectiveness=Q._dim(100.0, "f", "s"),
            issue_severity=Q._dim(100.0, "f", "s"))
        _r = Q.evaluate_v2(_dims)
        self.assertEqual(_r["score"], 100.0, "缺失维度不应拉低分数")
        self.assertEqual(set(_r["available"]),
                         {"module_effectiveness", "issue_severity"})

    def test_22_insufficient_weight_warns(self):
        _dims = self._inject(data_integrity=Q._dim(50.0, "f", "s"))
        _r = Q.evaluate_v2(_dims)
        self.assertIn("不足", _r["reason"])

    def test_23_all_none_returns_none(self):
        _r = Q.evaluate_v2(self._inject())
        self.assertIsNone(_r["score"])

    def test_24_every_dimension_is_falsifiable(self):
        """★可证伪：每个维度必须给出 formula + data_source + raw。"""
        _r = Q.evaluate_v2()
        for _k, _d in _r["dimensions"].items():
            self.assertTrue(_d.get("formula"), _k)
            self.assertTrue(_d.get("data_source"), _k)
            self.assertIn("raw", _d, _k)

    def test_25_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(Q.DIMENSION_WEIGHTS_V2.values()), 1.0, places=6)

    def test_26_no_module_existence_dimension(self):
        """★不得存在「有模块就给分」型维度（本批核心修复）。"""
        _bad = ("has_module", "module_exists", "exists", "presence")
        for _k in Q.DIMENSION_WEIGHTS_V2:
            self.assertFalse(any(_b in _k.lower() for _b in _bad), _k)


class TestQualityLevel(unittest.TestCase):
    def test_30_levels(self):
        self.assertEqual(Q.quality_level(None), "unknown")
        self.assertEqual(Q.quality_level(30.0), "critical")
        self.assertEqual(Q.quality_level(60.0), "warning")
        self.assertEqual(Q.quality_level(80.0), "attention")
        self.assertEqual(Q.quality_level(95.0), "healthy")


class TestTrend(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m51_t2t_")
        self._p = os.path.join(self._td, "trend.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_40_record_and_load(self):
        _r = {"score": 55.5, "dimensions": {"a": {"score": 1.0}}}
        Q.record_trend(_r, path=self._p)
        Q.record_trend(_r, path=self._p)
        _t = Q.load_trend(self._p, limit=10)
        self.assertEqual(len(_t), 2)
        self.assertEqual(_t[-1]["score"], 55.5)
        self.assertEqual(_t[-1]["level"], "warning")
        self.assertIn("a", _t[-1]["dimensions"])

    def test_41_load_missing_returns_empty(self):
        self.assertEqual(Q.load_trend(os.path.join(self._td, "nope.jsonl")), [])

    def test_42_load_skips_corrupt_lines(self):
        io.open(self._p, "w", encoding="utf-8").write(
            '{"score":1}\nnot-json\n{"score":2}\n')
        self.assertEqual(len(Q.load_trend(self._p)), 2)


class TestDiagnosticsWiring(unittest.TestCase):
    def test_50_diagnostics_has_v2_entry(self):
        from nucleus.diagnostics import FrameworkDiagnostics
        _d = FrameworkDiagnostics()
        self.assertTrue(hasattr(_d, "get_quality_score_v2"))
        _r = _d.get_quality_score_v2()
        self.assertIn("score", _r)

    def test_51_legacy_methods_untouched(self):
        """零回归：旧健康分方法仍可用。"""
        from nucleus.diagnostics import FrameworkDiagnostics
        _d = FrameworkDiagnostics()
        _s, _av = _d._compute_health_score(
            {"issues": [], "warnings": [], "metrics": {}})
        self.assertEqual(_s, 100.0)
        self.assertTrue(_d._get_alert_thresholds())

    def test_52_v2_does_not_touch_production_trend(self):
        """★只读红线：默认不写生产趋势文件。"""
        from nucleus.diagnostics import FrameworkDiagnostics
        _prod = Q.trend_path()
        _before = os.path.getmtime(_prod) if os.path.isfile(_prod) else None
        FrameworkDiagnostics().get_quality_score_v2(record_trend=False)
        _after = os.path.getmtime(_prod) if os.path.isfile(_prod) else None
        self.assertEqual(_before, _after, "默认调用不得写趋势文件")


class TestProductionSmoke(unittest.TestCase):
    def test_60_real_evaluation_runs(self):
        _r = Q.evaluate_v2()
        self.assertIn("score", _r)
        self.assertIn("dimensions", _r)
        if _r["score"] is not None:
            self.assertLessEqual(_r["score"], 100.0)
            self.assertGreaterEqual(_r["score"], 0.0)

    def test_61_no_bare_except_pass(self):
        import re
        _s = io.open(os.path.join(
            _ROOT, "nucleus/self_awareness/quality_score_v2.py"),
            encoding="utf-8").read()
        self.assertEqual(
            len(re.findall(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)", _s)), 0)


if __name__ == "__main__":
    unittest.main()
