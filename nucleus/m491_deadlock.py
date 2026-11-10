# -*- coding: utf-8 -*-
"""★491-6：跨线程锁等待图 + 死锁环检测（默认关，纯观测，绝不自动重启）。

设计要点（与烛微前置分析 §3.3 一致）：
  - 只在锁等待 ≥1ms 时记边（与 479 同阈值），无竞争路径零开销；
  - RLock 不暴露 owner ⇒ 自己在 acquire 成功后记 `_owners[organ] = tid`；
  - 等待图顶点是线程 ident，边 `waiter_tid -> owner_tid`；
  - 环 = 沿 wait-for 边能绕回起点；单器官等待 ≥30s 且 owner 不在等别人
    ⇒ 判「慢 handler」（不是死锁）；
  - 命中环：sys._current_frames() 全线程栈落 logs/ + 发告警脉冲；
    **不重启、不重建池、不中断 handler**（重启会丢整队消息）。

所有公开 API 首行判开关；关时不取栈、不建字典、不写文件、不发脉冲。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("M491Deadlock")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_LOCK = threading.Lock()
_OWNERS: dict[str, int] = {}                 # organ -> 持锁 tid
_WAITS: dict[int, tuple] = {}                # waiter tid -> (organ, owner_tid, since)
_REPORTED: set[tuple] = set()                # 已报环（防刷屏）


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception as _e:
        silent_exc(_e, where="m491_deadlock::_cfg", level="debug")
        return default


def on() -> bool:
    """总开关（出厂默认 False）。"""
    return bool(_cfg("ENABLE_M491_DEADLOCK_DETECT", False))


def reset_for_tests() -> None:
    with _LOCK:
        _OWNERS.clear()
        _WAITS.clear()
        _REPORTED.clear()


def note_acquired(organ: str) -> None:
    """acquire 成功后：清自身等待边 + 登记 owner。"""
    if not on():
        return
    try:
        _tid = threading.get_ident()
        with _LOCK:
            _WAITS.pop(_tid, None)
            _OWNERS[str(organ)] = _tid
    except Exception as _e:
        silent_exc(_e, where="m491_deadlock::note_acquired", level="debug")


def note_released(organ: str) -> None:
    """release 前：删 owner 登记。"""
    if not on():
        return
    try:
        _tid = threading.get_ident()
        with _LOCK:
            if _OWNERS.get(str(organ)) == _tid:
                _OWNERS.pop(str(organ), None)
    except Exception as _e:
        silent_exc(_e, where="m491_deadlock::note_released", level="debug")


def note_wait(organ: str, owner_tid: int | None, wait_ms: float) -> dict | None:
    """等待 ≥阈值时记一条 wait-for 边，并顺便检测环 / 慢 handler。

    返回诊断 dict（供单测与活体自检）；关开关 / 未达阈值 ⇒ None。
    """
    if not on():
        return None
    try:
        _min_ms = float(_cfg("M491_WAIT_EDGE_MIN_MS", 1.0))
        if wait_ms < _min_ms:
            return None
        _tid = threading.get_ident()
        _now = time.time()
        with _LOCK:
            _ttl = float(_cfg("M491_WAIT_EDGE_TTL_SEC", 30.0))
            for _k, _v in list(_WAITS.items()):
                if _now - float(_v[2]) > _ttl:
                    _WAITS.pop(_k, None)
            if owner_tid is not None and owner_tid != _tid:
                _WAITS[_tid] = (str(organ), int(owner_tid), _now)
            _edges = {k: int(v[1]) for k, v in _WAITS.items()
                      if v[1] is not None}
            _slow = [k for k, v in _WAITS.items()
                     if _now - float(v[2]) >= float(
                         _cfg("M491_SLOW_HANDLER_SEC", 30.0))
                     and v[1] not in _edges]
        _ring = _find_ring(_edges)
        _out = {"wait_ms": round(float(wait_ms), 3), "organ": str(organ),
                "edges": _edges, "ring": _ring, "slow": _slow}
        if _ring and not _ring_seen(_ring):
            _path = dump_ring(_ring)
            _out["dump"] = _path
        return _out
    except Exception as _e:
        silent_exc(_e, where="m491_deadlock::note_wait", level="debug")
        return None


def _find_ring(edges: dict[int, int]):
    """迭代式 DFS 找 wait-for 环；返回 tid 列表，无环返回 None。"""
    _color: dict[int, int] = {}
    for _start in list(edges):
        if _color.get(_start):
            continue
        _stack = [(_start, iter([edges.get(_start)]))]
        _path = [_start]
        _color[_start] = 1
        while _stack:
            _node, _it = _stack[-1]
            _nxt = next(_it, None)
            if _nxt is None:
                _color[_node] = 2
                _stack.pop()
                _path.pop()
                continue
            if _nxt not in edges:
                continue
            if _color.get(_nxt) == 1:
                try:
                    _i = _path.index(_nxt)
                    return _path[_i:] + [_nxt]
                except ValueError as _e:
                    silent_exc(_e, where="m491_deadlock::_find_ring:index",
                               level="debug")
                    return None
            if not _color.get(_nxt):
                _color[_nxt] = 1
                _path.append(_nxt)
                _stack.append((_nxt, iter([edges.get(_nxt)])))
    return None


def _ring_seen(ring) -> bool:
    _key = tuple(sorted(set(int(x) for x in ring)))
    with _LOCK:
        if _key in _REPORTED:
            return True
        _REPORTED.add(_key)
        return False


def dump_ring(ring) -> str:
    """全线程栈落 logs/deadlock_*.log（append，flush），返回文件路径。"""
    _dir = os.path.join(_ROOT, "logs")
    _p = os.path.join(_dir, "deadlock_%s.log" % time.strftime("%Y%m%d"))
    try:
        os.makedirs(_dir, exist_ok=True)
        try:
            _frames = sys._current_frames()
        except Exception as _e:
            silent_exc(_e, where="m491_deadlock::dump_ring:frames", level="debug")
            _frames = {}
        _lines = ["[%s] M491_DEADLOCK_DUMP ring=%s" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), list(ring))]
        for _t in threading.enumerate():
            _tid = _t.ident
            _lines.append("  name=%s ident=%s alive=%s daemon=%s" % (
                _t.name, _tid, _t.is_alive(), _t.daemon))
            if _tid in _frames:
                try:
                    _stack = traceback.format_stack(_frames[_tid])
                    _lines.extend("    " + s.strip().replace("\n", " ")
                                  for s in _stack[-12:])
                except Exception as _e:
                    silent_exc(_e, where="m491_deadlock::dump_ring:stack",
                               level="debug")
        with open(_p, "a", encoding="utf-8", errors="replace") as _f:
            _f.write("\n".join(_lines) + "\n")
            _f.flush()
        _module_logger.error("[491-6] 检出锁等待环 %s，全线程栈已落 %s"
                             "（只 dump + 告警，绝不自动重启）", list(ring), _p)
        return _p
    except Exception as _e:
        silent_exc(_e, where="m491_deadlock::dump_ring", level="debug")
        return ""


def alarm_payload(ring, dump_path: str) -> dict:
    """供 InfoField 在锁外发告警脉冲的 payload（带 ts_ns 防去重）。"""
    return {"type": "deadlock_wait_graph_ring", "level": "L0",
            "ring": [int(x) for x in ring], "dump": str(dump_path),
            "ts_ns": time.time_ns()}


def snapshot() -> dict:
    """只读快照（诊断 / 单测）。"""
    if not on():
        return {}
    with _LOCK:
        return {"owners": dict(_OWNERS),
                "waits": {k: tuple(v) for k, v in _WAITS.items()}}


def owner_of(organ: str):
    """acquire 之前读当前持锁 tid；无人持锁返回 None（= 不会等待）。"""
    if not on():
        return None
    with _LOCK:
        return _OWNERS.get(str(organ))


def note_wait_start(organ: str, owner_tid) -> dict | None:
    """★acquire **之前**写 wait-for 边并检环。

    死锁时 acquire 永不返回 ⇒ 边必须在此刻就存在，否则无法检测。
    只有确实有人持锁（owner_tid 非空且不是自己）时才写边：无竞争路径
    不建字典、不取栈、不写盘，等价于 479 的「只在可能等待时记录」。
    """
    if not on():
        return None
    try:
        _tid = threading.get_ident()
        _now = time.time()
        with _LOCK:
            _ttl = float(_cfg("M491_WAIT_EDGE_TTL_SEC", 30.0))
            for _k, _v in list(_WAITS.items()):
                if _now - float(_v[2]) > _ttl:
                    _WAITS.pop(_k, None)
            if owner_tid is not None and int(owner_tid) != _tid:
                _WAITS[_tid] = (str(organ), int(owner_tid), _now)
            _edges = {k: int(v[1]) for k, v in _WAITS.items()}
        _ring = _find_ring(_edges)
        _out = {"organ": str(organ), "edges": _edges, "ring": _ring}
        if _ring and not _ring_seen(_ring):
            _out["dump"] = dump_ring(_ring)
        return _out
    except Exception as _e:
        silent_exc(_e, where="m491_deadlock::note_wait_start", level="debug")
        return None


def note_wait_end(organ: str, wait_ms: float) -> dict | None:
    """acquire 成功后：清自身等待边、登记 owner，并对 ≥阈值等待做慢 handler 提示。"""
    if not on():
        return None
    try:
        _tid = threading.get_ident()
        _min_ms = float(_cfg("M491_WAIT_EDGE_MIN_MS", 1.0))
        with _LOCK:
            _WAITS.pop(_tid, None)
            _OWNERS[str(organ)] = _tid
            _slow_sec = float(_cfg("M491_SLOW_HANDLER_SEC", 30.0))
            _slow = (wait_ms / 1000.0 >= _slow_sec)
        return {"wait_ms": round(float(wait_ms), 3),
                "slow_handler": _slow, "organ": str(organ),
                "recorded": wait_ms >= _min_ms}
    except Exception as _e:
        silent_exc(_e, where="m491_deadlock::note_wait_end", level="debug")
        return None
