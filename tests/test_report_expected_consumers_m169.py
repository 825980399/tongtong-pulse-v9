# -*- coding: utf-8 -*-
"""169批 C1（T-报告契约-2）门控测试。

锁定三件事：
  1. 发射方「期望消费类别」声明生效（declare/get）；
  2. 无订阅者 / 期望未覆盖 → 走 check_consumer_staleness 同族 WARNING（节流）；
  3. 声明≠订阅：C1 只加声明与告警，**不新建消费者**（禁重复实现）。

零回归：开关 ENABLE_REPORT_EXPECTED_CONSUMERS=False 时完全不告警、不校验。
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

from nucleus.reporting import report_bus as rb


def _new_bus():
    """隔离总线：落盘关 + 目录指向临时目录（绝不写生产 data/）。"""
    return rb.ReportBus(base_dir=tempfile.mkdtemp(prefix="m169_c1_"),
                        persist=False)


class _Rec:
    """替代 get_module_logger 的假日志器，捕获 warning 文案。"""

    def __init__(self):
        self.warnings = []

    def warning(self, msg, *a, **k):
        self.warnings.append(str(msg))

    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class TestExpectedConsumersDeclared(unittest.TestCase):
    """① 发射方期望消费类别声明生效。"""

    def test_01_declare_and_read_all(self):
        b = _new_bus()
        self.assertEqual(b.get_expected_consumers(), {})
        b.declare_expected_consumers("health", ["c1", "c2"])
        self.assertEqual(b.get_expected_consumers(), {"health": ["c1", "c2"]})

    def test_02_declare_and_read_one_type(self):
        b = _new_bus()
        b.declare_expected_consumers("health", ["c1"])
        self.assertEqual(b.get_expected_consumers("health"), ["c1"])
        self.assertEqual(b.get_expected_consumers("nope"), [])

    def test_03_declare_dedups_and_ignores_empty_type(self):
        b = _new_bus()
        b.declare_expected_consumers("health", ["c1", "c1", "c2"])
        self.assertEqual(b.get_expected_consumers("health"), ["c1", "c2"])
        b.declare_expected_consumers("", ["c9"])        # 空类型：忽略
        self.assertNotIn("", b.get_expected_consumers())

    def test_04_declare_is_not_subscribe(self):
        """★C1 禁新建消费者：声明只落契约表，不进 _subscribers。"""
        b = _new_bus()
        b.declare_expected_consumers("health", ["c1"])
        self.assertIsNone(b._subscribers.get("health"))
        self.assertFalse(b._has_any_subscriber("health"))


class TestMissingConsumerAlert(unittest.TestCase):
    """② 无订阅者 / 期望未覆盖 → 同族告警；③ 开关关闭零回归。"""

    def _publish_capture(self, bus, report_type="health"):
        """发布一次并捕获 ReportBus 的 warning 文案。"""
        _rec = _Rec()
        with mock.patch("nucleus.logger.get_module_logger",
                        return_value=_rec):
            bus.publish_simple(report_type, "m169_test",
                               content={"k": 1})
        return _rec.warnings

    def test_10_no_subscriber_warns(self):
        b = _new_bus()
        _w = self._publish_capture(b)
        self.assertTrue(any("消费契约机检" in x and "无订阅者" in x for x in _w),
                        "无订阅者应触发同族告警: %s" % _w)

    def test_11_expected_covered_no_warn(self):
        b = _new_bus()

        def c1(env):
            return True

        b.subscribe("health", c1)
        b.declare_expected_consumers("health", ["c1"])
        _w = self._publish_capture(b)
        self.assertFalse([x for x in _w if "消费契约机检" in x],
                         "期望被覆盖时不应告警: %s" % _w)

    def test_12_expected_missing_warns(self):
        b = _new_bus()

        def c1(env):
            return True

        b.subscribe("health", c1)
        b.declare_expected_consumers("health", ["c1", "c2"])
        _w = self._publish_capture(b)
        self.assertTrue(
            any("消费契约机检" in x and "期望消费者未覆盖" in x and "c2" in x
                for x in _w),
            "期望未覆盖应告警且点名缺失者: %s" % _w)

    def test_13_switch_off_zero_regression(self):
        """开关关闭 → 不告警、不校验（行为回到 C1 之前）。"""
        b = _new_bus()
        with mock.patch.object(config, "ENABLE_REPORT_EXPECTED_CONSUMERS",
                               False, create=True):
            self.assertFalse(rb.ReportBus._m169_expected_consumers_on())
            _w = self._publish_capture(b)
        self.assertFalse([x for x in _w if "消费契约机检" in x],
                         "开关关闭时不得告警: %s" % _w)

    def test_14_throttled_within_window(self):
        """同族节流：窗口内只告警一次。"""
        b = _new_bus()
        _rec = _Rec()
        with mock.patch("nucleus.logger.get_module_logger", return_value=_rec):
            b.publish_simple("health", "m169_test")
            b.publish_simple("health", "m169_test")
        _hits = [x for x in _rec.warnings if "消费契约机检" in x]
        self.assertEqual(len(_hits), 1, "节流失效: %s" % _rec.warnings)


class TestCheckMissingConsumers(unittest.TestCase):
    """机检：期望声明与实际订阅者不匹配的类型。"""

    def test_20_gap_detected(self):
        b = _new_bus()
        b.declare_expected_consumers("health", ["c9"])
        self.assertEqual(b.check_missing_consumers(), ["health"])

    def test_21_gap_cleared_after_subscribe(self):
        b = _new_bus()
        b.declare_expected_consumers("health", ["c9"])

        def c9(env):
            return True

        b.subscribe("health", c9)
        self.assertEqual(b.check_missing_consumers(), [])

    def test_22_undeclared_types_not_reported(self):
        """未声明期望的类型不参与（零噪声）。"""
        b = _new_bus()

        def c1(env):
            return True

        b.subscribe("pollution", c1)     # 有订阅但未声明期望
        self.assertEqual(b.check_missing_consumers(), [])


if __name__ == "__main__":
    unittest.main()
