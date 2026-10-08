# -*- coding: utf-8 -*-
"""169批 C11 门控单测：冷池驱逐/召回运行留痕 + 计数可查。

覆盖 PulseNodePool._m169_cold_trace 与其接线：
  - 驱逐成功/失败均留痕且 evict_count 只在成功时递增；
  - 召回命中/未命中留痕且 recall_count 只在成功时递增；
  - get_cold_stats 暴露 evict_count/recall_count（新键，不改既有键）；
  - 观测代码自身异常不得抛出（留痕不拖垮主链路）。
"""
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.mnemosyne.PulseNodePool import PulseNodePool  # noqa: E402


def _mk_pool():
    p = PulseNodePool.__new__(PulseNodePool)
    p._cold_stats = {"recall_hits": 0, "recall_misses": 0}
    return p


class TestColdTraceCounters:
    def test_evict_success_increments_and_logs(self, caplog):
        p = _mk_pool()
        with caplog.at_level(logging.INFO, logger="pulse.module.PulseNodePool"):
            p._m169_cold_trace("evict", "n1", True, "evol_level=L2")
        assert p._cold_stats["evict_count"] == 1
        assert any("冷池evict" in r.getMessage() for r in caplog.records)
        assert any("n1" in r.getMessage() for r in caplog.records)

    def test_evict_failure_not_counted_but_logged(self, caplog):
        p = _mk_pool()
        with caplog.at_level(logging.INFO, logger="pulse.module.PulseNodePool"):
            p._m169_cold_trace("evict", "n2", False, "写盘失败")
        assert "evict_count" not in p._cold_stats
        assert any("ok=False" in r.getMessage() for r in caplog.records)

    def test_recall_success_and_failure(self, caplog):
        p = _mk_pool()
        with caplog.at_level(logging.INFO, logger="pulse.module.PulseNodePool"):
            p._m169_cold_trace("recall", "n3", True, "侧车索引命中")
            p._m169_cold_trace("recall", "n4", False, "批量回退未命中")
        assert p._cold_stats["recall_count"] == 1
        assert any("n4" in r.getMessage() and "ok=False" in r.getMessage()
                   for r in caplog.records)

    def test_missing_cold_stats_is_recreated(self):
        p = PulseNodePool.__new__(PulseNodePool)
        p._m169_cold_trace("evict", "n5", True)
        assert p._cold_stats["evict_count"] == 1

    def test_trace_never_raises(self):
        """观测代码自身异常（如 _cold_stats 不可写）不得抛。"""
        p = PulseNodePool.__new__(PulseNodePool)

        class _Bad:
            def get(self, *a, **k):
                raise RuntimeError("boom")

            def __setitem__(self, *a):
                raise RuntimeError("boom")

        p._cold_stats = _Bad()
        p._m169_cold_trace("evict", "n6", True)  # 不抛即通过

    def test_get_cold_stats_exposes_new_keys(self):
        import threading
        p = PulseNodePool.__new__(PulseNodePool)
        p._lock = threading.RLock()
        p._cold = {}
        p._cold_evicted = set()
        p._cold_storage_enabled = True
        p._cold_stats = {"recall_hits": 3, "recall_misses": 1, "evict_count": 5,
                         "recall_count": 4}
        s = p.get_cold_stats()
        assert s["evict_count"] == 5
        assert s["recall_count"] == 4
        # 既有键零变化
        assert s["recall_hits"] == 3
        assert s["recall_misses"] == 1
        assert s["recall_hit_rate"] == 0.75


class TestColdTraceWiring:
    def test_evict_and_recall_methods_call_trace(self):
        """结构守卫：_evict_cold_node 与 _recall_cold_node 均已接线留痕。"""
        import inspect
        ev = inspect.getsource(PulseNodePool._evict_cold_node)
        assert ev.count("_m169_cold_trace") >= 3, "驱逐三条出口应各留痕"
        rc = inspect.getsource(PulseNodePool._recall_cold_node)
        assert rc.count("_m169_cold_trace") >= 2, "召回命中/未命中应各留痕"