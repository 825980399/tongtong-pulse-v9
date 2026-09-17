# -*- coding: utf-8 -*-
"""
channel_speed_profiler.py —— 主线第58批 T2：渠道推理速度画像

轻量、纯内存的渠道延迟统计器。每次渠道推理完成后调用 ``record(name, seconds)``，
``profile(name)`` 返回该渠道（或全量）的移动统计：采样数、均值、最小/最大、
最近一次、p95（保留最近 ``max_samples`` 条样本）。

设计要点：
- 不依赖任何外部存储，进程内字典 + deque，零副作用；
- 所有方法异常自吞（绝不阻断主流程）；
- 可被单测完全覆盖（纯逻辑，无网络/IO）。
"""
from __future__ import annotations

from collections import deque
from typing import Any


class ChannelSpeedProfiler:
    """渠道推理速度画像：按渠道名累计延迟统计。"""

    def __init__(self, max_samples: int = 50):
        self._max_samples = int(max_samples)
        # name -> {"samples": deque, "min": float, "max": float}
        self._stats: dict[str, dict[str, Any]] = {}

    def record(self, name: str, seconds: float) -> None:
        """记录一次渠道推理耗时（秒）。seconds 必须为非负实数。"""
        try:
            _name = str(name)
            _s = float(seconds)
            if _s < 0 or _s != _s:  # 拒绝负数与 NaN
                return
            _bucket = self._stats.get(_name)
            if _bucket is None:
                _bucket = {"samples": deque(maxlen=self._max_samples),
                           "min": _s, "max": _s}
                self._stats[_name] = _bucket
            _bucket["samples"].append(_s)
            if _s < _bucket["min"]:
                _bucket["min"] = _s
            if _s > _bucket["max"]:
                _bucket["max"] = _s
        except Exception:
            pass

    def profile(self, name: str | None = None) -> dict:
        """返回画像。name=None 时返回 {渠道名: 画像} 的全量字典。"""
        try:
            if name is not None:
                return self._profile_one(str(name))
            return {_n: self._profile_one(_n) for _n in self._stats.keys()}
        except Exception:
            return {} if name is None else {}

    def _profile_one(self, name: str) -> dict:
        _bucket = self._stats.get(name)
        if not _bucket or not _bucket["samples"]:
            return {"count": 0, "avg": 0.0, "min": 0.0,
                    "max": 0.0, "last": 0.0, "p95": 0.0}
        _samples = list(_bucket["samples"])
        _n = len(_samples)
        _avg = sum(_samples) / _n
        _sorted = sorted(_samples)
        # p95：取 95% 分位（向上取整索引）
        _idx = min(_n - 1, int(round(0.95 * _n)) - 1)
        if _idx < 0:
            _idx = 0
        _p95 = _sorted[_idx]
        return {
            "count": _n,
            "avg": round(_avg, 4),
            "min": round(_bucket["min"], 4),
            "max": round(_bucket["max"], 4),
            "last": round(_samples[-1], 4),
            "p95": round(_p95, 4),
        }

    def reset(self, name: str | None = None) -> None:
        """清空统计。name=None 清空全量。"""
        try:
            if name is None:
                self._stats.clear()
            else:
                self._stats.pop(str(name), None)
        except Exception:
            pass
