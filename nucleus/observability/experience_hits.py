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

★412刀2 追加：命中排序对**模板类经验降权**（`experience_governance.is_template`）。
降权不是删除 —— 模板条目仍在池里、仍可召回，只是不再靠复制得多霸占 top2。

★423刀1（候选窗口放开）：原 `CANDIDATE_LIMIT=200` 只看「最近 200 条」，
  而池里 418 条只有 133 种文本（模板复制品最多一份 72 条）⇒ 近期窗口被复制品占满。
  T0 实测（15 个真实用户问题当 query，ground truth = 池内含该问题的条目 id）：
      窗口 200 条   recall@2 = 20.0%   单次 2.7ms
      全量  418 条  recall@2 = 66.7%   单次 4.5ms
  ⇒ 放开窗口的收益是 +46.7pp，代价约 +1.8ms（每轮对话最多 2 次调用）。
  实现用**分层召回**而不是简单放大常量：首层 500 条（覆盖当前全池），
  命中不足 `top_k` 且池还没到底才按 4 倍往外扩，硬上限 20000。
  这样「现在快、将来池长大也不退化」，且最坏情况仍能扫到全池。
  ★`top_hits()` 的**签名与返回形态一字未改**（仍返回 list、不命中返回 []），
    新增的读数走 `top_hits_ex()`，避免炸既有 `len(hits)==0` 判据。
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

#: 首层候选窗口（★423 刀1：200 → 500）
#: ★为什么不是 200：T0 实测（池 418 条 / 15 个真实用户问题）200 条窗口
#:   recall@2 只有 20.0%，全量 66.7%。而 `query_experiences(limit=N)` 的成本
#:   主要来自**遍历全池 + 排序**（limit=200 与全量实测 0.49ms vs 0.52ms，
#:   几乎相同），真正随候选数增长的是**打分**（每条一次正则）。
#:   500 条 ≈ 4.6ms/次，每轮对话最多 2 次 ⇒ +9ms，相对 LLM 推理可忽略。
CANDIDATE_LIMIT = 500

#: 分层扩窗倍数：首层命中不足 `top_k` 且池还没到底时，按此倍数往外扩
CANDIDATE_GROWTH = 4

#: 硬上限（防病态池）。实测：候选 20000 条时单次 top_hits ≈ 260ms，
#: 到那个量级就该上索引了，不是继续放大窗口。
CANDIDATE_MAX = 20000

#: 中文词片（2~4 字）——与仓内既有 `_make_experience_key` 同口径
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,4}")


