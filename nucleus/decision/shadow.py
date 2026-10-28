# -*- coding: utf-8 -*-
"""385 刀2：DL3 影子账本（**只写不判**）。

## 三条红线（洞鉴 385 §6.6 + 本批任务书）

1. **不改任何执行行为**：所有结果 `shadow=True`，执行器必须忽略；
   `action` 默认 `DEFER`（影子期不该出现 `EXECUTE`，否则账本会看起来像"在下指令"）。
2. **不新写意图分类／补丁准入／队列限流**：这里只**抄**既有链路的事实，
   判据一概不重造（洞鉴 §6.6 明列为反重复建设红线）。
3. **不引入本地小模型**。

## 为什么叫「只写不判」

影子期不产出新的裁决 —— 它把「既有链路实际怎么判的」记成 L3 的格式，
让将来真接 L3 时**有历史可比**（同输入 ⇒ 同结果？）。
所以 `reason_code` 用 `SHADOW_ONLY`，不借用 `INTENT_MATCH` 之类看起来像真判的原因。

## 三个调用点（票面第 3 条）

| 域 | 时机 | 节流 |
|---|---|---|
| `patch` | 每次派单后 | 无（派单本来就低频） |
| `resource` | 每 N 拍 | `--every-n` / 默认 30 |
| `chat` | 每 N 次回答抽样 | 默认 100 |

## 开关

`config.ENABLE_DEC_SHADOW`，**默认 False**。关 ⇒ 三个 observe 函数在**第一行就返回**，
不构造对象、不写盘（票面单测②要求"开关关时零写入"）。
"""
from __future__ import annotations

import time

from nucleus._silent_except import silent_exc
from nucleus.decision import ledger
from nucleus.decision.contracts import (
    DecisionRequest, make_request_id, shadow_result,
)

#: 全局限流参数（调用方可不传，用默认）
DEFAULT_RESOURCE_EVERY_N = 30
DEFAULT_CHAT_EVERY_N = 100

#: 耗时样本上限（只留最近 N 次，避免长跑无限涨）
_MAX_ELAPSED_SAMPLES = 200

#: 进程内计数：{"calls": {...}, "skipped": {...}, "written": {...}}
_COUNTS: dict = {
    "calls": {"patch": 0, "resource": 0, "chat": 0},
    "skipped": {"patch": 0, "resource": 0, "chat": 0},
    "written": {"patch": 0, "resource": 0, "chat": 0},
    "by_status": {},
}

#: 最近 N 次「构造 + 写盘」耗时（毫秒）
_ELAPSED_MS: list = []

#: ★385刀2 性能：进程代缓存（见 `_proc_gen_cached`）
_PG_CACHE: str = ""


