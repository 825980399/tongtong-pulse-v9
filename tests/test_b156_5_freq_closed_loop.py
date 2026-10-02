# -*- coding: utf-8 -*-
"""B156-5 降频闭环 · 片2 隔离测试。

覆盖四项交付物：
1. 阈值按 L3 物理上限(100/300)重标 → assess_load_level 边界 100/200/300；
2. 生产者接线 → _sync_adaptive_level 随队列深度升级等级；
3. should_execute 跳过可观测 → 等级变化 INFO / 节流 DEBUG / 暂停 WARNING / LOW 不记录；
4. 告警值过期标记 → 陈旧锁存与同值重复抑制 + expires_at。

框架已停机，可独立运行：python -m pytest tests/test_b156_5_freq_closed_loop.py -q
"""
import logging
import time
import unittest

from nucleus.runtime_metrics import (
    AdaptiveFrequencyController,
    RuntimeMetrics,
    assess_load_level,
    get_adaptive_controller,
)


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class TestB1565ThresholdRecalibration(unittest.TestCase):
    """票②：阈值锚定 L3 物理上限（软100/硬300）。"""

    def test_boundaries(self):
        # MEDIUM=100 / HIGH=200 / CRITICAL=300
        self.assertEqual(assess_load_level(queue_depth=99), "LOW")
        self.assertEqual(assess_load_level(queue_depth=100), "MEDIUM")
        self.assertEqual(assess_load_level(queue_depth=199), "MEDIUM")
        self.assertEqual(assess_load_level(queue_depth=200), "HIGH")
        self.assertEqual(assess_load_level(queue_depth=299), "HIGH")
        self.assertEqual(assess_load_level(queue_depth=300), "CRITICAL")
        self.assertEqual(assess_load_level(queue_depth=500), "CRITICAL")

    def test_cpu_dimension_unchanged(self):
        # CPU 维度阈值不在本批重标范围，保持 50/70/85 语义
        self.assertEqual(assess_load_level(cpu_percent=60), "MEDIUM")
        self.assertEqual(assess_load_level(cpu_percent=75), "HIGH")
        self.assertEqual(assess_load_level(cpu_percent=90), "CRITICAL")

    def test_snapshot_saving_bumps_to_medium(self):
        self.assertEqual(assess_load_level(snapshot_saving=True), "MEDIUM")


class TestB1565Producer(unittest.TestCase):
    """票①：assess_load_level 生产者经 _sync_adaptive_level 下发等级。"""

    def setUp(self):
        self.rm = RuntimeMetrics()
        self.ctrl = get_adaptive_controller()
        self.ctrl.set_level("LOW")

    def tearDown(self):
        self.ctrl.set_level("LOW")

    def test_low_no_escalation(self):
        self.rm._last_queue_depth = 50
        self.rm._sync_adaptive_level()
        self.assertEqual(self.ctrl.get_level(), "LOW")

    def test_medium_at_soft_cap(self):
        self.rm._last_queue_depth = 100
        self.rm._sync_adaptive_level()
        self.assertEqual(self.ctrl.get_level(), "MEDIUM")

    def test_high(self):
        self.rm._last_queue_depth = 250
        self.rm._sync_adaptive_level()
        self.assertEqual(self.ctrl.get_level(), "HIGH")

    def test_critical_at_hard_cap(self):
        self.rm._last_queue_depth = 300
        self.rm._sync_adaptive_level()
        self.assertEqual(self.ctrl.get_level(), "CRITICAL")


