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
from typing import Any

import config as _cfg

_lock = threading.Lock()
_stats = {
    "external_channel_calls": 0,   # 外部渠道调用计数
    "llm_total": 0,                # 语义理解判定总次数
    "llm_used_false": 0,           # 判定无需外部 LLM（本地处理）次数
    "delegate_browser": 0,         # 委托浏览器路由次数
}


def _enabled() -> bool:
    return bool(getattr(_cfg, "ENABLE_SEMANTIC_CACHE_OBSERVE", True))


def observe_external_channel_call() -> None:
    """旁路记录一次外部渠道调用发起（仅统计，无异常分支）。"""
    if not _enabled():
        return
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


def observe_delegate_browser() -> None:
    """旁路记录一次委托浏览器（自主探索路由）决策（仅统计，无异常分支）。"""
    if not _enabled():
        return
    with _lock:
        _stats["delegate_browser"] += 1


def snapshot() -> dict[str, Any]:
    with _lock:
        _ratio = (_stats["llm_used_false"] / _stats["llm_total"]) if _stats["llm_total"] else 0.0
        return {
            "external_channel_calls": _stats["external_channel_calls"],
            "llm_total": _stats["llm_total"],
            "llm_used_false": _stats["llm_used_false"],
            "llm_used_false_ratio": round(_ratio, 4),
            "delegate_browser": _stats["delegate_browser"],
        }


def reset() -> None:
    with _lock:
        for _k in _stats:
            _stats[_k] = 0