def enabled() -> bool:
    """★影子开关（默认关）。读 config 每次都重取 ⇒ 改配置即时生效。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_DEC_SHADOW", False))
    except Exception as _e:
        silent_exc(_e, where="decision.shadow.enabled", level="debug")
        return False


def _proc_gen_cached() -> str:
    """★385刀2 性能：进程代在进程生命周期内不变，取一次就够（每拍 import 太贵）。"""
    global _PG_CACHE
    if _PG_CACHE:
        return _PG_CACHE
    try:
        from nucleus.proc_gen import proc_gen_str as _pgf
        _PG_CACHE = str(_pgf())
    except Exception as _e:
        silent_exc(_e, where="decision.shadow._proc_gen_cached", level="debug")
        _PG_CACHE = ""
    return _PG_CACHE


def _note_elapsed(ms: float) -> None:
    _ELAPSED_MS.append(float(ms))
    if len(_ELAPSED_MS) > _MAX_ELAPSED_SAMPLES:
        del _ELAPSED_MS[:len(_ELAPSED_MS) - _MAX_ELAPSED_SAMPLES]


def p99_ms() -> float:
    """最近样本的 P99 耗时（毫秒）。无样本返 0.0（**不编造**）。"""
    if not _ELAPSED_MS:
        return 0.0
    _s = sorted(_ELAPSED_MS)
    _i = min(len(_s) - 1, int(round(0.99 * (len(_s) - 1))))
    return float(_s[_i])


def stats() -> dict:
    """诊断读数（供自省/报告/单测）。"""
    return {
        "enabled": enabled(),
        "calls": dict(_COUNTS["calls"]),
        "skipped": dict(_COUNTS["skipped"]),
        "written": dict(_COUNTS["written"]),
        "by_status": dict(_COUNTS["by_status"]),
        "elapsed_samples": len(_ELAPSED_MS),
        "p99_ms": round(p99_ms(), 3),
    }


def reset_stats() -> None:
    """清空计数与样本（测试用）。"""
    for _k in ("calls", "skipped", "written"):
        for _d in _COUNTS[_k]:
            _COUNTS[_k][_d] = 0
    _COUNTS["by_status"].clear()
    _ELAPSED_MS.clear()


def _emit(*, scope: str, candidates: list, signals: dict, constraints: dict,
          evidence: list, target: str = "", priority: int = 0,
          action: str = "DEFER", path: str | None = None,
          explicit: bool = False, digest_parts=()) -> dict | None:
    """统一出口：构造 request/result → 写账本 → 记耗时。**不抛异常**。"""
    if not enabled():
        _COUNTS["skipped"][scope] = _COUNTS["skipped"].get(scope, 0) + 1
        return None
    _t0 = time.perf_counter()
    try:
        _now = time.time()
        _pg = _proc_gen_cached()      # ★385刀2 性能：进程内只取一次
        _req = DecisionRequest(
            request_id=make_request_id(scope, *digest_parts),
            ts=_now,
            proc_gen=_pg,
            scope=scope,
            candidates=list(candidates or []),
            signals=dict(signals or {}),
            constraints=dict(constraints or {}),
            epoch=str((constraints or {}).get("epoch") or ""),
        )
        _bad = _req.validate()
        if _bad:
            _COUNTS["by_status"]["req_invalid"] = _COUNTS["by_status"].get("req_invalid", 0) + 1
            return None
        _res = shadow_result(_req, action=action, target=target, priority=priority,
                             evidence=list(evidence or []))
        _st = ledger.append(_res, _req, path=path, explicit=explicit)
        _COUNTS["by_status"][_st] = _COUNTS["by_status"].get(_st, 0) + 1
        if _st == "ok":
            _COUNTS["written"][scope] = _COUNTS["written"].get(scope, 0) + 1
        return {"status": _st, "decision_id": _res.decision_id,
                "request_id": _req.request_id}
    except Exception as _e:
        silent_exc(_e, where="decision.shadow._emit", level="warning")
        return None
    finally:
        _note_elapsed((time.perf_counter() - _t0) * 1000.0)


# ------------------------------------------------------------------ 三个域
def observe_patch(*, patch_id: str, file: str = "", method: str = "",
                  issue_type: str = "", status: str = "", trust=None, risk=None,
                  dispatched: bool = False, evidence: list | None = None,
                  path: str | None = None, explicit: bool = False) -> dict | None:
    """patch 域：**每次派单后**写一条（票面第 3 条）。

    `dispatched=True` 表示这一拍真的派了单 —— 记 `EXECUTE` 加 rollback 句柄；
    否则记 `DEFER`（没人派它，这是事实，不是裁决）。
    """
    _COUNTS["calls"]["patch"] = _COUNTS["calls"].get("patch", 0) + 1
    _ev = list(evidence or [])
    if not _ev:
        _ev = ["patch:%s" % (patch_id or "-")]
    return _emit(
        scope="patch",
        candidates=[{"id": str(patch_id or ""), "kind": "patch",
                     "cost": {"llm_calls": 0}, "risk": risk,
                     "evidence_ref": str(file or "")}],
        signals={"l4": {"patch_status": status, "trust": trust, "risk": risk,
                        "issue_type": issue_type, "method": method}},
        constraints={"llm_budget": 0, "forbidden_files": [], "max_latency_ms": 5,
                     "user_override": False},
        evidence=_ev,
        target=str(patch_id or ""),
        priority=50 if dispatched else 10,
        action="EXECUTE" if dispatched else "DEFER",
        path=path, explicit=explicit,
        digest_parts=(patch_id, status),
    )


def observe_resource(*, queue_snapshot: dict | None = None, rss_mb=None,
                     tick: int = 0, every_n: int = DEFAULT_RESOURCE_EVERY_N,
                     evidence: list | None = None,
                     path: str | None = None, explicit: bool = False) -> dict | None:
    """resource 域：每 N 拍写一条（票面第 3 条）。`tick` 由调用方传拍号。"""
    _COUNTS["calls"]["resource"] = _COUNTS["calls"].get("resource", 0) + 1
    if not enabled():
        _COUNTS["skipped"]["resource"] = _COUNTS["skipped"].get("resource", 0) + 1
        return None
    _n = max(1, int(every_n or DEFAULT_RESOURCE_EVERY_N))
    if int(tick) % _n != 0:
        _COUNTS["skipped"]["resource"] = _COUNTS["skipped"].get("resource", 0) + 1
        return None
    _ev = list(evidence or []) or ["resource:tick=%d" % int(tick)]
    return _emit(
        scope="resource",
        candidates=[{"id": "keep", "kind": "noop", "cost": {}, "risk": None,
                     "evidence_ref": "resource:tick=%d" % int(tick)}],
        signals={"resource": dict(queue_snapshot or {}, rss_mb=rss_mb)},
        constraints={"llm_budget": 0, "forbidden_files": [], "max_latency_ms": 5,
                     "user_override": False},
        evidence=_ev,
        action="DEFER", priority=0,
        path=path, explicit=explicit,
        digest_parts=("resource", int(tick)),
    )


def observe_chat(*, intent_type: str = "", confidence=None, route: str = "",
                 tick: int = 0, every_n: int = DEFAULT_CHAT_EVERY_N,
                 evidence: list | None = None,
                 path: str | None = None, explicit: bool = False) -> dict | None:
    """chat 域：每 N 次回答抽样写一条（票面第 3 条）。"""
    _COUNTS["calls"]["chat"] = _COUNTS["calls"].get("chat", 0) + 1
    if not enabled():
        _COUNTS["skipped"]["chat"] = _COUNTS["skipped"].get("chat", 0) + 1
        return None
    _n = max(1, int(every_n or DEFAULT_CHAT_EVERY_N))
    if int(tick) % _n != 0:
        _COUNTS["skipped"]["chat"] = _COUNTS["skipped"].get("chat", 0) + 1
        return None
    _ev = list(evidence or []) or ["chat:tick=%d" % int(tick)]
    return _emit(
        scope="chat",
        candidates=[{"id": "answer", "kind": "chat", "cost": {},
                     "risk": None, "evidence_ref": str(route or "")}],
        signals={"l2": {"intent_type": intent_type, "confidence": confidence,
                        "suggested_method": route}},
        constraints={"llm_budget": 0, "forbidden_files": [], "max_latency_ms": 5,
                     "user_override": False},
        evidence=_ev,
        action="DEFER", priority=0,
        path=path, explicit=explicit,
        digest_parts=("chat", int(tick), intent_type),
    )
