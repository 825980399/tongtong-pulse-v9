# -*- coding: utf-8 -*-
"""★454刀2/3/4：本地推理算子（compare / exclude / quantify）。

三条纪律
--------
1. **纯本地**：0 次 LLM 调用、0 次网络、0 次写盘；输入决定输出，便于离线单测。
2. **不编造**：证据不足 / 缺数值 ⇒ 返回显式 ``status``，绝不拿形容词或 0 冒充结论。
3. **不静默清空**：``exclude`` 全被排除时返回**原集合** + 告警，而不是空列表。

本批（454a/454b）只实现算子函数，**计划打标不执行算子**（刀1 的 ``op!=retrieve``
先只记 trace）；真正的算子调度由 455 接。故本模块不 import 框架器官、不翻开关。
"""
from __future__ import annotations

import re
from typing import Any

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DIM_SPLIT_RE = re.compile(r"[，,；;。\n]+")


def _as_text(x: Any) -> str:
    return "" if x is None else str(x)


def _evidence_rows(evidence: Any) -> list[dict]:
    out = []
    for i, e in enumerate(list(evidence or [])):
        if isinstance(e, dict):
            out.append({"id": str(e.get("id") or e.get("evidence_id") or f"E{i + 1}"),
                        "text": _as_text(e.get("text") or e.get("inject_text") or e.get("content")),
                        "score": e.get("score")})
        elif e is not None:
            out.append({"id": f"E{i + 1}", "text": _as_text(e), "score": None})
    return [r for r in out if r["text"].strip()]


def _numbers(text: str) -> list[float]:
    vals = []
    for m in _NUM_RE.finditer(text):
        try:
            vals.append(float(m.group(0)))
        except ValueError as _e:      # 正则已保证是数值，留痕仅为合规
            from nucleus._silent_except import silent_exc
            silent_exc(_e, where="local_ops::_numbers", level="debug")
    return vals


def _dim_snippets(text: str, dimension: str) -> list[str]:
    """把正文按句切开，取含该维度词的片段（供逐维对照）。"""
    parts = [p.strip() for p in _DIM_SPLIT_RE.split(text) if p.strip()]
    return [p for p in parts if dimension and dimension in p]


# ---------------------------------------------------------------- compare
def compare(operands: list[Any] | None, dimensions: list[str] | None,
            evidence: list[Any] | None = None) -> dict:
    """逐维对照两个（或多个）对象，返回相同点 / 不同点与证据 id。

    ``operands``   2 个及以上（str 或可字符串化对象）
    ``dimensions`` 对照维度，如 ``["性能", "复杂度", "运维"]``
    ``evidence``   ``[{"id","text"}]`` 或纯文本列表

    ★证据不足（对象数 <2 / 维度为空 / 命中证据 <2 条 / 某维度两边都无素材）
      ⇒ ``status="insufficient_evidence"``，不编造相似或差异。
    """
    ops = [_as_text(o).strip() for o in list(operands or []) if _as_text(o).strip()]
    dims = [_as_text(d).strip() for d in list(dimensions or []) if _as_text(d).strip()]
    rows = _evidence_rows(evidence)
    base = {"status": "insufficient_evidence", "similarities": [],
            "differences": [], "evidence_ids": [], "confidence": 0.0,
            "dimensions": dims, "operands": ops,
            "reason": "evidence_insufficient"}
    if len(ops) < 2 or not dims:
        base["reason"] = "bad_input"
        return base
    if len(rows) < 2:
        return base

    # 每个对象命中哪些证据（证据正文里出现对象名）
    per_op: list[list[dict]] = [
        [r for r in rows if o in r["text"]] for o in ops
    ]
    if any(not hit for hit in per_op):
        base["reason"] = "operand_without_evidence"
        return base

    used = {r["id"] for hit in per_op for r in hit}
    similarities: list[dict] = []
    differences: list[dict] = []
    unknown: list[str] = []
    for dim in dims:
        snips = [
            [s for r in hit for s in _dim_snippets(r["text"], dim)]
            for hit in per_op
        ]
        if any(not s for s in snips):
            unknown.append(dim)
            continue
        # 逐维比较：数值优先，其次文本是否一致
        vals = [_numbers(" ".join(s)) for s in snips]
        if vals[0] and all(v and len(v) == len(vals[0]) for v in vals[1:]):
            if all(v == vals[0] for v in vals[1:]):
                similarities.append({"dimension": dim, "value": vals[0],
                                     "evidence_ids": sorted(used)})
            else:
                differences.append({"dimension": dim, "values": vals,
                                    "evidence_ids": sorted(used)})
        elif len({tuple(s) for s in snips}) == 1:
            similarities.append({"dimension": dim, "text": snips[0][0],
                                 "evidence_ids": sorted(used)})
        else:
            differences.append({"dimension": dim, "texts": [s[0] for s in snips],
                                "evidence_ids": sorted(used)})

    judged = len(similarities) + len(differences)
    if judged == 0:
        base["reason"] = "no_dimension_evidence"
        return base
    conf = round(judged / max(1, len(dims)) * min(1.0, len(rows) / 4.0), 3)
    return {"status": "ok", "similarities": similarities,
            "differences": differences, "unknown_dimensions": unknown,
            "evidence_ids": sorted(used), "confidence": conf,
            "dimensions": dims, "operands": ops, "reason": "compared"}


