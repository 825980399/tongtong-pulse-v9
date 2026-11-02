# -*- coding: utf-8 -*-
"""归档回收（★423 刀3）—— 把 archive 里「运行时验过但没应用」的件重新入池。

背景与 T0 实测（**与任务书不一致，按实测施工**）
------------------------------------------------
任务书说「`pending_patches_archive.json` 里有 9 条 `runtime_verified` 但
`applied=false`」。T0 逐条核实的结果：

  * 文件形状是 **list of 10 个归档批次**（每项 ``{"version","items","last_archived_at"}``），
    真正的补丁在 ``items`` 里，展开后共 **8 条**（不是 9 条）；
  * 满足 ``runtime_verified is True`` 且 ``applied`` 非真的只有 **1 条**
    （``o3_e53538204dfd18aa`` / ``nucleus/evolution/DiffArchiver.py::get_trace``）；
  * 而那一条同时带 ``o3_dead_code=True`` / ``o3_dispatch_verdict="sandbox_only"``，
    ``archive_reason`` 写的是「O3 死代码候选，从未派单」
    ⇒ 按任务书自己列的排除项「已经 O3 删过的不要回收」，**它不该回收**。

⇒ 生产可回收数 = **0**。本刀的价值在**能力与判据**，不在今天的回收量；
  活体验证只能用隔离构造件（见交付报告 §三）。

★为什么回收后不会立刻被 apply
    ``status`` 恢复成**归档前状态**（``status_before_archive``，缺失时退化为
    ``runtime_verified``），而周期 apply 只统计 ``status == "approved"``
    （`main._m422_count_approved_pending`）⇒ 回收件最多"重新排队等审批/验证"，
    不会越过审批直接改代码。

★为什么单独成模块而不是塞进 PatchManager
    `PatchManager` 已 4000+ 行且入池口 `save_pending_patch` 有六道闸
    （域守卫 / def 名对齐 / 片段→完整 / 危险预检 / 风险定级 / 滞留清理），
    任何一道都可能把历史件挡掉，且**不返回值**（成功失败分不清）。
    回收是"批量重放历史件"，需要的是**可计数的逐条判据**，所以走独立模块 +
    独立审计账，写 pending 时也只做"读-改-写"三件事。
"""

from __future__ import annotations

import copy
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("ArchiveRecovery")

_M423_K3_VERSION = "423.1"

#: 灰度开关（config 登记）。关掉时入口直接返回 status=disabled，零副作用。
RECOVER_SWITCH = "ENABLE_M423_ARCHIVE_RECOVERY"

#: 太老的不回收（任务书：超过 30 天的不要）
MAX_AGE_DAYS = 30.0

#: 单次回收上限（防一次把 pending 灌爆）
MAX_RECOVER = 20

#: 回收标记字段（任务书要求「打个 recovered_from_archive 标记，方便追溯」）
RECOVERED_FLAG = "recovered_from_archive"
RECOVERED_BY = "423刀3_archive_recovery"

_ARCHIVE_REL = os.path.join("data", "patches", "pending_patches_archive.json")
_PENDING_REL = os.path.join("data", "patches", "pending_patches.json")
_DEAD_REL = os.path.join("data", "patches", "apply_retry_422.json")
_LEDGER_REL = os.path.join("data", "patches", "archive_recovery_log.jsonl")

#: 年龄判据取的候选时间字段（谁先有值用谁，都不信 created_at 一个）
_AGE_KEYS = ("created_at", "generated_at", "saved_at", "timestamp")

#: 「运行时验证过」的 status 取值
_RV_STATUS = "runtime_verified"


def enabled() -> bool:
    """灰度开关 `ENABLE_M423_ARCHIVE_RECOVERY`；缺省 True。"""
    try:
        import config as _c
        return bool(getattr(_c, RECOVER_SWITCH, True))
    except Exception as _e:
        silent_exc(_e, where="evolution.archive_recovery:enabled", level="debug")
        return True


