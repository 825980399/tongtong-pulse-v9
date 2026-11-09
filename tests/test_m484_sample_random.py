# -*- coding: utf-8 -*-
"""484-5 门控：PulseNodePool.sample_random（烛微设计 §1.6 的 7 条 + 降级路径）。

★T0 偏差 D16：覆盖率判据 71.3% = 理论 74.3% − 3pp；90% 数学上不可能。
★用轻量假节点（SimpleNamespace）构造合成池，避免造 1.5 万个真实 PulseNode。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.mnemosyne.PulseNodePool import PulseNodePool

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _N:
    """只暴露 sample_random 会读到的字段，够真实方法的过滤/加权路径用。"""

    def __init__(self, nid, level="L3", state="active", quality="clean", blanked=False):
        self.node_id = nid
        self.evol_level = level
        self.state = state
        self.quality_flag = quality
        self._m70_blanked = blanked
        self.activation_count = 0
        self.last_activated = 123.0
        self.value = "v_" + nid
        self.keywords = ["k"]
        self.space_path = "/"


def _pool(n, level="L3", dead_ratio=0.0, inject=None):
    p = PulseNodePool(max_hot=max(n, 10), max_warm=max(n, 10))
    with p._lock:
        for i in range(n):
            nid = f"n{i}"
            node = _N(nid, level)
            if inject is not None:
                inject(i, node)
            p._hot[nid] = node
            p._level_index[level].add(nid)
        if dead_ratio:
            for j in range(int(n * dead_ratio)):
                p._level_index[level].add(f"dead{j}")
    return p


class TestSampleRandom(unittest.TestCase):
    def test_01_api_present(self):
        self.assertTrue(hasattr(PulseNodePool, "sample_random"))

    def test_02_coverage_1000x20_ge_71_3pct(self):
        """烛微修正 C：N=14718 / k=20 / m=1000，理论 74.3%，判据 ≥71.3%。"""
        p = _pool(14718)
        seen = set()
        for _ in range(1000):
            for n in p.sample_random(20, evol_level="L3"):
                seen.add(n.node_id)
        self.assertGreaterEqual(len(seen) / 14718, 0.713,
                                f"覆盖率 {len(seen) / 14718:.4f} < 71.3%")

    def test_03_coverage_2000x20_ge_88pct(self):
        p = _pool(14718)
        seen = set()
        for _ in range(2000):
            for n in p.sample_random(20, evol_level="L3"):
                seen.add(n.node_id)
        self.assertGreaterEqual(len(seen) / 14718, 0.88)

    def test_04_k_greater_than_n(self):
        p = _pool(5)
        self.assertEqual(len(p.sample_random(20, evol_level="L3")), 5)

    def test_05_dead_ids_dropped(self):
        p = _pool(100, dead_ratio=0.30)
        got = p.sample_random(20, evol_level="L3")
        self.assertEqual(len(got), 20)
        self.assertGreater(p._m484_last_sample.get("dead_dropped", 0), 0)

    def test_06_three_filters(self):
        def _inject(i, n):
            if i < 10:
                n.state = "dormant"
            elif i < 20:
                n.quality_flag = "polluted"
            elif i < 30:
                n._m70_blanked = True
        p = _pool(200, inject=_inject)
        got = p.sample_random(30, evol_level="L3")
        bad = [n for n in got if n.state == "dormant"
               or n.quality_flag == "polluted" or n._m70_blanked]
        self.assertEqual(bad, [])
        self.assertGreater(p._m484_last_sample.get("filtered_dropped", 0), 0)

    def test_07_boundaries_return_empty(self):
        p = _pool(20)
        self.assertEqual(p.sample_random(0, evol_level="L3"), [])
        self.assertEqual(p.sample_random(10, evol_level="NOPE"), [])
        empty = PulseNodePool(max_hot=10, max_warm=10)
        self.assertEqual(empty.sample_random(10, evol_level="L3"), [])

    def test_08_no_side_effects(self):
        p = _pool(50)
        before = [(n.activation_count, n.last_activated)
                  for n in p._hot.values()]
        for _ in range(100):
            p.sample_random(10, evol_level="L3")
        after = [(n.activation_count, n.last_activated)
                 for n in p._hot.values()]
        self.assertEqual(before, after)

    def test_09_bypass_stats_fields(self):
        p = _pool(50)
        p.sample_random(10, evol_level="L3")
        keys = set(p._m484_last_sample)
        self.assertTrue({"scanned", "dead_dropped", "blanked_dropped",
                         "filtered_dropped", "shortfall", "elapsed_ms"} <= keys)

    def test_10_reservoir_fallback_over_threshold(self):
        """N > M484_SAMPLE_MAX_POOL 时切蓄水池，行为不降级。"""
        old = getattr(config, "M484_SAMPLE_MAX_POOL", None)
        try:
            config.M484_SAMPLE_MAX_POOL = 100
            p = _pool(300)
            got = p.sample_random(10, evol_level="L3")
            self.assertEqual(len(got), 10)
            self.assertEqual(len({n.node_id for n in got}), 10)
        finally:
            if old is None:
                # try 内必然已赋值，delattr 不会抛；不写 try/except 以免触发静默门禁
                delattr(config, "M484_SAMPLE_MAX_POOL")
            else:
                config.M484_SAMPLE_MAX_POOL = old

    def test_11_query_semantics_untouched(self):
        """不得顺手改 query 的「哈希序前 N」既有语义。"""
        p = _pool(30)
        got = p.query(evol_level="L3", limit=10)
        self.assertEqual(len(got), 10)


if __name__ == "__main__":
    unittest.main()
