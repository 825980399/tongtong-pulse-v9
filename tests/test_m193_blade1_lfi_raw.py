# -*- coding: utf-8 -*-
"""193批刀1隔离单测：SelfAwarenessProfile.lfi_raw 影子拍（零权重）。

覆盖：
- 字段存在且默认 None；
- to_dict 含 lfi_raw；from_dict 往返还原；
- lfi_raw 不在 _PROFILE_DICT_FIELDS（自然零权重）；
- compute_overall_score 不受 lfi_raw 影响；
- LFI 四维原值结构（连通性/中心性/冗余度/健康度，剔除活跃度）。
"""
import sys
import os
sys.path.insert(0, os.getcwd())

from nucleus.self_awareness.SelfAwarenessEngine import (
    SelfAwarenessProfile,
    _PROFILE_DICT_FIELDS,
    compute_overall_score,
)
from tools.export_lfi import compute_lfi


def test_lfi_raw_field_default_none():
    p = SelfAwarenessProfile()
    assert hasattr(p, "lfi_raw")
    assert p.lfi_raw is None


def test_lfi_raw_in_to_dict():
    p = SelfAwarenessProfile()
    p.lfi_raw = {"node_count": 57, "nodes": []}
    d = p.to_dict()
    assert "lfi_raw" in d
    assert d["lfi_raw"] == {"node_count": 57, "nodes": []}


def test_lfi_raw_from_dict_roundtrip():
    src = {
        "lfi_raw": {"version": "v1", "node_count": 57, "nodes": [{"class_name": "X"}]},
        "overall_score": 80.0,
        "health_level": "healthy",
    }
    p = SelfAwarenessProfile.from_dict(src)
    assert isinstance(p.lfi_raw, dict)
    assert p.lfi_raw["node_count"] == 57
    # 非 dict 入参退化为 None
    p2 = SelfAwarenessProfile.from_dict({"lfi_raw": "garbage"})
    assert p2.lfi_raw is None


def test_lfi_raw_not_in_weighted_fields():
    # 关键：lfi_raw 不进入 _PROFILE_DICT_FIELDS → 不参与 overall_score 加权
    assert "lfi_raw" not in _PROFILE_DICT_FIELDS


def test_overall_score_ignores_lfi_raw():
    # 设一个可用维度（code_health 评分），对比 lfi_raw 有无不影响 overall_score
    base = SelfAwarenessProfile()
    base.code_health = {"score": 90, "status": "ok"}
    r1 = compute_overall_score(base)

    base.lfi_raw = {"node_count": 57, "nodes": [{"dims": {"connectivity": 5}}]}
    r2 = compute_overall_score(base)
    assert r1["overall_score"] == r2["overall_score"]


def test_lfi_raw_structure_excludes_activity():
    payload = compute_lfi()
    assert payload["node_count"] > 0
    nodes = payload.get("nodes", [])
    assert nodes
    # 复刻 run_all_analyses 的 lfi_raw 构造：剔除 activity
    built = []
    for nd in nodes:
        d = dict(nd.get("dims") or {})
        d.pop("activity", None)
        built.append({"class_name": nd.get("class_name"), "dims": d})
    # 每个节点四维齐备，无 activity
    for n in built:
        assert set(n["dims"].keys()) == {"connectivity", "centrality", "redundancy", "health"}
    assert "activity" not in built[0]["dims"]
