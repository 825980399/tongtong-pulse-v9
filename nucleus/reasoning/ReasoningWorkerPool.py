# -*- coding: utf-8 -*-
"""
ReasoningWorkerPool.py —— 推理工作池

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 多推理任务的并行工作池
机制: 基于ReasoningWorkerPool类实现，包含10个核心方法
定位: 推理调度层
"""

import os
import sys
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from typing import Any

from nucleus.logger import get_module_logger
from nucleus._silent_except import silent_exc


_module_logger = get_module_logger("ReasoningWorkerPool")


def _is_alive(proc) -> bool:
    """★P2-24：安全判断子进程是否存活。

    `Process.is_alive()` 在进程已被回收/句柄失效时可能抛异常，
    关闭路径上不能因为一次探测失败就放弃整个优雅关闭流程。
    """
    try:
        return bool(proc.is_alive())
    except Exception as e:
        silent_exc(e, "nucleus/reasoning/ReasoningWorkerPool.py:36:推理工作池操作异常", level="warning")
        return False


class ReasoningWorkerPool:
    """
    推理任务进程池。
    
    使用 ProcessPoolExecutor 在独立进程中执行 CPU 密集的推理任务，
    绕过 Python GIL 限制，充分利用多核 CPU。
    """
    
    def __init__(self):
        # ★P1 优化：进程数接入全局并行调度器信号（硬件自适应），与其余线程池统一调度信号源。
        # 全局并行度是「建议线程并行度」，进程池绕过 GIL 跑 CPU 密集任务，取该值的 0.75 倍
        # 留出余量给主进程与 I/O，避免与其余线程池叠加后 oversubscribe CPU。
        # 调度器不可用时回退到原 0.75×CPU 硬编码，保证进程池仍可启动。
        _cpu_count = os.cpu_count() or 8
        _fallback = max(2, int(_cpu_count * 0.75))
        try:
            from nucleus.parallel_scheduler import get_parallel_scheduler
            _global_p = get_parallel_scheduler().get_parallelism()
            _global_p = max(2, int(_global_p))
        except Exception:
            _global_p = 0
        # ★v9.5硬件自适应（调回 0.75×）：worker 随并行度走，但保留缩减系数。
        # 过程池绕过 GIL、每过程独占核心，必须给主进程 + 信息场4层线程池留余量，
        # 否则 16 逻辑核 oversubscribe（同时性能反而下降）。
        # 参考：并行度12(空闲回落 6) → worker 9(空闲 4)；仍随硬件自适应，不写死固定值。
        self._max_workers = max(2, int(_global_p * 0.75)) if _global_p > 0 else _fallback
        self._parallelism_at_init = _global_p  # ★v9.5：记录并行度供日志
        self._pool: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()
        # ★H1（v9.5-worker动态调整）：平滑重建状态
        self._last_resize_time = 0.0        # 上次重建时间
        self._resize_min_interval = 120.0   # 重建节流：最短间隔（秒）
        self._resize_delta_threshold = 3    # 并行度差异触发阈值
        self._resize_lock = threading.Lock()  # ★P1-1：重建决策原子化（防并发连续重建）
        self._task_count = 0
        self._success_count = 0
        self._fail_count = 0
        self._enabled = True
        # ★A4【P1】：追踪进行中的 Future，供重建时等待未完成任务迁移结果，
        # 避免旧池 shutdown(wait=False) 导致运行中任务结果丢失。
        self._inflight_futures: set[Future] = set()

        # ===== ★主线第15批 T2/P1-92：崩溃后自动重建 =====
        # 背景（12h 实测 12 次）：`A child process terminated abruptly, the process
        #   pool is not usable anymore` 之后**没有任何重建**，进程池永久失效，
        #   FrequencyCodec.encode_batch / verify_patch_effect_in_process 持续失败。
        # 机制：submit 捕获 BrokenProcessPool → 指数退避重建 → 重试本次提交；
        #   连续失败达上限则降级为**同进程同步执行**（返回已完成的 Future，功能不失效）；
        #   另有定期健康检查提前发现 _broken。
        # 灰度 ENABLE_POOL_AUTO_REBUILD（默认 True）；关闭时与修复前行为一致。
        self._rebuild_lock = threading.RLock()      # 重建串行化（重建期间提交排队等待）
        self._rebuild_count = 0                     # 成功重建次数
        self._rebuild_consecutive_failures = 0      # 连续重建失败次数
        self._rebuild_duration_ms_max = 0.0         # 单次重建最长耗时（毫秒）
        self._broken_detected_count = 0             # 检测到 BrokenProcessPool 的次数
        self._degraded_sync = False                 # True=已降级为同步执行
        self._pool_generation = 1                   # 进程池代数（每次重建 +1）
        self._last_health_check = 0.0
        self._last_degraded_recover = 0.0           # ★第22批 T2：上次「降级后恢复尝试」时间
        self._closed = False                        # shutdown 后禁止再重建（防停机复活）
        self._pool_config_cache: tuple[bool, int, float] | None = None

        # 初始化进程池
        self._init_pool()
    
    def _init_pool(self):
        """初始化进程池"""
        try:
            self._pool = ProcessPoolExecutor(
                max_workers=self._max_workers,
            )
            _module_logger.info(f"推理进程池已启动: {self._max_workers}个工作进程 "
                  f"(CPU={os.cpu_count()}核, 并行度={self._parallelism_at_init})")
        except Exception as e:
            _module_logger.warning(f"进程池初始化失败: {e}，回退到同步执行")
            self._pool = None
            self._enabled = False
    
    @staticmethod
    def _deep_think_bypass_enabled() -> bool:
        """★主线第31批 T1（P2-185）：深度思考任务是否跳过进程池提交。

        `PulseInnerWorld._deep_think` 依赖**主进程内存态**（node_pool /
        knowledge_tree / _model_cache），子进程无法执行它 ——
        `_execute_reasoning_task` 对该任务恒返回降级标记（第15批 P1-5 架构决策）。
        既然结果 100% 无效，就不必付一次跨进程往返（pickle question + 进程调度 +
        Future 等待）。直接返回 None，调用方 `if _future:` 判空即回退主进程同步
        执行 —— 与 submit 的既有失败路径语义完全一致。

        灰度 `ENABLE_DEEP_THINK_SUBPROCESS_BYPASS`（默认 True）；关闭时保持原行为。
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_DEEP_THINK_SUBPROCESS_BYPASS", True))
        except Exception:
            return True

    def submit(self, func_name: str, *args, **kwargs) -> Future | None:
        # ★主线第31批 T1（P2-185）：有状态任务不进进程池 —— 见方法 docstring。
        if func_name == "PulseInnerWorld._deep_think" and self._deep_think_bypass_enabled():
            return None
        # ★主线第15批 T2/P1-92：已降级为同步执行 → 直接在主进程跑完并返回已完成 Future
        if self._degraded_sync:
            # ★主线第22批 T2/P2-120：降级后定期恢复尝试。
            #   原实现此分支直接返回，**永远不会**走到 _maybe_health_check
            #   （那里在 _degraded_sync 时也直接 return）→ 一旦降级便永久同步
            #   执行，推理性能再也回不去。此处按配置间隔(默认300s)节流，
            #   用最小配置(1 worker)尝试重建；失败仅 DEBUG，不重复 ERROR。
            self._maybe_recover_from_degraded()
            if self._degraded_sync:
                return self._sync_future(func_name, *args, **kwargs)
        if not self._enabled or self._pool is None:
            # 池缺失（初始化失败或崩溃后被摘引用）→ 尝试自动重建
            #   （_closed=True 表示已主动 shutdown，禁止复活）
            if not self._closed and self._rebuild_pool(
                    reason=f"池不可用(submit 前, {func_name})"):
                pass
            elif self._pool is None:
                if self._pool_auto_rebuild_config()[0] and not self._closed:
                    # 重建失败但自动重建开关开启 → 同进程同步执行，保证功能可用
                    return self._sync_future(func_name, *args, **kwargs)
                return None

        # ★v9.5硬件压力门控：高压力时降级为同步（不新建进程，
        # 减轻内存/显存压力）；恢复后自动回进程池。
        # 调用方对 submit 返回 None 的处理都是安全降级（如 PulseLiver 仅 if _future:）。
        try:
            from nucleus.parallel_scheduler import get_parallel_scheduler
            if get_parallel_scheduler().get_hardware_pressure() == "high":
                # 门控时进入：低频日志（防刷屏）
                try:
                    import time as _t
                    _now = _t.time()
                    if _now - getattr(self, "_last_gate_log", 0) > 300.0:
                        self._last_gate_log = _now
                        _module_logger.warning(
                            "硬件压力高→推理降级为同步: %s", func_name)
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                return None
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ★H1（v9.5-worker动态调整）：随全局并行度平滑重建进程池。
        # 并行度显著变化时重建（新worker=并行度×0.75），资源变差→缩池、
        # 变好→扩池；旧池优雅退出不取消运行任务。压力门控管瞬时尖峰，
        # 本重建管持续变化，二者互补。
        try:
            self._maybe_resize_pool()
        except Exception as e:
            _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ★主线第15批 T2/P1-92：定期健康检查（按间隔节流），提前发现 broken 并重建
        try:
            self._maybe_health_check()
        except Exception as e:
            _module_logger.debug(f"进程池健康检查异常（已忽略）: {type(e).__name__}: {e}")

        with self._lock:
            self._task_count += 1

        # ★主线第15批 T2/P1-92：崩溃 → 重建 → 重试本次提交（最多两轮）
        for _attempt in range(2):
            try:
                future = self._pool.submit(
                    self._execute_reasoning_task,
                    func_name,
                    *args,
                    **kwargs
                )
                # ★A4【P1】：登记 inflight Future，供重建时等待
                with self._lock:
                    self._inflight_futures.add(future)
                return future
            except BrokenProcessPool as e:
                # 进程池已被判定不可用：先重建，成功则重试本次提交
                _module_logger.warning(
                    f"检测到进程池崩溃({func_name}): {type(e).__name__}: {e}")
                if not self._pool_auto_rebuild_config()[0]:
                    # ★灰度关闭：与修复前完全一致（记 ERROR 后返回 None，
                    #   由调用方按既有 `if _future:` 判空逻辑自行降级）
                    with self._lock:
                        self._fail_count += 1
                    _module_logger.error(f"任务提交失败({func_name}): {e}")
                    return None
                if not self._rebuild_pool(
                        reason=f"BrokenProcessPool({func_name}): {e}"):
                    # 重建失败/已降级 → 同进程同步执行，保证功能可用
                    return self._sync_future(func_name, *args, **kwargs)
                continue
            except Exception as e:
                with self._lock:
                    self._fail_count += 1
                # ★P1-5修复：打印异常日志便于排查
                try:
                    import traceback
                    _module_logger.error(
                        f"任务提交失败({func_name}): {e}\n{traceback.format_exc()[:300]}")
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                return None
        # 重试后仍失败 → 降级同步（不再无限重试）
        _module_logger.error(
            f"进程池重建后仍提交失败，降级为同步执行: {func_name}")
        if not self._pool_auto_rebuild_config()[0]:
            with self._lock:
                self._fail_count += 1
            return None
        return self._sync_future(func_name, *args, **kwargs)
    
    @staticmethod
    def _execute_reasoning_task(func_name: str, *args, **kwargs):
        """
        在独立进程中执行的推理任务入口。
        ★P1-5修复：仅支持纯计算任务（批量频率编码、批量共振计算），
        不再尝试在子进程中重建PulseInnerWorld实例。
        深度思考任务改为主进程同步执行。
        """
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        
        # 批量频率编码
        if func_name == "FrequencyCodec.encode_batch":
            try:
                from nucleus.pulse.FrequencyCodec import FrequencyCodec
                _codec = FrequencyCodec()
                _results = []
                for _node_dict in args[0] if args else []:
                    try:
                        from nucleus.mnemosyne.PulseNode import PulseNode
                        _node = PulseNode.from_dict(_node_dict)
                        _codec.encode_node(_node)
                        _results.append(_node.frequency_signature)
                    except Exception:
                        _results.append(0.0)
                return _results
            except Exception as e:
                import traceback
                _module_logger.error(f"FrequencyCodec.encode_batch 失败: {e}\n{traceback.format_exc()[:300]}")
                return []
        
        # 批量共振计算
        if func_name == "ResonanceEngine.resonate_batch":
            try:
                from nucleus.synapsys.ResonanceEngine import ResonanceEngine
                _engine = ResonanceEngine()
                _query_pulse = args[0] if args else {}
                _candidates = args[1] if len(args) > 1 else []
                return _engine.resonate(_query_pulse, _candidates, top_k=20)
            except Exception:
                return []
        
        # ★P1-5修复：深度思考任务返回明确的降级标记
        # 子进程无法访问node_pool/knowledge_tree等核心依赖，
        # 深度思考应由主进程同步执行以保证推理质量。
        if func_name == "PulseInnerWorld._deep_think":
            return {
                "status": "degraded",
                "reason": "子进程无知识上下文，请改用主进程同步执行深度思考",
                "result": None,
            }
        
        return {"func_name": func_name, "args": args, "kwargs": kwargs,
                "status": "delegated"}
    
    def get_stats(self) -> dict[str, Any]:
        """获取进程池统计"""
        with self._lock:
            _stats = {
                "max_workers": self._max_workers,
                "enabled": self._enabled,
                "task_count": self._task_count,
                "success_count": self._success_count,
                "fail_count": self._fail_count,
            }
        # ★主线第15批 T2/P1-92：并入重建统计（供自省/诊断）
        try:
            _stats.update(self.get_rebuild_stats())
        except Exception:
            pass
        return _stats
    
    # ==================================================================
    # ★主线第15批 T2/P1-92：进程池崩溃自动重建
    # ==================================================================
    @staticmethod
    def _pool_auto_rebuild_config() -> tuple[bool, int, float]:
        """读取自动重建配置：(开关, 最大重试次数, 健康检查间隔秒)。

        配置缺失/异常时回退到安全默认值 (True, 3, 60.0)。
        """
        enabled, max_retries, interval = True, 3, 60.0
        try:
            import config as _cfg
            enabled = bool(getattr(_cfg, "ENABLE_POOL_AUTO_REBUILD", True))
            max_retries = int(getattr(_cfg, "POOL_REBUILD_MAX_RETRIES", 3) or 3)
            interval = float(getattr(_cfg, "POOL_HEALTH_CHECK_INTERVAL", 60) or 60.0)
        except Exception as _e:
            _module_logger.debug(
                f"进程池自动重建配置读取失败，使用默认值(True,3,60): "
                f"{type(_e).__name__}: {_e}")
        return enabled, max(0, max_retries), max(1.0, interval)

    def _is_pool_broken(self) -> bool:
        """判断当前进程池是否已不可用（None / 已标记 _broken）。"""
        if self._pool is None:
            return True
        try:
            return bool(getattr(self._pool, "_broken", False))
        except Exception:
            return True

    def _maybe_health_check(self) -> None:
        """定期健康检查：发现进程池已 broken 则提前重建（按间隔节流）。"""
        enabled, _retries, interval = self._pool_auto_rebuild_config()
        if not enabled or self._degraded_sync:
            return
        _now = time.time()
        if _now - self._last_health_check < interval:
            return
        self._last_health_check = _now
        if self._is_pool_broken():
            _module_logger.warning("推理进程池健康检查发现不可用，触发提前重建")
            self._rebuild_pool(reason="健康检查发现进程池不可用")

    def _sync_future(self, func_name: str, *args, **kwargs) -> Future:
        """★降级路径：在主进程同步执行并返回**已完成**的 Future。

        与 submit 的契约保持一致（仍返回 Future 或类 Future 对象），
        使调用方 `if _future:` 的既有判空逻辑无需改动。
        """
        _f: Future = Future()
        try:
            _f.set_result(self._execute_reasoning_task(func_name, *args, **kwargs))
        except Exception as e:
            _f.set_exception(e)
        return _f

    def _rebuild_pool(self, reason: str = "", force: bool = False) -> bool:
        """重建进程池（指数退避 + 串行化）。

        Args:
            reason: 崩溃/触发原因（打进日志）。
            force: True 时忽略"池仍可用"的短路判断（健康检查用）。

        Returns:
            True=新池就绪；False=重建失败或已耗尽重试（调用方应降级同步）。
        """
        enabled, max_retries, _interval = self._pool_auto_rebuild_config()
        if not enabled or self._closed:
            return False
        with self._rebuild_lock:
            if self._degraded_sync:
                return False
            # 已被别的线程重建好（本层判断避免重复重建）
            if not force and self._pool is not None and not self._is_pool_broken():
                return True
            if self._rebuild_consecutive_failures >= max_retries:
                # ★主线第22批 T2/P2-120：降级**之前**最后一次机会 —— 用最小配置
                #   (1 worker) 重建；成功则继续走进程池，避免过早转入永久同步。
                if self._try_minimal_rebuild(reason=f"降级前最后尝试({reason or '未知'})"):
                    return True
                self._degrade_to_sync(reason)
                return False

            _attempt = self._rebuild_consecutive_failures + 1
            # 指数退避：0.1s → 0.2s → 0.4s …（上限 1s；够快以满足「5 秒内重建完成」）
            _backoff = min(1.0, 0.1 * (2 ** (self._rebuild_consecutive_failures)))
            if _backoff > 0:
                time.sleep(_backoff)

            _t0 = time.time()
            _old_pool = self._pool
            _old_workers = self._max_workers
            self._broken_detected_count += 1
            try:
                _new_pool = ProcessPoolExecutor(max_workers=self._max_workers)
            except Exception as e:
                self._rebuild_consecutive_failures += 1
                # ★主线第22批 T2/P2-120：补充系统资源状态，便于区分「真实故障」
                #   与「资源耗尽（句柄/内存/子进程数受限）」——原实现只有异常类型与消息。
                _sysinfo = self._collect_pool_sysinfo()
                _module_logger.error(
                    f"推理进程池重建失败(第{_attempt}次, 原因={reason or '未知'}): "
                    f"{type(e).__name__}: {e}；系统状态[{_sysinfo}]")
                return False

            self._pool = _new_pool
            self._pool_generation += 1
            self._rebuild_count += 1
            self._rebuild_consecutive_failures = 0
            _cost_ms = (time.time() - _t0) * 1000.0
            self._rebuild_duration_ms_max = max(self._rebuild_duration_ms_max, _cost_ms)
            self._enabled = True
            _module_logger.info(
                f"推理进程池自动重建成功: 第{self._rebuild_count}次, "
                f"worker={_old_workers}个, 代数={self._pool_generation}, "
                f"耗时={_cost_ms:.1f}ms, 原因={reason or '未知'}")

            # 旧池：不取消运行任务，等它们自然结束（与平滑重建同一口径）
            if _old_pool is not None and _old_pool is not _new_pool:
                try:
                    with self._lock:
                        _pending = [f for f in self._inflight_futures if not f.done()]
                    if _pending:
                        try:
                            wait(_pending, timeout=5.0)
                        except Exception:
                            pass
                        with self._lock:
                            self._inflight_futures.difference_update(_pending)
                except Exception as e:
                    _module_logger.debug(
                        f"重建时等待旧池在途任务失败（已忽略）: {type(e).__name__}: {e}")
                try:
                    _old_pool.shutdown(wait=False)
                except Exception as e:
                    _module_logger.debug(
                        f"旧推理进程池关闭异常（已忽略）: {type(e).__name__}: {e}")
            return True

    @staticmethod
    def _degraded_recovery_config() -> tuple[bool, float]:
        """★第22批 T2：读取「降级后恢复」配置 → (开关, 恢复尝试间隔秒)。

        配置缺失/异常时回退安全默认 (True, 300.0)。
        """
        _enabled, _interval = True, 300.0
        try:
            import config as _cfg
            _enabled = bool(getattr(_cfg, "ENABLE_POOL_DEGRADED_RECOVERY", True))
            _interval = float(
                getattr(_cfg, "POOL_DEGRADED_RECOVER_INTERVAL_SEC", 300.0) or 300.0)
        except Exception as _e:
            _module_logger.debug(
                f"降级恢复配置读取失败，使用默认值(True,300): {type(_e).__name__}: {_e}")
        return _enabled, max(1.0, _interval)

    @staticmethod
    def _collect_pool_sysinfo() -> str:
        """★第22批 T2：采集进程池相关系统状态（诊断用，失败不影响主流程）。"""
        _parts: list[str] = []
        try:
            _parts.append(f"CPU核={os.cpu_count()}")
        except Exception:
            pass
        try:
            import multiprocessing as _mp
            _parts.append(f"活跃子进程={len(_mp.active_children())}")
        except Exception:
            pass
        try:
            _parts.append(f"pid={os.getpid()}")
        except Exception:
            pass
        return ", ".join(_parts) or "不可用"

    def _maybe_recover_from_degraded(self) -> bool:
        """★主线第22批 T2/P2-120：降级为同步执行后的定期恢复尝试。

        背景：`_maybe_health_check` 在 `_degraded_sync=True` 时直接 return，
        且 `submit` 的降级分支也不调用它 → 原实现一旦降级便**永久同步执行**。
        本方法按 `POOL_DEGRADED_RECOVER_INTERVAL_SEC`（默认 300s）节流，
        用最小配置（1 worker）尝试重建。

        Returns:
            True=已恢复（`_degraded_sync` 已复位）；False=未到时间/仍失败。
        """
        if not self._degraded_sync or self._closed:
            return False
        _enabled, _interval = self._degraded_recovery_config()
        if not _enabled:
            return False
        _now = time.time()
        with self._rebuild_lock:
            if not self._degraded_sync:          # 其他线程已恢复
                return True
            if _now - self._last_degraded_recover < _interval:
                return False
            self._last_degraded_recover = _now
        return self._try_minimal_rebuild(reason="降级后定期恢复尝试")

    def _try_minimal_rebuild(self, reason: str = "") -> bool:
        """★主线第22批 T2/P2-120：以最小配置（1 worker）做最后一次进程池重建。

        用于两处：① 达到重试上限、降级**之前**的最后一次机会；
        ② 降级**之后**的定期恢复尝试。成功后复位降级状态。

        失败仅记 DEBUG（降级状态下不重复 ERROR，避免刷屏）。

        Returns:
            True=池已就绪（`_degraded_sync` 已复位）；False=仍然失败。
        """
        if self._closed:
            return False
        try:
            _pool = ProcessPoolExecutor(max_workers=1)
        except Exception as e:
            _module_logger.debug(
                f"推理进程池最小配置(1 worker)重建失败: {type(e).__name__}: {e}；"
                f"系统状态[{self._collect_pool_sysinfo()}]")
            return False
        with self._rebuild_lock:
            self._pool = _pool
            self._max_workers = 1
            self._pool_generation += 1
            self._rebuild_count += 1
            self._rebuild_consecutive_failures = 0
            self._enabled = True
            _was_degraded = self._degraded_sync
            self._degraded_sync = False
            self._last_degraded_recover = 0.0
        if _was_degraded:
            _module_logger.info(
                f"推理进程池已恢复（worker=1，原因={reason or '定期恢复'}）")
        else:
            _module_logger.info(
                f"推理进程池最小配置重建成功（worker=1，原因={reason or '未知'}）")
        return True

    def _degrade_to_sync(self, reason: str = "") -> None:
        """连续重建失败达上限 → 永久降级为同进程同步执行（保证功能可用）。"""
        if self._degraded_sync:
            return
        self._degraded_sync = True
        self._enabled = False
        # ★第22批 T2：记录降级时刻 → 首次恢复尝试从此刻起按间隔节流
        self._last_degraded_recover = time.time()
        _module_logger.error(
            f"推理进程池连续重建失败达上限，降级为同步执行（功能保持可用）: "
            f"原因={reason or '未知'}")

    def get_rebuild_stats(self) -> dict[str, Any]:
        """进程池重建统计（供自省/诊断消费）。"""
        enabled, max_retries, interval = self._pool_auto_rebuild_config()
        return {
            "auto_rebuild_enabled": enabled,
            "max_retries": max_retries,
            "health_check_interval": interval,
            "rebuild_count": self._rebuild_count,
            "consecutive_failures": self._rebuild_consecutive_failures,
            "broken_detected_count": self._broken_detected_count,
            "rebuild_duration_ms_max": round(self._rebuild_duration_ms_max, 2),
            "pool_generation": self._pool_generation,
            "degraded_sync": self._degraded_sync,
        }

    def _maybe_resize_pool(self):
        # ★P1-1（推理池竞态）：整个重建决策（读并行度+节流检查+更新时间+重建）
        # 原子化。此前 _last_resize_time 检查与赋值非原子，并发 submit 可同时通过
        # 节流 → 同一秒连续重建两次（日志曾现 9→18→4），违背平滑设计。
        with self._resize_lock:
            self._maybe_resize_pool_locked()

    def _maybe_resize_pool_locked(self):
        """★H1（v9.5-worker动态调整）：随全局并行度平滑重建进程池。

        触发条件（需同时满足，避免频繁重建）：
          1. 全局并行度相对初始化时显著变化（差值 >= 阈值）
          2. 距上次重建已过最短间隔（120s 节流）
        新 worker 数 = max(2, int(并行度 * 0.75))（与原 0.75 系数一致）。
        旧池优雅退出（shutdown(wait=False)，不取消运行任务），新池接替。
        """
        try:
            from nucleus.parallel_scheduler import get_parallel_scheduler
            _cur_p = get_parallel_scheduler().get_parallelism()
            _cur_p = max(2, int(_cur_p))
        except Exception:
            return
        _new_workers = max(2, int(_cur_p * 0.75))
        _old_workers = self._max_workers
        _delta = abs(_cur_p - self._parallelism_at_init)
        if _new_workers == _old_workers:
            return
        if _delta < self._resize_delta_threshold:
            return
        _now = time.time()
        if _now - self._last_resize_time < self._resize_min_interval:
            return
        self._last_resize_time = _now
        _old_pool = self._pool
        try:
            self._pool = ProcessPoolExecutor(max_workers=_new_workers)
            self._max_workers = _new_workers
            _module_logger.info(
                f"推理进程池平滑重建: {_old_workers}→{_new_workers}个工作进程 "
                f"(并行度{self._parallelism_at_init}→{_cur_p})")
        except Exception as _e:
            _module_logger.warning(f"推理进程池重建失败: {_e}，保留原池")
            self._pool = _old_pool
            return
        if _old_pool:
            # ★A4【P1】：旧池关闭前先等待未完成任务（30秒超时），避免结果丢失。
            # 原实现 shutdown(wait=False) 直接关闭，运行中任务的 Future 可能永远拿不到结果。
            # 方案二（wait=True + 超时）：用 wait(..., timeout=30) 等待 inflight future，
            # 超时后仍强制 shutdown，保证不阻塞主线程超过 30 秒。
            try:
                with self._lock:
                    _pending = [f for f in self._inflight_futures if not f.done()]
                if _pending:
                    _done, _not_done = wait(_pending, timeout=30.0)
                    if _not_done:
                        _module_logger.warning(
                            f"推理池重建：{len(_not_done)}个任务超30秒未完成，强制关闭旧池")
                # 清理已完成/已等待的 inflight future（其结果的 Future 引用仍由调用方持有）
                with self._lock:
                    self._inflight_futures.difference_update(_pending)
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            try:
                # 优雅退出：不取消运行任务，让已提交任务自然完成
                _old_pool.shutdown(wait=False)
            except Exception as e:
                _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    # ★P2-24（2026-09-09 技术债务第一批）：优雅关闭等待上限（秒）。
    #   只在「确有任务在跑」时才等待；空池关闭依旧是瞬时的，不增加停机耗时。
    GRACEFUL_SHUTDOWN_TIMEOUT = 5.0
    # 子进程自然收尾的额外观察窗口（秒）：任务已完成后给 worker 一点退出时间。
    _WORKER_EXIT_GRACE = 1.0

    def shutdown(self, timeout: float | None = None) -> None:
        """关闭进程池。

        ★P2-24：先优雅等待，超时后才降级强杀。
        原实现 `shutdown(wait=False, cancel_futures=True)` 之后立刻 `_p.terminate()`
        子进程，运行中的推理任务被腰斩 → 子进程异常终止、停机刷 ERROR。

        ⚠ 实测（Python 3.12.6 / Windows）：`ProcessPoolExecutor.shutdown()` 内部
        会把 `self._processes` 置为 **None**，所以原实现放在 shutdown **之后**的
        强杀循环实际是死代码 —— `None.values()` 抛 AttributeError，被外层的
        `except Exception: pass` 静默吞掉，一次都没真正执行过。
        修复：① shutdown 之前先快照子进程；② 等任务自然结束；③ 只对超时幸存者强杀。

        ⚠ 另注：`cancel_futures=True` 只能取消**尚未开始**的任务，
        已在运行的 Future 取消不掉（实测 state 仍为 running），所以必须「等」而不是「杀」。
        """
        self._enabled = False
        # ★主线第15批 T2/P1-92：关闭时复位降级标记，重启后重新尝试进程池能力；
        #   同时置 _closed 禁止再自动重建（否则停机期的 submit 会把池「复活」）
        self._degraded_sync = False
        self._closed = True
        _pool = self._pool
        self._pool = None          # 先摘引用，保证重复调用安全（幂等）
        if _pool is None:
            return
        _timeout = self.GRACEFUL_SHUTDOWN_TIMEOUT if timeout is None else timeout

        # ① 先快照子进程 —— shutdown() 之后 _processes 会被置 None（见 docstring）
        _snapshot = []
        try:
            _snapshot = [p for p in (getattr(_pool, "_processes", None) or {}).values()
                         if hasattr(p, "terminate")]
        except Exception as e:
            _module_logger.debug(
                f"推理进程池子进程快照失败（强杀降级为不可用）: {type(e).__name__}: {e}")

        # ② 取消尚未开始的任务，避免它们刚被调度就被打断
        _inflight: list = []
        try:
            with self._lock:
                _inflight = [f for f in self._inflight_futures if not f.done()]
            for _f in _inflight:
                try:
                    _f.cancel()
                except Exception:
                    pass
            with self._lock:
                self._inflight_futures.difference_update(_inflight)
        except Exception as e:
            _module_logger.debug(
                f"推理进程池取消待处理任务失败: {type(e).__name__}: {e}")

        # ③ 等待仍在运行的任务自然结束（带上限，绝不挂死）
        _not_done: list = []
        try:
            _running = [f for f in _inflight if not f.cancelled() and not f.done()]
            if _running:
                _done, _not_done = wait(_running, timeout=_timeout)
        except Exception as e:
            _module_logger.debug(
                f"推理进程池等待运行中任务失败: {type(e).__name__}: {e}")
            _not_done = list(_inflight)

        # ④ 发起关闭：wait=False 永不阻塞；cancel_futures 清掉队列里未开始的项
        try:
            _pool.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            _module_logger.debug(
                f"推理进程池 shutdown 异常已忽略: {type(e).__name__}: {e}")

        # ⑤ 给子进程一个自然收尾窗口，仅对超时幸存者强杀（兜底，防止停机挂死）
        if _snapshot:
            _deadline = time.time() + self._WORKER_EXIT_GRACE
            while time.time() < _deadline and any(_is_alive(p) for p in _snapshot):
                time.sleep(0.05)
        _killed = 0
        for _p in _snapshot:
            try:
                if not _is_alive(_p):
                    continue
                _p.terminate()
                _killed += 1
                try:
                    _p.join(1.0)   # 回收，避免留下僵尸
                except Exception:
                    pass
            except Exception as e:
                _module_logger.debug(
                    f"推理进程池终止子进程失败: {type(e).__name__}: {e}")
        if _killed:
            _module_logger.warning(
                f"推理进程池优雅关闭超时（已等待{_timeout}s），"
                f"强制终止{_killed}个子进程，运行中的推理任务结果将丢失")
        elif _not_done:
            _module_logger.warning(
                f"推理进程池关闭时仍有{len(_not_done)}个任务未返回结果"
                f"（子进程已自然退出，结果由调用方按需处理）")
        else:
            _module_logger.debug(
                "推理进程池已优雅关闭（子进程自然退出，无强杀）")


# 模块级单例
_reasoning_pool: ReasoningWorkerPool | None = None
_reasoning_pool_lock = threading.Lock()


def get_reasoning_pool() -> ReasoningWorkerPool:
    """获取推理进程池单例"""
    global _reasoning_pool
    if _reasoning_pool is None:
        with _reasoning_pool_lock:
            if _reasoning_pool is None:
                _reasoning_pool = ReasoningWorkerPool()
    return _reasoning_pool


def shutdown_reasoning_pool() -> None:
    """★P1: 复位 ReasoningWorkerPool 单例，满足器官零状态（规则4）。

    原停机流程直接调用 get_reasoning_pool().shutdown() 关闭进程池，但全局变量
    _reasoning_pool 仍持有已关闭实例。此处显式置空，使重启时重建全新进程池，
    避免复用带残留子进程的旧实例（进程池子进程非 daemon，需主动 terminate）。
    """
    global _reasoning_pool
    _inst = _reasoning_pool
    _reasoning_pool = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception:
                pass