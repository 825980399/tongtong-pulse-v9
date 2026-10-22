# -*- coding: utf-8 -*-
"""
tooling_runner.py —— 工具运行器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 外部工具调用与执行管理
机制: 基于ToolingRunner类实现，包含10个核心方法
定位: 工具管理层
"""

import json
import os
import re
import subprocess
import threading
from typing import Any

from config import TIMEOUT_CONFIG
from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底


#: ★342刀2：内建默认扫描域（`config.TOOL_SCAN_ROOTS` 缺失/为空时用）。
#: 与「全库 `.`」的区别只在**不进入** data/ tmp/ docs/ docker/ models/ logs/ tests/ ——
#: 这些目录里既有 GB 级产物，也是 Ruff 在仓库根 panic 的触发面（343 实证：根 NUL 文件）。
_TOOL_SCAN_ROOTS_DEFAULT = (
    "main.py", "config.py", "pulse_doctor.py",
    "nucleus", "organs", "functions", "base", "pulses", "somatics", "utils", "tools",
)


class ToolingRunner(SilentLogMixin):
    """调度外部代码检查工具，返回结构化问题列表。"""

    def __init__(self, project_root: str):
        self._project_root = project_root
        # ★342刀2：每通道记账槽。`{tool: {degraded, reason, rc, stdout_bytes,
        #   stderr_bytes, targets, domain, ...}}` —— 用它把「本通道 0 条」与
        #   「本通道没跑到」分开（340刀5 的归零点就是这两件事被读成同一件事）。
        self._tool_flags: dict[str, dict[str, Any]] = {}

    def _run_cmd_split(self, cmd: list[str], timeout: int = 180) -> dict[str, Any]:
        """★342刀2：**分开**返回 stdout / stderr / 返回码（失败不抛异常，但要记账）。

        为什么必须有这个「分开」：`_run_cmd` 把两股流拼成一个串，任何拿它去
        `json.loads` 的调用方，只要工具往 stderr 写一个字节就整串报废
        —— 而 ruff / bandit 的 JSON 只在 stdout。340刀5 实测的「通道静默归零」
        就是这条：`.ruff_cache` 的 package-root 警告 / 全库扫描的 Ruff panic
        都写 stderr ⇒ `JSONDecodeError` ⇒ `return []` ⇒ 看起来像「本仓零问题」。

        Returns:
            `{cmd, stdout, stderr, rc, ok, err}`；执行失败时 `ok=False` 且 `err` 非空。
        """
        _rec: dict[str, Any] = {"cmd": list(cmd), "stdout": "", "stderr": "",
                                "rc": None, "ok": False, "err": ""}
        try:
            _r = subprocess.run(
                cmd, check=False, cwd=self._project_root, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace"
            )
            _rec["stdout"] = _r.stdout or ""
            _rec["stderr"] = _r.stderr or ""
            _rec["rc"] = _r.returncode
            _rec["ok"] = True
        except Exception as _e:  # noqa: BLE001 - 执行失败必须显式记账（不静默归一为「0 条」）
            _rec["err"] = f"{type(_e).__name__}: {_e}"
            self._log(LogLevel.WARNING,
                      f"[342tool] 命令执行失败 cmd={' '.join(cmd)} err={_rec['err']}")
        return _rec

    def _run_cmd(self, cmd: list[str], timeout: int = 180) -> str:
        """运行命令并返回 stdout+stderr 拼接串（失败不抛异常）。

        ★342刀2：保留本方法只为**非 JSON 解析**的调用方（compileall / mypy 走正则）。
        需要 `json.loads` 的一律改用 `_run_cmd_split` —— 拼接串里 stderr 的位置
        没有保证，拿它解析等于赌工具不写 stderr。
        """
        _r = self._run_cmd_split(cmd, timeout)
        if _r.get("err"):
            return f"[工具执行失败] {' '.join(cmd)}: {_r['err']}"
        return (_r.get("stdout") or "") + "\n" + (_r.get("stderr") or "")

    def _scan_targets(self, files: list[str] | None) -> tuple[list[str], str]:
        """★342刀2：解析本次扫描域，返回 `(targets, domain)`。

        `domain ∈ {"explicit","config","default","fallback_root"}`，写进 `_tool_flags`。
        显式传 files ⇒ 尊重调用方；否则读 `config.TOOL_SCAN_ROOTS`，缺/空用内建默认域。
        **绝不默认扫全库**：全库扫会进 data/（GB 级产物）并触发 Ruff 在仓库根 panic。
        """
        if files:
            return list(files), "explicit"
        _roots = None
        try:
            import config as _cfg342
            _roots = getattr(_cfg342, "TOOL_SCAN_ROOTS", None)
        except Exception as _e342:  # noqa: BLE001 - 配置读不到要留痕，不能静默换域
            self._log(LogLevel.WARNING,
                      f"[342tool] 读 config.TOOL_SCAN_ROOTS 失败，改用内建默认域: "
                      f"{type(_e342).__name__}: {_e342}")
        _src = "config"
        if not _roots:
            _roots = _TOOL_SCAN_ROOTS_DEFAULT
            _src = "default"
        _t = [str(r) for r in _roots if os.path.exists(os.path.join(self._project_root, str(r)))]
        if not _t:
            self._log(LogLevel.WARNING,
                      "[342tool] 配置的扫描域一个都不存在，退回项目根"
                      "（可能触发 Ruff panic，已记账 domain=fallback_root）")
            return [self._project_root], "fallback_root"
        return _t, _src

    def _tool_json(self, tool: str, rec: dict[str, Any],
                   targets: list[str], domain: str):
        """★342刀2：解析一个工具的 JSON 输出，**无条件记账**后返回 `_data` 或 None。

        「没解析出来」和「解析出来是 0 条」在这里就已经分开了：
        前者 `_tool_flags[tool].degraded=True` + `reason`，后者 `degraded=False`。
        调用方拿到 None 一律当「本通道不可用」，**不得**读成「本仓零问题」。
        """
        _out = rec.get("stdout") or ""
        _err = rec.get("stderr") or ""
        _flag: dict[str, Any] = {
            "degraded": False, "reason": "", "rc": rec.get("rc"),
            "stdout_bytes": len(_out), "stderr_bytes": len(_err),
            "targets": list(targets), "domain": domain,
        }
        if _err.strip():
            # stderr **单独留痕、不参与解析** —— 这正是 340刀5 的归零点所在。
            _flag["stderr_head"] = _err.strip()[:200]
            self._log(LogLevel.WARNING,
                      f"[342tool] {tool} stderr 非空（{len(_err)} 字节，已隔离不参与解析）: "
                      f"{_err.strip()[:300]}")
        if rec.get("err"):
            _flag["degraded"] = True
            _flag["reason"] = "exec_failed"
            _flag["exec_err"] = rec.get("err", "")
            self._tool_flags[tool] = _flag
            self._log(LogLevel.ERROR,
                      f"[342tool] {tool} 通道不可用（执行失败）⇒ 本通道 0 条，已记账")
            return None
        if not _out.strip():
            _flag["degraded"] = True
            _flag["reason"] = "empty_stdout"
            self._tool_flags[tool] = _flag
            self._log(LogLevel.WARNING,
                      f"[342tool] {tool} stdout 为空（rc={rec.get('rc')} domain={domain}）"
                      "⇒ 本通道 0 条，已记账")
            return None
        try:
            _data = json.loads(_out)
        except ValueError as _je:  # json.JSONDecodeError 是 ValueError 子类
            _flag["degraded"] = True
            _flag["reason"] = "json_decode"
            _flag["parse_error"] = f"{type(_je).__name__}: {_je}"
            self._tool_flags[tool] = _flag
            self._log(LogLevel.ERROR,
                      f"[342tool] {tool} stdout 解析失败（rc={rec.get('rc')} "
                      f"stdout={len(_out)}B stderr={len(_err)}B domain={domain}）"
                      f"⇒ 本通道 0 条，已记账: {type(_je).__name__}: {_je}")
            return None
        # ruff / bandit 的 rc=1 表示「检查到问题」，不是失败；其余非零才记降级。
        if rec.get("rc") not in (0, 1, None):
            _flag["degraded"] = True
            _flag["reason"] = f"rc_{rec.get('rc')}"
        self._tool_flags[tool] = _flag
        _cnt = len(_data) if isinstance(_data, list) else "obj"
        self._log(LogLevel.INFO,
                  f"[342tool] {tool} ok items={_cnt} rc={rec.get('rc')} "
                  f"stdout={len(_out)}B stderr={len(_err)}B domain={domain} "
                  f"degraded={_flag['degraded']}")
        return _data

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

    # ★340刀5 [340排查] 定位 → ★342刀2 已修（D-3 裁决）：
    #   原实现 `_run_cmd` 返回 **stdout + "\n" + stderr** 拼接串，而下面两个函数对
    #   **整串** `json.loads(...)` ⇒ 工具只要往 stderr 写一个字节（`.ruff_cache` 的
    #   package-root 警告 / 全库扫描的 Ruff panic）就 JSONDecodeError ⇒ `except`
    #   ⇒ `return []` ⇒ **整条通道静默归零**（无日志、无记账）。这是 per-item
    #   `item_count` 在 947 / 661 两档间跳变（24%）的成因 —— 规则通道恒 661 零波动。
    #   341 刀2 的改法：解析只吃 stdout（`_run_cmd_split`）／降级无条件记账
    #   （`_tool_flags` + `[342tool]` 行）／固定扫描域（`_scan_targets`）。
    def run_ruff_check(self, files: list[str] | None = None) -> list[dict[str, Any]]:
        """规范检查（ruff）。files 为空时用**固定扫描域**（不再扫全库），否则只查变更文件。"""
        issues = []
        try:
            _targets, _domain = self._scan_targets(files)
            # ★342刀2：`--no-cache` 不是性能选项，是**正确性**选项。
            #   实测：即便把扫描域限到代码面，ruff 仍会 rc=2 并在 stderr 打
            #   `error: Panic during linting` + `Different package root in cache:
            #   expected ... got '        '` —— 脏的是 .ruff_cache 里的 package-root
            #   记录（疑似 343 的根目录 NUL 文件污染），不是扫描域。
            #   加 `--no-cache` 后：rc=1、stderr 0 字节、1346 条稳定产出。
            #   一次性通道本来就用不上跨进程缓存，关掉它换确定性。
            _rec = self._run_cmd_split([
                "python", "-m", "ruff", "check", "--no-cache",
                "--output-format", "json",
                "--exclude", ".venv,__pycache__",
                *_targets,
            ])
            _data = self._tool_json("ruff", _rec, _targets, _domain)
            if _data is None:
                return issues
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
        """安全扫描（bandit）。files 为空时用**固定扫描域**，否则只查变更文件。"""
        issues = []
        try:
            _targets, _domain = self._scan_targets(files)
            _rec = self._run_cmd_split([
                "python", "-m", "bandit", "-r", "-q", "-f", "json",
                "-x", ".venv,__pycache__",
                *_targets,
            ])
            _data = self._tool_json("bandit", _rec, _targets, _domain)
            if _data is None:
                return issues
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
        # ★342刀2：每轮清账 —— 记账只描述**本轮**，累积会把上一轮的降级读成本轮的。
        self._tool_flags = {}
        _res = {
            "compile": self.run_compile_check(),
            "ruff": self.run_ruff_check(_changed or None),
            "mypy": self.run_mypy_check(),
            "bandit": self.run_bandit_check(_changed or None),
        }
        # ★342刀2：通道记账随结果一起出去，下游能判「0 条」还是「没跑到」。
        _res["tool_flags"] = dict(self._tool_flags)
        return _res


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
            except Exception as e:
                silent_exc(e, where="nucleus.tooling_runner::shutdown_tooling_runner L213")
