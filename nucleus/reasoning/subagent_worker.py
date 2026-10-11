# -*- coding: utf-8 -*-
"""186批刀2：SUBAGENT worker 最小实装（零停窗）。

定位：
  - 子代理 runner（nucleus/reasoning/subagent_runner.py，183批刀1）在「真起子进程」
    （刀B共窗实证）时通过以下命令调起的 worker 本体：
        python -m nucleus.reasoning.subagent_worker <input.json> <output.json> [stderr.log]
  - 读 input json（task/budget/timeout）→ 单次调用（DETERMINISTIC 路径，不调 LLM）
    → 写 output json（result/exit_code）→ exit code 语义（0=成功 / 非0=失败）。

★不做（任务书明确）：
  - 不真起子进程：本文件就是被 runner 真起的 worker 本体；本刀只就位它，是否真起推刀B共窗；
  - 不翻开关：默认关——仅当 runner 在 ENABLE_SUBAGENT_RUNNER=True 且共窗验证后 dry_run=False
    才会调起本 worker，故本文件存在对系统「零行为变化」；
  - 不调 LLM：worker 只走 DETERMINISTIC 路径（纯函数计算 / 确定性 ack），无任何模型调用。

输入约定（与 subagent_runner 对齐）：
  input  : {"task": <dict|str>, "budget_tokens": <int>, "timeout": <seconds>, "model": <str>, ...}
            （runner 将 task 规格展开合并到顶层，故 task 缺省时取整个 spec）
输出约定：
  output : {"result": <any>, "exit_code": <int>}
  exit code: 0=成功；非0=失败（约定 2=输入读取/解析失败）。

确定性操作（task.op 可选）：
  echo / sum / hash / noop —— 均为纯函数，无副作用、无 IO（除 input/output 文件）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from typing import Any, Tuple

from nucleus._silent_except import silent_exc

_MODULE_TAG = "subagent_worker"


def _open_stderr_log(stderr_file: "str | None"):
    """打开 stderr 诊断日志（可选）；返回可写句柄或 None。"""
    if not stderr_file:
        return None
    try:
        return open(stderr_file, "a", encoding="utf-8")
    except OSError as exc:
        silent_exc(exc, where="_open_stderr_log")
        return None


def run_once(spec: Any) -> "Tuple[dict, int]":
    """单次确定性调用（DETERMINISTIC 路径，不调 LLM）。

    Args:
        spec: 输入 JSON 反序列化后的对象（dict / str / 其它）。
    Returns:
        (result_dict, exit_code)
        - exit_code=0 表示确定性处理成功；
        - 仅当 spec 非法（非 dict 且无 type 可判定）时仍返回 0（确定性 ack），
          真正的输入读取失败由 main 层以 exit_code=2 处理。
    """
    budget = spec.get("budget_tokens") if isinstance(spec, dict) else None
    timeout = spec.get("timeout") if isinstance(spec, dict) else None
    model = spec.get("model") if isinstance(spec, dict) else None

    result: dict[str, Any] = {"deterministic": True, "model": model}

    if not isinstance(spec, dict):
        # 非 dict 输入：确定性回显其类型
        result["handled"] = True
        result["task_type"] = type(spec).__name__
        result["budget_tokens"] = budget
        result["timeout"] = timeout
        return result, 0

    task = spec.get("task", spec)
    op = task.get("op") if isinstance(task, dict) else None

    if isinstance(task, dict):
        if op == "echo":
            result["echo"] = task.get("payload")
        elif op == "sum":
            _nums = task.get("nums", [])
            try:
                result["sum"] = sum(int(x) for x in _nums)
            except (TypeError, ValueError) as exc:
                silent_exc(exc, where="run_once.sum")
                result["sum"] = None
        elif op == "hash":
            result["sha256"] = hashlib.sha256(
                str(task.get("data", "")).encode("utf-8")
            ).hexdigest()
        elif op == "noop":
            result["handled"] = True
        else:
            # 未知 op / 无 op：确定性 ack（不调 LLM，仅登记任务键集合）
            result["handled"] = True
            result["task_keys"] = sorted(task.keys())
    elif isinstance(task, str):
        result["echo"] = task
    else:
        result["handled"] = True

    result["budget_tokens"] = budget
    result["timeout"] = timeout
    return result, 0


def _write_output(output_file: str, result: dict, exit_code: int) -> None:
    """安全写入 output json（先写临时文件再重命名，避免半写）。"""
    payload = {"result": result, "exit_code": exit_code}
    tmp = output_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    import os
    os.replace(tmp, output_file)


def main(argv: "list[str] | None" = None) -> int:
    """CLI 入口：读 input → run_once → 写 output → 返回 exit_code。

    约定 argv = [input_file, output_file, stderr_file?]
    """
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        sys.stderr.write(
            "usage: subagent_worker <input.json> <output.json> [stderr.log]\n"
        )
        return 2

    input_file, output_file = argv[0], argv[1]
    stderr_file = argv[2] if len(argv) > 2 else None
    _log = _open_stderr_log(stderr_file)

    def _log_line(msg: str) -> None:
        if _log is not None:
            _log.write(msg + "\n")
            _log.flush()

    try:
        with open(input_file, encoding="utf-8") as f:
            spec = json.load(f)
    except Exception as exc:  # noqa: BLE001 - 输入失败归一为 exit_code=2，写 output 后退出
        _log_line(f"[{_MODULE_TAG}] input read failed: {type(exc).__name__}: {exc}")
        _write_output(output_file, {
            "error": f"input read failed: {type(exc).__name__}: {exc}",
            "deterministic": True,
        }, 2)
        if _log is not None:
            _log.close()
        return 2

    result, exit_code = run_once(spec)
    _log_line(f"[{_MODULE_TAG}] deterministic run done: exit_code={exit_code}")
    _write_output(output_file, result, exit_code)
    if _log is not None:
        _log.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
