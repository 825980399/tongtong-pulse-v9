# -*- coding: utf-8 -*-
"""★450批：告警「连续计数」判定（补的正是既有两套缺的第三种语义）。

为什么不新造一套降噪机制
------------------------
本仓已有两套，各管一件事（`nucleus/log_state.py` 的 docstring 里写明了分工）：

  · ``nucleus/_warn_throttle.should_warn(key, interval)`` ＝ **时间窗**节流
    （同一 key 每 interval 秒放行一次，放行与内容无关）；
  · ``nucleus/log_state.changed(tag, state)`` ＝ **内容**节流
    （state 与上次相同则不放行）。

450 要治的四类告警里，两类正好落在这两套上（指标采集器降级 / 问题池超限 = 内容
没变就别重报），另两类需要的是**第三种语义**：

  · 控制器超时、百科 403：单次抖动不该 WARNING，**连续 N 次**才算"渠道真挂了"。

「连续 N 次」既不是时间窗也不是内容变化 —— 一条消息内容一样、间隔也不规整，
只有"连续第几次"能区分"偶发"与"持续"。所以本模块只补这一种，且：

  ★**不复制已有逻辑**：`should_warn_content()` 直接组合上面两套的调用
    （顺序写死在这里，因为 `_warn_throttle` 只在放行时更新时间戳，
    短路求值会让时间戳与内容状态错位 —— 这是唯一容易写错的地方）。

三条纪律（本仓既有红线）
------------------------
1. 本模块**只做判定**，不写盘、不自己打日志 —— 级别与出口由调用方决定。
2. 全部函数**绝不抛异常**：告警路径上抛异常等于把噪声换成崩溃。
3. 全局表带锁且有界（超 `_MAX_KEYS` 清最久未更新的那一半），
   不让"降噪工具"自己变成内存泄漏。

开关
----
`ENABLE_M450_ALERT_DEDUP`（默认 True）。关 ⇒ 所有 `should_warn*` 一律返回 True
（逐字回到 450 之前的"每次都报"），但 streak 计数照常维护 ——
线上临时放开看全量时不用改代码。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from nucleus import log_state as _log_state
from nucleus._silent_except import silent_exc
from nucleus._warn_throttle import should_warn as _should_warn_time

#: 全局表上限（防降噪工具自己变内存泄漏）。超限时清最久未更新的那一半。
_MAX_KEYS: int = 4096

#: `key → {"streak": int, "last_warn": float, "last_at": float, "suppressed": int}`
_STATE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _on() -> bool:
    """降噪总开关（默认开）。读不到 config ⇒ 按开处理（宁可有降噪）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M450_ALERT_DEDUP", True))
    except Exception as _e:
        silent_exc(_e, where="alert_dedup::_on", level="debug")
        return True


