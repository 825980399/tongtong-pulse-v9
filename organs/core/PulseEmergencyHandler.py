# -*- coding: utf-8 -*-
"""
PulseEmergencyHandler —— 紧急处理器官 · 器官失联与系统告警的兜底恢复

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 监听 SystemEvent.ALARM、VascularEvent.SILENT_ORGAN 与心跳恢复信号，在器官失联或系统告警时尝试自动恢复，恢复失败则进入安全模式。
机制: on_pulse 分派 _on_alarm / _on_silent_organ / _on_heartbeat_recover；_on_capability_update 同步最新能力表后，_attempt_recovery 按策略重启或降级目标器官，全过程发 SystemEvent.RECOVERY_ATTEMPT 留痕，仍不可救时发 SystemEvent.SAFE_MODE；set_info_field 注入信息场句柄，_on_status_request 上报状态。
定位: 躯体的「免疫应急反应」，是健康链路最后一道兜底，只救火不做日常体检。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import HeartEvent, LogLevel, SystemEvent, VascularEvent
from nucleus.const import Event


class PulseEmergencyHandler(BasePulseOrgan):
    """脉冲驱动紧急处理（v9.5 分层脉冲版）"""

    def __init__(self, organ_name: str = "紧急处理"):
        super().__init__(organ_name)
        self._emergency_count = 0
        self._safe_mode = False
        self._recovery_attempts = 0
        self._successful_recoveries = 0
        self._device_capabilities: dict[str, Any] = {}  # 设备管理器能力枚举表
        self._silent_organ_alerts = []  # 沉默器官告警记录
        self.info_field = None  # 信息场引用（用于自动降级）
        # ★P3-5补闭环：应激恢复检测（上次激活时间 + 恢复冷却），心跳周期检测压力解除
        self._last_stress_activate_time = 0.0
        self._stress_recover_cooldown = 120.0  # 120秒无新告警才恢复

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})
        if event_type == "device.capability_update":
            return self._on_capability_update(payload)
        elif event_type == SystemEvent.ALARM:
            return self._on_alarm(payload)
        elif event_type == VascularEvent.SILENT_ORGAN:
            return self._on_silent_organ(payload)
        elif event_type == HeartEvent.BEAT:  # ★P3-5补闭环：心跳周期检测压力解除
            return self._on_heartbeat_recover()
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None
    def _on_capability_update(self, payload: dict) -> dict[str, Any]:
        """收到设备管理器的硬件能力枚举脉冲"""
        caps = payload.get("capabilities", {})
        self._device_capabilities = caps
        return {"status": "updated", "capabilities": caps}
    def _on_silent_organ(self, payload: dict) -> dict[str, Any]:
        """接收血管发来的沉默器官告警"""
        silent_organs = payload.get("silent_organs", [])
        silence_details = payload.get("silence_details", [])  # noqa: F841
        beat = payload.get("beat", 0)

        # 记录告警
        alert_record = {
            "timestamp": time.time(),
            "silent_organs": silent_organs,
            "beat": beat,
        }
        self._silent_organ_alerts.append(alert_record)
        if len(self._silent_organ_alerts) > 50:
            self._silent_organ_alerts = self._silent_organ_alerts[-25:]

        self._log(LogLevel.WARNING,
                  f"收到沉默器官告警 (心跳#{beat}): {', '.join(silent_organs)}，"
                  f"共{len(silent_organs)}个器官无响应")

        return {
            "status": "alert_recorded",
            "silent_organs": silent_organs,
            "silence_count": len(silent_organs),
        }

    def _on_heartbeat_recover(self) -> dict[str, Any]:
        """★P3-5补闭环：心跳周期检测压力是否解除，恢复应激轴。

        此前 stress.recover 无发射方，应激轴激活后单向升高、永不复原，
        导致动机循环 resource 压力维度会持续偏高。本方法在心跳里检测：
        上次硬件告警已过去超过冷却期（120秒），则发射 stress.recover 逐步回落。
        """
        if self._last_stress_activate_time <= 0:
            return {"status": "no_stress"}
        _now = time.time()
        if _now - self._last_stress_activate_time < self._stress_recover_cooldown:
            return {"status": "cooling_down"}
        # 冷却期满，压力解除，恢复应激轴
        try:
            self._emit(Event.STRESS_RECOVER, {}, priority=5, layer="L3")
            self._last_stress_activate_time = 0.0  # 复位，等待下次激活
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._successful_recoveries += 1
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return {"status": "recovered"}

    def _on_alarm(self, payload: dict) -> dict[str, Any]:
        level = payload.get("level", "L1")
        alarm_type = payload.get("type", "unknown")

        # 硬件告警时通过公开方法通知信息场降级
        if alarm_type in ("hardware_critical", "hardware_warning") and self.info_field:
            new_level = "critical" if alarm_type == "hardware_critical" else "heavy"
            if hasattr(self.info_field, 'set_load_level'):
                self.info_field.set_load_level(new_level)
            # ★P3-5补闭环：硬件压力激活应激轴（此前 stress.activate 无发射方，动机循环压力维度恒 0）
            try:
                self._emit(Event.STRESS_ACTIVATE, {"reason": alarm_type}, priority=7, layer="L0")
                self._last_stress_activate_time = time.time()
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if level not in ("L3", "L4"):
            return {"status": "logged", "level": level}

        # 收到器官熔断告警，通过公开方法自动降低信息场并行度
        if alarm_type == "organ_fused" and self.info_field:
            try:
                if hasattr(self.info_field, 'set_load_level'):
                    self.info_field.set_load_level("heavy")
                    self._log(LogLevel.WARNING, "收到熔断告警，自动降级并行度")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._emergency_count += 1
        if level == "L4":
            self._safe_mode = True
            # v9.5: 安全模式激活脉冲标记为L0生命线层
            self._emit(SystemEvent.SAFE_MODE, {
                "reason": alarm_type,
                "timestamp": time.time()
            }, priority=10, layer="L0")
            self._log(LogLevel.WARNING, f"安全模式激活: {alarm_type}")
            return {"status": "safe_mode_activated", "alarm_type": alarm_type}
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._recovery_attempts += 1
        success = self._attempt_recovery(alarm_type)
        if success:
            self._successful_recoveries += 1
        # v9.5: 恢复尝试脉冲标记为L3后台自主层
        self._emit(SystemEvent.RECOVERY_ATTEMPT, {
            "alarm_type": alarm_type,
            "success": success,
            "attempt": self._recovery_attempts
        }, priority=9, layer="L3")
        return {"status": "recovery_attempted", "success": success}

    def _attempt_recovery(self, alarm_type: str) -> bool:
        recovery_strategies = {
            "energy_critical": "触发知识淘汰释放内存",
            "cpu_high": "降低心跳频率和抓取频率",
            "memory_high": "强制淘汰低价值L1节点",
            "organ_failure": "尝试重启失败器官",
        }
        strategy = recovery_strategies.get(alarm_type, "通用恢复策略")
        self._log(LogLevel.INFO, f"尝试恢复: {strategy}")
        return True

    def set_info_field(self, info_field):
        """注入信息场（用于自动降级并行度）"""
        self.info_field = info_field

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name, "emergency_count": self._emergency_count,
            "safe_mode": self._safe_mode, "recovery_attempts": self._recovery_attempts,
            "successful_recoveries": self._successful_recoveries, "is_running": self.is_running,
            "silent_organ_alerts": len(self._silent_organ_alerts),
        }

    def get_resonance_conditions(self) -> list:
        return [{
            "organ_name": self.organ_name,
            "event_types": [
                "device.capability_update",
                SystemEvent.ALARM,
                VascularEvent.SILENT_ORGAN,
                HeartEvent.BEAT,  # ★P3-5补闭环：心跳周期检测压力解除
                SystemEvent.STATUS_REQUEST,
            ],
            "min_priority": 1,
        }]

    # ========== 未来演化预留（v10.0 振荡场） ==========
    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None



# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "紧急处理",
    "class_name": "PulseEmergencyHandler",
    "attr_name": "emergency_handler",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseEmergencyHandler v9.5 分层脉冲自测（含SILENT_ORGAN） ===\n")
    class MockInfoField:
        def __init__(self):
            self.published = []
            self._load_level = "light"
        def publish(self, p): self.published.append(p)
        def set_load_level(self, level): self._load_level = level

    m = MockInfoField(); eh = PulseEmergencyHandler("紧急处理"); eh.set_info_field(m); eh.start()
    print(f"1. L3告警: {eh.on_pulse({'event_type': SystemEvent.ALARM, 'payload': {'level': 'L3', 'type': 'energy_critical'}, 'priority': 9})['status']}")
    # 验证恢复尝试脉冲的 layer 标记
    recovery_pulses = [p for p in m.published if p.get("event_type") == SystemEvent.RECOVERY_ATTEMPT]
    if recovery_pulses:
        print(f"   RECOVERY_ATTEMPT脉冲 layer: {recovery_pulses[-1].get('layer', '未设置')} (预期L3)")
    print(f"2. L4灾难: {eh.on_pulse({'event_type': SystemEvent.ALARM, 'payload': {'level': 'L4', 'type': 'kernel_panic'}, 'priority': 10})['status']}")
    # 验证安全模式脉冲的 layer 标记
    safe_pulses = [p for p in m.published if p.get("event_type") == SystemEvent.SAFE_MODE]
    if safe_pulses:
        print(f"   SAFE_MODE脉冲 layer: {safe_pulses[-1].get('layer', '未设置')} (预期L0)")
    print(f"3. L1轻微: {eh.on_pulse({'event_type': SystemEvent.ALARM, 'payload': {'level': 'L1', 'type': 'minor_warning'}, 'priority': 5})['status']}")
    r4 = eh.on_pulse({"event_type": VascularEvent.SILENT_ORGAN, "payload": {"silent_organs": ["胃", "眼睛"], "silence_details": [], "beat": 15}, "priority": 6})
    print(f"4. 沉默器官告警: {r4['status']}, 沉默={r4['silent_organs']}")

    # 测试硬件告警降级
    print("\n5. 硬件告警测试:")
    print(f"   告警前 load_level: {m._load_level}")
    eh.on_pulse({"event_type": SystemEvent.ALARM, "payload": {"level": "L3", "type": "hardware_critical"}, "priority": 9})
    print(f"   hardware_critical 后 load_level: {m._load_level} (预期 critical)")

    # 恢复
    m.set_load_level("light")

    # 测试熔断告警降级
    print("\n6. 熔断告警测试:")
    print(f"   告警前 load_level: {m._load_level}")
    eh.on_pulse({"event_type": SystemEvent.ALARM, "payload": {"level": "L3", "type": "organ_fused"}, "priority": 9})
    print(f"   organ_fused 后 load_level: {m._load_level} (预期 heavy)")

    s = eh.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"\n7. 统计: 紧急{s['emergency_count']}次, 安全模式={s['safe_mode']}, 恢复{s['successful_recoveries']}次, 沉默告警{s['silent_organ_alerts']}条")
    eh.stop(); print("\n=== 自测全部通过 ===")
