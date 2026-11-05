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

import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable
from typing import Any

from nucleus._silent_except import silent_exc

#: 默认留痕相对路径（仓根解析）
DEFAULT_TRACE_REL: str = os.path.join("data", "eval", "evidence_trace.jsonl")
_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SCORE_KEYS = ("score", "_score")
_TEXT_KEYS = ("text", "content", "snippet", "answer", "body", "example_question",
              # ★453刀1：经验池的真实正文字段（烛微 §1.1 实测）。
              #   452 时只认前 6 个 ⇒ 对真实经验命中 text 恒为空串。
              "motivation", "summary", "explanation", "hypothesis")
# 经验池一条记录是「motivation + summary」两字段共同构成正文，
# 只取 `_get` 的第一个命中键会把「动机循环内部评估」这半句丢掉。
# 口径与 nucleus.mnemosyne.experience_retriever.experience_text 逐字一致。
_PRIMARY_TEXT_KEYS = ("text", "content", "snippet", "answer", "body",
                      "example_question")
_MULTI_TEXT_KEYS = ("motivation", "summary", "explanation", "hypothesis")
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


def _text_of(hit: Any) -> str:
    """★453刀1：单键优先；经验池形态按 motivation。summary 拼装。

    与 ``experience_retriever.experience_text`` 同口径（本模块不 import 检索器，
    避免观测层把重型编码器依赖带进来）。
    """
    # ★先判「通用单键」：经验池的 motivation/summary 属于 _TEXT_KEYS，
    #   若直接用 _get(_TEXT_KEYS) 会只拿到 motivation，把 summary 半句丢掉。
    single = str(_get(hit, _PRIMARY_TEXT_KEYS, "") or "")
    if single:
        return single[:_MAX_TEXT]
    parts = [str(_get(hit, (k,), "") or "").strip() for k in _MULTI_TEXT_KEYS]
    return "。".join(p for p in parts if p)[:_MAX_TEXT]


def _reward_of(hit: Any) -> float:
    """经验池 ``result_reward`` 是 **dict**（377/377 实测），取 intensity。"""
    v = _get(hit, ("result_reward", "reward"), None)
    if isinstance(v, dict):
        v = v.get("intensity")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(v)))


def quality_weight(hit: Any, score: float | None = None) -> float:
    """★453刀2：可解释的质量权重（0~1），模板 / 低 reward / 高风险逐项打折。

    • ``is_template=True``  ⇒ ×0.1（经验池 220/377 = 58.4% 是模板）
    • ``result_reward``    ⇒ 线性加成（Q&A 里只有 8 条 ≥0.5）
    • ``pollution_risk``  ⇒ high ×0.5 / medium ×0.8
    • 检索 score          ⇒ 最终乘子
    """
    _s = float(score) if score is not None else (_score_of(hit) or 0.0)
    _r = _reward_of(hit)
    _w = 0.35 + 0.45 * _r + 0.20 * max(0.0, min(1.0, _s))
    if bool(_get(hit, ("is_template",), False)):
        _w *= 0.1
    _risk = str(_get(hit, ("pollution_risk",), "") or "").lower()
    if _risk == "high":
        _w *= 0.5
    elif _risk == "medium":
        _w *= 0.8
    return round(max(0.0, min(1.0, _w)), 4)


def stable_request_id(query: str) -> str:
    """调用方没给 request_id 时的稳定兜底：同一问题必然共享一个 id。"""
    return hashlib.sha1(str(query or "").encode("utf-8", "ignore")).hexdigest()[:16]


#: ★453R刀5：经验正文前 100 字常是「回答用户关于…」的问题复述，
#: 真正的结论在「回答方法 / 结论 / 结果 / 优化建议」等字段里。
_CONCLUSION_RE = re.compile(
    r"(?:回答方法|结论|结果|方法|优化建议|处置)\s*[:：]\s*([^，,；;。\n]+)")


def inject_text_of(hit: Any) -> str:
    """★453R刀5：结论优先的注入正文；``text`` 仍保留全文供 trace 抽查。

    四种真实形态（经验池实测）：
      ① JSON 语义摘要  → 取「优化建议 / 结论 / 结果」的完整值
      ② 问答记录        → 从「回答方法：」起取到句尾（含置信度，不只取方法名）
      ③ 普通 summary   → 去掉「用户访客提问：…」的问题复述
      ④ 其余           → 退回全文（不臆造结论）
    """
    summary = str(_get(hit, ("summary", "compressed_summary", "raw_summary"), "") or "")
    if summary:
        # ① JSON：semantic 摘要的正文是 JSON 字符串
        if summary.lstrip().startswith("{"):
            try:
                j = json.loads(summary)
                if isinstance(j, dict):
                    for k in ("优化建议", "结论", "结果", "建议", "处置"):
                        if j.get(k):
                            return str(j[k]).strip()[:_MAX_TEXT]
            except ValueError as _e:
                silent_exc(_e, where="evidence_trace::inject_text_of JSON 摘要", level="debug")
        # ② 问答：从第一个结论标记取到句尾，保留「方法 + 置信度」整段
        m = _CONCLUSION_RE.search(summary)
        if m:
            return summary[m.start():].strip()[:_MAX_TEXT]
        # ③ 去问题复述
        m2 = re.search(r"用户访客提问[:：].*?[，,；;]", summary)
        if m2 and m2.end() < len(summary):
            return summary[m2.end():].strip()[:_MAX_TEXT]
        # ④ 普通 summary
        if "提问" not in summary and "回答用户关于" not in summary:
            return summary.strip()[:_MAX_TEXT]
    return _text_of(hit)[:_MAX_TEXT]


