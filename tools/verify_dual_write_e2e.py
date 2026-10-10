# -*- coding: utf-8 -*-
"""主线第72批 T1：Neo4j 双写端到端验证（真实双写代码路径 + mock/真实后端）。

设计
----
- 默认 ``--backend mock``：把一个 **内存 MockNeo4jStore** 注入到
  ``nucleus.graph_store.neo4j_store.get_neo4j_store`` 单例，并临时打开
  ``ENABLE_NEO4J_GRAPH_STORE`` + ``ENABLE_NEO4J_DUAL_WRITE``，
  从而驱动 **真实的** ``PulseNodePool`` 双写方法（``add`` / ``remove`` /
  ``add_relationship`` / ``update`` → ``_dual_write_neo4j_node`` /
  ``_dual_write_neo4j_relationship``），验证节点 + 关系确实"双写"到图库，
  且"关闭开关"时零副作用。
- ``--backend real``：使用真实 ``Neo4jStore``（需用户已安装驱动 + 启动服务，
  见 ``setup_local_databases.py``）。无服务时自动降级 mock 并说明。

验证场景
--------
1. 创建节点 A/B → 图库中出现 A、B（双写成功）
2. 添加关系 A-RELATED->B → 图库关系存在
3. 更新节点 A → 图库属性同步
4. 删除节点 A → 图库节点 + 关系被 DETACH DELETE 移除
5. 一致性检查：pool 与 图库 节点字段一致
6. 异常场景：图库不可用（mock 抛异常）→ 主流程不抛异常，双写统计记失败
7. 开关关闭：双写不执行，零副作用

退出码：全部通过=0；任一失败=1。绝不编造通过结果。
"""
import argparse
import os
import sys

from nucleus._silent_except import silent_exc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = []
FAIL = []


def _check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("[PASS] {}{}".format(name, ("  " + detail) if detail else ""))
    else:
        FAIL.append(name)
        print("[FAIL] {}{}".format(name, ("  " + detail) if detail else ""))


# ===== 内存 Mock Neo4j 后端（复用真实 Neo4jStore 接口）=====
class MockNeo4jStore:
    def __init__(self, fail_mode=False):
        self.nodes = {}
        self.rels = []  # (from, to, rel_type)
        self.fail_mode = fail_mode
        self.calls = {"add_node": 0, "remove_node": 0,
                      "add_relationship": 0, "remove_relationship": 0}

    def is_available(self):
        return True

    def add_node(self, node_id, properties=None):
        self.calls["add_node"] += 1
        if self.fail_mode:
            raise RuntimeError("mock neo4j unavailable")
        self.nodes[node_id] = dict(properties or {})
        return True

    def update_node(self, node_id, properties):
        self.calls["add_node"] += 1
        if self.fail_mode:
            raise RuntimeError("mock neo4j unavailable")
        self.nodes[node_id] = dict(properties or {})
        return True

    def remove_node(self, node_id):
        self.calls["remove_node"] += 1
        if self.fail_mode:
            raise RuntimeError("mock neo4j unavailable")
        self.nodes.pop(node_id, None)
        self.rels = [r for r in self.rels if r[0] != node_id and r[1] != node_id]
        return True

    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def add_relationship(self, from_id, to_id, rel_type, properties=None):
        self.calls["add_relationship"] += 1
        if self.fail_mode:
            raise RuntimeError("mock neo4j unavailable")
        if (from_id, to_id, rel_type) not in self.rels:
            self.rels.append((from_id, to_id, rel_type))
        return True

    def update_relationship(self, from_id, to_id, rel_type, properties=None):
        return True

    def remove_relationship(self, from_id, to_id, rel_type):
        self.calls["remove_relationship"] += 1
        if self.fail_mode:
            raise RuntimeError("mock neo4j unavailable")
        self.rels = [r for r in self.rels
                     if not (r[0] == from_id and r[1] == to_id and r[2] == rel_type)]
        return True

    def get_relationships(self, node_id, direction="both"):
        out = []
        for f, t, rt in self.rels:
            if direction == "both" and (f == node_id or t == node_id):
                out.append({"from": f, "to": t, "rel_type": rt})
            elif direction == "out" and f == node_id:
                out.append({"from": f, "to": t, "rel_type": rt})
            elif direction == "in" and t == node_id:
                out.append({"from": f, "to": t, "rel_type": rt})
        return out

    def get_stats(self):
        return {"nodes": len(self.nodes), "relationships": len(self.rels),
                "calls": dict(self.calls)}


def _build_real_writer():
    from nucleus.graph_store.neo4j_store import get_neo4j_store
    store = get_neo4j_store()
    if not store.is_available():
        raise RuntimeError("真实 Neo4j 不可用（驱动未装/开关关闭/服务未起）。请改用默认 mock 或先 setup_local_databases.py。")
    return store


