"""PulseBoneMarrow —— PulseBoneMarrow 相关实现

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

"""
PulseBoneMarrow —— 脉冲驱动骨髓（免疫系统第四器官 · v9.5 分层脉冲版）
版本: v9.5 PulseNet
设计: 路灯、小林、星轨
日期: 2026年6月9日
更新: 2026年6月13日（P0-2+P0-5: 五合一全面改造——事件枚举+补充get_stats+自测同步）
更新: 2026年6月14日（v9.5: 生成结果脉冲标记layer=L3，适配分层异步调度）

职责:
    1. 错误特征库生成：从白细胞免疫记忆中提取错误模式
    2. 特征归类：将相似错误归入同一类别
    3. 预防性规则生成：基于历史错误特征生成预防规则
"""

from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import BoneMarrowEvent, SystemEvent


class PulseBoneMarrow(BasePulseOrgan):
    """
    脉冲驱动骨髓（v9.5 分层脉冲版）

    特征生成流程:
        BoneMarrowEvent.GENERATE 脉冲到达
        → 从白细胞获取免疫记忆
        → 提取错误类型模式
        → 归类相似错误
        → 生成预防规则
        → 发射 BoneMarrowEvent.GENERATE_RESULT 脉冲（L3后台自主层）
    """

    def __init__(self, organ_name: str = "骨髓"):
        super().__init__(organ_name)

        # 关联组件
        self.white_cell = None

        # 错误特征库
        self._error_patterns: dict[str, list[str]] = {}

        # 预防规则库
        self._preventive_rules: list[str] = []

        # 统计
        self._generate_count = 0

    # ========== 框架注入接口 ==========

    def set_white_cell(self, white_cell):
        self.white_cell = white_cell

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == BoneMarrowEvent.GENERATE:
            return self._on_generate(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _on_generate(self, payload: dict) -> dict[str, Any]:
        """生成错误特征库"""
        if self.white_cell is None:
            return {"status": "skipped", "reason": "白细胞未注入"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._generate_count += 1

        immune_memory = self.white_cell.get_immune_memory() if hasattr(self.white_cell, 'get_immune_memory') else {}
        new_patterns = 0
        new_rules = 0

        for signature, memory in immune_memory.items():
            error_type = memory.get("error_type", "unknown")

            # 归类
            if error_type not in self._error_patterns:
                self._error_patterns[error_type] = []
            if signature not in self._error_patterns[error_type]:
                self._error_patterns[error_type].append(signature)
                new_patterns += 1

            # 生成预防规则（失败次数多的错误类型）
            fail_count = memory.get("fail_count", 0)
            if fail_count >= 3:
                rule = f"预防规则: 对 [{error_type}] 类型错误进行前置检查"
                if rule not in self._preventive_rules:
                    self._preventive_rules.append(rule)
                    new_rules += 1

        # v9.5: 生成结果脉冲标记为L3后台自主层
        self._emit(BoneMarrowEvent.GENERATE_RESULT, {
            "new_patterns": new_patterns,
            "new_rules": new_rules,
            "pattern_categories": len(self._error_patterns),
            "preventive_rules": len(self._preventive_rules),
        }, priority=4, layer="L3")

        return {
            "status": "generated",
            "new_patterns": new_patterns,
            "new_rules": new_rules,
            "pattern_categories": len(self._error_patterns),
        }

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "generate_count": self._generate_count,
            "pattern_categories": len(self._error_patterns),
            "preventive_rules": len(self._preventive_rules),
            "is_running": self.is_running,
        }

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    BoneMarrowEvent.GENERATE,
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
    "name": "骨髓",
    "class_name": "PulseBoneMarrow",
    "attr_name": "bone_marrow",
    "system": "immune",
    "always_online": False,
    "feature_flag": "enable_immune",
    "extra_deps": {},
    "post_wiring": [
        {"target": "白细胞", "setter": "set_white_cell"},
    ],
}

if __name__ == "__main__":
    print("=== PulseBoneMarrow v9.5 分层脉冲自测 ===\n")

    class MockWhiteCell:
        def __init__(self):
            self._immune_memory = {
                "timeout:Ollama": {"error_type": "timeout", "fail_count": 5},
                "timeout:API": {"error_type": "timeout", "fail_count": 2},
                "encoding:utf8": {"error_type": "encoding", "fail_count": 3},
            }

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()
    mock_wbc = MockWhiteCell()

    marrow = PulseBoneMarrow("骨髓")
    marrow.set_info_field(mock_field)
    marrow.set_white_cell(mock_wbc)
    marrow.start()

    # 生成
    result = marrow.on_pulse({
        "event_type": BoneMarrowEvent.GENERATE,
        "payload": {},
        "priority": 4,
    })
    print(f"1. 生成结果: 新模式{result['new_patterns']}个, 新规则{result['new_rules']}个, 类别{result['pattern_categories']}个")

    # 验证生成结果脉冲的 layer 标记
    generate_pulses = [p for p in mock_field.published if p.get("event_type") == BoneMarrowEvent.GENERATE_RESULT]
    if generate_pulses:
        print(f"   GENERATE_RESULT脉冲 layer: {generate_pulses[-1].get('layer', '未设置')} (预期L3)")

    # 统计
    status = marrow.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"2. 统计: 生成{status['generate_count']}次, 类别{status['pattern_categories']}个, 规则{status['preventive_rules']}条")

    marrow.stop()
    print("\n=== 自测全部通过 ===")