# -*- coding: utf-8 -*-
"""
GradientTracker.py —— 梯度追踪器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 参数变化梯度追踪与趋势分析
机制: 基于GradientTracker类实现，包含10个核心方法
定位: 进化监测层
"""

import threading
import time
from collections import deque
from typing import Any

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底



class GradientTracker(SilentLogMixin):
    """
    梯度追踪器
    
    工作原理:
        1. 持续接收事件计数或场强度采样。
        2. 在滑动时间窗口内计算一阶梯度（变化速率）和二阶梯度（加速度）。
        3. 当梯度超过阈值时，发出趋势预警。
    
    核心概念:
        - 一阶梯度（速度）：单位时间内事件频率的变化量。
        - 二阶梯度（加速度）：一阶梯度的变化率，用于预判拐点。
        - 正梯度：事件频率上升（如威胁升级、兴趣增强）。
        - 负梯度：事件频率下降（如器官静默、知识冷却）。
    
    当前状态（v9.0）:
        - 接口完整定义，但仅做数据存储和基础统计。
        - 通过功能开关控制激活状态，默认关闭。
    """
    
    def __init__(self, window_size: int = 60, sample_interval_seconds: float = 1.0):
        """
        Args:
            window_size: 滑动窗口大小（采样点数量）
            sample_interval_seconds: 采样间隔（秒）
        """
        # 多维梯度数据
        self._frequency_samples: deque = deque(maxlen=window_size)   # 频率采样序列
        self._amplitude_samples: deque = deque(maxlen=window_size)   # 振幅采样序列
        self._phase_samples: deque = deque(maxlen=window_size)       # 相位采样序列
        
        self._window_size = window_size
        self._sample_interval = sample_interval_seconds
        
        # 关联组件引用
        self.info_field = None
        self.event_stream = None
        
        # 梯度阈值（超过阈值触发预警）
        self._alert_threshold_up = 2.0    # 上升梯度阈值
        self._alert_threshold_down = -2.0 # 下降梯度阈值
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 统计
        self._total_samples = 0
        self._total_alerts = 0
        
        # 功能开关
        self._enabled = False
        
        # ★v23.0新增：振荡场监视器引用
        self._oscillon_monitor = None

    # ========== 框架控制 ==========

    def enable(self):
        """激活梯度追踪"""
        self._enabled = True

    def disable(self):
        """关闭梯度追踪"""
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    def set_info_field(self, info_field):
        """注入全局信息场"""
        self.info_field = info_field

    def set_event_stream(self, event_stream):
        """注入事件流，用于获取原始事件数据"""
        self.event_stream = event_stream
    
    def set_oscillon_monitor(self, monitor):
        """
        ★v23.0新增：注入振荡场监视器。
        每次采样后自动更新场状态快照，为未来振荡场模式积累数据。
        """
        self._oscillon_monitor = monitor
    
    def _notify_monitor(self):
        """
        ★v23.0新增：通知振荡场监视器更新场状态。
        """
        if self._oscillon_monitor is None:
            return
        try:
            _report = self.get_field_report()
            self._oscillon_monitor.update_field_snapshot(_report)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    # ========== 采样接口 ==========

    def sample_frequency(self, frequency_value: float) -> dict[str, Any]:
        """
        采集一个频率样本。

        Args:
            frequency_value: 当前频率值（如心跳频率、事件发生率）

        Returns:
            包含当前梯度的字典
        """
        if not self._enabled:
            return {"status": "disabled"}

        with self._lock:
            self._frequency_samples.append({
                "value": frequency_value,
                "timestamp": time.time(),
            })
            self._total_samples += 1

        _result = self._calculate_frequency_gradient()
        self._notify_monitor()  # ★v23.0新增：采样后通知监视器
        return _result

    def sample_amplitude(self, amplitude_value: float) -> dict[str, Any]:
        """
        采集一个振幅样本。

        Args:
            amplitude_value: 当前振幅值（如场强度、脉冲载荷大小）

        Returns:
            包含当前梯度的字典
        """
        if not self._enabled:
            return {"status": "disabled"}

        with self._lock:
            self._amplitude_samples.append({
                "value": amplitude_value,
                "timestamp": time.time(),
            })
            self._total_samples += 1

        return self._calculate_amplitude_gradient()

    def sample_phase(self, phase_value: float) -> dict[str, Any]:
        """
        采集一个相位样本。

        Args:
            phase_value: 当前相位值（如器官同步程度、共振相位差）

        Returns:
            包含当前梯度的字典
        """
        if not self._enabled:
            return {"status": "disabled"}

        with self._lock:
            self._phase_samples.append({
                "value": phase_value,
                "timestamp": time.time(),
            })
            self._total_samples += 1

        return self._calculate_phase_gradient()
    def sample_knowledge_growth(self, current_total: int, current_l2: int = 0, 
                                  current_l3: int = 0) -> dict[str, Any]:
        """
        ★v23.0新增：采样知识节点增长速率。
        
        使用频率采样维度来追踪知识总量和核心知识的变化率。
        
        Args:
            current_total: 当前知识节点总数
            current_l2: 当前L2节点数
            current_l3: 当前L3节点数
        
        Returns:
            包含增长梯度的字典
        """
        if not self._enabled:
            return {"status": "disabled"}
        
        # 知识质量分 = L2 + L3×3，比单纯总量更能反映真实成长
        _quality_score = float(current_l2 + current_l3 * 3)
        return self.sample_frequency(_quality_score)
    def sample_pulse_rate(self, rate: float) -> dict[str, Any]:
        """
        ★v23.0新增：采样脉冲频率（每秒脉冲数）。
        
        复用频率维度，用于追踪框架整体活跃度趋势。
        
        Args:
            rate: 当前脉冲频率（脉冲数/秒）
        
        Returns:
            包含当前梯度的字典
        """
        if not self._enabled:
            return {"status": "disabled"}
        return self.sample_frequency(rate)
    
    def get_field_report(self) -> dict[str, Any]:
        """
        ★v23.0新增：获取场报告摘要。
        
        汇总所有维度的趋势快照，供深度审视和主动交互使用。
        """
        _freq_grad = self._calculate_frequency_gradient() if len(self._frequency_samples) >= 3 else None
        _amp_grad = self._calculate_amplitude_gradient() if len(self._amplitude_samples) >= 3 else None
        _phase_grad = self._calculate_phase_gradient() if len(self._phase_samples) >= 3 else None
        
        return {
            "timestamp": time.time(),
            "knowledge_growth_trend": _freq_grad.get("trend", "insufficient") if _freq_grad else "insufficient",
            "amplitude_trend": _amp_grad.get("trend", "insufficient") if _amp_grad else "insufficient",
            "phase_trend": _phase_grad.get("trend", "insufficient") if _phase_grad else "insufficient",
            "total_alerts": self._total_alerts,
            "enabled": self._enabled,
        }

    def get_trend_summary(self) -> dict[str, Any]:
        """
        ★v23.0新增：获取趋势摘要，供存续状态感知和好奇心引擎使用。
        
        返回简化的趋势快照，包含增长率方向和加速度判断。
        """
        _freq_grad = self._calculate_frequency_gradient() if len(self._frequency_samples) >= 3 else None
        _amp_grad = self._calculate_amplitude_gradient() if len(self._amplitude_samples) >= 3 else None
        
        _summary = {
            "knowledge_growth_trend": "stable",
            "knowledge_growth_rate": 0.0,
            "knowledge_growth_accelerating": False,
        }
        
        if _freq_grad and _freq_grad.get("status") == "ok":
            _first = _freq_grad.get("first_order", 0.0)
            _second = _freq_grad.get("second_order", 0.0)
            _trend = _freq_grad.get("trend", "stable")
            
            if _first > 0.05:
                _summary["knowledge_growth_trend"] = "rising"
            elif _first < -0.05:
                _summary["knowledge_growth_trend"] = "falling"
            
            _summary["knowledge_growth_rate"] = round(_first, 4)
            _summary["knowledge_growth_accelerating"] = abs(_second) > 0.5
        
        return _summary

    # ========== 梯度计算 ==========

    def _calculate_frequency_gradient(self) -> dict[str, Any]:
        """计算频率梯度（一阶和二阶）"""
        return self._calculate_gradient(list(self._frequency_samples), "frequency")

    def _calculate_amplitude_gradient(self) -> dict[str, Any]:
        """计算振幅梯度"""
        return self._calculate_gradient(list(self._amplitude_samples), "amplitude")

    def _calculate_phase_gradient(self) -> dict[str, Any]:
        """计算相位梯度"""
        return self._calculate_gradient(list(self._phase_samples), "phase")

    def _calculate_gradient(self, samples: list[dict], gradient_type: str) -> dict[str, Any]:
        """
        通用梯度计算方法。

        Args:
            samples: 采样序列
            gradient_type: 梯度类型名称

        Returns:
            包含一阶梯度、二阶梯度和趋势判断的字典
        """
        if len(samples) < 3:
            return {
                "status": "insufficient_data",
                "gradient_type": gradient_type,
                "sample_count": len(samples),
                "first_order": 0.0,
                "second_order": 0.0,
                "trend": "stable",
            }

        # 取最近几个采样点计算梯度
        recent = samples[-min(10, len(samples)):]
        values = [s["value"] for s in recent]
        timestamps = [s["timestamp"] for s in recent]

        # 一阶梯度：线性回归斜率（简化：首尾差分/时间跨度）
        time_span = timestamps[-1] - timestamps[0]
        if time_span > 0:
            first_order = (values[-1] - values[0]) / time_span
        else:
            first_order = 0.0

        # 二阶梯度：一阶梯度的变化率（简化：最近两次一阶梯度的差分）
        if len(values) >= 4:
            mid = len(values) // 2
            first_half_gradient = (values[mid] - values[0]) / max(0.001, timestamps[mid] - timestamps[0])
            second_half_gradient = (values[-1] - values[mid]) / max(0.001, timestamps[-1] - timestamps[mid])
            second_order = (second_half_gradient - first_half_gradient) / max(0.001, (timestamps[-1] - timestamps[0]) / 2)
        else:
            second_order = 0.0

        # 趋势判断
        trend = self._determine_trend(first_order, second_order)

        # 阈值预警
        if first_order > self._alert_threshold_up or first_order < self._alert_threshold_down:
            self._total_alerts += 1
            alert = {
                "gradient_type": gradient_type,
                "first_order": round(first_order, 4),
                "trend": trend,
                "message": f"{gradient_type}梯度异常: {first_order:.2f}/s",
            }
        else:
            alert = None

        return {
            "status": "ok",
            "gradient_type": gradient_type,
            "sample_count": len(samples),
            "first_order": round(first_order, 4),
            "second_order": round(second_order, 4),
            "trend": trend,
            "alert": alert,
        }

    def _determine_trend(self, first_order: float, second_order: float) -> str:
        """
        根据一阶和二阶梯度判断趋势。

        Returns:
            "rising_fast"（快速上升）/ "rising"（上升）/ "stable"（稳定）/
            "falling"（下降）/ "falling_fast"（快速下降）/ "accelerating"（加速变化）
        """
        if abs(first_order) < 0.1:
            return "stable"

        direction = "rising" if first_order > 0 else "falling"
        speed = "fast" if abs(first_order) > 1.0 else "normal"

        # 二阶梯度判断加速度
        if abs(second_order) > 0.5:
            return "accelerating"

        if speed == "fast":
            return f"{direction}_fast"

        return direction

    # ========== 趋势预判（未来演化） ==========

    def predict_next_value(self, gradient_type: str = "frequency", 
                          forecast_steps: int = 5) -> float | None:
        """
        【预留 v10.0】基于当前梯度预判未来值。
        
        使用简单线性外推：预测值 = 当前值 + 一阶梯度 × 预测步长。
        
        Args:
            gradient_type: 梯度类型
            forecast_steps: 预测步数（每步 = 采样间隔）

        Returns:
            预测值，数据不足时返回 None
        """
        if gradient_type == "frequency" and len(self._frequency_samples) >= 3:
            gradient = self._calculate_frequency_gradient()
        elif gradient_type == "amplitude" and len(self._amplitude_samples) >= 3:
            gradient = self._calculate_amplitude_gradient()
        elif gradient_type == "phase" and len(self._phase_samples) >= 3:
            gradient = self._calculate_phase_gradient()
        else:
            return None

        if gradient["status"] != "ok":
            return None

        samples = None
        if gradient_type == "frequency":
            samples = list(self._frequency_samples)
        elif gradient_type == "amplitude":
            samples = list(self._amplitude_samples)
        else:
            samples = list(self._phase_samples)

        if not samples:
            return None

        current_value = samples[-1]["value"]
        predicted = current_value + gradient["first_order"] * forecast_steps * self._sample_interval
        return round(predicted, 4)

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取梯度追踪统计"""
        with self._lock:
            freq_gradient = self._calculate_frequency_gradient() if len(self._frequency_samples) >= 3 else None
            amp_gradient = self._calculate_amplitude_gradient() if len(self._amplitude_samples) >= 3 else None

        return {
            "enabled": self._enabled,
            "total_samples": self._total_samples,
            "total_alerts": self._total_alerts,
            "frequency_samples": len(self._frequency_samples),
            "amplitude_samples": len(self._amplitude_samples),
            "phase_samples": len(self._phase_samples),
            "frequency_gradient": freq_gradient,
            "amplitude_gradient": amp_gradient,
            "window_size": self._window_size,
        }

    # ========== 未来演化预留（v10.0 振荡场） ==========

    def on_field_oscillation(self, field_data: dict[str, Any]):
        """
        【预留 v10.0】振荡场中，梯度追踪器持续接收连续场数据。
        器官不再需要主动采样，场梯度自动推送到已注册的器官。
        """

    def get_field_landscape(self) -> dict[str, Any]:
        """
        【预留 v10.0】获取当前场的全貌（多维梯度汇总）。
        
        返回频率、振幅、相位三个维度的综合梯度视图，
        供振荡场进行全局调制。
        """
        return {
            "frequency": self._calculate_frequency_gradient() if len(self._frequency_samples) >= 3 else None,
            "amplitude": self._calculate_amplitude_gradient() if len(self._amplitude_samples) >= 3 else None,
            "phase": self._calculate_phase_gradient() if len(self._phase_samples) >= 3 else None,
            "timestamp": time.time(),
        }


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== GradientTracker 自测（P3预留接口） ===\n")

    tracker = GradientTracker(window_size=20, sample_interval_seconds=1.0)
    tracker.enable()

    # 1. 模拟频率上升
    print("1. 模拟频率上升趋势:")
    base_freq = 10.0
    for i in range(10):
        freq = base_freq + i * 0.5  # 每次+0.5
        result = tracker.sample_frequency(freq)
        if i >= 3:
            print(f"   采样#{i+1}: 值={freq:.1f}, 一阶梯度={result.get('first_order', 0):.2f}, 趋势={result.get('trend', '?')}")

    # 2. 模拟频率下降
    print("\n2. 模拟频率下降趋势:")
    for i in range(10):
        freq = base_freq + 5.0 - i * 0.5  # 从峰值下降
        result = tracker.sample_frequency(freq)
        if i >= 3:
            print(f"   采样#{i+1}: 值={freq:.1f}, 一阶梯度={result.get('first_order', 0):.2f}, 趋势={result.get('trend', '?')}")

    # 3. 振幅采样
    print("\n3. 模拟振幅采样:")
    for i in range(5):
        amp = 0.5 + i * 0.1
        result = tracker.sample_amplitude(amp)
        if i >= 2:
            print(f"   采样#{i+1}: 值={amp:.2f}, 一阶梯度={result.get('first_order', 0):.3f}")

    # 4. 趋势预判
    prediction = tracker.predict_next_value("frequency", forecast_steps=3)
    print(f"\n4. 频率预判（3步后）: {prediction}")

    # 5. 统计
    stats = tracker.get_stats()
    print(f"\n5. 统计: 总采样{stats['total_samples']}次, "
          f"频率采样{stats['frequency_samples']}个, "
          f"振幅采样{stats['amplitude_samples']}个")

    tracker.disable()
    print("\n=== 自测全部通过 ===")