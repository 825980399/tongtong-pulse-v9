# -*- coding: utf-8 -*-
"""493.19 刀1：影子态主笔算子规则表（纯影子，绝不换主笔）。

职责：
  - 从 `data/self_index/absorb_trace.jsonl` 读 `result=applied` 的真实规则，
    建「影子规则表」（rule_id / concept / path / source）；
  - 对含某概念的 query，算「主笔路径 vs 影子路径」对比：
    * 主笔路径 = 主笔当前的知识路径（调用方可传入真实值；缺省为基线 ["/知识"]）；
    * 影子路径 = **如果**这条规则生效会走的路径（规则 path 优先，口径对齐
      QICA._resolve_knowledge_paths 的领域知识命中分支）；
  - 把对比记录追加到影子日志 `data/monitor/shadow_rule_compare.jsonl`；
  - 给 fastpath 的 explain 加 `shadow_suggestion` 字段。

纪律（任务书 §刀1）：**只影子态**——不写 QICA._domain_knowledge、不改
classify 判定、不改主笔答案、不 emit 事件、不开任何生产主笔开关。
本模块所有行为都是「记录如果用了会怎样」，主笔路径保持原样。
"""
from __future__ import annotations

import json
import os
import re
import time

from nucleus._silent_except import silent_exc

_SHADOW_RULES_REL = os.path.join("data", "self_index", "absorb_trace.jsonl")
_SHADOW_COMPARE_REL = os.path.join("data", "monitor", "shadow_rule_compare.jsonl")

# 未注入知识树/节点池时 QICA 的默认知识路径（493.17 探针实证基线）
_BASELINE_PATHS = ["/知识"]

# mtime 缓存：避免每条 chat 都重读 absorb_trace
_RULES_CACHE = {"mtime": None, "rules": None}


def load_shadow_rules(path: str = None) -> list[dict]:
    """从 absorb_trace.jsonl 读 result=applied* 的规则 → 影子规则表。

    返回 [{rule_id, concept, path, source}, ...]；文件缺失/损坏返回 []（不编规则）。
    带 mtime 缓存（仅默认路径启用；显式 path 用于单测隔离）。
    """
    p = path or _SHADOW_RULES_REL
    try:
        _mt = os.path.getmtime(p) if os.path.isfile(p) else None
        if (path is None and _RULES_CACHE["rules"] is not None
                and _RULES_CACHE["mtime"] == _mt):
            return list(_RULES_CACHE["rules"])
        _out = []
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _r = json.loads(_line)
                    except Exception as _e:
                        silent_exc(_e, where="shadow_rules.load_shadow_rules.json",
                                   level="debug")
                        continue
                    if _r.get("result") not in ("applied", "applied_correction_edge"):
                        continue
                    _out.append({
                        "rule_id": _r.get("rule_id"),
                        "concept": _r.get("concept"),
                        "path": _r.get("path"),
                        "source": _r.get("source"),
                    })
        if path is None:
            _RULES_CACHE["mtime"] = _mt
            _RULES_CACHE["rules"] = list(_out)
        return _out
    except Exception as _e:
        silent_exc(_e, where="shadow_rules.load_shadow_rules", level="debug")
        return []


def match_shadow_rules(query, rules: list[dict] = None) -> list[dict]:
    """返回 concept 命中 query 的影子规则（子串口径，与 QICA 领域知识一致）。"""
    try:
        _q = str(query or "")
        if not _q:
            return []
        _rs = rules if rules is not None else load_shadow_rules()
        return [r for r in _rs if r.get("concept") and r["concept"] in _q]
    except Exception as _e:
        silent_exc(_e, where="shadow_rules.match_shadow_rules", level="debug")
        return []


