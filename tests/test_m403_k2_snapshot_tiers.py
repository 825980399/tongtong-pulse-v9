# -*- coding: utf-8 -*-
"""★403 刀2 判据：可观测快照分频（热 5s / 温 60s / 冷 300s）。

守什么
------
1. **切片守恒**：hot+warm+cold 必须等于原快照键集，一个键都不许凭空消失。
2. **热文件不残留温冷键**：留了就等于"拆了但没拆"，体积也不会降。
3. **读方必须合并**：三处读方（health_ui / runtime_state_writer / expectation_ledger）
   都得走 `read_merged`，否则某一块会静默变空——这是本刀最容易埋的坑。
4. **降级不填 0**：hourly 缺失时返回热文件 + `merge_source=hot_only`，不把温冷键补成 0。
5. **默认行为不变**：`skip_slow=False`（既有调用方/测试）必须每次全量现算。
"""
import ast
import importlib.util
import io
import json
import os
import shutil
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    _p = os.path.join(_ROOT, rel)
    _spec = importlib.util.spec_from_file_location(name, _p)
    _m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    return _m


ST = _load("m403_snapshot_tiers", os.path.join("utils", "snapshot_tiers.py"))


def _full_snapshot():
    _d = {"timestamp": 1.0, "beat_count": 3, "proc_gen": "g1", "pulse": {"emitted": 1},
          "info_field": {"published": 2}, "memory_pressure": {"percent": 40.0},
          "heartbeat": {"interval": 5.0}}
    for _k in ST.WARM_KEYS:
        _d[_k] = {"v": _k}
    for _k in ST.COLD_KEYS:
        _d[_k] = {"v": _k}
    return _d


class TestConstants(unittest.TestCase):
    def test_01_tier_partition_declared(self):
        self.assertEqual(ST.WARM_INTERVAL_S, 60.0)
        self.assertEqual(ST.COLD_INTERVAL_S, 300.0)
        self.assertEqual(len(ST.WARM_KEYS), 6)
        self.assertEqual(len(ST.COLD_KEYS), 3)
        self.assertEqual(set(ST.WARM_KEYS) & set(ST.COLD_KEYS), set())

    def test_02_hourly_path_same_dir(self):
        p = ST.hourly_path(os.path.join("a", "b", "health_snapshot.json"))
        self.assertEqual(os.path.basename(p), "health_snapshot_hourly.json")
        self.assertEqual(os.path.dirname(p), os.path.abspath(os.path.join("a", "b")))


class TestSplit(unittest.TestCase):
    def test_10_partition_is_lossless(self):
        full = _full_snapshot()
        hot, warm, cold = ST.split(full)
        self.assertEqual(set(hot) | set(warm) | set(cold), set(full))
        self.assertEqual(set(hot) & set(warm), set())
        self.assertEqual(set(hot) & set(cold), set())

    def test_11_warm_cold_complete(self):
        _hot, warm, cold = ST.split(_full_snapshot())
        self.assertEqual(set(warm), set(ST.WARM_KEYS))
        self.assertEqual(set(cold), set(ST.COLD_KEYS))

    def test_12_hot_has_no_warm_cold(self):
        hot, _w, _c = ST.split(_full_snapshot())
        for _k in ST.valid_keys():
            self.assertNotIn(_k, hot, "热文件不得残留 %s" % _k)

    def test_13_build_hot_metadata(self):
        full = _full_snapshot()
        hot = ST.build_hot(full, warm_at=100.0, cold_at=200.0, now=230.0)
        t = hot["tiers"]
        self.assertEqual(t["schema"], ST.SCHEMA)
        self.assertEqual(t["warm_age_s"], 130.0)
        self.assertEqual(t["cold_age_s"], 30.0)
        self.assertEqual(t["warm_interval_s"], 60.0)
        self.assertEqual(t["cold_interval_s"], 300.0)
        self.assertEqual(t["hourly_file"], "health_snapshot_hourly.json")
        self.assertNotIn("organs", hot)

    def test_14_build_hourly_shape(self):
        _h, warm, cold = ST.split(_full_snapshot())
        rec = ST.build_hourly(warm, cold, 100.0, 200.0, now=500.0)
        self.assertEqual(rec["schema"], ST.SCHEMA)
        self.assertEqual(rec["warm_age_s"], 400.0)
        self.assertEqual(set(rec["warm"]), set(ST.WARM_KEYS))
        self.assertEqual(set(rec["cold"]), set(ST.COLD_KEYS))

    def test_15_shrinks_by_construction(self):
        full = _full_snapshot()
        hot = ST.build_hot(full, 1.0, 1.0, now=1.0)
        self.assertLess(len(hot), len(full), "热文件键数必须少于全量")


