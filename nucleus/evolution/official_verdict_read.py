# -*- coding: utf-8 -*-
"""★349刀2（P1 · 裁决①落地）：`official_verdict` 的**只读**接线三处。

## 裁决① 原文与本文档的关系

措辞是「**先写后读**」：判定结果必须先落盘，读侧才能全只读零副作用。
349 之前 `official_verdict` 只活在 `o3_open_gate_report()` 的返回值里，
`health_ui`（独立 HTTP 进程）与日报（另一条链）都读不到 —— 于是"官方结论"
事实上只有调用它的人看得见。本批补的就是这条路：
    `o3_open_gate_report()` 末尾落盘 → 本模块三处只读入口。

## 三处读点（全部只读、零副作用）

  1. 日报：`open_gate_line()` → 一行 `open_gate=… judge=… official=…`
  2. health_ui：`official_row()` → 结构化行（进 evolution 页数据）
  3. 问题池：`gate_field()` → 每个 entry 的 `gate` 字段（供自知柱面周报，零新采集）

## ★NOT_RUN 语义钉死（烛微349审计2）

    `NOT_RUN = 判据面**未跑**`（不是"跑了但没过"，也不是"读不到"）。

    三条硬约束（写进代码，不靠纪律）：
      · **禁止计入 UNAVAILABLE 计数**：把"没跑"混进"读不到"会污染误报统计，
        让"判据面一直没跑"看起来像"环境坏了"；
      · **禁止在 NOT_RUN 时给"未过"叙事**：`open_gate_line()` 对 NOT_RUN 输出
        `official=NOT_RUN(未跑)`，绝不输出"未通过/不合格"；
      · NOT_RUN 只能由**判定链自己**如实写入，读侧**不得**把"文件缺失"翻译成
        NOT_RUN（缺文件是 `ABSENT`，另一种事实）。

## ★写侧禁令（同一份代码里的结构化清单，可 grep 可断言）

`official_verdict` 是**读数**，不是**开关**。禁止：
  · 由它自动触发入池 / 派单 / 调档；
  · 用 `OPEN` 替代 `judge_action_open`（两者是**独立**的闸，不能互相顶替）；
  · `OPEN` 且判死面 `False` 时**不许只印一个**（必须 `gate=OPEN judge=CLOSED` 双印）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc

#: ★判定值域。`ABSENT` 是**读侧**的事实（文件不存在），不是判定链的值。
VERDICT_OPEN = "OPEN"
VERDICT_CLOSED = "CLOSED"
VERDICT_UNAVAILABLE = "UNAVAILABLE"
VERDICT_NOT_RUN = "NOT_RUN"
VERDICT_ABSENT = "ABSENT"

VERDICTS = (VERDICT_OPEN, VERDICT_CLOSED, VERDICT_UNAVAILABLE,
            VERDICT_NOT_RUN, VERDICT_ABSENT)

#: 计数分档：NOT_RUN 与 UNAVAILABLE **必须分开**（审计2 的核心要求）
COUNT_BUCKETS = (VERDICT_OPEN, VERDICT_CLOSED, VERDICT_UNAVAILABLE,
                 VERDICT_NOT_RUN, VERDICT_ABSENT)

#: 落盘路径（相对项目根）。★可被 config.OFFICIAL_VERDICT_PATH 覆盖（隔离验证用）
DEFAULT_REL = os.path.join("data", "reports", "gate", "official_verdict.json")
SCHEMA = "official_verdict/349.v1"

#: ★写侧禁令清单（结构化，供自检脚本/报告直接引用；`grep WRITE_SIDE_BANS`）
WRITE_SIDE_BANS = (
    ("no_auto_intake",
     "禁止由 official_verdict 自动触发入池（含问题池/待修池）"),
    ("no_auto_dispatch",
     "禁止由 official_verdict 自动触发派单（O3/自动分诊/生成器）"),
    ("no_auto_retune",
     "禁止由 official_verdict 自动调档（阈值/max_hot/预算/限额）"),
    ("no_open_replaces_judge",
     "禁止用 OPEN 替代 judge_action_open —— 两者是独立闸，不得互相顶替"),
    ("must_print_both",
     "OPEN 且判死面 False 时必须双字段并印（gate=OPEN judge=CLOSED）"),
    ("not_run_not_unavailable",
     "禁止把 NOT_RUN 计入 UNAVAILABLE 计数"),
    ("not_run_no_fail_narrative",
     "禁止在 NOT_RUN 时给\"未过/不合格\"叙事"),
)


def _cfg(name: str, default: Any = None) -> Any:
    try:
        import config as _c

        return getattr(_c, name, default)
    except Exception as _e:
        silent_exc(_e, where=f"official_verdict_read::_cfg {name}", level="debug")
        return default


def verdict_path(project_root: str | None = None) -> str:
    """判定结果落盘路径（`project_root` 可注入，铁律158）。"""
    _rel = str(_cfg("OFFICIAL_VERDICT_PATH", "") or "") or DEFAULT_REL
    if os.path.isabs(_rel):
        return _rel
    _root = project_root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(str(_root), _rel)


def read_verdict(project_root: str | None = None) -> dict:
    """读最近一次官方判定（**只读**，不触发任何判定）。

    返回 `{state, verdict, judge_action_open, not_run_steps, step7, source, why}`。
    `state` ∈ `ok` / `absent` / `unreadable`。
    ★"文件不存在"⇒ `verdict=ABSENT`，**不翻译成 NOT_RUN**（那是判定链的事实）。
    """
    _p = verdict_path(project_root)
    _out: dict = {"state": "absent", "verdict": VERDICT_ABSENT,
                  "judge_action_open": None, "not_run_steps": [],
                  "step7": None, "source": _p, "why": "file_absent",
                  "as_of": None}
    if not os.path.isfile(_p):
        return _out
    try:
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (ValueError, OSError) as _e:
        silent_exc(_e, where="official_verdict_read::read_verdict", level="debug")
        _out["state"] = "unreadable"
        _out["why"] = "read_error:%s" % type(_e).__name__
        return _out
    if not isinstance(_d, dict):
        _out["state"] = "unreadable"
        _out["why"] = "not_a_dict"
        return _out
    _v = str(_d.get("official_verdict") or "").strip().upper()
    if _v not in VERDICTS:
        # 值不在域内 ⇒ 如实报 unreadable，**不猜**成最接近的那档
        _out["state"] = "unreadable"
        _out["why"] = "verdict_out_of_domain:%s" % _v
        return _out
    _out.update({
        "state": "ok", "verdict": _v,
        "judge_action_open": _d.get("judge_action_open"),
        "not_run_steps": list(_d.get("not_run_steps") or []),
        "step7": _d.get("step7"),
        "as_of": _d.get("as_of"),
        "proc_gen": _d.get("proc_gen"),
        "why": "ok",
    })
    return _out


def open_gate_line(project_root: str | None = None,
                   row: dict | None = None) -> str:
    """★读点①（日报）：一行状态串。

    形如：`open_gate=OPEN judge=CLOSED official=OPEN proc_gen=pid…@…`
    ★`must_print_both`：`gate` 与 `judge` **永远同时出现**，绝不只印一个。
    ★NOT_RUN / ABSENT 用不同措辞，且**不给"未过"叙事**。
    """
    _r = row if isinstance(row, dict) else read_verdict(project_root)
    _v = str(_r.get("verdict") or VERDICT_ABSENT)
    _j = _r.get("judge_action_open")
    _j_txt = {True: "OPEN", False: "CLOSED"}.get(_j, "UNKNOWN")
    if _v == VERDICT_NOT_RUN:
        _tail = "NOT_RUN(判据面未跑)"
    elif _v == VERDICT_ABSENT:
        _tail = "ABSENT(无落盘结论)"
    elif _v == VERDICT_UNAVAILABLE:
        _tail = "UNAVAILABLE(判据不可用)"
    else:
        _tail = _v
    _nr = _r.get("not_run_steps") or []
    _nr_txt = (" not_run=%d" % len(_nr)) if _nr else ""
    _pg = _r.get("proc_gen")
    _pg_txt = (" proc_gen=%s" % _pg) if _pg else ""
    return ("open_gate=%s judge=%s official=%s%s%s"
            % (_v, _j_txt, _tail, _nr_txt, _pg_txt))


def official_row(project_root: str | None = None,
                 row: dict | None = None) -> dict:
    """★读点②（health_ui）：结构化行（前端直接渲染，不需要再解析字符串）。"""
    _r = row if isinstance(row, dict) else read_verdict(project_root)
    _v = str(_r.get("verdict") or VERDICT_ABSENT)
    _j = _r.get("judge_action_open")
    return {
        "official": _v,
        "judge": {True: "OPEN", False: "CLOSED"}.get(_j, "UNKNOWN"),
        "judge_action_open": _j,
        "is_not_run": _v == VERDICT_NOT_RUN,
        "narrative": ("判据面未跑（不是未通过）" if _v == VERDICT_NOT_RUN
                      else ("无落盘结论" if _v == VERDICT_ABSENT
                            else ("" if _v in (VERDICT_OPEN, VERDICT_CLOSED)
                                  else "判据不可用"))),
        "not_run_steps": list(_r.get("not_run_steps") or []),
        "step7": _r.get("step7"),
        "as_of": _r.get("as_of"),
        "proc_gen": _r.get("proc_gen"),
        "source": _r.get("source"),
        "state": _r.get("state"),
    }


def gate_field(project_root: str | None = None,
               row: dict | None = None) -> str:
    """★读点③（问题池 entries）：单值 `gate` 字段。

    为什么池条目也要带：自知柱面周报要能按 `gate` 分组，
    而"零新采集"意味着**不能**为它单独跑一次判定 ⇒ 只能读现成的。
    """
    _r = row if isinstance(row, dict) else read_verdict(project_root)
    return str(_r.get("verdict") or VERDICT_ABSENT)


def verdict_counts(rows: list) -> dict:
    """★NOT_RUN 口径钉死的**唯一实现**：分档计数。

    `rows` 里每项可以是 verdict 字符串或含 `verdict` 的 dict。
    `unavailable` **不含** `not_run` —— 这是审计2 要求的硬分离。
    """
    _c = {k: 0 for k in COUNT_BUCKETS}
    for _x in rows or []:
        _v = _x if isinstance(_x, str) else str((_x or {}).get("verdict") or "")
        _v = _v.strip().upper()
        if _v in _c:
            _c[_v] += 1
        else:
            # 域外值单独记，**不并入任何一档**（并入就是把未知当已知）
            _c.setdefault("out_of_domain", 0)
            _c["out_of_domain"] += 1
    _c["unavailable_excludes_not_run"] = True
    _c["note"] = "NOT_RUN 与 UNAVAILABLE 分档计数；unavailable 不含 not_run"
    return _c


def is_write_side_banned(action: str) -> tuple:
    """写侧禁令判定：`(是否被禁, 命中条目)`。

    `action` 取 `WRITE_SIDE_BANS` 里的 id。未登记的 action ⇒ `(False, None)`
    （不臆造禁令；但调用方应当只传登记过的 id）。
    """
    for _k, _desc in WRITE_SIDE_BANS:
        if _k == action:
            return True, {"id": _k, "desc": _desc}
    return False, None


def ban_ids() -> tuple:
    """禁令 id 清单（自检/报告直接引用，避免文字与代码分叉）。"""
    return tuple(_k for _k, _ in WRITE_SIDE_BANS)


# ========== ★写侧：判定链落盘（唯一的写口）==========


def persist_verdict(report: dict, project_root: str | None = None,
                    *, now: float | None = None) -> dict:
    """把 `o3_open_gate_report` 的结果**精简落盘**（判定链唯一写口）。

    ★这不是"由 official_verdict 触发动作"，而是"把判定结果本身留案"。
      `WRITE_SIDE_BANS` 禁的是**动作**，不是"记录结论"。
    ★写盘走 `.tmp` → `os.replace` → 读回（铁律182），并**上报 persisted**（铁律198）。
    """
    _out = {"ok": False, "persisted": False, "path": verdict_path(project_root),
            "why": ""}
    if not isinstance(report, dict):
        _out["why"] = "report_not_dict"
        return _out
    _v = str(report.get("official_verdict") or "").strip().upper()
    if _v not in VERDICTS:
        _out["why"] = "verdict_out_of_domain:%s" % _v
        return _out
    _now = float(now if now is not None else time.time())
    _body = {
        "schema": SCHEMA,
        "as_of": _now,
        "official_verdict": _v,
        "official_reason": str(report.get("official_reason") or "")[:500],
        "judge_action_open": report.get("judge_action_open"),
        "not_run_steps": list(report.get("not_run_steps") or [])[:40],
        "step7": report.get("step7"),
        "unjudged_steps": list(report.get("unjudged_steps") or [])[:20],
        "proc_gen": str(report.get("proc_gen") or "unknown"),
        "write_side_bans": [k for k, _ in WRITE_SIDE_BANS],
    }
    _p = _out["path"]
    try:
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
        _tmp = _p + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(_body, _f, ensure_ascii=False, indent=1)
        os.replace(_tmp, _p)
        with open(_p, encoding="utf-8") as _f:
            _back = json.load(_f)
        _out["persisted"] = bool(_back.get("official_verdict") == _v)
        _out["ok"] = _out["persisted"]
        _out["why"] = "saved" if _out["persisted"] else "readback_mismatch"
    except Exception as _e:
        silent_exc(_e, where="official_verdict_read::persist_verdict", level="debug")
        _out["why"] = "%s: %s" % (type(_e).__name__, str(_e)[:200])
    return _out
