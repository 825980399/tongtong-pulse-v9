# -*- coding: utf-8 -*-
"""365 刀F1 验收：增量检测器基线刷新留痕（只观测，不改行为）。

覆盖：刷新记 before/after／首轮 vs 稳态双列＋skew／环形上限截断／读数结构。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.self_inspector as si  # noqa: E402


def _mk():
    return si.SelfInspector.__new__(si.SelfInspector)


class TestF1BaselineTrace(unittest.TestCase):
    def setUp(self):
        si._M365_BASELINE_TRACE.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "god_baseline.json")

    def tearDown(self):
        self._tmp.cleanup()
        si._M365_BASELINE_TRACE.clear()

    def test_01_records_before_after(self):
        s = _mk()
        s._si_write_god_baseline(self.path, {"a.py": {}, "b.py": {}})
        s._si_write_god_baseline(self.path, {"a.py": {}})
        rep = si.m365_baseline_refresh_report()
        self.assertEqual(rep["total_refresh"], 2)
        self.assertEqual(rep["recent"][0]["before"], 0)
        self.assertEqual(rep["recent"][0]["after"], 2)
        self.assertEqual(rep["recent"][1]["before"], 2)
        self.assertEqual(rep["recent"][1]["after"], 1)

    def test_02_first_round_vs_steady(self):
        s = _mk()
        for _ in range(3):
            s._si_write_god_baseline(self.path, {"a.py": {}, "b.py": {}})
        rep = si.m365_baseline_refresh_report()
        _d = os.path.basename(self.path)
        self.assertIn(_d, rep["detectors"])
        self.assertEqual(rep["first_round"][_d], 2)
        self.assertEqual(rep["steady"][_d], 2)
        self.assertFalse(rep["skew"][_d])

    def test_03_ring_cap(self):
        s = _mk()
        _old = si._M365_BASELINE_TRACE_MAX
        try:
            si._M365_BASELINE_TRACE_MAX = 3
            for i in range(6):
                s._si_write_god_baseline(self.path, {"x%d.py" % i: {}})
        finally:
            si._M365_BASELINE_TRACE_MAX = _old
        self.assertLessEqual(len(si._M365_BASELINE_TRACE), 3)

    def test_04_baseline_file_still_written(self):
        """留痕不得影响写盘本身（写进去的内容必须仍是原 dict）。"""
        s = _mk()
        s._si_write_god_baseline(self.path, {"k.py": {"loc": 3}})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"k.py": {"loc": 3}})

    def test_05_report_schema(self):
        rep = si.m365_baseline_refresh_report()
        for _k in ("schema", "total_refresh", "detectors", "first_round",
                   "steady", "skew", "recent"):
            self.assertIn(_k, rep)


if __name__ == "__main__":
    unittest.main()
