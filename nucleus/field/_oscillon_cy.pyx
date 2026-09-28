# _oscillon_cy.pyx
# ★v23.0新增：振荡场纯数值计算的Cython加速版
# 仅包含纯数学计算，不涉及Python对象操作
# 注意：nogil块内只允许C类型变量和libc.math中的C函数
# 禁止在nogil块内调用任何Python对象/模块

from libc.math cimport fabs, sqrt


def calculate_resonance_cy(double source_freq, double target_freq,
                            double coupling_strength) -> float:
    """
    ★v23.0：共振强度计算（纯数值，与OscillonField.calculate_resonance输出一致）。

    共振公式：R = coupling_strength / sqrt(1 + (source_freq - target_freq)^2)
    """
    cdef double resonance = 0.0
    cdef double freq_diff = 0.0

    with nogil:
        freq_diff = fabs(source_freq - target_freq)
        resonance = coupling_strength / sqrt(1.0 + freq_diff * freq_diff)
        if resonance > 1.0:
            resonance = 1.0
        if resonance < 0.0:
            resonance = 0.0

    return resonance


def try_lock_frequency_cy(double source_freq, double target_freq,
                           double lock_threshold) -> float:
    """
    ★v23.0：频率锁定判断（纯数值部分，与FrequencyPhaseLock.try_lock输出一致）。

    计算频率差比率。由Python层根据比率判断是否锁定。
    freq_diff_ratio = |source_freq - target_freq| / max(source_freq, target_freq)
    """
    cdef double freq_diff_ratio = 0.0
    cdef double max_freq = 0.0

    with nogil:
        if source_freq <= 0 or target_freq <= 0:
            freq_diff_ratio = 1.0  # 无效频率，返回大差值
        else:
            if source_freq > target_freq:
                max_freq = source_freq
            else:
                max_freq = target_freq
            freq_diff_ratio = fabs(source_freq - target_freq) / max_freq

    return freq_diff_ratio