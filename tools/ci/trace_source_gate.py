#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""溯源门禁（trace-source-gate）· 171 批微光侧门禁脚本（任务1 · 第一支）

判据：交付报告 / 任务书中的「来源声明」必须可被 git 历史与登记册反查，悬空即 FAIL。

三类来源声明：
  ① 票号      形如 T-名-号（名单字面见下）→ 必须存在于 docs/台账/待裁决登记册.csv 的 ID 列
  ② 提交 SHA  7-40 位十六进制          → `git cat-file -e <sha>^{commit}` 必须成功
              取值限定：反引号内，或紧跟 @ / HEAD / SHA / commit / 提交 / 基线 等上下文；
              纯数字串（日期、编号）与已知占位串不算 SHA，避免把 20261008 误判为提交号
  ③ 文件路径  反引号内 或 Markdown 链接目标，且扩展名在白名单内
              → 含 / 者按「仓根相对存在性 或 git 跟踪集」校验；
                仅 basename 者按 `git ls-files` basename 索引校验（容忍散文体 basename 引用）

退出码：0 = PASS（无悬空）；1 = FAIL（有悬空，`--warn-only` 可降级为 0）；2 = 内部错误。
只读：本脚本不写任何文件（`--json` 仅输出到 stdout）。

与 M2（tools/ci/check_pending_register.py）互补不重复：
  M2 管「登记册总数 + ID 集合 + 到期逾期」；本件管「报告里写的来源声明能否反查」。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):  # 管道/CI 下控制台可能为 cp936，符号字符会触发 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
LEDGER_PATH = os.path.join(REPO_ROOT, "docs", "台账", "待裁决登记册.csv")
DEFAULT_SCAN_DIR = os.path.join(REPO_ROOT, "docs", "微光")

_PATH_EXTS = (
    ".py", ".pyi", ".md", ".txt", ".json", ".jsonl", ".csv", ".yaml", ".yml",
    ".ps1", ".bat", ".cmd", ".sh", ".toml", ".cfg", ".ini", ".lock", ".html",
    ".js", ".ts", ".css", ".sql",
)
_SKIP_PARTS = ("归档", "archive", ".aionclaw-tmp", "tmp", "__pycache__")

#: 票号：T-<中英数下划线短横>-<数字>；排除反引号/空白/中文标点/括号等边界字符
_TICKET_RE = re.compile(r"T-(?:[^`\s，。；、：（）()\[\]{}|*<>\"'])+?-\d+")
_BACKTICK_RE = re.compile(r"`([^`\n]{1,200})`")
_MDLINK_RE = re.compile(r"\[[^\]\n]{0,80}\]\(([^)\s]{1,200})\)")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_SHA_CTX_RE = re.compile(
    r"(?:@|HEAD|head|SHA|sha|commit|Commit|提交|基线)\s*[:：]?\s*`?([0-9a-fA-F]{7,40})`?"
)
_STOP_SHA = {"deadbeef", "0000000", "fffffff", "0123456", "1234567"}


def _rel_path(fp):
    """跨盘符安全：外盘/临时目录文件 os.path.relpath 会抛 ValueError。"""
    try:
        return os.path.relpath(fp, REPO_ROOT).replace("\\", "/")
    except ValueError as exc:
        print("[trace-source-gate] relpath 跨盘符回退：%s" % exc, file=sys.stderr)
        return fp.replace("\\", "/")


