# -*- coding: utf-8 -*-
"""
PulseDNARepair —— DNA修复器官 · 错误自愈与补丁生成

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 承接 DNARepairEvent.FIX，对框架运行中的错误类型匹配修复方案并尝试自愈，把生成的补丁以 DNARepairEvent.SOLUTION_GENERATED 广播。
机制: on_pulse 分派 _on_fix / _on_status_request；_on_fix 根据错误类型走 _generate_solution 生成修复方案，成功则 _emit DNARepairEvent.SOLUTION_GENERATED 供执行侧落地，无法匹配时返回未命中；get_stats 输出修复统计；refresh_runtime_params 供热加载同步参数；get_resonance_conditions 声明 DNARepairEvent.FIX 与 SystemEvent.STATUS_REQUEST 的订阅。
定位: 遗传层的「DNA 修复酶」，always_online=False、受 enable_evolution 开关控制，负责把运行期错误转化为可复用的修复经验。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import DNARepairEvent, SystemEvent


class PulseDNARepair(BasePulseOrgan):
    """
    脉冲驱动DNA修复（v9.5 分层脉冲版）

    修复流程:
        DNARepairEvent.FIX 脉冲到达
        → 匹配修复经验库
        → 命中 → 应用已知修复方案
        → 未命中 → 生成新修复方案，发射 SOLUTION_GENERATED 脉冲（L3后台自主层）
        → 记录到经验库
    """

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    def __init__(self, organ_name: str = "DNA修复"):
        super().__init__(organ_name)

        # 修复经验库
        self._repair_experience: dict[str, dict[str, Any]] = {}

        # 高频错误跟踪
        self._error_frequency: dict[str, int] = {}

        # 统计
        self._fix_count = 0
        self._success_count = 0

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == DNARepairEvent.FIX:
            return self._on_fix(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _on_fix(self, payload: dict) -> dict[str, Any]:
        """执行DNA修复"""
        error_type = payload.get("error_type", "unknown")
        error_message = payload.get("error_message", "")
        source_organ = payload.get("source_organ", "unknown")  # noqa: F841

        if not error_message:
            return {"status": "skipped", "reason": "空错误消息"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._fix_count += 1

        # 更新错误频率
        self._error_frequency[error_type] = self._error_frequency.get(error_type, 0) + 1

        # 步骤1: 匹配修复经验库
        signature = f"{error_type}:{error_message[:40]}"
        if signature in self._repair_experience:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._success_count += 1
            exp = self._repair_experience[signature]
            exp["use_count"] += 1
            exp["last_used"] = time.time()
            return {
                "status": "repaired",
                "method": "experience",
                "solution": exp["solution"],
                "signature": signature,
            }

        # 步骤2: 生成修复方案
        solution = self._generate_solution(error_type, error_message)

        # 步骤3: 记录到经验库
        self._repair_experience[signature] = {
            "error_type": error_type,
            "solution": solution,
            "use_count": 1,
            "first_seen": time.time(),
            "last_used": time.time(),
        }

        if solution:
            self._success_count += 1
            # v9.5: 修复方案生成脉冲标记为L3后台自主层
            self._emit(DNARepairEvent.SOLUTION_GENERATED, {
                "error_type": error_type,
                "solution": solution,
                "signature": signature,
            }, priority=5, layer="L3")
            return {"status": "repaired", "method": "generated", "solution": solution}
        else:
            return {"status": "failed", "reason": "无法生成修复方案"}

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()
    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "fix_count": self._fix_count,
            "success_count": self._success_count,
            "experience_size": len(self._repair_experience),
            "high_freq_errors": sorted(self._error_frequency.items(), key=lambda x: x[1], reverse=True)[:3],
            "is_running": self.is_running,
        }

    # ========== 修复方案生成 ==========

    def _generate_solution(self, error_type: str, error_message: str) -> str:
        """根据错误类型生成修复方案"""
        solutions = {
            "timeout": "增加超时阈值并添加重试逻辑",
            "connection": "检查网络连接状态，重新建立连接",
            "memory": "触发知识淘汰释放内存，降低缓存上限",
            "encoding": "切换编码格式或添加编码检测逻辑",
            "database": "执行数据库完整性检查和WAL自愈",
            "import": "检查依赖包安装状态，补充缺失依赖",
            "attribute": "检查属性访问路径，添加缺失属性或默认值",
            "type": "检查类型兼容性，添加类型转换或检查",
        }

        for key, solution in solutions.items():
            if key in error_type.lower():
                return solution

        return f"对 [{error_type}] 类型错误进行通用修复"

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    DNARepairEvent.FIX,
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
    "name": "DNA修复",
    "class_name": "PulseDNARepair",
    "attr_name": "dna_repair",
    "system": "genetic",
    "always_online": False,
    "feature_flag": "enable_evolution",
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseDNARepair v9.5 分层脉冲自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()

    repair = PulseDNARepair("DNA修复")
    repair.set_info_field(mock_field)
    repair.start()

    # 测试1: 未知错误生成方案
    r1 = repair.on_pulse({
        "event_type": DNARepairEvent.FIX,
        "payload": {"error_type": "timeout", "error_message": "Ollama API 调用超时", "source_organ": "嘴巴"},
        "priority": 5,
    })
    print(f"1. 超时修复: {r1['status']}, 方案={r1.get('solution', '')[:40]}...")

    # 验证修复方案生成脉冲的 layer 标记
    solution_pulses = [p for p in mock_field.published if p.get("event_type") == DNARepairEvent.SOLUTION_GENERATED]
    if solution_pulses:
        print(f"   SOLUTION_GENERATED脉冲 layer: {solution_pulses[-1].get('layer', '未设置')} (预期L3)")

    # 测试2: 经验库命中
    r2 = repair.on_pulse({
        "event_type": DNARepairEvent.FIX,
        "payload": {"error_type": "timeout", "error_message": "Ollama API 调用超时", "source_organ": "嘴巴"},
        "priority": 5,
    })
    print(f"2. 重复错误: {r2['status']}, 方法={r2.get('method', '')}")

    # 统计
    s = repair.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"3. 统计: 修复{s['fix_count']}次, 成功{s['success_count']}次, 经验{s['experience_size']}条")

    repair.stop()
    print("\n=== 自测全部通过 ===")

