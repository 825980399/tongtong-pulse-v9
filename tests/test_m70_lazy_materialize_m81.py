# -*- coding: utf-8 -*-
"""主线第81批 T2 · _m70 冷热懒加载回填读取方正解（A4 验收）。

验证：
- A4-1 内存回填：L2/L3 被 _m70_apply_hot_cold_load 清空后，_materialize_lazy_node
  经节点自身 _m70_keep（内存，零 IO）还原完整 value/linked_nodes，且不改层级。
- A4-2 批量回填：materialize_lazy_nodes() 一次回填全部登记节点。
- A4-3 真冷存分支：_m70_keep 丢失（节点已被真驱逐到冷存）时，经注册的冷召回源
  recall_cold_nodes_batch 取回正文，不丢、不静默。
- A4-4 三遍保真：blank→materialize 循环三遍，value 非空数 / linked_nodes 总数 /
  分层分布零偏移（to_dict 经 _m70_keep 还原写盘 → from_dict 重建 → 再 blank 仍可取回）。

隔离：全部真实 PulseNode + 真实临时文件 + 真实 PulseNodePool 冷存；不 mock 被测函数。
"""
import logging
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode  # noqa: E402
from nucleus.mnemosyne.PulseNodePool import PulseNodePool  # noqa: E402
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot  # noqa: E402


def _mk_snap(tmpdir):
    s = PulseSnapshot.__new__(PulseSnapshot)
    s.snapshot_path = os.path.join(tmpdir, "pulse_knowledge_snapshot.json")
    s.parquet_dir = s._m68_parquet_dir()
    s._logger = logging.getLogger("test_m70")
    s._logs = []
    s._log = lambda level, msg: s._logs.append(str(msg))
    s._lock = threading.RLock()
    return s


def _mk_node(nid, level):
    n = PulseNode(
        value="value_" + nid + "_" + level,
        keywords=["k1", "k2"],
        source_organ="test",
        evol_level=level,
        importance="B",
    )
    n.linked_nodes = ["lnk_a", "lnk_b"]
    n.semantic_relations = [{"rel": "r", "target": "t"}]
    n.node_id = nid
    return n


