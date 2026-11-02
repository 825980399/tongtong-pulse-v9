# -*- coding: utf-8 -*-
"""dialogue_process_trace —— 426 刀5：把「对话中间过程」真正落盘。

为什么要有它
------------
改前这些中间结果**只散落在日志行里**，且每样只露一角：
  · QICA 只打 `语义理解: intent=... confidence=... method=...`
  · 皮层只打 `QICA意图路由: 建议方法=... 知识路径=[...]`
  · 情绪只进 `_current_emotion` 内存变量
  · 经验命中只打 DEBUG 一行
  · 渠道只有 `[353均衡] picked=...` 那一行，且**没有 correlation_id**，
    无法与某一次对话对上。
于是"这一轮到底识别成什么、拿了哪些知识、命中了哪条经验"事后不可复原。

记录边界（一次对话一条）
------------------------
`begin()` 在 QICA 分类结果落地时开一条；`stamp()` 在路由阶段补中间结果；
`finish()` 在 `InferenceEvent.RESULT` 回来时收口（带方法、答案预览、端到端耗时）。

落盘：`data/experience/dialogue_process_YYYYMMDD.jsonl`，**append-only**，
一行一条 JSON。不重写、不聚合、不压缩 —— 便于直接 grep / 用 jq 过滤。

刻意不存什么
------------
不存 prompt 全文、不存回答全文（只留 120 字预览）、不存文件内容/代码块、
不存 strategy_context 整块。一句话：**存判据，不存素材**。

三个硬约束
----------
1. 永不抛异常（任何失败都只是少一条记录，绝不影响对话）；
2. 有界：pending 最多 `_MAX_PENDING` 条，超了把最旧的以 `stage=dropped_overflow`
   收口；超 `_TTL_SEC` 未收口的以 `stage=dropped_stale` 收口，防内存泄漏；
3. 可关：`config.ENABLE_M426_DIALOGUE_TRACE=False` ⇒ 完全静默、不建文件。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("DialogueProcessTrace")

#: 项目根（本文件在 nucleus/ 下）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 未收口记录上限（一次对话一条；正常并发几十条，200 足够）
_MAX_PENDING = 200
#: 未收口记录的存活上限（秒）：推理最长 30-50s，10 分钟足够兜住
_TTL_SEC = 600
#: 单条记录里各列表字段的最大长度（存判据，不存素材）
_MAX_LIST = 10
#: 文本字段截断
_MAX_TEXT = 200


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception as _e:
        silent_exc(_e, where="dialogue_process_trace::_cfg", level="debug")
        return default


def enabled() -> bool:
    """总开关（`ENABLE_M426_DIALOGUE_TRACE`，默认 True）。"""
    return bool(_cfg("ENABLE_M426_DIALOGUE_TRACE", True))


def _root() -> str:
    """数据根。测试用 `M426_TRACE_ROOT` 显式注入（铁律 38：先改路径）。"""
    _env = os.environ.get("M426_TRACE_ROOT")
    if _env:
        return os.path.abspath(os.path.normpath(_env))
    return _ROOT


def _out_dir() -> str:
    return os.path.join(_root(), "data", "experience")


def out_path(now: float | None = None) -> str:
    """当天的 jsonl 路径（按本地日期切分，跨天自动换文件）。"""
    _t = time.localtime(float(now if now is not None else time.time()))
    return os.path.join(_out_dir(), "dialogue_process_{}.jsonl".format(time.strftime("%Y%m%d", _t)))


#: correlation_id -> 记录（有界，见 _MAX_PENDING）
_pending: dict[str, dict] = {}


def _clip(value: Any, limit: int = _MAX_TEXT) -> str:
    # ★None 必须变空串，不能变字符串 "None" —— 否则报告里会出现一堆假值，
    #   读的人分不清"没取到"和"取到的就是 None"。
    if value is None:
        return ""
    try:
        _s = value if isinstance(value, str) else str(value)
    except Exception as _e:
        silent_exc(_e, where="dialogue_process_trace::_clip", level="debug")
        return ""
    return _s[:limit]


def _list_of(value: Any, limit: int = _MAX_LIST, item_limit: int = 40) -> list:
    """把任意值收敛成「短、可序列化、有界」的列表。"""
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [_clip(value, item_limit)]
    try:
        _it = list(value)
    except TypeError as _e:
        silent_exc(_e, where="dialogue_process_trace::_list_of", level="debug")
        return [_clip(value, item_limit)]
    return [_clip(x, item_limit) for x in _it[:limit]]


def _sweep(now: float) -> None:
    """把超时未收口的记录以 dropped_stale 落盘（防泄漏）。"""
    _stale = [k for k, v in _pending.items() if now - float(v.get("_t0", now)) > _TTL_SEC]
    for _k in _stale:
        try:
            _finish_one(_k, stage="dropped_stale")
        except Exception as _e:
            silent_exc(_e, where="dialogue_process_trace::_sweep", level="debug")


def begin(correlation_id: str, question: str, user_name: str = "", **extra) -> None:
    """开一条对话过程记录（同一 correlation_id 重复调用以最后一次为准）。"""
    if not enabled():
        return
    try:
        _cid = str(correlation_id or "")
        if not _cid:
            return
        _now = time.time()
        if len(_pending) >= _MAX_PENDING:
            _sweep(_now)
        if len(_pending) >= _MAX_PENDING:
            # 仍满：把最旧的收口成 dropped_overflow（宁可丢最旧，不丢新的）
            _oldest = min(_pending, key=lambda k: float(_pending[k].get("_t0", _now)))
            _finish_one(_oldest, stage="dropped_overflow")
        # ★begin 必须**幂等合并**：一轮对话会被两处调用
        #   （QICA 分类落地、_route_by_intent 兜底），若第二次直接覆盖，
        #   第一次刚 stamp 进去的 intent_type/keywords/confidence 会全被抹掉。
        _exist = _pending.get(_cid)
        if isinstance(_exist, dict):
            if not _exist.get("user_question"):
                _exist["user_question"] = _clip(question)
            if not _exist.get("user_name"):
                _exist["user_name"] = _clip(user_name, 40)
            for _k, _v in (extra or {}).items():
                _exist.setdefault(str(_k), _v)
            return
        _rec = {
            "_t0": _now,
            "ts": round(_now, 3),
            "correlation_id": _cid,
            "user_question": _clip(question),
            "user_name": _clip(user_name, 40),
        }
        for _k, _v in (extra or {}).items():
            _rec[str(_k)] = _v
        _pending[_cid] = _rec
    except Exception as _e:
        silent_exc(_e, where="dialogue_process_trace::begin", level="debug")


def stamp(correlation_id: str, **fields) -> None:
    """给已开的记录补中间结果（记录不存在则忽略 —— 说明这一轮没经过 begin）。"""
    if not enabled():
        return
    try:
        _rec = _pending.get(str(correlation_id or ""))
        if _rec is None:
            return
        for _k, _v in (fields or {}).items():
            if _v is None:
                continue
            _rec[str(_k)] = _v
    except Exception as _e:
        silent_exc(_e, where="dialogue_process_trace::stamp", level="debug")


def _sanitize(rec: dict) -> dict:
    """按白名单收敛类型与长度，保证一行一条、可 JSON 序列化。"""
    _out = {
        "ts": rec.get("ts"),
        "ts_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(rec.get("ts") or 0))),
        "correlation_id": _clip(rec.get("correlation_id"), 60),
        "stage": _clip(rec.get("stage") or "finished", 24),
        "user_question": _clip(rec.get("user_question")),
        "user_name": _clip(rec.get("user_name"), 40),
        "intent": _clip(rec.get("intent"), 40),
        "intent_type": _clip(rec.get("intent_type"), 40),
        "intent_confidence": rec.get("intent_confidence"),
        "keywords": _list_of(rec.get("keywords")),
        "knowledge_paths": _list_of(rec.get("knowledge_paths")),
        "suggested_method": _clip(rec.get("suggested_method"), 40),
        "strategy_method": _clip(rec.get("strategy_method"), 40),
        "reasoning_preference": _clip(rec.get("reasoning_preference"), 40),
        "emotion": _clip(rec.get("emotion"), 40),
        "interest_changes": _list_of(rec.get("interest_changes"), 5),
        "experience_hits": _list_of(rec.get("experience_hits"), 5, 60),
        "channel_candidates": _list_of(rec.get("channel_candidates"), 8, 32),
        "channel_selected": _clip(rec.get("channel_selected"), 40) or None,
        "channel_reason": _clip(rec.get("channel_reason"), 120),
        "method": _clip(rec.get("method"), 40),
        "answer_preview": _clip(rec.get("answer_preview"), 120),
        "latency_ms": rec.get("latency_ms"),
        "error": _clip(rec.get("error"), 120) or None,
    }
    return _out


def _finish_one(correlation_id: str, stage: str = "finished", **fields) -> dict | None:
    """内部收口：写一行 + 从 pending 移除。"""
    _rec = _pending.pop(str(correlation_id or ""), None)
    if _rec is None:
        return None
    for _k, _v in (fields or {}).items():
        if _v is not None:
            _rec[str(_k)] = _v
    # 端到端耗时：调用方没给就从开记录那一刻算（内层推理耗时只是其中一段）
    if _rec.get("latency_ms") is None and _rec.get("_t0"):
        try:
            _rec["latency_ms"] = round((time.time() - float(_rec["_t0"])) * 1000, 1)
        except (TypeError, ValueError) as _e:
            silent_exc(_e, where="dialogue_process_trace::_finish_one:latency", level="debug")
    _rec["stage"] = stage
    _row = _sanitize(_rec)
    _path = out_path(_rec.get("ts"))
    try:
        from nucleus.data.write_guard import guard_write as _gw
        if not _gw(_path, explicit=bool(os.environ.get("M426_TRACE_ROOT")),
                   component="DialogueProcessTrace"):
            return _row
    except Exception as _e:
        silent_exc(_e, where="dialogue_process_trace::_finish_one:guard", level="debug")
    try:
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        with open(_path, "a", encoding="utf-8", newline="\n") as _f:
            _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
    except Exception as _e:
        silent_exc(_e, where="dialogue_process_trace::_finish_one:write", level="debug")
        _module_logger.debug("[426刀5] 对话过程落盘失败: %s", _e)
    return _row


def finish(correlation_id: str, **fields) -> dict | None:
    """收口并落盘一行。返回落盘的内容（供单测/活体自检），失败返回 None。"""
    if not enabled():
        return None
    try:
        return _finish_one(correlation_id, stage="finished", **fields)
    except Exception as _e:
        silent_exc(_e, where="dialogue_process_trace::finish", level="debug")
        return None


def stats() -> dict:
    """只读自检：pending 条数、今天写了几行、开关状态。"""
    _path = out_path()
    _n = 0
    try:
        if os.path.exists(_path):
            with open(_path, "r", encoding="utf-8", errors="ignore") as _f:
                for _ in _f:
                    if _.strip():
                        _n += 1
    except Exception as _e:
        silent_exc(_e, where="dialogue_process_trace::stats", level="debug")
    return {"enabled": enabled(), "pending": len(_pending), "path": _path, "lines": _n}


def version() -> str:
    return "426.1"
