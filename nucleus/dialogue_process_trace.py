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

import datetime
import json
import os
import threading
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


def _trace_rows(value: Any, limit: int = 12, item_limit: int = 60) -> list:
    """★454刀5：plan / step_results 是**列表套字典**，不能走 `_list_of`
    （那个会把整个 dict 截成一个字符串）。这里保留结构，只截字符串值。
    """
    if value is None:
        return []
    try:
        _it = list(value)
    except TypeError as _e:
        silent_exc(_e, where="dialogue_process_trace::_trace_rows", level="debug")
        return []
    _out = []
    for _r in _it[:limit]:
        if isinstance(_r, dict):
            _out.append({str(k): (_clip(v, item_limit) if isinstance(v, str) else v)
                          for k, v in list(_r.items())[:12]})
        else:
            _out.append(_clip(_r, item_limit))
    return _out


# ★454刀5：多步执行器把 plan / step_results 暂存在线程局部，
#   finish() 与环形缓冲两处 trace 都从同一个口读，避免「只有字段没有内容」。
_M454_TLS = threading.local()
_M454_LAST: dict = {"plan": [], "step_results": []}


def set_plan(plan: list | None, step_results: list | None) -> None:
    _st = {"plan": list(plan or []), "step_results": list(step_results or [])}
    _M454_TLS.state = _st
    _M454_LAST.update(_st)


def get_plan() -> dict:
    _st = getattr(_M454_TLS, "state", None)
    return dict(_st or _M454_LAST)


def clear_plan() -> None:
    _M454_TLS.state = None
    _M454_LAST.update({"plan": [], "step_results": []})


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
        # ★452刀2：finish() 传进来的步数/证据数/寒暄标记。
        #   `_sanitize` 是**显式白名单**，不加这三行的话 finish(step_count=…)
        #   会被静默丢掉 —— 这是本刀最容易漏的地方。
        "step_count": rec.get("step_count"),
        "evidence_count": rec.get("evidence_count"),
        "is_chitchat": bool(rec.get("is_chitchat") or False),
        # ★454刀5：显式白名单必须登记这两个字段，否则 finish(plan=…)
        #   会被静默丢掉（452 批同型坑）。只存摘要，不存用户原文。
        "plan": _trace_rows(rec.get("plan")),
        "step_results": _trace_rows(rec.get("step_results")),
        # ★456刀3/刀4：意图 handler 影子（只记录，不执行）。
        #   `_sanitize` 是**显式白名单** —— 不在这里登记，stamp() 传进来会被静默丢掉
        #   （452/454 同型坑已经栽过两次）。
        "qica_intent_type": _clip(rec.get("qica_intent_type"), 40) or None,
        "would_handler": _clip(rec.get("would_handler"), 40) or None,
        "code_evidence_count": rec.get("code_evidence_count"),
        "reason": _clip(rec.get("reason"), 120) or None,
        "handled_by": _clip(rec.get("handled_by"), 40) or None,
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
    # ★454刀5：多步执行器写的 plan/step_results 自动补进本行
    _m454_state = get_plan()
    if _m454_state.get("plan") or _m454_state.get("step_results"):
        _rec.setdefault("plan", _m454_state.get("plan"))
        _rec.setdefault("step_results", _m454_state.get("step_results"))
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
    """收口并落盘一行。返回落盘的内容（供单测/活体自检），失败返回 None。

    ★452刀2：支持 `step_count` / `evidence_count` / `is_chitchat` 三个新字段
      （已在 `_sanitize` 白名单里登记）。不传 ⇒ 落 None / False，**不填假 0**。
    """
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


def _retention_days() -> int:
    """留存天数 ← `config.DIALOGUE_TRACE_RETENTION_DAYS`（默认 30）。

    非法值（bool/非 int/<=0）一律返回 0 = **不清理**。方向很关键：
    配置写坏时宁可让文件攒着，也不能把不该删的删掉（不可逆）。
    """
    try:
        import config
        _v = getattr(config, "DIALOGUE_TRACE_RETENTION_DAYS", 30)
        if isinstance(_v, bool) or not isinstance(_v, int) or _v <= 0:
            return 0
        return int(_v)
    except Exception as _e:
        silent_exc(_e, where="dialogue_process_trace::_retention_days", level="debug")
        return 0


