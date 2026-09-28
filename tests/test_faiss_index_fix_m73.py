# -*- coding: utf-8 -*-
"""主线第73批测试：FAISS 索引构建修复 + 性能验证 + 口径确认 + 双读一致性比对。

覆盖 T1（FAISS 索引自动构建修复）、T2（benchmark 可用性）、T3（snapshot 口径）、
T4（双读一致性实时比对）。所有新增代码默认关闭，测试中按需开启开关并注入 mock。
"""
import os
import sys
import time

import numpy as np
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.vector_store.faiss_store import FAISSVectorStore

try:
    import faiss  # noqa: F401
    _FAISS_OK = True
except Exception:
    _FAISS_OK = False


def _make_store(dim=8, idx_type="FlatL2"):
    return FAISSVectorStore(dimension=dim, index_type=idx_type, batch_size=10)


def _rand_vecs(n, dim):
    return [np.random.rand(dim).astype(np.float32) for _ in range(n)]


class TestT1FaissAutoBuild(unittest.TestCase):
    """T1：FAISS 索引构建 bug 修复。"""

    def setUp(self):
        if not _FAISS_OK:
            self.skipTest("faiss 未安装，跳过 FAISS 修复测试")

    def test_01_add_vectors_auto_builds_index(self):
        s = _make_store()
        ids = ["n%d" % i for i in range(30)]
        self.assertTrue(s.add_vectors(ids, _rand_vecs(30, 8)))
        self.assertTrue(s.is_available(), "首次 add_vectors 必须自动构建索引")
        self.assertTrue(s.is_index_built())

    def test_02_search_uses_faiss_not_brute(self):
        s = _make_store()
        s.add_vectors(["n%d" % i for i in range(50)], _rand_vecs(50, 8))
        s.search(np.random.rand(8).astype(np.float32), top_k=5)
        st = s.get_index_stats()
        self.assertGreaterEqual(st["search_faiss_count"], 1)
        self.assertEqual(st["search_brute_count"], 0, "索引存在时不应走暴力")

    def test_03_search_returns_topk(self):
        s = _make_store()
        s.add_vectors(["n%d" % i for i in range(20)], _rand_vecs(20, 8))
        res = s.search(np.random.rand(8).astype(np.float32), top_k=10)
        self.assertLessEqual(len(res), 10)
        self.assertTrue(all(isinstance(r, tuple) and len(r) == 2 for r in res))

    def test_04_save_load_restores_vectors(self):
        s = _make_store()
        s.add_vectors(["n%d" % i for i in range(40)], _rand_vecs(40, 8))
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".faiss", delete=False) as f:
            p = f.name
        try:
            self.assertTrue(s.save(p))
            s2 = _make_store()
            self.assertTrue(s2.load(p))
            self.assertTrue(s2.is_available())
            self.assertEqual(len(s2._vectors), 40, "load 必须恢复向量内容")
        finally:
            for ext in ("", ".ids.json", ".vectors.npy"):
                if os.path.exists(p + ext):
                    os.remove(p + ext)

    def test_05_get_index_stats_fields(self):
        s = _make_store()
        s.add_vectors(["n%d" % i for i in range(10)], _rand_vecs(10, 8))
        st = s.get_index_stats()
        for k in ("faiss_available", "index_ready", "index_built", "vector_count",
                  "id_map_size", "index_type", "dimension", "trained_count",
                  "last_build_time_sec", "rebuild_count", "search_faiss_count",
                  "search_brute_count", "search_path"):
            self.assertIn(k, st, "get_index_stats 缺少字段 %s" % k)
        self.assertEqual(st["trained_count"], 10)

    def test_06_rebuild_index(self):
        s = _make_store()
        s.add_vectors(["n%d" % i for i in range(10)], _rand_vecs(10, 8))
        self.assertTrue(s.rebuild_index())
        self.assertGreaterEqual(s.get_index_stats()["rebuild_count"], 1)

    def test_07_is_index_built_alias(self):
        s = _make_store()
        self.assertFalse(s.is_index_built())
        s.add_vectors(["n0"], [np.random.rand(8).astype(np.float32)])
        self.assertTrue(s.is_index_built())

    def test_08_brute_fallback_when_faiss_unavailable(self):
        s = _make_store()
        s._faiss_available = False
        s._vectors = {"a": np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32)}
        res = s.search(np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32), top_k=1)
        self.assertEqual(res[0][0], "a")
        self.assertGreaterEqual(s.get_index_stats()["search_brute_count"], 1)
        self.assertFalse(s.is_available())

    def test_09_brute_fallback_when_index_none(self):
        s = _make_store()
        # faiss 可用但索引未构建（bug 修复前的常态）
        s._vectors = {"a": np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32)}
        res = s.search(np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32), top_k=1)
        self.assertEqual(res[0][0], "a")
        self.assertEqual(s.get_index_stats()["search_path"], "brute_force")

    def test_10_incremental_add_grows_id_map(self):
        s = _make_store()
        s.add_vectors(["n%d" % i for i in range(10)], _rand_vecs(10, 8))
        s.add_vectors(["m%d" % i for i in range(5)], _rand_vecs(5, 8))
        self.assertEqual(len(s._id_map), 15)
        self.assertEqual(s.get_stats()["vector_count"], 15)

    def test_11_ivfflat_builds_and_searches(self):
        s = FAISSVectorStore(dimension=8, index_type="IVFFlat", batch_size=10)
        s.add_vectors(["n%d" % i for i in range(200)], _rand_vecs(200, 8))
        self.assertTrue(s.is_available(), "IVFFlat 也应自动构建")
        res = s.search(np.random.rand(8).astype(np.float32), top_k=5)
        self.assertLessEqual(len(res), 5)

    def test_12_load_without_vectors_npy_reconstruct(self):
        s = _make_store()
        s.add_vectors(["n%d" % i for i in range(30)], _rand_vecs(30, 8))
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".faiss", delete=False) as f:
            p = f.name
        try:
            self.assertTrue(s.save(p))
            # 删除 npy，强制走 reconstruct 回退
            if os.path.exists(p + ".vectors.npy"):
                os.remove(p + ".vectors.npy")
            s2 = _make_store()
            self.assertTrue(s2.load(p))
            # reconstruct 至少恢复部分向量
            self.assertGreaterEqual(len(s2._vectors), 1)
        finally:
            for ext in ("", ".ids.json", ".vectors.npy"):
                if os.path.exists(p + ext):
                    os.remove(p + ext)

    def test_13_search_speed_top1000_small(self):
        """功能性验证 FAISS 索引检索显著快于暴力（小集）。"""
        s = _make_store()
        n = 2000
        s.add_vectors(["n%d" % i for i in range(n)], _rand_vecs(n, 8))
        q = np.random.rand(8).astype(np.float32)
        _t0 = time.time()
        res = s.search(q, top_k=1000)
        _dt = (time.time() - _t0) * 1000.0
        self.assertLessEqual(len(res), 1000)
        self.assertLess(_dt, 200.0, "FAISS 索引检索应远快于暴力（2000 维集 <200ms）")

    def test_14_get_stats_includes_search_counts(self):
        s = _make_store()
        s.add_vectors(["n%d" % i for i in range(10)], _rand_vecs(10, 8))
        s.search(np.random.rand(8).astype(np.float32), top_k=3)
        gs = s.get_stats()
        self.assertIn("search_faiss_count", gs)
        self.assertIn("search_brute_count", gs)
        self.assertGreaterEqual(gs["search_faiss_count"], 1)


