#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate_health_check —— 曈曈 PulseNet 门禁健康巡检工具（216 批·刀1）。

八项指标巡检，每指标一行 `metric=value PASS|WARN|FAIL` + 末行汇总 + exit 0/1/2。
纯 stdout / 可选 --json / 不落盘（与 data/ 零写入纪律同构）。

设计边界（依据任务书 + 烛微 216 前置分析）：
- 只读五类源：logs/monitor/experiments/self_awareness/config。
- 不 import 生产单例写面（本工具不 import config、不实例化任何生产对象），
  阈值常量硬编码并支持 CLI 覆写，保持与生产线解耦。
- 所有 checker 为纯函数，接收已解析的数据结构，便于隔离单测（铁律 151）。

八项指标：
  M1  PAIR 覆盖率      —— pairs 当日 jsonl，精确 seq 式（混格式兼容，老行跳过），连续 seq 占比≥95% PASS
  M2  Pairs 尺寸/cap   —— 行数 vs 20000 行 / 体积 vs 16MB；>20000=FAIL（回归监测），首窗一次裁回 20000 属预期不判 FAIL
  M3  填充率差分      —— pulse.log 填充率行两行差分，输出附「待复核」旗
  M4  分布行密度      —— pulse.log 分布行 /10min >40 调 N（此处量化为密度，>40 WARN）
  M5  IO/silent_exc 留痕 —— pulse.log `[静默异常可见化]` 计数（pair/lfi 通道>0 WARN，RESONANCE 路径>0 FAIL）
  M6  心跳节奏偏移    —— 心跳 INFO 行间隔 vs 13.5s（>10% WARN）
  M7  dump 行字段完备 —— pairs 新行键完备 {seq,target_organ,amplitude}⊆keys
  M8  装配/启动对账   —— [装配计数] 行 vs 停止指令行 vs 启动序列行 三方对账（对不上=起停异常）
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime

logger = logging.getLogger("tools.gate_health_check")

# ---------------------------------------------------------------------------
# 路径与阈值常量（硬编码 + CLI 可覆写；不依赖生产单例）
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOG_DIR = os.path.join(_ROOT, "logs")
DEFAULT_MONITOR_DIR = os.path.join(_ROOT, "data", "monitor")
DEFAULT_EXPERIMENTS_DIR = os.path.join(_ROOT, "data", "experiments")
DEFAULT_SELF_AWARENESS_DIR = os.path.join(_ROOT, "data", "self_awareness")
DEFAULT_LOG_FILE = os.path.join(DEFAULT_LOG_DIR, "pulse.log")

PAIR_FILE_CAP = 20000                       # pulse_pairs 行数上限（pulse_tracer._PAIR_FILE_CAP）
PAIR_FILE_MAX_BYTES = 16 * 1024 * 1024      # 16MB
HEARTBEAT_NOMINAL_S = 13.5                  # 标称拍长（任务书明定）
HEARTBEAT_WARN_PCT = 0.10                   # 偏移 >10% WARN
DISTRIBUTION_DENSITY_WARN = 40              # 每 10min 分布行密度阈值
COVERAGE_PASS_RATIO = 0.95                  # 连续 seq 占比阈值
REQUIRED_PAIR_KEYS = ("seq", "target_organ", "amplitude")

# silent_exc 留痕通道关键字（where 字段子串匹配）
_PAIR_CHANNEL_KW = ("pulse_tracer", "pair")
_LFI_CHANNEL_KW = ("lif_shadow", "lfi")
_RESONANCE_CHANNEL_KW = ("resonance", "Resonance")

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


# ---------------------------------------------------------------------------
# 纯函数 checker（接收已解析数据，便于隔离测试）
# ---------------------------------------------------------------------------
def check_pair_coverage(rows):
    """M1：精确 seq 式连续率（混格式兼容：无 seq 老行跳过）。

    采用「区间密度」= 实际 seq 数 / (max-min+1)。完全连续文件=100%；
    中间任意缺失都会线性拉低密度，直接反映 seq 缺漏/断档。
    """
    seqs = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        s = r.get("seq", None)
        if s is None:
            continue  # 老格式行：无 seq，跳过
        try:
            seqs.append(int(s))
        except (TypeError, ValueError):
            logger.debug("seq 非整数，跳过: %r", s)
            continue
    total = len(seqs)
    if total == 0:
        return _result("M1_PAIR_COVERAGE", "n/a", WARN,
                       "无新格式行（含 seq），无法评估连续率；老行已跳过")
    uniq = set(seqs)
    lo, hi = min(uniq), max(uniq)
    span = hi - lo + 1
    density = total / span if span > 0 else 1.0
    pct = round(density * 100.0, 2)
    status = PASS if density >= COVERAGE_PASS_RATIO else FAIL
    return _result("M1_PAIR_COVERAGE", "{}%".format(pct), status,
                   "新格式行={} 跨度=[{},{}] 缺失={} 阈值≥{}%".format(
                       total, lo, hi, span - total, int(COVERAGE_PASS_RATIO * 100)))


