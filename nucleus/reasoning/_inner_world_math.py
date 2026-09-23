# -*- coding: utf-8 -*-
"""
_inner_world_math.py —— 内在世界数学

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 内在世界模拟的数学计算工具
机制: 函数式模块，包含7个工具函数
定位: 推理支撑层
"""

from __future__ import annotations

import math
from typing import Any



def calc_trust_boost(current_trust: float, base_boost: float) -> float:
    """信任分边际递减：信任>80时加分减半，信任>90时仅给10%"""
    if current_trust >= 90.0:
        return base_boost * 0.1
    if current_trust >= 80.0:
        return base_boost * 0.5
    return base_boost


def calc_branch_weight(relevance: float, content_len: int, correlation_score: float) -> float:
    """计算分支权重（多分支推理评分核心）"""
    weight = 1.0
    weight += relevance * 0.6
    if content_len >= 80:
        weight += 0.4
    elif content_len >= 40:
        weight += 0.2
    weight += min(0.3, correlation_score)
    return round(weight, 2)


def calc_correlation_score(shared_keywords: list[int]) -> float:
    """计算分支间关联分"""
    score = 0.0
    for count in shared_keywords:
        score += count * 0.1
    return min(0.3, score)


def weighted_fusion(values: list[float], weights: list[float]) -> float:
    """通用加权融合评分"""
    total = 0.0
    weight_sum = 0.0
    for v, w in zip(values, weights):
        total += v * w
        weight_sum += w
    if weight_sum <= 0.0:
        return 0.0
    return total / weight_sum


def normalize_scores(scores: list[float]) -> list[float]:
    """批量分数归一化（Softmax）"""
    if not scores:
        return []
    max_score = max(scores)
    exps = [math.exp(s - max_score) for s in scores]
    sum_exp = sum(exps)
    if sum_exp <= 0.0:
        return [1.0 / len(scores)] * len(scores)
    return [e / sum_exp for e in exps]


def cosine_similarity_py(vec_a: list[float], vec_b: list[float]) -> float:
    """余弦相似度（纯Python列表版本）"""
    if not vec_a or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# 统一导出接口
def get_inner_world_math() -> Any:
    """获取数学模块（优先Cython，fallback到Python）"""
    try:
        from nucleus.reasoning._inner_world_math_cy import (
            calc_branch_weight as _cy_branch,
        )
        from nucleus.reasoning._inner_world_math_cy import (
            calc_correlation_score as _cy_corr,
        )
        from nucleus.reasoning._inner_world_math_cy import (
            calc_trust_boost as _cy_trust,
        )
        from nucleus.reasoning._inner_world_math_cy import (
            cosine_similarity_cy as _cy_cosine,
        )
        from nucleus.reasoning._inner_world_math_cy import (
            normalize_scores as _cy_norm,
        )
        from nucleus.reasoning._inner_world_math_cy import (
            weighted_fusion as _cy_fusion,
        )
        return type("InnerWorldMath", (), {
            "calc_trust_boost": staticmethod(_cy_trust),
            "calc_branch_weight": staticmethod(_cy_branch),
            "calc_correlation_score": staticmethod(_cy_corr),
            "weighted_fusion": staticmethod(_cy_fusion),
            "normalize_scores": staticmethod(_cy_norm),
            "cosine_similarity": staticmethod(_cy_cosine),
            "_cython_available": True,
        })
    except ImportError:
        return type("InnerWorldMath", (), {
            "calc_trust_boost": staticmethod(calc_trust_boost),
            "calc_branch_weight": staticmethod(calc_branch_weight),
            "calc_correlation_score": staticmethod(calc_correlation_score),
            "weighted_fusion": staticmethod(weighted_fusion),
            "normalize_scores": staticmethod(normalize_scores),
            "cosine_similarity": staticmethod(cosine_similarity_py),
            "_cython_available": False,
        })
