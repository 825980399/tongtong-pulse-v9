# -*- coding: utf-8 -*-
"""RequestDeduplicator OBSERVE 埋点（178批 刀2：只观察不接线）。

观测「跨轮在途复用机会」，为 179 批 RequestDeduplicator 接线提供基线数据。
复用 ENABLE_SEMANTIC_CACHE_OBSERVE 的 shadow 规范：
  - 开关关闭（默认 True）→ 不启线程、不落盘、零 IO、零行为变化。
  - 本模块不调用 RequestDeduplicator.try_claim/complete（接线推 179）。
  - 观测逻辑全部为无异常操作（dict/hashlib/attr），故不含任何静默 except。
"""
from __future__ import annotations

import hashlib
import threading
from typing import Any

import config as _cfg

# 线程局部：承载当前请求的 user_name（由 PulseLung._on_select_model 写入）
_tls = threading.local()

_lock = threading.Lock()
_stats = {
    "observed": 0,            # 累计被观察的外部调用发起次数
    "_inflight": 0,           # 当前在途
    "inflight_peak": 0,       # 在途峰值
    "fingerprints": {},       # request_id(指纹) -> 出现次数
}


def _enabled() -> bool:
    return bool(getattr(_cfg, "ENABLE_SEMANTIC_CACHE_OBSERVE", True))


def set_current_user(user_name: str) -> None:
    """由调用方在发起外部调用前写入当前请求 user_name（线程局部）。"""
    _tls.user_name = user_name or "?"


def get_current_user() -> str:
    return getattr(_tls, "user_name", "?") or "?"


def _fingerprint(user_name: str, prompt: str) -> str:
    _h = hashlib.sha1(f"{user_name}|{prompt}".encode("utf-8", "ignore")).hexdigest()
    return _h[:16]


def observe_request(user_name: str, prompt: str) -> None:
    """旁路记录一次外部调用发起（仅统计，无异常分支）。"""
    if not _enabled():
        return
    _fp = _fingerprint(user_name, prompt)
    with _lock:
        _stats["observed"] += 1
        _stats["_inflight"] += 1
        if _stats["_inflight"] > _stats["inflight_peak"]:
            _stats["inflight_peak"] = _stats["_inflight"]
        _stats["fingerprints"][_fp] = _stats["fingerprints"].get(_fp, 0) + 1


def complete_request(user_name: str, prompt: str) -> None:
    """旁路记录一次外部调用结束（仅维护在途计数，无异常分支）。"""
    if not _enabled():
        return
    with _lock:
        if _stats["_inflight"] > 0:
            _stats["_inflight"] -= 1


class _ObserveSpan:
    """上下文管理器：进入时 observe，退出时 complete。"""

    __slots__ = ("_user", "_prompt")

    def __init__(self, user_name: str, prompt: str) -> None:
        self._user = user_name
        self._prompt = prompt

    def __enter__(self) -> "_ObserveSpan":
        observe_request(self._user, self._prompt)
        return self

    def __exit__(self, *exc: Any) -> bool:
        complete_request(self._user, self._prompt)
        return False


def observe_span(user_name: str, prompt: str) -> _ObserveSpan:
    return _ObserveSpan(user_name, prompt)


def snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "observed": _stats["observed"],
            "inflight": _stats["_inflight"],
            "inflight_peak": _stats["inflight_peak"],
            "distinct_fingerprints": len(_stats["fingerprints"]),
            "fingerprint_top": sorted(_stats["fingerprints"].items(),
                                      key=lambda kv: -kv[1])[:20],
        }


def reset() -> None:
    with _lock:
        _stats["observed"] = 0
        _stats["_inflight"] = 0
        _stats["inflight_peak"] = 0
        _stats["fingerprints"] = {}
