# -*- coding: utf-8 -*-
"""Active Memory 双路三态（179批 刀1：O-A4 实装）。

设计稿：177批《O-A4 Active Memory 双路设计稿》。
三态：DETERMINISTIC（命中确定性规则→免 LLM，直接凭证，落 matched_rule_id）/
      SUBAGENT（需推理但受限→复用 evolution_worker + O-B1 策略面，本刀仅留接口，真起子代理推后续批）/
      NONE（无匹配且无授权→不动作，留痕待裁决）。

纪律：
  - 总开关 ENABLE_O_A4_DUAL_PATH 默认 False → 完全回到原单路，零行为变化。
  - DETERMINISTIC 路径**从不调用外部 LLM**（命中即返回 matched_rule_id）。
  - SUBAGENT 态本刀不实装真实子代理，仅返回「deferred」结果（不调 LLM）。
  - 全程无静默 except（cw2 红线）：所有分支为确定性返回，异常由调用方处理。
  - ★182批补刀3：DETERMINISTIC 命中后追加「本地确定性凭证」产出（本地时钟 / 本地常量），
    不走 SUBAGENT 子进程、不调用任何 LLM；开关默认关 → 返回值与 179A 完全一致。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime as _dt
from typing import Any

import config as _cfg
from nucleus._silent_except import silent_exc


def _enabled() -> bool:
    return bool(getattr(_cfg, "ENABLE_O_A4_DUAL_PATH", False))


def _state() -> str:
    return str(getattr(_cfg, "O_A4_MODE", "NONE")).upper()


def _subagent_enabled() -> bool:
    return bool(getattr(_cfg, "O_A4_SUBAGENT_ENABLED", False))


def _match_rule(request_text: str) -> str:
    """声明式确定性规则匹配（不调 LLM）。命中返回 matched_rule_id，否则空串。"""
    _t = (request_text or "").strip()
    for _match, _rid in getattr(_cfg, "O_A4_DETERMINISTIC_RULES", []) or []:
        if _match and _match in _t:
            return _rid
    return ""


# ===== ★383刀2（T-383-OA4单向影子）：**单向**影子观察 =====
#: 为什么是单向：双向影子要同一 prompt 走两遍（规则 + LLM）⇒ LLM 调用**翻倍**，
#:   而渠道里挂着收费兜底。单向影子只用**已经发生的** LLM 回答做对照，零额外调用。
#:
#: 它能回答什么（说清楚，不夸大）：
#:   ① 规则覆盖率 —— 有多少请求本来可以被确定性规则免掉 LLM；
#:   ② 凭证可见率 —— 规则命中时，本地凭证字符串是否出现在 LLM 回答里。
#: 它**不能**回答「规则答案与 LLM 答案语义是否一致」——那需要双向，本批不做。
_M383_SHADOW: dict = {"scored": 0, "hit": 0, "no_rule": 0,
                     "agree": 0, "disagree": 0}


def _m383_shadow_on() -> bool:
    """★383刀2（T-383-OA4单向影子）：开关 `ENABLE_M383_OA4_SHADOW`（默认开）。"""
    return bool(getattr(_cfg, "ENABLE_M383_OA4_SHADOW", True))


def _m383_observe_impl(request_text: str, llm_answer: str) -> dict:
    """★383刀2（T-383-OA4单向影子）：拿**已有的** LLM 回答做单向对照，记录规则覆盖率与凭证可见率。

    ★绝不调用 LLM、绝不调用 `resolve()`（避免递归观测）、绝不产生 IO。

    Returns: {on, rule_hit, rule_id, agree(True/False/None), bucket}
      · agree=None 表示「无法判」（没命中规则 / 没产出凭证），**不计入一致率分母**。
    """
    _out = {"on": False, "rule_hit": False, "rule_id": "",
            "agree": None, "bucket": "off"}
    if not _m383_shadow_on():
        return _out
    _out["on"] = True
    _rid = _match_rule(request_text)
    _M383_SHADOW["scored"] = int(_M383_SHADOW.get("scored", 0)) + 1
    if not _rid:
        _M383_SHADOW["no_rule"] = int(_M383_SHADOW.get("no_rule", 0)) + 1
        _out["bucket"] = "no_rule"
        return _out
    _out["rule_hit"] = True
    _out["rule_id"] = _rid
    _M383_SHADOW["hit"] = int(_M383_SHADOW.get("hit", 0)) + 1
    _evidence, _src = _m182_local_evidence(_rid)
    if not _evidence:
        # 规则命中了，但没开凭证 / 没登记产出器 ⇒ 无法判一致，只记覆盖率
        _out["bucket"] = "hit_no_evidence"
        return _out
    _agree = str(_evidence) in str(llm_answer or "")
    _out["agree"] = bool(_agree)
    _out["bucket"] = "agree" if _agree else "disagree"
    _M383_SHADOW["agree" if _agree else "disagree"] = int(
        _M383_SHADOW.get("agree" if _agree else "disagree", 0)) + 1
    return _out


def observe_llm_answer(request_text: str, llm_answer: str) -> dict:
    """★383刀2 原语义 + ★450刀3 旁路观测（**只记不改**：返回值逐字不变）。

    包装而不改内层的原因：内层有 4 个 return 分支（关/无规则/无凭证/有结论），
    在每个分支里插记录会漏一个；包装层只在一个地方兜住全部返回。
    """
    _out = _m383_observe_impl(request_text, llm_answer)
    try:
        _m450_note_observe(request_text, llm_answer, _out)
    except Exception as _e:
        silent_exc(_e, where="active_memory_dual::observe_llm_answer wrap", level="debug")
    return _out


# ==================== ★450刀3：O-A4 OBSERVE 影子观测 ====================
#
# 任务书 §刀3：O-A4 还没开，先开**影子观测** —— 只记录不改行为，看命中率与差异。
#
# 能观测什么、不能观测什么（说清楚，不夸大）
# ------------------------------------------
# 383 那套是**单向**影子：拿"已经产出的 LLM 回答"做规则对照，零额外调用。
# 因此本刀能回答：
#   · 规则覆盖率 —— 有多少请求本来能被确定性规则免掉 LLM；
#   · 可省未省率（diverged）—— 规则命中了、却还是调了 LLM 的比例；
#   · 凭证可见率（agree）—— 规则命中且有本地凭证时，凭证是否出现在回答里。
# 它**不能**回答「规则答案与 LLM 答案语义是否一致」—— 那需要双向（同一 prompt
# 走两遍），会让 LLM 调用翻倍，本批明确不做。
#
# 落盘策略（为什么不逐条写）
# --------------------------
# `_m40_trace_call` 是**每次** LLM 调用都会经过的收口（实测 ~84 次/小时）。
# 逐条落盘 = 2000 行/天，观测文件自己变成噪声源（与 449 治的是同一种病）。
# 所以：
#   · **规则命中**的观测一律落行（这类少，且正是"差异"所在）；
#   · 其余按 `M450_OA4_SUMMARY_SEC`（默认 3600s）落一行**汇总**（带精确计数）；
#   · 汇总行也保证"文件还在写"这件事本身可验证。
#
# ★不改决策：`resolve()` 的调用方一字未动，本模块只读 `_match_rule` 与 `_m182_local_evidence`。
_M450_OA4_TRACE_REL_DEFAULT: str = os.path.join("data", "o_a4_observe_trace.jsonl")
_M450_OA4_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

_M450_OA4_STAT: dict = {
    "since": time.time(), "scored": 0, "rule_hit": 0, "no_rule": 0,
    "diverged": 0, "would_agree": 0, "would_disagree": 0,
    "rows": 0, "summaries": 0, "last_summary": time.time(),
}


def _m450_oa4_on() -> bool:
    """★450刀3 开关 `ENABLE_M450_OA4_OBSERVE`（默认 True）。"""
    try:
        return bool(getattr(_cfg, "ENABLE_M450_OA4_OBSERVE", True))
    except Exception as _e:
        silent_exc(_e, where="active_memory_dual::_m450_oa4_on", level="debug")
        return True


def _m450_oa4_path() -> str:
    """留痕文件绝对路径（相对路径按仓根解析）；空串表示不写。"""
    try:
        _rel = str(getattr(_cfg, "M450_OA4_TRACE_PATH",
                           _M450_OA4_TRACE_REL_DEFAULT) or "")
    except Exception as _e:
        silent_exc(_e, where="active_memory_dual::_m450_oa4_path cfg", level="debug")
        _rel = _M450_OA4_TRACE_REL_DEFAULT
    if not _rel:
        return ""
    return _rel if os.path.isabs(_rel) else os.path.join(_M450_OA4_ROOT, _rel)


def _m450_oa4_write(row: dict) -> bool:
    """追加一行；默认生产路径**在测试进程里拒写**（446/447 三次同型坑的护栏）。"""
    try:
        _p = _m450_oa4_path()
        if not _p:
            return False
        if "pytest" in sys.modules and os.path.normcase(os.path.normpath(_p)) == \
                os.path.normcase(os.path.normpath(
                    os.path.join(_M450_OA4_ROOT, _M450_OA4_TRACE_REL_DEFAULT))):
            return False
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8", newline="\n") as _f:
            _f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _M450_OA4_STAT["rows"] = int(_M450_OA4_STAT.get("rows") or 0) + 1
        return True
    except Exception as _e:
        silent_exc(_e, where="active_memory_dual::_m450_oa4_write", level="debug")
        return False


def _m450_oa4_summary(force: bool = False) -> str:
    """按 `M450_OA4_SUMMARY_SEC` 落一行汇总。返回写入路径或空串。"""
    try:
        if not _m450_oa4_on():
            return ""
        from nucleus import alert_dedup as _ad450o
        _iv = _ad450o.cfg_num("M450_OA4_SUMMARY_SEC", 3600.0)
        _now = time.time()
        if not force and (_now - float(_M450_OA4_STAT.get("last_summary") or 0.0)) < _iv:
            return ""
        _s = _M450_OA4_STAT
        _n = int(_s.get("scored") or 0)
        _h = int(_s.get("rule_hit") or 0)
        _row = {
            "ts": _now, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": "summary", "mode": "observe",
            "scored": _n, "rule_hit": _h, "no_rule": int(_s.get("no_rule") or 0),
            "rule_coverage_pct": (round(_h * 100.0 / _n, 2) if _n else None),
            "diverged": int(_s.get("diverged") or 0),
            "diverged_pct_of_hit": (round(int(_s.get("diverged") or 0) * 100.0 / _h, 2)
                                     if _h else None),
            "would_agree": int(_s.get("would_agree") or 0),
            "would_disagree": int(_s.get("would_disagree") or 0),
            "uptime_sec": round(_now - float(_s.get("since") or _now), 1),
            "note": "单向影子：只用已有 LLM 回答，零额外调用；不改任何决策",
        }
        _p = _m450_oa4_write(_row)
        _s["last_summary"] = _now
        if _p:
            _s["summaries"] = int(_s.get("summaries") or 0) + 1
        return _p
    except Exception as _e:
        silent_exc(_e, where="active_memory_dual::_m450_oa4_summary", level="debug")
        return ""


def _m450_note_observe(request_text: str, llm_answer: str, out: dict) -> None:
    """★450刀3：把一次单向影子观测记进计数，并按策略落盘（**绝不抛异常**）。"""
    try:
        if not _m450_oa4_on():
            return
        _o = out or {}
        if not _o.get("on"):
            return                       # 383 影子自己关着 ⇒ 不记（否则分母掺假）
        _s = _M450_OA4_STAT
        _s["scored"] = int(_s.get("scored") or 0) + 1
        _bucket = str(_o.get("bucket") or "")
        _rule_hit = bool(_o.get("rule_hit"))
        if _rule_hit:
            _s["rule_hit"] = int(_s.get("rule_hit") or 0) + 1
            # 规则命中却仍走了 LLM ⇒ "可省未省"，这就是 O-A4 与现有决策的差异
            _s["diverged"] = int(_s.get("diverged") or 0) + 1
        else:
            _s["no_rule"] = int(_s.get("no_rule") or 0) + 1
        _agree = _o.get("agree")
        if _agree is True:
            _s["would_agree"] = int(_s.get("would_agree") or 0) + 1
        elif _agree is False:
            _s["would_disagree"] = int(_s.get("would_disagree") or 0) + 1
        # 规则命中 ⇒ 逐条落（差异样本）；否则交给汇总
        if _rule_hit:
            _m450_oa4_write({
                "ts": time.time(), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": "observe", "mode": "observe",
                "request_head": str(request_text or "")[:120],
                "o_a4_suggestion": (f"deterministic:{_o.get('rule_id')}"
                                     if _o.get("rule_id") else "llm"),
                "actual_decision": "llm",
                "diverged": True,
                "rule_id": str(_o.get("rule_id") or ""),
                "bucket": _bucket,
                "agree": _agree,
                "answer_len": len(str(llm_answer or "")),
                "note": "规则可免 LLM 但实际仍调用 ⇒ 可省未省（不改决策，仅观测）",
            })
        _m450_oa4_summary()
    except Exception as _e:
        silent_exc(_e, where="active_memory_dual::_m450_note_observe", level="debug")


def o_a4_observe_stats() -> dict:
    """★450刀3：影子观测台面读数（精确计数；供快照/单测读）。"""
    try:
        _s = dict(_M450_OA4_STAT)
        _n = int(_s.get("scored") or 0)
        _h = int(_s.get("rule_hit") or 0)
        return {
            "enabled": _m450_oa4_on(), "mode": "observe",
            "scored": _n, "rule_hit": _h, "no_rule": int(_s.get("no_rule") or 0),
            "rule_coverage_pct": (round(_h * 100.0 / _n, 2) if _n else None),
            "diverged": int(_s.get("diverged") or 0),
            "would_agree": int(_s.get("would_agree") or 0),
            "would_disagree": int(_s.get("would_disagree") or 0),
            "rows": int(_s.get("rows") or 0),
            "summaries": int(_s.get("summaries") or 0),
            "since": _s.get("since"), "last_summary": _s.get("last_summary"),
            "trace_path": _m450_oa4_path(),
        }
    except Exception as _e:
        silent_exc(_e, where="active_memory_dual::o_a4_observe_stats", level="debug")
        return {"enabled": False, "mode": "observe", "scored": 0}


def reset_observe_stats() -> None:
    """清空影子观测计数（**仅供测试**）。"""
    try:
        for _k in list(_M450_OA4_STAT):
            if _k in ("since", "last_summary"):
                _M450_OA4_STAT[_k] = time.time()
            elif isinstance(_M450_OA4_STAT[_k], int):
                _M450_OA4_STAT[_k] = 0
    except Exception as _e:
        silent_exc(_e, where="active_memory_dual::reset_observe_stats", level="debug")



def shadow_stats() -> dict:
    """★383刀2（T-383-OA4单向影子）：影子读数 —— 覆盖率 / 凭证可见率分开算，**不给合并结论**。"""
    _s = dict(_M383_SHADOW)
    _n = int(_s.get("scored", 0)) or 0
    _h = int(_s.get("hit", 0)) or 0
    _scorable = int(_s.get("agree", 0)) + int(_s.get("disagree", 0))
    return {
        "scored": _n,
        "rule_hit": _h,
        "rule_coverage_pct": (round(_h * 100.0 / _n, 2) if _n else None),
        "scorable": _scorable,
        "agree": int(_s.get("agree", 0)),
        "disagree": int(_s.get("disagree", 0)),
        "agree_rate_pct": (round(int(_s.get("agree", 0)) * 100.0 / _scorable, 2)
                            if _scorable else None),
        "note": "单向影子：只用已有 LLM 回答，零额外调用；agree_rate 仅在…有凭证时可算",
        # ★450刀3：把 OBSERVE 影子的落盘计数一并给观测面（443 已把 shadow_stats
        #   接进 `_build_observability_snapshot()["oa4_shadow"]`）——
        #   否则"影子开了没开、落了几行"只能去翻文件。
        "observe": o_a4_observe_stats(),
    }


def reset_shadow_stats() -> None:
    """★383刀2（T-383-OA4单向影子）：清空影子计数（**仅供测试**）。"""
    for _k in list(_M383_SHADOW):
        _M383_SHADOW[_k] = 0


def resolve(request_text: str, *, llm_call=None) -> dict[str, Any]:
    """双路三态解析。

    返回 dict: {state, matched_rule_id, llm_used, action, detail}
    - 任何当前态（DETERMINISTIC / SUBAGENT-off / NONE / 总开关关）都**不会**调用 llm_call。
    - llm_call 仅作为「未来需外部 LLM 的扩展点」保留参数，本刀实现不触发。
    """
    if not _enabled():
        return {
            "state": "NONE",
            "matched_rule_id": "",
            "llm_used": False,
            "action": "passthrough",
            "detail": "dual path disabled",
        }

    _st = _state()
    if _st == "DETERMINISTIC":
        _rid = _match_rule(request_text)
        if _rid:
            _evidence, _src = _m182_local_evidence(_rid)
            return {
                "state": "DETERMINISTIC",
                "matched_rule_id": _rid,
                "llm_used": False,
                "action": "use_cached_rule",
                "detail": "rule hit, no LLM call",
                # ★182批补刀3：本地确定性凭证（空串=未启用/未登记，行为同 179A）
                "evidence": _evidence,
                "evidence_source": _src,
            }
        # 确定性前置只处理已知规则；未知请求不调 LLM，回落 NONE 待裁决
        return {
            "state": "NONE",
            "matched_rule_id": "",
            "llm_used": False,
            "action": "no_action",
            "detail": "no deterministic rule matched",
        }

    if _st == "SUBAGENT":
        if _subagent_enabled():
            return {
                "state": "SUBAGENT",
                "matched_rule_id": "",
                "llm_used": False,
                "action": "deferred",
                "detail": "subagent path deferred to evolution_worker (not launched this knife)",
            }
        return {
            "state": "NONE",
            "matched_rule_id": "",
            "llm_used": False,
            "action": "no_action",
            "detail": "subagent disabled",
        }

    # NONE（默认）
    return {
        "state": "NONE",
        "matched_rule_id": "",
        "llm_used": False,
        "action": "no_action",
        "detail": "no active memory action",
    }


# ===== [M182-LOCAL-EVIDENCE] 182批补刀3：DETERMINISTIC 本地确定性凭证（O-A4 续）=====
# 来源：182批补充任务书补刀3；179A 已实装三态接口 + DETERMINISTIC 免 LLM 路径（落 matched_rule_id）。
# 约束：① 绝不调用外部 LLM；② 绝不启动 SUBAGENT 子进程（真起子代理推 183，依赖 O-B1 三层策略面）；
#       ③ 开关关（默认）→ 命中仅落 matched_rule_id，与 179A 行为完全一致。


def _local_evidence_on() -> bool:
    return bool(getattr(_cfg, "ENABLE_O_A4_LOCAL_EVIDENCE", False))


def _m182_local_time() -> str:
    """本地时钟凭证（确定性：本机系统时钟，无网络、无 LLM、无子进程）。"""
    return _dt.now().strftime("%Y-%m-%d %H:%M:%S")


def _m182_local_identity() -> str:
    """本地身份凭证（确定性：取配置常量，缺省回落字面量）。"""
    return str(getattr(_cfg, "ASSISTANT_NAME", "") or "tongtong")


_M182_LOCAL_PROVIDERS = {
    "local_time": _m182_local_time,
    "local_identity": _m182_local_identity,
}


def _m182_local_evidence(rule_id: str) -> tuple[str, str]:
    """按配置映射产出本地确定性凭证；返回 (evidence, source)。

    - 开关关 / 规则未登记 / 产出器缺失 => ("", "")，调用方行为同 179A；
    - 全程确定性分支：无 except、无 IO、无网络、无子进程、无 LLM。
    """
    if not rule_id or not _local_evidence_on():
        return "", ""
    _mapping = getattr(_cfg, "O_A4_LOCAL_EVIDENCE_PROVIDERS", {}) or {}
    _provider = _M182_LOCAL_PROVIDERS.get(_mapping.get(rule_id, ""))
    if _provider is None:
        return "", ""
    return str(_provider()), "local_deterministic"
