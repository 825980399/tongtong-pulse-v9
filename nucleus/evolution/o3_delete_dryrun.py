# -*- coding: utf-8 -*-
"""O3 删死代码放行 **dry-run 报表**（★412 批刀4）—— 只统计，不 apply。

T0 实测（两处与任务书不同，处置记在这里）
------------------------------------------
1. **样本根本不在补丁层**。任务书说"O3 类删死代码全被窄闸挡死"，但
   `pending_patches.json` 里只有 **1 条** o3 补丁；O3 的本体在**问题池**
   `data/reports/audit/audit_YYYYMMDD.json`：今日 647 条，`dead_code` **590 条**
   （580 open / 10 claimed），而**这 590 条一条都没有 `modified_code` /
   `original_code` / `diff`** ⇒ `patch_risk_gate.diff_kind()` 对它们恒为 `unknown`。
   ⇒ 所以"如果开闸会放行多少条"只能**在问题层**用另一套判据算（见下），
      不能等补丁生成后再判（那时本批没有样本）。

2. **"无入边"能力存在，但不叫那个名**。任务书说"在 CallGraphAnalyzer 里确认无入边"，
   实测该类的公开 API 是 **`get_isolated_functions()`**（返回
   `{id,name,file,line,type,public_api,entry_like}`），没有 `in_edge/callers/orphan`。

★397 批的教训必须带上：**调用图"零入度" ≠ 真孤儿** —— 文件压根没被扫进图时，
   入度也是 0（"没采到"会被读成"没人调用"）。所以本判据要求
   **该文件在图内** 且 **该方法是孤立函数**，覆盖不可判就**不放行**。

判据（三者全满足才 eligible）
----------------------------
  a. `issue_type ∈ (dead_code, dead_code_candidate)` 且 `status == "open"`；
  b. 目标方法在调用图里**在图内**且是孤立函数（入度 0），且非 public_api、非 entry_like；
  c. 目标文件**不在核心清单**（审批池/状态机/审计池一类）。

产物：只出报表，**不真 apply 任何 O3 补丁**；`eligible` 在图不可用时为 `None`
（不报 0 —— 0 会被读成"没有可放行的"）。
"""

from __future__ import annotations

import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("O3DeleteDryRun")

#: ★414刀1 起判据换版：public_api 改实证三源、新增 test_only 拒因。
#: **前缀必须保持 "412."** —— 412 单测 test_36/test_43 钉的就是这个前缀。
_M412_DD_VERSION = "412.5"

#: 灰度开关
SWITCH = "ENABLE_M412_O3_DELETE_DRYRUN"

#: O3 判死族（与 `audit_problem_pool._M383_O3_DEAD_TYPES` 同集；有守卫测试断言一致）
O3_DEAD_TYPES = ("dead_code", "dead_code_candidate")

#: 核心文件：删这些一律不放行（与 `patch_risk_gate.APPROVAL_POOL_FILES` 同源思路）
CORE_FILES = frozenset((
    "config.py", "main.py", "nucleus/const.py",
    "nucleus/reasoning/PatchManager.py",
    "nucleus/evolution/patch_risk_gate.py",
    "nucleus/evolution/patch_execution_dispatch.py",
    "nucleus/audit_problem_pool.py",
))


def enabled() -> bool:
    """灰度开关 `ENABLE_M412_O3_DELETE_DRYRUN`；缺省 True（**只统计，不 apply**）。"""
    try:
        import config as _c
        return bool(getattr(_c, SWITCH, True))
    except Exception as _e:
        silent_exc(_e, where="evolution.o3_delete_dryrun:enabled")
        return True


# ------------------------------------------------------------------ 输入

def load_o3_entries(now: float | None = None, entries: Any = None) -> dict[str, Any]:
    """取今日问题池条目（可用 `entries` 注入替身）。"""
    if isinstance(entries, list):
        return {"state": "ok", "entries": [x for x in entries if isinstance(x, dict)],
                "source": "injected", "reason": ""}
    try:
        import nucleus.audit_problem_pool as ap
        _p = ap.snapshot_path(now=now)
        if not os.path.exists(_p):
            return {"state": "empty", "entries": [], "source": _p,
                    "reason": "snapshot_not_found"}
        _d = ap.read_snapshot(_p)
        _rows = _d.get("entries") if isinstance(_d, dict) else _d
        if not isinstance(_rows, list):
            return {"state": "empty", "entries": [], "source": _p,
                    "reason": "snapshot_has_no_entries"}
        return {"state": "ok", "entries": [x for x in _rows if isinstance(x, dict)],
                "source": _p, "reason": ""}
    except Exception as _e:
        silent_exc(_e, where="evolution.o3_delete_dryrun:load_o3_entries", level="warning")
        return {"state": "unavailable", "entries": [], "source": "",
                "reason": "load_failed:%s" % type(_e).__name__}


