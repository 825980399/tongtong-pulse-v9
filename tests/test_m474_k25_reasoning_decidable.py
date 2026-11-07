# -*- coding: utf-8 -*-
"""474 刀25：观测桥只把「推理记录」计入自给率分母。

背景（20:29 首拍实证）：hub 114 条记录里 0 条含 `local_result.solved`，
全是 code_review / local_analysis_* / exploration_audit 等通用记录，
旧判据把它们全算成「依赖外部的推理尝试」⇒ 自给率 0.0 / 外部依赖 0.9714。

判据纪律：
  · 基准来自真实记录形态（写入点 `PulseInnerWorld._detect_symbolic_reason` 的
    `local_result.solved` 契约），不拿被测函数输出反推（铁律 30）。
  · 反证用例：全通用记录时，新逻辑必须给出「无数据(None)」，
    而不能像旧逻辑那样给出 0.0（铁律 35 判别力）。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nucleus.autonomy import CapabilityKernel
from nucleus.autonomy.reasoning import DOMAIN_REASONING, ReasoningObserver, classify_hub_entry


def _reasoning(eid, solved=True, api_better=False, needs_verify=False,
               task="symbolic_compare"):
    """与 `_detect_symbolic_reason` 写入契约一致（独立基准）。"""
    return {"id": eid, "task_type": task, "organ": "inner_world",
            "local_result": {"solved": solved, "answer": "x", "steps_count": 2},
            "api_better": api_better, "needs_verification": needs_verify}


def _generic(eid, task="code_review", needs_verify=True, api_better=False):
    """与磁盘 hub 里真实通用记录同构（keys: issue_type/file/line/method）。"""
    return {"id": eid, "task_type": task, "organ": "self_inspector",
            "local_result": {"issue_type": "print_debug", "file": "organs/x.py",
                             "line": 598, "method": "_print_report_console"},
            "api_better": api_better, "needs_verification": needs_verify}


class _Kernel:
    """不落盘的最小 kernel 替身，便于查台账记录。"""

    def __init__(self):
        self.rows = []
        self.external = 0
        self.runs = 0

    def note_experience(self, rec, domain=None):
        self.rows.append(dict(rec))
        self.runs += 1
        if rec.get("external_used"):
            self.external += 1
        return {}

    def snapshot(self):
        return {"schema": "fake", "runs": self.runs}


def _observer():
    return ReasoningObserver(_Kernel())


class TestDecidableClassification(unittest.TestCase):
    def test_01_real_generic_form_is_undecidable(self):
        _r = classify_hub_entry(_generic("g1"))
        self.assertEqual("other", _r["record_kind"])
        self.assertFalse(_r["decidable"])
        self.assertFalse(_r["external_used"])
        self.assertFalse(_r["solved_locally"])

    def test_02_reasoning_contract_is_decidable(self):
        _r = classify_hub_entry(_reasoning("s1", solved=True))
        self.assertEqual("reasoning", _r["record_kind"])
        self.assertTrue(_r["decidable"])
        self.assertTrue(_r["solved_locally"])

    def test_03_symbolic_task_type_counts_even_without_solved_key(self):
        _e = _reasoning("s2")
        del _e["local_result"]["solved"]
        self.assertTrue(classify_hub_entry(_e)["decidable"])

    def test_04_each_real_task_type_is_undecidable(self):
        """磁盘里出现过的 9 种 task_type 逐个体检（真实字面量）。"""
        for _t in ("code_review", "local_analysis_success",
                   "local_analysis_insufficient", "exploration_audit",
                   "growth_hypothesis", "output_relevance",
                   "output_correction", "digestion_quality",
                   "runtime_fix_verification"):
            with self.subTest(task_type=_t):
                self.assertFalse(
                    classify_hub_entry(_generic("x", task=_t))["decidable"])

    def test_05_pure_function_never_mutates(self):
        _e = _generic("g2")
        _before = copy.deepcopy(_e)
        classify_hub_entry(_e)
        self.assertEqual(_before, _e)

    def test_06_bad_input_stays_safe(self):
        self.assertEqual("other", classify_hub_entry(None)["record_kind"])
        self.assertEqual("unknown", classify_hub_entry({"local_result": []})["organ"])


class TestDecidableMetrics(unittest.TestCase):
    def test_07_mixed_batch_metrics_use_only_reasoning_records(self):
        """114 通用 + 3 推理（2 解出 / 1 未解）⇒ 分母只算 3。"""
        _rows = [_generic(f"g{i}") for i in range(114)]
        _rows += [_reasoning("s1", solved=True),
                  _reasoning("s2", solved=True),
                  _reasoning("s3", solved=False, needs_verify=True)]
        _s = _observer().observe(_rows)
        self.assertEqual(3, _s["total"])
        self.assertEqual(114, _s["non_reasoning_total"])
        self.assertEqual(2, _s["solved_locally"])
        self.assertEqual(1, _s["external"])
        self.assertAlmostEqual(2 / 3, _s["reasoning_self_sufficiency_rate"], places=4)
        self.assertAlmostEqual(1 / 3, _s["external_dependency_rate"], places=4)

    def test_08_counterproof_all_generic_is_none_not_zero(self):
        """★反证：旧逻辑会给 0.0（假「完全依赖外部」），新逻辑必须给 None。"""
        _s = _observer().observe([_generic(f"g{i}") for i in range(35)])
        self.assertEqual(0, _s["total"])
        self.assertEqual(35, _s["non_reasoning_total"])
        self.assertIsNone(_s["reasoning_self_sufficiency_rate"])
        self.assertIsNone(_s["external_dependency_rate"])
        self.assertNotEqual(0.0, _s["reasoning_self_sufficiency_rate"])

    def test_09_generic_records_still_land_in_ledger(self):
        _k = _Kernel()
        _o = ReasoningObserver(_k)
        _o.observe([_generic("g1"), _reasoning("s1")])
        self.assertEqual(2, len(_k.rows), "留痕不能丢观测")
        self.assertTrue(any(not _r["decidable"] for _r in _k.rows))
        self.assertEqual(0, _k.external, "通用记录不得污染外部依赖计数")

    def test_10_by_task_type_excludes_generic(self):
        _s = _observer().observe([_generic("g1", task="code_review"),
                                  _reasoning("s1", task="symbolic_compare")])
        self.assertEqual(["symbolic_compare"], list(_s["by_task_type"]))

    def test_11_dedup_still_covers_generic_records(self):
        _o = _observer()
        self.assertEqual(1, _o.observe([_generic("g1")])["new"])
        self.assertEqual(0, _o.observe([_generic("g1")])["new"])
        self.assertEqual(1, _o.snapshot()["non_reasoning_total"])

    def test_12_kernel_domain_unchanged(self):
        """第二期的「同一 kernel 服务多域」契约不该被本刀破坏。"""
        _k = CapabilityKernel(ledger_enabled=False)
        _s = ReasoningObserver(_k).observe([_reasoning("s1")])
        self.assertEqual(DOMAIN_REASONING, _s["domain"])
        self.assertIn("runs", _s["kernel"])

    def test_13_disk_hub_shape_matches_fixture(self):
        """只读实证：磁盘 hub 里通用记录确实没有 solved 键（若文件可读）。"""
        _p = os.path.join(_ROOT, "data", "verification_learning.json")
        if not os.path.isfile(_p):
            self.skipTest("hub 文件不存在")
        with open(_p, encoding="utf-8") as _fh:
            _d = json.load(_fh)
        _rows = _d.get("entries", []) if isinstance(_d, dict) else _d
        _generic_rows = [_r for _r in _rows if isinstance(_r, dict)
                         and isinstance(_r.get("local_result"), dict)
                         and "solved" not in _r["local_result"]]
        self.assertTrue(_generic_rows, "磁盘 hub 应含通用记录")
        for _r in _generic_rows[:20]:
            self.assertFalse(classify_hub_entry(_r)["decidable"])


if __name__ == "__main__":
    unittest.main()