def build_shadow_suggestion(query, primary_paths=None,
                            rules: list[dict] = None) -> dict | None:
    """算「主笔路径 vs 影子路径」对比建议（纯计算，不改任何主笔状态）。

    primary_paths: 主笔当前知识路径；None → 基线 ["/知识"]（未注入知识树口径）。
    返回 None = 无规则命中（不编造建议）。
    """
    try:
        _hits = match_shadow_rules(query, rules)
        if not _hits:
            return None
        _pp = list(primary_paths) if primary_paths else list(_BASELINE_PATHS)
        # 影子路径：规则 path 优先，其后补主笔路径（对齐 _resolve_knowledge_paths:1510-1518）
        _sp = []
        for _r in _hits:
            if _r.get("path") and _r["path"] not in _sp:
                _sp.append(_r["path"])
        for _x in _pp:
            if _x not in _sp:
                _sp.append(_x)
        _sp = _sp[:3]
        return {
            "query": str(query),
            "primary_paths": _pp,
            "shadow_paths": _sp,
            "changed": _sp != _pp,
            "rules": [{"rule_id": _r.get("rule_id"), "concept": _r.get("concept"),
                       "path": _r.get("path"), "source": _r.get("source")}
                      for _r in _hits],
            "note": "影子态建议：仅记录「若规则生效会走的路径」，主笔路径与答案不变",
        }
    except Exception as _e:
        silent_exc(_e, where="shadow_rules.build_shadow_suggestion", level="debug")
        return None


def record_shadow_compare(suggestion: dict, path: str = None,
                          answer_b: str = None, answer_a: str = None,
                          winner: str = None,
                          answer_a_source: str = None) -> int:
    """把「主笔路径 vs 影子路径」对比记录追加到影子日志 jsonl（不 emit）。

    493.20 刀1 格式修复：**每条规则一条记录**，顶层带
    `rule_id` / `primary_path` / `shadow_path`（单数字段，任务书验收口径），
    同时保留 493.19 的 `primary_paths` / `shadow_paths` 复数结构（向后兼容）。
    返回实际写入的记录数（无建议/无规则 → 0）。

    493.22 刀3 A/B 真值（可选、默认 None，向后兼容）：
      - `answer_b`：影子候选文本（would_answer 同源的本地 fastpath 候选，**真实可得**）；
      - `answer_a`：主笔实际回答文本。当前 `ENABLE_M493_SELF_QUERY_FASTPATH=False`，
        主笔实际回答走大模型且**未落盘**，取不到 ⇒ 如实为 None，不编造；
      - `winner`：只有 answer_a/answer_b 都有真实文本时才能判；当前无真实双答案，
        **恒为 None**（任务书「没有真实对比不要写假 winner」）。
      要产生真对比，需主笔 emit 链路按同一 cid/session 落盘实际回答文本（后续批次）。
    """
    if not suggestion:
        return 0
    _rules = suggestion.get("rules") or []
    if not _rules:
        return 0
    _p = path or _SHADOW_COMPARE_REL
    _pp = suggestion.get("primary_paths") or list(_BASELINE_PATHS)
    _sp = suggestion.get("shadow_paths") or []
    _primary = _pp[0] if _pp else None
    # 493.21 刀2：按 (query, rule_id) 去重，同一 query 的同一规则不重复写
    _seen = set()
    if os.path.isfile(_p):
        try:
            with open(_p, encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _old = json.loads(_line)
                    except Exception:
                        continue
                    _seen.add((_old.get("query"), _old.get("rule_id")))
        except Exception as _e:
            silent_exc(_e, where="shadow_rules.record_shadow_compare.dedup",
                       level="debug")
    try:
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
        _n = 0
        with open(_p, "a", encoding="utf-8") as _f:
            for _r in _rules:
                _key = (suggestion.get("query"), _r.get("rule_id"))
                if _key in _seen:
                    continue
                _seen.add(_key)
                _shadow = _r.get("path") or (_sp[0] if _sp else None)
                _rec = {
                    "ts": round(time.time(), 3),
                    "query": suggestion.get("query"),
                    # 493.20：单数顶层字段（任务书验收）
                    "rule_id": _r.get("rule_id"),
                    "primary_path": _primary,
                    "shadow_path": _shadow,
                    "changed": bool(suggestion.get("changed")),
                    # 493.19 复数结构保留（向后兼容）
                    "primary_paths": _pp,
                    "shadow_paths": _sp,
                    "rules": _rules,
                    "note": suggestion.get("note"),
                    # 493.22 刀3：A/B 真值字段，默认 None（不写假 winner）
                    "answer_a": answer_a,
                    "answer_a_source": answer_a_source,
                    "answer_b": answer_b,
                    "winner": winner,
                }
                _f.write(json.dumps(_rec, ensure_ascii=False) + "\n")
                _n += 1
        return _n
    except Exception as _e:
        silent_exc(_e, where="shadow_rules.record_shadow_compare", level="debug")
        return 0


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s)).lower()


