# -*- coding: utf-8 -*-
"""影子 v2 只读检索探针（★199刀1）。

Lung 系统 prompt 组装区对 retriever 做一次**只读**查询（不落 prompt、不翻开关、
不改 L2 缓存——零行为变化），把检索命中的 score 分布与条数并入同一条 INFO
（含节流键，防长期日志噪音）。实弹刀（推 200）会把真实 retriever 注入
``retriever``；当前未接线时 ``retriever=None`` → 记 unavailable，零副作用。

只读约束：本模块绝不写入/翻开关/改缓存；任何异常被吞（记 silent_exc 后返回 error
片段），绝不向上抛，避免污染主链路。
"""
import time
from collections.abc import Callable
from typing import Any

from nucleus._silent_except import silent_exc

# 节流键粒度：同一进程内以该秒数为桶，方便下游日志聚合按桶去重降噪。
THROTTLE_BUCKET_SECONDS = 600

# Lung 默认不接线真实 retriever（实弹刀推 200 注入）；模块级可被覆盖。
DEFAULT_RETRIEVER: Callable[[str, int], list[Any]] | None = None


def _extract_scores(hits: list[Any]) -> list[float]:
    _scores: list[float] = []
    for _h in hits or []:
        # 形状对齐（239刀2 一验即闭）：helper 原只读 `score` 键；真实
        # ``ExperienceRetriever.retrieve`` 返回命中含 ``_score`` 键（下划线）。
        # 两键都认，兼容既有调用方契约与真实检索器两种命中形状。
        if isinstance(_h, dict):
            _s = _h.get("score")
            if not isinstance(_s, (int, float)) or isinstance(_s, bool):
                _s = _h.get("_score")
        else:
            _s = getattr(_h, "score", None)
        if isinstance(_s, (int, float)) and not isinstance(_s, bool):
            _scores.append(float(_s))
    return _scores


def _distribution(hits: list[Any]) -> dict:
    _scores = _extract_scores(hits)
    _n = len(_scores)
    if _n == 0:
        return {"count": 0, "min": None, "max": None, "mean": None}
    return {
        "count": _n,
        "min": round(min(_scores), 4),
        "max": round(max(_scores), 4),
        "mean": round(sum(_scores) / _n, 4),
    }


def throttle_key() -> str:
    """当前节流桶键（tk=<bucket>），供 INFO 行内嵌入与下游去重。"""
    return "tk=%d" % int(time.time() // THROTTLE_BUCKET_SECONDS)


def build_shadow_v2_fragment(retriever: Callable[[str, int], list[Any]] | None,
                             query_hint: str,
                             top_k: int = 3) -> str:
    """对 retriever 做一次只读查询，返回待并入影子 INFO 的片段字符串。

    - ``retriever`` 为 None 或异常 → 返回 unavailable/error 片段（仍并入同一条
      INFO，便于验收与长期可观测），不抛异常。
    - 仅读取，绝不写入/翻开关/改缓存。
    """
    _tk = throttle_key()
    if retriever is None:
        return ("[v2只读检索] retriever=unavailable 条数=0 "
                "score分布=无 节流键=%s" % _tk)
    try:
        _hits = retriever(query_hint, top_k) or []
        _d = _distribution(_hits)
        return ("[v2只读检索] retriever=available 条数=%d "
                "score分布=min%s/max%s/mean%s 节流键=%s"
                % (_d["count"], _d["min"], _d["max"], _d["mean"], _tk))
    except Exception as _e:
        silent_exc(_e, "nucleus/shadow_v2_probe.py:build_shadow_v2_fragment",
                   level="debug")
        return ("[v2只读检索] retriever=error 条数=0 "
                "score分布=无 节流键=%s" % _tk)
