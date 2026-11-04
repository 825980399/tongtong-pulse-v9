# -*- coding: utf-8 -*-
"""★452刀1（P0-1a）：检索内容 dry-run 留痕 —— 只记录，不注入 prompt。

为什么要有它
------------
199/239 批的影子 v2 探针（`nucleus/shadow_v2_probe.py`）**只记检索命中的 score 分布**，
正文一个字都不留。于是"检索到的 3 条知识到底和这个问题相关不相关"无从判断 ——
下一批要决定"要不要把证据真正注入回答"，没有内容就没法评估。

本模块做什么
------------
把影子探针**已经取到**的 hits（不重复检索、不改缓存、不注入 prompt）里的
正文 / 来源 / score 落成 `data/eval/evidence_trace.jsonl`，一行一条。

三条纪律
--------
1. **纯只读**：不调用 LLM、不写 prompt、不改 retriever 缓存、不翻任何开关；
2. **绝不抛异常**：观测失败只少一行，绝不影响主推理；
3. **测试进程不写默认生产路径**（446/447/450 三次同型坑）。

命中形状（239刀2 实测）
----------------------
真实 `ExperienceRetriever.retrieve` 返回的命中可能是：
  · dict，score 在 `score` 或 `_score` 键；
  · 对象，score 在 `.score` 属性。
正文键按 `text / content / snippet / answer / body` 依次找，来源键按
`source / file / path / origin` 依次找 —— 找不到就留空串（**不臆造**）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any

from nucleus._silent_except import silent_exc

#: 默认留痕相对路径（仓根解析）
DEFAULT_TRACE_REL: str = os.path.join("data", "eval", "evidence_trace.jsonl")
_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SCORE_KEYS = ("score", "_score")
_TEXT_KEYS = ("text", "content", "snippet", "answer", "body", "example_question")
_SOURCE_KEYS = ("source", "file", "path", "origin", "doc_id")

#: 正文最长保留字数（只留判据，不把整篇文档灌进留痕）
_MAX_TEXT = 300


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception as _e:
        silent_exc(_e, where="evidence_trace::_cfg", level="debug")
        return default


def _on() -> bool:
    """开关 `ENABLE_M452_EVIDENCE_TRACE`（默认 True）。"""
    return bool(_cfg("ENABLE_M452_EVIDENCE_TRACE", True))


def _trace_path() -> str:
    p = str(_cfg("M452_EVIDENCE_TRACE_PATH", DEFAULT_TRACE_REL) or "").strip()
    if not p:
        return ""
    return p if os.path.isabs(p) else os.path.join(_ROOT, p)


def _get(hit: Any, keys: tuple, default: Any = None) -> Any:
    """dict / 对象两种形状都认，取不到返回 default。"""
    for k in keys:
        if isinstance(hit, dict):
            v = hit.get(k)
        else:
            v = getattr(hit, k, None)
        if v is not None and v != "":
            return v
    return default


def _score_of(hit: Any) -> float | None:
    v = _get(hit, _SCORE_KEYS)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def build_evidence_block(retriever: Callable[[str, int], list[Any]] | None,
                         query: str, top_k: int = 3,
                         min_score: float = 0.6) -> list[dict]:
    """★452刀1 任务书签名：对 retriever 做一次**只读**查询，返回 `[{text, source, score}]`。

    - `retriever is None` ⇒ 返回 `[]`（不猜、不造数据）；
    - `score < min_score` 的命中**过滤掉**（但 `note_hits` 会把未过滤的原因留在留痕里）；
    - 返回按 score 降序的前 `top_k` 条；
    - **绝不注入 prompt** —— 调用方拿它干什么由调用方决定，本函数只产数据。
    """
    if not _on() or retriever is None:
        return []
    try:
        hits = retriever(str(query or ""), int(top_k)) or []
    except Exception as _e:
        silent_exc(_e, where="evidence_trace::build_evidence_block retrieve", level="debug")
        return []
    out: list[dict] = []
    for h in hits:
        s = _score_of(h)
        if s is None or s < float(min_score):
            continue
        out.append({
            "text": str(_get(h, _TEXT_KEYS, "") or "")[:_MAX_TEXT],
            "source": str(_get(h, _SOURCE_KEYS, "") or "")[:120],
            "score": round(s, 4),
        })
    out.sort(key=lambda x: -x["score"])
    return out[:max(0, int(top_k))]


def note_hits(query: str, hits: list[Any], *, top_k: int = 3,
              min_score: float = 0.6) -> int:
    """★452刀1：把**已经取到**的 hits 落 `evidence_trace.jsonl`（一行一条命中）。

    为什么按命中逐行而不是按 query 一行：抽查时按 text/source/score 直接 grep / jq
    最方便，且一行一条不会出现超长数组。返回成功写入的行数。
    """
    if not _on() or not hits:
        return 0
    p = _trace_path()
    if not p:
        return 0
    # ★测试进程不许写**默认生产路径**（显式改路径的单测照常写）
    default = os.path.join(_ROOT, DEFAULT_TRACE_REL)
    if "pytest" in sys.modules and os.path.normcase(os.path.normpath(p)) == \
            os.path.normcase(os.path.normpath(default)):
        return 0
    rows: list[dict] = []
    for i, h in enumerate(list(hits)[:max(1, int(top_k))]):
        s = _score_of(h)
        rows.append({
            "ts": time.time(),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": "evidence_dry_run",
            "query": str(query or "")[:200],
            "rank": i,
            "score": round(s, 4) if s is not None else None,
            "kept": bool(s is not None and s >= float(min_score)),
            "min_score": float(min_score),
            "text": str(_get(h, _TEXT_KEYS, "") or "")[:_MAX_TEXT],
            "source": str(_get(h, _SOURCE_KEYS, "") or "")[:120],
            "note": "影子 dry-run：只记录，不注入 prompt / 不改回答",
        })
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        return len(rows)
    except Exception as _e:
        silent_exc(_e, where="evidence_trace::note_hits 写盘", level="debug")
        return 0
