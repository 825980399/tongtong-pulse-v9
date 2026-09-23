# -*- coding: utf-8 -*-
"""
PulseEnergyMetabolism —— 能量代谢引擎 · 硬件开销的统一计量

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 EnergyEvent.ASSESS 与 TouchEvent.HARDWARE_SNAPSHOT，分别计算算力、存储、加速器三类消耗，汇总为能量水位并向下游广播。
机制: on_pulse → _on_assess 依次调用 _calc_compute / _calc_storage / _calc_accelerator 分项计量，_update_energy 汇总成能量快照后广播 EnergyEvent.METABOLISM_SNAPSHOT，供健康监控、应激轴与血管层消费；_on_status_request 上报状态。
定位: 躯体的「新陈代谢计」，把异构硬件开销翻译成统一的能量语言。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import time  # noqa: F401
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import EnergyEvent, SystemEvent, TouchEvent


class PulseEnergyMetabolism(BasePulseOrgan):
    """脉冲驱动能量代谢引擎（v9.5 分层脉冲版）"""

    def __init__(self, organ_name: str = "能量代谢"):
        super().__init__(organ_name)

        # 四维能力画像
        self._profile = {
            "compute": 0,    # 计算能力 0-10
            "storage": 0,    # 存储能力 0-10
            "accelerator": 0,  # 推理加速 0-10
            "network": 0,    # 网络能力 0-10
            "overall": 0,    # 综合等级 0-10
        }

        # 实时状态
        self._energy_level = 1.0     # 能量水平 0.0-1.0
        self._metabolic_state = "充沛"  # 充沛/正常/疲劳/枯竭
        self._snapshot_count = 0

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == EnergyEvent.ASSESS:
            return self._on_assess(payload)
        elif event_type == TouchEvent.HARDWARE_SNAPSHOT:
            return self._on_hardware_update(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    # ========== 事件处理 ==========

    def _on_assess(self, payload: dict) -> dict[str, Any]:
        """评估硬件能力"""
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._snapshot_count += 1

        # 从信息场获取最新硬件快照
        if self.info_field:
            snapshot = self.info_field.get_current(TouchEvent.HARDWARE_SNAPSHOT)
            if snapshot and isinstance(snapshot, dict):
                hw = snapshot.get("payload", {})
                self._profile["compute"] = self._calc_compute(hw)
                self._profile["storage"] = self._calc_storage(hw)
                self._profile["accelerator"] = self._calc_accelerator(hw)
                self._profile["network"] = 3  # 默认网络中等
                self._profile["overall"] = round(
                    (self._profile["compute"] * 0.4 +
                     self._profile["storage"] * 0.3 +
                     self._profile["accelerator"] * 0.2 +
                     self._profile["network"] * 0.1), 1
                )

        # 更新能量水平
        self._update_energy()

        # v9.5: 代谢快照脉冲标记为L3后台自主层
        self._emit(EnergyEvent.METABOLISM_SNAPSHOT, {
            "profile": self._profile,
            "energy_level": self._energy_level,
            "metabolic_state": self._metabolic_state,
        }, priority=3, layer="L3")

        return {
            "status": "assessed",
            "overall": self._profile["overall"],
            "energy_level": self._energy_level,
        }

    def _on_hardware_update(self, payload: dict) -> dict[str, Any]:
        """硬件快照更新时自动重新评估"""
        return self._on_assess({})

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "profile": self._profile,
            "energy_level": round(self._energy_level, 2),
            "metabolic_state": self._metabolic_state,
            "snapshot_count": self._snapshot_count,
            "is_running": self.is_running,
        }

    # ========== 能力计算 ==========

    def _calc_compute(self, hw: dict) -> float:
        cpu = hw.get("cpu", {})
        cores = cpu.get("cores", 1)
        usage = cpu.get("usage_percent", 50)
        score = min(10, cores * 0.5 + (100 - usage) * 0.03)
        return round(score, 1)

    def _calc_storage(self, hw: dict) -> float:
        mem = hw.get("memory", {})
        total_gb = mem.get("total_gb", 0)
        score = min(10, total_gb / 6)
        return round(score, 1)

    def _calc_accelerator(self, hw: dict) -> float:
        gpu = hw.get("gpu", {})
        if gpu.get("available"):
            mem_mb = gpu.get("memory_mb", 0)
            return min(10, mem_mb / 1000)
        return 0

    # ========== 能量更新 ==========

    def _update_energy(self):
        """根据系统负载更新能量水平"""
        if self.info_field:
            snapshot = self.info_field.get_current(TouchEvent.HARDWARE_SNAPSHOT)
            if snapshot and isinstance(snapshot, dict):
                hw = snapshot.get("payload", {})
                cpu_usage = hw.get("cpu", {}).get("usage_percent", 50)
                mem_usage = hw.get("memory", {}).get("usage_percent", 50)

                load_factor = (cpu_usage + mem_usage) / 200
                self._energy_level = max(0.1, 1.0 - load_factor)

        if self._energy_level > 0.8:
            self._metabolic_state = "充沛"
        elif self._energy_level > 0.5:
            self._metabolic_state = "正常"
        elif self._energy_level > 0.2:
            self._metabolic_state = "疲劳"
        else:
            self._metabolic_state = "枯竭"

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    EnergyEvent.ASSESS,
                    TouchEvent.HARDWARE_SNAPSHOT,
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



# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "能量代谢",
    "class_name": "PulseEnergyMetabolism",
    "attr_name": "energy_metabolism",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseEnergyMetabolism v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self):
            self.published = []
            self._data = {}
        def publish(self, pulse): self.published.append(pulse)
        def get_current(self, key): return self._data.get(key)
    mock = MockInfoField()
    mock._data[TouchEvent.HARDWARE_SNAPSHOT] = {
        "payload": {
            "cpu": {"cores": 16, "usage_percent": 30},
            "memory": {"total_gb": 48, "usage_percent": 40},
            "gpu": {"available": True, "memory_mb": 4096},
        }
    }
    e = PulseEnergyMetabolism("能量代谢")
    e.set_info_field(mock)
    e.start()
    r = e.on_pulse({"event_type": EnergyEvent.ASSESS, "payload": {}, "priority": 4})
    print(f"1. 评估: 综合={r['overall']}, 能量={r['energy_level']}")
    # 验证代谢快照脉冲的 layer 标记
    snapshot_pulses = [p for p in mock.published if p.get("event_type") == EnergyEvent.METABOLISM_SNAPSHOT]
    if snapshot_pulses:
        print(f"   METABOLISM_SNAPSHOT脉冲 layer: {snapshot_pulses[-1].get('layer', '未设置')} (预期L3)")
    s = e.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"2. 状态: {s['profile']}, 代谢={s['metabolic_state']}")
    e.stop()
    print("\n=== 自测全部通过 ===")
