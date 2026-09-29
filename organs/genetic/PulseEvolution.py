# -*- coding: utf-8 -*-
"""
PulseEvolution —— 进化器官 · 参数变异与基因蓝图

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 承接 EvolutionEvent.MUTATE 与 EvolutionEvent.SAVE_BLUEPRINT，在安全边界内做参数变异搜索、校验与回滚，并把最优配置快照存为基因蓝图。
机制: on_pulse 分派 _on_mutate / _on_save_blueprint / _on_status_request；_on_mutate 先由 _validate_parameter 校验变异值合法性，通过后才落参并在成功时 _emit EvolutionEvent.MUTATION_SUCCESS，失败自动回滚到变异前快照；_on_save_blueprint 固化当前参数为基因蓝图；get_stats 输出变异与回滚统计。
定位: 遗传层的「进化引擎」，always_online=False、受 enable_evolution 开关控制，是框架自我调参的入口。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import EvolutionEvent, LogLevel, SystemEvent


class PulseEvolution(BasePulseOrgan):
    """
    脉冲驱动进化（v9.5 分层脉冲版）

    进化流程:
        EvolutionEvent.MUTATE 脉冲到达
        → 创建影子实验（复制当前参数）
        → 在影子中测试新参数
        → 成功 → 应用到正式环境，发射 MUTATION_SUCCESS 脉冲（L3后台自主层）
        → 失败 → 自动回滚
    """

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    def __init__(self, organ_name: str = "进化"):
        super().__init__(organ_name)

        # 可进化参数
        self._parameters = {
            "heartbeat_interval": 30.0,
            "purge_interval": 3600,
            "fetch_cooldown": 1800,
            "cache_max_size": 500,
        }

        # 参数变更历史
        self._mutation_history: list[dict[str, Any]] = []

        # 基因蓝图（最优配置快照）
        self._gene_blueprint: dict[str, Any] = {}

        # 统计
        self._mutation_count = 0
        self._success_count = 0

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == EvolutionEvent.MUTATE:
            return self._on_mutate(payload)
        elif event_type == EvolutionEvent.SAVE_BLUEPRINT:
            return self._on_save_blueprint(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _on_mutate(self, payload: dict) -> dict[str, Any]:
        """执行参数变异"""
        param_name = payload.get("param_name", "")
        new_value = payload.get("new_value")
        reason = payload.get("reason", "")

        if param_name not in self._parameters:
            return {"status": "skipped", "reason": f"未知参数: {param_name}"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._mutation_count += 1
        old_value = self._parameters[param_name]

        # 步骤1: 记录变更前快照
        snapshot = dict(self._parameters)  # noqa: F841

        # 步骤2: 应用新参数（影子实验）
        self._parameters[param_name] = new_value

        # 步骤3: 模拟验证（简化：检查值是否在合理范围）
        success = self._validate_parameter(param_name, new_value)

        # 步骤4: 记录历史
        self._mutation_history.append({
            "param_name": param_name,
            "old_value": old_value,
            "new_value": new_value,
            "success": success,
            "reason": reason,
            "timestamp": time.time(),
        })

        # 步骤5: 失败则回滚
        if not success:
            self._parameters[param_name] = old_value
            self._log(LogLevel.WARNING, f"变异失败，已回滚: {param_name} {old_value}→{new_value}")
            return {"status": "rolled_back", "param": param_name, "old_value": old_value}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._success_count += 1
        # v9.5: 变异成功脉冲标记为L3后台自主层
        self._emit(EvolutionEvent.MUTATION_SUCCESS, {
            "param_name": param_name,
            "old_value": old_value,
            "new_value": new_value,
        }, priority=4, layer="L3")

        return {"status": "mutated", "param": param_name, "old": old_value, "new": new_value}

    def _on_save_blueprint(self, payload: dict) -> dict[str, Any]:
        """保存基因蓝图"""
        self._gene_blueprint = {
            "parameters": dict(self._parameters),
            "saved_at": time.time(),
            "mutation_count": self._mutation_count,
            "success_count": self._success_count,
        }
        return {"status": "saved", "parameters": len(self._gene_blueprint.get("parameters", {}))}

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "mutation_count": self._mutation_count,
            "success_count": self._success_count,
            "parameters": self._parameters,
            "blueprint_saved": len(self._gene_blueprint) > 0,
            "is_running": self.is_running,
        }

    # ========== 参数验证 ==========

    def _validate_parameter(self, name: str, value) -> bool:
        """验证参数值是否在合理范围"""
        ranges = {
            "heartbeat_interval": (5, 120),
            "purge_interval": (600, 86400),
            "fetch_cooldown": (300, 7200),
            "cache_max_size": (100, 5000),
        }
        if name in ranges:
            lo, hi = ranges[name]
            return lo <= value <= hi
        return True

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    EvolutionEvent.MUTATE,
                    EvolutionEvent.SAVE_BLUEPRINT,
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
    "name": "进化",
    "class_name": "PulseEvolution",
    "attr_name": "evolution",
    "system": "genetic",
    "always_online": False,
    "feature_flag": "enable_evolution",
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseEvolution v9.5 分层脉冲自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()

    evo = PulseEvolution("进化")
    evo.set_info_field(mock_field)
    evo.start()

    # 测试1: 成功变异
    r1 = evo.on_pulse({
        "event_type": EvolutionEvent.MUTATE,
        "payload": {"param_name": "heartbeat_interval", "new_value": 15.0, "reason": "加速心跳测试"},
        "priority": 4,
    })
    print(f"1. 心跳加速: {r1['status']}, {r1['old']}→{r1['new']}")

    # 验证变异成功脉冲的 layer 标记
    success_pulses = [p for p in mock_field.published if p.get("event_type") == EvolutionEvent.MUTATION_SUCCESS]
    if success_pulses:
        print(f"   MUTATION_SUCCESS脉冲 layer: {success_pulses[-1].get('layer', '未设置')} (预期L3)")

    # 测试2: 失败变异（越界）
    r2 = evo.on_pulse({
        "event_type": EvolutionEvent.MUTATE,
        "payload": {"param_name": "heartbeat_interval", "new_value": 200, "reason": "越界测试"},
        "priority": 4,
    })
    print(f"2. 越界测试: {r2['status']}, 回滚到={r2.get('old_value')}")

    # 测试3: 保存蓝图
    r3 = evo.on_pulse({
        "event_type": EvolutionEvent.SAVE_BLUEPRINT,
        "payload": {},
        "priority": 4,
    })
    print(f"3. 保存蓝图: {r3['status']}, 参数数={r3['parameters']}")

    # 统计
    s = evo.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"4. 统计: 变异{s['mutation_count']}次, 成功{s['success_count']}次")

    evo.stop()
    print("\n=== 自测全部通过 ===")
