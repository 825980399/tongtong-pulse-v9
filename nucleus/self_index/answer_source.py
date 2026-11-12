# -*- coding: utf-8 -*-
"""493.22 刀4：回答来源标识（三态）+ 按天/按会话占比统计。

背景：控制台看不出哪条是本地 fastpath 命中、哪条是大模型；影子日志缺
`answer_source` 字段。

★诚实口径（子代理实证）：影子 writer 层**只有本地候选信号**（explain.answer
非空/None），没有主笔实际回答文本、没有 LLM/混合标记（主笔开关
`ENABLE_M493_SELF_QUERY_FASTPATH=False`），因此 writer 层**不能凭空判定**
self_only/self_enhanced/llm_only 三态——填即编造。三态必须按**主笔链路的
真实 `source` 事件**判定（`chat_service.py` 的回复 payload）。

本模块提供：
  - `infer_answer_source(source, local_hit=None)`：按真实来源串映射三态，
    无法判定返回 None（不猜、不编造）。
  - `CONSOLE_PREFIX`：三态→控制台前缀。
  - `record_answer_source_event()`：主笔链路落一条真实来源事件（不污染影子日志）。
  - `stats_by_day()` / `stats_by_session()`：按天/会话统计三态占比（供面板饼图）。
"""
from __future__ import annotations

import json
import os
import time

from nucleus._silent_except import silent_exc

_EVENTS_REL = os.path.join("data", "monitor", "answer_source_events.jsonl")

# 三态（任务书定义）
SELF_ONLY = "self_only"          # 本地 fastpath 完全命中，无大模型调用
SELF_ENHANCED = "self_enhanced"  # 本地先给路径/器官，大模型再润色
LLM_ONLY = "llm_only"            # 本地没命中，直接走大模型

CONSOLE_PREFIX = {
    SELF_ONLY: "[曈曈自答]",
    SELF_ENHANCED: "[本地+大模型]",
    LLM_ONLY: "[大模型直答]",
}


def infer_answer_source(source: str, local_hit: bool = None) -> str | None:
    """按**真实**来源串判定三态；判定不了返回 None（不编造）。

    映射依据 `chat_service.py::_source_icon` 的既有语义分组：
      - `lung`/`remote`/`local_model`/`llm` = 大模型生成（证据：_source_icon :360-362
        注释「大模型生成（肺部、远程、本地模型）」）；
      - `self_index`/`fastpath`/`self_query_local` = 本地索引完全命中；
      - `hybrid`/`local_enhanced`/`self_enhanced` = 本地给线索 + 大模型润色；
      - 其余（system_status / code_sandbox / initiative / unknown）无真实信号 → None。
    `local_hit`（explain.answer 非空）仅作辅助：明确来源为大模型但本地也命中
    时判 self_enhanced；来源不明时**不**用 local_hit 硬猜成 self_only。
    """
    try:
        _s = str(source or "").lower()
        if not _s or _s == "unknown":
            return None
        _llm = any(k in _s for k in ("lung", "remote", "local_model", "llm", "model_gen"))
        _local = any(k in _s for k in ("self_index", "fastpath", "self_query_local"))
        _hybrid = any(k in _s for k in ("hybrid", "local_enhanced", "self_enhanced"))
        if _hybrid:
            return SELF_ENHANCED
        if _llm:
            return SELF_ENHANCED if local_hit else LLM_ONLY
        if _local:
            return SELF_ONLY
        return None  # 系统状态/代码沙箱/主动话题等：不是「回答来源」三态，如实不判
    except Exception as _e:
        silent_exc(_e, where="answer_source.infer_answer_source", level="debug")
        return None


def console_prefix(source: str, local_hit: bool = None) -> str:
    """控制台前缀；无真实判定信号时返回空串（不加假前缀）。"""
    _kind = infer_answer_source(source, local_hit)
    return CONSOLE_PREFIX.get(_kind, "") if _kind else ""


def record_answer_source_event(source: str, answer_source: str,
                               session_id: str = None,
                               path: str = None) -> bool:
    """主笔链路落一条真实来源事件（按天/会话统计用），不写影子日志。"""
    if not answer_source:
        return False
    _p = path or _EVENTS_REL
    try:
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
        _rec = {"ts": round(time.time(), 3), "source": str(source or ""),
                "answer_source": answer_source, "session_id": session_id}
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_rec, ensure_ascii=False) + "\n")
        return True
    except Exception as _e:
        silent_exc(_e, where="answer_source.record_answer_source_event", level="debug")
        return False


def _load_events(path: str = None) -> list[dict]:
    _p = path or _EVENTS_REL
    _out = []
    if not os.path.isfile(_p):
        return _out
    try:
        with open(_p, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _out.append(json.loads(_line))
                except Exception as _e:
                    silent_exc(_e, where="answer_source._load_events.json",
                               level="debug")
                    continue
    except Exception as _e:
        silent_exc(_e, where="answer_source._load_events", level="debug")
    return _out


def _ratio(rows: list[dict]) -> dict:
    _total = len(rows)
    _c = {k: 0 for k in (SELF_ONLY, SELF_ENHANCED, LLM_ONLY)}
    for _r in rows:
        _k = _r.get("answer_source")
        if _k in _c:
            _c[_k] += 1
    return {"total": _total, "counts": _c,
            "percent": {k: round(100.0 * v / _total, 1) if _total else 0.0
                        for k, v in _c.items()}}


def stats_by_day(path: str = None) -> dict:
    """按天统计三态占比（供面板饼图）。"""
    _by = {}
    for _r in _load_events(path):
        try:
            _day = time.strftime("%Y-%m-%d", time.localtime(float(_r.get("ts", 0))))
        except Exception as _e:
            silent_exc(_e, where="answer_source.stats_by_day.ts", level="debug")
            _day = "unknown"
        _by.setdefault(_day, []).append(_r)
    return {d: _ratio(rows) for d, rows in sorted(_by.items())}


def stats_by_session(path: str = None) -> dict:
    """按会话统计三态占比（供面板饼图）。"""
    _by = {}
    for _r in _load_events(path):
        _by.setdefault(str(_r.get("session_id") or "unknown"), []).append(_r)
    return {s: _ratio(rows) for s, rows in sorted(_by.items())}


def overall_stats(path: str = None) -> dict:
    """总体占比（面板饼图默认值）。"""
    return _ratio(_load_events(path))
