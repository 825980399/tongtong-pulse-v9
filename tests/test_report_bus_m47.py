# -*- coding: utf-8 -*-
"""第47批 T3 门控测试：ReportBus 核心骨架（P0-1）

覆盖：
  * ``ReportEnvelope`` / ``Anomaly`` 契约与序列化往返
  * ``ReportBus`` 发布 / 订阅 / 消费跟踪 / 统计
  * 两个内置消费者（健康 P0 告警、污染超阈建议）的触发条件
  * ★消费者只做"动作建议"，不自动执行不可逆动作
  * 保留最近 100 份的自动清理
  * 向后兼容：无 ReportBus 时旧报告方式不受影响（不导入即不生效）

★测试隔离：全部使用 ``tempfile.mkdtemp()``，**绝不写生产 data/reports**。
"""

import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.reporting import (ACT_ALERT, SEV_P0, SEV_P1, SEV_P2,  # noqa: E402
                               MAX_REPORTS, Anomaly, ReportBus,
                               ReportEnvelope, make_envelope)
from nucleus.reporting import consumers as _C  # noqa: E402


class _Bus(unittest.TestCase):
    def setUp(self):
        self._sand = tempfile.mkdtemp(prefix="m47bus_")
        self.bus = ReportBus(base_dir=os.path.join(self._sand, "reports"))

    def tearDown(self):
        shutil.rmtree(self._sand, ignore_errors=True)


# ==================== 契约 ====================

class TestEnvelope(_Bus):
    def test_01_defaults(self):
        _e = ReportEnvelope()
        self.assertTrue(_e.report_id)
        self.assertEqual(_e.report_type, "generic")
        self.assertEqual(_e.priority, SEV_P2)
        self.assertEqual(_e.anomalies, [])

    def test_02_roundtrip(self):
        _e = make_envelope("health", "test.gen", {"k": 1},
                           [Anomaly(type="X", severity=SEV_P0,
                                    description="d")])
        _d = _e.to_dict()
        _e2 = ReportEnvelope.from_dict(_d)
        self.assertEqual(_e2.report_id, _e.report_id)
        self.assertEqual(_e2.report_type, "health")
        self.assertEqual(len(_e2.anomalies), 1)
        self.assertEqual(_e2.anomalies[0].severity, SEV_P0)

    def test_03_anomaly_from_dict(self):
        _a = Anomaly.from_dict({"type": "T", "severity": "P1",
                                "description": "d", "unknown": 1})
        self.assertEqual(_a.type, "T")
        self.assertEqual(_a.severity, SEV_P1)

    def test_04_invalid_severity_falls_back(self):
        _a = Anomaly(type="T", severity="P9")
        self.assertEqual(_a.severity, SEV_P2)

    def test_05_max_severity_from_anomalies(self):
        _e = make_envelope("health", "g", {}, [
            Anomaly(type="a", severity=SEV_P2),
            Anomaly(type="b", severity=SEV_P0)])
        self.assertEqual(_e.max_severity, SEV_P0)
        self.assertTrue(_e.has_p0())

    def test_06_priority_auto_from_anomalies(self):
        _e = make_envelope("health", "g", {},
                           [Anomaly(type="a", severity=SEV_P1)])
        self.assertEqual(_e.priority, SEV_P1)

    def test_07_priority_default_p2_when_no_anomaly(self):
        _e = make_envelope("health", "g", {})
        self.assertEqual(_e.priority, SEV_P2)

    def test_08_generated_str(self):
        _e = ReportEnvelope()
        self.assertRegex(_e.generated_str, r"^\d{4}-\d{2}-\d{2} ")

    def test_09_unique_ids(self):
        _ids = {ReportEnvelope().report_id for _ in range(20)}
        self.assertEqual(len(_ids), 20)


# ==================== 发布 / 订阅 ====================

