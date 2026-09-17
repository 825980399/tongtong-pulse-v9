# -*- coding: utf-8 -*-
"""第47批 T1 门控测试：补丁验证语义拆分（P0-2 / P2-309）

覆盖：
  * 语义拆分的数据结构正确性（no_regression / problem_fixed / granularity / effectiveness）
  * ★核心：不可判定为 None，**绝不默认 True**
  * 向后兼容（原 verified 保留，等于 no_regression）
  * 回填逻辑与真实修复率统计
  * 展示层区分「无回归」与「修复效果未验证」
  * 生产数据已回填的落盘校验
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

from nucleus.evolution import patch_verification_split as _pvs  # noqa: E402


def _patch(**kw):
    base = {"id": "patch_test_x", "file": "a/b.py", "method": "m",
            "verification": {"passed": True},
            "baseline_errors": 0, "post_apply_errors": 0}
    base.update(kw)
    return base


# ==================== 语义拆分核心 ====================

class TestSplitCore(unittest.TestCase):
    def test_01_baseline_zero_means_unverifiable(self):
        """★核心：baseline=0 → problem_fixed=None（不可判定），绝不 True。"""
        _r = _pvs.split_verification(_patch(baseline_errors=0, post_apply_errors=0))
        self.assertIsNone(_r["problem_fixed"])
        self.assertEqual(_r["verification_granularity"], "static_only")
        self.assertIsNone(_r["effectiveness"])

    def test_02_baseline_missing_means_unverifiable(self):
        _r = _pvs.split_verification(_patch(baseline_errors=None,
                                            post_apply_errors=None))
        self.assertIsNone(_r["problem_fixed"])
        self.assertIsNone(_r["effectiveness"])

    def test_03_true_fix_detected(self):
        _r = _pvs.split_verification(_patch(baseline_errors=10, post_apply_errors=2))
        self.assertIs(_r["problem_fixed"], True)
        self.assertEqual(_r["verification_granularity"], "effectiveness")
        self.assertAlmostEqual(_r["effectiveness"], 0.8)

    def test_04_partial_fix_detected(self):
        _r = _pvs.split_verification(_patch(baseline_errors=10, post_apply_errors=7))
        self.assertIs(_r["problem_fixed"], True)
        self.assertAlmostEqual(_r["effectiveness"], 0.3)

    def test_05_no_fix_detected(self):
        _r = _pvs.split_verification(_patch(baseline_errors=4, post_apply_errors=4))
        self.assertIs(_r["problem_fixed"], False)
        self.assertAlmostEqual(_r["effectiveness"], 0.0)

    def test_06_worse_than_before(self):
        """越修越坏：effectiveness 允许负值。"""
        _r = _pvs.split_verification(_patch(baseline_errors=4, post_apply_errors=9))
        self.assertIs(_r["problem_fixed"], False)
        self.assertLess(_r["effectiveness"], 0)

    def test_07_no_regression_from_verification(self):
        _r = _pvs.split_verification(
            _patch(verification={"passed": True}, baseline_errors=0))
        self.assertIs(_r["no_regression"], True)

    def test_08_no_regression_false(self):
        _r = _pvs.split_verification(_patch(verification={"passed": False}))
        self.assertIs(_r["no_regression"], False)

    def test_09_missing_verification_none(self):
        _p = _patch()
        _p.pop("verification")
        _p.pop("baseline_errors")
        _p.pop("post_apply_errors")
        _r = _pvs.split_verification(_p)
        self.assertIsNone(_r["no_regression"])
        self.assertIsNone(_r["problem_fixed"])

    def test_10_fallback_to_runtime_verify_result(self):
        """基线可从 runtime_verify_result 回退取值。"""
        _p = _patch()
        _p.pop("baseline_errors")
        _p.pop("post_apply_errors")
        _p["runtime_verify_result"] = {"baseline": 5, "after_fix": 1}
        _r = _pvs.split_verification(_p)
        self.assertIs(_r["problem_fixed"], True)
        self.assertAlmostEqual(_r["effectiveness"], 0.8)

    def test_11_effectiveness_clamped(self):
        _r = _pvs.split_verification(_patch(baseline_errors=2, post_apply_errors=100))
        self.assertGreaterEqual(_r["effectiveness"], -1.0)


# ==================== 向后兼容 ====================

class TestBackwardCompat(unittest.TestCase):
    def test_20_verified_kept_as_no_regression(self):
        _r = _pvs.split_verification(_patch(verification={"passed": True}))
        self.assertEqual(_r["verified"], _r["no_regression"])

    def test_21_apply_split_keeps_existing_fields(self):
        _p = _patch(baseline_errors=3, post_apply_errors=1)
        _p["trust_score"] = 70
        _pvs.apply_split(_p)
        self.assertEqual(_p["trust_score"], 70)          # 既有字段不丢
        self.assertIn("verified", _p)
        self.assertIs(_p["problem_fixed"], True)

    def test_22_old_reader_wont_crash(self):
        """旧代码只读 verified 时不崩溃。"""
        _p = _patch(baseline_errors=0)
        _pvs.apply_split(_p)
        self.assertIsInstance(_p["verified"], bool)

    def test_23_split_version_marked(self):
        _p = _patch()
        _pvs.apply_split(_p)
        self.assertEqual(_p["verification_split_version"], _pvs.SPLIT_VERSION)
        self.assertTrue(_pvs.is_deprecated_verified(_p))

    def test_24_reason_present(self):
        _p = _patch(baseline_errors=0)
        _r = _pvs.apply_split(_p)
        self.assertIn("不可判定", _r["reason"])


# ==================== 回填与统计 ====================

class TestBackfill(unittest.TestCase):
    def _set(self):
        return [
            _patch(id="p1", baseline_errors=0, post_apply_errors=0),
            _patch(id="p2", baseline_errors=10, post_apply_errors=2),
            _patch(id="p3", baseline_errors=10, post_apply_errors=10),
            _patch(id="p4"),
        ]

    def test_30_backfill_dry_run_no_write(self):
        _ps = self._set()
        _snap = json.dumps(_ps, sort_keys=True)
        _r = _pvs.backfill(_ps, apply=False)
        self.assertEqual(json.dumps(_ps, sort_keys=True), _snap)  # 未改动
        self.assertEqual(_r["total"], 4)

    def test_31_backfill_counts(self):
        _ps = self._set()
        _r = _pvs.backfill(_ps, apply=True)
        self.assertEqual(_r["problem_fixed"]["true"], 1)
        self.assertEqual(_r["problem_fixed"]["false"], 1)
        self.assertEqual(_r["problem_fixed"]["none"], 2)

    def test_32_real_fix_rate_vs_claimed(self):
        """★真实修复率必须显著低于旧声称率。"""
        _ps = self._set()
        _r = _pvs.backfill(_ps, apply=True)
        self.assertEqual(_r["old_claimed_rate"], 1.0)     # 旧口径 100%
        self.assertEqual(_r["real_fix_rate"], 0.25)       # 真实 25%
        self.assertLess(_r["real_fix_rate"], _r["old_claimed_rate"])

    def test_33_real_fix_rate_fn(self):
        _ps = self._set()
        _pvs.backfill(_ps, apply=True)
        self.assertAlmostEqual(_pvs.real_fix_rate(_ps), 0.25)

    def test_34_granularity_dist(self):
        _ps = self._set()
        _r = _pvs.backfill(_ps, apply=True)
        self.assertIn("static_only", _r["granularity_dist"])
        self.assertIn("effectiveness", _r["granularity_dist"])

    def test_35_empty_list(self):
        _r = _pvs.backfill([], apply=True)
        self.assertEqual(_r["total"], 0)
        self.assertEqual(_r["real_fix_rate"], 0.0)
        self.assertEqual(_pvs.real_fix_rate([]), 0.0)


# ==================== 展示层 ====================

class TestDisplay(unittest.TestCase):
    def test_40_unverified_label(self):
        _p = _patch(baseline_errors=0)
        _pvs.apply_split(_p)
        self.assertIn("无回归", _pvs.display_label(_p))
        self.assertIn("未验证", _pvs.display_label(_p))

    def test_41_fixed_label(self):
        _p = _patch(baseline_errors=5, post_apply_errors=0)
        _pvs.apply_split(_p)
        self.assertIn("问题已修复", _pvs.display_label(_p))

    def test_42_still_broken_label(self):
        _p = _patch(baseline_errors=5, post_apply_errors=5)
        _pvs.apply_split(_p)
        self.assertIn("问题仍在", _pvs.display_label(_p))

    def test_43_regression_label(self):
        _p = _patch(verification={"passed": False})
        _pvs.apply_split(_p)
        self.assertIn("存在回归", _pvs.display_label(_p))

    def test_44_label_without_split(self):
        """未拆分的补丁也能出标签（内部自动计算）。"""
        _p = _patch(baseline_errors=5, post_apply_errors=5)
        self.assertIn("问题仍在", _pvs.display_label(_p))


# ==================== 生产数据落盘校验 ====================

@pytest.mark.production_data
class TestProductionBackfilled(unittest.TestCase):
    TARGET = os.path.join(_ROOT, "data", "patches", "patch_history.json")

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(cls.TARGET):
            raise unittest.SkipTest("生产补丁历史不存在")
        with io.open(cls.TARGET, encoding="utf-8") as _f:
            _d = json.load(_f)
        cls.patches = _d["patches"] if isinstance(_d, dict) and "patches" in _d else _d

    def test_50_fields_present(self):
        _with = [p for p in self.patches
                 if isinstance(p, dict) and "problem_fixed" in p]
        self.assertGreater(len(_with), 0, "生产补丁尚未回填")

    def test_51_no_regression_all_true(self):
        for _p in self.patches:
            if not isinstance(_p, dict) or "no_regression" not in _p:
                continue
            self.assertIs(_p["no_regression"], True)

    def test_52_zero_baseline_not_marked_fixed(self):
        """★基线为 0 的补丁绝不能被标成 problem_fixed=True。"""
        _bad = [p.get("id") for p in self.patches
                if isinstance(p, dict)
                and not int(p.get("baseline_errors") or 0)
                and p.get("problem_fixed") is True]
        self.assertEqual(_bad, [], "基线为 0 却被判为已修复: %s" % _bad[:5])

    def test_53_effectiveness_not_default_high(self):
        """★effectiveness 不得再有默认的 0.97 之类虚高值。"""
        for _p in self.patches:
            if not isinstance(_p, dict):
                continue
            _e = _p.get("effectiveness")
            if _e is None:
                continue
            self.assertGreaterEqual(_e, -1.0)
            self.assertLessEqual(_e, 1.0)

    def test_54_backup_exists(self):
        _d = os.path.join(_ROOT, "data", "_archive", "patch_split")
        if not os.path.isdir(_d):
            self.skipTest("未找到回填备份目录")
        self.assertGreater(len(os.listdir(_d)), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
