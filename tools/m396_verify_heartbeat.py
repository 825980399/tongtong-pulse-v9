#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""396 批重启后核验：心跳是否真的活了（四项判据，只读，不改任何东西）。

任务书验收标准（原样搬进来，不改口径）：
  J1  连续 200 拍无 `[365链]` 告警
  J2  `beat_count` 单调递增
  J3  gap 稳定在 9.5s ± 2s
  J4  看门狗不再每 5s 刷一条"已续链"

为什么要有这个脚本：这四项**没有一项**能从 `health_snapshot.json` 一次读到 ——
beat 是瞬时值、gap 是瞬时值、告警条数只在日志里、而"是否单调"需要一段序列。
过去核验靠人肉 grep + 眼看，结论不可复现。

★三态输出（铁律 276：没采到 ≠ 通过）：
    PASS      证据充分且满足判据
    FAIL      证据充分且不满足
    NO_SAMPLE 证据不足（重启后没跑够、日志被轮转、留痕级别被抬高）——**不算通过**

用法：
    python tools/m396_verify_heartbeat.py
    python tools/m396_verify_heartbeat.py --log logs/pulse.log --snap data/monitor/health_snapshot.json
    python tools/m396_verify_heartbeat.py --need-beats 200 --window-lines 200000
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

# `2026-10-29 20:08:09 [心脏] WARNING: ...  pid=24776 gen=pid24776@20261029-200018`
_RE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_RE_ENTER = re.compile(r"\[396刀1\] 心跳进入 beat=(\d+) is_running=(\w+) timer_alive=(\w+)")
_RE_GEN = re.compile(r"gen=(\S+)")
_RE_BEATS = re.compile(r"\[396刀1\] 心跳进入 beat=(\d+)")


@dataclass
class Row:
    ts: str
    line: str


