# -*- coding: utf-8 -*-
"""nucleus/evolution/approval_bridge.py —— 439 刀1：审批桥（**只出建议，不改状态**）。

【为什么需要（T0 实测 2026-11-03）】
  · `PatchAutoApprover` 是个 28KB 的类，**生产里零实例化**（唯一命中是 CI 回放数据串）；
  · `PatchManager.approve_patch()` 存在，但**只接受下标 index**，且只有人工 CLI 会调；
  · 结果：`runtime_verified` 之后**没有任何东西把它变成 `approved`** ——
    「验证通过」到「可以应用」之间是一座断桥。438 批把 pending 从 9 清到 1 之后，
    剩下这 1 条同样卡在这里。

【分工（硬边界，单测守着）】
    PatchAutoApprover.classify()  → **建议**（问「这条该不该放行」）
    本模块 decision()             → **闸门复核**（八项硬条件逐条判，缺一不放过）
    本模块 record()               → **落盘**（data/patches/auto_approved.json，只写决策）
    PatchManager.approve_patch_by_id() → **唯一状态写口**（status 只能由它改）

★本模块**绝不** import 任何执行器、绝不写 pending、绝不改源码。
  它只返回 `{"approved": bool, "gates": {...}, "reason": str}`；
  要真改状态必须调用方显式拿 `PatchManager` 去写 —— 这样「桥」和「闸」不会互相掩护。

【八项硬条件（任务书原文逐条落地）】
    ① risk_level == 低
    ② trust_score >= M439_APPROVE_MIN_TRUST（缺省 60）
    ③ confidence == high（复用 PatchAutoApprover._confidence 的归一）
    ④ baseline_errors > 0（438 刀2/439 刀2 的基线；缺失或 0 一律不放行）
    ⑤ problem_gone is True（必须是显式布尔真，不从 status/理由串反推）
    ⑥ blast <= 1（改动波及文件数）
    ⑦ 非核心文件（nucleus/ · main.py · config.py 视为核心，一律人工）
    ⑧ 单文件（只允许一个 target file）
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

#: 核心代数：命中即人工（不自动）
CORE_PREFIXES = ("nucleus/", "nucleus\\", "main.py", "config.py", "utils/")

#: 决策落盘（相对仓根）
DECISION_REL = os.path.join("data", "patches", "auto_approved.json")

DEFAULT_MIN_TRUST = 60.0


def _cfg(name: str, default: Any) -> Any:
    try:
        _c = sys.modules.get("config")
        if _c is None:
            return default
        return getattr(_c, name, default)
    except Exception as _e:
        _log_debug("读 config.%s 失败: %s", name, _e)
        return default


def _log_debug(msg: str, *args: Any) -> None:
    import logging
    logging.getLogger("pulse.module.approval_bridge").debug(msg, *args)


def enabled() -> bool:
    """`ENABLE_M439_APPROVAL_BRIDGE`（缺省 True）。关 ⇒ 只记录不调用写口。"""
    return bool(_cfg("ENABLE_M439_APPROVAL_BRIDGE", True))


def min_trust() -> float:
    try:
        return float(_cfg("M439_APPROVE_MIN_TRUST", DEFAULT_MIN_TRUST))
    except (TypeError, ValueError) as _e:
        _log_debug("min_trust 取值非法，回落 60: %s", _e)
        return DEFAULT_MIN_TRUST


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError) as _e:
        _log_debug("_num 取值非法(%r): %s", v, _e)
        return None


def _confidence(patch: dict) -> str:
    """与 `PatchAutoApprover._confidence` 同源（同一份 token 归一），不另立口径。"""
    try:
        from nucleus.evolution.PatchAutoApprover import PatchAutoApprover
        return PatchAutoApprover._confidence(patch)
    except Exception as _e:
        _log_debug("取 confidence 失败: %s", _e)
        return str(patch.get("confidence") or "").strip().lower() or "unknown"


def target_files(patch: dict) -> list[str]:
    """补丁波及的文件清单（`files` 优先，其次单 `file`）。"""
    _files = patch.get("files")
    if isinstance(_files, (list, tuple, set)):
        _out = [str(x) for x in _files if str(x).strip()]
        if _out:
            return _out
    _one = str(patch.get("file") or "").strip()
    return [_one] if _one else []


def decision(patch: dict) -> dict:
    """八项硬条件逐条判。**纯函数**：不写盘、不改 patch、不碰执行器。

    Returns:
        ``{"approved": bool, "gates": {名字: {"ok": bool, "why": str}}, "reason": str}``
    """
    _g: dict[str, dict] = {}

    def _put(name: str, ok: bool, why: str) -> None:
        _g[name] = {"ok": bool(ok), "why": why}

    _risk = str(patch.get("risk_level") or "").strip()
    _put("risk_low", _risk in ("低", "low"), f"risk_level={_risk or '空'}")

    _trust = _num(patch.get("trust_score"))
    _put("trust", _trust is not None and _trust >= min_trust(),
         f"trust={patch.get('trust_score')!r} 门槛={min_trust():g}")

    _conf = _confidence(patch)
    _put("confidence_high", _conf == "high", f"confidence={_conf}")

    _base = _num(patch.get("baseline_errors"))
    _put("baseline_positive", _base is not None and _base > 0,
         f"baseline_errors={patch.get('baseline_errors')!r}（缺失/0 一律不放行）")

    _gone = patch.get("problem_gone")
    _put("problem_gone_true", _gone is True, f"problem_gone={_gone!r}（只认显式 True）")

    _files = target_files(patch)
    _blast = len(_files)
    _put("blast_le_1", _blast <= 1, f"blast={_blast} 文件={_files}")

    _core = [f for f in _files if any(f.replace("\\", "/").startswith(p)
                                      for p in CORE_PREFIXES)]
    _put("not_core", not _core, f"核心文件={_core or '无'}")

    _put("single_file", _blast == 1, f"文件数={_blast}")

    _bad = [k for k, v in _g.items() if not v["ok"]]
    return {
        "approved": not _bad,
        "gates": _g,
        "reason": "全部通过" if not _bad else "未通过: " + ", ".join(_bad),
    }


def record(decision_result: dict, patch: dict, root: str | None = None,
           by: str = "auto:bridge") -> dict:
    """把决策追加到 `data/patches/auto_approved.json`（只记录，不改任何状态）。"""
    _root = root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    _path = os.path.join(_root, DECISION_REL)
    _row = {
        "at": time.time(),
        "at_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "by": by,
        "patch_id": str(patch.get("id") or ""),
        "file": patch.get("file") or "",
        "method": patch.get("method") or "",
        "approved": bool(decision_result.get("approved")),
        "reason": decision_result.get("reason") or "",
        "gates": {k: v["ok"] for k, v in (decision_result.get("gates") or {}).items()},
        "gate_why": {k: v["why"] for k, v in (decision_result.get("gates") or {}).items()},
        "event_verified": bool(patch.get("verified")),
        "ran_at": time.time(),
    }
    try:
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        _doc = []
        if os.path.isfile(_path):
            try:
                with open(_path, encoding="utf-8") as _f:
                    _doc = json.load(_f)
                if not isinstance(_doc, list):
                    _doc = []
            except (OSError, ValueError) as _e:
                _log_debug("读旧决策文件失败（覆盖重写）: %s", _e)
                _doc = []
        _doc.append(_row)
        with open(_path, "w", encoding="utf-8") as _f:
            json.dump(_doc, _f, ensure_ascii=False, indent=1)
        return {"ok": True, "path": _path, "rows": len(_doc)}
    except OSError as _e:
        _log_debug("写决策文件失败: %s", _e)
        return {"ok": False, "why": f"{type(_e).__name__}: {_e}", "path": _path}


def maybe_approve(patch: dict, patch_manager: Any, *, by: str = "auto:bridge",
                  root: str | None = None) -> dict:
    """完整一键：判 → 记录 → （全过才）调用 PatchManager 的**唯一状态写口**。

    Args:
        patch: 待审批补丁字典（必须已带 baseline_errors / problem_gone 等闸门字段）。
        patch_manager: 提供 `approve_patch_by_id` 的 PatchManager 实例。

    Returns:
        ``{"approved","reason","recorded","status_written","write_result"}``
    """
    _d = decision(patch)
    _rec = record(_d, patch, root=root, by=by)
    _out = {"approved": _d["approved"], "reason": _d["reason"],
            "gates": _d["gates"], "recorded": _rec}

    if not _d["approved"]:
        _out["status_written"] = False
        return _out

    if not enabled():
        _out["status_written"] = False
        _out["write_result"] = {"ok": False, "reason": "ENABLE_M439_APPROVAL_BRIDGE=False"}
        return _out

    _writer = getattr(patch_manager, "approve_patch_by_id", None)
    if not callable(_writer):
        _out["status_written"] = False
        _out["write_result"] = {"ok": False, "reason": "PatchManager 无 approve_patch_by_id"}
        return _out

    _wr = _writer(str(patch.get("id") or ""), by=by,
                  reason=_d["reason"],
                  judged_by="m439_approval_bridge")
    _out["status_written"] = bool(_wr.get("ok"))
    _out["write_result"] = _wr
    return _out
