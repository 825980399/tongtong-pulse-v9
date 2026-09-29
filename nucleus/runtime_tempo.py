# -*- coding: utf-8 -*-
"""
runtime_tempo.py —— 运行时节律

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架运行节奏控制与自适应调速
机制: 基于RuntimeTempo类实现，包含10个核心方法
定位: 运行时控制层
"""

from __future__ import annotations

import threading
import time

from nucleus.const import LogLevel
from nucleus.logger import get_module_logger


from typing import Any, Self

from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底


_logger = get_module_logger("runtime_tempo")


class RuntimeTempo(SilentLogMixin):
    """全局运行节奏调节器（单例）"""

    # 状态阈值
    CONVERSING_WINDOW = 30.0       # 对话活跃窗口（秒）
    COOLING_DOWN_WINDOW = 120.0    # 对话冷却窗口（秒）

    # 各状态目标 tempo（<1=加速间隔缩短，>1=减速间隔拉长）
    TEMPO_CONVERSING = 1.8
    TEMPO_COOLING_DOWN = 1.3
    TEMPO_IDLE_LOW = 0.5
    TEMPO_IDLE_MEDIUM = 0.75
    TEMPO_IDLE_HIGH = 1.0

    # 平滑过渡：每次调整最大变化量（防突变）
    MAX_STEP_PER_CALL = 0.1

    _instance: RuntimeTempo | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> Self:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._lock = threading.RLock()
        self._last_conversation_time: float = 0.0
        self._current_tempo: float = 1.0
        self._last_state: str = "idle_low"
        self._last_hardware_pressure: str = "low"
        self._last_adjust_time: float = 0.0

    # ------------------------------------------------------------------
    # 对话状态通知
    # ------------------------------------------------------------------
    def notify_conversation(self) -> None:
        """收到用户对话时调用，更新最后对话时间。"""
        with self._lock:
            self._last_conversation_time = time.time()

    def get_last_conversation_time(self) -> float:
        return self._last_conversation_time

    # ------------------------------------------------------------------
    # 硬件压力获取（复用 parallel_scheduler，失败时兜底 low）
    # ------------------------------------------------------------------
    def _get_hardware_pressure(self) -> str:
        try:
            from nucleus.parallel_scheduler import get_parallel_scheduler
            _sched = get_parallel_scheduler()
            if _sched is not None:
                return _sched.get_hardware_pressure()
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return "low"

    # ------------------------------------------------------------------
    # 状态计算
    # ------------------------------------------------------------------
    def _compute_target_tempo(self) -> tuple[float, str]:
        """计算目标 tempo 和状态名。"""
        now = time.time()
        elapsed = now - self._last_conversation_time

        if elapsed < self.CONVERSING_WINDOW:
            return self.TEMPO_CONVERSING, "conversing"
        if elapsed < self.COOLING_DOWN_WINDOW:
            return self.TEMPO_COOLING_DOWN, "cooling_down"

        # idle 状态：根据硬件压力
        pressure = self._get_hardware_pressure()
        self._last_hardware_pressure = pressure
        if pressure == "high":
            return self.TEMPO_IDLE_HIGH, "idle_high"
        if pressure == "medium":
            return self.TEMPO_IDLE_MEDIUM, "idle_medium"
        return self.TEMPO_IDLE_LOW, "idle_low"

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def get_background_tempo(self) -> float:
        """获取后台任务强度系数（平滑过渡后）。

        返回值：
          <1.0 = 加速（间隔缩短，后台学习更频繁）
          =1.0 = 正常
          >1.0 = 减速（间隔拉长，后台学习减少）
        """
        with self._lock:
            target, state = self._compute_target_tempo()
            # 平滑过渡：每次最多变化 MAX_STEP_PER_CALL
            diff = target - self._current_tempo
            if abs(diff) > self.MAX_STEP_PER_CALL:
                self._current_tempo += self.MAX_STEP_PER_CALL * (1 if diff > 0 else -1)
            else:
                self._current_tempo = target
            # ★2026-09-03日志巡检修复：状态变化时打印日志，便于观察runtime_tempo运行
            if state != self._last_state:
                _logger.info(f"状态变化: {self._last_state} → {state}, tempo={self._current_tempo:.2f}→{target:.2f}, 压力={self._last_hardware_pressure}")
            self._last_state = state
            self._last_adjust_time = time.time()
            return round(self._current_tempo, 3)

    def get_state(self) -> str:
        """获取当前状态名（用于日志/调试）。"""
        return self._last_state

    def get_status(self) -> dict[str, Any]:
        """获取完整状态快照（用于日志/监控）。"""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_conversation_time if self._last_conversation_time > 0 else -1
            return {
                "state": self._last_state,
                "tempo": round(self._current_tempo, 3),
                "last_conversation_seconds_ago": round(elapsed, 1) if elapsed >= 0 else None,
                "hardware_pressure": self._last_hardware_pressure,
            }


# ----------------------------------------------------------------------
# 模块级单例访问
# ----------------------------------------------------------------------
def get_runtime_tempo() -> RuntimeTempo:
    return RuntimeTempo()
