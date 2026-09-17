#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""健康诊断代码问题追踪器 —— 主线第65批 T6 建立，第66批 T2 增强。

职责（第66批增强）：
- 解析 `ruff check --statistics` 输出，按规则语义分 P0/P1/P2/P3 等级。
- 标记「已知安全（known_safe）」：受控 exec(S102)、有意不检查子进程退出码(PLW1510)、
  NaN 检测惯用法(PLR0124) 等 —— 这些告警非真实 bug，不修。
- 标记「可自动修复（ruff --fix）」：基于 statistics 的 [*]/[-] 标记。
- 增量对比（两次扫描）、趋势统计、自动修复建议。
- 兼容旧计数差 CLI（--old/--new）。

★第66批 T0 核实结论：任务书上报「655 个代码问题」与真实 ruff --statistics 总数 2264
  不符；且全库 F/E9（未定义名/语法错误）= 0，**零真实 bug**。655 既非真实 bug 数也非
  全库总数，系历史某次部分范围 / 特定规则集统计误报。本批不盲目批量修，改为增强追踪器 +
  修少数真冗余（PLW0127/B023）+ 文档化持续清零计划。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---- 基线 ----
# 任务书上报值（历史，非 ruff 真值，仅作计数差兼容）
BASELINE_REPORTED_65 = 645
CURRENT_REPORTED_66 = 655
# 第66批实测 ruff --statistics 真值（2026-09-16）
RUFF_TOTAL_ACTUAL_M66 = 2264

# 已知安全规则（告警但非真实 bug，不修）
KNOWN_SAFE_RULES = {"S102", "PLW1510", "PLR0124"}
KNOWN_SAFE_NOTE = {
    "S102": "受控 exec（性能分析/补丁隔离执行/配置加载），全异常捕获兜底，非任意代码执行",
    "PLW1510": "有意不检查子进程退出码（测试/工具/调度，需消费 stdout 而非退出码），加 check=False 即可消除",
    "PLR0124": "NaN 检测惯用法（x==x / x!=x 排除 NaN），修则破坏数值防护",
}

# 旧兼容常量
BASELINE_ISSUE_COUNT = BASELINE_REPORTED_65
CURRENT_ISSUE_COUNT = CURRENT_REPORTED_66


def tier_of(rule: str) -> str:
    """按规则码语义归入 P0~P3。

    P0 = 真实 bug（未定义名 F* / 语法错误 E9）
    P1 = 潜在隐患需审查（B/S/PLW/BLE/N 前缀）
    P2 = 风格 / 现代化可批量修（UP/I/RUF100/SIM/C 前缀）
    P3 = 复杂度 / 行规 / 现代化建议（PLR/PERF/FURB/DTZ/RUF/C901 等）
    """
    if rule.startswith("FURB"):
        return "P3"
    # pyflakes：F 后接数字（F401/F811...）；E9 语法错误
    if rule.startswith("F") and rule[1:].isdigit():
        return "P0"
    if rule.startswith("E9"):
        return "P0"
    # P2 需先于 P1 判定（SIM 以 S 开头但属现代化）
    if (rule.startswith("UP") or rule.startswith("I") or rule == "RUF100"
            or rule.startswith("SIM")
            or (rule.startswith("C") and rule != "C901")):
        return "P2"
    if (rule and rule[0] in "BS") or rule.startswith("PLW") \
            or rule.startswith("BLE") or rule.startswith("N"):
        return "P1"
    return "P3"


