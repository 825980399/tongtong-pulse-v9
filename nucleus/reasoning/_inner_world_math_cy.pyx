# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
"""
_inner_world_math_cy —— PulseInnerWorld 纯数值计算热路径 C 化
版本: v9.5 PulseNet
日期: 2026年9月2日

职责:
    1. 分支相关性权重计算（多分支推理评分）
    2. 信任分边际递减计算（知识验证信任调整）
    3. 通用加权融合评分（节点融合/证据链权重）
    4. 批量相似度归一化
"""
from libc.math cimport sqrt, exp, fabs


cpdef double calc_trust_boost(double current_trust, double base_boost):
    """信任分边际递减：信任>80时加分减半，信任>90时仅给10%"""
    if current_trust >= 90.0:
        return base_boost * 0.1
    elif current_trust >= 80.0:
        return base_boost * 0.5
    return base_boost


cpdef double calc_branch_weight(double relevance, int content_len, double correlation_score):
    """
    计算分支权重（多分支推理评分核心）

    Args:
        relevance: 与原始问题的相关性 (0.0-1.0)
        content_len: 内容长度
        correlation_score: 与其他方向的关联分

    Returns:
        综合权重 (保留2位小数)
    """
    cdef double weight = 1.0
    # 因子1：与原始问题的相关性（+0.0~+0.6）
    weight += relevance * 0.6
    # 因子2：内容充实度（+0.2~+0.4）
    if content_len >= 80:
        weight += 0.4
    elif content_len >= 40:
        weight += 0.2
    # 因子3：与其他方向的关联（+0.0~+0.3）
    weight += min(0.3, correlation_score)
    return round(weight, 2)


cpdef double calc_correlation_score(list shared_keywords):
    """
    计算分支间关联分

    Args:
        shared_keywords: 共享关键词数量列表

    Returns:
        关联分 (上限0.3)
    """
    cdef double score = 0.0
    cdef int n = len(shared_keywords)
    cdef int i
    for i in range(n):
        score += shared_keywords[i] * 0.1
    return min(0.3, score)


cpdef double weighted_fusion(list values, list weights):
    """
    通用加权融合评分

    Args:
        values: 数值列表
        weights: 权重列表（与values等长）

    Returns:
        加权平均值
    """
    cdef double total = 0.0
    cdef double weight_sum = 0.0
    cdef int n = len(values)
    cdef int i
    for i in range(n):
        total += values[i] * weights[i]
        weight_sum += weights[i]
    if weight_sum <= 0.0:
        return 0.0
    return total / weight_sum


cpdef list normalize_scores(list scores):
    """
    批量分数归一化（Softmax）

    Args:
        scores: 原始分数列表

    Returns:
        归一化后的概率分布
    """
    cdef int n = len(scores)
    if n == 0:
        return []
    cdef double max_score = scores[0]
    cdef int i
    for i in range(1, n):
        if scores[i] > max_score:
            max_score = scores[i]
    cdef double sum_exp = 0.0
    cdef list exps = []
    cdef double e
    for i in range(n):
        e = exp(scores[i] - max_score)
        exps.append(e)
        sum_exp += e
    cdef list result = []
    for i in range(n):
        result.append(exps[i] / sum_exp if sum_exp > 0.0 else 1.0 / n)
    return result


cpdef double cosine_similarity_cy(list vec_a, list vec_b):
    """
    余弦相似度（纯Python列表版本，用于不支持numpy的场景）

    Args:
        vec_a: 向量A
        vec_b: 向量B

    Returns:
        余弦相似度 (-1.0 ~ 1.0)
    """
    cdef int n = len(vec_a)
    if n == 0 or n != len(vec_b):
        return 0.0
    cdef double dot = 0.0
    cdef double norm_a = 0.0
    cdef double norm_b = 0.0
    cdef int i
    for i in range(n):
        dot += vec_a[i] * vec_b[i]
        norm_a += vec_a[i] * vec_a[i]
        norm_b += vec_b[i] * vec_b[i]
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (sqrt(norm_a) * sqrt(norm_b))
