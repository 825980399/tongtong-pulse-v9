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

import io
import os
import time
from datetime import datetime

from nucleus._silent_except import silent_exc

#: 压测圈窗默认长度（秒）。来源：336 实测 12 圈窗长 3594.6–3728.2s，
#: 中位 3626.7s；取 3625 作为"一圈"的标称值。**不要在脚本里再抄这个数**。
CRITERIA_WINDOW_DEFAULT_SEC = 3625.0

#: 已知判据版本。未登记的版本 ⇒ 窗口不可判（不猜）。
# ★344批刀4：表里的窗口长度**一律引用** `CRITERIA_WINDOW_DEFAULT_SEC`，
#   不再各自抄一遍同一个数值。原来 3 个版本各写一份字面量 ⇒ 改默认值时表里
#   两份会留在旧值，这就是"同一口径两份实现"的温床（本模块内部先自证）。
CRITERIA_VERSIONS = {
    "336.r12": {"window_sec": CRITERIA_WINDOW_DEFAULT_SEC,
                "note": "336 压测 12 圈（R01—R12）"},
    "335.cg.v1": {"window_sec": CRITERIA_WINDOW_DEFAULT_SEC,
                  "note": "图版本 335.cg.v1 起的代次窗口"},
    "343.j3": {"window_sec": CRITERIA_WINDOW_DEFAULT_SEC,
               "note": "343 三段式判据（A∧B∧C）"},
}

#: 不可判升级为红色告警的阈值（洞鉴：不可判≥3 自动触发审计）
UNJUDGED_ALARM_THRESHOLD = 3

# ==================== 段B（收敛层作废条数）数据源 · 344批刀2 ====================
#
# 343刀7 留下的是**判据**，不是**数据**：12 圈 B 段全 `None` ⇒ RED_ALARM。
# 本段补的是数据源，口径三条：
#
#   ① **结构化台账优先**：`data/reports/converge_voided.jsonl`（344 起由
#      收敛层作废点写入，append-only、带 ts/proc_gen）。
#      为什么必须有台账：作废行今天只进 `pulse.log`，而 pulse.log 是
#      10MB×5 的滚动文件 —— 336 期（10-21 13:00→10-22 01:08）的日志
#      **已经轮转消失**，事后重放无从谈起。只靠日志＝数据永远补不上。
#   ② **日志只作回退**：台账覆盖不到的时间段，才回退扫 `logs/pulse*.log*`，
#      且必须先证明**日志覆盖该窗口**（首末时间戳跨过 win_lo/win_hi），
#      覆盖不到就是 `unavailable`，**不猜 0**（铁律156）。
#   ③ **只数逐条行**：`[284刀5] 结构缺陷补丁作废: <file>:<method> ...`。
#      批量汇总行 `批量作废: N 条` 不数 —— 数了就是同一次作废记两笔
#      （铁律155：归因计数 ≠ 分母）。

#: 台账相对路径（生产唯一写入口见 `note_converge_voided`）。
CONVERGE_VOIDED_LEDGER_REL = os.path.join("data", "reports", "converge_voided.jsonl")

#: 逐条件废行标记（**不含**批量汇总行）。
CONVERGE_VOIDED_MARKER = "[284刀5] 结构缺陷补丁作废"

#: 日志回退扫描上限（防长驻进程每拍把几 MB 日志全扫一遍）。
_VOIDED_LOG_SCAN_MAX_FILES = 12
_VOIDED_LOG_SCAN_MAX_LINES = 400000

#: 日志行时间戳格式（`nucleus.logger` 落盘格式，本地时区无偏移）。
_LOG_TS_FMT = "%Y-%m-%d %H:%M:%S"

#: 覆盖判定的容差（秒）：轮转接缝处常有毫秒级空隙，不算"没覆盖"。
_COVERAGE_TOL_SEC = 2.0


def _cw_repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def converge_voided_ledger_path(path: str | None = None) -> str:
    """台账路径（`path` 仅供隔离验证注入，铁律158）。"""
    if path:
        return path
    return os.path.join(_cw_repo_root(), CONVERGE_VOIDED_LEDGER_REL)


