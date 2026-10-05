# -*- coding: utf-8 -*-
"""tools/measure_baseline.py —— ★第146批 T146-4：**唯一**规模度量真值源。

背景：历史各份报告里的「Python 文件数 / 代码行数 / 器官数 / 测试用例数 /
data 体积」由不同临时命令产出，口径互不统一（有的含 tmp/ 备份，有的不含，
有的连 .bak_batchN 一起算），导致对外数据卡片与内部报告互相打架。

本脚本把口径固化为一处，**后续所有报告的规模数字必须引用本脚本输出**。

排除原则（强制，不受命令行影响）：
  .bak*/ tmp/ .release-tmp/ data/code_backups/ venv/ workspace/
  __pycache__/ .pytest_cache/ .mypy_cache/ .ruff_cache/ node_modules/ .git/ 等缓存与备份。

用法：
    python tools/measure_baseline.py            # 人类可读表格
    python tools/measure_baseline.py --json     # 机读 JSON（CI / 报告生成器消费）
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 度量脚本自身的观测出口 —— 抓取失败必须留痕，禁止静默吞异常
_log = logging.getLogger("tools.measure_baseline")

#: 目录名黑名单（命中任意**路径分量**即整棵子树跳过）
EXCLUDE_DIR_NAMES: frozenset[str] = frozenset({
    ".git", ".workbuddy", ".idea", ".vscode",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis",
    "node_modules", "venv", ".venv", "env", ".env_bak",
    "tmp", ".release-tmp", "workspace",
    "code_backups",            # data/code_backups/
    ".aionclaw-tmp",           # ★段A A2：与 const.SCAN_EXCLUDE_DIRS 对齐（157-E 假修复订正，同步补点号目录名）
    "build", "dist", ".eggs",
})

#: 目录名前缀黑名单（.bak_batch146 / .bak 开头的历史备份）
EXCLUDE_DIR_PREFIXES: tuple[str, ...] = (".bak",)

#: 相对项目根的精确路径黑名单（防止 data/xxx 这类有歧义的名字误伤/漏掉）
EXCLUDE_EXACT_PATHS: frozenset[str] = frozenset({
    "data",
})

#: 参与统计的 Python 文件后缀
PY_EXT: frozenset[str] = frozenset({".py"})


def rel_norm(path: str) -> str:
    return os.path.relpath(path, PROJECT_ROOT).replace("\\", "/")


def _excluded_dir(dname: str) -> bool:
    return dname in EXCLUDE_DIR_NAMES or dname.startswith(EXCLUDE_DIR_PREFIXES)


def iter_python_files(root: str):
    """产出纳入统计的 Python 文件绝对路径（备份与缓存目录已整棵剪枝）。"""
    for cur, dirs, files in os.walk(root):
        # 剪枝：目录黑名单 + data/ 精确排除（data 体积单独统计，不算代码）
        keep = []
        for d in dirs:
            full_d = os.path.join(cur, d)
            if _excluded_dir(d):
                continue
            if rel_norm(full_d) in EXCLUDE_EXACT_PATHS:
                continue
            keep.append(d)
        dirs[:] = keep
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in PY_EXT:
                yield os.path.join(cur, f)


def count_lines(path: str) -> int:
    """物理行数（含空行与注释）；不可读按 0 计。"""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as _e:
        _log.warning("行数统计失败，该文件按 0 行计: exc=%s path=%s", _e, path)
        return 0
    if not data:
        return 0
    n = data.count(b"\n")
    if not data.endswith(b"\n"):
        n += 1
    return n


def count_test_cases(path: str) -> int:
    """统计文件内的 pytest 用例数：模块级 test_* 函数 + 类内 test_* 方法（去重展开）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError, ValueError) as _e:
        _log.warning("AST 解析失败，该文件用例数按 0 计: exc=%s path=%s", _e, path)
        return 0
    n = 0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
            n += 1
        elif isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test"):
            n += 1
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and sub.name.startswith("test"):
                    n += 1
    return n


def dir_size_bytes(path: str) -> int:
    total = 0
    if not os.path.isdir(path):
        return 0
    for cur, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(cur, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                continue
    return total


def git_head(root: str) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=root, capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError) as _e:
        _log.debug("git HEAD 读取失败，版本号回退 unknown: exc=%s", _e)
    return "unknown"


def measure(root: str) -> dict:
    files = sorted(iter_python_files(root))
    py_n = len(files)
    total_lines = 0
    core_lines = 0
    organ_files = 0
    for p in files:
        ln = count_lines(p)
        total_lines += ln
        rel = rel_norm(p)
        if rel.startswith(("nucleus/", "organs/", "tools/")):
            core_lines += ln
        if rel.startswith("organs/") and os.path.basename(p) not in ("__init__.py",):
            organ_files += 1

    tests_dir = os.path.join(root, "tests")
    test_files = 0
    test_cases = 0
    if os.path.isdir(tests_dir):
        for name in sorted(os.listdir(tests_dir)):
            fp = os.path.join(tests_dir, name)
            if not os.path.isfile(fp) or not name.startswith("test_"):
                continue
            if os.path.splitext(name)[1].lower() != ".py":
                continue
            test_files += 1
            test_cases += count_test_cases(fp)

    data_bytes = dir_size_bytes(os.path.join(root, "data"))

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python_version": sys.version.split()[0],
        "git_head": git_head(root),
        "root": root,
        "python_files": py_n,
        "total_python_lines": total_lines,
        "core_lines_nucleus_organs_tools": core_lines,
        "organ_files": organ_files,
        "test_files": test_files,
        "test_cases": test_cases,
        "data_dir_mb": round(data_bytes / 1024 / 1024, 2),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="曈曈 PulseNet 规模基线度量（第146批 T146-4）")
    ap.add_argument("--json", action="store_true", help="输出机读 JSON")
    ap.add_argument("--root", default=PROJECT_ROOT, help="项目根（默认自动探测）")
    args = ap.parse_args(argv)

    m = measure(os.path.abspath(args.root))

    if args.json:
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0

    print("=" * 58)
    print("曈曈 PulseNet · 规模基线度量（唯一真值源 · 第146批 T146-4）")
    print("=" * 58)
    print("| 指标 | 实测值 |")
    print("|---|---|")
    print("| Python 文件数 | %d |" % m["python_files"])
    print("| Python 总代码行数 | %d |" % m["total_python_lines"])
    print("| 核心代码行数(nucleus+organs+tools) | %d |" % m["core_lines_nucleus_organs_tools"])
    print("| 仿生器官文件数 | %d |" % m["organ_files"])
    print("| 测试文件数 | %d |" % m["test_files"])
    print("| 测试用例数 | %d |" % m["test_cases"])
    print("| data/ 目录体积 | %.2f MB |" % m["data_dir_mb"])
    print("-" * 58)
    print("版本号(HEAD): %s" % m["git_head"])
    print("运行时间: %s   Python: %s" % (m["generated_at"], m["python_version"]))
    print("机读输出: python tools/measure_baseline.py --json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
