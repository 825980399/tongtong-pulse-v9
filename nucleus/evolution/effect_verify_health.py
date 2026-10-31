# -*- coding: utf-8 -*-
"""效果验证（effect_verify）健康读数（★411 批刀2）。

T0 实测：任务书的诊断**被推翻**
------------------------------
任务书说「`PatchManager` 里 effect_verify 调用被 try/except 吞了，导致 10-25 后停更」。

实测三件事都不成立：

1. `config.ENABLE_EVOLUTION_EFFECT_VERIFY = True`（开关是开的）；
2. 调用点在 `PatchManager.py:5170`，`except` 分支是
   `_module_logger.warning(...)` —— **不是静默**；
3. 真正断的是**上游供给**：`patch_history.json` 87 条里最近一次 `applied=True`
   是 **10-14 16:40**，之后全失败；`data/audit/failed_patches/` 里 10-31 的三条
   失败原因全是 **stage 2a 入库前静态闸**（`try 块不平衡`）——
   **补丁根本没走到 apply**，自然不会有新的 effect_verify。

所以本模块不"恢复触发"（它没坏），而是把**"效果验证到底停在哪一环"**变成可读读数：

  * :func:`derive_state` —— 单条补丁的派生状态
    `passed / failed / unverifiable / missing`（**由 verdicts 反查，不改既有字段**）；
  * :func:`stale_info` —— `effect_verify.json` 的 `updated` 多久没动（>24h 即 stale）；
  * :func:`snapshot_block` —— 给 `health_snapshot` 的 `world_model.effect_verify_stale`；
  * :func:`summarize_history` —— 全量分布（passed/failed/unverifiable/missing 各几条）。

语义纪律
--------
* `missing` 与 `unverifiable` 是**两件事**：前者"从没验证过"，后者"验证过但判不了"
  （`passed is None`）。合并会让人以为"全都没验过"。
* `stale` 只在**能读到 `updated`** 时判定；读不到 ⇒ `None`（不填 False 冒充"不旧"）。
"""

from __future__ import annotations

import io
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("EffectVerifyHealth")

_M411_EV_VERSION = "411.2"

#: 判定新鲜期（任务书：超过 24h 未变即 stale）
STALE_AFTER_SEC = 86400.0

#: 派生状态四值（不同事实不同措辞）
STATE_PASSED = "passed"
STATE_FAILED = "failed"
STATE_UNVERIFIABLE = "unverifiable"
STATE_MISSING = "missing"
ALL_STATES: tuple[str, ...] = (STATE_PASSED, STATE_FAILED,
                               STATE_UNVERIFIABLE, STATE_MISSING)

_DATA_DIR: str | None = None


