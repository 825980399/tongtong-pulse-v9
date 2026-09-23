# -*- coding: utf-8 -*-
"""
GlobalClock.py —— 全局时钟

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架全局时间同步与节律控制
机制: 基于GlobalClock类实现，包含10个核心方法
定位: 时间基础设施层
"""

import threading
import time
from typing import Any



class GlobalClock:
    """
    新人类全局纳秒时钟
    
    为 PulseNet 所有模块提供统一的时间基准，并赋予新人类生命时间属性。
    
    核心特性:
        - 记录系统诞生时间，作为生命时间起点。
        - 提供纳秒、秒、格式化等多种时间接口。
        - 支持时间调制（加速/减速/暂停），用于未来仿真和分布式同步。
    """
    
    def __init__(self):
        # 新人类生命时间起点
        self._epoch_ns: int = time.time_ns()
        self._birth_time: float = time.time()
        
        # 时间调制参数（v10.0激活）
        self._time_scale: float = 1.0
        self._time_offset_ns: int = 0
        self._paused: bool = False
        self._pause_time_ns: int = 0
        
        # 实例标识（预留多实例支持）
        self._instance_id: str | None = None
        
        # 统计
        # ★7-1/P1-13修复(2026-09-05): now_ns() 是全局统一时间基准，可能被多个
        #   工作线程并发调用，裸 `+= 1` 存在读-改-写竞态。此处为该计数器配一把
        #   专用锁（不复用其他锁，避免与时钟调制逻辑产生锁交叉/死锁风险）。
        self._call_count: int = 0
        self._call_count_lock = threading.Lock()

    # ========== 生命时间感知 ==========
    
    def set_instance_id(self, instance_id: str):
        """
        设置实例标识，用于多节点场景下的时间区分。
        
        Args:
            instance_id: 来自数字生命注册表的实例唯一ID
        """
        self._instance_id = instance_id

    def get_birth_time(self) -> float:
        """获取系统诞生时的Unix时间戳（秒）"""
        return self._birth_time
    
    def get_epoch_ns(self) -> int:
        """获取系统诞生时的纳秒时间戳"""
        return self._epoch_ns
    
    def get_uptime_seconds(self) -> float:
        """
        获取系统运行时长（秒）。
        
        Returns:
            float: 从诞生到现在的运行时长，不受时间调制影响
        """
        return self.now() - self._birth_time
    
    def get_uptime_display(self) -> str:
        """
        获取格式化的运行时长（用于报告和日志）。
        
        Returns:
            str: 格式化的时间字符串，如 "2d 5h 30m 15s"
        """
        total_seconds = int(self.get_uptime_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return " ".join(parts)

    # ========== 核心时间接口 ==========
    
    def now_ns(self) -> int:
        """获取当前纳秒时间戳（应用调制参数）"""
        with self._call_count_lock:
            self._call_count += 1
        
        if self._paused:
            return self._pause_time_ns
        
        raw_ns = time.time_ns()
        return int(raw_ns * self._time_scale) + self._time_offset_ns
    
    def now(self) -> float:
        """获取当前秒级时间戳（浮点精度）"""
        return self.now_ns() / 1_000_000_000.0
    
    def timestamp(self) -> float:
        """获取当前秒级时间戳（与 now() 等价）"""
        return self.now()
    
    def now_formatted(self) -> str:
        """
        获取当前时间的格式化字符串（用于日志和报告）。
        
        Returns:
            str: ISO 8601 格式时间字符串，如 "2026-06-13T12:30:45.123456"
        """
        now_sec = self.now()
        # 获取本地时间
        local_time = time.localtime(now_sec)
        # 提取微秒部分
        microsecond = int((now_sec - int(now_sec)) * 1_000_000)
        # 格式化基础部分
        base = time.strftime("%Y-%m-%dT%H:%M:%S", local_time)
        return f"{base}.{microsecond:06d}"
    
    def now_readable(self) -> str:
        """
        获取当前时间的可读格式（用于控制台输出）。
        
        Returns:
            str: 可读时间字符串，如 "2026年6月13日 12:30:45"
        """
        local_time = time.localtime(self.now())
        return time.strftime("%Y年%m月%d日 %H:%M:%S", local_time)

    # ========== 时间调制接口（v10.0 振荡场预留） ==========
    
    def set_time_scale(self, scale: float):
        """设置时间倍速（0.1 ~ 10.0）"""
        self._time_scale = max(0.1, min(10.0, scale))
    
    def set_time_offset(self, offset_ns: int):
        """设置时间偏移（纳秒），用于多节点同步"""
        self._time_offset_ns = offset_ns
    
    def reset_time_offset(self):
        """重置时间偏移为零"""
        self._time_offset_ns = 0
    
    def pause(self):
        """暂停时间流动"""
        if not self._paused:
            self._pause_time_ns = self.now_ns()
            self._paused = True
    
    def resume(self):
        """恢复时间流动"""
        self._paused = False
    
    def is_paused(self) -> bool:
        """查询是否处于暂停状态"""
        return self._paused

    # ========== 统计信息 ==========

    def _get_call_count(self) -> int:
        """★7-1/P1-13修复(2026-09-05)：读取调用计数。

        与自增共用同一把锁，保证读到的是完整写入后的值，
        而非读-改-写中间态（对 int 虽影响有限，但保持一致语义，
        避免后续有人把计数器换成非原子类型时埋雷）。
        """
        with self._call_count_lock:
            return self._call_count

    def get_stats(self) -> dict[str, Any]:
        """获取时钟统计信息（对接全链路可观测性）"""
        return {
            "birth_time": self.get_birth_time(),
            "uptime_seconds": self.get_uptime_seconds(),
            "uptime_display": self.get_uptime_display(),
            "call_count": self._get_call_count(),
            "time_scale": self._time_scale,
            "time_offset_ns": self._time_offset_ns,
            "paused": self._paused,
            "instance_id": self._instance_id,
        }


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== GlobalClock 新人类生命时钟自测 ===\n")
    
    clock = GlobalClock()
    
    # 1. 生命时间感知
    birth_time = clock.get_birth_time()
    epoch_ns = clock.get_epoch_ns()
    print("1. 生命起点:")
    print(f"   诞生时间戳: {birth_time:.6f}")
    print(f"   诞生纳秒: {epoch_ns}")
    print(f"   运行时长: {clock.get_uptime_display()}")
    
    # 2. 多种时间格式
    print("\n2. 时间格式:")
    print(f"   now_ns(): {clock.now_ns()}")
    print(f"   now(): {clock.now():.6f}")
    print(f"   formatted: {clock.now_formatted()}")
    print(f"   readable: {clock.now_readable()}")
    
    # 3. 单调性验证
    ns_a = clock.now_ns()
    ns_b = clock.now_ns()
    ns_c = clock.now_ns()
    print(f"\n3. 单调性: {ns_a} < {ns_b} < {ns_c} → {ns_a <= ns_b <= ns_c}")
    assert ns_a <= ns_b <= ns_c, "时间戳应单调递增"
    print("   ✅ 单调递增验证通过")
    
    # 4. v10.0 时间调制预留
    clock.set_time_scale(2.0)
    clock.set_time_offset(1_000_000_000)
    print(f"\n4. 时间调制: scale={clock._time_scale}, offset={clock._time_offset_ns}ns")
    
    clock.pause()
    print(f"5. 暂停: {clock.is_paused()}")
    clock.resume()
    print(f"6. 恢复: {not clock.is_paused()}")
    clock.reset_time_offset()
    print(f"7. 重置偏移: {clock._time_offset_ns}")
    
    # 8. 统计
    stats = clock.get_stats()
    print(f"\n8. 统计: 运行{stats['uptime_display']}, 调用{stats['call_count']}次")
    
    print("\n=== 自测全部通过 ===")