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

import io
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.evolution.task_ledger import _atomic_save

__all__ = [
    "PATCH_LIFECYCLE_VERSION",
    "VALID_PATCH_STAGES",
    "STAGE_PROPOSED",
    "STAGE_VALIDATED",
    "STAGE_ACTIVATED",
    "STAGE_OBSERVING",
    "STAGE_COMMITTED",
    "STAGE_ROLLED_BACK",
    "STAGE_REJECTED",
    "lifecycle_path",
    "load_lifecycle",
    "record_activation",
    "record_rollback",
    "touch_patch",
    "reconcile_patches",
    "publish_handoff",
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
        with io.open(_p, encoding="utf-8") as _f:
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
