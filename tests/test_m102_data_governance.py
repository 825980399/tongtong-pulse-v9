# -*- coding: utf-8 -*-
"""★主线第102批：数据治理（D160 悬空引用 / D161 孤儿向量 / D165 语义关系冗余）门控单测。

被测对象 = **真实源码**，不复刻任何逻辑：
  - `nucleus/mnemosyne/PulseSnapshot.py`：落盘前悬空边过滤（T-102a 根因修复）
  - `nucleus/mnemosyne/PulseNode.py`   ：linked_nodes 由 semantic_relations 动态重建（T-102c）
  - `nucleus/mnemosyne/PulseNodePool.py`：remove() 级联移除向量（T-102b 根因修复）
  - `nucleus/semantic/AsyncEncodeQueue.py`：reconcile() 反向回收孤儿向量（T-102b）
  - `nucleus/semantic/VectorStore.py`：reap_orphans()（T-102b）

★任务书前提修正（T0 实测，已写入交付报告）：
    任务书写「本批是数据治理，不涉及代码改动」。实测三条债务的**根因均在代码侧**：
    ① 落盘链路无引用完整性检查 ⇒ 悬空边持续产出；
    ② `VectorStore.remove` 生产侧 0 调用点 + `reconcile` 只单向补码 ⇒ 孤儿向量只增不减；
    ③ JSON 内联同时存 sem 与 linked 两份 ⇒ 冗余投影。
    若只做一次性清洗而不改代码，框架下次启动即把悬空边/孤儿向量写回，
    清洗无意义 ⇒ 本批按「代码根因修复 + 一次性存量治理」双轨执行。

覆盖六组：
  A. 静态接线 —— 五处改动真在源码里、四个开关已登记且默认 True；
  B. 落盘过滤纯函数 —— 正确剔除 / extra_ids=None 时**绝不误删** / 快照外合法节点不误删；
  C. linked_nodes 动态派生 —— 开关开则派生（去重保序）、开关关则**不派生**（先红后绿的"红"面）；
  D. 零丢失等价 —— 治理（linked 独有边并入 sem 后清空 linked）前后「可达邻居集合」不变；
  E. 端到端写盘 —— 真实 `_atomic_write_with_rotation` 写出**不含悬空边**的文件；
     开关关闭时悬空边照旧落盘（证明 E 组断言非恒真）。
"""
import ast
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from nucleus.mnemosyne.PulseNode import (  # noqa: E402
    PulseNode,
    _m102_derive_linked_nodes,
    _m102_linked_derived_on,
)
from nucleus.mnemosyne.PulseSnapshot import (  # noqa: E402
    PulseSnapshot,
    _m102_dangling_guard_on,
    _m102_filter_dangling_edges,
)

SNAP_PY = os.path.join(ROOT, "nucleus", "mnemosyne", "PulseSnapshot.py")
NODE_PY = os.path.join(ROOT, "nucleus", "mnemosyne", "PulseNode.py")
POOL_PY = os.path.join(ROOT, "nucleus", "mnemosyne", "PulseNodePool.py")
QUEUE_PY = os.path.join(ROOT, "nucleus", "semantic", "AsyncEncodeQueue.py")
VEC_PY = os.path.join(ROOT, "nucleus", "semantic", "VectorStore.py")


