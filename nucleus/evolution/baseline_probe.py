# -*- coding: utf-8 -*-
"""nucleus/evolution/baseline_probe.py —— 438 刀2：补丁**修复前基线**探针。

【为什么要它（实测盘面，2026-11-03）】
`data/patches/patch_history.json` 87 条里 `baseline_source` **0 条**；
`pending_patches.json` 9 条里 `baseline_errors=0` 的 **8 条**。
没有基线时 `after_fix == 0` 就判 `verified` —— 那是**自证**：
「跑完之后没再看到这个错」既可能是修好了，也可能是**本来就没在报**。

【口径】
    baseline_errors = 在 [now-window, now] 这段日志里，命中 (file, method) 的
                      **错误级**行数
    baseline_source = "log_window"    能观测到整段窗口 ⇒ 数值可信
                    = "unavailable"   窗口没有被日志完整覆盖 ⇒ 数值为 **None**

★三条纪律
1. **取不到就 None，绝不填 0**（任务书原文）。0 与「不知道」在判定链里语义相反：
   0 ⇒ 无错可修 ⇒ 不可判定；None ⇒ 同样不可判定。但**两者都不能被当成"修好了"**。
2. **日志没覆盖窗口 ≠ 窗口内没有错**。判据看日志首行时间戳是否早于窗口起点，
   以及日志是否仍在更新（`M438_BASELINE_LOG_FRESH_SEC`，缺省 1h）。
   这条是 46/47 批根因（日志轮转 ⇒ baseline 恒 0 ⇒ 无法复现被当成修复有效）的直接对策。
3. **不改判定**。本模块只记录；是否采信由 `ENABLE_M438_DELAYED_VERIFY_STRICT`（刀3）决定。

【与既有实现的关系】
匹配档位与 `SafeEvolutionExecutor._m41_line_matches` **同源同名**（`EVOLUTION_BASELINE_MATCH_MODE`：
`file` / `file_method` / `file_or_method`），错误级判定复用 `is_error_level_line`。
不复制日志格式知识——两边读同一份 `logs/pulse.log`。
"""
from __future__ import annotations

import os
import re
import sys
import time
from typing import Any

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")

SOURCE_LOG_WINDOW = "log_window"
SOURCE_UNAVAILABLE = "unavailable"

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_WINDOW_HOURS = 24.0
DEFAULT_FRESH_SEC = 3600.0


# ---------------------------------------------------------------- 配置
def _cfg(name: str, default: Any) -> Any:
    try:
        _c = sys.modules.get("config")
        if _c is None:
            return default
        return getattr(_c, name, default)
    except Exception as _e:
        _log_debug("读 config.%s 失败: %s: %s", name, type(_e).__name__, _e)
        return default


def _log_debug(msg: str, *args: Any) -> None:
    """标准库 logging（本模块可能在 nucleus.logger 装配前被导入）。"""
    import logging
    logging.getLogger("pulse.module.baseline_probe").debug(msg, *args)


def enabled() -> bool:
    """总闸 `ENABLE_M438_BASELINE_PROBE`（缺省 True）。关 ⇒ 入队口不写任何基线字段。"""
    return bool(_cfg("ENABLE_M438_BASELINE_PROBE", True))


def shadow_mode() -> bool:
    """`M438_BASELINE_SHADOW`（缺省 True）：只记录、不回溯改写历史件的判定。"""
    return bool(_cfg("M438_BASELINE_SHADOW", True))


def match_mode() -> str:
    """日志匹配档位：与 `SafeEvolutionExecutor._m41_match_mode` 同源同默认。"""
    _m = str(_cfg("EVOLUTION_BASELINE_MATCH_MODE", "file") or "file")
    return _m if _m in ("file_method", "file", "file_or_method") else "file"


def window_hours() -> float:
    _v = _cfg("M438_BASELINE_WINDOW_HOURS", DEFAULT_WINDOW_HOURS)
    try:
        _f = float(_v)
    except (TypeError, ValueError) as _e:
        _log_debug("window_hours 取值非法(%r)，回落缺省: %s", _v, _e)
        return DEFAULT_WINDOW_HOURS
    return _f if _f > 0 else DEFAULT_WINDOW_HOURS


