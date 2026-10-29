#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
223批 刀2 · join判读口径固化（只读件）
=====================================
固化「配对频率判读」口径为可复用只读脚本，为乙档判读（224/225批）提供测量源。

方法学（烛微223前置分析 §二任务2 定稿，经 4 万行实测验证）：
    join = receive.pulse_id → emit 行(organ, event_type)
    频次表 count(emitter, event, receiver)

说明：
- 不依赖 222 批补键（from / matched_organs）。emit 行与 receive 行本就共享 pulse_id 主键，
  join 走 pulse_id 即可还原「谁发给谁」，222 补键仅便利化、非存在性前提。
- 只读：不写任何文件、不改任何状态。
- 频次按 (emitter, event, receiver) 三元组聚合；广播事件会出现 (emitter,event) 对应多个 receiver。

★第300批刀11（动态边定案）：**动态边 = pulse_pairs 日聚合，不新增埋点**——
本件即该口径的落地实现：读当日 ``data/monitor/pulse_pairs_YYYYMMDD.jsonl``
（pulse_tracer 按日分文件，207批刀3 + 212批刀1），按 pulse_id 做 emit→receive
join 聚合三元组频次 ⇒ 动态边由既有埋点派生，**零新增埋点、零行为变更**。
勿再另起一套动态边采集，与本件并存会造成两份动态边口径打架。

★★398 刀3（落空率换源）——**旧口径的假阳性来自哪里，已定案**
--------------------------------------------------------------------------
旧口径把"没有 ``pulse_id`` 的 receive 行"也算成落空。这类行结构上**无法 join**
（没有主键），本来就不该进落空分子/分母。

2026-10-15 数据实测（`pulse_pairs_20261015.jsonl`，155,168 条 receive）：

======================  ========  ============================================
读数                     值        说明
======================  ========  ============================================
``unmatched_receive``    34,357    旧口径的"未匹配"
其中无 ``pulse_id``       20,661    **60.1%** —— 结构上不可 join，不是落空
其中真落空               13,696    有 pid 但整份文件里没有对应 emit
``rate_legacy``           22.14%    34,357 / 155,168 ← **虚高**
``rate_true``              8.83%    13,696 / 155,168 ← 也无法 join 的剔除后
``rate_true_joinable``     9.24%    13,696 / (120,811 + 13,696) ← 分母只含可 join
======================  ========  ============================================

**更早的归因（"65.8% 是 emit 回填时序假阳性"）已被证伪**：
把单遍顺序扫改成"先全量建 emit index 再扫 receive"，未匹配数**一个都没变**
（34,357 = 34,357）⇒ 时序不是成因，**无 pulse_id 才是**。
记录在此，免得下一批再按旧归因去改一遍扫描顺序。

**与 `health_snapshot.json` 的对账**：该文件**没有** `matched`/`published` 字段
（2026-10-30 实测顶层 26 个键，`pulse` 段只有 `emitted/completed/timeout/pending`）。
真正同源的匹配读数在 ``nucleus/events/EventBus.py::m360_event_metrics()``
（`published` / `delivered` / `zero_match` / `zero_match_rate`），但那是 **EventBus
订阅侧**的口径，与 pulse_pairs 的 **tracer 侧**口径不是同一个事实，**不能互相校验**。
换个说法：本件算的是"tracer 记录的 emit→receive 配对"，EventBus 算的是
"publish→订阅命中"。两者都对，只是量的不是一件事 —— 别拿一个证另一个。

用法：
    python tools/count_match_frequency.py [--path data/monitor/pulse_pairs_20261015.jsonl] [--top 50] [--min 1]

