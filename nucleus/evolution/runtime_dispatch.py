# -*- coding: utf-8 -*-
"""runtime_dispatch.py —— 主进程运行期派单 + 闭环结果回传（第461批）

## 补的是前面几轮聊的那条架构缺口

现状（取证）：`EvolutionLoop.discover_all_issues()` 只有三个源——运行日志、
增量代码审查、self_inspector 全量静态扫描，**没有「主进程运行中发现的问题」
主动派给闭环**的通道；子进程回来只做两件事：计数与打日志，
`k1_offload` 是唯一把产出写回池的口（且只卸 review 报告，语义很窄）。
`SafeEvolutionExecutor.py:7166` 的注释自己记着「子进程不可见父进程运行期指标」。

## 本模块契约

* **一条 append-only 台账** `data/reports/evolution/runtime_issues.jsonl`，
  三种 kind：`ticket`（主进程派单）/ `result`（闭环回传）/ `claim`（闭环取走）。
* 派单必须带**证据**（不是「某条 ERROR」就算问题），并按 `make_dedup_key` +
  `M461_RUNTIME_AGGREGATE_SEC`（默认 15min）聚合，防 457 事故那种
  「Playwright page_load 超时整天在报」把队列刷爆。
* **不新增第二个池写口**：不直接写 `audit_problem_pool`，避免与
  `persist_pool` 单写者语义打架；闭环侧把台账当第四问题源读取。
* 结果回传是**结构化结果单**（verdict / applied / verified / rolled_back /
  reason），主进程心跳里有明确消费点，不是只打一行日志。
* 失败绝不影响主流程；pytest 护栏：默认生产路径 + pytest ⇒ 不写盘。
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import config
from nucleus._silent_except import silent_exc

SCHEMA = "runtime_dispatch/461.v1"
_DEFAULT_REL = "data/reports/evolution/runtime_issues.jsonl"


def _cfg(name: str, default: Any) -> Any:
    _v = getattr(config, name, None)
    return default if _v is None else _v


def _on() -> bool:
    return bool(_cfg("ENABLE_M461_RUNTIME_DISPATCH", True))


def _root(root: str | None = None) -> str:
    if root:
        return str(root)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _writable(root: str | None) -> bool:
    if root is not None:
        return True
    return "pytest" not in sys.modules


def ledger_path(root: str | None = None) -> str:
    return os.path.join(_root(root), str(_cfg("M461_RUNTIME_ISSUE_REL", _DEFAULT_REL)))


def dedup_key(issue: dict) -> str:
    """复用 audit_problem_pool 的 sha1 口径（弃行号、路径归一、跨进程稳定）。"""
    _f = str((issue or {}).get("file") or "").replace("\\", "/").lstrip("./")
    _m = str((issue or {}).get("method") or "").strip()
    _t = str((issue or {}).get("issue_type") or "").strip()
    try:
        from nucleus.audit_problem_pool import make_dedup_key as _mk
        return str(_mk(_f, _m, _t))
    except Exception as _e:
        silent_exc(_e, where="runtime_dispatch::dedup_key fallback", level="debug")
    import hashlib
    _raw = f"{_f}|{_m}|{_t}"
    return hashlib.sha1(_raw.encode("utf-8")).hexdigest()[:16]


def _note(row: dict, root: str | None = None) -> bool:
    if not _writable(root):
        return False
    _p = ledger_path(root)
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception as _e:
        silent_exc(_e, where="runtime_dispatch::_note", level="debug")
        return False


def _rows(root: str | None = None) -> list[dict]:
    _p = ledger_path(root)
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
                    silent_exc(_e, where="runtime_dispatch::_rows bad line", level="debug")
                    continue
                if isinstance(_d, dict):
                    _out.append(_d)
    except Exception as _e:
        silent_exc(_e, where="runtime_dispatch::_rows", level="debug")
    return _out


def submit_runtime_issue(issue: dict, *, root: str | None = None,
                         now: float | None = None) -> dict:
    """主进程派单。必须带证据；窗口内同键聚合，不重复占队列。

    Returns: `{"admitted", "ticket", "merged", "hits", "why"}`
    """
    _now = float(now if now is not None else time.time())
    _out: dict = {"admitted": False, "ticket": None,
                  "merged": False, "hits": 0, "why": ""}
    if not _on():
        _out["why"] = "ENABLE_M461_RUNTIME_DISPATCH=False"
        return _out
    if not isinstance(issue, dict) or not issue:
        _out["why"] = "空问题单"
        return _out

    _file = str(issue.get("file") or "").strip()
    _method = str(issue.get("method") or "").strip()
    _evidence = str(issue.get("evidence") or "").strip()
    _question = str(issue.get("question") or "").strip()
    if not (_evidence or _question or (_file and _method)):
        _out["why"] = "无证据（evidence / question / file+method 均缺）"
        return _out

    _key = dedup_key(issue)
    _win = float(_cfg("M461_RUNTIME_AGGREGATE_SEC", 900.0))
    _hits = 0
    try:
        for _r in _rows(root):
            if _r.get("kind") != "ticket" or str(_r.get("dedup_key") or "") != _key:
                continue
            _ts = float(_r.get("at") or 0)
            if 0 < _now - _ts <= _win:
                _hits += 1
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="runtime_dispatch::submit aggregate", level="debug")
    _out["hits"] = _hits
    _out["merged"] = _hits > 0

    _ticket = {
        "at": _now,
        "at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_now)),
        "kind": "ticket",
        "schema": SCHEMA,
        "dedup_key": _key,
        "file": _file,
        "method": _method,
        "issue_type": str(issue.get("issue_type") or "runtime_anomaly"),
        "evidence": _evidence[:300],
        "question": _question[:200],
        "correlation_id": str(issue.get("correlation_id") or "")[:64],
        "source": str(issue.get("source") or "main_process"),
        "severity": str(issue.get("severity") or "medium"),
        "merged": _out["merged"],
        "hits_in_window": _hits,
        "status": "pending",
    }
    _note(_ticket, root=root)
    _out["ticket"] = _ticket
    _out["admitted"] = True
    _out["why"] = "窗口内同键聚合" if _hits else "派单成功"
    return _out


def pending_tickets(*, limit: int = 10, root: str | None = None,
                    now: float | None = None) -> list[dict]:
    """闭环侧取待处理问题单（第四问题源）。

    同一 dedup_key 只取最新一条；已被 `claim` 标记或已有 `result` 的不再返回。
    """
    # ★461 刀3 隔离护栏：pytest + 默认生产路径 ⇒ 不返回任何真实派单，
    #   否则既有 discover_all_issues 用例的「问题条数」断言会被外部台账带红。
    if not _writable(root):
        return []
    _rows_all = _rows(root)
    _claimed = {str(r.get("dedup_key") or "") for r in _rows_all
                if r.get("kind") == "claim"}
    _answered = {str(r.get("dedup_key") or "") for r in _rows_all
                 if r.get("kind") == "result"}
    _latest: dict[str, dict] = {}
    for _r in _rows_all:
        if _r.get("kind") != "ticket" or _r.get("status") != "pending":
            continue
        _k = str(_r.get("dedup_key") or "")
        if not _k or _k in _claimed or _k in _answered:
            continue
        if _k not in _latest or float(_r.get("at") or 0) > float(_latest[_k].get("at") or 0):
            _latest[_k] = _r
    _out = sorted(_latest.values(), key=lambda r: float(r.get("at") or 0), reverse=True)
    return _out[:max(0, int(limit))]


def claim_ticket(dedup_key_: str, *, root: str | None = None,
                 now: float | None = None, by: str = "evolution_loop") -> bool:
    """闭环取走一张单（防止每轮重复派同一问题）。"""
    return _note({"kind": "claim", "at": float(now if now is not None else time.time()),
                  "dedup_key": str(dedup_key_ or ""), "by": str(by),
                  "schema": SCHEMA}, root=root)


def note_loop_result(*, patch_id: str, dedup_key_: str, verdict: str,
                     applied: bool = False, verified: bool = False,
                     rolled_back: bool = False, reason: str = "",
                     root: str | None = None,
                     now: float | None = None) -> dict:
    """闭环结束 ⇒ 结构化结果单回传主进程。"""
    _now = float(now if now is not None else time.time())
    _row = {
        "kind": "result", "at": _now, "schema": SCHEMA,
        "at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_now)),
        "dedup_key": str(dedup_key_ or ""),
        "patch_id": str(patch_id or "")[:64],
        "verdict": str(verdict or ""),
        "applied": bool(applied),
        "verified": bool(verified),
        "rolled_back": bool(rolled_back),
        "reason": str(reason or "")[:300],
    }
    _ok = _note(_row, root=root)
    return {"ok": _ok, "result": _row}


def consume_loop_results(*, since: float = 0.0, limit: int = 20,
                         root: str | None = None) -> list[dict]:
    """主进程消费口：返回 `at > since` 的结果单（按时间升序）。"""
    _out: list[dict] = []
    try:
        for _r in _rows(root):
            if _r.get("kind") != "result":
                continue
            _ts = float(_r.get("at") or 0)
            if _ts > float(since):
                _out.append(_r)
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="runtime_dispatch::consume_loop_results", level="debug")
    _out.sort(key=lambda r: float(r.get("at") or 0))
    return _out[:max(0, int(limit))]