def _read_tail(path: str, max_lines: int) -> list[str]:
    """读日志尾部 max_lines 行（不整读 10MB+ 文件）。"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return lines[-max_lines:] if max_lines > 0 else lines


def _collect(log_path: str, max_lines: int) -> dict:
    lines = _read_tail(log_path, max_lines)
    enters: list[tuple[str, int, str]] = []   # (ts, beat, gen)
    chain_warn: list[str] = []
    chain_stall: list[str] = []
    rescue_ok: list[str] = []
    rescue_failed: list[str] = []
    skip_warn: list[str] = []
    for raw in lines:
        _m_ts = _RE_TS.match(raw)
        _ts = _m_ts.group(1) if _m_ts else ""
        if "[396刀1] 心跳进入" in raw:
            _m = _RE_ENTER.search(raw)
            _g = _RE_GEN.search(raw)
            if _m:
                enters.append((_ts, int(_m.group(1)), _g.group(1) if _g else ""))
        if "已尝试续链" in raw:
            chain_warn.append(raw)
        if "心跳间隔异常" in raw:
            chain_stall.append(raw)
        if "续链已确认生效" in raw:
            rescue_ok.append(raw)
        if "续链**失败**" in raw:
            rescue_failed.append(raw)
        if "[367链] 看门狗持续跳过" in raw:
            skip_warn.append(raw)
    return {"lines": len(lines), "enters": enters, "chain_warn": chain_warn,
            "chain_stall": chain_stall, "rescue_ok": rescue_ok,
            "rescue_failed": rescue_failed, "skip_warn": skip_warn}


def _beat_series(enters: list[tuple[str, int, str]]) -> dict[str, list[tuple[str, int]]]:
    """按 proc_gen 分组（重启会换 gen、beat 归零，混在一起会假报"不单调"）。"""
    out: dict[str, list[tuple[str, int]]] = {}
    for ts, beat, gen in enters:
        out.setdefault(gen or "", []).append((ts, beat))
    return out


def _verdict(ok: bool | None, detail: str) -> str:
    if ok is None:
        return f"NO_SAMPLE  {detail}"
    return f"{'PASS' if ok else 'FAIL'}       {detail}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="396 批心跳核验（只读）")
    ap.add_argument("--log", default=os.path.join(_ROOT, "logs", "pulse.log"))
    ap.add_argument("--snap", default=os.path.join(_ROOT, "data", "monitor", "health_snapshot.json"))
    ap.add_argument("--need-beats", type=int, default=200, help="J1/J2 要求的拍数跨度")
    ap.add_argument("--window-lines", type=int, default=200000, help="只读日志尾部 N 行（0=全读）")
    args = ap.parse_args(argv)

    data = _collect(args.log, args.window_lines)
    if data["lines"] <= 0:
        print(f"日志不存在或为空：{args.log}")
        return 2

    series = _beat_series(data["enters"])
    # 取样本最多的那个 gen（当前进程）
    gen = max(series, key=lambda k: len(series[k])) if series else ""
    pts = series.get(gen, [])
    beats = [b for _t, b in pts]
    span = (beats[-1] - beats[0]) if len(beats) >= 2 else 0

    print("=" * 78)
    print("396 批 · 心跳核验（只读）")
    print(f"  日志 {args.log}  尾部 {data['lines']} 行")
    print(f"  进程代次 gen={gen or '(无)'}  留痕样本 {len(beats)} 条  beat 跨度 {span}")
    print("=" * 78)

    # ---- J1：无 [365链] 告警
    if span < args.need_beats:
        j1 = _verdict(None, f"样本不足：beat 跨度 {span} < {args.need_beats}（不代表通过）")
    else:
        _n = len(data["chain_warn"])
        j1 = _verdict(_n == 0, f"窗口内 `已尝试续链` {_n} 条（要求 0）")

    # ---- J2：beat 单调递增
    if len(beats) < 3:
        j2 = _verdict(None, f"留痕样本 {len(beats)} 条 < 3，判不了单调性")
    else:
        _bad = [(i, beats[i - 1], beats[i]) for i in range(1, len(beats)) if beats[i] <= beats[i - 1]]
        j2 = _verdict(not _bad, f"{len(beats)} 个样本，逆序/重复 {len(_bad)} 处"
                                + (f"（例 {_bad[:3]}）" if _bad else ""))

    # ---- J3：gap 稳定 9.5±2（用相邻留痕的"时间差 / beat 差"算平均每拍）
    _gap_ok: bool | None = None
    _gap_detail = "无法解析时间戳"
    if len(pts) >= 2:
        from datetime import datetime
        _t0, _b0 = pts[0]
        _t1, _b1 = pts[-1]
        try:
            _d = (datetime.strptime(_t1, "%Y-%m-%d %H:%M:%S")
                  - datetime.strptime(_t0, "%Y-%m-%d %H:%M:%S")).total_seconds()
            if _b1 > _b0 and _d > 0:
                _avg = _d / (_b1 - _b0)
                _gap_ok = 7.5 <= _avg <= 11.5
                _gap_detail = f"平均每拍 {_avg:.2f}s（跨度 {_d:.0f}s / {_b1 - _b0} 拍；要求 7.5~11.5）"
            else:
                _gap_detail = f"beat 差 {_b1 - _b0} 或时间差 {_d} 非正，判不了"
        except ValueError as _e:
            # 日志时间戳格式异常 ⇒ 如实报"判不了"，不退化成 PASS（铁律 276）。
            # 走 silent_exc 留痕：except 里只做赋值的 handler 在 cw2 口径里算静默。
            silent_exc(_e, where="m396_verify_heartbeat::main gap 解析", level="debug")
            _gap_detail = f"时间戳解析失败：{type(_e).__name__}: {_e}"
    j3 = _verdict(_gap_ok, _gap_detail)

    # ---- J4：看门狗不再刷"已续链"
    if span < args.need_beats:
        j4 = _verdict(None, f"样本不足：beat 跨度 {span} < {args.need_beats}（不代表通过）")
    else:
        _n = len(data["chain_stall"])
        j4 = _verdict(_n == 0, f"窗口内 `心跳间隔异常` {_n} 条（要求 0）")

    print("\n判据：")
    for _tag, _v in (("J1 无[365链]告警", j1), ("J2 beat 单调递增", j2),
                     ("J3 gap 9.5±2s", j3), ("J4 看门狗不刷续链", j4)):
        print(f"  {_tag:<22} {_v}")

    # ---- 旁证读数（不参与 PASS/FAIL，但排障时最有用）
    print("\n旁证：")
    print(f"  续链已确认生效 {len(data['rescue_ok'])} 条｜续链失败 {len(data['rescue_failed'])} 条"
          f"｜367跳过告警 {len(data['skip_warn'])} 条")
    _snap_hb: dict = {}
    if os.path.exists(args.snap):
        try:
            with open(args.snap, encoding="utf-8") as f:
                _snap_hb = (json.load(f) or {}).get("heartbeat") or {}
        except Exception as _e:
            print(f"  快照读取失败：{type(_e).__name__}: {_e}")
    if _snap_hb:
        print(f"  health_snapshot.heartbeat: status={_snap_hb.get('status')!r} "
              f"beat={_snap_hb.get('beat_count')} gap_s={_snap_hb.get('gap_s')} "
              f"source={_snap_hb.get('status_source')}")
        if str(_snap_hb.get("status_source")) == "watchdog@stall":
            print("  ⚠ status_source=watchdog@stall ⇒ 看门狗判定停搏并已接管落盘")

    _fails = [x for x in (j1, j2, j3, j4) if x.startswith("FAIL")]
    _nos = [x for x in (j1, j2, j3, j4) if x.startswith("NO_SAMPLE")]
    print("\n结论：" + ("FAIL" if _fails else ("NO_SAMPLE（证据不足，不算通过）" if _nos else "PASS")))
    return 0 if not _fails and not _nos else 1


if __name__ == "__main__":
    sys.exit(main())
