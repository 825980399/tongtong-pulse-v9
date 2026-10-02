#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C-8 断链门禁（157 余票 #1）：死代码断链红线校验。

设计（据 broken_chain_baseline_157.json 的 red_line_rule）：
  - 调用 tools/dead_code_scan.scan_v3() 取当前断链指标；
  - 与 tools/ci/baselines/broken_chain_baseline_157.json 的红线指标比对：
      孤岛文件数(island_files) / 零引用入口方法数(entry_zero_ref) /
      零引用 set_* 数(set_zero_ref) 任一项较基线「上升」即 FAIL（机制断链增量>0）。
  - 三项均 <= 基线即为绿（本批引入机制断链增量=0）；每批交付报告须含此自证。

与 pre-commit 的关系：
  - 轻量 AST 扫描（不跑 pytest），可挂 per-commit hook（见 .git/hooks/pre-commit 1.7 段）。

★红线只计三项；method_zero_ref / module_zero_ref 为参考指标，不计入红线。

用法：
  python tools/ci/check_broken_chain_gate.py            # 默认比对（pre-commit 调用）
  python tools/ci/check_broken_chain_gate.py --selftest
"""
import os
import sys
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

BASELINE_PATH = os.path.join(SCRIPT_DIR, "baselines", "broken_chain_baseline_157.json")

# 红线指标（任一较基线上升即 FAIL）
RED_LINE_METRICS = ("island_files", "entry_zero_ref", "set_zero_ref")


def load_baseline(path):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d.get("baseline", {})


def current_metrics():
    import tools.dead_code_scan as dcs
    s = dcs.scan_v3()
    return {k: s.get(k) for k in RED_LINE_METRICS}


def check():
    if not os.path.isfile(BASELINE_PATH):
        print("[broken-chain] 基线缺失：%s" % BASELINE_PATH, file=sys.stderr)
        return 2
    base = load_baseline(BASELINE_PATH)
    cur = current_metrics()
    print("[broken-chain] 基线 island=%s entry=%s set=%s"
          % (base.get("island_files"), base.get("entry_zero_ref"), base.get("set_zero_ref")),
          file=sys.stderr)
    print("[broken-chain] 当前 island=%s entry=%s set=%s"
          % (cur["island_files"], cur["entry_zero_ref"], cur["set_zero_ref"]),
          file=sys.stderr)
    violations = []
    for m in RED_LINE_METRICS:
        b = base.get(m)
        c = cur.get(m)
        if b is None or c is None:
            continue
        if c > b:
            violations.append((m, b, c))
    if violations:
        print("[broken-chain] ❌ 断链红线上升（机制断链增量>0）：", file=sys.stderr)
        for m, b, c in violations:
            print("   ! %s: 基线 %s -> 当前 %s (+%s)" % (m, b, c, c - b), file=sys.stderr)
        print("[broken-chain] 结论：FAIL（本批引入机制断链，须先消红再提）", file=sys.stderr)
        return 1
    print("[broken-chain] ✅ PASS（三项红线均<=基线，本批引入机制断链增量=0）", file=sys.stderr)
    return 0


def selftest():
    cur = current_metrics()
    for k in RED_LINE_METRICS:
        assert isinstance(cur[k], int), cur
    print("[selftest] 当前红线指标=%s" % cur)
    return 0


def main(argv):
    if "--selftest" in argv:
        return selftest()
    return check()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
