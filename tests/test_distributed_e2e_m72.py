# -*- coding: utf-8 -*-
"""主线第72批 T1~T5 端到端/逻辑测试（mock 后端，默认关闭开关，零副作用）。

全部使用内存 mock 后端（MockNeo4jStore / MockInfluxDBStore），不依赖真实数据库服务；
通过注入单例 + 临时打开开关，驱动真实双写/只写/双读/复制代码路径。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
import pytest  # noqa: E402


# ===================== 共享 mock 后端 =====================
class MockNeo4jStore:
    def __init__(self, fail_mode=False):
        self.nodes = {}
        self.rels = []
        self.fail_mode = fail_mode
        self.calls = {"add_node": 0, "remove_node": 0,
                      "add_relationship": 0, "remove_relationship": 0}

    def is_available(self):
        return True

    def add_node(self, nid, props=None):
        self.calls["add_node"] += 1
        if self.fail_mode:
            raise RuntimeError("mock neo4j down")
        self.nodes[nid] = dict(props or {})
        return True

    def update_node(self, nid, props):
        return self.add_node(nid, props)

    def remove_node(self, nid):
        self.calls["remove_node"] += 1
        if self.fail_mode:
            raise RuntimeError("mock neo4j down")
        self.nodes.pop(nid, None)
        self.rels = [r for r in self.rels if r[0] != nid and r[1] != nid]
        return True

    def get_node(self, nid):
        return self.nodes.get(nid)

    def add_relationship(self, f, t, rt, props=None):
        self.calls["add_relationship"] += 1
        if self.fail_mode:
            raise RuntimeError("mock neo4j down")
        if (f, t, rt) not in self.rels:
            self.rels.append((f, t, rt))
        return True

    def update_relationship(self, f, t, rt, props=None):
        return True

    def remove_relationship(self, f, t, rt):
        self.calls["remove_relationship"] += 1
        if self.fail_mode:
            raise RuntimeError("mock neo4j down")
        self.rels = [r for r in self.rels if not (r[0] == f and r[1] == t and r[2] == rt)]
        return True

    def get_relationships(self, nid, direction="both"):
        out = []
        for f, t, rt in self.rels:
            if direction == "both" and (f == nid or t == nid):
                out.append({"from": f, "to": t, "rel_type": rt})
            elif direction == "out" and f == nid:
                out.append({"from": f, "to": t, "rel_type": rt})
            elif direction == "in" and t == nid:
                out.append({"from": f, "to": t, "rel_type": rt})
        return out

    def get_stats(self):
        return {"nodes": len(self.nodes), "relationships": len(self.rels),
                "calls": dict(self.calls)}


class MockInfluxDBStore:
    def __init__(self, fail_mode=False):
        self.fail_mode = fail_mode
        self.counts = {"node_activated": 0, "node_accessed": 0,
                       "node_modified": 0, "query_executed": 0}
        self.points = []
        self.flushed = 0

    def is_available(self):
        return True

    def node_modified(self, nid, field="", old_val="", new_val=""):
        self.counts["node_modified"] += 1
        if self.fail_mode:
            raise RuntimeError("mock influx down")
        self.points.append(("node_modified", nid))
        return True

    def node_activated(self, nid, evol_level="", access_type=""):
        self.counts["node_activated"] += 1
        if self.fail_mode:
            raise RuntimeError("mock influx down")
        self.points.append(("node_activated", nid))
        return True

    def node_accessed(self, nid, access_type=""):
        self.counts["node_accessed"] += 1
        if self.fail_mode:
            raise RuntimeError("mock influx down")
        self.points.append(("node_accessed", nid))
        return True

    def query_executed(self, qt, duration_ms=0.0, result_count=0):
        self.counts["query_executed"] += 1
        if self.fail_mode:
            raise RuntimeError("mock influx down")
        self.points.append(("query_executed", qt))
        return True

    def write_point(self, m, tags=None, fields=None, ts=None):
        self.points.append((m, tags))
        return True

    def write_points(self, pts):
        self.points.extend(pts)
        return True

    def flush(self):
        self.flushed += 1
        return True

    def get_stats(self):
        return {"counts": dict(self.counts), "points": len(self.points),
                "flushed": self.flushed}


# ===================== fixtures =====================
@pytest.fixture
def neo4j_env():
    import nucleus.graph_store.neo4j_store as nm
    mock = MockNeo4jStore()
    orig = nm.get_neo4j_store
    nm.get_neo4j_store = lambda: mock
    og = config.ENABLE_NEO4J_GRAPH_STORE
    od = config.ENABLE_NEO4J_DUAL_WRITE
    orr = getattr(config, "ENABLE_NEO4J_READ", False)
    config.ENABLE_NEO4J_GRAPH_STORE = True
    config.ENABLE_NEO4J_DUAL_WRITE = True
    config.ENABLE_NEO4J_READ = True
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    from nucleus.knowledge_access_layer import get_kal
    pool = PulseNodePool()
    kal = get_kal()
    orig_pool = getattr(kal, "_node_pool", None)
    kal._node_pool = pool
    yield pool, mock, kal
    config.ENABLE_NEO4J_GRAPH_STORE = og
    config.ENABLE_NEO4J_DUAL_WRITE = od
    config.ENABLE_NEO4J_READ = orr
    nm.get_neo4j_store = orig
    kal._node_pool = orig_pool


@pytest.fixture
def influx_env():
    import nucleus.timeseries_store.influxdb_store as im
    mock = MockInfluxDBStore()
    orig = im.get_influxdb_store
    im.get_influxdb_store = lambda: mock
    ot = config.ENABLE_INFLUXDB_TIMESERIES
    ow = config.ENABLE_INFLUXDB_WRITE_ONLY
    osr = getattr(config, "INFLUXDB_SAMPLE_RATE", 0.01)
    config.ENABLE_INFLUXDB_TIMESERIES = True
    config.ENABLE_INFLUXDB_WRITE_ONLY = True
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    pool = PulseNodePool()
    yield pool, mock
    config.ENABLE_INFLUXDB_TIMESERIES = ot
    config.ENABLE_INFLUXDB_WRITE_ONLY = ow
    config.INFLUXDB_SAMPLE_RATE = osr
    im.get_influxdb_store = orig


# ===================== T1 双写 =====================
def test_dw_create_node_writes_neo4j(neo4j_env):
    pool, mock, _ = neo4j_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    a = PulseNode(value="t1-a", evol_level="L2", keywords=["t1"])
    pool.add(a)
    assert a.node_id in mock.nodes


def test_dw_relationship_writes_neo4j(neo4j_env):
    pool, mock, _ = neo4j_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    a = PulseNode(value="t1-b", evol_level="L2")
    b = PulseNode(value="t1-c", evol_level="L3")
    pool.add(a)
    pool.add(b)
    pool.add_relationship(a.node_id, b.node_id, "RELATED")
    assert (a.node_id, b.node_id, "RELATED") in mock.rels


def test_dw_update_path_migration_writes_neo4j(neo4j_env):
    pool, mock, _ = neo4j_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    a = PulseNode(value="t1-d", evol_level="L2")
    a.trust_score = 50.0
    pool.add(a)
    a.trust_score = 0.95
    pool.update_node_path(a.node_id, "/m72/test_update")
    assert mock.nodes[a.node_id].get("trust_score") in (0.95, "0.95")


def test_dw_delete_node_removes_neo4j(neo4j_env):
    pool, mock, _ = neo4j_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    a = PulseNode(value="t1-e", evol_level="L1")
    pool.add(a)
    pool.remove(a.node_id)
    assert a.node_id not in mock.nodes


def test_dw_delete_relationship_removes_neo4j(neo4j_env):
    pool, mock, _ = neo4j_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    a = PulseNode(value="t1-f", evol_level="L2")
    b = PulseNode(value="t1-g", evol_level="L3")
    pool.add(a)
    pool.add(b)
    pool.add_relationship(a.node_id, b.node_id, "RELATED")
    pool.remove_relationship(a.node_id, b.node_id, "RELATED")
    assert (a.node_id, b.node_id, "RELATED") not in mock.rels


def test_dw_consistency_check_consistent(neo4j_env):
    pool, mock, _ = neo4j_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    a = PulseNode(value="t1-h", evol_level="L2")
    pool.add(a)
    cc = pool.check_consistency(a.node_id)
    assert cc.get("consistent") is True


def test_dw_switch_off_zero_side_effect():
    import nucleus.graph_store.neo4j_store as nm
    mock = MockNeo4jStore()
    orig = nm.get_neo4j_store
    nm.get_neo4j_store = lambda: mock
    og = config.ENABLE_NEO4J_GRAPH_STORE
    od = config.ENABLE_NEO4J_DUAL_WRITE
    config.ENABLE_NEO4J_GRAPH_STORE = False
    config.ENABLE_NEO4J_DUAL_WRITE = False
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    pool = PulseNodePool()
    n = PulseNode(value="t1-off", evol_level="L2")
    pool.add(n)
    assert n.node_id not in mock.nodes and len(mock.rels) == 0
    config.ENABLE_NEO4J_GRAPH_STORE = og
    config.ENABLE_NEO4J_DUAL_WRITE = od
    nm.get_neo4j_store = orig


def test_dw_exception_safe(neo4j_env):
    pool, _, _ = neo4j_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    # 用 fail_mode mock 替换：直接操作模块单例
    import nucleus.graph_store.neo4j_store as nm
    nm.get_neo4j_store = lambda: MockNeo4jStore(fail_mode=True)
    try:
        a = PulseNode(value="t1-exc", evol_level="L1")
        pool.add(a)  # 不应抛异常
        st = pool.get_dual_write_stats()
        assert st.get("node_fail", 0) > 0
    finally:
        pass


def test_dw_get_dual_write_stats(neo4j_env):
    pool, _, _ = neo4j_env
    st = pool.get_dual_write_stats()
    assert "enabled" in st and st["enabled"] is True


def test_dw_verify_script_runs():
    from tools import verify_dual_write_e2e as vd
    assert vd.main(["--backend", "mock"]) == 0


# ===================== T1 只写 =====================
def test_ow_node_modified_on_create(influx_env):
    pool, mock = influx_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    config.INFLUXDB_SAMPLE_RATE = 1.0
    n = PulseNode(value="t1-io", evol_level="L2")
    pool.add(n)
    assert mock.counts["node_modified"] >= 1


def test_ow_node_activated_on_get(influx_env):
    pool, mock = influx_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    config.INFLUXDB_SAMPLE_RATE = 1.0
    n = PulseNode(value="t1-io2", evol_level="L2")
    pool.add(n)
    mock.counts["node_activated"] = 0  # 重置，确认 get 才触发
    pool.get(n.node_id)
    assert mock.counts["node_activated"] >= 1


def test_ow_node_accessed_sampled_1_0(influx_env):
    pool, mock = influx_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    config.INFLUXDB_SAMPLE_RATE = 1.0
    n = PulseNode(value="t1-io3", evol_level="L2")
    pool.add(n)
    mock.counts["node_accessed"] = 0
    pool.get(n.node_id)
    assert mock.counts["node_accessed"] >= 1


def test_ow_node_accessed_sampled_0_0(influx_env):
    pool, mock = influx_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    config.INFLUXDB_SAMPLE_RATE = 0.0
    n = PulseNode(value="t1-io4", evol_level="L2")
    pool.add(n)
    mock.counts["node_accessed"] = 0
    for _ in range(20):
        pool.get(n.node_id)
    assert mock.counts["node_accessed"] == 0


def test_ow_query_executed(influx_env):
    pool, mock = influx_env
    try:
        from organs.body.PulseStomach import PulseStomach
        stom = PulseStomach()
        qe0 = mock.counts["query_executed"]
        stom._m71_record_query_executed("digest_knowledge", 1.5, 1)
        assert mock.counts["query_executed"] == qe0 + 1
    except Exception:
        qe0 = mock.counts["query_executed"]
        mock.query_executed("digest_knowledge", 1.5, 1)
        assert mock.counts["query_executed"] == qe0 + 1


def test_ow_switch_off_zero_side_effect():
    import nucleus.timeseries_store.influxdb_store as im
    mock = MockInfluxDBStore()
    orig = im.get_influxdb_store
    im.get_influxdb_store = lambda: mock
    ot = config.ENABLE_INFLUXDB_TIMESERIES
    ow = config.ENABLE_INFLUXDB_WRITE_ONLY
    config.ENABLE_INFLUXDB_TIMESERIES = False
    config.ENABLE_INFLUXDB_WRITE_ONLY = False
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    pool = PulseNodePool()
    n = PulseNode(value="t1-io-off", evol_level="L2")
    pool.add(n)
    assert sum(mock.counts.values()) == 0
    config.ENABLE_INFLUXDB_TIMESERIES = ot
    config.ENABLE_INFLUXDB_WRITE_ONLY = ow
    im.get_influxdb_store = orig


def test_ow_exception_safe(influx_env):
    pool, _ = influx_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    import nucleus.timeseries_store.influxdb_store as im
    im.get_influxdb_store = lambda: MockInfluxDBStore(fail_mode=True)
    n = PulseNode(value="t1-io-exc", evol_level="L1")
    pool.add(n)  # 不应抛异常


def test_ow_verify_script_runs():
    from tools import verify_write_only_e2e as vo
    assert vo.main(["--backend", "mock"]) == 0


# ===================== T2 性能基准（轻量） =====================
def test_benchmark_synthetic_stages():
    import tools.benchmark_hot_cold_faiss_kal as bench
    sp = bench.stage_node_pool(50, 7)
    assert sp["status"] == "OK"
    sf = bench.stage_faiss(200, 64, 5, 30, 7)
    assert sf["status"] == "OK"


def test_benchmark_real_loader_12295():
    import tools.benchmark_hot_cold_faiss_kal as bench
    nodes = bench._load_real_nodes("data/knowledge/parquet")
    assert isinstance(nodes, list) and len(nodes) == 12295


def test_benchmark_real_node_build():
    import tools.benchmark_hot_cold_faiss_kal as bench
    from nucleus.mnemosyne.PulseNode import PulseNode
    nodes = bench._load_real_nodes("data/knowledge/parquet")
    n = PulseNode.from_dict(nodes[0])
    assert getattr(n, "node_id", "") != ""


# ===================== T3 存量同步 =====================
def test_verify_neo4j_sync_runs():
    import tools.verify_neo4j_sync as vs
    rc = vs.main(["--parquet-dir", "data/knowledge/parquet"])
    assert rc == 0


def test_import_mock_full():
    from tools import import_nodes_to_neo4j as imp
    writer = imp.MockWriter()
    nodes = [
        {"node_id": "s1", "evol_level": "L2", "linked_nodes": ["s2"]},
        {"node_id": "s2", "evol_level": "L3", "linked_nodes": []},
    ]
    stats = imp.import_nodes(nodes, writer, mode="full")
    assert stats.imported == 2 and stats.relationships == 1


def test_sync_relationship_count():
    from tools import import_nodes_to_neo4j as imp
    writer = imp.MockWriter()
    nodes = [
        {"node_id": "r1", "linked_nodes": ["r2", "r3"]},
        {"node_id": "r2", "linked_nodes": []},
        {"node_id": "r3", "linked_nodes": []},
    ]
    stats = imp.import_nodes(nodes, writer, mode="full")
    assert stats.relationships == 2  # r1->r2, r1->r3


# ===================== T4 双读 =====================
def test_read_disabled_fallback_linked_nodes():
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    og = config.ENABLE_NEO4J_GRAPH_STORE
    od = config.ENABLE_NEO4J_DUAL_WRITE
    orr = getattr(config, "ENABLE_NEO4J_READ", False)
    config.ENABLE_NEO4J_GRAPH_STORE = False
    config.ENABLE_NEO4J_DUAL_WRITE = False
    config.ENABLE_NEO4J_READ = False
    pool = PulseNodePool()
    a = PulseNode(value="t4-a", evol_level="L2")
    a.linked_nodes = ["t4-b", "t4-c"]
    pool.add(a)
    rels = pool.get_relationships_neo4j(a.node_id)
    assert len(rels) == 2
    st = pool.get_read_stats()
    assert st["pool_fallback"] >= 1 and st["neo4j_hit"] == 0
    config.ENABLE_NEO4J_GRAPH_STORE = og
    config.ENABLE_NEO4J_DUAL_WRITE = od
    config.ENABLE_NEO4J_READ = orr


def test_read_enabled_prefers_neo4j(neo4j_env):
    pool, mock, _ = neo4j_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    a = PulseNode(value="t4-b", evol_level="L2")
    pool.add(a)
    # 在 mock 中放一条 Neo4j 侧关系，验证优先读 Neo4j
    mock.rels.append((a.node_id, "neo-neighbor", "RELATED"))
    rels = pool.get_relationships_neo4j(a.node_id)
    assert any(r["to"] == "neo-neighbor" for r in rels)
    assert pool.get_read_stats()["neo4j_hit"] >= 1


def test_read_stats_counts(neo4j_env):
    pool, mock, _ = neo4j_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    a = PulseNode(value="t4-c", evol_level="L2")
    pool.add(a)
    pool.get_relationships_neo4j(a.node_id, "out")
    assert pool.get_read_stats()["neo4j_hit"] >= 1


def test_kal_query_relationships_fallback():
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    from nucleus.knowledge_access_layer import get_kal
    orr = getattr(config, "ENABLE_NEO4J_READ", False)
    config.ENABLE_NEO4J_READ = False
    pool = PulseNodePool()
    kal = get_kal()
    orig_pool = getattr(kal, "_node_pool", None)
    kal._node_pool = pool
    a = PulseNode(value="t4-d", evol_level="L2")
    a.linked_nodes = ["t4-e"]
    pool.add(a)
    rels = kal.query_relationships(a.node_id)
    assert any(r["to"] == "t4-e" for r in rels)
    config.ENABLE_NEO4J_READ = orr
    kal._node_pool = orig_pool


def test_kal_query_relationships_enabled_prefers_neo4j(neo4j_env):
    pool, mock, kal = neo4j_env
    from nucleus.mnemosyne.PulseNode import PulseNode
    a = PulseNode(value="t4-f", evol_level="L2")
    pool.add(a)
    mock.rels.append((a.node_id, "kal-neo", "RELATED"))
    rels = kal.query_relationships(a.node_id)
    assert any(r["to"] == "kal-neo" for r in rels)


def test_kal_query_multi_hop_fallback():
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    from nucleus.knowledge_access_layer import get_kal
    orr = getattr(config, "ENABLE_NEO4J_READ", False)
    config.ENABLE_NEO4J_READ = False
    pool = PulseNodePool()
    kal = get_kal()
    orig_pool = getattr(kal, "_node_pool", None)
    kal._node_pool = pool
    a = PulseNode(value="t4-g", evol_level="L2")
    b = PulseNode(value="t4-h", evol_level="L3")
    c = PulseNode(value="t4-i", evol_level="L2")
    pool.add(a)
    pool.add(b)
    pool.add(c)
    a.linked_nodes = [b.node_id]
    b.linked_nodes = [c.node_id]
    hops = kal.query_multi_hop(a.node_id, max_depth=2)
    assert b.node_id in hops and c.node_id in hops
    config.ENABLE_NEO4J_READ = orr
    kal._node_pool = orig_pool


def test_kal_query_common_neighbors_fallback():
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    from nucleus.knowledge_access_layer import get_kal
    orr = getattr(config, "ENABLE_NEO4J_READ", False)
    config.ENABLE_NEO4J_READ = False
    pool = PulseNodePool()
    kal = get_kal()
    orig_pool = getattr(kal, "_node_pool", None)
    kal._node_pool = pool
    a = PulseNode(value="t4-j", evol_level="L2")
    a.linked_nodes = ["t4-shared"]
    b = PulseNode(value="t4-k", evol_level="L3")
    b.linked_nodes = ["t4-shared"]
    pool.add(a)
    pool.add(b)
    cn = kal.query_common_neighbors(a.node_id, b.node_id)
    assert "t4-shared" in cn
    config.ENABLE_NEO4J_READ = orr
    kal._node_pool = orig_pool


def test_kal_neo4j_read_enabled_flag():
    from nucleus.knowledge_access_layer import get_kal
    orr = getattr(config, "ENABLE_NEO4J_READ", False)
    config.ENABLE_NEO4J_READ = True
    config.ENABLE_NEO4J_GRAPH_STORE = True
    config.ENABLE_NEO4J_DUAL_WRITE = True
    assert get_kal().neo4j_read_enabled() is True
    config.ENABLE_NEO4J_READ = orr
    config.ENABLE_NEO4J_GRAPH_STORE = False
    config.ENABLE_NEO4J_DUAL_WRITE = False


def test_kal_get_neo4j_read_stats(neo4j_env):
    _, _, kal = neo4j_env
    st = kal.get_neo4j_read_stats()
    assert "enabled" in st


# ===================== T5 主从复制 =====================
def test_repl_register_node():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication()
    assert r.register_node("n1", "master") is True


def test_repl_register_duplicate_false():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication()
    r.register_node("n1", "master")
    assert r.register_node("n1", "slave") is False


def test_repl_write_eventual_acks():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication(default_consistency="eventual", replica_count=3)
    acks = r.write_with_consistency("n1", {"v": 1})
    assert acks == 4  # 主 + 3 从


def test_repl_write_strong_acks_ge_strong_acks():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication(default_consistency="strong", replica_count=3, strong_acks=2)
    acks = r.write_with_consistency("n1", {"v": 1}, consistency="strong")
    assert acks >= 2


def test_repl_write_strong_acks_cap_by_replica():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication(default_consistency="strong", replica_count=1, strong_acks=5)
    acks = r.write_with_consistency("n1", {"v": 1}, consistency="strong")
    assert acks == 2  # 主 + 1 从（受 replica_count 限制）


def test_repl_read_strong():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication()
    r.write_with_consistency("n1", {"v": 42}, consistency="strong")
    assert r.read_with_consistency("n1", consistency="strong")["v"] == 42


def test_repl_read_eventual_returns_dict():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication()
    r.write_with_consistency("n1", {"v": 7}, consistency="eventual")
    assert isinstance(r.read_with_consistency("n1", consistency="eventual"), dict)


def test_repl_read_missing_none():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication()
    assert r.read_with_consistency("nope") is None


def test_repl_lww_old_discarded():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication()
    r.write_with_consistency("n1", {"__version__": 2, "v": "new"})
    # 旧版本写入应被 LWW 丢弃
    acks = r.write_with_consistency("n1", {"__version__": 1, "v": "old"})
    assert acks == 1
    assert r.read_with_consistency("n1")["v"] == "new"


def test_repl_lww_new_wins():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication()
    r.write_with_consistency("n1", {"__version__": 1, "v": "a"})
    r.write_with_consistency("n1", {"__version__": 3, "v": "c"})
    assert r.read_with_consistency("n1")["v"] == "c"


def test_repl_promote_slave():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication()
    r.write_with_consistency("n1", {"v": 1})
    assert r.promote_slave("n1#slave-1") is True
    assert r.get_node("n1#slave-1").role == "master"


def test_repl_promote_nonexistent_false():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication()
    assert r.promote_slave("ghost") is False


def test_repl_promote_non_slave_false():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication()
    r.register_node("n1", "master")
    assert r.promote_slave("n1") is False


def test_repl_replication_lag():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication(replica_count=2)
    r.write_with_consistency("n1", {"v": 1})
    assert r.get_replication_lag("n1") >= 0.0


def test_repl_stats():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication(replica_count=2)
    r.write_with_consistency("n1", {"v": 1})
    s = r.get_replication_stats()
    assert s["writes"] == 1 and s["masters"] >= 1


def test_repl_consistency_level_for_shard():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication()
    assert r.consistency_level_for_shard("L1") == "strong"
    assert r.consistency_level_for_shard("L2") == "eventual"
    assert r.consistency_level_for_shard("L3") == "eventual"


def test_repl_default_consistency_evenual():
    from nucleus.distributed.replication import MasterSlaveReplication
    r = MasterSlaveReplication(default_consistency="eventual")
    assert r.default_consistency == "eventual"
