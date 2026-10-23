# -*- coding: utf-8 -*-
"""verify_feedback.py —— 验证回灌环（350 批刀1 · 烛微 96 号）

**断点在哪**（350 开工前实测复核）：破零闭环五段 `消费→审批→应用→验证→回灌`
前四段都通，唯独第五段没有实现 ——
  · `audit_problem_pool_consumer.consume_pool()` 只把池条目推到 `claimed` 就结束；
  · `EvolutionEffectVerifier.verify()` 写出 `effect_verify.json`（verdict）；
  · 两者之间**没有任何代码**把 verdict 写回池条目。
于是池条目永远停在 `claimed`，"修好了没有"在池里读不出来 —— 破零统计因此
只能数"派了多少"，数不出"真修好几条"。

**本模块补的就是这一段**：`effect_verify.json` →（映射）→ 问题池条目
（状态翻转 + `verify_feedback` 字段 + 历史留痕）。

三条纪律：
  1. **幂等**（刀1③）：回灌键 = `patch_id|verified_at|passed`。同一 verdict 重复
     回灌只记一次（`ledger`），且状态机本身就是幂等（`noop` 不计 changed）——
     两层各管一件事：ledger 防"同一份 verdict 被重复当成新证据"，
     状态机防"同一条目被推进两次"。
  2. **不可判定不写状态**：`passed is None`（三项都没测）时只记 `verify_feedback`，
     **不改 status** —— 把"没验出来"写成 `verified` 是白送一个修复率。
  3. **映射不上就报 unmap**，不按 `file+issue_type` 模糊抓一条来凑（抓错比不抓更糟：
     它会把一条无关条目判成已修，且从此不会再被派单）。
"""

import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("verify_feedback")

#: 回灌台账 schema（字段只增不改名）
FEEDBACK_SCHEMA = "evolution_verify_feedback/350.v1"

#: verdict 落盘文件名（与 `EvolutionEffectVerifier._save_path` 同名）
EFFECT_VERIFY_FILE = "effect_verify.json"

#: 回灌台账文件名
LEDGER_FILE = "verify_feedback_ledger.json"

#: 池补丁 id 前缀（`audit_problem_pool_consumer._route_patch_candidate` 用 `pool_`）
POOL_PATCH_PREFIX = "pool_"


def feedback_key(patch_id: Any, verified_at: Any, passed: Any) -> str:
    """回灌幂等键。**不含墙钟**：同一条 verdict 任何时候算出来都是同一个键。"""
    return "%s|%s|%s" % (str(patch_id or ""), "%r" % (verified_at,), "%r" % (passed,))


def _ledger_path(base_dir: str | None = None) -> str:
    _d = base_dir or os.path.join("data", "evolution")
    return os.path.join(_d, LEDGER_FILE)


def _load_ledger(base_dir: str | None = None) -> dict:
    _p = _ledger_path(base_dir)
    try:
        with open(_p, "r", encoding="utf-8") as _f:
            _d = json.load(_f)
        if not isinstance(_d, dict):
            return {"schema": FEEDBACK_SCHEMA, "applied": {}, "_read": "not_dict", "_path": _p}
        _d.setdefault("applied", {})
        _d["_read"] = "ok"
        _d["_path"] = _p
        return _d
    except FileNotFoundError:
        _module_logger.debug("[350回灌] 台账不存在 path=%s（首次运行）", _p)
        return {"schema": FEEDBACK_SCHEMA, "applied": {}, "_read": "absent", "_path": _p}
    except Exception as _e:                                   # noqa: BLE001
        silent_exc(_e, where="verify_feedback::_load_ledger")
        return {"schema": FEEDBACK_SCHEMA, "applied": {}, "_read": "unreadable", "_path": _p}


def _save_ledger(led: dict, base_dir: str | None = None) -> bool:
    """原子落盘（铁律182）；异常不外抛，返回值必须由调用方上报（铁律198）。"""
    _p = _ledger_path(base_dir)
    _body = {k: v for k, v in led.items() if not str(k).startswith("_")}
    try:
        _dir = os.path.dirname(_p)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        _tmp = _p + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(_body, _f, ensure_ascii=False, indent=1)
        os.replace(_tmp, _p)
        return True
    except Exception as _e:                                   # noqa: BLE001
        silent_exc(_e, where="verify_feedback::_save_ledger", level="warning")
        return False