def _slot(key: str) -> dict[str, Any]:
    """取（必要时新建）一个 key 的状态槽。调用方必须已持锁。"""
    _s = _STATE.get(key)
    if _s is None:
        if len(_STATE) >= _MAX_KEYS:
            _old = sorted(_STATE, key=lambda k: float(
                (_STATE.get(k) or {}).get("last_at") or 0.0))
            for _k in _old[: max(1, len(_old) // 2)]:
                _STATE.pop(_k, None)
        _s = {"streak": 0, "last_warn": 0.0, "last_at": 0.0, "suppressed": 0}
        _STATE[key] = _s
    return _s


# ---------------- 语义一：连续计数（本模块唯一新增）----------------

def bump_streak(key: str, now: float | None = None) -> int:
    """连续计数 +1 并返回新值。**只计数，不判定**。"""
    _now = float(time.time() if now is None else now)
    try:
        with _LOCK:
            _s = _slot(str(key))
            _s["streak"] = int(_s.get("streak") or 0) + 1
            _s["last_at"] = _now
            return int(_s["streak"])
    except Exception as _e:
        silent_exc(_e, where="alert_dedup::bump_streak", level="debug")
        return 1


def reset_streak(key: str) -> None:
    """连续计数归零（成功一次就调它）。"""
    try:
        with _LOCK:
            _s = _STATE.get(str(key))
            if _s is not None:
                _s["streak"] = 0
                _s["last_at"] = time.time()
    except Exception as _e:
        silent_exc(_e, where="alert_dedup::reset_streak", level="debug")
        return


def streak_of(key: str) -> int:
    """读当前连续计数（观测用）。"""
    try:
        with _LOCK:
            return int((_STATE.get(str(key)) or {}).get("streak") or 0)
    except Exception as _e:
        silent_exc(_e, where="alert_dedup::streak_of", level="debug")
        return 0


def should_warn_streak(key: str, *, threshold: int = 3,
                       renotify_sec: float = 600.0,
                       now: float | None = None) -> tuple[bool, int]:
    """连续 N 次才 WARNING；已 WARNING 后每 `renotify_sec` 再报一次。

    语义（任务书 450 §刀4）：单次超时降 DEBUG，连续 ≥`threshold` 次才说明"渠道真挂了"。
    但**不能只报一次**：渠道挂了 3 小时还在挂着，必须还能看见 —— 所以过阈值后
    每 `renotify_sec` 再报一条（消息里带连续次数，读者一眼知道挂了多久）。

    这把"连续计数"与"时间窗"合成一个判据：
      · 未达阈值 ⇒ 不报（`suppressed` 累计被压掉的次数）；
      · 刚达阈值 ⇒ 立即报（这是"真挂了"的那一刻）；
      · 已达阈值但未到 `renotify_sec` ⇒ 不报；
      · 已达阈值且到点 ⇒ 再报一次。

    Returns: ``(emit, streak)``；`emit=False` 时调用方降 DEBUG 并带 `streak`。
    """
    _now = float(time.time() if now is None else now)
    _thr = max(1, int(threshold))
    _ren = max(0.0, float(renotify_sec))
    _n = bump_streak(key, now=_now)
    if not _on():
        return True, _n
    try:
        with _LOCK:
            _s = _slot(str(key))
            if _n < _thr:
                _s["suppressed"] = int(_s.get("suppressed") or 0) + 1
                return False, _n
            _last = float(_s.get("last_warn") or 0.0)
            if _last <= 0.0 or (_now - _last) >= _ren:
                _s["last_warn"] = _now
                _s["suppressed"] = 0
                return True, _n
            _s["suppressed"] = int(_s.get("suppressed") or 0) + 1
            return False, _n
    except Exception as _e:
        silent_exc(_e, where="alert_dedup::should_warn_streak", level="debug")
        return True, _n


# ---------------- 语义二：内容 + 时间窗（组合既有两套，不另存状态）----------------

def should_warn_content(key: str, content: Any, *,
                        renotify_sec: float = 3600.0) -> tuple[bool, int]:
    """内容没变**且**时间窗未到 ⇒ 压掉；内容一变立刻报。

    用于"状态型"告警（指标采集器降级 / 问题池超限）：文案里带着数字，
    数字不变就说明状态没变，再报一遍没有任何新信息。

    ★实现是**组合**：`_warn_throttle.should_warn`（时间窗）+ `log_state.changed`
      （内容变化）。两个都必须**无条件调用**（不能短路）—— `should_warn` 只在
      放行时才更新时间戳，短路会让时间戳与内容状态错位，症状是"内容变的那一刻
      报一次、下一秒又报一次"。

    Returns: ``(emit, suppressed_since_last)``
    """
    if not _on():
        return True, 0
    _due = _should_warn_time(str(key), max(0.0, float(renotify_sec)))
    _chg = _log_state.changed(str(key), content)
    try:
        _sup = int(_log_state.suppressed(str(key)) or 0)
    except Exception as _e:
        silent_exc(_e, where="alert_dedup::should_warn_content", level="debug")
        _sup = 0
    return bool(_due or _chg), _sup




def cfg_num(name: str, default: float) -> float:
    """读数值配置：**只有 None 才算缺省**，``0`` / ``0.0`` 是有效值。

    为什么要单开这个函数：`getattr(cfg, X, d) or d` 会把 0 吞成默认值，
    而本批好几个间隔参数的 0 是**有意义**的（"每次都报"，单测靠它）。
    同一行代码在测试里表现为"节流失效、一条都不落"，排查成本极高。
    """
    try:
        import config
        _v = getattr(config, name, None)
        if _v is None:
            return float(default)
        return float(_v)
    except Exception as _e:
        silent_exc(_e, where="alert_dedup::cfg_num", level="debug")
        return float(default)


def cfg_int(name: str, default: int) -> int:
    """读整数配置（同 `cfg_num` 的 0 语义；结果至少为 1）。"""
    return max(1, int(cfg_num(name, float(default))))


def stats() -> dict:
    """降噪台面读数（连续计数侧；内容侧的读数由 `log_state.snapshot()` 给）。"""
    try:
        with _LOCK:
            _items = {}
            for _k, _v in _STATE.items():
                _items[_k] = {"streak": int(_v.get("streak") or 0),
                              "suppressed": int(_v.get("suppressed") or 0),
                              "last_warn": _v.get("last_warn")}
            _tot = sum(int(v.get("suppressed") or 0) for v in _STATE.values())
            return {"on": _on(), "keys": len(_STATE), "suppressed_total": _tot,
                    "items": _items}
    except Exception as _e:
        silent_exc(_e, where="alert_dedup::stats", level="debug")
        return {"on": False, "keys": 0, "suppressed_total": 0, "items": {}}


def reset_all() -> None:
    """清空全部状态（**仅供测试**；生产不要调）。"""
    try:
        with _LOCK:
            _STATE.clear()
    except Exception as _e:
        silent_exc(_e, where="alert_dedup::reset_all", level="debug")
        return