# ---------------------------------------------------------------- exclude
def exclude(candidates: list[Any] | None, criteria: dict | list | None,
            evidence: list[Any] | None = None) -> dict:
    """按标准筛掉不符合的候选；全被排除时返回**原集合** + 告警，不静默清空。

    ``criteria`` 支持：
      * ``{"must": [...], "negations": [...], "min_score": 0.5}``
      * 或直接给关键词列表（等价于 must）
    """
    items = list(candidates or [])
    rows = _evidence_rows(evidence)
    if isinstance(criteria, dict):
        must = [_as_text(k) for k in list(criteria.get("must") or []) if _as_text(k)]
        negs = [_as_text(k) for k in list(criteria.get("negations") or []) if _as_text(k)]
        min_score = criteria.get("min_score")
    else:
        must = [_as_text(k) for k in list(criteria or []) if _as_text(k)]
        negs = []
        min_score = None

    dropped: list[dict] = []
    kept: list[Any] = []
    for i, c in enumerate(items):
        txt = _as_text(c.get("text") if isinstance(c, dict) else c)
        score = c.get("score") if isinstance(c, dict) else None
        reasons = []
        if negs and any(n in txt for n in negs):
            reasons.append("negation_hit:" + next(n for n in negs if n in txt))
        if must and not any(k in txt for k in must):
            reasons.append("must_keywords_missing")
        if isinstance(min_score, (int, float)) and not isinstance(min_score, bool):
            if isinstance(score, (int, float)) and score < float(min_score):
                reasons.append("score_below_threshold")
        if reasons:
            dropped.append({"item": c, "reason": ";".join(reasons), "index": i})
        else:
            kept.append(c)

    all_excluded = bool(items) and not kept
    return {
        "status": "all_excluded" if all_excluded else "ok",
        # ★不静默清空：全排除时原样退回，让上层决定怎么办
        "kept": list(items) if all_excluded else kept,
        "dropped": dropped,
        "evidence_ids": [r["id"] for r in rows],
        "warning": ("全部候选均被排除，已按『不静默清空』返回原集合，"
                    "请人工复核 criteria") if all_excluded else None,
        "counts": {"total": len(items), "kept": len(kept), "dropped": len(dropped)},
    }


# ---------------------------------------------------------------- quantify
_METRICS = {
    "sum": "sum", "总和": "sum", "合计": "sum", "total": "sum",
    "mean": "mean", "均值": "mean", "平均": "mean", "avg": "mean",
    "ratio": "ratio", "比例": "ratio", "占比": "ratio",
    "count": "count", "计数": "count", "个数": "count",
}


def quantify(metric: str, values: list[Any] | None, unit: str = "",
             evidence: list[Any] | None = None) -> dict:
    """从证据/数值里抽数字，算总和 / 均值 / 比例 / 计数。

    缺数值 ⇒ ``status="insufficient_numbers"``，绝不返回 0 冒充结果。
    """
    op = _METRICS.get(str(metric or "").strip().lower(), "sum")
    rows = _evidence_rows(evidence)
    nums: list[float] = []
    for v in list(values or []):
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            nums.append(float(v))
        else:
            nums.extend(_numbers(_as_text(v)))
    if not nums and rows:
        for r in rows:
            nums.extend(_numbers(r["text"]))

    base = {"status": "insufficient_numbers", "value": None, "unit": unit,
            "formula": None, "evidence_ids": [r["id"] for r in rows],
            "numbers": [], "reason": "no_numbers"}
    if not nums:
        return base
    if op == "sum":
        value, formula = sum(nums), "sum(" + ",".join(str(n) for n in nums) + ")"
    elif op == "mean":
        value = round(sum(nums) / len(nums), 4)
        formula = "mean(" + ",".join(str(n) for n in nums) + ")"
    elif op == "count":
        value, formula = float(len(nums)), "count(numbers)"
    else:  # ratio：第一个数 / 第二个数（缺第二个 ⇒ 用总和作分母）
        denom = nums[1] if len(nums) > 1 else sum(nums)
        if denom == 0:
            base["reason"] = "zero_denominator"
            return base
        value = round(nums[0] / denom, 4)
        formula = f"ratio({nums[0]}/{denom})"
    return {"status": "ok", "value": value, "unit": unit, "formula": formula,
            "evidence_ids": [r["id"] for r in rows], "numbers": nums,
            "metric": op, "reason": "computed"}
