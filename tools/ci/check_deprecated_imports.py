#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_deprecated_imports.py —— T155-R3 P2-64 并发收敛（Step2 门禁）

禁止「新代码」import 已 @deprecated 的并发调度器：
    nucleus.HybridParallelScheduler
    nucleus.StructuredParallelScheduler

允许遗留调用点（指纹豁免）：
    main.py
    nucleus/self_inspector.py

实现：扫描本次提交（staged diff）新增行的 import 语句，命中弃用模块且不在豁免文件即 FAIL。
用法：git diff --cached 已有内容时运行；无暂存内容则直接 PASS。
退出码：0=通过，1=阻断。
"""
import re
import subprocess
import sys


def repo_root() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], encoding="utf-8"
    ).strip()


DEPRECATED = [
    "nucleus.HybridParallelScheduler",
    "nucleus.StructuredParallelScheduler",
]
EXEMPT_FILES = {"main.py", "nucleus/self_inspector.py", "nucleus/parallel_scheduler.py"}


def scan_violations(root: str):
    r = subprocess.run(
        ["git", "diff", "--cached", "-U0"],
        cwd=root, capture_output=True, text=True, encoding="utf-8",
    )
    violations = []
    cur = None
    for line in r.stdout.splitlines():
        if line.startswith("+++ "):
            cur = line[4:].strip()
            if cur.startswith("b/"):
                cur = cur[2:]
            if cur == "/dev/null":
                cur = None
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added = line[1:]
            for dep in DEPRECATED:
                if re.search(r"(from\s+" + re.escape(dep) + r"\b|import\s+" + re.escape(dep) + r"\b)", added):
                    if cur not in EXEMPT_FILES:
                        violations.append((cur, added.strip(), dep))
    return violations


def main():
    root = repo_root()
    violations = scan_violations(root)
    if not violations:
        print("[deprecated-import] PASS —— 无新增非豁免弃用调度器 import")
        return 0
    print("[deprecated-import] FAIL —— 检测到新增弃用调度器 import（应改用 "
          "nucleus.parallel_scheduler.get_parallel_scheduler）：")
    for rel, src, dep in violations:
        print("    %s  %s  (弃用: %s)" % (rel, src, dep))
    print("[deprecated-import] 豁免文件仅限: %s" % ", ".join(sorted(EXEMPT_FILES)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
