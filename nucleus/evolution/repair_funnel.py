# -*- coding: utf-8 -*-
"""repair_funnel.py —— 修复漏斗留痕（第463批）

## 为什么要有这一层

462 交接要查「发现 15 个问题却 0 轮修复」时，只能从 10MB×5 的滚动日志里
grep 四行（``[P1-3 冷却隔离]`` / ``[修复蒸馏] 共N个`` / ``[自主进化] 第N轮完成`` /
``[自主进化] 修复完成``）再手工拼漏斗。344 批的作废台账早就证明：滚动日志里的
关键读数会随轮转消失。本模块把每轮的**逐层读数**一次落盘。

## 契约

* append-only JSONL，一行一轮；字段固定、可复算，不写补丁正文。
* ``note()`` 失败绝不影响修复主流程（日志不决定返回值）。
* pytest 护栏：默认生产路径 + pytest ⇒ 不写盘；显式 root 照常写。
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import config
from nucleus._silent_except import silent_exc

SCHEMA = "repair_funnel/463.v1"
_DEFAULT_REL = "data/reports/evolution/repair_funnel.jsonl"


def _cfg(name: str, default: Any) -> Any:
    _v = getattr(config, name, None)
    return default if _v is None else _v


def _root(root: str | None = None) -> str:
    if root:
        return str(root)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _writable(root: str | None) -> bool:
    if root is not None:
        return True
    return "pytest" not in sys.modules


def funnel_path(root: str | None = None) -> str:
    return os.path.join(_root(root), str(_cfg("M463_FUNNEL_REL", _DEFAULT_REL)))


def note(counts: dict, *, root: str | None = None,
         now: float | None = None) -> bool:
    """记一轮漏斗。``counts`` 只需给读数，其余字段由本函数补齐。"""
    if not bool(_cfg("ENABLE_M463_REPAIR_FUNNEL", True)) or not _writable(root):
        return False
    _now = float(now if now is not None else time.time())
    _row = {"at": _now, "schema": SCHEMA,
            "at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_now))}
    for _k in ("candidate", "filtered", "cooldown_skipped", "forced_recheck",
               "admitted", "planned", "processed", "submitted",
               "cool_skipped_by_policy", "cooldown_registered"):
        _v = (counts or {}).get(_k, 0)
        try:
            _row[_k] = int(_v)
        except (TypeError, ValueError) as _e:
            silent_exc(_e, where="repair_funnel::note value", level="debug")
            _row[_k] = 0
    # ★464 刀3：明细行（不进 int 白名单，单独收，防止被 int() 抹平）
    if bool(_cfg("ENABLE_M464_FUNNEL_DETAIL", True)):
        _det = (counts or {}).get("cooled_detail")
        if isinstance(_det, list) and _det:
            _buckets: dict = {}
            _top: list = []
            for _r in _det[:20]:
                if not isinstance(_r, dict):
                    continue
                _act = str(_r.get("policy") or _r.get("action") or "?")[:24]
                _buckets[_act] = _buckets.get(_act, 0) + 1
                if len(_top) < 5:
                    try:
                        _rem = int(float(_r.get("rem_s") or 0))
                    except (TypeError, ValueError) as _e2:
                        silent_exc(_e2, where="repair_funnel::note rem_s", level="debug")
                        _rem = 0
                    _top.append({"policy": _act,
                                 "type": str(_r.get("type") or "")[:40],
                                 "file": str(_r.get("file") or "")[:60],
                                 "method": str(_r.get("method") or "")[:60],
                                 "rem_s": _rem})
            _row["cooled_buckets"] = _buckets
            _row["cooled_top"] = _top

    _p = funnel_path(root)
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
        return True
    except Exception as _e:
        silent_exc(_e, where="repair_funnel::note", level="debug")
        return False


def rows(*, root: str | None = None, limit: int = 20) -> list[dict]:
    """读最近 ``limit`` 轮漏斗（按落盘顺序）。"""
    _p = funnel_path(root)
    _out: list[dict] = []
    try:
        if not os.path.isfile(_p):
            return _out
        with open(_p, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _d = json.loads(_line)
                except ValueError as _e:
                    silent_exc(_e, where="repair_funnel::rows bad line", level="debug")
                    continue
                if isinstance(_d, dict):
                    _out.append(_d)
    except Exception as _e:
        silent_exc(_e, where="repair_funnel::rows", level="debug")
    return _out[-max(0, int(limit)):]
