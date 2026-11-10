# -*- coding: utf-8 -*-
"""★491-7：渠道轮换排序优化（默认关）。

排序键严格按任务书：状态 ok 优先 → 剩余额度比例高优先 → 近 200 次成功率
高优先 → 原 priority；同分用原下标稳定排序，**不用随机**。

为什么单独维护「近 200 次成功率」：既有
`channel_health.success_rate()` 是**累计** success/calls，没有滑动窗口
（window_size 只是构造参数，record() 并不修剪）。本模块用 deque(maxlen=200)
记每次调用结果，只在开关开时记账。
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any

from nucleus._silent_except import silent_exc

_RECENT_WINDOW = 200
_LOCK = threading.Lock()
_RECENT: dict[str, deque] = {}


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception as _e:
        silent_exc(_e, where="m491_smart_rotate::_cfg", level="debug")
        return default


def on() -> bool:
    return bool(_cfg("ENABLE_M491_SMART_CHANNEL_ROTATE", False))


def record(name: str, success: bool) -> None:
    """记一次调用结果（开关关时首行返回，零开销）。"""
    if not on():
        return
    try:
        with _LOCK:
            _q = _RECENT.get(str(name))
            if _q is None:
                _q = deque(maxlen=_RECENT_WINDOW)
                _RECENT[str(name)] = _q
            _q.append(1 if success else 0)
    except Exception as _e:
        silent_exc(_e, where="m491_smart_rotate::record", level="debug")


def recent_success_rate(name: str) -> float:
    """近 N 次成功率；无记录返回 1.0（未知不惩罚，与 channel_balance 同口径）。"""
    if not on():
        return 1.0
    try:
        with _LOCK:
            _q = _RECENT.get(str(name))
            if not _q:
                return 1.0
            return sum(_q) / len(_q)
    except Exception as _e:
        silent_exc(_e, where="m491_smart_rotate::recent_success_rate", level="debug")
        return 1.0


def reset_for_tests() -> None:
    with _LOCK:
        _RECENT.clear()


def _remaining_ratio(name: str, quota) -> float:
    """取不到额度读数回落 1.0，绝不写 0（0 会被读成彻底不可用）。"""
    if quota is None or not name:
        return 1.0
    try:
        _r = quota.get_remaining_ratio(name)
        if _r is None:
            return 1.0
        return max(0.0, min(1.0, float(_r)))
    except Exception as _e:
        silent_exc(_e, where="m491_smart_rotate::_remaining_ratio", level="debug")
        return 1.0


def order(candidates: list, *, state_store=None, quota=None) -> list:
    """返回重排后的候选；开关关时**原样返回同一对象**（逐字回改前）。

    state_store 需提供 `state_of(name)`；quota 需提供 `get_remaining_ratio(name)`。
    两者为 None 时对应因子取中性值（不改顺序）。
    """
    if not on():
        return candidates
    try:
        _scored = []
        for _i, _c in enumerate(candidates or []):
            if not isinstance(_c, dict):
                _scored.append((0, 1.0, 1.0, 999, _i, _c))
                continue
            _name = str(_c.get("name", "") or "")
            _st = "ok"
            if state_store is not None:
                try:
                    _st = str(state_store.state_of(_name) or "ok")
                except Exception as _e:
                    silent_exc(_e, where="m491_smart_rotate::order:state",
                               level="debug")
            _scored.append((
                0 if _st == "ok" else 1,
                -_remaining_ratio(_name, quota),
                -recent_success_rate(_name),
                _c.get("priority", 999),
                _i,
                _c,
            ))
        _scored.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
        return [x[5] for x in _scored]
    except Exception as _e:
        silent_exc(_e, where="m491_smart_rotate::order", level="debug")
        return candidates
