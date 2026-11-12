#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""493.22 刀5：每小时 RSS 采样器（只观察，不修内存）。

用法（项目根下，外部每小时调度一次，如系统计划任务 / PulseHeart 定时任务）：
    python tools/rss_hourly_probe_49322.py              # 采一次并追加
    python tools/rss_hourly_probe_49322.py --report     # 基于已有采样出趋势报告

采样写 `data/monitor/rss_hourly_49322.jsonl`（数据不进 git）。
★诚实口径：本批只交付采样器与首轮真实采样；**24 小时连续数据需框架运行侧
累积**，不伪造。修复放到后续综合优化 P0 批次（任务书明确不做修复）。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc  # noqa: E402

_PROBE = os.path.join("data", "monitor", "rss_hourly_49322.jsonl")
_HEALTH = os.path.join("data", "monitor", "health_snapshot.json")


def rss_mb():
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 1)
    except Exception as _e:
        silent_exc(_e, where="rss_hourly_probe.rss_mb.psutil", level="debug")
        try:  # /proc 兜底（Linux）
            with open("/proc/self/status", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return round(int(line.split()[1]) / 1024, 1)
        except Exception as _e2:
            silent_exc(_e2, where="rss_hourly_probe.rss_mb.proc", level="debug")
    return None


def sample_once():
    rec = {"ts": round(time.time(), 3), "rss_mb": rss_mb(),
           "hour": time.strftime("%Y-%m-%d %H:00")}
    # 附带框架 health 快照里已有的真实读数（若存在），便于交叉核对
    try:
        if os.path.isfile(_HEALTH):
            d = json.load(open(_HEALTH, encoding="utf-8"))
            m = d.get("memory", {}) or {}
            ga = (d.get("memory_pressure", {}) or {}).get("growth_alarm", {}) or {}
            rec["health_process_rss_mb"] = m.get("process_rss_mb")
            rec["health_growth_mb_per_min"] = ga.get("growth_mb_per_min")
            rec["health_is_leak_signal"] = ga.get("is_leak_signal")
    except Exception as _e:
        silent_exc(_e, where="rss_hourly_probe.sample_once.health", level="debug")
    os.makedirs(os.path.dirname(_PROBE), exist_ok=True)
    with open(_PROBE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def trend_report():
    rows = []
    if os.path.isfile(_PROBE):
        rows = [json.loads(l) for l in open(_PROBE, encoding="utf-8").read().splitlines()
                if l.strip()]
    valid = [r for r in rows if r.get("rss_mb") is not None]
    print("采样条数:", len(rows), "| 有效 RSS:", len(valid))
    if valid:
        print("首/末 RSS(MB):", valid[0]["rss_mb"], "→", valid[-1]["rss_mb"])
        _span_h = (valid[-1]["ts"] - valid[0]["ts"]) / 3600
        if _span_h > 0:
            print("跨度 %.2f h，增长 %.1f MB/min（采样器自身进程，非活体框架）"
                  % (_span_h, (valid[-1]["rss_mb"] - valid[0]["rss_mb"]) / (_span_h * 60)))
        hr = [r for r in rows if r.get("health_process_rss_mb") is not None]
        if hr:
            print("活体 health 读数(MB):", hr[-1]["health_process_rss_mb"],
                  "| growth_mb_per_min:", hr[-1].get("health_growth_mb_per_min"),
                  "| is_leak_signal:", hr[-1].get("health_is_leak_signal"))
    if not rows or len(valid) < 24:
        print("★24 小时连续样本尚未累积（当前 %d 条），不出 24h 结论——不伪造数据" % len(valid))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只出趋势报告不采样")
    a = ap.parse_args()
    if not a.report:
        print("sample:", json.dumps(sample_once(), ensure_ascii=False))
    trend_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