def enabled() -> bool:
    """灰度开关 `ENABLE_M411_EFFECT_VERIFY_HEALTH`；缺省 True（纯读数，无副作用）。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M411_EFFECT_VERIFY_HEALTH", True))
    except Exception as _e:
        silent_exc(_e, where="evolution.effect_verify_health:enabled")
        return True


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_root() -> str:
    return _DATA_DIR or os.path.join(project_root(), "data")


def set_data_dir(path: str | None) -> None:
    global _DATA_DIR
    _DATA_DIR = path


def verify_path() -> str:
    return os.path.join(data_root(), "evolution", "effect_verify.json")


def history_path() -> str:
    return os.path.join(data_root(), "patches", "patch_history.json")


def _read_json(path: str) -> Any:
    try:
        return json.loads(io.open(path, encoding="utf-8").read())
    except Exception as _e:
        silent_exc(_e, where="evolution.effect_verify_health:_read_json", level="debug")
        return None


def load_verdicts(path: str | None = None) -> dict[str, dict]:
    """读 `effect_verify.json` 的 `verdicts`（真实形态是 **dict**，键为 patch_id）。"""
    _d = _read_json(path or verify_path())
    if not isinstance(_d, dict):
        return {}
    _v = _d.get("verdicts")
    if isinstance(_v, dict):
        return {str(k): (x if isinstance(x, dict) else {}) for k, x in _v.items()}
    if isinstance(_v, list):
        # 兼容 list 形态（老版本/其他写入方）
        _out = {}
        for _x in _v:
            if isinstance(_x, dict):
                _pid = str(_x.get("patch_id") or _x.get("id") or "")
                if _pid:
                    _out[_pid] = _x
        return _out
    return {}


def derive_state(patch: Any, verdicts: dict[str, dict]) -> tuple[str, str]:
    """单条补丁的派生状态。

    Returns:
        ``(state, reason)``。顺序：先看补丁自带 verdict 字段，再反查 verdicts 表 ——
        因为这个字段是**派生**的，不能覆盖既有事实。
    """
    if not isinstance(patch, dict):
        return STATE_MISSING, "not_a_dict"
    _pid = str(patch.get("id") or "")
    # ① 老字段（`effect_verified` 是 no_regression 语义，缺省才算）
    _v = verdicts.get(_pid) if _pid else None
    if isinstance(_v, dict):
        _p = _v.get("passed")
        if _p is True:
            return STATE_PASSED, str(_v.get("reason") or "")[:80]
        if _p is False:
            return STATE_FAILED, str(_v.get("reason") or "")[:80]
        return STATE_UNVERIFIABLE, "verdict_passed_is_none"
    # ② 补丁上是否已有判定痕迹（不回读 verdicts 表也能判）
    if patch.get("effect_verified") is True:
        return STATE_PASSED, "patch.effect_verified=True（非 verdicts 表来源）"
    if patch.get("effect_verified") is False:
        return STATE_FAILED, "patch.effect_verified=False（非 verdicts 表来源）"
    if not patch.get("applied"):
        return STATE_MISSING, "patch_not_applied"
    return STATE_MISSING, "no_verdict_record"


def summarize_history(history: Any = None, verdicts: dict[str, dict] | None = None,
                      limit: int = 0) -> dict[str, Any]:
    """全量分布 + 最近应用时刻（"停更停在哪一环"的核心取证）。"""
    _h = history if isinstance(history, list) else _read_json(history_path())
    _rows = _h if isinstance(_h, list) else []
    if limit:
        _rows = _rows[:int(limit)]
    _v = verdicts if isinstance(verdicts, dict) else load_verdicts()
    _counts = {s: 0 for s in ALL_STATES}
    _applied = 0
    _last_applied_ts = 0.0
    for _p in _rows:
        _s, _ = derive_state(_p, _v)
        _counts[_s] = _counts.get(_s, 0) + 1
        if isinstance(_p, dict) and _p.get("applied") is True:
            _applied += 1
            try:
                _t = float(_p.get("applied_at") or _p.get("saved_at") or 0.0)
            except (TypeError, ValueError) as _e:
                silent_exc(_e, where="evolution.effect_verify_health:summarize_history:ts",
                           level="debug")
                _t = 0.0
            _last_applied_ts = max(_last_applied_ts, _t)
    return {
        "total": len(_rows),
        "applied": _applied,
        "states": _counts,
        "verdicts_total": len(_v),
        "last_applied_ts": round(_last_applied_ts, 3) if _last_applied_ts > 0 else None,
        "last_applied_at": (time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.localtime(_last_applied_ts))
                            if _last_applied_ts > 0 else ""),
        "note": ("`missing` = 从没验证过；`unverifiable` = 验过但判不了（passed is None）"
                 "—— 两者不可合并"),
    }


def stale_info(path: str | None = None, now: float | None = None,
               stale_after_sec: float = STALE_AFTER_SEC) -> dict[str, Any]:
    """`effect_verify.json` 的新鲜度。

    Returns:
        ``{"state": ok|stale|missing|broken|unknown, "updated": float|None,
           "age_sec": float|None, "stale": bool|None, "path": str, "reason": str}``

        ★`stale=None` 表示**判不了**（读不到 `updated`），不是"不旧"。
    """
    _p = path or verify_path()
    _out: dict[str, Any] = {"state": "missing", "updated": None, "age_sec": None,
                            "stale": None, "path": _p, "reason": ""}
    if not os.path.exists(_p):
        _out["reason"] = "file_not_found"
        return _out
    _d = _read_json(_p)
    if not isinstance(_d, dict):
        _out["state"] = "broken"
        _out["reason"] = "not_a_dict_or_unreadable"
        return _out
    try:
        _upd = float(_d.get("updated") or 0.0)
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="evolution.effect_verify_health:stale_info:updated", level="debug")
        _upd = 0.0
    if _upd <= 0:
        _out["state"] = "unknown"
        _out["reason"] = "updated_missing"
        return _out
    _now = float(now if now is not None else time.time())
    _age = round(max(0.0, _now - _upd), 1)
    _out.update({"updated": round(_upd, 3), "age_sec": _age,
                 "stale": bool(_age > float(stale_after_sec))})
    _out["state"] = "stale" if _out["stale"] else "ok"
    if _out["stale"]:
        _out["reason"] = "age_sec>%ss" % int(stale_after_sec)
    return _out


def snapshot_block(now: float | None = None) -> dict[str, Any]:
    """给 `health_snapshot.world_model` 的两三个字段（任务书要求 `effect_verify_stale`）。"""
    _s = stale_info(now=now)
    return {
        "effect_verify_stale": _s["stale"],
        "effect_verify_state": _s["state"],
        "effect_verify_age_sec": _s["age_sec"],
        "effect_verify_updated": _s["updated"],
    }


def annotate(history: Any = None, verdicts: dict[str, dict] | None = None,
             dry_run: bool = True, path: str | None = None) -> dict[str, Any]:
    """给 `patch_history.json` 补**派生字段** `effect_verify_state`（不动既有字段）。

    Returns:
        ``{"ok", "path", "scanned", "changed", "states", "written", "reason"}``
    """
    _p = path or history_path()
    _h = history if isinstance(history, list) else _read_json(_p)
    if not isinstance(_h, list):
        return {"ok": False, "path": _p, "scanned": 0, "changed": 0, "states": {},
                "written": False, "reason": "history_not_a_list"}
    _v = verdicts if isinstance(verdicts, dict) else load_verdicts()
    _changed = 0
    _counts: dict[str, int] = {s: 0 for s in ALL_STATES}
    for _row in _h:
        if not isinstance(_row, dict):
            continue
        _s, _why = derive_state(_row, _v)
        _counts[_s] = _counts.get(_s, 0) + 1
        if _row.get("effect_verify_state") != _s:
            _row["effect_verify_state"] = _s
            _row["effect_verify_state_reason"] = _why
            _changed += 1
    if dry_run or _changed == 0:
        return {"ok": True, "path": _p, "scanned": len(_h), "changed": _changed,
                "states": _counts, "written": False,
                "reason": "dry_run" if dry_run else "no_change"}
    _tmp = _p + ".tmp411"
    try:
        with io.open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_h, _f, ensure_ascii=False, indent=2)
        os.replace(_tmp, _p)
        return {"ok": True, "path": _p, "scanned": len(_h), "changed": _changed,
                "states": _counts, "written": True, "reason": ""}
    except Exception as _e:
        silent_exc(_e, where="evolution.effect_verify_health:annotate:write", level="warning")
        return {"ok": False, "path": _p, "scanned": len(_h), "changed": _changed,
                "states": _counts, "written": False,
                "reason": "write_failed:%s" % type(_e).__name__}


def version() -> str:
    return _M411_EV_VERSION