# ------------------------------------------------------------------ 调用图索引

def isolated_index(graph: Any = None) -> dict[str, Any]:
    """造"孤儿方法索引"。

    Args:
        graph: 调用图实例（需有 `get_isolated_functions()`）；None 时自己构造
            `CallGraphAnalyzer`（**慢**，一天跑一次可接受；单测请注入替身）。

    Returns:
        ``{"state": ok|empty|unavailable, "orphans": {(file, name): row},
           "files": set|None, "reason": str}``
    """
    _out: dict[str, Any] = {"state": "unavailable", "orphans": {}, "files": None,
                            "reason": ""}
    _g = graph
    try:
        if _g is None:
            from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
            _g = CallGraphAnalyzer()
        _iso = _g.get_isolated_functions()
    except Exception as _e:
        silent_exc(_e, where="evolution.o3_delete_dryrun:isolated_index", level="warning")
        _out["reason"] = "graph_failed:%s" % type(_e).__name__
        return _out
    if not isinstance(_iso, list):
        _out["reason"] = "graph_returned_non_list"
        return _out
    _orph: dict[tuple[str, str], dict] = {}
    _files: set[str] = set()
    for _r in _iso:
        if not isinstance(_r, dict):
            continue
        _f = str(_r.get("file") or "").replace("\\", "/")
        _n = str(_r.get("name") or "")
        if _f and _n:
            _orph[(_f, _n)] = _r
            _files.add(_f)
    # 覆盖：尽量拿到"图扫过哪些文件"，拿不到就只能用孤儿清单里的文件（欠覆盖）
    try:
        _gd = _g._ensure() if hasattr(_g, "_ensure") else None
        if isinstance(_gd, dict):
            for _n in (_gd.get("nodes") or []):
                if isinstance(_n, dict) and _n.get("file"):
                    _files.add(str(_n["file"]).replace("\\", "/"))
    except Exception as _e:
        silent_exc(_e, where="evolution.o3_delete_dryrun:isolated_index:nodes",
                   level="debug")
    _out["orphans"] = _orph
    _out["files"] = _files or None
    if not _orph and not _files:
        _out["state"] = "empty"
        _out["reason"] = "graph_returned_nothing（可能是扫描开关关着 ⇒ 别把 0 当成「没孤儿」）"
        return _out
    _out["state"] = "ok"
    return _out


# ------------------------------------------------------------------ 单条判据

def judge_entry(entry: dict, index: dict[str, Any]) -> dict[str, Any]:
    """判一条 O3 条目"如果开闸会不会放行"。

    Returns:
        ``{"eligible": bool|None, "checks": {...}, "reasons": [...]}``
        ★`eligible=None` = **判不了**（覆盖不可判），与 `False` 不同。
    """
    _checks: dict[str, Any] = {}
    _reasons: list[str] = []
    if not isinstance(entry, dict):
        return {"eligible": False, "checks": {}, "reasons": ["not_a_dict"]}

    _f = str(entry.get("file") or "").replace("\\", "/")
    _m = str(entry.get("method") or "")
    _t = str(entry.get("issue_type") or "")
    _s = str(entry.get("status") or "open")

    _ok_t = _t in O3_DEAD_TYPES
    _checks["issue_type"] = _t
    if not _ok_t:
        _reasons.append("not_o3_type")

    _ok_s = _s == "open"
    _checks["status"] = _s
    if not _ok_s:
        _reasons.append("not_open")

    _files = (index or {}).get("files")
    _orph = (index or {}).get("orphans") or {}
    if _files is None:
        _checks["in_graph"] = None
        _reasons.append("graph_coverage_unknown")
    else:
        _checks["in_graph"] = _f in _files
        if not _checks["in_graph"]:
            _reasons.append("file_not_in_graph（397 教训：没采到 ≠ 没人调用）")

    _row = _orph.get((_f, _m))
    _checks["orphan"] = _row is not None
    if _row is None:
        _reasons.append("method_not_isolated")
    else:
        _checks["public_api"] = bool(_row.get("public_api"))
        _checks["entry_like"] = bool(_row.get("entry_like"))
        # ★414刀1 改动3：测试引用前置排除。
        #   413 抽查的 15 条"仅测试引用"删了 `pytest` 立刻红 ⇒ 它们不是死代码，
        #   是**测试的唯一消费者**。单列 `test_only` 拒因（不混进 public_api），
        #   这样"被测试用"和"对外契约"在审计时能分开找不同人确认。
        _checks["test_ref"] = bool(_row.get("test_ref"))
        if _row.get("public_api"):
            _reasons.append("public_api（在 __all__ 或反射表里）")
        if _row.get("entry_like"):
            _reasons.append("entry_like（像入口）")
        if _row.get("test_ref"):
            _reasons.append("test_only（被测试直接引用，删了 pytest 就红）")
        # ★414刀1 补档：方法被当作**值**传递（`Thread(target=self._monitor_loop)`、
        #   `_pool.submit(self._run_guarded, ...)`）—— 调用图里没有 call 边，
        #   但它是线程体/回调/定时器入口，删了功能直接断。
        #   414 抽样实测：30 条里 4 条假死**全部**是这一类（13.3%）。
        _checks["value_ref"] = bool(_row.get("value_ref"))
        if _row.get("value_ref"):
            _reasons.append("value_ref（被当作值传递/线程体/回调，静态图无 call 边）")
        # ★414刀1 补档：`getattr(obj, "方法名")` 字面派发 —— 显式按名调用证据。
        #   414 复验时修完 value_ref 剩下的 4 条假死**全部**是这一类。
        _checks["getattr_ref"] = bool(_row.get("getattr_ref"))
        if _row.get("getattr_ref"):
            _reasons.append("getattr_ref（有 getattr/hasattr 字面名派发）")

    _core = _f in CORE_FILES
    _checks["core_file"] = _core
    if _core:
        _reasons.append("core_file（核心文件不许删）")

    if (index or {}).get("state") != "ok":
        return {"eligible": None, "checks": _checks,
                "reasons": _reasons + ["graph_unavailable"]}
    if "graph_coverage_unknown" in _reasons:
        return {"eligible": None, "checks": _checks, "reasons": _reasons}
    return {"eligible": not _reasons, "checks": _checks, "reasons": _reasons}


