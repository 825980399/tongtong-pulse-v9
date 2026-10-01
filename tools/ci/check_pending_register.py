# -*- coding: utf-8 -*-
"""待裁决登记册校验（烛微审计 S9 / 第148批 阶段二）。

职责：
  1. 校验 docs/台账/待裁决登记册.csv：
     - ID 唯一且非空
     - 状态 ∈ {待裁决, 已裁, 已排期, 已结案, 已撤销, 已裁决待排期}
     - 到期批次 非空且为整数；亦支持中文形态「第N批」（如 第157批）
  2. 输出「本批到期未裁项」= 状态=待裁决 且 到期批次 <= 本批；
     非空即阻断任务下发（exit 2）。

用法：
  python check_pending_register.py [--batch N] [--csv PATH]

本脚本自身零静默 handler（满足 CI 门禁），导入时不产生副作用。
"""
import argparse
import csv
import os
import re
import subprocess
import sys

VALID_STATES = {"待裁决", "已裁", "已排期", "已结案", "已撤销", "已裁决待排期"}
DEFAULT_BATCH = 148

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "docs", "台账", "待裁决登记册.csv")
)
#: 项目根（tools/ci → tools → 项目根，共两层）。
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
from nucleus._silent_except import silent_exc  # 静默异常统一走 CI 门禁认可通道


def fail(msg):
    sys.stderr.write("CHECK_FAIL: %s\n" % msg)
    sys.exit(2)


_BATCH_CN_RE = re.compile(r"^第(\d+)批$")


def _parse_batch(s):
    """非负整数批次解析；支持 '157' 与 '第157批' 中文形态。非法返回 None。

    避免引入 try/except（CI 门禁会记为新增静默 handler），改用纯正则/字符串判定。
    """
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    m = _BATCH_CN_RE.match(s)
    if m:
        return int(m.group(1))
    return None


# ============================================================================
# 新票同提交入册 硬校验（T153-7 / Q152-8 / Q153-8 双票合一）
# ============================================================================
#: 登记册 ID 语法（与 docs/台账/待裁决登记册.csv 实际 ID 形态一致）：
#:   Q\d+-\d+       裁决票（Q153-1）
#:   D-[A-Z]\d+     债务票（D-A1）
#:   D\d{2,4}(-x)?  债务票（D008 / D148-v2score / D153-6）
#:   P\d-\d+        优先票（P0-2 / P2-162）
#: 注：批次任务号 T153-1 等**不是票**，不匹配，避免误阻断正常提交。
TICKET_RE = re.compile(r"\b(Q\d{2,3}-\d+|D-[A-Z]\d+|D\d{2,4}(?:-[A-Za-z0-9]+)?|P\d-\d+)\b")

#: 已提交树（HEAD）已知票扫描用 POSIX-ERE 安全形态（避免 \d/\b 依赖 PCRE）。
_TICKET_ERE = r"(Q[0-9]{2,3}-[0-9]+|D-[A-Z][0-9]+|D[0-9]{2,4}(-[A-Za-z0-9]+)?|P[0-9]-[0-9]+)"
_SCAN_EXTS = {".py", ".md", ".txt", ".csv", ".json", ".jsonl", ".yaml",
              ".yml", ".rst", ".toml"}

#: 机器生成的「基线/快照件」目录：其中的内容不得当作票号来源。
#: 原因（第156批实测）：ruff 规则码（形如 D### / F### 的 pydocstyle·pyflakes 码）
#: 与债务票号语法 D\d{2,4} **撞型**，RUF100 三区基线件 tools/ci/baselines/ruff_ruf100_156.json
#: 里的规则码会被误判为「新票未入册」而阻断提交。
#: 该目录只放门禁基线数据，不是票的载体，故排除；对文档/代码的新票拦截不受影响。
_SKIP_TICKET_SCAN_PREFIXES = ("tools/ci/baselines/",)


def _register_ids(csv_path):
    """读取登记册 ID 列（去重集合）；不可读时返回空集合（不阻断既有校验）。"""
    ids = set()
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.reader(f):
                if r and r[0].strip():
                    ids.add(r[0].strip())
    except OSError as _e:
        silent_exc(_e, where="check_pending_register._register_ids", level="debug")
    return ids


