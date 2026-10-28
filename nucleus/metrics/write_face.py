# -*- coding: utf-8 -*-
"""383 刀3 新建：写面观察（只读采样，不拦截任何写）。

★为什么不用 wrap `builtins.open`：那会把开销与风险挂到**每一次写**上（热路径 + 与
safe-delete 交互），而我们要的只是"哪些文件写得最频繁"。改用**mtime 差分采样**：
每 N 秒扫一次目标目录，与上次快照比对，变了的计 1 次。零侵入、可随时关掉。

能力边界：采样是**窗口级**的 —— 同一窗口内改 10 次与改 1 次无法区分（都记 1 次）。
所以输出是"被改动的**拍数**"，不是"写次数"。要看真实写次数请走 `guard_write` 侧计数。
"""
from __future__ import annotations

import os

import config as _cfg
from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_logger = get_module_logger("WriteFace")

#: rel_path -> 被观察到变化的拍数
_M383_COUNTS: dict = {}
#: rel_path -> (size, mtime_ns)
_M383_SNAPSHOT: dict = {}
_M383_BEATS = {"scans": 0, "last_ts": 0.0}


def _on() -> bool:
    """开关 `ENABLE_WRITE_FACE_OBSERVE`（默认开；关掉 = 完全不扫、零读数）。"""
    return bool(getattr(_cfg, "ENABLE_WRITE_FACE_OBSERVE", True))


def _every_sec() -> float:
    try:
        return float(getattr(_cfg, "WRITE_FACE_OBSERVE_EVERY_SEC", 300.0))
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="write_face::_every_sec", level="debug")
        return 300.0


def _scan(root: str, max_files: int = 20000) -> dict:
    """扫一遍目录，返回 {rel: (size, mtime_ns)}（跳过 .bak*/__pycache__/tmp）。"""
    _out: dict = {}
    _n = 0
    for _dp, _dn, _fn in os.walk(root):
        _dn[:] = [d for d in _dn if not d.startswith(".bak") and d != "__pycache__"]
        for _f in _fn:
            _p = os.path.join(_dp, _f)
            try:
                _st = os.stat(_p)
            except OSError as _e:
                silent_exc(_e, where="write_face::_scan stat", level="debug")
                continue
            _out[os.path.relpath(_p, root).replace("\\", "/")] = (
                int(_st.st_size), int(getattr(_st, "st_mtime_ns", 0)))
            _n += 1
            if _n >= max_files:
                return _out
    return _out


def observe_once(root: str = "data", now_ts: float | None = None,
                 force: bool = False) -> dict:
    """采一拍：与上次快照比对，变化文件计 1 拍。返回本轮增量信息。

    节流：`WRITE_FACE_OBSERVE_EVERY_SEC`（默认 300s）。`force=True` 跳过节流（测试用）。
    """
    _out = {"on": _on(), "scanned": 0, "changed": 0, "skipped": ""}
    if not _out["on"]:
        _out["skipped"] = "switch_off"
        return _out
    _now = float(now_ts if now_ts is not None else _now_ts())
    if not force:
        _last = float(_M383_BEATS.get("last_ts") or 0.0)
        if _now - _last < _every_sec():
            _out["skipped"] = "throttled"
            return _out
    _M383_BEATS["last_ts"] = _now
    _M383_BEATS["scans"] = int(_M383_BEATS.get("scans", 0)) + 1
    if not os.path.isdir(root):
        _out["skipped"] = "root_missing"
        return _out
    _cur = _scan(root)
    _out["scanned"] = len(_cur)
    _prev = _M383_SNAPSHOT
    _changed = 0
    for _rel, _sig in _cur.items():
        if _prev.get(_rel) != _sig:
            _M383_COUNTS[_rel] = int(_M383_COUNTS.get(_rel, 0)) + 1
            _changed += 1
    _M383_SNAPSHOT.clear()
    _M383_SNAPSHOT.update(_cur)
    _out["changed"] = _changed
    return _out


def _now_ts() -> float:
    import time as _t
    return _t.time()


def stats(top: int = 20) -> dict:
    """读数：{scans, files, total_beats, top:[{path, beats}]} —— 只给"拍数"，不给写次数。"""
    _items = sorted(_M383_COUNTS.items(), key=lambda kv: -int(kv[1]))
    _top = [{"path": _p, "beats": int(_c)} for _p, _c in _items[:max(1, int(top))]]
    return {
        "on": _on(),
        "scans": int(_M383_BEATS.get("scans", 0)),
        "files": len(_M383_COUNTS),
        "total_beats": sum(int(v) for v in _M383_COUNTS.values()),
        "top": _top,
        "note": "拍数≠写次数：同一窗口内改多次只记 1 拍",
    }


def reset() -> None:
    """清空（**仅供测试**）。"""
    _M383_COUNTS.clear()
    _M383_SNAPSHOT.clear()
    _M383_BEATS["scans"] = 0
    _M383_BEATS["last_ts"] = 0.0
