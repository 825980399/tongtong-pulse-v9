# -*- coding: utf-8 -*-
"""★492-6：假设**生成台账**（append-only，纯观测）。

诚实边界：现有代码里**没有**「假设 → 后续被证据支持」的判定点
（409 批 hypothesis_reconciler 判的是经验策略，不认本批的 claim/evidence_ids）。
所以本模块只落**生成侧**（分母），`supported` 一律留 ``None`` 作为 493 批
回写接口，**绝不**写 false/0，也**不产出 hit_rate**。

纪律：开关关时首行返回，不建目录、不写文件、不 import 重依赖。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any

from nucleus._silent_except import silent_exc

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_REL = os.path.join("data", "evolution", "hypothesis_hits.jsonl")


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception as _e:
        silent_exc(_e, where="m492_hypothesis_log::_cfg", level="debug")
        return default


def on() -> bool:
    """总开关（出厂默认开，fail-closed）。"""
    return bool(_cfg("ENABLE_M492_HYPOTHESIS_LOG", False))


def path() -> str:
    _p = str(_cfg("M492_HYPOTHESIS_LOG_PATH", "") or "")
    return _p or os.path.join(_ROOT, _DEFAULT_REL)


def hyp_key(claim: str, confidence: Any) -> str:
    """无唯一 id ⇒ 用内容 hash 做关联键（供后续按 key 回写命中结论）。"""
    _raw = "%s|%s" % (str(claim or ""), str(confidence or ""))
    return hashlib.sha256(_raw.encode("utf-8")).hexdigest()[:16]


def record(hypotheses, *, question: str = "", correlation_id: str = "",
           intent_type: str = "", origin: str = "push_pending") -> list[dict]:
    """落一批假设（每假设一行）。返回实际写入的行（供单测/自检）。

    默认生产路径在 pytest 下拒写；路径可由 config 注入供测试重定向。
    """
    if not on():
        return []
    _rows = [h for h in list(hypotheses or []) if isinstance(h, dict)]
    if not _rows:
        return []
    _p = path()
    _default = os.path.join(_ROOT, _DEFAULT_REL)
    try:
        if "pytest" in sys.modules and os.path.abspath(_p) == os.path.abspath(_default):
            return []
    except Exception as _e:
        silent_exc(_e, where="m492_hypothesis_log::record guard", level="debug")
        return []
    _out = []
    _now = time.time()
    for _h in _rows:
        _claim = str(_h.get("claim") or "").strip()[:200]
        _conf = _h.get("confidence")
        _out.append({
            "schema": "hypothesis_hits/492.v1",
            "ts": round(_now, 3),
            "hyp_key": hyp_key(_claim, _conf),
            "question": str(question or "")[:200],
            "correlation_id": str(correlation_id or ""),
            "intent_type": str(intent_type or ""),
            "origin": str(origin),
            "claim": _claim,
            "confidence": _conf,
            "evidence_ids": [str(x) for x in (_h.get("evidence_ids") or [])][:4],
            "assumptions": [str(x)[:80] for x in (_h.get("assumptions") or [])][:3],
            "test": str(_h.get("test") or "")[:120],
            # ★命中侧接口占位，不是判定结果：
            "supported": None,
            "support_evidence": None,
            "resolved_at": None,
        })
    try:
        _dir = os.path.dirname(_p)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        with open(_p, "a", encoding="utf-8", newline="\n") as _f:
            for _row in _out:
                _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
        return _out
    except Exception as _e:
        silent_exc(_e, where="m492_hypothesis_log::record write", level="debug")
        return []
