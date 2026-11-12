#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""493.20 刀2：两套账对账 CLI（可反复运行、幂等）。

用法（项目根下）：
    python tools/reconcile_ledgers.py              # 核对并修复
    python tools/reconcile_ledgers.py --check-only # 只核对不修复

交付后**重启曈曈加载新代码**后跑一次，即可验证两套账关联不再丢失；
若活体旧代码又覆盖了 verification_learning.json，随时重跑本脚本即自愈。
数据文件不进 git，仅改盘。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.mnemosyne import ledger_reconcile as lr  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="两套账对账")
    ap.add_argument("--check-only", action="store_true", help="只核对不修复")
    args = ap.parse_args()

    at_rows = lr.load_applied_rules()
    vl_path = lr._VL_REL
    vl_entries = []
    if os.path.isfile(vl_path):
        vl_entries = json.load(open(vl_path, encoding="utf-8")).get("entries", [])
    before = lr.check_alignment(at_rows, vl_entries)
    print("=== 对账前 ===")
    print(json.dumps(before, ensure_ascii=False, indent=2))

    if args.check_only:
        return 0 if not before["missing_rule_ids"] else 1

    result = lr.reconcile()
    print("=== 修复 ===")
    print("repaired:", result["repaired"])
    print("=== 对账后 ===")
    print(json.dumps(result["after"], ensure_ascii=False, indent=2))
    ok = (result["after"]["bidirectional"] == result["after"]["applied_rules"]
          and not result["after"]["missing_rule_ids"])
    print("结论:", "PASS 3/3 双向ID对齐" if ok else "FAIL 仍有未对齐")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
