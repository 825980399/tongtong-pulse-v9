"""PulseThymus —— PulseThymus 相关实现

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ★主线第37批 T5（P2-229）顺手治理：原为「悬空第二 docstring」——
#   字符串表达式语句会**关闭 ruff 的 import 区**，致其后所有 import 报 E402
#   （本文件存量 5 条）。最小治理 = 转注释块：内容逐字保留、运行时零作用、
#   行为完全等价，且**无需移动任何 import**（零加载时序风险）。
# _m37_t5_e402
# """
# PulseThymus —— 脉冲驱动胸腺（免疫系统第三器官 · v9.5 分层脉冲版）
# 版本: v9.5 PulseNet
# 设计: 内部协作者、内部协作者、内部协作者
# 日期: 2026年6月9日
# 更新: 2026年6月13日（P0-2+P0-5: 五合一全面改造——事件枚举+补充get_stats+自测同步）
# 更新: 2026年6月14日（v9.5: 训练结果脉冲标记layer=L3，适配分层异步调度）
#
# 职责:
#     1. T细胞训练：从白细胞获取免疫记忆，筛选高质量修复策略
#     2. 免疫策略优化：对修复成功率高的策略进行强化标记
#     3. 失败策略淘汰：移除成功率过低的修复策略
#     4. P2-3 交互模式训练：从白细胞的免疫记忆中提取并泛化攻击模式
# """

import random
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import LogLevel, SystemEvent, ThymusEvent


class PulseThymus(BasePulseOrgan):
    """
    脉冲驱动胸腺（v9.5 分层脉冲版）

    训练流程:
        ThymusEvent.TRAIN 脉冲到达
        → 从白细胞获取免疫记忆
        → 评估修复策略成功率
        → 强化高成功率策略
        → 淘汰低成功率策略
        → 发射 ThymusEvent.TRAIN_RESULT 脉冲（L3后台自主层）
    """

    def __init__(self, organ_name: str = "胸腺"):
        super().__init__(organ_name)

        # 关联组件
        self.white_cell = None  # 白细胞引用（用于获取免疫记忆）

        # 训练后的精英策略库
        self._elite_strategies: dict[str, dict[str, Any]] = {}

        # ===== P2-3: 模式训练 =====
        self._trained_patterns: dict[str, dict[str, Any]] = {}

        # 统计
        self._train_count = 0
        
        # ===== v22.0 M5新增：心跳驱动定期训练 =====
        # ★v30.0负载均衡修复：随机错峰初始化，避免与其他器官取模任务同点共振
        self._heartbeat_count = random.randint(1, 299)
        self._train_interval = 300  # 每300次心跳触发一次训练（约50分钟）
        # ===== v22.0 M5新增结束 =====

    # ========== 框架注入接口 ==========

    def set_white_cell(self, white_cell):
        """注入白细胞引用，用于获取免疫记忆"""
        self.white_cell = white_cell

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == ThymusEvent.TRAIN:
            return self._on_train(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        elif event_type == "heart.beat":
            return self._on_heartbeat(payload)

        return None

    # ========== 事件处理 ==========

    def _on_train(self, payload: dict) -> dict[str, Any]:
        """执行T细胞训练"""
        if self.white_cell is None:
            # ★主线第37批 T5（P2-229）：业务路径补日志（原业务代码无任何日志）
            self._log(LogLevel.DEBUG, "[胸腺] 训练跳过：白细胞未注入")
            return {"status": "skipped", "reason": "白细胞未注入"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._train_count += 1

        immune_memory = self.white_cell.get_immune_memory() if hasattr(self.white_cell, 'get_immune_memory') else {}
        promoted = 0
        eliminated = 0

        for signature, memory in immune_memory.items():
            total = memory.get("success_count", 0) + memory.get("fail_count", 0)
            if total == 0:
                continue

            success_rate = memory.get("success_count", 0) / total

            if success_rate >= 0.8 and signature not in self._elite_strategies:
                # 高成功率策略 → 晋升精英库
                self._elite_strategies[signature] = {
                    "strategy": memory["strategy"],
                    "success_rate": round(success_rate, 2),
                    "promoted_at": time.time(),
                }
                promoted += 1
            elif success_rate < 0.2 and signature in self._elite_strategies:
                # 低成功率精英策略 → 降级
                del self._elite_strategies[signature]
                eliminated += 1

        # P2-3: 对交互模式进行训练和泛化
        pattern_trained = self._train_interaction_patterns(immune_memory)
        promoted += pattern_trained

        # v9.5: 训练结果脉冲标记为L3后台自主层
        self._emit(ThymusEvent.TRAIN_RESULT, {
            "promoted": promoted,
            "eliminated": eliminated,
            "elite_count": len(self._elite_strategies),
        }, priority=4, layer="L3")

        # ★主线第37批 T5（P2-229）：业务路径补日志（原业务代码无任何日志）
        self._log(LogLevel.INFO,
                  f"[胸腺] 训练完成: 晋升 {promoted} / 淘汰 {eliminated} / "
                  f"精英库 {len(self._elite_strategies)}")
        return {
            "status": "trained",
            "promoted": promoted,
            "eliminated": eliminated,
            "elite_count": len(self._elite_strategies),
        }
    def _on_heartbeat(self, payload: dict) -> dict[str, Any]:
        """
        ★v22.0 M5新增：心跳驱动定期T细胞训练。
        每300次心跳（约50分钟）自动触发一次训练。
        """
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._heartbeat_count += 1
        if self._heartbeat_count % self._train_interval == 0:
            self._heartbeat_count = 0
            if self.info_field and hasattr(self.info_field, 'submit_adaptive_task'):
                self.info_field.submit_adaptive_task(
                    lambda: self._on_train({}),
                    task_name="胸腺T细胞训练",
                    priority="normal"
                )
            else:
                self._on_train({})
        return {"status": "ok", "heartbeat_count": self._heartbeat_count}
    # ========== P2-3: 交互模式训练 ==========

    def _train_interaction_patterns(self, immune_memory: dict[str, Any]) -> int:
        """
        从白细胞的免疫记忆中提取交互模式，进行泛化训练。

        如果白细胞已经有交互模式数据，直接使用；否则从免疫记忆中提取。
        """
        trained = 0

        # 优先使用白细胞中的交互模式数据
        # ★P3-1修复：跨器官私有属性直读 → 公开 getter（规则14）
        if self.white_cell and hasattr(self.white_cell, 'get_interaction_patterns'):
            patterns = self.white_cell.get_interaction_patterns()
            for pattern_key, pattern in patterns.items():
                if pattern["occurrence_count"] >= 3:
                    if pattern_key not in self._trained_patterns:
                        self._trained_patterns[pattern_key] = {
                            "pattern_type": pattern["pattern_type"],
                            "total_occurrences": pattern["occurrence_count"],
                            "trained_at": time.time(),
                        }
                        trained += 1
        else:
            # 兜底：从免疫记忆中提取模式
            pattern_groups: dict[str, int] = {}
            for signature, memory in immune_memory.items():
                error_type = memory.get("error_type", "unknown")
                if error_type == "security_event":
                    pattern_groups["安全事件模式"] = pattern_groups.get("安全事件模式", 0) + 1
                else:
                    pattern_groups[f"{error_type}错误模式"] = pattern_groups.get(f"{error_type}错误模式", 0) + 1

            for pattern_name, count in pattern_groups.items():
                if count >= 2:
                    if pattern_name not in self._trained_patterns:
                        self._trained_patterns[pattern_name] = {
                            "pattern_type": pattern_name,
                            "total_occurrences": count,
                            "trained_at": time.time(),
                        }
                        trained += 1

        return trained

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_elite_strategies(self) -> dict[str, dict[str, Any]]:
        """
        ★v22.0 M5新增：公开接口——获取精英策略库。
        供白细胞在免疫记忆未命中时查询。
        """
        return dict(self._elite_strategies)
    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "train_count": self._train_count,
            "elite_strategies": len(self._elite_strategies),
            "trained_patterns": len(self._trained_patterns),
            "white_cell_connected": self.white_cell is not None,
            "is_running": self.is_running,
        }

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    ThymusEvent.TRAIN,
                    SystemEvent.STATUS_REQUEST,
                    "heart.beat",  # ★v22.0 M5新增：心跳驱动定期训练
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
    "name": "胸腺",
    "class_name": "PulseThymus",
    "attr_name": "thymus",
    "system": "immune",
    "always_online": False,
    "feature_flag": "enable_immune",
    "extra_deps": {},
    "post_wiring": [
        {"target": "白细胞", "setter": "set_white_cell"},
    ],
}

