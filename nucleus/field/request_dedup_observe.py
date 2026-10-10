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
import time

from nucleus.logger import get_module_logger

_log = get_module_logger("observe.request_dedup")

# ★180刀6（X1 缺口闭合）：周期性将观测快照以 INFO 落日志（此前 snapshot() 从未被调用落盘）。
_periodic_interval = 300  # 周期快照日志间隔（秒）
_periodic_thread = None
_periodic_lock = threading.Lock()


def log_snapshot() -> None:
    """将当前观测快照以 INFO 级落日志（解决 179B「snapshot 无日志落点」X1 缺口）。"""
    _snap = snapshot()
    _log.info("[request_dedup_observe] snapshot inflight=%s inflight_peak=%s observed=%s "
              "distinct_fingerprints=%s fingerprint_top=%s",
              _snap.get("inflight"), _snap.get("inflight_peak"), _snap.get("observed"),
              _snap.get("distinct_fingerprints"), _snap.get("fingerprint_top"))


def _periodic_loop(interval: int) -> None:
    # 守护线程循环：周期性落快照日志；无 try/except（异常上抛，符合 cw2 红线——不新增静默 except）。
    while True:
        time.sleep(interval)
        log_snapshot()


def start_periodic_snapshot(interval_seconds: int = _periodic_interval) -> None:
    """启动周期快照日志守护线程（幂等；仅开关开启时生效；开关关则零 IO）。"""
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
    _maybe_start_periodic()
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