# ------------------------------------------------------------------ 报表

def report(entries: Any = None, graph: Any = None, now: float | None = None,
           sample: int = 5) -> dict[str, Any]:
    """dry-run 报表：**若开闸会放行多少条**（**不 apply**）。"""
    _now = float(now if now is not None else time.time())
    _src = load_o3_entries(now=_now, entries=entries)
    _idx = isolated_index(graph) if enabled() else {"state": "unavailable",
                                                    "orphans": {}, "files": None,
                                                    "reason": "disabled"}
    _out: dict[str, Any] = {
        "version": _M412_DD_VERSION,
        "generated_at": round(_now, 3),
        "dry_run": True,
        "applied": 0,
        "source": _src.get("source", ""),
        "entries_state": _src.get("state"),
        "scanned": 0,
        "o3_total": 0,
        "eligible": None,
        "undecidable": 0,
        "rejected": {},
        "samples": [],
        "graph": {"state": _idx.get("state"), "orphans": len(_idx.get("orphans") or {}),
                  "files": (len(_idx["files"]) if _idx.get("files") else None),
                  "reason": _idx.get("reason", "")},
        "basis": ("a)O3判死族且open b)在图内且孤立(非public_api/非entry_like/"
                  "非test_only) c)非核心文件 —— 三者全满足；图不可判 ⇒ 不放行"
                  "｜414起 public_api=在__all__或在main.py反射表；test_only=被tests/引用；"
                  "value_ref=被当作值传递(Thread target/submit 实参)；"
                  "getattr_ref=有 getattr/hasattr 字面名派发"),
        "reason": "",
    }
    if _src.get("state") != "ok":
        _out["reason"] = _src.get("reason") or "entries_unavailable"
        return _out

    _rows = _src["entries"]
    _out["scanned"] = len(_rows)
    _o3 = [x for x in _rows if str(x.get("issue_type") or "") in O3_DEAD_TYPES]
    _out["o3_total"] = len(_o3)

    _elig = 0
    _und = 0
    _rej: dict[str, int] = {}
    _samples: list[dict] = []
    for _e in _o3:
        _j = judge_entry(_e, _idx)
        if _j["eligible"] is True:
            _elig += 1
            if len(_samples) < max(0, int(sample)):
                _samples.append({"id": str(_e.get("id") or "")[:24],
                                 "file": str(_e.get("file") or ""),
                                 "method": str(_e.get("method") or ""),
                                 "line": _e.get("line")})
        elif _j["eligible"] is None:
            _und += 1
            for _r in _j["reasons"]:
                _rej[_r] = _rej.get(_r, 0) + 1
        else:
            for _r in _j["reasons"]:
                _rej[_r] = _rej.get(_r, 0) + 1
    if _idx.get("state") == "ok":
        _out["eligible"] = _elig
    else:
        _out["reason"] = _idx.get("reason") or "graph_unavailable"
    _out["undecidable"] = _und
    _out["rejected"] = dict(sorted(_rej.items(), key=lambda kv: -kv[1]))
    _out["samples"] = _samples
    return _out


def version() -> str:
    return _M412_DD_VERSION