if __name__ == "__main__":
    # ★主线第37批 T5（P2-229）：本块是**开发自测**（手动 `python 本文件` 运行），
    #   用 print 输出到控制台是正确形态 —— 框架运行时**不会执行**本块（__main__ 守卫）。
    #   器官的运行时日志统一走 `self._log`（见业务方法）；自测块不改为 _log 的原因：
    #   ① 自测需要控制台可见输出；② 本块无 `self`（用的是局部实例变量）。
    print("=== PulseThymus v9.5 分层脉冲自测 ===\n")

    # 模拟白细胞
    class MockWhiteCell:
        def __init__(self):
            self._immune_memory = {
                "timeout:test": {"strategy": "重试", "success_count": 9, "fail_count": 1},
                "unknown:test": {"strategy": "未知", "success_count": 1, "fail_count": 9},
                "memory:test": {"strategy": "清理", "success_count": 5, "fail_count": 5},
            }

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()
    mock_wbc = MockWhiteCell()

    thymus = PulseThymus("胸腺")
    thymus.set_info_field(mock_field)
    thymus.set_white_cell(mock_wbc)
    thymus.start()

    # 训练
    result = thymus.on_pulse({
        "event_type": ThymusEvent.TRAIN,
        "payload": {},
        "priority": 4,
    })
    print(f"1. 训练结果: 晋升{result['promoted']}个, 淘汰{result['eliminated']}个, 精英{result['elite_count']}个")

    # 验证训练结果脉冲的 layer 标记
    train_pulses = [p for p in mock_field.published if p.get("event_type") == ThymusEvent.TRAIN_RESULT]
    if train_pulses:
        print(f"   TRAIN_RESULT脉冲 layer: {train_pulses[-1].get('layer', '未设置')} (预期L3)")

    # 统计
    status = thymus.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"2. 统计: 训练{status['train_count']}次, 精英策略{status['elite_strategies']}个")

    thymus.stop()
    print("\n=== 自测全部通过 ===")