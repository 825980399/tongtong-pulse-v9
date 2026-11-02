# -*- coding: utf-8 -*-
"""Step5a 叙事落盘（★412 批刀1）—— 让叙事**重启不断**。

问题（任务书 + T0 实测）
------------------------
`PulseNarrativeSelf`（`organs/identity/PulseNarrativeSelf.py`）目前：

  * 没有 `persist()` / `load()` 方法（全仓 0 处）；
  * `data/narrative/` 目录**不存在**；
  * `_narrative_events` 只在内存里（超 300 裁到 **150**）⇒ **重启即归零**，
    「连续存在了多少天」这件事根本没有事实来源。

★T0 推翻了任务书的两处措辞（见交付报告 §三）：

  1. 任务书点名的 `_generate_life_plan` **不存在**（真名 `_generate_life_stage_summary`
     / `_generate_life_story` / `_generate_weekly_report`）；
  2. 「阶段判定 `narrative_events<20/<50` 阈值」**也不存在** ——
     `_generate_life_stage_summary` 里 `total_events = len(self._narrative_events)`
     带 `# noqa: F841`（**算出来没用**），六维里"维度6 叙事事件丰富度"是空的。
     ⇒ 本刀不是"改锚点"，而是**把这条断掉的线真正接上**，且用**落盘口径**（不是内存数）。

产物
----
`data/narrative/narrative_YYYYMMDD.jsonl` —— 每天一个文件、**每天一条摘要行**，
append-only 语义（同一天重跑 ⇒ 替换当天那一行，不产生第二条，日级幂等）。

行格式（任务书指定）::

    {date, version, headline, delta{nodes,llm_ratio,patches,narrative_events},
     intent, prev_intent_verdict, evidence_refs, generated_at,
     narrative_events, continuous_days}
"""

from __future__ import annotations

import io
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("NarrativeStore")

#: ★414刀2：delta 从三格扩到四格（加 `narrative_events`）。
#: **前缀必须保持 "412."** —— 412 单测钉的是这个前缀，且本文件仍是 412 的产物。
_M412_NS_VERSION = "412.2"

#: ★414刀2：delta 第四格的灰度开关。关 ⇒ `delta` 退回 412 的三格（可秒级回退）。
EVENTS_SWITCH = "ENABLE_M414_NARRATIVE_EVENTS"

#: 相对数据根的落盘目录
NARRATIVE_REL = os.path.join("data", "narrative")

#: 文件名：`narrative_YYYYMMDD.jsonl`
FILE_PREFIX = "narrative_"
FILE_SUFFIX = ".jsonl"

#: `load_recent()` 默认回放天数（任务书：启动时回放最近 7 天）
DEFAULT_DAYS = 7

#: 回放条数上限（与 organ 侧 `_narrative_events` 的 150 保留对齐，不擅自放大）
DEFAULT_REPLAY_CAP = 150

#: 连续天数最多往前看多少天（防目录里堆了几年文件时无限回溯）
MAX_STREAK_LOOKBACK = 400

#: 灰度开关
SWITCH = "ENABLE_M412_NARRATIVE_STORE"

#: ★每天一行里最多带多少条**事件体**（不是条数，是内容）。
#:   为什么必须有：任务书说"load 恢复 `_narrative_events`"，但**每天一条摘要**
#:   只存计数的话重启后事件是空的 —— 摘要行必须同时带事件体，否则
#:   "重启不归零"这条验收根本过不了（本批 T0 发现的任务书内部矛盾）。
EVENTS_PER_DAY = 50

_DATA_DIR: str | None = None


def enabled() -> bool:
    """灰度开关 `ENABLE_M412_NARRATIVE_STORE`；缺省 True（本刀只加落盘与回放）。"""
    try:
        import config as _c
        return bool(getattr(_c, SWITCH, True))
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_store:enabled", level="debug")
        return True


def events_enabled() -> bool:
    """★414刀2：`delta` 里是否带第四格 `narrative_events`。

    为什么单独一个开关而不是复用 `ENABLE_M412_NARRATIVE_STORE`：
    关掉整个落盘会把叙事摘要一起关掉（波及面太大），而这里只是想
    **只回退 delta 契约**（万一有下游按三键解析）。
    """
    try:
        import config as _c
        return bool(getattr(_c, EVENTS_SWITCH, True))
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_store:events_enabled", level="debug")
        return True


