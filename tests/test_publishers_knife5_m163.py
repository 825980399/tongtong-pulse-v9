# -*- coding: utf-8 -*-
"""163批 刀5 门控单测：HEALTH_CRITICAL 落盘行携带「问题明细」。

验收口径（施工任务书 刀5）：
- health_anomalies 在 critical 时产出的 HEALTH_CRITICAL 异常，
  description 内含实际 issue 文本（前 5 条），metric_value == 健康分。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.reporting.publishers as PUB


class TestKnife5M163(unittest.TestCase):
    def test_health_critical_carries_issue_detail(self):
        _diag = {
            "overall_health": "critical",
            "health_score": 30.0,
            "issues": ["disk nearly full", "memory leak in X", "timeout surge"],
            "warnings": [],
        }
        _anoms = PUB.health_anomalies(_diag)
        _hc = [a for a in _anoms if a.type == "HEALTH_CRITICAL"]
        self.assertEqual(len(_hc), 1)
        self.assertEqual(_hc[0].metric_value, 30.0)
        # 问题明细进入 description（落盘字段）
        self.assertIn("disk nearly full", _hc[0].description)
        self.assertIn("memory leak in X", _hc[0].description)
        self.assertIn("严重问题 3 项", _hc[0].description)

    def test_health_critical_no_issues_still_ok(self):
        _diag = {"overall_health": "warning", "warnings": ["minor"]}
        _anoms = PUB.health_anomalies(_diag)
        # warning 不产 HEALTH_CRITICAL
        self.assertEqual([a for a in _anoms if a.type == "HEALTH_CRITICAL"], [])


if __name__ == "__main__":
    unittest.main()
