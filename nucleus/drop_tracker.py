# -*- coding: utf-8 -*-
"""★第360批 刀3：数据条目「静默丢失」统一计数出口。

背景（烛微 359 · 任务书刀3）：
    9 处 `except ...: continue` 在数据装载/回扫路径上**跳过坏条目**，
    跳过即丢、丢即对不上账 —— 356 批回写对账的对象正是这 9 处，
    而此前**没有任何一处**留下"我丢了多少"的读数，故障时只会表现为
    "总数莫名少了 N 条"却查不出丢在哪一环。

设计：
- 单一计数出口（铁律162）：各丢失点只负责 `note_drop(source, kind)`，
  口径统一，可观测、可对账、可断言。
- 只计数 + warning，**不改任何跳过行为**（跳过的仍跳过，零行为变化）。
- `kind` 语义：bad_line / bad_record / cast_failed / pair_none / node_error …
  便于回答"丢的是哪一类"。

不覆盖：正常业务过滤（如空行 `if not _line: continue`）—— 那不是"丢失"。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from nucleus._silent_except import silent_exc

_MODULE_LOGGER = logging.getLogger(__name__)

_COUNTS: dict[str, dict[str, int]] = {}
_LOCK = threading.RLock()
_WARN_KEY: set = set()      # 已告警过的 (source, kind)，避免刷屏
_WARN_MAX_PER_KEY = 3
_WARN_HITS: dict[tuple, int] = {}


def note_drop(source: str, kind: str, detail: str = "") -> dict[str, Any]:
    """记一次「条目被跳过/丢失」。返回 {"source","kind","count"}。"""
    _s = str(source or "unknown")
    _k = str(kind or "unknown")
    _out: dict[str, Any] = {"source": _s, "kind": _k, "count": 0}
    try:
        with _LOCK:
            _b = _COUNTS.setdefault(_s, {})
            _b[_k] = int(_b.get(_k) or 0) + 1
            _out["count"] = _b[_k]
            _key = (_s, _k)
            _n = _WARN_HITS.get(_key, 0)
            _emit = _n < _WARN_MAX_PER_KEY
            _WARN_HITS[_key] = _n + 1
    except Exception as _e:
        silent_exc(_e, where="drop_tracker::note_drop 计数", level="debug")
        _emit = False
    if _emit:
        try:
            _MODULE_LOGGER.warning(
                "[条目丢失] %s/%s 累计 %s 条%s（跳过即丢，回写对账会差这一笔）",
                _s, _k, _out.get("count"),
                "：%s" % detail if detail else "")
        except Exception as _e:
            silent_exc(_e, where="drop_tracker::note_drop warning", level="debug")
    return _out


def drop_counts() -> dict[str, dict[str, int]]:
    """各丢失点计数快照（无丢失 ⇒ 空 dict，不是 0，铁律155）。"""
    try:
        with _LOCK:
            return {_k: dict(_v) for _k, _v in _COUNTS.items()}
    except Exception as _e:
        silent_exc(_e, where="drop_tracker::drop_counts", level="debug")
        return {}


def drop_total(source: str | None = None) -> int:
    """丢失总数；`source=None` ⇒ 全库合计。"""
    try:
        with _LOCK:
            if source is None:
                return sum(int(v) for _b in _COUNTS.values() for v in _b.values())
            return sum(int(v) for v in (_COUNTS.get(str(source)) or {}).values())
    except Exception as _e:
        silent_exc(_e, where="drop_tracker::drop_total", level="debug")
        return 0


def reset_for_test() -> None:
    """清空计数（仅供测试）。"""
    try:
        with _LOCK:
            _COUNTS.clear()
            _WARN_HITS.clear()
    except Exception as _e:
        silent_exc(_e, where="drop_tracker::reset_for_test", level="debug")
