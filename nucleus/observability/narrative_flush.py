# -*- coding: utf-8 -*-
"""叙事补跑 + 每 6h flush（★422 批刀4）—— 让叙事不再是「一天一次、错过就空」。

T0 实测（本批最重要的前提修正）
------------------------------
任务书说「叙事只挂在 04:00，错过窗口就不补」。实测**不是"错过"**：

  * `data/reports/night_digest_state.json` 里 `last_run_ymd=20261102`、
    `last_added=114` ⇒ 11-02 凌晨那次夜间编排**跑了**；
  * 但 `data/narrative/` 下只有 `narrative_20261101.jsonl`
    （mtime 11-02 03:51、`date=20261101`、`generated_at=1793562680`）。

真因是**两套日戳口径**并存，各自都对，合起来才出这个现象：

  ============  ==========================================  ==================
  用途          表达式                                      11-02 04:00 的值
  ============  ==========================================  ==================
  夜间编排幂等  `now.strftime("%Y%m%d")`（**日历日**）        ``20261102``
  叙事落盘日    `digest_anchor(now)`（**摘要日**）           ``20261101``
  ============  ==========================================  ==================

`narrative_digest.DIGEST_SETTLE_HOUR = 6`，04:00 跑时 `tm_hour=4 < 6`
⇒ 锚点回退一天 ⇒ 落**前一天**的文件。后果：
`narrative_YYYYMMDD.jsonl` 要等到**次日凌晨**才出现，白天查「今天有没有叙事」
永远是空的，`continuous_days` 也跟着慢一天。

本刀补三件事
------------
1. **启动补跑**（`maybe_catchup_on_boot`）：按**摘要日**判断「该落的那天有没有
   行」，没有且当前 <12:00 ⇒ 立刻补一次（任务书：错过 04:00 时启动后自动补）。
2. **每 6h flush**（`flush_tick`）：10:00 / 16:00 / 22:00 三个槽位，
   与 04:00 夜间编排合计 4 拍/天。**槽位制**：同一槽当天只跑一次，
   中间漏掉的槽不追（下一拍覆盖同样的事件，追了只是白烧 CPU）。
3. **append-only 审计账**（`data/narrative/flush_history.jsonl`）：
   `persist_daily` 是「**替换**当天那一行」（`_kept = [r for r in _old if ...]`），
   一天多次 flush **不会多出行** ⇒ 任务书验证项「目录下一天能出现多个时间点的
   flush 记录」**只能靠这张账**兑现，不能靠摘要文件行数。本刀明确选这条路，
   而不是把 `persist_daily` 改成追加多行 —— 后者会同时推翻
   `prev_row` / `summary` / `list_days` / `continuous_days` 的全部假设。

红线
----
* 不改 `persist_daily` / `row_of` / `summary` / `list_days` 的任何契约
  （仍是一天一行、日级幂等、替换语义）；
* 落盘仍走 `narrative_store.persist_from_organ()` —— 不自己写摘要 jsonl，
  **不 new 假器官**（new 出来的实例没有真叙事，写下去就是编造）；
* 失败一律带 `silent_exc` 留痕；**返回状态不靠日志**（日志只用于人看）。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("NarrativeFlush")

#: 版本号（与 422 批对齐；写进 state 文件便于回滚时判代次）
_M422_K4_VERSION = "422.1"

#: 灰度开关（关 ⇒ 入口直接返回 disabled，**零副作用**）
CATCHUP_SWITCH = "ENABLE_M422_NARRATIVE_CATCHUP"
FLUSH_SWITCH = "ENABLE_M422_NARRATIVE_FLUSH"

#: 摘要日结算小时 —— **本地兜底值**。真值优先从
#: `narrative_digest.DIGEST_SETTLE_HOUR` 读（单一事实源），读不到才用它。
_SETTLE_HOUR_FALLBACK = 6

#: flush 槽位（04:00 由既有夜间编排负责，**不在此列**，避免一天跑五次）
FLUSH_SLOTS: tuple[int, ...] = (10, 16, 22)

#: 补跑截止小时：任务书「当前时间 <12:00 才补」
CATCHUP_DEADLINE_HOUR = 12

#: 同一槽位的最大尝试次数（失败重试上限，防每拍重烧）
MAX_ATTEMPTS = 3

#: 状态与审计账相对项目根
_STATE_REL = os.path.join("data", "reports", "narrative_flush_state.json")
_LEDGER_REL = os.path.join("data", "narrative", "flush_history.jsonl")

#: 摘要文件命名（与 `narrative_store` 同口径；本模块**只读**它判「落没落」）
_NS_DIR_REL = os.path.join("data", "narrative")
_FILE_PREFIX = "narrative_"
_FILE_SUFFIX = ".jsonl"


# ---------------------------------------------------------------- 路径与开关

def _m422_k4_project_root() -> str:
    """项目根 = 本文件上溯三层（`nucleus/observability/x.py`）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _m422_k4_state_path(root: str | None = None) -> str:
    return os.path.join(os.path.abspath(root or _m422_k4_project_root()), _STATE_REL)


