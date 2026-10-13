# -*- coding: utf-8 -*-
"""
ResonanceDetector.py —— 共振检测器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 检测器官间信号共振与同步
机制: 基于ResonanceDetector类实现，包含9个核心方法
定位: 共振感知层
"""

import threading
import time
from typing import Any

# 权重四元组默认值（202批 · 三层预留架构 第二层：权重表示层）
# encoding_type 默认与 pulse_types.DEFAULT_SPIKE_TYPE 对齐（同为 'rate'）
DEFAULT_WEIGHT = 1.0
DEFAULT_DELAY = 0.0
DEFAULT_ENCODING_TYPE = 'rate'


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

        # 类脑架构三层预留 · 第二层（权重表示层）四元组映射
        #   键 = (pre_id, post_id)；值 = (weight, delay, encoding_type)
        #   默认空字典 —— 纯数据结构，不接入任何脉冲流，零行为变化
        self._weight_map: dict[tuple[str, str], tuple[float, float, str]] = {}

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

    def get_weight(self, pre_id: str, post_id: str) -> tuple[float, float, str]:
        """查询突触权重四元组。

        Args:
            pre_id:  突触前 ID（神经元 / 器官）
            post_id: 突触后 ID（神经元 / 器官）

        Returns:
            (weight, delay, encoding_type)；未登记时返回默认语义三元组
            (DEFAULT_WEIGHT, DEFAULT_DELAY, DEFAULT_ENCODING_TYPE)。
        """
        with self._lock:
            return self._weight_map.get(
                (pre_id, post_id),
                (DEFAULT_WEIGHT, DEFAULT_DELAY, DEFAULT_ENCODING_TYPE),
            )

    def set_weight(self, pre_id: str, post_id: str, weight: float,
                   delay: float = DEFAULT_DELAY,
                   encoding_type: str = DEFAULT_ENCODING_TYPE) -> None:
        """设置（或覆盖）突触权重四元组。

        Args:
            pre_id:        突触前 ID
            post_id:       突触后 ID
            weight:        突触权重
            delay:         突触延迟（秒），默认 DEFAULT_DELAY=0.0
            encoding_type: 编码类型，默认 DEFAULT_ENCODING_TYPE='rate'
        """
        with self._lock:
            self._weight_map[(pre_id, post_id)] = (weight, delay, encoding_type)

    def update_weight(self, pre_id: str, post_id: str, delta: float,
                     encoding_type: str = DEFAULT_ENCODING_TYPE) -> float:
        """按增量更新突触权重（STDP 预留，本批不含学习规则）。

        未登记的突触以 DEFAULT_WEIGHT 为起点累加；delay 保持不变，encoding_type
        由调用方按映射口径写入（'real' / 'simplified'）。

        Args:
            pre_id:         突触前 ID
            post_id:        突触后 ID
            delta:          权重增量
            encoding_type:  映射口径（'real'=真实 target_organ，'simplified'=回退 event_type）

        Returns:
            更新后的新权重。
        """
        # ★不在持锁内调用 get_weight：self._lock 是 threading.Lock（非可重入），
        #   嵌套获取会自死锁 —— 此处直接内联查表。
        with self._lock:
            weight, delay, _enc = self._weight_map.get(
                (pre_id, post_id),
                (DEFAULT_WEIGHT, DEFAULT_DELAY, DEFAULT_ENCODING_TYPE),
            )
            new_weight = weight + delta
            # 207批·刀2：写入调用方声明的映射口径（覆盖默认 enc）
            self._weight_map[(pre_id, post_id)] = (new_weight, delay, encoding_type)
            return new_weight

    def get_mapping_distribution(self) -> dict[str, Any]:
        """207批·刀2：统计 weight_map 中映射口径分布（real / simplified）。

        仅观测用；encoding_type 第三槽记录每条突触的映射口径。
        """
        with self._lock:
            _real = 0
            _simplified = 0
            for _w, _d, _enc in self._weight_map.values():
                if _enc == "real":
                    _real += 1
                elif _enc == "simplified":
                    _simplified += 1
            return {
                "synapses": len(self._weight_map),
                "real": _real,
                "simplified": _simplified,
            }

    def get_weight_stats(self) -> dict[str, Any]:
        """获取权重映射统计（只读观测）。"""
        with self._lock:
            return {
                "synapses": len(self._weight_map),
                "total_weight": sum(w for w, _d, _e in self._weight_map.values()),
            }

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
# ===== 204批·刀3：D#2 封存解封 + 模块级单例（供 PulseCore.emit 接入实际脉冲流）=====
_detector_instance = None

def get_detector():
    """模块级单例：解封后供 PulseCore.emit 调用，跨 emit 共享同一 _weight_map。"""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = ResonanceDetector()
    return _detector_instance


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