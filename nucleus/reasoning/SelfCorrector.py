# -*- coding: utf-8 -*-
"""
SelfCorrector.py —— 自我修正器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 推理过程的自我发现与修正
机制: 基于SelfCorrector类实现，包含10个核心方法
定位: 推理治理层
"""

from __future__ import annotations

import time
from typing import Any, ClassVar



class SelfCorrector:
    """推理自我纠错引擎。"""

    # 纠错策略优先级（从低成本到高成本）
    _STRATEGY_PRIORITY: ClassVar[list[str]] = [
        "adjust_retrieval",   # 调整检索参数（增加候选数/降低阈值）
        "switch_method",      # 换推理方法（规则→多步→深度思考）
        "supplement_context", # 补充上下文（更多记忆/知识）
        "lower_confidence",   # 降低置信度阈值（接受次优答案）
    ]

    def __init__(self, max_retries: int = 2, min_interval: float = 0.5,
                 enabled: bool = True) -> None:
        """
        Args:
            max_retries: 最大纠错次数（压力均衡，避免无限重试）
            min_interval: 两次纠错间的最小间隔秒数
            enabled: 开关
        """
        self._max_retries = max(1, int(max_retries))
        self._min_interval = max(0.1, float(min_interval))
        self._enabled = enabled
        self._correction_history: list[dict[str, Any]] = []
        self._last_correction_time = 0.0
        self._stats = {"total_attempts": 0, "success": 0, "failed": 0, "strategy_usage": {}}
        # ★v25.1 P0智能化: 策略成功率跟踪（指数移动平均EMA）
        #   初始值0.5（乐观初始值，鼓励探索新策略）
        #   _strategy_success: strategy → {"success": n, "total": n, "ema_rate": float}
        self._strategy_success: dict[str, dict[str, Any]] = {}
        for _s in self._STRATEGY_PRIORITY:
            self._strategy_success[_s] = {"success": 0, "total": 0, "ema_rate": 0.5}
        self._ema_alpha = 0.3  # EMA衰减系数，近期结果权重更高
        self._epsilon = 0.3    # ε-greedy探索概率（30%探索，70%利用）

    # ========== 核心：自我纠错 ==========

    def should_correct(self, verification: dict[str, Any],
                       retry_count: int = 0) -> bool:
        """判断是否应该进行自我纠错。

        Args:
            verification: 输出验证结果（含 relevance_score/reason/needs_verification）
            retry_count: 当前已重试次数

        Returns:
            True=应该纠错，False=直接降级到远程大模型
        """
        if not self._enabled:
            return False
        if retry_count >= self._max_retries:
            return False
        # 节流：两次纠错间最小间隔
        _now = time.time()
        if (_now - self._last_correction_time) < self._min_interval:
            return False
        # 相关性极低（<0.2）且原因明确时纠错收益高
        _score = float(verification.get("relevance_score", 0.0))
        return _score < 0.6

    def generate_correction(self, verification: dict[str, Any],
                            context: dict[str, Any] | None = None,
                            retry_count: int = 0) -> dict[str, Any] | None:
        """生成纠错方案。

        Args:
            verification: 输出验证结果
            context: 当前推理上下文（method/question/answer/检索参数等）
            retry_count: 当前已重试次数（用于选择不同策略）

        Returns:
            纠错方案 dict，含 strategy/params/reason；无法纠错返回 None
        """
        if not self._enabled:
            return None
        if retry_count >= self._max_retries:
            return None

        _ctx = context or {}
        _reason = verification.get("reason", "unknown")
        _score = float(verification.get("relevance_score", 0.0))
        _current_method = _ctx.get("method", "unknown")

        # 根据失败原因选择策略
        _strategy = self._select_strategy(_reason, _current_method, retry_count)
        if not _strategy:
            return None

        _params = self._build_correction_params(_strategy, _ctx, _reason)
        _correction = {
            "correction_id": f"correct_{int(time.time() * 1000)}",
            "strategy": _strategy,
            "params": _params,
            "reason": f"相关性{_score:.2f}，原因={_reason}",
            "retry_count": retry_count + 1,
            "max_retries": self._max_retries,
            "timestamp": time.time(),
        }

        self._last_correction_time = time.time()
        self._stats["total_attempts"] += 1
        self._stats["strategy_usage"][_strategy] = self._stats["strategy_usage"].get(_strategy, 0) + 1

        return _correction

    def report_result(self, correction: dict[str, Any], success: bool,
                      new_score: float = 0.0) -> None:
        """报告纠错结果，更新统计和历史。"""
        correction["success"] = success
        correction["new_relevance_score"] = new_score
        correction["duration_ms"] = round((time.time() - correction.get("timestamp", time.time())) * 1000, 1)
        self._correction_history.append(correction)
        # 限制历史长度（内存压力均衡）
        if len(self._correction_history) > 500:
            self._correction_history = self._correction_history[-300:]
        if success:
            self._stats["success"] += 1
        else:
            self._stats["failed"] += 1

        # ★v25.1 P0智能化: 更新策略成功率EMA
        _used_strategy = correction.get("strategy", "")
        if _used_strategy and _used_strategy in self._strategy_success:
            _sr = self._strategy_success[_used_strategy]
            _sr["total"] += 1
            if success:
                _sr["success"] += 1
            # EMA更新：新值 = alpha * 新结果 + (1-alpha) * 旧值
            _outcome = 1.0 if success else 0.0
            _sr["ema_rate"] = self._ema_alpha * _outcome + (1 - self._ema_alpha) * _sr["ema_rate"]

    # ========== 策略选择 ==========

    def _select_strategy(self, reason: str, current_method: str,
                         retry_count: int) -> str | None:
        """根据失败原因和当前方法选择纠错策略。
        ★v25.1 P0智能化: 从纯关键词匹配升级为ε-greedy策略学习——
          先根据关键词筛选候选策略，再按历史成功率选择（70%选最优，30%探索）。
        """
        import random as _random
        _r = reason.lower()

        # 第一步：根据关键词筛选候选策略集（可能有多个匹配）
        _candidates = []
        if any(k in _r for k in ("知识", "检索", "knowledge", "retriev", "找不到", "无相关")):
            _candidates.append("adjust_retrieval")
        if any(k in _r for k in ("推理", "逻辑", "方法", "reason", "logic", "太简单", "不够深")):
            _candidates.append("switch_method")
        if any(k in _r for k in ("上下文", "context", "记忆", "memory", "缺少背景")):
            _candidates.append("supplement_context")
        if any(k in _r for k in ("置信度", "confidence", "阈值", "太严格")):
            _candidates.append("lower_confidence")

        # 无关键词匹配时，所有策略都是候选
        if not _candidates:
            _candidates = list(self._STRATEGY_PRIORITY)

        # 第二步：ε-greedy选择
        # 70%概率选历史成功率最高的策略（利用），30%概率随机选（探索）
        if _random.random() < self._epsilon:
            # 探索：随机选一个候选策略
            _chosen = _random.choice(_candidates)
        else:
            # 利用：选EMA成功率最高的策略
            _chosen = max(_candidates,
                         key=lambda s: self._strategy_success.get(s, {}).get("ema_rate", 0.5))

        return _chosen

    @staticmethod
    def _build_correction_params(strategy: str, context: dict[str, Any],
                                 reason: str) -> dict[str, Any]:
        """构建纠错参数。"""
        if strategy == "adjust_retrieval":
            return {
                "top_k": min(50, int(context.get("top_k", 20)) * 2),  # 候选数翻倍
                "resonance_threshold": max(0.3, float(context.get("resonance_threshold", 0.5)) - 0.1),
                "search_layers": ["L1", "L2", "L3"],  # 全层搜索
            }
        if strategy == "switch_method":
            _method = context.get("method", "")
            # 方法升级链：simple → rule → multi_step → deep_think
            _upgrade = {
                "simple_logic": "rule_reason",
                "rule_reason": "multi_step_execute",
                "multi_step_execute": "deep_think",
                "composite_logic": "deep_think",
            }
            return {
                "new_method": _upgrade.get(_method, "multi_step_execute"),
                "reason": f"原方法{_method}不适用，升级推理深度",
            }
        if strategy == "supplement_context":
            return {
                "include_conversation_memory": True,
                "include_search_experience": True,
                "context_window": min(4096, int(context.get("context_window", 2048)) * 2),
            }
        if strategy == "lower_confidence":
            return {
                "confidence_threshold": max(0.3, float(context.get("confidence_threshold", 0.6)) - 0.15),
                "accept_suboptimal": True,
            }
        return {}

    # ========== 查询与统计 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取纠错统计。"""
        _total = self._stats["total_attempts"]
        _rate = round(self._stats["success"] / _total, 3) if _total > 0 else 0.0
        return {
            "enabled": self._enabled,
            "max_retries": self._max_retries,
            "total_attempts": _total,
            "success": self._stats["success"],
            "failed": self._stats["failed"],
            "success_rate": _rate,
            "strategy_usage": dict(self._stats["strategy_usage"]),
            "strategy_success_rates": {
                s: round(v["ema_rate"], 3) for s, v in self._strategy_success.items()
            },
            "history_size": len(self._correction_history),
        }

    def get_recent_corrections(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取最近的纠错记录。"""
        return self._correction_history[-limit:]


# ========== 便捷函数 ==========

_self_corrector: SelfCorrector | None = None


def get_self_corrector() -> SelfCorrector:
    """获取 SelfCorrector 单例。"""
    global _self_corrector
    if _self_corrector is None:
        _self_corrector = SelfCorrector()
    return _self_corrector


def set_self_corrector(corrector: SelfCorrector) -> None:
    """设置 SelfCorrector 单例（用于依赖注入/测试）。"""
    global _self_corrector
    _self_corrector = corrector


if __name__ == "__main__":
    # 自测
    sc = SelfCorrector(max_retries=2, min_interval=0.1)
    # 模拟输出验证失败
    ver = {"relevance_score": 0.3, "reason": "知识检索不足，找不到相关内容", "needs_verification": True}
    print(f"should_correct: {sc.should_correct(ver, 0)}")
    corr = sc.generate_correction(ver, {"method": "simple_logic", "top_k": 20}, 0)
    print(f"correction: {corr}")
    if corr:
        sc.report_result(corr, success=True, new_score=0.75)
    print(f"stats: {sc.get_stats()}")
