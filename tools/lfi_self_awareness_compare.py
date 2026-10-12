# -*- coding: utf-8 -*-
"""198批 刀2：LFI 对照拍本体件（独立离线件，零停窗）。

小时级自取 compute_lfi 双算「实际 overall」与「若五维参与的模拟 overall」，
双套并行（原始值套 + 归一化套）落 data/self_awareness/ 新曲线文件。

背景（烛微§2）：organ_activity 是单调不减累计计数——若原始计数直接入权，
模拟 overall 将随 uptime 系统性上漂。对策：对照拍同时跑两套序列。

判据四项：
- 第〇检查单：approx 必须翻为「运行态(pulse计数)」（确认已进入运行态口径）
- |Δ|≤2分：实际 overall 与模拟 overall 之差绝对值 ≤ 2 分（快照稳定性）
- 档位零翻转：按 activity 分档，原始值套与归一化套之间器官档位零翻转
- ≥1自然日：曲线文件时间戳跨度 ≥ 1 自然日（纵向证据；单点为 pending）

不做：不改 LFI 权重、不翻生产开关、不跑微权拍（推199）。
"""
from __future__ import annotations

import json
import os
import time

from nucleus._silent_except import silent_exc
from tools.export_lfi import compute_lfi

# 实际 overall 的活动维采用「冻结固定参考基准」归一化（不随 uptime 增长 → 暴露漂移）；
# 模拟 overall 的活动维采用「当前最大值」归一化（有界、稳定）。
# 首次运行冻结 ref = 当前最大值；后续 uptime 增长使原始值套 overall 相对 ref 抬升 → Δ 增大。
_ACTIVITY_ABS_REF_FROZEN = "lfi_overall_compare_ref.json"

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SELF_AWARENESS_DIR = os.path.join(_REPO, "data", "self_awareness")
_CURVE_FILE = os.path.join(_SELF_AWARENESS_DIR, "lfi_overall_compare.jsonl")


def _load_frozen_ref(ref_path: str) -> float | None:
    if os.path.exists(ref_path):
        try:
            with open(ref_path, "r", encoding="utf-8") as f:
                return float(json.load(f)["ref"])
        except Exception as e:  # noqa: BLE001
            silent_exc(e, where="lfi_self_awareness_compare._load_frozen_ref")
    return None


