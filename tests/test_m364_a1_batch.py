# -*- coding: utf-8 -*-
"""364 批 A1 验收（内存归属采样器：结构 / 斜率 / 启动尖峰剔除 / 类型增量）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.memory_attribution_probe import (  # noqa: E402
    _slope_mb_per_min,
    _type_growth,
    run,
    sample_once,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestA1MemoryProbe(unittest.TestCase):
    def test_01_sample_structure(self):
        s = sample_once(top_n=5)
        for k in ("ts", "rss_mb", "gc_counts", "obj_total", "top_types"):
            self.assertIn(k, s)
        self.assertIsInstance(s["obj_total"], int)
        self.assertGreater(s["obj_total"], 0)
        self.assertLessEqual(len(s["top_types"]), 5)

    def test_02_slope_known_linear(self):
        """60 秒涨 120MB ⇒ 120MB/min。"""
        _s = [{"ts": 0.0, "rss_mb": 100.0}, {"ts": 60.0, "rss_mb": 220.0}]
        self.assertAlmostEqual(_slope_mb_per_min(_s), 120.0, places=2)

    def test_03_slope_needs_two_points(self):
        self.assertEqual(_slope_mb_per_min([]), 0.0)
        self.assertEqual(_slope_mb_per_min([{"ts": 0.0, "rss_mb": 100.0}]), 0.0,
                         "单样本不得给伪斜率")

    def test_04_type_growth(self):
        _s = [{"all_types": {"dict": 10, "list": 5}},
              {"all_types": {"dict": 10, "list": 25, "Foo": 3}}]
        g = dict(_type_growth(_s))
        self.assertEqual(g.get("list"), 20)
        self.assertEqual(g.get("Foo"), 3)
        self.assertNotIn("dict", g, "零增量不该进榜")

    def test_05_warmup_excluded(self):
        d = run(duration=2.0, interval=0.5, warmup=1.0, top_n=3)
        self.assertGreaterEqual(d["sample_count"], 2)
        self.assertLess(d["steady_sample_count"], d["sample_count"],
                        "warmup 内样本必须被剔除，不进后段斜率")
        warm = [s for s in d["samples"] if s["in_warmup"]]
        self.assertTrue(warm, "至少应有一个 warmup 样本")
        self.assertIn(d["verdict"], ("stable", "leak_suspect"))

    def test_06_threshold_from_config(self):
        d = run(duration=1.0, interval=0.5, warmup=0.5, top_n=3)
        self.assertIsInstance(d["threshold_mb_per_min"], float)
        self.assertGreater(d["threshold_mb_per_min"], 0.0)

    def test_07_cli_atomic_output(self):
        _d = tempfile.mkdtemp()
        _out = os.path.join(_d, "probe.json")
        _r = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "tools", "memory_attribution_probe.py"),
             "--duration", "1", "--interval", "0.5", "--warmup", "0.5", "--out", _out],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(_r.returncode, 0, _r.stderr[-300:])
        doc = json.load(open(_out, encoding="utf-8"))
        for k in ("slope_all_mb_per_min", "slope_steady_mb_per_min", "verdict", "pid"):
            self.assertIn(k, doc)
        self.assertEqual(doc["verdict"], "stable" if doc["slope_steady_mb_per_min"]
                         <= doc["threshold_mb_per_min"] else "leak_suspect")


if __name__ == "__main__":
    unittest.main(verbosity=2)