class TestReadMerged(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m403k2_")
        self.hp = os.path.join(self.d, "health_snapshot.json")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, obj, path=None):
        with io.open(path or self.hp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False)

    def test_20_merge_restores_every_key(self):
        full = _full_snapshot()
        _hot, warm, cold = ST.split(full)
        self._write(ST.build_hot(full, 10.0, 10.0, now=10.0))
        self._write(ST.build_hourly(warm, cold, 10.0, 10.0, now=10.0), ST.hourly_path(self.hp))
        merged = ST.read_merged(self.hp)
        self.assertEqual(set(ST.valid_keys()) - set(merged), set())
        self.assertEqual(merged["tiers"]["merge_source"], "hot+hourly")

    def test_21_missing_hourly_degrades_not_zero(self):
        full = _full_snapshot()
        self._write(ST.build_hot(full, 10.0, 10.0, now=10.0))
        merged = ST.read_merged(self.hp)
        self.assertEqual(merged["tiers"]["merge_source"], "hot_only")
        for _k in ST.valid_keys():
            self.assertNotIn(_k, merged, "降级时不得把 %s 填成 0/空壳" % _k)

    def test_22_old_format_passthrough(self):
        """旧格式（无 tiers 段）必须原样返回——滚动升级期间不能把老快照读空。"""
        old = {"timestamp": 1.0, "organs": {"total": 5}, "knowledge_evolution": {"l1_count": 1}}
        self._write(old)
        self.assertEqual(ST.read_merged(self.hp), old)

    def test_23_missing_file(self):
        self.assertEqual(ST.read_merged(os.path.join(self.d, "nope.json")), {})

    def test_24_bad_hourly_json_falls_back(self):
        full = _full_snapshot()
        self._write(ST.build_hot(full, 10.0, 10.0, now=10.0))
        with io.open(ST.hourly_path(self.hp), "w", encoding="utf-8", newline="\n") as f:
            f.write("{broken")
        merged = ST.read_merged(self.hp)
        self.assertEqual(merged["tiers"]["merge_source"], "hot_only")


