# -*- coding: utf-8 -*-
"""doc_style.py · 星轨自用：文档体例一键校验（BOM / 行尾 / 表格列数 / 行数 / 字节数）。

用途：替代每批分散手写的 csv_style/final_check 体例探针（复盘 3.3 · P0 工具 3）。
边界：只读校验，不回写文件；判据由调用方按文档类别显式传入，工具不擅自改默认口径。

体例参照（实测）：
  总账 / 规范 / 交付报告 md  → BOM required、纯 LF
  任务书正式书 / 草案 md     → BOM absent、纯 LF
  登记册 CSV                 → BOM required、CRLF（由 tools/ci 三门禁主责，本件仅辅助查看）

用法：
    python tools/xingrui/doc_style.py --file docs/完整进化路线与技术债务清单_v1.0.md --bom required --eol lf
    python tools/xingrui/doc_style.py --file docs/路灯与星轨对话/任务书/第365批_施工任务书_20261025.md \
        --bom absent --eol lf --expect-rows 254
    python tools/xingrui/doc_style.py --file a.md --file b.md --bom required --eol lf --json

退出码：0 = 全部符合；1 = 存在不符项；2 = 内部错误（文件缺失等）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))


def check_file(path: str, bom: str, eol: str, expect_rows: int | None) -> dict:
    result = {"file": os.path.relpath(path, REPO_ROOT), "errors": []}
    if not os.path.isfile(path):
        result["errors"].append("文件缺失")
        return result

    raw = open(path, "rb").read()
    result["bytes"] = len(raw)
    result["has_bom"] = raw[:3] == b"\xef\xbb\xbf"
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    result["crlf"] = crlf
    result["lf"] = lf

    if bom == "required" and not result["has_bom"]:
        result["errors"].append("缺 BOM")
    if bom == "absent" and result["has_bom"]:
        result["errors"].append("不应有 BOM")
    if eol == "lf" and crlf > 0:
        result["errors"].append("含 CRLF %d 处，要求纯 LF" % crlf)
    if eol == "crlf" and lf > 0:
        result["errors"].append("含裸 LF %d 处，要求纯 CRLF" % lf)

    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.split("\n")
    result["rows"] = len(lines)
    if expect_rows is not None and result["rows"] != expect_rows:
        result["errors"].append("行数 %d ≠ 期望 %d" % (result["rows"], expect_rows))

    # 表格块列数一致性
    blocks: list[list[str]] = []
    cur: list[str] = []
    for ln in lines:
        if ln.strip().startswith("|"):
            cur.append(ln)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    result["table_blocks"] = len(blocks)
    bad_blocks = []
    for idx, blk in enumerate(blocks, start=1):
        # 转义竖线 `\|` 是表格内文字，不计入列分隔符
        col_sets = {ln.replace("\\|", "").count("|") for ln in blk}
        if len(col_sets) != 1:
            bad_blocks.append((idx, sorted(col_sets)))
    if bad_blocks:
        result["errors"].append("表格块列数不一致: %s" %
                                "; ".join("块#%d 列符=%s" % (i, c) for i, c in bad_blocks))
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description="文档体例校验（星轨自用，只读）")
    p.add_argument("--file", action="append", required=True, help="待校验文件，可重复传入")
    p.add_argument("--bom", choices=["required", "absent", "any"], default="required")
    p.add_argument("--eol", choices=["lf", "crlf", "any"], default="lf")
    p.add_argument("--expect-rows", type=int, default=None, help="可选：期望行数")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args(argv)

    reports = []
    for f in args.file:
        path = f if os.path.isabs(f) else os.path.join(REPO_ROOT, f)
        reports.append(check_file(path, args.bom, args.eol, args.expect_rows))

    all_ok = all(not r["errors"] for r in reports)

    if args.json:
        print(json.dumps({"all_ok": all_ok, "files": reports}, ensure_ascii=False, indent=2))
    else:
        for r in reports:
            tag = "OK" if not r["errors"] else "FAIL"
            print("[%s] %s" % (tag, r["file"]))
            if "bytes" in r:
                print("    字节=%d BOM=%s CRLF=%d LF=%d 行数=%d 表格块=%d" % (
                    r["bytes"], r["has_bom"], r["crlf"], r["lf"], r["rows"], r["table_blocks"]))
            for e in r["errors"]:
                print("    ✗ " + e)
        print("=== 汇总: %s ===" % ("全部符合体例" if all_ok else "存在不符项，禁止提交"))

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
