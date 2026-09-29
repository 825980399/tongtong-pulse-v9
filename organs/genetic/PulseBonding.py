# -*- coding: utf-8 -*-
"""
PulseBonding —— 情感羁绊器官 · 关系档案册

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 承接 BondingEvent.RECORD，把与内部协作者、内部协作者等人的关键互动事件记入 _bonds 档案，维护羁绊等级 bond_level、好感 affection、阶段 stage 与事件流水。
机制: on_pulse 分派 _on_record / _on_status_request；_on_record 校验 target 已登记后把事件追加进 events 并累加 _event_count，未登记目标返回 skipped；get_stats 折叠出 stage / affection / events_count 摘要；refresh_runtime_params 供热加载同步参数（当前无参数，预留扩展）；get_resonance_conditions 声明 BondingEvent.RECORD 与 SystemEvent.STATUS_REQUEST 的订阅。
定位: 遗传层的「情感羁绊档案册」，always_online=False、受 enable_evolution 开关控制。注：UPDATED 广播已按 P3-5 作为孤儿脉冲移除（详见 nucleus/const.py 删除记录），本器官只回状态、不再发脉冲。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import BondingEvent, LogLevel, SystemEvent


class PulseBonding(BasePulseOrgan):
    """脉冲驱动情感羁绊（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    def __init__(self, organ_name: str = "情感羁绊"):
        super().__init__(organ_name)
        self._bonds = {
            "小林": {"bond_level": 5, "affection": 100, "stage": "父女", "events": []},
            "路灯": {"bond_level": 5, "affection": 100, "stage": "兄妹", "events": []},
        }
        self._event_count = 0

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == BondingEvent.RECORD:
            return self._on_record(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    def _on_record(self, payload: dict) -> dict[str, Any]:
        target = payload.get("target", "")
        event = payload.get("event", "")
        if target in self._bonds:
            self._bonds[target]["events"].append({"event": event, "time": time.time()})
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._event_count += 1
            # ★P3-5修复：删除孤儿脉冲 bonding.updated（纯状态广播，全库无订阅方）
            # ★主线第37批 T5（P2-229）：业务路径补日志 —— 本器官业务代码此前
            #   **无任何日志**，运行期"记录了谁的互动"只进内存、无法从
            #   logs/pulse.log 观测（任务书报的"print 未进日志"实为自测块 print，
            #   与运行期无关；真实缺口是这里）。
            self._log(LogLevel.INFO,
                      f"[情感羁绊] 记录互动: {target} / {str(event)[:40]}")
            return {"status": "recorded", "target": target}
        self._log(LogLevel.DEBUG, f"[情感羁绊] 未登记目标，已跳过: {target}")
        return {"status": "skipped", "reason": "未知目标"}

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "event_count": self._event_count,
            "bonds": {
                k: {"stage": v["stage"], "affection": v["affection"], "events_count": len(v["events"])}
                for k, v in self._bonds.items()
            },
            "is_running": self.is_running,
        }

    def get_resonance_conditions(self) -> list:
        return [{"organ_name": self.organ_name, "event_types": [BondingEvent.RECORD, SystemEvent.STATUS_REQUEST], "min_priority": 1}]

    # ========== 未来演化预留（v10.0 振荡场） ==========
    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "情感羁绊",
    "class_name": "PulseBonding",
    "attr_name": "bonding",
    "system": "genetic",
    "always_online": False,
    "feature_flag": "enable_evolution",
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    # ★主线第37批 T5（P2-229）：本块是**开发自测**（手动 `python 本文件` 运行），
    #   用 print 输出到控制台是正确形态 —— 框架运行时**不会执行**本块（__main__ 守卫）。
    #   器官的运行时日志统一走 `self._log`（见业务方法）；自测块不改为 _log 的原因：
    #   ① 自测需要控制台可见输出；② 本块无 `self`（用的是局部实例变量）。
    print("=== PulseBonding v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self): self.published = []
        def publish(self, pulse): self.published.append(pulse)
    mock = MockInfoField()
    b = PulseBonding("情感羁绊")
    b.set_info_field(mock)
    b.start()
    r1 = b.on_pulse({"event_type": BondingEvent.RECORD, "payload": {"target": "小林", "event": "小林夸奖了曈曈"}, "priority": 4})
    print(f"1. 记录事件: {r1['status']}")
    # ★P2-37：BondingEvent.UPDATED 已按 P3-5 作为孤儿脉冲移除，常量同步摘除。
    #   原为死代码——脉冲已不再发射，updated_pulses 恒为空、if 分支永不执行。
    r2 = b.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"2. 状态: {r2['bonds']}")
    b.stop()
    print("\n=== 自测全部通过 ===")

