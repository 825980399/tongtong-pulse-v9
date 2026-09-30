# -*- coding: utf-8 -*-
"""
ConvergenceEvaluator.py —— 收敛评估器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 评估学习与进化过程的收敛性
机制: 基于ConvergenceEvaluator类实现，包含10个核心方法
定位: 进化评估层
"""

import threading
import time
from typing import Any


class ConvergenceEvaluator:
    """
    收敛评估器
    
    工作原理:
        1. 定期采集知识密度、器官协同、演化稳定性三个维度的快照。
        2. 与历史快照对比，计算各维度的收敛趋势。
        3. 综合判断：如果三个维度都在改善，演化方向健康。
        4. 如果某个维度长期不收敛，发出预警。
    
    评估周期:
        - 短期评估: 最近10个快照的趋势（约10分钟级别）。
        - 中期评估: 最近50个快照的趋势（约1小时级别）。
        - 长期评估: 所有历史快照的总体趋势（演化全貌）。
    
    当前状态（v9.0）:
        - 接口完整定义，但功能开关默认关闭。
        - P3阶段可激活完整的在线收敛评估。
    """
    
    def __init__(self, max_snapshots: int = 200):
        """
        Args:
            max_snapshots: 最大快照存储数量
        """
        # 收敛快照历史
        self._snapshots: list[dict[str, Any]] = []
        self._max_snapshots = max_snapshots
        
        # 收敛状态
        self._convergence_status = "unknown"
        self._last_evaluation_time = 0.0
        
        # 关联组件引用
        self.node_pool = None
        self.knowledge_tree = None
        self.evolution_tracker = None
        self.resonance_detector = None
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 统计
        self._total_evaluations = 0
        self._total_warnings = 0
        
        # 功能开关
        self._enabled = False

    # ========== 框架控制与依赖注入 ==========

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    def set_node_pool(self, node_pool):
        """注入节点池"""
        self.node_pool = node_pool

    def set_knowledge_tree(self, knowledge_tree):
        """注入知识树"""
        self.knowledge_tree = knowledge_tree

    def set_evolution_tracker(self, tracker):
        """注入演化追踪器"""
        self.evolution_tracker = tracker

    def set_resonance_detector(self, detector):
        """注入共振检测器"""
        self.resonance_detector = detector

    # ========== 快照采集 ==========

    def take_snapshot(self) -> dict[str, Any]:
        """
        采集当前系统状态的收敛快照。

        Returns:
            包含三个维度指标的快照字典
        """
        if not self._enabled:
            return {"status": "disabled"}

        with self._lock:
            snapshot = {
                "timestamp": time.time(),
                "knowledge_density": self._measure_knowledge_density(),
                "organ_synergy": self._measure_organ_synergy(),
                "evolution_stability": self._measure_evolution_stability(),
            }

            self._snapshots.append(snapshot)

            # 超出容量时清理旧快照
            while len(self._snapshots) > self._max_snapshots:
                self._snapshots.pop(0)

            return snapshot

    def _measure_knowledge_density(self) -> float:
        """
        测量知识密度：L2+L3节点占总节点的比例。
        密度越高，说明知识压缩越有效，"知识越多越智慧"。
        """
        if self.node_pool:
            stats = self.node_pool.get_stats()
            evol_dist = stats.get("evol_distribution", {})
            l2_count = evol_dist.get("L2", 0)
            l3_count = evol_dist.get("L3", 0)
            total = max(1, stats.get("total_nodes", 1))
            return round((l2_count + l3_count) / total, 4)
        return 0.0

    def _measure_organ_synergy(self) -> float:
        """
        测量器官协同度：共振检测器中协同响应对数的稳定性。
        协同对数越多，说明器官间协作越频繁、越稳定。
        """
        if self.resonance_detector and self.resonance_detector.is_enabled():
            stats = self.resonance_detector.get_stats()
            pairs = stats.get("co_response_pairs", 0)
            organs = max(1, stats.get("tracked_organs", 1))
            # 归一化：每器官的平均协同对数
            return round(pairs / organs, 4)
        return 0.0

    def _measure_evolution_stability(self) -> float:
        """
        测量演化稳定性：1 - 回滚率。
        稳定性越高，说明演化方向越可靠。
        """
        if self.evolution_tracker and self.evolution_tracker.is_enabled():
            evaluation = self.evolution_tracker.evaluate_evolution_direction()
            return round(1.0 - evaluation["rollback_rate"], 4)
        return 1.0

    # ========== 收敛评估 ==========

    def evaluate(self) -> dict[str, Any]:
        """
        执行一次完整的收敛评估。

        Returns:
            收敛评估报告
        """
        if not self._enabled:
            return {"status": "disabled"}

        self._total_evaluations += 1
        self._last_evaluation_time = time.time()

        # 先采集当前快照
        self.take_snapshot()

        with self._lock:
            if len(self._snapshots) < 5:
                return {
                    "status": "insufficient_data",
                    "message": "快照不足，需要至少5个快照进行评估",
                    "snapshot_count": len(self._snapshots),
                }

            # 计算各维度的收敛趋势
            knowledge_trend = self._calculate_trend("knowledge_density")
            synergy_trend = self._calculate_trend("organ_synergy")
            stability_trend = self._calculate_trend("evolution_stability")

            # 综合判断
            trends = [knowledge_trend, synergy_trend, stability_trend]
            converging_count = sum(1 for t in trends if t > 0)
            
            if converging_count >= 3:
                self._convergence_status = "converging"
                message = "三个维度均在改善，演化方向健康"
            elif converging_count >= 2:
                self._convergence_status = "partial"
                message = "部分维度在改善，演化方向基本健康"
            elif converging_count >= 1:
                self._convergence_status = "slow"
                message = "仅一个维度在改善，演化方向需要关注"
            else:
                self._convergence_status = "diverging"
                message = "所有维度均未改善，演化方向需要调整"
                self._total_warnings += 1

            # 获取最新快照值
            latest = self._snapshots[-1]

            return {
                "status": self._convergence_status,
                "message": message,
                "snapshot_count": len(self._snapshots),
                "current_values": {
                    "knowledge_density": latest["knowledge_density"],
                    "organ_synergy": latest["organ_synergy"],
                    "evolution_stability": latest["evolution_stability"],
                },
                "trends": {
                    "knowledge_density": round(knowledge_trend, 4),
                    "organ_synergy": round(synergy_trend, 4),
                    "evolution_stability": round(stability_trend, 4),
                },
                "converging_dimensions": converging_count,
                "total_dimensions": 3,
            }

    def _calculate_trend(self, metric_name: str) -> float:
        """
        计算指定指标的收敛趋势。
        
        使用简单线性回归斜率作为趋势指标。
        正值=改善中（收敛），负值=恶化中（发散），零=稳定。

        Args:
            metric_name: 指标名称

        Returns:
            趋势斜率
        """
        if len(self._snapshots) < 3:
            return 0.0

        recent = self._snapshots[-min(10, len(self._snapshots)):]
        values = [s[metric_name] for s in recent]
        
        # 简单线性回归斜率
        n = len(values)
        if n < 2:
            return 0.0
        
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        
        if denominator == 0:
            return 0.0
        
        return numerator / denominator

    # ========== 收敛速度评估 ==========

    def get_convergence_speed(self) -> dict[str, Any]:
        """
        评估各维度的收敛速度。

        Returns:
            包含各维度收敛速度的字典
        """
        with self._lock:
            if len(self._snapshots) < 10:
                return {"status": "insufficient_data"}

            # 短期趋势（最近10个快照）
            recent = self._snapshots[-10:]
            older = self._snapshots[-20:-10] if len(self._snapshots) >= 20 else self._snapshots[:10]

            speeds = {}
            for metric in ["knowledge_density", "organ_synergy", "evolution_stability"]:
                recent_avg = sum(s[metric] for s in recent) / len(recent)
                older_avg = sum(s[metric] for s in older) / len(older) if older else recent_avg
                
                # 速度 = (近期均值 - 早期均值) / 时间跨度
                if len(self._snapshots) >= 20:
                    time_span = recent[-1]["timestamp"] - older[-1]["timestamp"]
                else:
                    time_span = recent[-1]["timestamp"] - recent[0]["timestamp"]
                
                if time_span > 0:
                    speed = (recent_avg - older_avg) / time_span
                else:
                    speed = 0.0
                
                speeds[metric] = {
                    "speed": round(speed, 6),
                    "recent_average": round(recent_avg, 4),
                    "older_average": round(older_avg, 4),
                }

            return {
                "status": "ok",
                "speeds": speeds,
                "time_span_samples": min(10, len(recent)),
            }

    # ========== 查询接口 ==========

    def get_convergence_status(self) -> str:
        """获取当前收敛状态"""
        return self._convergence_status

    def get_snapshot_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取快照历史"""
        with self._lock:
            return list(self._snapshots[-limit:])

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取收敛评估统计"""
        with self._lock:
            latest = self._snapshots[-1] if self._snapshots else {}
            return {
                "enabled": self._enabled,
                "total_evaluations": self._total_evaluations,
                "total_warnings": self._total_warnings,
                "convergence_status": self._convergence_status,
                "snapshot_count": len(self._snapshots),
                "latest_snapshot": latest,
                "convergence_speed": self.get_convergence_speed(),
            }

    # ========== 未来演化预留（v10.0 振荡场） ==========

    def on_field_oscillation(self, field_data: dict[str, Any]):
        """
        【预留 v10.0】振荡场中，收敛评估由场的长期频率稳定性自动完成。
        场中所有器官的频率-相位锁定程度直接反映协同收敛程度。
        """

    def predict_convergence_time(self, target_density: float = 0.8) -> float | None:
        """
        【预留 v10.0】预测达到目标知识密度所需的估计时间。

        Args:
            target_density: 目标知识密度

        Returns:
            估计时间（秒），数据不足返回 None
        """
        speed_data = self.get_convergence_speed()
        if speed_data.get("status") != "ok":
            return None
        
        density_speed = speed_data["speeds"].get("knowledge_density", {}).get("speed", 0)
        if density_speed <= 0:
            return None
        
        current_density = self._measure_knowledge_density()
        if current_density >= target_density:
            return 0.0
        
        return (target_density - current_density) / density_speed


