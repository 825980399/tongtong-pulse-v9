# -*- coding: utf-8 -*-
"""第69批测试：冷热分离 + FAISS集成 + 自适应降频接线 + KAL迁移。

测试覆盖：
- T1: 冷热分离加载测试、缓存测试、节点升降级测试、内存保护测试
- T2: FAISS索引构建测试、检索测试、增删改测试、保存加载测试、回退测试
- T3: 自适应降频接线测试、负载等级变化测试、关键操作白名单测试
- T4: KAL迁移测试、性能对比测试、回退测试
"""
import os
import sys
import unittest
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestColdHotSeparation(unittest.TestCase):
    """T1: 冷热分离三层架构测试。"""

    def setUp(self):
        from nucleus.mnemosyne.PulseNodePool import PulseNodePool
        from nucleus.mnemosyne.PulseNode import PulseNode
        self.PulseNode = PulseNode
        self.pool = PulseNodePool.__new__(PulseNodePool)
        self.pool._hot = {}
        self.pool._warm = {}
        self.pool._cold = {}
        self.pool._instinct = {}
        self.pool._max_hot = 10000
        self.pool._max_warm = 50000
        self.pool._lock = __import__("threading").Lock()
        self.pool._content_add_total = 0
        self.pool._content_dedup_total = 0
        self.pool._cold_storage_enabled = False
        self.pool._max_cold_cache = 5000
        self.pool._cold_dir = "data/knowledge/cold"
        self.pool._cold_evicted = set()
        self.pool._cold_stats = {"recall_hits": 0, "recall_misses": 0}
        self.pool._cold_recall_fail_count = 0
        self.pool._cold_recall_fail_last_log = 0.0
        self.pool._level_index = {"L1": set(), "L2": set(), "L3": set()}
        self.pool._path_index = {}
        self.pool._semantic_index = {}
        self.pool._semantic_index_valid = False
        self.pool.resonance_engine = None
        self.pool._index_store = None
        self.pool._total_added = 0
        self.pool._total_removed = 0
        self.pool._total_activated = 0
        self.pool._field_strength = 1.0
        self.pool._hebbian_learner = None
        # T1 init attributes
        self.pool._access_count = {}
        self.pool._warm_lru = OrderedDict()
        self.pool._cold_metadata = {}
        self.pool._cache_stats = {"warm_hits": 0, "warm_misses": 0, "cold_loads": 0, "cache_shrinks": 0}
        self.pool._hot_cold_enabled = True
        self.pool._warm_cache_size = 10000
        self.pool._promotion_threshold = 100
        self.pool._demotion_threshold = 10
        self.pool._memory_warning_threshold = 0.8

    def _make_node(self, node_id="test_001", evol_level="L1"):
        n = self.PulseNode.__new__(self.PulseNode)
        n.node_id = node_id
        n.evol_level = evol_level
        n.keywords = ["test"]
        n.value = "test value"
        n.space_path = "/test"
        n.trust_score = 50.0
        n.hebbian_weight = 0.0
        n.frequency_signature = "test_freq"
        n._activated_count = 0
        n.activate = lambda: setattr(n, '_activated_count', n._activated_count + 1)
        n.to_dict = lambda: {"node_id": node_id, "evol_level": evol_level}
        return n

    def test_01_access_count_tracking(self):
        """访问频率跟踪：get() 后 _access_count 递增。"""
        node = self._make_node("n1", "L2")
        self.pool._warm["n1"] = node
        self.pool._warm_lru["n1"] = node
        self.pool.get("n1")
        self.pool.get("n1")
        self.pool.get("n1")
        self.assertEqual(self.pool._access_count.get("n1", 0), 3)

    def test_02_lru_warm_cache_hit(self):
        """温缓存LRU命中：访问后移到末尾。"""
        node = self._make_node("n1", "L2")
        self.pool._warm["n1"] = node
        self.pool._warm_lru["n1"] = node
        self.pool.get("n1")
        self.assertTrue("n1" in self.pool._warm_lru)
        self.assertEqual(list(self.pool._warm_lru.keys())[-1], "n1")
        self.assertEqual(self.pool._cache_stats["warm_hits"], 1)

    def test_03_promote_to_hot(self):
        """L2->L1升级：访问次数超阈值后升级到热池。"""
        node = self._make_node("n1", "L2")
        self.pool._warm["n1"] = node
        self.pool._warm_lru["n1"] = node
        self.pool._access_count["n1"] = self.pool._promotion_threshold
        self.pool._promote_to_hot(node)
        self.assertIn("n1", self.pool._hot)
        self.assertNotIn("n1", self.pool._warm)

    def test_04_demote_to_warm(self):
        """L1->L2降级：热池节点降级到温池。"""
        node = self._make_node("n1", "L1")
        self.pool._hot["n1"] = node
        self.pool._access_count["n1"] = 5
        self.pool._demote_to_warm(node)
        self.assertIn("n1", self.pool._warm)
        self.assertIn("n1", self.pool._warm_lru)
        self.assertNotIn("n1", self.pool._hot)

    def test_05_enforce_warm_lru_eviction(self):
        """温缓存LRU淘汰：超容量时淘汰最久未访问的节点。"""
        for i in range(15):
            n = self._make_node(f"n{i}", "L2")
            self.pool._warm[f"n{i}"] = n
            self.pool._warm_lru[f"n{i}"] = n
        self.pool._warm_cache_size = 10
        self.pool._enforce_warm_lru()
        self.assertLessEqual(len(self.pool._warm_lru), 10)
        self.assertEqual(len(self.pool._warm_lru), 10)
        self.assertEqual(len(self.pool._cold), 5)

    def test_06_cache_stats(self):
        """缓存命中率统计。"""
        node = self._make_node("n1", "L2")
        self.pool._warm["n1"] = node
        self.pool._warm_lru["n1"] = node
        self.pool.get("n1")  # hit
        stats = self.pool.get_cache_stats()
        self.assertEqual(stats["warm_hits"], 1)

    def test_07_hot_cold_disabled(self):
        """关闭冷热分离时不跟踪访问频率。"""
        self.pool._hot_cold_enabled = False
        node = self._make_node("n1", "L2")
        self.pool._warm["n1"] = node
        self.pool._warm_lru["n1"] = node
        self.pool.get("n1")
        self.assertEqual(len(self.pool._access_count), 0)

    def test_08_demote_resets_access_count(self):
        """降级后重置访问计数。"""
        node = self._make_node("n1", "L1")
        self.pool._hot["n1"] = node
        self.pool._access_count["n1"] = 5
        self.pool._demote_to_warm(node)
        self.assertGreater(self.pool._access_count["n1"], self.pool._demotion_threshold)


