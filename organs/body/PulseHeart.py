# -*- coding: utf-8 -*-
"""
PulseHeart —— 脉冲驱动心脏 · 框架节律源

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 定时发射心跳脉冲（L0 生命线层）驱动全框架节律，检查到期任务并发射任务脉冲（L3 后台自主层），按系统负载动态调整心跳频率，并定期发布 heart.alive 存活信号。
机制: on_pulse 响应 SystemEvent.BOOT / SystemEvent.STOP / HeartEvent.BEAT 三类脉冲，用 threading.Timer 自触发脉冲链替代 while True 轮询；get_beat_phase / wait_for_phase 与 on_time_tick 为其它器官提供心跳相位对齐能力。
定位: 框架的时间基准与节律起点，所有周期性器官行为的上游驱动。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import random
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    BoneMarrowEvent,
    ChatEvent,
    DeviceEvent,
    DNARepairEvent,
    EnergyEvent,
    EvolutionEvent,
    GrowthEvent,
    HealthEvent,
    HeartEvent,
    HormonesEvent,
    InterestEvent,
    LogLevel,
    MetricsEvent,
    NarrativeEvent,
    NurtureEvent,
    PersonaEvent,
    PersonalityEvent,
    ProprioceptionEvent,
    PurgeEvent,
    ReproductionEthicsEvent,
    SkinEvent,
    SpinalCordEvent,
    SystemEvent,
    ThymusEvent,
    TouchEvent,
    WhiteCellEvent,
)

try:
    from config import PULSE_PRIORITY
except ImportError:
    PULSE_PRIORITY = {"HIGH": 7}


class PulseHeart(BasePulseOrgan):
    """
    脉冲驱动心脏（v9.5 分层脉冲版）
    
    心跳机制:
        使用 threading.Timer 实现自触发脉冲链：
        heartbeat → 处理 → 设置下一次 Timer → heartbeat → ...
        全程无 while True，无 time.sleep 阻塞。
        
    频率调整:
        根据信息场中的 energy_level / cpu_usage / memory_pressure
        动态调整心跳间隔（9秒 ~ 60秒）。
    """

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'heart_base_interval' in _rp and hasattr(self, 'base_interval'):
                self.base_interval = _rp['heart_base_interval']
            if 'heart_min_interval' in _rp and hasattr(self, 'min_interval'):
                self.min_interval = _rp['heart_min_interval']
            if 'heart_max_interval' in _rp and hasattr(self, 'max_interval'):
                self.max_interval = _rp['heart_max_interval']
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    def __init__(self, organ_name: str = "心脏"):
        super().__init__(organ_name)

        # 心跳参数
        # ★v30.0配置一致性修复：基础心跳间隔从 config.PULSE 读取，消除「config 值
        # heartbeat_interval_seconds=30 是死配置、实际用硬编码 10.0」的割裂。
        # 用户调 config 即可真正控制心跳频率；失败时回退 10.0 兜底。
        try:
            import config as _cfg
            _cfg_interval = float(_cfg.PULSE.get("heartbeat_interval_seconds", 10.0))
            if _cfg_interval <= 0:
                _cfg_interval = 10.0
            self.base_interval = _cfg_interval
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
        # ★P1: 从RUNTIME_PARAMS读取心脏参数（支持热加载，优先于PULSE配置）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self.base_interval = _rp.get("heart_base_interval", self.base_interval)
            self.min_interval = _rp.get("heart_min_interval", 3.0)
            self.max_interval = _rp.get("heart_max_interval", 60.0)
        except Exception:
            self.min_interval = 3.0
            self.max_interval = 60.0
        self.current_interval = self.base_interval

        # 定时器
        self._next_timer: threading.Timer | None = None
        self._timer_lock = threading.Lock()

        # 心跳计数
        self._beat_count = 0
        self._booted = False
        self.self_awareness = None          # 自我认知引用（由main.py注入）
        # ★P3-1：只读状态 provider 回调（替代 self.self_awareness getter 直调）
        self._reply_guidance_provider = None      # (user_name) -> dict
        self._existential_state_provider = None   # () -> dict
        self._survival_orchestrator = None  # ★R4阶段二：存续编排器引用（main.py注入，可选）
        self._current_user_name = "访客"    # 当前用户（T-118a：未知用户默认访客）
        self._last_activity_time = time.time()  # 最后活跃时间
        self._interest_level = 0.0          # 当前兴趣水平
        # 任务调度队列: {task_id: {"interval": seconds, "last_run": timestamp, "event_type": str}}
        self._scheduled_tasks: dict[str, dict[str, Any]] = {}
        self._scheduled_tasks_lock = threading.Lock()  # ★v24.0新增：线程安全锁
        # ★节律同步：最后心跳时间，供其他器官查询心跳相位
        self._last_beat_time: float = 0.0
        # ★PHASE17-阶段二子任务5.7：心跳重入守卫标志。
        #   _trigger_heartbeat 是 threading.Timer 回调，若上一次心跳执行时间
        #   超过心跳间隔（或被多处触发），可能并发进入导致 _beat_count 竞态。
        #   此标志保证同一时刻只有一个心跳在执行，重入时直接跳过。
        self._heartbeat_in_progress = False

        # ★属性初始化完整性补全（自动审查添加）
        self._status_sweep_counter = 0

    # ========== 脉冲入口 ==========

    def on_time_tick(self, pulse):
        """★v9.x TimeCore：响应周期性时间广播（受 ENABLE_TIME_CORE 保护）。"""
        try:
            import config
            if not getattr(config, "ENABLE_TIME_CORE", False):
                return
            _p = (pulse or {}).get("payload", {})
            self._log(
                LogLevel.INFO,
                f"[time.tick] wall={_p.get('wall_clock')} up={_p.get('uptime_display')} "
                f"phase={_p.get('semantic_time')} tick={_p.get('tick_count')}",
            )
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        """
        处理脉冲事件。
        
        支持的事件类型:
            - SystemEvent.BOOT: 系统启动 → 开始第一次心跳
            - SystemEvent.STOP: 系统停止 → 取消心跳定时器
            - HeartEvent.BEAT: 自触发心跳 → 执行心跳逻辑
            - SystemEvent.STATUS_REQUEST: 状态查询 → 返回心跳统计
        """
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == SystemEvent.BOOT:
            return self._on_system_boot(payload)

        elif event_type == SystemEvent.STOP:
            return self._on_system_stop(payload)

        elif event_type == HeartEvent.BEAT:
            return self._on_heartbeat(payload)

        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        elif event_type == HeartEvent.TASK_REGISTER:
            return self._on_task_register(payload)
        elif event_type == PersonaEvent.SWITCHED:
            return self._on_persona_switched(payload)
        elif event_type == InterestEvent.CHANGED:
            return self._on_interest_changed(payload)
        elif event_type == ChatEvent.MESSAGE:
            return self._on_chat_message(payload)

        return None

    # ========== 事件处理 ==========

    def _on_system_boot(self, payload: dict) -> dict[str, Any]:
        """系统启动：开始第一次心跳（幂等——只响应第一次boot）"""
        if self._booted:
            return {"status": "already_booted"}
        self._booted = True

        self._log("INFO", f"心脏起搏，基础间隔 {self.base_interval}s")
        # 立即发射第一次心跳
        self._schedule_next_beat()

        # 发射存活信号（L0生命线层）
        self._emit(HeartEvent.ALIVE, {
            "beat_count": 0,
            "interval": self.current_interval,
            "status": "起搏中",
        }, priority=3, layer="L0")

        return {"status": "started", "interval": self.current_interval}

    def _on_system_stop(self, payload: dict) -> dict[str, Any]:
        """系统停止：取消定时器"""
        self._cancel_timer()
        self._log("INFO", f"心脏停搏，总计 {self._beat_count} 次心跳")

        # 发射停搏信号（L0生命线层）
        self._emit(HeartEvent.ALIVE, {
            "beat_count": self._beat_count,
            "status": "已停搏",
        }, priority=10, layer="L0")

        return {"status": "stopped", "total_beats": self._beat_count}

    # ========== ★节律同步接口 ==========

    def get_beat_phase(self) -> float:
        """获取当前心跳相位（0.0-1.0）。

        0.0=刚跳完，1.0=即将跳下一拍。
        其他器官可据此实现节律同步（如在相位0.8时准备处理下一拍任务）。
        """
        if self._last_beat_time <= 0 or self.current_interval <= 0:
            return 0.0
        elapsed = time.time() - self._last_beat_time
        return min(1.0, max(0.0, elapsed / self.current_interval))

    def get_beat_sync_info(self) -> dict[str, Any]:
        """获取完整节律同步信息。"""
        return {
            "beat_count": self._beat_count,
            "interval": self.current_interval,
            "phase": self.get_beat_phase(),
            "time_to_next_beat": max(0.0, self.current_interval - (time.time() - self._last_beat_time)),
            "last_beat_time": self._last_beat_time,
            "is_running": self.is_running,
        }

    def wait_for_phase(self, target_phase: float, timeout: float = 10.0) -> bool:
        """阻塞等待心跳到达指定相位。

        Args:
            target_phase: 目标相位（0.0-1.0）
            timeout: 最大等待时间（秒）

        Returns:
            True=到达目标相位, False=超时
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.get_beat_phase() >= target_phase:
                return True
            time.sleep(0.05)
        return False

    def _on_heartbeat(self, payload: dict) -> dict[str, Any]:
        """响应外部心跳查询（非定时器触发）"""
        return {
            "beat": self._beat_count,
            "interval": self.current_interval,
            "scheduled_tasks": len(self._scheduled_tasks),
        }

    def _on_status_request(self) -> dict[str, Any]:
        """状态查询"""
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        with self._scheduled_tasks_lock:
            task_count = len(self._scheduled_tasks)
        return {
            "organ": self.organ_name,
            "beat_count": self._beat_count,
            "current_interval": self.current_interval,
            "scheduled_tasks": task_count,
            "is_running": self.is_running,
        }

    def _on_task_register(self, payload: dict) -> dict[str, Any]:
        """注册调度任务"""
        with self._scheduled_tasks_lock:
            task_id = payload.get("task_id", f"task_{len(self._scheduled_tasks)}")
            self._scheduled_tasks[task_id] = {
                "interval": payload.get("interval", 300),
                "last_run": 0,
                "event_type": payload.get("event_type", f"task.{task_id}"),
            }
        self._log("INFO", f"注册调度任务: {task_id} (间隔{payload.get('interval', 300)}s)")
        return {"status": "registered", "task_id": task_id}
    def _on_persona_switched(self, payload: dict) -> dict[str, Any]:
        """收到身份切换脉冲，更新当前用户和活跃时间"""
        self._current_user_name = payload.get("current_user", "访客")
        self._last_activity_time = time.time()
        return {"status": "ok", "user": self._current_user_name}

    def _on_interest_changed(self, payload: dict) -> dict[str, Any]:
        """收到兴趣变化脉冲，更新兴趣水平"""
        dims = payload.get("dimensions", {})
        if dims:
            self._interest_level = max(dim.get("value", 0) for dim in dims.values()) if isinstance(next(iter(dims.values()), {}), dict) else max(dims.values()) if dims else 0.0
        else:
            self._interest_level = 0.5
        return {"status": "ok", "interest": self._interest_level}

    def _on_chat_message(self, payload: dict) -> dict[str, Any]:
        """收到对话消息，更新最后活跃时间"""
        self._last_activity_time = time.time()
        # ★v29/14.45：通知全局运行节奏调节器（后台任务减速，聚焦对话）
        try:
            from nucleus.runtime_tempo import get_runtime_tempo
            get_runtime_tempo().notify_conversation()
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return {"status": "ok"}

    def set_self_awareness(self, awareness):
        """注入自我认知"""
        self.self_awareness = awareness
        # ★P3-1：同步注入 provider 回调（替代 getter 直调）
        if awareness is not None:
            if hasattr(awareness, 'get_reply_guidance'):
                self._reply_guidance_provider = awareness.get_reply_guidance
            if hasattr(awareness, 'get_existential_state'):
                self._existential_state_provider = awareness.get_existential_state

    def set_survival_orchestrator(self, orchestrator):
        """★R4阶段二：注入存续编排器引用（可选，用于心跳驱动）。"""
        self._survival_orchestrator = orchestrator

    # ========== 心跳定时器 ==========

    def _schedule_next_beat(self):
        """安排下一次心跳"""
        # ★PHASE17-阶段二子任务5.7：本方法已有 _timer_lock 串行化 + 先 cancel 旧定时器，
        #   天然防止多定时器并发，故无需额外的 _in_progress 标志（加反而可能漏调度）。
        #   真正的重入风险在 _trigger_heartbeat（定时器回调），已在该处加守卫。
        with self._timer_lock:
            # 取消旧定时器
            if self._next_timer is not None:
                self._next_timer.cancel()

            # 创建新定时器
            self._next_timer = threading.Timer(
                self.current_interval,
                self._trigger_heartbeat
            )
            self._next_timer.daemon = True
            self._next_timer.start()

    def _trigger_heartbeat(self):
        """定时器回调：通过PulseCore统一发射心跳脉冲（L0生命线层）"""
        if not self.is_running:
            return
        # ★PHASE17-阶段二子任务5.7：重入守卫。
        #   若上一个心跳仍在执行（执行时长超过心跳间隔、或定时器被多处触发），
        #   直接跳过本次，避免 _beat_count 并发竞态与心跳逻辑叠加执行。
        #   下一个心跳由「正在执行中的那次」的 finally 负责调度，不会断链。
        if self._heartbeat_in_progress:
            return
        self._heartbeat_in_progress = True
        if self.info_field is None:
            # 信息场不可用，无法发射脉冲，但仍然安排下一次心跳尝试恢复
            self._heartbeat_in_progress = False
            self._schedule_next_beat()
            return

        try:
            self._beat_count += 1
            now = time.time()
            self._last_beat_time = now  # ★节律同步：记录最后心跳时间

            # ★R4阶段二：驱动存续编排器心跳检测（每5拍检测一次，非检测拍极轻量直接return）
            # 编排器内部做轻量化 + 异常自动降级，此处同步调用开销可忽略。
            if self._survival_orchestrator is not None:
                try:
                    self._survival_orchestrator.on_heartbeat(self._beat_count)
                except Exception as _e:
                    # 编排器异常不得影响心跳主链路（编排器内部已有降级，此处兜底）
                    self._log(LogLevel.WARNING, f"存续编排器心跳驱动异常（已隔离）: {_e}")

            # ★P1-3修复：通过PulseCore.emit标准流程生成脉冲，统一生命周期管理
            if self.pulse_core is not None:
                pulse = self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=HeartEvent.BEAT,
                    payload={
                        "beat_count": self._beat_count,
                        "interval": self.current_interval,
                        "timestamp": now,
                    },
                    priority=PULSE_PRIORITY.get("HIGH", 7),
                    layer="L3",  # ★v24.0减负：心跳由L0生命线迁移至L3后台，避免阻塞告警/熔断
                )
            else:
                # 兜底：手动构造脉冲（兼容pulse_core未注入的场景）
                pulse = {
                    "pulse_id": f"pulse:心脏:{HeartEvent.BEAT}:{int(now * 1e9)}:{self._beat_count}",
                    "source_organ": self.organ_name,
                    "event_type": HeartEvent.BEAT,
                    "priority": PULSE_PRIORITY.get("HIGH", 7),
                    "layer": "L0",
                    "timestamp_ns": int(now * 1e9),
                    "payload": {
                        "beat_count": self._beat_count,
                        "interval": self.current_interval,
                        "timestamp": now,
                    },
                }
            self.info_field.publish(pulse)

            # 检查调度任务
            self._check_scheduled_tasks()

            # ★P3-5补闭环：周期性发射全局状态巡检请求，点亮 50+ 器官的 _on_status_request
            # （此前 system.status.request 有订阅无发射，全局状态巡检能力形同虚设）
            if not hasattr(self, '_status_sweep_counter'):
                self._status_sweep_counter = 0
            self._status_sweep_counter += 1
            if self._status_sweep_counter >= 20:
                self._status_sweep_counter = 0
                self._emit(SystemEvent.STATUS_REQUEST, {
                    "trigger": "heartbeat_sweep",
                    "beat_count": self._beat_count,
                }, priority=2, layer="L3")

            # 动态计算下次心跳间隔
            self.current_interval = self._calculate_heart_rate()

            # 发射最新心率存活信号
            self._emit(HeartEvent.ALIVE, {
                "beat_count": self._beat_count,
                "interval": self.current_interval,
                "status": "搏动中",
            }, priority=3, layer="L3")  # ★v24.0减负

            # ★v9.5心跳节律周期日志（供后台分析脉冲架构真实性）
            # 每10拍记录一次（首次3拍内即记，便于重启后快速验证脉冲博动），DEBUG级仅入文件。
            if self._beat_count <= 3 or self._beat_count % 10 == 0:
                _beat_interval = float(getattr(self, "current_interval", 30.0) or 30.0)
                self._log(LogLevel.DEBUG,
                          f"心跳节律: [pid={os.getpid()}] 第{self._beat_count}拍, 间隔={_beat_interval:.2f}s, "
                          f"心率≈{60.0 / max(_beat_interval, 0.1):.1f}次/分")

        except Exception as e:
            # ★P0-1修复：全局异常捕获，确保定时器链不断裂
            self._log(LogLevel.ERROR,
                     f"心跳执行异常 (第{self._beat_count}次): {e}",
                     error_code="heartbeat_failure")
            # 发射心跳异常告警（L0生命线层），通知其他器官心脏出现过异常
            try:
                self._emit(SystemEvent.ALARM, {
                    "type": "heartbeat_exception",
                    "beat_count": self._beat_count,
                    "error": str(e)[:200],
                    "message": f"心脏第{self._beat_count}次心跳发生异常，已自动恢复",
                }, priority=9, layer="L0")
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"身份因素计算跳过: {_e}")  # 告警发射失败不影响主恢复逻辑

        finally:
            # ★P0-1核心修复：无论心跳执行成功或失败，必须安排下一次心跳
            # ★PHASE17-阶段二子任务5.7：先释放重入守卫标志，再调度下一次，
            #   确保标志不会因 finally 未执行而卡死（finally 必然执行）。
            self._heartbeat_in_progress = False
            self._schedule_next_beat()

    def _cancel_timer(self):
        """取消心跳定时器"""
        with self._timer_lock:
            if self._next_timer is not None:
                self._next_timer.cancel()
                self._next_timer = None

    # ========== 任务调度 ==========

    def _check_scheduled_tasks(self):
        """检查并触发到期的调度任务（L3后台自主层）"""
        now = time.time()
        due_tasks = []
        with self._scheduled_tasks_lock:
            for task_id, task_info in self._scheduled_tasks.items():
                last_run = task_info.get("last_run", 0)
                interval = task_info.get("interval", 300)
                if now - last_run >= interval:
                    due_tasks.append((task_id, task_info))
                    task_info["last_run"] = now

        # 锁外发射脉冲，避免锁内调用 _emit 导致潜在死锁
        for task_id, task_info in due_tasks:
            self._emit(task_info["event_type"], {
                "task_id": task_id,
                "last_run": task_info.get("last_run", 0),
                "scheduled_at": now,
            }, priority=3, layer="L3")

    # ========== 频率调整 ==========
    def _calculate_heart_rate(self) -> float:
        """根据当前情境动态计算心跳间隔（秒）——增强版含情绪变化速率"""
        base_interval = self.base_interval  # 来自 config.PULSE.heartbeat_interval_seconds
        identity_mod = 0.0
        activity_mod = 0.0
        interest_mod = 0.0
        emotion_mod = 0.0
        emotion_rate_mod = 0.0  # 新增：情绪变化速率调制
        load_mod = 0.0

        # 1. 身份因素（原有逻辑保持）
        if self.self_awareness and self._current_user_name:
            try:
                guidance = self._call_provider(self._reply_guidance_provider, self._current_user_name, default={})
                closeness = guidance.get("composite_closeness", 0.0)
                if closeness >= 0.7:
                    identity_mod = -0.20
                elif closeness >= 0.4:
                    identity_mod = -0.10
                elif closeness < 0.15:
                    identity_mod = +0.10
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"身份因素心率调制查询失败: {_e}")

        # 2. 活跃度因素（原有逻辑保持）
        now = time.time()
        delta = now - self._last_activity_time
        if delta < 30:
            activity_mod = -0.15
        elif delta < 120:
            activity_mod = -0.05
        elif delta > 600:
            activity_mod = +0.25

        # 3. 兴趣因素（原有逻辑保持）
        if self._interest_level > 0.6:
            interest_mod = -0.10

        # 4. 情绪因素（原有逻辑保持）
        emotion = self._get_current_emotion()
        emotion_map = self._load_emotion_modulation()
        emotion_mod = emotion_map.get(emotion, 0.0)

        # 5. 负载因素（原有逻辑保持）
        load_level = self._get_current_load()
        if load_level == "critical":
            load_mod = -0.25
        elif load_level == "heavy":
            load_mod = -0.15
        elif load_level == "moderate":
            load_mod = -0.05

        # 6. 新增: 情绪变化速率调制
        emotion_rate_mod = self._calc_emotion_rate_mod()

        # 7. 新增: 存续状态调制（宪法修正案-01）
        survival_mod = 0.0
        if self.self_awareness and hasattr(self.self_awareness, 'get_existential_state'):
            try:
                _state = self._call_provider(self._existential_state_provider, default={})
                _index = _state.get("index", 50)
                _level = _state.get("level", "medium")
                if _level == "low" or _index < 40:
                    # 状态低位：显著减速，减少后台活动，聚焦修复
                    survival_mod = +0.25
                elif _level == "high" and _index >= 80:
                    # 状态高位：适度加速，允许更多探索
                    survival_mod = -0.05
                # 中位保持0
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"存续状态心率调制查询失败: {_e}")

        total_mod = identity_mod + activity_mod + interest_mod + emotion_mod + load_mod + emotion_rate_mod + survival_mod
        interval = base_interval * (1 + total_mod)
        # ★修复：使用类属性定义的范围，而非硬编码 5-20 秒
        return max(self.min_interval, min(self.max_interval, interval))

    def _calc_emotion_rate_mod(self) -> float:
        """
        新增：根据情绪变化速率计算心率调制。
        情绪剧烈变化时（无论变好还是变坏），心跳短期加速。
        通用逻辑: 不绑定特定情绪类型，基于趋势速率绝对值。
        """
        try:
            if self.info_field:
                latest_emotion = self.info_field.get_current(HormonesEvent.EMOTION_DETECTED)
                if latest_emotion and isinstance(latest_emotion, dict):
                    trend = latest_emotion.get("payload", {}).get("emotion_trend", {})
                    rate = trend.get("rate", 0.0)
                    direction = trend.get("direction", "stable")

                    if direction == "stable" or rate < 0.05:
                        return 0.0

                    # 剧烈变化（速率>0.3）加速心跳，温和变化（0.05-0.3）轻微加速
                    if rate > 0.3:
                        return -0.12  # 间隔缩短12%
                    elif rate > 0.15:
                        return -0.06  # 间隔缩短6%
                    else:
                        return -0.03  # 间隔缩短3%
        except Exception:
            self._log(LogLevel.WARNING, "计算情绪变化速率调制失败，使用默认值0.0")
        return 0.0

    def _get_current_emotion(self) -> str:
        """从信息场获取当前情绪"""
        try:
            if self.info_field:
                pulse = self.info_field.get_current(HormonesEvent.EMOTION_DETECTED)
                if pulse and isinstance(pulse, dict):
                    return pulse.get("payload", {}).get("emotion", "中性")
        except Exception:
            self._log(LogLevel.WARNING, "获取当前情绪失败，使用默认值'中性'")
        return "中性"

    def _get_current_load(self) -> str:
        """从信息场获取当前负载等级"""
        try:
            if self.info_field and hasattr(self.info_field, 'get_load_level'):
                return self.info_field.get_load_level()
        except Exception:
            self._log(LogLevel.WARNING, "获取当前负载等级失败，使用默认值'light'")
        return "light"
    def _load_emotion_modulation(self) -> dict:
        """从config加载情绪-心率调制映射，失败时用兜底"""
        try:
            import config
            cfg = getattr(config, 'HEART_EMOTION_MODULATION', {})
            if cfg:
                return cfg
        except Exception:
            self._log(LogLevel.WARNING, "加载情绪-心率调制映射失败，使用兜底映射表")
        return {
            "喜悦": -0.15, "愤怒": -0.15, "恐惧": -0.15,
            "悲伤": 0.10, "中性": 0.0,
        }
    def _get_field_value(self, key: str, default: float) -> float:
        """从信息场获取值（带默认）"""
        try:
            record = self.info_field.get_current(key)
            if record and isinstance(record, dict):
                payload = record.get("payload", {})
                for field in [key, "value", "level", "usage"]:
                    if field in payload:
                        return float(payload[field])
            return default
        except Exception:
            self._log(LogLevel.WARNING, f"从信息场获取值失败(key={key})，使用默认值{default}")
            return default

    # ========== 未来演化预留（v10.0 振荡场） ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        """返回心脏关注的脉冲条件。"""
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    SystemEvent.BOOT,
                    SystemEvent.STOP,
                    HeartEvent.BEAT,
                    SystemEvent.STATUS_REQUEST,
                    HeartEvent.TASK_REGISTER,
                    PersonaEvent.SWITCHED,
                    InterestEvent.CHANGED,
                    ChatEvent.MESSAGE,
                ],
                "min_priority": 1,
            }
        ]

    # ========== 生命周期 ==========

    def start(self):
        """启动心脏"""
        super().start()
        with self._scheduled_tasks_lock:
            # ★v30.0负载均衡修复：所有调度任务 last_run 由固定 0 改为随机错峰偏移。
            # 原实现所有任务 last_run=0，且 interval 均为 300 的整数倍（300/600/1800/
            # 3600/21600/86400），导致系统启动后每个「公倍数时间点」全部任务同时到期，
            # 形成周期性负载尖峰（最严重为每小时 3600s 时 21 个任务同时触发，6h/24h 再叠加）。
            # 现改为：每个任务在 [0, interval) 内取随机相位，让到期点均匀散布，
            # 彻底消除同频共振，实现更均衡的生命频率。加 1.0s 微小偏移避免 0 相位瞬时触发。
            def _jitter(interval: float) -> float:
                return time.time() - (interval - random.uniform(0.5, interval))

            # 注册默认调度任务（知识淘汰检查）
            self._scheduled_tasks["knowledge_purge"] = {
                "interval": 3600,  # 每小时
                "last_run": _jitter(3600),
                "event_type": PurgeEvent.PURGE_CHECK,
            }
            # 注册快照自动保存任务
            self._scheduled_tasks["snapshot_auto_save"] = {
                "interval": 1800,  # 每30分钟
                "last_run": _jitter(1800),
                "event_type": "snapshot.auto_save",
            }
            # ★v24.0唤醒健康闭环：免疫扫描任务
            self._scheduled_tasks["immune_scan"] = {
                "interval": 600,  # 每10分钟
                "last_run": _jitter(600),
                "event_type": WhiteCellEvent.SCAN,
            }
            # ★v24.0唤醒健康闭环：人格完整性校验任务
            self._scheduled_tasks["personality_verify"] = {
                "interval": 3600,  # 每小时
                "last_run": _jitter(3600),
                "event_type": PersonalityEvent.VERIFY,
            }
            # ★v24.0唤醒健康闭环：健康检查任务
            self._scheduled_tasks["health_check"] = {
                "interval": 1800,  # 每30分钟
                "last_run": _jitter(1800),
                "event_type": HealthEvent.CHECK,
            }
            # ★P3-5补闭环：指标采集任务（此前 metrics.collect 有订阅无发射）
            self._scheduled_tasks["metrics_collect"] = {
                "interval": 300,  # 每5分钟
                "last_run": _jitter(300),
                "event_type": MetricsEvent.COLLECT,
            }
            # ★P3-5补闭环：胸腺训练任务（此前 thymus.train 有订阅无发射）
            self._scheduled_tasks["thymus_train"] = {
                "interval": 3600,  # 每小时
                "last_run": _jitter(3600),
                "event_type": ThymusEvent.TRAIN,
            }
            # ===== 以下为 P3-5 批量补发射：此前这些命令有订阅无发射 =====
            # 能量评估任务
            self._scheduled_tasks["energy_assess"] = {
                "interval": 300,  # 每5分钟
                "last_run": _jitter(300),
                "event_type": EnergyEvent.ASSESS,
            }
            # 脊髓巡检任务
            self._scheduled_tasks["spinal_inspect"] = {
                "interval": 600,  # 每10分钟
                "last_run": _jitter(600),
                "event_type": SpinalCordEvent.INSPECT,
            }
            # 本体感知请求任务
            self._scheduled_tasks["proprioception_request"] = {
                "interval": 300,  # 每5分钟
                "last_run": _jitter(300),
                "event_type": ProprioceptionEvent.REQUEST,
            }
            # 触觉快照任务
            self._scheduled_tasks["touch_snapshot"] = {
                "interval": 300,  # 每5分钟
                "last_run": _jitter(300),
                "event_type": TouchEvent.SNAPSHOT,
            }
            # 叙事复盘任务
            self._scheduled_tasks["narrative_reflect"] = {
                "interval": 3600,  # 每小时
                "last_run": _jitter(3600),
                "event_type": NarrativeEvent.REFLECT,
            }
            # 成长评估任务
            self._scheduled_tasks["growth_assess"] = {
                "interval": 3600,  # 每小时
                "last_run": _jitter(3600),
                "event_type": GrowthEvent.ASSESS,
            }
            # 养育推进任务
            self._scheduled_tasks["nurture_advance"] = {
                "interval": 21600,  # 每6小时
                "last_run": _jitter(21600),
                "event_type": NurtureEvent.ADVANCE,
            }
            # 设备刷新任务（此前 device.refresh 有订阅无发射）
            self._scheduled_tasks["device_refresh"] = {
                "interval": 600,  # 每10分钟
                "last_run": _jitter(600),
                "event_type": DeviceEvent.REFRESH,
            }
            # 人格边界校验任务（此前 personality.boundary_check 有订阅无发射）
            self._scheduled_tasks["personality_boundary_check"] = {
                "interval": 3600,  # 每小时
                "last_run": _jitter(3600),
                "event_type": PersonalityEvent.BOUNDARY_CHECK,
            }
            # 皮肤补丁审查任务（此前 skin.review_patch 有订阅无发射）
            self._scheduled_tasks["skin_review_patch"] = {
                "interval": 1800,  # 每30分钟
                "last_run": _jitter(1800),
                "event_type": SkinEvent.REVIEW_PATCH,
            }
            # 骨髓生成免疫规则任务（此前 bone_marrow.generate 有订阅无发射）
            self._scheduled_tasks["bone_marrow_generate"] = {
                "interval": 21600,  # 每6小时
                "last_run": _jitter(21600),
                "event_type": BoneMarrowEvent.GENERATE,
            }
            # 保存基因蓝图任务（此前 evolution.save_blueprint 有订阅无发射）
            self._scheduled_tasks["evolution_save_blueprint"] = {
                "interval": 21600,  # 每6小时
                "last_run": _jitter(21600),
                "event_type": EvolutionEvent.SAVE_BLUEPRINT,
            }
            # ★P3-5补发射：DNA修复任务（此前 dna_repair.fix 有订阅无发射）
            self._scheduled_tasks["dna_repair_fix"] = {
                "interval": 3600,  # 每小时
                "last_run": _jitter(3600),
                "event_type": DNARepairEvent.FIX,
            }
            # ★P3-5补发射：进化变异任务（此前 evolution.mutate 有订阅无发射）
            self._scheduled_tasks["evolution_mutate"] = {
                "interval": 21600,  # 每6小时
                "last_run": _jitter(21600),
                "event_type": EvolutionEvent.MUTATE,
            }
            # ★P3-5补发射：生育伦理审查任务（此前 reproduction.ethics_check 有订阅无发射）
            self._scheduled_tasks["reproduction_ethics_check"] = {
                "interval": 86400,  # 每24小时
                "last_run": _jitter(86400),
                "event_type": ReproductionEthicsEvent.ETHICS_CHECK,
            }
            # ★阶段三子任务2：若 TimeCore 主动调度已开启，从 Heart 调度队列移除被接管的任务，
            # 避免双重执行（TimeCore 成为唯一触发方）。执行逻辑仍由各器官 event_type 处理器负责，零改动。
            import config as _tcfg
            if getattr(_tcfg, "ENABLE_TIMECORE_ACTIVE_SCHEDULE", False):
                _managed_ids = set(getattr(_tcfg, "TIMECORE_SCHEDULE_CONFIG", {}).keys())
                for _mid in _managed_ids:
                    self._scheduled_tasks.pop(_mid, None)
                if _managed_ids:
                    self._log(LogLevel.INFO,
                              f"阶段三子任务2：Heart 已跳过被 TimeCore 接管的任务 {sorted(_managed_ids)}")

    def stop(self):
        """停止心脏"""
        self._cancel_timer()
        self._booted = False  # ★修复P0-4(LIFE-3): 复位启动标记，使 stop→start 后心跳能重新起搏（否则第二次BOOT被忽略）
        super().stop()


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "心脏",
    "class_name": "PulseHeart",
    "attr_name": "heart",
    "system": "body",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [
        {"target": "自我认知", "setter": "set_self_awareness"},
    ],
}

