# -*- coding: utf-8 -*-
"""第51批 T1（P0-2）门控测试：补丁主动复现探针。

覆盖：
* 检测器（silent_exception / print_instead_of_log / 无判据型）
* 复现结论 6 类（true_pass / partial_fix / false_pass / ineffective /
  verification_failed / not_applicable）
* 边界（无法解析 / 空代码 / original==modified / 非 dict 输入）
* 修复率口径（严格 vs 宽松 / 无可判定样本 → None）
* 接入验证（split_verification **优先**采信 reaprobe_verdict）
* 真实数据 smoke（生产补丁历史可跑通 + 不产生 false_pass 之外的意外）
"""
import io
import pytest
import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.evolution import patch_active_reprobe as M  # noqa: E402
from nucleus.evolution.patch_verification_split import (  # noqa: E402
    GRAN_ACTIVE_REPROBE, split_verification)

_SILENT_BEFORE = """try:
    risky()
except Exception:
    pass
"""

_SILENT_AFTER = """try:
    risky()
except Exception as e:
    self._log(LogLevel.ERROR, f"异常: {e}")
"""

_PRINT_BEFORE = """print("hello")
"""

_PRINT_AFTER = """self._log(LogLevel.INFO, "hello")
"""


def _patch(issue, orig, modi, **kw):
    _p = {"id": "t", "issue_type": issue, "original_code": orig,
          "modified_code": modi}
    _p.update(kw)
    return _p


class TestDetectors(unittest.TestCase):
    def test_01_silent_detected_in_original(self):
        self.assertEqual(len(M.detect_silent_exception(_SILENT_BEFORE)), 1)

    def test_02_silent_absent_in_fixed(self):
        self.assertEqual(len(M.detect_silent_exception(_SILENT_AFTER)), 0)

    def test_03_silent_detects_bare_except(self):
        _c = "try:\n    x()\nexcept:\n    pass\n"
        self.assertEqual(len(M.detect_silent_exception(_c)), 1)

    def test_04_logged_except_is_not_silent(self):
        _c = "try:\n    x()\nexcept Exception as e:\n    log.warning(e)\n"
        self.assertEqual(len(M.detect_silent_exception(_c)), 0)

    def test_05_print_detected(self):
        self.assertEqual(len(M.detect_print_instead_of_log(_PRINT_BEFORE)), 1)

    def test_06_print_absent_after_fix(self):
        self.assertEqual(len(M.detect_print_instead_of_log(_PRINT_AFTER)), 0)

    def test_07_no_detector_for_optimization(self):
        self.assertIsNone(M.detector_for("code_optimization"))
        self.assertIsNone(M.detector_for("unknown_type"))
        self.assertIsNone(M.detector_for(None))

    def test_08_indented_snippet_parsable(self):
        _c = "        try:\n            x()\n        except Exception:\n            pass\n"
        self.assertEqual(len(M.detect_silent_exception(_c)), 1)