def fresh_sec() -> float:
    _v = _cfg("M438_BASELINE_LOG_FRESH_SEC", DEFAULT_FRESH_SEC)
    try:
        _f = float(_v)
    except (TypeError, ValueError) as _e:
        _log_debug("fresh_sec 取值非法(%r)，回落缺省: %s", _v, _e)
        return DEFAULT_FRESH_SEC
    return _f if _f > 0 else DEFAULT_FRESH_SEC


# ---------------------------------------------------------------- 工具
def log_path() -> str:
    return os.path.join(_PROJECT_ROOT, "logs", "pulse.log")


def _parse_ts(line: str) -> float | None:
    """行首时间戳 → epoch 秒；无时间戳返回 None（Traceback 续行属此类）。"""
    _m = _TS_RE.match(line)
    if not _m:
        return None
    try:
        return time.mktime(time.strptime(f"{_m.group(1)} {_m.group(2)}",
                                         "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError) as _e:
        _log_debug("时间戳解析失败(%r): %s", line[:32], _e)
        return None


def log_span(path: str | None = None) -> dict:
    """廉价取日志的时间跨度：首行读头部、末行 seek 到尾部（**不全文扫**）。

    Returns:
        ``{"first_ts": float|None, "last_ts": float|None, "bytes": int}``
    """
    _p = path or log_path()
    _out = {"first_ts": None, "last_ts": None, "bytes": 0}
    if not os.path.exists(_p):
        return _out
    try:
        _out["bytes"] = os.path.getsize(_p)
        with open(_p, "rb") as _f:
            # 头部：最多 200 行足够跨过启动横幅
            for _i, _raw in enumerate(_f):
                if _i >= 200:
                    break
                _ts = _parse_ts(_raw.decode("utf-8", "replace"))
                if _ts is not None:
                    _out["first_ts"] = _ts
                    break
            # 尾部：最后 64KB 里找最后一个时间戳
            _size = _out["bytes"]
            _f.seek(max(0, _size - 65536))
            _tail = _f.read().decode("utf-8", "replace")
        for _line in reversed(_tail.splitlines()):
            _ts = _parse_ts(_line)
            if _ts is not None:
                _out["last_ts"] = _ts
                break
    except OSError as _e:
        _log_debug("log_span 失败: %s: %s", type(_e).__name__, _e)
    return _out


def min_window_hours() -> float:
    """低于这个长度的观测窗口不配叫「基线」（缺省 0.5h）。"""
    try:
        _v = float(_cfg("M438_BASELINE_MIN_WINDOW_HOURS", 0.5))
    except (TypeError, ValueError) as _e:
        _log_debug("min_window_hours 取值非法(%r)，回落缺省: %s", _v, _e)
        return 0.5
    return _v if _v > 0 else 0.5


def _line_matches(line: str, file_basename: str, method: str, mode: str) -> bool:
    """与 `SafeEvolutionExecutor._m41_line_matches` 逐字同义（两处必须同口径）。"""
    _has_file = bool(file_basename) and file_basename in line
    _has_method = bool(method) and method in line
    if mode == "file_method":
        return _has_file and _has_method
    if mode == "file_or_method":
        return _has_file or _has_method
    return _has_file


def _is_error_line(line: str) -> bool:
    """错误级行：复用框架既有判定（`is_error_level_line`）+ Traceback/Exception。"""
    try:
        from nucleus.evolution.LogAnalyzer import is_error_level_line
        if is_error_level_line(line):
            return True
    except Exception as _e:
        _log_debug("is_error_level_line 不可用，回落文本判据: %s", _e)
        if re.search(r"\b(ERROR|CRITICAL|FATAL)\b", line):
            return True
    return ("Traceback" in line) or ("Exception" in line)


# ---------------------------------------------------------------- 扫描
def scan_window(file_path: str, method: str, *, since: float, until: float,
                path: str | None = None, mode: str | None = None) -> dict:
    """在 `[since, until]` 里数 (file, method) 的错误行，并回报**窗口覆盖情况**。

    Returns:
        ``{"count": int, "first_ts": float|None, "last_ts": float|None,
           "lines": int, "covered": bool, "why": str}``

        ``covered=False`` 表示「这段窗口没有被日志完整覆盖」⇒ 调用方必须写 None。
    """
    _p = path or log_path()
    _mode = mode or match_mode()
    _base = os.path.basename(file_path or "")
    _out = {"count": 0, "first_ts": None, "last_ts": None, "lines": 0,
            "covered": False, "why": ""}

    if not _base:
        _out["why"] = "file 为空"
        return _out
    if not os.path.exists(_p):
        _out["why"] = f"日志不存在: {_p}"
        return _out

    _carry: float | None = None
    try:
        with open(_p, "r", encoding="utf-8", errors="replace") as _f:
            for _line in _f:
                _out["lines"] += 1
                _ts = _parse_ts(_line)
                if _ts is not None:
                    _carry = _ts
                    if _out["first_ts"] is None:
                        _out["first_ts"] = _ts
                    _out["last_ts"] = _ts
                _eff = _ts if _ts is not None else _carry
                if _eff is None:
                    continue
                if _eff < since:
                    continue
                if _eff > until:
                    continue
                if _is_error_line(_line) and _line_matches(_line, _base, method, _mode):
                    _out["count"] += 1
    except OSError as _e:
        _out["why"] = f"读日志失败: {type(_e).__name__}: {_e}"
        _log_debug("读日志失败 %s: %s", _p, _e)
        return _out

    if _out["first_ts"] is None:
        _out["why"] = "日志为空（无法判断任何窗口）"
        return _out
    # 覆盖判据①：日志首行必须早于窗口起点，否则「窗口前半段」根本没被观测
    if float(_out["first_ts"]) > float(since):
        _out["why"] = (f"日志起点 {time.strftime('%m-%d %H:%M', time.localtime(_out['first_ts']))}"
                       f" 晚于窗口起点 {time.strftime('%m-%d %H:%M', time.localtime(since))}"
                       f"（轮转截断，窗口未覆盖）")
        return _out
    # 覆盖判据②：日志必须仍在更新，否则「到 now 为止没有错」无从谈起
    _stale = float(until) - float(_out["last_ts"])
    if _stale > fresh_sec():
        _out["why"] = f"日志末行比窗口终点早 {_stale / 60.0:.0f} 分钟（超过 {fresh_sec() / 60.0:.0f} 分钟）"
        return _out

    _out["covered"] = True
    _out["why"] = "窗口完整覆盖"
    return _out


def probe_baseline(file_path: str, method: str, *, window_hours_: float | None = None,
                   now: float | None = None, path: str | None = None,
                   mode: str | None = None) -> dict:
    """取「修复前」基线：窗口 = ``[now - window_hours, now]``。

    Returns:
        ``{"baseline_errors": int|None, "baseline_source": str, "detail": str,
           "window_start": float, "window_end": float}``
    """
    _now = float(time.time() if now is None else now)
    _wh = window_hours() if window_hours_ is None else float(window_hours_)
    _want_since = _now - _wh * 3600.0

    # ★实测（11-03 22:37）：pulse.log 首行只到当天 03:00 —— 请求 24h 窗口时
    #   「窗口前半段根本没被观测」。若粗暴判 unavailable，则**每个补丁都拿不到基线**
    #   （日志天天轮转），探针等于废掉。故改为：取「请求窗口 ∩ 日志实际覆盖」，
    #   并把**真实用到的窗口长度**回传（`baseline_window_sec`），让下游知道这份
    #   基线的强度，而不是假装它等于 24h。
    _span = log_span(path)
    _span_first = _span.get("first_ts")
    _span_last = _span.get("last_ts")
    if _span_first is None or _span_last is None:
        return {
            "baseline_errors": None,          # ★绝不填 0
            "baseline_source": SOURCE_UNAVAILABLE,
            "detail": "日志为空或不可读，无窗口可观测",
            "window_start": _want_since, "window_end": _now,
            "baseline_window_sec": 0.0,
        }
    # 日志必须仍在更新，否则「到 now 为止没有错」无从谈起
    _stale = _now - float(_span_last)
    if _stale > fresh_sec():
        return {
            "baseline_errors": None,
            "baseline_source": SOURCE_UNAVAILABLE,
            "detail": (f"日志末行比窗口终点早 {_stale / 60.0:.0f} 分钟"
                       f"（超过 {fresh_sec() / 60.0:.0f} 分钟）"),
            "window_start": _want_since, "window_end": _now,
            "baseline_window_sec": 0.0,
        }

    _eff_since = max(_want_since, float(_span_first))
    _eff_sec = max(0.0, _now - _eff_since)
    _min_sec = min_window_hours() * 3600.0
    if _eff_sec < _min_sec:
        return {
            "baseline_errors": None,
            "baseline_source": SOURCE_UNAVAILABLE,
            "detail": (f"实际可观测窗口仅 {_eff_sec / 60.0:.0f} 分钟"
                       f"（< {min_window_hours():g}h 下限），不足以称基线"),
            "window_start": _eff_since, "window_end": _now,
            "baseline_window_sec": _eff_sec,
        }

    _scan = scan_window(file_path, method, since=_eff_since, until=_now,
                        path=path, mode=mode)
    if not _scan["covered"]:
        return {
            "baseline_errors": None,
            "baseline_source": SOURCE_UNAVAILABLE,
            "detail": _scan["why"],
            "window_start": _eff_since, "window_end": _now,
            "baseline_window_sec": _eff_sec,
        }
    _short = "" if _eff_sec >= _wh * 3600.0 - 60 else f"（请求 {_wh:g}h，实际 {_eff_sec / 3600.0:.1f}h）"
    return {
        "baseline_errors": int(_scan["count"]),
        "baseline_source": SOURCE_LOG_WINDOW,
        "detail": (f"窗口 {_eff_sec / 3600.0:.1f}h 内命中 {_scan['count']} 条错误"
                   f"{_short}（日志 {_scan['lines']} 行，档位 {mode or match_mode()}）"),
        "window_start": _eff_since, "window_end": _now,
        "baseline_window_sec": _eff_sec,
    }


# ---------------------------------------------------------------- 写入
def apply_to_patch(patch: dict[str, Any], *, now: float | None = None,
                   path: str | None = None) -> dict:
    """把探针结果写进入队补丁字典。**幂等**：已有可信基线时不覆盖。

    写入字段：
        baseline_errors       int | None      ← 任务书要求的主字段
        baseline_source       str             ← log_window / unavailable
        baseline_probed_at    float
        baseline_shadow       bool            ← 是否影子模式（只记录不判定）
        baseline_detail       str             ← 人可读依据

    Returns:
        dict：``{"applied": bool, "why": str, ...probe_result}``
    """
    if not isinstance(patch, dict):
        return {"applied": False, "why": "patch 不是 dict"}
    if not enabled():
        return {"applied": False, "why": "ENABLE_M438_BASELINE_PROBE=False"}

    _file = str(patch.get("file") or "")
    _method = str(patch.get("method") or "")
    if not _file:
        return {"applied": False, "why": "file 为空"}

    # 幂等：已有来源标记且基线非 None ⇒ 不重复探测（入队口可能被重试多次）
    if patch.get("baseline_source") and patch.get("baseline_errors") is not None:
        return {"applied": False, "why": "已有基线，跳过"}

    _r = probe_baseline(_file, _method, now=now, path=path)
    _now = float(time.time() if now is None else now)
    patch["baseline_errors"] = _r["baseline_errors"]
    patch["baseline_source"] = _r["baseline_source"]
    patch["baseline_probed_at"] = _now
    patch["baseline_shadow"] = shadow_mode()
    patch["baseline_detail"] = _r["detail"]
    patch["baseline_window_sec"] = _r.get("baseline_window_sec")
    return {"applied": True, "why": "ok", **_r}