输出：
    统计摘要 + 三口径落空率 + Top-N 频次表（count 降序），
    每行 = emitter | event_type | receiver | count。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def compute_frequency(path: str) -> dict:
    """读 pulse_pairs JSONL，按 pulse_id 做 emit→receive join，返回聚合结果。

    ★398 刀3：改成**两遍扫描**（先全量建 emit 索引，再扫 receive）。
    单遍顺序扫会依赖"emit 行必须排在 receive 行之前"这个隐含假设 ——
    一旦 tracer 的刷盘顺序变化，同一条脉冲就会被误判成落空。
    实测 2026-10-15 数据两遍与单遍结果**完全相同**（34,357 = 34,357），
    即当前行序恰好满足假设；改成两遍是把这个巧合变成保证，读数不变。

    返回 dict:
        emit_rows / receive_rows / matched / unmatched_receive (int)
        recv_no_pid (int): ★398 刀3 新增 —— 无 ``pulse_id`` 的 receive 行。
            **结构上无法 join，不计入落空**（旧口径把它算进 unmatched，是虚高的主因）。
        groups (int): distinct (emitter, event) 组数
        freq (Counter): (emitter, event, receiver) -> count
        emit_index (dict): pulse_id -> (emitter, event)  # 供刀3 复用
    """
    emit_index: dict[str, tuple[str, str]] = {}
    freq: Counter = Counter()
    emit_rows = receive_rows = matched = unmatched_receive = 0
    recv_no_pid = 0

    # ---- 第一遍：emit 全量建索引（内存只放 pid → (organ, event)）----
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") == "emit":
                emit_rows += 1
                # emit 行的 organ 字段即发射器官
                emit_index[row.get("pulse_id")] = (row.get("organ", ""),
                                                   row.get("event_type", ""))

    # ---- 第二遍：只扫 receive，做 join ----
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") != "receive":
                continue
            receive_rows += 1
            pid = row.get("pulse_id")
            if not pid:
                # ★398 刀3：没有主键 ⇒ 判不了配对，单独计。
                #   这类行进 unmatched 会让落空率虚高（实测占 unmatched 的 60.1%）。
                recv_no_pid += 1
            elif pid in emit_index:
                emitter, event = emit_index[pid]
                receiver = row.get("organ", "")
                freq[(emitter, event, receiver)] += 1
                matched += 1
            else:
                unmatched_receive += 1

    groups = len({(e, ev) for (e, ev, _r) in freq})
    return {
        "emit_rows": emit_rows,
        "receive_rows": receive_rows,
        "matched": matched,
        "unmatched_receive": unmatched_receive,
        "recv_no_pid": recv_no_pid,
        "groups": groups,
        "freq": freq,
        "emit_index": emit_index,
    }


def _pct(a: int, b: int) -> float | None:
    """安全百分比；分母为 0 时返回 ``None``（**不是 0**，铁律 155/276：没样本≠零）。"""
    return (round(100.0 * a / b, 2) if b else None)


def _fmt_pct(v: float | None) -> str:
    """百分比格式化；``None`` 打成 ``n/a``，不伪装成 ``0.00%``。"""
    return "n/a" if v is None else f"{v:.2f}%"


def drop_rates(result: dict) -> dict:
    """落空率三口径（★398 刀3 换源，施工只该看 ``rate_true``）。

    分母口径是这一整件事的关键：

    * ``rate_legacy`` = ``unmatched_receive / receive_rows``
      —— **虚高**：分母混进了无 ``pulse_id`` 的接收行，它们结构上无法 join。
    * ``rate_true`` = ``unmatched_receive / receive_rows`` 但先剔除无 pid
      —— 即 ``unmatched / (matched + unmatched + recv_no_pid)``？**不是**。
      真落空率的分母应当是"**能 join 的接收**"= ``matched + unmatched_receive``，
      故 ``rate_true_joinable = unmatched / (matched + unmatched)``。
    * ``rate_true`` = ``unmatched_receive / receive_rows``（含无 pid 的总接收）：
      这是"在全部接收里有多少是真的配不上"，**保守口径**，便于与其他读数并列。

    两个都给出，是因为它们回答不同问题：
      * 想判"配对机制本身坏没坏"⇒ 用 ``rate_true_joinable``（只问能 join 的那部分）；
      * 想判"整体接收里有多大比例是落空的"⇒ 用 ``rate_true``。

    返回：
        matched / joinable / recv_no_pid / unmatched /
        rate_legacy / rate_true / rate_true_joinable / no_pid_share
    """
    _matched = int(result.get("matched", 0))
    _unmatched = int(result.get("unmatched_receive", 0))
    _no_pid = int(result.get("recv_no_pid", 0))
    _recv = int(result.get("receive_rows", 0))
    _joinable = _matched + _unmatched
    _legacy_unmatched = _unmatched + _no_pid   # 当年这两类混在一个计数器里
    return {
        "matched": _matched,
        "unmatched": _unmatched,
        "unmatched_legacy": _legacy_unmatched,
        "recv_no_pid": _no_pid,
        "joinable": _joinable,
        # ★旧口径：分子含"无 pid"那部分 ⇒ 虚高。保留只为对账历史报告，勿当施工指标。
        "rate_legacy": _pct(_legacy_unmatched, _recv),
        "rate_true": _pct(_unmatched, _recv),
        "rate_true_joinable": _pct(_unmatched, _joinable),
        "no_pid_share": _pct(_no_pid, _recv),
    }


