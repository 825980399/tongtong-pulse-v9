# -*- coding: utf-8 -*-
"""363 刀 B3：六处诊断/统计方法的**生产接线桥**。

为什么要有这一层
----------------
360 批遗留闭合批落地了六个诊断出口（`m360_tool_flags` / `m360_channel_diag` /
`m360_absorb_stats` / `m360_event_metrics` / `m360_check_queue_alarm` /
`m360_rollover_notes`），但它们的**唯一调用方是测试**——生产主循环里没有任何
调用点 ⇒ static_verified 成立、live 不可见（17:46:05 评审把其中 5 条扫进了
audit open dead_code：代码在、没人用）。

本桥把六处接到既有心跳上，**只加读与日志，不改任何控制流**，并满足：
  * 拿不到实例 ⇒ 如实记 `no_instance`（不伪装成 0 / 空）；
  * 采集异常 ⇒ 记 `error` + silent_exc，不吞；
  * 心跳侧按拍节流（默认每 10 拍一行），不刷屏。

注册方式：各宿主类在 `__init__` 里调 `register(kind, self)`（弱引用，不延长生命周期）。
EventBus 另有模块级 `get_event_bus()` 单例，优先走单例。
"""
from __future__ import annotations

import time
import weakref
from typing import Any

import logging

#: 模块级日志器（logging 是标准库，不需要 try 兜底 —— 兜底本身会变成静默 handler）
_LOG = logging.getLogger("pulse.silent_except")

try:
    from nucleus._silent_except import silent_exc
except Exception:  # 兜底：桥本身绝不能把宿主拖崩
    import logging as _m363_logging

    def silent_exc(_e: Exception, where: str = "", level: str = "debug") -> None:
        """降级实现：**仍要留痕**——静默 except 的兜底不能再是一个 pass。"""
        _m363_logging.getLogger("pulse.silent_except").log(
            {"debug": 10, "info": 20, "warning": 30, "error": 40,
             "critical": 50}.get(level, 10),
            f"[静默异常可见化] {where} {type(_e).__name__}: {_e}")

#: kind → 最近一次注册的宿主实例（弱引用）
_SLOTS: dict[str, Any] = {}

#: 六项的显式清单（顺序即输出顺序）
KINDS = ("tooling_runner", "resonance_engine", "qica", "event_bus", "logger")


def register(kind: str, obj: Any) -> None:
    """宿主类在 `__init__` 末尾登记自己（弱引用；重复注册以最后一次为准）。"""
    try:
        if obj is None:
            return
        try:
            _SLOTS[kind] = weakref.ref(obj)
        except TypeError as _te:  # 不可弱引用（如某些 C 扩展/带 __slots__ 的对象）
            # ★不得纯 assign：换成强引用会**延长宿主生命周期**，这个副作用必须可见
            _SLOTS[kind] = obj
            if _LOG is not None:
                _LOG.warning("[363B3] 宿主不可弱引用 ⇒ 改用强引用 kind=%s: %s",
                             kind, _te)
            silent_exc(_te, where="m363_diag_bridge::register(%s) 改用强引用" % kind,
                       level="warning")
    except Exception as e:
        if _LOG is not None:
            _LOG.warning("[363B3] 宿主登记失败 kind=%s: %s: %s", kind, type(e).__name__, e)
        silent_exc(e, where="nucleus.m363_diag_bridge::register(%s)" % kind,
                   level="warning")


def _get(kind: str) -> Any:
    """取宿主实例；弱引用失效返回 None（调用方须按 `no_instance` 处理）。"""
    _ref = _SLOTS.get(kind)
    if _ref is None:
        return None
    if isinstance(_ref, weakref.ReferenceType):
        return _ref()
    return _ref


