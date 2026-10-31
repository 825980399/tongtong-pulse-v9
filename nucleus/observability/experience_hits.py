# -*- coding: utf-8 -*-
"""经验池命中（★411 批刀4）—— **先只记不拼**。

背景与 T0 实测
--------------
任务书说「经验池 377 条，命中后 `search_experience` 恒 None，不进 prompt」。
T0 核实发现这是**两件事被同一个字段名混在一起**：

  * `memory_context["search_experience"]` 是**搜索工具经验**（哪个工具成功率高），
    数据源 `data/context/search_experience.json`（4 个主题键、100 条计数），
    由 `PulseInnerWorld.get_search_experience()` 按关键词重叠匹配 ——
    它服务的是"这次该用哪个检索工具"，**不是**经验池那 378 条体验记忆；
  * 经验池在 `data/experience/experience_pool.json`（`ExperiencePool`，378 条），
    从来没有进过 `memory_context`。

直接按任务书写法 `context["search_experience"] = 经验池top2` 会把**工具选择语义**
覆盖成"体验摘要" —— 下游读 `best_tool` / `success_rate` 的地方会读到不存在的东西。
所以本模块产出**独立键** `experience_hits`，`search_experience` 原语义分毫不动。

本批只做**观测**：算命中、打 DEBUG、把结果放进 context 供日后渲染侧取用；
**不拼进 prompt**（任务书要求"一周后再开拼"）。开关 `ENABLE_M411_EXPERIENCE_HITS`
关掉即完全静默。
"""

from __future__ import annotations

import re
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("ExperienceHits")

_M411_EH_VERSION = "411.4"

#: 单条摘要截断长度（任务书要求每条 80 字）
SNIPPET_CHARS = 80

#: 拉取候选的默认上限（池子 378 条，200 条足够覆盖近期；全量扫也没必要）
CANDIDATE_LIMIT = 200

#: 中文词片（2~4 字）——与仓内既有 `_make_experience_key` 同口径
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,4}")


