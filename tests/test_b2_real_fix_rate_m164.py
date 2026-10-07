# -*- coding: utf-8 -*-
"""164批 刀B2（P1）：补丁真修复率回填未改值 —— 门控测试。

背景（任务书 T-补丁真修复率回填未改值-1）：
  SafeEvolutionExecutor.verify_submitted_patches 的延迟复验写回块，对「零验证样本」
  （无 genuine 修复时间戳 fixed_at）用 since=0 重采错误数，恒为 0 会伪造"已修复"，
  把真修复率虚标到 100%（实测 8/54=14.81%），且同秒被反复改写（F-3 20:27:46 现象）；
  同时 _rverdict["baseline"] 硬编码 0 丢弃真实基线，使 problem_fixed 恒 None
  （"修正标记写值但未改 problem_fixed"）。

本次修复：
  1) 新增纯函数 is_genuine_reverify(patch) 判定是否具备 genuine 判定依据（fixed_at 非空）；
  2) 零验证样本 → 标 undecidable + parked（reverify_after 置远未来），绝不写入
     runtime_verified=True / problem_fixed=True；
  3) genuine 样本 → 用真实 baseline_errors 替代硬编码 0，据实算 effectiveness，
     problem_fixed 经 _m84_recompute_split 正确落到 True/False。

本测试覆盖（★宪法 N9 2.8.10 口径一致）：
  - is_genuine_reverify 判定；
  - 零验证样本不虚标（problem_fixed 保持 None，status=runtime_undecidable）；
  - 有 genuine 时间戳 + 真实 baseline → problem_fixed 据实（True/False），
    runtime_verify_result.baseline 不再恒 0；
  - real_fix_rate 不再被零验证样本改写（分母移出 None）；
  - 与 N9 语义拆分口径一致：real_fix_rate = problem_fixed=True / (True+False)。

★mock 隔离全部外部依赖（patch_manager / 错误计数 / 学习枢纽），**不写生产数据**。
"""
import os
import sys
import time
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.reasoning.SafeEvolutionExecutor import (  # noqa: E402
    SafeEvolutionExecutor,
)
from nucleus.evolution.patch_verification_split import (  # noqa: E402
    is_genuine_reverify,
    apply_split,
    real_fix_rate,
    backfill,
)


def _build_executor(pending, error_map):
    """构造一个只依赖内存桩的 SafeEvolutionExecutor，便于隔离测试。"""
    exe = SafeEvolutionExecutor()
    pm = mock.MagicMock()
    pm.get_pending_file.return_value = "pending.json"
    pm.load_json.return_value = pending
    pm._save_json.return_value = True
    pm.rollback_patch.return_value = {"ok": True}
    pm._m105_try_release_low_risk.return_value = False
    exe._patch_manager = pm
    exe._learn_from_verification = mock.MagicMock()
    exe._count_errors_for_location = mock.MagicMock(
        side_effect=lambda f, m, since=0: error_map.get((f, m), 0))
    return exe


def _zero_verify_patch():
    return {
        "id": "p_zero_verify_001",
        "file": "nucleus/foo/Bar.py",
        "method": "broken_method",
        "status": "needs_reverify",
        "needs_runtime_verify": True,
        "reverify_after": 0,        # 已到点（过去）
        "fixed_at": None,           # ★零验证样本：无 genuine 时间戳
        "baseline_errors": 5,
    }


def _genuine_patch(fixed_after=0, baseline=5):
    return {
        "id": "p_genuine_001",
        "file": "nucleus/foo/Baz.py",
        "method": "repaired_method",
        "status": "needs_reverify",
        "needs_runtime_verify": True,
        "reverify_after": 0,
        "fixed_at": 1000.0,         # ★genuine 时间戳
        "baseline_errors": baseline,
    }


