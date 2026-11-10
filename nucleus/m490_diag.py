# -*- coding: utf-8 -*-
"""★490-5/6 + 490-3：证据注入双闸归因 / 默认层迁移候选 —— 共享诊断落盘层。

纯观测：所有函数只累计与落盘，绝不参与任何判定、过滤或返回值。
开关关闭时函数首行即返回，逐字回到改前行为。
落盘统一经 nucleus.data.write_guard.guard_write 守卫（测试环境拒写生产 data/）。
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 490-5/6 分数分桶边界（与任务书一致）
_BUCKET_KEYS = ("<0.45", "0.45-0.55", "0.55-0.65", "0.65-0.8", ">=0.8")


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception as _e:
        silent_exc(_e, where="m490_diag::_cfg", level="debug")
        return default


def stats_on() -> bool:
    """490-5/6 共用开关。"""
    return bool(_cfg("ENABLE_M490_EVIDENCE_TRACE_STATS", False))


def layer_log_on() -> bool:
    """490-3 阶段A：仅 log 模式观测，其他任何值都不产生行为。"""
    return str(_cfg("M490_LAYER_DEFAULT_MODE", "off")) == "log"


def _bucket(score: float) -> str:
    if score < 0.45:
        return _BUCKET_KEYS[0]
    if score < 0.55:
        return _BUCKET_KEYS[1]
    if score < 0.65:
        return _BUCKET_KEYS[2]
    if score < 0.8:
        return _BUCKET_KEYS[3]
    return _BUCKET_KEYS[4]


def score_histogram(scores) -> dict[str, int]:
    """把一串分数摊进 5 个桶（读不到/非数值的分数跳过，不臆造）。"""
    _h = {k: 0 for k in _BUCKET_KEYS}
    for _s in (scores or ()):
        try:
            _h[_bucket(float(_s))] += 1
        except Exception as _e:
            silent_exc(_e, where="m490_diag::score_histogram", level="debug")
    return _h


def _path(prefix: str) -> str:
    _day = time.strftime("%Y%m%d", time.localtime())
    return os.path.join(_ROOT, "data", "monitor", f"{prefix}_{_day}.jsonl")


def append_jsonl(prefix: str, row: dict) -> dict | None:
    """通用落盘。返回落下的行；拒写或失败返回 None（观测失败只少一行）。"""
    _p = _path(prefix)
    try:
        from nucleus.data.write_guard import guard_write
        if not guard_write(_p, explicit=False, component="M490Diag"):
            return None
    except Exception as _e:
        silent_exc(_e, where="m490_diag::append_jsonl:guard", level="debug")
        return None
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8", newline="\n") as _f:
            _f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
    except Exception as _e:
        silent_exc(_e, where="m490_diag::append_jsonl:write", level="debug")
        return None


# ---------------------------------------------------------------------------
# 490-5 / 490-6：双闸数据「拼在一行」
# ---------------------------------------------------------------------------
#: 第一道闸的数据暂存到请求级 ContextVar；第二道闸合并后落一行。
#: 若同 context 上一请求只走到第一道，则由下一次 note_first_gate 补落一行。
_pending: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "m490_evidence_pending", default=None)

_EVIDENCE_PREFIX = "evidence_injection"


def _flush(row: dict) -> dict:
    append_jsonl(_EVIDENCE_PREFIX, row)
    return row


def _new_row(request_id: str) -> dict:
    return {
        "ts": round(time.time(), 3),
        "request_id": request_id,
        "origin": "dual_gate",
        "hits_raw": 0,
        "hits_kept_first": 0,
        "blocked_first": 0,
        "hits_kept_second": 0,
        "score_hist": score_histogram(()),
        "score_hist_second": score_histogram(()),
        "blocked_scores_first": [],
        "blocked_scores_second": [],
        "dropped": {},
        "injected": False,
        "fragment_len": 0,
    }


def stable_request_id(query: str) -> str:
    return hashlib.md5(str(query or "").encode("utf-8")).hexdigest()[:16]


def note_first_gate(query: str, hits_raw: int, kept_first: int,
                    all_scores, blocked_scores) -> dict | None:
    """490-5：第一道闸（evidence_trace.build_evidence_block）原始命中归因。

    只加计数，绝不改过滤逻辑。返回本次填好的行（未落盘，等第二道合并）。
    """
    if not stats_on():
        return None
    try:
        _prev = _pending.get()
        if _prev is not None:
            _flush(_prev)  # 上一请求没走到第二道 ⇒ 补落一行
        _row = _new_row(stable_request_id(query))
        _row["hits_raw"] = int(hits_raw)
        _row["hits_kept_first"] = int(kept_first)
        _row["blocked_first"] = max(0, int(hits_raw) - int(kept_first))
        _row["score_hist"] = score_histogram(all_scores)
        _row["blocked_scores_first"] = [
            round(float(_s), 4) for _s in list(blocked_scores)[:5]
        ]
        _pending.set(_row)
        return _row
    except Exception as _e:
        silent_exc(_e, where="m490_diag::note_first_gate", level="debug")
        return None


def note_second_gate(kept_second: int, all_scores, blocked_scores,
                     dropped: dict | None = None, injected: bool = False,
                     fragment_len: int = 0, query: str = "") -> dict | None:
    """490-6：第二道闸（evidence_injection.build_injection）合并落一行。

    三个字段 hits_raw / hits_kept_first / hits_kept_second 恒有数。
    """
    if not stats_on():
        return None
    try:
        _row = _pending.get()
        if _row is None:
            _row = _new_row(stable_request_id(query))
        _row["hits_kept_second"] = int(kept_second)
        _row["score_hist_second"] = score_histogram(all_scores)
        _row["blocked_scores_second"] = [
            round(float(_s), 4) for _s in list(blocked_scores)[:5]
        ]
        _row["dropped"] = dict(dropped or {})
        _row["injected"] = bool(injected)
        _row["fragment_len"] = int(fragment_len)
        _pending.set(None)
        return _flush(_row)
    except Exception as _e:
        silent_exc(_e, where="m490_diag::note_second_gate", level="debug")
        _pending.set(None)
        return None


# ---------------------------------------------------------------------------
# 490-3 阶段A：默认层迁移候选（只观测，不切默认值）
# ---------------------------------------------------------------------------
_layer_counts: dict[tuple, int] = {}
_layer_lock = threading.Lock()
_layer_last_flush = 0.0
_LAYER_FLUSH_INTERVAL = 60.0
_LAYER_FLUSH_STEP = 50
_LAYER_PREFIX = "layer_migration_candidates"


def infer_layer_quiet(event_type: str) -> str:
    """与 PulseCore._infer_layer 等价，但不打 warning（零副作用）。

    注册表开启时它是唯一真源；关闭时逐字回落旧默认映射。阶段A 就是拿
    「若按注册表推断该落哪层」的差异清单，纯查表即可，不触碰任何路由。
    """
    _et = str(event_type or "")
    try:
        if bool(_cfg("ENABLE_M484_EVENT_REGISTRY", False)):
            from nucleus.event_layers import EVENT_LAYER_MAP, UNKNOWN_LAYER
            return str(EVENT_LAYER_MAP.get(_et, UNKNOWN_LAYER))
        from nucleus.pulse.PulseCore import _DEFAULT_LAYER_MAP
        return str(_DEFAULT_LAYER_MAP.get(_et, "L1"))
    except Exception as _e:
        silent_exc(_e, where="m490_diag::infer_layer_quiet", level="debug")
        return "L1"


def _flush_layer_snapshot() -> None:
    with _layer_lock:
        _snap = [{"organ": k[0], "event": k[1], "current_layer": k[2],
                  "inferred_layer": k[3], "count": v,
                  "ts": round(time.time(), 3)}
                 for k, v in _layer_counts.items()]
        _layer_last_flush = time.time()
    for _row in _snap:
        append_jsonl(_LAYER_PREFIX, _row)


def note_layer_candidate(organ: str, event_type: str,
                         current_layer: str, inferred_layer: str) -> dict | None:
    """490-3 阶段A：记录「现层 ≠ 推层」的候选（按 organ/event/层 聚合次数）。

    **绝不修改 layer**，调用方仍按原默认 L1 走。
    """
    if not layer_log_on():
        return None
    if str(inferred_layer) == str(current_layer):
        return None
    try:
        _key = (str(organ), str(event_type), str(current_layer), str(inferred_layer))
        with _layer_lock:
            _layer_counts[_key] = _layer_counts.get(_key, 0) + 1
            _count = _layer_counts[_key]
            _due = ((time.time() - _layer_last_flush) >= _LAYER_FLUSH_INTERVAL
                    or _count % _LAYER_FLUSH_STEP == 0)
        if _due:
            _flush_layer_snapshot()
        return {"organ": _key[0], "event": _key[1],
                "current_layer": _key[2], "inferred_layer": _key[3],
                "count": _count}
    except Exception as _e:
        silent_exc(_e, where="m490_diag::note_layer_candidate", level="debug")
        return None


def observe_layer(organ: str, event_type: str, current_layer: str) -> dict | None:
    """490-3 阶段A 一站式入口：BasePulseOrgan._send 只加这一行。

    关开关时首行返回，绝不改 layer 默认值、绝不参与路由。
    """
    if not layer_log_on():
        return None
    return note_layer_candidate(organ, event_type, current_layer,
                                infer_layer_quiet(event_type))
