# _resonance_cy.pyx
# ★v23.0新增：五维共振得分的Cython加速版
# 仅包含纯数值计算，不涉及Python对象操作
# 注意：nogil块内只允许C类型变量和libc.math中的C函数
# 禁止在nogil块内调用任何Python对象/模块

from libc.math cimport fabs, log10

# PI常量在可能用到的地方定义
cdef double PI_CONST = 3.141592653589793


def calc_memory_dim_cy(double query_freq, double node_freq,
                        double hebbian, int activation_count) -> float:
    """
    ★v23.0：记忆维得分计算（纯数值，与ResonanceEngine输出一致）。
    
    频率相似度×0.5 + 赫布权重×0.3 + 激活计数×0.2
    """
    cdef double score = 0.0
    cdef double freq_sim = 0.0
    cdef double act_score = 0.0
    cdef double clamped_hebbian = hebbian

    with nogil:
        # 频率相似度（相对差值）
        if query_freq > 0 and node_freq > 0:
            freq_sim = 1.0 - fabs(query_freq - node_freq) / 50.0
            if freq_sim < 0.0:
                freq_sim = 0.0
            score += freq_sim * 0.5

        # 赫布权重（夹在0-1之间）
        if clamped_hebbian > 1.0:
            clamped_hebbian = 1.0
        score += clamped_hebbian * 0.3

        # 激活计数（对数变换，且上限为1）
        if activation_count > 0:
            act_score = log10(activation_count + 1) / 5.0
            if act_score > 1.0:
                act_score = 1.0
            score += act_score * 0.2

        # 总分不超过1
        if score > 1.0:
            score = 1.0

    return score


def calc_time_dim_cy(double elapsed_seconds, double source_age_seconds=0.0, bint enable_timeliness=False) -> float:
    """
    ★v23.0：时间维得分计算（纯数值，与ResonanceEngine输出一致）。

    阶段三子任务3.0/3.1 扩展参数：
      - enable_timeliness=False（默认）：仅激活新鲜度，与原实现逐字节一致，零回退。
      - enable_timeliness=True：0.5 * 激活新鲜度 + 0.5 * 来源时效性（time 维内部融合，
        不影响五维权重 0.10 / peak 门 0.90）。

    激活新鲜度：1小时内1.0，24小时内0.3-1.0，7天内0.1-0.3，超过7天0.05。
    来源时效性（分段阈值，天→秒）：<7天=1.0, <30天=0.85, <90天=0.65, <365天=0.40, >=365天=0.20。
    """
    cdef double score = 0.0
    cdef double activation = 0.0
    cdef double source = 0.0

    with nogil:
        # 激活新鲜度（与现状完全一致）
        if elapsed_seconds < 3600:
            activation = 1.0
        elif elapsed_seconds < 86400:
            activation = 1.0 - (elapsed_seconds - 3600) / 82800.0 * 0.7
            if activation < 0.3:
                activation = 0.3
        elif elapsed_seconds < 604800:
            activation = 0.3 - (elapsed_seconds - 86400) / 518400.0 * 0.2
            if activation < 0.1:
                activation = 0.1
        else:
            activation = 0.05

        if not enable_timeliness:
            score = activation
        else:
            # 来源时效性（分段阈值，天→秒）
            if source_age_seconds < 604800:        # <7天
                source = 1.0
            elif source_age_seconds < 2592000:     # <30天
                source = 0.85
            elif source_age_seconds < 7776000:     # <90天
                source = 0.65
            elif source_age_seconds < 31536000:    # <365天
                source = 0.40
            else:                                   # >=365天
                source = 0.20
            score = 0.5 * activation + 0.5 * source

    return score


def calc_state_dim_cy(int importance_code) -> float:
    """
    ★v23.0：状态维得分计算（纯数值，与ResonanceEngine输出一致）。
    
    importance_code: S=3, A=2, B=1, 其他=0
    """
    cdef double score = 0.0

    with nogil:
        if importance_code == 3:
            score = 1.0
        elif importance_code == 2:
            score = 0.8
        elif importance_code == 1:
            score = 0.5
        else:
            score = 0.2

    return score