class TestCollectorTiering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from organs.core.PulseMetricsCollector import PulseMetricsCollector
        cls.C = PulseMetricsCollector()

    def test_30_default_mode_still_full(self):
        snap = self.C._build_observability_snapshot()
        for _k in ST.valid_keys():
            self.assertIn(_k, snap, "默认模式必须仍产出 %s" % _k)

    def test_31_skip_slow_caches_within_ttl(self):
        c = self.C
        c._m403_warm_at = 0.0
        c._m403_warm_cache = {}
        c._m403_cold_at = 0.0
        c._m403_cold_cache = {}
        c._build_observability_snapshot(skip_slow=True)
        t1 = c._m403_warm_at
        c._build_observability_snapshot(skip_slow=True)
        self.assertEqual(c._m403_warm_at, t1, "60s 内不得重算温档")

    def test_32_refresh_marks_dirty(self):
        c = self.C
        c._m403_warm_at = 0.0
        c._m403_warm_cache = {}
        c._m403_tier_dirty = False
        c._build_observability_snapshot(skip_slow=True)
        self.assertTrue(c._m403_tier_dirty, "温档刷新后必须置 dirty，否则 hourly 永不落盘")

    def test_33_write_tiered_two_files(self):
        c = self.C
        d = tempfile.mkdtemp(prefix="m403k2w_")
        try:
            hp = os.path.join(d, "health_snapshot.json")
            c._m403_warm_at = 0.0
            c._m403_cold_at = 0.0
            c._m403_warm_cache = {}
            c._m403_cold_cache = {}
            c._m403_tier_dirty = True
            snap = c._build_observability_snapshot(skip_slow=True)
            ok = c._m403_write_tiered(hp, snap)
            self.assertTrue(ok)
            self.assertTrue(os.path.exists(hp))
            self.assertTrue(os.path.exists(ST.hourly_path(hp)))
            hot = json.load(io.open(hp, encoding="utf-8"))
            self.assertIn("tiers", hot)
            for _k in ST.valid_keys():
                self.assertNotIn(_k, hot)
            self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_34_hot_file_smaller(self):
        c = self.C
        d = tempfile.mkdtemp(prefix="m403k2s_")
        try:
            hp = os.path.join(d, "health_snapshot.json")
            c._m403_warm_at = 0.0
            c._m403_cold_at = 0.0
            c._m403_warm_cache = {}
            c._m403_cold_cache = {}
            snap = c._build_observability_snapshot(skip_slow=True)
            full_bytes = len(json.dumps(snap, ensure_ascii=False).encode("utf-8"))
            c._m403_write_tiered(hp, snap)
            hot_bytes = os.path.getsize(hp)
            self.assertLess(hot_bytes, full_bytes,
                            "热文件(%d) 应小于全量(%d)" % (hot_bytes, full_bytes))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestReadersMerged(unittest.TestCase):
    def _calls(self, rel, func_name=None):
        src = io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()
        tree = ast.parse(src)
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = getattr(f, "id", None) or getattr(f, "attr", None)
                if name in ("read_merged", "_merged_snapshot", "_m403_merged"):
                    hits.append((func_name, name, node.lineno))
        return hits

    def test_40_health_ui_merges(self):
        self.assertTrue(self._calls(os.path.join("functions", "health_ui.py")),
                        "health_ui 出口必须合并温冷块")

    def test_41_state_writer_merges(self):
        self.assertTrue(self._calls(os.path.join("functions", "runtime_state_writer.py")),
                        "runtime_state_writer 必须读合并视图")

    def test_42_expectation_ledger_merges(self):
        self.assertTrue(self._calls(os.path.join("nucleus", "world_model",
                                                 "expectation_ledger.py")),
                        "expectation_ledger 必须读合并视图")

    def test_43_no_reverse_import(self):
        """utils.snapshot_tiers 不得反向 import 器官/皮层（保持纯工具）。"""
        src = io.open(os.path.join(_ROOT, "utils", "snapshot_tiers.py"), encoding="utf-8").read()
        for _bad in ("organs.", "nucleus.field", "nucleus.pulse"):
            self.assertNotIn("import " + _bad, src)


class TestConfigWiring(unittest.TestCase):
    def test_50_intervals_match_ticket(self):
        """票面：热 5s / 温 60s / 冷 300s。"""
        self.assertEqual(ST.WARM_INTERVAL_S, 60.0)
        self.assertEqual(ST.COLD_INTERVAL_S, 300.0)

    def test_51_pmc_calls_skip_slow_in_write_paths(self):
        src = io.open(os.path.join(_ROOT, "organs", "core",
                                   "PulseMetricsCollector.py"), encoding="utf-8").read()
        self.assertGreaterEqual(src.count("_build_observability_snapshot(skip_slow=True)"), 2,
                                "两条写盘路径（collect / heartbeat）都要走分频")
        self.assertGreaterEqual(src.count("_m403_write_tiered("), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