def _judge_winner(answer_a, answer_b) -> str | None:
    """493.23 刀1：诚实 winner 判定——绝不写假 verdict。

    只有「双答案文本归一化相等」才能确定判为 ``平局``；其余皆无质量裁判，
    只能给**状态型**表述，不能冒充质量 verdict（主笔赢/影子赢/都不对 都需
    真实 ground truth 才能判，本环境没有 ⇒ 不写）。

    - answer_a / answer_b 任一缺失 → None（无真实双答案，不写假 winner）
    - 双答案归一化相等 → ``平局``
    - answer_b 缺失 → ``无影子候选``（影子侧无答案可比，非质量 verdict）
    - 双答案都在但不同 → ``双答案待评估``（已记录，等人工/金标裁判）
    """
    if not answer_a or not answer_b:
        return None
    if _norm(answer_a) == _norm(answer_b):
        return "平局"
    if answer_b is None or str(answer_b).strip() == "":
        return "无影子候选"
    return "双答案待评估"


def fill_compare_from_primary(primary_map: dict | None = None,
                              path: str = None) -> int:
    """493.23 刀1：用真实主笔答案(answer_a)补全 compare 记录并计算诚实 winner。

    primary_map: ``{query: (answer_text, provenance)}``；为 None 时从
    ``self_query_primary_answer.jsonl`` 取每 query 最新主笔答案。
    返回补全的记录数（answer_a 被写入且 winner 被诚实重算）。

    纪律：绝不写假 winner（见 ``_judge_winner``）；provenance 必须如实标注
    （如 ``offline_local_fallback`` 表示本沙箱 LLM 不可达时的真实兜底答案，
    不冒充 LLM 主笔答案）。
    """
    _p = path or _SHADOW_COMPARE_REL
    try:
        if not os.path.isfile(_p):
            return 0
        # 收集主笔答案
        _pmap = {}
        if primary_map:
            _pmap = dict(primary_map)
        else:
            _pap = os.path.join("data", "monitor",
                                "self_query_primary_answer.jsonl")
            if os.path.isfile(_pap):
                try:
                    with open(_pap, encoding="utf-8") as _pf:
                        for _l in _pf:
                            _l = _l.strip()
                            if not _l:
                                continue
                            try:
                                _r = json.loads(_l)
                            except Exception as _e:
                                silent_exc(_e, where="shadow_rules.fill_compare.primary_line",
                                           level="debug")
                                continue
                            _pmap[_r.get("query")] = (_r.get("answer"),
                                                       _r.get("source"))
                except Exception as _e:
                    silent_exc(_e, where="shadow_rules.fill_compare.primary",
                               level="debug")
        # 读现有 compare 并重算
        _recs = []
        with open(_p, encoding="utf-8") as _f:
            for _l in _f:
                _l = _l.strip()
                if not _l:
                    continue
                try:
                    _recs.append(json.loads(_l))
                except Exception as _e:
                    silent_exc(_e, where="shadow_rules.fill_compare.compare_line",
                               level="debug")
                    continue
        _filled = 0
        for _rec in _recs:
            _q = _rec.get("query")
            if _q in _pmap:
                _ans, _src = _pmap[_q]
                if _ans:
                    _rec["answer_a"] = _ans
                    _rec["answer_a_source"] = _src
                    _rec["winner"] = _judge_winner(_ans, _rec.get("answer_b"))
                    _filled += 1
        # 写回
        with open(_p, "w", encoding="utf-8") as _f:
            for _rec in _recs:
                _f.write(json.dumps(_rec, ensure_ascii=False) + "\n")
        return _filled
    except Exception as _e:
        silent_exc(_e, where="shadow_rules.fill_compare_from_primary",
                   level="debug")
        return 0