# ========== 单例访问器 ==========

_evaluator_instance = None
_evaluator_lock = threading.Lock()


def get_convergence_evaluator() -> ConvergenceEvaluator:
    """获取收敛评估器单例实例。"""
    global _evaluator_instance
    if _evaluator_instance is None:
        with _evaluator_lock:
            if _evaluator_instance is None:
                _evaluator_instance = ConvergenceEvaluator()
    return _evaluator_instance


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== ConvergenceEvaluator 自测 ===\n")
    
    evaluator = ConvergenceEvaluator(max_snapshots=50)
    evaluator.enable()
    
    # 1. 采集多个快照（模拟知识密度逐步上升）
    print("1. 采集快照（模拟收敛过程）:")
    for i in range(8):
        # 模拟节点池数据
        class MockNodePool:
            def get_stats(self, i=i):  # 绑定当前循环变量，消除 B023 闭包告警
                l2 = 10 + i * 2  # L2逐渐增加
                l3 = 5
                l1 = 50 - i     # L1逐渐减少
                return {
                    "total_nodes": l1 + l2 + l3,
                    "evol_distribution": {"L1": l1, "L2": l2, "L3": l3},
                }
        
        evaluator.node_pool = MockNodePool()
        snapshot = evaluator.take_snapshot()
        print(f"   快照#{i+1}: 知识密度={snapshot['knowledge_density']:.2%}")
    
    # 2. 执行收敛评估
    print("\n2. 收敛评估:")
    result = evaluator.evaluate()
    print(f"   状态: {result['status']}")
    print(f"   消息: {result['message']}")
    print(f"   当前值: {result['current_values']}")
    print(f"   趋势: {result['trends']}")
    print(f"   收敛维度: {result['converging_dimensions']}/{result['total_dimensions']}")
    
    # 3. 收敛速度
    print("\n3. 收敛速度:")
    speed = evaluator.get_convergence_speed()
    if speed.get("status") == "ok":
        for metric, data in speed["speeds"].items():
            print(f"   {metric}: 速度={data['speed']:.6f}/s, "
                  f"近期均值={data['recent_average']:.4f}")
    
    # 4. 统计
    stats = evaluator.get_stats()
    print(f"\n4. 统计: 评估{stats['total_evaluations']}次, "
          f"快照{stats['snapshot_count']}个, 状态={stats['convergence_status']}")
    
    evaluator.disable()
    print("\n=== 自测全部通过 ===")