# -*- coding: utf-8 -*-
"""★454刀6/7：人工种子 + KAL 证据的**唯一检索入口**。

为什么必须有这一层
------------------
烛微《455批前置风险深挖》§C3 指出：任务书只要求把种子落到
``data/eval/*.json``，但 ``ExperienceRetriever.candidates()`` 只读经验池，
**全仓没有任何代码引用这两个文件**。种子落盘 ≠ 可检索 —— 验收若用
「数文件条数」就会得到一个假通过的「候选从 2 涨到 50」。

本模块把种子转成与经验池**同形状**的候选（``source / text / inject_text /
timestamp / result_reward / quality_weight / source_class``），由
``PulseLung`` 在注入入口合并进命中列表，与检索器命中一起走**同一条**
``build_injection`` 准入 / 净化 / 排序链路。

★不改 ``ExperienceRetriever.candidates()``：它返回 list 且有 6 条既有单测
  断言 ``== []``（421刀3 教训），改它必炸。本层是「只加不改」的旁路口。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from difflib import SequenceMatcher
from typing import Any

from nucleus._silent_except import silent_exc

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_FILES = (
    os.path.join("data", "eval", "human_seed_evidence.json"),
    os.path.join("data", "eval", "kal_seed_evidence.json"),
)
_CACHE: dict[str, Any] = {"mtime": {}, "rows": []}


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception as _e:
        silent_exc(_e, where="seed_evidence::_cfg", level="debug")
        return default


def enabled() -> bool:
    return bool(_cfg("ENABLE_M454_SEED_EVIDENCE", True))


def _path(rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(_ROOT, rel)


def _norm(row: dict, fallback_source: str) -> dict:
    """把一条种子卡片转成经验池同形状候选（text 全文 / inject_text 结论优先）。"""
    src = str(row.get("source") or fallback_source)
    method = str(row.get("method") or "").strip()
    outcome = str(row.get("outcome") or "").strip()
    caveat = str(row.get("caveat") or "").strip()
    question = str(row.get("question_pattern") or "").strip()
    conf = row.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        conf = 0.8
    reward = row.get("reward")
    if isinstance(reward, bool) or not isinstance(reward, (int, float)):
        reward = conf
    full = "。".join(p for p in (method, outcome, "注意：" + caveat if caveat else "") if p)
    inject = "。".join(p for p in (method, outcome) if p)
    return {
        "id": str(row.get("id") or row.get("node_id") or
                  hashlib.sha1(full.encode("utf-8", "ignore")).hexdigest()[:12]),
        "source": str(row.get("node_id") or src),   # KAL 保留 node_id 作 source
        "source_class": src,
        "question_pattern": question,
        "motivation": question,
        "summary": outcome,
        "text": full,
        "inject_text": inject,
        "timestamp": row.get("timestamp") or time.time(),
        "result_reward": {"intensity": float(reward)},
        "quality_weight": round(max(0.0, min(1.0, float(conf))), 4),
        "is_template": False,
        "pollution_risk": "low",
        "dim": row.get("dim"),
        "domain": row.get("domain"),
        "node_id": row.get("node_id"),
        # ★455 刀1：种子是人工精选，注入层给它单独的准入下限（0.55，见
        #   evidence_injection.score_floor）。标在**归一化**这一处，
        #   保证 candidates() / candidate_stats() / PulseLung 三条路都带得上。
        "seed_override": True,
    }


def load_rows(*, refresh: bool = False) -> list[dict]:
    """读两个种子文件（按 mtime 缓存）；读不到 / 坏行都跳过并留痕，绝不抛。"""
    if not enabled():
        return []
    rows: list[dict] = []
    changed = False
    for rel in SEED_FILES:
        p = _path(rel)
        if not os.path.exists(p):
            continue
        try:
            mt = os.path.getmtime(p)
            if _CACHE["mtime"].get(p) != mt:
                changed = True
                _CACHE["mtime"][p] = mt
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            items = d.get("items") if isinstance(d, dict) else d
            for r in list(items or []):
                if isinstance(r, dict):
                    rows.append(_norm(r, "human_seed" if "human_seed" in rel else "kal"))
        except Exception as _e:
            silent_exc(_e, where="seed_evidence::load_rows " + os.path.basename(rel),
                       level="debug")
    if refresh or changed or not _CACHE["rows"]:
        _CACHE["rows"] = rows
    return list(_CACHE["rows"])


def _match_score(query: str, pattern: str) -> float:
    """question_pattern 与 query 的相似度（关键词交集 + 序列相似度）。"""
    q, p = str(query or "").strip(), str(pattern or "").strip()
    if not q or not p:
        return 0.65
    ratio = SequenceMatcher(None, q, p).ratio()
    qset = {c for c in q if "\u4e00" <= c <= "\u9fff"}
    pset = {c for c in p if "\u4e00" <= c <= "\u9fff"}
    overlap = len(qset & pset) / max(1, len(qset | pset))
    return round(min(0.95, 0.50 + 0.30 * ratio + 0.20 * overlap), 4)


def candidates(query: str = "", *, limit: int = 8) -> list[dict]:
    """按 query 给种子候选打分排序（与经验池命中同形状，可直接进 build_injection）。"""
    if not enabled():
        return []
    out = []
    for row in load_rows():
        r = dict(row)
        r["score"] = _match_score(query, r.get("question_pattern"))
        out.append(r)
    out.sort(key=lambda x: (-x["score"], -float(x.get("quality_weight") or 0)))
    return out[:max(0, int(limit))]


def candidate_stats(query: str = "", *, min_score: float = 0.65,
                    score_mode: str = "pool") -> dict:
    """★验收用「同一入口」统计，不数文件条数（烛微 §C3）。

    ``score_mode="pool"``（默认）：统计**池子里有多少条符合准入**，
      与具体 query 无关，给每条一个代表分（0.70）—— 这是「候选 2→N」的口径。
    ``score_mode="query"``：按 query 相似度打分，是线上单次请求的实际口径。
    ``raw_admitted`` = 走 ``evidence_source.source_admitted`` + score 闸后的候选数；
    ``injectable``   = 把这批候选喂给 ``build_injection`` 后真正能进注入段的条数
                       （会额外过去重 / 过期 / 净化 / top_k）。
    """
    try:
        from nucleus.evidence_injection import build_injection, score_floor, seed_min_score
        from nucleus.evidence_source import source_admitted
    except Exception as _e:
        silent_exc(_e, where="seed_evidence::candidate_stats import", level="debug")
        return {"available": False}
    if score_mode == "query":
        all_rows = [dict(r, score=_match_score(query, r.get("question_pattern")))
                   for r in load_rows()]
    else:
        all_rows = [dict(r, score=max(0.70, float(min_score)))
                   for r in load_rows()]
    # ★455 刀1：与线上**同一条判据**（score_floor 认 seed_override）——
    #   否则验收用 0.65 卡、线上用 0.55 放，两边永远对不上。
    _seed_min = seed_min_score()
    admitted = [r for r in all_rows
                if float(r["score"]) >= score_floor(r, float(min_score), _seed_min)
                and source_admitted(r)]
    inj = build_injection(admitted, now=time.time(), min_score=float(min_score),
                          top_k=max(1, len(admitted)))
    by_source: dict[str, int] = {}
    for r in admitted:
        k = str(r.get("source_class"))
        by_source[k] = by_source.get(k, 0) + 1
    return {"available": True, "score_mode": score_mode,
            "files": [os.path.basename(p) for p in SEED_FILES],
            "total_rows": len(all_rows), "raw_admitted": len(admitted),
            "injectable": len(inj["items"]), "by_source": by_source,
            "dropped": inj["dropped"], "min_score": min_score,
            "seed_min_score": _seed_min,
            "query": str(query or "")[:80]}


def stats() -> dict:
    rows = load_rows(refresh=True)
    by_src: dict[str, int] = {}
    by_dim: dict[str, int] = {}
    for r in rows:
        by_src[str(r.get("source_class"))] = by_src.get(str(r.get("source_class")), 0) + 1
        by_dim[str(r.get("dim"))] = by_dim.get(str(r.get("dim")), 0) + 1
    return {"total": len(rows), "by_source": by_src, "by_dim": by_dim,
            "enabled": enabled()}
