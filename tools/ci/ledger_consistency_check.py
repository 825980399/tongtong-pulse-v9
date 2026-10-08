#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""登记册一致性门禁（ledger-consistency-check）· 171 批微光侧门禁脚本（任务1 · 第三支）

判据：docs/台账/待裁决登记册.csv 的三项一致性——
  ① 行结构    每行字段数与表头一致（csv 引号解析后的真实列数，非 naive 逗号切分）
  ② 状态合法  状态 ∈ VALID_STATES（与 M2 同集合，避免两处口径漂移）
  ③ 状态分布  与基线 JSON（tools/ci/baselines/ledger_state_baseline.json）逐状态一致
另附：重复 ID 检出（同一票号出现两次）。

退出码：0 = PASS；1 = FAIL（结构/状态/分布任一不符）；2 = 内部错误。
默认**只读**；仅在显式 `--update-baseline` 时写基线 JSON（与 M2 的 --update-baseline 同名同义）。

与 M2（tools/ci/check_pending_register.py）互补不重复：
  M2 管「登记册总数 + ID 集合 + 到期逾期」；
  本件管「状态分布 + 行结构 + 状态合法性 + 重复 ID」——不查总数、不查 ID 集合、不查逾期。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):  # 管道/CI 下控制台可能为 cp936，符号字符会触发 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
LEDGER_PATH = os.path.join(REPO_ROOT, "docs", "台账", "待裁决登记册.csv")
BASELINE_PATH = os.path.join(SCRIPT_DIR, "baselines", "ledger_state_baseline.json")

#: 与 M2（check_pending_register.py:25）保持同一集合
VALID_STATES = ("待裁决", "已裁", "已排期", "已结案", "已撤销", "已裁决待排期")


def load_ledger(path=LEDGER_PATH):
    """→ (header, rows)；文件缺失 → (None, [])。使用 csv 模块（正确处理引号内逗号）。"""
    if not os.path.isfile(path):
        return None, []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return None, []
    header = [c.strip() for c in rows[0]]
    return header, [r for r in rows[1:] if any(c.strip() for c in r)]


def check_structure(header, rows):
    findings = []
    for col in ("ID", "状态"):
        if col not in header:
            findings.append({"kind": "missing_column", "detail": "表头缺列：%s" % col})
    if findings:
        return findings
    ncol, iid, ist = len(header), header.index("ID"), header.index("状态")
    seen = {}
    for n, row in enumerate(rows, 2):
        if len(row) != ncol:
            findings.append({"kind": "field_count", "row": n,
                             "detail": "字段数 %d ≠ 表头 %d（疑似引号/分隔符破损）" % (len(row), ncol)})
            continue
        st = row[ist].strip()
        if st not in VALID_STATES:
            findings.append({"kind": "invalid_state", "row": n,
                             "detail": "状态「%s」不在 VALID_STATES" % st})
        key = row[iid].strip()
        if key in seen:
            findings.append({"kind": "duplicate_id", "row": n,
                             "detail": "ID「%s」与第 %d 行重复" % (key, seen[key])})
        else:
            seen[key] = n
    return findings


def state_distribution(header, rows):
    """仅统计结构完好行（字段数 = 表头）的状态分布。"""
    if not header or "状态" not in header:
        return {}
    ncol, ist = len(header), header.index("状态")
    return dict(Counter(r[ist].strip() for r in rows if len(r) == ncol))


def compare_baseline(dist, baseline):
    """→ (ok, diffs:list[str])。"""
    if not baseline:
        return True, ["无基线（可用 --update-baseline 生成）"]
    base = baseline.get("states") or {}
    diffs = []
    for st in sorted(set(base) | set(dist)):
        if base.get(st, 0) != dist.get(st, 0):
            diffs.append("%s：基线 %s → 现值 %s" % (st, base.get(st, 0), dist.get(st, 0)))
    return (not diffs), diffs


def write_baseline(dist, rows, header, path=BASELINE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "generated_by": "tools/ci/ledger_consistency_check.py --update-baseline",
        "source": "docs/台账/待裁决登记册.csv",
        "total_rows": len(rows),
        "field_count": len(header or []),
        "states": dist,
    }
    with open(path, "w", encoding="utf-8", newline="") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return payload


