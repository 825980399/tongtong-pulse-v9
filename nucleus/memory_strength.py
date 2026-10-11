# -*- coding: utf-8 -*-
"""183批刀4：记忆强度衰减观测——纯函数 memory_strength。

观测级：仅由 PulseSubconscious 探索tick 调用，对节点列表做影子排序对比
（现排序 vs 加入 strength 的排序，top10 重合率），输出到日志；不改变
检索返回结果、不动宪法权重、不改 Parquet schema。
"""
from __future__ import annotations

import math
import time


def memory_strength(last_activated: float, count: int, tau: float = 30.0) -> float:
    """记忆强度 = 时间衰减 × 频率强化。

    - last_activated: 节点最后激活时间戳（秒）。
    - count: 激活次数。
    - tau: 衰减时间常数（秒），走 config.MEMORY_STRENGTH_TAU。
    返回相对强度（未归一化，仅用于排序对比）。
    """
    now = time.time()
    dt = max(0.0, now - float(last_activated))
    tau = max(1e-6, float(tau))
    recency = math.exp(-dt / tau)                    # exp(-Δt/τ) 连续时间衰减
    freq = 1.0 + math.log1p(max(0, int(count)))      # 高频访问强化（EMA 风格）
    return round(recency * freq, 4)