def _known_tickets():
    """已知票集合 = 登记册 ID ∪ 已提交(HEAD)仓库内票 ∪ 工作树 docs/ 在途已决票。

    设计要点（避免误阻断在途批次）：
      - 登记册是权威台账，但本批「在途已决票」（如 Q153-1/D-A4）由星轨验收时合并入册，
        任务书明确「路灯本批不改登记册」；这些票已写在 docs/ 任务书中，视为已知。
      - 仅「全仓（已提交+工作树docs）从未出现」的票才触发入册强制。
      - 工作树 docs 扫描**不含**代码文件，故在代码里新造的票不会被 docs 扫描误判为已知
        （HEAD 提交树也搜不到未提交的代码新增）→ 正确强制入册。
    """
    known = _register_ids(DEFAULT_CSV)
    # 1) 已提交树（HEAD）文本文件中的票
    p = subprocess.run(
        ["git", "grep", "-E", "-o", _TICKET_ERE, "HEAD", "--",
         "*.py", "*.md", "*.csv", "*.txt", "*.json", "*.jsonl",
         "*.yaml", "*.yml", "*.rst", "*.toml"],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if p.returncode == 0:
        for line in p.stdout.decode("utf-8", "replace").split("\n"):
            line = line.strip()
            if line:
                known.add(line)
    # 2) 工作树 docs/（任务书/交付报告含在途已决票，未提交亦视为已知）
    docs_root = os.path.join(REPO_ROOT, "docs")
    if os.path.isdir(docs_root):
        for dp, dns, fns in os.walk(docs_root):
            dns[:] = [d for d in dns if d not in (".git", "__pycache__")]
            for fn in fns:
                if os.path.splitext(fn)[1].lower() not in _SCAN_EXTS:
                    continue
                try:
                    with open(os.path.join(dp, fn), "r",
                              encoding="utf-8", errors="ignore") as fh:
                        data = fh.read()
                except OSError as _e:
                    silent_exc(_e, where="check_pending_register._known_tickets", level="debug")
                    continue
                for tid in TICKET_RE.findall(data):
                    known.add(tid)
    return known


def check_new_ticket_registered():
    """新票必须同提交入册：若本次提交在非登记册文件中引入「全仓从未出现」的新票，
    则登记册 CSV 必须在本提交内同步增加行（入册），否则返回错误列表。
    仅在存在暂存差异时生效；无暂存差异（独立运行/验收）返回 []。"""
    known = _known_tickets()
    p = subprocess.run(
        ["git", "diff", "--cached"],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if p.returncode != 0 or not p.stdout.strip():
        return []  # 非 git 上下文或无暂存差异 → 放行
    text = p.stdout.decode("utf-8", errors="replace")
    register_rel = os.path.relpath(DEFAULT_CSV, REPO_ROOT).replace(os.sep, "/")
    cur_file = None
    introduced = set()
    register_added_rows = 0
    for line in text.split("\n"):
        if line.startswith("diff --git"):
            m = re.search(r" b/(.+)$", line)
            cur_file = m.group(1) if m else None
            continue
        if not line.startswith("+"):
            continue
        if line.startswith("+++"):
            continue
        if cur_file == register_rel:
            body = line[1:]
            # 登记册新增数据行（含逗号且非表头行）
            if "," in body and not body.startswith("ID,"):
                register_added_rows += 1
        elif cur_file:
            # 机器生成的基线件目录：规则码与票号撞型，不参与票号提取
            if cur_file.startswith(_SKIP_TICKET_SCAN_PREFIXES):
                continue
            for tid in TICKET_RE.findall(line):
                introduced.add(tid)
    new_tickets = introduced - known
    if not new_tickets:
        return []
    if register_added_rows > 0:
        return []  # 本提交已同步入册 → 通过
    return [
        "新票未同提交入册：提交引入新票 %s（全仓未出现过），但登记册行数未同步增加"
        "（须在本提交内一并入册，Q152-8/Q153-8 双票合一）。" % "、".join(sorted(new_tickets))
    ]


def main(argv=None):
    p = argparse.ArgumentParser(description="待裁决登记册校验")
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                   help="当前批次号（默认 %d）" % DEFAULT_BATCH)
    p.add_argument("--csv", default=DEFAULT_CSV, help="登记册 CSV 路径")
    args = p.parse_args(argv)

    if not os.path.isfile(args.csv):
        fail("登记册不存在: %s" % args.csv)

    errors = []
    with open(args.csv, "r", encoding="utf-8-sig", newline="") as f:
        raw = list(csv.reader(f))
    if not raw:
        fail("登记册为空（无表头）")
    header = [c.strip() for c in raw[0]]
    cols = header
    required = ["ID", "提出方", "提出批次", "摘要", "P级", "状态", "到期批次", "裁决批次", "裁决内容"]
    missing = [c for c in required if c not in cols]
    if missing:
        fail("列缺失: %s（实际=%s）" % (missing, cols))
    # ★Dxxx-14 结构校验：每行列数须与表头一致；禁止出现第2个表头行（重复表头）
    rows = []
    for i, cells in enumerate(raw[1:], start=2):
        if len(cells) != len(header):
            errors.append("行%d: 列数 %d ≠ 表头列数 %d（结构损坏，门禁阻断）" % (i, len(cells), len(header)))
        if [c.strip() for c in cells] == cols:
            errors.append("行%d: 出现第2个表头行（重复表头，门禁阻断）" % i)
        row = {cols[k]: (cells[k].strip() if k < len(cells) else "") for k in range(len(cols))}
        rows.append(row)
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

        # 到期批次：仅「待裁决」必须非空（已裁/已结案/已撤销 无待办到期日，留空表示 N/A，不臆造批次号）；
        # 「已裁决待排期」可带目标批次（支持 第N批 中文形态），仅做格式校验不强制为空。
        # 非空时须为非负整数或 第N批 中文形态；用 _parse_batch 校验，避免引入 except 被 CI 门禁记为静默 handler。
        due = (r.get("到期批次") or "").strip()
        if st == "待裁决":
            if not due:
                errors.append("行%d [%s]: 状态=待裁决 但 到期批次为空" % (i, rid))
            elif _parse_batch(due) is None:
                errors.append("行%d [%s]: 到期批次非整数 %r" % (i, rid, due))
        elif due and _parse_batch(due) is None:
            errors.append("行%d [%s]: 到期批次非整数 %r" % (i, rid, due))

        # 提出批次非空率必须 100%（T149-6 新增硬校验）
        proposed = (r.get("提出批次") or "").strip()
        if not proposed:
            errors.append("行%d [%s]: 提出批次为空（非空率须100%%）" % (i, rid))

    # --- T153-7：新票同提交入册 硬校验（仅存在暂存差异时生效）---
    for e in check_new_ticket_registered():
        errors.append(e)

    if errors:
        for e in errors:
            sys.stderr.write("CHECK_FAIL: %s\n" % e)
        sys.exit(2)

    # 本批到期未裁项
    due_unresolved = []
    for r in rows:
        if r["状态"].strip() == "待裁决":
            d = _parse_batch(r["到期批次"])
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
