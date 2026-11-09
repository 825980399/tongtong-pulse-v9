# -*- coding: utf-8 -*-
"""484-7 门控：潜意识取种接线（烛微设计 §2.6 的 3 条 + 埋点出口）。

开关开 ⇒ sample_random(10, L3) + sample_random(20, L2)；
开关关 ⇒ 逐字回 query(L3,10) + query(L2,20)；候选为空返回 None 不抛。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from organs.brain.PulseSubconscious import PulseSubconscious

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_text(path):
    """统一用上下文管理器读源码：避免 SIM115 / UP020 净增。"""
    with open(path, encoding="utf-8") as _fh:
        return _fh.read()


class _Switch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = (hasattr(config, k), getattr(config, k, None))
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, (existed, val) in self._old.items():
            if existed:
                setattr(config, k, val)
            else:
                # __enter__ 必然已 setattr，delattr 不会抛；不写 try/except
                # 以免被 cw2_t2e 静默 except 门禁判为新增静默 handler。
                delattr(config, k)
        return False


class _N:
    def __init__(self, nid):
        self.node_id = nid
        self.value = "知识_" + nid
        self.keywords = ["架构"]
        self.space_path = "/知识/架构"
        self.evol_level = "L3"


class _Pool:
    def __init__(self, nodes):
        self.nodes = nodes
        self.query_calls = []
        self.sample_calls = []

    def query(self, evol_level=None, limit=100):
        self.query_calls.append((evol_level, limit))
        return self.nodes[:limit]

    def sample_random(self, k, evol_level=None, *, weight="uniform", **kw):
        self.sample_calls.append((k, evol_level, weight))
        return self.nodes[:k]


def _mk(pool, nodes=None):
    obj = PulseSubconscious.__new__(PulseSubconscious)
    obj.node_pool = pool
    obj._log = lambda *a, **kw: None
    obj._clean_topic = lambda q: q
    obj._last_self_question = None
    obj._last_self_answer = None
    obj._generate_deep_followup = lambda *a, **kw: None
    obj._seed_log = []
    obj._m484_record_seed = lambda **kw: obj._seed_log.append(kw)
    return obj


class TestSeedSource(unittest.TestCase):
    def test_01_switch_on_uses_sample_random(self):
        pool = _Pool([_N(f"n{i}") for i in range(30)])
        obj = _mk(pool)
        with _Switch(ENABLE_M484_RANDOM_SEED=True, M484_SEED_WEIGHT="uniform"):
            out = obj._generate_self_question()
        self.assertIsInstance(out, str)
        self.assertEqual(pool.sample_calls,
                         [(10, "L3", "uniform"), (20, "L2", "uniform")])
        self.assertEqual(pool.query_calls, [])
        self.assertTrue(any(r.get("exit_reason") == "ok" for r in obj._seed_log))

    def test_02_switch_off_uses_legacy_query(self):
        pool = _Pool([_N(f"n{i}") for i in range(30)])
        obj = _mk(pool)
        with _Switch(ENABLE_M484_RANDOM_SEED=False):
            out = obj._generate_self_question()
        self.assertIsInstance(out, str)
        self.assertEqual(pool.query_calls, [("L3", 10), ("L2", 20)])
        self.assertEqual(pool.sample_calls, [])

    def test_03_empty_candidates_returns_none(self):
        pool = _Pool([])
        obj = _mk(pool)
        with _Switch(ENABLE_M484_RANDOM_SEED=True):
            self.assertIsNone(obj._generate_self_question())
        self.assertTrue(any(r.get("exit_reason") == "empty" for r in obj._seed_log))

    def test_04_no_pool_records_exit_reason(self):
        obj = _mk(None)
        with _Switch(ENABLE_M484_RANDOM_SEED=True):
            self.assertIsNone(obj._generate_self_question())
        self.assertTrue(any(r.get("exit_reason") == "no_pool" for r in obj._seed_log))

    def test_05_weighted_formula_implemented_but_default_uniform(self):
        """2.3：公式实现但默认 uniform；开启后 weight 透传到 sample_random。"""
        self.assertEqual(getattr(config, "M484_SEED_WEIGHT", None), "uniform")
        from nucleus.mnemosyne.PulseNodePool import PulseNodePool
        src = _read_text(os.path.join(_ROOT, "nucleus", "mnemosyne",
                                    "PulseNodePool.py"))
        self.assertIn("log_activation", src)
        pool = _Pool([_N(f"n{i}") for i in range(30)])
        obj = _mk(pool)
        with _Switch(ENABLE_M484_RANDOM_SEED=True, M484_SEED_WEIGHT="log_activation"):
            obj._generate_self_question()
        self.assertEqual(pool.sample_calls[0][2], "log_activation")
        self.assertIsNotNone(PulseNodePool)


if __name__ == "__main__":
    unittest.main()
