# -*- coding: utf-8 -*-
"""第71批测试：Neo4j双写接线 + InfluxDB只写落地 + 分布式节点注册表/路由/心跳
+ 性能实测工具 + 全量导入工具 + 设计文档/债务清单更新验证。

★默认开关全部关闭 → 全部走"未启用/零副作用"分支。
  验证的是接口存在性、默认关闭、双写/只写逻辑在关闭时不写库、导入工具的
  全量/增量/断点续传/去重/checkpoint 正确性（mock 后端），不依赖真实数据库。
"""
import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 避免测试中触发真实持久化
config.NODE_REGISTRY_PATH = os.path.join(tempfile.gettempdir(), "m71_test_registry.json")


# ===== 配置默认值（第71批新增 7 项）=====
class TestM71Config(unittest.TestCase):
    def test_01_neo4j_dual_write_off(self):
        self.assertFalse(config.ENABLE_NEO4J_DUAL_WRITE)

    def test_02_influx_write_only_off(self):
        self.assertFalse(config.ENABLE_INFLUXDB_WRITE_ONLY)

    def test_03_sample_rate_default(self):
        self.assertAlmostEqual(config.INFLUXDB_SAMPLE_RATE, 0.01, places=4)

    def test_04_simulation_mode(self):
        self.assertTrue(config.DISTRIBUTED_SIMULATION_MODE)

    def test_05_heartbeat_interval(self):
        self.assertEqual(config.DISTRIBUTED_HEARTBEAT_INTERVAL, 30)

    def test_06_health_threshold(self):
        self.assertAlmostEqual(config.DISTRIBUTED_HEALTH_THRESHOLD, 0.5, places=4)

    def test_07_node_registry_path(self):
        self.assertIsInstance(config.NODE_REGISTRY_PATH, str)

    def test_08_three_feature_switches_off(self):
        self.assertFalse(config.ENABLE_NEO4J_GRAPH_STORE)
        self.assertFalse(config.ENABLE_INFLUXDB_TIMESERIES)
        self.assertFalse(config.ENABLE_DISTRIBUTED)

    def test_09_marker_present_in_config(self):
        src = open(os.path.join(_ROOT, "config.py"), encoding="utf-8").read()
        self.assertIn("M71-CFG", src)


# ===== T1: PulseNodePool 双写（关闭时不写库）=====
def _import_pool():
    if not hasattr(_import_pool, "_pool"):
        try:
            from nucleus.mnemosyne.PulseNodePool import PulseNodePool
            _import_pool._pool = PulseNodePool()
        except Exception:
            _import_pool._pool = None
    return _import_pool._pool