class TestFAISSStore(unittest.TestCase):
    """T2: FAISS向量数据库测试。"""

    def setUp(self):
        from nucleus.vector_store.faiss_store import FAISSVectorStore
        self.store = FAISSVectorStore(dimension=4, index_type="FlatL2", batch_size=10)

    def test_10_faiss_available(self):
        """faiss-cpu已安装。"""
        try:
            import faiss  # noqa: F401
        except ImportError:
            self.skipTest("faiss not installed in this Python runtime")
        self.assertTrue(self.store._faiss_available)

    def test_11_build_and_search(self):
        """FAISS索引构建与检索。"""
        try:
            import faiss  # noqa: F401
        except ImportError:
            self.skipTest("faiss not installed in this Python runtime")
        import numpy as np
        ids = ["n1", "n2", "n3"]
        vecs = [np.random.rand(4).astype(np.float32) for _ in range(3)]
        self.store._build_index(list(zip(ids, vecs)))
        self.assertTrue(self.store.is_available())
        qv = np.random.rand(4).astype(np.float32)
        results = self.store.search(qv, top_k=2)
        self.assertLessEqual(len(results), 2)

    def test_12_brute_force_fallback(self):
        """暴力余弦回退。"""
        import numpy as np
        self.store._vectors = {"n1": np.array([1, 0, 0, 0], dtype=np.float32),
                               "n2": np.array([0, 1, 0, 0], dtype=np.float32)}
        qv = np.array([1, 0, 0, 0], dtype=np.float32)
        results = self.store._brute_force_search(qv, top_k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "n1")

    def test_13_save_load(self):
        """FAISS索引保存加载。"""
        try:
            import faiss  # noqa: F401
        except ImportError:
            self.skipTest("faiss not installed in this Python runtime")
        import numpy as np
        import tempfile
        ids = ["n1", "n2"]
        vecs = [np.random.rand(4).astype(np.float32) for _ in range(2)]
        self.store._build_index(list(zip(ids, vecs)))
        with tempfile.NamedTemporaryFile(suffix=".faiss", delete=False) as f:
            path = f.name
        try:
            self.assertTrue(self.store.save(path))
            from nucleus.vector_store.faiss_store import FAISSVectorStore
            store2 = FAISSVectorStore(dimension=4, index_type="FlatL2")
            self.assertTrue(store2.load(path))
            self.assertTrue(store2.is_available())
        finally:
            if os.path.exists(path):
                os.remove(path)
            if os.path.exists(path + ".ids.json"):
                os.remove(path + ".ids.json")

    def test_14_update_vector(self):
        """向量更新标记索引需重建。"""
        try:
            import faiss  # noqa: F401
        except ImportError:
            self.skipTest("faiss not installed in this Python runtime")
        import numpy as np
        ids = ["n1"]
        vecs = [np.array([1, 0, 0, 0], dtype=np.float32)]
        self.store._build_index(list(zip(ids, vecs)))
        self.assertTrue(self.store.is_available())
        self.store.update_vectors("n1", np.array([0, 1, 0, 0], dtype=np.float32))
        self.assertFalse(self.store.is_available())

    def test_15_remove_vector(self):
        """向量删除标记索引需重建。"""
        import numpy as np
        ids = ["n1", "n2"]
        vecs = [np.array([1, 0], dtype=np.float32), np.array([0, 1], dtype=np.float32)]
        self.store._dimension = 2
        self.store._build_index(list(zip(ids, vecs)))
        self.store.remove_vector("n1")
        self.assertFalse(self.store.is_available())

    def test_16_stats(self):
        """统计信息。"""
        stats = self.store.get_stats()
        self.assertIn("faiss_available", stats)
        self.assertEqual(stats["dimension"], 4)