class TestIsGenuineReverify(unittest.TestCase):
    def test_empty(self):
        self.assertFalse(is_genuine_reverify({}))

    def test_fixed_at_zero(self):
        self.assertFalse(is_genuine_reverify({"fixed_at": 0}))

    def test_fixed_at_none(self):
        self.assertFalse(is_genuine_reverify({"fixed_at": None}))

    def test_fixed_at_float(self):
        self.assertTrue(is_genuine_reverify({"fixed_at": 1234.5}))

    def test_fixed_at_int(self):
        self.assertTrue(is_genuine_reverify({"fixed_at": 1}))


class TestZeroVerifySampleNotFaked(unittest.TestCase):
    """★核心回归：零验证样本不得被虚标为已修复。"""

    def test_zero_verify_parked_undecidable(self):
        pending = [_zero_verify_patch()]
        with mock.patch(
                "nucleus.mnemosyne.verification_learning_hub."
                "get_verification_learning_hub",
                return_value=mock.MagicMock()):
            exe = _build_executor(pending, {})
            summary = exe.verify_submitted_patches()

        p = pending[0]
        self.assertEqual(p["status"], "runtime_undecidable")
        self.assertFalse(p["runtime_verified"])
        self.assertFalse(p["needs_runtime_verify"])
        # ★最关键：problem_fixed 绝不虚标 True，应为 None（不可判定）
        self.assertIsNone(p.get("problem_fixed"))
        rvr = p["runtime_verify_result"]
        self.assertTrue(rvr["undecidable"])
        self.assertIsNone(rvr["after_fix"])
        self.assertIsNone(rvr["effectiveness"])
        # parked：reverify_after 被推到远未来
        self.assertGreater(p["reverify_after"], time.time() + 300 * 24 * 3600)
        # 未被计入"已验证通过"
        self.assertEqual(summary["verified"], 0)


class TestGenuineSampleRealBaseline(unittest.TestCase):
    """★修复点 3：genuine 样本用真实 baseline，problem_fixed 据实落地。"""

    def test_genuine_fixed(self):
        pending = [_genuine_patch(fixed_after=0, baseline=5)]
        # fixed_at 之后重采错误数 = 0（已修复）
        with mock.patch(
                "nucleus.mnemosyne.verification_learning_hub."
                "get_verification_learning_hub",
                return_value=mock.MagicMock()):
            exe = _build_executor(pending, {("nucleus/foo/Baz.py", "repaired_method"): 0})
            exe.verify_submitted_patches()

        p = pending[0]
        self.assertEqual(p["status"], "runtime_verified")
        self.assertTrue(p["runtime_verified"])
        self.assertTrue(p["problem_fixed"])           # ★修复：正确落到 True
        rvr = p["runtime_verify_result"]
        self.assertEqual(rvr["baseline"], 5)          # ★修复：真实 baseline，非硬编码 0
        self.assertFalse(rvr["undecidable"])
        self.assertEqual(rvr["after_fix"], 0)

    def test_genuine_failed(self):
        # ★N9 口径：problem_fixed = (after < baseline) 即"错误较基线减少"，
        #   "完全归零"是 runtime_verified 的判据。故"未修复"需 after >= baseline。
        pending = [_genuine_patch(baseline=5)]
        # fixed_at 之后重采错误数 = 5（未减少 → 未修复）
        with mock.patch(
                "nucleus.mnemosyne.verification_learning_hub."
                "get_verification_learning_hub",
                return_value=mock.MagicMock()):
            exe = _build_executor(pending, {("nucleus/foo/Baz.py", "repaired_method"): 5})
            exe.verify_submitted_patches()

        p = pending[0]
        self.assertEqual(p["status"], "runtime_failed")
        self.assertTrue(p["needs_repair"])
        self.assertFalse(p["problem_fixed"])          # ★修复：正确落到 False
        rvr = p["runtime_verify_result"]
        self.assertEqual(rvr["baseline"], 5)          # ★真实 baseline
        self.assertEqual(rvr["after_fix"], 5)