def _m423_k3_load(path: str) -> Any:
    """读 JSON；文件不存在或读不动都返回 None（调用方据 None 判"没有"）。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as _f:
            return json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="evolution.archive_recovery:_load", level="warning")
        return None


def flatten_archive(doc: Any) -> tuple[list[dict[str, Any]], str]:
    """把 archive 文档摊平成 patch 列表，兼容三种历史形状。

    历史上有两套写入者、三种落地形状（都实测过）：
      * ``list[dict]`` 且元素**不是**批次（`PatchManager.sweep_runtime_failed` 写的
        扁平 patch 列表）；
      * ``list[dict]`` 且元素**是**批次 ``{"items": [...], "version", ...}``
        （`tools/m411_archive_expired_watch.py` 写的；**当前生产就是这个形状**）；
      * ``dict`` 且带 ``"items"``（同一个工具在"读到的不是 list"时包一层的结果）。

    Returns:
        ``(rows, shape)``；``shape`` ∈
        ``list_of_patches`` / ``list_of_batches`` / ``dict_items`` / ``unknown``。
    """
    if isinstance(doc, list):
        if not doc:
            return [], "list_of_patches"
        if any(isinstance(x, dict) and isinstance(x.get("items"), list) for x in doc):
            _out: list[dict[str, Any]] = []
            for _b in doc:
                if isinstance(_b, dict) and isinstance(_b.get("items"), list):
                    _out.extend(x for x in _b["items"] if isinstance(x, dict))
                elif isinstance(_b, dict) and _b.get("id"):
                    # 混合形状：批次里混了裸 patch，也照收
                    _out.append(_b)
            return _out, "list_of_batches"
        return [x for x in doc if isinstance(x, dict)], "list_of_patches"
    if isinstance(doc, dict) and isinstance(doc.get("items"), list):
        return [x for x in doc["items"] if isinstance(x, dict)], "dict_items"
    return [], "unknown"


def is_runtime_verified(row: dict) -> bool:
    """这条算不算"运行时验证过"—— **两种口径任一成立即可**。

    ★为什么不能只看字段（423 刀3 T0 二次核实，这里栽过一次）：
        生产 archive 里那 8 条裸 patch 的 `runtime_verified` **字段是 None**，
        真正标记它们"验过"的是 `status == "runtime_verified"`；
        而 O3 通道归档的件反过来只有 `runtime_verified=True` 字段（status 是 archived）。
      只按字段判 ⇒ 可回收 0 条，与任务书说的 8~9 条整整差一批。
    """
    if not isinstance(row, dict):
        return False
    if str(row.get("status") or "") == _RV_STATUS:
        return True
    return row.get("runtime_verified") is True


def _m423_k3_age_days(row: dict, now: float) -> float | None:
    """按 ``_AGE_KEYS`` 取最可信的时间算年龄（天）；都不像数就返回 None。"""
    _best = 0.0
    for _k in _AGE_KEYS:
        _v = row.get(_k)
        if isinstance(_v, (int, float)) and _v > 0:
            _best = max(_best, float(_v))
    if _best <= 0:
        return None
    return max(0.0, (float(now) - _best) / 86400.0)


def select_recoverable(rows: list[dict[str, Any]], now: float,
                       pending_ids: set[str] | None = None,
                       dead_ids: set[str] | None = None,
                       max_age_days: float = MAX_AGE_DAYS,
                       max_recover: int = MAX_RECOVER,
                       ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """逐条判据筛出可回收件；**每个排除理由都单独计数**（便于写报告）。

    判据（全过才回收，按任务书原文逐条落）：
        1. **运行时验证过** —— ``status == "runtime_verified"`` 或
           ``runtime_verified is True``（两种口径任一成立；理由见 `is_runtime_verified`）
        2. ``applied`` 非真 —— 应用过的不要
        3. 未过期 —— 年龄 ≤ ``max_age_days``（时间取不到 ⇒ 不回收，不猜）
        4. 非 O3 死码 —— ``o3_dead_code`` 非真（O3 判死的不回收）
        5. id 不在当前 pending 里 —— 避免重复入池
        6. id 不在 dead 账里 —— 被周期 apply 判死的不要捞回来空转
    """
    _pend = set(pending_ids or set())
    _dead = set(dead_ids or set())
    _picked: list[dict[str, Any]] = []
    _st: dict[str, Any] = {
        "scanned": 0, "recoverable": 0,
        "skip_not_runtime_verified": 0, "skip_applied": 0, "skip_o3_dead_code": 0,
        "skip_no_timestamp": 0, "skip_too_old": 0, "skip_no_id": 0,
        "skip_already_pending": 0, "skip_marked_dead": 0, "truncated": 0,
        "max_age_days": float(max_age_days), "max_recover": int(max_recover),
        #: 两个口径各回收了几条（恒存在，缺失会让报告读数不稳）
        "via_status": 0, "via_field": 0,
        "oldest_age_days": None, "newest_age_days": None,
    }
    _ages: list[float] = []
    for _r in rows or []:
        if not isinstance(_r, dict):
            continue
        _st["scanned"] += 1
        if not is_runtime_verified(_r):
            _st["skip_not_runtime_verified"] += 1
            continue
        if _r.get("applied"):
            _st["skip_applied"] += 1
            continue
        if _r.get("o3_dead_code"):
            _st["skip_o3_dead_code"] += 1
            continue
        _age = _m423_k3_age_days(_r, now)
        if _age is None:
            _st["skip_no_timestamp"] += 1
            continue
        _ages.append(_age)
        if _age > float(max_age_days):
            _st["skip_too_old"] += 1
            continue
        _pid = str(_r.get("id") or "")
        if not _pid:
            _st["skip_no_id"] += 1
            continue
        if _pid in _pend:
            _st["skip_already_pending"] += 1
            continue
        if _pid in _dead:
            _st["skip_marked_dead"] += 1
            continue
        _picked.append(_r)
        # ★口径计数放在**最终入选**处，不是"过了第一道判据"处 ——
        #   否则报告里「8 条来自 status 口径」会被排除件污染（首版就栽在这：
        #   already_applied / too_old 等 6 条 status 也是 runtime_verified，白算 6 条）。
        if str(_r.get("status") or "") == _RV_STATUS:
            _st["via_status"] = int(_st.get("via_status") or 0) + 1
        else:
            _st["via_field"] = int(_st.get("via_field") or 0) + 1
    if _ages:
        _st["oldest_age_days"] = round(max(_ages), 2)
        _st["newest_age_days"] = round(min(_ages), 2)
    if len(_picked) > int(max_recover):
        _st["truncated"] = len(_picked) - int(max_recover)
        _picked = _picked[:int(max_recover)]
    _st["recoverable"] = len(_picked)
    return _picked, _st


def _m423_k3_dead_ids(path: str) -> set[str]:
    """从周期 apply 的重试账里取"已判死"的 id 集合（读不到就当空集）。"""
    _doc = _m423_k3_load(path)
    if not isinstance(_doc, dict):
        return set()
    _out: set[str] = set()
    _rows = _doc.get("patches")
    if isinstance(_rows, dict):
        for _k, _v in _rows.items():
            if isinstance(_v, dict) and _v.get("dead"):
                _out.add(str(_k))
    elif isinstance(_rows, list):
        for _v in _rows:
            if isinstance(_v, dict) and _v.get("dead"):
                _out.add(str(_v.get("id") or ""))
    return {x for x in _out if x}


def _m423_k3_pending_ids(path: str) -> tuple[set[str], list[dict[str, Any]]]:
    """读 pending：返回 (id 集合, 原始行)。形状不认识 ⇒ 当空表（并让调用方中止）。"""
    _doc = _m423_k3_load(path)
    if _doc is None:
        return set(), []
    if isinstance(_doc, dict):
        _rows = _doc.get("patches") or _doc.get("items") or []
    elif isinstance(_doc, list):
        _rows = _doc
    else:
        return set(), []
    _rows = [x for x in _rows if isinstance(x, dict)]
    return {str(x.get("id") or "") for x in _rows if x.get("id")}, _rows


def _m423_k3_write_json(path: str, doc: Any) -> bool:
    """原子写 JSON（tmp + os.replace）；失败返回 False，不抛。"""
    _tmp = path + ".tmp423"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(doc, _f, ensure_ascii=False, indent=2)
        os.replace(_tmp, path)
        return True
    except (OSError, ValueError, TypeError) as _e:
        silent_exc(_e, where="evolution.archive_recovery:_write_json", level="warning")
        try:
            if os.path.exists(_tmp):
                os.remove(_tmp)
        except OSError as _e2:
            silent_exc(_e2, where="evolution.archive_recovery:_write_json:cleanup", level="debug")
        return False


def _m423_k3_append_ledger(path: str, rec: dict[str, Any]) -> bool:
    """审计账 append-only（一行一条）；失败不影响主流程。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as _f:
            _f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except (OSError, ValueError, TypeError) as _e:
        silent_exc(_e, where="evolution.archive_recovery:_append_ledger", level="warning")
        return False


