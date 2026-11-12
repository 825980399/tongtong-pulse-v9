#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""两套账**只读核对** CLI（493.21 方案A；493.20 版为「对账并修复」，已停用修复）。

用法（项目根下）：
    python tools/reconcile_ledgers.py

★493.21 刀1：两本账（absorb_trace 记 QICA 吸收的规则 / verification_learning
记验证枢纽的学习事件）天然不同粒度、不同来源，**不再做强制双向 ID 对齐**。
vl 的 record() 全量 _save() 会覆盖外部注入条目，反复修反复丢。因此本脚本
只打印只读核对报告，不写任何文件；`verification_learning_id` /
`absorb_trace_id` 允许为 None。两本账各自独立工作、不互相覆盖。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.mnemosyne import ledger_reconcile as lr  # noqa: E402


def main():
    result = lr.reconcile()
    stat = result["before"]
    print("=== 两套账只读核对（493.21 方案A：不做强制对齐）===")
    print(json.dumps(stat, ensure_ascii=False, indent=2))
    print("policy:", result["policy"])
    print("note:", result["note"])
    print("说明: 双向=%d/%d 仅作观测；关联字段允许 None，两本账独立记录不互相覆盖"
          % (stat["bidirectional"], stat["applied_rules"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
