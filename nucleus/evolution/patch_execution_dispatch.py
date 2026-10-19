# -*- coding: utf-8 -*-
"""第295批刀2/刀3：行动面通电——派单路径 + 执行结果回写。

刀2：从 patch_history 取 status=runtime_verified 且 risk=低 的补丁（13 条先例），
     按风险排序（低风险优先），单批上限 1 条，路由 motor.execute（PulseCodeSandbox）执行。
刀3：执行后回写 patch_history 的 runtime_verified（成功 True / 失败 False + 原因）。

边界（任务书明令）：
  * 首批只路由 runtime_verified 类低风险补丁（13 条先例）；
  * 单批上限 1 条；
  * 黑名单硬约束 = PulseCodeSandbox 自带的 DANGEROUS_KEYWORDS 安全检查（派单不绕过）；
  * 这是「执行验证」不是「放权自改」——只在沙箱子进程里 exec，绝不 apply 到源码。

挂载：register_with_daily_schedule 注册为 DailyScheduler 每日钩子（每日派单 1 条）。
framework 经 set_dispatch_framework 注入（main.py 装配后调用）。
"""
from __future__ import annotations

import io
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("PatchExecutionDispatch")

#: 全局 framework 桥接（main.py 装配后 set_dispatch_framework 注入）
_framework: Any = None


def set_dispatch_framework(fw: Any) -> None:
    """main.py 装配完成后注入 framework（供派单拿 code_sandbox 器官）。"""
    global _framework
    _framework = fw


def get_dispatch_framework() -> Any:
    return _framework


def _history_path() -> str:
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(_root, "data", "patches", "patch_history.json")


