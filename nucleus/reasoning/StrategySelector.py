# -*- coding: utf-8 -*-
"""
StrategySelector.py —— 策略选择器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 推理策略的动态选择与切换
机制: 基于StrategySelector类实现，包含10个核心方法
定位: 推理调度层
"""

from __future__ import annotations

import time
from typing import Any, ClassVar



class StrategySelector:
    """推理策略智能选择器。"""

    # 推理策略定义（成本从低到高）
    _STRATEGIES: ClassVar[dict[str, dict[str, Any]]] = {
        "simple_logic": {
            "name": "简单逻辑推理",
            "cost": 1,
            "complexity_range": (0.0, 0.3),
            "description": "直接规则匹配，适合简单事实性问题",
        },
        "rule_reason": {
            "name": "规则推理",
            "cost": 2,
            "complexity_range": (0.2, 0.5),
            "description": "基于知识库规则的推理，适合中等问题",
        },
        "multi_step_execute": {
            "name": "多步推理",
            "cost": 3,
            "complexity_range": (0.4, 0.7),
            "description": "分步推理+中间验证，适合复杂问题",
        },
        "deep_think": {
            "name": "深度思考",
            "cost": 4,
            "complexity_range": (0.6, 1.0),
            "description": "深度认知反思+多分支探索，适合极复杂问题",
        },
    }

    # 问题类型→默认策略映射
    _TYPE_DEFAULT: ClassVar[dict[str, str]] = {
        "事实查询": "simple_logic",
        "定义解释": "rule_reason",
        "比较分析": "multi_step_execute",
        "方案设计": "deep_think",
        "代码调试": "multi_step_execute",
        "数学计算": "rule_reason",
        "创意生成": "deep_think",
        "情感对话": "rule_reason",
        "未知": "rule_reason",
    }

    def __init__(self, enabled: bool = True,
                 adapt_rate: float = 0.1) -> None:
        """
        Args:
            enabled: 开关
            adapt_rate: 策略偏好自适应速率（0-1，越大调整越快）
        """
        self._enabled = enabled
        self._adapt_rate = max(0.01, min(0.5, float(adapt_rate)))

        # 策略成功率统计（滑动平均）
        self._strategy_success: dict[str, float] = {
            name: 0.7 for name in self._STRATEGIES  # 初始成功率0.7
        }
        self._strategy_count: dict[str, int] = {
            name: 0 for name in self._STRATEGIES
        }

        # 选择历史
        self._selection_history: list[dict[str, Any]] = []
        self._stats = {"total_selections": 0, "adaptations": 0}

    # ========== 核心：策略选择 ==========

    def select_strategy(self, question: str,
                        complexity: float = 0.5,
                        question_type: str = "未知",
                        context: dict[str, Any] | None = None) -> dict[str, Any]:
        """选择最优推理策略。

        Args:
            question: 问题文本
            complexity: 问题复杂度（0-1，可由调用方评估或默认0.5）
            question_type: 问题类型（事实查询/比较分析/方案设计等）
            context: 附加上下文（历史对话/用户偏好等）

        Returns:
            选择结果 dict，含 strategy/confidence/reason/cost
        """
        if not self._enabled:
            return self._fallback_selection(question, complexity, question_type)

        _ctx = context or {}

        # 1. 基于问题类型的默认策略
        _type_strategy = self._TYPE_DEFAULT.get(question_type, "rule_reason")

        # 2. 基于复杂度的策略调整
        _complexity_strategy = self._select_by_complexity(complexity)

        # 3. 基于历史成功率的策略偏好
        _success_bias = self._get_success_bias()

        # 4. 综合决策：取类型默认和复杂度推荐中成本较高的（保守），
        #    但如果该策略历史成功率极低，则降级到次优策略
        _candidate = _type_strategy
        if self._STRATEGIES[_complexity_strategy]["cost"] > self._STRATEGIES[_candidate]["cost"]:
            _candidate = _complexity_strategy

        # 如果候选策略成功率过低，尝试降级
        if self._strategy_success.get(_candidate, 0.5) < 0.4:
            _lower = self._get_lower_cost_strategy(_candidate)
            if _lower and self._strategy_success.get(_lower, 0.5) > 0.5:
                _candidate = _lower

        # 5. 计算置信度
        _confidence = self._calc_selection_confidence(
            _candidate, complexity, question_type, _success_bias)

        _result = {
            "strategy": _candidate,
            "strategy_name": self._STRATEGIES[_candidate]["name"],
            "cost": self._STRATEGIES[_candidate]["cost"],
            "confidence": round(_confidence, 3),
            "complexity": round(complexity, 3),
            "question_type": question_type,
            "reason": (f"类型={question_type}→{self._TYPE_DEFAULT.get(question_type, 'rule_reason')}, "
                      f"复杂度={complexity:.2f}→{_complexity_strategy}, "
                      f"历史成功率={self._strategy_success.get(_candidate, 0):.2f}"),
            "timestamp": time.time(),
        }

        self._selection_history.append(_result)
        self._stats["total_selections"] += 1
        # 限制历史长度
        if len(self._selection_history) > 500:
            self._selection_history = self._selection_history[-300:]

        return _result

    def report_result(self, strategy: str, success: bool) -> None:
        """报告策略执行结果，更新成功率统计（自适应学习）。"""
        if strategy not in self._strategy_success:
            return
        _old = self._strategy_success[strategy]
        _delta = 1.0 if success else 0.0
        # 滑动平均更新
        self._strategy_success[strategy] = round(
            _old * (1 - self._adapt_rate) + _delta * self._adapt_rate, 3)
        self._strategy_count[strategy] += 1
        self._stats["adaptations"] += 1

    # ========== 内部方法 ==========

    def _select_by_complexity(self, complexity: float) -> str:
        """基于复杂度选择策略。"""
        _c = max(0.0, min(1.0, float(complexity)))
        for name, info in self._STRATEGIES.items():
            _lo, _hi = info["complexity_range"]
            if _lo <= _c <= _hi:
                return name
        return "rule_reason"  # 默认

    def _get_success_bias(self) -> dict[str, float]:
        """获取各策略的成功率偏好。"""
        return dict(self._strategy_success)

    def _get_lower_cost_strategy(self, strategy: str) -> str | None:
        """获取成本更低的策略（降级用）。"""
        _current_cost = self._STRATEGIES[strategy]["cost"]
        _lower = [(n, i["cost"]) for n, i in self._STRATEGIES.items()
                  if i["cost"] < _current_cost]
        if not _lower:
            return None
        # 取成本最接近的更低策略
        _lower.sort(key=lambda x: x[1], reverse=True)
        return _lower[0][0]

    def _calc_selection_confidence(self, strategy: str, complexity: float,
                                   question_type: str,
                                   success_bias: dict[str, float]) -> float:
        """计算选择置信度。"""
        _base = 0.5
        # 复杂度匹配度
        _lo, _hi = self._STRATEGIES[strategy]["complexity_range"]
        if _lo <= complexity <= _hi:
            _base += 0.2
        # 类型匹配度
        if self._TYPE_DEFAULT.get(question_type) == strategy:
            _base += 0.15
        # 历史成功率
        _base += 0.15 * success_bias.get(strategy, 0.5)
        return min(1.0, _base)

    def _fallback_selection(self, question: str, complexity: float,
                            question_type: str) -> dict[str, Any]:
        """禁用时的降级选择。"""
        return {
            "strategy": "rule_reason",
            "strategy_name": "规则推理",
            "cost": 2,
            "confidence": 0.5,
            "complexity": complexity,
            "question_type": question_type,
            "reason": "策略选择器已禁用，使用默认策略",
            "timestamp": time.time(),
        }

    # ========== 查询 ==========

    def estimate_complexity(self, question: str) -> float:
        """基于问题文本特征估算复杂度（简易启发式）。

        特征：长度、是否含比较/方案/为什么/多步等关键词。
        """
        _q = question or ""
        _score = 0.3  # 基础复杂度
        # 长度
        if len(_q) > 100:
            _score += 0.15
        if len(_q) > 300:
            _score += 0.15
        # 复杂关键词
        _complex_keywords = ["比较", "对比", "方案", "设计", "为什么", "如何",
                            "分析", "优化", "架构", "系统", "多步", "综合"]
        for kw in _complex_keywords:
            if kw in _q:
                _score += 0.05
        # 简单关键词
        _simple_keywords = ["是什么", "定义", "解释", "多少", "谁", "哪"]
        for kw in _simple_keywords:
            if kw in _q:
                _score -= 0.05
        return max(0.0, min(1.0, round(_score, 3)))

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。"""
        return {
            "enabled": self._enabled,
            "total_selections": self._stats["total_selections"],
            "adaptations": self._stats["adaptations"],
            "strategy_success": dict(self._strategy_success),
            "strategy_count": dict(self._strategy_count),
            "history_size": len(self._selection_history),
        }

    def get_recent_selections(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取最近的选择记录。"""
        return self._selection_history[-limit:]


