# -*- coding: utf-8 -*-
"""M1 交付-销账自动对账门禁（洞鉴 M1 / 171 治理刀 星轨侧）。

职责：
  1. 扫描交付报告目录 docs/路灯与星轨对话/交付报告/*.md 中的票号
     （T-xxx-1 / C-N / O-XN / QN-N / PN-N / Dxxx 形态），
  2. 比对登记册 docs/台账/待裁决登记册.csv 状态：
     - 交付报告提及的票 在登记册中「已结案」  -> 正常（已销账）
     - 交付报告提及的票 登记册状态为「已排期/已裁决待排期/待裁决」 -> 欠销账候选
       （已交付未结案），列入清单。
  3. 本版挂 CI 只告警（exit 0 且打印清单）——二期可转阻断。

用法：
  python check_delivery_settle.py [--csv PATH] [--report-dir DIR]

本脚本自身零静默 handler（满足 CI 门禁），导入时不产生副作用。
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

DEFAULT_CSV = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "docs", "台账", "待裁决登记册.csv")
)
DEFAULT_REPORT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "docs", "路灯与星轨对话", "交付报告")
)

#: 交付报告/提交票号形态（与 check_pending_register.TICKET_RE 对齐 + T-xxx-1 主形态）
#:   T-xxx-1     债务票主形态（T-红基线归零重锚-1，含中文票名）
#:   C-N / O-XN  烛微/洞鉴分类票
#:   QN-N / PN-N 裁决/优先票
#:   Dxxx        早期债务票
_TICKET_RE = re.compile(
    r"(?:T-[\w\u4e00-\u9fff-]+-\d+|C-\d+|O-[A-Z]\d+|Q\d{2,3}-\d+|P\d-\d+|D-[A-Z]\d+|D\d{2,4}(?:-[A-Za-z0-9]+)?)"
)

#: 报告文件名中出现的批次号（第N批）用于标注来源批次
_BATCH_RE = re.compile(r"第(\d+)批")


def _load_register(csv_path):
    """读取登记册，返回 {ID: 状态} 与 {ID: 行信息}。不可读时返回空。"""
    states, rows = {}, {}
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.reader(f):
                if not r or not r[0].strip():
                    continue
                rid = r[0].strip()
                if rid == "ID":
                    continue
                states[rid] = (r[5].strip() if len(r) > 5 else "")
                rows[rid] = r
    except OSError as _e:
        sys.stderr.write("[M1] 登记册不可读（跳过对账）: %s: %s\n"
                         % (type(_e).__name__, _e))
        return states, rows
    return states, rows


def _scan_report_dir(report_dir):
    """扫描交付报告目录，返回 {票号: 出现次数} 与 {票号: 来源文件列表}。"""
    hits, sources = {}, {}
    if not os.path.isdir(report_dir):
        sys.stderr.write("[M1] 交付报告目录不存在: %s\n" % report_dir)
        return hits, sources
    for fn in sorted(os.listdir(report_dir)):
        if not fn.lower().endswith(".md"):
            continue
        fp = os.path.join(report_dir, fn)
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError as _e:
            sys.stderr.write("[M1] 报告不可读（跳过）: %s\n" % fn)
            continue
        for tid in _TICKET_RE.findall(text):
            hits[tid] = hits.get(tid, 0) + 1
            sources.setdefault(tid, []).append(fn)
    return hits, sources


def _scan_commits(max_count=500):
    """扫描提交信息中的票号（提交即交付证据），返回 {票号: 提交列表}。

    仅采信「施工交付形态」提交：subject 含刀号标记（如 'C12｜'、'刀5'、
    '★170 C8a'、'B1（'）或交付/落地/收口/完成等完成态动词。
    排期/任务书/拍板/分析类提交（仅提及票名、无施工语义）不算交付证据，
    避免把「已排期提及」误报为欠销账。
    """
    hits, commits = {}, {}
    #: 完成态动词（subject 命中其一即视为交付语义）
    _DONE_WORDS = ("交付", "落地", "收口", "完成", "入库", "施工", "实现",
                   "接线", "修复", "重锚", "销账", "验收", "通电", "接入")
    #: 非交付语义动词（命中即排除：排期/采信/立票/拍板/任务书/分析类）
    _NON_DONE_WORDS = ("排期", "采信", "立票", "拍板", "任务书", "分析",
                       "登记册", "基线同步", "归位", "撤票", "转设计",
                       "落盘", "草案", "建议", "评估", "审查")
    #: 刀号标记（C\d+｜ / ★\d+C\d+ / 刀\d+ / B\d+（ 等）
    _KNIFE_RE = re.compile(r"(?:C\d+｜|★\d+ *C\d+[a-z]?|刀\d+|B\d+（|A\d+（)")
    try:
        r = subprocess.run(
            ["git", "log", "--max-count=%d" % max_count, "--pretty=%h %s"],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as _e:
        sys.stderr.write("[M1] git log 不可用（跳过提交扫描）: %s\n"
                         % type(_e).__name__)
        return hits, commits
    if r.returncode != 0:
        sys.stderr.write("[M1] git log 失败（跳过提交扫描）\n")
        return hits, commits
    for line in r.stdout.decode("utf-8", "replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([0-9a-f]{7,})\s+(.*)$", line)
        if not m:
            continue
        sha, subject = m.group(1), m.group(2)
        # 非交付语义的提交跳过（排期/拍板/任务书/分析/基线类）
        if any(w in subject for w in _NON_DONE_WORDS):
            continue
        if not (_KNIFE_RE.search(subject) or
                any(w in subject for w in _DONE_WORDS)):
            continue
        for tid in _TICKET_RE.findall(subject):
            hits[tid] = hits.get(tid, 0) + 1
            commits.setdefault(tid, []).append(sha)
    return hits, commits


def selftest():
    """核心判定自证：票号正则、登记册读取、状态比对。"""
    # 票号正则形态
    cases = {
        "T-红基线归零重锚-1": True,
        "T-进化费用单价补全-1": True,
        "C-5": True,
        "O-B1": True,
        "O-B6": True,
        "Q153-1": True,
        "P0-2": True,
        "D148-v2score": True,
        "第163批": False,   # 批次号不是票
        "T153-1": False,    # 任务号不是票
    }
    for s, expect in cases.items():
        got = bool(_TICKET_RE.search(s))
        assert got == expect, "票号 %s: 期望 %s 实得 %s" % (s, expect, got)
    # 登记册读取（临时 CSV）
    import tempfile
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".csv",
                                     encoding="utf-8-sig", delete=False)
    try:
        import csv as _csv
        w = _csv.writer(tf)
        w.writerow(["ID", "提出方", "提出批次", "摘要", "P级", "状态",
                    "到期批次", "裁决批次", "裁决内容"])
        w.writerow(["T-X-1", "a", "1", "s", "P1", "已结案", "1", "1", "c"])
        w.writerow(["T-Y-1", "a", "1", "s", "P1", "已排期", "171", "", ""])
        tf.close()
        states, _ = _load_register(tf.name)
        assert states.get("T-X-1") == "已结案", "T-X-1 应已结案"
        assert states.get("T-Y-1") == "已排期", "T-Y-1 应已排期"
    finally:
        os.unlink(tf.name)
    print("[selftest] M1 对账脚本自证通过")
    return 0


def main(argv=None):
    if "--selftest" in (argv if argv is not None else sys.argv):
        return selftest()
    p = argparse.ArgumentParser(description="M1 交付-销账自动对账（告警档）")
    p.add_argument("--csv", default=DEFAULT_CSV, help="登记册 CSV 路径")
    p.add_argument("--report-dir", default=DEFAULT_REPORT_DIR,
                   help="交付报告目录")
    p.add_argument("--max-commits", type=int, default=500,
                   help="提交信息扫描条数（0=关闭提交源）")
    p.add_argument("--strict", action="store_true",
                   help="二期档：欠销账非空即 exit 2（默认告警 exit 0）")
    args = p.parse_args(argv)

    states, _rows = _load_register(args.csv)
    hits, sources = _scan_report_dir(args.report_dir)
    commit_hits, commit_refs = {}, {}
    if args.max_commits > 0:
        commit_hits, commit_refs = _scan_commits(args.max_commits)
        # 合并两源：票号命中次数累加，来源文件列表并集（提交源用 sha 标注）
    all_hits = dict(hits)
    all_sources = dict(sources)
    for tid, cnt in commit_hits.items():
        all_hits[tid] = all_hits.get(tid, 0) + cnt
        refs = commit_refs.get(tid, [])
        if refs:
            all_sources.setdefault(tid, []).extend(
                "[commit %s]" % c for c in refs)
    if not all_hits:
        sys.stderr.write("[M1] 交付报告/提交信息未解析到任何票号（目录空或格式异常）\n")
        return 0

    # 欠销账候选 = 交付报告/提交出现过 且 登记册状态未结案（排除已撤销）
    unsettled = []
    settled = 0
    unknown = []
    for tid in sorted(all_hits):
        st = states.get(tid)
        if st == "已结案":
            settled += 1
        elif st == "已撤销":
            pass  # 已撤销不欠账
        elif st is None:
            unknown.append(tid)
        else:
            unsettled.append(tid)

    sys.stderr.write("[M1] 票号命中 %d 个（报告源+提交源）：已结案 %d / 已撤销 %d / "
                     "未入册 %d / 欠销账候选 %d\n"
                     % (len(all_hits), settled,
                        sum(1 for t in all_hits if states.get(t) == "已撤销"),
                        len(unknown), len(unsettled)))
    if unknown:
        sys.stderr.write("[M1] 提示：以下票号未在登记册入册"
                         "（可能为任务书刀号/历史命名，不阻断）：%s\n"
                         % "、".join(unknown[:15]))
    if unsettled:
        sys.stderr.write("[M1] ⚠ 欠销账候选（已交付、登记册未结案）：\n")
        for tid in unsettled:
            src = "、".join(all_sources[tid][:3])
            sys.stderr.write("    %s  <- %s\n" % (tid, src))
        if args.strict:
            sys.stderr.write("CHECK_FAIL: M1 二期档——欠销账 %d 个，阻断。\n"
                             % len(unsettled))
            return 2
        sys.stderr.write("[M1] 告警档：以上 %d 项列入星轨销账待办（不阻断）。\n"
                         % len(unsettled))
    else:
        sys.stderr.write("[M1] ✅ 无欠销账候选（交付票号均已结案/已撤销）。\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
