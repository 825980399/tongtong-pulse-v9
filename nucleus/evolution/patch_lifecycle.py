# -*- coding: utf-8 -*-
"""★第159批上A 刀3（O-B7）：补丁生命周期表（激活态记账 + handoff）。

与 ``task_ledger`` 同目录、复用同一套单事务写机制，但**独立文件 + 独立枚举**——

* 补丁是「变更生命周期」，任务态账本是「重启恢复协议」，二者语义不同；
* 若复用 ``task_ledger.VALID_TASK_STATUS``，``reconcile_on_startup`` 会把
  补丁的 ``activated`` 误判为「标 running 但 pid 死」→ 改判 ``interrupted``，
  进而被「自动回滚」逻辑误伤。故本模块自带 7 态枚举，绝不混入任务态枚举。

设计边界
--------
* 只写 ``data/evolution/patch_lifecycle.json``（测试环境 pytest 不写生产 data/，
  由复用的 ``task_ledger._atomic_save`` 的 ``_writable`` 守卫保证）。
* 复用 ``task_ledger._atomic_save``（temp + os.replace + fsync 单事务）。
* ``reconcile_patches`` 仅出诊断回执、标记待人工，**绝不自动回滚**补丁。
* 本模块不改动任何进化决策、不发网络请求。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.evolution.task_ledger import _atomic_save

__all__ = [
    "PATCH_LIFECYCLE_VERSION",
    "STAGE_ACTIVATED",
    "STAGE_COMMITTED",
    "STAGE_OBSERVING",
    "STAGE_PROPOSED",
    "STAGE_REJECTED",
    "STAGE_ROLLED_BACK",
    "STAGE_VALIDATED",
    "VALID_PATCH_STAGES",
    "PATCH_HISTORY_STATUSES",
    "LEGAL_HISTORY_TRANSITIONS",
    "HISTORY_STATUS_ROLLED_BACK",
    "HISTORY_STATUS_WONT_FIX",
    "mark_history_wont_fix",
    "legal_history_transition",
    "mark_history_rolled_back",
    "lifecycle_path",
    "load_lifecycle",
    "publish_handoff",
    "reconcile_patches",
    "record_activation",
    "record_rollback",
    "touch_patch",
]

#: 账本结构版本（结构变更时递增）。
PATCH_LIFECYCLE_VERSION = "159-OB7-1"

# ---- 7 态枚举（单调可回退；rolled_back / rejected 为旁路终态） ----
STAGE_PROPOSED = "proposed"
STAGE_VALIDATED = "validated"
STAGE_ACTIVATED = "activated"
STAGE_OBSERVING = "observing"
STAGE_COMMITTED = "committed"
STAGE_ROLLED_BACK = "rolled_back"
STAGE_REJECTED = "rejected"
VALID_PATCH_STAGES = frozenset({
    STAGE_PROPOSED, STAGE_VALIDATED, STAGE_ACTIVATED, STAGE_OBSERVING,
    STAGE_COMMITTED, STAGE_ROLLED_BACK, STAGE_REJECTED,
})

#: 观察期默认参数：activated_at + N×tick 为 observation_deadline。
DEFAULT_OBSERVATION_TICKS = 1
DEFAULT_OBSERVATION_TICK_S = 3600  # 1h 观察窗口

#: handoff 事件名（EventBus publish，当前仅旁路遥测 EventTap 通配记账，无业务订阅者）。
EVENT_PATCH_HANDOFF = "evolution.patch_handoff"

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ★第162批刀5（E2）：handoff 事件发布失败累计计数（可观测 + 门控读取）
_handoff_publish_fail_count = 0


def get_handoff_publish_fail_count() -> int:
    """patch_lifecycle 自上线以来 handoff 事件发布失败累计次数（E2 升级 WARNING 后供读取）。"""
    return _handoff_publish_fail_count


# ------------------------------------------------------------------ 路径与读写
def lifecycle_path() -> str:
    """补丁生命周期表路径（``data/evolution/patch_lifecycle.json``）。"""
    return os.path.join(_PROJECT_ROOT, "data", "evolution", "patch_lifecycle.json")


def _empty_lifecycle() -> dict[str, Any]:
    return {"version": PATCH_LIFECYCLE_VERSION, "updated_at": 0.0, "patches": {}}


def load_lifecycle(path: str | None = None) -> dict[str, Any]:
    """读取补丁生命周期表；不存在/损坏返回空账本（**不抛异常**）。"""
    _p = path or lifecycle_path()
    if not os.path.isfile(_p):
        return _empty_lifecycle()
    try:
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="nucleus.evolution.patch_lifecycle::load_lifecycle",
                   level="warning")
        return _empty_lifecycle()
    if not isinstance(_d, dict) or not isinstance(_d.get("patches"), dict):
        silent_exc(ValueError("lifecycle 结构非法（缺 patches）"),
                   where="nucleus.evolution.patch_lifecycle::load_lifecycle",
                   level="warning")
        return _empty_lifecycle()
    _d.setdefault("version", PATCH_LIFECYCLE_VERSION)
    _d.setdefault("patches", {})
    return _d


def _new_entry() -> dict[str, Any]:
    return {
        "stage": STAGE_PROPOSED,
        "stage_at": 0.0,
        "task_id": None,
        "risk_level": 2,
        "file": "",
        "backup_path": "",
        "rollback_available": False,
        "verified_by": "",
        "attempts": 0,
        "observation_deadline": 0.0,
        "owner": "",
        "handed_off_at": 0.0,
        "notes": "",
        "hunks": [],  # ★第159批 刀3：hunks 级部分回滚占位（实施推迟到 160）
    }


def _risk_numeric(risk_level: Any) -> int:
    """中文枚举 低/中/高 → 数值 1/2/3；数字原样；未知 → 2。"""
    if isinstance(risk_level, bool):
        return 2
    if isinstance(risk_level, (int, float)):
        return int(risk_level)
    _s = str(risk_level or "").strip().lower()
    if _s in ("低", "low", "1"):
        return 1
    if _s in ("中", "medium", "mid", "2"):
        return 2
    if _s in ("高", "high", "3"):
        return 3
    try:
        return int(float(_s))
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="nucleus.evolution.patch_lifecycle::_risk_numeric",
                   level="debug")
        return 2


# ------------------------------------------------------------------ 写入侧
def _read_effect_verified(patch_id: str) -> Any:
    """★B1(吸收 O-B7)：从 ``data/param_patch_history.json`` 的 ``applied`` 数组
    读取该补丁的 ``effect_verified`` 标志，供 ``record_activation`` 落册。

    **非门控**：仅读数、不改任何 stage 逻辑；读取失败或缺席返回 ``None``。
    """
    _p = os.path.join(_PROJECT_ROOT, "data", "param_patch_history.json")
    try:
        if not os.path.isfile(_p):
            return None
        _hist = json.load(open(_p, encoding="utf-8"))
        _applied = _hist.get("applied") or []
        for _x in _applied:
            if isinstance(_x, dict) and str(_x.get("id")) == str(patch_id):
                return _x.get("effect_verified")
        return None
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_lifecycle::_read_effect_verified")
        return None


def record_activation(
    patch_id: str,
    *,
    risk_level: Any = 2,
    file: str = "",
    backup_path: str = "",
    rollback_available: bool = False,
    owner: str = "",
    approver: str = "",
    approved_source: str = "",
    approved_signer: str = "",
    approved_at: float = 0.0,
    meta: dict | None = None,
    task_id: str | None = None,
    path: str | None = None,
    ticks: int = DEFAULT_OBSERVATION_TICKS,
    tick_s: int = DEFAULT_OBSERVATION_TICK_S,
) -> dict[str, Any]:
    """补丁成功应用后登记生命周期：``stage=activated``。

    硬点（验收 a）：``rollback_available=False`` 者**不得进入 activated**——
    直接置 ``rejected``（现网 11 条将被挡，符合预期），并出 WARNING。

    风险分级接线（验收 7）：``risk≥3`` 或核心文件者激活必须
    ``rollback_available=True`` 且 ``backup_path`` 可读；此门由调用方
    （``apply_all_pending`` 已先备份）保证，这里仅作保守兜底校验。
    """
    _pid = str(patch_id)
    _d = load_lifecycle(path)
    _e = _d["patches"].get(_pid, _new_entry())
    _now = time.time()
    _risk = _risk_numeric(risk_level)

    # ★B1(吸收 O-B7)：激活登记**前置**读取 effect_verified 落册（非门控，仅读数）
    _e["effect_verified"] = _read_effect_verified(_pid)

    if not rollback_available:
        # ★验收 a：无回滚能力者禁止进入 activated
        _e["stage"] = STAGE_REJECTED
        _e["stage_at"] = _now
        _e["notes"] = "rollback_available=False，禁止进入 activated（验收 a 硬点）"
        _d["patches"][_pid] = _e
        _atomic_save(_d, path)
        return _e

    _e["stage"] = STAGE_ACTIVATED
    _e["stage_at"] = _now
    _e["task_id"] = task_id
    _e["risk_level"] = _risk
    _e["file"] = file
    _e["backup_path"] = backup_path
    _e["rollback_available"] = True
    _e["owner"] = owner or approver or (meta or {}).get("owner", "") or ""
    _e["attempts"] = int(_e.get("attempts", 0) or 0) + 1
    _e["observation_deadline"] = _now + (max(1, int(ticks)) * int(tick_s))
    _e["verified_by"] = approved_source or approved_signer or ""
    _e["notes"] = "activated by apply_all_pending"
    _d["patches"][_pid] = _e
    _atomic_save(_d, path)
    publish_handoff(_pid, STAGE_ACTIVATED, _e)
    return _e


def record_rollback(patch_id: str, reason: str = "", path: str | None = None) -> dict[str, Any]:
    """补丁成功回滚后登记：``stage=rolled_back``（验收 c/d：rolled_back_at 0→≥1；
    失败不置位由调用方保证——本函数只在成功路径被调用）。"""
    _pid = str(patch_id)
    _d = load_lifecycle(path)
    _e = _d["patches"].get(_pid, _new_entry())
    _now = time.time()
    _e["stage"] = STAGE_ROLLED_BACK
    _e["stage_at"] = _now
    _e["rolled_back_at"] = _now
    _e["rollback_reason"] = reason or "rolled back"
    _e["notes"] = reason or "rolled back"
    _d["patches"][_pid] = _e
    _atomic_save(_d, path)
    publish_handoff(_pid, STAGE_ROLLED_BACK, _e)
    return _e


def touch_patch(patch_id: str, path: str | None = None) -> bool:
    """观察期存活证明（新增；**不**放宽 ``heartbeat_task`` 只认 running 的判断）。

    仅刷新 ``activated`` / ``observing`` 条目的观察心跳时间戳，不影响 stage。
    """
    _pid = str(patch_id)
    _d = load_lifecycle(path)
    _e = _d["patches"].get(_pid)
    if not isinstance(_e, dict) or _e.get("stage") not in (
            STAGE_ACTIVATED, STAGE_OBSERVING):
        return False
    _e["last_observation"] = time.time()
    _atomic_save(_d, path)
    return True


def reconcile_patches(path: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """★补丁段对账（启动期）：``activated`` / ``observing`` 超 ``observation_deadline``
    未 ``committed`` → 出诊断回执、标记待人工（``overdue=True`` + ``overdue_receipts``），
    **绝不自动回滚**。

    与 ``task_ledger.reconcile_on_startup`` 解耦：本函数只读/写 patch_lifecycle.json，
    不会把补丁条目误判为死任务（验收 b 的根基）。
    """
    _d = load_lifecycle(path)
    _now = time.time()
    _receipts: list[dict[str, Any]] = []
    for _pid, _e in list(_d.get("patches", {}).items()):
        if not isinstance(_e, dict):
            continue
        _st = _e.get("stage")
        if _st not in (STAGE_ACTIVATED, STAGE_OBSERVING):
            continue
        _dl = _e.get("observation_deadline") or 0
        if _dl and _now > _dl and _st != STAGE_COMMITTED:
            _e["overdue"] = True
            _rcpt = {
                "at": _now,
                "from_stage": _st,
                "action": "mark_pending_human",
                "auto_rollback": False,  # ★绝不为补丁自动回滚
                "reason": "观察期超 observation_deadline 未 committed，待人工裁定",
            }
            _e.setdefault("overdue_receipts", []).append(_rcpt)
            _receipts.append({
                "patch_id": _pid, "stage": _st, "auto_rollback": False,
                "receipt": _rcpt,
            })
    if _receipts and not dry_run:
        _atomic_save(_d, path)
    return {
        "overdue": len(_receipts),
        "receipts": _receipts,
        "dry_run": bool(dry_run),
    }


def publish_handoff(patch_id: str, stage: str, entry: dict[str, Any] | None = None) -> int:
    """发布 ``evolution.patch_handoff`` 事件（验收 e）。

    当前仅旁路遥测（EventTap 通配记账），无业务订阅者。EventBus 未启用/不可用时不阻断主流程。
    """
    try:
        from nucleus.events.EventBus import get_event_bus
        _bus = get_event_bus()
        _entry = entry or load_lifecycle().get("patches", {}).get(str(patch_id), {})
        _payload = {
            "patch_id": str(patch_id),
            "stage": stage,
            "file": _entry.get("file", ""),
            "owner": _entry.get("owner", ""),
            "backup_path": _entry.get("backup_path", ""),
            "observation_deadline": _entry.get("observation_deadline", 0),
            "risk_level": _entry.get("risk_level", 2),
            "handoff_at": time.time(),
        }
        return _bus.publish(EVENT_PATCH_HANDOFF, _payload, source="patch_lifecycle")
    except Exception as _e:  # 事件总线不可用不阻断补丁生命周期闭环
        global _handoff_publish_fail_count
        _handoff_publish_fail_count += 1
        silent_exc(_e, where="nucleus.evolution.patch_lifecycle::publish_handoff",
                   level="warning")
        return 0


def PatchManager_is_core_file(file: str) -> bool:
    """惰性判定核心文件（避免循环 import；失败保守返回 False）。"""
    try:
        from nucleus.reasoning.PatchManager import PatchManager as _PM
        return bool(_PM._m80_is_core_file(str(file or "")))
    except Exception as _e:
        silent_exc(_e, where="nucleus.evolution.patch_lifecycle::PatchManager_is_core_file",
                   level="debug")
        return False


# ============================================================
# ★346批刀2：``patch_history`` 状态枚举 + 回退记账（挂账69/58）
# ============================================================
#
# **真缺口（烛微346自纠）**：``rolled_back`` 全仓 119 处命中，但代码补丁台账
# ``data/patches/patch_history.json`` 的 status 实测只有
# ``approved``(73) / ``runtime_verified``(13) 两种 —— 回退动作只把
# ``applied=False`` + ``rollback_available=False`` + ``rolled_back_at`` 改掉，
# **status 停在 approved**。于是：
#   · 统计口径里「已回退」永远是 0（``evolution_stats`` 靠 status 判 rolled_back）；
#   · 事后看台账，「这条到底被回退过没有」要靠翻 change_log.md 猜。
# ⇒ 回退不记账 = 状态机缺一个必需的旁路终态。
#
# 本段只做三件事：**补枚举 / 补迁移表 / 补写入口**，不改任何决策逻辑。

#: patch_history status 枚举（实测值 + 回退新态）
HISTORY_STATUS_PENDING = "pending"
HISTORY_STATUS_APPROVED = "approved"
HISTORY_STATUS_APPLIED = "applied"
HISTORY_STATUS_RUNTIME_VERIFIED = "runtime_verified"
HISTORY_STATUS_ROLLED_BACK = "rolled_back"
# ★348刀10（74号裁定）：撤单终态。**不是**「验证失败」，是「人工裁定不该做」。
#   与 rejected_* 的区别：rejected_* 是机器/审批拒绝一次提交；wont_fix 是**议题级**
#   结论（例：CompanionBridge.handshake 是新人类族群通信桥的协议接口，不可删）。
HISTORY_STATUS_WONT_FIX = "wont_fix"

#: ``rejected_*`` 是一族（实测有 rejected_core_guard / rejected_by_baseline /
#: rejected_safety / rejected_verify / rejected_ambiguous / rejected_syntax …），
#: 按前缀归族，**不逐个列**——列不全就等于把新 reject 形态判成非法迁移。
HISTORY_STATUS_REJECTED_PREFIX = "rejected_"

PATCH_HISTORY_STATUSES = frozenset({
    HISTORY_STATUS_PENDING, HISTORY_STATUS_APPROVED, HISTORY_STATUS_APPLIED,
    HISTORY_STATUS_RUNTIME_VERIFIED, HISTORY_STATUS_ROLLED_BACK,
    HISTORY_STATUS_WONT_FIX,
})

#: 合法迁移表 ``旧态 → 允许到达的新态``。
#:
#: 口径：
#:  · 空/未知旧态（存量条目没有 status）⇒ 允许任何目标（存量数据不能因为
#:    缺字段就永远动不了，但也仅限"没有任何旧态"这一种）；
#:  · ``rolled_back`` 是**旁路终态**：只允许人工"重开"回 pending/approved
#:    （回退后要重修，必须显式重开，不能凭空变成 applied）；
#:  · ``applied`` 与 ``runtime_verified`` 互转（运行时验证就是应用后再验）。
LEGAL_HISTORY_TRANSITIONS = {
    "": PATCH_HISTORY_STATUSES | {"rejected_*"},
    HISTORY_STATUS_PENDING: (HISTORY_STATUS_APPROVED, "rejected_*",
                             HISTORY_STATUS_ROLLED_BACK, HISTORY_STATUS_WONT_FIX),
    HISTORY_STATUS_APPROVED: (HISTORY_STATUS_APPLIED, HISTORY_STATUS_RUNTIME_VERIFIED,
                              "rejected_*", HISTORY_STATUS_ROLLED_BACK,
                              HISTORY_STATUS_WONT_FIX),
    HISTORY_STATUS_APPLIED: (HISTORY_STATUS_RUNTIME_VERIFIED,
                             HISTORY_STATUS_ROLLED_BACK),
    HISTORY_STATUS_RUNTIME_VERIFIED: (HISTORY_STATUS_APPLIED,
                                      HISTORY_STATUS_ROLLED_BACK),
    HISTORY_STATUS_ROLLED_BACK: (HISTORY_STATUS_PENDING, HISTORY_STATUS_APPROVED),
}

#: 迁移表里 ``rejected_*`` 族通配的落地名（供调用方读取）
_REJECTED_FAMILY = "rejected_*"


def _norm_history_status(status: Any) -> str:
    """status 归一：小写去空白；``rejected_*`` 族原样保留（族内形态有意义）。"""
    return str(status or "").strip().lower()


def legal_history_transition(old: Any, new: Any, raw_status: Any = None) -> bool:
    """``patch_history`` 状态迁移是否合法。

    ``raw_status``：原值（当 ``new`` 是归一后的 ``rolled_back`` 而原值可能是
    大小写混写时传入；不传则用 ``old``）。
    未知旧态（不属于任何枚举值、也不是 ``rejected_*``）⇒ **拒绝**
    （不是"随便放行"——未知态意味着有人手改过台账，该由人看）。
    """
    _old = _norm_history_status(old if raw_status is None else raw_status)
    _new = _norm_history_status(new)
    if not _new:
        return False
    _allowed = LEGAL_HISTORY_TRANSITIONS.get(_old)
    if _allowed is None:
        # 旧态是 rejected_* 族：等价于终态，只允许重开
        if _old.startswith(HISTORY_STATUS_REJECTED_PREFIX):
            return _new in (HISTORY_STATUS_PENDING, HISTORY_STATUS_APPROVED)
        return False
    if _new in _allowed:
        return True
    # 目标态是否属 rejected_* 族，看的是**目标**而不是旧态
    if _new.startswith(HISTORY_STATUS_REJECTED_PREFIX):
        return _REJECTED_FAMILY in _allowed
    return False


def mark_history_rolled_back(patch: Any, reason: str = "",
                             now: float | None = None,
                             source: str = "") -> dict:
    """把**回退动作**记进台账（原地改 patch，落盘由调用方负责）。

    写入：``status="rolled_back"`` + ``rolled_back_reason`` + ``rolled_back_at``
    + ``status_history`` 追加一条（谁、何时、为什么）。

    非法迁移**不写**并返回 ``ok=False``（不静默纠正 —— 与
    ``audit_problem_pool.set_status`` 同一纪律）。

    Returns: ``{ok, changed, from_status, to_status, reason}``
    """
    if not isinstance(patch, dict):
        return {"ok": False, "changed": False, "from_status": None,
                "to_status": None, "reason": "patch_not_dict"}
    _ts = float(now if now is not None else time.time())
    _old_raw = patch.get("status")
    _old = _norm_history_status(_old_raw)
    if not legal_history_transition(_old, HISTORY_STATUS_ROLLED_BACK):
        return {"ok": False, "changed": False, "from_status": _old_raw,
                "to_status": HISTORY_STATUS_ROLLED_BACK,
                "reason": "illegal_transition_from:%s" % (_old or "<empty>")}
    patch["status"] = HISTORY_STATUS_ROLLED_BACK
    patch["rolled_back_at"] = _ts
    patch["rolled_back_reason"] = str(reason or "")[:200]
    _hist = patch.get("status_history")
    if not isinstance(_hist, list):
        _hist = []
    _hist.append({"status": HISTORY_STATUS_ROLLED_BACK, "at": _ts,
                  "from": _old_raw or "", "reason": str(reason or "")[:200],
                  "source": str(source or "")[:80]})
    patch["status_history"] = _hist
    return {"ok": True, "changed": True, "from_status": _old_raw,
            "to_status": HISTORY_STATUS_ROLLED_BACK, "reason": str(reason or "")}


def mark_effect_verify_rolled_back(patch_id: str, reason: str = "",
                                   base_dir: str | None = None) -> dict:
    """刀2②：回退时**同步标记** ``effect_verify.json`` 里的效果验证结论。

    为什么必须联动：效果验证台账回答"这条补丁改动有效吗"。补丁被回退后，
    台账里若仍留着 ``passed=True``，就会出现"已验证有效"与"已回退"并存的自相矛盾读数
    （铁律166：读数必须能回答它声称回答的问题）。

    ``base_dir`` 注入给隔离验证用；生产不传 ⇒ 走 ``EvolutionEffectVerifier`` 默认目录。
    """
    _pid = str(patch_id or "").strip()
    if not _pid:
        return {"ok": False, "reason": "empty_patch_id"}
    try:
        from nucleus.evolution.EvolutionEffectVerifier import EvolutionEffectVerifier
        _v = EvolutionEffectVerifier(base_dir)
        _verdicts = getattr(_v, "_verdicts", None)
        if not isinstance(_verdicts, dict):
            return {"ok": False, "reason": "verdicts_not_dict"}
        _cur = _verdicts.get(_pid)
        if not isinstance(_cur, dict):
            # 没有效果验证记录：不凭空造一条（"没验过"与"验过但回退"不同）
            return {"ok": False, "reason": "no_verdict_record"}
        _cur["rolled_back"] = True
        _cur["rolled_back_at"] = time.time()
        _cur["rolled_back_reason"] = str(reason or "")[:200]
        _v.save()
        return {"ok": True, "reason": "", "patch_id": _pid}
    except Exception as _e:
        silent_exc(_e, where="nucleus.evolution.patch_lifecycle::mark_effect_verify_rolled_back",
                   level="warning")
        return {"ok": False, "reason": "%s: %s" % (type(_e).__name__, _e)}


def mark_history_wont_fix(patch: Any, reason: str = "", now: float | None = None,
                          source: str = "", by: str = "") -> dict:
    """★348刀10（74号裁定）：把**撤单**记进台账（原地改 patch，落盘由调用方负责）。

    写入：``status="wont_fix"`` + ``wont_fix_reason`` + ``wont_fix_at``
    + ``status_history`` 追加一条（谁、何时、为什么）。
    另置 ``o3_auto_dispatch_ready=False`` —— 撤单的候选**不得**再进 O3 派单链
    （不置这一位，撤单只改标签不改行为，下一拍照样派出去）。

    非法迁移**不写**并返回 ``ok=False``（不静默纠正，与 mark_history_rolled_back 同纪律）。
    """
    if not isinstance(patch, dict):
        return {"ok": False, "changed": False, "from_status": None,
                "to_status": None, "reason": "patch_not_dict"}
    _ts = float(now if now is not None else time.time())
    _old_raw = patch.get("status")
    _old = _norm_history_status(_old_raw)
    if not legal_history_transition(_old, HISTORY_STATUS_WONT_FIX):
        return {"ok": False, "changed": False, "from_status": _old_raw,
                "to_status": HISTORY_STATUS_WONT_FIX,
                "reason": "illegal_transition_from:%s" % (_old or "<empty>")}
    patch["status"] = HISTORY_STATUS_WONT_FIX
    patch["wont_fix_at"] = _ts
    patch["wont_fix_reason"] = str(reason or "")[:300]
    patch["wont_fix_by"] = str(by or "")[:80]
    patch["o3_auto_dispatch_ready"] = False
    _hist = patch.get("status_history")
    if not isinstance(_hist, list):
        _hist = []
    _hist.append({"status": HISTORY_STATUS_WONT_FIX, "at": _ts,
                  "from": _old_raw or "", "reason": str(reason or "")[:300],
                  "source": str(source or "")[:80], "by": str(by or "")[:80]})
    patch["status_history"] = _hist
    return {"ok": True, "changed": True, "from_status": _old_raw,
            "to_status": HISTORY_STATUS_WONT_FIX, "reason": str(reason or "")}