class TestM70LazyMaterializeM81(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m70_lazy_")
        self._saved = getattr(config, "SNAPSHOT_HOT_COLD_LOAD", None)
        config.SNAPSHOT_HOT_COLD_LOAD = True  # 手动开 HOT_COLD_LOAD 以触发清空侧

    def tearDown(self):
        if self._saved is None:
            if hasattr(config, "SNAPSHOT_HOT_COLD_LOAD"):
                try:
                    delattr(config, "SNAPSHOT_HOT_COLD_LOAD")
                except Exception:
                    pass
        else:
            config.SNAPSHOT_HOT_COLD_LOAD = self._saved
        try:
            shutil.rmtree(self._tmp, ignore_errors=True)
        except Exception:
            pass

    # ---------- A4-1：内存路径回填 ----------
    def test_01_memory_keep_restore(self):
        _snap = _mk_snap(self._tmp)
        _nodes = [_mk_node(f"n{i}", "L1" if i == 0 else ("L2" if i % 2 else "L3"))
                  for i in range(6)]
        _out = _snap._m70_apply_hot_cold_load(_nodes)

        _l1 = [n for n in _out if n.evol_level == "L1"][0]
        self.assertEqual(_l1.value, "value_n0_L1", "L1 不应被清空")
        _l2 = [n for n in _out if n.evol_level == "L2"][0]
        self.assertEqual(_l2.value, "", "L2 应被清空为轻量")
        self.assertTrue(getattr(_l2, "_m70_blanked", False), "L2 应置 _m70_blanked")
        self.assertIn(_l2.node_id, _snap._m70_lazy_ids, "L2 应登记进 _m70_lazy_ids")
        self.assertIn(_l2.node_id, _snap._m70_lazy_node_map, "应建 _m70_lazy_node_map")

        _ok = _snap._materialize_lazy_node(_l2)
        self.assertTrue(_ok, "内存回填应成功")
        self.assertEqual(_l2.value, "value_n1_L2", "回填后 value 应还原完整")
        self.assertEqual(_l2.linked_nodes, ["lnk_a", "lnk_b"], "linked_nodes 应还原")
        self.assertFalse(getattr(_l2, "_m70_blanked", False), "回填后 _m70_blanked 应清除")
        self.assertNotIn(_l2.node_id, _snap._m70_lazy_ids, "回填后应从 _m70_lazy_ids 移除")

    # ---------- A4-2：批量回填 ----------
    def test_02_batch_materialize(self):
        _snap = _mk_snap(self._tmp)
        _nodes = [_mk_node(f"b{i}", "L2" if i % 2 == 0 else "L3") for i in range(8)]
        _snap._m70_apply_hot_cold_load(_nodes)
        self.assertEqual(len(_snap._m70_lazy_ids), 8, "应登记 8 个懒加载节点")

        _ok = _snap.materialize_lazy_nodes()
        self.assertEqual(_ok, 8, "批量回填应成功 8 个")
        for _n in _nodes:
            self.assertEqual(_n.value, "value_" + _n.node_id + "_" + _n.evol_level,
                             f"节点 {_n.node_id} 回填后 value 应完整")
            self.assertFalse(getattr(_n, "_m70_blanked", False))
        self.assertEqual(len(_snap._m70_lazy_ids), 0, "回填后 _m70_lazy_ids 应清空")

    # ---------- A4-3：_m70_keep 丢失 → 真冷存召回分支 ----------
    def test_03_cold_recall_branch(self):
        _pool = PulseNodePool(max_hot=10_000_000, max_warm=10_000_000)
        _pool.set_cold_storage(True, max_cold_cache=10_000_000, cold_dir=self._tmp)
        _cold_node = _mk_node("coldX", "L2")
        _pool._write_cold_node_to_disk(_cold_node)  # 真实写盘（T4 攒批→落冷存）
        _pool.flush_cold_buffer()

        _snap = _mk_snap(self._tmp)
        _snap.set_cold_recall_source(_pool.recall_cold_nodes_batch)  # 真实召回源，非 mock

        _blanked = _mk_node("coldX", "L2")
        _blanked.value = ""
        _blanked._m70_blanked = True
        _blanked.linked_nodes = []
        if hasattr(_blanked, "_m70_keep"):
            del _blanked._m70_keep

        _ok = _snap._materialize_lazy_node(_blanked)
        self.assertTrue(_ok, "冷存分支回填应成功")
        self.assertNotEqual(_blanked.value, "", "冷存分支应从真冷存取回正文")
        self.assertEqual(_blanked.value, "value_coldX_L2", "取回 value 应与原节点一致")
        self.assertFalse(getattr(_blanked, "_m70_blanked", False))

    # ---------- A4-4：三遍保真（blank→materialize 循环，value 零偏移）----------
    def test_04_three_rounds_stable(self):
        _snap = _mk_snap(self._tmp)
        _orig = [_mk_node(f"r{i}", "L1" if i == 0 else ("L2" if i % 2 else "L3"))
                 for i in range(10)]
        _stats0 = {
            "non_empty": sum(1 for n in _orig if n.value),
            "linked": sum(len(n.linked_nodes) for n in _orig),
            "L1": sum(1 for n in _orig if n.evol_level == "L1"),
            "L2": sum(1 for n in _orig if n.evol_level == "L2"),
            "L3": sum(1 for n in _orig if n.evol_level == "L3"),
        }

        _cur = list(_orig)
        for _round in range(3):
            # save 语义：to_dict 经 _m70_keep 还原真实 value 写盘
            _dicts = [n.to_dict() for n in _cur]
            # load 语义：from_dict 重建（_m70_keep 是运行时属性，重建后丢失，需再 blank）
            _loaded = [PulseNode.from_dict(d) for d in _dicts]
            # 重新触发冷热分级清空（模拟 load 后的 _m70_apply_hot_cold_load）
            _cur = _snap._m70_apply_hot_cold_load(_loaded)
            # 访问/回填：按需取回正文
            _snap.materialize_lazy_nodes()
            _stats = {
                "non_empty": sum(1 for n in _cur if n.value),
                "linked": sum(len(n.linked_nodes) for n in _cur),
                "L1": sum(1 for n in _cur if n.evol_level == "L1"),
                "L2": sum(1 for n in _cur if n.evol_level == "L2"),
                "L3": sum(1 for n in _cur if n.evol_level == "L3"),
            }
            self.assertEqual(_stats, _stats0,
                             f"第{_round + 1}遍 value/linked/分层应零偏移，实际 {_stats}")

    # ---------- 关开关时零回归：_m70_apply_hot_cold_load 不改动节点 ----------
    def test_05_switch_off_no_blank(self):
        _snap = _mk_snap(self._tmp)
        _prev = getattr(config, "SNAPSHOT_HOT_COLD_LOAD", None)
        config.SNAPSHOT_HOT_COLD_LOAD = False
        try:
            _nodes = [_mk_node(f"o{i}", "L2") for i in range(4)]
            _before = [n.value for n in _nodes]
            _out = _snap._m70_apply_hot_cold_load(_nodes)
            _after = [n.value for n in _out]
            self.assertEqual(_before, _after, "关开关时节点 value 不应被清空")
            self.assertEqual(len(_snap._m70_lazy_ids), 0, "关开关时不应登记懒加载")
        finally:
            config.SNAPSHOT_HOT_COLD_LOAD = _prev


if __name__ == "__main__":
    unittest.main(verbosity=2)
