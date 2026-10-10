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

from datetime import datetime as _dt
from typing import Any

import config as _cfg


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
