# -*- coding: utf-8 -*-
"""刀3 验收：STDP 离线回放实验（隔离单测 · 铁律151）。"""
import io
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tools.stdp_experiment as ste  # noqa: E402


# ---------- 1. Δw 符号与量级 ----------
def test_delta_w_ltp_positive():
    dw = ste.stdp_delta_w(5.0)
    assert dw > 0
    assert abs(dw - ste.STDP_A_PLUS * math.exp(-5.0 / ste.STDP_TAU_PLUS_MS)) < 1e-12


def test_delta_w_ltd_negative():
    dw = ste.stdp_delta_w(-5.0)
    assert dw < 0
    assert abs(dw - (-ste.STDP_A_MINUS * math.exp(-5.0 / ste.STDP_TAU_MINUS_MS))) < 1e-12


def test_delta_w_zero_boundary_is_ltp():
    assert ste.stdp_delta_w(0.0) == ste.STDP_A_PLUS
    # 量级随 |Δt| 增大而衰减
    assert ste.stdp_delta_w(20.0) < ste.stdp_delta_w(5.0)
    assert ste.stdp_delta_w(5.0) > ste.stdp_delta_w(10.0)


# ---------- 2. 窗口内候选对构造 ----------
def test_build_pairs_window_filter():
    recs = [
        {"trace_id": "a1", "ts": 100.000, "origin": "A"},
        {"trace_id": "b1", "ts": 100.005, "origin": "B"},
        {"trace_id": "a2", "ts": 100.010, "origin": "A"},
        {"trace_id": "b2", "ts": 100.050, "origin": "B"},  # 与前面均 >20ms
    ]
    pairs = ste.build_pairs(recs, window_ms=20.0)
    assert len(pairs) == 3
    dts = sorted(round(p["delta_t_ms"], 6) for p in pairs)
    assert dts == [5.0, 5.0, 10.0]
    for p in pairs:
        assert p["delta_t_ms"] <= 20.0
        assert p["delta_t_ms"] >= 0.0


def test_build_pairs_excludes_outside_window():
    recs = [
        {"trace_id": "x", "ts": 100.000, "origin": "A"},
        {"trace_id": "y", "ts": 100.030, "origin": "B"},  # 30ms > 20ms 窗口
    ]
    assert ste.build_pairs(recs, window_ms=20.0) == []


def test_build_pairs_zero_dt_boundary():
    recs = [
        {"trace_id": "x", "ts": 100.000, "origin": "A"},
        {"trace_id": "z", "ts": 100.000, "origin": "B"},  # 同拍 → dt=0
    ]
    pairs = ste.build_pairs(recs, window_ms=20.0)
    assert len(pairs) == 1
    assert pairs[0]["delta_t_ms"] == 0.0


# ---------- 3. 回放 + 权重累加到本地 dict ----------
def _write_tmp_jsonl(tmp_path, recs):
    p = tmp_path / "tp.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(p)


def test_run_experiment_accumulates_local_weights(tmp_path):
    recs = [
        {"trace_id": "a1", "ts": 100.000, "origin": "A"},
        {"trace_id": "b1", "ts": 100.005, "origin": "B"},
        {"trace_id": "a2", "ts": 100.010, "origin": "A"},
    ]
    inp = _write_tmp_jsonl(tmp_path, recs)
    out = str(tmp_path / "stdp_out.jsonl")
    exp = ste.run_experiment([inp], out_path=out)
    assert exp["pair_count"] == 3  # (a1,b1) dt5, (a1,a2) dt10, (b1,a2) dt5
    res = exp["results"]
    # 每条 w_before=0, w_after=delta_w（首次更新）
    for r in res:
        assert r["w_before"] == 0.0
        assert abs(r["w_after"] - r["delta_w"]) < 1e-12
        assert r["params"]["version"] == ste.STDP_VERSION
    # 落盘文件存在且含 3 行
    with open(out, encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    assert len(lines) == 3


def test_run_experiment_seed_reproducible(tmp_path):
    recs = [
        {"trace_id": "a1", "ts": 100.000, "origin": "A"},
        {"trace_id": "b1", "ts": 100.005, "origin": "B"},
        {"trace_id": "a2", "ts": 100.010, "origin": "A"},
    ]
    inp = _write_tmp_jsonl(tmp_path, recs)
    e1 = ste.run_experiment([inp])
    e2 = ste.run_experiment([inp])
    assert [r["pair_id"] for r in e1["results"]] == [r["pair_id"] for r in e2["results"]]
    assert [r["delta_w"] for r in e1["results"]] == [r["delta_w"] for r in e2["results"]]
    assert e1["weights"] == e2["weights"]


# ---------- 4. 红线：不 import / 不写生产 weight_map ----------
def test_red_line_no_resonance_detector_reference():
    src_path = os.path.join(ROOT, "tools", "stdp_experiment.py")
    with io.open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    # 红线：不得 import / 调用 ResonanceDetector（docstring 内的禁述文字允许）
    assert "import ResonanceDetector" not in src
    assert "ResonanceDetector." not in src
    assert "ResonanceDetector(" not in src
    # 红线：不得触碰生产 weight_map 实例（本地 dict 命名为 _weights）
    # 仅在代码区（docstring 之后）检查 weight_map 属性访问
    code_after_doc = src.split('"""', 2)[-1]
    assert "weight_map" not in code_after_doc, "红线：不得触碰生产 weight_map"