def run(backend):
    import config
    import nucleus.graph_store.neo4j_store as neo4j_mod
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    # 注入后端
    if backend == "real":
        try:
            mock = _build_real_writer()
            print("[INFO] 使用真实 Neo4j 后端")
        except RuntimeError as e:
            print("[WARN] {}；自动降级为 mock。".format(e))
            mock = MockNeo4jStore()
            neo4j_mod.get_neo4j_store = lambda: mock
    else:
        mock = MockNeo4jStore()
        neo4j_mod.get_neo4j_store = lambda: mock

    # 打开双写开关（仅本进程，不污染 config.py）
    _orig_g = config.ENABLE_NEO4J_GRAPH_STORE
    _orig_d = config.ENABLE_NEO4J_DUAL_WRITE
    config.ENABLE_NEO4J_GRAPH_STORE = True
    config.ENABLE_NEO4J_DUAL_WRITE = True

    try:
        pool = PulseNodePool()

        # 1. 创建节点
        a = PulseNode(value="e2e-A", evol_level="L2", keywords=["e2e"])
        b = PulseNode(value="e2e-B", evol_level="L3", keywords=["e2e"])
        pool.add(a)
        pool.add(b)
        _check("节点双写-创建A", a.node_id in mock.nodes, "neo4j.nodes=%d" % len(mock.nodes))
        _check("节点双写-创建B", b.node_id in mock.nodes)

        # 2. 添加关系
        pool.add_relationship(a.node_id, b.node_id, "RELATED")
        _check("关系双写-添加",
               (a.node_id, b.node_id, "RELATED") in mock.rels,
               "neo4j.rels=%d" % len(mock.rels))

        # 3. 更新节点（路径迁移触发双写update，重写节点属性）
        try:
            a.trust_score = 0.95
            _old_path = getattr(a, "space_path", "/") or "/"
            _new_path = (_old_path.rstrip("/") + "/m72_update") if _old_path != "/" else "/m72_update"
            _ok_upd = pool.update_node_path(a.node_id, _new_path)
            _ts = mock.nodes.get(a.node_id, {}).get("trust_score")
            _check("节点双写-更新属性(路径迁移触发)",
                   _ts in (0.95, "0.95"),
                   "trust_score={} updated={}".format(_ts, _ok_upd))
        except Exception as e:
            _check("节点双写-更新属性(路径迁移触发)", False, "更新异常: {}".format(e))

        # 4. 一致性检查
        cc = pool.check_consistency(a.node_id)
        _check("一致性检查-节点一致", cc.get("consistent", False),
               "detail={}".format(cc.get("detail", "")))

        # 5. 删除节点（DETACH DELETE 行为）
        pool.remove(a.node_id)
        _check("节点双写-删除节点", a.node_id not in mock.nodes)
        _check("节点双写-删除关系",
               (a.node_id, b.node_id, "RELATED") not in mock.rels)

        # 6. 异常场景：图库不可用 → 主流程不抛异常
        mock_fail = MockNeo4jStore(fail_mode=True)
        neo4j_mod.get_neo4j_store = lambda: mock_fail
        try:
            c = PulseNode(value="e2e-C", evol_level="L1", keywords=["e2e"])
            pool.add(c)  # 双写应捕获异常
            _check("异常场景-主流程不抛异常", True)
            st = pool.get_dual_write_stats()
            _check("异常场景-双写统计记失败", st.get("node_fail", 0) > 0,
                   "node_fail={}".format(st.get("node_fail")))
        except Exception as e:
            _check("异常场景-主流程不抛异常", False, "意外抛异常: {}".format(e))

        # 7. 开关关闭 → 零副作用
        config.ENABLE_NEO4J_GRAPH_STORE = False
        config.ENABLE_NEO4J_DUAL_WRITE = False
        mock2 = MockNeo4jStore()
        neo4j_mod.get_neo4j_store = lambda: mock2
        d = PulseNode(value="e2e-D", evol_level="L2", keywords=["e2e"])
        pool.add(d)
        _check("开关关闭-零副作用", d.node_id not in mock2.nodes
               and len(mock2.rels) == 0,
               "neo4j.nodes=%d rels=%d" % (len(mock2.nodes), len(mock2.rels)))

    finally:
        # 还原（虽然只是本进程，仍还原以防后续步骤误解）
        config.ENABLE_NEO4J_GRAPH_STORE = _orig_g
        config.ENABLE_NEO4J_DUAL_WRITE = _orig_d
        neo4j_mod.get_neo4j_store = _orig_real = getattr(neo4j_mod, "get_neo4j_store", None)
        # 还原为原始函数（import 回原函数）
        try:
            from nucleus.graph_store.neo4j_store import get_neo4j_store as _real
            neo4j_mod.get_neo4j_store = _real
        except Exception as e:
            silent_exc(e, "verify_dual_write_e2e:228:还原neo4j_store异常", level="warning")

    print("\n双写统计快照: {}".format(mock.get_stats()))
    return 0 if not FAIL else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Neo4j 双写端到端验证")
    ap.add_argument("--backend", choices=["mock", "real"], default="mock")
    args = ap.parse_args(argv)
    return run(args.backend)


if __name__ == "__main__":
    sys.exit(main())