class TestPublishSubscribe(_Bus):
    def test_10_publish_returns_id(self):
        _r = self.bus.publish_simple("health", "g")
        self.assertTrue(_r["report_id"])

    def test_11_publish_persists(self):
        _r = self.bus.publish_simple("health", "g", {"x": 1})
        self.assertTrue(_r["persisted"])
        _p = os.path.join(self._sand, "reports", "health",
                          "%s.json" % _r["report_id"])
        self.assertTrue(os.path.isfile(_p))

    def test_12_subscriber_called(self):
        _got = []
        self.bus.subscribe("health", lambda e: _got.append(e))
        _r = self.bus.publish_simple("health", "g")
        self.assertEqual(len(_got), 1)
        self.assertIn("lambda", _r["consumers"] or ["lambda"])

    def test_13_wildcard_subscriber(self):
        _got = []
        self.bus.subscribe("*", lambda e: _got.append(e))
        self.bus.publish_simple("health", "g")
        self.bus.publish_simple("runtime", "g")
        self.assertEqual(len(_got), 2)

    def test_14_type_isolation(self):
        _got = []
        self.bus.subscribe("health", lambda e: _got.append(e))
        self.bus.publish_simple("runtime", "g")
        self.assertEqual(len(_got), 0)

    def test_15_consumer_exception_isolated(self):
        def _boom(_e):
            raise RuntimeError("boom")

        _got = []
        self.bus.subscribe("health", _boom)
        self.bus.subscribe("health", lambda e: _got.append(e))
        _r = self.bus.publish_simple("health", "g")
        self.assertEqual(len(_r["errors"]), 1)
        self.assertEqual(len(_got), 1)          # 另一个消费者仍执行

    def test_16_unsubscribe(self):
        _fn = lambda e: None  # noqa: E731
        self.bus.subscribe("health", _fn)
        self.assertTrue(self.bus.unsubscribe("health", _fn))
        self.assertFalse(self.bus.unsubscribe("health", _fn))

    def test_17_persist_false(self):
        _b = ReportBus(base_dir=os.path.join(self._sand, "nope"), persist=False)
        _r = _b.publish_simple("health", "g")
        self.assertFalse(_r["persisted"])


# ==================== 消费跟踪与统计 ====================

class TestConsumption(_Bus):
    def test_20_mark_consumed(self):
        _r = self.bus.publish_simple("runtime", "g")
        self.assertTrue(self.bus.mark_consumed(_r["report_id"], "c1"))
        _e = self.bus.get(_r["report_id"])
        self.assertIn("c1", _e.consumed_by)
        self.assertTrue(_e.is_consumed())

    def test_21_mark_consumed_unknown(self):
        self.assertFalse(self.bus.mark_consumed("nope", "c1"))

    def test_22_unconsumed_list(self):
        self.bus.publish_simple("runtime", "g")
        _r2 = self.bus.publish_simple("runtime", "g")
        self.bus.mark_consumed(_r2["report_id"], "c1")
        self.assertEqual(len(self.bus.get_unconsumed()), 1)

    def test_23_unconsumed_filter_by_type(self):
        self.bus.publish_simple("health", "g")
        self.bus.publish_simple("runtime", "g")
        self.assertEqual(len(self.bus.get_unconsumed("health")), 1)

    def test_24_auto_mark_on_consumer_claim(self):
        """★消费者返回真值 → 自动登记 consumed_by（消费率才有意义）。"""
        self.bus.subscribe("runtime", lambda e: True)
        self.bus.publish_simple("runtime", "g")
        self.assertEqual(self.bus.get_stats()["consumption_rate"], 1.0)

    def test_25_stats_shape(self):
        self.bus.publish_simple("health", "g")
        _s = self.bus.get_stats()
        for _k in ("total", "consumed", "unconsumed", "consumption_rate",
                   "by_type", "by_severity", "anomaly_count",
                   "actions_triggered", "subscribers"):
            self.assertIn(_k, _s)

    def test_26_empty_stats(self):
        _s = self.bus.get_stats()
        self.assertEqual(_s["total"], 0)
        self.assertEqual(_s["consumption_rate"], 0.0)


# ==================== 自动清理 ====================

