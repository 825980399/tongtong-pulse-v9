#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""观测标记门禁（observe-marker-gate）· 171 批微光侧门禁脚本（任务1 · 第二支）

判据：**观测类交付**（快照 / 指纹 / 采样 / 活体观测 / 只读扫描等）必须带三项观测窗标记，
      缺任一即 FAIL：
  ① 观测窗起点  同一行同时出现「窗」类词（观测窗 / 时间窗 / 窗口 / 采样窗 / 观测期 /
                读数区间 / 观测区间）与日期或时刻（YYYY-MM-DD 或 HH:MM[:SS]）
  ② HEAD        出现 7-40 位十六进制提交号，且带 HEAD / commit / 提交 / SHA / 基线 等上下文
  ③ 工具调用数  「工具调用」后跟数字（兼容「工具调用 9 次」「工具调用数：9」等写法）

「观测类」判定：文件名或正文命中 观测 / 快照 / 指纹 / 采样 / 活体 / 读数 / 只读扫描。
`--all`：对每个目标文件强制要求三项（严格模式，用于门禁链全量扫描）。

豁免：正文含 `<!-- observe-gate: off -->` 者跳过，并**逐条打印为「豁免」**（不静默跳过）。

退出码：0 = PASS；1 = FAIL（有缺失标记）；2 = 内部错误。只读：本脚本不写任何文件。

与其余门禁的关系：本件只管「观测类交付的观测窗三要素是否齐全」，不评价观测内容质量；
数据口径类校验见 tools/ci/check_pending_register.py（M2）与 check_pii_final_scan.py。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):  # 管道/CI 下控制台可能为 cp936，符号字符会触发 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DEFAULT_SCAN_DIR = os.path.join(REPO_ROOT, "docs", "微光")

_SKIP_PARTS = ("归档", "archive", ".aionclaw-tmp", "tmp", "__pycache__")
_EXEMPT_MARK = "<!-- observe-gate: off -->"
_OBSERVE_HINT = re.compile(r"观测|快照|指纹|采样|活体|读数|只读扫描")
_WINDOW_WORD = re.compile(r"观测窗|时间窗|窗口|采样窗|观测期|读数区间|观测区间|观测时段")
_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_TIME = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")
_HEAD_CTX = re.compile(
    r"(?:HEAD|head|commit|Commit|提交|SHA|sha|基线)\s*[:：]?\s*`?([0-9a-fA-F]{7,40})`?"
)
_CALLS = re.compile(r"工具调用\s*(?:数|次数)?\s*[:：=]?\s*\**\s*(\d{1,4})")

MARKERS = ("观测窗起点", "HEAD", "工具调用数")


def _rel_path(fp):
    """跨盘符安全：外盘/临时目录文件 os.path.relpath 会抛 ValueError。"""
    try:
        return os.path.relpath(fp, REPO_ROOT).replace("\\", "/")
    except ValueError as exc:
        print("[观测标记门禁] relpath 跨盘符回退：%s" % exc, file=sys.stderr)
        return fp.replace("\\", "/")


def _has_window(text):
    """观测窗起点：某一行同时出现「窗」类词与日期/时刻。"""
    for line in text.splitlines():
        if _WINDOW_WORD.search(line) and (_DATE.search(line) or _TIME.search(line)):
            return True
    return False


def _has_head(text):
    """HEAD 标记：带上下文词（HEAD/commit/提交/SHA/基线…）的 7-40 位十六进制串。
    此处**不**排除纯数字串——真实提交号可能全为数字（如 0306626）；上下文词已是足够强的判据，
    而纯日期（2026-10-08）因含短横不匹配本正则。"""
    return _HEAD_CTX.search(text) is not None


def _has_calls(text):
    return bool(_CALLS.search(text))


def is_observe_doc(path, text, force_all=False):
    if force_all:
        return True
    if _OBSERVE_HINT.search(os.path.basename(path)):
        return True
    head = "\n".join(text.splitlines()[:40])
    return bool(_OBSERVE_HINT.search(head))


def check_text(path, text, force_all=False):
    """→ dict（豁免 / 观测类 / 缺失标记 / 命中标记）。"""
    if _EXEMPT_MARK in text:
        return {"file": path, "exempt": True, "observe": False, "missing": [], "present": []}
    observe = is_observe_doc(path, text, force_all=force_all)
    if not observe:
        return {"file": path, "exempt": False, "observe": False, "missing": [], "present": []}
    present, missing = [], []
    for name, fn in (("观测窗起点", _has_window), ("HEAD", _has_head), ("工具调用数", _has_calls)):
        (present if fn(text) else missing).append(name)
    return {"file": path, "exempt": False, "observe": True, "missing": missing, "present": present}


