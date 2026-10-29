# -*- coding: utf-8 -*-
"""385 刀2：DL3 决策层契约（`DecisionRequest` / `DecideResult`）。

字段严格按 `docs/洞鉴与星轨对话/分析报告/洞鉴_385前置_内在模型方向审查_20261028.md`
§6.2 / §6.3 抄录，**一个不多一个不少**。改动字段请同步该文档。

## 命名

`DL3` / `dec_` 前缀是刻意的：InfoField 已有一个 **L3 脉冲层**
（`nucleus/field/InfoField.py` 的 L0–L3 队列），此 L3 是**决策层**。
撞名会让日志与代码检索持续互误（洞鉴 385 第 6 条明写）。

## 本批只到 Step 1（影子账本）

`ENABLE_DEC_SHADOW` 默认关；开了也只**写账本**，不产出可执行指令
（所有结果 `shadow=True`，执行器必须忽略）。
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

#: 决策域（洞鉴 §6.2 `scope` 闭集）
SCOPES = ("chat", "patch", "resource", "schedule")

#: 动作闭集（洞鉴 §6.3 `action`）
ACTIONS = ("EXECUTE", "DEFER", "REJECT", "ESCALATE", "DEGRADE")

#: 原因码（洞鉴 §6.3 给了 7 个 + 「等」；`SHADOW_ONLY` 是本批新增：
#: 影子模式没有真裁决，需要一个**诚实**的原因码，不能借用别的原因）
REASON_CODES = (
    "INTENT_MATCH", "RESOURCE_PRESSURE", "BUDGET_EXHAUSTED", "POLICY_BLOCK",
    "EVIDENCE_MISSING", "STALE_INPUT", "USER_OVERRIDE",
    "SHADOW_ONLY",
)

#: 降级策略（洞鉴 §6.3）
DEGRADE_POLICIES = ("NONE", "LOCAL_ONLY", "DEFER_ONE_TICK", "DEFER_UNTIL")

#: 策略集版本（不同版本结果不可混统 —— 洞鉴 §6.2）
POLICY_VERSION = "dec.v1"

#: 契约版本（结构自身的版本，与 policy_version 分离）
CONTRACT_SCHEMA = "dec_contract/385.v1"


def _sha8(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:8]


def make_request_id(scope: str, *parts) -> str:
    """幂等键：同域同输入 ⇒ 同 id（洞鉴 §6.2「同请求重放不产生第二次决策」）。"""
    return "req_%s_%s" % (scope, _sha8("|".join(str(p) for p in parts)))


def make_decision_id(scope: str, ts: float, request_id: str) -> str:
    """`dec_<scope>_<ts>_<hash8>`（洞鉴 §6.3 规定格式）。"""
    return "dec_%s_%d_%s" % (scope, int(float(ts)), _sha8(request_id))


@dataclass
class DecisionRequest:
    """决策输入（洞鉴 §6.2，8 必填 + 4 可选信号）。

    `signals` 保持**嵌套**结构（`{"l2": {...}, "l4": {...}, "resource": {...}, "pool": {...}}`），
    因为洞鉴原文就是 `signals.l2` 这种点号写法，压平成 `signals_l2` 会让
    「哪些是信号」这件事在 JSON 里看不出来。
    """

    request_id: str
    ts: float
    proc_gen: str
    scope: str
    candidates: list
    constraints: dict
    policy_version: str = POLICY_VERSION
    epoch: str = ""
    signals: dict = field(default_factory=dict)

    def validate(self) -> list:
        """返回问题清单（空 = 合规）。**不抛异常**，交给调用方决定怎么处理。"""
        bad = []
        if not str(self.request_id or "").strip():
            bad.append("request_id_empty")
        if not isinstance(self.ts, (int, float)) or self.ts <= 0:
            bad.append("ts_invalid")
        if not str(self.proc_gen or "").strip():
            bad.append("proc_gen_empty")
        if str(self.scope or "") not in SCOPES:
            bad.append("scope_invalid:%r" % (self.scope,))
        if not isinstance(self.candidates, list) or not self.candidates:
            bad.append("candidates_empty")
        if not isinstance(self.constraints, dict):
            bad.append("constraints_not_dict")
        if str(self.policy_version or "") != POLICY_VERSION:
            bad.append("policy_version_invalid")
        return bad

    def to_dict(self) -> dict:
        return {
            "schema": CONTRACT_SCHEMA,
            "request_id": self.request_id,
            "ts": float(self.ts),
            "proc_gen": self.proc_gen,
            "scope": self.scope,
            "candidates": list(self.candidates or []),
            "signals": dict(self.signals or {}),
            "constraints": dict(self.constraints or {}),
            "policy_version": self.policy_version,
            "epoch": self.epoch,
        }

    def digest(self) -> str:
        """输入摘要（账本里与结果同行存，用于「同输入是否复现同结果」的对账）。"""
        _parts = [self.scope, self.proc_gen, str(self.ts),
                  repr(sorted((c.get("id") for c in self.candidates
                               if isinstance(c, dict)) if self.candidates else []))]
        return _sha8("|".join(_parts))


@dataclass
class DecideResult:
    """决策输出（洞鉴 §6.3，唯一结果对象）。

    ★`evidence` **不允许为空**（洞鉴原文）—— 没有证据的"决定"是猜。
    ★`EXECUTE` 时 `target` 与 `rollback` 必填（同上）。
    """

    decision_id: str
    request_id: str
    action: str
    target: str
    priority: int
    budget: dict
    expires_at: float
    evidence: list
    reason_code: str
    degrade_policy: str
    rollback: dict
    policy_version: str = POLICY_VERSION
    shadow: bool = True
    #: ★397刀5：仲裁结果（`arbitrate.arbitrate()` 的产物）。
    #: 过渡期为**唯一**承载"真裁决"的字段 —— `action` 仍然是 `DEFER`。
    #: 影子期/未走仲裁的域为 `{}`（不是 None）：空字典表示"本条没有仲裁结论"，
    #: 与"仲裁了但结论是 DEFER"用 `would_be` 字段区分（铁律 276：没采到 ≠ 结论为 0）。
    arbitration: dict = field(default_factory=dict)

    def validate(self) -> list:
        bad = []
        if not str(self.decision_id or "").startswith("dec_"):
            bad.append("decision_id_bad_prefix")
        if not str(self.request_id or "").strip():
            bad.append("request_id_empty")
        if str(self.action or "") not in ACTIONS:
            bad.append("action_invalid:%r" % (self.action,))
        if not isinstance(self.evidence, list) or not self.evidence:
            bad.append("evidence_empty")
        if str(self.reason_code or "") not in REASON_CODES:
            bad.append("reason_code_invalid:%r" % (self.reason_code,))
        if str(self.degrade_policy or "") not in DEGRADE_POLICIES:
            bad.append("degrade_policy_invalid:%r" % (self.degrade_policy,))
        if not isinstance(self.priority, int) or not 0 <= self.priority <= 100:
            bad.append("priority_out_of_range:%r" % (self.priority,))
        if self.action in ("EXECUTE", "REJECT") and not str(self.target or "").strip():
            bad.append("target_required_for_%s" % self.action)
        if self.action == "EXECUTE" and not (self.rollback or {}):
            bad.append("rollback_required_for_EXECUTE")
        return bad

    def to_dict(self) -> dict:
        return {
            "schema": CONTRACT_SCHEMA,
            "decision_id": self.decision_id,
            "request_id": self.request_id,
            "action": self.action,
            "target": self.target,
            "priority": int(self.priority),
            "budget": dict(self.budget or {}),
            "expires_at": float(self.expires_at),
            "evidence": list(self.evidence or []),
            "reason_code": self.reason_code,
            "degrade_policy": self.degrade_policy,
            "rollback": dict(self.rollback or {}),
            "policy_version": self.policy_version,
            "shadow": bool(self.shadow),
            "arbitration": dict(self.arbitration or {}),
        }


def shadow_result(request: DecisionRequest, *, action: str = "DEFER",
                  target: str = "", priority: int = 0,
                  evidence: list | None = None,
                  reason_code: str = "SHADOW_ONLY",
                  degrade_policy: str = "NONE",
                  rollback: dict | None = None,
                  budget: dict | None = None,
                  ttl_sec: float = 60.0,
                  arbitration: dict | None = None) -> DecideResult:
    """按「只写不判」造一条影子结果。

    ★为什么 action 默认 `DEFER`：影子期不该出现 `EXECUTE` ——
      那会让账本看起来像"L3 已经在下指令"，而执行器又必须忽略它。
      真要记录"既有链路执行了"，由调用方显式传 `action="EXECUTE"` 并给 rollback。
    """
    _ts = float(request.ts or time.time())
    return DecideResult(
        decision_id=make_decision_id(request.scope, _ts, request.request_id),
        request_id=request.request_id,
        action=action,
        target=str(target or ""),
        priority=int(priority),
        budget=dict(budget or {"llm_calls": 0, "time_ms": 0, "max_bytes": 0}),
        expires_at=_ts + float(ttl_sec),
        evidence=list(evidence or []),
        reason_code=reason_code,
        degrade_policy=degrade_policy,
        rollback=dict(rollback or {}),
        policy_version=request.policy_version,
        shadow=True,
        arbitration=dict(arbitration or {}),
    )