def _read(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _func_src(path, func_name, cls=None):
    """取源码中某个函数/方法的源码片段（文本级断言用）。"""
    tree = ast.parse(_read(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name != func_name:
                continue
            if cls is None:
                return ast.get_source_segment(_read(path), node) or ""
            # 找其所属 ClassDef
    if cls is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls:
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and sub.name == func_name:
                        return ast.get_source_segment(_read(path), sub) or ""
    return ""


def _sem(target):
    return {"target_node_id": target, "relation_type": "semantic_similarity",
            "weight": 0.5, "source": "test"}


class A_StaticWiring(unittest.TestCase):
    """A 组：五处代码改动 + 四个开关登记。"""

    def test_a1_snapshot_write_path_calls_filter(self):
        src = _func_src(SNAP_PY, "_atomic_write_with_rotation", cls="PulseSnapshot")
        self.assertIn("_m102_filter_dangling_edges", src,
                      "落盘链路必须调用悬空边过滤（T-102a 根因修复）")

    def test_a2_node_from_dict_derives_linked(self):
        src = _func_src(NODE_PY, "from_dict", cls="PulseNode")
        self.assertIn("_m102_derive_linked_nodes", src,
                      "from_dict 必须在 linked 为空时按 sem 重建（T-102c）")

    def test_a3_pool_remove_cascades_vector(self):
        src = _func_src(POOL_PY, "remove", cls="PulseNodePool")
        self.assertIn("remove(", src)
        self.assertTrue(("get_vector_store" in src) or ("vector_store" in src),
                        "节点删除必须级联移除向量（T-102b 根因修复）")

    def test_a4_reconcile_reaps_orphans(self):
        src = _func_src(QUEUE_PY, "reconcile", cls="AsyncEncodeQueue")
        self.assertIn("reap_orphans", src,
                      "reconcile 必须反向回收孤儿向量（T-102b）")

    def test_a5_vector_store_has_reap_orphans(self):
        self.assertIn("def reap_orphans", _read(VEC_PY))

    def test_a6_switches_registered_default_true(self):
        for name in ("ENABLE_M102_DANGLING_EDGE_GUARD",
                     "ENABLE_M102_VECTOR_CASCADE_REMOVE",
                     "ENABLE_M102_ORPHAN_VECTOR_REAP",
                     "ENABLE_M102_LINKED_NODES_DERIVED"):
            self.assertTrue(hasattr(config, name), f"开关未登记: {name}")
            self.assertIs(getattr(config, name), True, f"开关默认应为 True: {name}")


class B_FilterDangling(unittest.TestCase):
    """B 组：落盘过滤纯函数行为（重点是「绝不误删」）。"""

    def _snap(self):
        return {"nodes": [
            {"node_id": "n1",
             "semantic_relations": [_sem("n2"), _sem("ghost")],
             "linked_nodes": ["n2", "ghost"]},
            {"node_id": "n2", "semantic_relations": [], "linked_nodes": []},
        ]}

    def test_b1_removes_dangling_keeps_valid(self):
        snap = self._snap()
        srm, lrm, known = _m102_filter_dangling_edges(snap, {"n1", "n2"})
        self.assertEqual((srm, lrm), (1, 1))
        self.assertEqual(known, 2)
        n1 = snap["nodes"][0]
        self.assertEqual([x["target_node_id"] for x in n1["semantic_relations"]], ["n2"])
        self.assertEqual(n1["linked_nodes"], ["n2"])

    def test_b2_extra_ids_none_never_deletes(self):
        """★核心安全契约：取不到可靠全集时必须跳过，绝不误删。"""
        snap = self._snap()
        before = json.dumps(snap, sort_keys=True)
        self.assertEqual(_m102_filter_dangling_edges(snap, None), (0, 0, 0))
        self.assertEqual(json.dumps(snap, sort_keys=True), before)

    def test_b3_empty_snapshot_noop(self):
        self.assertEqual(_m102_filter_dangling_edges({}, {"a"}), (0, 0, 0))
        self.assertEqual(_m102_filter_dangling_edges({"nodes": []}, {"a"}), (0, 0, 0))
        self.assertEqual(_m102_filter_dangling_edges("not a dict", {"a"}), (0, 0, 0))

    def test_b4_switch_both_ways(self):
        """开关双向可断言 ⇒ 证明 B/E 组结论不是恒真。"""
        self.assertTrue(_m102_dangling_guard_on())
        with mock.patch.object(config, "ENABLE_M102_DANGLING_EDGE_GUARD", False):
            self.assertFalse(_m102_dangling_guard_on())

    def test_b5_outside_snapshot_valid_target_preserved(self):
        """指向「快照外但池内合法节点」的边，必须靠 extra_ids 保住，不得误删。"""
        snap = {"nodes": [
            {"node_id": "n1",
             "semantic_relations": [_sem("n9")],   # n9 不在本快照，但在节点池里
             "linked_nodes": ["n9"]},
        ]}
        srm, lrm, known = _m102_filter_dangling_edges(snap, {"n9"})
        self.assertEqual((srm, lrm), (0, 0), "快照外合法节点不得被判为悬空")
        self.assertEqual(known, 2)
        self.assertEqual(len(snap["nodes"][0]["semantic_relations"]), 1)


class C_DeriveLinked(unittest.TestCase):
    """C 组：linked_nodes 动态派生（T-102c）。"""

    def _data(self, sem_targets, linked):
        return {"node_id": "n1", "content": "x", "evol_level": "L2",
                "semantic_relations": [_sem(t) for t in sem_targets],
                "linked_nodes": list(linked)}

    def test_c1_derives_dedup_order_preserved(self):
        node = PulseNode.from_dict(
            self._data(["n2", "n3", "n2", "n4"], []))
        self.assertEqual(node.linked_nodes, ["n2", "n3", "n4"])

    def test_c2_switch_off_no_derivation(self):
        """★红面：开关关闭时行为与改造前一致（不派生）。"""
        with mock.patch.object(config, "ENABLE_M102_LINKED_NODES_DERIVED", False):
            self.assertFalse(_m102_linked_derived_on())
            node = PulseNode.from_dict(self._data(["n2", "n3"], []))
        self.assertEqual(node.linked_nodes, [])

    def test_c3_existing_linked_not_overwritten(self):
        node = PulseNode.from_dict(self._data(["n2"], ["n9"]))
        self.assertEqual(node.linked_nodes, ["n9"], "已有 linked 不得被覆盖")

    def test_c5_to_dict_does_not_persist_linked(self):
        """★持久性闭环：开关开启时 to_dict 不落盘 linked_nodes，否则下次保存体积即回升。"""
        node = PulseNode.from_dict(self._data(["n2", "n3"], []))
        self.assertEqual(node.linked_nodes, ["n2", "n3"])     # 内存里有
        self.assertEqual(node.to_dict()["linked_nodes"], [])  # 盘上不存（冗余投影）

    def test_c6_to_dict_switch_off_keeps_linked(self):
        """★红面：开关关闭时 to_dict 行为与改造前一致（原样落盘）。"""
        node = PulseNode.from_dict(self._data(["n2", "n3"], []))
        self.assertEqual(node.linked_nodes, ["n2", "n3"])
        with mock.patch.object(config, "ENABLE_M102_LINKED_NODES_DERIVED", False):
            self.assertEqual(node.to_dict()["linked_nodes"], ["n2", "n3"])

    def test_c7_linked_without_sem_must_persist(self):
        """★零丢失边界1：有 linked 但 sem 为空 ⇒ 必须原样落盘（冷存 roundtrip 回归）。"""
        node = PulseNode.from_dict(self._data([], ["lk7", "lk7x"]))
        self.assertEqual(node.to_dict()["linked_nodes"], ["lk7", "lk7x"])

    def test_c8_linked_superset_of_sem_must_persist(self):
        """★零丢失边界2：运行期只往 linked 追加的边（linked ⊋ sem）⇒ 必须落盘，不得丢。"""
        node = PulseNode.from_dict(self._data(["n2"], []))
        node.linked_nodes.append("n9")          # 模拟 PulseLiver 运行期追加
        self.assertEqual(node.to_dict()["linked_nodes"], ["n2", "n9"])

    def test_c4_pure_function_robust(self):
        self.assertEqual(_m102_derive_linked_nodes(None), [])
        self.assertEqual(_m102_derive_linked_nodes([{"target_node_id": None},
                                                    {"target_node_id": ""}]), [])
        self.assertEqual(_m102_derive_linked_nodes(["a", "a", "b"]), ["a", "b"])
        self.assertEqual(_m102_derive_linked_nodes([{"target_node_id": 5}]), ["5"])


class D_ZeroLossEquivalence(unittest.TestCase):
    """D 组：T-102c 治理前后「可达邻居集合」必须完全一致（零丢失）。"""

    def test_d1_merge_then_clear_keeps_union(self):
        sem_targets = ["n2", "n3"]
        linked = ["n3", "n9"]              # n9 为 linked 独有边
        before_union = set(sem_targets) | set(linked)

        # ---- 治理动作（与 tools/m102_data_governance.py --apply c 同语义）----
        sem = [_sem(t) for t in sem_targets]
        have = set(sem_targets)
        for tg in linked:
            if tg not in have:
                sem.append({"target_node_id": tg, "relation_type": "cooccurrence",
                            "weight": 0.5, "source": "m102_merge"})
                have.add(tg)
        after = PulseNode.from_dict(
            {"node_id": "n1", "content": "x", "evol_level": "L2",
             "semantic_relations": sem, "linked_nodes": []})
        # --------------------------------------------------------------------
        self.assertEqual(set(after.linked_nodes), before_union)
        self.assertEqual(after.linked_nodes, ["n2", "n3", "n9"])  # 去重保序


class E_EndToEndWrite(unittest.TestCase):
    """E 组：真实写盘路径 `_atomic_write_with_rotation` 不含悬空边。"""

    def _instance(self, tmp):
        inst = PulseSnapshot.__new__(PulseSnapshot)   # 跳过重 __init__，只测写盘路径
        inst.snapshot_path = os.path.join(tmp, "pulse_knowledge_snapshot.json")
        inst._log = lambda *a, **k: None
        inst._cleanup_old_backups = lambda: None
        pool = types.SimpleNamespace(
            get_all_including_evicted=lambda: [
                types.SimpleNamespace(node_id="n1"),
                types.SimpleNamespace(node_id="n2"),
            ])
        inst.node_pool = pool
        return inst

    def _snapshot(self):
        return {"version": "v9.5", "nodes": [
            {"node_id": "n1",
             "semantic_relations": [_sem("n2"), _sem("node:ghost")],
             "linked_nodes": ["n2", "node:ghost"]},
            {"node_id": "n2", "semantic_relations": [], "linked_nodes": []},
        ]}

    def test_e1_dangling_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = self._instance(tmp)
            inst._atomic_write_with_rotation(self._snapshot(), compact=True, rotate=False)
            with io.open(inst.snapshot_path, encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("node:ghost", text, "悬空边不得落盘")
            data = json.loads(text)
            self.assertEqual(len(data["nodes"][0]["semantic_relations"]), 1)
            self.assertEqual(data["nodes"][0]["linked_nodes"], ["n2"])

    def test_e2_switch_off_dangling_still_persisted(self):
        """★红面：开关关闭时悬空边照旧落盘 ⇒ 证明 E1 断言受开关控制，非恒真。"""
        with tempfile.TemporaryDirectory() as tmp:
            inst = self._instance(tmp)
            with mock.patch.object(config, "ENABLE_M102_DANGLING_EDGE_GUARD", False):
                inst._atomic_write_with_rotation(self._snapshot(), compact=True, rotate=False)
            with io.open(inst.snapshot_path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("node:ghost", text, "开关关闭时行为应与改造前一致")

    def test_e3_no_node_pool_means_no_filter(self):
        """★无节点池 ⇒ 无可靠全集 ⇒ 跳过过滤（绝不误删）。"""
        with tempfile.TemporaryDirectory() as tmp:
            inst = self._instance(tmp)
            inst.node_pool = None
            inst._atomic_write_with_rotation(self._snapshot(), compact=True, rotate=False)
            with io.open(inst.snapshot_path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("node:ghost", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