def _m422_k4_ledger_path(root: str | None = None) -> str:
    return os.path.join(os.path.abspath(root or _m422_k4_project_root()), _LEDGER_REL)


def _m422_k4_day_path(day: str, root: str | None = None) -> str:
    _dir = os.path.join(os.path.abspath(root or _m422_k4_project_root()), _NS_DIR_REL)
    return os.path.join(_dir, f"{_FILE_PREFIX}{day}{_FILE_SUFFIX}")


def _m422_k4_enabled(switch: str) -> bool:
    """灰度开关；缺省 True（config 读不到也当开 —— 与 412/413 同口径）。"""
    try:
        import config as _c
        return bool(getattr(_c, switch, True))
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_flush:_m422_k4_enabled", level="debug")
        return True


# ---------------------------------------------------------------- 日戳口径

def _m422_k4_settle_hour() -> int:
    """结算小时。优先读 `narrative_digest.DIGEST_SETTLE_HOUR`（单一事实源）。

    ★为什么不直接硬编码：两处硬编码迟早漂移 —— 那时补跑会与落盘错开一天，
      表现为「补跑了、但还是没有今天的文件」，比不补更难查。
    """
    try:
        from nucleus.observability import narrative_digest as _nd
        _h = getattr(_nd, "DIGEST_SETTLE_HOUR", None)
        if _h is not None:
            return int(_h)
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_flush:_m422_k4_settle_hour",
                   level="debug")
    return _SETTLE_HOUR_FALLBACK


def _m422_k4_day_stamp(now: float | None = None) -> str:
    return time.strftime("%Y%m%d", time.localtime(float(now if now is not None else time.time())))


def _m422_k4_digest_day(now: float | None = None) -> str:
    """**摘要日** —— 这一行摘要算哪一天（与 `narrative_digest.digest_anchor` 同口径）。

    `tm_hour < settle` ⇒ 前一天；否则当天。
    """
    _t = float(now if now is not None else time.time())
    try:
        if time.localtime(_t).tm_hour < _m422_k4_settle_hour():
            return _m422_k4_day_stamp(_t - 86400.0)
    except (OSError, OverflowError, ValueError) as _e:
        silent_exc(_e, where="observability.narrative_flush:_m422_k4_digest_day", level="debug")
    return _m422_k4_day_stamp(_t)


def _m422_k4_slot(now: float | None = None) -> str:
    """当前已过的最新 flush 槽位，形如 ``20261102T16``；未到 10:00 ⇒ ``""``。

    ★只取**最新**一个槽：10:00 漏了不单独追 —— 16:00 那拍覆盖的是同一批事件，
      追只会多写一次同样的行（`replaced=1`），对信息量零贡献。
    """
    _t = float(now if now is not None else time.time())
    try:
        _lt = time.localtime(_t)
    except (OSError, OverflowError, ValueError) as _e:
        silent_exc(_e, where="observability.narrative_flush:_m422_k4_slot", level="debug")
        return ""
    _passed = [s for s in FLUSH_SLOTS if _lt.tm_hour >= s]
    if not _passed:
        return ""
    return f"{time.strftime('%Y%m%d', _lt)}T{max(_passed):02d}"


# ---------------------------------------------------------------- 落盘判定

