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
"""
from __future__ import annotations

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
            return {
                "state": "DETERMINISTIC",
                "matched_rule_id": _rid,
                "llm_used": False,
                "action": "use_cached_rule",
                "detail": "rule hit, no LLM call",
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
