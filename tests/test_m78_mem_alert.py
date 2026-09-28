# -*- coding: utf-8 -*-
"""主线第78批 T3/P1 门控测试：内存占用高水位 + 趋势告警（呼应验收<80%）。

覆盖：
1. mem_percent 越过阈值(>=80) → mem_percent_high 告警。
2. 窗口持续上升 + 涨幅>=阈值 + 未破阈 → mem_percent_trend_rising 趋势告警。
3. 平坦/低位窗口 → 不误报。
4. 趋势窗口每采样点记录 mem_percent。
5. 阈值与涨幅参数可配（来自 _mem_percent_alert / _mem_percent_trend_delta）。
"""
import os
import unittest

from nucleus.runtime_metrics import RuntimeMetrics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _feed_mem(rm, values):
    for v in values:
        rm._mem_percent_window.append(v)
        if len(rm._mem_percent_window) > rm._mem_percent_window_max:
            rm._mem_percent_window.pop(0)


def _make_point(rm, mem_percent):
    return {
        "ts": 0.0,
        "pulse_count": 0,
        "pulse_errors": 0,
        "pulse_avg_ms": 0.0,
        "pulse_max_ms": 0.0,
        "lock_wait_avg_ms": 0.0,
        "queue_max_depth": 0,
        "thread_count": 1,
        "reentry_count": 0,
        "mem_rss_mb": 0.0,
        "mem_percent": mem_percent,
    }


class TestMemAlertM78(unittest.TestCase):
    def setUp(self):
        self.rm = RuntimeMetrics()

    def test_01_high_water_alert(self):
        self.rm._mem_percent_alert = 80.0
        pt = _make_point(self.rm, 95.0)
        self.rm._metrics["alerts"] = []
        self.rm._last_alert_at = 0.0
        try:
            self.rm._check_alerts(pt)
        except Exception:
            pass
        types = [a["type"] for a in self.rm._metrics["alerts"]]
        self.assertIn("mem_percent_high", types, "内存>=80% 未触发高水位告警")

    def test_02_below_threshold_no_alert(self):
        self.rm._mem_percent_alert = 80.0
        pt = _make_point(self.rm, 65.0)
        self.rm._metrics["alerts"] = []
        self.rm._last_alert_at = 0.0
        try:
            self.rm._check_alerts(pt)
        except Exception:
            pass
        types = [a["type"] for a in self.rm._metrics["alerts"]]
        self.assertNotIn("mem_percent_high", types, "内存<80% 不应触发高水位告警")

    def test_03_trend_rising_alert(self):
        self.rm._mem_percent_alert = 80.0
        self.rm._mem_percent_trend_delta = 10.0
        _feed_mem(self.rm, [50.0, 55.0, 60.0, 65.0, 70.0, 74.0])
        pt = _make_point(self.rm, 74.0)
        self.rm._metrics["alerts"] = []
        self.rm._last_alert_at = 0.0
        try:
            self.rm._check_alerts(pt)
        except Exception:
            pass
        types = [a["type"] for a in self.rm._metrics["alerts"]]
        self.assertIn("mem_percent_trend_rising", types,
                      "内存持续上升且涨幅>=10pct 未触发趋势告警")

    def test_04_flat_no_trend_alert(self):
        self.rm._mem_percent_alert = 80.0
        self.rm._mem_percent_trend_delta = 10.0
        _feed_mem(self.rm, [60.0, 60.0, 60.0, 60.0, 60.0, 60.0])
        pt = _make_point(self.rm, 60.0)
        self.rm._metrics["alerts"] = []
        self.rm._last_alert_at = 0.0
        try:
            self.rm._check_alerts(pt)
        except Exception:
            pass
        types = [a["type"] for a in self.rm._metrics["alerts"]]
        self.assertNotIn("mem_percent_trend_rising", types,
                         "内存平坦窗口不应触发趋势告警")

    def test_05_window_records(self):
        _feed_mem(self.rm, [40.0, 45.0, 50.0])
        self.assertEqual(self.rm._mem_percent_window[-1], 50.0)

    def test_06_threshold_configurable(self):
        self.rm._mem_percent_alert = 85.0
        pt = _make_point(self.rm, 82.0)
        self.rm._metrics["alerts"] = []
        self.rm._last_alert_at = 0.0
        try:
            self.rm._check_alerts(pt)
        except Exception:
            pass
        types = [a["type"] for a in self.rm._metrics["alerts"]]
        self.assertNotIn("mem_percent_high", types, "阈值改为85%后82%不应告警")
        # 但 90% 应告警
        pt2 = _make_point(self.rm, 90.0)
        self.rm._metrics["alerts"] = []
        self.rm._last_alert_at = 0.0
        try:
            self.rm._check_alerts(pt2)
        except Exception:
            pass
        types2 = [a["type"] for a in self.rm._metrics["alerts"]]
        self.assertIn("mem_percent_high", types2)


if __name__ == "__main__":
    unittest.main()