def enabled() -> bool:
    """灰度开关 `ENABLE_M411_EXPERIENCE_HITS`；缺省 True（本刀只读+日志，无副作用）。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M411_EXPERIENCE_HITS", True))
    except Exception as _e:
        silent_exc(_e, where="observability.experience_hits:enabled")
        return True


def tokens_of(text: str) -> set[str]:
    """中文 2~4 字词片集合（小写化，去重）。"""
    return {m.group().lower() for m in _TOKEN_RE.finditer(str(text or ""))}


def _text_of(exp: dict) -> str:
    """一条体验的可读文本：优先 summary，其次 content，最后 motivation。"""
    for _k in ("summary", "content", "motivation"):
        _v = exp.get(_k)
        if isinstance(_v, str) and _v.strip():
            return _v.strip()
    return ""


def score_one(question_tokens: set[str], exp: dict) -> int:
    """命中分 = 问题词片与体验文本词片的交集大小（0 表示不命中）。"""
    if not question_tokens or not isinstance(exp, dict):
        return 0
    _t = tokens_of(_text_of(exp))
    return len(question_tokens & _t)


def top_hits(question: str, top_k: int = 2,
             pool: Any = None, limit: int = CANDIDATE_LIMIT) -> list[dict[str, Any]]:
    """取与问题最相关的 top_k 条经验。

    Args:
        question: 用户问题原文。
        top_k: 返回条数（任务书要求 2）。
        pool: 经验池实例；None 时取 `get_experience_pool()` 单例。
        limit: 候选上限（按时间倒序取最近 limit 条）。

    Returns:
        列表，每条 ``{"id", "score", "text", "ts", "motivation"}``；不命中返回 ``[]``
        （**不返回空壳条目**——调用方据长度判"这次没命中"）。
    """
    if not enabled():
        return []
    _q = tokens_of(question)
    if not _q:
        return []
    if pool is None:
        try:
            from nucleus.mnemosyne.experience_pool import get_experience_pool
            pool = get_experience_pool()
        except Exception as _e:
            silent_exc(_e, where="observability.experience_hits:top_hits:pool", level="debug")
            return []
    try:
        _cands = pool.query_experiences(limit=int(limit))
    except Exception as _e:
        silent_exc(_e, where="observability.experience_hits:top_hits:query", level="debug")
        return []
    _scored: list[tuple[int, float, dict]] = []
    for _e in (_cands or []):
        if not isinstance(_e, dict):
            continue
        _s = score_one(_q, _e)
        if _s <= 0:
            continue
        try:
            _ts = float(_e.get("timestamp") or 0.0)
        except (TypeError, ValueError) as _exc:
            # ★不是"没有时间戳"，是"时间戳不是数"
            silent_exc(_exc, where="observability.experience_hits:top_hits:ts", level="debug")
            _ts = 0.0
        _scored.append((_s, _ts, _e))
    if not _scored:
        return []
    # 先按分数、同分按时间新→旧
    _scored.sort(key=lambda x: (-x[0], -x[1]))
    _out: list[dict[str, Any]] = []
    _seen: dict[str, int] = {}
    for _s, _ts, _e in _scored:
        if len(_out) >= max(1, int(top_k)):
            break
        _txt = _text_of(_e)
        # ★411 实测（必读）：池里 379 条有 268 条是**同一段模板 JSON 的复制品**
        #   （`{"来源": "semantic", "问题描述": "综合优化：2条样本指向同类改进点"...}`），
        #   且 `content` 字段**一条都没有**。不去重的话 top2 会是两条一模一样的文本，
        #   "命中 2 条"其实只有 1 条信息。这里按归一化文本去重并如实记 `dup` 次数。
        _key = re.sub(r"\s+", "", _txt)[:120]
        if _key in _seen:
            _seen[_key] += 1
            continue
        _seen[_key] = 1
        _out.append({
            "id": str(_e.get("id") or ""),
            "score": _s,
            "text": _txt[:SNIPPET_CHARS],
            "ts": round(_ts, 3) if _ts > 0 else None,
            "motivation": str(_e.get("motivation") or "")[:40],
            "dup": 0,
        })
    # 回填"这条文本在候选里还重复了几次"（0 = 唯一）
    for _h in _out:
        _k = re.sub(r"\s+", "", _h["text"])[:120]
        _h["dup"] = max(0, _seen.get(_k, 1) - 1)
    return _out


def pool_composition(pool: Any = None, limit: int = 0) -> dict[str, Any]:
    """经验池成分读数（供交付报告与 411 决策取证）。

    Returns:
        ``{"state", "total", "distinct_texts", "template_top", "template_ratio",
           "with_content", "summarized", "reason"}``

        ★`template_ratio` 是本批最重要的一个数：**重复文本占比**。
        它高说明"经验池有多少条"是个误导性指标 —— 条数在涨，信息量没涨。
    """
    _out: dict[str, Any] = {"state": "unavailable", "total": 0, "distinct_texts": 0,
                            "template_top": "", "template_ratio": None,
                            "with_content": 0, "summarized": 0, "reason": ""}
    try:
        _p = pool
        if _p is None:
            from nucleus.mnemosyne.experience_pool import get_experience_pool
            _p = get_experience_pool()
        _all = _p.query_experiences(limit=int(limit) if limit else 100000)
    except Exception as _e:
        silent_exc(_e, where="observability.experience_hits:pool_composition", level="debug")
        _out["reason"] = "query_failed:%s" % type(_e).__name__
        return _out
    _all = [x for x in (_all or []) if isinstance(x, dict)]
    if not _all:
        _out["state"] = "empty"
        _out["reason"] = "pool_returned_nothing"
        return _out
    from collections import Counter
    _c = Counter(re.sub(r"\s+", "", _text_of(x))[:120] for x in _all)
    _top_txt, _top_n = _c.most_common(1)[0]
    _out.update({
        "state": "ok",
        "total": len(_all),
        "distinct_texts": len(_c),
        "template_top": _top_txt[:60],
        "template_ratio": round(_top_n / len(_all), 4),
        "with_content": sum(1 for x in _all
                            if isinstance(x.get("content"), str) and x.get("content").strip()),
        "summarized": sum(1 for x in _all if x.get("is_summarized")),
    })
    return _out


def observe(question: str, top_k: int = 2, pool: Any = None,
            logger: Any = None, now: float | None = None) -> dict[str, Any]:
    """算一次命中并**打 DEBUG 日志**（观测口径的唯一出口）。

    Returns:
        ``{"enabled", "n", "hits", "candidates", "at", "log"}`` ——
        `log` 是要写进日志的一行文本（便于单测断言，不必抓 handler）。
    """
    _t = float(now if now is not None else time.time())
    if not enabled():
        return {"enabled": False, "n": 0, "hits": [], "candidates": 0,
                "at": _t, "log": ""}
    _hits = top_hits(question, top_k=top_k, pool=pool)
    _cand = 0
    try:
        _p = pool
        if _p is None:
            from nucleus.mnemosyne.experience_pool import get_experience_pool
            _p = get_experience_pool()
        _cand = len(_p.query_experiences(limit=CANDIDATE_LIMIT) or [])
    except Exception as _e:
        silent_exc(_e, where="observability.experience_hits:observe:candidates", level="debug")
    if _hits:
        _log = "[411刀4] 经验命中 %d 条（候选 %d）：%s" % (
            len(_hits), _cand,
            " | ".join("%s(score=%d)" % (h["text"][:30], h["score"]) for h in _hits))
    else:
        _log = "[411刀4] 经验命中 0 条（候选 %d）" % _cand
    _lg = logger or _module_logger
    try:
        _lg.debug(_log)
    except Exception as _e:
        silent_exc(_e, where="observability.experience_hits:observe:log", level="debug")
    return {"enabled": True, "n": len(_hits), "hits": _hits,
            "candidates": _cand, "at": _t, "log": _log}


def version() -> str:
    """契约版本。"""
    return _M411_EH_VERSION