class TestT2Benchmark(unittest.TestCase):
    """T2：benchmark 脚本可用性（真实 12295 节点基准单独后台运行）。"""

    def test_20_benchmark_module_importable(self):
        try:
            import tools.benchmark_hot_cold_faiss_kal as bm
        except Exception as e:
            self.skipTest("benchmark 模块不可导入: %s" % e)
        self.assertTrue(hasattr(bm, "main"))
        self.assertTrue(callable(bm.main))

    def test_21_benchmark_faiss_stage_callable(self):
        try:
            import tools.benchmark_hot_cold_faiss_kal as bm
        except Exception as e:
            self.skipTest("benchmark 模块不可导入: %s" % e)
        # stage_node_pool_real 存在说明 FAISS 真实物化阶段已就绪
        self.assertTrue(hasattr(bm, "stage_node_pool_real") or hasattr(bm, "_load_real_nodes"))


class TestT3SnapshotCaliber(unittest.TestCase):
    """T3：snapshot_active_nodes 口径确认（设计行为，非 bug）。"""

    def test_30_snapshot_returns_hot_plus_warm(self):
        from nucleus.mnemosyne.PulseNodePool import PulseNodePool
        from nucleus.mnemosyne.PulseNode import PulseNode
        pool = PulseNodePool()
        a = PulseNode(value="t3-a", evol_level="L1")
        b = PulseNode(value="t3-b", evol_level="L2")
        pool.add(a)
        pool.add(b)
        snap = pool.snapshot_active_nodes()
        hot_warm = list(pool._hot.values()) + list(pool._warm.values())
        self.assertEqual(len(snap), len(hot_warm))
        self.assertEqual(set(id(x) for x in snap), set(id(x) for x in hot_warm))


