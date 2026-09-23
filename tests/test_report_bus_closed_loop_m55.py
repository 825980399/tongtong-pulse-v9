# -*- coding: utf-8 -*-
"""第55批 T2 门控测试：ReportBus 闭环补齐（P0-1 自认知闭环断裂）。

★T0 重大偏差（第 31 次）
------------------------
任务书称"ReportBus 全库零调用、需接入"。实测**第50批已完成接入**：
publishers 有 6 处生产调用，``main.py:588`` 已注册消费者，``ENABLE_REPORT_BUS=True``。
真正没做完的是两件事（本测试覆盖）：

1. **``publish_patch_quality`` 指标取不到** —— ``DailyScheduler`` 传的是
   ``evaluate_and_report()`` 的返回值 ``{"summary": {...}}``，而发布器只取顶层
   → 恒 None → anomalies 为空 → **「真实修复率 0.0」在总线上隐身**（P0-2）。
2. **只有 health / pollution 有消费者** —— self_cognition / evolution
   发布后无人认领（实测 5 份生产报告仅 1 份被消费 = 20%）。

覆盖
----
① ``_pick_metric`` 能从 ``summary`` 嵌套取值（★真实 bug 复现）
② 补丁质量报告（summary 结构）能产出异常（修复前 anomalies 恒空）
③ summary 指标归一到 content 顶层（消费者可读）
④ evolution 报告被 ``evolution_anomaly_consumer`` 认领
⑤ self_cognition 报告被 ``self_cognition_consumer`` 认领
⑥ 灰度关闭 → 只注册原有两个类型（零回归）
⑦ 既有 health / pollution 行为不变（回归保护）
⑧ 四类报告发布后消费率 > 0（★P0-1 的核心度量）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

import config  # noqa: E402
from nucleus.reporting import (consumers as _cons,  # noqa: E402
                               publishers as _pub, report_bus as _rb)
from nucleus.reporting.report_envelope import Anomaly  # noqa: E402

# 真实结构：DailyScheduler → evaluate_and_report() 的返回值
REAL_PQ = {
    "summary": {
        "fake_pass": 2, "unverifiable": 56, "not_applied": 2, "good": 0,
        "mediocre": 2, "bad": 0, "avg_score": 78.06, "applied": 58,
        "verified": 58, "real_effectiveness_computable": 2,
        "claimed_effectiveness_avg": 0.9741, "no_regression_rate": 0.9355,
        "real_fix_rate": 0.0, "verifiable_rate": 0.0333,
    },
    "patches": [],
    "findings": [],
}


class TestReportBusClosedLoopM55(unittest.TestCase):
    """★第55批 T2：闭环补齐。"""

    def setUp(self):
        _rb.reset_report_bus()
        self.bus = _rb.get_report_bus()
        self.types = _cons.register_builtin_consumers(self.bus)

    def tearDown(self):
        _rb.reset_report_bus()

    # ---------- ① 指标取值 ----------
    def test_01_pick_metric_reads_nested_summary(self):
        """★真实 bug 复现：指标在 ``summary`` 下时必须能取到。"""
        self.assertEqual(
            _pub._pick_metric({"summary": {"real_fix_rate": 0.0}},
                              ("real_fix_rate",)), 0.0)
        # 顶层优先
        self.assertEqual(
            _pub._pick_metric({"real_fix_rate": 0.5}, ("real_fix_rate",)), 0.5)
        # 都没有 → None
        self.assertIsNone(_pub._pick_metric({"other": 1}, ("real_fix_rate",)))
        # 非 dict 入参不抛异常
        self.assertIsNone(_pub._pick_metric(None, ("real_fix_rate",)))

    # ---------- ② 异常产出 ----------
    def test_02_patch_quality_yields_anomaly(self):
        """修复前：summary 结构 → anomalies 恒空（P0-2 隐身）；修复后必须有异常。"""
        _out = _pub.publish_patch_quality(REAL_PQ, generator="test")
        self.assertIsNotNone(_out, "发布失败（开关未开？）")
        _env = self.bus.get(_out["report_id"])
        self.assertTrue(_env.anomalies,
                        "真实修复率 0.0 应产出 PATCH_REAL_FIX_RATE_LOW 异常")
        self.assertEqual(_env.anomalies[0].type, "PATCH_REAL_FIX_RATE_LOW")

    def test_03_summary_metrics_normalized_to_content(self):
        """summary 指标归一到 content 顶层 → 消费者可读。"""
        _out = _pub.publish_patch_quality(REAL_PQ, generator="test")
        _env = self.bus.get(_out["report_id"])
        self.assertIn("real_fix_rate", _env.content)
        self.assertEqual(_env.content["real_fix_rate"], 0.0)
        self.assertIn("no_regression_rate", _env.content)

    # ---------- ③④⑤ 消费者认领 ----------
    def test_04_evolution_consumed(self):
        """evolution 报告被认领（修复前无人认领）。"""
        _out = _pub.publish_patch_quality(REAL_PQ, generator="test")
        self.assertIn("evolution_anomaly_consumer", _out["consumers"])

    def test_05_self_cognition_consumed(self):
        """self_cognition 报告被认领（修复前无人认领）。"""
        _out = _pub.publish_self_cognition(
            "报告正文", summary={"headline_issue": "补丁验证空转",
                                 "overall_score": 45.0})
        self.assertIsNotNone(_out)
        self.assertIn("self_cognition_consumer", _out["consumers"])

    # ---------- ⑥ 灰度 ----------
    def test_06_switch_off_keeps_only_two(self):
        """关闭 ENABLE_REPORT_EXT_CONSUMERS → 只注册 health / pollution（零回归）。"""
        _rb.reset_report_bus()
        _bus2 = _rb.get_report_bus()
        with mock.patch.object(config, "ENABLE_REPORT_EXT_CONSUMERS", False):
            self.assertEqual(_cons.register_builtin_consumers(_bus2),
                             ["health", "pollution"])

    # ---------- ⑦ 回归保护 ----------
    def test_07_existing_consumers_unchanged(self):
        """既有 health(P0 认领) / pollution(超阈认领) 行为不变。"""
        _r1 = self.bus.publish_simple(
            "health", "g", anomalies=[
                Anomaly(type="H_P0", severity="P0", description="严重异常",
                        suggested_action="alert")])
        self.assertIn("health_anomaly_consumer", _r1["consumers"])

        _r2 = self.bus.publish_simple("pollution", "g", {"pollution_rate": 0.78})
        self.assertIn("pollution_anomaly_consumer", _r2["consumers"])

    # ---------- ⑧ 消费率 ----------
    def test_08_consumption_rate_positive(self):
        """★P0-1 核心度量：四类报告发布后消费率 > 0。"""
        _pub.publish_health({"overall_health": "critical", "health_score": 30.0,
                             "issues": ["a"], "warnings": []})
        _pub.publish_pollution({"pollution_rate": 0.77, "total": 100,
                                "polluted": 77, "clean": 23})
        _pub.publish_patch_quality(REAL_PQ)
        _pub.publish_self_cognition("正文", summary={"headline_issue": "空转"})
        _st = self.bus.get_stats()
        self.assertGreater(_st["consumption_rate"], 0,
                           "四类报告发布后仍有报告无人消费：%s" % _st)


if __name__ == "__main__":
    unittest.main()
