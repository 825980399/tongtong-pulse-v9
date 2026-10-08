# -*- coding: utf-8 -*-
"""第170批 C10 门控单测：冷池分位动态化驱逐（L-7）。

覆盖：
* 默认（开关关）→ 旧固定阈值 LRU 行为不变（零回归）
* 开关开 → 按 last_access 最低 20% 分位驱逐
* 批上限 CAP（每批 -1000）
* 批间间隔（≥10min）风暴防护
* 调度入口 ``_enforce_cold_cache`` 按开关路由

★测试隔离：``PulseNodePool.__new__`` 构造，桩 ``_evict_cold_node``（不写盘），绝不碰磁盘。
"""
import inspect
import os
import sys
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode  # noqa: E402
from nucleus.mnemosyne.PulseNodePool import PulseNodePool  # noqa: E402


def _mk_pool():
    p = PulseNodePool.__new__(PulseNodePool)
    import threading
    p._lock = threading.Lock()
    p._cold = {}
    p._warm = {}
    p._hot = {}
    p._cold_evicted = set()
    p._cold_storage_enabled = True
    p._last_cold_shrink_ts = 0.0
    p._max_cold_cache = 50  # 容量下限门槛（让分位触发可命中）
    # 桩 _evict_cold_node：模拟成功驱逐（不写盘）
    def _fake_evict(node):
        _nid = node.node_id
        p._cold.pop(_nid, None)
        p._cold_evicted.add(_nid)
        return True
    p._evict_cold_node = _fake_evict
    return p


def _mk_node(nid, last_activated):
    n = PulseNode.__new__(PulseNode)
    n.node_id = nid
    n.last_activated = float(last_activated)
    n.evol_level = "L1"
    return n


class TestColdPoolShrink(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        self._set("ENABLE_COLD_POOL_PERCENTILE_SHRINK", False)
        self._set("COLD_POOL_SHRINK_PERCENTILE", 0.20)
        self._set("COLD_POOL_SHRINK_BATCH_MAX", 1000)
        self._set("COLD_POOL_SHRINK_BATCH_INTERVAL_SEC", 600)

    def tearDown(self):
        for _k, _v in self._saved.items():
            setattr(config, _k, _v)

    def _set(self, name, value):
        if name not in self._saved:
            self._saved[name] = getattr(config, name, None)
        setattr(config, name, value)

    def _fill(self, p, n):
        for i in range(n):
            p._cold["n%03d" % i] = _mk_node("n%03d" % i, float(i))

    # ---- 旧行为（开关关）零回归 ----
    def test_01_lru_fallback_when_off(self):
        p = _mk_pool()
        p._max_cold_cache = 5
        self._fill(p, 10)  # last_activated 0..9
        p._enforce_cold_cache_lru()
        self.assertEqual(len(p._cold), 5)
        self.assertEqual(len(p._cold_evicted), 5)
        # 被驱逐的是 last_activated 最小（最旧）的 5 个：0..4
        for i in range(5):
            self.assertIn("n%03d" % i, p._cold_evicted)
        for i in range(5, 10):
            self.assertIn("n%03d" % i, p._cold)

    # ---- 分位驱逐：默认 20% ----
    def test_02_percentile_20pct(self):
        self._set("ENABLE_COLD_POOL_PERCENTILE_SHRINK", True)
        p = _mk_pool()
        self._fill(p, 100)  # 0..99
        p._enforce_cold_cache_percentile()
        self.assertEqual(len(p._cold_evicted), 20)  # 100*0.2
        # 驱逐的是 last_activated 最小的 0..19
        for i in range(20):
            self.assertIn("n%03d" % i, p._cold_evicted)
        for i in range(20, 100):
            self.assertIn("n%03d" % i, p._cold)

    # ---- 批上限 CAP ----
    def test_03_batch_cap(self):
        self._set("ENABLE_COLD_POOL_PERCENTILE_SHRINK", True)
        self._set("COLD_POOL_SHRINK_PERCENTILE", 1.0)  # 目标 100%
        self._set("COLD_POOL_SHRINK_BATCH_MAX", 10)
        p = _mk_pool()
        self._fill(p, 100)
        p._enforce_cold_cache_percentile()
        self.assertEqual(len(p._cold_evicted), 10)  # 被批上限截断
        self.assertGreater(len(p._cold), 0)

    # ---- 批间间隔风暴防护 ----
    def test_04_interval_storm_guard(self):
        self._set("ENABLE_COLD_POOL_PERCENTILE_SHRINK", True)
        self._set("COLD_POOL_SHRINK_BATCH_INTERVAL_SEC", 600)
        p = _mk_pool()
        self._fill(p, 100)
        p._enforce_cold_cache_percentile()
        self.assertEqual(len(p._cold_evicted), 20)
        # 紧接第二次：间隔不足 → 跳过（0 驱逐）
        p._enforce_cold_cache_percentile()
        self.assertEqual(len(p._cold_evicted), 20)  # 未增加
        # 模拟 10min 已过
        p._last_cold_shrink_ts = time.time() - 700
        p._enforce_cold_cache_percentile()
        # 第二次批次针对剩余 80 的 20% = 16（向容量下限收敛，非恒 20）
        self.assertEqual(len(p._cold_evicted), 36)  # 再驱逐一批

    # ---- 调度入口路由 ----
    def test_05_dispatcher_routes_by_switch(self):
        # 关 → LRU 路径（用 _max_cold_cache 触发）
        p_off = _mk_pool()
        p_off._max_cold_cache = 3
        self._fill(p_off, 10)
        p_off._enforce_cold_cache()
        self.assertEqual(len(p_off._cold), 3)  # LRU 削减到阈值
        # 开 → 分位路径
        self._set("ENABLE_COLD_POOL_PERCENTILE_SHRINK", True)
        p_on = _mk_pool()
        self._fill(p_on, 100)
        p_on._enforce_cold_cache()
        self.assertEqual(len(p_on._cold_evicted), 20)  # 20% 分位

    # ---- 结构守卫：分发入口引用开关 + 两个子策略 ----
    def test_06_dispatcher_references_switch(self):
        src = inspect.getsource(PulseNodePool._enforce_cold_cache)
        self.assertIn("ENABLE_COLD_POOL_PERCENTILE_SHRINK", src)
        self.assertIn("_enforce_cold_cache_percentile", src)
        self.assertIn("_enforce_cold_cache_lru", src)


if __name__ == "__main__":
    unittest.main()


