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


def record_shadow_compare(suggestion: dict, path: str = None) -> bool:
    """把「主笔路径 vs 影子路径」对比记录追加到影子日志 jsonl（不 emit）。"""
    if not suggestion:
        return False
    _p = path or _SHADOW_COMPARE_REL
    try:
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
        _rec = dict(suggestion)
        _rec["ts"] = round(time.time(), 3)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_rec, ensure_ascii=False) + "\n")
        return True
    except Exception as _e:
        silent_exc(_e, where="shadow_rules.record_shadow_compare", level="debug")
        return False
