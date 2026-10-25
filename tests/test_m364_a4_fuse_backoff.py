# -*- coding: utf-8 -*-
"""364 批 A4 验收（融合失败退避：数据指纹真正生效 / 封顶留痕 / 停滞读出口）。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from organs.body.PulseLiver import PulseLiver, m364_fuse_backoff_plan  # noqa: E402

BASE = 300.0   # 5 分钟（_ADAPTIVE_FUSE_COOLDOWN * 0.5）
CAP = 6 * 3600.0


def _liver() -> PulseLiver:
    p = PulseLiver.__new__(PulseLiver)
    p._log = lambda *a, **k: None
    return p


class TestA4FuseBackoff(unittest.TestCase):
    def test_01_first_failure_is_base(self):
        """首次失败：起步档 5 分钟，计数 1。"""
        dur, n, reason = m364_fuse_backoff_plan(1, (10, 3), None, BASE)
        self.assertEqual((dur, n, reason), (BASE, 1, "first"))

    def test_02_unchanged_fingerprint_caps(self):
        """★核心：指纹未变且已失败 ≥2 次 ⇒ 直接封顶，不再逐级 5→10→20→…。"""
        dur, n, reason = m364_fuse_backoff_plan(2, (10, 3), (10, 3), BASE)
        self.assertEqual((dur, n, reason), (CAP, 2, "data_unchanged_capped"))
        # 第 6 次仍指纹未变：依旧封顶（旧实现会到 160min 仍不收敛）
        dur6, n6, r6 = m364_fuse_backoff_plan(6, (10, 3), (10, 3), BASE)
        self.assertEqual((dur6, r6), (CAP, "data_unchanged_capped"))
        self.assertEqual(n6, 6)

    def test_03_fingerprint_changed_resets_counter(self):
        """数据指纹变化 ⇒ 计数重置为 1，退避回到起步档（旧实现只增不减）。"""
        dur, n, reason = m364_fuse_backoff_plan(7, (12, 5), (10, 3), BASE)
        self.assertEqual((dur, n, reason), (BASE, 1, "fingerprint_changed"))

    def test_04_exponential_fallback(self):
        """指纹缺失（如持久化丢失）但计数已累积 ⇒ 走指数档并封顶。"""
        dur, n, reason = m364_fuse_backoff_plan(3, (10, 3), None, BASE)
        self.assertEqual(reason, "exponential")
        self.assertEqual(dur, BASE * 2 ** 2)
        self.assertEqual(n, 3)
        # 封顶生效
        dur_big, _, _ = m364_fuse_backoff_plan(20, (10, 3), None, BASE)
        self.assertEqual(dur_big, CAP)

    def test_05_cap_is_six_hours(self):
        self.assertEqual(CAP, 21600.0)
        _, _, r = m364_fuse_backoff_plan(2, (1, 1), (1, 1), BASE)
        self.assertEqual(r, "data_unchanged_capped")

    def test_06_stall_report_empty_by_default(self):
        """读出口在属性缺失时全 getattr 兜底，不抛异常、不造伪读数。"""
        p = _liver()
        rep = p.m364_fuse_stall_report()
        self.assertEqual(rep["stall_paths"], 0)
        self.assertEqual(rep["stalls"], {})
        self.assertEqual(rep["fail_counts"], {})

    def test_07_stall_report_reflects_state(self):
        p = _liver()
        p._m364_fuse_stall = {"/成长/评估": {"ts": 2.0, "nodes": 193, "quality": 4}}
        p._fuse_fail_count = {"/成长/评估": 9, "/其它": 1}
        p._fuse_fail_fingerprint = {"/成长/评估": (193, 4)}
        p._adaptive_fuse_blocked_paths = {"/成长/评估": 123.0}
        rep = p.m364_fuse_stall_report()
        self.assertEqual(rep["stall_paths"], 1)
        self.assertEqual(rep["fail_counts"]["/成长/评估"], 9)
        self.assertEqual(rep["fingerprints"]["/成长/评估"], (193, 4))


    def test_08_placeholder_alias_rejected(self):
        """上游生成器侧：含方括号/裸「器官别名」的别名不得产出节点。"""
        from organs.brain.PulseCodeLearner import is_placeholder_alias
        for bad in ("[器官别名]", "器官别名", "", "  ", "心脏[别名]", None, 123):
            self.assertTrue(is_placeholder_alias(bad), f"应判占位符: {bad!r}")
        for ok in ("心脏", "指标采集器", "消化"):
            self.assertFalse(is_placeholder_alias(ok), f"不应判占位符: {ok!r}")

    def test_09_placeholder_block_switches_are_on(self):
        """融合侧/召回侧占位符封堵开关必须处于开启态（160/167 批已翻 True）。

        ★守不变量：若因应急回滚置 False，须同步改本例并留独立 commit，不得静默关闸。
        """
        import config
        self.assertTrue(getattr(config, "KNOWLEDGE_FUSION_SKIP_PLACEHOLDER", False))
        self.assertTrue(getattr(config, "KNOWLEDGE_RECALL_SKIP_PLACEHOLDER", False))


if __name__ == "__main__":
    unittest.main()