def note_converge_voided(patch_id, file: str = "", method: str = "",
                         reason: str = "", path: str | None = None,
                         now: float | None = None, **fields) -> str:
    """**写**一条作废台账（append-only JSONL）。返回写入路径；失败返回空串。

    由收敛层作废点（`PatchManager._m280_converge_pending`）调用 —— 这是把
    "作废只进日志、日志一滚就没了"补成"作废有结构化台账"的那一刀。
    写失败**不影响作废主流程**（作废本身照常发生），只留 warning。
    """
    _p = converge_voided_ledger_path(path)
    _rec = {"ts": float(now if now is not None else time.time()),
            "patch_id": str(patch_id or "")[:64],
            "file": str(file or ""),
            "method": str(method or ""),
            "reason": str(reason or "")[:200],
            "proc_gen": "unknown"}
    try:
        from nucleus.proc_gen import proc_gen_str as _pgs
        _rec["proc_gen"] = str(_pgs() or "unknown")
    except Exception as _e:                                    # noqa: BLE001
        silent_exc(_e, where="criteria_window::note_converge_voided proc_gen",
                   level="debug")
    _rec.update(fields)
    try:
        _dir = os.path.dirname(os.path.abspath(_p))
        if _dir and not os.path.isdir(_dir):
            os.makedirs(_dir, exist_ok=True)
        with io.open(_p, "a", encoding="utf-8", newline="\n") as _f:
            _f.write(_json_dumps(_rec) + "\n")
    except OSError as _e:
        silent_exc(_e, where="criteria_window::note_converge_voided", level="warning")
        return ""
    return _p


def _json_dumps(obj) -> str:
    import json as _json
    return _json.dumps(obj, ensure_ascii=False)


def read_converge_voided_ledger(path: str | None = None) -> list:
    """读台账（坏行跳过并计数，**绝不因台账坏了判 0**）。"""
    import json as _json
    _p = converge_voided_ledger_path(path)
    _rows = []
    _bad = 0
    try:
        if not os.path.isfile(_p):
            return []
        with io.open(_p, encoding="utf-8", errors="ignore") as _f:
            for _ln in _f:
                _ln = _ln.strip()
                if not _ln:
                    continue
                try:
                    _d = _json.loads(_ln)
                except ValueError as _e:
                    silent_exc(_e, where="criteria_window::read_ledger bad_line",
                               level="debug")
                    _bad += 1
                    continue
                if isinstance(_d, dict) and _d.get("ts") is not None:
                    _rows.append(_d)
    except OSError as _e:
        silent_exc(_e, where="criteria_window::read_ledger", level="warning")
        return []
    if _bad:
        # 坏行只影响计数精度，不影响"有没有台账"这个事实，故不并入条数。
        silent_exc(ValueError("bad_rows=%d" % _bad),
                   where="criteria_window::read_ledger 坏行统计", level="debug")
    return _rows


def _log_candidates(root: str | None = None) -> list:
    """候选日志文件（只收 pulse 系：生产事件流；pytest.log 是测试流，不算生产）。"""
    _root = root or _cw_repo_root()
    _out = []
    for _sub in ("logs", os.path.join("logs", "archive")):
        _d = os.path.join(_root, _sub)
        if not os.path.isdir(_d):
            continue
        try:
            _names = sorted(os.listdir(_d))
        except OSError as _e:
            silent_exc(_e, where="criteria_window::_log_candidates listdir",
                       level="debug")
            continue
        for _n in _names:
            if not _n.startswith("pulse"):
                continue
            if not (".log" in _n):
                continue
            _p = os.path.join(_d, _n)
            if os.path.isfile(_p):
                _out.append(_p)
    return _out


def _parse_log_ts(line: str):
    """行首 `YYYY-MM-DD HH:MM:SS` → epoch（本地时区）；**不是时间戳行**返回 None。

    先用**形状校验**挡掉绝大多数"本来就不是时间戳"的行（日志正文绝大多数如此），
    只把真正形状对但值非法的极少数交给 strptime；异常仍留 debug 痕迹
    （铁律150：纯 return 的 handler 一律判静默 ⇒ 必须有上报）。
    """
    _s = line[:19]
    if (len(_s) != 19 or _s[4] != "-" or _s[7] != "-" or _s[10] != " "
            or _s[13] != ":" or _s[16] != ":"):
        return None
    try:
        return datetime.strptime(_s, _LOG_TS_FMT).timestamp()
    except ValueError as _e:
        silent_exc(_e, where="criteria_window::_parse_log_ts", level="debug")
        return None


def _scan_log_range(path: str) -> dict:
    """返回该日志的时间戳跨度与行数（只读首尾块，不整读）。"""
    _out = {"path": path, "first": None, "last": None, "lines": 0}
    try:
        _size = os.path.getsize(path)
    except OSError as _e:
        silent_exc(_e, where="criteria_window::_scan_log_range stat", level="debug")
        return _out
    _head_n = 200
    _tail_bytes = 65536
    try:
        with io.open(path, encoding="utf-8", errors="ignore") as _f:
            for _i, _ln in enumerate(_f):
                _ts = _parse_log_ts(_ln)
                if _ts is not None:
                    _out["first"] = _ts
                    break
                if _i > _head_n:
                    break
        with io.open(path, "rb") as _fb:
            _fb.seek(max(0, _size - _tail_bytes))
            _tail = _fb.read().decode("utf-8", "ignore").splitlines()
        for _ln in reversed(_tail):
            _ts = _parse_log_ts(_ln)
            if _ts is not None:
                _out["last"] = _ts
                break
        with io.open(path, "rb") as _fb:
            _out["lines"] = sum(1 for _ in _fb)
    except OSError as _e:
        silent_exc(_e, where="criteria_window::_scan_log_range read", level="debug")
    return _out


