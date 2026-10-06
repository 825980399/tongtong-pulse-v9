# -*- coding: utf-8 -*-
"""163批 刀2 门控单测：生产侧类型白名单拒写 H_P0/HEALTH_P0。

验收口径（施工任务书 刀2）：
- 经 publishers 统一出口(_emit) 发布的 H_P0/HEALTH_P0 异常被剔除，不进总线
  （即不落生产队列 data/reports）；被拒异常打 _isolated/_cleanup_ticket 隔离标注。
- 其他类型不受影响，正常通过。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.reporting.publishers as PUB
import nucleus.reporting.report_envelope as E


class _FakeBus:
    def __init__(self):
        self.published_anomaly_types = []

    def publish(self, envelope):
        self.published_anomaly_types = [getattr(a, "type", None)
                                        for a in (envelope.anomalies or [])]
        return {"consumers": [], "persisted": False}


class TestKnife2M163(unittest.TestCase):
    def test_hp0_rejected_before_publish(self):
        _fb = _FakeBus()
        with patch.object(PUB, "get_report_bus", return_value=_fb):
            _a = E.Anomaly(type="H_P0", severity="P0", description="x",
                           suggested_action="alert")
            PUB.publish_generic("health", "g", content={}, anomalies=[_a])
        self.assertEqual(_fb.published_anomaly_types, [])
        self.assertTrue(getattr(_a, "_isolated", False))
        self.assertEqual(getattr(_a, "_cleanup_ticket", None), "generic")

    def test_health_p0_rejected_before_publish(self):
        _fb = _FakeBus()
        with patch.object(PUB, "get_report_bus", return_value=_fb):
            _a = E.Anomaly(type="HEALTH_P0", severity="P0", description="y")
            PUB.publish_generic("health", "g", content={}, anomalies=[_a])
        self.assertEqual(_fb.published_anomaly_types, [])

    def test_normal_anomaly_passes_through(self):
        _fb = _FakeBus()
        with patch.object(PUB, "get_report_bus", return_value=_fb):
            _a = E.Anomaly(type="SOME_REAL", severity="P0", description="z")
            PUB.publish_generic("health", "g", content={}, anomalies=[_a])
        self.assertEqual(_fb.published_anomaly_types, ["SOME_REAL"])


if __name__ == "__main__":
    unittest.main()