def _day_of_name(fn: str):
    """从 `dialogue_process_YYYYMMDD.jsonl` 里取日期；取不到返回 None。"""
    _p = "dialogue_process_"
    if not fn.startswith(_p) or not fn.endswith(".jsonl"):
        return None
    _s = fn[len(_p):-len(".jsonl")]
    if len(_s) != 8 or not _s.isdigit():
        return None
    try:
        return datetime.date(int(_s[0:4]), int(_s[4:6]), int(_s[6:8]))
    except ValueError as _e:
        silent_exc(_e, where="dialogue_process_trace::_day_of_name", level="debug")
        return None


def sweep_old(days: int | None = None, dry_run: bool = False,
              now: float | None = None) -> dict:
    """★★431刀4：删掉 `days` 天前的整份对话落盘文件（**默认 30 天**）。

    判据：文件名里的日期 `< 今日 - days`。今日与 `days` 天前都保留
    （「30 天滚动」=留最近 30 天），`days=30` 时第 31 天的那份才删。

    Returns:
        ``{"enabled","days","scanned","removed","freed_bytes","files","kept",
           "dry_run","failed","reason"}``

    ★永不抛异常：清理是夜间链的搭车项，炸了也只少清一次。
    ★失败可重试：本函数无「今天已清」内存标记，失败下次调用照常再试
      （对齐 `call_recorder._rotate` 的 371A5 教训）。
    """
    _d = _retention_days() if days is None else int(days)
    _out = {"enabled": enabled(), "days": _d, "scanned": 0, "removed": 0,
            "freed_bytes": 0, "files": [], "kept": 0, "dry_run": bool(dry_run),
            "failed": 0, "reason": ""}
    if not _out["enabled"]:
        _out["reason"] = "trace_disabled"
        return _out
    if _d <= 0:
        _out["reason"] = "retention_days_invalid"
        return _out
    try:
        _dir = _out_dir()
        if not os.path.isdir(_dir):
            _out["reason"] = "no_dir"
            return _out
        # ★DTZ012 是有意屏蔽的：这里**要的就是本地日期** —— 落盘文件按本地
        #   日期切分（`dialogue_process_YYYYMMDD.jsonl`），用 UTC 会差一天。
        _today = datetime.date.fromtimestamp(  # noqa: DTZ012
            float(now if now is not None else time.time()))
        _cutoff = _today - datetime.timedelta(days=_d)
        for _fn in sorted(os.listdir(_dir)):
            _day = _day_of_name(_fn)
            if _day is None:
                continue
            _out["scanned"] += 1
            if _day >= _cutoff:
                _out["kept"] += 1
                continue
            _fp = os.path.join(_dir, _fn)
            try:
                _sz = os.path.getsize(_fp)
            except OSError as _se:
                silent_exc(_se, where="dialogue_process_trace::sweep_old stat",
                           level="debug")
                _sz = 0
            if dry_run:
                _out["files"].append({"name": _fn, "day": str(_day), "bytes": _sz})
                _out["removed"] += 1
                _out["freed_bytes"] += _sz
                continue
            try:
                os.remove(_fp)
            except OSError as _re:
                # 单个文件删不掉不足以让整轮失败，但要计数出声（下次再来）
                _out["failed"] += 1
                silent_exc(_re, where="dialogue_process_trace::sweep_old remove",
                           level="warning")
                continue
            _out["files"].append({"name": _fn, "day": str(_day), "bytes": _sz})
            _out["removed"] += 1
            _out["freed_bytes"] += _sz
        _out["reason"] = "ok"
        if _out["removed"]:
            _module_logger.info(
                "[431刀4] 对话落盘清理%s：删 %d 个文件，释放 %.2f MB "
                "(留存 %d 天，截止 %s，扫描 %d 份，清单=%s)",
                "(演练)" if dry_run else "", _out["removed"],
                _out["freed_bytes"] / 1048576.0, _d, _cutoff, _out["scanned"],
                ",".join(x["name"] for x in _out["files"]))
        else:
            _module_logger.info(
                "[431刀4] 对话落盘清理%s：无过期文件（留存 %d 天，截止 %s，"
                "扫描 %d 份，保留 %d 份）",
                "(演练)" if dry_run else "", _d, _cutoff, _out["scanned"],
                _out["kept"])
        if _out["failed"]:
            _module_logger.warning(
                "[431刀4] 对话落盘清理有 %d 个过期文件未能删除（下次重试）",
                _out["failed"])
    except Exception as _e:
        silent_exc(_e, where="dialogue_process_trace::sweep_old", level="warning")
        _out["reason"] = f"error:{type(_e).__name__}"
    return _out


def version() -> str:
    return "431.1"
