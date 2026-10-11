# -*- coding: utf-8 -*-
"""subagent_runner.py —— SUBAGENT 子代理 runner 封装（183批刀1 · 协议刀A）

职责：封装"真起子代理"的安全边界协议，供后续共窗实证（刀B）调用。
设计依据：烛微183前置 §2.4 刀A + 任务书硬条款。

硬条款（立法禁止既有"有意不检查exit code"模式）：
  ① 强制检查子进程退出码——非零退出 → 记录 + 弃结果，绝不静默忽略。
  ② NO_MESSAGE_TOOL 闸先于真起翻开——子代理不得直发 Mouth/SPEAK；
     未翻开 ENABLE_SUBAGENT_NO_MESSAGE_TOOL 则拒绝真起。
  ③ 预算校验——SUBAGENT_CHEAP_MODEL 非空强校验（禁用最贵主模型直连）
     + 单次调用预算上限 SUBAGENT_BUDGET_TOKENS。
  ④ timeout 清理——subprocess.run(timeout=...) 超时后强制清理子进程。

零行为变化：本模块仅就位协议与封装；默认 dry_run（不真起子进程）。
调用方在 ENABLE_SUBAGENT_RUNNER 翻为 True 且共窗验证后，方可 dry_run=False 真起。
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time

_L = logging.getLogger("subagent_runner")

# 子进程入口模块（复用 evolution_worker 模式：subprocess + JSON 文件 in/out）。
# 真起推刀B共窗实证；本刀 dry_run 默认不真起，故该 worker 是否就绪不影响就位。
_SUBAGENT_ENTRY = "nucleus.reasoning.subagent_worker"


class SubagentRunRefused(Exception):
    """真起被协议闸拒绝（NO_MESSAGE_TOOL 未翻开 / 预算校验失败）。"""


class SubagentRunResult:
    """一次子代理运行的归一化结果。"""

    __slots__ = ("status", "returncode", "error", "stats", "duration_ms", "pid")

    def __init__(self, status, returncode=None, error=None, stats=None,
                 duration_ms=0.0, pid=None):
        self.status = status          # success | failure | refused | timeout | budget_exceeded
        self.returncode = returncode
        self.error = error
        self.stats = stats or {}
        self.duration_ms = duration_ms
        self.pid = pid

    def to_dict(self):
        return {
            "status": self.status,
            "returncode": self.returncode,
            "error": self.error,
            "stats": self.stats,
            "duration_ms": self.duration_ms,
            "pid": self.pid,
        }


def _get_cfg():
    import config as _cfg
    return _cfg


def _read_json(path):
    """读取 JSON 结果文件；缺失/损坏则抛出，由调用方外层 except 归一为 failure。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    """安全写入 JSON（先写临时文件再重命名，避免半写状态）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _ms(started):
    return round((time.time() - started) * 1000, 1)


def _cleanup_on_timeout(exc):
    """硬条款④：超时后强制清理子进程，避免僵尸。"""
    proc = getattr(exc, "proc", None)
    if proc is not None:
        with contextlib.suppress(Exception):
            proc.kill()
            proc.wait(timeout=5)


def run_subagent(task, *, cfg=None, input_file=None, output_file=None,
                 stderr_file=None, timeout=None, dry_run=None):
    """执行一次子代理任务（封装安全边界协议）。

    Args:
        task: 任务规格 dict（含 prompt / budget_tokens 等）。
        cfg: 配置对象（默认 import config）；单测可注入 mock。
        input_file/output_file/stderr_file: 文件协议路径（默认临时文件）。
        timeout: 超时秒；默认取 cfg.SUBAGENT_TIMEOUT_SECONDS。
        dry_run: None 时由 ENABLE_SUBAGENT_RUNNER 决定（默认关 → True 不真起）；
                 显式传 False 用于共窗实证。
    Returns:
        SubagentRunResult
    """
    cfg = cfg or _get_cfg()
    if dry_run is None:
        dry_run = not getattr(cfg, "ENABLE_SUBAGENT_RUNNER", False)
    started = time.time()

    # —— 硬条款②：NO_MESSAGE_TOOL 闸先于真起翻开 ——
    if not getattr(cfg, "ENABLE_SUBAGENT_NO_MESSAGE_TOOL", False):
        _L.warning("[subagent_runner] 拒绝真起：ENABLE_SUBAGENT_NO_MESSAGE_TOOL 未翻开"
                   "（子代理不得直发 Mouth/SPEAK）")
        return SubagentRunResult("refused",
                                 error="NO_MESSAGE_TOOL gate not enabled",
                                 duration_ms=_ms(started))

    # —— 硬条款③：预算校验 ——
    cheap = getattr(cfg, "SUBAGENT_CHEAP_MODEL", "")
    if not cheap:
        _L.warning("[subagent_runner] 拒绝真起：SUBAGENT_CHEAP_MODEL 为空"
                   "（禁止子代理直连最贵主模型）")
        return SubagentRunResult("budget_exceeded",
                                 error="SUBAGENT_CHEAP_MODEL empty (main model forbidden for subagent)",
                                 duration_ms=_ms(started))
    budget = getattr(cfg, "SUBAGENT_BUDGET_TOKENS", 0)
    if budget:
        task_budget = int(task.get("budget_tokens", budget))
        if task_budget > budget:
            _L.warning("[subagent_runner] 拒绝真起：任务预算 %d 超过上限 %d",
                       task_budget, budget)
            return SubagentRunResult("budget_exceeded",
                                     error="task budget exceeds SUBAGENT_BUDGET_TOKENS",
                                     duration_ms=_ms(started))

    # —— dry_run：协议闸与预算校验通过，但不真起（默认，符合"本刀不真起"）——
    if dry_run:
        return SubagentRunResult("success", returncode=0,
                                 stats={"dry_run": True}, duration_ms=_ms(started))

    # —— 真起子进程（仅共窗实证阶段 dry_run=False）——
    if input_file is None:
        input_file = tempfile.mktemp(suffix=".json", prefix="subagent_in_")
    if output_file is None:
        output_file = tempfile.mktemp(suffix=".json", prefix="subagent_out_")
    if stderr_file is None:
        stderr_file = tempfile.mktemp(suffix=".log", prefix="subagent_err_")
    _write_json(input_file, {**task, "model": cheap,
                             "budget_tokens": int(task.get("budget_tokens", budget))})

    if timeout is None:
        timeout = getattr(cfg, "SUBAGENT_TIMEOUT_SECONDS", 300)

    try:
        proc = subprocess.run(
            [sys.executable, "-m", _SUBAGENT_ENTRY, input_file, output_file, stderr_file],
            capture_output=True, text=True, timeout=timeout,
        )
        # —— 硬条款①：强制检查退出码（立法禁止"不检查exit code"）——
        if proc.returncode != 0:
            _L.error("[subagent_runner] 子代理非零退出 code=%s，弃结果", proc.returncode)
            return SubagentRunResult("failure", returncode=proc.returncode,
                                    error=f"subagent exited with code {proc.returncode}",
                                    duration_ms=_ms(started), pid=proc.pid)
        result = _read_json(output_file)
        return SubagentRunResult("success", returncode=0,
                                 stats=result.get("stats", {}),
                                 duration_ms=_ms(started), pid=proc.pid)
    except subprocess.TimeoutExpired as exc:
        # —— 硬条款④：timeout 清理 ——
        _cleanup_on_timeout(exc)
        _L.error("[subagent_runner] 子代理超时（%ss），已清理", timeout)
        return SubagentRunResult("timeout", error="subagent exceeded timeout",
                                 duration_ms=_ms(started))
    except Exception as exc:  # noqa: BLE001 - 归一为 failure，绝不静默忽略
        _L.error("[subagent_runner] 子代理运行异常: %s: %s",
                 type(exc).__name__, exc)
        return SubagentRunResult("failure",
                                 error=f"{type(exc).__name__}: {exc}",
                                 duration_ms=_ms(started))