class TestB1565ShouldExecuteObservable(unittest.TestCase):
    """票③：should_execute 跳过可观测（节流防刷屏）。"""

    def setUp(self):
        self.handler = _CaptureHandler()
        self.logger = logging.getLogger("pulse.module.runtime_metrics")
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(self.handler)
        self.ctrl = AdaptiveFrequencyController()
        self.ctrl.register("op_x", 30.0)

    def tearDown(self):
        self.logger.removeHandler(self.handler)

    def _msgs(self):
        return [r.getMessage() for r in self.handler.records]

    def test_level_change_logs_info(self):
        self.ctrl.set_level("CRITICAL")  # LOW -> CRITICAL
        self.assertTrue(any("等级变化" in m for m in self._msgs()))

    def test_pause_logs_warning(self):
        self.ctrl.set_level("CRITICAL")
        self.assertFalse(self.ctrl.should_execute("op_x", now=time.time()))  # iv=inf
        self.assertTrue(any("已暂停" in m for m in self._msgs()))

    def test_throttle_logs_debug_when_primed(self):
        self.ctrl.set_level("HIGH")
        _now = time.time()
        self.ctrl._last_run["op_x"] = _now  # 武装，使下次调用落在间隔内→跳过
        self.assertFalse(self.ctrl.should_execute("op_x", now=_now))
        self.assertTrue(any("节流跳过" in m for m in self._msgs()))

    def test_no_skip_log_when_low(self):
        self.ctrl.set_level("LOW")
        self.handler.records.clear()
        _now = time.time()
        self.ctrl._last_run["op_x"] = _now
        self.assertFalse(self.ctrl.should_execute("op_x", now=_now))
        self.assertFalse(any("[自适应降频]" in m for m in self._msgs()))


class TestB1565AlertExpiry(unittest.TestCase):
    """票④：告警值过期标记——陈旧锁存不再反复告警。"""

    def setUp(self):
        self.rm = RuntimeMetrics()
        self.rm._alert_cooldown = 0.0  # 关闭冷避免干扰去重测试

    def _point(self):
        return {"pulse_errors": 0, "lock_wait_avg_ms": 0.0, "mem_percent": 0.0}

    def test_fresh_high_alert_carries_expires_at(self):
        self.rm._last_queue_depth = 14890
        self.rm._last_queue_depth_at = time.time()  # 新鲜
        self.rm._queue_alert_last_at = 0.0
        self.rm._last_alert_at = 0.0
        self.rm._check_alerts(self._point())
        self.assertEqual(len(self.rm._metrics["alerts"]), 1)
        self.assertIn("expires_at", self.rm._metrics["alerts"][-1])
        self.assertEqual(self.rm._metrics["alerts"][-1]["type"], "queue_depth_high")

    def test_repeat_same_value_suppressed(self):
        self.rm._last_queue_depth = 14890
        self.rm._last_queue_depth_at = time.time()
        self.rm._queue_alert_last_at = 0.0
        self.rm._last_alert_at = 0.0
        self.rm._check_alerts(self._point())
        # 紧接同值重复：绕过 30s 冷却，专测去重
        self.rm._last_alert_at = 0.0
        self.rm._check_alerts(self._point())
        self.assertEqual(len(self.rm._metrics["alerts"]), 1)

    def test_stale_latch_no_alert(self):
        # 陈旧锁存：值久未刷新 → 不告警（根治 X-3）
        self.rm._last_queue_depth = 14890
        self.rm._last_queue_depth_at = time.time() - 1000
        self.rm._last_alert_at = 0.0
        self.rm._check_alerts(self._point())
        self.assertEqual(len(self.rm._metrics["alerts"]), 0)

    def test_new_value_after_refresh_realerts(self):
        self.rm._last_queue_depth = 14890
        self.rm._last_queue_depth_at = time.time()
        self.rm._queue_alert_last_at = 0.0
        self.rm._last_alert_at = 0.0
        self.rm._check_alerts(self._point())
        # 刷新采样时刻并改变值 → 应重新告警
        self.rm._last_queue_depth_at = time.time()
        self.rm._queue_alert_last_depth = 14890
        self.rm._queue_alert_last_at = 0.0
        self.rm._last_alert_at = 0.0
        self.rm._last_queue_depth = 14891
        self.rm._check_alerts(self._point())
        self.assertEqual(len(self.rm._metrics["alerts"]), 2)


if __name__ == "__main__":
    unittest.main()
