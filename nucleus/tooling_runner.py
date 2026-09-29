# -*- coding: utf-8 -*-
"""
tooling_runner.py —— 工具运行器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 外部工具调用与执行管理
机制: 基于ToolingRunner类实现，包含10个核心方法
定位: 工具管理层
"""

from config import TIMEOUT_CONFIG
import json
import os
import re
import subprocess
import threading
from typing import Any

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底



class ToolingRunner(SilentLogMixin):
    """调度外部代码检查工具，返回结构化问题列表。"""

    def __init__(self, project_root: str):
        self._project_root = project_root

    def _run_cmd(self, cmd: list[str], timeout: int = 180) -> str:
        """运行命令并返回 stdout+stderr（失败不抛异常）。"""
        try:
            _r = subprocess.run(
                cmd, check=False, cwd=self._project_root, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace"
            )
            return (_r.stdout or "") + "\n" + (_r.stderr or "")
        except Exception as _e:
            return f"[工具执行失败] {' '.join(cmd)}: {_e}"

    def run_compile_check(self) -> list[dict[str, Any]]:
        """语法编译检查（compileall，标准库，零依赖）。"""
        issues = []
        try:
            _out = self._run_cmd([
                "python", "-m", "compileall", "-q", "-f",
                "-x", r"\.venv|__pycache__|\.git",
                self._project_root,
            ])
            # 解析 SyntaxError：形如  File "xxx.py", line N  ...  SyntaxError: ...
            for _m in re.finditer(r'File "([^"]+\.py)", line (\d+)[^\n]*\n\s*(.*?SyntaxError:.*)', _out):
                _file = _m.group(1)
                _line = int(_m.group(2))
                _msg = _m.group(3).strip().split('\n')[0]
                issues.append({
                    "file": _file,
                    "line": _line,
                    "type": "syntax_error",
                    "severity": "high",
                    "tool": "compileall",
                    "message": f"语法错误: {_msg}",
                })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return issues

    def run_ruff_check(self, files: list[str] | None = None) -> list[dict[str, Any]]:
        """规范检查（ruff）。files 为空时全量，否则只查变更文件（增量）。"""
        issues = []
        try:
            _targets = files or [self._project_root]
            _out = self._run_cmd([
                "python", "-m", "ruff", "check", "--output-format", "json",
                "--exclude", ".venv,__pycache__",
                *_targets,
            ])
            if not _out.strip() or _out.startswith("[工具执行失败]"):
                return issues
            _data = json.loads(_out)
            for _item in _data:
                _loc = _item.get("location", {}) or {}
                issues.append({
                    "file": _item.get("filename", ""),
                    "line": _loc.get("row", 0),
                    "type": f"lint_{_item.get('code', 'unknown')}",
                    "severity": "low",
                    "tool": "ruff",
                    "message": _item.get("message", ""),
                })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return issues

    def run_mypy_check(self) -> list[dict[str, Any]]:
        """类型检查（mypy）。"""
        issues = []
        try:
            _out = self._run_cmd([
                "python", "-m", "mypy", "--ignore-missing-imports", "--no-error-summary",
                "--exclude", "\\.venv",
                self._project_root,
            ])
            for _m in re.finditer(r'^(.+?\.py):(\d+):\s*(error|warning):\s*(.+)$', _out, re.MULTILINE):
                issues.append({
                    "file": _m.group(1),
                    "line": int(_m.group(2)),
                    "type": "type_error",
                    "severity": "medium",
                    "tool": "mypy",
                    "message": _m.group(4).strip(),
                })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return issues

    def run_bandit_check(self, files: list[str] | None = None) -> list[dict[str, Any]]:
        """安全扫描（bandit）。files 为空时全量，否则只查变更文件（增量）。"""
        issues = []
        try:
            _targets = files or [self._project_root]
            _out = self._run_cmd([
                "python", "-m", "bandit", "-r", "-q", "-f", "json",
                "-x", ".venv,__pycache__",
                *_targets,
            ])
            if not _out.strip() or _out.startswith("[工具执行失败]"):
                return issues
            _data = json.loads(_out)
            _sev_map = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
            for _item in _data.get("results", []):
                issues.append({
                    "file": _item.get("filename", ""),
                    "line": _item.get("line_number", 0),
                    "type": f"security_{_item.get('test_id', 'unknown')}",
                    "severity": _sev_map.get(_item.get("issue_severity", "LOW"), "low"),
                    "tool": "bandit",
                    "message": _item.get("issue_text", ""),
                })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return issues

    def get_changed_py_files(self, limit: int = 50) -> list[str]:
        """获取最近变更的 Python 文件（基于 git，★FIX: 增量审查）。"""
        try:
            _changed = []
            _r = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                check=False, cwd=self._project_root, capture_output=True, text=True, timeout=TIMEOUT_CONFIG['subprocess_default'],
                encoding="utf-8", errors="replace"
            )
            _changed = [f for f in (_r.stdout or "").split('\n') if f.endswith('.py')]
            _r2 = subprocess.run(
                ["git", "status", "--porcelain"],
                check=False, cwd=self._project_root, capture_output=True, text=True, timeout=TIMEOUT_CONFIG['subprocess_default'],
                encoding="utf-8", errors="replace"
            )
            for _line in (_r2.stdout or "").split('\n'):
                _parts = _line.strip().split()
                if len(_parts) >= 2 and _parts[1].endswith('.py') and _parts[1] not in _changed:
                    _changed.append(_parts[1])
            return _changed[:limit]
        except Exception:
            return []

    def run_all_checks(self, incremental: bool = True) -> dict[str, Any]:
        """运行全部工具检查，返回按工具聚合的结果。
        incremental=True 时，ruff/bandit 只查变更文件（增量），mypy/compileall 保持全量。
        """
        _changed = self.get_changed_py_files() if incremental else []
        return {
            "compile": self.run_compile_check(),
            "ruff": self.run_ruff_check(_changed or None),
            "mypy": self.run_mypy_check(),
            "bandit": self.run_bandit_check(_changed or None),
        }


# 模块级单例（双检锁）
_runner: ToolingRunner | None = None
_runner_lock = threading.Lock()


def get_tooling_runner(project_root: str = "") -> ToolingRunner:
    """获取 ToolingRunner 单例。"""
    global _runner
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                if not project_root:
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                _runner = ToolingRunner(project_root)
    return _runner


def shutdown_tooling_runner() -> None:
    """★P0批次3：复位 ToolingRunner 单例，满足器官零状态（规则4）。

    原停机流程未清理该全局单例，重启时会复用带残留运行缓存的旧实例。
    此处显式置空，使下次获取重建全新零状态实例。
    """
    global _runner
    _inst = _runner
    _runner = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception:
                pass