def collect() -> dict[str, Any]:
    """采集六处诊断读数（每项独立 try，单项失败不影响其他项）。"""
    out: dict[str, Any] = {"ts": time.time()}

    # 1) tool 通道记账（compileall/mypy 失败与超时）
    try:
        _r = _get("tooling_runner")
        out["tool_flags"] = (_r.m360_tool_flags() if _r is not None
                             else {"state": "no_instance"})
    except Exception as e:
        silent_exc(e, where="m363_diag_bridge::collect.tool_flags", level="warning")
        if _LOG is not None:
            _LOG.warning("[363B3] 采集 tool_flags 失败: %s: %s",
                         type(e).__name__, e)
        out["tool_flags"] = {"state": "error", "err": f"{type(e).__name__}"}

    # 2) 共振五维通道命中率
    try:
        _e = _get("resonance_engine")
        out["channel_diag"] = (_e.m360_channel_diag() if _e is not None
                               else {"state": "no_instance"})
    except Exception as e:
        silent_exc(e, where="m363_diag_bridge::collect.channel_diag", level="warning")
        if _LOG is not None:
            _LOG.warning("[363B3] 采集 channel_diag 失败: %s: %s",
                         type(e).__name__, e)
        out["channel_diag"] = {"state": "error", "err": f"{type(e).__name__}"}

    # 3) QICA 成长规则吸收归因
    try:
        _q = _get("qica")
        out["absorb_stats"] = (_q.m360_absorb_stats() if _q is not None
                               else {"state": "no_instance"})
    except Exception as e:
        silent_exc(e, where="m363_diag_bridge::collect.absorb_stats", level="warning")
        if _LOG is not None:
            _LOG.warning("[363B3] 采集 absorb_stats 失败: %s: %s",
                         type(e).__name__, e)
        out["absorb_stats"] = {"state": "error", "err": f"{type(e).__name__}"}

    # 4/5) EventBus 零匹配按名分桶 + 队列水位（优先走模块级单例）
    try:
        _bus = _get("event_bus")
        if _bus is None:
            try:
                from nucleus.events.EventBus import get_event_bus as _geb
                _bus = _geb()
            except Exception as _ge:
                _bus = None
                # ★不得纯 assign：拿不到 EventBus 单例会让「事件零匹配」永远读不到
                if _LOG is not None:
                    _LOG.warning("[363B3] 取 EventBus 单例失败: %s: %s",
                                 type(_ge).__name__, _ge)
                silent_exc(_ge, where="m363_diag_bridge::collect.event_bus 单例",
                           level="warning")
        if _bus is None:
            out["event_metrics"] = {"state": "no_instance"}
            out["queue_alarm"] = {"state": "no_instance"}
        else:
            out["event_metrics"] = _bus.m360_event_metrics()
            # ★434刀4：`m360_check_queue_alarm()` 返回 dict，`bool(dict)` 对任何
            #   非空 dict 都为 True ⇒ 队列告警永远为 True（假告警淹没真告警）。
            #   改为解引用 dict 的 `alarm` 字段，并保留 depth/threshold/suppressed
            #   供可观测面使用。
            _qa = _bus.m360_check_queue_alarm()
            if not isinstance(_qa, dict):
                _qa = {}
            out["queue_alarm"] = {
                "alarm": bool(_qa.get("alarm")),
                "depth": _qa.get("depth"),
                "threshold": _qa.get("threshold"),
                "suppressed": bool(_qa.get("suppressed")),
            }
    except Exception as e:
        silent_exc(e, where="m363_diag_bridge::collect.event_bus", level="warning")
        if _LOG is not None:
            _LOG.warning("[363B3] 采集 event_metrics 失败: %s: %s",
                         type(e).__name__, e)
        out["event_metrics"] = {"state": "error", "err": f"{type(e).__name__}"}
        out["queue_alarm"] = {"state": "error", "err": f"{type(e).__name__}"}

    # 6) 日志轮转留痕（模块级函数，无实例依赖）
    try:
        from nucleus.logger import m360_rollover_notes as _rn
        out["rollover_notes"] = _rn()
    except Exception as e:
        silent_exc(e, where="m363_diag_bridge::collect.rollover_notes", level="warning")
        if _LOG is not None:
            _LOG.warning("[363B3] 采集 rollover_notes 失败: %s: %s",
                         type(e).__name__, e)
        out["rollover_notes"] = {"state": "error", "err": f"{type(e).__name__}"}
    return out


def diag_line(data: dict | None = None) -> str:
    """一行摘要（心跳打印用）。**不造假**：拿不到实例就写 no_instance。"""
    d = data if isinstance(data, dict) else collect()
    _em = d.get("event_metrics") if isinstance(d.get("event_metrics"), dict) else {}
    _cd = d.get("channel_diag") if isinstance(d.get("channel_diag"), dict) else {}
    _tf = d.get("tool_flags") if isinstance(d.get("tool_flags"), dict) else {}
    _ab = d.get("absorb_stats") if isinstance(d.get("absorb_stats"), dict) else {}
    _rn = d.get("rollover_notes") if isinstance(d.get("rollover_notes"), dict) else {}
    _sem = _cd.get("semantic") if isinstance(_cd.get("semantic"), dict) else {}
    return (
        "[363B3] 诊断 tool=%s 共振命中率=%s 吸收归因=%s 事件零匹配=%s/%s 队列告警=%s 轮转留痕=%s"
        % (len([k for k, v in _tf.items() if isinstance(v, dict) and v.get("degraded")])
           if _tf.get("state") is None else _tf.get("state"),
           _sem.get("hit_rate") if _sem else _cd.get("state", "-"),
           sum((_ab.get("reasons") or {}).values()) if _ab.get("reasons") is not None
           else _ab.get("state", "-"),
           _em.get("zero_match", "-"), _em.get("published", "-"),
           (d.get("queue_alarm") or {}).get("alarm", "-"),
           len(_rn) if _rn.get("state") is None else _rn.get("state", "-"))
    )
