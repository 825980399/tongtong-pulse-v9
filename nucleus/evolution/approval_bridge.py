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

from nucleus._silent_except import silent_exc  # ★460 批：自裁决接线的静默 except 记账

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


# ==================== ★447刀1：接进生产的「安全边界 + 全留痕」外层 ====================
#
# 【为什么要有这一层，而不是直接调 maybe_approve】
# 439 的 `maybe_approve()` 只回答「八闸过不过」，它不知道生产侧答应过什么。
# 447 的接线边界（小林拍板）是四条**额外**约束，八闸里一条都没有：
#   ① 只对 risk=低（八闸①）**且** 非核心（八闸⑦）**且** 单文件（八闸⑧）**且** blast≤1（八闸⑥）开
#      —— 这四条与八闸重合，但**必须单独再判一次**：八闸是"能不能放行"，
#      这里是"本批接线敢不敢自动放行"，两者日后会各自演化，不能共用一个布尔。
#   ② 每天最多自动批准 1 条（八闸里没有频次概念）
#   ③ 失败自动回滚（由 PatchManager 既有的 rollback/applied 机制负责，本层只记）
#   ④ 所有决策留痕（含被边界挡下的那些 —— 它们才是"为什么今天没批准"的答案）
#
# ★本层**不改任何闸判据**：`decision()` 一个字没动；放行仍由
#   `PatchManager.approve_patch_by_id()` 这个唯一写口执行。
# ★本层**不 import 执行器**，不写 pending、不改源码 —— 与模块原有的硬边界一致。

#: 审批留痕（相对仓根）
APPROVAL_TRACE_REL = os.path.join("data", "patches", "approval_trace.jsonl")


def approval_trace_path(root: str | None = None) -> str:
    """审批留痕文件绝对路径。`root` 供隔离验证注入；生产不传。"""
    _root = root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(_root, APPROVAL_TRACE_REL)


def daily_cap() -> int:
    """每天最多自动批准几条（`M447_APPROVE_DAILY_CAP`，缺省 1；<=0 视为关闭自动批准）。"""
    try:
        return int(_cfg("M447_APPROVE_DAILY_CAP", 1))
    except (TypeError, ValueError) as _e:
        _log_debug("M447_APPROVE_DAILY_CAP 取值非法，回落 1: %s", _e)
        return 1


def scope_ok(patch: dict) -> tuple[bool, str]:
    """447 安全边界：`risk=低 + 非核心 + 单文件 + blast<=1`。

    Returns:
        ``(ok, why)`` —— `why` 是给人看的判据串（会原样进留痕）。
    """
    _risk = str((patch or {}).get("risk_level") or "").strip()
    _files = target_files(patch or {})
    _core = [f for f in _files if any(f.replace("\\", "/").startswith(p)
                                      for p in CORE_PREFIXES)]
    _why = []
    if _risk not in ("低", "low"):
        _why.append(f"risk={_risk!r}(需低)")
    if _core:
        _why.append(f"核心文件={','.join(_core)}")
    if len(_files) != 1:
        _why.append(f"文件数={len(_files)}(需1)")
    if len(_files) > 1:
        _why.append(f"blast={len(_files)}(需<=1)")
    return (not _why), ("; ".join(_why) if _why else "边界通过")