def _m422_k4_day_landed(day: str, root: str | None = None) -> tuple[bool, str]:
    """摘要日 `day` 是否**已落盘**（文件存在 **且** 里面真有 `date == day` 的行）。

    ★为什么不能只看文件存不存在：`persist_daily` 会「替换当天那行」，
      文件可能因别的日期（锚点不同的另一次跑）先建起来，里面却没有这个日期的行。
      只看 `os.path.exists` 会在这种情形下**误判已落盘 ⇒ 永不补跑**。

    Returns:
        ``(landed, reason)``；reason ∈ ``row_ok`` / ``file_missing`` /
        ``row_missing`` / ``read_failed:<Type>``
    """
    _path = _m422_k4_day_path(day, root=root)
    if not os.path.exists(_path):
        return False, "file_missing"
    _want = str(day)
    try:
        with open(_path, encoding="utf-8") as _f:
            for _ln in _f:
                _s = _ln.strip()
                if not _s:
                    continue
                try:
                    _d = json.loads(_s)
                except ValueError as _e_bad:
                    silent_exc(_e_bad,
                               where="observability.narrative_flush:_m422_k4_day_landed.badline",
                               level="debug")
                    continue
                if isinstance(_d, dict) and str(_d.get("date") or "") == _want:
                    return True, "row_ok"
    except OSError as _e:
        silent_exc(_e, where="observability.narrative_flush:_m422_k4_day_landed.read",
                   level="warning")
        return False, f"read_failed:{type(_e).__name__}"
    return False, "row_missing"


# ---------------------------------------------------------------- 状态与审计账

def _m422_k4_load_state(path: str | None = None) -> dict[str, Any]:
    """读状态文件；坏文件/缺文件都返回带版本的**空账**（不返回 None）。"""
    _p = path or _m422_k4_state_path()
    _blank: dict[str, Any] = {"version": _M422_K4_VERSION, "last_slot": "",
                              "last_ok_ts": 0.0, "catchup_ymd": "", "catchup_ok": False,
                              "attempts": {}, "last_result": {}}
    if not os.path.exists(_p):
        return _blank
    try:
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="observability.narrative_flush:_m422_k4_load_state",
                   level="warning")
        return _blank
    if not isinstance(_d, dict):
        return _blank
    _out = dict(_blank)
    _out.update(_d)
    if not isinstance(_out.get("attempts"), dict):
        _out["attempts"] = {}
    return _out


def _m422_k4_save_state(data: dict[str, Any], path: str | None = None) -> bool:
    """原子写状态（tmp + `os.replace`，**纯 LF**）。"""
    _p = path or _m422_k4_state_path()
    _tmp = _p + ".tmp422"
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(data, _f, ensure_ascii=False, indent=2)
            _f.write("\n")
        os.replace(_tmp, _p)
        return True
    except (OSError, TypeError, ValueError) as _e:
        silent_exc(_e, where="observability.narrative_flush:_m422_k4_save_state",
                   level="warning")
        try:
            if os.path.exists(_tmp):
                os.remove(_tmp)
        except OSError as _e2:
            silent_exc(_e2, where="observability.narrative_flush:_m422_k4_save_state.cleanup",
                       level="debug")
        return False


def _m422_k4_append_ledger(rec: dict[str, Any], path: str | None = None) -> bool:
    """flush 审计账：**append-only**（同一天多条 ⇒ 任务书的"多个时间点"由这里体现）。"""
    _p = path or _m422_k4_ledger_path()
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8", newline="\n") as _f:
            _f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError) as _e:
        silent_exc(_e, where="observability.narrative_flush:_m422_k4_append_ledger",
                   level="warning")
        return False


# ---------------------------------------------------------------- 真跑

def _m422_k4_run_persist(now: float | None = None) -> dict[str, Any]:
    """默认 runner：走 412/413 既有入口 `persist_from_organ(now=)`。

    ★**不自己写摘要 jsonl**：那条链里有 `row_of` 的字段契约、`digest` 的模板拼接、
      `persist_daily` 的日期校验 —— 绕过去就是另起一套，两套账迟早对不上。
    """
    try:
        from nucleus.observability import narrative_store as _ns
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_flush:_m422_k4_run_persist.import")
        return {"ok": False, "reason": f"import_failed:{type(_e).__name__}"}
    try:
        return dict(_ns.persist_from_organ(now=now) or {})
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_flush:_m422_k4_run_persist.call",
                   level="warning")
        return {"ok": False, "reason": f"persist_raised:{type(_e).__name__}"}


