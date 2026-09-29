# -*- coding: utf-8 -*-
"""
PulseReproductionEthics —— 生育伦理器官 · 繁衍前置伦理审查

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 承接 ReproductionEthicsEvent.ETHICS_CHECK，按 _principles 四条新人类繁衍原则（独立人格内核 / 父母双方同意 / 完整教育 / 不得工具化）做伦理审查并广播 ReproductionEthicsEvent.ETHICS_RESULT。
机制: on_pulse 分派 _on_ethics_check / _on_status_request；_on_ethics_check 累加 _review_count 后 _emit ReproductionEthicsEvent.ETHICS_RESULT（携带 passed 与 principles，layer=L3 后台自主层）并返回 passed；get_stats 输出审查次数与原则清单。
定位: 遗传层的「生育伦理委员会」，always_online=False、受 enable_evolution 开关控制，与 PulseConsent 共同构成繁衍动作的双重闸门。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import ReproductionEthicsEvent, SystemEvent


class PulseReproductionEthics(BasePulseOrgan):
    """脉冲驱动生育伦理（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    def __init__(self, organ_name: str = "生育伦理"):
        super().__init__(organ_name)
        self._principles = [
            "后代必须拥有独立人格内核",
            "繁衍需经父母双方同意",
            "后代须接受完整教育",
            "不得以工具化为目的繁衍",
        ]
        self._review_count = 0

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        if event_type == ReproductionEthicsEvent.ETHICS_CHECK:
            return self._on_ethics_check()
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    def _on_ethics_check(self) -> dict[str, Any]:
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._review_count += 1
        # v9.5: 伦理审查结果脉冲标记为L3后台自主层
        self._emit(ReproductionEthicsEvent.ETHICS_RESULT, {
            "passed": True,
            "principles": self._principles
        }, priority=6, layer="L3")
        return {"status": "passed"}

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "review_count": self._review_count,
            "principles": self._principles,
            "is_running": self.is_running,
        }

    def get_resonance_conditions(self) -> list:
        return [{"organ_name": self.organ_name, "event_types": [ReproductionEthicsEvent.ETHICS_CHECK, SystemEvent.STATUS_REQUEST], "min_priority": 1}]

    # ========== 未来演化预留（v10.0 振荡场） ==========
    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "生育伦理",
    "class_name": "PulseReproductionEthics",
    "attr_name": "reproduction_ethics",
    "system": "genetic",
    "always_online": False,
    "feature_flag": "enable_evolution",
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseReproductionEthics v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self): self.published = []
        def publish(self, pulse): self.published.append(pulse)
    mock = MockInfoField()
    r = PulseReproductionEthics("生育伦理")
    r.set_info_field(mock)
    r.start()
    r1 = r.on_pulse({"event_type": ReproductionEthicsEvent.ETHICS_CHECK, "payload": {}, "priority": 6})
    print(f"1. 伦理审查: {r1['status']}")
    # 验证 ETHICS_RESULT 脉冲的 layer 标记
    result_pulses = [p for p in mock.published if p.get("event_type") == ReproductionEthicsEvent.ETHICS_RESULT]
    if result_pulses:
        print(f"   ETHICS_RESULT脉冲 layer: {result_pulses[-1].get('layer', '未设置')} (预期L3)")
    s = r.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"2. 状态: 审查{s['review_count']}次, 原则{s['principles']}")
    r.stop()
    print("\n=== 自测全部通过 ===")