def enabled() -> bool:
    """灰度开关 `ENABLE_M411_EXPERIENCE_HITS`；缺省 True（本刀只读+日志，无副作用）。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M411_EXPERIENCE_HITS", True))
    except Exception as _e:
        silent_exc(_e, where="observability.experience_hits:enabled", level="debug")
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


def _m423_rank(question_tokens: set[str], cands: list[dict],
               top_k: int) -> list[dict[str, Any]]:
    """对一批候选打分→降权→排序→去重，取 top_k（★423 刀1 抽出的纯函数）。

    分层召回会对不同窗口反复调用它，所以不能再把逻辑埋在 `top_hits` 里。
    """
    #: (原始相关分, 时间戳, 条目, 权重) —— ★存**原始分**，不存加权分
    _scored: list[tuple[float, float, dict, float]] = []
    for _e in (cands or []):
        if not isinstance(_e, dict):
            continue
        _s = score_one(question_tokens, _e)
        if _s <= 0:
            continue
        # ★412刀2：模板类经验**降权不删除**（权重 1.0 = 不降权）
        try:
            from nucleus.observability import experience_governance as _eg412
            _w = _eg412.retrieval_weight(_e) if _eg412.enabled() else 1.0
        except Exception as _e_w:
            silent_exc(_e_w, where="observability.experience_hits:_m423_rank:weight",
                       level="debug")
            _w = 1.0
        try:
            _ts = float(_e.get("timestamp") or 0.0)
        except (TypeError, ValueError) as _exc:
            # ★不是"没有时间戳"，是"时间戳不是数"
            silent_exc(_exc, where="observability.experience_hits:_m423_rank:ts", level="debug")
            _ts = 0.0
        # ★权重放在元组里带回，**不往经验条目上写字段** ——
        #   池里的 dict 会被落盘，写进去就是把观测口径污染进产物。
        # ★414刀4 修：这里只存**原始分**。原实现存 `_s * _w`，而输出时
        #   `score_weighted` 又乘一次 `_w` ⇒ 权重被平方（0.3 变成 0.09）。
        #   412/413 没暴露是因为生产池 `is_template` 全缺、`_w` 恒 1.0。
        _scored.append((float(_s), _ts, _e, float(_w)))
    if not _scored:
        return []
    # 先按**加权分**、同分按时间新→旧（加权分在这里现算，不再依赖元组里的值）
    _scored.sort(key=lambda x: (-(x[0] * x[3]), -x[1]))
    _out: list[dict[str, Any]] = []
    _seen: dict[str, int] = {}
    for _s, _ts, _e, _w in _scored:
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
            # ★414刀4：`score` = **原始相关分**（只反映文本匹配，不受权重影响），
            #   `score_weighted` = 原始分 × 权重（只乘一次）。
            #   这样"模板被降权了多少"可以直接从两个数看出来，不会被平方混淆。
            "score": round(_s, 4),
            "score_weighted": round(_s * _w, 2),
            "weight": _w,
            "is_template": bool(_e.get("is_template")),
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


def top_hits_ex(question: str, top_k: int = 2, pool: Any = None,
                limit: int | None = None) -> dict[str, Any]:
    """分层召回：命中结果 + 扫描过程读数（★423 刀1 新增口）。

    Args:
        question: 用户问题原文。
        top_k: 返回条数。
        pool: 经验池实例；None 时取 `get_experience_pool()` 单例。
        limit: 显式给定 ⇒ **只扫这一层**（严格单窗口，保持 423 之前语义，
               既有调用方传了 limit 就还会得到"只扫这么多"的行为）；
               None ⇒ 从 `CANDIDATE_LIMIT` 起分层，命中不足 `top_k` 且池还没到底
               就按 `CANDIDATE_GROWTH` 往外扩，直到够数 / 覆盖全池 / 到 `CANDIDATE_MAX`。

    Returns:
        ``{"hits", "layers", "candidates", "window", "exhausted", "elapsed_ms",
        "reason"}``

        - ``layers``：每层 ``{"window", "returned", "hits"}``，用于证明"扩了几层"
        - ``candidates``：**最后一层**真实打分的候选条数
        - ``exhausted``：True = 池已到底（再扩也没有更多条目）
        - ``reason``：只在提前返回时出现（disabled / no_question_tokens /
          pool_unavailable / query_failed）
    """
    _empty = {"hits": [], "layers": [], "candidates": 0, "window": 0,
              "exhausted": False, "elapsed_ms": 0.0}
    if not enabled():
        return dict(_empty, reason="disabled")
    _q = tokens_of(question)
    if not _q:
        return dict(_empty, reason="no_question_tokens")
    if pool is None:
        try:
            from nucleus.mnemosyne.experience_pool import get_experience_pool
            pool = get_experience_pool()
        except Exception as _e:
            silent_exc(_e, where="observability.experience_hits:top_hits_ex:pool",
                       level="debug")
            return dict(_empty, reason="pool_unavailable")
    _want = max(1, int(top_k))
    _strict = bool(limit) and int(limit) > 0
    _max = max(1, int(CANDIDATE_MAX))
    _win = max(1, min(int(limit) if _strict else int(CANDIDATE_LIMIT), _max))
    _t0 = time.perf_counter()
    _layers: list[dict[str, Any]] = []
    _hits: list[dict[str, Any]] = []
    _cands_n = 0
    _exhausted = False
    for _ in range(8):          # 兜底上限：GROWTH>=2 时 8 层足够扩到 CANDIDATE_MAX
        try:
            _raw = pool.query_experiences(limit=_win)
        except Exception as _e:
            silent_exc(_e, where="observability.experience_hits:top_hits_ex:query",
                       level="debug")
            return dict(_empty, layers=_layers, reason="query_failed")
        _cands = [x for x in (_raw or []) if isinstance(x, dict)]
        _cands_n = len(_cands)
        _hits = _m423_rank(_q, _cands, _want)
        _layers.append({"window": _win, "returned": _cands_n, "hits": len(_hits)})
        # 「到底」判据：池返回条数 < 请求窗口 ⇒ 池里就这么多，再扩也是同一批
        if _cands_n < _win:
            _exhausted = True
        if len(_hits) >= _want or _strict or _exhausted or _win >= _max:
            break
        _win = min(_win * max(2, int(CANDIDATE_GROWTH)), _max)
    return {
        "hits": _hits,
        "layers": _layers,
        "candidates": _cands_n,
        "window": _win,
        "exhausted": _exhausted,
        "elapsed_ms": round((time.perf_counter() - _t0) * 1000.0, 3),
    }


def top_hits(question: str, top_k: int = 2,
             pool: Any = None, limit: int | None = None) -> list[dict[str, Any]]:
    """取与问题最相关的 top_k 条经验（**返回形态与 423 之前逐字一致**）。

    ★423 刀1：内部改走分层召回 `top_hits_ex()`，本函数只取 `hits`。
      保留「返回 list、不命中返回 []」的契约 —— 调用方（`PulseLung` /
      `observe`）与既有单测都按长度判"这次没命中"，改形态会当场炸。

    Returns:
        列表，每条 ``{"id", "score", "score_weighted", "weight", "is_template",
        "text", "ts", "motivation", "dup"}``；不命中返回 ``[]``。
    """
    return top_hits_ex(question, top_k=top_k, pool=pool, limit=limit)["hits"]


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
        _out["reason"] = f"query_failed:{type(_e).__name__}"
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
    # ★423 刀1 保留原结构：这里**必须**继续调 `top_hits()` 而不是 `top_hits_ex()`。
    #   原因（实测踩到）：`test_m411_k4::test_51` 用 monkeypatch 替换 `eh.top_hits`
    #   注入假池 —— 改成直调 `top_hits_ex` 会绕过桩、当场去查生产池，
    #   该用例从「命中 1 条」变成「命中 0 条」而红。
    #   ⇒ `observe` 的职责是"算命中 + 记一行"，分层读数不进它的日志；
    #     要看扩了几层请直接调 `top_hits_ex()`。
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
        _log = (f"[411刀4] 经验命中 {len(_hits)} 条"
                f"（候选 {_cand}，窗口 {CANDIDATE_LIMIT}）："
                + " | ".join(f"{h['text'][:30]}(score={h['score']})"
                             for h in _hits))
    else:
        _log = (f"[411刀4] 经验命中 0 条（候选 {_cand}，"
                f"窗口 {CANDIDATE_LIMIT}）")
    _lg = logger or _module_logger
    try:
        _lg.debug(_log)
    except Exception as _e:
        silent_exc(_e, where="observability.experience_hits:observe:log", level="debug")
    # ★431刀3：命中明细落盘（旁路）。`_log` 只有一行文本、score 埋在括号里，
    #   事后无法统计"哪几条最常被复用"；这里把每条的 id/score/is_template 存下来。
    try:
        from nucleus.observability import observations as _m431_obs
        _m431_obs.record_experience_hits(question, _hits, _cand)
    except Exception as _m431_e:
        silent_exc(_m431_e, where="observability.experience_hits:observe:observations",
                   level="debug")
    return {"enabled": True, "n": len(_hits), "hits": _hits,
            "candidates": _cand, "at": _t, "log": _log}


def version() -> str:
    """契约版本。"""
    return _M411_EH_VERSION