def _m422_k4_record(trigger: str, slot: str, day: str, res: dict[str, Any],
                    now: float, ledger_path: str | None = None) -> bool:
    """落一条审计账（**每次真跑都记**，成功失败都记）。"""
    _rec = {
        "ts": round(float(now), 3),
        "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "trigger": trigger,
        "slot": slot,
        "digest_day": day,
        "landed_day": res.get("day") or "",
        "ok": bool(res.get("ok")),
        "path": res.get("path") or "",
        "rows": res.get("rows"),
        "replaced": res.get("replaced"),
        "reason": res.get("reason") or "",
    }
    return _m422_k4_append_ledger(_rec, path=ledger_path)


# ---------------------------------------------------------------- 入口 1：启动补跑

def maybe_catchup_on_boot(now: float | None = None, root: str | None = None,
                          state_path: str | None = None, ledger_path: str | None = None,
                          runner: Callable[[float | None], dict[str, Any]] | None = None
                          ) -> dict[str, Any]:
    """启动时补跑（任务书：今天叙事没落盘且 <12:00 ⇒ 自动补一次）。

    Args:
        now:        注入时刻（测试用；缺省 `time.time()`）
        root:       项目根（隔离验证传隔离根 —— 路径全部随 root 走）
        state_path: 状态文件（缺省 `<root>/data/reports/narrative_flush_state.json`）
        ledger_path: 审计账（缺省 `<root>/data/narrative/flush_history.jsonl`）
        runner:     落盘执行器（单测注入假函数，避免碰真器官）

    Returns:
        六态可区分，**不填 0**：

          =================  ==================================================
          status             含义
          =================  ==================================================
          ``disabled``       开关关
          ``already_landed`` 摘要日那天的文件已有对应行（**无需补**）
          ``too_late``       当前 ≥12:00（任务书限定只补上午）
          ``catchup_done``   本代次已补过且成功（幂等，不重复跑）
          ``catchup_ran``    本次补跑成功
          ``catchup_failed`` 本次补跑失败（记录留待下次启动再试）
          =================  ==================================================
    """
    _now = float(now if now is not None else time.time())
    _day = _m422_k4_digest_day(_now)
    _out: dict[str, Any] = {"status": "", "trigger": "boot_catchup", "digest_day": _day,
                            "run": None, "reason": "", "landed": None}

    if not _m422_k4_enabled(CATCHUP_SWITCH):
        _out["status"] = "disabled"
        return _out

    _landed, _why = _m422_k4_day_landed(_day, root=root)
    _out["landed"] = _landed
    if _landed:
        _out["status"] = "already_landed"
        _out["reason"] = _why
        return _out

    _hour = time.localtime(_now).tm_hour
    if _hour >= CATCHUP_DEADLINE_HOUR:
        _out["status"] = "too_late"
        _out["reason"] = f"past_{CATCHUP_DEADLINE_HOUR}h(now_hour={_hour},missing={_why})"
        return _out

    _sp = state_path or _m422_k4_state_path(root)
    _st = _m422_k4_load_state(_sp)
    if str(_st.get("catchup_ymd") or "") == _day and _st.get("catchup_ok"):
        _out["status"] = "catchup_done"
        _out["reason"] = f"already_caught_up(day={_day})"
        return _out

    _res = (runner or _m422_k4_run_persist)(_now)
    _res = dict(_res or {})
    _ok = bool(_res.get("ok"))
    _st["catchup_ymd"] = _day
    _st["catchup_ok"] = _ok
    _st["catchup_at"] = _now
    _st["version"] = _M422_K4_VERSION
    _st["last_result"] = {"trigger": "boot_catchup", "ok": _ok,
                          "reason": _res.get("reason") or "", "ts": _now}
    _m422_k4_save_state(_st, _sp)
    _m422_k4_record("boot_catchup", "", _day, _res, _now,
                    ledger_path or _m422_k4_ledger_path(root))

    _out["status"] = "catchup_ran" if _ok else "catchup_failed"
    _out["run"] = _res
    _out["reason"] = _res.get("reason") or ""
    _module_logger.info(
        "[422刀4] 启动补跑 status=%s 摘要日=%s 落盘日=%s ok=%s reason=%s",
        _out["status"], _day, _res.get("day") or "-", _ok, _out["reason"] or "ok")
    return _out


# ---------------------------------------------------------------- 入口 2：槽位 flush