def real_receivers(result: dict, emitter: str, event: str) -> set[str]:
    """供刀3 复用：返回某 (emitter, event) 在 pair dump 中真实有接收的器官集合。"""
    return {r for (e, ev, r) in result["freq"] if e == emitter and ev == event}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="223批 刀2 · 配对频率判读（只读）")
    default_path = str(Path(__file__).resolve().parent.parent / "data" / "monitor" / "pulse_pairs_20261015.jsonl")
    parser.add_argument("--path", default=default_path, help="pulse_pairs JSONL 路径")
    parser.add_argument("--top", type=int, default=50, help="频次表输出前 N 行")
    parser.add_argument("--min", type=int, default=1, help="频次过滤下限")
    args = parser.parse_args(argv)

    if not Path(args.path).exists():
        print(f"[ERR] 文件不存在: {args.path}", file=sys.stderr)
        return 2

    res = compute_frequency(args.path)
    freq = res["freq"]
    rates = drop_rates(res)

    print("=" * 72)
    print(f"配对频率判读 · 数据源: {args.path}")
    print("=" * 72)
    print(f"emit 行总数      : {res['emit_rows']}")
    print(f"receive 行总数   : {res['receive_rows']}")
    print(f"join 命中        : {res['matched']}")
    print(f"有 pid 但无 emit : {rates['unmatched']}  ← 真落空")
    print(f"无 pulse_id      : {rates['recv_no_pid']}  ← 结构上无法 join，不计落空")
    print(f"distinct (emitter,event) 组数 : {res['groups']}")
    print(f"distinct (emitter,event,receiver) 三元组: {len(freq)}")
    print("-" * 72)
    print("落空率三口径（★398 刀3 换源；施工只看后两行）")
    print(f"  旧口径 (含无pid)/receive : {_fmt_pct(rates['rate_legacy'])}"
          f"   ← 虚高，仅用于对账历史报告")
    print(f"  真落空 /receive          : {_fmt_pct(rates['rate_true'])}")
    print(f"  真落空 /可join接收       : {_fmt_pct(rates['rate_true_joinable'])}"
          f"   ← 判配对机制坏没坏看这个")
    print(f"  无 pid 占 receive        : {_fmt_pct(rates['no_pid_share'])}")
    print("-" * 72)
    print(f"{'emitter':<16}{'event_type':<28}{'receiver':<16}{'count':>8}")
    print("-" * 72)

    shown = 0
    for (emitter, event, receiver), cnt in sorted(freq.items(), key=lambda kv: -kv[1]):
        if cnt < args.min:
            continue
        if shown >= args.top:
            print(f"... 省略剩余（共 {len(freq)} 三元组，按 --top/--min 调整）")
            break
        print(f"{emitter:<16}{event:<28}{receiver:<16}{cnt:>8}")
        shown += 1

    print("=" * 72)
    print("说明: 频次按 (emitter,event,receiver) 聚合；广播事件 (emitter,event) 对应多 receiver。")
    print("      join 走 pulse_id，不依赖 222 补键（from/matched_organs）。")
    print("      ★无 pulse_id 的 receive 行无法 join，已从落空分子剔除（398 刀3）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
