# -*- coding: utf-8 -*-
"""
parallel_scheduler.py —— 并行调度器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 通用并行任务调度与结果聚合
机制: 基于ParallelScheduler类实现，包含10个核心方法
定位: 任务调度层
"""

from config import TIMEOUT_CONFIG
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底
from nucleus.logger import exc_location  # ★第32批 T3：异常位置动态获取
from nucleus._silent_except import silent_exc

# ★P2-64 并发收敛（D3-A）：parallel 作为唯一门面，内部委托保留实现的 Hybrid/Structured。
#   nucleus/parallel_scheduler.py 已在 tools/ci/check_deprecated_imports.py 的 EXEMPT_FILES 中豁免。
from nucleus.HybridParallelScheduler import TaskType as _HybridTaskType, get_hybrid_scheduler
from nucleus.StructuredParallelScheduler import SubTask, get_structured_parallel_scheduler

__all__ = [
    "ParallelScheduler",
    "get_parallel_scheduler",
    "shutdown_parallel_scheduler",
    "SubTask",
]



class ParallelScheduler(SilentLogMixin):
    """全局并行调度器（硬件自适应）。"""

    # ★三期：安全熔断上限（R5 红线，永久保留，防止线程爆炸/OOM）
    _HARD_FUSE_MAX_PARALLELISM = 64

    def __init__(self):
        self._lock = threading.Lock()
        self._cpu_count = os.cpu_count() or 8
        self._physical_cores = 0  # ★v9.5深度硬件自适应：物理核（update_hardware_capability 注入）
        self._memory_usage_pct = None
        self._gpu = None
        self._tier = "high"  # ★三期：硬件 tier，默认 high（可被 update_hardware_capability 覆盖）
        self._adaptive_enabled = self._read_adaptive_flag()
        self._base_parallelism = self._calc_base_parallelism()
        self._current_parallelism = self._base_parallelism
        self._pool: ThreadPoolExecutor | None = None
        self._last_adjust_time = 0.0
        self._adjust_interval = 30.0  # 30 秒调整一次
        self._adjust_cooldown_cycles = 2  # ★三期：调整冷却（至少 N 个周期内不再调整）
        self._last_adjust_cycle = 0
        self._adjust_cycle_counter = 0

    @staticmethod
    def _read_adaptive_flag() -> bool:
        """读取 use_runtime_adaptive_tuning 开关（保守兜底 False）。"""
        try:
            from config import FEATURE
            return bool(FEATURE.get("use_runtime_adaptive_tuning", False))
        except Exception as e:
            silent_exc(e, where="nucleus.parallel_scheduler::_read_adaptive_flag L57")
            return False

    def update_hardware_capability(self, tier: str | None = None, cores: int | None = None,
                                   physical_cores: int | None = None,
                                   memory_usage_pct: float | None = None,
                                   gpu: dict | None = None) -> None:
        """★v9.5深度注入硬件能力快照（tier/cores/物理核/内存余量/GPU），重算并行度。

        由 main.py 在装配后拿到深度硬件探测结果后调用（打通断链）。
        关闭时保持原行为（零回归）。
        """
        if not self._adaptive_enabled:
            return
        _changed = False
        if tier in ("high", "standard", "minimal") and tier != self._tier:
            self._tier = tier
            _changed = True
        if cores and cores > 0 and cores != self._cpu_count:
            self._cpu_count = cores
            _changed = True
        if physical_cores and physical_cores > 0 and physical_cores != getattr(self, "_physical_cores", 0):
            self._physical_cores = physical_cores
            _changed = True
        if memory_usage_pct is not None:
            self._memory_usage_pct = memory_usage_pct
        if gpu:
            self._gpu = gpu
        if _changed:
            with self._lock:
                self._base_parallelism = self._calc_base_parallelism()
                self._current_parallelism = self._base_parallelism
                self._rebuild_pool()

    def _calc_base_parallelism(self) -> int:
        """★v9.5纯硬件自适应：按实际硬件计算并行度，不写死固定上下限。

        算法（全部来自硬件实际性能）：
          ① 物理核数为基准。超线程核心副助度低，取物理核×1.5 →
             兼顾红流期与并发占用。如未知物理核，用逻辑核×0.5保底。
          ② 内存余量校准：内存使用率 >90% 时加权×0.5（防 OOM）。
          ③ 硬件 tier 保留作“保命场景”降级补丁（minimal ×0.5，其余不置曲线）。
          ④ 不设下限，上限仅保留安全融断 64（防线程爆炸/OOM）。
        “不写死固定并行/异步数量”：自适应任何硬件，随物理核/内存自动推演。
        """
        _logical = max(1, self._cpu_count or 1)

        # ① 硬件基准：优先物理核，没有则用逻辑核保底
        _physical = getattr(self, "_physical_cores", 0) or 0
        if _physical > 0:
            # 超线程带来的辅助性能约 +50%，而非简单×2
            _base = max(2, int(_physical * 1.5))
        else:
            _base = max(2, int(_logical * 0.6))

        # ② 内存余量校准（防 OOM）
        _mem_pct = getattr(self, "_memory_usage_pct", None)
        if _mem_pct is not None and _mem_pct > 90.0:
            _base = max(2, int(_base * 0.5))

        # ③ 保命场景降级补丁
        if getattr(self, "_tier", "high") == "minimal":
            _base = max(2, _base // 2)

        # ④ 上限仅保留安全融断
        return min(_base, self._HARD_FUSE_MAX_PARALLELISM)

    def get_parallelism(self) -> int:
        """获取当前建议并行度（供各模块查询）。"""
        self._maybe_adjust()
        return self._current_parallelism

    def _maybe_adjust(self):
        """根据运行时负载动态调整并行度（队列积压大→提升，空闲→回落）。

        ★三期增强：震荡抑制——调整后至少 _adjust_cooldown_cycles 个周期内不再调整，
        且仅在开关开启时走动态上限；开关关闭时保持原行为（零回归）。
        """
        # ★P1-2（并行度探测加锁）：节流检查+更新时间原子化。
        # 此前非原子：并发 get_parallelism()（推理池每次 submit 都会调）可同时通过
        # 节流窗口，导致 _current_parallelism 被多线程瞬时覆盖（日志出现过并行度
        # 12→24→6 剧烈波动而调度器平滑记录仅到 12 的矛盾）。
        with self._lock:
            _now = time.time()
            if _now - self._last_adjust_time < self._adjust_interval:
                return
            self._last_adjust_time = _now

        # ★三期：震荡抑制（调整冷却）
        # ★7-1/P1-13修复(2026-09-05)：上方 :136-141 的锁块此时已释放，
        #   本计数器裸自增存在竞态。单独加锁保护；刻意不并入上方锁块，
        #   以免扩大临界区、影响调度自适应的响应灵敏度。
        with self._lock:
            self._adjust_cycle_counter += 1
        if self._adjust_cycle_counter <= self._last_adjust_cycle:
            return
        # 若本周期不做调整，重置冷却计数（冷却只约束「连续调整」）
        # 实际冷却逻辑在下方 _target 变化时生效，见 _last_adjust_cycle 更新。

        try:
            from nucleus.runtime_metrics import get_runtime_metrics
            _snap = get_runtime_metrics().get_snapshot()
            _queue_depth = _snap.get("queue_max_depth", 0)
            _pulse_avg_ms = _snap.get("pulse_avg_ms", 0.0)
        except Exception as e:
            silent_exc(e, where="nucleus.parallel_scheduler::_maybe_adjust L163")
            _queue_depth = 0
            _pulse_avg_ms = 0.0

        _target = self._base_parallelism
        if _queue_depth > 200 or _pulse_avg_ms > 50:
            # ★三期：动态上限（去硬编码）；开关关闭时 _base_parallelism 已含封顶，等价原行为
            _target = min(self._current_parallelism * 2, self._base_parallelism * 2)
            if self._adaptive_enabled:
                _target = min(_target, self._HARD_FUSE_MAX_PARALLELISM)
            else:
                _target = min(_target, self._cpu_count)
        elif _queue_depth < 50 and _pulse_avg_ms < 10:
            _target = max(2, self._base_parallelism // 2)

        # ★v9.5双向硬件自适应：资源不足降级 / 恢复后升级
        # 硬件资源压力优先于任务负载：压力高时即使任务积压也不能盲目升。
        _hw = self._probe_hardware_pressure()
        _hw_pressure = _hw.get("pressure", "low")
        if _hw_pressure == "high":
            # 硬降级：并行度压到最低安全档（仍保持 >=2 保命）
            _target = min(_target, max(2, self._base_parallelism // 4))
        elif _hw_pressure == "medium" and _target > self._base_parallelism:
            # 中等压力：不允许超过基础并行度
            _target = self._base_parallelism
        # ★v9.5 CPU核数物理约束：并行度绝不超逻辑核（16），
        # 否则进程池/线程池叠加主进程+信息场线程池会 oversubscribe。
        # 此前缺此维度：内存/显存充足(压力=low)时并行度惯性爬升到 24，
        # 实际已超逻辑核 16，造成 CPU 争抢。此约束对三个压力等级都生效。
        if _target > self._cpu_count:
            _target = max(2, self._cpu_count)
        # 低压力：不干预，任务负载驱动自然升降（恢复后自动升级）

        # ★v9.5平滑步进：不断崖提升/压到底，每周期只走一档
        # 降级时 12→6→3 分步回落（避免一步到底断崖）
        # 升级时 3→5→8→12 分步爬升（避免一步拉满爆表）
        _target = self._smooth_step(self._current_parallelism, _target)

        if _target != self._current_parallelism:
            with self._lock:
                # ★v9.5修复：先记录旧值再赋值，否则 _target>self._current_parallelism 恒为False，
                # 导致日志方向恒为「降级」且两数值相等（11→11/8→8 等误导信息）。
                _old_parallelism = self._current_parallelism
                self._current_parallelism = _target
                self._rebuild_pool()
                # ★v9.5：平滑步进时记录日志（INFO 仅突变时，DEBUG 常规）
                try:
                    import logging as _lg
                    _log = _lg.getLogger("pulse.module.ParallelScheduler")
                    if _log is not None:
                        _step_dir = "升级" if _target > _old_parallelism else "降级"
                        _log.info(f"并行度平滑{_step_dir}: "
                                  f"{_old_parallelism} → {_target} "
                                  f"（压力={_hw_pressure}）")
                except Exception as e:
                    silent_exc(e, where="nucleus.parallel_scheduler::_maybe_adjust L217")
                # ★三期：记录本次调整周期，接下来 _adjust_cooldown_cycles 个周期不再调整
                self._last_adjust_cycle = self._adjust_cycle_counter

    def _smooth_step(self, current: int, target: int) -> int:
        """★v9.5平滑步进：从 current 向 target 只走一档（防断崖、防爆表）。

        每次最多移动一个档位：
          - 升级：每步增加 max(1, current//4) ，即约 +25%。
          - 降级：每步减少 max(1, current//4) ，即约 -25%。
        配合 30s 调整间隔与冷却，高压力回落约需 2-3 个周期，
        恢复升级约需 3-5 个周期，平滑过渡。
        """
        if target == current:
            return target
        # 步长 = 目标距离的 1/4（约 ±25%/步），至少 1，不超过目标距离
        _step = max(1, min(abs(target - current) // 4 or 1, abs(target - current)))
        if target > current:
            return min(target, current + _step)
        return max(target, current - _step)

    def _probe_hardware_pressure(self) -> dict[str, Any]:
        """★v9.5双向硬件自适应：轻量采集当前硬件资源压力。

        采集维度：内存使用率（psutil，快）+ GPU 显存使用率
        （低频刷新 60s 缓存，避免频繁 nvidia-smi 子进程。

        压力等级（滞回防抖振）：
          high   = 内存>90% 或 GPU显存>95%   → 硬降级
          medium = 内存>75% 或 GPU显存>80%   → 不超基础并行度
          low    = 严格低于上述阈值       → 不干预（允许升级）
        任何采集失败回退 low（保守不误伤）。
        """
        _r: dict[str, Any] = {"memory_usage_pct": None, "gpu_mem_usage_pct": None, "pressure": "low"}
        try:
            import psutil
            _r["memory_usage_pct"] = psutil.virtual_memory().percent
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # GPU 显存：低频刷新（60s 缓存）
        _now = time.time()
        _gpu_cache = getattr(self, "_gpu_mem_cache", None)
        if _gpu_cache is None or _now - _gpu_cache[0] > 60.0:
            _fresh = self._refresh_gpu_mem_usage()
            self._gpu_mem_cache = (_now, _fresh)
        else:
            _fresh = _gpu_cache[1]
        if _fresh is not None:
            _r["gpu_mem_usage_pct"] = _fresh

        _mem = _r.get("memory_usage_pct")
        _g = _r.get("gpu_mem_usage_pct")
        if (_mem is not None and _mem > 90.0) or (_g is not None and _g > 95.0):
            _r["pressure"] = "high"
        elif (_mem is not None and _mem > 75.0) or (_g is not None and _g > 80.0):
            _r["pressure"] = "medium"
        return _r

    def _refresh_gpu_mem_usage(self) -> float | None:
        """低频刷新 GPU 显存使用率（%），失败返回 None。"""
        # ★G1：优先用 GPUCore（torch 精确探测），失败回退 nvidia-smi
        try:
            from nucleus.GPUCore import get_gpu_core
            _info = get_gpu_core().probe_gpu()
            if _info.get("available"):
                _used = _info.get("vram_used_pct")
                if _used is not None:
                    return float(_used)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        _gpu = getattr(self, "_gpu", None)
        if not _gpu or not _gpu.get("available"):
            return None
        _total = _gpu.get("memory_total_mb") or 0
        if _total <= 0:
            return None
        try:
            import subprocess
            _r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                check=False, capture_output=True, text=True, timeout=TIMEOUT_CONFIG['hardware_probe_fast'],
                encoding="utf-8", errors="replace",
            )
            if _r.returncode == 0 and _r.stdout.strip():
                _parts = [s.strip() for s in _r.stdout.strip().split(",")]
                if len(_parts) >= 2 and _parts[0].isdigit() and int(_parts[1]) > 0:
                    return round(float(_parts[0]) / float(_parts[1]) * 100.0, 1)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return None

    def get_hardware_pressure(self) -> str:
        """★v9.5：资源压力信号（low/medium/high），供其他模块决策降级/升级。"""
        return self._probe_hardware_pressure().get("pressure", "low")

    def _rebuild_pool(self):
        """并行度变化时重建线程池。"""
        _old = self._pool
        self._pool = ThreadPoolExecutor(
            max_workers=self._current_parallelism, thread_name_prefix="cortex_parallel"
        )
        if _old:
            try:
                _old.shutdown(wait=False)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def get_pool(self) -> ThreadPoolExecutor:
        """获取当前线程池（懒初始化）。"""
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(
                        max_workers=self._current_parallelism, thread_name_prefix="cortex_parallel"
                    )
        return self._pool

    def submit_parallel(self, tasks: list[Callable]) -> list[Any]:
        """并行提交一组任务，返回结果列表（保持顺序）。"""
        _pool = self.get_pool()
        _futures = [_pool.submit(_t) for _t in tasks]
        return [_f.result() for _f in _futures]


    # ★P2-64 并发收敛（D3-A）：门面补全——CPU 密集（进程池）与任务组扇出，
    #   内部委托给保留实现的 Hybrid/Structured（零行为变更，仅路由）。
    def submit_cpu_bound(self, func: Callable[..., Any], *args: Any,
                         task_name: str = "", **kwargs: Any):
        """CPU 密集型任务走进程池后端（委托 HybridParallelScheduler）。

        仅在确需真并行（绕过 GIL）时使用；IO/通用任务仍走 submit_parallel 线程池。
        """
        _hs = get_hybrid_scheduler()
        return _hs.submit(func, *args, task_type=_HybridTaskType.CPU_BOUND,
                          task_name=task_name, **kwargs)

    def run_group(self, name: str, subtasks: list,
                  aggregator: Callable[[dict], Any] | None = None,
                  timeout: float = 30.0):
        """任务组扇出 + 协调者汇总（委托 StructuredParallelScheduler）。

        subtasks 为 nucleus.parallel_scheduler.SubTask 列表（本模块再导出）。
        返回 StructuredParallelScheduler.TaskGroupResult（含 aggregated_result /
        success_count / failed_count / duration_ms / group_id / status）。
        """
        _sps = get_structured_parallel_scheduler()
        return _sps.run_group(name=name, subtasks=subtasks,
                              aggregator=aggregator, timeout=timeout)
    def is_parallelizable(self, task_name: str) -> bool:
        """判断任务是否适合并行（有顺序依赖/副作用/写文件的任务返回 False）。"""
        _serial_markers = (
            "snapshot_save", "patch_apply", "file_write",
            "shutdown", "stop", "restart", "commit",
        )
        return not any(_m in task_name for _m in _serial_markers)

    def get_stats(self) -> dict[str, Any]:
        return {
            "cpu_count": self._cpu_count,
            "tier": self._tier,
            "adaptive_enabled": self._adaptive_enabled,
            "base_parallelism": self._base_parallelism,
            "current_parallelism": self._current_parallelism,
        }

    def shutdown(self):
        """关闭线程池（★FIX: 进程退出清理，避免非 daemon 线程阻止退出）。"""
        try:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None
        except Exception:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")


# 模块级单例（双检锁）
_scheduler: ParallelScheduler | None = None
_scheduler_lock = threading.Lock()


def get_parallel_scheduler() -> ParallelScheduler:
    """获取 ParallelScheduler 单例。"""
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = ParallelScheduler()
    return _scheduler


def shutdown_parallel_scheduler() -> None:
    """★P0批次3：关闭并复位 ParallelScheduler 单例，满足器官零状态（规则4）。

    原停机流程直接调用 get_parallel_scheduler().shutdown() 关闭线程池，但全局变量
    _scheduler 仍持有已关闭实例。此处显式置空，使重启时重建全新调度器。
    """
    global _scheduler
    _inst = _scheduler
    _scheduler = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as e:
                silent_exc(e, where="nucleus.parallel_scheduler::shutdown_parallel_scheduler L396")