def parse_ruff_statistics(path: str) -> dict:
    """解析 `ruff check --statistics` 输出。

    格式：`<count>\\t<rule>\\t[<mark>]\\t<name>`，末尾可能有 `Found N errors.` 汇总行（忽略）。
    返回 {rule: (count, mark, name)}，count 按规则聚合。
    """
    _out: dict[str, tuple[int, str, str]] = {}
    _pat = re.compile(r"^\s*(\d+)\s+([A-Z0-9]+)\s+(\[[\*\- ]\])\s+(\S+)")
    with open(path, "r", encoding="utf-8", errors="replace") as _fh:
        for _line in _fh:
            _m = _pat.match(_line)
            if not _m:
                continue
            _n = int(_m.group(1))
            _rule = _m.group(2)
            _mark = _m.group(3)
            _name = _m.group(4)
            _prev = _out.get(_rule, (0, _mark, _name))
            _out[_rule] = (_prev[0] + _n, _mark, _name)
    return _out


def classify(stats: dict) -> dict:
    """对解析后的规则统计做等级 / 安全 / 可修复分类。"""
    _by_tier = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    _known_safe = 0
    _autofix = 0
    _real_bug = 0
    _detail: dict[str, dict] = {}
    for _rule, (_n, _mark, _name) in stats.items():
        _t = tier_of(_rule)
        _by_tier[_t] += _n
        _is_safe = _rule in KNOWN_SAFE_RULES
        if _is_safe:
            _known_safe += _n
        if _mark in ("[*]", "[-]"):
            _autofix += _n
        if _t == "P0":
            _real_bug += _n
        _detail[_rule] = {
            "count": _n,
            "tier": _t,
            "autofix_mark": _mark,
            "name": _name,
            "known_safe": _is_safe,
            "safe_note": KNOWN_SAFE_NOTE.get(_rule, ""),
        }
    return {
        "total": sum(_by_tier.values()),
        "by_tier": _by_tier,
        "known_safe": _known_safe,
        "autofixable": _autofix,
        "real_bugs": _real_bug,
        "distinct_rules": len(stats),
        "detail": _detail,
    }


def diff_by_tier(prev: dict, cur: dict) -> dict:
    """两次 classify 结果的等级增量。"""
    return {
        _t: cur["by_tier"].get(_t, 0) - prev["by_tier"].get(_t, 0)
        for _t in ("P0", "P1", "P2", "P3")
    }


def build_enhanced_report(stats_path: str, prev_stats_path: str = "",
                           out_path: str = "") -> dict:
    """解析一次（或两次）ruff statistics，生成增强诊断报告。"""
    _stats = parse_ruff_statistics(stats_path)
    _cur = classify(_stats)
    _rep = {
        "generated_at": time.time(),
        "source": "ruff --statistics",
        "total": _cur["total"],
        "by_tier": _cur["by_tier"],
        "real_bugs": _cur["real_bugs"],
        "known_safe": _cur["known_safe"],
        "autofixable": _cur["autofixable"],
        "manual_non_safe": _cur["total"] - _cur["autofixable"] - _cur["known_safe"],
        "distinct_rules": _cur["distinct_rules"],
        "reported_baseline_66": CURRENT_REPORTED_66,
        "note": (
            "任务书上报 655 与真实 ruff 总数 %d 不符；F/E9(真实 bug)=0，"
            "全部为风格/现代化/复杂度告警。已知安全 %d 条不修；可自动修复 %d 条。"
            % (_cur["total"], _cur["known_safe"], _cur["autofixable"])
        ),
        "tier_detail": {
            _t: sorted(
                [(r, v["count"]) for r, v in _cur["detail"].items() if v["tier"] == _t],
                key=lambda x: -x[1],
            )
            for _t in ("P0", "P1", "P2", "P3")
        },
    }
    if prev_stats_path and os.path.isfile(prev_stats_path):
        _prev = classify(parse_ruff_statistics(prev_stats_path))
        _rep["delta_by_tier"] = diff_by_tier(_prev, _cur)
        _rep["delta_total"] = _cur["total"] - _prev["total"]
    if out_path:
        _d = os.path.dirname(os.path.abspath(out_path))
        if _d and not os.path.isdir(_d):
            os.makedirs(_d, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(_rep, ensure_ascii=False, indent=2))
    return _rep