def _count_marker_in(path: str, lo: float, hi: float) -> dict:
    """在某日志里数窗口内的**逐条件废行**。"""
    _n = 0
    _scanned = 0
    try:
        with io.open(path, encoding="utf-8", errors="ignore") as _f:
            for _ln in _f:
                _scanned += 1
                if _scanned > _VOIDED_LOG_SCAN_MAX_LINES:
                    break
                if CONVERGE_VOIDED_MARKER not in _ln:
                    continue
                if "批量作废" in _ln:      # 汇总行，不数（否则同一次作废记两笔）
                    continue
                _ts = _parse_log_ts(_ln)
                if _ts is None:
                    continue
                if lo <= _ts <= hi:
                    _n += 1
    except OSError as _e:
        silent_exc(_e, where="criteria_window::_count_marker_in", level="debug")
    return {"count": _n, "lines_scanned": _scanned}


def _merge_intervals(iv: list) -> list:
    _iv = sorted([x for x in iv if x and x[0] is not None and x[1] is not None])
    if not _iv:
        return []
    _out = [list(_iv[0])]
    for _lo, _hi in _iv[1:]:
        if _lo <= _out[-1][1] + _COVERAGE_TOL_SEC:
            _out[-1][1] = max(_out[-1][1], _hi)
        else:
            _out.append([_lo, _hi])
    return [(_a, _b) for _a, _b in _out]


def _coverage_of(intervals: list, lo: float, hi: float) -> bool:
    for _a, _b in intervals:
        if _a <= lo + _COVERAGE_TOL_SEC and _b >= hi - _COVERAGE_TOL_SEC:
            return True
    return False


def count_converge_voided(win_lo, win_hi, ledger_path: str | None = None,
                          log_paths=None, root: str | None = None) -> dict:
    """**全仓唯一**的"窗口内收敛层作废条数"取数口。

    Returns:
        dict: `state` / `count` / `source` / `reason` / `coverage` / `files`
            - `state="ok"`：`count` 是**有依据的**整数（可以是 0 —— 前提是
              证据覆盖了整个窗口）
            - `state="unavailable"`：`count=None`，`reason` 说明为什么判不了
              （窗口不合法 / 无台账且日志已轮转出留存窗 / 日志未覆盖窗口）

    ★"数到 0" 与 "没数据" 是两件事（铁律156/166）：只有**证据覆盖窗口**
    时才允许返回 0；覆盖不到必须 `unavailable`。
    """
    if win_lo is None or win_hi is None:
        return {"state": "unavailable", "count": None, "source": "none",
                "reason": "窗口未给出（win_lo/win_hi 均为 None）⇒ 无法界定统计域",
                "coverage": None, "files": []}
    _lo = float(win_lo)
    _hi = float(win_hi)
    if _hi <= _lo:
        return {"state": "unavailable", "count": None, "source": "none",
                "reason": "窗口非正（win_hi<=win_lo）⇒ 统计域无效",
                "coverage": None, "files": []}

    _notes = []
    _files = []
    _total = 0
    _covered_lo = None

    # ① 台账（结构化，优先）
    _rows = read_converge_voided_ledger(ledger_path)
    _led = [r for r in _rows if _lo <= float(r.get("ts") or 0.0) <= _hi]
    _lpath = converge_voided_ledger_path(ledger_path)
    if os.path.isfile(_lpath):
        _all_ts = [float(r.get("ts") or 0.0) for r in _rows] or None
        if _all_ts:
            _covered_lo = min(_all_ts)
            _notes.append("台账首条 ts=%.0f（覆盖起点）" % _covered_lo)
        else:
            _notes.append("台账存在但无有效行 ⇒ 视同未覆盖")
        _total += len(_led)
        _files.append({"kind": "ledger", "path": _lpath,
                       "rows_in_window": len(_led), "rows_total": len(_rows)})

    # ② 日志回退（只补台账覆盖不到的 [win_lo, min(win_hi, 台账起点)) ）
    _need_log = (_covered_lo is None) or (_covered_lo > _lo + _COVERAGE_TOL_SEC)
    _log_end = _hi if _covered_lo is None else min(_hi, _covered_lo)
    _log_cov = []
    _log_hits = 0
    if _need_log and _log_end > _lo:
        _paths = log_paths if log_paths is not None else _log_candidates(root)
        _paths = list(_paths)[:_VOIDED_LOG_SCAN_MAX_FILES]
        for _p in _paths:
            _rng = _scan_log_range(_p)
            _log_cov.append((_rng["first"], _rng["last"]))
            _files.append({"kind": "log", "path": _p,
                           "first": _rng["first"], "last": _rng["last"],
                           "lines": _rng["lines"]})
        _iv = _merge_intervals(_log_cov)
        if _coverage_of(_iv, _lo, _log_end):
            for _p in _paths:
                _c = _count_marker_in(_p, _lo, _log_end)
                if _c["count"]:
                    _log_hits += _c["count"]
                    _notes.append("%s 命中 %d 条"
                                  % (os.path.basename(_p), _c["count"]))
            _total += _log_hits
        else:
            _notes.append("日志未覆盖 [%.0f, %.0f]（合并覆盖=%s）"
                          % (_lo, _log_end,
                             [(round(a), round(b)) for a, b in _iv] or "无带时间戳日志"))
            if _log_hits == 0 and not _led:
                return {"state": "unavailable", "count": None, "source": "none",
                        "reason": ("无台账，且日志留存已轮转出该窗口 ⇒ "
                                   "既无数到 0 的证据，也不能猜 0"),
                        "coverage": {"intervals": [(round(a), round(b))
                                                   for a, b in _iv],
                                     "window": [round(_lo), round(_hi)]},
                        "files": _files, "notes": _notes}
            if not _led:
                return {"state": "unavailable", "count": None, "source": "none",
                        "reason": "台账未覆盖窗口起点且日志缺口未补齐 ⇒ 不许猜 0",
                        "coverage": {"intervals": [(round(a), round(b))
                                                   for a, b in _iv],
                                     "window": [round(_lo), round(_hi)]},
                        "files": _files, "notes": _notes}

    # `source` 反映**谁提供了覆盖证据**，而不是"谁产出了行"：
    # 窗内 0 条时两侧都没行，但覆盖起点来自台账 ⇒ 判据来源仍是台账。
    # （344 刀2 附：原写法在"台账覆盖起点成立 + 窗内 0 条"时错报成 log。）
    if _led and _log_hits:
        _src = "ledger+log"
    elif _led:
        _src = "ledger"
    elif _log_hits:
        _src = "log"
    else:
        _src = "ledger" if _covered_lo is not None else "log"
    return {"state": "ok", "count": int(_total), "source": _src,
            "reason": "台账 %d 条 + 日志回退 %d 条（逐条行，不含批量汇总行）"
                      % (len(_led), _log_hits),
            "coverage": {"window": [round(_lo), round(_hi)]},
            "files": _files, "notes": _notes}


