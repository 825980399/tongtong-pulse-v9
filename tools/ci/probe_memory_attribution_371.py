# -*- coding: utf-8 -*-
"""371 刀1 · N1 内存爬坡归属「只读取证」探针。

★定位（任务书 §二）：只出取证数据，**不先改代码**、不改阈值、不写 data/ 活体对象、
不新起定时器、不进生产心跳链、**不被生产代码 import**。

两种模式，口径必须分开（不许混写）：

  --pid N    **进程外读数**：psutil 读目标进程的 RSS / 句柄 / 线程数 / 打开文件 / CPU，
             并按线程名做归属粗分。→ 这一档是**曈曈活体的真实读数**。

  --self     **进程内分器**：在当前（=探针自己）进程里做 gc 类型聚合、线程枚举、
             大对象 Top 排名。→ ★这一档**不反映曈曈**（跨进程拿不到 gc 对象图），
             它的用途是：①自测；②将来若把 sample_inproc() 挂进活体命令面/心跳，
             直接复用同一函数，口径不二次开发。

归属结论口径：本工具只输出「分布 + 候选排序」，**不下归属定论**。
定论需要进程内注入采样长窗（本批不接，任务书 §二④ 边界）。

用法：
  python -X utf8 tools/ci/probe_memory_attribution_371.py --pid 25948 --out-json X.json
  python -X utf8 tools/ci/probe_memory_attribution_371.py --self --out-json X.json
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import threading
import time
from collections import Counter

import logging  # noqa: E402  —— 标准库，刻意不依赖任何生产模块（任务书 §二「零生产 import 依赖」）

# ★留痕口径：用标准库 logging 的 warning/debug，函数名命中 cw2 的 LOG_FUNCS，
#   既满足静默门「异常必须说出来」，又不引入 nucleus/* 的生产依赖。
_LOG = logging.getLogger("probe.memattr.371")

PROBE_VERSION = "m371.n1.memattr.v1"

# 与 organs/body/PulseLiver.py:48–53 同源（只读引用，不改它）
LIVER_SAMPLE_INTERVAL_SEC = 300.0
LIVER_RSS_WARN_BYTES = 6 * 1024 ** 3
LIVER_RSS_GROWTH_BYTES = 2 * 1024 ** 3
LIVER_HANDLES_WARN = 5000
LIVER_OPEN_FILES_WARN = 2000

_MB = 1024.0 * 1024.0


def _psutil():
    """延迟取 psutil（缺失时如实返回 None，不伪造读数）。"""
    try:
        import psutil  # noqa: PLC0415
        return psutil
    except ImportError as _e:
        _LOG.warning("psutil 不可用，进程外读数将如实置 None: %s: %s", type(_e).__name__, _e)
        return None


# ------------------------------------------------------------------ 进程外

def sample_outside(pid: int) -> dict:
    """进程外读数：RSS / 句柄 / 线程 / 打开文件 / 线程名分布。

    取不到的字段一律 `None` + `err` 说明，**不用 0 冒充**（铁律：不同事实不同措辞）。
    """
    ps = _psutil()
    out = {
        "mode": "outside",
        "pid": pid,
        "ts": time.time(),
        "rss_mb": None,
        "vms_mb": None,
        "num_handles": None,
        "num_threads": None,
        "num_open_files": None,
        "cpu_percent": None,
        "create_time": None,
        "thread_name_top": [],
        "access_ok": False,
        "err": None,
    }
    if ps is None:
        out["err"] = "psutil 不可用"
        return out
    try:
        p = ps.Process(pid)
        mi = p.memory_info()
        out["rss_mb"] = round(mi.rss / _MB, 1)
        out["vms_mb"] = round(mi.vms / _MB, 1)
        out["create_time"] = p.create_time()
        out["num_threads"] = p.num_threads()
        out["access_ok"] = True
        # 句柄：Windows 上叫 num_handles；*nix 无此字段
        _nh = getattr(p, "num_handles", None)
        out["num_handles"] = int(_nh()) if callable(_nh) else None
        # 打开文件
        _of = getattr(p, "num_fds", None)
        if callable(_of):
            out["num_open_files"] = int(_of())
        else:
            _nof = getattr(p, "num_handles", None)  # Windows 退化为句柄口径，标注见 note
            out["num_open_files"] = None
            out["note_open_files"] = "Windows 无 num_fds；open_files 需枚举句柄，本探针不枚举（成本高）"
        out["cpu_percent"] = p.cpu_percent(interval=0.0)
    except Exception as _e:  # psutil.Error / NoSuchProcess / AccessDenied
        _LOG.warning("进程外采样失败 pid=%s: %s: %s", pid, type(_e).__name__, _e)
        out["err"] = "%s: %s" % (type(_e).__name__, _e)
        return out

    # 线程名归属粗分（psutil.Process.threads() 只给 tid，名字要额外取；不可得则留空）
    out["thread_name_top"] = _thread_names_via_psutil(ps, pid)
    return out


def _thread_names_via_psutil(ps, pid: int) -> list:
    """按线程名聚合计数。psutil 跨进程拿不到线程名 ⇒ 如实返回空并附说明。

    Windows 需 NtQueryInformationThread，代价高且易触发杀软；本刀不做，
    线程名分布转由 `--self` 档（同进程）或日志 `pid=... ` 行获取。
    """
    return []


# ------------------------------------------------------------------ 进程内

def sample_inproc(top_n: int = 15) -> dict:
    """进程内分器（★仅反映**本进程**；跨进程拿不到 gc 对象图）。

    维度：GC 分代计数 / 对象类型 Top / 线程枚举 / 最大对象估算。
    """
    gc.collect()  # 规范化：先收一遍，让计数可复跑（只读，不改业务状态）
    counts = gc.get_count()
    stats = gc.get_stats()

    type_cnt: Counter = Counter()
    type_bytes: Counter = Counter()
    n_objs = 0
    for o in gc.get_objects():
        n_objs += 1
        t = type(o).__name__
        type_cnt[t] += 1
        try:
            type_bytes[t] += sys.getsizeof(o)
        except TypeError as _e:
            # 少数对象不支持 getsizeof —— 如实计入 0，不中断采样
            _LOG.debug("getsizeof 不支持该对象: %s", type(_e).__name__)

    threads = []
    for t in threading.enumerate():
        threads.append({"name": t.name, "daemon": bool(t.daemon), "alive": bool(t.is_alive())})

    return {
        "mode": "self",
        "pid": os.getpid(),
        "ts": time.time(),
        "note": "★本档为探针自身进程读数，不代表曈曈；活体归属需把 sample_inproc 注入目标进程",
        "gc_count": list(counts),
        "gc_stats": [
            {"collections": s.get("collections"), "collected": s.get("collected"),
             "uncollectable": s.get("uncollectable")}
            for s in stats
        ],
        "gc_tracked_objects": n_objs,
        "top_types_by_count": [
            {"type": k, "count": v, "est_bytes": type_bytes.get(k, 0)}
            for k, v in type_cnt.most_common(top_n)
        ],
        "top_types_by_bytes": [
            {"type": k, "est_bytes": v, "count": type_cnt.get(k, 0)}
            for k, v in type_bytes.most_common(top_n)
        ],
        "threads": threads,
        "thread_count": len(threads),
        "thread_name_top": Counter(x["name"] for x in threads).most_common(12),
    }


# ------------------------------------------------------------------ 归属候选

def rank_candidates(outside: dict, inproc: dict | None, liver_rows: list | None = None) -> list:
    """基于**可得证据**给出归属候选排序（不是定论）。

    判据只用可复算的量：RSS 绝对值 vs 肝阈值、线程数、句柄数、以及（若给了）
    肝资源采样行的增长斜率。每条候选都带 `evidence` 字段，便于反驳。
    """
    cands = []
    rss = outside.get("rss_mb")
    if isinstance(rss, (int, float)):
        cands.append({
            "candidate": "进程 RSS 总量（症状锚点）",
            "reading": "%.1f MB" % rss,
            "evidence": "psutil 进程外读数；肝阈值 RSS_WARN=%.1fMB、成长线=%.1fMB/300s"
                        % (LIVER_RSS_WARN_BYTES / _MB, LIVER_RSS_GROWTH_BYTES / _MB),
            "verdict_hint": "超阈值" if rss * _MB > LIVER_RSS_WARN_BYTES else "未超阈值",
        })
    nt = outside.get("num_threads")
    if isinstance(nt, int):
        cands.append({
            "candidate": "线程数（每线程栈+局部持有）",
            "reading": "%d" % nt,
            "evidence": "psutil num_threads；370 长窗 137→193（+56）",
            "verdict_hint": "需对照重启后基线",
        })
    nh = outside.get("num_handles")
    if isinstance(nh, int):
        cands.append({
            "candidate": "句柄数（泄漏常客）",
            "reading": "%d" % nh,
            "evidence": "psutil num_handles；肝阈值 %d；370 长窗 1835→2515(+680)" % LIVER_HANDLES_WARN,
            "verdict_hint": "未超阈值但增长快" if nh < LIVER_HANDLES_WARN else "超阈值",
        })
    if inproc:
        top = (inproc.get("top_types_by_bytes") or [])
        if top:
            t0 = top[0]
            cands.append({
                "candidate": "探针进程内最大对象类型（口径参考，非曈曈）",
                "reading": "%s ≈ %.1fMB" % (t0["type"], t0["est_bytes"] / _MB),
                "evidence": "gc.get_objects() + sys.getsizeof 递归单层（低估嵌套）",
                "verdict_hint": "仅口径参考",
            })
    if liver_rows:
        cands.append({
            "candidate": "肝 T-107a 资源采样增长（日志侧）",
            "reading": "%d 拍" % len(liver_rows),
            "evidence": "logs/pulse.log 过滤 gen 后的资源采样行",
            "verdict_hint": "见 growth 字段",
        })
    return cands


# ------------------------------------------------------------------ 日志/快照辅助

def read_liver_rows(log_path: str, gen: str = "", limit: int = 4000) -> list:
    """从 pulse.log 尾部抓肝资源采样行（只读，按 gen 过滤防混代次）。"""
    if not os.path.exists(log_path):
        return []
    rows = []
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-limit:]
    for ln in lines:
        if "[资源采样]" not in ln and "资源采样" not in ln:
            continue
        if gen and ("gen=" + gen) not in ln:
            continue
        rows.append(ln.rstrip("\n")[:300])
    return rows


def read_health_snapshot(path: str) -> dict:
    """读运行时健康快照（只读）。缺失/损坏如实返回空 dict + err。"""
    if not os.path.exists(path):
        return {"err": "快照不存在: %s" % path}
    try:
        import json as _json  # noqa: PLC0415
        with open(path, "r", encoding="utf-8") as f:
            d = _json.load(f)
        return d if isinstance(d, dict) else {"err": "快照非 dict"}
    except (OSError, ValueError) as _e:
        _LOG.warning("健康快照读取失败: %s: %s", type(_e).__name__, _e)
        return {"err": "%s: %s" % (type(_e).__name__, _e)}


# ------------------------------------------------------------------ CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="371 N1 内存归属只读取证探针")
    ap.add_argument("--pid", type=int, default=0, help="目标进程 pid（进程外读数）")
    ap.add_argument("--self", action="store_true", help="在探针自身进程做分器采样")
    ap.add_argument("--out-json", default="", help="机读产物路径")
    ap.add_argument("--log", default="logs/pulse.log", help="日志路径（肝资源采样行）")
    ap.add_argument("--gen", default="", help="只取该代次日志行（gen=... ）")
    ap.add_argument("--snapshot", default="data/monitor/health_snapshot.json")
    args = ap.parse_args(argv)

    if not args.pid and not args.self:
        ap.error("至少要给 --pid 或 --self 之一")

    outside = sample_outside(args.pid) if args.pid else {"mode": "outside", "skipped": "未给 --pid"}
    inproc = sample_inproc() if args.self else None
    liver_rows = read_liver_rows(args.log, args.gen)
    snap = read_health_snapshot(args.snapshot)

    payload = {
        "probe_version": PROBE_VERSION,
        "generated_ts": time.time(),
        "outside": outside,
        "inproc": inproc,
        "liver_rows_n": len(liver_rows),
        "liver_rows_head": liver_rows[:5],
        "health_snapshot": snap,
        "candidates": rank_candidates(outside, inproc, liver_rows),
        "boundary": [
            "本工具只读：不改阈值、不写 data/、不新起定时器、不被生产 import",
            "进程外档是曈曈真实读数；进程内档只反映探针自己",
            "归属定论须进程内注入长窗采样，本批不接",
        ],
    }

    body = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8", newline="\n") as f:
            f.write(body + "\n")
        print("[write] %s" % args.out_json)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