def iter_targets(paths, all_docs):
    if paths:
        seeds = [p if os.path.isabs(p) else os.path.join(REPO_ROOT, p) for p in paths]
    elif all_docs:
        seeds = [os.path.join(REPO_ROOT, "docs")]
    else:
        seeds = [DEFAULT_SCAN_DIR]
    for seed in seeds:
        if os.path.isfile(seed):
            yield seed
        elif os.path.isdir(seed):
            for root, dirs, files in os.walk(seed):
                dirs[:] = [d for d in dirs if d not in _SKIP_PARTS]
                if any(part in root for part in _SKIP_PARTS):
                    continue
                for name in sorted(files):
                    if name.endswith((".md", ".txt")):
                        yield os.path.join(root, name)


def selftest():
    _head = "HEAD " + "0306626"
    _t = "T-" + "样例"
    good = (
        "# 观测件\n观测窗：2026-10-08 17:00:00 起，取数 %s；工具调用 9 次。\n" % _head
    )
    r = check_text("观测_样例.md", good)
    assert r["observe"] and not r["missing"], r
    bad1 = "# 观测件\n观测窗：2026-10-08 起。\n" + _head + "\n"
    r1 = check_text("观测_样例.md", bad1)
    assert r1["missing"] == ["工具调用数"], r1
    bad2 = "# 观测件\n观测窗：2026-10-08 起；工具调用 3 次。\n"
    r2 = check_text("观测_样例.md", bad2)
    assert r2["missing"] == ["HEAD"], r2
    bad3 = "# 观测件\n" + _head + "；工具调用 3 次。\n"
    r3 = check_text("观测_样例.md", bad3)
    assert r3["missing"] == ["观测窗起点"], r3
    plain = "# 普通交付报告\n本批完成若干项。\n"
    r4 = check_text("普通报告.md", plain)
    assert not r4["observe"] and not r4["missing"], r4
    r5 = check_text("普通报告.md", plain, force_all=True)
    assert r5["observe"] and len(r5["missing"]) == 3, r5
    r6 = check_text("观测_样例.md", good + "\n" + _EXEMPT_MARK + "\n")
    assert r6["exempt"], r6
    assert not _has_head("见 " + "20261008" + " 号记录"), "纯数字不得判为 HEAD"
    assert not _CALLS.search("工具调用次数见附表"), "无数值不得判为已填"
    print("[selftest] observe-marker-gate 自证通过（正例 1 + 反例 3 + 豁免 + 非观测类 2）")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        selftest()
        return 0
    ap = argparse.ArgumentParser(description="观测标记门禁：观测类交付必须带观测窗三要素")
    ap.add_argument("--paths", nargs="*", default=None, help="待扫描文件或目录（默认 docs/微光）")
    ap.add_argument("--all-docs", action="store_true", help="扫描整个 docs/")
    ap.add_argument("--all", action="store_true", help="严格模式：所有目标文件均须带三要素")
    ap.add_argument("--warn-only", action="store_true", help="缺失仅告警，退出码仍为 0")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    files = list(iter_targets(args.paths, args.all_docs))
    results = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace", newline="") as fh:
                text = fh.read()
        except OSError as exc:
            print("[观测标记门禁] 读取失败：%s（%s）" % (fp, exc), file=sys.stderr)
            continue
        item = check_text(_rel_path(fp), text, args.all)
        results.append(item)

    observed = [r for r in results if r["observe"]]
    exempted = [r for r in results if r["exempt"]]
    failed = [r for r in observed if r["missing"]]

    if args.json:
        print(json.dumps({"files": len(results), "observe": len(observed), "exempt": len(exempted),
                          "failed": failed, "results": results}, ensure_ascii=False, indent=2))
    else:
        print("[观测标记门禁] 扫描 %d 文件；观测类 %d；豁免 %d" % (len(results), len(observed), len(exempted)))
        for r in exempted:
            print("  ⊘ 豁免 %s（显式 observe-gate: off）" % r["file"])
        for r in failed:
            print("  ❌ %s  缺：%s（已有：%s）"
                  % (r["file"], "／".join(r["missing"]), "／".join(r["present"]) or "无"))
        if failed:
            print("[观测标记门禁] %s：%d 件观测类交付缺标记" % ("WARN" if args.warn_only else "FAIL", len(failed)))
        else:
            print("[观测标记门禁] ✅ PASS：观测类交付三要素齐全（或无观测类交付）")
    return 1 if (failed and not args.warn_only) else 0


if __name__ == "__main__":
    sys.exit(main())
