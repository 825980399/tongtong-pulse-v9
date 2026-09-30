# -*- coding: utf-8 -*-
"""
ReasoningFeedbackLoop.py —— 推理反馈循环

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 推理结果的反馈与自我修正
机制: 基于ReasoningFeedbackLoop类实现，包含9个核心方法
定位: 推理学习层
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, ClassVar
from nucleus._silent_except import silent_exc



class ReasoningFeedbackLoop:
    """推理质量反馈回流引擎。"""

    # 参数调整步长（保守调整，避免震荡）
    _ADJUST_STEP: ClassVar[dict[str, float]] = {
        "top_k": 5.0,              # 检索候选数调整步长
        "resonance_threshold": 0.05,  # 共振阈值调整步长
        "confidence_threshold": 0.05,  # 置信度阈值调整步长
        "reasoning_depth": 1.0,    # 推理深度调整步长
    }

    # 参数安全范围（防止过度调整）
    _PARAM_RANGES: ClassVar[dict[str, tuple[float, float]]] = {
        "top_k": (5.0, 100.0),
        "resonance_threshold": (0.2, 0.9),
        "confidence_threshold": (0.3, 0.9),
        "reasoning_depth": (1.0, 5.0),
    }

    def __init__(self, window_size: int = 50, min_interval: float = 60.0,
                 success_threshold: float = 0.7,
                 enabled: bool = True) -> None:
        """
        Args:
            window_size: 滑动窗口大小（最近N次推理结果）
            min_interval: 两次参数优化间的最小间隔秒数（压力均衡）
            success_threshold: 成功率阈值（高于此值可降低资源，低于此值需增加资源）
            enabled: 开关
        """
        self._window_size = max(10, int(window_size))
        self._min_interval = max(10.0, float(min_interval))
        self._success_threshold = float(success_threshold)
        self._enabled = enabled

        # 滑动窗口：最近N次推理质量记录
        self._quality_window: deque[dict[str, Any]] = deque(maxlen=self._window_size)

        # 当前推理参数（建议值）
        self._current_params: dict[str, float] = {
            "top_k": 20.0,
            "resonance_threshold": 0.5,
            "confidence_threshold": 0.6,
            "reasoning_depth": 2.0,
        }

        # 优化历史
        self._optimization_history: list[dict[str, Any]] = []
        self._last_optimization_time = 0.0
        self._stats = {"total_records": 0, "optimizations": 0, "param_changes": 0}

    # ========== 核心：质量记录 ==========

    def record_quality(self, method: str, relevance_score: float,
                       success: bool, latency_ms: float = 0.0,
                       metadata: dict[str, Any] | None = None) -> None:
        """记录一次推理的质量评分。

        Args:
            method: 推理方法（rule_reason/multi_step/deep_think等）
            relevance_score: 输出相关性评分（0-1）
            success: 是否成功（输出验证通过/用户认可）
            latency_ms: 推理耗时毫秒
            metadata: 附加信息（问题类型/复杂度等）
        """
        if not self._enabled:
            return
        _record = {
            "method": method,
            "relevance_score": float(relevance_score),
            "success": bool(success),
            "latency_ms": float(latency_ms),
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        self._quality_window.append(_record)
        self._stats["total_records"] += 1

        # ★v25.1修复: 记录质量后自动触发参数优化，形成完整反馈闭环
        # （should_optimize内部有样本数≥10和最小间隔60秒的节流，不会频繁触发）
        try:
            _opt_result = self.optimize_params()
            if _opt_result:
                self._stats["optimizations_done"] = self._stats.get("optimizations_done", 0) + 1
                from nucleus.logger import get_module_logger as _gml_rfl
                _logging_rfl = _gml_rfl('ReasoningFeedbackLoop')
                _logging_rfl.info(
                    f"[推理反馈闭环] 参数自动优化: "
                    f"成功率={_opt_result.get('success_rate', 0):.2f}, "
                    f"调整={_opt_result.get('changes', {})}, "
                    f"原因={_opt_result.get('reasons', [])[:1]}"
                )
        except Exception as _opt_e:
            silent_exc(_opt_e, where="nucleus.reasoning.ReasoningFeedbackLoop::record_quality L113")

    # ========== 核心：参数优化 ==========

    def should_optimize(self) -> bool:
        """判断是否应该进行参数优化。"""
        if not self._enabled:
            return False
        if len(self._quality_window) < 10:
            return False  # 样本不足
        _now = time.time()
        return (_now - self._last_optimization_time) >= self._min_interval

    def optimize_params(self) -> dict[str, Any] | None:
        """基于滑动窗口统计优化推理参数。

        Returns:
            优化结果 dict，含 before/after/reason；无需优化返回 None
        """
        if not self.should_optimize():
            return None

        _window = list(self._quality_window)
        _total = len(_window)
        _success_count = sum(1 for r in _window if r["success"])
        _success_rate = _success_count / _total if _total > 0 else 0.0
        _avg_relevance = sum(r["relevance_score"] for r in _window) / _total if _total > 0 else 0.0
        _avg_latency = sum(r["latency_ms"] for r in _window) / _total if _total > 0 else 0.0

        _before = dict(self._current_params)
        _changes: dict[str, float] = {}
        _reasons: list[str] = []

        # 成功率高 → 可降低资源（减少候选数/提高阈值）
        if _success_rate >= self._success_threshold:
            _changes["top_k"] = -self._ADJUST_STEP["top_k"]
            _changes["confidence_threshold"] = self._ADJUST_STEP["confidence_threshold"]
            _reasons.append(f"成功率{_success_rate:.2f}≥{self._success_threshold}，降低资源消耗")

        # 成功率低 → 需增加资源（增加候选数/降低阈值/加深推理）
        elif _success_rate < (self._success_threshold - 0.15):
            _changes["top_k"] = self._ADJUST_STEP["top_k"]
            _changes["resonance_threshold"] = -self._ADJUST_STEP["resonance_threshold"]
            _changes["reasoning_depth"] = self._ADJUST_STEP["reasoning_depth"]
            _reasons.append(f"成功率{_success_rate:.2f}<{self._success_threshold - 0.15:.2f}，增加推理资源")

        # 平均相关性低 → 降低共振阈值（召回更多）
        if _avg_relevance < 0.5:
            _changes["resonance_threshold"] = _changes.get("resonance_threshold", 0) - self._ADJUST_STEP["resonance_threshold"]
            _reasons.append(f"平均相关性{_avg_relevance:.2f}<0.5，降低召回阈值")

        # 平均延迟高 → 减少候选数/降低推理深度
        if _avg_latency > 5000:  # >5秒
            _changes["top_k"] = _changes.get("top_k", 0) - self._ADJUST_STEP["top_k"]
            _changes["reasoning_depth"] = _changes.get("reasoning_depth", 0) - self._ADJUST_STEP["reasoning_depth"]
            _reasons.append(f"平均延迟{_avg_latency:.0f}ms>5000ms，降低推理负载")

        if not _changes:
            self._last_optimization_time = time.time()
            return None

        # 应用调整（限制在安全范围内）
        for _param, _delta in _changes.items():
            _lo, _hi = self._PARAM_RANGES.get(_param, (0, 100))
            _new_val = max(_lo, min(_hi, self._current_params[_param] + _delta))
            if _new_val != self._current_params[_param]:
                self._current_params[_param] = _new_val
                self._stats["param_changes"] += 1

        self._last_optimization_time = time.time()
        self._stats["optimizations"] += 1

        _result = {
            "optimization_id": f"opt_{int(time.time() * 1000)}",
            "before": _before,
            "after": dict(self._current_params),
            "changes": _changes,
            "reasons": _reasons,
            "window_stats": {
                "size": _total,
                "success_rate": round(_success_rate, 3),
                "avg_relevance": round(_avg_relevance, 3),
                "avg_latency_ms": round(_avg_latency, 1),
            },
            "timestamp": time.time(),
        }
        self._optimization_history.append(_result)
        # 限制历史长度
        if len(self._optimization_history) > 200:
            self._optimization_history = self._optimization_history[-100:]

        return _result

    # ========== 查询 ==========

    def get_recommended_params(self) -> dict[str, float]:
        """获取当前推荐的推理参数。"""
        return dict(self._current_params)

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。"""
        _window = list(self._quality_window)
        _total = len(_window)
        _success_rate = sum(1 for r in _window if r["success"]) / _total if _total > 0 else 0.0
        return {
            "enabled": self._enabled,
            "window_size": self._window_size,
            "current_window": _total,
            "success_rate": round(_success_rate, 3),
            "total_records": self._stats["total_records"],
            "optimizations": self._stats["optimizations"],
            "param_changes": self._stats["param_changes"],
            "recommended_params": dict(self._current_params),
        }

    def get_recent_optimizations(self, limit: int = 5) -> list[dict[str, Any]]:
        """获取最近的参数优化记录。"""
        return self._optimization_history[-limit:]


# ========== 便捷函数 ==========

_feedback_loop: ReasoningFeedbackLoop | None = None


def get_reasoning_feedback_loop() -> ReasoningFeedbackLoop:
    """获取 ReasoningFeedbackLoop 单例。"""
    global _feedback_loop
    if _feedback_loop is None:
        _feedback_loop = ReasoningFeedbackLoop()
    return _feedback_loop



if __name__ == "__main__":
    # 自测
    loop = ReasoningFeedbackLoop(window_size=20, min_interval=0.1)
    # 模拟低成功率推理
    for i in range(15):
        loop.record_quality("rule_reason", 0.3 + i * 0.01, success=False, latency_ms=3000)
    print(f"should_optimize: {loop.should_optimize()}")
    result = loop.optimize_params()
    print(f"optimization: {result}")
    print(f"stats: {loop.get_stats()}")
