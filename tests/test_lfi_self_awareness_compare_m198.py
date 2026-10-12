# -*- coding: utf-8 -*-
"""198批 刀2：LFI 对照拍本体件 隔离单测。

验收：双套并行（实际/模拟 overall）+ 判据四项（第〇 approx 运行态 / |Δ|≤2 / 档位零翻转 / ≥1自然日）
+ 隔离单测过（含双套并行断言）。
"""
import json
import os

from tools.export_lfi import compute_lfi
from tools.lfi_self_awareness_compare import run_once


def _organ_names() -> list:
    payload = compute_lfi(runtime_activity=None)
    return [nd["name"] for nd in payload["nodes"]]


def _synthetic_activity() -> dict:
    names = _organ_names()
    # 小数值（低于首跑冻结 ref = max）保证快照 Δ≤2
    return {name: float((i % 20) + 1) for i, name in enumerate(names)}


def test_tool_file_has_four_checks_in_source():
    """静态断言：工具文件包含判据四项关键字。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "lfi_self_awareness_compare.py")
    src = open(path, "r", encoding="utf-8").read()
    for kw in ["approx_running_state", "delta_within_2", "tier_zero_flip", "span_ge_1_day"]:
        assert kw in src, f"判据缺失：{kw}"


def test_dual_set_parallel_and_four_checks(tmp_path):
    """注入合成运行态活动，验证双套并行 + 判据四项（快照态）。"""
    curve = os.path.join(str(tmp_path), "lfi_overall_compare.jsonl")
    ref = os.path.join(str(tmp_path), "lfi_overall_compare_ref.json")

    rec = run_once(runtime_activity=_synthetic_activity(), curve_path=curve, ref_path=ref)

    # 双套并行：实际 / 模拟 overall 均存在且为浮点
    assert isinstance(rec["actual_overall"], float)
    assert isinstance(rec["sim_overall"], float)
    assert "actual_overall" in rec and "sim_overall" in rec

    checks = rec["checks"]
    # 第〇检查单：approx 翻为运行态
    assert checks["approx_running_state"] is True
    # |Δ|≤2 分（首跑 ref=max → Δ=0）
    assert checks["delta_within_2"] is True
    assert checks["delta"] <= 2.0
    # 档位零翻转（归一化套与原始值套同序）
    assert checks["tier_zero_flip"] is True
    assert checks["tier_flips"] == []
    # ≥1自然日：单点 → 尚未达到（pending，非失败）
    assert checks["span_ge_1_day"] is False

    # 曲线文件落盘（双曲线：单行含 actual+sim 两列）
    assert os.path.exists(curve)
    lines = [l for l in open(curve, "r", encoding="utf-8").read().splitlines() if l.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert "actual_overall" in row and "sim_overall" in row


def test_approx_check_false_when_no_runtime(tmp_path):
    """无运行态活动（离线）时，第〇检查单应为 False（正确反映未进入运行态）。"""
    curve = os.path.join(str(tmp_path), "lfi_overall_compare_noact.jsonl")
    ref = os.path.join(str(tmp_path), "lfi_overall_compare_ref_noact.json")
    rec = run_once(runtime_activity={}, curve_path=curve, ref_path=ref)
    assert rec["checks"]["approx_running_state"] is False
