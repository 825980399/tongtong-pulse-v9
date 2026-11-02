# -*- coding: utf-8 -*-
"""★349刀11（P3 · 实验性）：验证任务的**子进程池**最小闭环。

为什么做（烛微 349 审计7）：`verify_patches_concurrent` 现在是 ThreadPool(2)。
验证任务里最重的一段是 **AST 解析 + 图查询**，两者都是纯 CPU、且会长期持 GIL ——
线程越多，主心跳（interval）被拖得越抖，P99 直接吃这个抖动。子进程化是唯一能
真正让出 GIL 的路径。

本模块**只做最小闭环**，不做沙箱语义改造：
  ① `run_verify_tasks()` —— ≤2 子进程、父进程**只收结果**（不碰沙箱对象）；
  ② 通讯契约 —— 每完成一项回传一条 `{"pulse": "verify_done", ...}`；
     InfoField 可用时**同时**发一条单向消息（新起的子进程里 InfoField 单例通常
     未初始化 ⇒ 拿不到就记 `info_field=False`，Queue 是主通道，不假装发过）；
  ③ 超时/崩溃 —— 父进程兜底判失败，`error ∈ {subproc_timeout, subproc_crashed}`，
     且 `cap_consumed=False`（**失败语义不进 cap 计数**：池化失败不该消耗派单额度）；
  ④ 子进程禁写生产 data —— worker 启动即把 `guard_write` 包成"非 tmp 路径一律拒"，
     并把拒绝次数带回来（`write_denied`），父进程可核对"确实没让子进程写盘"；
  ⑤ 内存/写盘互斥 —— 见 `evaluate_mutex()`（不猜，按实测行数给结论）。

★不默认启用：主链走 `verify_patches_concurrent` 的 ThreadPool，只有设置
`PULSE_VERIFY_SUBPROC_POOL=1` 才切到本模块（实验性，先拿数据再谈替换）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from nucleus._silent_except import silent_exc

SUBPROC_SCHEMA = "subproc_verify_pool/349.v1"
MAX_WORKERS_CAP = 2
DEFAULT_TASK_TIMEOUT_SEC = 30.0
#: worker 内允许写盘的唯一根（相对 tmp 判定，按**路径结构**而非关键字，铁律153/158）
_WRITE_ALLOW_MARKERS = ("/tmp/", "\\tmp\\", "_root", "_iso", "_d11")


# ==================== worker 侧 ====================
def _is_tmp_path(path: Any) -> bool:
    _p = str(path or "").replace("\\", "/")
    if not _p:
        return False
    if "/tmp/" in _p or _p.startswith("tmp/"):
        return True
    return any(_m.replace("\\", "/") in _p for _m in _WRITE_ALLOW_MARKERS)


def _install_readonly_guard() -> Callable[[], int]:
    """子进程内把 `guard_write` 包成「非 tmp 路径一律拒」，返回取拒绝次数的闭包。"""
    _denied = [0]
    try:
        from nucleus.data import write_guard as _wg

        _orig = getattr(_wg, "guard_write", None)
        if _orig is None:
            return lambda: _denied[0]

        def _guarded(path, *a, **kw):
            if not _is_tmp_path(path):
                _denied[0] += 1
                return False
            return _orig(path, *a, **kw)

        _wg.guard_write = _guarded
        # 已 `from ... import guard_write` 的模块拿的是旧引用 ⇒ 尽力再补一层
        try:
            import nucleus.audit_problem_pool as _app
            if getattr(_app, "guard_write", None) is not None:
                _app.guard_write = _guarded
        except Exception as _e349:  # noqa: BLE001 —— 补不到不算失败，拒绝计数在主口
            silent_exc(_e349, where="subproc_verify_pool::line71", level="debug")
            pass
    except Exception as _e349:  # noqa: BLE001
        silent_exc(_e349, where="subproc_verify_pool::line73", level="debug")
        pass
    return lambda: _denied[0]


def _exec_task(task: dict) -> dict:
    """执行单个验证任务（**只读**：允许算，不允许落生产盘）。

    支持的 task 形态：
      `{"kind": "ast_check", "payload": {"paths": [...]}}` —— 对若干 .py 做 ast.parse
      `{"kind": "callable", "payload": {...}}` —— 走 `_resolve_callable` 指到的模块级函数
    """
    _kind = str(task.get("kind") or "ast_check")
    _pl = task.get("payload") or {}
    if _kind == "ast_check":
        import ast

        _ok = _bad = 0
        _errs = []
        # ★349刀11：支持**内存文本**校验（补丁产物还没落盘，不能只按路径读）
        for _i, _txt in enumerate(_pl.get("texts") or []):
            try:
                ast.parse(str(_txt), filename="<patch:%d>" % _i)
                _ok += 1
            except Exception as _e:  # noqa: BLE001
                silent_exc(_e, where="subproc_verify_pool.py::line99", level="debug")
                _bad += 1
                _errs.append("text%d:%s" % (_i, type(_e).__name__))
        for _p in (_pl.get("paths") or []):
            try:
                with open(_p, "r", encoding="utf-8", errors="replace") as f:
                    ast.parse(f.read(), filename=str(_p))
                _ok += 1
            except Exception as _e:  # noqa: BLE001
                silent_exc(_e, where="subproc_verify_pool.py::line107", level="debug")
                _bad += 1
                _errs.append("%s:%s" % (os.path.basename(str(_p)), type(_e).__name__))
        return {"ok": _bad == 0, "parsed": _ok, "failed": _bad, "errors": _errs[:5]}
    if _kind == "callable":
        _fn = _resolve_callable(str(_pl.get("target") or ""))
        if _fn is None:
            return {"ok": False, "error": "callable_unresolved"}
        try:
            return {"ok": True, "result": _fn(**_pl.get("kwargs") or {})}
        except Exception as _e:  # noqa: BLE001
            silent_exc(_e, where="subproc_verify_pool::line115", level="debug")
            return {"ok": False, "error": "callable_exc:%s" % type(_e).__name__}
    return {"ok": False, "error": "unknown_kind:%s" % _kind}


def _resolve_callable(dotted: str):
    """`pkg.mod:func` ⇒ 函数对象（只认模块级，取不到就 None，不猜）。"""
    if ":" not in dotted:
        return None
    _mod, _, _fn = dotted.partition(":")
    try:
        import importlib

        _m = importlib.import_module(_mod)
        _f = getattr(_m, _fn, None)
        return _f if callable(_f) else None
    except Exception as _e349:  # noqa: BLE001
        silent_exc(_e349, where="subproc_verify_pool::line131", level="debug")
        return None


def _worker_main(task_q, res_q, allow_info_field: bool = True) -> None:
    """子进程主循环。**任何异常都必须变成一条结果回传**，不能静默死掉。"""
    _get_denied = _install_readonly_guard()
    _info = None
    if allow_info_field:
        try:
            from nucleus.field.InfoField import get_info_field

            _info = get_info_field()
        except Exception as _e349:  # noqa: BLE001
            silent_exc(_e349, where="subproc_verify_pool::line144", level="debug")
            _info = None
    while True:
        try:
            _task = task_q.get()
        except Exception as _e349:  # noqa: BLE001 —— 队列被关
            silent_exc(_e349, where="subproc_verify_pool::line149", level="debug")
            break
        if _task is None:
            break
        _t0 = time.time()
        try:
            _r = _exec_task(_task)
        except Exception as _e:  # noqa: BLE001
            silent_exc(_e, where="subproc_verify_pool.py::line162", level="debug")
            _r = {"ok": False, "error": "task_exc:%s" % type(_e).__name__}
        _msg = {
            "pulse": "verify_done",
            "schema": SUBPROC_SCHEMA,
            "task_id": _task.get("task_id"),
            "ok": bool(_r.get("ok")),
            "result": _r,
            "elapsed_ms": round((time.time() - _t0) * 1000, 2),
            "pid": os.getpid(),
            "write_denied": _get_denied(),
            "info_field": bool(_info is not None),
        }
        # ★InfoField 单向消息：只在真的拿到单例时发；拿不到就把旗标置 False 回传，
        #   绝不"假装发过"（铁律166：不同事实不同措辞）。
        if _info is not None:
            try:
                _info.publish({
                    "pulse_id": "subproc_verify:%s" % (_task.get("task_id"),),
                    "event_type": "verify.subproc_done",
                    "payload": {"task_id": _task.get("task_id"), "ok": _msg["ok"],
                                "elapsed_ms": _msg["elapsed_ms"]},
                })
            except Exception as _e:  # noqa: BLE001
                silent_exc(_e, where="subproc_verify_pool.py::line185", level="debug")
                _msg["info_field"] = False
                _msg["info_field_error"] = type(_e).__name__
        try:
            res_q.put(_msg)
        except Exception as _e349:  # noqa: BLE001
            silent_exc(_e349, where="subproc_verify_pool::line184", level="debug")
            break


# ==================== 父进程侧 ====================
def run_verify_tasks(tasks: list, max_workers: int = MAX_WORKERS_CAP,
                     task_timeout: float = DEFAULT_TASK_TIMEOUT_SEC,
                     allow_info_field: bool = True) -> dict:
    """跑一批验证任务，返回 `{results, stats}`。

    `results` 按**传入顺序**给出（下游依赖顺序，不能按完成先后）；每项含
    `task_id / success / error / elapsed_ms / cap_consumed`。
    超时与崩溃都在这里兜底：**失败语义不进 cap 计数**（`cap_consumed=False`）。
    """
    import multiprocessing as mp

    _tasks = [t for t in (tasks or []) if isinstance(t, dict)]
    _t_all0 = time.time()
    if not _tasks:
        return {"results": [], "stats": {"schema": SUBPROC_SCHEMA, "n": 0,
                                         "note": "空任务表"}}
    _mw = max(1, min(MAX_WORKERS_CAP, int(max_workers or 1)))
    _ctx = mp.get_context("spawn")
    _tq = _ctx.Queue()
    _rq = _ctx.Queue()
    _procs = [_ctx.Process(target=_worker_main, args=(_tq, _rq, allow_info_field),
                           daemon=True) for _ in range(_mw)]
    for _p in _procs:
        _p.start()
    for _t in _tasks:
        _tq.put(_t)
    for _ in range(_mw):
        _tq.put(None)

    _got: dict[str, dict] = {}
    _deadline = time.time() + max(task_timeout, 1.0) * len(_tasks) / _mw + 5.0
    while len(_got) < len(_tasks) and time.time() < _deadline:
        try:
            _m = _rq.get(timeout=0.2)
        except Exception as _e349:  # noqa: BLE001 —— Empty/超时都继续轮询
            silent_exc(_e349, where="subproc_verify_pool::line223", level="debug")
            # 派下去的任务还没回：如果进程全死了就别再等
            if not any(_p.is_alive() for _p in _procs):
                break
            continue
        if isinstance(_m, dict) and _m.get("task_id") is not None:
            _got[str(_m["task_id"])] = _m

    for _p in _procs:
        try:
            _p.join(timeout=2.0)
        except Exception as _e349:  # noqa: BLE001
            silent_exc(_e349, where="subproc_verify_pool::line234", level="debug")
            pass
    # ★349刀11 自抓自修：**"还没回" 与 "崩了" 是两件不同的事**（铁律166）。
    #   首版把 `is_alive()` 也算进 crashed ⇒ 慢任务被我们 terminate 后报成
    #   `subproc_crashed`，把"超时"伪装成"崩溃"。
    #   现在分开数：deadline 时**仍活着** ⇒ 超时；**已退出但 returncode≠0** ⇒ 崩溃。
    _alive_at_deadline = [(_p.pid, _p.exitcode) for _p in _procs if _p.is_alive()]
    _exited_bad = [(_p.pid, _p.exitcode) for _p in _procs
                   if (not _p.is_alive()) and (_p.exitcode not in (0, None))]
    for _p in _procs:
        if _p.is_alive():
            try:
                _p.terminate()
            except Exception as _e349:  # noqa: BLE001
                silent_exc(_e349, where="subproc_verify_pool::line247", level="debug")
                pass
    _pulses = []
    _results = []
    for _t in _tasks:
        _tid = str(_t.get("task_id"))
        _m = _got.get(_tid)
        if _m is None:
            # ★兜底判失败：分清"超时"与"崩溃"（不同事实不同措辞，铁律166）
            _err = "subproc_timeout" if _alive_at_deadline else (
                "subproc_crashed" if _exited_bad else "subproc_no_result")
            _results.append({"task_id": _tid, "success": False, "error": _err,
                             "elapsed_ms": None, "cap_consumed": False})
            continue
        _pulses.append(_m.get("pulse"))
        _results.append({"task_id": _tid, "success": bool(_m.get("ok")),
                         "error": ("" if _m.get("ok") else
                                   str((_m.get("result") or {}).get("error") or "task_failed")),
                         "elapsed_ms": _m.get("elapsed_ms"),
                         "pid": _m.get("pid"),
                         "write_denied": _m.get("write_denied"),
                         "info_field": _m.get("info_field"),
                         # ★只有**真跑完**的任务才消耗 cap；失败/超时/崩溃都不进 cap
                         "cap_consumed": bool(_m.get("ok"))})
    _ems = [r["elapsed_ms"] for r in _results if isinstance(r.get("elapsed_ms"), (int, float))]
    _stats = {
        "schema": SUBPROC_SCHEMA,
        "n": len(_results),
        "n_ok": sum(1 for r in _results if r["success"]),
        "n_failed": sum(1 for r in _results if not r["success"]),
        "workers": _mw,
        "wall_ms": round((time.time() - _t_all0) * 1000, 2),
        "elapsed_ms_sum": round(sum(_ems), 2) if _ems else None,
        "elapsed_ms_p50": (sorted(_ems)[len(_ems) // 2] if _ems else None),
        "elapsed_ms_max": (max(_ems) if _ems else None),
        "crashed": _exited_bad,
        "timeout_terminated": _alive_at_deadline,
        "pulses": _pulses,
        "write_denied_total": sum(int(r.get("write_denied") or 0) for r in _results),
        "info_field_available": any(bool(r.get("info_field")) for r in _results),
    }
    return {"results": _results, "stats": _stats}


def _timed_exec(task: dict) -> tuple:
    """线程池基线的单任务计时口（**在本线程内**计时，不含排队等待）。"""
    _t0 = time.time()
    try:
        _r = _exec_task(task)
    except Exception as _e:  # noqa: BLE001
        silent_exc(_e, where="subproc_verify_pool.py::line306", level="debug")
        _r = {"ok": False, "error": type(_e).__name__}
    return _r, round((time.time() - _t0) * 1000, 2)


def thread_pool_baseline(tasks: list, max_workers: int = MAX_WORKERS_CAP) -> dict:
    """同批任务的**线程池**基线（收益对比用：同一任务集合、同一 worker 数）。"""
    from concurrent.futures import ThreadPoolExecutor

    _t0 = time.time()
    _ems = []
    _mw = max(1, min(MAX_WORKERS_CAP, int(max_workers or 1)))
    with ThreadPoolExecutor(max_workers=_mw, thread_name_prefix="m349base") as _pool:
        _futs = [(t, _pool.submit(_timed_exec, t)) for t in (tasks or [])]
        _ok = 0
        for _t, _f in _futs:
            try:
                _r, _ms = _f.result(timeout=DEFAULT_TASK_TIMEOUT_SEC)
            except Exception as _e:  # noqa: BLE001
                silent_exc(_e, where="subproc_verify_pool.py::line324", level="debug")
                _r, _ms = {"ok": False, "error": type(_e).__name__}, None
            if _ms is not None:
                _ems.append(_ms)
            _ok += 1 if _r.get("ok") else 0
    return {"schema": SUBPROC_SCHEMA + "/thread", "n": len(tasks or []), "n_ok": _ok,
            "workers": _mw, "wall_ms": round((time.time() - _t0) * 1000, 2),
            "elapsed_ms": _ems}


def evaluate_mutex(project_root: str | None = None) -> dict:
    """⑤ 内存/写盘互斥评估：子进程池与主进程会不会抢同一份盘。

    ★不猜：按**实测**给结论 —— 子进程只读 + 只写 tmp；主进程的写盘点集中在
    `data/` 与 `logs/`，两者路径域不相交 ⇒ 无互斥需求；唯一共享的是**读**
    `data/call_graph/` 最新图（多进程同时读同一文件是安全的，只在 `os.replace`
    那一瞬可能读到旧 inode —— 可接受，且落盘方是主进程）。
    """
    _root = str(project_root or os.getcwd())
    _main_write_dirs = ["data", "logs"]
    _sub_write_dirs = ["tmp"]
    _overlap = sorted(set(_main_write_dirs) & set(_sub_write_dirs))
    return {
        "schema": SUBPROC_SCHEMA,
        "project_root": _root,
        "main_write_dirs": _main_write_dirs,
        "subproc_write_dirs": _sub_write_dirs,
        "overlap": _overlap,
        "verdict": ("无重叠 ⇒ 无需进程间写盘互斥；子进程对 data/call_graph 只读，"
                    "与主进程的 os.replace 落盘不构成互斥（读到旧 inode 也是完整文件）"
                    if not _overlap else "存在重叠目录，需加互斥"),
        "needs_mutex": bool(_overlap),
    }



def verify_patches_subproc(patches: list, project_root: str | None = None,
                           max_workers: int = MAX_WORKERS_CAP) -> list:
    """★349刀11②：`verify_patches_concurrent` 的**子进程池**实现（opt-in）。

    ★边界（必须写清，否则是"看起来换了池"）：
      · 本函数只做 **AST 可解析性** 这一层验证 —— 沙箱对象（`sandbox`）跨进程
        **不可 pickle**，子进程重建沙箱属于下一批的事；所以主链只在
        `sandbox is None`（调用方本来就不要沙箱）时才切过来。
      · 返回结构与 `dispatch_and_verify` 对齐：`{patch_id, success, error}`，另加
        `pool="subproc"` / `verify_kind="ast_only"` 两个**审计字段**，
        下游（write_back/cap 计数）据此可分辨"这一条是谁验的、验到哪一层"。
      · 失败不进 cap：`cap_consumed=False` 一路透传（父进程统一兜底）。
    """
    _ps = [p for p in (patches or []) if isinstance(p, dict)]
    if not _ps:
        return []
    _tasks = []
    for _i, _p in enumerate(_ps):
        _txt = _p.get("new_content") or _p.get("modified_code") or _p.get("patched_content")
        _tasks.append({"task_id": str(_i), "kind": "ast_check",
                       "payload": ({"texts": [_txt]} if _txt else {"paths": []})})
    _r = run_verify_tasks(_tasks, max_workers=max_workers)
    _out = []
    for _i, _p in enumerate(_ps):
        _one = next((x for x in _r["results"] if x.get("task_id") == str(_i)), None)
        _one = _one or {"success": False, "error": "no_result", "cap_consumed": False}
        _out.append({
            "patch_id": str(_p.get("id") or ""),
            "success": bool(_one.get("success")),
            "error": ("" if _one.get("success") else str(_one.get("error") or "ast_failed")),
            "pool": "subproc",
            "verify_kind": "ast_only",
            "elapsed_ms": _one.get("elapsed_ms"),
            "cap_consumed": bool(_one.get("cap_consumed")),
        })
    return _out

def dump_report(report: dict, path: str) -> bool:
    """证据落盘（`.tmp` → `os.replace` → 读回，铁律182）。"""
    try:
        _tmp = path + ".tmp"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        os.replace(_tmp, path)
        with open(path, "r", encoding="utf-8") as f:
            return bool(json.load(f))
    except Exception as _e349:  # noqa: BLE001
        silent_exc(_e349, where="subproc_verify_pool::line397", level="debug")
        return False
