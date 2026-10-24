# -*- coding: utf-8 -*-
"""★第360批 刀2：语义层「**调用失败** vs **真无结果**」统一留痕出口。

背景（烛微 359 前置 · 任务书刀2）：
    语义层 4 件 `except Exception: return {}`，异常时**静默返回空结果**，
    上层无法区分「真的没有召回」与「调用失败了」——把故障读成"无召回"，
    是典型的**故障伪装空结果**。

T0 实测偏差（必须认账）：任务书给的 4 个行号（VectorStore:62 /
VectorEncoder:96 / SemanticKernelService:39 / AsyncEncodeQueue:34）
指向的都是 `_load_config()` —— 语义是「**配置加载失败**回落空配置」，
**不是**召回路径。真正的召回类伪装是 `VectorStore.search_by_vector`
的 `except → _logger.debug(...) → return []`（debug 级等于不可见）。
故本模块同时服务两类落点：配置加载（4 处）+ 召回失败（1 处）。

设计：
- 单一留痕出口（避免 4 个文件各写一份计数器 ⇒ 口径打架，铁律162）。
- 计数 + 末次失败快照 ⇒ 上层可区分"空结果是真无召回还是故障"。
- 只留痕，**不改变任何返回值语义**（仍返回空容器，零行为变化）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from nucleus._silent_except import silent_exc

_MODULE_LOGGER = logging.getLogger(__name__)

_COUNTS: dict[str, dict[str, int]] = {}
_LAST: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()


def note_semantic_failure(component: str, op: str,
                          exc: BaseException) -> dict[str, Any]:
    """记录一次**调用失败**（≠ 真无结果）。返回本次记录（含累计次数）。"""
    _c = str(component or "unknown")
    _o = str(op or "unknown")
    _rec: dict[str, Any] = {
        "component": _c, "op": _o, "count": 0,
        "error": "%s: %s" % (type(exc).__name__, exc),
    }
    try:
        with _LOCK:
            _bucket = _COUNTS.setdefault(_c, {})
            _bucket[_o] = int(_bucket.get(_o) or 0) + 1
            _rec["count"] = _bucket[_o]
            _LAST[_c] = dict(_rec)
    except Exception as _e:
        silent_exc(_e, where="sem_failure_note::note_semantic_failure 计数")
    try:
        _MODULE_LOGGER.warning(
            "[语义层故障] %s.%s 失败（累计 %s 次）—— 返回空结果**不代表无召回**，"
            "上层须按失败标记区分：%s: %s",
            _c, _o, _rec.get("count"), type(exc).__name__, exc)
    except Exception as _e:
        silent_exc(_e, where="sem_failure_note::note_semantic_failure warning")
    return _rec


def sem_failure_counts() -> dict[str, dict[str, int]]:
    """各组件的失败计数（只读快照；无失败时为空 dict）。"""
    try:
        with _LOCK:
            return {_k: dict(_v) for _k, _v in _COUNTS.items()}
    except Exception as _e:
        silent_exc(_e, where="sem_failure_note::sem_failure_counts")
        return {}


def last_failure(component: str) -> dict[str, Any] | None:
    """该组件**最后一次**失败快照；从未失败 ⇒ None（不是空 dict，铁律155）。"""
    try:
        with _LOCK:
            _r = _LAST.get(str(component or ""))
            return dict(_r) if _r else None
    except Exception as _e:
        silent_exc(_e, where="sem_failure_note::last_failure")
        return None


def reset_for_test() -> None:
    """清空计数（仅供测试；生产不调用）。"""
    try:
        with _LOCK:
            _COUNTS.clear()
            _LAST.clear()
    except Exception as _e:
        silent_exc(_e, where="sem_failure_note::reset_for_test")
