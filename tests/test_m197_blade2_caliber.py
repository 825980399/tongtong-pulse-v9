# -*- coding: utf-8 -*-
"""197刀2 隔离单测：口径统一层①③（4 消费点切 v2 + 快照 caliber_version）。

验收：consumers/publishers 消费点经 v2 入口取值；SelfAwarenessProfile 快照标 v1、v2 趋势标 v2。
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from nucleus.reporting import consumers
from nucleus.reporting import publishers
from nucleus.self_awareness import quality_score_v2
from nucleus.self_awareness.SelfAwarenessEngine import SelfAwarenessProfile


class TestM197Blade2(unittest.TestCase):
    def test_consumers_read_switch_to_v2(self):
        with mock.patch(
            "nucleus.diagnostics.FrameworkDiagnostics.get_quality_score_v2",
            return_value={"score": 77.0, "level": "warning"},
        ):
            self.assertEqual(consumers._v2_quality_score(), 77.0)

    def test_publishers_read_switch_to_v2(self):
        with mock.patch(
            "nucleus.diagnostics.FrameworkDiagnostics.get_quality_score_v2",
            return_value={"score": 88.0},
        ):
            _s, _l = publishers._v2_quality()
            self.assertEqual(_s, 88.0)
            self.assertEqual(_l, "attention")  # quality_level(88)=attention

    def test_profile_snapshot_caliber_v1(self):
        _d = SelfAwarenessProfile().to_dict()
        self.assertEqual(_d.get("caliber_version"), "v1")

    def test_record_trend_caliber_v2(self):
        _tmp = tempfile.mkdtemp()
        _f = os.path.join(_tmp, "quality_trend.jsonl")
        with mock.patch.object(quality_score_v2, "trend_path", return_value=_f):
            quality_score_v2.record_trend({"score": 50.0, "dimensions": {}})
        with open(_f, encoding="utf-8") as fh:
            _row = json.loads(fh.readline())
        self.assertEqual(_row.get("caliber_version"), "v2")


if __name__ == "__main__":
    unittest.main()
