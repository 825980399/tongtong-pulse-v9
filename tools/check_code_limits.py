#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主线第9批 T3 / P2-66：代码行数红线检查工具。

扫描全库 Python 文件，按三档红线检查：
  - max_lines_per_file       单一文件最大行数
  - max_lines_per_function   单一函数最大行数
  - max_functions_per_file   单一文件最大函数数

并提供"警告档"（warn_*）：超过即报告但不阻断；远超红线才在 CI 升级为错误。

用法：
  python tools/check_code_limits.py                 # 文本报告（仅超限文件）
  python tools/check_code_limits.py --json          # JSON 输出（供 CI/CD 集成）
  python tools/check_code_limits.py --strict        # 红线上限视为错误（退出码 1）
  python tools/check_code_limits.py --top 20        # 报告前 N 个最严重文件
  python tools/check_code_limits.py --config custom_config.py  # 自定义配置（可选）

退出码：0 = 无超限；1 = 存在红线上限超限（仅 --strict）；2 = 参数/运行错误。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from nucleus.data.exclude_dirs import AUDIT_SCAN_EXCLUDED  # ★第55批 T4（统一排除清单）

# ★主线第12批 T2/P2-82：备份目录排除统一走共用模块（不再逐批次硬编码）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nucleus._silent_except import silent_exc
from tools.audit_utils import is_backup_path, should_skip_dir  # noqa: E402

# -------- 默认配置（与 config.CODE_QUALITY_CONFIG 对齐；此处内联以便独立运行）--------
DEFAULT_CONFIG: dict[str, Any] = {
    "max_lines_per_file": 5000,
    "max_lines_per_function": 300,
    "max_functions_per_file": 50,
    "enable_line_count_check": True,
    "warn_lines_per_file": 3000,
    "warn_lines_per_function": 200,
    "warn_functions_per_file": 40,
}

# 扫描时排除的目录（叶子名匹配，避免误伤备份/缓存/运行时数据）
# ★主线第12批 T2/P2-82：移除硬编码的 .bak_mainline8/9 —— 备份目录改由
#   tools/audit_utils.should_skip_dir() 统一按前缀判定，新增批次零维护。


def _m55_unified_excludes() -> bool:
    """★第55批 T4 灰度开关：关掉可回退到第55批前的各自定义清单。"""
    try:
        import config as _m55_cfg

        return bool(getattr(_m55_cfg, "ENABLE_EXCLUDE_DIRS_UNIFIED", True))
    except Exception as e:
        silent_exc(e, where="tools.check_code_limits::_m55_unified_excludes L59")
        return True

_M55_LEGACY_EXCLUDE_DIRS = {
    ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".idea", ".vscode", ".mpy-workbench", ".workbuddy", "logs", "tmp",
    "node_modules", "models", "hardware", "code_backups",
    "knowledge", "experience", "stream", "qica", "corrupted",
}

#: ★第55批 T4：统一清单（基础项来自 exclude_dirs，新增目录只需改一处）
EXCLUDE_DIRS: frozenset[str] = (
    AUDIT_SCAN_EXCLUDED if _m55_unified_excludes() else _M55_LEGACY_EXCLUDE_DIRS
)

# 扫描时排除的文件名（叶子名）
EXCLUDE_FILES = {"__init__.py"}  # 通常不计入"上帝文件"评估


@dataclass
class FileReport:
    path: str
    total_lines: int
    func_count: int
    func_over_300: list[tuple[str, int]] = field(default_factory=list)  # (name, lines)
    severity: str = "ok"  # ok | warn | error


def _count_function_lines(node: ast.AST, source_lines: list[str]) -> int:
    """统计一个函数定义（含装饰器与 docstring）占用的行数。"""
    start = node.lineno
    end = getattr(node, "end_lineno", start)
    return max(0, end - start + 1)


def _iter_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    funcs: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node)  # type: ignore[arg-type]
    return funcs


def scan_file(path: str) -> FileReport | None:
    """扫描单个 .py 文件，返回 FileReport；解析失败返回 None。"""
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        silent_exc(e, where="tools.check_code_limits::scan_file L107")
        return None

    lines = source.splitlines()
    total = len(lines)

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        # 语法错误由 ruff 负责，此处跳过（不污染行数统计）
        silent_exc(e, where="tools.check_code_limits::scan_file L115")
        return None

    funcs = _iter_functions(tree)
    func_over = [
        (getattr(f, "name", "<anon>"), _count_function_lines(f, lines))
        for f in funcs
        if _count_function_lines(f, lines) > DEFAULT_CONFIG["max_lines_per_function"]
    ]
    return FileReport(
        path=path,
        total_lines=total,
        func_count=len(funcs),
        func_over_300=func_over,
    )