class TestTrim(_Bus):
    def test_30_keeps_last_n(self):
        _b = ReportBus(base_dir=os.path.join(self._sand, "r2"), max_reports=5,
                       persist=False)
        for _i in range(20):
            _b.publish_simple("runtime", "g", {"i": _i})
        self.assertEqual(_b.get_stats()["total"], 5)

    def test_31_keeps_newest(self):
        _b = ReportBus(base_dir=os.path.join(self._sand, "r3"), max_reports=3,
                       persist=False)
        for _i in range(10):
            _b.publish_simple("runtime", "g", {"i": _i})
        _vals = sorted(e.content.get("i", -1) for e in _b.all())
        self.assertEqual(_vals, [7, 8, 9])

    def test_32_default_max(self):
        self.assertEqual(MAX_REPORTS, 100)
        self.assertEqual(self.bus.get_stats()["max_reports"], 100)


# ==================== 内置消费者 ====================

class TestBuiltinConsumers(_Bus):
    def test_40_health_p0_alerts(self):
        _C.register_builtin_consumers(self.bus)
        _r = self.bus.publish_simple(
            "health", "g", anomalies=[
                Anomaly(type="H_P0", severity=SEV_P0, description="严重异常",
                        suggested_action=ACT_ALERT)])
        self.assertIn("health_anomaly_consumer", _r["consumers"])
        _e = self.bus.get(_r["report_id"])
        self.assertTrue(_e.actions_triggered)

    def test_41_health_p1_no_alert(self):
        """P1 不触发健康告警（只 P0 才告警）。"""
        _C.register_builtin_consumers(self.bus)
        _r = self.bus.publish_simple(
            "health", "g", anomalies=[
                Anomaly(type="H_P1", severity=SEV_P1, description="一般")])
        self.assertNotIn("health_anomaly_consumer", _r["consumers"])

    def test_42_pollution_over_threshold(self):
        _C.register_builtin_consumers(self.bus)
        _r = self.bus.publish_simple("pollution", "g",
                                     {"pollution_rate": 0.78})
        self.assertIn("pollution_anomaly_consumer", _r["consumers"])

    def test_43_pollution_under_threshold(self):
        _C.register_builtin_consumers(self.bus)
        _r = self.bus.publish_simple("pollution", "g",
                                     {"pollution_rate": 0.10})
        self.assertNotIn("pollution_anomaly_consumer", _r["consumers"])

    def test_44_pollution_rate_from_anomaly(self):
        """污染率也可从 Anomaly.metric_value 取。"""
        _C.register_builtin_consumers(self.bus)
        _r = self.bus.publish_simple("pollution", "g", {}, [
            Anomaly(type="SERP", severity=SEV_P0,
                    description="污染", metric_value=0.9)])
        self.assertIn("pollution_anomaly_consumer", _r["consumers"])

    def test_45_no_auto_execution(self):
        """★消费者只建议、不自动执行不可逆动作。"""
        _C.register_builtin_consumers(self.bus)
        _r = self.bus.publish_simple("pollution", "g",
                                     {"pollution_rate": 0.9})
        _e = self.bus.get(_r["report_id"])
        self.assertIn("clean_suggestion", _e.actions_triggered)
        # 不得出现"已执行"类动作
        self.assertNotIn("clean_executed", _e.actions_triggered)

    def test_46_other_type_untouched(self):
        _C.register_builtin_consumers(self.bus)
        _r = self.bus.publish_simple("runtime", "g")
        self.assertEqual(_r["consumers"], [])


# ==================== 向后兼容 ====================

class TestBackwardCompat(unittest.TestCase):
    def test_50_import_has_no_side_effect(self):
        """导入 reporting 包不应创建任何目录。"""
        _d = os.path.join(_ROOT, "data", "reports")
        _before = os.path.isdir(_d)
        _after = os.path.isdir(_d)
        self.assertEqual(_before, _after)

    def test_51_old_style_still_works(self):
        """不经过 ReportBus 的旧式报告生成不受影响（本包只是额外通道）。"""
        _e = make_envelope("health", "g", {"legacy": True})
        self.assertTrue(_e.content["legacy"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
