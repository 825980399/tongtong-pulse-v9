# -*- coding: utf-8 -*-
"""tools/pytest_shard —— 分片 pytest 运行 / 结果解析工具（主线第56批 T4/P2-392）

第55批缺陷（已被门禁拦截的误读）：``tmp/check_m55_pytest.py`` 把
collection / 超时 / 中断等**异常**计成 ``0 passed / 0 failed``，而调用方只凭
``failed`` 数判成败 → 被误读为"通过"（实证：片3 单独跑 114 passed，分片脚本却报
``0/0``）。

★本工具修复（方案B + 方案C）：
  * 方案B：显式检查 pytest **exit code**（0=通过 / 1=失败 / 2=中断 / 3=内部错误 /
    4=命令行错误 / 5=无测试收集），不再只看 passed/failed 数字。
  * 方案C：对 ``passed+failed == 0 且 文件数 > 0`` 的情况**显式报警**（alarm），
    绝不误读为通过。
  * 额外：扫描输出中的 pytest 错误关键词（ERROR collecting / INTERNALERROR /
    UsageError 等），兜底识别 collection 错误。

API
---
* classify_exit_code(code) -> str
* parse_summary(output) -> {"passed","failed","error"}
* evaluate_shard(output, exit_code, file_count) -> dict   （核心判定）
* run_shard(pytest_bin, files, timeout, cwd) -> dict       （运行+判定）

超时约定：``run_shard`` 捕获 ``subprocess.TimeoutExpired`` → ``exit_code=-1``（status="timeout"）。
"""
from __future__ import annotations

import re
import subprocess

__all__ = [
    "classify_exit_code", "parse_summary", "evaluate_shard", "run_shard",
]

# pytest exit code 语义（见 pytest 文档）
EXIT_CODE_MEANING = {
    0: "pass",
    1: "fail",
    2: "interrupt",
    3: "internal_error",
    4: "cmdline_error",
    5: "no_tests_collected",
}

# 输出中含这些关键词 → 视为 pytest 报错（兜底，独立于 exit code）
ERROR_KEYWORDS = (
    "ERROR collecting",
    "INTERNALERROR",
    "UsageError",
    "collection error",
    "pytest: error:",
    "ERROR: ",
)


def classify_exit_code(code: int) -> str:
    """把 pytest exit code 映射为可读语义；未知码返回 'unknown'。"""
    return EXIT_CODE_MEANING.get(code, "unknown")


def parse_summary(output: str) -> dict:
    """★从 pytest 文本输出解析 passed/failed，并检测错误关键词。

    注意：collection 错误 / 内部错误时 pytest **不会**打印 ``X passed``，
    此时 passed/failed 均为 0 —— 这正是第55批被误读的根因。
    """
    _passed = _failed = 0
    _error = False
    for _line in (output or "").splitlines():
        if " passed" in _line:
            _m = re.search(r"(\d+) passed", _line)
            if _m:
                _passed = int(_m.group(1))
        if " failed" in _line:
            _m = re.search(r"(\d+) failed", _line)
            if _m:
                _failed = int(_m.group(1))
        if any(_k in _line for _k in ERROR_KEYWORDS):
            _error = True
    return {"passed": _passed, "failed": _failed, "error": _error}


def evaluate_shard(output: str, exit_code: int, file_count: int) -> dict:
    """★核心修复：综合 exit code + 解析结果 + 文件数，判定分片状态。

    Returns:
        dict: passed / failed / status / alarm / exit_code / file_count / reason

    status 取值：
        pass             —— exit 0 且 passed+failed>0 且无错误关键词
        fail             —— failed>0 或 exit==1
        error            —— 含错误关键词，或 exit ∈ {2,3,4}
        timeout          —— exit_code==-1（超时约定）
        no_tests_collected—— exit==5（异常：文件数>0 却无测试）
        suspicious       —— passed+failed==0 且 file_count>0（★显式报警，误读防护）
        unknown          —— 其他（极少）
    alarm=True 当且仅当 ``passed+failed==0 且 file_count>0``（异常却看似"通过"）。
    """
    _s = parse_summary(output)
    _passed, _failed, _error = _s["passed"], _s["failed"], _s["error"]
    _alarm = (_passed + _failed == 0 and file_count > 0)

    if exit_code == -1:
        _status = "timeout"
    elif _error or exit_code in (2, 3, 4):
        _status = "error"
    elif _failed > 0 or exit_code == 1:
        _status = "fail"
    elif exit_code == 5:
        _status = "no_tests_collected"
    elif exit_code == 0 and (_passed + _failed) > 0 and not _error:
        _status = "pass"
    elif file_count == 0:
        _status = "pass"  # 无文件可跑 → 无失败（区别于 exit==5 的"有文件却无测试"）
    else:
        _status = "unknown"

    # ★方案C：passed+failed==0 但确实有文件 → 绝不误读为通过
    if _alarm and _status in ("pass", "unknown"):
        _status = "suspicious"

    _reason = []
    if _alarm:
        _reason.append("passed+failed==0 但文件数>0，疑似 collection 错误/超时/中断")
    if _error:
        _reason.append("输出含 pytest 错误关键词")
    if exit_code in (2, 3, 4):
        _reason.append(f"pytest exit_code={exit_code}（{classify_exit_code(exit_code)}）")
    return {
        "passed": _passed,
        "failed": _failed,
        "status": _status,
        "alarm": _alarm,
        "exit_code": exit_code,
        "file_count": file_count,
        "reason": "; ".join(_reason),
    }


def run_shard(pytest_bin: str, files: list, timeout: int = 1800,
              cwd: str | None = None) -> dict:
    """★运行单个分片并返回 evaluate_shard 结果。

    pytest_bin: pytest 可执行路径；files: 测试路径列表；
    超时（``subprocess.TimeoutExpired``）→ ``exit_code=-1``（status="timeout"）。
    """
    if not files:
        return evaluate_shard("", 0, 0)
    _cmd = [pytest_bin, "-q", "--tb=line", "-p", "no:cacheprovider"] + list(files)
    try:
        _p = subprocess.run(
            _cmd, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
        _out = (_p.stdout or "") + (_p.stderr or "")
        _code = _p.returncode
    except subprocess.TimeoutExpired:
        return evaluate_shard("TIMEOUT", -1, len(files))
    return evaluate_shard(_out, _code, len(files))


if __name__ == "__main__":  # pragma: no cover - 自演示
    import os
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _demo_files = [os.path.join("tests", "test_does_not_exist_xyz.py")]
    _r = run_shard(r"D:\Program Files\Python312\Scripts\pytest.exe",
                   _demo_files, cwd=_root)
    print("demo evaluate (不存在的文件) =>", _r)
