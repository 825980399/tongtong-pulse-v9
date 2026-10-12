# -*- coding: utf-8 -*-
"""195刀2 隔离单测：export_lfi 活跃度数据源切换（method_count -> BloodVessel organ_activity）。

验收：运行态活跃度注入时 activity 维切到真实值并标注运行态；未注入时回退 method_count（零行为变化）。
"""
from tools.export_lfi import compute_lfi, export_lfi


def _find(nodes, name):
    for n in nodes:
        if n["name"] == name:
            return n
    return None


def test_default_fallback_method_count():
    res = compute_lfi()
    assert len(res["nodes"]) == res["node_count"]
    for n in res["nodes"]:
        assert n["dims"]["activity"]["approx"] == "静态近似(method_count)"


def test_runtime_activity_switch():
    base = compute_lfi()
    name = base["nodes"][0]["name"]
    res = compute_lfi(runtime_activity={name: 99})
    node = _find(res["nodes"], name)
    assert node["dims"]["activity"]["value"] == 99
    assert node["dims"]["activity"]["approx"] == "运行态(pulse计数)"


def test_runtime_activity_partial():
    base = compute_lfi()
    n0 = base["nodes"][0]["name"]
    n1 = base["nodes"][1]["name"]
    res = compute_lfi(runtime_activity={n0: 5})
    a = _find(res["nodes"], n0)
    b = _find(res["nodes"], n1)
    assert a["dims"]["activity"]["value"] == 5
    assert b["dims"]["activity"]["approx"] == "静态近似(method_count)"


def test_export_lfi_passthrough():
    name = compute_lfi()["nodes"][0]["name"]
    res = export_lfi(runtime_activity={name: 3})
    assert _find(res["nodes"], name)["dims"]["activity"]["value"] == 3
