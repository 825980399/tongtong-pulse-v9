# -*- coding: utf-8 -*-
"""
EventStream.py —— 事件流

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 全局事件流总线与订阅发布机制
机制: 基于EventStream类实现，包含10个核心方法
定位: 事件基础设施层
"""

import threading
import time
from typing import Any



class EventStream:
    """
    连续事件流
    
    工作原理:
        1. 器官通过 push() 将脉冲记录推入事件流。
        2. 查询时通过 query_window() 获取指定时间范围内的事件。
        3. 在振荡场模式下，事件流自动计算频率梯度，为器官共振提供连续信号。
    
    当前状态（v9.0）:
        - 经典脉冲模式，仅做脉冲历史存储。
        - 通过功能开关控制是否记录事件，默认关闭以减少内存开销。
    """
    
    def __init__(self, max_events: int = 10000, window_size_seconds: float = 300.0):
        """
        Args:
            max_events: 最大缓存事件数（超出自动淘汰旧事件）
            window_size_seconds: 默认时间窗口大小（秒）
        """
        self._events: list[dict[str, Any]] = []
        self._max_events = max_events
        self._default_window = window_size_seconds
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 全局时钟引用（v10.0注入）
        self.global_clock = None
        
        # 统计
        self._total_pushed = 0
        self._total_queried = 0
        
        # 功能开关
        self._enabled = False

    # ========== 框架控制 ==========

    def enable(self):
        """激活事件流记录"""
        self._enabled = True

    def disable(self):
        """关闭事件流记录"""
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    def set_global_clock(self, clock):
        """
        【预留 v10.0】注入全局时钟。
        振荡场模式下，事件流需要与全局时钟同步，支持时间调制。
        """
        self.global_clock = clock

    # ========== 事件记录 ==========

    def push(self, event: dict[str, Any]) -> bool:
        """
        将一条事件推入事件流。

        Args:
            event: 事件字典，应包含 event_type、source_organ、timestamp_ns 等脉冲字段

        Returns:
            是否成功推入
        """
        if not self._enabled:
            return False

        with self._lock:
            # 确保事件有时间戳
            if "timestamp" not in event:
                event["timestamp"] = time.time()
            if "timestamp_ns" not in event:
                event["timestamp_ns"] = time.time_ns()

            self._events.append(event)
            self._total_pushed += 1

            # 超出容量时淘汰最旧的事件
            while len(self._events) > self._max_events:
                self._events.pop(0)

        return True

    def push_pulse(self, pulse: dict[str, Any]) -> bool:
        """
        将一条脉冲记录推入事件流（便捷方法）。
        
        自动提取脉冲的关键字段，适配 v9.0 经典脉冲格式。

        Args:
            pulse: 标准 Pulse 字典

        Returns:
            是否成功推入
        """
        if not self._enabled:
            return False

        event_record = {
            "event_type": pulse.get("event_type", "unknown"),
            "source_organ": pulse.get("source_organ", "unknown"),
            "priority": pulse.get("priority", 5),
            "timestamp_ns": pulse.get("timestamp_ns", time.time_ns()),
            "timestamp": pulse.get("timestamp_ns", time.time_ns()) / 1_000_000_000.0,
            "payload_keys": list(pulse.get("payload", {}).keys()) if pulse.get("payload") else [],
        }
        return self.push(event_record)

    # ========== 时间窗口查询 ==========

    def query_window(self, window_seconds: float | None = None, 
                     event_type: str | None = None,
                     source_organ: str | None = None,
                     limit: int = 100) -> list[dict[str, Any]]:
        """
        查询指定时间窗口内的事件。

        Args:
            window_seconds: 时间窗口大小（秒），None 则使用默认窗口
            event_type: 事件类型过滤（可选）
            source_organ: 来源器官过滤（可选）
            limit: 返回上限

        Returns:
            匹配的事件列表（按时间倒序）
        """
        self._total_queried += 1
        window = window_seconds or self._default_window
        now = time.time()
        threshold = now - window

        with self._lock:
            results = []
            for event in reversed(self._events):
                event_time = event.get("timestamp", 0)
                if event_time < threshold:
                    continue
                if event_type and event.get("event_type") != event_type:
                    continue
                if source_organ and event.get("source_organ") != source_organ:
                    continue
                results.append(event)
                if len(results) >= limit:
                    break

        return results

    def query_recent(self, count: int = 20, 
                     event_type: str | None = None) -> list[dict[str, Any]]:
        """
        查询最近的事件（便捷方法）。

        Args:
            count: 返回数量
            event_type: 事件类型过滤（可选）

        Returns:
            最近的事件列表
        """
        with self._lock:
            results = []
            for event in reversed(self._events):
                if event_type and event.get("event_type") != event_type:
                    continue
                results.append(event)
                if len(results) >= count:
                    break
        return results

    def query_by_organ(self, organ_name: str, count: int = 50) -> list[dict[str, Any]]:
        """
        查询指定器官发出的事件。

        Args:
            organ_name: 器官名称
            count: 返回数量

        Returns:
            匹配的事件列表
        """
        return self.query_window(
            window_seconds=self._default_window,
            source_organ=organ_name,
            limit=count
        )

    # ========== 事件统计 ==========

    def get_event_type_distribution(self, window_seconds: float | None = None) -> dict[str, int]:
        """
        获取时间窗口内的事件类型分布。

        Args:
            window_seconds: 时间窗口，None 使用默认窗口

        Returns:
            {event_type: count} 字典
        """
        events = self.query_window(window_seconds=window_seconds)
        distribution = {}
        for event in events:
            etype = event.get("event_type", "unknown")
            distribution[etype] = distribution.get(etype, 0) + 1
        return distribution

    def get_organ_activity_summary(self, window_seconds: float | None = None) -> dict[str, int]:
        """
        获取时间窗口内各器官的活动次数。

        Args:
            window_seconds: 时间窗口，None 使用默认窗口

        Returns:
            {organ_name: count} 字典
        """
        events = self.query_window(window_seconds=window_seconds)
        activity = {}
        for event in events:
            organ = event.get("source_organ", "unknown")
            activity[organ] = activity.get(organ, 0) + 1
        return activity

    # ========== 清理 ==========

    def clear(self):
        """清空所有事件"""
        with self._lock:
            self._events.clear()

    def purge_old_events(self, max_age_seconds: float = 3600.0):
        """
        清理超过指定时长的事件。

        Args:
            max_age_seconds: 最大保留时间（秒）
        """
        now = time.time()
        threshold = now - max_age_seconds
        with self._lock:
            self._events = [e for e in self._events if e.get("timestamp", 0) >= threshold]

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取事件流统计"""
        with self._lock:
            return {
                "enabled": self._enabled,
                "total_pushed": self._total_pushed,
                "total_queried": self._total_queried,
                "current_size": len(self._events),
                "max_size": self._max_events,
                "default_window_seconds": self._default_window,
                "oldest_event_time": self._events[0].get("timestamp", 0) if self._events else 0,
                "newest_event_time": self._events[-1].get("timestamp", 0) if self._events else 0,
            }

    # ========== 未来演化预留（v10.0 振荡场） ==========

    def get_frequency_gradient(self, window_seconds: float | None = None) -> dict[str, float]:
        """
        【预留 v10.0】计算时间窗口内的事件频率梯度。
        
        振荡场中，事件频率的变化率（梯度）是器官共振的基础。
        频率上升的器官吸引更多共振，频率下降的器官逐渐静默。
        
        Args:
            window_seconds: 时间窗口，None 使用默认窗口
            
        Returns:
            {event_type: gradient} 字典，正值=频率上升，负值=下降
        """
        # P3阶段返回空字典，v10.0实现完整梯度计算
        return {}

    def detect_bursts(self, threshold: float = 3.0, window_seconds: float | None = None) -> list[dict[str, Any]]:
        """
        【预留 v10.0】检测事件爆发（短时间内大量同类型事件）。
        
        Args:
            threshold: 爆发阈值（标准差倍数）
            window_seconds: 检测窗口
            
        Returns:
            爆发事件列表
        """
        # P3阶段返回空列表，v10.0实现完整爆发检测
        return []


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== EventStream 自测（P3预留接口） ===\n")
    
    stream = EventStream(max_events=100, window_size_seconds=60)
    stream.enable()
    
    # 1. 推入事件
    for i in range(5):
        stream.push({
            "event_type": "heart.beat",
            "source_organ": "心脏",
            "priority": 7,
            "timestamp": time.time(),
            "timestamp_ns": time.time_ns(),
        })
    
    for i in range(3):
        stream.push({
            "event_type": "knowledge.written",
            "source_organ": "胃",
            "priority": 5,
            "timestamp": time.time(),
            "timestamp_ns": time.time_ns(),
        })
    
    print(f"1. 推入事件: {stream._total_pushed} 条")
    
    # 2. 时间窗口查询
    recent = stream.query_recent(10)
    print(f"2. 最近10条: {len(recent)} 条")
    
    heart_events = stream.query_by_organ("心脏")
    print(f"3. 心脏事件: {len(heart_events)} 条")
    
    # 4. 事件分布
    dist = stream.get_event_type_distribution()
    print(f"4. 事件分布: {dist}")
    
    # 5. 器官活跃度
    activity = stream.get_organ_activity_summary()
    print(f"5. 器官活跃度: {activity}")
    
    # 6. 脉冲便捷推入
    test_pulse = {
        "event_type": "reflection.insight",
        "source_organ": "前额叶",
        "priority": 4,
        "timestamp_ns": time.time_ns(),
        "payload": {"domain": "身份", "issue_types": ["identity_erosion"]},
    }
    stream.push_pulse(test_pulse)
    print(f"6. 脉冲推入后总数: {len(stream._events)}")
    
    # 7. 统计
    stats = stream.get_stats()
    print(f"7. 统计: 推送{stats['total_pushed']}次 查询{stats['total_queried']}次 "
          f"当前{stats['current_size']}条")
    
    # 8. 清理
    stream.clear()
    print(f"8. 清空后: {len(stream._events)} 条")
    
    stream.disable()
    print("\n=== 自测全部通过 ===")