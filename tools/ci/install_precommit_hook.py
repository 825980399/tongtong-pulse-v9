#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第162批刀13/14/15 · 门禁 hook 安装源（入库，供星轨复核 + 重装）。

本脚本把 tools/ci/hooks/ 下的 hook 模板安装到 .git/hooks/：
  - pre-commit   （含刀13 GATE_PATH_SPLIT / 刀14 GATE_PARALLEL）
  - commit-msg   （含刀15 重锚分类 classify_reanchor 调用）

用法：
    "D:/Program Files/Python312/python.exe" tools/ci/install_precommit_hook.py

说明：
  - .git/hooks/ 下的 hook 本体不入库（git 默认忽略），故以本脚本为「安装源」；
  - 每次修改 tools/ci/hooks/* 模板后，重跑本脚本即可同步本地 hook；
  - 回退开关为环境变量：GATE_PATH_SPLIT=0 / GATE_PARALLEL=0 / GATE_COLLECT_LEVEL=full。
"""
import os
import shutil
import stat
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(SCRIPT_DIR, "hooks")
ROOT = subprocess.check_output(
    ["git", "rev-parse", "--show-toplevel"], text=True
).strip()
HOOKS_DIR = os.path.join(ROOT, ".git", "hooks")

HOOKS = ("pre-commit", "commit-msg")


def _install(name):
    src = os.path.join(TEMPLATE_DIR, name)
    dst = os.path.join(HOOKS_DIR, name)
    if not os.path.isfile(src):
        print(f"[install] 模板缺失：{src}", file=sys.stderr)
        return False
    shutil.copyfile(src, dst)
    # 确保可执行（POSIX 权限位；Windows 下 git 以 sh 调用，权限不影响）
    try:
        st = os.stat(dst)
        os.chmod(dst, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as e:
        print(f"[install] chmod 跳过（{e}）", file=sys.stderr)
    print(f"[install] {name} -> {dst}")
    return True


def main():
    if not os.path.isdir(HOOKS_DIR):
        print(f"[install] hooks 目录不存在：{HOOKS_DIR}", file=sys.stderr)
        return 1
    ok = True
    for h in HOOKS:
        ok = _install(h) and ok
    if ok:
        print("[install] 完成。重装验证：git commit 任一改动触发 hook；")
        print("         文档专用提交应见 'GATE_PATH_SPLIT：仅文档路径，跳过代码/collect 门禁'；")
        print("         含 .py 提交应见 'GATE_PARALLEL：5 静态门并发执行'。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
