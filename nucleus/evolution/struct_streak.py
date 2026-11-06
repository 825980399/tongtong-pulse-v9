# -*- coding: utf-8 -*-
"""★467刀5：结构缺陷的跨轮计数与「不可修」退出（append-only 台账）。

为什么单独一个台账、而不复用 `criteria_window.no_def_streak`：
  那个台账里已经有 `PulseLiver.py::_compress_group` 的 **51+ 次**历史 `no_def`
  （来自 466 及以前）。若直接拿它判「连续 5 次即不可修」，467 一上线就会
  立刻把这个目标永久关掉 —— 而 467 恰恰是想先把它修好。
  所以本台账**只记 467 结构门之后的失败**，起点干净。

退出条件为什么必须有：
  `_compress_group` 已试 63 次仍未收敛。若「优化这个问题」本身就是误报，
  再好的喂料也收敛不了 —— 继续重试只是把 LLM 配额烧在同一种确定性失败上。
  判「不可修」不是放弃，而是把结论**记下来**，让人能看见它、再决定删台账重试。
"""
from __future__ import annotations

import io
import json
import os
import time

from nucleus._silent_except import silent_exc

LEDGER_REL = os.path.join("data", "evolution", "struct_defect_ledger.jsonl")

#: 计入「连续缺陷」的事件名。`ok` 会把连续计数清零。
EV_DEFECT = "struct_defect"
EV_OK = "ok"


def ledger_path(path: str | None = None) -> str:
    """台账路径（`path` 仅供隔离验证注入）。"""
    if path:
        return str(path)
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
    except Exception as _e:
        silent_exc(_e, where="struct_streak::ledger_path 根目录", level="debug")
        root = "."
    return os.path.join(root, LEDGER_REL)


def _key(file: str, method: str) -> tuple:
    """归一成 `(文件名, 方法名)`：两侧可能一个给绝对路径、一个给相对路径。"""
    try:
        _f = os.path.basename(str(file or "").replace("\\", "/"))
    except Exception as _e:
        silent_exc(_e, where="struct_streak::_key basename", level="debug")
        _f = str(file or "")
    return _f, str(method or "")


def note(file: str = "", method: str = "", defects=None, patch_id: str = "",
         event: str = EV_DEFECT, path: str | None = None,
         now: float | None = None) -> str:
    """写一条结构缺陷/恢复记录（append-only）。返回路径；失败返回空串。"""
    _p = ledger_path(path)
    _f, _m = _key(file, method)
    _rec = {
        "ts": float(now if now is not None else time.time()),
        "file": _f,
        "method": _m,
        "event": str(event or EV_DEFECT),
        "defects": [str(_d) for _d in (defects or [])][:8],
        "patch_id": str(patch_id or "")[:64],
    }
    try:
        from nucleus.proc_gen import proc_gen_str as _pgs
        _rec["proc_gen"] = str(_pgs() or "unknown")
    except Exception as _e:
        silent_exc(_e, where="struct_streak::note proc_gen", level="debug")
    try:
        _dir = os.path.dirname(os.path.abspath(_p))
        if _dir and not os.path.isdir(_dir):
            os.makedirs(_dir, exist_ok=True)
        with io.open(_p, "a", encoding="utf-8", newline="\n") as _f2:
            _f2.write(json.dumps(_rec, ensure_ascii=False) + "\n")
        return _p
    except Exception as _e:
        silent_exc(_e, where="struct_streak::note write", level="warning")
        return ""


def _read(path: str | None = None) -> list:
    _p = ledger_path(path)
    _rows = []
    if not os.path.exists(_p):
        return _rows
    try:
        with io.open(_p, "r", encoding="utf-8") as _f:
            for _ln in _f:
                _ln = _ln.strip()
                if not _ln:
                    continue
                try:
                    _rows.append(json.loads(_ln))
                except Exception as _je:
                    silent_exc(_je, where="struct_streak::_read 坏行", level="debug")
    except Exception as _e:
        silent_exc(_e, where="struct_streak::_read open", level="warning")
    return _rows


def streak(file: str = "", method: str = "", path: str | None = None) -> dict:
    """同一 (file,method) 的**连续结构缺陷次数**。

    从最后一条往回数：`struct_defect` +1，遇到 `ok` 立即停（那是恢复点）。
    别的目标（不同 file/method）的记录跳过、不打断计数 —— 否则多目标轮转
    会把每个目标的计数都冲掉，退出条件永远触发不了。

    Returns: ``{"streak": int, "last_ts": float|None, "records": int}``
    """
    _out = {"streak": 0, "last_ts": None, "records": 0}
    _f, _m = _key(file, method)
    if not _f or not _m:
        return _out
    _rows = _read(path)
    _out["records"] = len(_rows)
    for _r in reversed(_rows):
        if not isinstance(_r, dict):
            continue
        if _key(_r.get("file"), _r.get("method")) != (_f, _m):
            continue
        _ev = str(_r.get("event") or EV_DEFECT)
        if _ev == EV_OK:
            break
        if _ev == EV_DEFECT:
            _out["streak"] += 1
            if _out["last_ts"] is None:
                try:
                    _out["last_ts"] = float(_r.get("ts") or 0)
                except (TypeError, ValueError) as _e:
                    silent_exc(_e, where="struct_streak::streak last_ts", level="debug")
    return _out


def should_stop(file: str = "", method: str = "", limit: int | None = None,
                path: str | None = None) -> dict:
    """是否已判「不可修」。

    Returns: ``{"stop": bool, "streak": int, "limit": int}``
    """
    if limit is None:
        try:
            import config as _c
            _v = getattr(_c, "M467_UNFIXABLE_STREAK", None)
            limit = 5 if _v is None else int(_v)
        except Exception as _e:
            silent_exc(_e, where="struct_streak::should_stop cfg", level="debug")
            limit = 5
    _lim = max(1, int(limit))
    _st = streak(file, method, path=path)
    return {"stop": int(_st["streak"]) >= _lim, "streak": int(_st["streak"]),
            "limit": _lim}