# ========== 便捷函数 ==========

_strategy_selector: StrategySelector | None = None


def get_strategy_selector() -> StrategySelector:
    """获取 StrategySelector 单例。"""
    global _strategy_selector
    if _strategy_selector is None:
        _strategy_selector = StrategySelector()
    return _strategy_selector


def set_strategy_selector(selector: StrategySelector) -> None:
    """设置 StrategySelector 单例（用于依赖注入/测试）。"""
    global _strategy_selector
    _strategy_selector = selector


if __name__ == "__main__":
    # 自测
    sel = StrategySelector()
    # 简单问题
    r1 = sel.select_strategy("什么是脉冲架构？", complexity=0.2, question_type="定义解释")
    print(f"简单问题: {r1['strategy']} (置信度={r1['confidence']})")
    # 复杂问题
    r2 = sel.select_strategy("设计一个超越人类的AI架构，需要考虑哪些维度？",
                             complexity=0.8, question_type="方案设计")
    print(f"复杂问题: {r2['strategy']} (置信度={r2['confidence']})")
    # 报告结果
    sel.report_result("deep_think", success=True)
    sel.report_result("simple_logic", success=False)
    print(f"stats: {sel.get_stats()}")
    # 复杂度估算
    print(f"复杂度估算: {sel.estimate_complexity('比较分析两种架构的优劣')}")