def judge_round_auto(win_lo, win_hi, seg2_intake, executed,
                     criteria_version: str = "343.j3", proc_gen: str = "",
                     ledger_path: str | None = None, root: str | None = None) -> dict:
    """三段式判圈的**自动取数**版本：B 段由 `count_converge_voided` 取。

    与 `judge_round` 的唯一差别：不给 `converge_voided`，改为自己从唯一数据源取；
    取不到（`unavailable`）⇒ B 段 `None` ⇒ 该圈 `不可判`（**不是"合格"**）。
    """
    _b = count_converge_voided(win_lo, win_hi, ledger_path=ledger_path, root=root)
    _bv = _b.get("count") if _b.get("state") == "ok" else None
    _out = judge_round(seg2_intake, _bv, executed, criteria_version, proc_gen)
    _out["B_source"] = _b
    return _out

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
                        now: float | None = None,
                        include_converge_voided: bool = False,
                        ledger_path: str | None = None) -> dict:
    """**全仓唯一**的判据窗口取数口。

    Args:
        criteria_version: 判据版本串（如 `336.r12` / `335.cg.v1` / `343.j3`）。
        proc_gen: 代次串（`pid<N>@YYYYMMDD-HHMMSS`），窗口锚点。
        window_sec: 显式窗口长度（不给 ⇒ 按版本查表，再不行用默认值）。
        now: 注入"当前时刻"（测试用；不给取 `time.time()`）。
        include_converge_voided: True 时**顺带**取该窗口的段B读数（344批刀2）。
            **默认 False**：窗口函数会被报表高频调用，日志回退扫描不该搭在
            每次取窗口上；需要 B 段的调用方显式要（`judge_round_auto` 就要）。
        ledger_path: 段B台账路径注入（隔离验证用）。

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
    _out = {"criteria_version": _ver, "proc_gen": _pg,
            "win_lo": _lo, "win_hi": _hi, "window_sec": _win,
            "state": "ok", "basis": "proc_gen", "notes": _notes}
    if include_converge_voided:
        # ★344批刀2：段B（收敛层作废条数）**从窗口函数里取**，保证"窗口"与
        #   "该窗口的作废数"永远同一口径（铁律160：分母须与被测对象同域）。
        _out["converge_voided"] = count_converge_voided(
            _lo, _hi, ledger_path=ledger_path)
    return _out


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
