# -*- coding: utf-8 -*-
"""
PulseHands —— 双手器官 · 本地动作的短平快执行器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 MotorEvent.EXECUTE 与 HandsEvent.EXECUTE，执行本地动作类任务，执行前做算力准入判断，完成后广播 HandsEvent.RESULT。
机制: on_pulse → _on_execute，先由 _is_gpu_intensive 判断负载类型，再用 _check_gpu_available / _check_cpu_load 做资源准入，资源不足时降级或延后而非硬执行；_on_status_request 响应 SystemEvent.STATUS_REQUEST 上报状态；get_resonance_conditions / on_field_oscillation 接入共振场；refresh_runtime_params 支持参数热加载。
定位: 运动层的「精细执行器」，负责短时延的本地动作，与双腿的长时抓取形成分工。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import HandsEvent, LogLevel, MotorEvent, SystemEvent, TouchEvent


class PulseHands(BasePulseOrgan):
    """脉冲驱动双手（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    def __init__(self, organ_name: str = "双手"):
        super().__init__(organ_name)

        self._device_preferences: dict[str, int] = {"gpu": 0, "cpu": 0}
        self._task_count = 0

        self._gpu_keywords = [
            "gpu", "cuda", "训练", "模型", "推理", "深度学习",
            "矩阵", "向量", "张量", "tensor", "pytorch", "神经网络",
        ]

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == HandsEvent.EXECUTE:
            return self._on_execute(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _on_execute(self, payload: dict) -> dict[str, Any]:
        code = payload.get("code", "")
        user_name = payload.get("user_name", "用户")
        task_id = payload.get("task_id", f"task_{int(time.time())}")

        if not code:
            return {"status": "skipped", "reason": "空代码"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._task_count += 1

        is_gpu_task = self._is_gpu_intensive(code)
        gpu_available = self._check_gpu_available()
        cpu_load = self._check_cpu_load()

        if is_gpu_task and gpu_available:
            device = "gpu"
            reason = "GPU密集型任务"
        elif is_gpu_task and not gpu_available:
            device = "cpu"
            reason = "GPU不可用，降级为CPU"
        elif cpu_load > 0.8:
            device = "gpu" if gpu_available else "cpu"
            reason = "CPU负载过高"
        else:
            device = "cpu"
            reason = "CPU通用任务"

        self._device_preferences[device] += 1

        # v9.5: 代码执行请求标记为L1实时交互层
        self._emit(MotorEvent.EXECUTE, {
            "code": code,
            "device": device,
            "task_id": task_id,
            "user_name": user_name,
        }, priority=6, layer="L1")

        # v9.5: 调度结果也标记为L1实时交互层
        self._emit(HandsEvent.RESULT, {
            "task_id": task_id,
            "device": device,
            "reason": reason,
            "gpu_available": gpu_available,
            "cpu_load": cpu_load,
        }, priority=6, layer="L1")

        self._log(LogLevel.DEBUG, f"任务分配: {task_id} → {device} ({reason})")

        return {
            "status": "scheduled",
            "device": device,
            "reason": reason,
        }

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "task_count": self._task_count,
            "device_preferences": self._device_preferences,
            "is_running": self.is_running,
        }

    # ========== 硬件感知 ==========

    def _is_gpu_intensive(self, code: str) -> bool:
        code_lower = code.lower()
        return any(kw in code_lower for kw in self._gpu_keywords)

    def _check_gpu_available(self) -> bool:
        if self.info_field is None:
            return False

        snapshot = self.info_field.get_current(TouchEvent.HARDWARE_SNAPSHOT)
        if snapshot and isinstance(snapshot, dict):
            payload = snapshot.get("payload", {})
            gpu_info = payload.get("gpu", {})
            return gpu_info.get("available", False)

        return False

    def _check_cpu_load(self) -> float:
        if self.info_field is None:
            return 0.0

        snapshot = self.info_field.get_current(TouchEvent.HARDWARE_SNAPSHOT)
        if snapshot and isinstance(snapshot, dict):
            payload = snapshot.get("payload", {})
            cpu_info = payload.get("cpu", {})
            return cpu_info.get("usage_percent", 0.0) / 100.0

        return 0.0

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    HandsEvent.EXECUTE,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]

    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "双手",
    "class_name": "PulseHands",
    "attr_name": "hands",
    "system": "motor",
    "always_online": False,
    "feature_flag": "enable_motor",
    "extra_deps": {},
    "post_wiring": [],
}
