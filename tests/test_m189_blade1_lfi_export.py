# -*- coding: utf-8 -*-
"""189批刀1 隔离单测：LFI 五维导出。

验证点：
  - 57 节点；
  - 五维（connectivity/centrality/redundancy/health/activity）齐全；
  - 健康度纯静态 => 未装配(None)；
  - 活跃度标注「静态近似」；
  - 不加权、不接 overall_score、不翻生产开关；
  - 各维度非负、中心性∈[0,1]；
  - JSON 可序列化往返。
"""
from __future__ import annotations

import json

from tools.export_lfi import compute_lfi, export_lfi


def test_lfi_node_count_57():
    _p = compute_lfi()
    assert _p["node_count"] == 57, _p["node_count"]


def test_lfi_five_dims_present():
    _p = compute_lfi()
    _req = {"connectivity", "centrality", "redundancy", "health", "activity"}
    for _n in _p["nodes"]:
        assert set(_n["dims"].keys()) == _req, _n["class_name"]


def test_lfi_health_static_unassembled():
    _p = compute_lfi()
    # 纯静态导出：无运行态 => 全部未装配(None)
    assert all(_n["dims"]["health"]["score"] is None for _n in _p["nodes"])
    assert all(_n["dims"]["health"]["status"] == "未装配" for _n in _p["nodes"])


def test_lfi_activity_static_approx():
    _p = compute_lfi()
    for _n in _p["nodes"]:
        _a = _n["dims"]["activity"]
        assert isinstance(_a["value"], int) and _a["value"] >= 0
        assert "静态近似" in _a["approx"]


def test_lfi_no_weight_no_overall():
    _p = compute_lfi()
    assert _p["meta"]["weighted"] is False
    assert _p["meta"]["overall_score"] is False
    assert _p["meta"]["production_switch_flipped"] is False


def test_lfi_dims_nonneg():
    _p = compute_lfi()
    for _n in _p["nodes"]:
        _d = _n["dims"]
        assert isinstance(_d["connectivity"], int) and _d["connectivity"] >= 0
        assert isinstance(_d["centrality"], (int, float)) and 0.0 <= _d["centrality"] <= 1.0
        assert isinstance(_d["redundancy"], int) and _d["redundancy"] >= 0


def test_export_lfi_json_roundtrip():
    _s = export_lfi(as_json=True)
    _o = json.loads(_s)
    assert _o["node_count"] == 57
    assert len(_o["nodes"]) == 57
