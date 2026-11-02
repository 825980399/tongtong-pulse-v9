# -*- coding: utf-8 -*-
"""427 刀4：growth_mb_per_min 的窗口 / 相位 / 启动基线。

守护三件事：
  1) ramp 期的高斜率**不许**被当成泄漏信号；
  2) 稳态真泄漏必须报 signal，且 `steady_growth_mb_per_min` 与之一致；
  3) `get_memory_growth_rate()` 的标量返回**逐位不变**（既有调用方契约）。
"""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.runtime_metrics import RuntimeMetrics

SAMPLE = 5.0     # 与生产采样间隔一致


def _mk(uptime_sec: float, rss: list) -> RuntimeMetrics:
    m = RuntimeMetrics()
    now = time.time()
    m._created_at = now - uptime_sec
    n = len(rss)
    m._history.clear()
    for i, _r in enumerate(rss):
        m._history.append({"ts": m._created_at + (uptime_sec - (n - 1 - i) * SAMPLE),
                           "mem_rss_mb": float(_r), "mem_percent": 40.0})
    return m


def _naive_rate(m: RuntimeMetrics, n: int = 60) -> float:
    """独立复算（基准取自输入的 ts/rss，不取被测函数输出，铁律 30）。"""
    pts = [p for p in list(m._history) if p.get("mem_rss_mb")][-n:]
    if len(pts) < 2:
        return 0.0
    span = float(pts[-1]["ts"]) - float(pts[0]["ts"])
    return 0.0 if span <= 0 else (
        float(pts[-1]["mem_rss_mb"]) - float(pts[0]["mem_rss_mb"])) / (span / 60.0)


class TestGrowthReport(unittest.TestCase):
    def test_01_ramp_phase_is_not_leak_signal(self):
        """启动 3 分钟、RSS 台阶式上涨（180MB/min）⇒ phase=ramp 且不报泄漏。

        这正是任务书点名的假读数场景（生产曾报 1815MB/min）。
        """
        m = _mk(3 * 60, [500 + i * 15 for i in range(37)])
        r = m.get_memory_growth_report(window_sec=300.0)
        self.assertEqual(r["phase"], "ramp", "启动 10 分钟内必须标 ramp")
        self.assertGreater(r["growth_mb_per_min"], 100.0, "ramp 期斜率本来就大，要如实给出")
        self.assertFalse(r["is_leak_signal"], "ramp 期的高斜率不是泄漏信号")
        self.assertIsNone(r["rss_at_10min"], "还没到 10 分钟，基线应为 None")
        self.assertGreater(r["window_sec"], 0.0, "window_sec 必须给出真实窗口")

    def test_02_steady_leak_is_flagged(self):
        """稳态 +20MB/min ⇒ is_leak_signal=True，且两条斜率口径接近。"""
        m = _mk(30 * 60, [3000 + i * 1.667 for i in range(361)])
        r = m.get_memory_growth_report(window_sec=300.0)
        self.assertEqual(r["phase"], "steady")
        self.assertTrue(r["is_leak_signal"], "稳态持续上涨就是泄漏信号，必须报")
        self.assertAlmostEqual(r["growth_mb_per_min"], 20.0, delta=0.5)
        self.assertAlmostEqual(r["steady_growth_mb_per_min"], 20.0, delta=0.5,
                               msg="相对 10 分钟基线的斜率应接近窗口斜率")
        self.assertIsNotNone(r["rss_at_10min"])

    def test_03_steady_flat_is_not_leak_signal(self):
        """稳态平台（平台化后微降）⇒ 不报泄漏。"""
        s = [float(3000 + min(i, 120)) for i in range(361)]
        s[-1] = s[-2] - 1
        m = _mk(30 * 60, s)
        r = m.get_memory_growth_report(window_sec=300.0)
        self.assertEqual(r["phase"], "steady")
        self.assertFalse(r["is_leak_signal"], "平台化后的微降不该报泄漏")
        self.assertLess(abs(r["growth_mb_per_min"]), 1.0)

    def test_04_scalar_rate_is_bit_identical(self):
        """`get_memory_growth_rate(60)` 与独立复算逐位一致（零回归）。"""
        for _name, _m in (("ramp", _mk(3 * 60, [500 + i * 15 for i in range(37)])),
                          ("steady", _mk(30 * 60, [3000 + i * 1.667 for i in range(361)]))):
            self.assertAlmostEqual(_m.get_memory_growth_rate(60), _naive_rate(_m),
                                   places=9, msg=f"{_name} 场景的标量口径变了")

    def test_05_cold_start(self):
        """样本不足 ⇒ phase=cold、growth=0，不抛异常。"""
        r = _mk(10, [700.0]).get_memory_growth_report()
        self.assertEqual(r["phase"], "cold")
        self.assertEqual(r["growth_mb_per_min"], 0.0)


if __name__ == "__main__":
    unittest.main()