def _extract_def_name(code: str) -> str:
    """提取代码首个 def 名（与 PatchManager._m280_extract_def_name 同源）；无 def → 空串。"""
    if not code or not isinstance(code, str):
        return ""
    try:
        import re
        _m = re.search(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", code, re.MULTILINE)
        return _m.group(1) if _m else ""
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_execution_dispatch::_extract_def_name")
        return ""


def is_fragment_to_full(patch: dict) -> bool:
    """★第295批刀5形态判据：original 是方法体片段（无 def）而 modified 是完整方法（含 def）。

    该形态应用到原方法体内会「再嵌一个 def」→ 结构破坏 → 判可疑，不派单。
    """
    _orig = patch.get("original_code") or ""
    _mod = patch.get("modified_code") or ""
    return (_extract_def_name(_orig) == "") and (_extract_def_name(_mod) != "")


#: ★第305批 刀4：落盘调用图**读缓存**（对齐 DailyScheduler:573 既有口径
#:   「运行时消费者只读缓存，绝不现扫」）。304 刀5 在 select 循环里每条候选
#:   json.load 一次 3.1MB 图 ⇒ N×3MB 磁盘读，与该口径冲突。
_call_graph_cache: dict[str, Any] = {"path": None, "graph": None,
                                     "out_adj": None, "in_adj": None}


def load_call_graph_cache(force: bool = False) -> dict[str, Any] | None:
    """装载最新落盘调用图（**带缓存**）；无图 ⇒ None。

    同时构建出边/入边邻接表（`out_adj` / `in_adj`），供牵连半径 BFS 使用。
    """
    global _call_graph_cache
    try:
        import glob as _g
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _files = sorted(_g.glob(os.path.join(_root, "data", "call_graph", "graph_*.json")))
        if not _files:
            return None
        _latest = _files[-1]
        if (not force and _call_graph_cache["path"] == _latest
                and _call_graph_cache["graph"] is not None):
            return _call_graph_cache["graph"]
        with open(_latest, encoding="utf-8") as _f:
            _gd = json.load(_f)
        _out_adj: dict[str, list[str]] = {}
        _in_adj: dict[str, list[str]] = {}
        for _e in (_gd.get("edges") or []):
            if isinstance(_e, dict) and _e.get("caller") and _e.get("callee"):
                _out_adj.setdefault(_e["caller"], []).append(_e["callee"])
                _in_adj.setdefault(_e["callee"], []).append(_e["caller"])
        _call_graph_cache = {"path": _latest, "graph": _gd,
                             "out_adj": _out_adj, "in_adj": _in_adj}
        return _gd
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_execution_dispatch::load_call_graph_cache")
        return None


def _bfs_count(adj: dict[str, list[str]], starts: list[str], max_depth: int) -> int:
    """从起点集沿邻接表 BFS，返回可达节点数（不含起点自身）。"""
    _seen = set(starts)
    _frontier = list(starts)
    for _ in range(max(1, int(max_depth))):
        _next: list[str] = []
        for _nid in _frontier:
            for _nxt in adj.get(_nid, []):
                if _nxt not in _seen:
                    _seen.add(_nxt)
                    _next.append(_nxt)
        _frontier = _next
        if not _frontier:
            break
    return len(_seen) - len(set(starts))


def blast_radius_of(qualified: str, file: str | None = None,
                    max_depth: int = 3, direction: str = "in") -> int | None:
    """★第305批 刀3：牵连半径 —— 默认**入边方向（谁依赖我）**。

    304 刀5 的三处缺陷与本批修正：
      ① **方向错**：沿 caller→callee 出边 BFS ⇒ 度量的是「我依赖谁」，
         而改动风险的实际来源是「**谁依赖我**」⇒ 默认改 `direction="in"`（入边）。
      ② **缺名返回 0** ⇒ 调用方按「不拦」放行，把「查不到」当成「牵连小」（**假通过**）
         ⇒ 改为**返回 None**，调用方 `is None` 必须**转人工**。
      ③ **裸 name 起 BFS** ⇒ 同名方法跨文件合并，量到的是别人的半径
         ⇒ **起点消歧**：优先用 `file` + 方法名精确定位（补丁的 file/method 字段），
         其次要求 `Class.method` / `module.Class.method` 限定名；
         仅给裸 name 且无 file ⇒ 返回 None（消歧失败，转人工）。

    Returns: 可达节点数（不含起点）；**图缺失或起点无法确定 ⇒ None**（绝不返回 0 冒充「牵连小」）。
    """
    if not qualified or not isinstance(qualified, str):
        return None
    _gd = load_call_graph_cache()
    if not _gd:
        return None
    _nodes = _gd.get("nodes") or []
    _q = qualified.strip()
    _f = str(file or "").replace("\\", "/").strip()
    _name = _q.split(".")[-1]
    _starts: list[str] = []
    # 一级：文件 + 方法名（补丁的 file/method ⇒ 精确，不要求类限定，避免「饿死」）
    if _f and _name:
        for _n in _nodes:
            if not isinstance(_n, dict):
                continue
            if (str(_n.get("file", "") or "").replace("\\", "/") == _f
                    and str(_n.get("name") or "") == _name):
                _starts.append(_n.get("id"))
    # 二级：限定名（Class.method / module.Class.method）
    if not _starts and "." in _q:
        for _n in _nodes:
            if not isinstance(_n, dict):
                continue
            _nid = str(_n.get("id") or "")
            _qual = str(_n.get("qualname") or "")
            if _nid.endswith(_q) or _qual == _q or _qual.endswith("." + _q):
                _starts.append(_nid)
    if not _starts:
        _module_logger.info("[305刀3] 起点无法定位（name=%s file=%s）⇒ 转人工", _q, _f or "-")
        return None
    _adj = _call_graph_cache.get("in_adj") if direction == "in" else _call_graph_cache.get("out_adj")
    if _adj is None:
        return None
    return _bfs_count(_adj, [str(_s) for _s in _starts if _s], max_depth)


def select_patch_for_execution(patch_history: list, limit: int = 1,
                               max_blast_radius: int = 1) -> list:
    """从 patch_history 选「status=runtime_verified 且 risk=低」的补丁（低风险优先）。

    纯函数（便于隔离单测）；只读，不改任何字段。
    ★第295批刀5：先跑形态判据回扫，「片段→完整」（嵌套 def）补丁过滤掉，不派单。
    ★第304批刀5：再查**牵连半径**（读落盘调用图）—— 牵连节点数 > `max_blast_radius`
      者**不入队、转人工**（自动分诊的前置依赖；图缺失时 blast=0 ⇒ 按「不拦」处理，零回归）。
    """
    if not isinstance(patch_history, list):
        return []
    _cands = [p for p in patch_history if isinstance(p, dict)
              and p.get("status") == "runtime_verified"
              and p.get("risk_level") in ("低", "low")]
    # ★第298批刀3：is_fragment_to_full 为 select 永久排除项（非首批边界）——
    #   即使后续放宽到 approved（71条），片段→完整结构损坏补丁（含2条problem_fixed=True）也绝不派单。
    _safe = [p for p in _cands if not is_fragment_to_full(p)]
    # ★第304批刀5：牵连半径闸门（> max_blast_radius ⇒ 转人工，不自动派单）
    _scoped = []
    for _p in _safe:
        _m = str(_p.get("method") or "")
        _f = str(_p.get("file") or "")
        # ★第305批刀3：入边方向（谁依赖我）；**None = 不可判定 ⇒ 转人工**（禁止当 0 放行）
        _br = blast_radius_of(_m, file=_f)
        if _br is None:
            _module_logger.info(
                "[305刀3] 牵连不可判定（图缺/起点未定位）⇒ 转人工: patch=%s method=%s file=%s",
                str(_p.get("id", ""))[:16], _m, _f or "-")
            continue
        if _br > max_blast_radius:
            _module_logger.info(
                "[304刀5] 牵连半径超限转人工: patch=%s method=%s blast=%d > %d",
                str(_p.get("id", ""))[:16], _m, _br, max_blast_radius)
            continue
        _scoped.append(_p)
    # 低风险优先；同风险按 trust_score 降序
    _scoped.sort(key=lambda p: (
        0 if p.get("risk_level") in ("低", "low") else 1,
        -(float(p.get("trust_score", 0) or 0))))
    return _scoped[:limit]


def dispatch_and_verify(sandbox: Any, patch: dict) -> dict:
    """把补丁 modified_code 路由 sandbox 执行，返回执行结论（不落盘，由调用方回写）。

    执行走 PulseCodeSandbox.on_pulse(MotorEvent.EXECUTE)，命中其 DANGEROUS_KEYWORDS
    黑名单与子进程隔离沙箱；绝不 apply 到源码。
    """
    _pid = patch.get("id", "unknown")
    _code = patch.get("modified_code") or patch.get("original_code") or ""
    _lang = patch.get("language", "python")
    if not _code:
        return {"patch_id": _pid, "success": False, "error": "empty_code"}
    try:
        _res = sandbox.on_pulse({
            "event_type": "motor.execute",  # 等价 MotorEvent.EXECUTE
            "payload": {"code": _code, "language": _lang,
                        "user_name": "patch_dispatch_295", "task_id": _pid},
        })
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_execution_dispatch::dispatch_and_verify sandbox")
        return {"patch_id": _pid, "success": False,
                "error": f"sandbox_exc:{type(e).__name__}"}
    _ok = bool(isinstance(_res, dict) and _res.get("success"))
    _err = "" if _ok else ((_res or {}).get("error", "") if isinstance(_res, dict) else str(_res))
    return {"patch_id": _pid, "success": _ok, "error": _err}


def _write_back(patch: dict, verdict: dict) -> None:
    """刀3：把执行结果回写补丁字段（内存态；落盘由调用方统一原子写）。"""
    _ok = bool(verdict.get("success"))
    _err = verdict.get("error", "")
    patch["runtime_verified"] = _ok
    patch["runtime_verify_result"] = {
        "verified": _ok,
        "detail": ("[执行验证] motor.execute " + ("成功" if _ok else "失败")
                   + (f": {_err}" if _err else "")),
        "execution_source": "motor.execute",
        "executed_at": time.time(),
    }
    if not _ok:
        patch["runtime_verify_failure_reason"] = _err


def dispatch_runtime_verified_patches(limit: int = 1, framework: Any = None) -> dict:
    """主入口：每日派单 ≤1 条 runtime_verified 低风险补丁到 motor.execute，回写 runtime_verified。"""
    _fw = framework or get_dispatch_framework()
    if _fw is None:
        return {"status": "no_framework", "dispatched": 0}
    _sandbox = getattr(_fw, "organs", {}).get("代码沙箱")
    if _sandbox is None or not hasattr(_sandbox, "on_pulse"):
        return {"status": "no_sandbox", "dispatched": 0}
    if not getattr(_sandbox, "is_running", False):
        return {"status": "sandbox_stopped", "dispatched": 0}
    _hp = _history_path()
    if not os.path.isfile(_hp):
        return {"status": "no_history", "dispatched": 0}
    try:
        _hist = json.load(io.open(_hp, encoding="utf-8"))
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_execution_dispatch::dispatch load history")
        return {"status": "load_failed", "dispatched": 0}
    if isinstance(_hist, dict):
        _hist = _hist.get("patches") or []
    if not isinstance(_hist, list):
        return {"status": "history_not_list", "dispatched": 0}
    # ★第295批刀5：派单前先回扫全部 runtime_verified 低风险补丁，标记「片段→完整」可疑形态
    _rv_cands = [p for p in _hist if isinstance(p, dict)
                 and p.get("status") == "runtime_verified"
                 and p.get("risk_level") in ("低", "low")]
    _suspect_n = 0
    for _p in _rv_cands:
        if is_fragment_to_full(_p):
            _p["_295_suspect_morphology"] = True
            _p["_295_suspect_reason"] = "片段→完整(嵌套def风险)"
            _suspect_n += 1
    _selected = select_patch_for_execution(_hist, limit=limit)
    _results = []
    for _p in _selected:
        _v = dispatch_and_verify(_sandbox, _p)
        _write_back(_p, _v)
        _results.append(_v)
    # 落盘（原子写；失败只 warning 不影响调度主流程）——有标记或有执行结果即落盘
    if _results or _suspect_n:
        try:
            _tmp = _hp + ".m295.tmp"
            with io.open(_tmp, "w", encoding="utf-8") as _f:
                json.dump(_hist, _f, ensure_ascii=False, indent=2)
            os.replace(_tmp, _hp)
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.patch_execution_dispatch::dispatch save history")
    _module_logger.info("[行动面通电] 回扫=%d 可疑=%d 派单=%d 结果=%s",
                        len(_rv_cands), _suspect_n, len(_results),
                        [(r.get("patch_id"), r.get("success")) for r in _results])
    return {"status": "ok", "reswept": len(_rv_cands), "suspect": _suspect_n,
            "dispatched": len(_results), "results": _results}


def register_with_daily_schedule(scheduler: Any = None) -> str:
    """把派单挂入 DailyScheduler 每日钩子（单批上限 1 条，每日 1 次）。"""
    if scheduler is None:
        from nucleus.self_awareness.DailyScheduler import get_daily_scheduler
        scheduler = get_daily_scheduler()
    if not hasattr(scheduler, "add_daily_hook"):
        return ""
    return scheduler.add_daily_hook(
        "patch_execution_dispatch_295",
        lambda: dispatch_runtime_verified_patches(limit=1))