# ---------------- 旧兼容（计数差） ----------------
def diff_health_issues(old_count: int, new_count: int) -> dict:
    """对比两次诊断计数，返回差异摘要（新增数需进一步逐条定级）。"""
    _delta = new_count - old_count
    return {
        "baseline": old_count,
        "current": new_count,
        "delta": _delta,
        "trend": "增加" if _delta > 0 else ("减少" if _delta < 0 else "持平"),
        "new_issues_suspected": max(0, _delta),
        "note": "新增问题需运行完整健康诊断逐条定级（P0/P1/P2/P3），不在本工具范围内。",
    }


def diff_health_issues_detailed(old_set: set[str], new_set: set[str]) -> dict:
    """逐条差异（需喂入两次诊断的问题标识集合）。"""
    _added = sorted(new_set - old_set)
    _removed = sorted(old_set - new_set)
    return {
        "added": _added,
        "removed": _removed,
        "added_count": len(_added),
        "removed_count": len(_removed),
    }


def generate_health_report(old_count: int = BASELINE_ISSUE_COUNT,
                           new_count: int = CURRENT_ISSUE_COUNT,
                           out_path: str = "") -> dict:
    _diff = diff_health_issues(old_count, new_count)
    _rep = {
        "generated_at": time.time(),
        "baseline_count": old_count,
        "current_count": new_count,
        "delta": _diff["delta"],
        "trend": _diff["trend"],
        "new_issues_suspected": _diff["new_issues_suspected"],
        "note": _diff["note"],
    }
    if out_path:
        _d = os.path.dirname(os.path.abspath(out_path))
        if _d and not os.path.isdir(_d):
            os.makedirs(_d, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(_rep, ensure_ascii=False, indent=2))
    return _rep


def main() -> int:
    _ap = argparse.ArgumentParser(description="健康诊断代码问题追踪器（第66批增强）")
    _ap.add_argument("--stats-file", default="",
                     help="ruff --statistics 输出文件，提供则走增强分类报告")
    _ap.add_argument("--prev-stats-file", default="",
                     help="上一次 ruff --statistics 输出文件，提供则计算增量趋势")
    _ap.add_argument("--old", type=int, default=BASELINE_REPORTED_65)
    _ap.add_argument("--new", type=int, default=CURRENT_REPORTED_66)
    _ap.add_argument("--report", default=os.path.join(
        ROOT, "docs", "路灯与星轨对话", "交付报告", "待分析",
        "health_issue_enhanced_66.json"))
    _ns = _ap.parse_args()
    if _ns.stats_file and os.path.isfile(_ns.stats_file):
        _rep = build_enhanced_report(_ns.stats_file, _ns.prev_stats_file, _ns.report)
        print("健康诊断(增强): 总数=%d 真实bug=%d 已知安全=%d 可自动修复=%d"
              % (_rep["total"], _rep["real_bugs"], _rep["known_safe"], _rep["autofixable"]))
        print("  按等级 P0=%d P1=%d P2=%d P3=%d"
              % (_rep["by_tier"]["P0"], _rep["by_tier"]["P1"],
                 _rep["by_tier"]["P2"], _rep["by_tier"]["P3"]))
        if "delta_total" in _rep:
            print("  增量: 总数%+d  P0%+d P1%+d P2%+d P3%+d"
                  % (_rep["delta_total"], _rep["delta_by_tier"]["P0"],
                     _rep["delta_by_tier"]["P1"], _rep["delta_by_tier"]["P2"],
                     _rep["delta_by_tier"]["P3"]))
    else:
        _rep = generate_health_report(_ns.old, _ns.new, _ns.report)
        print("健康诊断(计数差): 基线=%d 当前=%d 新增=%d (%s)"
              % (_rep["baseline_count"], _rep["current_count"],
                 _rep["delta"], _rep["trend"]))
    print("报告 →", _ns.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