class TestT1Neo4jDualWritePool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool = _import_pool()
        if cls.pool is None:
            raise unittest.SkipTest("PulseNodePool 不可用")

    def test_10_new_methods_exist(self):
        for m in ("add_relationship", "remove_relationship",
                  "sync_node_relationships", "get_dual_write_stats",
                  "check_consistency", "full_consistency_check"):
            self.assertTrue(hasattr(self.pool, m), m)

    def test_11_dual_write_disabled(self):
        self.assertFalse(self.pool._m71_dw_enabled())

    def test_12_stats_structure(self):
        st = self.pool.get_dual_write_stats()
        for k in ("enabled", "node_success", "node_fail",
                  "rel_success", "rel_fail", "last_error"):
            self.assertIn(k, st)
        self.assertFalse(st["enabled"])
        self.assertEqual(st["node_success"], 0)

    def test_13_add_relationship_safe_when_off(self):
        # 关闭时仍返回 True（接口成功），但不触发写库（统计为 0）
        self.assertTrue(self.pool.add_relationship("a", "b", "RELATED"))
        self.assertEqual(self.pool.get_dual_write_stats()["rel_success"], 0)

    def test_14_remove_relationship_safe_when_off(self):
        self.assertTrue(self.pool.remove_relationship("a", "b", "RELATED"))
        self.assertEqual(self.pool.get_dual_write_stats()["rel_fail"], 0)

    def test_15_sync_node_relationships_off(self):
        self.assertEqual(self.pool.sync_node_relationships("nope"), 0)

    def test_16_check_consistency_off(self):
        r = self.pool.check_consistency("nope")
        self.assertEqual(r["node_id"], "nope")
        self.assertFalse(r["in_pool"])
        self.assertIn("双写未开启", r["detail"])

    def test_17_full_consistency_off(self):
        r = self.pool.full_consistency_check()
        self.assertFalse(r["enabled"])
        self.assertIn("note", r)

    def test_18_add_relationship_invalid(self):
        self.assertFalse(self.pool.add_relationship("", "b", "RELATED"))
        self.assertFalse(self.pool.add_relationship("a", "", "RELATED"))

    def test_19_sample_rate_zero_no_write(self):
        old = config.INFLUXDB_SAMPLE_RATE
        config.INFLUXDB_SAMPLE_RATE = 0.0
        try:
            self.assertFalse(self.pool._m71_influx_sample())
        finally:
            config.INFLUXDB_SAMPLE_RATE = old

    def test_20_sample_rate_one_full(self):
        old = config.INFLUXDB_SAMPLE_RATE
        config.INFLUXDB_SAMPLE_RATE = 1.0
        try:
            self.assertTrue(self.pool._m71_influx_sample())
        finally:
            config.INFLUXDB_SAMPLE_RATE = old

    def test_21_influx_disabled(self):
        self.assertFalse(self.pool._m71_influx_enabled())

    def test_22_influx_write_safe_when_off(self):
        # 关闭时不写库、不抛异常
        try:
            self.pool._m71_influx_write("node_activated", "n1", "L1", "test")
        except Exception as e:
            self.fail("influx write raised when off: %s" % e)


# ===== T1: KAL 双写接口 =====
class TestT1KALInterface(unittest.TestCase):
    def setUp(self):
        from nucleus.knowledge_access_layer import get_kal
        self.kal = get_kal()

    def test_23_kal_dual_write_disabled(self):
        self.assertFalse(self.kal.neo4j_dual_write_enabled())

    def test_24_kal_stats(self):
        st = self.kal.get_neo4j_dual_write_stats()
        self.assertIn("enabled", st)

    def test_25_kal_sync_returns_int(self):
        self.assertEqual(self.kal.sync_node_to_neo4j("x"), 0)

    def test_26_kal_save_node_fix_resolves_add(self):
        # 修复前：_f 解析 add_node/update_node 均不存在 -> None -> 返回 False
        # 修复后：回退到真实 add 方法（若池存在）。这里只验证解析链包含 add。
        import nucleus.mnemosyne.PulseNodePool as pnp
        self.assertTrue(hasattr(pnp.PulseNodePool, "add"))


# ===== T2: PulseStomach 接线（源码核查 + 方法存在）=====
class TestT2StomachWiring(unittest.TestCase):
    def setUp(self):
        self.src = open(os.path.join(_ROOT, "organs/body/PulseStomach.py"),
                       encoding="utf-8").read()

    def test_27_marker_present(self):
        self.assertIn("M71-T2-STOMACH", self.src)

    def test_28_influx_helper_methods(self):
        self.assertIn("_m71_influx_enabled", self.src)
        self.assertIn("_m71_influx_store", self.src)
        self.assertIn("_m71_record_query_executed", self.src)

    def test_29_query_executed_hook(self):
        # _on_digest_knowledge 内部挂钩 _m71_record_query_executed
        self.assertIn("_m71_record_query_executed(", self.src)

    def test_30_method_callable_on_instance(self):
        from organs.body.PulseStomach import PulseStomach
        # 仅验证方法存在（不实例化以避免重依赖）
        self.assertTrue(hasattr(PulseStomach, "_m71_record_query_executed"))


