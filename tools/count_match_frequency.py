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

用法：
    python tools/count_match_frequency.py [--path data/monitor/pulse_pairs_20261015.jsonl] [--top 50] [--min 1]

输出：
    统计摘要 + Top-N 频次表（count 降序），每行 = emitter | event_type | receiver | count。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def compute_frequency(path: str) -> dict:
    """读 pulse_pairs JSONL，按 pulse_id 做 emit→receive join，返回聚合结果。

    返回 dict:
        emit_rows / receive_rows / matched / unmatched_receive (int)
        groups (int): distinct (emitter, event) 组数
        freq (Counter): (emitter, event, receiver) -> count
        emit_index (dict): pulse_id -> (emitter, event)  # 供刀3 复用
    """
    emit_index: dict[str, tuple[str, str]] = {}
    freq: Counter = Counter()
    emit_rows = receive_rows = matched = unmatched_receive = 0

    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rtype = row.get("type")
            pid = row.get("pulse_id")
            if rtype == "emit":
                emit_rows += 1
                # emit 行的 organ 字段即发射器官
                emit_index[pid] = (row.get("organ", ""), row.get("event_type", ""))
            elif rtype == "receive":
                receive_rows += 1
                if pid in emit_index:
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
        "groups": groups,
        "freq": freq,
        "emit_index": emit_index,
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

    print("=" * 72)
    print(f"配对频率判读 · 数据源: {args.path}")
    print("=" * 72)
    print(f"emit 行总数      : {res['emit_rows']}")
    print(f"receive 行总数   : {res['receive_rows']}")
    print(f"join 命中        : {res['matched']}")
    print(f"join 未命中(孤儿接收无对应emit): {res['unmatched_receive']}")
    print(f"distinct (emitter,event) 组数 : {res['groups']}")
    print(f"distinct (emitter,event,receiver) 三元组: {len(freq)}")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
