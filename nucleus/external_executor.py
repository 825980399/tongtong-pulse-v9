# -*- coding: utf-8 -*-
"""
external_executor.py —— 外部执行器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 安全沙箱中执行外部代码与命令
机制: 基于OperationPriority类实现，包含10个核心方法
定位: 安全执行层
"""
import concurrent.futures
import os
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel
from nucleus.logger import get_module_logger
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底

_logger = get_module_logger("external_executor")


# 共享操作执行线程池：避免每次外部操作都新建/销毁 ThreadPoolExecutor（★PERF-3修复）
# [批次4·深度体检][PERF-3] 模块级共享线程池（替代每次执行新建线程池）
# ★FIX(硬件自适应): 外部操作是 IO 密集，线程数可到 CPU 核数 2 倍，设 8~32 上下限
_OP_WORKERS = max(8, min(32, (os.cpu_count() or 8) * 2))
_OP_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=_OP_WORKERS, thread_name_prefix="ExtOp")

# ★A1【P0】外层调度线程池：替代 _try_execute_next 中每任务新建 threading.Thread 的无界创建。
#   设计说明（为何不用星轨字面的 _OP_EXECUTOR.submit(self._execute_operation)）：
#   _execute_operation 内部（:323）又向 _OP_EXECUTOR 提交 executor_func 并阻塞等待
#   future.result(timeout)。若外层也提交到同一个 _OP_EXECUTOR，当活跃任务数 ≥ 池线程数时，
#   所有池线程都会「提交内层任务后阻塞等待」，池内无空闲线程执行内层 executor_func，
#   导致同池自锁死锁。故外层用独立的、有界的调度池，与内层执行池分离，两层互不阻塞。
#   外层 max_workers 与并发上限对齐（默认 2，见 _load_config 的 _max_concurrent），
#   因此外层线程数严格有界，彻底消除线程爆炸隐患。
_SCHEDULER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="ExtOpSched")


class OperationPriority(Enum):
    """操作优先级枚举"""
    URGENT = 0    # 紧急：生命线操作，插队执行
    HIGH = 1      # 高：重要交互，优先于普通任务
    NORMAL = 2    # 普通：常规操作
    LOW = 3       # 低：后台探索，可被降级


class OperationStatus(Enum):
    """操作状态枚举"""
    PENDING = "pending"        # 等待执行
    EXECUTING = "executing"    # 执行中
    COMPLETED = "completed"    # 已完成
    FAILED = "failed"          # 执行失败
    TIMEOUT = "timeout"        # 超时
    CANCELLED = "cancelled"    # 已取消


@dataclass
class ExternalOperation:
    """
    外部操作任务数据结构。
    封装了所有外部操作的通用属性，不限制具体操作类型。
    """
    # 任务标识
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    operation_type: str = "generic"  # 操作类型：search/http/file/hardware等
    
    # 调度参数
    priority: OperationPriority = OperationPriority.NORMAL
    max_timeout: float = 60.0       # 最大执行时间（秒）
    submitted_at: float = field(default_factory=time.time)
    
    # 视角标记
    view_mode: str = "OUTER_VIEW"   # INNER_VIEW/OUTER_VIEW
    
    # 执行函数与回调
    executor_func: Callable | None = None
    on_completed: Callable | None = None  # 完成回调
    on_failed: Callable | None = None     # 失败回调
    on_progress: Callable | None = None   # 进度回调（用于硬件操作）
    
    # 状态追踪
    status: OperationStatus = OperationStatus.PENDING
    started_at: float = 0.0
    completed_at: float = 0.0
    progress: float = 0.0           # 进度 0.0-1.0
    result: Any = None
    error: str | None = None
    
    # 额外参数（传递给执行函数）
    kwargs: dict[str, Any] = field(default_factory=dict)