class TestT4DualReadCompare(unittest.TestCase):
    """T4：双读一致性实时比对。"""

    def setUp(self):
        import config
        self._cfg_backup = {}
        for _k in ("ENABLE_NEO4J_GRAPH_STORE", "ENABLE_NEO4J_DUAL_WRITE",
                   "ENABLE_NEO4J_READ", "NEO4J_READ_COMPARE_RATE",
                   "NEO4J_READ_COMPARE_WARN_THRESHOLD",
                   "NEO4J_READ_COMPARE_FALLBACK_THRESHOLD"):
            self._cfg_backup[_k] = getattr(config, _k, None)
        config.ENABLE_NEO4J_GRAPH_STORE = True
        config.ENABLE_NEO4J_DUAL_WRITE = True
        config.ENABLE_NEO4J_READ = True
        config.NEO4J_READ_COMPARE_RATE = 1.0  # 每次都比对
        config.NEO4J_READ_COMPARE_WARN_THRESHOLD = 0.05
        config.NEO4J_READ_COMPARE_FALLBACK_THRESHOLD = 0.10
        # 注入 MockNeo4jStore
        from nucleus.mnemosyne.PulseNodePool import PulseNodePool
        from nucleus.mnemosyne.PulseNode import PulseNode
        self.pool = PulseNodePool()
        self.mock = _MockNeo4jStore()
        self._patch = mock.patch(
            "nucleus.graph_store.neo4j_store.get_neo4j_store",
            return_value=self.mock)
        self._patch.start()
        # 节点：linked_nodes=["x"]；但 Neo4j 侧关系指向 ["y"] → 故意不一致
        self.node = PulseNode(value="t4-a", evol_level="L2")
        self.node.linked_nodes = ["x"]
        self.pool.add(self.node)
        self.mock.add_node(self.node.node_id, {})
        self.mock.add_relationship(self.node.node_id, "y", "RELATED")

    def tearDown(self):
        import config
        for _k, _v in self._cfg_backup.items():
            if _v is None:
                if hasattr(config, _k):
                    delattr(config, _k)
            else:
                setattr(config, _k, _v)
        self._patch.stop()

    def test_40_compare_disabled_when_read_off(self):
        import config
        config.ENABLE_NEO4J_READ = False
        st = self.pool.get_read_compare_stats()
        self.assertFalse(st["enabled"])
        self.assertEqual(st["sampled"], 0)

    def test_41_compare_detects_mismatch(self):
        for _ in range(5):
            self.pool.get_relationships_neo4j(self.node.node_id, "both")
        cs = self.pool.get_read_compare_stats()
        self.assertGreaterEqual(cs["sampled"], 5)
        self.assertGreater(cs["mismatch"], 0, "应检测到 Neo4j 与节点内关联不一致")
        self.assertIn(self.node.node_id, cs["inconsistent_nodes"])

    def test_42_compare_force_fallback_on_high_mismatch(self):
        # 全部不一致，mismatch_rate 必超 0.10 → 自动回退
        self.mock.set_all_mismatch(True)  # 让每次返回的关系都与节点内不同
        for _ in range(20):
            self.pool.get_relationships_neo4j(self.node.node_id, "both")
        cs = self.pool.get_read_compare_stats()
        self.assertTrue(cs["force_fallback"], "不一致率超阈值应自动回退节点内")
        # 回退后不再命中 Neo4j
        before = self.pool.get_read_stats()["neo4j_hit"]
        self.pool.get_relationships_neo4j(self.node.node_id, "both")
        after = self.pool.get_read_stats()["neo4j_hit"]
        self.assertEqual(before, after, "force_fallback 后不应再查 Neo4j")

    def test_43_compare_warn_threshold(self):
        config = __import__("config")
        config.NEO4J_READ_COMPARE_FALLBACK_THRESHOLD = 1.0  # 不触发回退，只看告警
        for _ in range(10):
            self.pool.get_relationships_neo4j(self.node.node_id, "both")
        cs = self.pool.get_read_compare_stats()
        self.assertGreater(cs["mismatch_rate"], 0.05, "不一致率应超告警阈值")
        self.assertFalse(cs["force_fallback"])
        self.assertIn("告警", cs["last_warn"])

    def test_44_repair_inconsistent_node(self):
        self.pool.get_relationships_neo4j(self.node.node_id, "both")
        synced = self.pool.repair_inconsistent_node(self.node.node_id)
        self.assertGreaterEqual(synced, 1, "repair 应至少同步 1 条关系到 Neo4j")
        # 修复后该节点应从不一致列表移除
        cs = self.pool.get_read_compare_stats()
        self.assertNotIn(self.node.node_id, cs["inconsistent_nodes"])

    def test_45_zero_overhead_when_disabled(self):
        import config
        config.ENABLE_NEO4J_READ = False
        # 关闭时不应抛异常，也不应触达 store
        out = self.pool.get_relationships_neo4j(self.node.node_id, "both")
        self.assertIsInstance(out, list)

    def test_46_kal_compare_stats_delegation(self):
        from nucleus.knowledge_access_layer import KnowledgeAccessLayer
        kal = KnowledgeAccessLayer(node_pool=self.pool)
        cs = kal.get_neo4j_read_compare_stats()
        self.assertIn("sampled", cs)
        self.assertIn("mismatch", cs)

    def test_47_compare_reset(self):
        for _ in range(3):
            self.pool.get_relationships_neo4j(self.node.node_id, "both")
        self.pool.reset_read_compare()
        cs = self.pool.get_read_compare_stats()
        self.assertEqual(cs["sampled"], 0)
        self.assertEqual(cs["mismatch"], 0)
        self.assertFalse(cs["force_fallback"])

    def test_48_kal_reset_delegation(self):
        from nucleus.knowledge_access_layer import KnowledgeAccessLayer
        kal = KnowledgeAccessLayer(node_pool=self.pool)
        for _ in range(3):
            self.pool.get_relationships_neo4j(self.node.node_id, "both")
        kal.reset_neo4j_read_compare()
        cs = self.pool.get_read_compare_stats()
        self.assertEqual(cs["sampled"], 0)


class _MockNeo4jStore:
    """轻量 Neo4j mock，用于 T4 双读一致性测试。"""

    def __init__(self):
        self._nodes = {}
        self._rels = {}  # from_id -> list of (to_id, rel_type)
        self._all_mismatch = False

    def set_all_mismatch(self, v):
        self._all_mismatch = v

    def is_available(self):
        return True

    def add_node(self, node_id, properties=None):
        self._nodes[node_id] = dict(properties or {})
        return True

    def add_relationship(self, from_id, to_id, rel_type, properties=None):
        self._rels.setdefault(from_id, []).append((to_id, rel_type))
        return True

    def get_relationships(self, node_id, direction="both"):
        rels = self._rels.get(node_id, [])
        if self._all_mismatch:
            # 返回一个与节点内不同的目标，强制不一致
            return [{"from": node_id, "to": "zzz_mismatch", "rel_type": "RELATED"}]
        return [{"from": node_id, "to": t, "rel_type": rt} for t, rt in rels]

    def get_stats(self):
        return {"nodes": len(self._nodes), "rels": sum(len(v) for v in self._rels.values())}


if __name__ == "__main__":
    unittest.main(verbosity=2)
