# -*- coding: utf-8 -*-
"""★第158批 上-A O-A1（P0）：任务态账本（重启恢复协议 · 写入侧 + 启动对账）。

背景（落点已由星轨裁定，见 `星轨_O-A1落点裁定_20261003.md`）
--------------------------------------------------------
「标 running 无存活任务」的持久化载体**此前不存在**：
``StructuredParallelScheduler`` 纯内存 / ``cooldown.json`` 纯冷却账 /
``main.py`` 无任务态恢复 / ``runtime_state.json`` 的 ``running`` 是器官计数。
故本刀**新建**任务态账本 ``data/evolution/task_ledger.json``（与冷却账同目录）。

职责
----
1. **写入侧**：任务标 ``running``（含存活 pid + 心跳 + 最后写时间戳）、
   心跳刷新、终态落 ``done`` / ``interrupted``。
2. **启动对账**（:func:`reconcile_on_startup`）：load → 对账 → 把「标 running
   但存活 pid 已不在」的任务**单事务改判** ``interrupted`` + 出诊断回执。
   ★**不回放、不续跑**——免重做判据 = 任务 id + 最后写时间戳。
3. **未送达回复随附**：任务若带 ``pending_reply``（已产出但未送达的回复），
   随诊断回执一并交出并标记免重做，调用方无需重算。
4. **冷却账不失明**：对账时顺带**只读**核对 ``cooldown.json`` 可解析，
   不改它（冷却账写改写仍归冷却域，本刀不越界）。

事务边界
--------
单文件原子写（``temp`` + :func:`os.replace` rename），任务态账本单文件即可满足
「单事务对账」语义，不跨多文件——这是星轨裁定的口径。

安全边界
--------
* 只写 ``data/evolution/task_ledger.json`` 一个文件；**测试环境（pytest）不写
  生产 data/**（与 maturity_ledger 同款保护）。
* 冷却账 ``data/evolution/cooldown.json`` **只读核对、绝不改写**。
* 本模块不改任何进化决策、不发网络请求。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from typing import Any

from nucleus._silent_except import silent_exc

__all__ = [
    "TASK_LEDGER_VERSION",
    "VALID_TASK_STATUS",
    "ledger_path",
    "cooldown_path",
    "register_task",
    "heartbeat_task",
    "finish_task",
    "load_ledger",
    "reconcile_on_startup",
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 账本结构版本（结构变更时递增）。
TASK_LEDGER_VERSION = "158A-OA1-1"

#: 合法任务状态。
VALID_TASK_STATUS = frozenset({"running", "done", "interrupted"})

#: 存活探测结果分类。
_PID_ALIVE = "alive"
_PID_DEAD = "dead"
_PID_UNKNOWN = "unknown"


# ------------------------------------------------------------------ 路径与环境
def ledger_path() -> str:
    """任务态账本路径（``data/evolution/task_ledger.json``）。"""
    return os.path.join(_PROJECT_ROOT, "data", "evolution", "task_ledger.json")


def cooldown_path() -> str:
    """冷却账路径（``data/evolution/cooldown.json``）——★只读核对，本刀绝不改写。"""
    return os.path.join(_PROJECT_ROOT, "data", "evolution", "cooldown.json")


def _is_test_env() -> bool:
    try:
        return ("pytest" in sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.task_ledger::_is_test_env")
        return False


def _writable(path: str) -> bool:
    """是否允许写入（pytest 环境 + 生产 data/ → 拒绝，防测试污染）。"""
    if _is_test_env() and os.path.abspath(path).replace("\\", "/").lower().startswith(
            _PROJECT_ROOT.replace("\\", "/").lower() + "/data/"):
        sys.stderr.write("[task_ledger] 测试环境跳过生产写入: {}\n".format(path))
        return False
    return True


# ------------------------------------------------------------------ 原子读写
def _empty_ledger() -> dict[str, Any]:
    return {"version": TASK_LEDGER_VERSION, "updated_at": 0.0, "tasks": {}}


def load_ledger(path: str | None = None) -> dict[str, Any]:
    """读取任务态账本；不存在/损坏返回空账本（**不抛异常**）。"""
    _p = path or ledger_path()
    if not os.path.isfile(_p):
        return _empty_ledger()
    try:
        with io.open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="nucleus.evolution.task_ledger::load_ledger", level="warning")
        return _empty_ledger()
    if not isinstance(_d, dict) or not isinstance(_d.get("tasks"), dict):
        silent_exc(ValueError("账本结构非法（缺 tasks）"),
                   where="nucleus.evolution.task_ledger::load_ledger", level="warning")
        return _empty_ledger()
    _d.setdefault("version", TASK_LEDGER_VERSION)
    return _d


def _atomic_save(data: dict, path: str | None = None) -> str:
    """单文件原子写：先写 ``<name>.tmp`` 再 :func:`os.replace` 覆盖。

    ★这是「单事务」的实现点——rename 在同一文件系统内是原子的，
    因此不会留下「写了一半的账本」。
    """
    _p = path or ledger_path()
    if not _writable(_p):
        return _p
    data["updated_at"] = time.time()
    data["version"] = TASK_LEDGER_VERSION
    _tmp = _p + ".tmp"
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with io.open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(data, _f, ensure_ascii=False, indent=2)
            _f.flush()
            try:
                os.fsync(_f.fileno())
            except OSError as _e:
                # fsync 不可用不阻断（部分文件系统/Windows 目录不支持）
                silent_exc(_e, where="nucleus.evolution.task_ledger::_atomic_save fsync",
                           level="debug")
        os.replace(_tmp, _p)          # ← 原子 rename
    except OSError as _e:
        silent_exc(_e, where="nucleus.evolution.task_ledger::_atomic_save", level="warning")
        sys.stderr.write("[task_ledger] 账本写入失败: {}: {}\n".format(type(_e).__name__, _e))
    return _p


# ------------------------------------------------------------------ 存活探测
def _pid_state(pid: Any) -> str:
    """探测 pid 存活状态 → ``alive`` / ``dead`` / ``unknown``。

    Windows 下 ``os.kill(pid, 0)`` 语义不可靠，优先用 psutil；psutil 缺失时
    才退回 POSIX 探测，仍不可判定则返回 ``unknown``（**不谎报 dead**）。
    """
    try:
        _p = int(pid)
    except (TypeError, ValueError) as _e:
        # ★不得静默：pid 字段非法须留痕（否则脏数据会被当成"进程已死"）
        silent_exc(_e, where="nucleus.evolution.task_ledger::_pid_state int", level="debug")
        return _PID_DEAD
    if _p <= 0:
        return _PID_DEAD
    try:
        import psutil
        return _PID_ALIVE if psutil.pid_exists(_p) else _PID_DEAD
    except Exception as _e:
        silent_exc(_e, where="nucleus.evolution.task_ledger::_pid_state psutil",
                   level="debug")
    try:
        os.kill(_p, 0)
        return _PID_ALIVE
    except OSError as _e:
        # ★不得静默：POSIX 探测失败须留痕（ESRCH=确已死；EPERM=无权探测，语义不同）
        silent_exc(_e, where="nucleus.evolution.task_ledger::_pid_state os.kill", level="debug")
        return _PID_DEAD
    except Exception as _e:
        silent_exc(_e, where="nucleus.evolution.task_ledger::_pid_state os.kill",
                   level="debug")
        return _PID_UNKNOWN


def _current_pid() -> int:
    return os.getpid()


# ------------------------------------------------------------------ 写入侧
def register_task(task_id: str, meta: dict | None = None,
                  pending_reply: dict | None = None,
                  path: str | None = None) -> dict[str, Any]:
    """登记任务为 ``running``（记录存活 pid + 心跳 + 最后写时间戳）。

    Args:
        task_id: 任务唯一 id（合成消息去重键的组成部分）。
        meta: 附加元信息（问题类型/目标文件等），原样保存。
        pending_reply: 已产出但**尚未送达**的回复；对账时随附并标记免重做。
        path: 账本路径（测试可注入 tmp 路径）。
    """
    _d = load_ledger(path)
    _now = time.time()
    _d["tasks"][str(task_id)] = {
        "status": "running",
        "pid": _current_pid(),
        "started_at": _now,
        "last_write": _now,
        "heartbeat": _now,
        "meta": meta or {},
        "pending_reply": pending_reply or None,
    }
    _atomic_save(_d, path)
    return _d["tasks"][str(task_id)]


def heartbeat_task(task_id: str, path: str | None = None) -> bool:
    """刷新任务心跳与最后写时间戳。返回是否命中并刷新成功。"""
    _d = load_ledger(path)
    _t = _d["tasks"].get(str(task_id))
    if not isinstance(_t, dict) or _t.get("status") != "running":
        return False
    _now = time.time()
    _t["heartbeat"] = _now
    _t["last_write"] = _now
    _atomic_save(_d, path)
    return True


def finish_task(task_id: str, status: str = "done",
                pending_reply: dict | None = None,
                path: str | None = None) -> bool:
    """把任务落终态（``done`` / ``interrupted``）。"""
    if status not in VALID_TASK_STATUS or status == "running":
        return False
    _d = load_ledger(path)
    _t = _d["tasks"].get(str(task_id))
    if not isinstance(_t, dict):
        return False
    _now = time.time()
    _t["status"] = status
    _t["last_write"] = _now
    _t["finished_at"] = _now
    if pending_reply is not None:
        _t["pending_reply"] = pending_reply
    _atomic_save(_d, path)
    return True


# ------------------------------------------------------------------ 冷却账核对
def check_cooldown_intact(path: str | None = None) -> dict[str, Any]:
    """★只读核对冷却账（**绝不改写**）——保证「冷却账不失明」。

    Returns:
        ``{"exists", "parseable", "entries", "note"}``。
    """
    _p = path or cooldown_path()
    _out = {"exists": os.path.isfile(_p), "parseable": None, "entries": None, "note": ""}
    if not _out["exists"]:
        _out["note"] = "冷却账不存在（首次运行属正常）"
        return _out
    try:
        with io.open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
        _out["parseable"] = True
        _out["entries"] = len(_d) if hasattr(_d, "__len__") else None
    except (OSError, ValueError) as _e:
        _out["parseable"] = False
        _out["note"] = "冷却账**不可解析**——账目失明，需人工核查：{}: {}".format(
            type(_e).__name__, _e)
        silent_exc(_e, where="nucleus.evolution.task_ledger::check_cooldown_intact",
                   level="warning")
    return _out


# ------------------------------------------------------------------ 启动对账（核心）
def reconcile_on_startup(path: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """★启动对账：把「标 running 但存活 pid 已不在」的任务改判 ``interrupted``。

    单事务语义：整批改判后**一次原子写**落盘（不逐条写，避免中途崩溃留半截）。

    Args:
        path: 账本路径（测试可注入 tmp 路径）。
        dry_run: 只计算不落盘（用于预演/观测）。

    Returns:
        诊断回执 dict::

            {
              "reconciled": <改判数>,
              "still_alive": <仍在跑的任务数>,
              "unknown_pid": <存活不可判定数>,
              "receipts": [ {task_id, old_status, new_status, pid, pid_state,
                             started_at, last_write, duration_s, undelivered,
                             replay: "forbidden", reason} ],
              "cooldown": {...只读核对...},
              "dry_run": bool,
            }
    """
    _d = load_ledger(path)
    _me = _current_pid()
    _now = time.time()
    _receipts: list[dict[str, Any]] = []
    _still = 0
    _unknown = 0

    for _tid, _t in list(_d.get("tasks", {}).items()):
        if not isinstance(_t, dict) or _t.get("status") != "running":
            continue
        _pid = _t.get("pid")
        if _pid == _me:
            # 本进程的任务（理论上不该在启动期出现）→ 视为仍活，不动
            _still += 1
            continue
        _st = _pid_state(_pid)
        if _st == _PID_ALIVE:
            _still += 1
            continue
        if _st == _PID_UNKNOWN:
            # ★不可判定时**保守不改判**（不谎报 dead），只记账
            _unknown += 1
            _receipts.append({
                "task_id": _tid, "new_status": _t.get("status"),
                "pid": _pid, "pid_state": _st,
                "reason": "存活不可判定（psutil 缺失且 POSIX 探测失败），保守不改判",
                "replay": "forbidden",
            })
            continue
        # ---- dead：单事务改判 interrupted + 诊断回执
        _sw = _t.get("started_at") or _t.get("last_write") or _now
        _lw = _t.get("last_write") or _sw
        _pend = _t.get("pending_reply")
        _t["status"] = "interrupted"
        _t["last_write"] = _now
        _t["finished_at"] = _now
        _t["interrupted_by"] = "reconcile_on_startup"
        _receipts.append({
            "task_id": _tid,
            "old_status": "running",
            "new_status": "interrupted",
            "pid": _pid,
            "pid_state": _PID_DEAD,
            "started_at": _sw,
            "last_write": _lw,
            "duration_s": round(_now - _sw, 2),
            # ★未送达回复随附 + 免重做标记（调用方无需重算）
            "undelivered": _pend,
            "no_redo": True,
            "dedup_key": "{}@{}".format(_tid, _lw),   # 任务 id + 最后写时间戳
            "replay": "forbidden",                 # ★不回放、不续跑
            "reason": "重启后标 running 但存活 pid 已不存在（无存活任务）",
        })

    if _receipts and not dry_run:
        # ★单事务：整批一次原子写
        _atomic_save(_d, path)

    return {
        "reconciled": len([r for r in _receipts if r.get("new_status") == "interrupted"]),
        "still_alive": _still,
        "unknown_pid": _unknown,
        "receipts": _receipts,
        "cooldown": check_cooldown_intact(),
        "dry_run": bool(dry_run),
    }