def check_pair_size(row_count, file_bytes):
    """M2：尺寸/cap。>20000 行=FAIL；==20000=首窗收缩预期 WARN；体积>16MB=FAIL。"""
    notes = []
    if file_bytes > PAIR_FILE_MAX_BYTES:
        return _result("M2_PAIR_SIZE", "{}B".format(file_bytes), FAIL,
                       "文件体积 {}B > 16MB 上限".format(file_bytes))
    if row_count > PAIR_FILE_CAP:
        status = FAIL
        notes.append("行数 {} > {} 上限（疑似回归）".format(row_count, PAIR_FILE_CAP))
    elif row_count == PAIR_FILE_CAP:
        status = WARN
        notes.append("行数恰为 {}（首窗一次性裁回 20000 属预期，不判 FAIL）".format(PAIR_FILE_CAP))
    else:
        status = PASS
    return _result("M2_PAIR_SIZE", "{}rows/{}B".format(row_count, file_bytes), status,
                   "; ".join(notes) if notes else "行数≤上限 体积≤16MB")


def check_fillrate_diff(fillrate_lines):
    """M3：填充率两行差分，附「待复核」旗。

    fillrate_lines: 已解析的 list[(window, non_empty, ratio_pct)]，按时间升序。
    """
    if len(fillrate_lines) < 2:
        return _result("M3_FILLRATE_DIFF", "n/a", WARN,
                       "填充率行不足两行（实际 {}），无法差分；待复核".format(len(fillrate_lines)))
    a, b = fillrate_lines[-2], fillrate_lines[-1]
    diff = round(b[2] - a[2], 4)
    # 占比差分本身不直接判级；若存在占比下滑（<0）给 WARN 提示，否则 PASS，均附待复核旗
    status = WARN if diff < 0 else PASS
    return _result("M3_FILLRATE_DIFF", "{}pp".format(diff), status,
                   "末两行占比 {}%→{}%（窗口 {}→{}）；【待复核】".format(
                       a[2], b[2], a[0], b[0]))


