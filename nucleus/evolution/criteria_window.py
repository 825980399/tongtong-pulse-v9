# -*- coding: utf-8 -*-
"""判据体系单一真相源（343 刀7 · 洞鉴第二季）。

本模块回答三个"以前每个脚本各算一遍"的问题，全仓只在这里算：

1. **窗口**：`get_criteria_window(criteria_version, proc_gen)` → `WIN_LO/WIN_HI`。
   336 教训：窗口常数在脚本里硬编码（03:55 vs 03:50）⇒ 两套口径同时"合格"。
   窗口必须**锚在被测对象的代次起点**（proc_gen），否则分母与被测对象不同域
   （铁律160）。

2. **合格判定**：`judge_round(...)` → 三段式 (A) 段2入池>0 ∧ (B) 收敛层未作废
   ∧ (C) executed>0。旧口径只看 (C)，于是"12 圈全部不合格"这个结论**碰巧正确**，
   但 (B) 从未被测过 —— 收敛链断了也会判成"只是没执行"。

3. **"不可判"升级**：`note_unjudged(...)` + `check_unjudged_alarm()`。
   洞鉴指出的第四类口径漂移是"'不可判'被误读为'没问题'"，所以不可判要
   **计数并升级成红色告警**（阈值 3），而不是在报表里留个空单元格。

设计约束：
  · 纯函数优先，模块级状态只用于"不可判计数"（进程内）；
  · 取不到数据一律 `None` + `state="unavailable"`，**绝不填 0**；
  · 本模块不改任何既有判据的真值/结论，只改"判据怎么算、怎么被复核"。
"""
from __future__ import annotations

import time
from datetime import datetime

from nucleus._silent_except import silent_exc

#: 压测圈窗默认长度（秒）。来源：336 实测 12 圈窗长 3594.6–3728.2s，
#: 中位 3626.7s；取 3625 作为"一圈"的标称值。**不要在脚本里再抄这个数**。
CRITERIA_WINDOW_DEFAULT_SEC = 3625.0

#: 已知判据版本。未登记的版本 ⇒ 窗口不可判（不猜）。
CRITERIA_VERSIONS = {
    "336.r12": {"window_sec": 3625.0, "note": "336 压测 12 圈（R01—R12）"},
    "335.cg.v1": {"window_sec": 3625.0, "note": "图版本 335.cg.v1 起的代次窗口"},
    "343.j3": {"window_sec": CRITERIA_WINDOW_DEFAULT_SEC,
               "note": "343 三段式判据（A∧B∧C）"},
}

#: 不可判升级为红色告警的阈值（洞鉴：不可判≥3 自动触发审计）
UNJUDGED_ALARM_THRESHOLD = 3

#: 不可判缓冲的保持时长（秒）。活体是长驻进程，**必须剪枝**：
#: 否则列表无限增长（内存）且累计到 3 条后告警永远为红（失去信息量）。
UNJUDGED_KEEP_SEC = 3600.0

_UNJUDGED: list = []