def _run_git(args, timeout=180):
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        return subprocess.run(
            ["git", "-C", REPO_ROOT] + list(args),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print("[trace-source-gate] git 执行失败：%s" % exc, file=sys.stderr)
        return None


def load_ledger_ids(path=LEDGER_PATH):
    """登记册 ID 集合；文件缺失或表头异常 → None（调用方跳过票号校验并显式提示）。"""
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return None
    header = [c.strip() for c in rows[0]]
    if "ID" not in header:
        return None
    idx = header.index("ID")
    return {r[idx].strip() for r in rows[1:] if len(r) > idx and r[idx].strip()}


def tracked_files():
    r = _run_git(["ls-files"])
    if r is None or r.returncode != 0:
        return set()
    return {ln.strip().replace("\\", "/") for ln in r.stdout.split("\n") if ln.strip()}


def sha_exists(sha):
    r = _run_git(["cat-file", "-e", sha + "^{commit}"])
    return r is not None and r.returncode == 0


def _is_probable_sha(tok, allow_digits=False):
    """反引号内的裸十六进制串排除纯数字（日期/编号）；带 HEAD/commit 上下文时允许纯数字
    —— 真实提交号可能全为数字（如 0306626）。"""
    tok = (tok or "").strip().strip("`")
    if not _HEX_RE.match(tok) or tok.lower() in _STOP_SHA:
        return False
    if tok.isdigit() and not allow_digits:
        return False
    return True


def _looks_like_path(tok):
    tok = (tok or "").strip()
    if not tok or tok.startswith(("http://", "https://", "mailto:", "#")):
        return False
    if any(ch in tok for ch in "*<>|") or " " in tok:
        return False
    low = tok.lower().rstrip("/")
    if not low.endswith(_PATH_EXTS):
        return False
    base = os.path.basename(low)
    return any(base.endswith(e) and len(base) > len(e) for e in _PATH_EXTS)


def extract_claims(text):
    """抽取三类来源声明候选 → [(kind, token, line_no)]（按行号升序）。"""
    out = []
    for ln, line in enumerate(text.splitlines(), 1):
        for m in _TICKET_RE.finditer(line):
            out.append(("ticket", m.group(0), ln))
        for m in _BACKTICK_RE.finditer(line):
            inner = m.group(1).strip()
            if _is_probable_sha(inner):
                out.append(("sha", inner, ln))
            if _looks_like_path(inner):
                out.append(("path", inner, ln))
        for m in _MDLINK_RE.finditer(line):
            if _looks_like_path(m.group(1)):
                out.append(("path", m.group(1).strip(), ln))
        for m in _SHA_CTX_RE.finditer(line):
            if _is_probable_sha(m.group(1), allow_digits=True):
                out.append(("sha", m.group(1), ln))
    return out


_RUNTIME_PREFIXES = ("data/", "logs/", "tmp/", ".aionclaw-tmp/", "output/", "cache/")


def _norm_tok(tok):
    """归一：去反引号/首尾空白/反斜杠，仅剥前导 ./（不可用 lstrip("./")，会吃掉点文件名）。"""
    t = tok.strip().strip("`").replace("\\", "/")
    while t.startswith("./"):
        t = t[2:]
    return t


def _is_runtime_path(tok):
    t = _norm_tok(tok).lower()
    return any(t.startswith(p) or ("/" + p) in t for p in _RUNTIME_PREFIXES)


def _path_ok(tok, tracked, basenames):
    t = _norm_tok(tok)
    if os.path.exists(os.path.join(REPO_ROOT, t)):
        return True
    if t in tracked:
        return True
    if "/" not in t:
        return t in basenames
    return False


def verify(claims, ids, tracked, basenames, check_sha=True):
    findings = []
    for kind, tok, ln in claims:
        if kind == "ticket":
            if ids is None:
                ok, why = True, "登记册不可用，跳过票号校验"
            else:
                ok, why = (tok in ids), ("在册" if tok in ids else "登记册无此票号")
        elif kind == "sha":
            if not check_sha:
                ok, why = True, "按 --no-sha 跳过"
            else:
                ok = sha_exists(tok)
                why = "git 可解析" if ok else "git 无法解析该提交号"
        else:
            if _is_runtime_path(tok):
                ok, why = True, "运行期路径（不入 git，按 INFO 处理）"
            else:
                ok = _path_ok(tok, tracked, basenames)
                why = "存在" if ok else "盘上不存在且未被 git 跟踪"
        findings.append({"kind": kind, "token": tok, "line": ln, "ok": ok, "why": why})
    return findings


def iter_targets(paths, all_docs):
    if paths:
        seeds = [p if os.path.isabs(p) else os.path.join(REPO_ROOT, p) for p in paths]
    elif all_docs:
        seeds = [os.path.join(REPO_ROOT, "docs")]
    else:
        seeds = [DEFAULT_SCAN_DIR]
    for seed in seeds:
        if os.path.isfile(seed) and seed.endswith(".md"):
            yield seed
        elif os.path.isdir(seed):
            for root, dirs, files in os.walk(seed):
                dirs[:] = [d for d in dirs if d not in _SKIP_PARTS]
                if any(part in root for part in _SKIP_PARTS):
                    continue
                for name in sorted(files):
                    if name.endswith(".md"):
                        yield os.path.join(root, name)


def scan_file(path, ids, tracked, basenames, check_sha=True):
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            text = fh.read()
    except OSError as exc:
        print("[trace-source-gate] 读取失败：%s（%s）" % (path, exc), file=sys.stderr)
        return []
    return verify(extract_claims(text), ids, tracked, basenames, check_sha)


def selftest():
    _base = "T-" + "样例"
    ids = {_base + "-1"}
    text = (
        "来源：" + _base + "-1（在册）；反例：" + _base + "-9（不在册）。\n"
        "提交 @" + "abc1234" + "；路径 `tools/ci/trace_source_gate.py`；"
        "占位 `deadbeef` 不算 SHA；纯数字 `20261008` 不算 SHA。\n"
    )
    claims = extract_claims(text)
    kinds = [c[0] for c in claims]
    assert kinds.count("ticket") == 2, claims
    assert kinds.count("sha") == 1, claims
    assert kinds.count("path") == 1, claims
    tracked = tracked_files()
    basenames = {os.path.basename(p) for p in tracked}
    f = verify(claims, ids, tracked, basenames, check_sha=True)
    bad = [x for x in f if not x["ok"]]
    assert len(bad) == 2, f
    assert {b["kind"] for b in bad} == {"ticket", "sha"}, f
    assert all(x["ok"] for x in f if x["kind"] == "path"), f
    assert all(x["ok"] for x in verify(claims, None, tracked, basenames) if x["kind"] == "ticket"), \
        "登记册缺失时票号校验应降级为通过（SHA/路径仍照常校验）"
    assert not any(c[1] == "20261008" for c in claims), "纯数字不得被判为 SHA"
    print("[selftest] trace-source-gate 自证通过（正例 3 类 + 反例 悬空票号/悬空 SHA）")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        selftest()
        return 0
    ap = argparse.ArgumentParser(description="溯源门禁：校验来源声明（票号 / SHA / 路径）可反查")
    ap.add_argument("--paths", nargs="*", default=None, help="待扫描文件或目录（默认 docs/微光）")
    ap.add_argument("--all-docs", action="store_true", help="扫描整个 docs/")
    ap.add_argument("--warn-only", action="store_true", help="发现悬空仅告警，退出码仍为 0")
    ap.add_argument("--no-sha", action="store_true", help="跳过 SHA 解析校验（离线/浅克隆场景）")
    ap.add_argument("--max-print", type=int, default=40, help="最多打印条数（默认 40）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    ids = load_ledger_ids()
    if ids is None:
        print("[trace-source-gate] ⚠️ 登记册不可用（%s），票号校验降级" % LEDGER_PATH, file=sys.stderr)
    tracked = tracked_files()
    if not tracked:
        print("[trace-source-gate] ⚠️ git ls-files 为空，路径校验降级为盘上存在性", file=sys.stderr)
    basenames = {os.path.basename(p) for p in tracked}

    files = list(iter_targets(args.paths, args.all_docs))
    all_findings = []
    for fp in files:
        for item in scan_file(fp, ids, tracked, basenames, check_sha=not args.no_sha):
            item["file"] = _rel_path(fp)
            all_findings.append(item)

    dangling = [x for x in all_findings if not x["ok"]]
    if args.json:
        print(json.dumps({"files": len(files), "claims": len(all_findings),
                          "dangling": dangling, "findings": all_findings},
                         ensure_ascii=False, indent=2))
    else:
        counts = {"ticket": 0, "sha": 0, "path": 0}
        for x in all_findings:
            counts[x["kind"]] += 1
        print("[溯源门禁] 扫描 %d 文件 / 声明 %d 条（票号 %d / SHA %d / 路径 %d）"
              % (len(files), len(all_findings), counts["ticket"], counts["sha"], counts["path"]))
        for x in dangling[:args.max_print]:
            print("  ❌ %s:%d  %s  %s  —— %s"
                  % (x["file"], x["line"], x["kind"], x["token"], x["why"]))
        if len(dangling) > args.max_print:
            print("  …（其余 %d 条见 --json）" % (len(dangling) - args.max_print))
        if dangling:
            print("[溯源门禁] %s：%d 条悬空引用" % ("WARN" if args.warn_only else "FAIL", len(dangling)))
        else:
            print("[溯源门禁] ✅ PASS：零悬空引用")
    if dangling and not args.warn_only:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
