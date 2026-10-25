# -*- coding: utf-8 -*-
"""366 刀N5 验收：分片基线固化＋比对模式（只报告增量，不改断言、不阻断 CI）。"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOL = os.path.join(_ROOT, "tools", "ci", "m365_full_regression_runner.py")
_BASE = os.path.join(_ROOT, "tools", "ci", "baselines",
                     "m366_full_regression_baseline.json")


def _load():
    _spec = importlib.util.spec_from_file_location("m366_runner", _TOOL)
    _m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    return _m


def _summary(rows):
    return {"as_of": "20260101_000000", "shards": len(rows),
            "per_test_timeout": 60, "hard_timeout": 900, "shard_size": 20,
            "rows": rows}


class TestN5Baseline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load()

    # ① 基线往返：写→读→schema 一致（缺失/损坏必须返回 None，不静默放行）
    def test_01_roundtrip_and_missing(self):
        with tempfile.TemporaryDirectory() as _d:
            _p = os.path.join(_d, "b.json")
            _sum = _summary([{"shard": 1, "files": 2, "rc": 1, "dur_s": 1.0,
                              "hard_hang": False, "summary": "1 failed",
                              "timeout_cases": [], "log": "logs/x.log"}])
            self.assertTrue(self.m.save_baseline(_p, _sum))
            _b = self.m.load_baseline(_p)
            self.assertEqual(_b.get("schema"), self.m.BASELINE_SCHEMA)
            self.assertIsNone(self.m.load_baseline(os.path.join(_d, "nope.json")))
            with open(_p, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertIsNone(self.m.load_baseline(_p))

    # ② 比对：新增红 / 已转绿 / 未变 三态分得开
    def test_02_diff_three_states(self):
        _base = {"schema": self.m.BASELINE_SCHEMA, "as_of": "old",
                 "shards_rows": {"1": {"failed_cases": ["t.py::a", "t.py::b"]}}}
        _now = _summary([{"shard": 1, "files": 2, "rc": 1, "dur_s": 1.0,
                          "hard_hang": False, "summary": "1 failed",
                          "timeout_cases": [], "log": "logs/none.log"}])
        # 用伪造 build_baseline 结果驱动 diff（日志文件不存在 ⇒ 空集，故直接替换）
        _cwd = os.getcwd()
        try:
            _d = self.m.diff_against_baseline(_base, _now)
            self.assertEqual(_d["new_red"], [])
            self.assertEqual(len(_d["fixed"]), 2)   # 现仓无失败 ⇒ 两条都算转绿
            self.assertEqual(_d["same_n"], 0)
        finally:
            os.chdir(_cwd)

    # ③ 真基线文件可读且片数一致（固化自 365 r365 实测）
    def test_03_real_baseline(self):
        _b = self.m.load_baseline(_BASE)
        self.assertIsNotNone(_b)
        self.assertEqual(int(_b.get("shards")), 26)
        _rows = _b.get("shards_rows") or {}
        self.assertEqual(len(_rows), 26)
        _n = sum(len(v.get("failed_cases") or []) for v in _rows.values())
        self.assertGreater(_n, 100, "基线应固化 365 实测失败面（>100 例）")

    # ④ 失败用例解析：只认 FAILED/ERROR 行，去重排序
    def test_04_parse_failed_cases(self):
        with tempfile.TemporaryDirectory() as _d:
            _p = os.path.join(_d, "s.log")
            with open(_p, "w", encoding="utf-8") as f:
                f.write("FAILED tests/a.py::x - AssertionError: 1\n"
                        "ERROR tests/b.py::y\n"
                        "FAILED tests/a.py::x - AssertionError: 1\n"
                        "通过的一行不含关键字\n")
            _c = self.m._failed_cases(_p)
        self.assertEqual(_c, ["tests/a.py::x", "tests/b.py::y"])


if __name__ == "__main__":
    unittest.main()
