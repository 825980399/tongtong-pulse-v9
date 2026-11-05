# -*- coding: utf-8 -*-
"""★453R刀6：证据源准入分类（从**现有字段**推导，不臆造 ``source`` 字段）。

为什么需要推导
--------------
任务书刀6 写「只收 ``source in [qa, human_seed, kal]``、排除
``semantic_summary / self_inspection / delegated``」。实测经验池
``data/experience/experience_pool.json`` **384 条里没有一条带 ``source`` 字段**
（字段全集见 453R 交付报告 §0）。烛微《454批前置风险深挖》§P1-3 的
`semantic_summary / self_inspection` 也是**按 reward 与正文内容推断**的类别，
§570 才建议「未来单独打标」。

所以本模块做两件事：

1. 把「类别」变成**可计算、可审计**的函数 `classify_source(hit)`，
   只用盘面真有的字段（`is_template / template_key / motivation / summary /
   raw_summary / result_reward`）；
2. 把「准入」做成**只加不删的标记**（`admitted`），dry-run 留痕保留全量命中
   供抽查，真正的过滤发生在注入层 —— 既不丢观测，也不打红 453 的正文提取单测。

`human_seed` / `kal` 目前**池子里一条都没有**（未来由 454 的人工种子 / KAL
节点接入时才会出现），如实计入统计，不编造。
"""
from __future__ import annotations

import json
from typing import Any

from nucleus._silent_except import silent_exc

#: 任务书刀6 的准入白名单
ADMITTED_SOURCES: tuple[str, ...] = ("qa", "human_seed", "kal")

#: 明确排除的类别（用于留痕 reason，不是第二套开关）
EXCLUDED_SOURCES: tuple[str, ...] = ("template", "semantic_summary",
                                      "self_inspection", "delegated", "event_distill")

_SEMANTIC_MARKS = ('"来源": "semantic"', "来源: semantic", "semantic_summary",
                   "综合优化", "样本证据尚分散")
_DELEGATED_MARKS = ("delegated", "meta_not_search", "委托给", "delegated_to_lung")
_SELF_QUESTION_MARKS = ("曈曈", "你自己", "认知状态", "专注度", "能量、情绪",
                        "运行状态", "自我审计", "自问自答")
_SELF_MARKS = ("self_inspection", "复盘确认", "情绪体验", "自检", "全域自学习")
_QA_MARKS = ("用户访客提问", "回答方法", "回答用户关于")


def _blob(hit: Any) -> str:
    """把一条命中的所有可判据字段拼成小写检索串（dict / 对象两种形状都认）。"""
    if not isinstance(hit, dict):
        return str(hit or "")
    parts = []
    for k in ("source", "source_class", "origin", "motivation", "summary",
              "raw_summary", "raw_motivation", "compressed_summary", "template_key",
              "text", "inject_text"):
        v = hit.get(k)
        if v:
            parts.append(str(v))
    return " ".join(parts)


def _is_template(hit: Any) -> bool:
    if not isinstance(hit, dict):
        return False
    return bool(hit.get("is_template")) or bool(hit.get("template_key"))


def classify_source(hit: Any) -> str:
    """把一条命中归到 8 类之一；**只依据盘面真有的字段**。

    返回：``qa / human_seed / kal / semantic_summary / self_inspection /
    delegated / event_distill / template / other``
    """
    if not isinstance(hit, dict):
        return "other"
    # 显式类别优先（454 若接入人工种子/KAL，会带 source / source_class）
    # ★454刀7：任务书要求 KAL 卡片「保留 node_id 作为 source」，于是 source
    #   会变成 "node:xxx" 而不是类别名。source_class 是明确的类别标记，
    #   必须优先读它；否则 KAL 20 条会被误判成 other 全部挡在准入之外。
    explicit = str(hit.get("source_class") or hit.get("source") or "").strip().lower()
    if explicit:
        return explicit if explicit in (ADMITTED_SOURCES + EXCLUDED_SOURCES + ("other",)) \
            else "other"
    if _is_template(hit):
        return "template"
    b = _blob(hit)
    low = b.lower()
    if any(m.lower() in low for m in _SEMANTIC_MARKS):
        return "semantic_summary"
    if any(m.lower() in low for m in _DELEGATED_MARKS):
        return "delegated"
    if any(m in b for m in _QA_MARKS):
        # 「回答用户关于曈曈自身状态」是自检问答，不是可外借的通用问答经验
        if any(m in b for m in _SELF_QUESTION_MARKS):
            return "self_inspection"
        return "qa"
    if any(m in b for m in _SELF_MARKS):
        return "self_inspection"
    return "event_distill" if ("动机循环" in b or "心跳" in b) else "other"


def source_gate_enabled() -> bool:
    """准入闸开关（默认 True）；关掉只用于 A/B 对照，不改变 dry-run 留痕。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M453R_SOURCE_GATE", True))
    except Exception as _e:
        silent_exc(_e, where="evidence_source::source_gate_enabled", level="debug")
        return True


def source_admitted(hit: Any, *, enabled: bool | None = None) -> bool:
    """该命中是否允许进入注入候选（dry-run 仍会记录它，只是标 False）。"""
    if _is_template(hit):
        return False
    cls = classify_source(hit)
    if cls not in ADMITTED_SOURCES:
        return False
    if enabled is False:
        return True
    return cls in ADMITTED_SOURCES


def describe(items: list[Any] | None = None, *, path: str | None = None) -> dict:
    """统计一批命中的类别分布 / 准入条数（供交付报告与留痕）。

    ``path`` 给定时直接读经验池 JSON（离线统计用），否则用传入 items。
    """
    rows = list(items or [])
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            ex = d.get("experiences") if isinstance(d, dict) else d
            if isinstance(ex, dict):
                ex = list(ex.values())
            rows = list(ex or [])
        except Exception as _e:
            silent_exc(_e, where="evidence_source::describe 读池", level="debug")
    dist: dict[str, int] = {}
    admitted = 0
    for h in rows:
        cls = classify_source(h)
        dist[cls] = dist.get(cls, 0) + 1
        if source_admitted(h):
            admitted += 1
    return {"total": len(rows), "distribution": dict(sorted(dist.items())),
            "admitted": admitted,
            "admitted_sources": list(ADMITTED_SOURCES)}
