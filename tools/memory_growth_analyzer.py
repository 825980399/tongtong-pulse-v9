#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""内存增长排查分析器（只读，绝不写生产数据）—— 主线第65批 T5/P2。

用法：
    python tools/memory_growth_analyzer.py [--top 20] [--report <out.json>]

产出：进程 RSS/VMS、top 对象类型（按实例数量）、GC 统计，可选写出结构化报告。
安全：仅做 gc/sys 内省与 psutil 进程快照，不修改任何框架状态、不写生产 data/。
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def get_process_memory() -> dict:
    """当前进程内存占用（psutil 可用时；否则降级为 0）。"""
    _rss = _vms = _pct = 0.0
    try:
        import psutil
        _p = psutil.Process()
        _mi = _p.memory_info()
        _rss = _mi.rss / 1048576.0
        _vms = _mi.vms / 1048576.0
        _pct = _p.memory_percent()
    except Exception:
        pass
    return {"rss_mb": round(_rss, 1), "vms_mb": round(_vms, 1), "percent": round(_pct, 1)}


def analyze_top_object_types(limit: int = 20) -> list[dict]:
    """按实例数量统计 top 对象类型（排查内存增长来源，如缓存无限增长）。"""
    _cnt: dict[str, int] = {}
    for _o in gc.get_objects():
        _t = type(_o).__name__
        _cnt[_t] = _cnt.get(_t, 0) + 1
    _sorted = sorted(_cnt.items(), key=lambda kv: kv[1], reverse=True)
    return [{"type": k, "count": v} for k, v in _sorted[:limit]]


def build_report(limit: int = 20) -> dict:
    _gc_stats = []
    try:
        _gc_stats = [s.get("collections", 0) for s in gc.get_stats()]
    except Exception:
        _gc_stats = []
    return {
        "generated_at": time.time(),
        "process_memory": get_process_memory(),
        "top_object_types": analyze_top_object_types(limit),
        "gc_collections": _gc_stats,
        "read_only": True,
    }


def compare_snapshots(prev: dict, cur: dict) -> dict:
    """★主线第66批 T1.1：对比两份 build_report() 快照，定位增长最快的对象类型。

    prev/cur 均为 build_report() 产出的 dict。返回：
      {rss_delta_mb, vms_delta_mb, delta_top_types:[{type, prev, cur, delta}...]}
    delta_top_types 按 delta 降序（增长最快在前），供定位内存泄漏模块。
    """
    _prev_map = {r["type"]: r["count"] for r in prev.get("top_object_types", [])}
    _cur_map = {r["type"]: r["count"] for r in cur.get("top_object_types", [])}
    _all = set(_prev_map) | set(_cur_map)
    _deltas = []
    for _t in _all:
        _p = _prev_map.get(_t, 0)
        _c = _cur_map.get(_t, 0)
        if _c != _p:
            _deltas.append({"type": _t, "prev": _p, "cur": _c, "delta": _c - _p})
    _deltas.sort(key=lambda d: d["delta"], reverse=True)
    _pm = prev.get("process_memory", {})
    _cm = cur.get("process_memory", {})
    return {
        "rss_delta_mb": round(_cm.get("rss_mb", 0.0) - _pm.get("rss_mb", 0.0), 1),
        "vms_delta_mb": round(_cm.get("vms_mb", 0.0) - _pm.get("vms_mb", 0.0), 1),
        "delta_top_types": _deltas,
    }


def main() -> int:
    _ap = argparse.ArgumentParser(description="内存增长排查分析器（只读）")
    _ap.add_argument("--top", type=int, default=20)
    _ap.add_argument("--report", default="")
    _ns = _ap.parse_args()
    _rep = build_report(_ns.top)
    print(f"进程内存: RSS={_rep['process_memory']['rss_mb']:.1f}MB VMS={_rep['process_memory']['vms_mb']:.1f}MB 占比={_rep['process_memory']['percent']:.1f}%")
    print(f"Top {_ns.top} 对象类型:")
    for _r in _rep["top_object_types"]:
        print(f"  {_r['type']:<30} {_r['count']}")
    if _ns.report:
        _d = os.path.dirname(os.path.abspath(_ns.report))
        if _d and not os.path.isdir(_d):
            os.makedirs(_d, exist_ok=True)
        with open(_ns.report, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(_rep, ensure_ascii=False, indent=2))
        print("报告已写出 →", _ns.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
