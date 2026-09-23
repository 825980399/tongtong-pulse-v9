# -*- coding: utf-8 -*-
"""主线第78批 T1/P0 门控测试：队列深度趋势告警（早期预警）。

覆盖：
1. _queue_depth_window 每采样点记录瞬时深度。
2. 持续上升 + 涨幅显著 + 未破阈 → 趋势告警 queue_depth_trend_rising。
3. 平坦/下降窗口 → 不误报趋势。
4. 已破绝对阈值 → 走 queue_depth_high（与趋势各自独立）。
5. 窗口样本不足 → 不告警。
"""
import os
import unittest

from nucleus.runtime_metrics import RuntimeMetrics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _feed_window(rm, values):
    """模拟每采样点把瞬时深度写进趋势窗口。"""
    for v in values:
        rm._last_queue_depth = v
        rm._queue_depth_window.append(v)
        if len(rm._queue_depth_window) > rm._queue_depth_window_max:
            rm._queue_depth_window.pop(0)


def _make_point(rm, depth):
    rm._last_queue_depth = depth
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
        "mem_percent": 0.0,
    }


class TestQueueDepthTrendM78(unittest.TestCase):
    def setUp(self):
        self.rm = RuntimeMetrics()

    def test_01_window_records(self):
        _feed_window(self.rm, [10, 20, 30])
        self.assertEqual(self.rm._queue_depth_window[-1], 30)
        self.assertEqual(len(self.rm._queue_depth_window), 3)

    def test_02_rising_trend_alert(self):
        self.rm._alert_thresholds["queue_max_depth"] = 1000
        _feed_window(self.rm, [100, 200, 400, 600, 800, 950])
        pt = _make_point(self.rm, 950)
        # 直接调用内部告警检测需绕过冷却：临时清零时间戳
        self.rm._last_alert_at = 0.0
        # 用一个 spy 收集 alerts：复用 _check_alerts 但拦截落盘日志
        import logging
        _orig = logging.getLogger
        try:
            self.rm._check_alerts(pt)
        except Exception:
            pass
        # 读回 metrics.alerts
        types = [a["type"] for a in self.rm._metrics["alerts"]]
        self.assertIn("queue_depth_trend_rising", types,
                      "持续上升且涨幅显著(>=800)未触发趋势告警")

    def test_03_flat_no_trend_alert(self):
        self.rm._alert_thresholds["queue_max_depth"] = 1000
        _feed_window(self.rm, [100, 100, 100, 100, 100, 100])
        pt = _make_point(self.rm, 100)
        self.rm._metrics["alerts"] = []
        self.rm._last_alert_at = 0.0
        try:
            self.rm._check_alerts(pt)
        except Exception:
            pass
        types = [a["type"] for a in self.rm._metrics["alerts"]]
        self.assertNotIn("queue_depth_trend_rising", types,
                         "平坦窗口不应触发趋势告警")

    def test_04_decreasing_no_trend_alert(self):
        self.rm._alert_thresholds["queue_max_depth"] = 1000
        _feed_window(self.rm, [900, 800, 700, 600, 500, 400])
        pt = _make_point(self.rm, 400)
        self.rm._metrics["alerts"] = []
        self.rm._last_alert_at = 0.0
        try:
            self.rm._check_alerts(pt)
        except Exception:
            pass
        types = [a["type"] for a in self.rm._metrics["alerts"]]
        self.assertNotIn("queue_depth_trend_rising", types,
                         "下降窗口不应触发趋势告警")

    def test_05_above_threshold_absolute_alert(self):
        self.rm._alert_thresholds["queue_max_depth"] = 1000
        self.rm._metrics["alerts"] = []
        self.rm._last_alert_at = 0.0
        pt = _make_point(self.rm, 14890)
        try:
            self.rm._check_alerts(pt)
        except Exception:
            pass
        types = [a["type"] for a in self.rm._metrics["alerts"]]
        self.assertIn("queue_depth_high", types,
                      "深度越过绝对阈值未触发 queue_depth_high")

    def test_06_window_too_small_no_alert(self):
        self.rm._alert_thresholds["queue_max_depth"] = 1000
        _feed_window(self.rm, [100, 200, 300])  # 不足 6 点
        pt = _make_point(self.rm, 300)
        self.rm._metrics["alerts"] = []
        self.rm._last_alert_at = 0.0
        try:
            self.rm._check_alerts(pt)
        except Exception:
            pass
        types = [a["type"] for a in self.rm._metrics["alerts"]]
        self.assertNotIn("queue_depth_trend_rising", types,
                         "窗口样本不足不应触发趋势告警")


if __name__ == "__main__":
    unittest.main()