class TestVerdicts(unittest.TestCase):
    def test_10_true_pass(self):
        _r = M.active_reprobe(_patch("silent_exception", _SILENT_BEFORE, _SILENT_AFTER))
        self.assertEqual(_r[M.F_REPROBE_VERDICT], M.V_TRUE_PASS)
        self.assertEqual(_r[M.F_REPROBE_BASELINE], 1)
        self.assertEqual(_r[M.F_REPROBE_AFTER], 0)

    def test_11_false_pass_unreproducible(self):
        """★核心：修复前**没有**该问题 → 假通过（不判通过）。"""
        _r = M.active_reprobe(_patch("silent_exception", _SILENT_AFTER, _SILENT_AFTER))
        self.assertEqual(_r[M.F_REPROBE_VERDICT], M.V_FALSE_PASS)
        self.assertEqual(_r[M.F_REPROBE_BASELINE], 0)

    def test_12_partial_fix(self):
        """★减少但未清零 → partial_fix（不掩盖部分修复）。"""
        _before = _SILENT_BEFORE + "\n" + _SILENT_BEFORE
        _r = M.active_reprobe(_patch("silent_exception", _before, _SILENT_BEFORE))
        self.assertEqual(_r[M.F_REPROBE_VERDICT], M.V_PARTIAL_FIX)
        self.assertEqual(_r[M.F_REPROBE_BASELINE], 2)
        self.assertEqual(_r[M.F_REPROBE_AFTER], 1)

    def test_13_ineffective(self):
        """未减少 → 修复无效。"""
        _r = M.active_reprobe(_patch("silent_exception", _SILENT_BEFORE, _SILENT_BEFORE))
        self.assertEqual(_r[M.F_REPROBE_VERDICT], M.V_INEFFECTIVE)

    def test_14_verification_failed_on_bad_syntax(self):
        _r = M.active_reprobe(_patch("silent_exception", "def (:", _SILENT_AFTER))
        self.assertEqual(_r[M.F_REPROBE_VERDICT], M.V_VERIFY_FAILED)

    def test_15_not_applicable_for_optimization(self):
        _r = M.active_reprobe(_patch("code_optimization", "a=1", "a=2"))
        self.assertEqual(_r[M.F_REPROBE_VERDICT], M.V_NOT_APPLICABLE)

    def test_16_empty_code_is_failed(self):
        _r = M.active_reprobe(_patch("silent_exception", "", ""))
        self.assertEqual(_r[M.F_REPROBE_VERDICT], M.V_VERIFY_FAILED)

    def test_17_detail_contains_lines(self):
        _r = M.active_reprobe(_patch("silent_exception", _SILENT_BEFORE, _SILENT_AFTER))
        self.assertIn("L", str(_r[M.F_REPROBE_DETAIL]))

    def test_18_version_present(self):
        _r = M.active_reprobe(_patch("silent_exception", _SILENT_BEFORE, _SILENT_AFTER))
        self.assertEqual(_r[M.F_REPROBE_VERSION], M.REPROBE_VERSION)


class TestRates(unittest.TestCase):
    def test_20_strict_excludes_partial(self):
        _ps = [_patch("silent_exception", _SILENT_BEFORE, _SILENT_AFTER),
               _patch("silent_exception", _SILENT_BEFORE, _SILENT_BEFORE)]
        self.assertEqual(M.true_fix_rate(_ps, strict=True), 0.5)
        self.assertEqual(M.true_fix_rate(_ps, strict=False), 0.5)

    def test_21_loose_includes_partial(self):
        _before2 = _SILENT_BEFORE + "\n" + _SILENT_BEFORE
        _ps = [_patch("silent_exception", _before2, _SILENT_BEFORE)]
        self.assertEqual(M.true_fix_rate(_ps, strict=True), 0.0)
        self.assertEqual(M.true_fix_rate(_ps, strict=False), 1.0)

    def test_22_all_not_applicable_returns_none(self):
        """★无可判定样本 → None（不返回 0.0，避免 0/0 型虚假满分）。"""
        _ps = [_patch("code_optimization", "a=1", "a=2")]
        self.assertIsNone(M.true_fix_rate(_ps))

    def test_23_empty_returns_none(self):
        self.assertIsNone(M.true_fix_rate([]))

    def test_24_verdict_counts(self):
        _ps = [_patch("silent_exception", _SILENT_BEFORE, _SILENT_AFTER),
               _patch("code_optimization", "a=1", "a=2")]
        _c = M.verdict_counts(_ps)
        self.assertEqual(_c.get(M.V_TRUE_PASS), 1)
        self.assertEqual(_c.get(M.V_NOT_APPLICABLE), 1)


