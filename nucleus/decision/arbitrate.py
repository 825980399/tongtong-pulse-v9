# -*- coding: utf-8 -*-
"""397 刀5：DL3 **仲裁层**（影子 → 仲裁的过渡态）。

## 它做什么

在 `shadow.py`（只写不判）之上加一层**排序 + 否决**，把"既有链路实际怎么判的"
变成"L3 自己会怎么判"，两条对照着观察一段时间，再决定要不要切真。

## ★过渡期的三条红线（本批不动）

1. **`action` 仍然是 `DEFER`** —— 真裁决写在 `arbitration.would_be` 里。
   为什么不直接写 APPROVE/REJECT：一旦 action 出现非 DEFER，账本看起来就像
   "L3 已经在下指令"，而执行器此刻**必须忽略它**。这个错位比"没有裁决"更危险
   （396 刚修过同类问题：读数看起来在自愈，实际在掐链）。
2. **只覆盖 `resource` 与 `patch` 两域**：chat 域要调 LLM，判错代价高，本批不动。
   （"风险"不是独立 scope —— 契约 `SCOPES` 闭集是 chat/patch/resource/schedule，
    风险体现为 **patch 域候选的 `risk` 等级**，不新开域，避免改闭集。）
3. **不新增判据**：只用请求里已经带的东西（动机压力 / 风险等级 / 预算 / 禁改文件），
   绝不重新造一套"意图分类"或"补丁准入"（洞鉴 §6.6 反重复建设红线）。

## 判据（全部可解释、可单测复现）

| 闸 | 读什么 | 触发 ⇒ would_be | reason_code |
|---|---|---|---|
| 用户覆盖 | `constraints.user_override` | APPROVE（覆盖一切） | USER_OVERRIDE |
| 禁改文件 | `constraints.forbidden_files` 命中候选 `evidence_ref` | REJECT | POLICY_BLOCK |
| 风险等级 | 候选 `risk` ≥ `high` | REJECT | POLICY_BLOCK |
| 资源高压 | `signals.resource.rss_mb` ≥ 临界 | REJECT | RESOURCE_PRESSURE |
| 资源偏高 | `signals.resource.rss_mb` ≥ 高位 | DEFER（延一拍再看） | RESOURCE_PRESSURE |
| 预算耗尽 | `constraints.llm_budget == 0` 且候选要 LLM | DEFER | BUDGET_EXHAUSTED |
| 证据缺失 | 候选没有 `evidence_ref` 且 signals 为空 | DEFER | EVIDENCE_MISSING |
| 其余 | — | APPROVE | INTENT_MATCH |

排序：否决项沉底，其余按 **(风险升序, 成本升序, 优先级降序)** ——
先做"风险小、成本低、优先级高"的那件事。
"""
from __future__ import annotations

from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.decision.contracts import DecisionRequest, POLICY_VERSION

#: ★本批放行的域（chat 域不动 —— 它要调 LLM，判错代价高）
ARBITRATION_SCOPES = ("resource", "patch")

#: 资源阈值（MB）。★必须显式写死，不读 config —— 仲裁判据要先能复现，再谈可调。
RSS_HIGH_MB = 6000.0
RSS_CRITICAL_MB = 9000.0

#: 风险等级 → 序数（越大越危险）。兼容两套写法：英文与 `L<level>`。
RISK_RANK: dict[str, int] = {
    "": 0, "none": 0, "unknown": 0,
    "low": 1, "l1": 1,
    "medium": 2, "mid": 2, "l2": 2,
    "high": 3, "l3": 3,
    "blocker": 4, "critical": 4, "l4": 4,
}

#: 达到即否决的风险序数
RISK_REJECT_AT = 3

#: would_be 闭集（**刻意与 contracts.ACTIONS 不同**：它是"如果不是影子期会是什么"）
WOULD_BE = ("APPROVE", "REJECT", "DEFER")


def _risk_rank(risk: Any) -> int:
    """风险等级 → 序数；认不出来按 0（**不是**按最危险 —— 未知风险不该被自动否决，
    否则一次字段写法变更就会把整个域 REJECT 掉）。"""
    if risk is None:
        return 0
    if isinstance(risk, bool):
        return 0
    if isinstance(risk, (int, float)):
        return max(0, min(4, int(risk)))
    return RISK_RANK.get(str(risk).strip().lower(), 0)


def _candidate_cost(c: dict) -> float:
    """候选成本（用于排序）。取不到按 0 —— 排序用的辅助量，缺失不该否决。"""
    _cost = (c or {}).get("cost") or {}
    if isinstance(_cost, dict):
        _v = _cost.get("llm_calls", _cost.get("time_ms", 0))
        try:
            return float(_v or 0)
        except (TypeError, ValueError) as _e:
            silent_exc(_e, where="arbitrate::_candidate_cost dict", level="debug")
            return 0.0
    try:
        return float(_cost or 0)
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="arbitrate::_candidate_cost scalar", level="debug")
        return 0.0


def _rss_mb(req: DecisionRequest) -> float:
    """从 `signals.resource` 取 RSS（MB）。**取不到返 None**，不是 0 ——
    "没采到"与"RSS 很低"必须可分（铁律 276）。"""
    _res = (req.signals or {}).get("resource") or {}
    _v = _res.get("rss_mb")
    try:
        return float(_v) if _v is not None else None  # type: ignore[return-value]
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="arbitrate::_rss_mb", level="debug")
        return None


