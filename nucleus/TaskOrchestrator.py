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

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from typing import Any



class TaskStatus(Enum):
    """子任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class SubTask:
    """子任务定义"""
    name: str
    func: Callable[..., Any] | Coroutine[Any, Any, Any]
    args: tuple = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    # 依赖的子任务名称（完成后才能执行）
    depends_on: list[str] = field(default_factory=list)
    # 是否关键路径（失败会导致整个任务失败）
    critical: bool = True


@dataclass
class TaskResult:
    """任务汇总结果"""
    task_id: str
    task_name: str
    status: TaskStatus
    start_time: float
    end_time: float
    duration_ms: float
    subtasks: dict[str, SubTask] = field(default_factory=dict)
    summary: Any = None
    errors: list[str] = field(default_factory=list)

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.subtasks.values() if t.status == TaskStatus.COMPLETED)

    @property
    def failed_count(self) -> int:
        return sum(1 for t in self.subtasks.values() if t.status == TaskStatus.FAILED)

    @property
    def success_rate(self) -> float:
        total = len(self.subtasks)
        if total == 0:
            return 1.0
        return self.completed_count / total


class TaskOrchestrator:
    """
    全框架任务协调器（单例模式）

    使用方式：
        orchestrator = get_task_orchestrator()
        result = orchestrator.run(
            name="代码学习",
            subtasks=[
                SubTask(name="代码解析", func=parse_code, args=(code,)),
                SubTask(name="大模型补强", func=call_llm, args=(prompt,), depends_on=["代码解析"]),
                SubTask(name="记忆固化", func=store_memory, args=(result,), depends_on=["大模型补强"]),
            ],
            aggregator=lambda results: {"parsed": results["代码解析"], "enhanced": results["大模型补强"]},
        )
    """

    _instance: TaskOrchestrator | None = None

    def __init__(self):
        self._active_tasks: dict[str, TaskResult] = {}
        self._history: list[TaskResult] = []
        self._max_history = 100

    @classmethod
    def get_instance(cls) -> TaskOrchestrator:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def run(
        self,
        name: str,
        subtasks: list[SubTask],
        aggregator: Callable[[dict[str, Any]], Any] | None = None,
        timeout: float = 120.0,
    ) -> TaskResult:
        """
        同步执行任务（适用于非异步上下文）

        Args:
            name: 任务名称
            subtasks: 子任务列表
            aggregator: 汇总函数，接收所有子任务结果，返回最终结果
            timeout: 总超时时间（秒）

        Returns:
            TaskResult 任务汇总结果
        """
        task_id = str(uuid.uuid4())[:8]
        result = TaskResult(
            task_id=task_id,
            task_name=name,
            status=TaskStatus.RUNNING,
            start_time=time.time(),
            end_time=0.0,
            duration_ms=0.0,
        )
        self._active_tasks[task_id] = result

        # ★P1：任务协调器生命周期日志
        import logging as _logging
        _logger = _logging.getLogger("TaskOrchestrator")
        _logger.info(f"任务开始 [{task_id}] {name} | 子任务={len(subtasks)}个 | 超时={timeout}s")
        _start_time = time.time()
        _layer = 0

        try:
            # 按依赖关系分层执行
            completed: set[str] = set()
            remaining = list(subtasks)
            deadline = time.time() + timeout

            while remaining and time.time() < deadline:
                # 找出所有依赖已满足的子任务
                ready = [
                    t for t in remaining
                    if all(dep in completed for dep in t.depends_on)
                ]

                if not ready:
                    # 有循环依赖或依赖未满足
                    for t in remaining:
                        t.status = TaskStatus.FAILED
                        t.error = f"依赖未满足: {t.depends_on}"
                        result.errors.append(f"{t.name}: 依赖未满足")
                    break

                # 并行执行当前层的所有子任务
                _layer += 1
                _logger.info(f"任务[{task_id}] 第{_layer}层并行: {len(ready)}个子任务 "
                           f"({', '.join(t.name for t in ready[:5])}{'...' if len(ready) > 5 else ''})")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)  # P1-4修复：Python3.12需设为当前循环
                try:
                    coros = [self._run_subtask(t) for t in ready]
                    loop.run_until_complete(asyncio.gather(*coros, return_exceptions=True))
                finally:
                    loop.close()
                _success = sum(1 for t in ready if t.status == TaskStatus.COMPLETED)
                _failed = sum(1 for t in ready if t.status == TaskStatus.FAILED)
                _logger.info(f"任务[{task_id}] 第{_layer}层完成: 成功={_success}, 失败={_failed}")

                for t in ready:
                    result.subtasks[t.name] = t
                    if t.status == TaskStatus.COMPLETED:
                        completed.add(t.name)
                    elif t.critical and t.status == TaskStatus.FAILED:
                        result.errors.append(f"{t.name}: {t.error}")
                    remaining.remove(t)

            # 标记未完成的子任务
            for t in remaining:
                if t.status == TaskStatus.PENDING:
                    t.status = TaskStatus.SKIPPED
                    result.subtasks[t.name] = t

            # 汇总结果
            _total = len(result.subtasks)
            _completed_count = sum(1 for t in result.subtasks.values() if t.status == TaskStatus.COMPLETED)
            _failed_count = sum(1 for t in result.subtasks.values() if t.status == TaskStatus.FAILED)
            if aggregator:
                _logger.info(f"任务[{task_id}] 开始汇总: {_completed_count}/{_total}完成, {_failed_count}失败")
                results_dict = {
                    name: t.result for name, t in result.subtasks.items()
                    if t.status == TaskStatus.COMPLETED
                }
                try:
                    result.summary = aggregator(results_dict)
                except Exception as e:
                    result.errors.append(f"汇总失败: {e}")

            # 判定最终状态
            if result.failed_count > 0:
                result.status = TaskStatus.FAILED
            else:
                result.status = TaskStatus.COMPLETED
            _duration = time.time() - _start_time
            _logger.info(f"任务完成 [{task_id}] {name} | 耗时={_duration:.1f}s | "
                        f"完成={_completed_count}/{_total} | 失败={_failed_count} | 层数={_layer}")

        except Exception as e:
            result.status = TaskStatus.FAILED
            result.errors.append(f"任务执行异常: {e}")
            _duration = time.time() - _start_time
            _logger.error(f"任务失败 [{task_id}] {name} | 耗时={_duration:.1f}s | 错误={str(e)[:200]}")
        finally:
            result.end_time = time.time()
            result.duration_ms = round((result.end_time - result.start_time) * 1000, 2)
            self._active_tasks.pop(task_id, None)
            self._history.append(result)
            if len(self._history) > self._max_history:
                self._history.pop(0)

        return result

    async def _run_subtask(self, subtask: SubTask) -> None:
        """执行单个子任务"""
        subtask.status = TaskStatus.RUNNING
        subtask.start_time = time.time()
        try:
            if asyncio.iscoroutinefunction(subtask.func):
                subtask.result = await subtask.func(*subtask.args, **subtask.kwargs)
            elif callable(subtask.func):
                # ★P1-4修复(2026-09-03)：同步函数必须在线程池中执行，
                # 否则asyncio.gather下所有同步函数串行执行，无法真正并行。
                subtask.result = await asyncio.to_thread(
                    subtask.func, *subtask.args, **subtask.kwargs
                )
            subtask.status = TaskStatus.COMPLETED
        except Exception as e:
            subtask.status = TaskStatus.FAILED
            subtask.error = str(e)
        finally:
            subtask.end_time = time.time()
            subtask.duration_ms = round((subtask.end_time - subtask.start_time) * 1000, 2)

    def get_active_tasks(self) -> dict[str, TaskResult]:
        """获取当前活跃任务"""
        return dict(self._active_tasks)

    def get_history(self, limit: int = 10) -> list[TaskResult]:
        """获取历史任务"""
        return self._history[-limit:]

    def get_stats(self) -> dict[str, Any]:
        """获取协调器统计信息"""
        if not self._history:
            return {"total_tasks": 0, "avg_duration_ms": 0, "avg_success_rate": 0}
        total = len(self._history)
        avg_duration = sum(t.duration_ms for t in self._history) / total
        avg_success = sum(t.success_rate for t in self._history) / total
        return {
            "total_tasks": total,
            "avg_duration_ms": round(avg_duration, 2),
            "avg_success_rate": round(avg_success, 4),
            "active_tasks": len(self._active_tasks),
        }


# 全局单例
_orchestrator: TaskOrchestrator | None = None


def get_task_orchestrator() -> TaskOrchestrator:
    """获取全局任务协调器单例"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TaskOrchestrator.get_instance()
    return _orchestrator