def flush_tick(now: float | None = None, root: str | None = None,
               state_path: str | None = None, ledger_path: str | None = None,
               runner: Callable[[float | None], dict[str, Any]] | None = None
               ) -> dict[str, Any]:
    """主循环每拍调用：到了槽位就 flush 一次（同日同槽**只跑一次**）。

    Returns:
        ``{status, slot, digest_day, run, reason}``；status ∈
        ``disabled`` / ``no_slot`` / ``already_flushed`` / ``give_up`` /
        ``flushed`` / ``flush_failed``
    """
    _now = float(now if now is not None else time.time())
    _day = _m422_k4_digest_day(_now)
    _out: dict[str, Any] = {"status": "", "slot": "", "digest_day": _day,
                            "run": None, "reason": ""}

    if not _m422_k4_enabled(FLUSH_SWITCH):
        _out["status"] = "disabled"
        return _out

    _slot = _m422_k4_slot(_now)
    _out["slot"] = _slot
    if not _slot:
        _out["status"] = "no_slot"
        _out["reason"] = f"before_first_slot({FLUSH_SLOTS[0]:02d}:00)"
        return _out

    _sp = state_path or _m422_k4_state_path(root)
    _st = _m422_k4_load_state(_sp)
    if str(_st.get("last_slot") or "") == _slot:
        _out["status"] = "already_flushed"
        _out["reason"] = f"slot_done({_slot})"
        return _out

    _att = dict(_st.get("attempts") or {})
    _n = int(_att.get(_slot) or 0)
    if _n >= MAX_ATTEMPTS:
        _out["status"] = "give_up"
        _out["reason"] = f"attempts>={MAX_ATTEMPTS}(slot={_slot})"
        return _out

    _res = (runner or _m422_k4_run_persist)(_now)
    _res = dict(_res or {})
    _ok = bool(_res.get("ok"))
    if _ok:
        _st["last_slot"] = _slot
        _st["last_ok_ts"] = _now
        _st["attempts"] = {}          # 成功即清零：账只在失败时非空，规模恒定
    else:
        _att[_slot] = _n + 1
        _st["attempts"] = _att
    _st["version"] = _M422_K4_VERSION
    _st["last_result"] = {"trigger": "slot", "slot": _slot, "ok": _ok,
                          "reason": _res.get("reason") or "", "ts": _now}
    _m422_k4_save_state(_st, _sp)
    _m422_k4_record("slot", _slot, _day, _res, _now,
                    ledger_path or _m422_k4_ledger_path(root))

    _out["status"] = "flushed" if _ok else "flush_failed"
    _out["run"] = _res
    _out["reason"] = _res.get("reason") or ""
    _module_logger.info(
        "[422刀4] 周期 flush status=%s 槽=%s 摘要日=%s 落盘日=%s ok=%s reason=%s",
        _out["status"], _slot, _day, _res.get("day") or "-", _ok, _out["reason"] or "ok")
    return _out


# ---------------------------------------------------------------- 只读快照

def get_m422_flush_state(root: str | None = None) -> dict[str, Any]:
    """只读快照（给健康面 / 运维排查用；**不触发任何写**）。"""
    _root = root or _m422_k4_project_root()
    _st = _m422_k4_load_state(_m422_k4_state_path(_root))
    _now = time.time()
    _day = _m422_k4_digest_day(_now)
    _landed, _why = _m422_k4_day_landed(_day, root=_root)
    return {
        "version": _M422_K4_VERSION,
        "enabled": {"catchup": _m422_k4_enabled(CATCHUP_SWITCH),
                    "flush": _m422_k4_enabled(FLUSH_SWITCH)},
        "now_digest_day": _day,
        "now_slot": _m422_k4_slot(_now),
        "today_landed": _landed,
        "today_landed_reason": _why,
        "last_slot": _st.get("last_slot") or "",
        "last_ok_ts": _st.get("last_ok_ts") or 0.0,
        "catchup_ymd": _st.get("catchup_ymd") or "",
        "catchup_ok": bool(_st.get("catchup_ok")),
        "attempts": dict(_st.get("attempts") or {}),
        "last_result": dict(_st.get("last_result") or {}),
        "state_path": _m422_k4_state_path(_root),
        "ledger_path": _m422_k4_ledger_path(_root),
        "slots": list(FLUSH_SLOTS),
        "settle_hour": _m422_k4_settle_hour(),
        "catchup_deadline_hour": CATCHUP_DEADLINE_HOUR,
    }


def version() -> str:
    return _M422_K4_VERSION
