# -*- coding: utf-8 -*-
"""
PulseHealthMonitor —— 健康监控器官 · 能量与存活状态的体检医生

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 承接 HealthEvent.CHECK、EnergyEvent.METABOLISM_SNAPSHOT 与 VascularEvent.SILENT_ORGAN，综合能量水位、硬件快照与器官存活状态评估健康度，输出报告并在异常时告警。
机制: on_pulse 分派 _on_check，汇总能量快照与血管静默告警后按阈值判定，越界则发 SystemEvent.ALARM 交由紧急处理器官处置，并周期性回 HealthEvent.REPORT；_on_status_request 上报状态。
定位: 躯体的「体检医生」，只负责监测与告警，恢复动作交给 PulseEmergencyHandler。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    EnergyEvent,
    HealthEvent,
    SystemEvent,
    TouchEvent,
    VascularEvent,
)


class PulseHealthMonitor(BasePulseOrgan):
    """脉冲驱动健康监控（事件驱动版 · v9.5 分层脉冲版）"""

    def __init__(self, organ_name: str = "健康监控"):
        super().__init__(organ_name)
        self._latest_hw = {}          # 最新的硬件快照
        self._latest_energy = {}      # 最新的能量数据
        self._check_count = 0
        self._alarm_count = 0
        self._thresholds = {
            "energy_low": 0.3, "energy_critical": 0.1,
            "cpu_high": 90, "memory_high": 90,
        }

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == TouchEvent.HARDWARE_SNAPSHOT:
            self._latest_hw = payload
            return None

        elif event_type == EnergyEvent.METABOLISM_SNAPSHOT:
            self._latest_energy = payload
            return None
        elif event_type == VascularEvent.SILENT_ORGAN:
            self._alarm_count += 1
            silent_organs = payload.get("silent_organs", [])
            # v9.5: 沉默器官告警标记为L0生命线层
            self._emit(SystemEvent.ALARM, {
                "type": "organ_silent",
                "level": "L3",
                "organs": silent_organs,
                "message": f"沉默器官告警: {', '.join(silent_organs)}"
            }, priority=9, layer="L0")
            return None

        elif event_type == HealthEvent.CHECK:
            return self._on_check(payload)

        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    def _on_check(self, payload: dict) -> dict[str, Any]:
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._check_count += 1
        alarms = []

        # 检查能量水平
        el = self._latest_energy.get("energy_level", 1.0)
        if el < self._thresholds["energy_critical"]:
            alarms.append({"level": "L3", "type": "energy_critical", "value": el, "message": f"能量危急: {el:.2f}"})
        elif el < self._thresholds["energy_low"]:
            alarms.append({"level": "L2", "type": "energy_low", "value": el, "message": f"能量偏低: {el:.2f}"})

        # 检查硬件负载
        cpu = self._latest_hw.get("cpu", {}).get("usage_percent", 0)
        mem = self._latest_hw.get("memory", {}).get("usage_percent", 0)
        if cpu > self._thresholds["cpu_high"]:
            alarms.append({"level": "L2", "type": "cpu_high", "value": cpu, "message": f"CPU使用率过高: {cpu}%"})
        if mem > self._thresholds["memory_high"]:
            alarms.append({"level": "L2", "type": "memory_high", "value": mem, "message": f"内存使用率过高: {mem}%"})

        for a in alarms:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._alarm_count += 1
            # v9.5: 健康告警标记为L0生命线层
            self._emit(SystemEvent.ALARM, {
                "type": a["type"], "level": a["level"], "value": a["value"], "message": a["message"]
            }, priority=8, layer="L0")

        status_text = "critical" if any(a["level"] in ("L3","L4") for a in alarms) else ("warning" if alarms else "healthy")
        # v9.5: 健康报告标记为L3后台自主层
        self._emit(HealthEvent.REPORT, {
            "status": status_text, "alarms": len(alarms), "check_id": self._check_count
        }, priority=4, layer="L3")

        return {"status": status_text, "alarms": len(alarms), "check_id": self._check_count}

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name, "check_count": self._check_count,
            "alarm_count": self._alarm_count, "thresholds": self._thresholds,
            "has_hw_data": bool(self._latest_hw), "has_energy_data": bool(self._latest_energy),
            "is_running": self.is_running,
        }

    def get_resonance_conditions(self) -> list:
        return [
            {"organ_name": self.organ_name,
             "event_types": [HealthEvent.CHECK, TouchEvent.HARDWARE_SNAPSHOT, EnergyEvent.METABOLISM_SNAPSHOT, SystemEvent.STATUS_REQUEST],
             "min_priority": 1}
        ]

    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None



# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "健康监控",
    "class_name": "PulseHealthMonitor",
    "attr_name": "health_monitor",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseHealthMonitor v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self): self.published = []
        def publish(self, p): self.published.append(p)
    m = MockInfoField()
    h = PulseHealthMonitor("健康监控"); h.set_info_field(m); h.start()

    # 模拟接收硬件快照和能量数据
    h.on_pulse({"event_type": TouchEvent.HARDWARE_SNAPSHOT, "payload": {"cpu": {"usage_percent": 95}, "memory": {"usage_percent": 92}}, "priority": 4})
    h.on_pulse({"event_type": EnergyEvent.METABOLISM_SNAPSHOT, "payload": {"energy_level": 0.05}, "priority": 4})
    r = h.on_pulse({"event_type": HealthEvent.CHECK, "payload": {}, "priority": 4})
    print(f"1. 异常检查: 状态={r['status']}, 告警={r['alarms']}")
    # 验证告警脉冲的 layer 标记
    alarm_pulses = [p for p in m.published if p.get("event_type") == SystemEvent.ALARM]
    if alarm_pulses:
        print(f"   ALARM脉冲 layer: {alarm_pulses[-1].get('layer', '未设置')} (预期L0)")
    # 验证健康报告脉冲的 layer 标记
    report_pulses = [p for p in m.published if p.get("event_type") == HealthEvent.REPORT]
    if report_pulses:
        print(f"   REPORT脉冲 layer: {report_pulses[-1].get('layer', '未设置')} (预期L3)")
    s = h.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"2. 统计: 检查{s['check_count']}次, 告警{s['alarm_count']}次")
    h.stop(); print("\n=== 自测全部通过 ===")