def evaluate(report: FileReport, cfg: dict[str, Any]) -> str:
    """根据配置评估严重程度。"""
    if not cfg.get("enable_line_count_check", True):
        return "ok"
    errors = 0
    warns = 0
    if report.total_lines > cfg["max_lines_per_file"]:
        errors += 1
    elif report.total_lines > cfg.get("warn_lines_per_file", 10 ** 9):
        warns += 1
    if report.func_count > cfg["max_functions_per_file"]:
        errors += 1
    elif report.func_count > cfg.get("warn_functions_per_file", 10 ** 9):
        warns += 1
    if report.func_over_300:
        errors += 1
    if errors:
        return "error"
    if warns:
        return "warn"
    return "ok"


def collect(root: str, cfg: dict[str, Any]) -> list[FileReport]:
    """递归扫描 root 下所有 .py，返回已评估的 FileReport 列表。"""
    results: list[FileReport] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 原地剪枝排除目录（备份目录由共用模块按前缀判定，含未来批次）
        dirnames[:] = [
            d for d in dirnames
            if not should_skip_dir(d, frozenset(EXCLUDE_DIRS))
        ]
        for fn in filenames:
            if not fn.endswith(".py") or fn in EXCLUDE_FILES:
                continue
            full = os.path.join(dirpath, fn)
            if is_backup_path(fn):
                continue
            rep = scan_file(full)
            if rep is None:
                continue
            rep.severity = evaluate(rep, cfg)
            results.append(rep)
    return results


def severity_rank(rep: FileReport) -> int:
    """排序键：error 优先，其次按文件行数降序。"""
    rank = {"error": 0, "warn": 1, "ok": 2}.get(rep.severity, 3)
    return (rank, -rep.total_lines)


def build_report(results: list[FileReport], cfg: dict[str, Any], top: int | None) -> dict[str, Any]:
    over = [r for r in results if r.severity != "ok"]
    over.sort(key=severity_rank)
    if top is not None:
        over = over[:top]

    items = []
    for r in over:
        items.append({
            "path": r.path,
            "severity": r.severity,
            "total_lines": r.total_lines,
            "func_count": r.func_count,
            "functions_over_limit": [
                {"name": n, "lines": ln} for n, ln in r.func_over_300
            ],
            "max_lines_per_file": cfg["max_lines_per_file"],
            "max_functions_per_file": cfg["max_functions_per_file"],
            "max_lines_per_function": cfg["max_lines_per_function"],
        })

    return {
        "config": cfg,
        "scanned_files": len(results),
        "over_limit_count": len(over),
        "error_count": sum(1 for r in results if r.severity == "error"),
        "warn_count": sum(1 for r in results if r.severity == "warn"),
        "over_limit_files": items,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="代码行数红线检查")
    parser.add_argument("--root", default=".", help="扫描根目录（默认当前目录）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--strict", action="store_true", help="红线上限超限视为错误（退出码1）")
    parser.add_argument("--top", type=int, default=None, help="仅报告前 N 个最严重文件")
    parser.add_argument("--config", default=None, help="自定义配置 .py（须定义 CODE_QUALITY_CONFIG）")
    args = parser.parse_args(argv)

    cfg = dict(DEFAULT_CONFIG)
    if args.config:
        ns: dict[str, Any] = {}
        with open(args.config, encoding="utf-8") as fh:
            exec(compile(fh.read(), args.config, "exec"), ns)  # 受控的本地配置加载
        if "CODE_QUALITY_CONFIG" in ns:
            cfg.update(ns["CODE_QUALITY_CONFIG"])

    results = collect(args.root, cfg)
    report = build_report(results, cfg, args.top)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"扫描文件数: {report['scanned_files']}")
        print(f"超限文件数: {report['over_limit_count']} "
              f"（error={report['error_count']}, warn={report['warn_count']}）")
        print("-" * 72)
        for item in report["over_limit_files"]:
            print(f"[{item['severity'].upper():5}] {item['path']}")
            print(f"         行数={item['total_lines']} (红线 {item['max_lines_per_file']}) | "
                  f"函数数={item['func_count']} (红线 {item['max_functions_per_file']}) | "
                  f"超长函数={len(item['functions_over_limit'])}")

    if args.strict and report["error_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
