# -*- coding: utf-8 -*-
"""186批刀1 隔离单测：图谱 v0.5 事件路由边构建器（零停窗）。

验收：
  - derive_event_edges 就位，产出事件路由边（Y→T→X）；
  - 复用 scan_organ_classes 权威器官清单（57 器官）；
  - 纯 AST 零运行时依赖（不调 LLM / 不读运行态 / 不落盘）；
  - export_event_graph 导出 JSON，结构可往返。
"""
from __future__ import annotations

import json

import tools.export_event_graph as m


def test_taxonomy_heart_beat():
    tax = m.load_event_taxonomy()
    assert tax["member_full"].get("HeartEvent.BEAT") == "heart.beat"
    assert tax["value_to_member"].get("heart.beat") == "HeartEvent.BEAT"
    # Event（无 Event 后缀）命名空间也应纳入
    assert "Event.CHAT_MESSAGE" in tax["member_full"]


def test_export_shape_and_counts():
    p = m.export_event_graph()
    assert p["version"] == "v0.5"
    assert p["organ_count"] == 57
    assert p["event_type_count"] > 0
    assert p["edge_count"] > 0
    # 边按 (emitter, event_type, subscriber) 升序、去重
    edges = p["edges"]
    assert edges == sorted(edges, key=lambda e: (e["emitter"], e["event_type"], e["subscriber"]))
    for e in edges:
        assert set(e.keys()) == {"emitter", "event_type", "subscriber"}
        assert e["emitter"] != e["subscriber"]  # 跨器官路由，排除自环


def test_known_edge_heart_beat_to_bloodvessel():
    """心脏发射 heart.beat，血管订阅 heart.beat → 应存在路由边。"""
    p = m.export_event_graph()
    emitters = p["emits"]
    subs = p["subscribes"]
    assert "heart.beat" in emitters.get("PulseHeart", [])
    assert "heart.beat" in subs.get("PulseBloodVessel", [])
    assert {"emitter": "PulseHeart", "event_type": "heart.beat",
            "subscriber": "PulseBloodVessel"} in p["edges"]


def test_wildcard_bloodvessel():
    """PulseBloodVessel 订阅通配 '*' → 应入 wildcards 且对全部已发射类型有边。"""
    p = m.export_event_graph()
    assert "PulseBloodVessel" in p["wildcards"]
    emitted_types = {t for ts in p["emits"].values() for t in ts}
    bv_edges = [e for e in p["edges"] if e["subscriber"] == "PulseBloodVessel"]
    # 通配订阅应收录所有已发射类型（排除自身发射的 heart.beat/...，但 BloodVessel 发射 organ.*）
    for t in emitted_types:
        if any(e["emitter"] == "PulseBloodVessel" and e["event_type"] == t for e in p["edges"]):
            continue
        assert any(e["event_type"] == t for e in bv_edges), f"通配缺失类型 {t}"


def test_json_roundtrip():
    s = m.export_event_graph(as_json=True)
    obj = json.loads(s)
    assert obj["edge_count"] == len(obj["edges"])
    assert isinstance(obj["emits"], dict)
    assert isinstance(obj["subscribes"], dict)


def test_derive_event_edges_keys():
    res = m.derive_event_edges()
    assert set(res.keys()) >= {"edges", "emits", "subscribes", "wildcards", "stats"}
    assert res["stats"]["files_scanned"] == 57