def _parse_proc_gen(proc_gen: str):
    """`pid<N>@YYYYMMDD-HHMMSS` → 起点 epoch 秒；解析不了返回 None（不填 0）。"""
    _s = str(proc_gen or "").strip()
    if not _s or "@" not in _s:
        return None
    _ts = _s.split("@", 1)[1]
    for _fmt in ("%Y%m%d-%H%M%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(_ts, _fmt).timestamp()
        except ValueError:
            continue
    return None


def get_criteria_window(criteria_version: str, proc_gen: str,
                        window_sec: float | None = None,
                        now: float | None = None) -> dict:
    """**全仓唯一**的判据窗口取数口。

    Args:
        criteria_version: 判据版本串（如 `336.r12` / `335.cg.v1` / `343.j3`）。
        proc_gen: 代次串（`pid<N>@YYYYMMDD-HHMMSS`），窗口锚点。
        window_sec: 显式窗口长度（不给 ⇒ 按版本查表，再不行用默认值）。
        now: 注入"当前时刻"（测试用；不给取 `time.time()`）。

    Returns:
        dict: `win_lo` / `win_hi` / `window_sec` / `state` / `basis` / `notes`
            - `state="ok"`：`win_lo`/`win_hi` 可用（**唯一可用来判判据的状态**）
            - `state="unavailable"`：`proc_gen` 不可解析 ⇒ 两值 `None`，**不填 0**
            - `state="unsupported_version"`：版本未登记且未显式给 `window_sec`
    """
    _now = float(now if now is not None else time.time())
    _ver = str(criteria_version or "").strip()
    _pg = str(proc_gen or "").strip()
    _notes = []

    _win = window_sec
    if _win is None:
        _meta = CRITERIA_VERSIONS.get(_ver)
        if _meta is None:
            return {"criteria_version": _ver, "proc_gen": _pg,
                    "win_lo": None, "win_hi": None, "window_sec": None,
                    "state": "unsupported_version", "basis": "version_table",
                    "notes": ["版本 %r 未登记且未显式给 window_sec ⇒ 不猜窗口" % _ver]}
        _win = float(_meta["window_sec"])
        _notes.append(_meta.get("note", ""))
    _win = float(_win)

    _lo = _parse_proc_gen(_pg)
    if _lo is None:
        return {"criteria_version": _ver, "proc_gen": _pg,
                "win_lo": None, "win_hi": None, "window_sec": _win,
                "state": "unavailable", "basis": "proc_gen",
                "notes": _notes + ["proc_gen 不可解析（非 pid<N>@YYYYMMDD-HHMMSS）"
                                   "⇒ 窗口不可判，不得填 0"]}
    _hi = _lo + _win
    if _now < _hi:
        _notes.append("窗口尚未走满（now < win_hi）：本圈为**进行中**，不是完整圈")
    if _now > _hi + _win:
        _notes.append("窗口已过期 >1 圈：读数不是本代次当前圈，注意跨圈拼接")
    return {"criteria_version": _ver, "proc_gen": _pg,
            "win_lo": _lo, "win_hi": _hi, "window_sec": _win,
            "state": "ok", "basis": "proc_gen", "notes": _notes}


def judge_round(seg2_intake, converge_voided, executed,
                criteria_version: str = "343.j3", proc_gen: str = "") -> dict:
    """三段式合格判据：**(A) 段2入池>0 ∧ (B) 收敛层未作废 ∧ (C) executed>0**。

    每段都返回**三态**（True / False / None=不可判），不用"0 当作没事"。
    `verdict`：`合格` / `不合格` / `不可判`（有 False ⇒ 不合格优先于不可判）。

    Args:
        seg2_intake: 段2 真入池条数（int 或 None）
        converge_voided: 同窗收敛层作废条数（int 或 None；0 = 未作废）
        executed: 段4 executed 次数（int 或 None）
    """
    def _tri(_v, _zero_ok=False):
        if _v is None:
            return None
        try:
            _n = int(_v)
        except (TypeError, ValueError) as _e:
            # 读数非数值 ⇒ 判**不可判**（None），不是判 False。
            # ★不许当成"不合格"：口径错配与真实缺口是两件事（铁律166）。
            silent_exc(_e, where="criteria_window::judge_round _tri")
            return None
        return _n > 0 if not _zero_ok else _n == 0

    _a = _tri(seg2_intake)                    # A: 入池 > 0
    _b = _tri(converge_voided, _zero_ok=True)  # B: 作废 == 0
    _c = _tri(executed)                        # C: executed > 0
    _segs = {"A": _a, "B": _b, "C": _c}
    _missing = [_k for _k, _v in _segs.items() if _v is False]
    _unjudged = [_k for _k, _v in _segs.items() if _v is None]

    if _missing:
        _verdict = "不合格"
    elif _unjudged:
        _verdict = "不可判"
    else:
        _verdict = "合格"
    return {"verdict": _verdict, "A": _a, "B": _b, "C": _c,
            "missing": _missing, "unjudged": _unjudged,
            "criteria_version": criteria_version, "proc_gen": proc_gen}


def note_unjudged(item: str, reason: str = "", now: float | None = None,
                  **fields) -> int:
    """登记一条"不可判"（同窗剪枝后返回条数，供调用方即时判断是否越线）。"""
    _now = float(now if now is not None else time.time())
    _UNJUDGED.append({"ts": _now, "item": str(item or ""),
                      "reason": str(reason or ""), "fields": fields})
    _cut = _now - UNJUDGED_KEEP_SEC
    _UNJUDGED[:] = [x for x in _UNJUDGED if float(x.get("ts") or 0.0) >= _cut]
    return len(_UNJUDGED)


def check_unjudged_alarm(threshold: int | None = None,
                         reset: bool = False) -> dict:
    """不可判是否越线 → 红色告警（洞鉴：不可判≥3 自动触发审计）。

    返回 `{"count","threshold","alarm","items","verdict"}`；
    `verdict ∈ {"CLEAR","RED_ALARM","UNAVAILABLE"}` —— 没有登记过任何判据时
    是 `UNAVAILABLE`（**不能读成 CLEAR**：没判过 ≠ 判过且没问题）。
    """
    _th = int(threshold if threshold is not None else UNJUDGED_ALARM_THRESHOLD)
    _n = len(_UNJUDGED)
    _items = [dict(x) for x in _UNJUDGED]
    if _n == 0:
        _v = "UNAVAILABLE"
        _alarm = False
    else:
        _alarm = _n >= _th
        _v = "RED_ALARM" if _alarm else "CLEAR"
    _out = {"count": _n, "threshold": _th, "alarm": _alarm,
            "verdict": _v, "items": _items}
    if reset:
        _UNJUDGED.clear()
    return _out


def reset_unjudged() -> None:
    """清空不可判缓冲（测试/新一圈开始时调用）。"""
    _UNJUDGED.clear()


def judge_rounds(rows, criteria_version: str = "343.j3",
                 proc_gen: str = "", threshold: int | None = None,
                 logger=None, emit_alarm: bool = True) -> dict:
    """批量判圈（336 12 圈那种）：逐圈三段式 + 汇总不可判 + 越线红色告警。

    Args:
        rows: `[{"round": "R01", "seg2_intake": 1, "converge_voided": 0,
                 "executed": 0}, ...]`
        logger: 传入则用其 `.error()` 打红色告警行；不传则仅返回（便于单测/隔离）。

    Returns:
        `{"rounds": [...], "n_qualified", "n_unqualified", "n_unjudged",
          "alarm": {...}, "verdict": ...}`
    """
    _rounds = []
    _nq = _nu = _nun = 0
    for _r in (rows or []):
        _one = judge_round(_r.get("seg2_intake"), _r.get("converge_voided"),
                           _r.get("executed"), criteria_version, proc_gen)
        _one["round"] = str(_r.get("round") or "")
        _rounds.append(_one)
        if _one["verdict"] == "合格":
            _nq += 1
        elif _one["verdict"] == "不合格":
            _nu += 1
        else:
            _nun += 1
        # ★逐**段**登记（不是逐圈）：某圈可能"C 段判死 ⇒ 整圈不合格"，
        #   但 B 段仍不可判。若只在整圈不可判时登记，B 的缺口就永远不进告警
        #   （336 实测：12 圈 B 全未采，却一条都不会被记）。
        for _seg in _one["unjudged"]:
            note_unjudged("%s:%s" % (_one["round"], _seg),
                          "该段读数未采集（None）")
    _alarm = check_unjudged_alarm(threshold)
    if emit_alarm and _alarm["alarm"] and logger is not None:
        try:
            logger.error(
                "[343判据][红色告警] 不可判 %d 条 ≥ 阈值 %d ⇒ 自动触发审计"
                "（items=%s；不可判 ≠ 没问题，禁止读成通过）",
                _alarm["count"], _alarm["threshold"],
                ",".join(str(x.get("item")) for x in _alarm["items"])[:200])
        except Exception as _le:                                  # noqa: BLE001
            # 打告警失败不影响返回：alarm 字段已随返回值带出，调用方仍可判定。
            silent_exc(_le, where="criteria_window::judge_rounds logger.error")
    return {"rounds": _rounds, "n_qualified": _nq,
            "n_unqualified": _nu, "n_unjudged": _nun,
            "alarm": _alarm,
            "verdict": ("合格" if (_nq == len(_rounds) and _rounds)
                        else ("不可判" if _nu == 0 and _nun else "不合格"))}