def read_verdicts(base_dir: str | None = None) -> dict:
    """读 `effect_verify.json` 的 `verdicts`（读不到如实报状态，不返回空冒充"没有"）。"""
    _p = os.path.join(base_dir or os.path.join("data", "evolution"), EFFECT_VERIFY_FILE)
    try:
        with open(_p, "r", encoding="utf-8") as _f:
            _d = json.load(_f)
    except FileNotFoundError:
        _module_logger.debug("[350回灌] effect_verify 不存在 path=%s", _p)
        return {"state": "absent", "path": _p, "verdicts": {}, "mtime": None}
    except Exception as _e:                                   # noqa: BLE001
        silent_exc(_e, where="verify_feedback::read_verdicts")
        return {"state": "unreadable", "path": _p, "verdicts": {}, "mtime": None}
    if not isinstance(_d, dict):
        return {"state": "not_dict", "path": _p, "verdicts": {}, "mtime": None}
    _v = _d.get("verdicts")
    if not isinstance(_v, dict):
        return {"state": "no_verdicts", "path": _p, "verdicts": {}, "mtime": None}
    try:
        _mt = os.path.getmtime(_p)
    except OSError as _mte:
        _module_logger.debug("[350回灌] mtime 取不到: %s", _mte)
        _mt = None
    return {"state": "ok", "path": _p, "verdicts": _v, "mtime": _mt}


def _norm_rel(path: Any) -> str:
    _s = str(path or "").replace("\\", "/").strip()
    _i = _s.lower().find("tongtong-pulse-v9/")
    if _i >= 0:
        _s = _s[_i + len("tongtong-pulse-v9/"):]
    return _s.lstrip("./")


def map_verdict_to_entry(verdict: dict, entries: list) -> dict:
    """verdict → 池条目。

    三条映射路径，**按可靠度排序**，命中即停：
      1. `patch_id` 带 `pool_` 前缀 ⇒ 直取 `dedup_key`（最可靠：这是入池时写进去的）；
      2. 条目自身 `patch_id` 字段等于 verdict 的 `patch_id`；
      3. `(归一 file, issue_type)` 且**唯一**命中（多条同 file+type ⇒ `ambiguous`，不猜）。
    """
    _pid = str((verdict or {}).get("patch_id") or "")
    if _pid.startswith(POOL_PATCH_PREFIX):
        _key = _pid[len(POOL_PATCH_PREFIX):]
        for _e in entries:
            if str(_e.get("dedup_key")) == _key:
                return {"state": "ok", "by": "pool_prefix", "entry": _e}
        return {"state": "unmap", "by": "pool_prefix",
                "why": "前缀解出的 dedup_key=%s 在池中不存在" % _key}
    for _e in entries:
        if _pid and str(_e.get("patch_id") or "") == _pid:
            return {"state": "ok", "by": "entry_patch_id", "entry": _e}
    _f = _norm_rel((verdict or {}).get("file"))
    _t = str((verdict or {}).get("issue_type") or "")
    _hits = [_e for _e in entries
             if _norm_rel(_e.get("file")) == _f and str(_e.get("issue_type") or "") == _t]
    if len(_hits) == 1:
        return {"state": "ok", "by": "file_type", "entry": _hits[0]}
    if len(_hits) > 1:
        return {"state": "ambiguous", "by": "file_type",
                "why": "%s::%s 命中 %d 条（不猜）" % (_f, _t, len(_hits))}
    return {"state": "unmap", "by": "file_type",
            "why": "池中无 %s::%s" % (_f, _t)}


def _target_status(passed: Any) -> str | None:
    """verdict ⇒ 目标状态。`None`（不可判定）⇒ 不改状态。"""
    if passed is True:
        return "verified"
    if passed is False:
        return "open"
    return None


