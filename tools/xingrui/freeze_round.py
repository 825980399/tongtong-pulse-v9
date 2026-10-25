# -*- coding: utf-8 -*-
"""freeze_round.py · 星轨自用：排发轮公共段一体化收尾（备份 → 快照 → 门禁 → 体例 → 终检报告）。

用途：替代冻结/核验轮分散多轮的备份、门禁、体例、回读收尾探针（复盘 3.3 · P0 工具 5）。
边界：
  - 只固化**公共段**：改前备份、台账只读快照、三门禁只读复跑、docs 体例校验、终检报告落盘；
  - **不做**批次特异的台账行修改/状态推进（由星轨按批另写小脚本），**不提交 git**；
  - 不刷新基线、不传 --update-baseline；备份只复制原文件，不回写生产台账。

编排顺序：
  ① ledger_snapshot 改前快照（分布/到期未裁/逾期/结构异常）
  ② --backup 清单逐件备份到 docs/台账/备份/，记录 sha256 前 16、字节数、行数
  ③ gates_run 三门禁只读复跑
  ④ doc_style 校验 --docs 清单（BOM/行尾/表格列数/行数）
  ⑤ 汇总终检报告写入 --out

用法：
    python tools/xingrui/freeze_round.py --batch 366 \
        --backup docs/台账/待裁决登记册.csv tools/ci/baselines/ledger_state_baseline.json \
        --docs docs/完整进化路线与技术债务清单_v1.0.md --md-bom required --md-eol lf \
        --out docs/路灯与星轨对话/交付报告/证据件/366_freeze_round_check.txt
    python tools/xingrui/freeze_round.py --batch 366 --backup docs/a.md --docs docs/b.md --dry-run

退出码：0 = 公共段全绿；1 = 任一环节不符；2 = 内部错误。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
BACKUP_DIR = os.path.join(REPO_ROOT, "docs", "台账", "备份")
PY = sys.executable


def rel(p: str) -> str:
    return os.path.relpath(p, REPO_ROOT).replace("\\", "/")


def sha16(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def run(cmd: list[str]) -> dict:
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return {"rc": r.returncode, "output": ((r.stdout or "") + (r.stderr or "")).strip()}


def backup_one(src_rel: str, batch: int) -> dict:
    src = os.path.join(REPO_ROOT, src_rel)
    if not os.path.isfile(src):
        return {"file": src_rel, "error": "文件缺失"}
    raw = open(src, "rb").read()
    stem, ext = os.path.splitext(os.path.basename(src))
    stamp = datetime.now().strftime("%Y%m%d")
    name = "%s_%s_pre%d冻结轮%s" % (stem, stamp, batch, ext)
    dst = os.path.join(BACKUP_DIR, name)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(dst, "wb") as f:
        f.write(raw)
    rows = raw.decode("utf-8-sig", errors="replace").count("\n") + 1
    return {"file": src_rel, "backup": rel(dst), "bytes": len(raw), "rows": rows, "sha16": sha16(raw)}


def main(argv=None):
    p = argparse.ArgumentParser(description="排发轮公共段一体化收尾（星轨自用，不提交、不改台账内容）")
    p.add_argument("--batch", type=int, required=True)
    p.add_argument("--backup", nargs="*", default=[], help="改前备份件清单（相对仓库根）")
    p.add_argument("--docs", nargs="*", default=[], help="提交前体例校验件清单")
    p.add_argument("--md-bom", choices=["required", "absent", "any"], default="required")
    p.add_argument("--md-eol", choices=["lf", "crlf", "any"], default="lf")
    p.add_argument("--out", help="终检报告输出路径（相对仓库根）")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不写备份/不调门禁")
    args = p.parse_args(argv)

    report: list[str] = []
    report.append("=== 排发轮公共段收尾 · batch=%d · %s ===" % (args.batch, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    ok = True

    # ① 改前快照
    snap = run([PY, os.path.join(SCRIPT_DIR, "ledger_snapshot.py"), "--batch", str(args.batch)])
    report.append("\n[① 台账改前快照] rc=%d" % snap["rc"])
    report.append(snap["output"])
    ok = ok and snap["rc"] == 0

    # ② 备份
    report.append("\n[② 改前备份]")
    if args.dry_run:
        for f in args.backup:
            report.append("  (dry-run) 将备份: " + f)
    else:
        for f in args.backup:
            b = backup_one(f, args.batch)
            if "error" in b:
                report.append("  ✗ %s: %s" % (b["file"], b["error"]))
                ok = False
            else:
                report.append("  ✓ %s → %s（%dB / %d 行 / sha %s）" %
                              (b["file"], b["backup"], b["bytes"], b["rows"], b["sha16"]))

    # ③ 三门禁只读复跑
    gates = run([PY, os.path.join(SCRIPT_DIR, "gates_run.py"), "--batch", str(args.batch)])
    report.append("\n[③ 三门禁只读复跑] rc=%d" % gates["rc"])
    report.append(gates["output"])
    ok = ok and gates["rc"] == 0

    # ④ docs 体例校验
    if args.docs:
        style = run([PY, os.path.join(SCRIPT_DIR, "doc_style.py"),
                     "--bom", args.md_bom, "--eol", args.md_eol] +
                    [x for f in args.docs for x in ("--file", f)])
        report.append("\n[④ docs 体例校验] rc=%d" % style["rc"])
        report.append(style["output"])
        ok = ok and style["rc"] == 0

    report.append("\n=== 公共段汇总: %s ===" % ("全绿" if ok else "存在不符项，禁止进入提交步"))
    text = "\n".join(report)
    print(text)
    if args.out and not args.dry_run:
        out_path = os.path.join(REPO_ROOT, args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(b"\xef\xbb\xbf" + text.encode("utf-8"))
        print("终检报告: " + rel(out_path))

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
