# -*- coding: utf-8 -*-
"""
PulseStressAxis —— 应激轴器官 · 压力水平的中枢调节

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 承接 StressAxisEvent.ACTIVATE / RECOVER，维护内部应激水位并在等级变化时广播 StressAxisEvent.LEVEL_CHANGED，供动机循环等器官消费。
机制: on_pulse 分派 _on_activate / _on_recover，调整应激水位后广播等级变化；get_stress_level 对外提供只读查询；_on_status_request 上报状态。
定位: 躯体的「压力轴」（类比 HPA 轴），把外部刺激强度转化为统一的应激信号。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import StressAxisEvent, SystemEvent


class PulseStressAxis(BasePulseOrgan):
    """脉冲驱动应激轴（v9.5 分层脉冲版）"""

    def __init__(self, organ_name: str = "应激轴"):
        super().__init__(organ_name)
        self._stress_level = 0.0
        self._activation_count = 0

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        if event_type == StressAxisEvent.ACTIVATE:
            return self._on_activate(pulse.get("payload", {}))
        elif event_type == StressAxisEvent.RECOVER:
            return self._on_recover()
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    def _on_activate(self, payload: dict) -> dict[str, Any]:
        self._stress_level = min(1.0, self._stress_level + 0.3)
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._activation_count += 1
        # v9.5: 应激激活标记为L0生命线层（紧急响应）
        self._emit(StressAxisEvent.LEVEL_CHANGED, {
            "stress_level": self._stress_level
        }, priority=7, layer="L0")
        return {"status": "activated", "stress_level": self._stress_level}

    def _on_recover(self) -> dict[str, Any]:
        self._stress_level = max(0.0, self._stress_level - 0.1)
        # v9.5: 应激恢复标记为L3后台自主层（逐步恢复）
        self._emit(StressAxisEvent.LEVEL_CHANGED, {
            "stress_level": self._stress_level
        }, priority=5, layer="L3")
        return {"status": "recovering", "stress_level": self._stress_level}

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {"organ": self.organ_name, "stress_level": round(self._stress_level, 2), "activation_count": self._activation_count, "is_running": self.is_running}

    def get_stress_level(self) -> float:
        """★压力闭环：暴露当前应激水平（0.0-1.0），供内在世界调节推理策略。"""
        return round(self._stress_level, 4)

    def get_resonance_conditions(self) -> list:
        return [{"organ_name": self.organ_name, "event_types": [StressAxisEvent.ACTIVATE, StressAxisEvent.RECOVER, SystemEvent.STATUS_REQUEST], "min_priority": 1}]

    # ========== 未来演化预留（v10.0 振荡场） ==========
    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None



# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "应激轴",
    "class_name": "PulseStressAxis",
    "attr_name": "stress_axis",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseStressAxis v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self): self.published = []
        def publish(self, p): self.published.append(p)
    m = MockInfoField(); sa = PulseStressAxis("应激轴"); sa.set_info_field(m); sa.start()
    r1 = sa.on_pulse({'event_type': StressAxisEvent.ACTIVATE, 'payload': {}, 'priority': 7})
    print(f"1. 激活: 应激={r1['stress_level']}")
    # 验证激活脉冲的 layer 标记
    activate_pulses = [p for p in m.published if p.get("event_type") == StressAxisEvent.LEVEL_CHANGED]
    if activate_pulses:
        print(f"   激活 LEVEL_CHANGED脉冲 layer: {activate_pulses[-1].get('layer', '未设置')} (预期L0)")
    r2 = sa.on_pulse({'event_type': StressAxisEvent.ACTIVATE, 'payload': {}, 'priority': 7})
    print(f"2. 再次激活: 应激={r2['stress_level']}")
    m.published.clear()
    r3 = sa.on_pulse({'event_type': StressAxisEvent.RECOVER, 'payload': {}, 'priority': 5})
    print(f"3. 恢复: 应激={r3['stress_level']}")
    # 验证恢复脉冲的 layer 标记
    recover_pulses = [p for p in m.published if p.get("event_type") == StressAxisEvent.LEVEL_CHANGED]
    if recover_pulses:
        print(f"   恢复 LEVEL_CHANGED脉冲 layer: {recover_pulses[-1].get('layer', '未设置')} (预期L3)")
    s = sa.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"4. 统计: 应激={s['stress_level']}, 激活{s['activation_count']}次"); sa.stop()
    print("\n=== 自测全部通过 ===")