class TestSplitIntegration(unittest.TestCase):
    """主动复现接入 split_verification（★优先级最高）。"""

    def test_30_true_pass_maps_to_problem_fixed(self):
        _p = _patch("silent_exception", _SILENT_BEFORE, _SILENT_AFTER,
                    baseline_errors=0)
        M.apply_reprobe(_p)
        _r = split_verification(_p)
        # ★即便 baseline_errors=0（日志口径不可判定），复现口径仍给出 True
        self.assertIs(_r["problem_fixed"], True)
        self.assertEqual(_r["verification_granularity"], GRAN_ACTIVE_REPROBE)

    def test_31_false_pass_maps_to_not_fixed(self):
        _p = _patch("silent_exception", _SILENT_AFTER, _SILENT_AFTER)
        M.apply_reprobe(_p)
        _r = split_verification(_p)
        self.assertIs(_r["problem_fixed"], False)
        self.assertIn("无法复现", _r["reason"])

    def test_32_not_applicable_yields_none(self):
        _p = _patch("code_optimization", "a=1", "a=2", baseline_errors=5,
                    post_apply_errors=0)
        M.apply_reprobe(_p)
        _r = split_verification(_p)
        self.assertIsNone(_r["problem_fixed"])

    def test_33_fallback_to_baseline_when_no_reprobe(self):
        """★零回归：无 reprobe 字段 → 回退原日志基线逻辑。"""
        _p = {"id": "x", "baseline_errors": 5, "post_apply_errors": 0,
              "verification": {"passed": True}}
        _r = split_verification(_p)
        self.assertIs(_r["problem_fixed"], True)
        self.assertNotEqual(_r["verification_granularity"], GRAN_ACTIVE_REPROBE)

    def test_34_baseline_zero_still_unjudgeable_without_reprobe(self):
        _p = {"id": "y", "baseline_errors": 0, "post_apply_errors": 0,
              "verification": {"passed": True}}
        _r = split_verification(_p)
        self.assertIsNone(_r["problem_fixed"])


@pytest.mark.production_data
class TestProductionSmoke(unittest.TestCase):
    """生产补丁历史 smoke（只读）。"""

    def test_40_history_runs_and_shape_ok(self):
        _p = os.path.join(_ROOT, "data", "patches", "patch_history.json")
        if not os.path.isfile(_p):
            self.skipTest("生产补丁历史不存在")
        _d = json.load(io.open(_p, encoding="utf-8"))
        _ps = _d.get("patches") if isinstance(_d, dict) else _d
        if not isinstance(_ps, list) or not _ps:
            self.skipTest("无补丁记录")
        _r = M.reprobe_batch(_ps)
        self.assertEqual(_r["total"], len(_ps))
        _allowed = {M.V_TRUE_PASS, M.V_PARTIAL_FIX, M.V_FALSE_PASS,
                    M.V_INEFFECTIVE, M.V_VERIFY_FAILED, M.V_NOT_APPLICABLE}
        self.assertTrue(set(_r["by_verdict"]) <= _allowed,
                        "意外结论: %s" % (_r["by_verdict"],))

    def test_41_no_verdict_missing_fields(self):
        _p = os.path.join(_ROOT, "data", "patches", "patch_history.json")
        if not os.path.isfile(_p):
            self.skipTest("无生产历史")
        _d = json.load(io.open(_p, encoding="utf-8"))
        _ps = _d.get("patches") if isinstance(_d, dict) else _d
        if not isinstance(_ps, list):
            self.skipTest("格式不符")
        for _x in _ps[:20]:
            _r = M.active_reprobe(_x)
            for _k in (M.F_REPROBE_VERDICT, M.F_REPROBE_BASELINE,
                       M.F_REPROBE_AFTER, M.F_REPROBE_DETAIL):
                self.assertIn(_k, _r)


class TestSourceWiring(unittest.TestCase):
    def test_50_split_module_imports_reprobe_verdict(self):
        _s = io.open(os.path.join(
            _ROOT, "nucleus/evolution/patch_verification_split.py"),
            encoding="utf-8").read()
        self.assertIn("reprobe_verdict", _s)

    def test_51_no_bare_except_pass(self):
        import re
        _s = io.open(os.path.join(
            _ROOT, "nucleus/evolution/patch_active_reprobe.py"),
            encoding="utf-8").read()
        self.assertEqual(
            len(re.findall(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)", _s)), 0)


if __name__ == "__main__":
    unittest.main()