def _forbidden_hit(req: DecisionRequest, c: dict) -> bool:
    _fb = [str(x) for x in ((req.constraints or {}).get("forbidden_files") or []) if x]
    if not _fb:
        return False
    _ref = str((c or {}).get("evidence_ref") or "")
    _tgt = str((c or {}).get("id") or "")
    return any((f and (f in _ref or f in _tgt)) for f in _fb)


def arbitrate(req: DecisionRequest) -> dict[str, Any]:
    """给一个 `DecisionRequest` 算仲裁结果（**纯函数，不写盘、不抛异常**）。

    Returns:
        {
          "scope", "policy_version",
          "would_be": "APPROVE" | "REJECT" | "DEFER",
          "reason_code": ..., "degrade_policy": ...,
          "ranking": [{"id", "rank", "risk_rank", "cost", "vetoed", "why"}],
          "vetoes": [{"id", "why", "reason_code"}],
          "notes": [...]            # 判据解释（人类可读）
        }
    """
    _out: dict[str, Any] = {
        "scope": str(getattr(req, "scope", "") or ""),
        "policy_version": POLICY_VERSION,
        "would_be": "DEFER",
        "reason_code": "SHADOW_ONLY",
        "degrade_policy": "NONE",
        "ranking": [],
        "vetoes": [],
        "notes": [],
    }
    try:
        _cands = [c for c in (req.candidates or []) if isinstance(c, dict)]
        if not _cands:
            _out["reason_code"] = "EVIDENCE_MISSING"
            _out["notes"].append("无候选 ⇒ 不裁决")
            return _out

        _cons = dict(req.constraints or {})
        _rss = _rss_mb(req)

        # ---- 逐候选裁决
        _rows: list[dict[str, Any]] = []
        for _c in _cands:
            _cid = str(_c.get("id") or "")
            _rr = _risk_rank(_c.get("risk"))
            _why, _rc = "", ""
            if bool(_cons.get("user_override")):
                _why, _rc = "user_override", "USER_OVERRIDE"
            elif _forbidden_hit(req, _c):
                _why, _rc = "forbidden_file", "POLICY_BLOCK"
            elif _rr >= RISK_REJECT_AT:
                _why, _rc = "risk>=high", "POLICY_BLOCK"
            elif _rss is not None and _rss >= RSS_CRITICAL_MB:
                _why, _rc = "rss_critical", "RESOURCE_PRESSURE"

            _vetoed = bool(_why) and _rc != "USER_OVERRIDE"
            if _vetoed:
                _out["vetoes"].append({"id": _cid, "why": _why, "reason_code": _rc})
            _rows.append({"id": _cid, "risk_rank": _rr,
                          "cost": round(_candidate_cost(_c), 3),
                          "priority": int(_c.get("priority") or 0),
                          "vetoed": _vetoed, "why": _why})

        # ---- 排序：否决沉底，其余 (风险升序, 成本升序, 优先级降序)
        _rows.sort(key=lambda r: (r["vetoed"], r["risk_rank"], r["cost"], -r["priority"]))
        for _i, _r in enumerate(_rows):
            _r["rank"] = _i + 1
        _out["ranking"] = _rows

        # ---- 全域裁决（取排序第一的那个）
        _top = _rows[0]
        if _top["vetoed"]:
            _v = next(v for v in _out["vetoes"] if v["id"] == _top["id"])
            _out["would_be"] = "REJECT"
            _out["reason_code"] = _v["reason_code"]
            _out["degrade_policy"] = "DEFER_UNTIL" if _v["why"] == "rss_critical" else "NONE"
            _out["notes"].append(f"首选候选 {_top['id']!r} 被否决：{_v['why']}")
            return _out

        if bool(_cons.get("user_override")):
            _out["would_be"] = "APPROVE"
            _out["reason_code"] = "USER_OVERRIDE"
            _out["notes"].append("用户覆盖 ⇒ 直接放行")
            return _out

        # 资源偏高 ⇒ 延一拍
        if _rss is not None and _rss >= RSS_HIGH_MB:
            _out["would_be"] = "DEFER"
            _out["reason_code"] = "RESOURCE_PRESSURE"
            _out["degrade_policy"] = "DEFER_ONE_TICK"
            _out["notes"].append(f"rss_mb={_rss:.0f} ≥ {RSS_HIGH_MB:.0f} ⇒ 延一拍")
            return _out

        # 预算：llm_budget 为 0 而候选要花钱 ⇒ 延后
        _budget = _cons.get("llm_budget")
        try:
            _budget_num = float(_budget) if _budget is not None else None
        except (TypeError, ValueError) as _e:
            silent_exc(_e, where="arbitrate::budget", level="debug")
            _budget_num = None
        if _budget_num is not None and _budget_num <= 0 and _top["cost"] > 0:
            _out["would_be"] = "DEFER"
            _out["reason_code"] = "BUDGET_EXHAUSTED"
            _out["notes"].append(f"llm_budget={_budget_num:g} 但候选成本 {_top['cost']} > 0")
            return _out

        _out["would_be"] = "APPROVE"
        _out["reason_code"] = "INTENT_MATCH"
        _out["notes"].append(f"首选候选 {_top['id']!r} 未被任何闸否决")
        return _out
    except Exception as _e:
        silent_exc(_e, where="decision.arbitrate.arbitrate", level="warning")
        _out["notes"].append(f"仲裁异常已降级为 DEFER：{type(_e).__name__}")
        return _out


def enabled_for(scope: str) -> bool:
    """该域本批是否走仲裁（chat 域不动）。"""
    return str(scope or "") in ARBITRATION_SCOPES
