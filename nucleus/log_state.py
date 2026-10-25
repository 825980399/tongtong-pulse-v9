# -*- coding: utf-8 -*-
"""★369 刀1（E9 日志瘦身第一批）：**读数/状态变化才打** 的通用判据。

背景
----
E9 点名的高频日志族里，绝大多数不是「发生了值得知道的事」，而是
「控制流每拍必然刷一遍同样的读数」。典型实证（2026-10-24 窗口，logs/pulse.log.1）：

- ``[semantic_cache_l2] telemetry scene=...``  6034 条/40.9h，读数恒 0 仍每拍刷；
- ``[终身学习引擎] 当前累计 N 条对比记录``  同一 ``N`` 重复打印 27–28 次；
- ``总量压缩跳过: N 条...``  同一 ``N`` 重复打印 115 次；
- ``[317刀0][分诊节拍] 钩子未到时 ⇒ 未触发`` 863 条/16.5h（"没发生"也要报一遍）。

这些族的共同特征：**输出内容由状态决定，而状态大部分时间不变**。
本模块把「状态变没变」抽成一个纯判据，各族只需 2 行接入。

与既有 ``nucleus/_warn_throttle.py`` 的分工（★165 一开关一语义，不合并）
-----------------------------------------------------------------------
- ``_warn_throttle.should_warn(key, interval)`` ＝ **时间窗**节流：同一 key 每 ``interval`` 秒放行一次
  （用途：持续失败态的 WARNING 刷屏限流，放行与内容无关）。
- ``log_state.changed(tag, state)`` ＝ **内容**节流：``state`` 与上次同 tag 相同则返回 False
  （用途：读数/状态刷新类日志，放行只看内容有没有变）。

两者不互相替代：时间窗解决「同一个坏消息反复喊」，内容节流解决「同一个读数反复报」。

用法
----
    from nucleus.log_state import changed, sig, suppressed

    if changed("motivation.cycle", sig((self._pressure_state, len(self._active_motivations)))):
        self._log(LogLevel.INFO, f"动机循环#%d: ..." % self._cycle_count)

被抑制的次数由 ``suppressed(tag)`` 读出，可用于聚合行末尾的「（无变化 ×N 次）」后缀，
保证**降噪不等于消失**：读数一直不变这件事本身也是信息。

线程安全：进程内单例 + 互斥锁；不落盘、不联网、零 IO。
"""
from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()

#: tag -> 上一次放行的状态值
_STATE: dict[str, Any] = {}

#: tag -> 自上次放行以来被抑制的次数
_SUPPRESSED: dict[str, int] = {}

#: tag 数量上限（防止调用方用动态 tag 打穿内存；超出后按插入顺序淘汰最老的）
_MAX_TAGS = 2048

_MISSING = object()


def sig(obj: Any) -> Any:
    """把 dict/list/set 归一化成可 ``==`` 比较的稳定结构（递归）。

    - ``dict`` → 按键排序的 ``((k, sig(v)), ...)``（不受插入顺序影响）；
    - ``list``/``tuple`` → 逐元素 ``sig`` 后的 ``tuple``；
    - ``set``/``frozenset`` → 排序后的 ``tuple``；
    - 其余原样返回（``float/str/int/None`` 等可直接比较的值）。

    不可比较的自定义对象也原样返回：此时退化为「对象身份/自身 ``__eq__``」比较，
    不会抛异常——判据宁可保守放行（多打一条），也不允许因为归一化失败而吞掉日志。
    """
    if isinstance(obj, dict):
        return tuple(sorted((str(k), sig(v)) for k, v in obj.items()))
    if isinstance(obj, (list, tuple)):
        return tuple(sig(v) for v in obj)
    if isinstance(obj, (set, frozenset)):
        return tuple(sorted(sig(v) for v in obj))
    return obj


def changed(tag: str, state: Any) -> bool:
    """状态与上次同 ``tag`` 放行时不同 ⇒ 返回 True 并记下新状态；相同 ⇒ 返回 False 并累加抑制计数。

    - 首次调用（该 tag 无历史）**返回 True**：留出一条基线读数，便于重启后确认族仍在跑。
    - 只做比较与记账，**不产生任何副作用**（不打日志、不落盘、不改调用方状态）。
    """
    with _LOCK:
        _prev = _STATE.get(tag, _MISSING)
        if _prev is not _MISSING and _prev == state:
            _SUPPRESSED[tag] = _SUPPRESSED.get(tag, 0) + 1
            return False
        _STATE[tag] = state
        _SUPPRESSED[tag] = 0
        if len(_STATE) > _MAX_TAGS:
            for _k in list(_STATE)[: len(_STATE) - _MAX_TAGS]:
                _STATE.pop(_k, None)
                _SUPPRESSED.pop(_k, None)
        return True


def suppressed(tag: str) -> int:
    """返回 ``tag`` 自上次放行以来被抑制的次数（用于聚合行后缀）。未知 tag 返回 0。"""
    with _LOCK:
        return int(_SUPPRESSED.get(tag, 0))


def reset(tag: str | None = None) -> None:
    """清空记忆（测试用）：``tag`` 为 None 时全清，否则只清该 tag。"""
    with _LOCK:
        if tag is None:
            _STATE.clear()
            _SUPPRESSED.clear()
        else:
            _STATE.pop(tag, None)
            _SUPPRESSED.pop(tag, None)


def snapshot() -> dict[str, dict[str, Any]]:
    """观测快照：``{tag: {"suppressed": int, "has_state": bool}}``（不返回状态值本身，避免放大内存读数）。"""
    with _LOCK:
        return {
            _k: {"suppressed": int(_SUPPRESSED.get(_k, 0)), "has_state": True}
            for _k in _STATE
        }
