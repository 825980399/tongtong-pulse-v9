# -*- coding: utf-8 -*-
"""
offline_survival_protocol.py —— 断网生存协议（164批 刀A3 · T-断网生存协议-1）

设计背景：
    外脑（LLM / 肺模型）超时或不可达时，曈曈不应降级为「模板应答」或静默委托，
    而应完整走本地推理链 Symbolic → Causal → Analogy，给出本地推演结果，
    并统一标记 `[本地推理·待验证]`，待联网后由外脑批量复核沉淀。

设计约束（164批纪律）：
    - 不新增任何异常捕获块（异常上抛给调用方既有边界，cw2 零新增静默 handler）。
    - 不触碰 config.py（开关一律模块级常量）。
    - 纯函数式，无框架依赖，可独立 import 与隔离测试。

本地链顺序与 nucleus/reasoning/StrategySelector._LOCAL_FALLBACK_CHAIN 前端一致：
    ("symbolic", "causal", "analogy")，最后才是 "llm"（外脑）。

联网复核：
    离线产出的结果全部进入进程内复核缓冲 `_PENDING_RECHECK`；联网恢复后，
    由复核任务逐条调用外脑验证并沉淀，缓冲可经 drain_recheck() 清空。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from nucleus.reasoning.SymbolicReasoner import SymbolicReasoner
from nucleus.reasoning.CausalInferrer import get_causal_inferrer
from nucleus.reasoning.analogy_engine import AnalogyEngine


# ★模块级开关（不触碰 config.py）：False → 完全退化为原有委派行为。
OFFLINE_SURVIVAL_ENABLED: bool = True

# 离线推理结果统一标记（落点要求：结果标 `[本地推理·待验证]`）
OFFLINE_TAG: str = "[本地推理·待验证]"

# 本地推理链顺序（与 StrategySelector._LOCAL_FALLBACK_CHAIN 前端一致）
LOCAL_CHAIN: tuple[str, ...] = ("symbolic", "causal", "analogy")

# 类比跨域迁移判定置信度下限（低于此不采纳迁移假设）
ANALOGY_ADOPT_CONFIDENCE: float = 0.5


@dataclass
class OfflineResult:
    """断网生存协议的一次本地推理结果。"""

    answer: str | None            # 推演答案（可能为 None，表示本地也未解出）
    solved: bool                  # 本地链是否真正解出
    confidence: float             # 置信度
    method: str                   # 采用的本地方法：symbolic / causal / analogy / symbolic_partial / none
    verified: bool = False        # 离线结果恒为 False，须待联网外脑复核
    tag: str = OFFLINE_TAG        # 统一标记
    note: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def display(self) -> str | None:
        """带标记的可展示答案；answer 为空时返回 None（调用方据此决定是否委派外脑）。"""
        if not self.answer:
            return None
        return f"{self.answer} {self.tag}"


# ---- 联网后外脑复核缓冲（进程内） ----
_PENDING_RECHECK: list[dict[str, Any]] = []


def enqueue_recheck(record: dict[str, Any]) -> None:
    """将一条离线结果登记进复核缓冲，待联网后外脑批量复核沉淀。"""
    _PENDING_RECHECK.append(record)


def drain_recheck() -> list[dict[str, Any]]:
    """取出并清空全部待复核记录（联网恢复后的复核任务消费）。"""
    _out = list(_PENDING_RECHECK)
    _PENDING_RECHECK.clear()
    return _out


def pending_count() -> int:
    """当前待复核记录数。"""
    return len(_PENDING_RECHECK)


def run_offline_chain(
    question: str,
    semantic_hints: dict[str, Any] | None = None,
    *,
    causal_pairs: list[dict[str, Any]] | None = None,
    node_pool: Any = None,
    analogy_texts: tuple[str, str] | None = None,
) -> OfflineResult:
    """完整走本地推理链 Symbolic → Causal → Analogy，产出离线结果。

    说明（与落点一致）：
        - symbolic：对任意问题可用（SymbolicReasoner.reason），解出即用。
        - causal：需结构化因果对 causal_pairs + node_pool，由调用方在具备时传入。
        - analogy：需一对文本 analogy_texts（已知范例 + 当前问题），由调用方传入。
        对于「纯问题」场景，symbolic 是唯一可独立触发的本地腿；causal/analogy
        作为结构化增强腿存在，确保协议对三类本地推理能力「全通」而非模板降级。

    异常策略：本函数不捕获异常，由调用方既有边界处理。
    """
    # ---- 腿1：符号推理（对任意问题可用） ----
    _sym = SymbolicReasoner().reason(question, semantic_hints)
    if _sym.solved and _sym.answer:
        return OfflineResult(
            answer=_sym.answer,
            solved=True,
            confidence=_sym.confidence,
            method="symbolic",
            note="本地符号推理解出",
            evidence={"task_type": _sym.task_type, "steps": len(_sym.steps)},
        )

    _evidence = {"task_type": _sym.task_type, "symbolic_solved": _sym.solved}

    # ---- 腿2：因果校验（需结构化因果对） ----
    if causal_pairs is not None and node_pool is not None:
        _cres = get_causal_inferrer().verify_chain(causal_pairs, node_pool)
        if _cres and _cres.get("verified"):
            _ans = _cres.get("answer") or _cres.get("conclusion") or str(_cres)
            return OfflineResult(
                answer=str(_ans),
                solved=True,
                confidence=float(_cres.get("confidence", 0.0)),
                method="causal",
                note="本地因果链校验通过",
                evidence={"task_type": _sym.task_type, "causal": _cres},
            )
        _evidence["causal_verified"] = bool(_cres and _cres.get("verified"))

    # ---- 腿3：跨域类比迁移（需一对文本） ----
    if analogy_texts is not None and len(analogy_texts) == 2:
        _ares = AnalogyEngine().compare(analogy_texts[0], analogy_texts[1])
        if _ares.is_cross_domain and _ares.migrated_hypothesis \
                and _ares.confidence >= ANALOGY_ADOPT_CONFIDENCE:
            return OfflineResult(
                answer=_ares.migrated_hypothesis,
                solved=True,
                confidence=_ares.confidence,
                method="analogy",
                note="跨域类比迁移假设",
                evidence={"similarity": _ares.similarity,
                          "shared_structure": _ares.shared_structure},
            )
        _evidence["analogy_cross_domain"] = _ares.is_cross_domain

    # ---- 腿全未解出：返回「诚实未解出」的部分结果（非模板降级） ----
    # 仍带本地尝试证据与标记，交由外脑联网后复核，绝不返回空模板应答。
    return OfflineResult(
        answer=_sym.answer,          # 可能为 None（本地也未解出）
        solved=_sym.solved,
        confidence=_sym.confidence,
        method="symbolic_partial" if not _sym.solved else "symbolic",
        note="本地链未能完全解出，待联网外脑复核",
        evidence=_evidence,
    )


def survive_offline(
    question: str,
    llm_reply: Any,
    semantic_hints: dict[str, Any] | None = None,
    *,
    causal_pairs: list[dict[str, Any]] | None = None,
    node_pool: Any = None,
    analogy_texts: tuple[str, str] | None = None,
) -> tuple[bool, OfflineResult | None]:
    """断网生存协议对外统一入口。

    Args:
        question:    原始问题。
        llm_reply:   外脑（LLM）回复。为空（None / 空串 / 纯空白）即视为外脑不可达。
        semantic_hints / causal_pairs / node_pool / analogy_texts：透传给本地链。

    Returns:
        (used_offline, result)：
            - used_offline=True  → 已走本地链，result 为 OfflineResult（可能 answer=None）。
            - used_offline=False → 外脑在线，result=None（调用方应保持原在线路径不变）。
    """
    if not OFFLINE_SURVIVAL_ENABLED:
        return (False, None)
    # 外脑在线（有实质回复）→ 不改写任何行为，直接放行。
    if llm_reply and str(llm_reply).strip():
        return (False, None)
    # 外脑不可达 → 走本地链。
    _result = run_offline_chain(
        question, semantic_hints,
        causal_pairs=causal_pairs, node_pool=node_pool, analogy_texts=analogy_texts,
    )
    return (True, _result)