class TestAdaptiveFrequencyWiring(unittest.TestCase):
    """T3: 自适应降频接线测试。"""

    def test_20_controller_singleton(self):
        """控制器单例可获取。"""
        from nucleus.runtime_metrics import get_adaptive_controller
        ctrl = get_adaptive_controller()
        self.assertIsNotNone(ctrl)

    def test_21_stomach_wiring(self):
        """胃模块降频接线存在。"""
        src = open(os.path.join(_ROOT, "organs/body/PulseStomach.py"), encoding="utf-8").read()
        self.assertIn("stomach_digest", src)
        self.assertIn("should_execute", src)

    def test_22_organ_scan_wiring(self):
        """器官扫描降频接线存在。"""
        src = open(os.path.join(_ROOT, "nucleus/self_inspector.py"), encoding="utf-8").read()
        self.assertIn("organ_scan", src)
        self.assertIn("should_execute", src)

    def test_23_code_learning_wiring(self):
        """代码学习降频接线存在。"""
        src = open(os.path.join(_ROOT, "organs/brain/PulseCodeLearner.py"), encoding="utf-8").read()
        self.assertIn("code_learning", src)
        self.assertIn("should_execute", src)

    def test_24_experience_cleanup_wiring(self):
        """经验库清理降频接线存在。"""
        src = open(os.path.join(_ROOT, "nucleus/mnemosyne/experience_pool.py"), encoding="utf-8").read()
        self.assertIn("experience_cleanup", src)
        self.assertIn("should_execute", src)

    def test_25_cold_compaction_wiring(self):
        """冷存compaction降频接线存在。"""
        src = open(os.path.join(_ROOT, "nucleus/mnemosyne/PulseNodePool.py"), encoding="utf-8").read()
        self.assertIn("cold_compaction", src)
        self.assertIn("should_execute", src)

    def test_26_critical_op_whitelist(self):
        """关键操作白名单不受降频影响。"""
        from nucleus.runtime_metrics import get_adaptive_controller
        ctrl = get_adaptive_controller()
        ctrl.set_level("CRITICAL")
        # 白名单: "对话响应", "心跳", "生命体征", "快照保存", "chat", "heartbeat", "snapshot_save"
        self.assertTrue(ctrl.should_execute("heartbeat"))
        self.assertTrue(ctrl.should_execute("chat"))
        self.assertTrue(ctrl.should_execute("snapshot_save"))

    def test_27_non_critical_throttled(self):
        """非关键操作在高负载时降频。"""
        from nucleus.runtime_metrics import get_adaptive_controller
        ctrl = get_adaptive_controller()
        ctrl.register("test_op_m69", 1)
        ctrl.set_level("CRITICAL")
        ctrl._last_run.pop("test_op_m69", None)
        result = ctrl.should_execute("test_op_m69")
        self.assertFalse(result)

    def test_28_low_level_normal(self):
        """低负载时操作正常执行。"""
        from nucleus.runtime_metrics import get_adaptive_controller
        ctrl = get_adaptive_controller()
        ctrl.register("test_low_op_m69", 1)
        ctrl.set_level("LOW")
        ctrl._last_run.pop("test_low_op_m69", None)
        self.assertTrue(ctrl.should_execute("test_low_op_m69"))


