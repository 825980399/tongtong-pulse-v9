# -*- coding: utf-8 -*-
"""162批刀2 · A2 快照冷热分级硬门 #10 门控单测。

验证：LazySnapshotView 懒加载路径下 _m70_apply_hot_cold_load 真实生效
（不再因 TypeError 被 load() 捕获后原样返回），且 full_load=False 仍返回
LazySnapshotView 契约；非可迭代输入兜底原样返回并产出可观测标记。
"""
import os
import tempfile

from nucleus._silent_except import silent_exc
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.mnemosyne.lazy_snapshot import LazySnapshotView
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot

_SCRATCH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tmp", "_snapshot_k2")


def _tmp():
    try:
        if not os.path.isdir(_SCRATCH):
            os.makedirs(_SCRATCH)
    except OSError as _e:
        silent_exc(_e, where="tests.test_knife2_hot_cold_lazy_m162")
    fd, path = tempfile.mkstemp(suffix=".json", prefix="snap_k2_", dir=_SCRATCH)
    os.close(fd)
    return path


def _cleanup(path):
    for _p in (path, path + ".bak"):
        try:
            if _p and os.path.exists(_p):
                os.remove(_p)
        except OSError as _e:
            silent_exc(_e, where="tests.test_knife2_hot_cold_lazy_m162")


def _make_mixed(path, n_each=3):
    nodes = []
    for k, lvl in (("L1", PulseNode.EVOL_L1),
                   ("L2", PulseNode.EVOL_L2),
                   ("L3", PulseNode.EVOL_L3)):
        for i in range(n_each):
            nd = PulseNode("%s节点%d" % (k, i), source_organ="test", evol_level=lvl)
            if lvl != PulseNode.EVOL_L1:
                nd.value = "大字段_%s_%d" % (k, i)
                nd.linked_nodes = ["ln_%s_%d" % (k, i)]
            nodes.append(nd)
    snapshot = {
        "version": "v9.5",
        "created_at": "2026-10-06T00:00:00",
        "node_count_at_save": len(nodes),
        "nodes": [nd.to_dict() for nd in nodes],
    }
    safe_write_json(path, snapshot, indent=2)
    return nodes


def test_knife2_lazy_hot_cold_real_classification():
    path = _tmp()
    try:
        nodes = _make_mixed(path, n_each=3)
        ps = PulseSnapshot(path)
        view = ps.load(full_load=False)
        # 契约：full_load=False 仍返回 LazySnapshotView
        assert isinstance(view, LazySnapshotView)
        # 判据②：L2/L3 节点被登记到 _m70_lazy_ids（真实走了分级路径，非兜底）
        l2l3_ids = {n.node_id for n in nodes
                    if str(n.evol_level).upper() in ("L2", "L3")}
        assert ps._m70_lazy_ids == l2l3_ids, (ps._m70_lazy_ids, l2l3_ids)
        assert not ps._m70_hot_load_stats.get("degraded")
        # 判据①：L2/L3 在视图里被 blank（get_node 取回分级结果）；L1 完整
        for nd in nodes:
            got = view.get_node(nd.node_id)
            assert got is not None
            if str(nd.evol_level).upper() == "L1":
                assert got.value == nd.value, "L1 应保留完整 value"
            else:
                assert got.value == "", "L2/L3 应被 blank（value 清空）"
                assert got.linked_nodes == [], "L2/L3 应被 blank（linked_nodes 清空）"
        # 等价性：full_load=True 路径同样 blank L2/L3（修复前两者不一致导致测试失败）
        full = PulseSnapshot(path).load(full_load=True)
        full_by_id = {n.node_id: n for n in full}
        for nd in nodes:
            if str(nd.evol_level).upper() != "L1":
                assert full_by_id[nd.node_id].value == "", "full 路径也应 blank L2/L3"
    finally:
        _cleanup(path)


def test_knife2_non_iterable_fallback_marker():
    fd, p = tempfile.mkstemp(suffix=".json", prefix="snap_k2_")
    os.close(fd)
    ps = PulseSnapshot(p)
    captured = []
    _orig = ps._log

    def _cap(level, msg):
        captured.append((level, msg))
        return _orig(level, msg)

    ps._log = _cap
    try:
        fake = object()  # 不可迭代对象
        result = ps._m70_apply_hot_cold_load(fake)
        # 兜底：原样返回，不丢节点
        assert result is fake, "非可迭代输入应原样返回"
        # 可观测标记：降级标记 + 原因 + WARNING 日志
        assert ps._m70_hot_load_stats.get("degraded") is True
        assert ps._m70_hot_load_stats.get("reason") == "non_iterable_input"
        assert any("降级可观测" in m for _, m in captured), captured
    finally:
        try:
            os.remove(p)
        except OSError as _e:
            silent_exc(_e, where="tests.test_knife2_hot_cold_lazy_m162")
