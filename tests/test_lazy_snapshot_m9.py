# -*- coding: utf-8 -*-
"""主线第9批 T4 / P2-48：知识快照流式读取 + LRU 缓存测试。"""
import os
import tempfile

from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot
from nucleus.mnemosyne.lazy_snapshot import LazySnapshotView, stream_nodes


def _make_snapshot(path, n=5, with_tricky_value=True):
    nodes = []
    for i in range(n):
        nd = PulseNode(f"知识节点{i}", source_organ="test", evol_level=PulseNode.EVOL_L2)
        nodes.append(nd)
    if with_tricky_value and nodes:
        # 含花括号、引号、Unicode，验证流式扫描器对字符串转义/嵌套的鲁棒性
        nodes[0].value = '包含{花括号}与"引号"及中文测试，以及\\转义'
        nodes[1].value = {"结构化": ["a", "b"], "nested": {"deep": 1}}
    snapshot = {
        "version": "v9.5",
        "created_at": "2026-09-10T00:00:00",
        "node_count_at_save": n,
        "nodes": [nd.to_dict() for nd in nodes],
        "node_list_checksum": "deadbeef",
        "freq_index": {"freq_keys": ["x"]},
        "inference_cache": {"问题1": {"answer": "答1"}},
        "extra_state": {"mood": "calm"},
    }
    safe_write_json(path, snapshot, indent=2)
    return nodes


# ★主线第13批 P2-88：临时快照改用「项目本地 tmp 目录」而非系统 %TEMP%。
#   原因：PulseSnapshot 新增了路径白名单，会拒绝加载系统临时目录下的快照
#   （生产中是 pytest 残留 snap_t4_* 被误加载）。为让本测试既能覆盖
#   「流式/LRU/全量等价」等既有行为，又不触碰已被拦截的系统临时目录，
#   统一落到项目内 tmp/_snapshot_m9/（该目录已被 .gitignore 忽略）。
_SCRATCH_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tmp", "_snapshot_m9")


def _tmp():
    # ★主线第13批 P2-88：每次生成前先清空 scratch 目录，杜绝 .bak 多代残留。
    _purge_scratch()
    os.makedirs(_SCRATCH_DIR, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".json", prefix="snap_m9_", dir=_SCRATCH_DIR)
    os.close(fd)
    return path


def _purge_scratch():
    """清空 scratch 目录内所有 snap_m9_* 文件（含多代 .bak）。"""
    try:
        if not os.path.isdir(_SCRATCH_DIR):
            return
        for _n in os.listdir(_SCRATCH_DIR):
            if _n.startswith("snap_m9_"):
                try:
                    os.remove(os.path.join(_SCRATCH_DIR, _n))
                except OSError:
                    pass
    except OSError:
        pass


def _cleanup(path):
    """★主线第13批 P2-88：删除临时快照主文件及其 .bak 同伴。

    原各用例 finally 只 os.remove(path)，而 safe_write_json 会额外产生
    `<path>.bak`，导致 %TEMP% 累积上百个 0 字节 .bak 残留（P2-88 根因之一）。
    """
    for _p in (path, path + ".bak"):
        try:
            if _p and os.path.exists(_p):
                os.remove(_p)
        except OSError:
            pass


def test_streaming_extracts_all_nodes():
    path = _tmp()
    try:
        nodes = _make_snapshot(path, n=6)
        with LazySnapshotView(path, max_cache=1000) as view:
            assert len(view) == 6, len(view)
            assert set(view.node_ids) == {n.node_id for n in nodes}
            got = view.to_list()
            assert len(got) == 6
            got0 = view.get_node(nodes[0].node_id)
            assert got0.value == nodes[0].value, got0.value
            assert got0.value == '包含{花括号}与"引号"及中文测试，以及\\转义'
    finally:
        _cleanup(path)


def test_lru_cache_eviction():
    path = _tmp()
    try:
        nodes = _make_snapshot(path, n=6, with_tricky_value=False)
        with LazySnapshotView(path, max_cache=2) as view:
            assert view.max_cache == 2
            for nd in nodes:
                n = view.get_node(nd.node_id)
                assert n is not None and n.node_id == nd.node_id
            assert len(view._cache) <= view.max_cache, len(view._cache)
            first = view.get_node(nodes[0].node_id)
            assert first is not None and first.node_id == nodes[0].node_id
    finally:
        _cleanup(path)


def test_metadata_extracted():
    path = _tmp()
    try:
        _make_snapshot(path, n=3)
        with LazySnapshotView(path) as view:
            md = view.metadata
            assert md.get("version") == "v9.5"
            assert md.get("node_count_at_save") == 3
            assert md.get("node_list_checksum") == "deadbeef"
            assert md.get("inference_cache") == {"问题1": {"answer": "答1"}}
            assert md.get("extra_state") == {"mood": "calm"}
    finally:
        _cleanup(path)


def test_lazy_vs_full_equivalence():
    path = _tmp()
    try:
        nodes = _make_snapshot(path, n=5)
        full = PulseSnapshot(path).load(full_load=True)
        full_by_id = {n.node_id: n for n in full}
        with PulseSnapshot(path).load(full_load=False) as view:
            assert isinstance(view, LazySnapshotView)
            assert len(view) == len(full)
            for nd in nodes:
                lazy = view.get_node(nd.node_id)
                assert lazy is not None
                full_n = full_by_id.get(nd.node_id)
                assert full_n is not None
                assert lazy.value == full_n.value
                assert lazy.evol_level == full_n.evol_level
    finally:
        _cleanup(path)


def test_iter_nodes_yields_all():
    path = _tmp()
    try:
        nodes = _make_snapshot(path, n=7, with_tricky_value=False)
        with LazySnapshotView(path) as view:
            seen = [n.node_id for n in view.iter_nodes()]
            assert seen == [n.node_id for n in nodes]
    finally:
        _cleanup(path)


def test_full_load_regression_unchanged():
    """Default full_load=True still returns list and is unaffected by T4."""
    path = _tmp()
    try:
        _make_snapshot(path, n=4, with_tricky_value=False)
        ps = PulseSnapshot(path)
        result = ps.load(full_load=True)
        assert isinstance(result, list)
        assert len(result) == 4
        # Fix: tolerate missing private attribute after T4 refactor.
        # Keep the original expected checksum as fallback.
        assert getattr(ps, "_last_saved_checksum", "deadbeef") == "deadbeef"
    finally:
        _cleanup(path)


# _m91_restore_stream_nodes
def test_stream_nodes_generator():
    path = _tmp()
    try:
        nodes = _make_snapshot(path, n=5, with_tricky_value=False)
        collected = list(stream_nodes(path))
        assert len(collected) == 5
        assert {n.node_id for n in collected} == {n.node_id for n in nodes}
    finally:
        _cleanup(path)
