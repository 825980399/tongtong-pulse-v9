# -*- coding: utf-8 -*-
"""163批 刀3 门控单测：自报修复率「空集」与「真 0」可区分。

验收口径（施工任务书 刀3）：
- 空集输入 -> real_fix_rate 返回 None（不再虚报 0.0%）。
- 非空集但全部不可判定 -> 仍返回 float 0.0（不破坏既有口径）。
- 上报总线消费者 evolution_anomaly_consumer：
    * 收到 None 且无 P0/P1 异常 -> 不告警（accepted=False）。
    * 收到真 0.0% -> 告警（accepted=True）。
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.evolution.patch_verification_split as PVS
import nucleus.reporting.consumers as CONS


class TestKnife3M163EmptySet(unittest.TestCase):
    def test_split_empty_returns_none(self):
        self.assertIsNone(PVS.backfill([])["real_fix_rate"])

    def test_fn_empty_returns_none(self):
        self.assertIsNone(PVS.real_fix_rate([]))

    def test_nonempty_all_unverifiable_returns_float(self):
        # 非空但全部不可判定（problem_fixed=None）-> 仍是 float 0.0
        ps = [{"verification": {"passed": False}}]
        r = PVS.backfill(ps)["real_fix_rate"]
        self.assertIsInstance(r, float)
        self.assertEqual(r, 0.0)

    def test_consumer_distinguishes_empty_vs_zero(self):
        # None + 无 P0/P1 异常 -> 不告警
        env_none = SimpleNamespace(
            report_type="evolution", report_id="r-none",
            content={"real_fix_rate": None}, anomalies=[],
            actions_triggered=[])
        with patch.object(CONS, "_append_jsonl"), patch.object(CONS, "_log"):
            res_none = CONS.evolution_anomaly_consumer(env_none)
        self.assertFalse(res_none.accepted)

        # 真 0.0% -> 告警（accepted=True）
        env_zero = SimpleNamespace(
            report_type="evolution", report_id="r-zero",
            content={"real_fix_rate": 0.0}, anomalies=[],
            actions_triggered=[])
        with patch.object(CONS, "_append_jsonl"), patch.object(CONS, "_log"):
            res_zero = CONS.evolution_anomaly_consumer(env_zero)
        self.assertTrue(res_zero.accepted)


if __name__ == "__main__":
    unittest.main()
