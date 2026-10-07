# -*- coding: utf-8 -*-
"""165批 刀B1 门控单测：真值口径互斥修复（P1 · 宪法 N9 验收前置）

覆盖（任务书 刀B1）：
  * split_verification 的主动复现护栏：
      - true_pass 且 reprobe_baseline_hits>0 → True（复现基线命中）
      - true_pass 但 reprobe_baseline_hits<=0 / 缺失 → None（绝不再落 True）
      - partial_fix → None（不再等同"已修复"），保留 verification_granularity
      - false_pass / ineffective → False（明确未修复，不影响修复率口径）
  * 幂等保护判定 is_split_override_locked
  * 一致性校验 audit_verdict_consistency（暴露 43 True / 11 None 静默不一致）
  * 宪法 N9 strict 口径 = 54/61 ≈ 0.8852 可从生产数据复算
  * 回填归一后同 verdict 不再落两值（audit=0 WARNING）
"""
import os
import sys
import io
import json
import copy
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import nucleus.evolution.patch_verification_split as PVS  # noqa: E402


class TestSplitReprobeGuards(unittest.TestCase):
    def test_true_pass_with_hits_is_fixed(self):
        """★true_pass 且复现基线命中 → problem_fixed=True。"""
        _r = PVS.split_verification(
            {"reprobe_verdict": "true_pass", "reprobe_baseline_hits": 2})
        self.assertIs(_r["problem_fixed"], True)
        self.assertEqual(_r["verification_granularity"], PVS.GRAN_ACTIVE_REPROBE)

    def test_true_pass_zero_hits_is_undecidable(self):
        """★true_pass 但复现基线未命中 → None（绝不落 True）。"""
        _r = PVS.split_verification(
            {"reprobe_verdict": "true_pass", "reprobe_baseline_hits": 0})
        self.assertIsNone(_r["problem_fixed"])

    def test_true_pass_missing_hits_is_undecidable(self):
        """★true_pass 但缺复现基线字段 → None。"""
        _r = PVS.split_verification({"reprobe_verdict": "true_pass"})
        self.assertIsNone(_r["problem_fixed"])

    def test_partial_fix_is_none_but_keeps_granularity(self):
        """★partial_fix 落 None（不再等同已修复），保留粒度供审计。"""
        _r = PVS.split_verification(
            {"reprobe_verdict": "partial_fix", "reprobe_baseline_hits": 5})
        self.assertIsNone(_r["problem_fixed"])
        self.assertEqual(_r["verification_granularity"], PVS.GRAN_REPRODUCTION)

    def test_false_pass_is_not_fixed(self):
        """false_pass → False（明确未修复，属分母不属分子）。"""
        _r = PVS.split_verification(
            {"reprobe_verdict": "false_pass", "reprobe_baseline_hits": 3})
        self.assertIs(_r["problem_fixed"], False)

    def test_ineffective_is_not_fixed(self):
        _r = PVS.split_verification(
            {"reprobe_verdict": "ineffective", "reprobe_baseline_hits": 3})
        self.assertIs(_r["problem_fixed"], False)


class TestIdempotentLock(unittest.TestCase):
    def test_locked_when_corrected(self):
        self.assertTrue(
            PVS.is_split_override_locked({"problem_fixed_corrected": True}))

    def test_unlocked_when_not_corrected(self):
        self.assertFalse(
            PVS.is_split_override_locked({"problem_fixed_corrected": False}))
        self.assertFalse(PVS.is_split_override_locked({}))


class TestAuditConsistency(unittest.TestCase):
    def test_clean_history_has_zero_inconsistency(self):
        _hist = [
            {"problem_fixed": True, "reprobe_verdict": "true_pass",
             "reprobe_baseline_hits": 1},
            {"problem_fixed": None, "reprobe_verdict": "partial_fix",
             "reprobe_baseline_hits": 5},
        ]
        _n, _samples = PVS.audit_verdict_consistency(_hist)
        self.assertEqual(_n, 0)
        self.assertEqual(_samples, [])

    def test_inconsistent_history_counted(self):
        """同 verdict 落两值（partial_fix 却标 True）应被计入不一致。"""
        _hist = [
            {"problem_fixed": True, "reprobe_verdict": "partial_fix",
             "reprobe_baseline_hits": 5},
        ]
        _n, _samples = PVS.audit_verdict_consistency(_hist)
        self.assertEqual(_n, 1)
        self.assertEqual(len(_samples), 1)

    def test_empty_history(self):
        self.assertEqual(PVS.audit_verdict_consistency([]), (0, []))


pytest_production = unittest.skipUnless(
    os.path.isfile(os.path.join(_ROOT, "data", "patches", "patch_history.json")),
    "生产补丁历史不存在")


class TestProductionStrict(unittest.TestCase):
    TARGET = os.path.join(_ROOT, "data", "patches", "patch_history.json")

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(cls.TARGET):
            raise unittest.SkipTest("生产补丁历史不存在")
        with io.open(cls.TARGET, encoding="utf-8") as _f:
            _d = json.load(_f)
        cls.patches = _d["patches"] if isinstance(_d, dict) and "patches" in _d else _d

    def _strict_rate(self, recs):
        """宪法 N9 strict 口径：可靠主动复现（hits>0）样本中，
        true_pass（真正修好）/ (true_pass + partial_fix（未完全修好）)。"""
        _dec = 0
        _fixed = 0
        for _r in recs:
            if not isinstance(_r, dict):
                continue
            _rp = _r.get("reprobe_verdict")
            _hits = PVS._as_int(_r.get("reprobe_baseline_hits"))
            if _rp in ("true_pass", "partial_fix") and _hits is not None and _hits > 0:
                _dec += 1
                if _rp == "true_pass":
                    _fixed += 1
        return (_fixed / float(_dec)) if _dec else None

    def test_strict_rate_recomputable_0_8852(self):
        """★宪法 N9 口径 = strict 54/61 ≈ 0.8852 可从生产数据复算。"""
        _rate = self._strict_rate(self.patches)
        self.assertIsNotNone(_rate)
        # 54 / 61 = 0.8852459...
        self.assertAlmostEqual(_rate, 54.0 / 61.0, delta=1e-3)
        self.assertAlmostEqual(_rate, 0.8852, delta=1e-3)

    def test_backfill_normalization_yields_zero_inconsistency(self):
        """★回填归一后，全部条目 problem_fixed 与 split 重算一致（audit=0）。"""
        _hist = copy.deepcopy(self.patches)
        for _p in _hist:
            if isinstance(_p, dict):
                PVS.apply_split(_p)
        _n, _samples = PVS.audit_verdict_consistency(_hist)
        self.assertEqual(
            _n, 0,
            "回填后仍存在 %d 条 verdict 不一致: %s" % (_n, _samples[:5]))

    def test_field_present(self):
        _with = [p for p in self.patches
                 if isinstance(p, dict) and "problem_fixed" in p]
        self.assertGreater(len(_with), 0, "生产补丁尚未回填")


if __name__ == "__main__":
    unittest.main(verbosity=2)