class ExternalExecutor(SilentLogMixin):
    """
    统一外部操作调度器。
    
    所有与外部世界交互的耗时操作，都通过此调度器统一管理。
    不限制操作类型，不关心谁在执行——只管理“如何安全高效地调度”。
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        
        # 任务队列（按优先级分桶）
        self._urgent_queue: deque = deque()   # 紧急队列
        self._high_queue: deque = deque()     # 高优先级队列
        self._normal_queue: deque = deque()   # 普通队列
        self._low_queue: deque = deque()      # 低优先级队列
        
        # 执行中任务
        self._active_operations: dict[str, ExternalOperation] = {}
        
        # 历史记录（最近100条）
        self._history: deque = deque(maxlen=100)
        
        # 操作类型去重缓存（可选，由操作类型决定是否需要去重）
        self._dedup_cache: dict[str, dict[str, float]] = {}
        
        # 统计
        self._total_submitted = 0
        self._total_completed = 0
        self._total_failed = 0
        self._total_timeout = 0
        
        # 关闭标志
        self._shutting_down = False
        
        # 加载配置
        self._load_config()
    
    def _load_config(self):
        """从config加载调度参数"""
        try:
            import config
            cfg = getattr(config, 'EXTERNAL_EXECUTOR', {})
            self._max_concurrent = cfg.get("max_concurrent", 2)
            self._default_timeout = cfg.get("default_timeout", 60)
            self._max_history = cfg.get("max_history", 100)
            self._enable_dedup = cfg.get("enable_dedup", True)
            self._dedup_interval = cfg.get("dedup_interval", 1800)
            self._max_queue_size = cfg.get("max_queue_size", 1000)  # ★P2-2: 队列总上限，防止无界积压
        except Exception:
            self._max_concurrent = 2
            self._default_timeout = 60
            self._max_history = 100
            self._enable_dedup = True
            self._dedup_interval = 1800
            self._max_queue_size = 1000

    def _get_effective_max_concurrent(self) -> int:
        """★P3-11修复：让外部执行器的并发上限参考全局并行调度器的建议并行度。

        外部操作（搜索/http/硬件）较重，并发不宜过高，故取「配置并发数」与
        「建议并行度的一半」的较小值，实现「统一并行调度」的保守接入。
        """
        try:
            from nucleus.parallel_scheduler import get_parallel_scheduler
            _ps = get_parallel_scheduler().get_parallelism()
            return max(1, min(self._max_concurrent, _ps // 2))
        except Exception:
            return self._max_concurrent
    
    # ========== 任务提交 ==========
    
    def submit(self, operation_type: str, executor_func: Callable,
               priority: OperationPriority = OperationPriority.NORMAL,
               view_mode: str = "OUTER_VIEW",
               max_timeout: float | None = None,
               dedup_key: str | None = None,
               on_completed: Callable | None = None,
               on_failed: Callable | None = None,
               on_progress: Callable | None = None,
               **kwargs) -> ExternalOperation:
        """
        提交一个外部操作任务。
        
        Args:
            operation_type: 操作类型（search/http/file/hardware等）
            executor_func: 执行函数
            priority: 优先级
            view_mode: 视角标记（INNER_VIEW/OUTER_VIEW）
            max_timeout: 最大执行时间（秒），默认从config读取
            dedup_key: 去重键（为None则不参与去重）
            on_completed: 完成回调
            on_failed: 失败回调
            on_progress: 进度回调（用于硬件操作等需要回传中间状态的操作）
            **kwargs: 传递给执行函数的额外参数
        
        Returns:
            ExternalOperation对象，可用于追踪任务状态
        """
        if self._shutting_down:
            op = ExternalOperation(
                operation_type=operation_type,
                priority=priority,
                view_mode=view_mode,
                status=OperationStatus.CANCELLED,
                error="调度器已关闭"
            )
            return op
        
        # 去重检查
        if self._enable_dedup and dedup_key:
            if self._is_duplicate(operation_type, dedup_key):
                # ★7-1/P1-13：本方法是外部操作提交入口，可被多线程调用。
                #   仅此处的 _total_submitted 位于锁外；:245/:250 两处
                #   本就处于 :240 的 `with self._lock:` 之内，无需再加。
                with self._lock:
                    self._total_submitted += 1
                op = ExternalOperation(
                    operation_type=operation_type,
                    priority=priority,
                    view_mode=view_mode,
                    status=OperationStatus.CANCELLED,
                    error=f"去重跳过: {dedup_key[:40]}"
                )
                self._history.append(op)
                return op
        
        # 创建操作对象
        operation = ExternalOperation(
            operation_type=operation_type,
            priority=priority,
            view_mode=view_mode,
            max_timeout=max_timeout or self._default_timeout,
            executor_func=executor_func,
            on_completed=on_completed,
            on_failed=on_failed,
            on_progress=on_progress,
            kwargs=kwargs,
        )
        
        # 标记去重
        if dedup_key:
            self._mark_submitted(operation_type, dedup_key)
        
        # 按优先级加入对应队列（★P2-2修复：队列总长度上限，超限背压拒绝，防止无界积压）
        with self._lock:
            _queue_total = (len(self._urgent_queue) + len(self._high_queue) +
                            len(self._normal_queue) + len(self._low_queue))
            if _queue_total >= self._max_queue_size:
                # 队列已满：明确背压拒绝（返回 CANCELLED），避免静默丢失操作
                self._total_submitted += 1
                operation.status = OperationStatus.CANCELLED
                operation.error = f"队列已满({_queue_total}/{self._max_queue_size})，背压拒绝"
                self._history.append(operation)
                return operation
            self._total_submitted += 1
            if priority == OperationPriority.URGENT:
                self._urgent_queue.append(operation)
            elif priority == OperationPriority.HIGH:
                self._high_queue.append(operation)
            elif priority == OperationPriority.NORMAL:
                self._normal_queue.append(operation)
            else:
                self._low_queue.append(operation)
        
        # 尝试调度执行
        self._try_execute_next()
        
        return operation
    
    # ========== 队列调度 ==========
    
    def _try_execute_next(self):
        """尝试从队列中取出下一个任务执行"""
        if self._shutting_down:
            return
        
        with self._lock:
            active_count = len(self._active_operations)
            # ★P3-11修复：并发上限参考全局并行调度器的建议并行度
            if active_count >= self._get_effective_max_concurrent():
                return
            
            # 按优先级取任务
            operation = self._pop_next_task()
            if operation is None:
                return
            
            # 标记为执行中
            operation.status = OperationStatus.EXECUTING
            operation.started_at = time.time()
            self._active_operations[operation.task_id] = operation
        
        # 在锁外调度执行（★A1【P0】：由每任务新建 Thread 改为有界外层调度池，
        # 消除线程爆炸；线程池线程本身即 daemon，语义保留）
        _SCHEDULER_EXECUTOR.submit(self._execute_operation, operation)
    
    def _pop_next_task(self) -> ExternalOperation | None:
        """按优先级从各队列取任务"""
        for queue in [self._urgent_queue, self._high_queue, 
                      self._normal_queue, self._low_queue]:
            if queue:
                return queue.popleft()
        return None
    
    def _execute_operation(self, operation: ExternalOperation):
        """在专用线程中执行操作，并强制应用 max_timeout（超时标记为 TIMEOUT 并释放槽位）"""
        try:
            if operation.executor_func is None:
                operation.status = OperationStatus.FAILED
                operation.error = "执行函数为空"
                operation.completed_at = time.time()
                if operation.on_failed:
                    try:
                        operation.on_failed(operation)
                    except Exception as e:
                        self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                return

            # ★RES-2修复: 强制应用 max_timeout，避免挂死的外部操作永久阻塞调度器
            _future = _OP_EXECUTOR.submit(operation.executor_func, **operation.kwargs)
            try:
                result = _future.result(timeout=operation.max_timeout)
            except concurrent.futures.TimeoutError:
                operation.status = OperationStatus.TIMEOUT
                operation.error = f"操作超时（>{operation.max_timeout}s）"
                operation.completed_at = time.time()
                # ★7-1/P1-13：本段运行在共享线程池的工作线程内，需原子化。
                with self._lock:
                    self._total_timeout += 1
                # ★FIX(问题16): 尝试取消未完成任务，并告警底层线程可能残留
                try:
                    _future.cancel()
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                _logger.warning(f"操作超时，已尝试取消: {operation.operation_type}")
                if operation.on_failed:
                    try:
                        operation.on_failed(operation)
                    except Exception as e:
                        self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                self._history.append(operation)
                return
            # ★PERF-3修复: 复用模块级共享线程池，不在每次操作结束后关闭

            operation.result = result
            operation.status = OperationStatus.COMPLETED
            operation.completed_at = time.time()
            operation.progress = 1.0
            # ★7-1/P1-13：同上，工作线程内计数需原子化。
            with self._lock:
                self._total_completed += 1

            if operation.on_completed:
                try:
                    operation.on_completed(operation)
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # 记录历史
            self._history.append(operation)

        except Exception as e:
            operation.status = OperationStatus.FAILED
            operation.error = str(e)
            operation.completed_at = time.time()
            # ★7-1/P1-13：异常分支同样位于工作线程内。
            with self._lock:
                self._total_failed += 1

            if operation.on_failed:
                try:
                    operation.on_failed(operation)
                except Exception as e:
                    _logger.debug(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            self._history.append(operation)

        finally:
            # 从活跃列表移除
            with self._lock:
                self._active_operations.pop(operation.task_id, None)

            # 尝试执行下一个任务
            self._try_execute_next()
    
    # ========== 去重机制 ==========
    
    # [批次4·深度体检][PERF-4] 去重缓存惰性清理，避免无界增长
    def _is_duplicate(self, operation_type: str, dedup_key: str) -> bool:
        """检查操作是否在冷却期内（同时惰性清理已过期条目，避免 _dedup_cache 无界增长）"""
        cache_key = f"{operation_type}:{dedup_key[:80]}"
        with self._lock:
            if operation_type not in self._dedup_cache:
                return False
            _bucket = self._dedup_cache[operation_type]
            last_time = _bucket.get(cache_key, 0)
            if (time.time() - last_time) < self._dedup_interval:
                return True
            # ★PERF-4修复: 超过冷却期则删除该条目，防止缓存无界增长（内存泄漏）
            _bucket.pop(cache_key, None)
            return False
    
    def _mark_submitted(self, operation_type: str, dedup_key: str):
        """标记操作已提交"""
        cache_key = f"{operation_type}:{dedup_key[:80]}"
        with self._lock:
            if operation_type not in self._dedup_cache:
                self._dedup_cache[operation_type] = {}
            self._dedup_cache[operation_type][cache_key] = time.time()
    
    # ========== 状态查询 ==========
    
    def get_operation_status(self, task_id: str) -> dict[str, Any] | None:
        """查询指定任务的状态"""
        with self._lock:
            if task_id in self._active_operations:
                op = self._active_operations[task_id]
            else:
                for op in self._history:
                    if op.task_id == task_id:
                        break
                else:
                    return None
            
            return {
                "task_id": op.task_id,
                "operation_type": op.operation_type,
                "priority": op.priority.name,
                "status": op.status.value,
                "view_mode": op.view_mode,
                "progress": op.progress,
                "submitted_at": op.submitted_at,
                "started_at": op.started_at,
                "completed_at": op.completed_at,
                "error": op.error,
            }
    
    def get_active_count(self) -> int:
        """获取当前活跃任务数"""
        return len(self._active_operations)
    
    def get_queue_size(self) -> int:
        """获取当前排队任务数"""
        with self._lock:
            return (len(self._urgent_queue) + len(self._high_queue) + 
                    len(self._normal_queue) + len(self._low_queue))
    
    def get_stats(self) -> dict[str, Any]:
        """获取调度器统计信息"""
        with self._lock:
            return {
                "total_submitted": self._total_submitted,
                "total_completed": self._total_completed,
                "total_failed": self._total_failed,
                "total_timeout": self._total_timeout,
                "active_count": len(self._active_operations),
                "queue_size": (len(self._urgent_queue) + len(self._high_queue) + 
                              len(self._normal_queue) + len(self._low_queue)),
                "max_concurrent": self._max_concurrent,
                "history_size": len(self._history),
            }
    
    # ========== 生命周期 ==========
    
    def shutdown(self):
        """优雅关闭调度器"""
        self._shutting_down = True
        with self._lock:
            # 取消所有排队任务
            for queue in [self._urgent_queue, self._high_queue, 
                         self._normal_queue, self._low_queue]:
                while queue:
                    op = queue.popleft()
                    op.status = OperationStatus.CANCELLED
                    op.error = "调度器已关闭"
            # 等待活跃任务完成（最多等待10秒）
            deadline = time.time() + 10
            while self._active_operations and time.time() < deadline:
                time.sleep(0.1)


# ========== 模块级单例 ==========
_external_executor: ExternalExecutor | None = None
_external_executor_lock = threading.Lock()


def get_external_executor() -> ExternalExecutor:
    """获取ExternalExecutor单例"""
    global _external_executor
    if _external_executor is None:
        with _external_executor_lock:
            if _external_executor is None:
                _external_executor = ExternalExecutor()
    return _external_executor


def shutdown_global_executor():
    """关闭模块级共享执行器并复位单例（★P0批次3：满足器官零状态 规则4）。

    既关闭底层共享线程池 _OP_EXECUTOR，也将模块级单例 _external_executor 置空，
    使进程重启/重新初始化时 get_external_executor() 能重建干净实例，
    而非复用已停止的实例（原实现仅清子执行器，未复位单例本身）。
    """
    global _OP_EXECUTOR, _SCHEDULER_EXECUTOR, _external_executor
    try:
        if _OP_EXECUTOR is not None:
            _OP_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    except Exception as e:
        silent_exc(e, where="nucleus.external_executor::shutdown_global_executor L514")
    finally:
        _OP_EXECUTOR = None
    # ★A1【P0】：外层调度池一并关闭并复位
    try:
        if _SCHEDULER_EXECUTOR is not None:
            _SCHEDULER_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    except Exception as e:
        silent_exc(e, where="nucleus.external_executor::shutdown_global_executor L522")
    finally:
        _SCHEDULER_EXECUTOR = None
    _inst = _external_executor
    _external_executor = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as e:
                _logger.debug(f"清理异常已忽略: {type(e).__name__}: {e}")
