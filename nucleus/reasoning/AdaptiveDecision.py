# -*- coding: utf-8 -*-
"""
AdaptiveDecision.py —— 自适应决策

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 基于上下文的自适应决策机制
机制: 基于AdaptiveDecision类实现，包含9个核心方法
定位: 推理决策层
"""

from __future__ import annotations

import random
from typing import Any


class AdaptiveDecision:
    """通用自适应决策器。

    支持两种决策模式：
    1. 策略选择：从候选策略中按ε-greedy选择（参考历史成功率）
    2. 阈值调整：根据误杀/漏杀率动态调整阈值
    """

    def __init__(
        self,
        strategies: list[str] | None = None,
        epsilon: float = 0.3,
        ema_alpha: float = 0.3,
        initial_rate: float = 0.5,
        name: str = "adaptive",
    ) -> None:
        """
        Args:
            strategies: 候选策略列表
            epsilon: 探索概率（0-1），默认0.3（30%探索，70%利用）
            ema_alpha: EMA衰减系数（0-1），越大越重视近期结果
            initial_rate: 初始成功率（乐观初始值鼓励探索）
            name: 决策器名称（用于日志）
        """
        self._strategies = list(strategies or [])
        self._epsilon = max(0.0, min(1.0, epsilon))
        self._ema_alpha = max(0.01, min(1.0, ema_alpha))
        self._initial_rate = initial_rate
        self._name = name

        # 策略成功率跟踪：strategy → {"success": n, "total": n, "ema_rate": float}
        self._strategy_stats: dict[str, dict[str, Any]] = {}
        for s in self._strategies:
            self._strategy_stats[s] = {"success": 0, "total": 0, "ema_rate": initial_rate}

        # 阈值自适应状态
        self._threshold = 0.5
        self._threshold_stats = {"false_positive": 0, "false_negative": 0, "total": 0}
        self._threshold_min = 0.35
        self._threshold_max = 0.65
        self._threshold_step = 0.02
        self._threshold_adjust_interval = 30

    # ========== 策略选择（ε-greedy） ==========

    def choose(self, candidates: list[str] | None = None) -> str | None:
        """ε-greedy选择策略。

        Args:
            candidates: 候选策略列表（None则用全部策略）

        Returns:
            选中的策略名；无候选时返回None
        """
        _candidates = candidates or self._strategies
        if not _candidates:
            return None

        if random.random() < self._epsilon:
            # 探索：随机选
            return random.choice(_candidates)
        else:
            # 利用：选EMA成功率最高的
            return max(
                _candidates,
                key=lambda s: self._strategy_stats.get(s, {}).get("ema_rate", self._initial_rate),
            )

    def report(self, strategy: str, success: bool) -> None:
        """报告策略执行结果，更新EMA成功率。

        Args:
            strategy: 执行的策略名
            success: 是否成功
        """
        if strategy not in self._strategy_stats:
            self._strategy_stats[strategy] = {
                "success": 0, "total": 0, "ema_rate": self._initial_rate,
            }
        _s = self._strategy_stats[strategy]
        _s["total"] += 1
        if success:
            _s["success"] += 1
        _outcome = 1.0 if success else 0.0
        _s["ema_rate"] = self._ema_alpha * _outcome + (1 - self._ema_alpha) * _s["ema_rate"]

    # ========== 阈值自适应 ==========

    def set_threshold(self, value: float) -> None:
        """设置初始阈值。"""
        self._threshold = max(self._threshold_min, min(self._threshold_max, value))

    def get_threshold(self) -> float:
        """获取当前阈值。"""
        return self._threshold

    def report_threshold_outcome(
        self, false_positive: bool = False, false_negative: bool = False
    ) -> float | None:
        """报告阈值判定结果，定期调整阈值。

        Args:
            false_positive: 误杀（触发了验证但实际不需要）
            false_negative: 漏杀（没触发验证但实际需要）

        Returns:
            调整后的阈值（如果调整了），否则None
        """
        self._threshold_stats["total"] += 1
        if false_positive:
            self._threshold_stats["false_positive"] += 1
        if false_negative:
            self._threshold_stats["false_negative"] += 1

        if self._threshold_stats["total"] % self._threshold_adjust_interval != 0:
            return None

        _fp = self._threshold_stats["false_positive"]
        _fn = self._threshold_stats["false_negative"]
        _total = self._threshold_stats["total"]
        if _total == 0:
            return None

        _fp_rate = _fp / _total
        _fn_rate = _fn / _total
        _old = self._threshold
        _adjusted = False

        # 误杀多 → 降低阈值（减少触发）
        if _fp_rate > 0.2:
            self._threshold = max(self._threshold_min, _old - self._threshold_step)
            _adjusted = True
        # 漏杀多 → 提高阈值（增加触发）
        elif _fn_rate > 0.2:
            self._threshold = min(self._threshold_max, _old + self._threshold_step)
            _adjusted = True

        # 重置统计（滑动窗口）
        self._threshold_stats = {"false_positive": 0, "false_negative": 0, "total": 0}

        return self._threshold if _adjusted else None

    # ========== 查询与统计 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取决策统计。"""
        return {
            "name": self._name,
            "strategies": {
                s: {
                    "success": v["success"],
                    "total": v["total"],
                    "ema_rate": round(v["ema_rate"], 3),
                }
                for s, v in self._strategy_stats.items()
            },
            "threshold": round(self._threshold, 3),
            "epsilon": self._epsilon,
            "ema_alpha": self._ema_alpha,
        }

    def get_best_strategy(self) -> str | None:
        """获取当前成功率最高的策略。"""
        if not self._strategy_stats:
            return None
        return max(self._strategy_stats, key=lambda s: self._strategy_stats[s]["ema_rate"])


# ========== 便捷函数 ==========

_adaptive_decision_registry: dict[str, AdaptiveDecision] = {}


def get_adaptive_decision(name: str, **kwargs: Any) -> AdaptiveDecision:
    """获取或创建命名的自适应决策器（单例模式）。

    Args:
        name: 决策器名称
        **kwargs: 创建参数（仅首次创建时生效）

    Returns:
        AdaptiveDecision实例
    """
    if name not in _adaptive_decision_registry:
        _adaptive_decision_registry[name] = AdaptiveDecision(name=name, **kwargs)
    return _adaptive_decision_registry[name]


if __name__ == "__main__":
    # 自测
    ad = AdaptiveDecision(strategies=["a", "b", "c"], epsilon=0.3)
    print(f"初始: {ad.get_stats()}")
    # 模拟100次选择，策略a成功率80%，b 50%，c 20%
    for _ in range(100):
        choice = ad.choose()
        if choice == "a":
            ad.report(choice, success=random.random() < 0.8)
        elif choice == "b":
            ad.report(choice, success=random.random() < 0.5)
        else:
            ad.report(choice, success=random.random() < 0.2)
    print(f"100次后: {ad.get_stats()}")
    print(f"最优策略: {ad.get_best_strategy()}")
