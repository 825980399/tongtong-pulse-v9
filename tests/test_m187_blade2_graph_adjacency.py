# -*- coding: utf-8 -*-
"""187批刀2 隔离单测：self_inspector 图谱邻接只读查询 + blast_radius。

验收点：
  - _build_event_graph_adjacency 构建一阶邻接计数（改X波及谁/谁驱动X的并集大小）
  - query_event_graph_adjacency 回答「改 X 波及谁 / 谁驱动 X」
  - 纯只读、零行为变化（monkeypatch 图谱导出，不依赖真实 AST 扫描）
"""
import sys
import types

sys.path.insert(0, ".")

import tools.export_event_graph as eg
import nucleus.self_inspector as si


_FAKE_GRAPH = {
    "edges": [
        {"emitter": "PulseA", "event_type": "x", "subscriber": "PulseB"},
        {"emitter": "PulseA", "event_type": "y", "subscriber": "PulseC"},
        {"emitter": "PulseD", "event_type": "z", "subscriber": "PulseA"},
    ],
}


def _fake_export():
    return _FAKE_GRAPH


_inst = types.SimpleNamespace()


def test_build_adjacency_counts():
    _orig = eg.export_event_graph
    eg.export_event_graph = _fake_export
    try:
        adj = si.SelfInspector._build_event_graph_adjacency(_inst)
    finally:
        eg.export_event_graph = _orig
    # PulseA 波及 {B,C} 且被 D 驱动 → 并集 3
    assert adj == {"PulseA": 3, "PulseB": 1, "PulseC": 1, "PulseD": 1}, adj


def test_query_adjacency_affects_and_driven_by():
    _orig = eg.export_event_graph
    eg.export_event_graph = _fake_export
    try:
        q = si.SelfInspector.query_event_graph_adjacency(_inst, "PulseA")
    finally:
        eg.export_event_graph = _orig
    assert q["organ"] == "PulseA"
    assert q["affects"] == ["PulseB", "PulseC"], q["affects"]
    assert q["driven_by"] == ["PulseD"], q["driven_by"]
    assert q["blast_radius"] == 3, q["blast_radius"]


def test_query_adjacency_leaf_organ():
    _orig = eg.export_event_graph
    eg.export_event_graph = _fake_export
    try:
        q = si.SelfInspector.query_event_graph_adjacency(_inst, "PulseB")
    finally:
        eg.export_event_graph = _orig
    assert q["affects"] == []
    assert q["driven_by"] == ["PulseA"]
    assert q["blast_radius"] == 1


if __name__ == "__main__":
    import traceback
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    _fail = 0
    for t in _tests:
        try:
            t()
            print("PASS", t.__name__)
        except Exception:
            _fail += 1
            print("FAIL", t.__name__)
            traceback.print_exc()
    print(f"\n{len(_tests) - _fail}/{len(_tests)} passed")
    sys.exit(1 if _fail else 0)
