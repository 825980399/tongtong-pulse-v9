# -*- coding: utf-8 -*-
"""第70批测试：Neo4j图存储 + InfluxDB时序存储 + 分布式架构 + 第69批遗留延续。

测试覆盖：
- T1: Neo4j 封装（未启用/驱动缺失时的零副作用、接口存在性、配置）
- T2: InfluxDB 封装（同上 + 异常检测纯函数逻辑）
- T3: 分布式分片（哈希稳定性/分布/一致性级别）+ 路由（健康/负载均衡策略）
- T4: PulseSnapshot 冷热加载 + fast_ops FAISS 对接 + KAL 调用点
- 集成：配置项齐全 + 新包可导入

★Neo4j / InfluxDB 未安装且默认关闭 → 全部走"未启用"分支，
  验证的是**零副作用**而非真实数据库交互（符合任务书 5.3「使用mock，不依赖实际数据库」）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ===== T1: Neo4j =====

class TestNeo4jStore(unittest.TestCase):
    """T1: Neo4j 图存储封装测试。"""

    def setUp(self):
        from nucleus.graph_store.neo4j_store import Neo4jStore
        self.store = Neo4jStore()

    def test_01_store_instantiable(self):
        """Neo4jStore 可实例化。"""
        self.assertIsNotNone(self.store)

    def test_02_disabled_by_default(self):
        """默认关闭（ENABLE_NEO4J_GRAPH_STORE=False）。"""
        self.assertFalse(config.ENABLE_NEO4J_GRAPH_STORE)

    def test_03_not_available_when_disabled(self):
        """未启用时 is_available() 恒 False。"""
        self.assertFalse(self.store.is_available())

    def test_04_connect_returns_false_when_disabled(self):
        """未启用时 connect() 返回 False，不抛异常。"""
        self.assertFalse(self.store.connect())

    def test_05_operations_safe_when_unavailable(self):
        """不可用时所有 CRUD 返回安全值，不抛异常。"""
        self.assertFalse(self.store.add_node("n1", {"k": "v"}))
        self.assertFalse(self.store.remove_node("n1"))
        self.assertIsNone(self.store.get_node("n1"))
        self.assertFalse(self.store.add_relationship("n1", "n2", "RELATED"))
        self.assertEqual(self.store.get_relationships("n1"), [])
        self.assertEqual(self.store.query_multi_hop("n1", 2), [])
        self.assertEqual(self.store.query_shortest_path("n1", "n2"), [])
        self.assertEqual(self.store.query_common_neighbors("n1", "n2"), [])

    def test_06_sync_from_nodes_safe(self):
        """批量同步在不可用时返回 (0,0)，不抛异常。"""
        self.assertEqual(self.store.sync_from_nodes([]), (0, 0))

    def test_07_stats_structure(self):
        """统计信息结构完整。"""
        st = self.store.get_stats()
        for k in ("enabled", "driver_installed", "available", "uri", "database"):
            self.assertIn(k, st)

    def test_08_password_not_hardcoded(self):
        """密码不硬编码（默认空，从环境变量读）。"""
        self.assertEqual(self.store._password, "")

    def test_09_config_present(self):
        """Neo4j 配置项齐全。"""
        for c in ("NEO4J_URI", "NEO4J_USER", "NEO4J_DATABASE",
                  "NEO4J_BATCH_SIZE", "NEO4J_CONNECTION_POOL_SIZE"):
            self.assertTrue(hasattr(config, c), f"Missing: {c}")

    def test_10_close_safe(self):
        """close() 在未连接时安全。"""
        try:
            self.store.close()
        except Exception as e:
            self.fail(f"close() raised: {e}")


# ===== T2: InfluxDB =====

class TestInfluxDBStore(unittest.TestCase):
    """T2: InfluxDB 时序存储封装测试。"""

    def setUp(self):
        from nucleus.timeseries_store.influxdb_store import InfluxDBStore
        self.store = InfluxDBStore()

    def test_11_disabled_by_default(self):
        """默认关闭。"""
        self.assertFalse(config.ENABLE_INFLUXDB_TIMESERIES)

    def test_12_not_available(self):
        """未启用时不可用。"""
        self.assertFalse(self.store.is_available())

    def test_13_write_safe_when_unavailable(self):
        """不可用时写入返回 False，不抛异常。"""
        self.assertFalse(self.store.write_point("m", {"t": "1"}, {"v": 1}))
        self.assertFalse(self.store.write_points([{"measurement": "m"}]))

    def test_14_semantic_writers_safe(self):
        """语义化写入方法安全。"""
        self.assertFalse(self.store.node_activated("n1", "L1", "test"))
        self.assertFalse(self.store.node_accessed("n1", "get", 1.0))
        self.assertFalse(self.store.node_modified("n1", "value", "a", "b"))
        self.assertFalse(self.store.query_executed("semantic", 1.0, 5))

    def test_15_query_safe(self):
        """不可用时查询返回空。"""
        self.assertEqual(self.store.query_range("m"), [])
        self.assertEqual(self.store.query_latest("m"), [])
        self.assertEqual(self.store.query_count("m"), 0)
        self.assertEqual(self.store.query_trend("m"), [])
        self.assertEqual(self.store.query_top_k("m"), [])

    def test_16_anomaly_on_empty(self):
        """异常检测在数据不足时返回空。"""
        self.assertEqual(self.store.query_anomaly("m"), [])

    def test_17_retention_policy(self):
        """降采样保留策略存在。"""
        rp = self.store.get_retention_policy()
        self.assertEqual(rp["raw"], "7d")
        self.assertEqual(rp["1m_agg"], "30d")
        self.assertEqual(rp["1h_agg"], "365d")

    def test_18_token_not_hardcoded(self):
        """Token 不硬编码。"""
        self.assertEqual(self.store._token, "")

    def test_19_config_present(self):
        """InfluxDB 配置齐全。"""
        for c in ("INFLUXDB_URL", "INFLUXDB_ORG", "INFLUXDB_BUCKET",
                  "INFLUXDB_BATCH_SIZE", "INFLUXDB_FLUSH_INTERVAL"):
            self.assertTrue(hasattr(config, c), f"Missing: {c}")

    def test_20_stats_structure(self):
        """统计结构完整。"""
        st = self.store.get_stats()
        for k in ("enabled", "client_installed", "available", "bucket"):
            self.assertIn(k, st)


# ===== T3: 分布式分片 =====

class TestSharding(unittest.TestCase):
    """T3: 分片策略测试。"""

    def setUp(self):
        from nucleus.distributed.sharding import ShardStrategy
        self.s = ShardStrategy(shard_count=16, replica_count=3)

    def test_21_disabled_by_default(self):
        """分布式默认关闭。"""
        self.assertFalse(config.ENABLE_DISTRIBUTED)

    def test_22_shard_id_in_range(self):
        """分片 ID 在 [0, shard_count)。"""
        for i in range(50):
            sid = self.s.get_shard_id(f"n{i}", "L2")
            self.assertGreaterEqual(sid, 0)
            self.assertLess(sid, 16)

    def test_23_shard_id_stable(self):
        """同一 node_id 多次计算一致（稳定哈希）。"""
        a = self.s.get_shard_id("stable_node", "L1")
        b = self.s.get_shard_id("stable_node", "L1")
        self.assertEqual(a, b)

    def test_24_level_partition(self):
        """不同 evol_level 落在不同分区（L1/L2/L3 偏移不同）。"""
        l1 = {self.s.get_shard_id(f"n{i}", "L1") for i in range(20)}
        l3 = {self.s.get_shard_id(f"n{i}", "L3") for i in range(20)}
        self.assertTrue(l1 & l3 == set(), "L1 与 L3 分片应有分区隔离")

    def test_25_pure_hash_strategy(self):
        """纯哈希策略可用。"""
        from nucleus.distributed.sharding import ShardStrategy
        s = ShardStrategy(shard_count=8, strategy="hash")
        self.assertEqual(s.get_shard_id("x", "L1"), s.get_shard_id("x", "L1"))

    def test_26_get_shard_nodes(self):
        """副本节点列表长度 = replica_count。"""
        nodes = self.s.get_shard_nodes(0)
        self.assertEqual(len(nodes), 3)

    def test_27_invalid_shard(self):
        """越界分片返回空。"""
        self.assertEqual(self.s.get_shard_nodes(999), [])

    def test_28_migrate_not_implemented(self):
        """迁移为第71批+实施项，返回 False。"""
        self.assertFalse(self.s.migrate_shard(0, "node-2"))

    def test_29_distribution(self):
        """分布统计总数守恒。"""
        ids = [f"n{i}" for i in range(100)]
        dist = self.s.get_distribution(ids, {i: "L2" for i in ids})
        self.assertEqual(sum(dist.values()), 100)

    def test_30_consistency_levels(self):
        """一致性级别常量与校验。"""
        from nucleus.distributed.sharding import ConsistencyLevel
        self.assertTrue(ConsistencyLevel.validate("eventual"))
        self.assertTrue(ConsistencyLevel.validate("strong"))
        self.assertFalse(ConsistencyLevel.validate("bogus"))

    def test_31_consistency_ops_disabled(self):
        """未启用分布式时一致性接口返回未实施。"""
        from nucleus.distributed.sharding import (
            write_with_consistency, read_with_consistency, sync_replicas)
        self.assertFalse(write_with_consistency("n1", {}, "eventual"))
        self.assertIsNone(read_with_consistency("n1"))
        self.assertFalse(sync_replicas(0))

    def test_32_stats(self):
        """统计结构完整。"""
        st = self.s.get_shard_stats()
        self.assertEqual(st["shard_count"], 16)
        self.assertIn("level_shards", st)


# ===== T3: 路由 =====

class TestRouting(unittest.TestCase):
    """T3: 路由与负载均衡测试。"""

    def setUp(self):
        from nucleus.distributed.routing import NodeRouter
        self.r = NodeRouter(nodes=["node-1", "node-2", "node-3"])

    def test_33_healthy_nodes(self):
        """初始全部健康。"""
        self.assertEqual(len(self.r.get_healthy_nodes()), 3)

    def test_34_unhealthy_excluded(self):
        """健康分低于阈值被剔除。"""
        self.r.update_node_health("node-1", 0.1)
        self.assertNotIn("node-1", self.r.get_healthy_nodes())

    def test_35_route_returns_node(self):
        """路由返回有效节点。"""
        n = self.r.route_request("read", "n1")
        self.assertIn(n, ["node-1", "node-2", "node-3"])

    def test_36_least_conn_prefers_idle(self):
        """最少连接策略偏向空闲节点。"""
        self.r.acquire("node-1")
        self.r.acquire("node-1")
        n = self.r.route_request("read", "x")
        self.assertNotEqual(n, "node-1")

    def test_37_round_robin(self):
        """轮询策略轮流分配。"""
        from nucleus.distributed.routing import NodeRouter
        r = NodeRouter(nodes=["a", "b"], strategy="round_robin")
        picks = {r.route_request() for _ in range(4)}
        self.assertEqual(picks, {"a", "b"})

    def test_38_consistent_hash(self):
        """一致性哈希：同 key 恒定。"""
        from nucleus.distributed.routing import NodeRouter
        r = NodeRouter(nodes=["a", "b", "c"], strategy="consistent_hash")
        self.assertEqual(r.route_request("read", "same"),
                         r.route_request("read", "same"))

    def test_39_no_healthy_returns_none(self):
        """无健康节点返回 None。"""
        for n in ["node-1", "node-2", "node-3"]:
            self.r.update_node_health(n, 0.0)
        self.assertIsNone(self.r.route_request("read", "x"))

    def test_40_add_remove_node(self):
        """节点增删改。"""
        self.r.add_node("node-4")
        self.assertIn("node-4", self.r.get_healthy_nodes())
        self.r.remove_node("node-4")
        self.assertNotIn("node-4", self.r.get_healthy_nodes())

    def test_41_stats(self):
        """统计结构完整。"""
        st = self.r.get_stats()
        self.assertIn("strategy", st)
        self.assertIn("health", st)


# ===== T4: 第69批遗留延续 =====

class TestT4Carryover(unittest.TestCase):
    """T4: 冷热加载 + FAISS 对接 + KAL 调用点。"""

    def test_42_hot_cold_config(self):
        """冷热加载配置存在且默认开。"""
        self.assertTrue(config.SNAPSHOT_HOT_COLD_LOAD)
        self.assertTrue(config.ENABLE_FAISS_FAST_OPS)
        self.assertTrue(config.ENABLE_KAL_CALL_SITES)

    def test_43_snapshot_has_hot_cold_method(self):
        """PulseSnapshot 有冷热加载方法。"""
        src = open(os.path.join(_ROOT, "nucleus/mnemosyne/PulseSnapshot.py"),
                   encoding="utf-8").read()
        self.assertIn("_m70_apply_hot_cold_load", src)
        self.assertIn("_m70_hot_cold_enabled", src)

    def test_44_snapshot_load_wired(self):
        """load() 已接线冷热加载。"""
        src = open(os.path.join(_ROOT, "nucleus/mnemosyne/PulseSnapshot.py"),
                   encoding="utf-8").read()
        self.assertIn("_nodes = self._m70_apply_hot_cold_load(_nodes)", src)

    def test_45_fast_ops_faiss_wired(self):
        """fast_ops 已接入 FAISS。"""
        src = open(os.path.join(_ROOT, "nucleus/fast_ops.py"),
                   encoding="utf-8").read()
        self.assertIn("ENABLE_FAISS_FAST_OPS", src)
        self.assertIn("get_faiss_store", src)
        self.assertIn("IndexFlatL2", src)

    def test_46_fast_ops_importable(self):
        """fast_ops 可导入且保留原接口。"""
        import nucleus.fast_ops as fo
        self.assertTrue(callable(fo.fast_vector_search))

    def test_47_kal_callsites_stomach(self):
        """胃模块有实际调用点方法。"""
        src = open(os.path.join(_ROOT, "organs/body/PulseStomach.py"),
                   encoding="utf-8").read()
        self.assertIn("_m70_kal_get_node", src)
        self.assertIn("_m70_kal_search_keywords", src)

    def test_48_kal_callsites_liver(self):
        """肝模块有实际调用点方法。"""
        src = open(os.path.join(_ROOT, "organs/body/PulseLiver.py"),
                   encoding="utf-8").read()
        self.assertIn("_m70_kal_get_node", src)
        self.assertIn("_m70_kal_node_count", src)

    def test_49_kal_callsites_kidney(self):
        """肾模块有实际调用点方法。"""
        src = open(os.path.join(_ROOT, "organs/body/PulseKidney.py"),
                   encoding="utf-8").read()
        self.assertIn("_m70_kal_get_node", src)
        self.assertIn("_m70_kal_search_by_level", src)

    def test_50_save_intervals_config(self):
        """分层保存频率配置存在。"""
        self.assertEqual(config.SNAPSHOT_HOT_SAVE_INTERVAL, 600)
        self.assertEqual(config.SNAPSHOT_WARM_SAVE_INTERVAL, 1800)
        self.assertEqual(config.SNAPSHOT_COLD_SAVE_INTERVAL, 3600)


# ===== 集成 =====

class TestIntegrationM70(unittest.TestCase):
    """第70批集成测试。"""

    def test_51_all_distributed_config(self):
        """分布式配置项齐全。"""
        for c in ("DISTRIBUTED_MODE", "DISTRIBUTED_NODE_ID",
                  "DISTRIBUTED_SHARD_COUNT", "DISTRIBUTED_REPLICA_COUNT",
                  "DISTRIBUTED_CONSISTENCY"):
            self.assertTrue(hasattr(config, c), f"Missing: {c}")

    def test_52_packages_importable(self):
        """三个新包均可导入。"""
        import importlib
        for m in ("nucleus.graph_store", "nucleus.timeseries_store",
                  "nucleus.distributed", "nucleus.distributed.sharding",
                  "nucleus.distributed.routing"):
            try:
                importlib.import_module(m)
            except Exception as e:
                self.fail(f"import {m} failed: {e}")

    def test_53_package_init_exists(self):
        """新包都有 __init__.py（铁律71）。"""
        for d in ("graph_store", "timeseries_store", "distributed"):
            p = os.path.join(_ROOT, "nucleus", d, "__init__.py")
            self.assertTrue(os.path.isfile(p), f"Missing: {p}")

    def test_54_no_f_errors(self):
        """新模块无 ruff F 错误。"""
        import subprocess
        ruff = r"D:\Program Files\Python312\Scripts\ruff.exe"
        files = [os.path.join(_ROOT, "nucleus/graph_store/neo4j_store.py"),
                 os.path.join(_ROOT, "nucleus/timeseries_store/influxdb_store.py"),
                 os.path.join(_ROOT, "nucleus/distributed/sharding.py"),
                 os.path.join(_ROOT, "nucleus/distributed/routing.py")]
        r = subprocess.run([ruff, "check", "--select", "F",
                            "--output-format=concise"] + files,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, f"ruff F: {r.stdout}")

    def test_55_defaults_all_off(self):
        """三个新功能默认全部关闭（安全要求 3.4）。"""
        self.assertFalse(config.ENABLE_NEO4J_GRAPH_STORE)
        self.assertFalse(config.ENABLE_INFLUXDB_TIMESERIES)
        self.assertFalse(config.ENABLE_DISTRIBUTED)


if __name__ == "__main__":
    unittest.main()
