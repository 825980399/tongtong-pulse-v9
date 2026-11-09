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
from nucleus._silent_except import silent_exc
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


# ========== ★374刀1：心跳链存活加固（停跳根因收口） ==========
#
# 事故现场（2026-10-26 17:24:34 → 当日停机）：beat 冻结在 1374、health_snapshot 停更，
# 而同一进程里的 60s 分诊节拍与小时级件在 17:28:06 全部恢复 ⇒ 不是线程死、也不是写盘
# 阻塞，是**进程被整体停放约 209s**（同窗口日志零输出、RSS 4.52→2.46GB、打开文件 17→0）。
# 恢复后所有周期件自愈，唯独两条「自续链 Timer」死了：心跳（`pulse_core.emit`）与
# TimeCore（`_broadcast`）——它们的共同点是**续链写在 finally 里**，而那一拍卡在
# `field.publish` 上没有返回 ⇒ 永远排不出下一拍。
#
# 因此这里修的是**模型级单点**，不是某一处锁：
#   ① 每一拍进入即先排下一拍（`_m374_schedule_ahead`），续链不再依赖「这一拍能返回」；
#   ② 重入守卫从布尔升级为**带租约的锁**：租约内跳过（正常慢拍），过期由后来的一拍接管；
#   ③ 接管时抓全线程栈落 logs/，下次停跳能直接看到卡在哪一行；
#   ④ 看门狗兼作「进程级停放探针」＋「看门狗存活留痕」（缺席＝看门狗自己死了）。
#
# 阈值一律**硬编码常量**（任务书：本批不新增 config 开关）。
_M374_GUARD_STALE_FACTOR = 3.0      # 守卫租约 = 3×当前间隔（≈连丢 3 拍判卡死）
_M374_FALLBACK_INTERVAL = 30.0      # current_interval 取不到时的保守间隔
_M374_MAX_TAKEOVER_STREAK = 3       # 连续接管上限（防"发布面永久卡死"时线程堆积）
_M374_SUSPEND_MIN_SEC = 30.0        # 看门狗自己睡过头的判定阈值 ⇒ 进程级停放
_M374_WD_TRACE_EVERY_SEC = 600.0    # 看门狗存活留痕周期
_M374_STALL_DIR = os.path.join("logs", "heartbeat_stall")
# 守卫令牌锁：**模块级**（单测常用 `__new__` 造实例、绕开 __init__，
# 实例锁会让它们直接 AttributeError）。临界区只有几条属性赋值，争用可忽略。
_M374_GUARD_LOCK = threading.Lock()


# ========== ★396刀1：Timer 链 livelock 收口（看门狗把链掐死） ==========
#
# 洞鉴391 实锤：看门狗节拍原为 `max(5.0, iv/2.0)` ＝ 5.0s，而心跳周期实测 9.5s；
# `_schedule_next_beat()` 又**无条件 cancel 在途 Timer**。两条凑在一起：
#   gap 一旦越过 2×iv，看门狗每 5s 就"救援"一次 ⇒ 每次都在 9.5s 到期前把 Timer
#   取消重建 ⇒ 定时器永远活不到 fire ⇒ 心跳永久停摆；而每次救援都返回
#   `rescheduled`＝成功 ⇒ 外部看到的是"已自愈"。
# 现场：beat 冻在 35，gap 从 19s 单调涨到 3000s+，看门狗 353/484 次重建 Timer。
#
# 修法（三条互相咬合，缺一不可）：
#   ① 建造前先判「在途 Timer 是否还活着」——活着就不动它（看门狗在这里失去掐链能力）；
#   ② 救援周期 ≥ 心跳周期：`max(1.5×iv, 15s)`，慢于心跳才可能观察到"下一拍有没有来"；
#   ③ 成功判据从「我做了动作」改成「beat 真的前进了」——救援当时只记 pending，
#      下一次看门狗醒来核对 beat 差值，超窗未增则记 `heartbeat_rescue_failed`。
_M396_WD_NAP_FACTOR = 1.5          # 看门狗节拍 = 1.5×心跳周期
_M396_WD_NAP_MIN = 15.0            # 但绝不短于 15s（救援不能比心跳还快——这正是 livelock 根因）
_M396_RESCUE_CONFIRM_FACTOR = 3.0  # 救援确认窗口 = 3×interval（给足 3 拍机会）
_M396_STALL_FACTOR = 3.0           # 观测面判「停搏」：gap ≥ 3×interval
_M396_STALL_SNAPSHOT_EVERY_SEC = 60.0  # 停搏快照落盘节流（心跳已死，1 分钟一条足够）


