# -*- coding: utf-8 -*-
"""STDP 离线回放实验（类脑改造 P2 · 第205批 刀3）。

离线条目回放 training_pairs jsonl，按 STDP（Spike-Timing-Dependent Plasticity）
指数窗更新「实验件本地」突触权重字典，结果落盘 data/experiments/stdp_YYYYMMDD.jsonl。

★红线（与刀2一致）：
    - 不 import 也不写 ResonanceDetector 的 weight_map 实例（实验不蹭生产单例）；
    - 权重更新只发生在本模块的局部 dict（_weights）；
    - 纯离线回放，不接入任何生产脉冲链路。

模型（signed Δt，单位 ms）：
    Δt >= 0（pre 先于 post）→ 长时程增强 LTP：  Δw =  A₊ · e^(-Δt/τ₊)
    Δt <  0（post 先于 pre）→ 长时程抑制 LTD：  Δw = -A₋ · e^(Δt/τ₋)
    Δt = 0 归入 LTP 分支（pre 先，窗口内零延迟）。

窗口：仅 ±WINDOW_MS 内的候选 (pre_spike, post_spike) 对参与。

参数常量（文件头定值，随结果落盘为参数指纹，可复现）：A₊ / A₋ / τ₊ / τ₋ / WINDOW / SEED。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from nucleus._silent_except import silent_exc

# ===================== STDP 参数定值（参数指纹来源） =====================
STDP_VERSION = "m205-stdp-1"
STDP_A_PLUS = 0.01        # LTP 幅度 A₊
STDP_A_MINUS = 0.012      # LTD 幅度 A₋
STDP_TAU_PLUS_MS = 20.0   # LTP 时间常数 τ₊（ms）
STDP_TAU_MINUS_MS = 20.0  # LTD 时间常数 τ₋（ms）
STDP_WINDOW_MS = 20.0     # 候选对窗口 ±WINDOW_MS
STDP_SEED = 2051013       # 可复现种子（回放本身确定性；记录以备抽样扩展）
STDP_OUT_DIR = "data/experiments"


def stdp_delta_w(delta_t_ms: float,
                a_plus: float = STDP_A_PLUS, tau_plus: float = STDP_TAU_PLUS_MS,
                a_minus: float = STDP_A_MINUS, tau_minus: float = STDP_TAU_MINUS_MS) -> float:
    """STDP 指数窗 Δw（signed Δt，ms）。

    Δt>=0 → LTP（+）；Δt<0 → LTD（-）。越靠近 0 幅度越大，随 |Δt| 指数衰减。
    """
    if delta_t_ms >= 0:
        return a_plus * math.exp(-delta_t_ms / tau_plus)
    return -a_minus * math.exp(delta_t_ms / tau_minus)  # delta_t<0 ⇒ 指数为负


def param_fingerprint() -> Dict[str, Any]:
    """参数指纹（随每条结果落盘，保证可复现）。"""
    return {
        "version": STDP_VERSION,
        "A_plus": STDP_A_PLUS,
        "A_minus": STDP_A_MINUS,
        "tau_plus_ms": STDP_TAU_PLUS_MS,
        "tau_minus_ms": STDP_TAU_MINUS_MS,
        "window_ms": STDP_WINDOW_MS,
        "seed": STDP_SEED,
    }


def load_traces(paths: List[str]) -> List[Dict[str, Any]]:
    """读取一个或多个 training_pairs jsonl，过滤出含合法 ts 的记录。"""
    records: List[Dict[str, Any]] = []
    for _p in paths:
        if not os.path.exists(_p):
            continue
        try:
            with open(_p, encoding="utf-8") as _f:
                for _ln in _f:
                    _ln = _ln.strip()
                    if not _ln:
                        continue
                    try:
                        _o = json.loads(_ln)
                    except Exception as e:
                        silent_exc(e)
                        continue
                    if not isinstance(_o, dict) or "ts" not in _o:
                        continue
                    try:
                        _o["ts"] = float(_o["ts"])
                    except Exception as e:
                        silent_exc(e)
                        continue
                    records.append(_o)
        except Exception as e:
            silent_exc(e)
    return records


def _rec_id(rec: Dict[str, Any], idx: int) -> str:
    return str(rec.get("trace_id") or rec.get("id") or idx)


def build_pairs(records: List[Dict[str, Any]], window_ms: float = STDP_WINDOW_MS
                ) -> List[Dict[str, Any]]:
    """按 ts 排序后在 ±window_ms 内构造 (pre, post) 候选对。

    排序后对每个 i，向后扫描 j>i 直到 ts 差超过窗口（早停）。pre=较早者、
    post=较迟者，delta_t_ms = (post_ts - pre_ts) * 1000（恒 >= 0）。
    """
    recs = sorted(records, key=lambda r: r["ts"])
    n = len(recs)
    pairs: List[Dict[str, Any]] = []
    for i in range(n):
        pre = recs[i]
        pre_ts = pre["ts"]
        for j in range(i + 1, n):
            post = recs[j]
            dt_ms = (post["ts"] - pre_ts) * 1000.0
            if dt_ms > window_ms:
                break  # 后续 j 更迟，必超窗
            pairs.append({
                "pre": pre, "post": post,
                "pre_id": _rec_id(pre, i), "post_id": _rec_id(post, j),
                "delta_t_ms": dt_ms,
            })
    return pairs


def run_experiment(input_paths: List[str],
                   out_path: Optional[str] = None,
                   window_ms: float = STDP_WINDOW_MS,
                   weights: Optional[Dict[str, float]] = None
                   ) -> Dict[str, Any]:
    """执行一次 STDP 离线回放。

    返回 {results, weights, params, pair_count}；若 out_path 给定则追加落盘。
    权重更新仅在本模块局部 dict（_weights），绝不触碰生产单例。
    """
    fp = param_fingerprint()
    recs = load_traces(input_paths)
    pairs = build_pairs(recs, window_ms=window_ms)
    _weights: Dict[str, float] = weights if weights is not None else {}
    results: List[Dict[str, Any]] = []

    for k, pr in enumerate(pairs):
        pre_o = pr["pre"].get("origin", "?")
        post_o = pr["post"].get("origin", "?")
        key = "%s->%s" % (pre_o, post_o)
        dt_ms = pr["delta_t_ms"]
        dw = stdp_delta_w(dt_ms)
        w_before = _weights.get(key, 0.0)
        w_after = w_before + dw
        _weights[key] = w_after
        results.append({
            "pair_id": k,
            "pre_id": pr["pre_id"],
            "post_id": pr["post_id"],
            "pre_origin": pre_o,
            "post_origin": post_o,
            "delta_t_ms": round(dt_ms, 6),
            "delta_w": round(dw, 10),
            "w_before": round(w_before, 10),
            "w_after": round(w_after, 10),
            "synapse": key,
            "params": fp,
        })

    if out_path:
        try:
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as _of:
                for _r in results:
                    _of.write(json.dumps(_r, ensure_ascii=False))
                    _of.write("\n")
        except Exception as e:
            silent_exc(e)

    return {
        "results": results,
        "weights": _weights,
        "params": fp,
        "pair_count": len(results),
        "record_count": len(recs),
        "out_path": out_path,
    }


def _default_out_path() -> str:
    return os.path.join(
        STDP_OUT_DIR,
        "stdp_%s.jsonl" % datetime.now().strftime("%Y%m%d"),
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="STDP 离线回放实验（第205批 刀3）")
    ap.add_argument("--input", nargs="+", required=True,
                    help="training_pairs jsonl 路径（可多个）")
    ap.add_argument("--out", default=None,
                    help="结果落盘路径（默认 data/experiments/stdp_YYYYMMDD.jsonl）")
    ap.add_argument("--window-ms", type=float, default=STDP_WINDOW_MS)
    args = ap.parse_args(argv)

    out = args.out or _default_out_path()
    exp = run_experiment(args.input, out_path=out, window_ms=args.window_ms)
    sys.stderr.write(
        "[stdp_experiment] records=%d pairs=%d out=%s\n"
        % (exp["record_count"], exp["pair_count"], out)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

