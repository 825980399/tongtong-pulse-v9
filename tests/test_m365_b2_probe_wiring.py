# -*- coding: utf-8 -*-
"""365 刀B2 验收：内存归属同进程采样接线（探针复用／单实例／停止／命令面在位）。"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus import m365_mem_attribution as ma  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeProbe:
    def __init__(self):
        self.n = 0

    def sample_once(self, top_n=12):
        self.n += 1
        return {"ts": time.time(), "rss_mb": 100.0 + self.n,
                "obj_total": 1000 + self.n, "gc_counts": [1, 2, 3],
                "top_types": []}


class TestB2MemProbe(unittest.TestCase):
    def setUp(self):
        ma._PROBE["mod"] = _FakeProbe()
        ma._STATE.update({"thread": None, "stop": False, "finished": False,
                          "samples": 0, "error": None, "started_at": None})

    def tearDown(self):
        ma._STATE["stop"] = True
        t = ma._STATE.get("thread")
        if t is not None:
            t.join(timeout=3)

    def _out(self):
        return os.path.join(_ROOT, "tmp", "m365_b2_probe_test.jsonl")

    def test_01_probe_loaded_from_tools(self):
        """真探针可按路径加载（tools 不是包，不能 import tools.xxx）。"""
        ma._PROBE["mod"] = None
        _m = ma._load_probe()
        self.assertIsNotNone(_m)
        for _n in ("sample_once", "run", "_slope_mb_per_min"):
            self.assertTrue(hasattr(_m, _n), _n)

    def test_02_start_and_sample(self):
        r = ma.start(duration=0.2, interval=0.05, warmup=0.0, top_n=3,
                     note="unit", out=self._out())
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("pid"), os.getpid(), "必须同进程采样")
        time.sleep(0.5)
        st = ma.status()
        self.assertGreaterEqual(st.get("samples"), 1, st)
        self.assertIsNotNone(st.get("last"))

    def test_03_single_instance(self):
        ma.start(duration=1.0, interval=0.05, warmup=0.0, out=self._out())
        r2 = ma.start(duration=1.0, interval=0.05, warmup=0.0, out=self._out())
        self.assertFalse(r2.get("ok"))
        self.assertEqual(r2.get("why"), "already_running")

    def test_04_stop(self):
        ma.start(duration=5.0, interval=0.05, warmup=0.0, out=self._out())
        time.sleep(0.1)
        r = ma.stop()
        self.assertTrue(r.get("ok"), r)
        time.sleep(0.3)
        self.assertFalse(ma.status().get("running"))

    def test_05_command_wired(self):
        with open(os.path.join(_ROOT, "functions", "chat", "chat_service.py"),
                  encoding="utf-8") as f:
            _s = f.read()
        self.assertIn('cmd.startswith(("memprobe"', _s)
        self.assertIn("_m365_mem_probe", _s)


if __name__ == "__main__":
    unittest.main()
