# -*- coding: utf-8 -*-
"""第164批 刀A4：知识获取路由器浏览器熔断 —— 门控单测。

验证：连续失败触发熔断（有计数/日志证据）、恢复后自动复位、
半开探测、单测隔离、acquire 在熔断态返回 None（调用方走直连 wiki/RSS 降级）。
"""
import time
import unittest
from unittest import mock

import config
import nucleus.knowledge.KnowledgeAcquisitionRouter as M
from nucleus.knowledge.KnowledgeAcquisitionRouter import (
    KnowledgeAcquisitionRouter,
    report_browser_outcome,
)


class TestKaRouterCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self.router = KnowledgeAcquisitionRouter()
        self.router.reset_browser_circuit()
        # 固定阈值便于断言
        self._orig_thr = M.KA_ROUTER_BROWSER_FAIL_THRESHOLD
        self._orig_cd = M.KA_ROUTER_BROWSER_COOLDOWN_SEC
        self._orig_en = M.KA_ROUTER_BROWSER_CIRCUIT_BREAKER_ENABLED
        M.KA_ROUTER_BROWSER_FAIL_THRESHOLD = 5
        M.KA_ROUTER_BROWSER_COOLDOWN_SEC = 300.0
        M.KA_ROUTER_BROWSER_CIRCUIT_BREAKER_ENABLED = True

    def tearDown(self):
        M.KA_ROUTER_BROWSER_FAIL_THRESHOLD = self._orig_thr
        M.KA_ROUTER_BROWSER_COOLDOWN_SEC = self._orig_cd
        M.KA_ROUTER_BROWSER_CIRCUIT_BREAKER_ENABLED = self._orig_en
        self.router.reset_browser_circuit()

    # ---- 阈值/开关常量 ----
    def test_default_constants(self):
        self.assertTrue(M.KA_ROUTER_BROWSER_CIRCUIT_BREAKER_ENABLED)
        self.assertEqual(M.KA_ROUTER_BROWSER_FAIL_THRESHOLD, 5)
        self.assertEqual(M.KA_ROUTER_BROWSER_COOLDOWN_SEC, 300.0)

    # ---- 未达阈值不跳闸 ----
    def test_no_trip_below_threshold(self):
        for _ in range(4):
            self.router.record_browser_outcome(False)
        st = self.router.get_browser_circuit_state()
        self.assertFalse(st["open"])
        self.assertEqual(st["fail_streak"], 4)
        self.assertEqual(st["trip_count"], 0)

    # ---- 达阈值跳闸，且有日志/计数证据 ----
    def test_trip_after_threshold(self):
        logs = []
        self.router._log_fn = logs.append
        for _ in range(5):
            self.router.record_browser_outcome(False)
        st = self.router.get_browser_circuit_state()
        self.assertTrue(st["open"])
        self.assertEqual(st["fail_streak"], 5)
        self.assertEqual(st["trip_count"], 1)
        self.assertTrue(any("熔断触发" in m for m in logs))

    # ---- 一次成功即复位 ----
    def test_reset_on_success(self):
        for _ in range(5):
            self.router.record_browser_outcome(False)
        self.assertTrue(self.router.get_browser_circuit_state()["open"])
        self.router.record_browser_outcome(True)
        st = self.router.get_browser_circuit_state()
        self.assertFalse(st["open"])
        self.assertEqual(st["fail_streak"], 0)
        self.assertEqual(st["open_until"], 0.0)

    # ---- 冷却期内重复失败不重复计数跳闸 ----
    def test_no_overcount_during_cooldown(self):
        for _ in range(5):
            self.router.record_browser_outcome(False)
        self.assertEqual(self.router.get_browser_circuit_state()["trip_count"], 1)
        for _ in range(10):
            self.router.record_browser_outcome(False)
        st = self.router.get_browser_circuit_state()
        self.assertEqual(st["trip_count"], 1)  # 冷却期内只计 1 次跳闸
        self.assertTrue(st["open"])

    # ---- 半开探测（冷却到期后）仍失败 → 延长冷却、trip_count+1 ----
    def test_half_open_reprobe_failure_extends(self):
        for _ in range(5):
            self.router.record_browser_outcome(False)
        self.assertEqual(self.router.get_browser_circuit_state()["trip_count"], 1)
        # 模拟冷却到期
        self.router._browser_open_until = time.time() - 1.0
        self.router.record_browser_outcome(False)
        st = self.router.get_browser_circuit_state()
        self.assertTrue(st["open"])
        self.assertEqual(st["trip_count"], 2)

    # ---- 半开探测成功 → 闭合复位 ----
    def test_half_open_reprobe_success_closes(self):
        for _ in range(5):
            self.router.record_browser_outcome(False)
        self.router._browser_open_until = time.time() - 1.0  # 冷却到期
        self.router.record_browser_outcome(True)
        st = self.router.get_browser_circuit_state()
        self.assertFalse(st["open"])
        self.assertEqual(st["fail_streak"], 0)

    # ---- 关闭开关时永不打开 ----
    def test_disabled_switch_never_opens(self):
        M.KA_ROUTER_BROWSER_CIRCUIT_BREAKER_ENABLED = False
        for _ in range(20):
            self.router.record_browser_outcome(False)
        self.assertFalse(self.router.get_browser_circuit_state()["open"])

    # ---- acquire 在熔断态返回 None（调用方走直连 wiki/RSS 降级） ----
    def test_acquire_returns_none_when_open(self):
        # 打开路由器总开关，构造熔断态
        with mock.patch.object(config, "ENABLE_KNOWLEDGE_ACQUISITION_ROUTER", True):
            for _ in range(5):
                self.router.record_browser_outcome(False)
            self.assertTrue(self.router.get_browser_circuit_state()["open"])
            res = self.router.acquire("现在几点了")  # QT_REAL_TIME → 原 plan=[browser]
        self.assertIsNone(res)  # 熔断态下 acquire 返回 None，不委托 browser

    # ---- acquire 在闭合态对 browser 计划返回委托（不复用同一 router 单例状态） ----
    def test_acquire_delegates_when_closed(self):
        with mock.patch.object(config, "ENABLE_KNOWLEDGE_ACQUISITION_ROUTER", True):
            res = self.router.acquire("现在几点了")
        self.assertIsNotNone(res)
        self.assertTrue(res.get("delegate_browser"))

    # ---- 模块级便捷入口 report_browser_outcome 经共享单例生效 ----
    def test_module_entry_feeds_shared_singleton(self):
        shared = M.get_shared_knowledge_router()
        shared.reset_browser_circuit()
        for _ in range(5):
            report_browser_outcome(False)
        self.assertTrue(shared.get_browser_circuit_state()["open"])
        report_browser_outcome(True)
        self.assertFalse(shared.get_browser_circuit_state()["open"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