def check_distribution_density(dist_lines):
    """M4：分布行密度（每 10min 桶计数）。

    dist_lines: list[(datetime, real, simplified)]，取近 10min 窗口计数 + 全局最大桶密度。
    """
    if not dist_lines:
        return _result("M4_DISTRIBUTION_DENSITY", "n/a", WARN,
                       "无映射分布行（RESONANCE 可能未开闸），密度无法评估")
    # 全局最大 10min 桶密度
    buckets = {}
    for dt, _real, _simp in dist_lines:
        key = (dt.year, dt.month, dt.day, dt.hour, dt.minute // 10)
        buckets[key] = buckets.get(key, 0) + 1
    max_density = max(buckets.values())
    # 近 10min 窗口（按最新时间戳回看）
    latest = max(dt for dt, _, _ in dist_lines)
    recent = sum(1 for dt, _, _ in dist_lines if 0 <= (latest - dt).total_seconds() <= 600)
    status = WARN if max_density > DISTRIBUTION_DENSITY_WARN else PASS
    return _result("M4_DISTRIBUTION_DENSITY", "max{}/10min".format(max_density), status,
                   "全局最大桶密度={} 近10min={} 阈值>{} WARN".format(
                       max_density, recent, DISTRIBUTION_DENSITY_WARN))


def check_io_silent_exc(silent_except_lines):
    """M5：IO/silent_exc 留痕计数。pair/lfi 通道>0 WARN，RESONANCE 路径>0 FAIL。"""
    pair_n = lfi_n = reso_n = other_n = 0
    for line in silent_except_lines:
        low = line.lower()
        if any(k.lower() in low for k in _RESONANCE_CHANNEL_KW):
            reso_n += 1
        elif any(k.lower() in low for k in _PAIR_CHANNEL_KW):
            pair_n += 1
        elif any(k.lower() in low for k in _LFI_CHANNEL_KW):
            lfi_n += 1
        else:
            other_n += 1
    total = pair_n + lfi_n + reso_n + other_n
    if reso_n > 0:
        status = FAIL
    elif pair_n > 0 or lfi_n > 0:
        status = WARN
    else:
        status = PASS
    return _result("M5_IO_SILENT_EXC", "total={}".format(total), status,
                   "pair={} lfi={} resonance={} other={}".format(pair_n, lfi_n, reso_n, other_n))


def check_heartbeat_offset(heartbeat_intervals):
    """M6：心跳间隔均值 vs 标称 13.5s，>10% 偏移 WARN。"""
    if not heartbeat_intervals:
        return _result("M6_HEARTBEAT_OFFSET", "n/a", WARN,
                       "无心跳节律行，无法评估节奏偏移")
    mean_v = sum(heartbeat_intervals) / len(heartbeat_intervals)
    dev = abs(mean_v - HEARTBEAT_NOMINAL_S) / HEARTBEAT_NOMINAL_S
    status = WARN if dev > HEARTBEAT_WARN_PCT else PASS
    return _result("M6_HEARTBEAT_OFFSET", "{}s".format(round(mean_v, 2)), status,
                   "标称={}s 样本={} 偏移={:.1%}".format(
                       HEARTBEAT_NOMINAL_S, len(heartbeat_intervals), dev))


def check_pair_field_completeness(rows):
    """M7：pairs 新行（含 seq）字段完备 {seq,target_organ,amplitude}⊆keys。"""
    new_rows = [r for r in rows if isinstance(r, dict) and r.get("seq") is not None]
    if not new_rows:
        return _result("M7_PAIR_FIELD_COMPLETE", "n/a", WARN,
                       "无新格式行，字段完备性无法评估")
    missing = 0
    for r in new_rows:
        for k in REQUIRED_PAIR_KEYS:
            if k not in r:
                missing += 1
                break
    status = PASS if missing == 0 else FAIL
    return _result("M7_PAIR_FIELD_COMPLETE", "{}/{}".format(len(new_rows) - missing, len(new_rows)),
                   status, "缺失三字段行数={}（seq/target_organ/amplitude）".format(missing))


def check_assembly_startup_reconcile(assembly_count, stop_count, start_count):
    """M8：装配/启动对账。三方计数对账，对不上=起停异常。"""
    # 起停异常判定：
    #  - 有停止指令/停止完成但无启动完成 → 停后未重启（异常）
    #  - 有装配计数但无启动完成 → 装配却无启动（异常）
    abnormal = False
    notes = []
    if stop_count > 0 and start_count == 0:
        abnormal = True
        notes.append("有停止({}) 但无启动完成({})".format(stop_count, start_count))
    if assembly_count > 0 and start_count == 0:
        abnormal = True
        notes.append("有装配计数({}) 但无启动完成({})".format(assembly_count, start_count))
    status = FAIL if abnormal else PASS
    return _result("M8_ASSEMBLY_STARTUP", "asm={}/stop={}/start={}".format(
        assembly_count, stop_count, start_count), status,
        "; ".join(notes) if notes else "装配/停止/启动三方计数对账一致")


def _result(metric, value, status, detail):
    return {"metric": metric, "value": value, "status": status, "detail": detail}


# ---------------------------------------------------------------------------
# 解析器（从原始源读取，与 checker 解耦）
# ---------------------------------------------------------------------------
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_FILL_RE = re.compile(r"填充率:\s*窗口=(\d+)\s*非空=(\d+)\s*占比=([\d.]+)%")
_DIST_RE = re.compile(r"映射分布:\s*累计=(\d+)\s*突触=(\d+)\s*real=(\d+)\s*simplified=(\d+)")
_HB_RE = re.compile(r"心跳节律:.*间隔=([\d.]+)s")
_SILENT_RE = re.compile(r"静默异常可见化")
_ASM_RE = re.compile(r"装配计数")
_STOP_RE = re.compile(r"停止指令已发出|器官并行停止完成")
_START_RE = re.compile(r"器官并行启动完成")


def parse_log_lines(lines):
    """将 pulse.log 原始行解析为各指标所需结构。"""
    fillrate = []           # (window, non_empty, ratio_pct)
    dist = []               # (datetime, real, simplified)
    hb_intervals = []       # float seconds
    silent = []             # raw lines
    asm = 0
    stop = 0
    start = 0
    for line in lines:
        m = _TS_RE.search(line)
        ts = None
        if m:
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                logger.debug("时间戳解析失败，跳过该行")
                ts = None
        if "填充率" in line:
            fm = _FILL_RE.search(line)
            if fm:
                fillrate.append((int(fm.group(1)), int(fm.group(2)), float(fm.group(3))))
        if "映射分布" in line:
            dm = _DIST_RE.search(line)
            if dm and ts is not None:
                dist.append((ts, int(dm.group(3)), int(dm.group(4))))
        hm = _HB_RE.search(line)
        if hm:
            try:
                hb_intervals.append(float(hm.group(1)))
            except ValueError:
                logger.debug("心跳间隔解析失败，跳过")
                pass
        if _SILENT_RE.search(line):
            silent.append(line)
        if _ASM_RE.search(line):
            asm += 1
        if _STOP_RE.search(line):
            stop += 1
        if _START_RE.search(line):
            start += 1
    return {
        "fillrate": fillrate,
        "dist": dist,
        "hb_intervals": hb_intervals,
        "silent": silent,
        "assembly": asm,
        "stop": stop,
        "start": start,
    }


def load_pair_rows(pairs_file):
    """读取 pairs jsonl，容错单行解析（坏行跳过并记录）。"""
    rows = []
    if not pairs_file or not os.path.exists(pairs_file):
        return rows, 0
    with open(pairs_file, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except (ValueError, TypeError):
                logger.debug("跳过无法解析的 pairs 行: %r", ln[:80])
                continue  # 坏行跳过，不计入统计
    return rows, len(rows)


def find_today_pairs_file(monitor_dir, date_str=None):
    """定位当日 pulse_pairs_YYYYMMDD.jsonl（支持 --date 覆写）。"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    return os.path.join(monitor_dir, "pulse_pairs_{}.jsonl".format(date_str))


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------
def run_checks(rows, row_count, file_bytes, log_data):
    """对一组已加载数据运行全部八项指标，返回结果列表。"""
    results = [
        check_pair_coverage(rows),
        check_pair_size(row_count, file_bytes),
        check_fillrate_diff(log_data["fillrate"]),
        check_distribution_density(log_data["dist"]),
        check_io_silent_exc(log_data["silent"]),
        check_heartbeat_offset(log_data["hb_intervals"]),
        check_pair_field_completeness(rows),
        check_assembly_startup_reconcile(
            log_data["assembly"], log_data["stop"], log_data["start"]),
    ]
    return results


def summarize(results):
    """汇总 + exit code（0=全过 / 1=含 WARN / 2=含 FAIL）。"""
    counts = {PASS: 0, WARN: 0, FAIL: 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    if counts[FAIL] > 0:
        exit_code = 2
    elif counts[WARN] > 0:
        exit_code = 1
    else:
        exit_code = 0
    return counts, exit_code


def _load_sources(args):
    rows, row_count = load_pair_rows(args.pairs_file)
    file_bytes = 0
    if args.pairs_file and os.path.exists(args.pairs_file):
        file_bytes = os.path.getsize(args.pairs_file)
    log_lines = []
    if os.path.exists(args.log_file):
        with open(args.log_file, "r", encoding="utf-8", errors="replace") as f:
            log_lines = f.readlines()
    log_data = parse_log_lines(log_lines)
    return rows, row_count, file_bytes, log_data


def main(argv=None):
    global PAIR_FILE_CAP, HEARTBEAT_NOMINAL_S
    parser = argparse.ArgumentParser(description="曈曈门禁健康巡检（八项指标）")
    parser.add_argument("--pairs-file", default=None,
                        help="pairs jsonl 路径（默认当日 data/monitor/pulse_pairs_YYYYMMDD.jsonl）")
    parser.add_argument("--date", default=None,
                        help="指定日期 YYYYMMDD（定位当日 pairs 文件）")
    parser.add_argument("--log-file", default=DEFAULT_LOG_FILE, help="pulse.log 路径")
    parser.add_argument("--monitor-dir", default=DEFAULT_MONITOR_DIR, help="monitor 目录")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（仍同时退出码）")
    parser.add_argument("--pair-cap", type=int, default=PAIR_FILE_CAP, help="PAIR 行数上限覆写")
    parser.add_argument("--heartbeat-nominal", type=float, default=HEARTBEAT_NOMINAL_S,
                        help="心跳标称间隔(s)覆写")
    args = parser.parse_args(argv)

    # 应用 CLI 覆写
    PAIR_FILE_CAP = args.pair_cap
    HEARTBEAT_NOMINAL_S = args.heartbeat_nominal

    pairs_file = args.pairs_file or find_today_pairs_file(args.monitor_dir, args.date)
    args.pairs_file = pairs_file

    rows, row_count, file_bytes, log_data = _load_sources(args)
    results = run_checks(rows, row_count, file_bytes, log_data)
    counts, exit_code = summarize(results)

    if args.json:
        payload = {
            "checks": results,
            "summary": {"total": len(results), "pass": counts[PASS],
                        "warn": counts[WARN], "fail": counts[FAIL]},
            "exit_code": exit_code,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print("{} = {} {}".format(r["metric"], r["value"], r["status"]))
            if r["detail"]:
                print("    · {}".format(r["detail"]))
        print("SUMMARY total={} pass={} warn={} fail={} exit={}".format(
            len(results), counts[PASS], counts[WARN], counts[FAIL], exit_code))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