def _m423_k3_stamp(row: dict, now: float) -> dict[str, Any]:
    """把一条归档件改成"可重新入池"的样子（深拷贝，不动原表）。"""
    _n = copy.deepcopy(row)
    _prev = str(_n.pop("status_before_archive", "") or "").strip()
    _n["status"] = _prev or "runtime_verified"
    _n["applied"] = False
    _n[RECOVERED_FLAG] = True
    _n["recovered_at"] = round(float(now), 3)
    _n["recovered_by"] = RECOVERED_BY
    _n["recovered_version"] = _M423_K3_VERSION
    # 归档原因留一份（别让"为什么进过归档"这段历史被覆盖掉）
    _n["archive_reason_before_recover"] = str(_n.get("archive_reason") or "")
    _n["m423_recovered"] = True
    return _n


def recover(root: str, now: float | None = None,
            archive_path: str | None = None, pending_path: str | None = None,
            dead_path: str | None = None, ledger_path: str | None = None,
            dry_run: bool = False,
            max_age_days: float = MAX_AGE_DAYS,
            max_recover: int = MAX_RECOVER) -> dict[str, Any]:
    """扫一遍 archive，把符合条件的件重新入池（**启动时调一次**）。

    Args:
        root: **必传**仓根（不给默认值 —— 避免任何调用方在不知情时写到生产）。
        now: 判定用的墙钟；None 取 `time.time()`。
        archive_path / pending_path / dead_path / ledger_path: 覆盖默认路径（单测用）。
        dry_run: True 只算不写（返回同样结构的统计）。
        max_age_days: 超龄不回收。
        max_recover: 单次上限。

    Returns:
        ``{"status", "shape", "archive_rows", "pending_before", "pending_after",
        "recovered", "recovered_ids", "stats", "ledger", "reason", "dry_run"}``

        ``status`` ∈ ``disabled`` / ``no_archive`` / ``nothing_to_recover`` /
        ``dry_run`` / ``written`` / ``write_failed`` / ``skipped_bad_pending``
    """
    _now = float(time.time() if now is None else now)
    _out: dict[str, Any] = {
        "status": "no_archive", "shape": "unknown", "archive_rows": 0,
        "pending_before": 0, "pending_after": 0, "recovered": 0,
        "recovered_ids": [], "stats": {}, "ledger": "", "reason": "",
        "dry_run": bool(dry_run),
    }
    if not enabled():
        _out["status"] = "disabled"
        _out["reason"] = "switch_off"
        return _out
    if not root:
        _out["status"] = "no_archive"
        _out["reason"] = "root_required"
        return _out
    _archive = archive_path or os.path.join(root, _ARCHIVE_REL)
    _pending = pending_path or os.path.join(root, _PENDING_REL)
    _dead = dead_path or os.path.join(root, _DEAD_REL)
    _ledger = ledger_path or os.path.join(root, _LEDGER_REL)
    _out["ledger"] = _ledger

    _doc = _m423_k3_load(_archive)
    if _doc is None:
        _out["reason"] = "archive_missing_or_unreadable"
        return _out
    _rows, _shape = flatten_archive(_doc)
    _out["shape"] = _shape
    _out["archive_rows"] = len(_rows)

    _pend_ids, _pend_rows = _m423_k3_pending_ids(_pending)
    if _pend_rows and not _pend_ids:
        # pending 存在但一条都没解析出 id ⇒ 形状异常，宁可不动
        _out["status"] = "skipped_bad_pending"
        _out["reason"] = "pending_shape_unrecognized"
        return _out
    _out["pending_before"] = len(_pend_rows)

    _dead_ids = _m423_k3_dead_ids(_dead)
    _picked, _st = select_recoverable(_rows, _now, pending_ids=_pend_ids,
                                      dead_ids=_dead_ids,
                                      max_age_days=max_age_days,
                                      max_recover=max_recover)
    _out["stats"] = _st
    _out["recovered"] = len(_picked)
    _out["recovered_ids"] = [str(x.get("id") or "") for x in _picked]

    if not _picked:
        _out["status"] = "nothing_to_recover"
        _out["pending_after"] = len(_pend_rows)
        if dry_run:
            return _out
        _m423_k3_append_ledger(_ledger, {
            "at": round(_now, 3), "at_str": time.strftime("%Y-%m-%d %H:%M:%S",
                                                          time.localtime(_now)),
            "version": _M423_K3_VERSION, "status": "nothing_to_recover",
            "shape": _shape, "archive_rows": len(_rows), "recovered": 0,
            "stats": _st,
        })
        return _out

    if dry_run:
        _out["status"] = "dry_run"
        return _out

    _new_rows = list(_pend_rows)
    for _p in _picked:
        _new_rows.append(_m423_k3_stamp(_p, _now))

    _doc_p = _m423_k3_load(_pending)
    _payload: Any = _new_rows
    if isinstance(_doc_p, dict):
        _payload = dict(_doc_p)
        if "patches" in _doc_p:
            _payload["patches"] = _new_rows
        elif "items" in _doc_p:
            _payload["items"] = _new_rows
        else:
            _payload = _new_rows
    if not _m423_k3_write_json(_pending, _payload):
        _out["status"] = "write_failed"
        _out["reason"] = "pending_write_failed"
        return _out
    _out["status"] = "written"
    _out["pending_after"] = len(_new_rows)

    _m423_k3_append_ledger(_ledger, {
        "at": round(_now, 3), "at_str": time.strftime("%Y-%m-%d %H:%M:%S",
                                                      time.localtime(_now)),
        "version": _M423_K3_VERSION, "status": "written",
        "shape": _shape, "archive_rows": len(_rows),
        "recovered": len(_picked), "recovered_ids": _out["recovered_ids"],
        "pending_before": _out["pending_before"], "pending_after": len(_new_rows),
        "stats": _st,
    })
    _module_logger.info(
        "[423刀3] 归档回收：%d 条重新入池（archive %d 条 / %s，pending %d→%d）：%s",
        len(_picked), len(_rows), _shape, _out["pending_before"],
        len(_new_rows), ",".join(_out["recovered_ids"])[:200])
    return _out


def version() -> str:
    """契约版本。"""
    return _M423_K3_VERSION