def selftest():
    import tempfile
    _t = "T-" + "样例"
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "l.csv")
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write("ID,摘要,状态\n")
            fh.write("%s-1,普通行,已结案\n" % _t)
            fh.write('%s-2,"含,逗号的摘要",已排期\n' % _t)
        header, rows = load_ledger(p)
        assert header == ["ID", "摘要", "状态"], header
        assert len(rows) == 2 and len(rows[1]) == 3, rows
        assert check_structure(header, rows) == [], check_structure(header, rows)
        dist = state_distribution(header, rows)
        assert dist == {"已结案": 1, "已排期": 1}, dist
        ok, _ = compare_baseline(dist, {"states": dict(dist)})
        assert ok
        ok2, diffs = compare_baseline(dist, {"states": {"已结案": 0, "已排期": 2}})
        assert not ok2 and diffs, "反例：分布不同应 FAIL"
        bad = check_structure(header, rows + [["%s-3" % _t, "多一列", "已结案", "溢出"]])
        assert any(b["kind"] == "field_count" for b in bad), bad
        bad2 = check_structure(header, rows + [["%s-4" % _t, "非法状态", "已取消"]])
        assert any(b["kind"] == "invalid_state" for b in bad2), bad2
        bad3 = check_structure(header, rows + [["%s-1" % _t, "重复", "已结案"]])
        assert any(b["kind"] == "duplicate_id" for b in bad3), bad3
        h2, r2 = load_ledger(os.path.join(td, "缺失.csv"))
        assert h2 is None and r2 == []
    assert len(VALID_STATES) == 6 and "已裁决待排期" in VALID_STATES
    print("[selftest] ledger-consistency 自证通过（引号逗号 / 字段数 / 非法状态 / 重复 ID / 分布正反例）")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        selftest()
        return 0
    ap = argparse.ArgumentParser(description="登记册一致性门禁：行结构 / 状态合法性 / 状态分布")
    ap.add_argument("--ledger", default=LEDGER_PATH, help="登记册路径")
    ap.add_argument("--baseline", default=BASELINE_PATH, help="基线 JSON 路径")
    ap.add_argument("--update-baseline", action="store_true", help="显式刷新基线（写盘）")
    ap.add_argument("--warn-only", action="store_true", help="不一致仅告警，退出码仍为 0")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    header, rows = load_ledger(args.ledger)
    if header is None:
        print("[登记册一致性] ❌ 登记册不可用：%s" % args.ledger, file=sys.stderr)
        return 1
    struct = check_structure(header, rows)
    dist = state_distribution(header, rows)

    baseline = None
    if os.path.isfile(args.baseline):
        try:
            with open(args.baseline, "r", encoding="utf-8") as fh:
                baseline = json.load(fh)
        except (OSError, ValueError) as exc:
            print("[登记册一致性] ⚠️ 基线 JSON 不可解析：%s（%s）" % (args.baseline, exc), file=sys.stderr)
    ok_dist, diffs = compare_baseline(dist, baseline)

    if args.update_baseline:
        payload = write_baseline(dist, rows, header, args.baseline)
        print("[登记册一致性] 基线已刷新 → %s（有效行 %d，分布 %s）"
              % (payload["source"], len(rows), json.dumps(dist, ensure_ascii=False)))
        return 0

    failed = bool(struct) or not ok_dist
    if args.json:
        print(json.dumps({"rows": len(rows), "field_count": len(header), "states": dist,
                          "structure_findings": struct, "baseline_diffs": diffs,
                          "pass": not failed}, ensure_ascii=False, indent=2))
    else:
        print("[登记册一致性] 有效行 %d / 表头 %d 列" % (len(rows), len(header)))
        print("  状态分布：%s" % json.dumps(dist, ensure_ascii=False))
        for f in struct:
            print("  ❌ 结构/状态 %s：第 %s 行 —— %s"
                  % (f["kind"], f.get("row", "-"), f["detail"]))
        for d in diffs:
            if baseline:
                print("  ❌ 分布漂移 %s" % d)
            else:
                print("  ℹ️ %s" % d)
        if failed:
            print("[登记册一致性] %s：结构 %d 项 / 分布漂移 %d 项"
                  % ("WARN" if args.warn_only else "FAIL", len(struct), len(diffs) if baseline else 0))
        else:
            print("[登记册一致性] ✅ PASS：行结构一致、状态合法、分布与基线一致")
    return 1 if (failed and not args.warn_only) else 0


if __name__ == "__main__":
    sys.exit(main())
