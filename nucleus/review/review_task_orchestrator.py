# -*- coding: utf-8 -*-
"""
TaskOrchestrator.py —— 任务编排器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 复杂任务的编排与执行协调
机制: 基于TaskStatus类实现，包含10个核心方法
定位: 任务调度层
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nucleus.logger import get_module_logger

_logger = get_module_logger("TaskOrchestrator")


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"           # 等待执行
    RUNNING = "running"           # 执行中
    SUCCESS = "success"           # 成功
    FAILED = "failed"             # 失败
    SKIPPED = "skipped"           # 跳过（依赖失败）
    RETRYING = "retrying"         # 重试中
    CANCELLED = "cancelled"       # 已取消


@dataclass
class TaskResult:
    """任务结果"""
    task_id: str = ""
    success: bool = False
    output: Any | None = None
    error: str = ""
    execution_time: float = 0.0
    retry_count: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class Task:
    """任务定义"""
    task_id: str = ""
    name: str = ""
    description: str = ""
    func: Callable | None = None  # 执行函数
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    dependencies: list = field(default_factory=list)  # 依赖的task_id列表
    status: TaskStatus = TaskStatus.PENDING
    result: TaskResult = None
    max_retries: int = 2
    retry_count: int = 0
    timeout: int = 60  # 超时秒数
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    on_success: Callable | None = None  # 成功回调
    on_failure: Callable | None = None  # 失败回调


class TaskOrchestrator:
    """任务闭环编排引擎"""

    def __init__(self, name: str = "default"):
        """
        初始化任务编排器

        Args:
            name: 编排器名称
        """
        self.name = name
        self._tasks: dict = {}  # task_id -> Task
        self._execution_history: list = []
        self._running = False
        _logger.info(f"任务编排器初始化: {name}")

    def add_task(self, task_id: str, name: str, func: Callable,
                 args: tuple = (), kwargs: dict | None = None,
                 dependencies: list | None = None, max_retries: int = 2,
                 timeout: int = 60, description: str = "",
                 on_success: Callable | None = None,
                 on_failure: Callable | None = None) -> str:
        """
        添加任务

        Args:
            task_id: 任务ID（唯一）
            name: 任务名称
            func: 执行函数
            args: 位置参数
            kwargs: 关键字参数
            dependencies: 依赖的task_id列表
            max_retries: 最大重试次数
            timeout: 超时秒数
            description: 任务描述
            on_success: 成功回调
            on_failure: 失败回调

        Returns:
            任务ID
        """
        if task_id in self._tasks:
            _logger.warning(f"任务ID已存在，覆盖: {task_id}")

        task = Task(
            task_id=task_id,
            name=name,
            description=description,
            func=func,
            args=args,
            kwargs=kwargs or {},
            dependencies=dependencies or [],
            max_retries=max_retries,
            timeout=timeout,
            created_at=time.time(),
            on_success=on_success,
            on_failure=on_failure,
        )
        self._tasks[task_id] = task
        _logger.debug(f"添加任务: {task_id} ({name}), 依赖={task.dependencies}")
        return task_id

    def get_task(self, task_id: str) -> Task | None:
        """获取任务"""
        return self._tasks.get(task_id)

    def get_ready_tasks(self) -> list:
        """获取所有可执行的任务（依赖已完成且状态为PENDING）"""
        ready = []
        for task in self._tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            # 检查依赖
            deps_ok = True
            for dep_id in task.dependencies:
                dep = self._tasks.get(dep_id)
                if dep is None or dep.status != TaskStatus.SUCCESS:
                    deps_ok = False
                    break
            if deps_ok:
                ready.append(task)
        return ready

    def _execute_task(self, task: Task) -> TaskResult:
        """执行单个任务"""
        result = TaskResult(task_id=task.task_id)
        start_time = time.time()

        try:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()

            # 执行函数
            output = task.func(*task.args, **task.kwargs)

            result.success = True
            result.output = output
            task.status = TaskStatus.SUCCESS

            # 成功回调
            if task.on_success:
                try:
                    task.on_success(result)
                except Exception as e:
                    _logger.warning(f"任务成功回调异常: {task.task_id} - {e}")

        except Exception as e:
            result.error = str(e)
            result.success = False

            # 重试逻辑
            if task.retry_count < task.max_retries:
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                task.retry_count += 1
                result.retry_count = task.retry_count
                task.status = TaskStatus.RETRYING
                _logger.info(f"任务重试 ({task.retry_count}/{task.max_retries}): {task.task_id} - {e}")
                # 递归重试
                return self._execute_task(task)
            else:
                task.status = TaskStatus.FAILED
                _logger.error(f"任务失败（已重试{task.max_retries}次）: {task.task_id} - {e}")

                # 失败回调
                if task.on_failure:
                    try:
                        task.on_failure(result)
                    except Exception as cb_e:
                        _logger.warning(f"任务失败回调异常: {task.task_id} - {cb_e}")

        finally:
            result.execution_time = time.time() - start_time
            task.completed_at = time.time()
            task.result = result

        return result

    def run(self, parallel: bool = False) -> dict:
        """
        执行所有任务（按依赖顺序）

        Args:
            parallel: 是否并行执行无依赖的任务（暂不支持，预留）

        Returns:
            执行结果汇总
        """
        if self._running:
            _logger.warning("编排器已在运行中")
            return {"error": "already_running"}

        self._running = True
        start_time = time.time()
        results = {}

        try:
            while True:
                ready = self.get_ready_tasks()
                if not ready:
                    break

                for task in ready:
                    _logger.info(f"执行任务: {task.task_id} ({task.name})")
                    result = self._execute_task(task)
                    results[task.task_id] = result

                    # 如果任务失败，标记其下游任务为SKIPPED
                    if not result.success:
                        self._mark_dependents_skipped(task.task_id)

            # 检查是否有未完成的任务（循环依赖或死锁）
            pending = [t for t in self._tasks.values() if t.status == TaskStatus.PENDING]
            if pending:
                _logger.warning(f"有{len(pending)}个任务无法执行（可能循环依赖）: "
                                f"{[t.task_id for t in pending]}")
                for task in pending:
                    task.status = TaskStatus.CANCELLED

        finally:
            self._running = False

        # 汇总结果
        summary = self._generate_summary(results, start_time)
        self._execution_history.append(summary)
        _logger.info(f"任务编排完成: 成功={summary['success_count']}, "
                     f"失败={summary['failed_count']}, 耗时={summary['total_time']:.1f}秒")
        return summary

    def _mark_dependents_skipped(self, failed_task_id: str):
        """标记依赖失败任务的下游任务为SKIPPED"""
        for task in self._tasks.values():
            if failed_task_id in task.dependencies and task.status == TaskStatus.PENDING:
                task.status = TaskStatus.SKIPPED
                _logger.debug(f"任务跳过（依赖失败）: {task.task_id}")
                # 递归标记
                self._mark_dependents_skipped(task.task_id)

    def _generate_summary(self, results: dict, start_time: float) -> dict:
        """生成执行结果汇总"""
        success = [r for r in results.values() if r.success]
        failed = [r for r in results.values() if not r.success]
        skipped = [t for t in self._tasks.values() if t.status == TaskStatus.SKIPPED]

        return {
            "orchestrator": self.name,
            "total_tasks": len(self._tasks),
            "executed": len(results),
            "success_count": len(success),
            "failed_count": len(failed),
            "skipped_count": len(skipped),
            "total_time": time.time() - start_time,
            "results": {k: {"success": v.success, "error": v.error,
                            "time": v.execution_time} for k, v in results.items()},
            "timestamp": time.time(),
        }

    def run_pipeline(self, steps: list) -> dict:
        """
        便捷方法：按顺序执行一系列步骤（无依赖的简单流水线）

        Args:
            steps: 步骤列表，每项为 (name, func, args, kwargs) 或 dict

        Returns:
            执行结果
        """
        self._tasks.clear()
        prev_id = None

        for i, step in enumerate(steps):
            if isinstance(step, dict):
                task_id = step.get("id", f"step_{i}")
                name = step.get("name", task_id)
                func = step["func"]
                args = step.get("args", ())
                kwargs = step.get("kwargs", {})
            else:
                task_id = f"step_{i}"
                name = step[0]
                func = step[1]
                args = step[2] if len(step) > 2 else ()
                kwargs = step[3] if len(step) > 3 else {}

            deps = [prev_id] if prev_id else []
            self.add_task(task_id, name, func, args, kwargs, dependencies=deps)
            prev_id = task_id

        return self.run()

    def get_history(self, limit: int = 10) -> list:
        """获取执行历史"""
        return self._execution_history[-limit:]

    def reset(self):
        """重置所有任务状态"""
        for task in self._tasks.values():
            task.status = TaskStatus.PENDING
            task.result = None
            task.retry_count = 0
            task.started_at = 0.0
            task.completed_at = 0.0
        _logger.info("任务编排器已重置")


# 单例实例
_orchestrators: dict = {}

def get_task_orchestrator(name: str = "default") -> TaskOrchestrator:
    """获取任务编排器单例"""
    if name not in _orchestrators:
        _orchestrators[name] = TaskOrchestrator(name)
    return _orchestrators[name]