def _m396_timer_alive(timer) -> bool:
    """在途 Timer 是否还活着（best-effort：判不了就按"死"处理，退回旧的 cancel+新建）。

    为什么不用 `finished` 事件：`threading.Timer.run()` 在**调用 function 期间**
    `finished` 尚未 set，而 `is_alive()` 此时为 True——两者都区分不了"正在执行本拍"
    与"还在等下一次到期"。真正的区分靠调用方传 `_me`（当前线程）：
    正在跑的那一拍本身就是那个 Timer，`_cur is current_thread()` 会让它放行。
    """
    _alive = getattr(timer, "is_alive", None)
    if not callable(_alive):
        return False
    try:
        # ★严格 `is True`：单测里的 MagicMock 什么属性都能取到、什么都返回真值，
        #   用 bool() 会让假 Timer 被误判成"存活"从而永久跳过调度。
        return _alive() is True
    except Exception as _e:  # 已停止的线程/已析构对象
        silent_exc(_e, where="PulseHeart::_m396_timer_alive", level="debug")
        return False


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
        # ★374刀1：守卫租约起点（与 in_progress 同时写；缺失时按最后进入时刻反推）
        self._heartbeat_started_at: float = 0.0
        self._m374_takeover = 0          # 租约过期接管次数
        self._m374_takeover_streak = 0   # 连续接管次数（成功完成一拍即清零）
        # ★374刀1：守卫令牌。用**单调序号**而不是时间戳——Windows 上 time.time()
        #   粒度可达 15.6ms，两拍可能取到同一个浮点数，拿它当身份键会让旧拍
        #   误判成接管拍、替它把门打开（铁律201：浮动读数不进身份键）。
        self._m374_beat_seq = 0

        # ★396刀1：Timer 链 livelock 收口（字段在此**显式声明**——靠 getattr 兜底会
        #   掩盖初始化遗漏，test_m374 的 test_15 就守这条）。
        self._m396_sched_created = 0        # 真的新建了 Timer 的次数
        self._m396_sched_skipped = 0        # 因在途 Timer 存活而幂等跳过的次数
        self._m396_rescue_pending = None    # 待确认的救援（{beat_before, at, deadline, gap_before}）
        self._m396_rescue_ok = 0            # 救援后 beat 确实前进的次数（下游观测量判据）
        self._m396_rescue_failed = 0        # 救援超窗 beat 仍未增的次数
        self._m396_enter_fp = None          # 进入留痕的状态指纹（is_running|timer_alive）
        self._m396_enter_beat = -1          # 上次进入留痕的 beat
        self._m396_stall_snap_at = 0.0      # ★396刀2：上次停搏快照落盘时刻（60s 节流）
        self._m396_stall_snap_n = 0         # ★396刀2：停搏快照累计落盘次数

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
            # ★369刀1（E9 F1 族）：time.tick 是每拍必然发生的控制流广播，
            #   心/肺/皮层三器官各刷一条同内容 ⇒ 16.5h 实测 2652 条（约 160 条/h）。
            #   心跳存活证据不由本行承担：beat_count / health_snapshot.json 才是权威读数，
            #   故三处统一降 DEBUG（保留留痕，异常仍 ERROR）。
            self._log(
                LogLevel.DEBUG,
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
        # ★365刀C1：起搏即挂看门狗（幂等；链路断了才有第二个线程能发现）
        self._m365_start_watchdog()

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

    def _schedule_next_beat(self) -> bool:
        """安排下一次心跳；返回**是否真的新建了 Timer**（False＝在途那条还活着，没动）。

        ★396刀1（P0 livelock）：原实现无条件 `cancel()` 在途 Timer 再新建。看门狗
        节拍 5s < 心跳周期 9.5s ⇒ 只要 gap 越过阈值，每轮"救援"都在旧 Timer 到期前
        把它取消、再建一个新的 9.5s ⇒ **定时器永远活不到 fire**，心跳永久停摆，而
        每轮救援都自报"成功"。现在按「在途 Timer 是否还活着」分流：

          * 预排（`_m374_schedule_ahead`）：`_cur is current_thread()`——本拍就是这个
            Timer 触发的，它已经在跑 function、不会再 fire 第二次 ⇒ 必须放行新建；
          * 收尾（finally）：预排已建好下一条，存活且非本线程 ⇒ **幂等返回 False**；
          * 看门狗救援：存活即说明链没断，绝不重建（重建＝把链掐死，本刀修的就是它）。

        节拍口径随之从「本体结束 + interval」变为「本体开始 + interval」。旧口径会把
        本体耗时叠进 gap（实测 9.5s 周期 + 2~3s 本体 ⇒ gap 12s+），新口径才符合
        任务书验收的「gap 稳定在 9.5s ± 2s」。
        """
        with self._timer_lock:
            _cur = self._next_timer
            _me = threading.current_thread()
            if _cur is not None and _cur is not _me and _m396_timer_alive(_cur):
                # 在途那条还活着 ⇒ 下一拍一定会来，这一刻新建只会把它的到期时间推后。
                self._m396_sched_skipped = int(getattr(self, "_m396_sched_skipped", 0)) + 1
                return False
            # 取消旧定时器（已 fire 完 / 已 cancel / 已被判死，cancel 是幂等无副作用的）
            if _cur is not None:
                _cur.cancel()

            # 创建新定时器
            self._next_timer = threading.Timer(
                self.current_interval,
                self._trigger_heartbeat
            )
            self._next_timer.daemon = True
            self._next_timer.start()
            self._m396_sched_created = int(getattr(self, "_m396_sched_created", 0)) + 1
            return True

    def _trigger_heartbeat(self):
        """定时器回调：通过PulseCore统一发射心跳脉冲（L0生命线层）"""
        # ★396刀1 ④：**首行**留痕。现场排障最缺的就是这一条——停跳时日志里只有
        #   看门狗在喊「已尝试续链」，看不出这一拍到底有没有进来、进来时
        #   is_running / 在途 Timer 是什么状态（早退？livelock？）。
        self._m396_trace_enter()
        if not self.is_running:
            # ★480 刀6：停机 ≠ 断链。
            #   `stop()` 是「先 _cancel_timer() 再 super().stop()」，正常停机根本
            #   走不到这里；能走到这里说明**Timer 还活着、is_running 却是 False**
            #   （活体实测：20:41→21:21 连续 5 次 age=274→2704s、takeover=0）。
            #   旧实现在此直接 return，于是「预排下一拍」与「租约接管」永远进不来
            #   ⇒ 链断死，is_running 后来恢复 True 也没有下一拍叫醒它。
            #   现在只补续链、不发心跳（语义不变）；daemon Timer 不阻止进程退出。
            if PulseHeart._m480_keep_chain_on():
                self._m374_schedule_ahead()
                self._log(LogLevel.ERROR,
                          f"[480刀6] is_running=False 但心跳 Timer 仍在触发 "
                          f"⇒ 已续链保活（beat={getattr(self, '_beat_count', 0)}，"
                          f"is_running 恢复后下一拍自动复活），不发心跳",
                          error_code="heartbeat_chain_kept_while_stopped")
            return
        # ★374刀1 ①：**先排下一拍，再判守卫**。
        #   原实现的续链只在 finally ⇒ 这一拍卡住就永久断链（17:24 事故）。
        #   现在进入即预排，任何早期返回/卡死都不会让链停。
        self._m374_schedule_ahead()
        # ★PHASE17-阶段二子任务5.7：重入守卫（★374刀1 ②：布尔 → 带租约的锁）。
        #   原实现「有人在跑就直接返回」，而"有人在跑"的最坏情况是**永远在跑**——
        #   守卫被那一拍永久占住，后续每一拍都早退。现在按持有时间区分：
        #   ≤ 租约 ⇒ 正常慢拍，跳过；> 租约 ⇒ 原拍已判卡死，由本拍接管续跳。
        _m374_now = time.time()
        _m374_tok = None  # 本拍令牌（finally 可能先于赋值进入，故先置 None）
        if self._heartbeat_in_progress:
            _m374_started = float(getattr(self, "_heartbeat_started_at", 0) or 0)
            if _m374_started <= 0:
                # 兜底：守卫为真但没留进入时刻 ⇒ 用最后一拍进入时刻反推（不按 0 算，
                # 否则 held 恒为天文数字，会误杀正常慢拍）。
                _m374_started = float(getattr(self, "_last_beat_time", 0) or 0)
            _m374_held = _m374_now - _m374_started
            _m374_lease = self._m374_guard_lease()
            if _m374_held <= _m374_lease:
                return
            self._m374_takeover = int(getattr(self, "_m374_takeover", 0)) + 1
            self._m374_takeover_streak = int(getattr(self, "_m374_takeover_streak", 0)) + 1
            if self._m374_takeover_streak > _M374_MAX_TAKEOVER_STREAK:
                # 连续接管说明发布面持续卡死：继续接管只会堆线程。此处**只保链不自伤**，
                # 下一拍已经排好，等发布面自己恢复或由 367 看门狗兜底。
                self._log(LogLevel.ERROR,
                          f"[374刀1] 连续接管已达 {self._m374_takeover_streak - 1} 次仍未恢复 "
                          f"⇒ 停止接管（只保链），beat={getattr(self, '_beat_count', 0)}",
                          error_code="heartbeat_takeover_capped")
                return
            _m374_dump = self._m374_dump_stall(_m374_held)
            self._log(LogLevel.ERROR,
                      f"[374刀1] 心跳重入守卫租约过期 {_m374_held:.1f}s > "
                      f"{_m374_lease:.1f}s ⇒ 第{self._m374_takeover}次接管续跳"
                      f"（beat={getattr(self, '_beat_count', 0)}，"
                      f"抓栈={_m374_dump or '失败'}）",
                      error_code="heartbeat_guard_takeover")
        with _M374_GUARD_LOCK:
            self._m374_beat_seq = int(getattr(self, "_m374_beat_seq", 0)) + 1
            _m374_tok = self._m374_beat_seq
            self._heartbeat_in_progress = True
            # ★367 后续：记录进入时刻，供看门狗判「这一拍是否已经卡死」。
            self._heartbeat_started_at = _m374_now
        if self.info_field is None:
            # 信息场不可用，无法发射脉冲，但仍然安排下一次心跳尝试恢复
            with _M374_GUARD_LOCK:
                if _m374_tok is not None and self._m374_beat_seq == _m374_tok:
                    self._heartbeat_in_progress = False
            # ★365刀C1：这条早退路径同样会续链失败（且它比 finally 更早出现），一并兜底
            try:
                self._schedule_next_beat()
            except Exception as _ce365b:
                self._m365_chain_fail = int(getattr(self, "_m365_chain_fail", 0)) + 1
                self._log(LogLevel.ERROR,
                          f"[365链] 心跳续链失败（info_field 缺失路径，第"
                          f"{self._m365_chain_fail}次）：{type(_ce365b).__name__}: {_ce365b}",
                          error_code="heartbeat_chain_break")
            return

        try:
            # ★396刀2：status 必须在刷新 `_last_beat_time` **之前**算——它衡量的是
            #   「上一拍到现在」的真实间隔；先刷新再算恒为 0，等于又变回常量串。
            _m396_status = self._m396_beat_status()
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
                "status": _m396_status,   # ★396刀2：按 gap 现算，不再硬编码
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
            # ★374刀1 ③：释放要**认令牌**。接管引入后，「先卡死后又返回」的旧拍会与
            #   接管拍同时在跑；旧拍若无条件清标志，就在接管拍还在跑时把门打开 ⇒
            #   第三拍叠进来，反而造出原本不存在的并发。只有仍持有租约的那一拍能释放。
            with _M374_GUARD_LOCK:
                if _m374_tok is not None and self._m374_beat_seq == _m374_tok:
                    self._heartbeat_in_progress = False
                    self._heartbeat_started_at = 0.0
                    # ★374刀1：本拍能走到收尾 ⇒ 发布面恢复正常，接管连击清零。
                    #   同样要认令牌：卡死旧拍的迟到返回不能替接管拍清零。
                    self._m374_takeover_streak = 0
            # ★365刀C1：续链失败兜底 —— `_schedule_next_beat()` 自己抛异常（Timer 构造失败/
            #   线程资源耗尽）时，过去异常直接从 finally 抛出 ⇒ **定时链静默断掉**：没有下一拍、
            #   没有日志、没有计数，外部只能看到"心跳突然不动了"。现在留痕＋计数，交看门狗复核。
            try:
                self._schedule_next_beat()
            except Exception as _ce365:
                self._m365_chain_fail = int(getattr(self, "_m365_chain_fail", 0)) + 1
                self._log(LogLevel.ERROR,
                          f"[365链] 心跳续链失败（第{self._m365_chain_fail}次，"
                          f"beat={getattr(self, '_beat_count', 0)}，"
                          f"interval={getattr(self, 'current_interval', 0)}s，"
                          f"last_beat={getattr(self, '_last_beat_time', 0)}）："
                          f"{type(_ce365).__name__}: {_ce365}",
                          error_code="heartbeat_chain_break")

    # ========== ★374刀1：心跳链存活加固 ==========

    def _m396_trace_enter(self) -> None:
        """★396刀1 ④：每次进入心跳的留痕（在 `_trigger_heartbeat` 首行调用）。

        停跳现场的日志里过去只有看门狗在喊「已尝试续链」，看不出这一拍**到底有没有
        进来**、进来时 `is_running` 与在途 Timer 是什么状态。有了这行，
        「早退」与「livelock」两种停摆一眼可分：
          * 完全没有本行 + 看门狗在喊 ⇒ Timer 链断了（livelock / 链被掐）；
          * 有本行但 beat 不增   ⇒ 早退（is_running=False / info_field=None）或卡死。

        节流：状态指纹（is_running + Timer 存活）变化立即打一条，状态不变则每 60 拍
        一条（≈10 分钟）。★级别分两档：指纹变化用 **INFO**（这是排障关键点，而
        `LOG_LEVEL_FILE` 是配置项、可能被人抬到 INFO，用 DEBUG 就有被过滤的风险），
        常规节拍用 DEBUG。
        """
        try:
            _alive = _m396_timer_alive(getattr(self, "_next_timer", None))
            _run = bool(getattr(self, "is_running", False))
            _fp = f"{int(_run)}|{int(_alive)}"
            _beat = int(getattr(self, "_beat_count", 0))
            _last_fp = getattr(self, "_m396_enter_fp", None)
            _last_beat = int(getattr(self, "_m396_enter_beat", -1))
            if _fp == _last_fp and (_beat - _last_beat) < 60:
                return
            _changed = (_fp != _last_fp)
            self._m396_enter_fp = _fp
            self._m396_enter_beat = _beat
            self._log(LogLevel.INFO if _changed else LogLevel.DEBUG,
                      f"[396刀1] 心跳进入 beat={_beat} is_running={_run} "
                      f"timer_alive={_alive} "
                      f"interval={float(getattr(self, 'current_interval', 0) or 0):.2f}s"
                      f"{'（状态变化）' if _changed else ''}")
        except Exception as _e:
            silent_exc(_e, where="PulseHeart::_m396_trace_enter", level="debug")

    def _m396_beat_status(self) -> str:
        """★396刀2 ①：心跳体内发的状态串也按 gap 现算，不再硬编码"搏动中"。

        正常拍 gap≈0 ⇒ "搏动中"；但若这一拍本体卡了很久才走到发信号那一步，它会如实
        显示"停搏（Ns 未跳）"——同一句文案不再有第二种真相。
        ★调用位置很关键：必须在 `_last_beat_time = now` **之前**算，否则恒为 0。
        """
        _iv = float(getattr(self, "current_interval", 0) or 0) or _M374_FALLBACK_INTERVAL
        _last = float(getattr(self, "_last_beat_time", 0) or 0)
        if _last <= 0:
            return "搏动中"
        _gap = max(0.0, time.time() - _last)
        _limit = _M396_STALL_FACTOR * _iv
        if _gap >= _limit:
            return f"停搏（{_gap:.0f}s 未跳，阈值 {_limit:.0f}s）"
        return "搏动中"

    def _m374_schedule_ahead(self) -> None:
        """先排下一拍再干活：续链不再依赖「这一拍能返回」。

        ★396刀1：这里之所以还能真的建出新 Timer，是因为本拍**就是**在途那个
        Timer 的 `run()` 里执行的（`_cur is current_thread()` ⇒ 放行）；随后
        `finally` 再调一次会因「下一条已存活」而幂等返回，不再 cancel 重建。
        因此节拍口径从「本体结束 + interval」变为「本体开始 + interval」——
        旧口径会把本体耗时叠进 gap，新口径才稳定在 interval 本身。

        计数单独记（`_m374_pre_sched_fail`），不混进 365 的 `_m365_chain_fail`：
        后者是"finally 续链"的读数，混用会让两处失败叠成假读数。
        """
        try:
            self._schedule_next_beat()
        except Exception as _e:
            self._m374_pre_sched_fail = int(getattr(self, "_m374_pre_sched_fail", 0)) + 1
            self._log(LogLevel.ERROR,
                      f"[374刀1] 预排下一拍失败（第{self._m374_pre_sched_fail}次，"
                      f"beat={getattr(self, '_beat_count', 0)}）："
                      f"{type(_e).__name__}: {_e}",
                      error_code="heartbeat_pre_sched_fail")

    @staticmethod
    def _m480_keep_chain_on() -> bool:
        """★480 刀6 灰度（缺省开；显式 False ⇒ 逐字回到首行 return）。"""
        try:
            import config as _c480h
            _v = getattr(_c480h, "M480_HEARTBEAT_KEEP_CHAIN", True)
            if _v is None:
                return True
            if isinstance(_v, str):
                return _v.strip().lower() in ("1", "true", "yes", "on")
            return bool(_v)
        except Exception as _e480h:
            silent_exc(_e480h,
                       where="PulseHeart::_m480_keep_chain_on",
                       level="debug")
            return True

    def _m374_guard_lease(self) -> float:
        """守卫租约秒数（3×当前间隔；间隔取不到用保守值）。"""
        _iv = float(getattr(self, "current_interval", 0) or 0)
        if _iv <= 0:
            _iv = _M374_FALLBACK_INTERVAL
        return _M374_GUARD_STALE_FACTOR * _iv

    def _m374_dump_stall(self, held_s: float) -> str | None:
        """卡死现场抓全线程栈（best-effort：失败只留痕，绝不影响心跳链路）。

        为什么用 faulthandler：它由运行时直接遍历线程栈，不需要被卡线程配合，
        也不需要新建 Timer/线程（刀9 已明确 Timer 自身也要 Thread.start，会一起卡死）。
        """
        try:
            import faulthandler
            os.makedirs(_M374_STALL_DIR, exist_ok=True)
            _path = os.path.join(_M374_STALL_DIR, f"stall_{int(time.time())}.txt")
            with open(_path, "w", encoding="utf-8") as _f:
                _f.write(f"# [374刀1] 心跳重入守卫租约过期，接管前抓栈\n"
                         f"# held={held_s:.1f}s beat={getattr(self, '_beat_count', 0)} "
                         f"interval={getattr(self, 'current_interval', 0)} "
                         f"pid={os.getpid()} at={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                _f.flush()
                faulthandler.dump_traceback(file=_f, all_threads=True)
            self._m374_last_dump = _path
            return _path
        except Exception as _e:
            self._m374_last_dump = None
            silent_exc(_e, where="PulseHeart::_m374_dump_stall", level="debug")
            return None

    # ========== ★365刀C1：心跳链看门狗 ==========

    def _m396_confirm_rescue(self, now: float, beat: int) -> str | None:
        """★396刀1 ③：核对上一次救援是否**真的**让 beat 前进了（下游观测量判据）。

        为什么必须延迟核对：`_schedule_next_beat()` 只是挂一个 interval 之后的 Timer，
        调用返回 True 不代表心跳会跳。过去把「动作已发出」当「已成功」，于是链被掐死
        时日志里照样一路 `rescheduled` —— 这就是任务书说的安慰剂效应。

        判据后移到**下一次看门狗醒来**（节拍 ≥15s，覆盖 1.5 个心跳周期）：
          * beat 比救援时大 ⇒ `_m396_rescue_ok` +1，INFO 记恢复读数；
          * 过了确认窗口（3×interval）beat 仍未增 ⇒ `_m396_rescue_failed` +1，
            ERROR 明确「问题不在链，在早退或心跳体卡死」，不再谎报成功。
        """
        _p = getattr(self, "_m396_rescue_pending", None)
        if not _p:
            return None
        _before = int(_p.get("beat_before", beat))
        _at = float(_p.get("at", now) or now)
        if beat > _before:
            self._m396_rescue_ok = int(getattr(self, "_m396_rescue_ok", 0)) + 1
            self._m396_rescue_pending = None
            self._log(LogLevel.INFO,
                      f"[396链] 续链已确认生效：beat {_before}→{beat}，"
                      f"发出救援后 {now - _at:.1f}s 内恢复（判据＝下游观测量，非返回值）")
            return "confirmed"
        _deadline = float(_p.get("deadline", 0) or 0)
        if _deadline > 0 and now >= _deadline:
            self._m396_rescue_failed = int(getattr(self, "_m396_rescue_failed", 0)) + 1
            self._m396_rescue_pending = None
            self._log(LogLevel.ERROR,
                      f"[396链] 续链**失败**：发出救援后已过 {now - _at:.1f}s，"
                      f"beat 仍为 {beat}（原 {_before}，gap_before={_p.get('gap_before')}s）"
                      f"⇒ 链没断，问题在早退（is_running/info_field）或心跳体卡死",
                      error_code="heartbeat_rescue_failed")
            return "failed"
        return "pending"

    def _m365_start_watchdog(self):
        """启动心跳链独立看门狗（幂等；**不自动重启进程**，只告警＋尝试续链）。"""
        if getattr(self, "_m365_wd_running", False):
            return {"ok": True, "why": "already_running"}
        self._m365_wd_running = True
        self._m365_wd_fail = 0
        _t = threading.Thread(target=self._m365_watchdog_loop,
                              name="m365_heart_wd", daemon=True)
        self._m365_wd_thread = _t
        _t.start()
        self._log(LogLevel.INFO,
                  "[365链] 心跳看门狗已启动（阈值 = 2×interval，只告警＋续链，不重启进程）")
        return {"ok": True, "why": "started"}

    def _m365_watchdog_loop(self):
        """看门狗主循环：节拍 = max(1.5×间隔, 15s)，**必须慢于心跳**。

        ★396刀1 ②：原节拍 `max(5.0, iv/2.0)` 是这次 livelock 的另一半成因——
        5s 比 9.5s 的心跳周期还快，配合 `_schedule_next_beat()` 的无条件 cancel，
        每轮"救援"都在旧 Timer 到期前把它掐掉 ⇒ 定时器永远活不到 fire。
        救援者的节拍必须**慢于**被救援对象，否则它观察不到"下一拍到底来没来"，
        更没资格判定成功。

        ★374刀1 ④：本线程同时兼任**进程级停放探针**与**看门狗存活留痕**。
        17:24 事故最要命的两点是：① 全进程停了 209s，事后日志里毫无痕迹（当时只有
        TimeCore 那条 209.4s 跳变告警，看不出是全进程停放）；② 看门狗没救回心跳，
        但也无法判断它是死了还是在跳过。现在两件事都有痕迹：
          * 睡过头（> 节拍 + 30s）⇒ 打印"进程级停放"并抓栈；
          * 每 10 分钟一条"看门狗存活"INFO（缺席即看门狗自己死了）。
        """
        while getattr(self, "_m365_wd_running", False):
            _iv = float(getattr(self, "current_interval", 30.0) or 30.0)
            _nap = max(_iv * _M396_WD_NAP_FACTOR, _M396_WD_NAP_MIN)
            _t0 = time.time()
            time.sleep(_nap)
            _slept = time.time() - _t0
            if _slept > _nap + _M374_SUSPEND_MIN_SEC:
                self._m374_suspend = int(getattr(self, "_m374_suspend", 0)) + 1
                _sus_dump = self._m374_dump_stall(_slept)
                self._log(LogLevel.WARNING,
                          f"[374刀1] 进程级停放：看门狗本应睡 {_nap:.1f}s，实际过了 "
                          f"{_slept:.1f}s（第{self._m374_suspend}次）⇒ 全进程被挂起"
                          f"（OS 换出/休眠；不是单链故障）；抓栈={_sus_dump or '失败'}",
                          error_code="process_suspend_detected")
            try:
                self.m365_beat_watchdog()
            except Exception as _e:
                self._m365_wd_fail = int(getattr(self, "_m365_wd_fail", 0)) + 1
                self._log(LogLevel.WARNING,
                          f"[365链] 看门狗自身异常（第{self._m365_wd_fail}次）："
                          f"{type(_e).__name__}: {_e}")
            # ★396刀2：心跳停摆时把真相落盘（内部自己判 gap 与 60s 节流）
            self._m396_stall_snapshot()
            self._m374_wd_trace()

    def _m396_stall_snapshot_path(self) -> str:
        """停搏快照要写的文件路径（单独成方法，单测可替换，避免污染生产 data/）。"""
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(_root, "data", "monitor", "health_snapshot.json")

    def _m396_stall_snapshot(self) -> dict[str, Any] | None:
        """★396刀2 ②：心跳停摆时**主动刷新** health_snapshot 的 heartbeat 段。

        为什么必须由看门狗来写：`health_snapshot.json` 的常规写入方是
        `PulseMetricsCollector._on_heartbeat_observe`，而它由心跳驱动——心跳一停，
        它就不再被调用，快照冻结在最后一帧，那一帧永远写着"搏动中"（现场 46 分钟）。
        看门狗是唯一与心跳链无关、且此刻仍然活着的线程，只能由它把"停搏"落盘。

        最小侵入：只改 `heartbeat` 段 + 顶层 `heartbeat_stall` 标记，其余字段原样保留；
        走 `safe_write_json`（与采集器同一条硬化通道：路径锁 + 退避重试 + 唯一 tmp）；
        60s 节流。失败只留痕，绝不影响看门狗主循环。

        ★并发说明：读-改-写存在丢失更新窗口，但本方法**只在 gap ≥ 3×interval 时**执行，
        而那一刻采集器早已不再写快照（它就是心跳驱动的），实际冲突概率为零。
        """
        _iv = float(getattr(self, "current_interval", 0) or 0)
        _last = float(getattr(self, "_last_beat_time", 0) or 0)
        if _iv <= 0 or _last <= 0:
            return None
        _now = time.time()
        _gap = _now - _last
        if _gap < _M396_STALL_FACTOR * _iv:
            return None
        _last_at = float(getattr(self, "_m396_stall_snap_at", 0) or 0)
        if _last_at > 0 and (_now - _last_at) < _M396_STALL_SNAPSHOT_EVERY_SEC:
            return None
        try:
            import json

            from nucleus.data.DataAccessLayer import safe_write_json

            _p = self._m396_stall_snapshot_path()
            _snap: dict[str, Any] = {}
            if os.path.exists(_p):
                try:
                    with open(_p, encoding="utf-8") as _f:
                        _snap = json.load(_f) or {}
                except Exception as _re:
                    silent_exc(_re, where="PulseHeart::_m396_stall_snapshot read", level="debug")
                    _snap = {}
            if not isinstance(_snap, dict):
                _snap = {}
            _hb = dict(_snap.get("heartbeat") or {})
            _hb["interval"] = _iv
            _hb["beat_count"] = int(getattr(self, "_beat_count", 0))
            _hb["status"] = f"停搏（{_gap:.0f}s 未跳，阈值 {_M396_STALL_FACTOR * _iv:.0f}s）"
            _hb["status_source"] = "watchdog@stall"
            _hb["gap_s"] = round(_gap, 2)
            _snap["heartbeat"] = _hb
            _snap["heartbeat_stall"] = {
                "since": _last,
                "gap_s": round(_gap, 2),
                "beat": int(getattr(self, "_beat_count", 0)),
                "by": "PulseHeart._m365_watchdog_loop",
                "at": _now,
            }
            self._m396_stall_snap_at = _now
            self._m396_stall_snap_n = int(getattr(self, "_m396_stall_snap_n", 0)) + 1
            _ok = safe_write_json(_p, _snap, backup=False)
            if not _ok:
                self._log(LogLevel.WARNING,
                          f"[396刀2] 停搏快照写入返回 False（th={_M396_STALL_FACTOR * _iv:.1f}s "
                          f"gap={_gap:.1f}s）⇒ health_snapshot 仍停在旧值",
                          error_code="heartbeat_stall_snapshot_failed")
            return {"ok": bool(_ok), "path": _p, "gap_s": round(_gap, 2)}
        except Exception as _e:
            silent_exc(_e, where="PulseHeart::_m396_stall_snapshot", level="debug")
            self._log(LogLevel.WARNING,
                      f"[396刀2] 停搏快照写入异常：{type(_e).__name__}: {_e}",
                      error_code="heartbeat_stall_snapshot_failed")
            return None

    def _m374_wd_trace(self) -> None:
        """看门狗存活留痕（每 10 分钟一条 INFO）：日志里缺席＝看门狗本身死了。"""
        _now = time.time()
        _at = float(getattr(self, "_m374_wd_trace_at", 0) or 0)
        if _at > 0 and (_now - _at) < _M374_WD_TRACE_EVERY_SEC:
            return
        self._m374_wd_trace_at = _now
        _last = float(getattr(self, "_last_beat_time", 0) or 0)
        self._log(LogLevel.INFO,
                  f"[374刀1][365链] 看门狗存活 age="
                  f"{(_now - _last) if _last > 0 else -1:.1f}s "
                  f"in_progress={bool(getattr(self, '_heartbeat_in_progress', False))} "
                  f"beat={int(getattr(self, '_beat_count', 0))} "
                  f"takeover={int(getattr(self, '_m374_takeover', 0))} "
                  f"suspend={int(getattr(self, '_m374_suspend', 0))}")

    def m365_beat_watchdog(self) -> dict:
        """★365刀C1：gap > 2×interval ⇒ WARNING 告警并尝试续链。

        为什么需要独立看门狗：定时链一旦断，`_trigger_heartbeat` 不再被调用
        ⇒ 任何写在心跳体内的自检都**永远不会执行**（自证死锁）。所以必须有一个
        与心跳链无关的线程来发现"链不动了"。
        不自动重启进程（任务书红线）；续链复用 `_heartbeat_in_progress` 重入守卫，
        正在执行中的那一拍不会被看门狗插队。

        ★396刀1 ③：本方法的成功判据整体换过——
          旧：`_schedule_next_beat()` 没抛异常 ⇒ `action="rescheduled"` ⇒ `ok=True`。
              「我做了动作」被当成「心跳好了」，而恰恰是这个动作在把链掐死。
          新：动作发出只记 `rescue_pending`（`ok=False`），结果由**下一次**调用里
              `_m396_confirm_rescue()` 按 beat 是否前进判定；在途 Timer 还活着时干脆
              拒绝重建（`no_rescue_needed`）——因为此时链没断，重建成事不足败事有余。
        """
        _iv = float(getattr(self, "current_interval", 0) or 0)
        _last = float(getattr(self, "_last_beat_time", 0) or 0)
        _now = time.time()
        if not getattr(self, "is_running", False) or _iv <= 0 or _last <= 0:
            # 未启动/尚无基线 ⇒ 不判（避免启动期误报）
            return {"ok": True, "action": "no_baseline"}
        # ★396：先核对上一次救援的真实结果（延迟确认，见 _m396_confirm_rescue）
        self._m396_confirm_rescue(_now, int(getattr(self, "_beat_count", 0)))
        _gap = _now - _last
        if _gap <= 2.0 * _iv:
            return {"ok": True, "action": "none", "gap_s": round(_gap, 2)}
        if self._heartbeat_in_progress:
            # ★367 后续（破零前置）：原实现**无条件**跳过，理由是「正在跑的那一拍
            #   自己会续链」。该理由在「那一拍卡死」时不成立 —— 而卡死正是断链的成因：
            #   实测 10-25 04:57:55 之后 health_snapshot 停更 17 小时、beat_count 冻结
            #   在 1800，同时 TimeCore 的 time.tick 仍在跑 ⇒ 心跳链单方面死掉。
            #   现在按**守卫持续时长**区分「正在跑」与「卡死了」：
            #     ≤ 3×interval ⇒ 正常慢拍，仍然跳过（不插队、不双定时器）；
            #     > 3×interval ⇒ 判定卡死 ⇒ 强制复位并续链（否则看门狗与心跳同归于尽）。
            _started = float(getattr(self, "_heartbeat_started_at", 0) or 0)
            _held = (_now - _started) if _started > 0 else None
            _stuck_after = 3.0 * _iv
            if _held is None or _held <= _stuck_after:
                # ★367 后续：跳过分支过去**完全静默** ⇒ 外部只能看到"心跳不动了"，
                #   看不到"看门狗在跑但一直跳过"。这是本次 17 小时断链无人察觉的直接原因之一。
                #   现在按 60s 节流留痕：只在"已经开始慢"（held > 1×interval）时打，
                #   正常快拍（< 1×interval）仍然不刷屏。
                if _held is not None and _held > _iv:
                    _last_warn = float(getattr(self, "_m367_skip_warn_at", 0) or 0)
                    if _now - _last_warn >= 60.0:
                        self._m367_skip_warn_at = _now
                        self._m367_skip_warn = int(getattr(self, "_m367_skip_warn", 0)) + 1
                        self._log(LogLevel.WARNING,
                                  f"[367链] 看门狗持续跳过：心跳守卫已持有 {_held:.1f}s"
                                  f"（阈值 {_stuck_after:.1f}s=3×{_iv:.1f}s，第"
                                  f"{self._m367_skip_warn}次告警）gap={_gap:.1f}s",
                                  error_code="heartbeat_guard_held")
                return {"ok": True, "action": "skip_in_progress",
                        "gap_s": round(_gap, 2),
                        "held_s": (round(_held, 2) if _held is not None else None)}
            self._m367_stuck_forced = int(getattr(self, "_m367_stuck_forced", 0)) + 1
            self._log(LogLevel.ERROR,
                      f"[367链] 心跳重入守卫疑似卡死：已持续 {_held:.1f}s > "
                      f"{_stuck_after:.1f}s(3×{_iv:.1f}s)，第"
                      f"{self._m367_stuck_forced}次强制复位并续链 "
                      f"(beat={self._beat_count})",
                      error_code="heartbeat_guard_stuck")
            self._heartbeat_in_progress = False
            self._heartbeat_started_at = 0.0
        self._m365_wd_gap_warn = int(getattr(self, "_m365_wd_gap_warn", 0)) + 1
        _beat_before = int(getattr(self, "_beat_count", 0))
        _action = "reschedule_failed"
        _created = False
        try:
            _created = bool(self._schedule_next_beat())
        except Exception as _e:
            self._m365_chain_fail = int(getattr(self, "_m365_chain_fail", 0)) + 1
            self._log(LogLevel.ERROR,
                      f"[365链] 心跳间隔异常且续链失败 gap={_gap:.1f}s > 2×{_iv}s："
                      f"{type(_e).__name__}: {_e}",
                      error_code="heartbeat_chain_break")
            return {"ok": False, "action": _action, "gap_s": round(_gap, 2),
                    "beat": _beat_before, "created": False}

        if _created:
            # 动作发出 ≠ 成功。只记 pending，结果交给下一次调用的延迟确认。
            self._m396_rescue_pending = {
                "beat_before": _beat_before,
                "at": _now,
                "gap_before": round(_gap, 2),
                "deadline": _now + _M396_RESCUE_CONFIRM_FACTOR * _iv,
            }
            _action = "rescue_pending"
            self._log(LogLevel.WARNING,
                      f"[365链] 心跳间隔异常 gap={_gap:.1f}s > 2×{_iv}s（第"
                      f"{self._m365_wd_gap_warn}次）⇒ 已新建定时器（beat={_beat_before}，"
                      f"**结果未确认**：{_M396_RESCUE_CONFIRM_FACTOR * _iv:.0f}s 后按 beat 是否前进判定）",
                      error_code="heartbeat_rescue_pending")
        else:
            # ★396 核心：在途 Timer 还活着 ⇒ 链没断，绝不重建。
            #   旧实现在这里 cancel + 新建，只要看门狗比心跳快，就会把链永久掐死。
            _action = "no_rescue_needed"
            self._log(LogLevel.WARNING,
                      f"[365链] 心跳间隔异常 gap={_gap:.1f}s > 2×{_iv}s（第"
                      f"{self._m365_wd_gap_warn}次）⇒ **拒绝重建**：在途定时器仍存活，"
                      f"链没断（396：重建会把它掐死）。beat 不前进的原因在早退"
                      f"（is_running/info_field）或心跳体卡死，不在链；beat={_beat_before}",
                      error_code="heartbeat_chain_alive_no_rescue")
        return {"ok": False, "action": _action, "gap_s": round(_gap, 2),
                "beat": _beat_before, "created": _created}

    def m365_chain_stats(self) -> dict:
        """★365刀C1：心跳链健康读数（只读；供心跳/健康检查与 live 窗复核）。

        ★396刀1：新增四个读数，把「调度被跳过／救援已确认／救援失败」分开记，
        不再用单一 `chain_fail` 混口径（铁律 282：表头 `A or B` 混口径 ⇒ 拆键）。
        """
        _last = float(getattr(self, "_last_beat_time", 0) or 0)
        _iv = float(getattr(self, "current_interval", 0) or 0)
        return {"chain_fail": int(getattr(self, "_m365_chain_fail", 0)),
                "wd_running": bool(getattr(self, "_m365_wd_running", False)),
                "wd_fail": int(getattr(self, "_m365_wd_fail", 0)),
                "gap_warn": int(getattr(self, "_m365_wd_gap_warn", 0)),
                "beat_count": int(getattr(self, "_beat_count", 0)),
                "interval": _iv,
                "gap_s": (round(time.time() - _last, 2) if _last > 0 else None),
                # ★396刀1：调度面读数
                "sched_created": int(getattr(self, "_m396_sched_created", 0)),
                "sched_skipped": int(getattr(self, "_m396_sched_skipped", 0)),
                "rescue_pending": bool(getattr(self, "_m396_rescue_pending", None)),
                "rescue_ok": int(getattr(self, "_m396_rescue_ok", 0)),
                "rescue_failed": int(getattr(self, "_m396_rescue_failed", 0))}

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

    # ========== 未来演化预留（v10.0 振荡场） ==========

    # ★398刀4：预埋未接 —— 全仓 0 调用点（398 实测 64 def / 0 引用）。
    #   场共振阶段（PHASE19+）再接线；在那之前不要当它是已生效的场感知能力。
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
        # ★365刀C1：起搏路径（未走 boot 事件时）同样挂看门狗
        self._m365_start_watchdog()
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