def project_root() -> str:
    """项目根 = 本文件上溯三层（`nucleus/observability/x.py`）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_root() -> str:
    return _DATA_DIR or project_root()


def set_data_dir(path: str | None) -> None:
    """重定向**读与写**的根（两处同改 —— 409 批踩过只改一半的坑）。"""
    global _DATA_DIR
    _DATA_DIR = path


def narrative_dir() -> str:
    return os.path.join(data_root(), NARRATIVE_REL)


def day_stamp(now: float | None = None) -> str:
    return time.strftime("%Y%m%d", time.localtime(float(now if now is not None else time.time())))


def day_path(now: float | None = None) -> str:
    """当日文件路径。"""
    return os.path.join(narrative_dir(), "%s%s%s" % (FILE_PREFIX, day_stamp(now), FILE_SUFFIX))


def _day_path_of(day: str) -> str:
    return os.path.join(narrative_dir(), "%s%s%s" % (FILE_PREFIX, str(day), FILE_SUFFIX))


def _read_lines(path: str) -> tuple[list[dict], int]:
    """读一个 jsonl：坏行**跳过并计数**（不静默——计数会回到调用方）。"""
    _rows: list[dict] = []
    _bad = 0
    if not os.path.exists(path):
        return _rows, 0
    try:
        with io.open(path, encoding="utf-8") as _f:
            for _ln in _f:
                _s = _ln.strip()
                if not _s:
                    continue
                try:
                    _d = json.loads(_s)
                except ValueError as _e:
                    _bad += 1
                    silent_exc(_e, where="observability.narrative_store:_read_lines",
                               level="debug")
                    continue
                if isinstance(_d, dict):
                    _rows.append(_d)
                else:
                    _bad += 1
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_store:_read_lines:open", level="warning")
    return _rows, _bad


def _atomic_write_lines(path: str, rows: list[dict]) -> tuple[bool, str]:
    """原子写整个 jsonl（tmp + os.replace；**纯 LF**）。"""
    _dir = os.path.dirname(path)
    try:
        os.makedirs(_dir, exist_ok=True)
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_store:_atomic_write_lines:mkdir", level="debug")
        return False, "mkdir_failed:%s" % type(_e).__name__
    _tmp = path + ".tmp412"
    try:
        with io.open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            for _r in rows:
                _f.write(json.dumps(_r, ensure_ascii=False) + "\n")
        os.replace(_tmp, path)
        return True, ""
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_store:_atomic_write_lines:write",
                   level="warning")
        try:
            if os.path.exists(_tmp):
                os.remove(_tmp)
        except Exception as _e2:
            silent_exc(_e2, where="observability.narrative_store:_atomic_write_lines:cleanup",
                       level="debug")
        return False, "write_failed:%s" % type(_e).__name__


def row_of(date: str, generated_at: float | None = None, **fields: Any) -> dict[str, Any]:
    """构造一行摘要（任务书行格式；**未知字段不吞**，显式落进 `extra`）。

    ★412 时 `headline` / `delta` / `intent` 留空；413 起由
      `narrative_digest` 纯模板供给（不引入生成模型）；414 起 `delta` 增第 4 格
      `narrative_events`。每一格都是 cell 结构 `{cur, prev, delta, source, reason}`
      —— 取不到时**显式写 reason**，不填 0 假充。
    """
    _delta: dict[str, Any] = {
        "nodes": fields.pop("nodes", None),
        "llm_ratio": fields.pop("llm_ratio", None),
        "patches": fields.pop("patches", None),
    }
    # ★414刀2：第四格。参数名用 `delta_narrative_events` 是为了不与**顶层**的
    #   `narrative_events`（当日条数 int，`load()` 要读它）撞名 —— 两者语义不同：
    #   顶层 = 当日有多少条；这里 = 与前一日比多了/少了几条。
    if events_enabled():
        _delta["narrative_events"] = fields.pop("delta_narrative_events", None)
    _row: dict[str, Any] = {
        "date": str(date),
        "version": _M412_NS_VERSION,
        "headline": str(fields.pop("headline", "") or ""),
        "delta": _delta,
        "intent": str(fields.pop("intent", "") or ""),
        "prev_intent_verdict": fields.pop("prev_intent_verdict", None),
        "evidence_refs": list(fields.pop("evidence_refs", None) or []),
        "generated_at": round(float(generated_at if generated_at is not None else time.time()), 3),
        "narrative_events": fields.pop("narrative_events", None),
        "continuous_days": fields.pop("continuous_days", None),
    }
    # ★事件体（供 `load()` 回放）；超上限要**标记**，不许静默截断
    _evs = fields.pop("events", None)
    _evs = [x for x in (_evs or []) if isinstance(x, dict)]
    _row["events_truncated"] = len(_evs) > EVENTS_PER_DAY
    _row["events"] = _evs[-EVENTS_PER_DAY:]
    if fields:
        # 不认识的字段照实留下（宁可多一个键，也不静默丢事实）
        _row["extra"] = dict(fields)
    return _row


def persist_daily(row: dict[str, Any] | None = None, now: float | None = None,
                  **fields: Any) -> dict[str, Any]:
    """写/替换**当天那一行**（每天一条摘要 ⇒ 日级幂等）。

    Returns:
        ``{"ok", "path", "day", "rows", "replaced", "reason"}``
        ★`replaced` = 本次覆盖掉了几条当天旧行（正常 0，重跑 1）。
    """
    if not enabled():
        return {"ok": False, "path": day_path(now), "day": day_stamp(now), "rows": 0,
                "replaced": 0, "reason": "disabled"}
    _now = float(now if now is not None else time.time())
    _day = day_stamp(_now)
    _row = row if isinstance(row, dict) else row_of(_day, generated_at=_now, **fields)
    if str(_row.get("date") or "") != _day:
        # 行的日期与落盘日不符 ⇒ 拒绝（否则同一天会出现两个日期的行）
        return {"ok": False, "path": _day_path_of(_day), "day": _day, "rows": 0,
                "replaced": 0, "reason": "date_mismatch(row.date!=%s)" % _day}
    _path = _day_path_of(_day)
    _old, _bad = _read_lines(_path)
    _kept = [r for r in _old if str(r.get("date")) != _day]
    _replaced = len(_old) - len(_kept)
    _rows = _kept + [_row]
    _ok, _why = _atomic_write_lines(_path, _rows)
    if _ok:
        _module_logger.debug("[412刀1] 叙事落盘 day=%s rows=%d replaced=%d",
                             _day, len(_rows), _replaced)
    return {"ok": _ok, "path": _path, "day": _day, "rows": len(_rows) if _ok else len(_old),
            "replaced": _replaced, "reason": _why, "bad_lines": _bad}


def append_record(row: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """`persist_daily` 的同义入口（对外说"append-only"时用）。"""
    return persist_daily(row=row, now=now)


def prev_row(now: float | None = None) -> dict[str, Any]:
    """取**前一天**的叙事行（没有文件/没有行 ⇒ 返回 `{}`）。

    ★413刀3：`persist_daily` 只读写当天文件，而 Step5b 的 `delta` / `intent`
      需要「当日 vs 前一日」的对照 —— 日间对比必须显式跨文件读，不能靠内存。
    """
    _now = float(now if now is not None else time.time())
    _p = _day_path_of(day_stamp(_now - 86400.0))
    if not os.path.exists(_p):
        return {}
    _rows, _bad = _read_lines(_p)
    if _bad:
        silent_exc(RuntimeError("prev_row_bad_lines=%d" % _bad),
                   where="observability.narrative_store:prev_row", level="debug")
    return _rows[-1] if _rows else {}


def list_days(now: float | None = None, lookback: int = MAX_STREAK_LOOKBACK) -> list[str]:
    """从今天往前**有文件**的日期列表（升序）。"""
    _now = float(now if now is not None else time.time())
    _out: list[str] = []
    _t = _now
    for _ in range(max(1, int(lookback))):
        _d = day_stamp(_t)
        if os.path.exists(_day_path_of(_d)):
            _out.append(_d)
        _t -= 86400.0
    return sorted(_out)


def load_recent(days: int = DEFAULT_DAYS, now: float | None = None,
                cap: int = DEFAULT_REPLAY_CAP) -> dict[str, Any]:
    """回放最近 N 天的叙事行（启动时恢复用）。

    Returns:
        ``{"state": ok|empty|partial, "rows", "days_scanned", "days_with_file",
           "bad_lines", "truncated", "cap"}``

        ★`state="empty"` 与 `"partial"`（有文件但读不出内容）分开 ——
        "没落过盘"与"落了但坏了"是两件事。
    """
    _now = float(now if now is not None else time.time())
    _n = max(1, int(days))
    _rows: list[dict] = []
    _bad = 0
    _with_file = 0
    _t = _now
    for _ in range(_n):
        _d = day_stamp(_t)
        _p = _day_path_of(_d)
        if os.path.exists(_p):
            _with_file += 1
            _r, _b = _read_lines(_p)
            _rows.extend(_r)
            _bad += _b
        _t -= 86400.0
    _rows.sort(key=lambda r: float(r.get("generated_at") or 0.0))
    _trunc = False
    if cap and len(_rows) > int(cap):
        _rows = _rows[-int(cap):]
        _trunc = True
    if not _with_file:
        _state = "empty"
    elif not _rows:
        _state = "partial"
    else:
        _state = "ok"
    return {"state": _state, "rows": _rows, "days_scanned": _n,
            "days_with_file": _with_file, "bad_lines": _bad,
            "truncated": _trunc, "cap": int(cap) if cap else 0}


def continuous_days(now: float | None = None,
                    lookback: int = MAX_STREAK_LOOKBACK) -> dict[str, Any]:
    """从今天（或昨天）往前数**连续有记录**的天数。

    为什么允许从昨天起算：04:00 落盘的是**前一天**的摘要，当天文件可能还没生成；
    若只认今天，`continuous_days` 会在每天 00:00~04:00 之间恒为 0 —— 那是假归零。

    Returns:
        ``{"days", "anchor_date", "anchor_is_today", "gap_days", "reason"}``
        ★`gap_days` = 从今天到锚点的空档天数（0 = 今天有、1 = 今天没有但昨天有）。
    """
    _now = float(now if now is not None else time.time())
    _today = day_stamp(_now)
    _anchor = _today
    _gap = 0
    if not os.path.exists(_day_path_of(_today)):
        _anchor = day_stamp(_now - 86400.0)
        _gap = 1
        if not os.path.exists(_day_path_of(_anchor)):
            return {"days": 0, "anchor_date": _anchor, "anchor_is_today": False,
                    "gap_days": _gap, "reason": "no_record_today_or_yesterday"}
    _days = 0
    _t = _now - _gap * 86400.0
    for _ in range(max(1, int(lookback))):
        if not os.path.exists(_day_path_of(day_stamp(_t))):
            break
        _days += 1
        _t -= 86400.0
        if _days >= int(lookback):
            break
    return {"days": _days, "anchor_date": _anchor, "anchor_is_today": _gap == 0,
            "gap_days": _gap, "reason": ""}


def summary(now: float | None = None, days: int = DEFAULT_DAYS) -> dict[str, Any]:
    """紧凑读数（给 health_snapshot / 夜间摘要）。"""
    _r = load_recent(days=days, now=now)
    _c = continuous_days(now=now)
    _files = list_days(now=now)
    return {
        "state": _r["state"],
        "replayed_rows": len(_r["rows"]),
        "days_with_file": _r["days_with_file"],
        "continuous_days": _c["days"],
        "anchor_date": _c["anchor_date"],
        "gap_days": _c["gap_days"],
        "bad_lines": _r["bad_lines"],
        "total_day_files": len(_files),
        "dir": narrative_dir(),
    }


def find_narrative_organ() -> Any:
    """取活体 `PulseNarrativeSelf` 器官（**不 new、不启动**）。

    与 411刀1 的 `memory_by_organ._organ_map()` 同手法：从
    `SelfAwarenessEngine._AWARENESS_ORGANS` 里找。找不到返回 None ——
    **不 new 一个假的**：new 出来的实例没有真叙事，写下去就是编造。
    """
    try:
        from nucleus.self_awareness.SelfAwarenessEngine import _AWARENESS_ORGANS
        for _k, _o in dict(_AWARENESS_ORGANS or {}).items():
            if _o is None:
                continue
            if type(_o).__name__ == "PulseNarrativeSelf":
                return _o
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_store:find_narrative_organ",
                   level="debug")
    return None


def persist_from_organ(now: float | None = None) -> dict[str, Any]:
    """夜间 04:00 落盘入口：调活体器官的 `persist()`（**失败不编造**）。"""
    if not enabled():
        return {"ok": False, "reason": "disabled", "source": "switch"}
    _o = find_narrative_organ()
    if _o is None:
        return {"ok": False, "reason": "organ_missing（活体器官表里没有 PulseNarrativeSelf）",
                "source": "none"}
    if not hasattr(_o, "persist"):
        return {"ok": False, "reason": "organ_has_no_persist", "source": "organ"}
    try:
        _r = _o.persist(now=now)
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_store:persist_from_organ",
                   level="warning")
        return {"ok": False, "reason": "persist_failed:%s" % type(_e).__name__,
                "source": "organ"}
    _r = dict(_r) if isinstance(_r, dict) else {"ok": True}
    _r.setdefault("source", "organ")
    return _r


def version() -> str:
    return _M412_NS_VERSION
