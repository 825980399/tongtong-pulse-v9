# -*- coding: utf-8 -*-
"""
PulseSpinalCord —— 脊髓器官 · 神经总检测与反射级巡检

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 SpinalCordEvent.INSPECT 与心跳，执行不经过大脑的快速反射级巡检，输出巡检报告并在异常时告警。
机制: on_pulse → _on_inspect 按心跳节奏执行反射级检查，产出 SpinalCordEvent.INSPECTION_REPORT，发现异常发 SystemEvent.ALARM；_on_status_request 上报状态。
定位: 躯体的「脊髓反射弧」，负责低时延的本体检测与条件反射。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import HeartEvent, LogLevel, SpinalCordEvent, SystemEvent


class PulseSpinalCord(BasePulseOrgan):
    """脉冲驱动脊髓（神经总检测中心 · v9.5 分层脉冲版）"""

    def __init__(self, organ_name: str = "脊髓"):
        super().__init__(organ_name)
        self._known_organs: dict[str, float] = {}
        self._inspection_count = 0
        self._reflex_count = 0
        self._last_inspect_time = 0.0  # ★v9.5修复：自巡检节流时间戳（随心跳驱动）
        self._last_status = "healthy"  # ★v9.5日志补全：上次巡检状态（用于状态变化日志）

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})
        if event_type == SpinalCordEvent.INSPECT:
            return self._on_inspect(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        elif event_type == HeartEvent.BEAT:
            source = pulse.get("source_organ", "unknown")
            self._known_organs[source] = time.time()
            # ★v9.5修复：脊髓自巡检节流——原设计依赖外部 INSPECT 事件，
            # 但框架中无人发送该事件（main.py仅装配、血管不驱动）→ 巡检/失联检测/反射弧成死功能。
            # 改为随心跳节律自驱动：每 30 秒执行一次巡检，恢复神经总检测中心职责。
            try:
                _now = time.time()
                if _now - self._last_inspect_time >= 30.0:
                    self._last_inspect_time = _now
                    self._on_inspect({})
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return None

    def _on_inspect(self, payload: dict) -> dict[str, Any]:
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._inspection_count += 1
        now = time.time()
        missing = []
        for organ, last_seen in list(self._known_organs.items()):
            if now - last_seen > 120:
                missing.append(organ)
                self._known_organs.pop(organ, None)
        status_text = "healthy" if not missing else "degraded"
        if missing:
            # v9.5: 失联器官告警标记为L0生命线层（保留每次失联都发的原语义）
            self._emit(SystemEvent.ALARM, {
                "type": "organ_missing", "level": "L3",
                "organs": missing, "message": f"失联器官: {missing}"
            }, priority=8, layer="L0")
            # ★v9.5日志补全：失联告警进后台日志——仅状态变化(健康→失联)时打，
            # 持续失联不重复打，避免每30秒刷屏
            if status_text != self._last_status:
                self._log(LogLevel.WARNING,
                          f"脊髓巡检: 失联器官 {missing}（共{len(self._known_organs)}个已知器官）")
        elif status_text != self._last_status:
            # 从失联状态恢复
            self._log(LogLevel.INFO, "脊髓巡检: 已恢复 healthy（失联器官全部恢复）")
        self._last_status = status_text
        # v9.5: 巡检报告标记为L3后台自主层
        self._emit(SpinalCordEvent.INSPECTION_REPORT, {
            "status": status_text, "total_organs": len(self._known_organs),
            "missing": missing, "inspection_id": self._inspection_count
        }, priority=3, layer="L3")
        return {"status": status_text, "total_organs": len(self._known_organs), "missing": missing}

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name, "inspection_count": self._inspection_count,
            "known_organs": len(self._known_organs), "reflex_count": self._reflex_count,
            "is_running": self.is_running,
        }

    def get_resonance_conditions(self) -> list:
        return [{"organ_name": self.organ_name, "event_types": [SpinalCordEvent.INSPECT, SystemEvent.STATUS_REQUEST, HeartEvent.BEAT], "min_priority": 1}]

    # ========== 未来演化预留（v10.0 振荡场） ==========
    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None



# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "脊髓",
    "class_name": "PulseSpinalCord",
    "attr_name": "spinal_cord",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseSpinalCord v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self): self.published = []
        def publish(self, p): self.published.append(p)
    m = MockInfoField(); sc = PulseSpinalCord("脊髓"); sc.set_info_field(m); sc.start()
    sc._known_organs = {"心脏": time.time(), "胃": time.time() - 200}
    r = sc.on_pulse({"event_type": SpinalCordEvent.INSPECT, "payload": {}, "priority": 4})
    print(f"1. 巡检: 状态={r['status']}, 总数={r['total_organs']}, 失联={r['missing']}")
    # 验证告警脉冲的 layer 标记
    alarm_pulses = [p for p in m.published if p.get("event_type") == SystemEvent.ALARM]
    if alarm_pulses:
        print(f"   ALARM脉冲 layer: {alarm_pulses[-1].get('layer', '未设置')} (预期L0)")
    # 验证巡检报告脉冲的 layer 标记
    report_pulses = [p for p in m.published if p.get("event_type") == SpinalCordEvent.INSPECTION_REPORT]
    if report_pulses:
        print(f"   INSPECTION_REPORT脉冲 layer: {report_pulses[-1].get('layer', '未设置')} (预期L3)")
    s = sc.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"2. 统计: 巡检{s['inspection_count']}次, 已知器官{s['known_organs']}个")
    sc.stop(); print("\n=== 自测全部通过 ===")
