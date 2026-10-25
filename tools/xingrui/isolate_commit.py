# -*- coding: utf-8 -*-
"""isolate_commit.py · 星轨自用：暂存区隔离核对＋路径限定提交（落实自检规范 E5）。

用途：替代每批手工来回 status/add/diff --cached/show --stat 的提交辅助探针（复盘 3.3 · P0 工具 4）。
边界：
  - 不自动 `git restore --staged` 他方件；发现暂存区混入非本笔件即拒绝提交，提示星轨手工退暂存；
  - commit 末尾始终带 `-- <本笔路径清单>` 路径限定双保险；
  - 不执行 amend、不 push、不签提交；生产代码提交仍由路灯完成。

子命令：
  status                              分类列出 staged / 未暂存修改 / untracked
  check --files f1 f2 ...            核对暂存区与期望清单一一对应（多暂存=他方件混入）
  commit --message "..." --files f1 f2 ...
                                      先 check，一致后路径限定提交，并 show --stat HEAD 复核

用法：
    python tools/xingrui/isolate_commit.py status
    python tools/xingrui/isolate_commit.py check --files docs/a.md docs/b.md
    python tools/xingrui/isolate_commit.py commit --message "docs(366) 说明" --files docs/a.md docs/b.md

退出码：0 = 核对/提交成功；1 = 暂存区不符或提交失败；2 = 内部错误。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))


def norm(p: str) -> str:
    return p.replace("\\", "/").removeprefix("./")


def git(args: list[str]) -> tuple[int, str]:
    r = subprocess.run(["git"] + args, cwd=REPO_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def staged_files() -> list[str]:
    rc, out = git(["diff", "--cached", "--name-only"])
    if rc != 0:
        raise RuntimeError("git diff --cached 失败: " + out)
    return [norm(x) for x in out.splitlines() if x.strip()]


def cmd_status(_args) -> int:
    rc, out = git(["status", "--short"])
    if rc != 0:
        sys.stderr.write(out + "\n")
        return 2
    staged, modified, untracked = [], [], []
    for line in out.splitlines():
        if not line.strip():
            continue
        x, y = line[0], line[1]
        path = norm(line[3:])
        if x == "?" and y == "?":  # untracked 优先判定，避免 '?' 被当成已暂存状态
            untracked.append(path)
        elif x != " ":
            staged.append(path)
        elif y != " ":
            modified.append(path)
    print("=== 已暂存（%d）===" % len(staged))
    for x in staged:
        print("  " + x)
    print("=== 未暂存修改（%d）===" % len(modified))
    for x in modified:
        print("  " + x)
    print("=== untracked（%d）===" % len(untracked))
    for x in untracked:
        print("  " + x)
    return 0


def check_expected(files: list[str]) -> tuple[bool, list[str], list[str]]:
    want = {norm(f) for f in files}
    staged = set(staged_files())
    extra = sorted(staged - want)      # 多暂存＝他方件混入
    missing = sorted(want - staged)    # 缺暂存＝期望件未 add
    return not extra and not missing, extra, missing


def cmd_check(args) -> int:
    ok, extra, missing = check_expected(args.files)
    print("期望清单（%d）: %s" % (len(args.files), ", ".join(args.files)))
    if extra:
        print("✗ 混入暂存区的他方件（禁止连带，先手工 git restore --staged -- <件>）:")
        for x in extra:
            print("  " + x)
    if missing:
        print("✗ 未暂存的期望件:")
        for x in missing:
            print("  " + x)
    if ok:
        print("OK：暂存区与期望清单一一对应")
    return 0 if ok else 1


def cmd_commit(args) -> int:
    ok, extra, missing = check_expected(args.files)
    if not ok:
        cmd_check(args)
        sys.stderr.write("拒绝提交：暂存区不符，先清理他方件/补齐期望件。\n")
        return 1
    cmd = ["commit", "-m", args.message, "--"] + args.files
    rc, out = git(cmd)
    print(out)
    if rc != 0:
        sys.stderr.write("提交失败 rc=%d\n" % rc)
        return 1
    rc, stat = git(["show", "--stat", "HEAD"])
    print("=== show --stat HEAD 复核 ===")
    print(stat)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="暂存区隔离核对＋路径限定提交（星轨自用）")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("status", help="分类列出工作树状态")
    ps.set_defaults(func=cmd_status)

    pc = sub.add_parser("check", help="核对暂存区与期望清单")
    pc.add_argument("--files", nargs="+", required=True)
    pc.set_defaults(func=cmd_check)

    pm = sub.add_parser("commit", help="核对一致后路径限定提交")
    pm.add_argument("--message", required=True)
    pm.add_argument("--files", nargs="+", required=True)
    pm.set_defaults(func=cmd_commit)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as e:
        sys.stderr.write("内部错误: %r\n" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
