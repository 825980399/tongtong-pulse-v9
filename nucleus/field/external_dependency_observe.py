# -*- coding: utf-8 -*-
"""外部依赖度 OBSERVE 埋点（178批 刀4：只观察不接线）。

为 T-内在模型自研路线-1 提供三指标基线（本批仅观测，不实装 L3）：
  - external_channel_calls: 外部渠道(LLM/搜索)调用次数
  - llm_used_false / llm_total: 语义理解判定「无需外部 LLM」的次数与占比
  - delegate_browser: 委托浏览器(自主探索路由)次数
复用 ENABLE_SEMANTIC_CACHE_OBSERVE 的 shadow 规范：
  - 开关关闭（默认 True）→ 不计数、零 IO、零行为变化。
  - 观测逻辑全部为无异常操作（dict/operator），故不含任何静默 except（cw2 红线）。
"""
from __future__ import annotations

import threading
import time
from typing import Any

import config as _cfg
from nucleus.logger import get_module_logger

_log = get_module_logger("observe.external_dependency")

# ★180刀6（X1 缺口闭合）：周期性将观测快照以 INFO 落日志。
_periodic_interval = 300  # 周期快照日志间隔（秒）
_periodic_thread = None
_periodic_lock = threading.Lock()


def log_snapshot() -> None:
    """将当前观测快照以 INFO 级落日志（解决 179B「snapshot 无日志落点」X1 缺口）。"""
    _snap = snapshot()
    _log.info("[external_dependency_observe] snapshot external_channel_calls=%s llm_total=%s "
              "llm_used_false=%s llm_used_false_ratio=%s delegate_browser=%s",
              _snap.get("external_channel_calls"), _snap.get("llm_total"),
              _snap.get("llm_used_false"), _snap.get("llm_used_false_ratio"),
              _snap.get("delegate_browser"))


def _periodic_loop(interval: int) -> None:
    while True:
        time.sleep(interval)
        log_snapshot()


def start_periodic_snapshot(interval_seconds: int = _periodic_interval) -> None:
    global _periodic_thread
    if not _enabled():
        return
    with _periodic_lock:
        if _periodic_thread is not None and _periodic_thread.is_alive():
            return
        _periodic_thread = threading.Thread(target=_periodic_loop, args=(interval_seconds,), daemon=True)
        _periodic_thread.start()


def _maybe_start_periodic() -> None:
    start_periodic_snapshot()

_lock = threading.Lock()
_stats = {
    "external_channel_calls": 0,   # 外部渠道调用计数
    "llm_total": 0,                # 语义理解判定总次数
    "llm_used_false": 0,           # 判定无需外部 LLM（本地处理）次数
    "delegate_browser": 0,         # 委托浏览器路由次数
    "semantic_upstream": 0,        # ★383刀6：上游发布 SEMANTIC_CLASSIFY 的次数
}


def _enabled() -> bool:
    return bool(getattr(_cfg, "ENABLE_SEMANTIC_CACHE_OBSERVE", True))


def observe_external_channel_call() -> None:
    """旁路记录一次外部渠道调用发起（仅统计，无异常分支）。"""
    if not _enabled():
        return
    _maybe_start_periodic()
    with _lock:
        _stats["external_channel_calls"] += 1


def observe_semantic_decision(need_llm: bool) -> None:
    """旁路记录一次语义理解判定；need_llm=False 表示本地处理（无需外部 LLM）。"""
    if not _enabled():
        return
    with _lock:
        _stats["llm_total"] += 1
        if not need_llm:
            _stats["llm_used_false"] += 1


def note_semantic_upstream() -> None:
    """★383刀6：标记「上游已发出一次 SEMANTIC_CLASSIFY」。

    只计数、不判定、不抛 —— 挂在发布点前面，用来区分两件长得一模一样的事：
      · 上游根本没发（没有对话流量）⇒ snapshot 的 llm_state = no_upstream
      · 上游发了但埋点没收到（投递断了）⇒ llm_state = upstream_no_delivery
    没有这个计数，两种情况的读数都是 `llm_total=0`，必然被误读成「埋点坏了」
    —— 本批实测就是这个坑：live 恒 0，而同模块 delegate_browser 一直在涨。
    """
    if not _enabled():
        return
    with _lock:
        _stats["semantic_upstream"] += 1


def observe_delegate_browser() -> None:
    """旁路记录一次委托浏览器（自主探索路由）决策（仅统计，无异常分支）。"""
    if not _enabled():
        return
    with _lock:
        _stats["delegate_browser"] += 1


#: ★383刀6：判定「投递断了」的最小样本数。
#: 取 3 而不是 1，是因为 EventBus 可能异步投递 —— 单次差值不足以说明丢失。
_M383_NO_DELIVERY_MIN = 3


def _llm_state_locked(upstream: int, total: int, local_only: int) -> str:
    """★383刀6：把 LLM 依赖度读数翻译成可判读的状态。

    no_upstream           上游没发布过（无对话流量）—— **正常**，不是缺陷
    pending               上游刚发、埋点还没到（异步窗口内正常，**别当丢失**）
    upstream_no_delivery  上游发了 ≥3 次而埋点一次都没收到 —— **真缺陷**
    local_only            收到了，但全判「本地可处理」
    partial_loss          有丢失但仍在收（上游−收到 ≥3）
    ok                    其余
    """
    if upstream == 0 and total == 0:
        return "no_upstream"
    if total == 0:
        if upstream >= _M383_NO_DELIVERY_MIN:
            return "upstream_no_delivery"
        return "pending"
    if local_only == total:
        return "local_only"
    if upstream - total >= _M383_NO_DELIVERY_MIN:
        return "partial_loss"
    if total > 0:
        return "ok"
    return "unknown"


def snapshot() -> dict[str, Any]:
    with _lock:
        _ratio = (_stats["llm_used_false"] / _stats["llm_total"]) if _stats["llm_total"] else 0.0
        _up = _stats.get("semantic_upstream", 0)
        return {
            "external_channel_calls": _stats["external_channel_calls"],
            "llm_total": _stats["llm_total"],
            "llm_used_false": _stats["llm_used_false"],
            "llm_used_false_ratio": round(_ratio, 4),
            "delegate_browser": _stats["delegate_browser"],
            "semantic_upstream": _up,
            "semantic_undelivered": max(0, _up - _stats["llm_total"]),
            "llm_state": _llm_state_locked(_up, _stats["llm_total"],
                                           _stats["llm_used_false"]),
        }


def reset() -> None:
    with _lock:
        for _k in _stats:
            _stats[_k] = 0