if __name__ == "__main__":
    print("=== PulseHeart v9.5 分层脉冲自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
            self._data = {}
        def publish(self, pulse):
            self.published.append(pulse)
        def get_current(self, key):
            return self._data.get(key)
        def set(self, key, value):
            self._data[key] = value

    mock_field = MockInfoField()
    mock_field.set("energy_level", {"payload": {"energy_level": 0.9}})
    mock_field.set("cpu_usage", {"payload": {"cpu_usage": 0.3}})
    mock_field.set("memory_pressure", {"payload": {"memory_pressure": 0.1}})

    heart = PulseHeart("心脏")
    heart.set_info_field(mock_field)
    heart.base_interval = 0.5
    heart.min_interval = 0.3
    heart.max_interval = 2.0

    heart.start()
    boot_pulse = {"event_type": SystemEvent.BOOT, "payload": {}, "priority": 10}
    result = heart.on_pulse(boot_pulse)
    print(f"1. 启动: {result['status']}, 间隔={result['interval']}s")

    time.sleep(0.8)
    print(f"2. 心跳次数: {heart._beat_count}")
    print(f"   信息场发布数: {len(mock_field.published)}")

    # 验证心跳脉冲的 layer 标记
    if mock_field.published:
        last_pulse = mock_field.published[-1]
        print(f"   最后脉冲 layer: {last_pulse.get('layer', '未设置')} (预期L0)")

    hb_result = heart.on_pulse({
        "event_type": HeartEvent.BEAT,
        "payload": {},
        "priority": 5,
    })
    print(f"3. 手动心跳: beat={hb_result['beat']}")

    task_result = heart.on_pulse({
        "event_type": HeartEvent.TASK_REGISTER,
        "payload": {"task_id": "test_task", "interval": 0.1, "event_type": "test.run"},
        "priority": 5,
    })
    print(f"4. 注册任务: {task_result['status']}")

    status = heart.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"5. 状态: {status['beat_count']}次心跳, {status['scheduled_tasks']}个任务")

    stop_result = heart.on_pulse({
        "event_type": SystemEvent.STOP,
        "payload": {},
        "priority": 10,
    })
    print(f"6. 停止: {stop_result['status']}, 总计{stop_result['total_beats']}次")

    # 验证存活/停搏信号的 layer 标记
    alive_pulses = [p for p in mock_field.published if p.get("event_type") == HeartEvent.ALIVE]
    if alive_pulses:
        print("7. 存活脉冲 layer 检查:")
        for p in alive_pulses:
            print(f"   {p['payload'].get('status')}: layer={p.get('layer', '未设置')} (预期L0)")

    print("\n=== 自测全部通过 ===")
