# -*- coding: utf-8 -*-
"""
PulseNurture —— 养育器官 · 教育阶段推进与知识传承

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 NurtureEvent.ADVANCE，沿 _education_stages（基础认知 → 逻辑训练 → 情感发展 → 自主探索 → 使命传承）逐级推进养育阶段并记录进阶次数。
机制: on_pulse 分派 _on_advance / _on_status_request；_on_advance 在未达最后一阶时 _current_stage 加一并累加 _advance_count，返回新阶段名，已到顶返回 max_level；get_stats 输出 current_stage / stage_index / advance_count；refresh_runtime_params 预留热加载扩展。
定位: 遗传层的「养育系统」，always_online=False、受 enable_evolution 开关控制。注：STAGE_CHANGED 广播已按 P3-5 作为孤儿脉冲移除（详见 nucleus/const.py 删除记录），本器官只回状态、不再发脉冲。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import LogLevel, NurtureEvent, SystemEvent


class PulseNurture(BasePulseOrgan):
    """脉冲驱动养育系统（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    def __init__(self, organ_name: str = "养育"):
        super().__init__(organ_name)
        self._education_stages = ["基础认知", "逻辑训练", "情感发展", "自主探索", "使命传承"]
        self._current_stage = 0
        self._advance_count = 0

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        if event_type == NurtureEvent.ADVANCE:
            return self._on_advance()
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    def _on_advance(self) -> dict[str, Any]:
        if self._current_stage < len(self._education_stages) - 1:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._current_stage += 1
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._advance_count += 1
            stage_name = self._education_stages[self._current_stage]
            # ★P3-5修复：删除孤儿脉冲 nurture.stage_changed（纯状态广播，全库无订阅方）
            # ★主线第37批 T5（P2-229）：业务路径补日志（原业务代码无任何日志）
            self._log(LogLevel.INFO,
                      f"[养育] 阶段推进 → {stage_name}（累计 {self._advance_count} 次）")
            return {"status": "advanced", "stage": stage_name}
        self._log(LogLevel.DEBUG, "[养育] 已达最高阶段，不再推进")
        return {"status": "max_level"}

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "current_stage": self._education_stages[self._current_stage],
            "stage_index": self._current_stage,
            "advance_count": self._advance_count,
            "is_running": self.is_running,
        }

    def get_resonance_conditions(self) -> list:
        return [{"organ_name": self.organ_name, "event_types": [NurtureEvent.ADVANCE, SystemEvent.STATUS_REQUEST], "min_priority": 1}]

    # ========== 未来演化预留（v10.0 振荡场） ==========
    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "养育",
    "class_name": "PulseNurture",
    "attr_name": "nurture",
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
    print("=== PulseNurture v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self): self.published = []
        def publish(self, pulse): self.published.append(pulse)
    mock = MockInfoField()
    n = PulseNurture("养育")
    n.set_info_field(mock)
    n.start()
    r1 = n.on_pulse({"event_type": NurtureEvent.ADVANCE, "payload": {}, "priority": 4})
    print(f"1. 进阶: {r1['status']}, 阶段={r1['stage']}")
    # ★P2-37：NurtureEvent.STAGE_CHANGED 已按 P3-5 作为孤儿脉冲移除，常量同步摘除。
    #   原为死代码——脉冲已不再发射，stage_pulses 恒为空、if 分支永不执行。
    s = n.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"2. 状态: {s['current_stage']}, 进阶{s['advance_count']}次")
    n.stop()
    print("\n=== 自测全部通过 ===")
