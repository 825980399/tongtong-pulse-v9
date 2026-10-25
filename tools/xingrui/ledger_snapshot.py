# -*- coding: utf-8 -*-
"""ledger_snapshot.py · 星轨自用：登记册只读快照（台账分布/到期未裁/逾期/九列异常/指定票全字段）。

用途：替代每批从零手写的台账只读探针（复盘 3.1 工具零沉淀 · P0 工具 1）。
边界：**只读**，不写 CSV、不写基线、不改状态；口径与 tools/ci 三门禁保持一致。

用法：
    python tools/xingrui/ledger_snapshot.py --batch 365
    python tools/xingrui/ledger_snapshot.py --batch 365 --id L404
    python tools/xingrui/ledger_snapshot.py --batch 365 --json > snapshot.json

退出码：0 = 结构正常；1 = 结构/状态/P级/重复ID 异常；2 = 内部错误。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
LEDGER_PATH = os.path.join(REPO_ROOT, "docs", "台账", "待裁决登记册.csv")

EXPECTED_COLS = ["ID", "提出方", "提出批次", "摘要", "P级", "状态", "到期批次", "裁决批次", "裁决内容"]
VALID_STATES = ("待裁决", "已裁", "已排期", "已结案", "已撤销", "已裁决待排期")
VALID_P = ("P0", "P1", "P2", "P3")
SCHEDULED_STATES = ("已排期", "已裁决待排期")


def parse_batch(s: str):
    """口径对齐 tools/ci/check_pending_register.py:_parse_batch；支持数字与「第N批」。"""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    m = re.fullmatch(r"第?(\d+)批?", s)
    return int(m.group(1)) if m else None


def load_rows(path: str):
    raw = open(path, "rb").read()
    has_bom = raw[:3] == b"\xef\xbb\xbf"
    text = raw.decode("utf-8-sig")
    raw_rows = list(csv.reader(text.splitlines()))
    if not raw_rows:
        return has_bom, [], [], ["空文件"]
    header = raw_rows[0]
    data = [dict(zip(EXPECTED_COLS, r)) for r in raw_rows[1:] if any(c.strip() for c in r)]
    return has_bom, data, header, raw_rows


def snapshot(batch: int, path: str, want_id: str | None):
    has_bom, rows, header, raw_rows = load_rows(path)
    errors: list[str] = []

    # 表头
    if header != EXPECTED_COLS:
        errors.append("表头不符: %s" % "|".join(header))

    state = Counter(r["状态"] for r in rows)
    plevel = Counter(r["P级"] for r in rows)
    due_counter = Counter()
    for r in rows:
        d = parse_batch(r["到期批次"])
        if d is not None:
            due_counter[d] += 1

    # 结构/合法性异常（按原始行长度判列数，口径同 M1）
    for i, raw_r in enumerate(raw_rows[1:], start=2):
        if not any(c.strip() for c in raw_r):
            continue
        if len(raw_r) != len(EXPECTED_COLS):
            errors.append("行%d 列数=%d ≠9: %s" % (i, len(raw_r), raw_r[0] if raw_r else ""))
        r = dict(zip(EXPECTED_COLS, raw_r))
        if r["状态"] not in VALID_STATES:
            errors.append("行%d [%s] 状态非法 %r" % (i, r["ID"], r["状态"]))
        if r["P级"] not in VALID_P:
            errors.append("行%d [%s] P级非法 %r" % (i, r["ID"], r["P级"]))

    ids = [r["ID"] for r in rows]
    dup = [i for i, n in Counter(ids).items() if n > 1]
    if dup:
        errors.append("重复 ID: %s" % ", ".join(dup))

    # 本批到期未裁（状态=待裁决 且 到期批次≤batch），口径同 M2
    due_unresolved = [r for r in rows
                      if r["状态"] == "待裁决" and (parse_batch(r["到期批次"]) or 10**9) <= batch]
    # 逾期排期票：到期批次 < batch−1 仍未结案，口径同 M2
    overdue = [r for r in rows
               if r["状态"] in SCHEDULED_STATES and (parse_batch(r["到期批次"]) is not None)
               and parse_batch(r["到期批次"]) < batch - 1]

    out = {
        "batch": batch,
        "path": os.path.relpath(path, REPO_ROOT),
        "bom": has_bom,
        "rows": len(rows),
        "state_dist": dict(state),
        "p_dist": dict(plevel),
        "due_unresolved_count": len(due_unresolved),
        "due_unresolved": [{"ID": r["ID"], "到期": r["到期批次"], "摘要": r["摘要"][:40]} for r in due_unresolved],
        "overdue_count": len(overdue),
        "overdue": [{"ID": r["ID"], "状态": r["状态"], "到期": r["到期批次"], "摘要": r["摘要"][:40]} for r in overdue],
        "errors": errors,
    }
    if want_id:
        hit = [r for r in rows if r["ID"] == want_id]
        out["ticket"] = hit[0] if hit else None
    return out


def print_text(snap: dict):
    print("=== 登记册只读快照（batch=%d）===" % snap["batch"])
    print("路径: %s  BOM=%s  数据行=%d" % (snap["path"], snap["bom"], snap["rows"]))
    print("状态分布: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(snap["state_dist"].items())))
    print("P 级分布: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(snap["p_dist"].items())))
    print("本批(%d)到期未裁: %d 条" % (snap["batch"], snap["due_unresolved_count"]))
    for t in snap["due_unresolved"]:
        print("  %s [到期%s] %s" % (t["ID"], t["到期"], t["摘要"]))
    print("M2 排期逾期: %d 条" % snap["overdue_count"])
    for t in snap["overdue"]:
        print("  %s [%s 到期%s] %s" % (t["ID"], t["状态"], t["到期"], t["摘要"]))
    if "ticket" in snap:
        if snap["ticket"]:
            print("=== 指定票全字段 ===")
            for k, v in zip(EXPECTED_COLS, [snap["ticket"][c] for c in EXPECTED_COLS]):
                print("  %s: %s" % (k, v))
        else:
            print("指定票未找到")
    if snap["errors"]:
        print("=== 结构异常（%d）===" % len(snap["errors"]))
        for e in snap["errors"]:
            print("  " + e)
    else:
        print("结构: 正常（9 列 / 状态合法 / 无重复 ID）")


def main(argv=None):
    p = argparse.ArgumentParser(description="登记册只读快照（星轨自用，不写台账）")
    p.add_argument("--batch", type=int, required=True, help="当前施工批次号（M2 同口径）")
    p.add_argument("--csv", default=LEDGER_PATH, help="登记册 CSV 路径")
    p.add_argument("--id", default=None, help="打印指定票号全字段")
    p.add_argument("--json", action="store_true", help="输出 JSON 到 stdout")
    args = p.parse_args(argv)

    try:
        snap = snapshot(args.batch, args.csv, args.id)
    except Exception as e:  # 内部错误退出码 2
        sys.stderr.write("内部错误: %r\n" % e)
        return 2

    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        print_text(snap)
    return 1 if snap["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
