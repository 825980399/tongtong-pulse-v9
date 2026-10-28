# -*- coding: utf-8 -*-
"""
TimeCore.py —— 时间核心

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 时间感知与时间推理核心模块
机制: 基于TimeCore类实现，包含10个核心方法
定位: 时间认知层
"""

import random
import threading
import time
from typing import Any

import config
from nucleus._silent_except import silent_exc
from nucleus.chronos.GlobalClock import GlobalClock
from nucleus.const import SystemEvent

# ★374刀1：模块级锁（理由同 PulseHeart：单测用 __new__ 造实例，实例锁会炸）
_TIMER_LOCK = threading.Lock()
_M374_TICK_LOCK = threading.Lock()


#: ★380刀1（T-380-tick死信）：已因"零消费者"被跳过的托管任务（每个只记一次，避免每分钟刷屏）。
_M380_GATED_TICKS: set = set()


def _m380_gate_on() -> bool:
    """灰度开关 `ENABLE_M380_TICK_CONSUMER_GATE`（默认开）。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M380_TICK_CONSUMER_GATE", True))
    except Exception as _e:
        silent_exc(_e, where="TimeCore::_m380_gate_on")
        return True


def _m380_tick_consumer_ok(field, event_type: str) -> bool:
    """★380刀1（T-380-tick死信）：托管 tick 只发给**真有消费者**的事件名。

    为什么要在**这一层**挡：三个 tick 的名字写在 `config.TIMECORE_SCHEDULE_CONFIG` 里，
    源码里 grep 不到字面量（379 批"找不到发布点"的真因），所以改不到"发布点"本身；
    调度器是它们唯一的共同出口，闸门放这里才是一处管三个。
    """
    if not _m380_gate_on():
        return True
    try:
        _fn = getattr(field, "has_real_consumer", None)
        if _fn is None:
            return True
        return bool(_fn(event_type))
    except Exception as _e:
        silent_exc(_e, where="TimeCore::_m380_tick_consumer_ok")
        return True


def _m380_note_gated(task_id: str, event_type: str) -> None:
    """每个任务只记一次 —— 这类 tick 每分钟都可能到期。"""
    try:
        _k = str(task_id or "")
        if _k in _M380_GATED_TICKS:
            return
        _M380_GATED_TICKS.add(_k)
        import logging as _lg
        _lg.getLogger(__name__).info(
            "[380刀1] 托管任务 %s 的事件 %s 无真实消费者 ⇒ 停止投递（每任务只记一次）",
            _k, event_type)
    except Exception as _e:
        silent_exc(_e, where="TimeCore::_m380_note_gated", level="debug")


class TimeCore:
    """时间中枢内核单例。"""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._enabled = bool(getattr(config, "ENABLE_TIME_CORE", False))
        self._interval = float(getattr(config, "TIME_CORE_INTERVAL_SECONDS", 60))
        self._clock = GlobalClock()  # 组合复用全局时钟能力（基于系统时钟，多实例一致）
        self._tick_count = 0
        self._timer = None
        self._running = False
        self._thread_lock = threading.Lock()
        # ★阶段三子任务2：主动调度接管
        self._active_schedule_enabled = bool(getattr(config, "ENABLE_TIMECORE_ACTIVE_SCHEDULE", False))
        self._managed_tasks: dict[str, dict[str, Any]] = {}
        self._managed_tasks_lock = threading.Lock()
        self._last_tick_time = 0.0
        # ★374刀1：租约守卫字段。
        #   PulseHeart 侧本就有「先 cancel 再建」的串行化，TimeCore 侧一直没有；
        #   本刀把 `_tick` 改成「进入即预排 + finally 再排」后，不 cancel 就会每拍
        #   翻倍（1→2→4…），比断链更快致命，所以两件事必须一起做（锁见模块级常量）。
        self._m374_tick_in_progress = False
        self._m374_tick_started = 0.0
        self._m374_tick_takeover = 0
        # ★374刀1：令牌用单调序号（理由同 PulseHeart：时间戳粒度会撞车）
        self._m374_tick_seq = 0
        self._logger = None
        try:
            from nucleus.logger import get_organ_logger
            self._logger = get_organ_logger("时间中枢")
        except Exception:
            self._logger = None
        # 若开启主动调度，初始化托管任务（last_run 错峰，对齐 PulseHeart._jitter）
        if self._active_schedule_enabled:
            self._init_managed_tasks()

    # ---------- 状态查询 ----------
    def is_enabled(self) -> bool:
        return self._enabled

    def get_current_time(self) -> dict[str, Any]:
        """返回当前统一时间基线快照（供器官主动拉取）。"""
        return self._build_payload()

    # ---------- 生命周期 ----------
    def start(self) -> None:
        """启动时间广播线程（受开关保护）。"""
        if not self._enabled:
            self._log("TimeCore 未启用（ENABLE_TIME_CORE=False），不启动广播线程")
            return
        with self._thread_lock:
            if self._running:
                return
            self._running = True
        self._schedule_next()
        self._log(f"TimeCore 已启动，广播间隔 {self._interval}s")

    def stop(self) -> None:
        with self._thread_lock:
            self._running = False
            if self._timer is not None:
                try:
                    self._timer.cancel()
                except Exception as e:
                    silent_exc(e, where="nucleus.chronos.TimeCore::stop L91")
                self._timer = None
        self._log("TimeCore 已停止")

    def _schedule_next(self) -> None:
        try:
            with _TIMER_LOCK:
                # ★374刀1：**先取消旧的再建新的**（对齐 PulseHeart._schedule_next_beat）。
                #   对已触发的 Timer 调 cancel() 是空操作，不影响正在执行的这一拍；
                #   但能保证「预排」与「finally 重排」不会留下两个活定时器。
                _old = self._timer
                if _old is not None:
                    try:
                        _old.cancel()
                    except Exception as _ce:
                        silent_exc(_ce, where="nucleus.chronos.TimeCore::_schedule_next cancel")
                self._timer = threading.Timer(self._interval, self._tick)
                self._timer.daemon = True
                self._timer.start()
        except Exception as e:
            # ★365刀C1：续链失败必须**可数、可见** —— 原先只有一行 INFO（等价于静默），
            #   链断了在日志里翻不出来。现计数＋WARNING（与 PulseHeart 同口径）。
            self._m365_chain_fail = int(getattr(self, "_m365_chain_fail", 0)) + 1
            _msg = (f"[365链] TimeCore 调度失败（第{self._m365_chain_fail}次，"
                    f"interval={self._interval}s）：{type(e).__name__}: {e}")
            if self._logger is not None:
                try:
                    self._logger.warning(_msg)
                except Exception as _le:
                    silent_exc(_le, where="nucleus.chronos.TimeCore::_schedule_next warn")
                    print(_msg)
            else:
                print(_msg)

    def m365_chain_stats(self) -> dict:
        """★365刀C1：TimeCore 续链失败计数（只读；与 PulseHeart.m365_chain_stats 同名同口径）。"""
        return {"chain_fail": int(getattr(self, "_m365_chain_fail", 0)),
                "running": bool(getattr(self, "_running", False)),
                "interval": float(getattr(self, "_interval", 0) or 0)}

    def _tick(self) -> None:
        """定时器回调：广播 time.tick，并按需驱动被托管任务。

        ★374刀1：与 PulseHeart 同型收口 —— **进入即预排下一拍**，续链不再依赖
        「本拍能返回」。事故（10-26 17:28:06）正是这一拍卡在 `_broadcast()` 里
        没回来，而续链只写在 finally ⇒ 之后 `[time.tick]` 再没出现过。
        守卫的早退放在 try 之前（同 PulseHeart）：跳过就只是跳过，不该顺带
        再排一次让读数看起来像"跑了一拍"。
        """
        if not self._running:
            return
        self._schedule_next()
        now = time.time()
        # 租约守卫：租约内 ⇒ 正常慢拍，跳过；超租约 ⇒ 上一拍已判卡死，由本拍接管续跳。
        _m374_lease = 3.0 * float(getattr(self, "_interval", 60.0) or 60.0)
        _m374_held = now - float(getattr(self, "_m374_tick_started", 0) or 0)
        if getattr(self, "_m374_tick_in_progress", False):
            if _m374_held <= _m374_lease:
                return
            self._m374_tick_takeover = int(getattr(self, "_m374_tick_takeover", 0)) + 1
            self._log(f"[374刀1] TimeCore 上一拍卡死 {_m374_held:.1f}s > "
                      f"{_m374_lease:.1f}s ⇒ 第{self._m374_tick_takeover}次接管续跳")
        with _M374_TICK_LOCK:
            self._m374_tick_seq = int(getattr(self, "_m374_tick_seq", 0)) + 1
            _m374_tok = self._m374_tick_seq
            self._m374_tick_in_progress = True
            self._m374_tick_started = now
        try:
            # ★watchdog（可选增强）：检测上一拍是否异常延迟（超过 2 个间隔）。
            # 仅告警、不自动回退 Heart（回退逻辑需 Heart 重新注册，风险较高，避免引入新故障）。
            if self._last_tick_time > 0:
                gap = now - self._last_tick_time
                if gap > 2 * self._interval:
                    self._log(
                        f"⚠️ watchdog: 时间中枢跳变间隔异常 {gap:.1f}s > 2x{self._interval}s，"
                        f"托管调度可能停摆，建议关闭 ENABLE_TIMECORE_ACTIVE_SCHEDULE 回退 Heart"
                    )
            self._last_tick_time = now
            # 顺序（内部协作者确认）：先广播 time.tick，再触发被托管任务
            self._broadcast()
            if self._active_schedule_enabled:
                self._dispatch_managed_tasks()
        except Exception as e:
            self._log(f"tick 异常: {e}")
        finally:
            # ★374刀1：认令牌释放 —— 卡死旧拍的迟到返回不得替接管拍开门，
            #   否则第三拍会叠进来（造出原本不存在的并发）。
            with _M374_TICK_LOCK:
                if self._m374_tick_seq == _m374_tok:
                    self._m374_tick_in_progress = False
            if self._running:
                self._schedule_next()

    # ---------- 主动调度接管（子任务2） ----------
    def _init_managed_tasks(self) -> None:
        """从 config.TIMECORE_SCHEDULE_CONFIG 初始化托管任务（last_run 错峰，对齐 PulseHeart._jitter）。"""
        cfg = getattr(config, "TIMECORE_SCHEDULE_CONFIG", {}) or {}
        with self._managed_tasks_lock:
            for tid, spec in cfg.items():
                interval = float(spec.get("interval", 3600))
                self._managed_tasks[tid] = {
                    "interval": interval,
                    # 对齐 Heart：last_run = now - (interval - uniform(0.5, interval))，避免启动尖峰
                    "last_run": time.time() - (interval - random.uniform(0.5, interval)),
                    "event_type": spec.get("event_type", tid),
                }
        self._log(f"主动调度已初始化 {len(self._managed_tasks)} 个托管任务: {list(self._managed_tasks.keys())}")

    def _dispatch_managed_tasks(self) -> None:
        """检查并触发到期的托管任务（L3 后台自主层）。

        发射与 PulseHeart._emit 完全一致的 pulse：event_type 一致、payload={task_id,last_run,scheduled_at}、
        priority=3、layer="L3"、source_organ="TimeCore"。订阅器官按 event_type 路由，对其完全无感。
        """
        if not self._managed_tasks:
            return
        now = time.time()
        due = []
        with self._managed_tasks_lock:
            for tid, info in self._managed_tasks.items():
                last = info.get("last_run", 0)
                interval = info.get("interval", 3600)
                if now - last >= interval:
                    due.append(tid)
                    info["last_run"] = now  # 锁内更新，避免重复触发
        # 锁外发射脉冲，避免锁内调用 publish 导致潜在死锁（对齐 Heart._check_scheduled_tasks）
        for tid in due:
            info = self._managed_tasks[tid]
            try:
                from nucleus.field.InfoField import get_info_field
                field = get_info_field()
                if field is None:
                    self._log(f"InfoField 尚未就绪，跳过托管任务 {tid}")
                    continue
                if not _m380_tick_consumer_ok(field, info["event_type"]):
                    _m380_note_gated(tid, info["event_type"])
                    continue
                field.publish({
                    "event_type": info["event_type"],
                    "source_organ": "TimeCore",
                    "payload": {
                        "task_id": tid,
                        "last_run": info.get("last_run", 0),
                        "scheduled_at": now,
                    },
                    "priority": 3,
                    "layer": "L3",
                })
            except Exception as e:
                self._log(f"托管任务 {tid} 触发异常: {e}")

    # ---------- 广播 ----------
    def _broadcast(self) -> None:
        payload = self._build_payload()
        try:
            from nucleus.field.InfoField import get_info_field
            field = get_info_field()
            if field is None:
                self._log("InfoField 尚未就绪，跳过本次广播")
                return
            field.publish({
                "event_type": SystemEvent.TIME_TICK,
                "source_organ": "TimeCore",
                "payload": payload,
                "priority": 1,
                "layer": "L2",
            })
        except Exception as e:
            self._log(f"广播异常: {e}")

    def _build_payload(self) -> dict[str, Any]:
        self._tick_count += 1
        try:
            wall = self._clock.now_readable()
        except Exception:
            wall = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            uptime = self._clock.get_uptime_seconds()
            uptime_display = self._clock.get_uptime_display()
        except Exception:
            uptime = time.time() - getattr(self, "_start_monotonic", time.time())
            uptime_display = f"{uptime:.1f}s"
        try:
            epoch_ns = self._clock.get_epoch_ns()
        except Exception:
            epoch_ns = time.time_ns()
        return {
            "tick_count": self._tick_count,
            "wall_clock": wall,
            "epoch_ns": epoch_ns,
            "uptime_seconds": uptime,
            "uptime_display": uptime_display,
            "semantic_time": self._semantic_phase(),
            "logical_time": self._tick_count,  # 逻辑时间：自启动起的 tick 序号
        }

    @staticmethod
    def _semantic_phase() -> str:
        """语义时间：将墙钟映射到一天中的语义阶段（粗粒度）。"""
        try:
            h = time.localtime().tm_hour
        except Exception as e:
            silent_exc(e, where="nucleus.chronos.TimeCore::_semantic_phase L234")
            return "unknown"
        if 5 <= h < 11:
            return "morning"
        if 11 <= h < 14:
            return "noon"
        if 14 <= h < 18:
            return "afternoon"
        if 18 <= h < 23:
            return "evening"
        return "night"

    # ---------- 日志 ----------
    def _log(self, msg: str) -> None:
        if self._logger is not None:
            try:
                self._logger.info(msg)
                return
            except Exception as e:
                silent_exc(e, where="nucleus.chronos.TimeCore::_log L252")
        print(f"[TimeCore] {msg}")


def get_time_core() -> "TimeCore":
    """获取 TimeCore 内核单例。"""
    return TimeCore()
