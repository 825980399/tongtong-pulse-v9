# -*- coding: utf-8 -*-
"""484-6 门控：取种多样性埋点（烛微设计 §3.6 的 4 条 + 测试隔离）。

★T0 偏差 D15：取种路径今天 0 命中 ⇒ 必须记 exit_reason / attempts，
防止「0 行日志 = 多样性完美」的假通过。
"""
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

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


class TestSeedDiversity(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(_ROOT, "tmp", "_m484_div_test")
        if os.path.isdir(self.tmp):
            shutil.rmtree(self.tmp, ignore_errors=True)
        os.makedirs(self.tmp, exist_ok=True)
        import nucleus.m484_diversity as _m
        self.m = _m

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _record(self, **kw):
        args = {
            "out_dir": self.tmp, "l3_n": 10, "l2_n": 20,
            "ids": [f"n{i}" for i in range(30)], "chosen": "n7",
            "pool": {"hot": 100, "warm": 20, "cold": 3},
            "filters": {"state": 0, "quality": 0, "blanked": 0, "dead": 0},
            "shortfall": False, "weighted": False, "elapsed_ms": 0.12,
            "exit_reason": "ok", "proc_gen": "acc",
        }
        args.update(kw)
        return self.m.record_seed(**args)

    def test_01_module_and_exit_reasons(self):
        self.assertIsNotNone(self.m)
        self.assertEqual(set(self.m.EXIT_REASONS),
                         {"ok", "no_pool", "empty", "clean_reject", "deep_followup"})

    def test_02_16_fields_written(self):
        row = self._record()
        self.assertIsInstance(row, dict)
        want = {"ts", "proc_gen", "type", "caller", "l3_n", "l2_n", "total_n",
                "ids", "fingerprint", "chosen", "pool", "filters", "shortfall",
                "weighted", "elapsed_ms", "exit_reason"}
        self.assertTrue(want <= set(row), f"缺字段: {want - set(row)}")
        self.assertEqual(row["total_n"], 30)
        self.assertEqual(row["caller"], "subconscious.self_question")
        self.assertEqual(len(row["fingerprint"]), 16)
        files = [f for f in os.listdir(self.tmp) if f.startswith("seed_diversity_")]
        self.assertEqual(len(files), 1)
        lines = _read_text(os.path.join(self.tmp, files[0])).splitlines()
        self.assertEqual(json.loads(lines[-1])["fingerprint"], row["fingerprint"])

    def test_03_all_exit_reasons_recorded(self):
        for reason in self.m.EXIT_REASONS:
            row = self._record(exit_reason=reason)
            self.assertIsInstance(row, dict)
            self.assertEqual(row["exit_reason"], reason)
        files = [f for f in os.listdir(self.tmp) if f.startswith("seed_diversity_")]
        rows = [json.loads(l) for l in
                _read_text(os.path.join(self.tmp, files[0])).splitlines()
                if l.strip()]
        self.assertGreaterEqual(len(rows), 5)

    def test_04_day_open_and_fingerprint_distinct(self):
        self._record(ids=[f"a{i}" for i in range(30)])
        self._record(ids=[f"b{i}" for i in range(30)])
        files = [f for f in os.listdir(self.tmp) if f.startswith("seed_diversity_")]
        rows = [json.loads(l) for l in
                _read_text(os.path.join(self.tmp, files[0])).splitlines()
                if l.strip()]
        seeds = [r for r in rows if r["type"] == "seed"]
        self.assertEqual(len(seeds), 2)
        self.assertEqual(len({r["fingerprint"] for r in seeds}), 2,
                         "不同候选集必须产生不同指纹")
        opens = [r for r in rows if r["type"] == "day_open"]
        self.assertEqual(len(opens), 1)

    def test_05_switch_off_writes_nothing(self):
        with _Switch(M484_DIVERSITY_LOG=False):
            row = self._record()
            self.assertIsNone(row)
        files = [f for f in os.listdir(self.tmp) if f.startswith("seed_diversity_")]
        self.assertEqual(files, [])

    def test_06_no_out_dir_in_test_env_never_writes_production(self):
        """不注入 out_dir 且在 pytest 内 ⇒ 拒写生产 data/（第43批铁律）。"""
        row = self.m.record_seed(exit_reason="ok", l3_n=0, l2_n=0)
        self.assertIsNone(row)
        prod = os.path.join(_ROOT, "data", "monitor")
        if os.path.isdir(prod):
            hits = [f for f in os.listdir(prod) if f.startswith("seed_diversity_")]
            self.assertEqual(hits, [], "测试污染了生产 data/monitor")


if __name__ == "__main__":
    unittest.main()