def approved_today(root: str | None = None, *, now: float | None = None) -> int:
    """今天（本地日）已经自动批准了多少条 —— 数**留痕**，不数别的账本。

    为什么数留痕而不是 `auto_approved.json`：那份账本只记决策，
    而本层要数的是"本层放行了几条"，两者在人工批准混入后会分叉。
    """
    _p = approval_trace_path(root)
    _now = float(now if now is not None else time.time())
    _day = time.strftime("%Y-%m-%d", time.localtime(_now))
    _n = 0
    try:
        if not os.path.isfile(_p):
            return 0
        with open(_p, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _row = json.loads(_line)
                except ValueError as _e:
                    _log_debug("审批留痕坏行（跳过，不影响计数）: %s", _e)
                    continue
                if str(_row.get("day") or "") == _day and _row.get("approved") is True:
                    _n += 1
    except OSError as _e:
        _log_debug("读审批留痕失败（按 0 计，不阻断）: %s", _e)
        return 0
    return _n


def note_approval_trace(patch: dict, decision_result: dict, *, approved: bool,
                        hit: str = "", scope: str = "", blocked: str = "",
                        patch_manager: Any = None, root: str | None = None,
                        now: float | None = None) -> bool:
    """把**一次审批判定**追加到 `approval_trace.jsonl`。失败绝不影响主流程。

    任务书硬性要求：补丁 id、八闸每闸的判定结果、最终决策、决策理由、
    **修改前/修改后完整内容**。

    Args:
        decision_result: ``decision()`` 的返回（含 `gates` / `reason`）。
        approved: 最终是否真的放了行（边界挡下时为 False，即使八闸全过）。
        hit: 边界/频次挡下的原因（空串表示没被挡）。
        scope: `scope_ok()` 的判据串。
        blocked: 机器可读的挡下分类：``scope`` / ``daily_cap`` / ``gates`` / ``""``。
    """
    try:
        _p = approval_trace_path(root)
        # ★测试进程 + 默认生产路径 ⇒ 不写（与 446 两个留痕口同款护栏）。
        #   `root` 显式传入（单测都传 tmp）⇒ 照常写。
        if root is None and "pytest" in sys.modules:
            _def = approval_trace_path()
            if _p == _def:
                return False
        _dir = os.path.dirname(_p)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        _now = float(now if now is not None else time.time())
        _gates = (decision_result or {}).get("gates") or {}
        _patch = patch or {}
        _row = {
            "ts": _now,
            "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_now)),
            "day": time.strftime("%Y-%m-%d", time.localtime(_now)),
            "kind": "approval",
            "patch_id": str(_patch.get("id") or "")[:64],
            "file": str(_patch.get("file") or ""),
            "method": str(_patch.get("method") or ""),
            "issue_type": str(_patch.get("issue_type") or ""),
            "risk_level": str(_patch.get("risk_level") or ""),
            "trust_score": _patch.get("trust_score"),
            # 八闸逐条（ok + 判据串）—— 少记一闸，事后就说不清"它为什么没过"
            "gates": {k: {"ok": bool(v.get("ok")), "why": str(v.get("why") or "")}
                      for k, v in _gates.items()},
            "gates_total": len(_gates),
            "gates_passed": sum(1 for v in _gates.values() if v.get("ok")),
            "decision_reason": str((decision_result or {}).get("reason") or "")[:300],
            "approved": bool(approved),
            "blocked": str(blocked or ""),
            "blocked_why": str(hit or "")[:300],
            "scope": str(scope or "")[:300],
            "baseline_errors": _patch.get("baseline_errors"),
            "baseline_source": _patch.get("baseline_source"),
            "problem_gone": _patch.get("problem_gone"),
            # ★留痕铁律：修改前/修改后**完整内容**（不截断）
            "original_code": _patch.get("original_code") or "",
            "modified_code": _patch.get("modified_code") or "",
            "original_len": len(str(_patch.get("original_code") or "")),
            "modified_len": len(str(_patch.get("modified_code") or "")),
        }
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
        return True
    except Exception as _e:
        _log_debug("审批留痕写入失败（不影响判定）: %s: %s", type(_e).__name__, _e)
        return False


def maybe_approve_scoped(patch: dict, patch_manager: Any, *,
                         root: str | None = None,
                         by: str = "auto:bridge_447",
                         now: float | None = None) -> dict:
    """★447刀1：生产用入口 —— 先过安全边界与日限额，再走八闸，全程留痕。

    Returns:
        ``{"approved", "reason", "gates", "blocked", "daily_used", "trace"}``
    """
    _out: dict = {"approved": False, "reason": "", "gates": {},
                  "blocked": "", "daily_used": 0, "trace": False}
    if not isinstance(patch, dict) or not patch:
        _out["reason"] = "patch 为空"
        _out["blocked"] = "scope"
        return _out

    _scope_ok, _scope_why = scope_ok(patch)
    _out["scope"] = _scope_why
    if not _scope_ok:
        _out["blocked"] = "scope"
        _out["reason"] = "安全边界拦下: " + _scope_why
        _out["trace"] = note_approval_trace(
            patch, {"gates": {}, "reason": _out["reason"]}, approved=False,
            hit=_scope_why, scope=_scope_why, blocked="scope", root=root, now=now)
        _log_debug("[447刀1] 边界拦下 %s: %s", str(patch.get("id") or "")[:16], _scope_why)
        return _out

    _cap = daily_cap()
    _used = approved_today(root, now=now)
    _out["daily_used"] = _used
    if _cap <= 0 or _used >= _cap:
        _out["blocked"] = "daily_cap"
        _out["reason"] = f"日限额已用尽（今日已批准 {_used} / 上限 {_cap}）"
        _out["trace"] = note_approval_trace(
            patch, {"gates": {}, "reason": _out["reason"]}, approved=False,
            hit=_out["reason"], scope=_scope_why, blocked="daily_cap",
            root=root, now=now)
        return _out

    # ★460 批：自裁决闭环（星轨对话裁决「让曈曈自己裁决」）。
    #   审批前统一调用既有静态复现探针，回填八闸一直拿不到的证据字段。
    #   ★三道降级：总闸关 / 准入未过 / 依赖异常 ⇒ 一律用**原 patch**，
    #     逐字节回到 460 前行为（可用 ENABLE_M460_SELF_VERDICT=False 做 A/B）。
    #   ★本层不新增写口、不改 decision() 判据、不改原 patch 对象。
    _m460_prep: dict = {}
    _m460_patch = patch
    try:
        from nucleus.evolution.self_verdict import prepare as _m460_prepare
        _m460_prep = _m460_prepare(patch, root=root, now=now)
        if _m460_prep.get("admitted") and isinstance(_m460_prep.get("patch"), dict):
            _m460_patch = _m460_prep["patch"]
    except Exception as _e:
        silent_exc(_e, where="approval_bridge::460 prepare", level="debug")
        _m460_prep = {}
        _m460_patch = patch

    _r = maybe_approve(_m460_patch, patch_manager, by=by, root=root)
    _out["reason"] = _r.get("reason", "")
    _out["gates"] = _r.get("gates", {})
    _out["approved"] = bool(_r.get("approved")) and bool(_r.get("status_written"))
    if _r.get("approved") and not _r.get("status_written"):
        _out["blocked"] = "status_write"
        _out["reason"] = (f"{_out['reason']}（状态未写成: "
                          f"{(_r.get('write_result') or {}).get('reason', '')}）")
    elif not _r.get("approved"):
        _out["blocked"] = "gates"
    _out["trace"] = note_approval_trace(
        patch, _r, approved=_out["approved"], hit="" if _out["approved"] else _out["reason"],
        scope=_scope_why, blocked=_out["blocked"], root=root, now=now)

    # ★460 批 finalize：应用后验证；验证失败 ⇒ **真的**按字节快照回退，
    #   并把结构化失败原因写进既有 patch_failure_experience（下次生成 prompt 会带提醒）。
    #   ★留痕/回退绝不改变审批返回值（日志不决定返回值，铁律40）。
    if _m460_prep:
        try:
            from nucleus.evolution.self_verdict import finalize as _m460_finalize
            _m460_fin = _m460_finalize(_m460_prep, _out, patch_manager,
                                       root=root, now=now)
            _out["self_verdict"] = {
                "verdict": _m460_fin.get("verdict"),
                "verified": _m460_fin.get("verified"),
                "rolled_back": _m460_fin.get("rolled_back"),
                "why": str(_m460_fin.get("why") or "")[:120],
            }
        except Exception as _e:
            silent_exc(_e, where="approval_bridge::460 finalize", level="debug")
    return _out
