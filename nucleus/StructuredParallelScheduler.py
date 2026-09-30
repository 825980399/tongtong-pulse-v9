# -*- coding: utf-8 -*-
"""
StructuredParallelScheduler.py —— 结构化并行调度器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 结构化任务的并行调度与依赖管理
机制: 基于TaskGroupStatus类实现，包含10个核心方法
定位: 任务调度层

⚠️ @deprecated (P2-64 并发收敛): 新代码请使用 nucleus.parallel_scheduler.get_parallel_scheduler()。
本调度器保留实现，仅遗留调用点（main.py / self_inspector.py）使用，禁止新代码 import。
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from enum import Enum
from typing import Any



class TaskGroupStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class TaskType(Enum):
    MIXED = "mixed"
    CPU_BOUND = "cpu_bound"
    IO_BOUND = "io_bound"


@dataclass
class SubTask:
    """子任务定义"""
    name: str
    func: Callable
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    task_type: TaskType = TaskType.MIXED  # ★CPU密集型用进程池，IO密集型用线程池
    status: str = "pending"
    result: Any = None
    error: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0


@dataclass
class TaskGroupResult:
    """任务组执行结果"""
    group_id: str
    group_name: str
    status: TaskGroupStatus
    # ★PHASE17-B2修复（2026-09-07）：subtasks 原先是必填项且无默认值，
    #   导致「调度器已关闭」等早退分支无法构造空结果对象（被迫传入不存在的
    #   results/errors/total_time 关键字 → TypeError）。改为带默认值。
    subtasks: dict[str, SubTask] = field(default_factory=dict)
    aggregated_result: Any = None
    error: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    success_count: int = 0
    failed_count: int = 0

    # ★PHASE17-B2：补齐下游/外部代码曾按名访问的三个属性。
    #   设计为「从 subtasks 派生的只读 property」而非独立存储字段，
    #   避免与 subtasks 双份数据不同步（低侵入、零维护成本）。
    @property
    def results(self) -> dict[str, Any]:
        """{子任务名: 结果} —— 仅包含成功完成的子任务。"""
        return {
            name: t.result
            for name, t in self.subtasks.items()
            if t.status == "completed"
        }

    @property
    def errors(self) -> dict[str, str]:
        """{子任务名: 错误信息} —— 仅包含失败/超时的子任务。"""
        return {
            name: (t.error or "未完成")
            for name, t in self.subtasks.items()
            if t.status != "completed"
        }

    @property
    def total_time(self) -> float:
        """任务组总耗时（秒），与 duration_ms 同源。"""
        return self.duration_ms / 1000.0


class StructuredParallelScheduler:
    """
    结构化并行调度器

    使用示例：
        scheduler = StructuredParallelScheduler()
        result = scheduler.run_group(
            name="知识检索组",
            subtasks=[
                SubTask("器官知识", _search_organ, args=(keyword,)),
                SubTask("自我认知", _search_self, args=(keyword,)),
                SubTask("记忆检索", _search_memory, args=(keyword,)),
            ],
            aggregator=lambda results: "\n".join(results.values()),
            timeout=30.0,
        )
    """

    _instance: StructuredParallelScheduler | None = None

    def __init__(self):
        self._logger = logging.getLogger("pulse.structured_parallel")
        self._active_groups: dict[str, TaskGroupResult] = {}
        self._hybrid_scheduler = None  # ★延迟加载HybridParallelScheduler（进程池/线程池自动选择）
        self._history: list[TaskGroupResult] = []
        self._max_history = 100
        # 共享线程池（子任务执行用，进程池由HybridParallelScheduler负责）
        self._executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="StructParallel")
        self._shutdown = False  # ★v26.0修复：关闭标志，防止shutdown后仍提交任务

    @classmethod
    def get_instance(cls) -> StructuredParallelScheduler:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def run_group(
        self,
        name: str,
        subtasks: list[SubTask],
        aggregator: Callable[[dict[str, Any]], Any] | None = None,
        timeout: float = 30.0,
    ) -> TaskGroupResult:
        """
        执行一个任务组（结构化并行单元）

        Args:
            name: 任务组名称（如"知识检索组"、"代码分析组"）
            subtasks: 子任务列表
            aggregator: 汇总函数，输入为 {子任务名: 结果}，输出为汇总结果
            timeout: 整个任务组超时时间（秒）

        Returns:
            TaskGroupResult: 任务组执行结果
        """
        # ★v26.0修复：shutdown后拒绝新任务，避免cannot schedule new futures after shutdown
        if self._shutdown:
            self._logger.debug(f"[调度器已关闭] 拒绝任务组: {name}")
            # ★PHASE17-B2修复：原构造传入 results/errors/total_time 三个
            #   TaskGroupResult 并不存在的关键字参数，一旦调度器关闭后仍有任务
            #   提交就会抛 TypeError（且可能在器官关闭路径上，掩盖真实错误）。
            #   改为使用 dataclass 真实字段；errors 语义由派生 property 提供。
            return TaskGroupResult(
                group_id=f"grp_shutdown_{uuid.uuid4().hex[:8]}",
                group_name=name,
                status=TaskGroupStatus.FAILED,
                subtasks={},
                error="调度器已关闭",
                duration_ms=0.0,
            )
        
        group_id = f"grp_{uuid.uuid4().hex[:8]}"
        start_time = time.time()

        result = TaskGroupResult(
            group_id=group_id,
            group_name=name,
            status=TaskGroupStatus.RUNNING,
            subtasks={t.name: t for t in subtasks},
            start_time=start_time,
        )
        self._active_groups[group_id] = result

        # ★v25.1日志量再优化：例行器官扫描不记录开始日志，仅非例行任务记录
        if "器官扫描" not in name:
            self._logger.debug(
                f"[任务组开始] {group_id} {name} | "
                f"子任务={len(subtasks)}个 | 超时={timeout}s"
            )

        try:
            # 1. 提交所有子任务（CPU密集型尝试进程池，不可序列化时回退线程池）
            futures = {}
            for task in subtasks:
                task.status = "running"
                task.start_time = time.time()
                future = self._submit_task(task)
                futures[future] = task.name
                # ★日志量优化(2026-09-03)：不逐条打印子任务开始，任务组开始已列出全部子任务

            # 2. 等待所有子任务完成（带超时）
            deadline = start_time + timeout
            remaining = list(futures.keys())
            while remaining and time.time() < deadline:
                done, remaining = wait(
                    remaining,
                    timeout=min(1.0, deadline - time.time()),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    task_name = futures[future]
                    task = result.subtasks[task_name]
                    try:
                        task.result = future.result()
                        task.status = "completed"
                        task.end_time = time.time()
                        task.duration_ms = (task.end_time - task.start_time) * 1000
                        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                        result.success_count += 1
                        # ★日志量优化(2026-09-03)：不逐条打印成功子任务完成，
                        # 任务组结束时汇总成功/失败/超时数量及总耗时
                    except Exception as e:
                        task.status = "failed"
                        task.error = str(e)
                        task.end_time = time.time()
                        task.duration_ms = (task.end_time - task.start_time) * 1000
                        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                        result.failed_count += 1
                        self._logger.warning(
                            f"[子任务失败] {group_id} {name} > {task.name} | "
                            f"耗时={task.duration_ms:.0f}ms | 错误={str(e)[:100]}"
                        )

            # 3. 检查超时
            if remaining:
                result.status = TaskGroupStatus.TIMEOUT
                result.error = f"任务组超时，{len(remaining)}个子任务未完成"
                for future in remaining:
                    task_name = futures[future]
                    task = result.subtasks[task_name]
                    task.status = "timeout"
                    task.error = "任务组超时"
                    self._logger.warning(
                        f"[子任务超时] {group_id} {name} > {task_name}"
                    )
                self._logger.error(
                    f"[任务组超时] {group_id} {name} | "
                    f"完成={result.success_count}/{len(subtasks)} | "
                    f"失败={result.failed_count} | 超时={len(remaining)}"
                )
            else:
                # 4. 所有子任务完成，调用协调者汇总
                result.status = TaskGroupStatus.AGGREGATING
                # ★v25.1日志量优化：正常完成不记录汇总日志，仅失败时记录
                if result.failed_count > 0:
                    self._logger.debug(
                        f"[任务组汇总] {group_id} {name} | "
                        f"成功={result.success_count} | 失败={result.failed_count}"
                    )
                if aggregator:
                    try:
                        results_dict = {
                            name: task.result
                            for name, task in result.subtasks.items()
                            if task.status == "completed"
                        }
                        result.aggregated_result = aggregator(results_dict)
                        # ★v25.1日志量优化：移除正常汇总完成的debug日志
                    except Exception as e:
                        result.error = f"汇总失败: {e}"
                        self._logger.error(
                            f"[任务组汇总失败] {group_id} {name} | 错误={str(e)[:100]}"
                        )
                result.status = TaskGroupStatus.COMPLETED

        except Exception as e:
            result.status = TaskGroupStatus.FAILED
            result.error = str(e)
            self._logger.error(
                f"[任务组异常] {group_id} {name} | 错误={str(e)[:200]}"
            )
        finally:
            result.end_time = time.time()
            result.duration_ms = (result.end_time - result.start_time) * 1000
            self._active_groups.pop(group_id, None)
            self._history.append(result)
            if len(self._history) > self._max_history:
                self._history.pop(0)

            # ★v25.1日志量优化：正常完成降为DEBUG，仅失败/超时/慢速(>1s)时INFO
            _has_issue = result.failed_count > 0 or result.status in (
                TaskGroupStatus.FAILED, TaskGroupStatus.TIMEOUT)
            _is_slow = result.duration_ms > 1000
            # ★v25.1日志量再优化：快速完成(<500ms)且无失败的不记录日志
            _is_fast_normal = (not _has_issue) and result.duration_ms < 500
            if _has_issue or _is_slow:
                self._logger.info(
                    f"[任务组结束] {group_id} {name} | "
                    f"状态={result.status.value} | "
                    f"总耗时={result.duration_ms:.0f}ms | "
                    f"成功={result.success_count}/{len(subtasks)} | "
                    f"失败={result.failed_count}"
                )
            elif not _is_fast_normal:
                self._logger.debug(
                    f"[任务组结束] {group_id} {name} | "
                    f"耗时={result.duration_ms:.0f}ms | "
                    f"成功={result.success_count}/{len(subtasks)}"
                )

        return result

    def _submit_task(self, task: SubTask):
        """★P1-3(2026-09-03)：提交子任务到混合调度器。
        CPU密集型任务尝试进程池，函数不可序列化时自动回退本地线程池。
        IO密集型和MIXED直接用本地线程池。
        """
        # IO密集型或未指定：直接用本地线程池（避免进程池序列化开销）
        if task.task_type != TaskType.CPU_BOUND:
            return self._executor.submit(self._run_subtask, task)
        # CPU密集型：预检函数和参数是否可序列化（进程池要求）
        import pickle as _pickle
        try:
            _pickle.dumps(task.func)
            _pickle.dumps(task.args)
            _pickle.dumps(task.kwargs)
        except Exception:
            # 函数或参数不可序列化（如闭包、绑定方法携带线程锁），回退线程池
            return self._executor.submit(self._run_subtask, task)
        # 可序列化：尝试混合调度器的进程池
        try:
            if self._hybrid_scheduler is None:
                from nucleus.HybridParallelScheduler import TaskType as HPTaskType
                from nucleus.HybridParallelScheduler import get_hybrid_scheduler
                self._hybrid_scheduler = get_hybrid_scheduler()
                self._hp_task_type = HPTaskType
            # 注意：传给进程池的必须是task.func本身（可序列化），不能是self._run_subtask
            return self._hybrid_scheduler.submit(
                task.func, *task.args,
                task_type=self._hp_task_type.CPU_BOUND,
                task_name=task.name,
                **task.kwargs,
            )
        except Exception:
            # 进程池不可用，回退本地线程池
            return self._executor.submit(self._run_subtask, task)

    def _run_subtask(self, task: SubTask) -> Any:
        """执行单个子任务"""
        return task.func(*task.args, **task.kwargs)

    def get_active_groups(self) -> dict[str, TaskGroupResult]:
        """获取当前活跃的任务组"""
        return self._active_groups.copy()

    def get_history(self) -> list[TaskGroupResult]:
        """获取历史任务组记录"""
        return self._history.copy()

    def get_stats(self) -> dict[str, Any]:
        """获取调度器统计"""
        total = len(self._history)
        completed = sum(1 for r in self._history if r.status == TaskGroupStatus.COMPLETED)
        failed = sum(1 for r in self._history if r.status in (TaskGroupStatus.FAILED, TaskGroupStatus.TIMEOUT))
        avg_duration = (
            sum(r.duration_ms for r in self._history) / total if total > 0 else 0
        )
        return {
            "total_groups": total,
            "completed": completed,
            "failed": failed,
            "active_groups": len(self._active_groups),
            "avg_duration_ms": round(avg_duration, 1),
        }

    def shutdown(self, wait: bool = True, timeout: float = 30.0):
        """优雅关闭调度器"""
        self._shutdown = True  # ★v26.0修复：先设置关闭标志，拒绝新任务
        self._logger.info(f"[调度器关闭] 活跃任务组={len(self._active_groups)}")
        # ★修复：Python 3.12 ThreadPoolExecutor.shutdown()不支持timeout参数，
        # 使用wait=True等待任务完成，超时通过外部控制
        self._executor.shutdown(wait=wait)


# 全局单例
# @deprecated (P2-64 并发收敛): 见模块 docstring；新代码用 parallel_scheduler.get_parallel_scheduler()
def get_structured_parallel_scheduler() -> StructuredParallelScheduler:
    return StructuredParallelScheduler.get_instance()
