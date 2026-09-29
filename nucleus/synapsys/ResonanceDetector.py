# -*- coding: utf-8 -*-
"""
ResonanceDetector.py —— 共振检测器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 检测器官间信号共振与同步
机制: 基于ResonanceDetector类实现，包含9个核心方法
定位: 共振感知层
"""

import threading
import time
from typing import Any



class ResonanceDetector:
    """
    器官共振检测器
    
    工作原理:
        1. 监听 InfoField 中的脉冲匹配事件。
        2. 记录每个器官对各类事件的响应频率和强度。
        3. 分析器官间的协同模式——哪些器官经常一起响应同类事件。
    
    当前状态（v9.0）:
        - 接口完整定义，但仅做数据记录与统计。
        - 通过功能开关控制激活状态，默认关闭。
    """
    
    def __init__(self):
        # 器官共振记录: organ_name → {event_type: {"count": N, "last_seen": timestamp}}
        self._resonance_records: dict[str, dict[str, dict[str, Any]]] = {}
        
        # 器官协同矩阵: (organ_a, organ_b) → 共同响应次数
        self._co_response_matrix: dict[tuple, int] = {}
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 统计
        self._total_detections = 0
        
        # 功能开关
        self._enabled = False

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    def record_resonance(self, organ_name: str, event_type: str):
        """
        记录一次器官共振事件。

        Args:
            organ_name: 响应器官名称
            event_type: 触发共振的事件类型
        """
        if not self._enabled:
            return

        with self._lock:
            if organ_name not in self._resonance_records:
                self._resonance_records[organ_name] = {}
            
            organ_records = self._resonance_records[organ_name]
            if event_type not in organ_records:
                organ_records[event_type] = {"count": 0, "last_seen": 0.0}
            
            organ_records[event_type]["count"] += 1
            organ_records[event_type]["last_seen"] = time.time()
            self._total_detections += 1

    def record_co_response(self, organs: list[str], event_type: str):
        """
        记录一组器官对同一事件的共同响应。

        Args:
            organs: 共同响应的器官名称列表
            event_type: 触发共振的事件类型
        """
        if not self._enabled or len(organs) < 2:
            return

        with self._lock:
            # 更新协同矩阵
            for i in range(len(organs)):
                for j in range(i + 1, len(organs)):
                    pair = tuple(sorted([organs[i], organs[j]]))
                    self._co_response_matrix[pair] = self._co_response_matrix.get(pair, 0) + 1

    def get_organ_resonance_stats(self, organ_name: str) -> dict[str, Any]:
        """
        获取指定器官的共振统计。

        Args:
            organ_name: 器官名称

        Returns:
            该器官的共振统计字典
        """
        with self._lock:
            records = self._resonance_records.get(organ_name, {})
            return {
                "organ": organ_name,
                "total_resonances": sum(r["count"] for r in records.values()),
                "event_types": len(records),
                "details": records,
            }

    def get_top_co_responses(self, top_k: int = 10) -> list[dict[str, Any]]:
        """
        获取协同共振最频繁的器官对。

        Args:
            top_k: 返回数量

        Returns:
            协同共振排名列表
        """
        with self._lock:
            sorted_pairs = sorted(
                self._co_response_matrix.items(),
                key=lambda x: x[1],
                reverse=True
            )
            return [
                {"organs": list(pair), "count": count}
                for pair, count in sorted_pairs[:top_k]
            ]

    def get_stats(self) -> dict[str, Any]:
        """获取检测器统计"""
        with self._lock:
            return {
                "enabled": self._enabled,
                "total_detections": self._total_detections,
                "tracked_organs": len(self._resonance_records),
                "co_response_pairs": len(self._co_response_matrix),
            }


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== ResonanceDetector 自测 ===\n")
    
    detector = ResonanceDetector()
    detector.enable()
    
    # 1. 记录共振
    detector.record_resonance("心脏", "heart.beat")
    detector.record_resonance("血管", "heart.beat")
    detector.record_resonance("前额叶", "heart.beat")
    detector.record_resonance("胃", "digest.knowledge")
    
    # 2. 记录协同响应
    detector.record_co_response(["心脏", "血管", "前额叶"], "heart.beat")
    
    # 3. 查询统计
    heart_stats = detector.get_organ_resonance_stats("心脏")
    print(f"1. 心脏共振统计: 总响应{heart_stats['total_resonances']}次, 事件类型{heart_stats['event_types']}种")
    
    # 4. 协同对
    top_pairs = detector.get_top_co_responses(5)
    print(f"2. 最佳协同对: {top_pairs}")
    
    stats = detector.get_stats()
    print(f"3. 检测器统计: 总检测{stats['total_detections']}次, 追踪器官{stats['tracked_organs']}个")
    
    detector.disable()
    print("\n=== 自测全部通过 ===")