class TestKALMigration(unittest.TestCase):
    """T4: KAL迁移测试。"""

    def test_30_kal_stomach_method(self):
        """胃模块有KAL查询方法。"""
        src = open(os.path.join(_ROOT, "organs/body/PulseStomach.py"), encoding="utf-8").read()
        self.assertIn("_m69_kal_query", src)

    def test_31_kal_liver_method(self):
        """肝模块有KAL查询方法。"""
        src = open(os.path.join(_ROOT, "organs/body/PulseLiver.py"), encoding="utf-8").read()
        self.assertIn("_m69_kal_query", src)

    def test_32_kal_kidney_method(self):
        """肾模块有KAL查询方法（m70 命名 _m70_kal_search_by_level；m69 的 _m69_kal_query 仅胃/肝采用）。"""
        src = open(os.path.join(_ROOT, "organs/body/PulseKidney.py"), encoding="utf-8").read()
        # ★B156-3 T-A05 读码判定：肾采用 _m70_kal_* KAL 集成（git log -S 证实 _m69_kal_query
        #   从未在 Kidney 落地），生产非回归、测试锚过时 → 对齐肾实际 KAL 查询方法名。
        self.assertIn("_m70_kal_search_by_level", src)

    def test_33_kal_migration_config(self):
        """KAL迁移配置开关存在。"""
        self.assertTrue(hasattr(config, "ENABLE_KAL_MIGRATION"))

    def test_34_kal_disabled_returns_none(self):
        """关闭KAL迁移时返回None。"""
        from organs.body.PulseStomach import PulseStomach
        stom = PulseStomach.__new__(PulseStomach)
        result = stom._m69_kal_query(["test"])
        # ENABLE_KAL_MIGRATION=True but KAL returns empty result (no node_pool injected)
        self.assertTrue(result is None or result == [])

    def test_35_kal_singleton(self):
        """KAL单例可获取。"""
        from nucleus.knowledge_access_layer import get_kal, KnowledgeAccessLayer
        kal = get_kal()
        self.assertIsInstance(kal, KnowledgeAccessLayer)


class TestIntegration(unittest.TestCase):
    """集成测试。"""

    def test_40_all_configs_present(self):
        """所有第69批配置项存在。"""
        for c in ["ENABLE_HOT_COLD_SEPARATION", "ENABLE_FAISS_VECTOR_STORE",
                   "ENABLE_ADAPTIVE_FREQUENCY", "ENABLE_KAL_MIGRATION"]:
            self.assertTrue(hasattr(config, c), f"Missing: {c}")

    def test_41_faiss_store_importable(self):
        """FAISS存储模块可导入。"""
        from nucleus.vector_store.faiss_store import FAISSVectorStore, get_faiss_store
        store = get_faiss_store()
        self.assertIsInstance(store, FAISSVectorStore)

    def test_42_no_f_errors_in_new_module(self):
        """新模块无ruff F错误。"""
        import subprocess
        ruff = r"D:\Program Files\Python312\Scripts\ruff.exe"
        fp = os.path.join(_ROOT, "nucleus/vector_store/faiss_store.py")
        result = subprocess.run([ruff, "check", "--select", "F", "--output-format=concise", fp],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, f"ruff F errors: {result.stdout}")

    def test_43_vector_store_package_init(self):
        """vector_store包__init__.py存在。"""
        self.assertTrue(os.path.isfile(os.path.join(_ROOT, "nucleus/vector_store/__init__.py")))


if __name__ == "__main__":
    unittest.main()
