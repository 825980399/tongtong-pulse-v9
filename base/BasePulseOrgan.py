"""BasePulseOrgan —— 纯脉冲架构器官统一基类（v9.5 并发安全 · 终极完整版）

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日
"""

import threading
import time
from abc import ABC, abstractmethod
from typing import Any

import config  # noqa: F401
from nucleus.const import ErrorCode, LogLevel, OrganStatus, SystemEvent
from nucleus.logger import exc_location, get_organ_logger


class BasePulseOrgan(ABC):
    """
    器官基类（v9.5 并发安全 · 终极完整版）
    
    强制规范:
        1. 唯一入口: on_pulse(pulse) —— 所有外部事件通过脉冲传入
        2. 零本地状态: 禁止 self.xxx 长期缓存状态，状态统一在 InfoField + PulseNodePool
        3. 禁止直接调用其他器官: 所有跨器官通信通过 InfoField 脉冲广播
        4. 禁止轮询/阻塞: 不允许 while True / time.sleep / 长连接
        5. 脉冲发射: 使用 self._emit() 发射脉冲到信息场，不允许绕过
        6. 熔断保护: 连续异常达阈值自动暂停，冷却后恢复（P1-1新增）
        7. 并发安全: _handle_pulse_safe 支持多线程并发调用，锁仅保护熔断状态（v9.5新增）
        8. 分层脉冲: _emit/_send 原生支持 layer 参数，脉冲自动标记层级（v9.5新增）
    """
    
    def __init__(self, organ_name: str):
        """
        Args:
            organ_name: 器官名称（中文，如"心脏"、"大脑皮层"）
        """
        self.organ_name = organ_name
        self.info_field = None          # 由框架注入
        self.pulse_core = None          # 由框架注入
        self.is_running = False
        self._pulse_id_counter = 0
        
        # 接入统一日志系统
        self._logger = get_organ_logger(self.organ_name)
        
        # 器官状态（OrganStatus 落地）
        self.status = OrganStatus.IDLE
        
        # ===== 熔断机制（P1-1 · v9.5并发安全增强） =====
        self._error_count = 0
        self._fuse_threshold = 3
        self._fuse_cooldown = 60.0
        self._fuse_until = 0.0
        self._fuse_multiplier = 1
        self._fuse_lock = threading.Lock()
        # ★v17.0 D8新增：运行时追踪预埋（默认关闭，v18.0启用）
        self._trace_enabled = False          # 追踪开关（默认关闭）
        self._trace_buffer: list[dict[str, Any]] = []  # 追踪缓冲区
        self._trace_max_buffer = 100         # 缓冲区上限
        self._trace_lock = threading.Lock()  # 追踪缓冲区锁        
    
    # ========== 框架注入接口（基类防御性空实现） ==========
    
    def set_info_field(self, info_field):
        """
        ★v17.0防御性设计：注入全局信息场。
        子类可覆盖此方法以接收注入。如果子类未覆盖，基类提供空实现防止静默失效。
        心跳驱动器官必须覆盖此方法以确保心跳脉冲正常接收。
        """
        self.info_field = info_field
        
    def set_pulse_core(self, pulse_core):
        """注入脉冲核心引擎。由框架在初始化时调用。"""
        self.pulse_core = pulse_core

    def set_insight_board(self, board):
        """★骨架优化：标准依赖注入 setter（子类可覆盖，未覆盖时用基类实现）"""
        self._insight_board = board

    def set_self_inspector(self, inspector):
        """★骨架优化：标准依赖注入 setter"""
        self._self_inspector = inspector

    def set_verification_learning_hub(self, hub):
        """★骨架优化：标准依赖注入 setter"""
        self._vl_hub = hub

    def set_context_snapshot(self, snapshot):
        """★骨架优化：标准依赖注入 setter"""
        self._context_snapshot = snapshot

    def set_evolution_sandbox(self, sandbox):
        """★骨架优化：标准依赖注入 setter"""
        self._evolution_sandbox = sandbox

    def _get_insight_board(self):
        """★骨架优化：统一获取洞察黑板（依赖注入优先，缺失回退单例）。

        子类若通过 set_insight_board 注入了引用，则优先使用注入；
        未注入时惰性回退全局单例，保持兼容，避免各器官重复写单例直调。
        """
        _board = getattr(self, '_insight_board', None)
        if _board is not None:
            return _board
        from nucleus.InsightBoard import get_insight_board
        return get_insight_board()

    def _get_self_inspector(self):
        """★骨架优化：统一获取代码审查器（依赖注入优先，缺失回退单例）。"""
        _inspector = getattr(self, '_self_inspector', None)
        if _inspector is not None:
            return _inspector
        from nucleus.self_inspector import get_self_inspector
        return get_self_inspector()

    def _get_verification_learning_hub(self):
        """★骨架优化：统一获取验证学习枢纽（依赖注入优先，缺失回退单例）。"""
        _hub = getattr(self, '_vl_hub', None)
        if _hub is not None:
            return _hub
        from nucleus.mnemosyne.verification_learning_hub import (
            get_verification_learning_hub,
        )
        return get_verification_learning_hub()

    def _get_context_snapshot(self):
        """★骨架优化：统一获取上下文快照管理器（依赖注入优先，缺失回退单例）。"""
        _snap = getattr(self, '_context_snapshot', None)
        if _snap is not None:
            return _snap
        from nucleus.mnemosyne.ContextSnapshot import get_context_snapshot
        return get_context_snapshot()

    def _get_evolution_sandbox(self):
        """★骨架优化：统一获取进化沙箱（依赖注入优先，缺失回退单例）。"""
        _sandbox = getattr(self, '_evolution_sandbox', None)
        if _sandbox is not None:
            return _sandbox
        from nucleus.reasoning.EvolutionSandbox import get_evolution_sandbox
        return get_evolution_sandbox()

    def _get_config_section(self, section_name: str) -> dict:
        """★骨架优化：统一安全获取配置段，替代各器官重复的「import config + getattr + except」。

        Args:
            section_name: config.py 中的配置段名（如 'LIVER_CONFIG'、'INTEREST_MODEL_CONFIG'）。

        Returns:
            配置字典（安全获取，失败或类型不符时返回空字典）。
        """
        try:
            import config
            _cfg = getattr(config, section_name, {})
            return _cfg if isinstance(_cfg, dict) else {}
        except Exception:
            return {}

    def _call_provider(self, provider, *args, default=None):
        """★P3-1：统一调用「只读状态 provider 回调」（替代跨器官 getter 直调）。

        规则14「依赖注入 + 回调」范式：
            消费方不再直接调用被依赖器官的 get_xxx() 同步方法，
            而是通过装配层注入的 provider 回调获取只读状态快照。

        与「脉冲广播」的区别：provider 回调保留同步返回语义，
        不破坏调用方的时序（脉冲是单向异步的，无法提供同步返回值）。

        Args:
            provider: 回调函数（可能为 None），签名应与被替代的 getter 一致。
            *args: 透传给回调的位置参数（如 get_reply_guidance 的 user_name）。
            default: 回调未注入或抛异常时的降级返回值。

        Returns:
            回调返回值；未注入/异常时返回 default。
        """
        if provider is None:
            return default
        try:
            return provider(*args)
        except Exception:
            return default

    def reset_logger(self, name: str | None = None) -> None:
        """★P3-1：重置日志标签（替代跨模块对 _logger 的私有写入）。"""
        self._logger = get_organ_logger(name or self.organ_name)

    def set_registered_cond_ids(self, ids: list) -> None:
        """★P3-1：记录已注册的共振条件 ID（替代跨模块对 _registered_cond_ids 的私有写入）。"""
        self._registered_cond_ids = list(ids) if ids else []

    def get_registered_cond_ids(self) -> list:
        """★P3-1：返回已注册的共振条件 ID 副本。"""
        return list(getattr(self, '_registered_cond_ids', []))

    def clear_registered_cond_ids(self) -> None:
        """★P3-1：清空已注册的共振条件 ID。"""
        self._registered_cond_ids = []

    # ========== 时间中枢钩子（v9.x TimeCore） ==========
    
    def on_time_tick(self, pulse) -> None:
        """
        ★v9.x TimeCore 时间中枢钩子：响应周期性 time.tick 广播。

        由 TimeCore 内核单例每固定间隔（默认 60s）通过 InfoField 广播 time.tick 脉冲，
        统一分发到各器官的 on_time_tick（不经过 on_pulse 主业务逻辑），避免干扰核心脉冲处理。

        默认空实现：不改变任何现有器官的行为。子类（如调度相关器官）可覆盖以
        感知时间推进（墙钟时间、逻辑时间、语义时间）。
        """
        return None  # noqa: RET501

    # ========== 子类必须实现的抽象方法 ==========
    
    @abstractmethod
    def on_pulse(self, pulse) -> dict[str, Any] | None:
        """
        脉冲事件入口 —— 子类唯一需要实现的业务逻辑方法。
        
        v9.5 并发说明:
            - 此方法可能被多个线程并发调用（InfoField 分层异步分发）。
            - 只读全局数据（L3种子记忆、配置等）无需加锁。
            - 修改实例状态时，子类应自行加锁保护。
        """

    @abstractmethod
    def get_resonance_conditions(self) -> list:
        """返回此器官订阅的共振条件列表。"""
    
    # ========== 未来演化预留（v10.0 振荡场） ==========
    
    def on_field_oscillation(self, frequency: float, amplitude: float, 
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】感知连续振荡场的变化。"""
        return None
    # ========== ★P1-4新增：统一状态快照接口 ==========
    
    def get_state_snapshot(self) -> dict[str, Any]:
        """
        获取器官当前运行状态的快照，用于持久化恢复。
        子类按需覆盖，返回需要持久化的状态字典。
        基类默认返回空字典。
        
        Returns:
            {"status": str, "counters": {...}, "extra": ...}
        """
        return {}
    
    def load_state_snapshot(self, state: dict[str, Any]):
        """
        从快照恢复器官运行状态。子类按需覆盖。
        基类默认不做任何操作。
        
        Args:
            state: get_state_snapshot 方法返回的字典
        """
    
    # ========== 状态快照接口结束 ==========    
    # ========== 子类可用的工具方法 ==========
    
    def _emit(self, event_type: str, payload: dict | None = None, 
              priority: int = 5, ttl_ns: int = 5_000_000_000,
              layer: str = "L1") -> str | None:
        """
        发射脉冲到信息场（v18.0修复：统一走_send出口，保持PulseCore统计准确）。
        
        pulse_core不可用时_send内部自动降级为手动构造。
        所有调用_emit的器官代码无需任何修改。
        
        Args:
            event_type: 事件类型（如 'knowledge.written' / 'heart.beat'）
            payload:   载荷数据
            priority:  优先级 0-10
            ttl_ns:    脉冲有效期（纳秒），默认5秒
            layer:     脉冲层级（v9.5新增）
                        L0 生命线（心跳/熔断/告警）
                        L1 实时交互（对话/意图）
                        L2 认知思考（知识/推理）
                        L3 后台自主（清理/演化）
        Returns:
            发射的 pulse_id，如果信息场未注入则返回 None
        """
        return self._send(event_type, payload, priority, ttl_ns, layer)
        
    def _send(self, event_type: str, payload: dict | None = None,
              priority: int = 5, ttl_ns: int = 5_000_000_000,
              layer: str = "L1") -> str | None:
        """
        统一脉冲发送入口（v18.0修复：修正缩进结构，确保正常路径走PulseCore）。
        """
        if self.info_field is None:
            self._log(LogLevel.WARNING,
                      f"脉冲发送失败: info_field 未注入，丢弃事件 {event_type}")
            return None
        
        if self.pulse_core is not None:
            # v9.5: 直接将 layer 传入 PulseCore.emit，无需事后赋值
            pulse = self.pulse_core.emit(
                source_organ=self.organ_name,
                event_type=event_type,
                payload=payload or {},
                priority=priority,
                layer=layer,
            )
            # 脉冲追踪（先获取pulse_id再使用）
            try:
                import config
                if getattr(config, 'DEBUG_PULSE_TRACE', False):
                    # ★往期批次 相关任务（方案B）：同步 flush_to_file() 已移除 ——
                    #   它让器官发射线程直面 per-path 写锁（19:35 块实测 11 个参与者
                    #   排队），写盘改由 tracer 内部 daemon 线程承担。
                    from utils.pulse_tracer import log_emit
                    _trace_id = pulse.get("pulse_id", "") if isinstance(pulse, dict) else ""
                    log_emit(self.organ_name, event_type, layer, _trace_id)
            except Exception:
                self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
            
            # 发布脉冲并返回ID（正常路径，不依赖异常）
            self.info_field.publish(pulse)
            pulse_id = pulse.get("pulse_id", "") if isinstance(pulse, dict) else ""
            return pulse_id or None
        
        # 兜底：手动构造脉冲（仅在pulse_core不可用时）
        self._pulse_id_counter += 1
        pulse_id = f"pulse:{self.organ_name}:{event_type}:{time.time_ns()}:{self._pulse_id_counter}"
        
        pulse = {
            "pulse_id": pulse_id,
            "source_organ": self.organ_name,
            "event_type": event_type,
            "priority": priority,
            "layer": layer,
            "timestamp_ns": time.time_ns(),
            "payload": payload or {},
            "ttl_ns": ttl_ns,
        }
        
        self.info_field.publish(pulse)
        return pulse_id
        
    def _log(self, level: str, message: str, error_code: str | None = None):
        """使用统一日志系统输出日志。"""
        log_level = {
            LogLevel.DEBUG: 10,
            LogLevel.INFO: 20,
            LogLevel.WARNING: 30,
            LogLevel.ERROR: 40,
            LogLevel.CRITICAL: 50,
        }.get(level, 20)
        
        if error_code:
            message = f"[{error_code}] {message}"
        
        self._logger.log(log_level, message)
    # ========== ★v17.0 D8预埋：运行时追踪方法（v18.0启用） ==========
    
    def _trace_entry(self, method_name: str, args: dict[str, Any] | None = None):
        """
        ★v17.0 D8预埋：记录方法入口追踪。
        
        在关键方法的入口处调用，记录调用时间和参数。
        默认关闭（_trace_enabled=False），不影响性能。
        
        Args:
            method_name: 方法名
            args: 调用参数字典（可选）
        """
        if not self._trace_enabled:
            return
        
        import time as _time
        _entry = {
            "type": "entry",
            "organ": self.organ_name,
            "method": method_name,
            "args": args or {},
            "timestamp": _time.time(),
        }
        with self._trace_lock:
            self._trace_buffer.append(_entry)
            if len(self._trace_buffer) > self._trace_max_buffer:
                self._trace_buffer = self._trace_buffer[-self._trace_max_buffer:]
    
    def _trace_exit(self, method_name: str, result: Any = None, duration: float = 0.0):
        """
        ★v17.0 D8预埋：记录方法出口追踪。
        
        在关键方法的出口处调用，记录返回值和执行耗时。
        默认关闭（_trace_enabled=False），不影响性能。
        
        Args:
            method_name: 方法名
            result: 返回值（可选）
            duration: 执行耗时（秒）
        """
        if not self._trace_enabled:
            return
        
        import time as _time
        _exit = {
            "type": "exit",
            "organ": self.organ_name,
            "method": method_name,
            "result_preview": str(result)[:200] if result is not None else "None",
            "duration": round(duration, 4),
            "timestamp": _time.time(),
        }
        with self._trace_lock:
            self._trace_buffer.append(_exit)
    
    def _get_trace_summary(self) -> dict[str, Any]:
        """
        ★v17.0 D8预埋：获取追踪摘要。
        
        返回最近追踪记录的统计信息，供代码学习对比分析使用。
        """
        with self._trace_lock:
            _entries = list(self._trace_buffer)
        
        if not _entries:
            return {"total_traces": 0, "summary": "追踪缓冲区为空"}
        
        _entry_count = sum(1 for _e in _entries if _e["type"] == "entry")
        _exit_count = sum(1 for _e in _entries if _e["type"] == "exit")
        _methods = list({_e["method"] for _e in _entries})
        
        return {
            "total_traces": len(_entries),
            "entry_count": _entry_count,
            "exit_count": _exit_count,
            "methods_traced": _methods[:20],
            "buffer_usage": f"{len(_entries)}/{self._trace_max_buffer}",
            "enabled": self._trace_enabled,
        }
    
    def enable_tracing(self):
        """★v17.0 D8预埋：启用运行时追踪"""
        self._trace_enabled = True
        self._log(LogLevel.INFO, f"运行时追踪已启用 (缓冲区={self._trace_max_buffer})")
    
    def disable_tracing(self):
        """★v17.0 D8预埋：禁用运行时追踪"""
        self._trace_enabled = False
        self._log(LogLevel.INFO, f"运行时追踪已禁用 (共记录{len(self._trace_buffer)}条)")
        self._trace_buffer = []
    
    # ========== 追踪方法结束 ==========
    # ========== 熔断机制（P1-1 · v9.5并发安全增强） ==========
    
    def handle_pulse_safe(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        """公开封装 _handle_pulse_safe，供信息场安全分发调用（规则14）"""
        return self._handle_pulse_safe(pulse)

    def _handle_pulse_safe(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        """
        安全的脉冲处理入口（v9.5 并发安全版）。
        
        支持多线程并发调用：
            - 熔断状态检查与错误计数使用锁保护
            - 业务逻辑 on_pulse 在锁外执行，不阻塞其他线程并发处理不同脉冲
        """
        # === 熔断状态检查（锁保护） ===
        with self._fuse_lock:
            if self.status == OrganStatus.FUSED:
                now = time.time()
                if now < self._fuse_until:
                    return None
                # 冷却结束，进入半开状态
                self.status = OrganStatus.RECOVERING
                self._log(LogLevel.INFO, "熔断冷却结束，进入恢复状态")
        
        # 脉冲追踪模式：记录接收事件
        try:
            import config
            if getattr(config, 'DEBUG_PULSE_TRACE', False):
                # ★往期批次 相关任务（方案B）：同步 flush_to_file() 已移除（同上），
                #   器官接收线程不再卡在业务之前的写锁上。
                from utils.pulse_tracer import log_receive
                event_type = pulse.get("event_type", "?")
                source = pulse.get("source_organ", "?")
                log_receive(self.organ_name, event_type, source)
        except Exception:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
        # === 执行业务逻辑（锁外，其他线程可同时进入） ===
        try:
            # ★v9.x TimeCore：time.tick 脉冲走专用钩子 on_time_tick，不进入 on_pulse 主逻辑
            if pulse.get("event_type") == SystemEvent.TIME_TICK:
                result = self.on_time_tick(pulse)
            else:
                result = self.on_pulse(pulse)
            
            # 成功处理：重置错误计数（锁保护）
            with self._fuse_lock:
                if self.status == OrganStatus.RECOVERING:
                    self._error_count = 0
                    self._fuse_multiplier = 1
                    self.status = OrganStatus.RUNNING
                    self._log(LogLevel.INFO, "已从熔断中恢复正常")
                elif self._error_count > 0:
                    self._error_count = 0
            
            return result
            
        except Exception as e:
            # 异常处理（锁保护）
            import traceback
            _tb = traceback.format_exc()
            _need_alarm = False
            _alarm_payload = {}
            with self._fuse_lock:
                self._error_count += 1
                # ★v23.0新增：打印完整堆栈跟踪，便于定位问题
                self._log(LogLevel.ERROR, 
                          f"脉冲处理异常 (第{self._error_count}次): {e}\n{_tb[:2000]}",
                          error_code=ErrorCode.EXECUTION_FAILED)
                
                if self._error_count >= self._fuse_threshold:
                    self.status = OrganStatus.FUSED
                    cooldown = self._fuse_cooldown * self._fuse_multiplier
                    self._fuse_until = time.time() + cooldown
                    self._log(LogLevel.ERROR,
                              f"器官熔断! 冷却{cooldown}s (翻倍系数={self._fuse_multiplier})",
                              error_code=ErrorCode.ORGAN_TIMEOUT)
                    
                    self._fuse_multiplier = min(self._fuse_multiplier * 2, 16)
                    _need_alarm = True
                    _alarm_payload = {
                        "type": "organ_fused",
                        "organ": self.organ_name,
                        "error_count": self._error_count,
                        "cooldown": cooldown,
                        "message": f"器官 {self.organ_name} 已熔断",
                    }
            
            # ★P2-1修复：锁外发射熔断告警（L0 生命线层），避免锁内发射脉冲导致潜在死锁
            if _need_alarm:
                self._emit(SystemEvent.ALARM, _alarm_payload, priority=9, layer="L0")

            # ★P1 运行时埋点: 锁外记录异常现场快照（脉冲类型 + 调用栈 + 锁状态）
            try:
                from nucleus.runtime_metrics import get_runtime_metrics
                get_runtime_metrics().record_error(
                    pulse_type=pulse.get("event_type", "?"),
                    error=str(e),
                    traceback_text=_tb,
                    lock_held=True,
                )
            except Exception as e:
                self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")

            return None
    
    def is_available(self) -> bool:
        """判断器官当前是否可处理脉冲（v9.5: 线程安全）"""
        with self._fuse_lock:
            return self.is_running and self.status not in (OrganStatus.FUSED,)
        
    # ========== 生命周期方法 ==========
    
    def start(self):
        """启动器官。子类可覆盖，但必须调用 super().start()。"""
        self.is_running = True
        self.status = OrganStatus.RUNNING
        
    def stop(self):
        """停止器官。子类可覆盖，但必须调用 super().stop()。"""
        self.is_running = False
        self.status = OrganStatus.STOPPED

    # ========== ★F4处置闭环：软重启/降级/恢复（防御性最小实现，子类可覆盖） ==========

    def restart(self) -> bool:
        """
        ★F4处置闭环：软重启（stop → start 最小实现）。
        子类可覆盖以清理内部状态 / 重连依赖。
        不强制中断正在执行的任务，不强清内部状态，保证低侵入。

        Returns:
            是否成功回到运行态（is_running == True）。
        """
        self.stop()
        self.start()
        return self.is_running

    def degrade(self, reason: str = "") -> None:
        """
        ★F4处置闭环：降级（标记 SUSPENDED + 告警，软降级可回滚）。
        不销毁数据、不停止线程，仅标记状态，供系统管理器处置沉默器官。

        Args:
            reason: 降级原因（用于审计日志）。
        """
        self.status = OrganStatus.SUSPENDED
        self._log(LogLevel.WARNING, f"器官降级: {reason or '沉默自愈'}")

    def recover(self) -> None:
        """
        ★F4处置闭环：降级自动恢复（SUSPENDED → RUNNING）。
        与 degrade 互为逆操作，保证「降级 → 恢复」闭环。
        仅当当前处于 SUSPENDED 状态时执行，避免误覆盖其他状态。
        """
        if self.status == OrganStatus.SUSPENDED:
            self.status = OrganStatus.RUNNING
            self._log(LogLevel.INFO, "器官已从降级状态自动恢复")


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== BasePulseOrgan v9.5 并发安全自测 ===\n")
    
    # 1. 抽象类保护
    try:
        organ = BasePulseOrgan("测试器官")
        print("❌ 应抛出 TypeError")
    except TypeError as e:
        print(f"✅ 抽象类保护正常: {e}")
    
    # 2. 具体子类
    class TestOrgan(BasePulseOrgan):
        def on_pulse(self, pulse):
            event_type = pulse.get("event_type", "")
            if event_type == "test.error":
                raise ValueError("模拟业务异常")
            self._log("DEBUG", f"收到脉冲: {event_type}")
            return {"status": "ok"}
        
        def get_resonance_conditions(self):
            return []
    
    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)
            
    mock_field = MockInfoField()
    test_organ = TestOrgan("测试器官")
    test_organ.set_info_field(mock_field)
    test_organ.start()
    
    # 3. _emit 的 layer 参数
    print("3. _emit 新增 layer 参数:")
    test_organ._emit("test.event", {"msg": "hello"}, layer="L0")
    last_pulse = mock_field.published[-1]
    print(f"   发射脉冲 layer={last_pulse.get('layer', '未设置')} (预期L0)")
    assert last_pulse.get("layer") == "L0"
    print("   ✅ layer参数正常")
    
    # 4. 并发安全验证：10线程同时调用正常脉冲
    print("\n4. 并发安全验证（10线程同时调用）:")
    errors_in_threads = []
    
    def concurrent_call(organ, event_type):
        try:
            organ._handle_pulse_safe({"event_type": event_type, "priority": 5})
        except Exception as e:
            errors_in_threads.append(str(e))
    
    threads = []
    for i in range(10):
        t = threading.Thread(target=concurrent_call, args=(test_organ, "test.normal"))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    print(f"   线程异常数: {len(errors_in_threads)} (预期0)")
    assert len(errors_in_threads) == 0
    print("   ✅ 并发安全验证通过")
    
    # 5. 并发熔断验证：混合正常和异常脉冲
    print("\n5. 并发熔断验证（混合正常+异常脉冲）:")
    test_organ._error_count = 0
    test_organ.status = "RUNNING"
    
    mixed_threads = []
    for i in range(5):
        evt = "test.error" if i < 3 else "test.normal"
        t = threading.Thread(target=concurrent_call, args=(test_organ, evt))
        mixed_threads.append(t)
        t.start()
    for t in mixed_threads:
        t.join()
    print(f"   最终状态: {test_organ.status}")
    print(f"   错误计数: {test_organ._error_count}")
    assert test_organ.status == "fused" or test_organ._error_count >= 3
    print("   ✅ 并发熔断验证通过")
    
    # 6. _send 的 layer 透传（需要 mock PulseCore）
    class MockPulseCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            return {
                "pulse_id": "test:1",
                "source_organ": source_organ,
                "event_type": event_type,
                "priority": priority,
                "layer": layer,
                "timestamp_ns": int(time.time_ns()),
                "payload": payload or {},
                "ttl_ns": 5_000_000_000,
            }
    test_organ.set_pulse_core(MockPulseCore())
    mock_field.published.clear()
    test_organ._send("test.send", {"data": 1}, layer="L3")
    sent_pulse = mock_field.published[-1]
    print(f"\n6. _send 透传 layer: pulse['layer']={sent_pulse.get('layer')} (预期L3)")
    assert sent_pulse.get("layer") == "L3"
    print("   ✅ _send layer透传正常")
    
    test_organ.stop()
    print("\n=== 自测全部通过 ===")
    