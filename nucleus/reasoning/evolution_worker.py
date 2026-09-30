# -*- coding: utf-8 -*-
"""
evolution_worker.py —— 进化工作器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 进化任务的工作进程执行
机制: 函数式模块，包含2个工具函数
定位: 进化执行层
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from nucleus._silent_except import silent_exc

# ★主线第15批 T3/P1-93：本模块是 **spawn 子进程的入口模块**，顶层一律只留
#   stdlib 依赖。原因：实测 12h 子进程崩溃 109 次且 worker 内部 except 从未执行
#   （`evolution_worker.py:` 打印出现 0 次），说明崩溃发生在 worker 的 try 之外
#   ——概率最高的就是 spawn 期导入本模块时失败。把非 stdlib 导入改为惰性，
#   保证本模块在任何环境下都能被 import，从而有资格报告自己的失败原因。



def run_evolution_worker(input_file: str, output_file: str) -> None:
    """子进程入口：独立初始化，执行自主迭代，写入结果文件。

    此函数必须是模块级可导入的（Windows multiprocessing spawn模式要求）。
    """
    # ---- 1. 环境初始化 ----
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.chdir(project_root)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    result: dict = {
        "status": "error",
        "error": "",
        "stats": {},
        "started_at": time.time(),
        "finished_at": 0.0,
        "duration_ms": 0.0,
        "pid": os.getpid(),
    }

    try:
        # ---- 2. 读取输入 ----
        if not os.path.exists(input_file):
            result["error"] = f"输入文件不存在: {input_file}"
            _write_output(output_file, result)
            return

        input_data = _read_json(input_file, default={})

        mode = input_data.get("mode", "repair")
        issues = input_data.get("issues", [])
        max_issues = input_data.get("max_issues", 5)
        plans = input_data.get("plans", [])
        max_plans = input_data.get("max_plans", 3)

        if not issues and mode == "repair":
            result["status"] = "success"
            result["stats"] = {"skipped": "no_issues"}
            result["finished_at"] = time.time()
            result["duration_ms"] = round((result["finished_at"] - result["started_at"]) * 1000, 1)
            _write_output(output_file, result)
            return

        # ---- 3. 独立初始化组件（子进程内存空间，与主进程完全隔离）----
        from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
        from nucleus.self_inspector import get_self_inspector

        inspector = get_self_inspector()
        executor = SafeEvolutionExecutor()

        # ---- 4. 执行自主迭代 ----
        if mode == "repair":
            # 限制处理数量，避免子进程运行过久
            _issues = issues[:max_issues]
            stats = executor.repair_with_distillation(_issues, inspector)
        elif mode == "review":
            stats = executor.run_runtime_guided_review(inspector)
        elif mode == "execute":
            # 为推演方案生成可执行补丁
            _plans = plans[:max_plans] if plans else []
            stats = executor.execute(_plans, inspector)
        else:
            stats = {"error": f"unknown mode: {mode}"}

        result["status"] = "success"
        result["stats"] = stats

    except Exception as e:
        print(f"[WARNING] evolution_worker.py:86: {type(e).__name__}: {e}")
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()[-2000:]  # 截断避免输出过大

    # ---- 5. 写入结果 ----
    result["finished_at"] = time.time()
    result["duration_ms"] = round((result["finished_at"] - result["started_at"]) * 1000, 1)
    _write_output(output_file, result)


def _read_json(path: str, default: dict | None = None) -> dict:
    """读取 JSON：优先走框架统一数据访问层，不可用时退回 stdlib json。

    ★T3：惰性导入 safe_read_json，避免它成为子进程的导入期故障点。
    """
    _dflt = default if default is not None else {}
    try:
        from nucleus.data.DataAccessLayer import safe_read_json
        return safe_read_json(path, default=_dflt)
    except Exception:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return _dflt


def run_evolution_worker_captured(input_file: str, output_file: str,
                                  stderr_file: str = "") -> None:
    """★主线第15批 T3/P1-93：子进程入口包装（spawn 目标）。

    背景：崩溃只有 exitcode、没有原因。本包装保证「子进程自己的失败由自己说清楚」：
      1) 第一时间把子进程 stderr 重定向到 stderr_file（父进程在崩溃后读取）；
      2) worker 抛出的任何 BaseException 都写入 output_file（含 traceback + 阶段）；
      3) 原样重新抛出，保持退出码语义不变。

    Args:
        input_file: 输入 JSON 路径。
        output_file: 结果 JSON 路径（崩溃时写崩溃载荷）。
        stderr_file: 子进程 stderr 落盘路径（可空）。
    """
    _stderr_fp = None
    if stderr_file:
        try:
            # ★主线第30批 T4：此处**有意不使用 with** —— 该句柄被赋给 `sys.stderr`，
            #   需保持到子进程退出才能持续捕获崩溃栈；改为 with 会提前关闭而丢失日志。
            _stderr_fp = open(stderr_file, "w", encoding="utf-8", buffering=1)  # noqa: SIM115 - 见上
            sys.stderr = _stderr_fp
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.evolution_worker::run_evolution_worker_captured L147")
            _stderr_fp = None
    try:
        run_evolution_worker(input_file, output_file)
    except BaseException as e:  # 诊断层必须兜住一切异常
        try:
            traceback.print_exc()
        except Exception as _e2:
            print(f"[evolution_worker] traceback 输出失败: {type(_e2).__name__}: {_e2}")
        try:
            _write_output(output_file, {
                "status": "crashed",
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-4000:],
                "phase": "worker_entry",
                "pid": os.getpid(),
            })
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.evolution_worker::run_evolution_worker_captured L164")
        raise
    finally:
        if _stderr_fp is not None:
            try:
                _stderr_fp.flush()
                _stderr_fp.close()
            except Exception as e:
                silent_exc(e, where="nucleus.reasoning.evolution_worker::run_evolution_worker_captured L172")


def _write_output(output_file: str, result: dict) -> None:
    """安全写入结果文件（先写临时文件再重命名，避免半写状态）。"""
    tmp_file = output_file + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, output_file)
    except Exception:
        # 兜底：直接写
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.evolution_worker::_write_output L188")


if __name__ == "__main__":
    # 命令行直接调用：python evolution_worker.py input.json output.json
    if len(sys.argv) >= 3:
        run_evolution_worker(sys.argv[1], sys.argv[2])
    else:
        print("Usage: python evolution_worker.py <input_json> <output_json>")
        sys.exit(1)
