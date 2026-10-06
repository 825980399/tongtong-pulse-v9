# -*- coding: utf-8 -*-
"""163批 刀4 门控单测：LLM_DATA_QUALITY_LOW 走独立文案，不套用补丁空转文案。

验收口径（施工任务书 刀4）：
- 触发异常为 LLM_DATA_QUALITY_LOW 时，evolution_anomaly_consumer 写入
  alerts.jsonl 的 description 应为「数据质量」独立文案，且不含「补丁验证仍处空转」。
- anomaly_type 字段仍为 LLM_DATA_QUALITY_LOW。
"""
import os
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.reporting.consumers as CONS


class TestKnife4M163(unittest.TestCase):
    def _anom(self, atype, sev):
        return SimpleNamespace(type=atype, severity=sev,
                               description="x", suggested_action="log_only",
                               target="t")

    def test_llm_quality_independent_text(self):
        env = SimpleNamespace(
            report_type="evolution", report_id="r-llm",
            content={"score": 0.5},
            anomalies=[self._anom("LLM_DATA_QUALITY_LOW", CONS.SEV_P1)],
            actions_triggered=[])
        captured = {}

        def fake_append(path, record):
            captured["rec"] = record
            return True

        with patch.object(CONS, "_append_jsonl", side_effect=fake_append), \
                patch.object(CONS, "_log"):
            res = CONS.evolution_anomaly_consumer(env)
        self.assertTrue(res.accepted)
        rec = captured["rec"]
        self.assertEqual(rec["anomaly_type"], "LLM_DATA_QUALITY_LOW")
        self.assertNotIn("补丁验证仍处空转", rec["description"])
        self.assertIn("数据质量", rec["description"])

    def test_patch_fix_rate_keeps_patch_text(self):
        env = SimpleNamespace(
            report_type="evolution", report_id="r-patch",
            content={"real_fix_rate": 0.1},
            anomalies=[],
            actions_triggered=[])
        captured = {}

        def fake_append(path, record):
            captured["rec"] = record
            return True

        with patch.object(CONS, "_append_jsonl", side_effect=fake_append), \
                patch.object(CONS, "_log"):
            res = CONS.evolution_anomaly_consumer(env)
        self.assertTrue(res.accepted)
        rec = captured["rec"]
        self.assertIn("补丁验证仍处空转", rec["description"])


if __name__ == "__main__":
    unittest.main()
