# -*- coding: utf-8 -*-
"""待裁决登记册校验（烛微审计 S9 / 第148批 阶段二）。

职责：
  1. 校验 docs/台账/待裁决登记册.csv：
     - ID 唯一且非空
     - 状态 ∈ {待裁决, 已裁, 已排期, 已结案}
     - 到期批次 非空且为整数
  2. 输出「本批到期未裁项」= 状态=待裁决 且 到期批次 <= 本批；
     非空即阻断任务下发（exit 2）。

用法：
  python check_pending_register.py [--batch N] [--csv PATH]

本脚本自身零静默 handler（满足 CI 门禁），导入时不产生副作用。
"""
import argparse
import csv
import os
import sys

VALID_STATES = {"待裁决", "已裁", "已排期", "已结案"}
DEFAULT_BATCH = 148

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "docs", "台账", "待裁决登记册.csv")
)


def fail(msg):
    sys.stderr.write("CHECK_FAIL: %s\n" % msg)
    sys.exit(2)


def _safe_int(s):
    """非负整数解析；非法返回 None。避免 except 被 CI 门禁记为静默 handler。"""
    s = (s or "").strip()
    return int(s) if s.isdigit() else None


def main(argv=None):
    p = argparse.ArgumentParser(description="待裁决登记册校验")
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                   help="当前批次号（默认 %d）" % DEFAULT_BATCH)
    p.add_argument("--csv", default=DEFAULT_CSV, help="登记册 CSV 路径")
    args = p.parse_args(argv)

    if not os.path.isfile(args.csv):
        fail("登记册不存在: %s" % args.csv)

    with open(args.csv, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        required = ["ID", "提出方", "提出批次", "摘要", "P级", "状态", "到期批次", "裁决批次", "裁决内容"]
        missing = [c for c in required if c not in cols]
        if missing:
            fail("列缺失: %s（实际=%s）" % (missing, cols))
        rows = list(reader)

    errors = []
    seen = {}
    for i, r in enumerate(rows, start=2):  # 第1行为表头
        rid = (r.get("ID") or "").strip()
        if not rid:
            errors.append("行%d: ID 为空" % i)
        elif rid in seen:
            errors.append("行%d: ID 重复 %s（首现行%d）" % (i, rid, seen[rid]))
        else:
            seen[rid] = i

        st = (r.get("状态") or "").strip()
        if st not in VALID_STATES:
            errors.append("行%d [%s]: 状态非法 %r（允许=%s）" % (i, rid, st, sorted(VALID_STATES)))

        # 到期批次：仅「待裁决」必须非空（已裁决项无待办到期日，留空表示 N/A，不臆造批次号）；
        # 非空时必须为非负整数。用 str.isdigit() 校验，避免引入 except 被 CI 门禁记为静默 handler。
        due = (r.get("到期批次") or "").strip()
        if st == "待裁决":
            if not due:
                errors.append("行%d [%s]: 状态=待裁决 但 到期批次为空" % (i, rid))
            elif not due.isdigit():
                errors.append("行%d [%s]: 到期批次非整数 %r" % (i, rid, due))
        elif due and not due.isdigit():
            errors.append("行%d [%s]: 到期批次非整数 %r" % (i, rid, due))

        # 提出批次非空率必须 100%（T149-6 新增硬校验）
        proposed = (r.get("提出批次") or "").strip()
        if not proposed:
            errors.append("行%d [%s]: 提出批次为空（非空率须100%%）" % (i, rid))

    if errors:
        for e in errors:
            sys.stderr.write("CHECK_FAIL: %s\n" % e)
        sys.exit(2)

    # 本批到期未裁项
    due_unresolved = []
    for r in rows:
        if r["状态"].strip() == "待裁决":
            d = _safe_int(r["到期批次"])
            if d is not None and d <= args.batch:
                due_unresolved.append(r["ID"].strip())

    total = len(rows)
    by_status = {}
    for r in rows:
        by_status[r["状态"].strip()] = by_status.get(r["状态"].strip(), 0) + 1

    sys.stderr.write("[OK] 登记册校验通过：共 %d 条；状态分布=%s\n" % (total, by_status))
    if due_unresolved:
        sys.stderr.write("本批（%d）到期未裁项 %d 条：%s\n" % (
            args.batch, len(due_unresolved), ", ".join(due_unresolved)))
        sys.stderr.write("CHECK_FAIL: 存在到期未裁项，须先裁后派（阻断任务下发）\n")
        sys.exit(2)
    else:
        sys.stderr.write("本批（%d）到期未裁项：0（无阻断）\n" % args.batch)
        sys.exit(0)


if __name__ == "__main__":
    main()
