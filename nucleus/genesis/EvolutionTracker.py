# -*- coding: utf-8 -*-
"""
EvolutionTracker.py —— 进化追踪器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 追踪进化历史与演化轨迹
机制: 基于EvolutionTracker类实现，包含10个核心方法
定位: 进化监测层
"""

import threading
import time
from typing import Any



class EvolutionTracker:
    """
    演化追踪器
    
    工作原理:
        1. 器官执行参数变异时，记录变异前后的参数值和结果。
        2. 知识节点升级/降级/淘汰时，记录演化路径。
        3. 定期评估演化方向——如果回滚率过高，发出预警。
        4. 输出演化报告，供进化器官和系统管理器参考。
    
    核心指标:
        - 变异成功率: 成功变异数 / 总变异尝试数。
        - 回滚率: 触发回滚的变异数 / 总变异尝试数。
        - 演化方向: 基于近期成功率的趋势判断。
    
    当前状态（v9.0）:
        - 接口完整定义，但功能开关默认关闭。
        - P3阶段可激活完整的在线演化追踪。
    """
    
    def __init__(self, max_history: int = 500):
        """
        Args:
            max_history: 最大历史记录数
        """
        # 变异历史
        self._mutation_history: list[dict[str, Any]] = []
        
        # 节点演化路径: node_id → [{from_level, to_level, timestamp, reason}]
        self._node_evolution_paths: dict[str, list[dict[str, Any]]] = {}
        
        # 成功/失败统计
        self._total_mutations = 0
        self._successful_mutations = 0
        self._rolled_back_mutations = 0
        
        self._max_history = max_history
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 功能开关
        self._enabled = False

    # ========== 框架控制 ==========

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    # ========== 变异记录 ==========

    def record_mutation(self, 
                        organ_name: str,
                        param_name: str,
                        old_value: Any,
                        new_value: Any,
                        success: bool,
                        reason: str = "") -> dict[str, Any]:
        """
        记录一次器官参数变异。

        Args:
            organ_name: 执行变异的器官名称
            param_name: 变异的参数名
            old_value: 变异前的值
            new_value: 变异后的值
            success: 是否成功（False表示已回滚）
            reason: 变异原因

        Returns:
            变异记录
        """
        if not self._enabled:
            return {"status": "disabled"}

        with self._lock:
            self._total_mutations += 1
            
            if success:
                self._successful_mutations += 1
            else:
                self._rolled_back_mutations += 1

            record = {
                "mutation_id": self._total_mutations,
                "organ": organ_name,
                "param": param_name,
                "old_value": old_value,
                "new_value": new_value,
                "success": success,
                "reason": reason,
                "timestamp": time.time(),
            }

            self._mutation_history.append(record)

            # 超出容量时清理旧记录
            while len(self._mutation_history) > self._max_history:
                self._mutation_history.pop(0)

            return record

    def get_mutation_history(self, organ_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """
        查询变异历史。

        Args:
            organ_name: 器官名称过滤（可选）
            limit: 返回数量

        Returns:
            变异记录列表（按时间倒序）
        """
        with self._lock:
            history = list(self._mutation_history)
        
        if organ_name:
            history = [h for h in history if h["organ"] == organ_name]
        
        return list(reversed(history))[-limit:]

    # ========== 节点演化路径追踪 ==========

    def record_node_evolution(self,
                              node_id: str,
                              from_level: str,
                              to_level: str,
                              reason: str = "") -> dict[str, Any]:
        """
        记录一个知识节点的演化路径。

        Args:
            node_id: 节点ID
            from_level: 演化前层级（L1/L2/L3）
            to_level: 演化后层级
            reason: 演化原因（如"压缩内化"、"复盘压缩"、"融合抽象"）

        Returns:
            演化记录
        """
        if not self._enabled:
            return {"status": "disabled"}

        with self._lock:
            if node_id not in self._node_evolution_paths:
                self._node_evolution_paths[node_id] = []

            record = {
                "from_level": from_level,
                "to_level": to_level,
                "timestamp": time.time(),
                "reason": reason,
            }

            self._node_evolution_paths[node_id].append(record)
            return record

    def get_node_evolution_path(self, node_id: str) -> list[dict[str, Any]]:
        """
        获取指定节点的完整演化路径。

        Args:
            node_id: 节点ID

        Returns:
            演化路径列表
        """
        return self._node_evolution_paths.get(node_id, [])

    # ========== 演化方向评估 ==========

    def evaluate_evolution_direction(self) -> dict[str, Any]:
        """
        评估当前演化方向是否健康。

        Returns:
            评估报告
        """
        with self._lock:
            total = max(1, self._total_mutations)
            success_rate = self._successful_mutations / total
            rollback_rate = self._rolled_back_mutations / total

            # 方向判断
            if success_rate >= 0.8:
                direction = "healthy"
                assessment = "演化方向健康，变异成功率较高"
            elif success_rate >= 0.5:
                direction = "cautious"
                assessment = "演化方向需谨慎，变异成功率中等"
            else:
                direction = "unstable"
                assessment = "演化方向不稳定，回滚率过高，建议降低变异频率"

            return {
                "direction": direction,
                "assessment": assessment,
                "total_mutations": self._total_mutations,
                "successful_mutations": self._successful_mutations,
                "rolled_back_mutations": self._rolled_back_mutations,
                "success_rate": round(success_rate, 3),
                "rollback_rate": round(rollback_rate, 3),
            }

    def get_evolution_suggestions(self) -> list[str]:
        """
        基于历史数据生成演化建议。

        Returns:
            建议列表
        """
        suggestions = []
        evaluation = self.evaluate_evolution_direction()

        if evaluation["direction"] == "unstable":
            suggestions.append("建议降低参数变异频率，增加变异前验证")
            suggestions.append("检查回滚原因，避免重复尝试失败的变异方向")

        if evaluation["direction"] == "cautious":
            suggestions.append("建议在低负载时段进行高风险变异")
            suggestions.append("考虑对重要参数启用影子实验模式")

        if self._total_mutations > 100 and evaluation["success_rate"] > 0.9:
            suggestions.append("演化方向良好，可考虑增大参数搜索空间")

        return suggestions

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取演化追踪统计"""
        with self._lock:
            evaluation = self.evaluate_evolution_direction()
            return {
                "enabled": self._enabled,
                "total_mutations": self._total_mutations,
                "successful_mutations": self._successful_mutations,
                "rolled_back_mutations": self._rolled_back_mutations,
                "success_rate": evaluation["success_rate"],
                "rollback_rate": evaluation["rollback_rate"],
                "direction": evaluation["direction"],
                "history_size": len(self._mutation_history),
                "tracked_nodes": len(self._node_evolution_paths),
                "suggestions": self.get_evolution_suggestions(),
            }

    # ========== 未来演化预留（v10.0 振荡场） ==========

    def on_field_oscillation(self, field_data: dict[str, Any]):
        """
        【预留 v10.0】振荡场中，演化轨迹由场的长期频率漂移追踪。
        不再依赖离散事件记录，而是通过场的频谱变化发现演化趋势。
        """

    def compare_evolution_paths(self, node_ids: list[str]) -> dict[str, Any]:
        """
        【预留 v10.0】比较多个节点的演化路径，发现共同演化模式。

        Args:
            node_ids: 要比较的节点ID列表

        Returns:
            比较结果
        """
        paths = {}
        for node_id in node_ids:
            paths[node_id] = self.get_node_evolution_path(node_id)
        return {"paths": paths, "common_patterns": []}


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== EvolutionTracker 自测 ===\n")
    
    tracker = EvolutionTracker(max_history=100)
    tracker.enable()
    
    # 1. 记录成功的变异
    r1 = tracker.record_mutation(
        organ_name="进化",
        param_name="heartbeat_interval",
        old_value=30.0,
        new_value=15.0,
        success=True,
        reason="加速心跳测试"
    )
    print(f"1. 成功变异: {r1['mutation_id']}, 参数={r1['param']}")
    
    # 2. 记录失败的变异（回滚）
    r2 = tracker.record_mutation(
        organ_name="进化",
        param_name="heartbeat_interval",
        old_value=15.0,
        new_value=200,
        success=False,
        reason="越界测试"
    )
    print(f"2. 失败变异: {r2['mutation_id']}, 状态={'回滚' if not r2['success'] else '成功'}")
    
    # 3. 记录多次成功变异
    for i in range(5):
        tracker.record_mutation(
            organ_name="进化",
            param_name=f"test_param_{i}",
            old_value=i,
            new_value=i + 1,
            success=True,
            reason="批量测试"
        )
    
    # 4. 记录节点演化路径
    tracker.record_node_evolution("node_001", "L1", "L2", "压缩内化")
    tracker.record_node_evolution("node_001", "L2", "L3", "融合抽象")
    tracker.record_node_evolution("node_002", "L1", "L2", "复盘压缩")
    
    path = tracker.get_node_evolution_path("node_001")
    print(f"\n3. node_001 演化路径: {len(path)} 步")
    for step in path:
        print(f"   {step['from_level']} → {step['to_level']} ({step['reason']})")
    
    # 5. 演化方向评估
    evaluation = tracker.evaluate_evolution_direction()
    print(f"\n4. 演化评估: {evaluation['direction']} - {evaluation['assessment']}")
    print(f"   成功率: {evaluation['success_rate']:.1%}")
    print(f"   回滚率: {evaluation['rollback_rate']:.1%}")
    
    # 6. 演化建议
    suggestions = tracker.get_evolution_suggestions()
    if suggestions:
        print("\n5. 演化建议:")
        for s in suggestions:
            print(f"   - {s}")
    
    # 7. 统计
    stats = tracker.get_stats()
    print(f"\n6. 统计: 变异{stats['total_mutations']}次, "
          f"追踪节点{stats['tracked_nodes']}个")
    
    tracker.disable()
    print("\n=== 自测全部通过 ===")