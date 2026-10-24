# -*- coding: utf-8 -*-
"""364 批 B 组验收（B1 GC 接线开启 / B2 结构重扫 / B3 自适应 TTL / B4 健康排行接线 / B5 fail-closed）。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel: str) -> str:
    return open(os.path.join(_ROOT, rel), encoding="utf-8").read()


class TestB1MemoryAutoGC(unittest.TestCase):
    def _metrics(self):
        from nucleus.runtime_metrics import get_runtime_metrics
        return get_runtime_metrics()

    def test_01_switch_on(self):
        self.assertTrue(config.ENABLE_MEMORY_AUTO_GC, "B1 甲1：三刀后保守开启")

    def test_02_rss_threshold_wired(self):
        """★366 复核点：`MEMORY_AUTO_GC_RSS_MB=8192` 原先读了没人用。"""
        r = self._metrics()
        _u, _g = r.get_memory_usage, r.get_memory_growth_rate
        try:
            r.get_memory_usage = lambda: {"percent": 10.0, "rss_mb": 9000.0}
            r.get_memory_growth_rate = lambda: 0.0
            d = r.check_memory_alarm_detailed(auto_gc=False)
        finally:
            r.get_memory_usage, r.get_memory_growth_rate = _u, _g
        self.assertTrue(d["triggered"], "RSS≥8GB 必须触发告警")
        self.assertIn("rss", d["triggers"])
        self.assertEqual(d["rss_threshold_mb"], 8192.0)

    def test_03_gc_records_pause_and_count(self):
        """GC 是 STW：必须留回收对象数与暂停时长。"""
        r = self._metrics()
        _u, _g = r.get_memory_usage, r.get_memory_growth_rate
        try:
            r.get_memory_usage = lambda: {"percent": 95.0, "rss_mb": 9000.0}
            r.get_memory_growth_rate = lambda: 0.0
            d = r.check_memory_alarm_detailed(auto_gc=True)
        finally:
            r.get_memory_usage, r.get_memory_growth_rate = _u, _g
        self.assertTrue(d["gc_triggered"])
        last = d.get("auto_gc_last") or {}
        self.assertIn("collected", last)
        self.assertIn("pause_ms", last)
        self.assertIn("percent", last["triggers"])

    def test_04_call_site_passes_auto_gc(self):
        s = _src("organs/core/PulseMetricsCollector.py")
        self.assertIn("check_memory_alarm_detailed(auto_gc=True)", s,
                      "调用点必须传 auto_gc=True（366 复核：未传 ⇒ 半接线）")
        self.assertIn('"auto_gc_last": _al.get("auto_gc_last")', s)


class TestB2StructureRescan(unittest.TestCase):
    def test_01_switch_on(self):
        self.assertTrue(config.ENABLE_STRUCTURE_RESCAN)

    def test_02_elapsed_instrumented(self):
        s = _src("nucleus/self_awareness/SelfAwarenessEngine.py")
        self.assertIn("耗时=%.1fms", s)
        self.assertIn("def m364_last_rescan()", s)
        self.assertIn('"elapsed_ms": _m364_elapsed_ms', s)


class TestB3AdaptiveTTL(unittest.TestCase):
    def test_01_switch_on(self):
        self.assertTrue(config.ENABLE_ORGAN_SCAN_ADAPTIVE_TTL)

    def test_02_decision_recorded(self):
        from nucleus.self_inspector import SelfInspector, m364_ttl_events
        p = SelfInspector.__new__(SelfInspector)
        p._scan_cache_ttl = 300.0
        before = len(m364_ttl_events())
        ttl = p._get_adaptive_cache_ttl()
        self.assertIsInstance(float(ttl), float)
        self.assertGreaterEqual(len(m364_ttl_events()), min(before + 1, 1),
                                "TTL 决策必须留痕（开闸后要能回答「为什么是 600s」）")
        ev = m364_ttl_events()[-1]
        for k in ("ttl", "cpu", "queue_depth", "cpu_band"):
            self.assertIn(k, ev)

    def test_03_export_is_copy(self):
        from nucleus.self_inspector import m364_ttl_events
        a = m364_ttl_events()
        if a:
            a[0]["ttl"] = -1
            self.assertNotEqual(m364_ttl_events()[0]["ttl"], -1)


class TestB4LeaderboardWiring(unittest.TestCase):
    def test_01_switch_still_off(self):
        """★B4 第二刀（开灯）**未执行**：collect 实测 ~2.0s/次，不满足「不拖长心跳」前置。"""
        self.assertFalse(getattr(config, "ENABLE_ORGAN_HEALTH_LEADERBOARD", False),
                         "前置不满足 ⇒ 不得先开灯")

    def test_02_heartbeat_hook_present(self):
        s = _src("organs/core/PulseMetricsCollector.py")
        self.assertIn("def _m364_leaderboard_tick(self)", s)
        self.assertIn('_out["m364_leaderboard"] = self._m364_leaderboard_tick()', s)

    def test_03_zero_behavior_when_off(self):
        from organs.core.PulseMetricsCollector import PulseMetricsCollector
        p = PulseMetricsCollector.__new__(PulseMetricsCollector)
        p._log = lambda *a, **k: None
        p._beat_count = 0
        r = p._m364_leaderboard_tick()
        self.assertEqual(r.get("status"), "ok")
        self.assertEqual(r.get("cost_ms"), 0.0, "开关关闭时 on_heartbeat 应零行为")

    def test_04_throttled(self):
        from organs.core.PulseMetricsCollector import PulseMetricsCollector
        p = PulseMetricsCollector.__new__(PulseMetricsCollector)
        p._log = lambda *a, **k: None
        p._beat_count = 10
        p._m364_lb_beat = 10
        self.assertEqual(p._m364_leaderboard_tick().get("status"), "throttled")

    def test_05_organ_coverage_57(self):
        from nucleus.cognitive.organ_inventory import organ_names_all
        names = organ_names_all()
        self.assertEqual(len(names), 57, "注册前静态确认 57 器官覆盖不缺")
        self.assertEqual(len(set(names)), 57)


class TestB5FailClosed(unittest.TestCase):
    def test_01_missing_key_is_off(self):
        from organs.brain.PulseInnerWorld import PulseInnerWorld
        _had = hasattr(config, "ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION")
        _old = getattr(config, "ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION", None)
        try:
            if _had:
                delattr(config, "ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION")
            self.assertFalse(PulseInnerWorld._m31_operational_admission_on(),
                             "键缺失必须按「关」处理（fail-closed）")
        finally:
            if _had:
                config.ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION = _old

    def test_02_true_key_is_on(self):
        from organs.brain.PulseInnerWorld import PulseInnerWorld
        _had = hasattr(config, "ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION")
        _old = getattr(config, "ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION", None)
        try:
            config.ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION = True
            self.assertTrue(PulseInnerWorld._m31_operational_admission_on())
        finally:
            if _had:
                config.ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION = _old
            else:
                delattr(config, "ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION")

    def test_03_exception_branch_returns_false(self):
        s = _src("organs/brain/PulseInnerWorld.py")
        self.assertIn('"ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION", False', s)
        i = s.find("def _m31_operational_admission_on")
        seg = s[i:i + 900]
        self.assertIn("return False", seg, "读异常必须返回 False（fail-closed）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
