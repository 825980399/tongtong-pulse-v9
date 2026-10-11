# -*- coding: utf-8 -*-
"""184批刀3：器官关联图谱 v0 构建器（零停窗，纯测试刀 · 静态结构锁）。

锁定：
  - 节点目录 = 57 器官（与 181刀5 inventory 口径一致），含确定性元数据；
  - export_organ_graph(include_call_graph=False) 结果确定性（两次调用完全一致）；
  - JSON 序列化可往返；
  - 边表结构就绪（kinds ⊂ {import,usage,call}），零耦合时为空属正常（框架中介架构）。

不修改任何业务代码；避免触发 12s 全量调用图分析（include_call_graph 默认关，
与 CI 隔离）；调用图合并路径在交付前已手动验证可运行且不破坏确定性。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from nucleus.cognitive.organ_inventory import export_organ_graph, scan_organ_classes

EXPECTED_NODE_KEYS = {
    "class_name", "name", "type", "category", "cn_name",
    "method_count", "is_core", "is_brain_organ",
}


@pytest.fixture
def graph():
    return export_organ_graph(include_call_graph=False)


def test_node_count_matches_inventory(graph):
    """节点数 = scan_organ_classes 权威清单（57）。"""
    assert graph["organ_count"] == len(scan_organ_classes())
    assert graph["organ_count"] == 57


def test_node_metadata_shape(graph):
    """每个节点含确定性的 8 个元数据字段。"""
    assert graph["nodes"]
    for n in graph["nodes"]:
        assert set(n.keys()) == EXPECTED_NODE_KEYS
        assert isinstance(n["class_name"], str)
        assert isinstance(n["cn_name"], str)  # 含中文名或回退类名，均为 str


def test_edge_structure_ready(graph):
    """边表为列表；若出现边，字段与 kinds 取值合法（v0 零耦合时为正常空集）。"""
    assert isinstance(graph["edges"], list)
    for e in graph["edges"]:
        assert set(e.keys()) == {"from", "to", "kinds", "refs"}
        assert set(e["kinds"]) <= {"import", "usage", "call"}
        assert e["refs"] >= 1


def test_deterministic(graph):
    """同参数两次导出完全一致（确定性边表）。"""
    again = export_organ_graph(include_call_graph=False)
    assert again == graph


def test_json_roundtrip(graph):
    """JSON 导出可解析回等价结构。"""
    import json
    js = export_organ_graph(as_json=True, include_call_graph=False)
    parsed = json.loads(js)
    assert parsed["organ_count"] == graph["organ_count"]
    assert parsed["edge_count"] == graph["edge_count"]
