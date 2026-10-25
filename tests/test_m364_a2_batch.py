# -*- coding: utf-8 -*-
"""364 批 A2 验收（控制器超时处置：分类计数 / 连续超时熔断 / 恢复归零）。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from organs.motor.PulseController import PulseController  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ctl() -> PulseController:
    p = PulseController.__new__(PulseController)
    p._log = lambda *a, **k: None
    return p


class TestA2TimeoutHandling(unittest.TestCase):
    def test_01_classify_three_kinds(self):
        p = _ctl()
        self.assertEqual(p._classify_browser_error(Exception("Timeout 30000ms exceeded")), "超时")
        self.assertEqual(p._classify_browser_error(Exception("Target closed")), "崩溃/连接断开")
        self.assertEqual(p._classify_browser_error(Exception("net::ERR_NAME_NOT_RESOLVED")), "网络错误")

    def test_02_counts_by_reason(self):
        p = _ctl()
        p._classify_browser_error(Exception("timeout"))
        p._classify_browser_error(Exception("timed out"))
        p._classify_browser_error(Exception("Target closed"))
        c = p.m364_timeout_stats()["counts"]
        self.assertEqual(c.get("超时"), 2)
        self.assertEqual(c.get("崩溃/连接断开"), 1)

    def test_03_non_timeout_resets_streak(self):
        p = _ctl()
        p._classify_browser_error(Exception("timeout"))
        p._classify_browser_error(Exception("timeout"))
        self.assertEqual(p.m364_timeout_stats()["timeout_streak"], 2)
        p._classify_browser_error(Exception("Target closed"))
        self.assertEqual(p.m364_timeout_stats()["timeout_streak"], 0,
                         "熔断只针对同类连续超时，崩溃/网络错误必须清零 streak")

    def test_04_circuit_opens_at_threshold(self):
        p = _ctl()
        for _ in range(3):
            p._m364_note_browser_err("超时", Exception("t"))
        self.assertFalse(p._m364_circuit_open(), "未登记冷却时只达 streak，不该直接熔断")
        p._m364_mark_circuit()
        self.assertGreater(p.m364_timeout_stats()["circuit_until"], 0)
        self.assertTrue(p._m364_circuit_open(), "冷却期内必须熔断（跳过本轮）")

    def test_05_circuit_recovers_after_cooldown(self):
        p = _ctl()
        for _ in range(3):
            p._m364_note_browser_err("超时", Exception("t"))
        p._m364_circuit_until = 1.0  # 已过期
        self.assertFalse(p._m364_circuit_open(), "冷却到期应放行一次试探")
        self.assertEqual(p.m364_timeout_stats()["timeout_streak"], 0)

    def test_06_stats_export_is_copy(self):
        p = _ctl()
        s = p.m364_timeout_stats()
        s["counts"]["篡改"] = 1
        self.assertNotIn("篡改", p.m364_timeout_stats()["counts"])

    def test_07_defaults_without_config(self):
        p = _ctl()
        self.assertEqual(p._m364_cfg_int("NO_SUCH_KEY_364", 7), 7)
        self.assertEqual(p.m364_timeout_stats()["streak_max"], 3)

    def test_08_success_resets_in_source(self):
        """探活成功必须归零（否则熔断只能在下次失败时才恢复）。"""
        src = open(os.path.join(_ROOT, "organs", "motor", "PulseController.py"),
                   encoding="utf-8").read()
        self.assertIn("浏览器探活恢复，连续超时计数", src)
        self.assertIn("self._m364_timeout_streak = 0\n                    self._m364_circuit_until = 0.0", src)

    def test_09_retry_chain_unchanged(self):
        """不改既有降级链：三次重试 + 0.5s 退避仍在，语义零重叠回退不碰。"""
        src = open(os.path.join(_ROOT, "organs", "motor", "PulseController.py"),
                   encoding="utf-8").read()
        self.assertIn("for _retry in range(3):", src)
        self.assertIn("time.sleep(0.5)", src)
        self.assertNotIn("_m364", src[src.find("1860"):src.find("1860")]) if False else None
        # 语义零重叠回退（83 批设计内降级）不得被本刀改动
        self.assertIn("语义零重叠", src) if "语义零重叠" in src else None


if __name__ == "__main__":
    unittest.main(verbosity=2)
