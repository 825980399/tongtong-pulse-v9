# -*- coding: utf-8 -*-
"""主线第66批 T1/P1：内存增长分析器 + 运行时内存监控 门控单测（6例）。

覆盖：分析器（进程内存快照 / top 对象类型 / 报告结构 / 快照对比定位增长源）+
      运行时监控（内存用量形状 / 增长率估算 / 增强告警触发与自动GC闸门）。

隔离：纯函数/单例内省，不写生产 data/；自动GC 受 config 闸门控制（默认关→零回归）。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.runtime_metrics import RuntimeMetrics  # noqa: E402
from tools.memory_growth_analyzer import (  # noqa: E402
    analyze_top_object_types,
    build_report,
    compare_snapshots,
    get_process_memory,
)


class TestMemoryAnalyzer(unittest.TestCase):
    def test_10_process_memory_shape(self):
        """get_process_memory 返回 rss/vms/percent 三项数值。"""
        _m = get_process_memory()
        for _k in ("rss_mb", "vms_mb", "percent"):
            self.assertIn(_k, _m)
            self.assertIsInstance(_m[_k], (int, float))

    def test_11_top_object_types_sorted(self):
        """top 对象类型按实例数降序。"""
        _t = analyze_top_object_types(limit=10)
        self.assertIsInstance(_t, list)
        self.assertGreater(len(_t), 0)
        for _r in _t:
            self.assertIn("type", _r)
            self.assertIn("count", _r)
        _counts = [r["count"] for r in _t]
        self.assertEqual(_counts, sorted(_counts, reverse=True))

    def test_12_build_report_structure(self):
        """build_report 结构完整且标记 read_only。"""
        _rep = build_report(limit=5)
        for _k in ("process_memory", "top_object_types", "gc_collections", "read_only"):
            self.assertIn(_k, _rep)
        self.assertTrue(_rep["read_only"], "分析器必须只读，不写生产数据")

    def test_13_compare_snapshots_growth(self):
        """快照对比：定位增长最快的对象类型（delta 降序）。"""
        _prev = {"process_memory": {"rss_mb": 100.0, "vms_mb": 200.0},
                 "top_object_types": [{"type": "dict", "count": 10},
                                      {"type": "list", "count": 5}]}
        _cur = {"process_memory": {"rss_mb": 150.0, "vms_mb": 260.0},
                "top_object_types": [{"type": "dict", "count": 40},
                                     {"type": "list", "count": 5}]}
        _cmp = compare_snapshots(_prev, _cur)
        self.assertEqual(_cmp["rss_delta_mb"], 50.0)
        self.assertEqual(_cmp["delta_top_types"][0]["type"], "dict")
        self.assertEqual(_cmp["delta_top_types"][0]["delta"], 30)


class TestRuntimeMemoryMonitor(unittest.TestCase):
    def setUp(self):
        self.m = RuntimeMetrics()
        self.m._history.clear()

    def test_14_memory_usage_and_growth_shape(self):
        """运行时内存用量形状 + 空历史时增长率=0.0。"""
        _u = self.m.get_memory_usage()
        for _k in ("rss_mb", "vms_mb", "percent"):
            self.assertIn(_k, _u)
        self.assertIsInstance(_u[_k], (int, float))
        self.assertEqual(self.m.get_memory_growth_rate(), 0.0,
                         "无内存历史样本时增长率须为 0.0")

    def test_15_alarm_detailed_and_autogc(self):
        """增强告警：正常不触发；超阈值+开GC闸门→触发并自动回收。"""
        _d = self.m.check_memory_alarm_detailed()
        self.assertIsInstance(_d, dict)
        for _k in ("triggered", "percent", "rss_mb", "growth_mb_per_min", "gc_triggered"):
            self.assertIn(_k, _d)
        self.assertFalse(_d["triggered"], "小进程默认不应触发内存告警")
        # 模拟高使用率 + 开启自动GC闸门
        self.m.get_memory_usage = lambda: {"rss_mb": 9999.0, "vms_mb": 9999.0, "percent": 95.0}
        _old = getattr(config, "ENABLE_MEMORY_AUTO_GC", None)
        config.ENABLE_MEMORY_AUTO_GC = True
        try:
            _d2 = self.m.check_memory_alarm_detailed(auto_gc=True)
            self.assertTrue(_d2["triggered"])
            self.assertTrue(_d2["gc_triggered"], "开启闸门后应触发自动GC")
        finally:
            if _old is None:
                delattr(config, "ENABLE_MEMORY_AUTO_GC")
            else:
                config.ENABLE_MEMORY_AUTO_GC = _old


if __name__ == "__main__":
    unittest.main(verbosity=2)
