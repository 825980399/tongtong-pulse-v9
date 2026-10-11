# -*- coding: utf-8 -*-
"""183批刀3：认知状态机观测——纯函数 suggested_temperature。

观测级：仅由 PulseSubconscious 探索tick 调用，把 (energy, mood, focus, hour)
映射为建议温度并输出到日志；不进任何请求、不改变任何行为。
"""
from __future__ import annotations

# 与 config.LLM_TEMPERATURE_BASE 默认一致，避免循环 import；调用方可覆盖 base。
_DEFAULT_BASE = 0.7


def suggested_temperature(energy: float, mood: float, focus: float,
                         hour: int, base: float = _DEFAULT_BASE) -> float:
    """(energy, mood, focus, hour) -> suggested_temperature。

    - energy/mood/focus 取值域 [0,1]；hour 为本地小时 [0,23]。
    - 夜间(23:00-06:00)：低能量/低专注时倾向更高温度（发散联想）。
    - 白天：高专注时倾向更低温度（精确）。
    - 输出 clamp 到 [0.5, 0.9]。
    """
    energy = max(0.0, min(1.0, float(energy)))
    mood = max(0.0, min(1.0, float(mood)))
    focus = max(0.0, min(1.0, float(focus)))
    hour = int(hour) % 24

    t = float(base)
    night = (hour >= 23 or hour < 6)
    if night:
        t += 0.15 * (1.0 - energy)       # 夜间低能量更发散
    else:
        t -= 0.10 * focus                 # 白天高专注更精确
    t += 0.05 * (1.0 - mood)              # 低情绪（平静/低落）略发散

    return round(max(0.5, min(0.9, t)), 3)