def apply_feedback(verdicts: dict, entries: list, *, ledger: dict | None = None,
                   pool=None, now: float | None = None,
                   dry_run: bool = False, root: str | None = None,
                   audit_dir_path: str | None = None) -> dict:
    """把一批 verdict 回灌进池。

    `pool` 需提供 `note_verify_feedback(dedup_key, **kw) -> dict`
    （`nucleus.audit_problem_pool` 的同名函数；用注入而不是 import，便于隔离）。
    """
    _now = float(now if now is not None else time.time())
    _led = ledger if isinstance(ledger, dict) else {"applied": {}}
    _applied = _led.setdefault("applied", {})
    _stats = {"scanned": 0, "written": 0, "skipped_dup": 0, "unmap": 0,
              "ambiguous": 0, "undetermined": 0, "noop": 0, "failed": 0}
    _rows: list = []
    for _pid, _v in (verdicts or {}).items():
        if not isinstance(_v, dict):
            continue
        _stats["scanned"] += 1
        _passed = _v.get("passed")
        _vat = _v.get("verified_at")
        _key = feedback_key(_pid, _vat, _passed)
        if _key in _applied:
            _stats["skipped_dup"] += 1
            continue
        _m = map_verdict_to_entry(_v, entries)
        if _m["state"] != "ok":
            _stats[_m["state"]] = int(_stats.get(_m["state"], 0)) + 1
            _rows.append({"patch_id": _pid, "state": _m["state"], "why": _m.get("why", "")})
            _module_logger.info("[350回灌] 未回灌 patch=%s 因=%s", _pid, _m.get("why", ""))
            continue
        _ent = _m["entry"]
        _want = _target_status(_passed)
        if _want is None:
            _stats["undetermined"] += 1
        if dry_run:
            _rows.append({"patch_id": _pid, "dedup_key": _ent.get("dedup_key"),
                          "state": "dry_run", "want": _want, "dry_run": True})
            continue
        # ★路径注入（铁律158/189）：隔离验证必须把 root / audit_dir_path 传透，
        #   否则 `note_verify_feedback` 的默认参数**就是生产路径**。
        _r = pool.note_verify_feedback(
            str(_ent.get("dedup_key")), patch_id=_pid, passed=_passed,
            verified_at=_vat, dims=_v.get("dims"), reason=_v.get("reason", ""),
            advance=(_want is not None), now=_now,
            root=root, audit_dir_path=audit_dir_path)
        if _r.get("state") in ("ok", "noop"):
            _stats["written" if _r.get("changed") else "noop"] += 1
            _applied[_key] = {"patch_id": _pid, "dedup_key": _ent.get("dedup_key"),
                              "passed": _passed, "at": _now,
                              "status_from": _r.get("from"), "status_to": _r.get("to")}
        else:
            _stats["failed"] += 1
        _rows.append({"patch_id": _pid, "dedup_key": _ent.get("dedup_key"),
                      "state": _r.get("state"), "from": _r.get("from"),
                      "to": _r.get("to"), "passed": _passed,
                      "by": _m["by"], "why": _r.get("why", "")})
    return {"stats": _stats, "rows": _rows}


def feedback_once(*, base_dir: str | None = None,
                  entries: list | None = None,
                  pool=None, now: float | None = None,
                  dry_run: bool = False, root: str | None = None,
                  audit_dir_path: str | None = None) -> dict:
    """跑一次完整回灌（读 verdict → 映射 → 写池 → 落台账）。

    `entries` / `pool` 缺省时按生产路径取（隔离验证必须显式传，铁律158/189）。
    """
    _rv = read_verdicts(base_dir)
    if _rv["state"] != "ok":
        return {"state": _rv["state"], "path": _rv["path"],
                "why": "effect_verify 读不到（不写池）", "stats": {}, "persisted": None}
    if entries is None or pool is None:
        try:
            from nucleus import audit_problem_pool as _app
            _pool_res = _app.load_pool(root, audit_dir_path, now)
            if _pool_res.get("state") != "ok":
                return {"state": "pool_" + str(_pool_res.get("state")),
                        "why": "池不可用（不写）", "stats": {}, "persisted": None}
            entries = _pool_res["entries"]
            pool = _app
        except Exception as _e:                               # noqa: BLE001
            silent_exc(_e, where="verify_feedback::feedback_once pool", level="warning")
            return {"state": "pool_error", "why": "%s: %s" % (type(_e).__name__, _e),
                    "stats": {}, "persisted": None}
    _led = _load_ledger(base_dir)
    _res = apply_feedback(_rv["verdicts"], list(entries), ledger=_led,
                          pool=pool, now=now, dry_run=dry_run,
                          root=root, audit_dir_path=audit_dir_path)
    _persisted = None
    if not dry_run:
        _persisted = _save_ledger(_led, base_dir)
        if not _persisted:
            _module_logger.warning(
                "[350回灌] 台账落盘失败（内存已记、文件未写，铁律198）path=%s",
                _ledger_path(base_dir))
    _st = _res["stats"]
    _module_logger.info(
        "[350回灌] 一轮 扫=%d 写=%d 幂等跳过=%d 映射不上=%d 歧义=%d 不可判=%d "
        "空转=%d 失败=%d 台账=%s verdict_mtime=%s",
        _st.get("scanned", 0), _st.get("written", 0), _st.get("skipped_dup", 0),
        _st.get("unmap", 0), _st.get("ambiguous", 0), _st.get("undetermined", 0),
        _st.get("noop", 0), _st.get("failed", 0),
        "ok" if _persisted else ("dry_run" if dry_run else "**failed**"),
        "%r" % (_rv.get("mtime"),))
    return {"state": "ok", "verdict_state": _rv["state"], "verdict_mtime": _rv.get("mtime"),
            "stats": _st, "rows": _res["rows"], "persisted": _persisted,
            "ledger_path": _ledger_path(base_dir), "ledger_read": _led.get("_read")}
