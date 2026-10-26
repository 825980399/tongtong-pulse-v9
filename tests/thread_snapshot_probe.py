# -*- coding: utf-8 -*-
"""370 刀4 · 线程数三拍采集夹具（**测试/辅助面**，不绑生产代码、不连生产心跳）。

用途：为 342 E-7「同代次 ≥3 拍线程下降」验收提供**可复用**的采集夹具。
本代次跑改前代码，线程数结构上不可能下降 ⇒ 验收判据 **NOT_DUE 挂起**；
待同代次线程改造完成后，按本夹具实测三拍再对照基线判通过/未通过。

用法（测试内）：
    from tests.thread_snapshot_probe import collect
    rep = collect(pid=43864, ticks=3, interval_s=6.0, out_json=".../x.json")

★纪律：
- 只依赖 `psutil`，不 import 任何生产模块 ⇒ 不进生产 import 链。
- **不使用 try/except 吞异常**（铁律176）：采样失败就让它抛，避免把「没采到」
  伪装成「线程数为 0」——那会直接把一个 NOT_DUE 判据误读成「已下降」。
- 不修改既有线程验收阈值；不宣称线程已下降。
"""
import os
import time

__all__ = ["collect", "SAMPLE_FIELDS"]

#: 每拍落表字段（顺序即表头）
SAMPLE_FIELDS = ("tick", "pid", "threads", "rss_mb", "sampled_at")


def _now_hms():
    return time.strftime("%H:%M:%S", time.localtime())


def collect(pid=None, ticks=3, interval_s=6.0, out_json=None, note=""):
    """按固定间隔采 `ticks` 拍线程数，返回报告 dict（可落 JSON）。

    :param pid: 目标进程；None ⇒ 当前进程（供单测自采）
    :param ticks: 拍数，E-7 要求 ≥3
    :param interval_s: 快照间隔（秒）；342 基线口径为 6s
    :param out_json: 可选，落盘路径（父目录自动创建）
    :param note: 备注（如 NOT_DUE 理由）
    :return: {"proc_gen", "ticks", "interval_s", "rows", "note", "fields"}
    """
    import psutil  # 局部导入：本夹具只在测试面使用，import 失败应显式暴露

    if ticks < 3:
        raise ValueError("E-7 验收口径要求拍数 ≥3，实际 %d" % ticks)
    if interval_s <= 0:
        raise ValueError("快照间隔必须为正，实际 %r" % interval_s)

    _pid = int(pid) if pid else os.getpid()
    proc = psutil.Process(_pid)
    rows = []
    for i in range(1, ticks + 1):
        threads = proc.num_threads()
        rss_mb = round(proc.memory_info().rss / 1024.0 / 1024.0, 1)
        rows.append({"tick": i, "pid": _pid, "threads": threads,
                     "rss_mb": rss_mb, "sampled_at": _now_hms()})
        if i < ticks:
            time.sleep(interval_s)

    rep = {
        "proc_gen": "pid%d@%s" % (_pid, time.strftime("%Y%m%d-%H%M%S")),
        "pid": _pid,
        "ticks": ticks,
        "interval_s": interval_s,
        "fields": list(SAMPLE_FIELDS),
        "rows": rows,
        "thread_values": [r["threads"] for r in rows],
        "note": note,
    }
    if out_json:
        import json
        os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
    return rep