# ===== T3: 分布式 节点注册表 / 心跳 / 路由 =====
class TestT3NodeRegistry(unittest.TestCase):
    def setUp(self):
        from nucleus.distributed import node_registry as nr
        self.nr = nr.NodeRegistry(
            path=os.path.join(tempfile.gettempdir(), "m71_reg.json"),
            auto_persist=False)

    def test_31_sim_nodes_three(self):
        ids = [n["node_id"] for n in self.nr.get_active_nodes()]
        self.assertEqual(len(ids), 3)
        self.assertIn("node-1", ids)

    def test_32_load_stats_structure(self):
        st = self.nr.get_load_stats()
        self.assertIn("total", st)
        self.assertIn("active", st)
        self.assertIn("shards_covered", st)

    def test_33_register_unregister(self):
        self.nr.register("extra-1", "localhost", 6001)
        self.assertIsNotNone(self.nr.get_node("extra-1"))
        self.assertTrue(self.nr.unregister("extra-1"))
        # unregister 为软注销：保留注册信息并标记 inactive
        node = self.nr.get_node("extra-1")
        self.assertIsNotNone(node)
        self.assertEqual(node["status"], "inactive")
        self.assertNotIn("extra-1", [n["node_id"] for n in self.nr.get_active_nodes()])

    def test_34_get_nodes_by_shard(self):
        nodes = self.nr.get_nodes_by_shard(0)
        self.assertIsInstance(nodes, list)

    def test_35_update_status(self):
        self.nr.update_node_status("node-1", status="degraded")
        self.assertEqual(self.nr.get_node("node-1")["status"], "degraded")

    def test_36_mark_heartbeat(self):
        self.assertTrue(self.nr.mark_heartbeat("node-1", health_score=0.9))
        node = self.nr.get_node("node-1")
        self.assertIn("last_heartbeat", node)


class TestT3Heartbeat(unittest.TestCase):
    def setUp(self):
        from nucleus.distributed import heartbeat as hb
        self.hm = hb.HeartbeatManager()

    def test_37_health_healthy(self):
        import time
        s = self.hm.compute_health_score(
            "node-1", time.time() - 0.01,
            cpu_usage=0.2, memory_usage=0.1, queue_depth=5)
        self.assertGreater(s, 0.8)

    def test_38_health_bad(self):
        import time
        s = self.hm.compute_health_score(
            "node-1", time.time() - 1000,
            cpu_usage=0.95, memory_usage=0.9, queue_depth=95)
        self.assertLess(s, 0.2)

    def test_39_health_range(self):
        s = self.hm.compute_health_score(
            "node-1", 0, cpu_usage=0.5, memory_usage=0.5, queue_depth=50)
        self.assertGreaterEqual(s, 0.0)
        self.assertLessEqual(s, 1.0)

    def test_40_check_health(self):
        res = self.hm.check_health("node-1")
        self.assertIsInstance(res, dict)
        self.assertIn("node_id", res)


class TestT3Routing(unittest.TestCase):
    def setUp(self):
        from nucleus.distributed import routing as rt
        self.rt = rt
        self.router = rt.build_simulation_router()

    def test_41_build_simulation_router(self):
        self.assertEqual(len(self.router._nodes), 3)

    def test_42_register_local_handler(self):
        called = {}

        def h(r, d):
            called["x"] = (r, d)

        self.router.register_local_handler("node-1", h)
        self.assertIn("node-1", self.router._local_handlers)

    def test_43_forward_to_local_handler(self):
        called = {}

        def h(r, d):
            called["hit"] = (r, d)

        self.router.register_local_handler("node-1", h)
        old = config.ENABLE_DISTRIBUTED
        config.ENABLE_DISTRIBUTED = True
        try:
            res = self.router.forward_request("node-1", "ping", {"a": 1})
        finally:
            config.ENABLE_DISTRIBUTED = old
        # 本地处理器被实际调用：called 命中且返回状态为 ok
        self.assertIn("hit", called)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("node"), "node-1")

    def test_44_forward_no_route(self):
        old = config.ENABLE_DISTRIBUTED
        config.ENABLE_DISTRIBUTED = True
        try:
            res = self.router.forward_request("node-999", "ping")
        finally:
            config.ENABLE_DISTRIBUTED = old
        self.assertEqual(res.get("status"), "no_route")

    def test_45_load_stats(self):
        st = self.router.get_load_stats()
        self.assertIsInstance(st, dict)