class TestRealFixRateNotInflated(unittest.TestCase):
    """★核心回归：real_fix_rate 不被零验证样本改写（分母移出 None）。"""

    def test_zero_verify_excluded_from_denominator(self):
        # 1 个 genuine 未修复（after>=baseline → problem_fixed=False）
        # + 1 个零验证（应为 None）
        pending = [
            _genuine_patch(baseline=5),
            _zero_verify_patch(),
        ]
        with mock.patch(
                "nucleus.mnemosyne.verification_learning_hub."
                "get_verification_learning_hub",
                return_value=mock.MagicMock()):
            exe = _build_executor(
                pending,
                {("nucleus/foo/Baz.py", "repaired_method"): 5})
            exe.verify_submitted_patches()

        rate = real_fix_rate(pending)
        # 可判定样本仅 1 条（genuine 失败，False）；零验证为 None 移出分母。
        # 正确值 = 0/1 = 0.0；若为旧 bug（零验证虚标 True）则会变成 1/2 = 0.5。
        self.assertEqual(rate, 0.0)

    def test_genuine_fixed_with_zero_verify(self):
        pending = [
            _genuine_patch(fixed_after=0, baseline=5),
            _zero_verify_patch(),
        ]
        with mock.patch(
                "nucleus.mnemosyne.verification_learning_hub."
                "get_verification_learning_hub",
                return_value=mock.MagicMock()):
            exe = _build_executor(
                pending,
                {("nucleus/foo/Baz.py", "repaired_method"): 0})
            exe.verify_submitted_patches()

        rate = real_fix_rate(pending)
        # 可判定 1 条（True）；零验证 None 移出 → 1/1 = 1.0。
        self.assertEqual(rate, 1.0)


class TestN9SplitConsistency(unittest.TestCase):
    """★宪法 N9（2.8.10）口径一致：语义拆分拆分"没弄坏"与"修好了"。

    real_fix_rate = problem_fixed=True 条数 / 可判定条数（True+False），
    不可判定（None，含基线为 0）移出分母。
    """

    def test_split_baseline_zero_is_undecidable(self):
        p = _genuine_patch(fixed_after=0, baseline=0)
        p["fixed_at"] = None
        p["status"] = "needs_reverify"
        # baseline=0 应判不可判定 → problem_fixed=None
        apply_split(p)
        self.assertIsNone(p["problem_fixed"])
        self.assertIn("修复前基线为 0", p.get("verification_split_reason", ""))

    def test_real_fix_rate_denominator_excludes_none(self):
        a = {"id": "a", "baseline_errors": 5, "post_apply_errors": 0,
             "runtime_verify_result": {"after_fix": 0, "baseline": 5}}
        b = {"id": "b", "baseline_errors": 5, "post_apply_errors": 5,
             "runtime_verify_result": {"after_fix": 5, "baseline": 5}}
        c = {"id": "c", "baseline_errors": 0, "post_apply_errors": None,
             "runtime_verify_result": {"after_fix": None, "baseline": 0}}
        for _p in (a, b, c):
            apply_split(_p)
        self.assertTrue(a["problem_fixed"])
        self.assertFalse(b["problem_fixed"])
        self.assertIsNone(c["problem_fixed"])

        rate = real_fix_rate([a, b, c])
        # 可判定 = 2（True+False），True = 1 → 0.5；c 的 None 移出分母。
        self.assertEqual(rate, 0.5)

        rep = backfill([a, b, c])
        self.assertEqual(rep["real_fix_rate"], 0.5)
        self.assertEqual(rep["problem_fixed"]["true"], 1)
        self.assertEqual(rep["problem_fixed"]["false"], 1)
        self.assertEqual(rep["problem_fixed"]["none"], 1)
        self.assertEqual(rep["verifiable_count"], 2)


if __name__ == "__main__":
    unittest.main()
