# -*- coding: utf-8 -*-
"""364 刀 A1：内存增长归属采样器（**先证真伪，再谈释放**）。

为什么需要它
------------
A1 的现状是「+2.93GB/2h15m、后段 16–17MB/min」这类**只看总 RSS 的读数**，
它无法回答「涨的是谁的」。本工具补三件事：
  1. **对象类型采样**：按 `type().__name__` 统计存活对象数与增量 Top N
     （gc.get_objects()，只在采样时刻跑，不常驻勾子，避免自身改变被测对象）；
  2. **分代计数**：`gc.get_count()` 三代计数 + `gc.get_stats()` 回收统计；
  3. **启动尖峰剔除**：启动后 `WARMUP_SEC` 秒内的样本单独标记，**不参与后段斜率**
     （首拍 9270MB/min 的启动尖峰就是被这条规则剔掉的依据）。

判据（与任务书一致）
--------------------
  * 后段稳态斜率 = 剔除 warmup 后线性回归 (MB/min)；
  * `slope_steady > MEMORY_GROWTH_ALARM_MB_PER_MIN`（默认 10）⇒ `leak_suspect`；
  * 否则 `stable`；
  * ★本工具**只出读数与归属线索，不下「泄漏」终论** —— 归属需配合停学对照
    （`--note stop-learning` 人工标注窗口）与 release 开关评估。

用法
----
    python tools/memory_attribution_probe.py --duration 900 --interval 30 --out tmp/mem.json
    python tools/memory_attribution_probe.py --duration 60 --interval 5 --out tmp/mem_quick.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time

#: 采样器自身日志（采样热路径只打 DEBUG；失败不静默 —— 门禁口径：纯 pass/纯 assign 一律判静默）
_LOG = logging.getLogger("tools.memory_attribution_probe")


def _rss_mb() -> float:
    """进程 RSS（MB）：优先 psutil，退化读 /proc 或返回 -1（不猜 0）。"""
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception as _pe:
        _LOG.debug("psutil 取 RSS 失败，退化 /proc: %s: %s", type(_pe).__name__, _pe)
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(float(line.split()[1]) / 1024.0, 1)
    except Exception as _fe:
        _LOG.debug("/proc 读 VmRSS 失败，RSS 记 -1（不猜 0）: %s: %s",
                   type(_fe).__name__, _fe)
    return -1.0


def sample_once(top_n: int = 12) -> dict:
    """采一次样：RSS + gc 分代 + 存活对象类型 Top N。"""
    import gc
    _gc = gc.get_count()
    _stats = gc.get_stats()
    _types: dict[str, int] = {}
    _total = 0
    for _o in gc.get_objects():
        _total += 1
        try:
            _n = type(_o).__name__
        except Exception as _te:
            _n = "<unknown>"
            _LOG.debug("对象类型名获取失败: %s: %s", type(_te).__name__, _te)
        _types[_n] = _types.get(_n, 0) + 1
    _top = sorted(_types.items(), key=lambda x: -x[1])[:top_n]
    return {
        "ts": time.time(),
        "rss_mb": _rss_mb(),
        "gc_counts": list(_gc),
        "gc_collections": [int(s.get("collections", 0)) for s in _stats],
        "gc_collected": [int(s.get("collected", 0)) for s in _stats],
        "obj_total": _total,
        "top_types": [[k, v] for k, v in _top],
        "all_types": _types,
    }


def _slope_mb_per_min(samples: list) -> float:
    """最小二乘斜率（MB/min）；样本 <2 返回 0.0（无样本不给伪读数）。"""
    _pts = [(s["ts"], s["rss_mb"]) for s in samples
            if isinstance(s.get("rss_mb"), (int, float)) and s["rss_mb"] > 0]
    if len(_pts) < 2:
        return 0.0
    _n = float(len(_pts))
    _mx = sum(p[0] for p in _pts) / _n
    _my = sum(p[1] for p in _pts) / _n
    _num = sum((x - _mx) * (y - _my) for x, y in _pts)
    _den = sum((x - _mx) ** 2 for x, _ in _pts)
    if _den <= 0:
        return 0.0
    return round((_num / _den) * 60.0, 2)


def _type_growth(samples: list) -> list:
    """warmup 末样本 vs 末样本的类型计数增量 Top 12（归属线索）。"""
    if len(samples) < 2:
        return []
    _a = samples[0].get("all_types") or {}
    _b = samples[-1].get("all_types") or {}
    _diff = [(k, int(_b.get(k, 0)) - int(_a.get(k, 0))) for k in set(_a) | set(_b)]
    _diff = [d for d in _diff if d[1] != 0]
    _diff.sort(key=lambda x: -x[1])
    return [[k, v] for k, v in _diff[:12]]


def run(duration: float, interval: float, warmup: float, top_n: int = 12) -> dict:
    """按 `interval` 采样 `duration` 秒；`warmup` 内样本标 `in_warmup=True`。"""
    _t0 = time.time()
    _samples: list = []
    while True:
        _s = sample_once(top_n)
        _s["elapsed"] = round(_s["ts"] - _t0, 1)
        _s["in_warmup"] = _s["elapsed"] < float(warmup)
        _samples.append(_s)
        if _s["elapsed"] >= float(duration):
            break
        time.sleep(max(0.05, float(interval)))
    _steady = [s for s in _samples if not s["in_warmup"]]
    _slope_all = _slope_mb_per_min(_samples)
    _slope_steady = _slope_mb_per_min(_steady)
    try:
        import config as _c
        _thr = float(getattr(_c, "MEMORY_GROWTH_ALARM_MB_PER_MIN", 10.0))
    except Exception as _ce:
        _thr = 10.0
        _LOG.debug("阈值读取失败，用默认 10.0: %s: %s", type(_ce).__name__, _ce)
    return {
        "duration_sec": round(time.time() - _t0, 1),
        "interval_sec": float(interval),
        "warmup_sec": float(warmup),
        "sample_count": len(_samples),
        "steady_sample_count": len(_steady),
        "slope_all_mb_per_min": _slope_all,
        "slope_steady_mb_per_min": _slope_steady,
        "threshold_mb_per_min": _thr,
        # ★只给可疑/稳定两态，「是不是泄漏」要配合停学对照与 release 开关评估
        "verdict": ("leak_suspect" if _slope_steady > _thr else "stable"),
        "type_growth": _type_growth(_steady or _samples),
        "samples": [{k: v for k, v in s.items() if k != "all_types"} for s in _samples],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="内存增长归属采样器（364刀A1）")
    ap.add_argument("--duration", type=float, default=600.0, help="采样总时长（秒）")
    ap.add_argument("--interval", type=float, default=30.0, help="采样间隔（秒）")
    ap.add_argument("--warmup", type=float, default=120.0,
                    help="启动尖峰剔除窗口（秒），窗口内样本不计入后段斜率")
    ap.add_argument("--top-n", type=int, default=12)
    ap.add_argument("--note", default="", help="人工标注（如 stop-learning / release-on）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    _doc = run(args.duration, args.interval, args.warmup, args.top_n)
    _doc["note"] = args.note
    _doc["pid"] = os.getpid()
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_doc, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, args.out)
    print("[A1probe] samples=%d slope_all=%.2f slope_steady=%.2f verdict=%s -> %s"
          % (_doc["sample_count"], _doc["slope_all_mb_per_min"],
             _doc["slope_steady_mb_per_min"], _doc["verdict"], args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