# ===== T4: 性能实测工具 =====
class TestT4BenchmarkTool(unittest.TestCase):
    def test_46_tool_importable(self):
        import importlib
        m = importlib.import_module("tools.benchmark_hot_cold_faiss_kal")
        for fn in ("main", "stage_node_pool", "stage_faiss", "stage_kal"):
            self.assertTrue(callable(getattr(m, fn)), fn)

    def test_47_tool_cli_runs(self):
        import subprocess
        out = os.path.join(tempfile.gettempdir(), "m71_bench.json")
        r = subprocess.run(
            [sys.executable,
             os.path.join(_ROOT, "tools/benchmark_hot_cold_faiss_kal.py"),
             "--nodes", "200", "--faiss-count", "200", "--faiss-queries", "50",
             "--json-out", out],
            capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[:500])
        self.assertTrue(os.path.isfile(out))
        res = json.load(open(out, encoding="utf-8"))
        self.assertIn("stages", res)
        for k in ("node_pool", "faiss", "kal"):
            self.assertIn(k, res["stages"])

    def test_48_tool_cli_help(self):
        import subprocess
        r = subprocess.run(
            [sys.executable,
             os.path.join(_ROOT, "tools/benchmark_hot_cold_faiss_kal.py"),
             "--help"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)


# ===== T5: 全量导入工具 =====
class TestT5ImportTool(unittest.TestCase):
    def _sample_nodes(self):
        return [
            {"node_id": "n1", "evol_level": "L1", "linked_nodes": ["n2", "n3"]},
            {"node_id": "n2", "evol_level": "L2", "linked_nodes": ["n1"]},
            {"node_id": "n3", "evol_level": "L3", "linked_nodes": []},
        ]

    def test_49_mock_writer(self):
        from tools.import_nodes_to_neo4j import MockWriter, import_nodes
        w = MockWriter()
        stats = import_nodes(self._sample_nodes(), w, mode="full")
        self.assertEqual(stats.imported, 3)
        self.assertEqual(len(w.nodes), 3)
        self.assertEqual(len(w.rels), 3)  # n1->n2,n3 ; n2->n1

    def test_50_dry_run_no_write(self):
        from tools.import_nodes_to_neo4j import MockWriter, import_nodes
        w = MockWriter()
        stats = import_nodes(self._sample_nodes(), w, mode="full", dry_run=True)
        self.assertEqual(stats.imported, 3)
        self.assertEqual(len(w.nodes), 0)

    def test_51_checkpoint_resume_skip(self):
        from tools.import_nodes_to_neo4j import (
            MockWriter, import_nodes, load_checkpoint)
        ck = os.path.join(tempfile.gettempdir(), "m71_ckpt.json")
        if os.path.isfile(ck):
            os.remove(ck)
        w = MockWriter()
        import_nodes(self._sample_nodes(), w, checkpoint_path=ck, mode="full")
        # 第二次 full 重置 checkpoint -> 重新导入
        w2 = MockWriter()
        s2 = import_nodes(self._sample_nodes(), w2, checkpoint_path=ck, mode="full")
        self.assertEqual(s2.imported, 3)
        # 第三次 resume -> 全部跳过
        w3 = MockWriter()
        s3 = import_nodes(self._sample_nodes(), w3, checkpoint_path=ck, mode="resume")
        self.assertEqual(s3.skipped, 3)
        self.assertEqual(s3.imported, 0)
        ids, _ = load_checkpoint(ck)
        self.assertEqual(len(ids), 3)

    def test_52_incremental_only_new(self):
        from tools.import_nodes_to_neo4j import MockWriter, import_nodes
        ck = os.path.join(tempfile.gettempdir(), "m71_ckpt_inc.json")
        if os.path.isfile(ck):
            os.remove(ck)
        # 先 full 导入前两个
        initial = self._sample_nodes()[:2]
        w = MockWriter()
        import_nodes(initial, w, checkpoint_path=ck, mode="full")
        # 增量导入全部三个 -> 只新增第3个
        w2 = MockWriter()
        s = import_nodes(self._sample_nodes(), w2, checkpoint_path=ck,
                         mode="incremental")
        self.assertEqual(s.imported, 1)
        self.assertEqual(s.skipped, 2)

    def test_53_dedup_within_source(self):
        from tools.import_nodes_to_neo4j import MockWriter, import_nodes
        nodes = self._sample_nodes() + [{"node_id": "n1", "evol_level": "L1"}]
        w = MockWriter()
        stats = import_nodes(nodes, w, mode="full")
        # n1 重复：第二次命中 checkpoint（imported 后已记录），不重复写
        self.assertEqual(stats.imported, 3)
        self.assertEqual(len(w.nodes), 3)

    def test_54_invalid_node_skipped(self):
        from tools.import_nodes_to_neo4j import MockWriter, import_nodes
        nodes = [{"evol_level": "L1"}, {"node_id": "ok"}]
        w = MockWriter()
        stats = import_nodes(nodes, w, mode="full")
        self.assertEqual(stats.failed, 1)
        self.assertEqual(stats.imported, 1)

    def test_55_collect_from_json(self):
        from tools.import_nodes_to_neo4j import collect_nodes
        p = os.path.join(tempfile.gettempdir(), "m71_nodes.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self._sample_nodes(), f)
        nodes = collect_nodes(p)
        self.assertEqual(len(nodes), 3)

    def test_56_build_writer_mock(self):
        from tools.import_nodes_to_neo4j import build_writer, MockWriter
        w = build_writer(use_mock=True, require_real=False)
        self.assertIsInstance(w, MockWriter)

    def test_57_main_cli_mock(self):
        import subprocess
        p = os.path.join(tempfile.gettempdir(), "m71_nodes_cli.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self._sample_nodes(), f)
        out = os.path.join(tempfile.gettempdir(), "m71_out.json")
        ck = os.path.join(tempfile.gettempdir(), "m71_cli_ckpt.json")
        r = subprocess.run([
            sys.executable,
            os.path.join(_ROOT, "tools/import_nodes_to_neo4j.py"),
            "--source", p, "--mode", "full", "--mock",
            "--checkpoint", ck, "--json-out", out],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(out))
        result = json.load(open(out, encoding="utf-8"))
        self.assertEqual(result["imported"], 3)
        self.assertEqual(result["backend"], "mock")

    def test_58_main_empty_source(self):
        import subprocess
        r = subprocess.run([
            sys.executable,
            os.path.join(_ROOT, "tools/import_nodes_to_neo4j.py"),
            "--source", "pool", "--mock"],
            capture_output=True, text=True, timeout=120)
        # 无 live 池 -> 返回 0（空）而非崩溃
        self.assertEqual(r.returncode, 0)

    def test_59_node_to_properties(self):
        from tools.import_nodes_to_neo4j import node_to_properties
        props = node_to_properties({"node_id": "n1", "evol_level": "L1",
                                    "keywords": "a,b", "value": "x" * 500})
        self.assertEqual(props["node_id"], "n1")
        self.assertEqual(props["evol_level"], "L1")
        self.assertIn("value_preview", props)
        self.assertEqual(len(props["value_preview"]), 200)


# ===== 集成：文档更新状态 =====
# ★第96批 T-96f（D95-7）：3 个分布式设计文档已从根目录 docs/ 移入
#   docs/设计文档/（Neo4j、分布式还在「暂缓考虑/」子目录），而这些用例仍按
#   **根路径**断言 ⇒ FileNotFoundError。
#   ⇒ 改为**递归定位**（任务书改法2：更鲁棒）——今后再调整目录层级也不会失配。
def _m96_locate_doc(filename: str) -> str:
    """在 docs/ 下递归查找指定文档，返回最浅命中路径；未找到返回空串。"""
    hits = []
    for _dp, _dn, _fn in os.walk(os.path.join(_ROOT, "docs")):
        if filename in _fn:
            hits.append(os.path.join(_dp, filename))
    return sorted(hits, key=len)[0] if hits else ""


class TestM71Docs(unittest.TestCase):
    def test_60_design_docs_present(self):
        for f in ("Neo4j图数据库集成设计_20260917.md",
                  "InfluxDB时序数据库集成设计_20260917.md",
                  "分布式架构设计文档_20260917.md"):
            self.assertTrue(_m96_locate_doc(f),
                            f"设计文档缺失（已递归搜索 docs/）：{f}")

    def test_61_neo4j_doc_has_71_section(self):
        p = _m96_locate_doc("Neo4j图数据库集成设计_20260917.md")
        self.assertTrue(p, "Neo4j 设计文档未找到")
        src = open(p, encoding="utf-8").read()
        self.assertIn("第71批", src)

    def test_62_influx_doc_has_71_section(self):
        p = _m96_locate_doc("InfluxDB时序数据库集成设计_20260917.md")
        self.assertTrue(p, "InfluxDB 设计文档未找到")
        src = open(p, encoding="utf-8").read()
        self.assertIn("第71批", src)

    def test_63_distributed_doc_has_71_section(self):
        p = _m96_locate_doc("分布式架构设计文档_20260917.md")
        self.assertTrue(p, "分布式架构设计文档未找到")
        src = open(p, encoding="utf-8").read()
        self.assertIn("第71批", src)

    def test_64_debt_list_has_166(self):
        p = os.path.join(_ROOT, "docs/完整进化路线与技术债务清单_v1.0.md")
        src = open(p, encoding="utf-8").read()
        self.assertIn("第一百六十六章", src)

    def test_65_debt_list_has_71_delivery(self):
        p = os.path.join(_ROOT, "docs/完整进化路线与技术债务清单_v1.0.md")
        src = open(p, encoding="utf-8").read()
        self.assertIn("第71批交付结论", src)


# ===== 集成：ruff F 干净 =====
class TestM71RuffF(unittest.TestCase):
    def test_66_no_f_errors(self):
        import subprocess
        ruff = r"D:\Program Files\Python312\Scripts\ruff.exe"
        files = [
            os.path.join(_ROOT, "nucleus/mnemosyne/PulseNodePool.py"),
            os.path.join(_ROOT, "organs/body/PulseStomach.py"),
            os.path.join(_ROOT, "nucleus/knowledge_access_layer.py"),
            os.path.join(_ROOT, "nucleus/distributed/routing.py"),
            os.path.join(_ROOT, "nucleus/distributed/node_registry.py"),
            os.path.join(_ROOT, "nucleus/distributed/heartbeat.py"),
            os.path.join(_ROOT, "tools/benchmark_hot_cold_faiss_kal.py"),
            os.path.join(_ROOT, "tools/import_nodes_to_neo4j.py"),
        ]
        r = subprocess.run([ruff, "check", "--select", "F",
                            "--output-format=concise"] + files,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, f"ruff F:\n{r.stdout}\n{r.stderr}")


if __name__ == "__main__":
    unittest.main()