def _source_marks(hit: Any) -> tuple:
    """★453R刀6：准入标记只在**留痕/返回项**上加，不删命中（观测完整性）。"""
    try:
        from nucleus.evidence_source import ADMITTED_SOURCES, classify_source
        cls = classify_source(hit)
        tpl = bool(_get(hit, ("is_template", "template_key"), False))
        return cls, bool(cls in ADMITTED_SOURCES and not tpl)
    except Exception as _e:
        silent_exc(_e, where="evidence_trace::_source_marks", level="debug")
        return "other", False


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
            "text": _text_of(h),
            "source": str(_get(h, _SOURCE_KEYS, "") or "")[:120],
            "score": round(s, 4),
            # ★453刀2：质量字段随命中一起给注入层，避免二次查池
            "quality_weight": quality_weight(h, s),
            "is_template": bool(_get(h, ("is_template",), False)),
            "result_reward": _reward_of(h),
            "pollution_risk": str(_get(h, ("pollution_risk",), "") or ""),
            # ★453R刀5/6：结论正文 + 准入类别（只加不改，453 单测不受影响）
            "inject_text": inject_text_of(h),
            "source_class": _source_marks(h)[0],
            "admitted": _source_marks(h)[1],
            "timestamp": _get(h, ("timestamp", "ts"), None),
        })
    # ★453刀2：按质量权重降序（模板/低 reward 沉底），权重相同再比 score。
    out.sort(key=lambda x: (-x["quality_weight"], -x["score"]))
    return out[:max(0, int(top_k))]


def note_hits(query: str, hits: list[Any], *, top_k: int = 3,
              min_score: float = 0.6,
              request_id: str | None = None) -> int:
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
    # ★453刀3：同一次请求（多渠道 × 多轮）共享一个 request_id。
    #   调用方（PulseLung._call_via_channels 入口）生成 uuid 并透传；
    #   未透传时按 query 稳定派生，保证「同问题不虚高计数」。
    _rid = str(request_id or "").strip() or stable_request_id(query)
    rows: list[dict] = []
    for i, h in enumerate(list(hits)[:max(1, int(top_k))]):
        s = _score_of(h)
        rows.append({
            "ts": time.time(),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": "evidence_dry_run",
            "request_id": _rid,
            "query": str(query or "")[:200],
            "rank": i,
            "score": round(s, 4) if s is not None else None,
            "kept": bool(s is not None and s >= float(min_score)),
            "min_score": float(min_score),
            "text": _text_of(h),
            "source": str(_get(h, _SOURCE_KEYS, "") or "")[:120],
            # ★453刀2：数据闸要按「不同问题 + 可用证据」判，不是按行数
            "is_template": bool(_get(h, ("is_template",), False)),
            "result_reward": _reward_of(h),
            "quality_weight": quality_weight(h, s),
            "pollution_risk": str(_get(h, ("pollution_risk",), "") or ""),
            "activation_count": _get(h, ("activation_count",), None),
            "exp_timestamp": _get(h, ("timestamp", "ts"), None),
            "source_class": _source_marks(h)[0],
            "admitted": _source_marks(h)[1],
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

def trace_stats(path: str | None = None, *, min_score: float = 0.65,
                min_evidence: int = 2) -> dict:
    """★453刀3：按 ``distinct(request_id)`` 统计留痕，供数据闸读数。

    ``eligible_requests`` = 该 request_id 拥有 ≥ ``min_evidence`` 条
    ``score >= min_score`` 证据的请求数 —— 烛微 §4.1 的数据闸口径
    （"≥30 个不同请求，且每条有 ≥2 条 score≥0.65 的证据"）。

    ★文件不存在 / 读失败一律返回全 0 + ``available=False``，
      绝不抛异常（留痕是可观测附加物，不是主链路）。
    """
    p = str(path or "").strip() or _trace_path()
    if not p or not os.path.isabs(p):
        p = os.path.join(_ROOT, p)
    out = {"rows": 0, "distinct_requests": 0, "eligible_requests": 0,
           "available": False, "path": p}
    if not os.path.exists(p):
        return out
    buckets: dict = {}
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError as _bad:
                    silent_exc(_bad, where="evidence_trace::trace_stats 坏行", level="debug")
                    continue
                out["rows"] += 1
                rid = str(r.get("request_id") or "") or stable_request_id(str(r.get("query") or ""))
                b = buckets.setdefault(rid, {"rows": 0, "strong": 0})
                b["rows"] += 1
                s = r.get("score")
                if isinstance(s, (int, float)) and not isinstance(s, bool) and s >= float(min_score):
                    b["strong"] += 1
        out["available"] = True
        out["distinct_requests"] = len(buckets)
        out["eligible_requests"] = sum(
            1 for b in buckets.values() if b["strong"] >= int(min_evidence))
    except Exception as _e:
        silent_exc(_e, where="evidence_trace::trace_stats 读盘", level="debug")
    return out
