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
# 豁免条目（175刀5 起携带元数据：到期批次 / 复核日期 / 豁免理由）
#   expires_batch="—" 表示永久豁免，需主动销账；review_date 为最近复核日期。
EXEMPT_FILES = {
    "main.py": {"expires_batch": "—", "review_date": "2026-10-09",
                "reason": "入口装配，弃用调度器桥接豁免"},
    "nucleus/self_inspector.py": {"expires_batch": "—", "review_date": "2026-10-09",
                                  "reason": "自检器历史桥接豁免"},
    "nucleus/parallel_scheduler.py": {"expires_batch": "—", "review_date": "2026-10-09",
                                      "reason": "并行调度器历史桥接豁免"},
}


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


def selftest():
    # 核心判定自证：monkeypatch 注入伪 git diff，驱动 scan_violations 的
    # 弃用 import 检测（正例必命中 / 豁免必忽略 / 反例必无）。
    import subprocess as _sp

    # 健全性：对真实仓库返回 list
    assert isinstance(scan_violations(repo_root()), list)

    # 动态拼出弃用 import 字面量：避免源码静态出现被禁 import 文本
    # （如 HybridParallelScheduler / StructuredParallelScheduler 的 from import 行），
    # 否则本门在提交自身时会被自身扫描命中（check_deprecated_imports
    # 不在 EXEMPT_FILES）。运行时 _line_a/_line_b 才拼成完整弃用 import 行。
    _mod_a = "HybridParallel" + "Scheduler"
    _mod_b = "StructuredParallel" + "Scheduler"
    _line_a = "+from nucleus.%s import Foo\n" % _mod_a
    _line_b = "+from nucleus.%s import Bar\n" % _mod_b
    fake_diff = (
        "+++ b/prod/use_dep.py\n"
        "@@ -0,0 +1 @@\n"
        + _line_a +
        "+++ b/main.py\n"
        "@@ -0,0 +1 @@\n"
        + _line_b +
        "+++ b/prod/clean.py\n"
        "@@ -0,0 +1 @@\n"
        "+import os\n"
    )

    class _FakeDiff:
        stdout = fake_diff
        returncode = 0

    _real_run = _sp.run

    def _fake_run(cmd, **kw):
        if isinstance(cmd, (list, tuple)) and cmd[:2] == ["git", "diff"]:
            return _FakeDiff()
        return _real_run(cmd, **kw)

    _sp.run = _fake_run
    try:
        v = scan_violations(repo_root())
        files = {rel for rel, src, dep in v}
        # 正例：生产代码新引入弃用 import → 命中
        assert "prod/use_dep.py" in files, "正例应检测弃用 import"
        # 豁免例：EXEMPT_FILES 中的 main.py → 忽略
        assert "main.py" not in files, "main.py 应豁免"
        # 反例：普通 import 不误报
        assert "prod/clean.py" not in files, "普通 import 不应误报"
    finally:
        _sp.run = _real_run
    print("[selftest] deprecated-import 自证通过")
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv
    if "--selftest" in argv:
        return selftest()
    root = repo_root()
    violations = scan_violations(root)
    if not violations:
        print("[deprecated-import] PASS —— 无新增非豁免弃用调度器 import")
        return 0
    print("[deprecated-import] FAIL —— 检测到新增弃用调度器 import（应改用 "
          "nucleus.parallel_scheduler.get_parallel_scheduler）：")
    for rel, src, dep in violations:
        print("    %s  %s  (弃用: %s)" % (rel, src, dep))
    print("[deprecated-import] 豁免文件（到期批次/复核日期）:")
    for _f in sorted(EXEMPT_FILES):
        _m = EXEMPT_FILES[_f]
        print("  - %s  [到期:%s 复核:%s]  %s" % (
            _f, _m.get("expires_batch", "—"), _m.get("review_date", "—"), _m.get("reason", "")))
    return 1


if __name__ == "__main__":
    sys.exit(main())