def _save_frozen_ref(ref: float, ref_path: str) -> None:
    try:
        os.makedirs(os.path.dirname(ref_path), exist_ok=True)
        with open(ref_path, "w", encoding="utf-8") as f:
            json.dump({"ref": ref}, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        silent_exc(e, where="lfi_self_awareness_compare._save_frozen_ref")


def fetch_runtime_activity() -> dict:
    """从 PulseBloodVessel 取运行态 organ_activity（按器官 organ_name 为键）。

    离线/不可用时返回空 dict（调用方据此判定未进入运行态口径）。
    """
    try:
        from nucleus.PulseBloodVessel import PulseBloodVessel

        inst = PulseBloodVessel.get_instance()
        stats = inst.get_stats()
        act = stats.get("organ_activity") or {}
        return {str(k): float(v) for k, v in act.items()}
    except Exception as e:  # noqa: BLE001
        silent_exc(e, where="lfi_self_awareness_compare.fetch_runtime_activity")
        return {}


def _overall_score(payload: dict, activity_override: dict, ref: float) -> float:
    """由 compute_lfi 五维输出聚合 overall（0~100）。

    activity 维归一化：
      - 实际套：raw / ref * 100（ref 冻结 → uptime 增长使分数抬升，暴露漂移）
      - 模拟套：norm * 100（norm = raw/cur_max，有界稳定）
    其余四维（连通/中心/冗余/健康）以自身 0~100 口径参与，两套一致。
    """
    nodes = payload["nodes"]
    n = payload["node_count"]
    denom = (n - 1) if n > 1 else 1
    ref = ref if ref > 0 else 1.0

    subs: list[float] = []
    for nd in nodes:
        d = nd["dims"]
        connectivity_s = (d["connectivity"] / denom * 100.0) if denom else 0.0
        centrality_s = d["centrality"] * 100.0
        redundancy_s = (d["redundancy"] / denom * 100.0) if denom else 0.0
        health_s = float(d["health"]["score"] or 0.0)
        av = float(activity_override.get(nd["name"], d["activity"]["value"]))
        activity_s = av / ref * 100.0
        subs.append((connectivity_s + centrality_s + redundancy_s + health_s + activity_s) / 5.0)
    return sum(subs) / len(subs) if subs else 0.0


def _tiers(activity: dict) -> dict:
    """按 activity 值降序分三档：high / mid / low。"""
    items = sorted(activity.items(), key=lambda kv: kv[1], reverse=True)
    k = len(items)
    out: dict = {}
    for i, (name, _v) in enumerate(items):
        if k <= 1:
            out[name] = "mid"
        elif i < k / 3:
            out[name] = "high"
        elif i < 2 * k / 3:
            out[name] = "mid"
        else:
            out[name] = "low"
    return out


def _span_days(curve_path: str) -> float:
    if not os.path.exists(curve_path):
        return 0.0
    try:
        ts: list[float] = []
        with open(curve_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ts.append(float(json.loads(line)["ts"]))
        if len(ts) < 2:
            return 0.0
        return (max(ts) - min(ts)) / 86400.0
    except Exception as e:  # noqa: BLE001
        silent_exc(e, where="lfi_self_awareness_compare._span_days")
        return 0.0


def _evaluate_checks(
    payload_raw: dict, payload_norm: dict, raw: dict, norm: dict,
    actual_overall: float, sim_overall: float, curve_path: str,
) -> dict:
    # 第〇检查单：approx 翻为「运行态(pulse计数)」
    approx_running = all(
        nd["dims"]["activity"]["approx"] == "运行态(pulse计数)" for nd in payload_raw["nodes"]
    )
    # |Δ|≤2分
    delta = abs(actual_overall - sim_overall)
    delta_within_2 = delta <= 2.0
    # 档位零翻转（原始值套 vs 归一化套同序 → 零翻转）
    raw_tiers = _tiers(raw)
    norm_tiers = _tiers(norm)
    common = set(raw_tiers) & set(norm_tiers)
    flips = [k for k in common if raw_tiers[k] != norm_tiers[k]]
    tier_zero_flip = len(flips) == 0
    # ≥1自然日
    span_ge_1_day = _span_days(curve_path) >= 1.0
    return {
        "approx_running_state": approx_running,
        "delta": round(delta, 4),
        "delta_within_2": delta_within_2,
        "tier_zero_flip": tier_zero_flip,
        "tier_flips": flips,
        "span_days": round(_span_days(curve_path), 4),
        "span_ge_1_day": span_ge_1_day,
    }


def run_once(
    runtime_activity: dict | None = None,
    curve_path: str | None = None,
    ref_path: str | None = None,
    persist_ref: bool = True,
) -> dict:
    """执行一次对照拍：双算 overall + 判据四项 + 落盘双曲线。

    Args:
        runtime_activity: 运行态 organ_activity（按 organ_name 为键）；为空则尝试自取，仍空则视为未进入运行态。
        curve_path: 曲线文件（默认 data/self_awareness/lfi_overall_compare.jsonl）。
        ref_path: 冻结参考基准文件（默认同目录 lfi_overall_compare_ref.json）。
        persist_ref: 是否持久化冻结 ref（测试可置 False）。
    """
    if curve_path is None:
        curve_path = _CURVE_FILE
    if ref_path is None:
        ref_path = os.path.join(_SELF_AWARENESS_DIR, _ACTIVITY_ABS_REF_FROZEN)

    if not runtime_activity:
        runtime_activity = fetch_runtime_activity()

    raw = {str(k): float(v) for k, v in runtime_activity.items()}
    cur_max = max(raw.values()) if raw else 1.0
    norm = {k: (v / cur_max if cur_max > 0 else 0.0) for k, v in raw.items()}

    # 双算：原始值套（raw）/ 归一化套（norm）；两套均提供全器官键 → approx 翻运行态
    payload_raw = compute_lfi(runtime_activity={k: int(v) for k, v in raw.items()})
    payload_norm = compute_lfi(runtime_activity={k: float(v) for k, v in norm.items()})

    ref = _load_frozen_ref(ref_path)
    if ref is None:
        ref = cur_max  # 首次运行：冻结 ref = 当前最大值（此刻 实际套==模拟套，Δ=0）
        if persist_ref:
            _save_frozen_ref(ref, ref_path)

    actual_overall = _overall_score(payload_raw, activity_override=raw, ref=ref)
    norm_max = max(norm.values()) if norm else 1.0
    sim_overall = _overall_score(payload_norm, activity_override=norm, ref=norm_max)

    checks = _evaluate_checks(
        payload_raw, payload_norm, raw, norm, actual_overall, sim_overall, curve_path
    )
    record = {
        "ts": time.time(),
        "actual_overall": round(actual_overall, 4),
        "sim_overall": round(sim_overall, 4),
        "delta": round(abs(actual_overall - sim_overall), 4),
        "ref": round(ref, 4),
        "checks": checks,
    }
    _append_curve(record, curve_path)
    return record


def _append_curve(record: dict, curve_path: str) -> None:
    try:
        os.makedirs(os.path.dirname(curve_path), exist_ok=True)
        with open(curve_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        silent_exc(e, where="lfi_self_awareness_compare._append_curve")


if __name__ == "__main__":
    rec = run_once()
    print(json.dumps(rec, ensure_ascii=False, indent=2))
