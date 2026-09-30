# -*- coding: utf-8 -*-
"""
HybridParallelScheduler.py —— 混合并行调度器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 混合任务并行调度，支持同步/异步模式
机制: 基于TaskType类实现，包含10个核心方法
定位: 任务调度层
"""

from __future__ import annotations
from config import TIMEOUT_CONFIG

import os
import threading
import time

from nucleus.const import LogLevel
from nucleus.logger import get_module_logger


import uuid
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from enum import Enum
from typing import Any

from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底
from nucleus._silent_except import silent_exc


_logger = get_module_logger("HybridParallelScheduler")


class TaskType(Enum):
    CPU_BOUND = "cpu_bound"
    IO_BOUND = "io_bound"
    MIXED = "mixed"


class AdaptiveLevel(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


def _log(msg: str) -> None:
    """统一日志输出（控制台+后台日志）"""
    _logger.debug(f"{msg}")


class HybridParallelScheduler(SilentLogMixin):
    """混合并行调度器（单例模式）"""

    _instance: HybridParallelScheduler | None = None
    # ★A3【P1】：单例初始化锁（双重检查锁定用），防止多线程并发首次调用创建多实例。
    _instance_lock = threading.Lock()

    _HARD_FUSE_MAX_PROCESSES = 16
    _HARD_FUSE_MIN_PROCESSES = 2
    _HARD_FUSE_MAX_THREADS = 64
    _ADJUST_INTERVAL = 30.0
    _CPU_HIGH_THRESHOLD = 70.0
    _CPU_LOW_THRESHOLD = 30.0
    _QUEUE_HIGH_THRESHOLD = 10

    def __init__(self):
        self._lock = threading.Lock()
        self._cpu_count = os.cpu_count() or 8
        self._physical_cores = self._detect_physical_cores()
        self._memory_gb = self._detect_memory_gb()

        self._process_pool: ProcessPoolExecutor | None = None
        self._process_workers = 0
        self._process_pool_initialized = False
        self._target_process_workers = 0

        self._thread_pool: ThreadPoolExecutor | None = None
        self._thread_workers = 0

        self._current_level = AdaptiveLevel.NORMAL
        self._last_adjust_time = 0.0
        self._pending_tasks = 0
        self._cpu_usage = 0.0

        self._cpu_task_count = 0
        self._io_task_count = 0
        self._total_task_count = 0

        # 任务生命周期记录
        self._task_records: dict[str, dict[str, Any]] = {}
        self._max_task_records = 500

        self._shutting_down = False
        self._shutdown_complete = False
        self._monitor_thread: threading.Thread | None = None
        self._monitor_running = False

    @classmethod
    def get_instance(cls) -> HybridParallelScheduler:
        # ★A3【P1】：双重检查锁定，防止并发首次调用创建多实例。
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @staticmethod
    def _detect_physical_cores() -> int:
        try:
            import psutil
            return psutil.cpu_count(logical=False) or os.cpu_count() or 8
        except Exception:
            return max(2, (os.cpu_count() or 8) // 2)

    @staticmethod
    def _detect_memory_gb() -> float:
        try:
            import psutil
            return round(psutil.virtual_memory().total / (1024 ** 3), 1)
        except Exception as e:
            silent_exc(e, where="nucleus.HybridParallelScheduler::_detect_memory_gb L124")
            return 16.0

    def _get_cpu_usage(self) -> float:
        try:
            import psutil
            return psutil.cpu_percent(interval=0.1)
        except Exception as e:
            silent_exc(e, where="nucleus.HybridParallelScheduler::_get_cpu_usage L131")
            return 50.0

    def _calc_target_processes(self, level: AdaptiveLevel) -> int:
        base = max(self._HARD_FUSE_MIN_PROCESSES, int(self._physical_cores * 0.5))
        if level == AdaptiveLevel.LOW:
            return max(self._HARD_FUSE_MIN_PROCESSES, int(base * 0.5))
        elif level == AdaptiveLevel.NORMAL:
            return min(self._HARD_FUSE_MAX_PROCESSES, int(self._physical_cores * 0.6))
        elif level == AdaptiveLevel.HIGH:
            return min(self._HARD_FUSE_MAX_PROCESSES, int(self._physical_cores * 0.8))
        return self._process_workers or base

    def _ensure_pools(self) -> None:
        with self._lock:
            if self._shutting_down:
                return
            if not self._process_pool_initialized:
                self._target_process_workers = self._calc_target_processes(AdaptiveLevel.NORMAL)
                self._process_workers = self._target_process_workers
                if self._memory_gb < 8:
                    self._process_workers = min(self._process_workers, 2)
                elif self._memory_gb < 16:
                    self._process_workers = min(self._process_workers, 4)
                try:
                    self._process_pool = ProcessPoolExecutor(max_workers=self._process_workers)
                    self._process_pool_initialized = True
                    _log(f"进程池初始化完成: {self._process_workers}个进程 (物理核={self._physical_cores}, 内存={self._memory_gb}GB)")
                    self._start_monitor()
                except Exception as e:
                    _log(f"进程池初始化失败，降级为线程池: {e}")
                    self._process_pool = None
                    self._process_workers = 0
            if not self._thread_pool:
                self._thread_workers = min(self._HARD_FUSE_MAX_THREADS, max(4, self._cpu_count * 2))
                self._thread_pool = ThreadPoolExecutor(max_workers=self._thread_workers, thread_name_prefix="HybridIO")
                _log(f"线程池初始化完成: {self._thread_workers}个线程")

    def _start_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="HybridSchedulerMonitor")
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        while self._monitor_running and not self._shutting_down:
            try:
                time.sleep(self._ADJUST_INTERVAL)
                self._adjust_workers()
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def _adjust_workers(self) -> None:
        if self._shutting_down or not self._process_pool_initialized:
            return
        self._cpu_usage = self._get_cpu_usage()
        if self._cpu_usage > self._CPU_HIGH_THRESHOLD or self._pending_tasks > self._QUEUE_HIGH_THRESHOLD:
            new_level = AdaptiveLevel.HIGH
        elif self._cpu_usage < self._CPU_LOW_THRESHOLD and self._pending_tasks < 3:
            new_level = AdaptiveLevel.LOW
        else:
            new_level = AdaptiveLevel.NORMAL
        if new_level != self._current_level:
            old_level = self._current_level
            self._current_level = new_level
            self._target_process_workers = self._calc_target_processes(new_level)
            _log(f"自适应等级变化: {old_level.value}→{new_level.value} "
                 f"(CPU={self._cpu_usage:.0f}%, 待处理={self._pending_tasks}, 目标进程数={self._target_process_workers})")

    def submit(
        self,
        func: Callable[..., Any],
        *args: Any,
        task_type: TaskType = TaskType.MIXED,
        task_name: str = "",
        **kwargs: Any,
    ):
        """提交任务，自动选择执行器，并记录完整生命周期"""
        if self._shutting_down:
            raise RuntimeError("调度器正在关闭，不接受新任务")

        self._ensure_pools()
        # ★7-1/P1-13修复(2026-09-05)：submit 是并发提交入口，三个计数器原为
        #   裸自增（读-改-写三步非原子），并发下计数会偏小。用类内已有的
        #   self._lock 保护。刻意**不把 _ensure_pools() 包进本锁**——它内部
        #   (:134)已自持同一把锁，而 Lock 不可重入，套进去会直接死锁。
        with self._lock:
            self._total_task_count += 1
            self._pending_tasks += 1

        task_id = uuid.uuid4().hex[:8]
        func_name = task_name or getattr(func, '__name__', 'unknown')

        if task_type == TaskType.MIXED:
            task_type = self._auto_detect_task_type(func)

        executor_type = "process" if (task_type == TaskType.CPU_BOUND and self._process_pool) else "thread"
        if task_type == TaskType.CPU_BOUND and self._process_pool:
            with self._lock:
                self._cpu_task_count += 1
            pool = self._process_pool
        else:
            with self._lock:
                self._io_task_count += 1
            pool = self._thread_pool

        # 记录任务开始
        start_time = time.time()
        self._task_records[task_id] = {
            "id": task_id,
            "name": func_name,
            "type": task_type.value,
            "executor": executor_type,
            "start_time": start_time,
            "status": "running",
        }
        _log(f"任务开始 [{task_id}] {func_name} | 类型={task_type.value} | 执行器={executor_type}池 | 待处理={self._pending_tasks}")

        future = pool.submit(func, *args, **kwargs)

        # 任务完成回调
        def _on_done(f: Any, tid: str = task_id, fname: str = func_name,
                      ttype: str = task_type.value, etype: str = executor_type,
                      st: float = start_time) -> None:
            duration_ms = round((time.time() - st) * 1000, 2)
            with self._lock:
                if self._pending_tasks > 0:
                    self._pending_tasks -= 1
            try:
                result = f.result(timeout=TIMEOUT_CONFIG['scheduler_poll'])
                result_preview = str(result)[:80] if result is not None else "None"
                status = "success"
                _log(f"任务完成 [{tid}] {fname} | 耗时={duration_ms}ms | 状态=成功 | 结果={result_preview}")
            except Exception as e:
                status = "failed"
                _log(f"任务失败 [{tid}] {fname} | 耗时={duration_ms}ms | 状态=失败 | 错误={str(e)[:100]}")
            # 更新任务记录
            if tid in self._task_records:
                self._task_records[tid].update({
                    "end_time": time.time(),
                    "duration_ms": duration_ms,
                    "status": status,
                })
                # 清理旧记录
                if len(self._task_records) > self._max_task_records:
                    oldest = min(self._task_records.keys(), key=lambda k: self._task_records[k]["start_time"])
                    self._task_records.pop(oldest, None)

        future.add_done_callback(_on_done)
        return future

    def _auto_detect_task_type(self, func: Callable) -> TaskType:
        func_name = getattr(func, '__name__', '').lower()
        module_name = getattr(func, '__module__', '').lower()
        text = f"{func_name} {module_name}"
        cpu_keywords = ['calculate', 'compute', 'analyze', 'parse', 'encode', 'decode',
                        'compress', 'resonance', 'oscillon', 'cython', 'numpy',
                        '计算', '分析', '推理', '编码', '解码', '共振', '振荡']
        io_keywords = ['request', 'fetch', 'download', 'upload', 'read', 'write',
                       'save', 'load', 'api', 'http', 'socket', 'network',
                       '搜索', '请求', '读取', '写入', '保存', '加载', '网络']
        if any(kw in text for kw in cpu_keywords):
            return TaskType.CPU_BOUND
        elif any(kw in text for kw in io_keywords):
            return TaskType.IO_BOUND
        return TaskType.IO_BOUND

    def map(self, func: Callable[..., Any], iterable,
            task_type: TaskType = TaskType.MIXED, timeout: float | None = None):
        if self._shutting_down:
            raise RuntimeError("调度器正在关闭")
        self._ensure_pools()
        if task_type == TaskType.MIXED:
            task_type = self._auto_detect_task_type(func)
        pool = self._process_pool if (task_type == TaskType.CPU_BOUND and self._process_pool) else self._thread_pool
        return list(pool.map(func, iterable, timeout=timeout))

    def get_stats(self) -> dict[str, Any]:
        return {
            "cpu_count": self._cpu_count,
            "physical_cores": self._physical_cores,
            "memory_gb": self._memory_gb,
            "process_workers": self._process_workers,
            "target_process_workers": self._target_process_workers,
            "thread_workers": self._thread_workers,
            "process_pool_initialized": self._process_pool_initialized,
            "adaptive_level": self._current_level.value,
            "cpu_usage": self._cpu_usage,
            "pending_tasks": self._pending_tasks,
            "cpu_task_count": self._cpu_task_count,
            "io_task_count": self._io_task_count,
            "total_task_count": self._total_task_count,
            "active_task_records": len(self._task_records),
        }

    def get_recent_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取最近的任务记录"""
        sorted_tasks = sorted(self._task_records.values(), key=lambda x: x.get("start_time", 0), reverse=True)
        return sorted_tasks[:limit]

    def shutdown(self, wait: bool = True, timeout: float = 30.0) -> None:
        with self._lock:
            if self._shutdown_complete:
                return
            self._shutting_down = True
        _log(f"调度器开始关闭: 待处理任务={self._pending_tasks}, 总任务={self._total_task_count}")
        self._monitor_running = False
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)
        if self._process_pool:
            try:
                self._process_pool.shutdown(wait=wait)
                _log("进程池已关闭")
            except Exception as e:
                silent_exc(e, where="nucleus.HybridParallelScheduler::shutdown L348")
            self._process_pool = None
            self._process_pool_initialized = False
        if self._thread_pool:
            try:
                self._thread_pool.shutdown(wait=wait)
                _log("线程池已关闭")
            except Exception as e:
                self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")
            self._thread_pool = None
        self._shutdown_complete = True
        _log(f"调度器关闭完成: 总任务={self._total_task_count}, CPU任务={self._cpu_task_count}, IO任务={self._io_task_count}")


_hybrid_scheduler: HybridParallelScheduler | None = None


def get_hybrid_scheduler() -> HybridParallelScheduler:
    global _hybrid_scheduler
    if _hybrid_scheduler is None:
        _hybrid_scheduler = HybridParallelScheduler.get_instance()
    return _hybrid_scheduler
