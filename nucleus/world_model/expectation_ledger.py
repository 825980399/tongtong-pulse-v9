# -*- coding: utf-8 -*-
"""★317批刀4（长期13 · 世界模型最小版）：expectation_ledger——「预期 → 实测 → 差」。

外部观测结论是「有流水、无裁判」：alerts 上万条、health_snapshot 每分钟滚动、
ear_stream 活跃、`environment.mutated` 有记录；但 `world_model` 全库 0 命中，
没有任何「预期 vs 实测」的结构。本模块补的就是这一层。

每行一条断言：
    {assertion_id, statement, source,
     predicted{field, expect},
     measured{field, actual, observed_at, obs_source},
     verdict ∈ {met, violated, unverifiable},
     criteria_version, graph_file}

**红线（任务书 §六）**：
  * `violated` **不得影响入队 / 动机 / 分诊**——本模块只写台账，
    不 import 任何入队、规划、动机接口（可 grep 自证）；
  * 不做物理世界建模；**不新建调度器**——由 04:00
    `nucleus/evolution/night_orchestration.py::generate_night_digest` 复用调用。

实测源（4 类现成，零新增）：health_snapshot / alerts / pulse_pairs / 调用图 stats
（另含 316 批刀1 的 A4 掌握度断言，顺带把 G-6 的类簇口径钉死在记录里）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("ExpectationLedger")

#: 与 CallGraphAnalyzer 的 A4 口径保持同一版本号（317 批钉死：不含 _inner_world_math.py）
_CRITERIA_VERSION = "317.a4.v1"
#: A4 说明书侧基线（5 文件 def 总数，本批实测）
_A4_MANUAL_BASELINE = 455

_LEDGER_REL = os.path.join("data", "world_model", "expectation_ledger.jsonl")


def ledger_path(project_root: str | None = None) -> str:
    """台账文件绝对路径。"""
    _root = project_root or os.getcwd()
    return os.path.join(_root, _LEDGER_REL)


def _read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger._read_json")
        return default


def _read_jsonl(path: str) -> list[dict]:
    _rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for _line in f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _d = json.loads(_line)
                except Exception:
                    continue
                if isinstance(_d, dict):
                    _rows.append(_d)
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger._read_jsonl")
    return _rows


def _latest_graph(project_root: str) -> tuple[str, dict]:
    """取最近一次调用图快照（文件名按时间戳倒序）。"""
    _dir = os.path.join(project_root, "data", "call_graph")
    _cands = []
    try:
        for _n in os.listdir(_dir):
            if _n.startswith("graph_") and _n.endswith(".json"):
                _cands.append(_n)
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger._latest_graph")
        return "", {}
    if not _cands:
        return "", {}
    _cands.sort(reverse=True)
    _p = os.path.join(_dir, _cands[0])
    return os.path.join("data", "call_graph", _cands[0]), (_read_json(_p, {}) or {})


def _mk(assertion_id: str, statement: str, source: str, pred_field: str, expect: str,
        meas_field: str, actual: Any, verdict: str, obs_source: str,
        graph_file: str = "", criteria_version: str = _CRITERIA_VERSION,
        observed_at: float | None = None) -> dict:
    """构造一条 ledger 记录（统一 schema）。"""
    return {
        "assertion_id": assertion_id,
        "statement": statement,
        "source": source,
        "predicted": {"field": pred_field, "expect": expect},
        "measured": {"field": meas_field, "actual": actual,
                     "observed_at": round(float(observed_at or time.time()), 3),
                     "obs_source": obs_source},
        "verdict": verdict,
        "criteria_version": criteria_version,
        "graph_file": graph_file,
    }


def collect_assertions(project_root: str | None = None) -> list[dict]:
    """按 4 类现成实测源产出断言（每类至少 1 条，三态 verdict 均可出现）。"""
    _root = project_root or os.getcwd()
    _now = time.time()
    _graph_rel, _graph = _latest_graph(_root)
    _gstats = (_graph or {}).get("stats") or {}
    _nodes = (_graph or {}).get("nodes") or []
    _out: list[dict] = []

    # ---- 源1：health_snapshot（心跳是否在走）----
    _snap = _read_json(os.path.join(_root, "data", "monitor", "health_snapshot.json"), {}) or {}
    _beat = _snap.get("beat_count")
    _beat_ok = isinstance(_beat, (int, float)) and _beat >= 1
    _out.append(_mk(
        "health.beat_count.alive", "框架心跳计数应 ≥1（进程在跑、心跳在递增）",
        "health_snapshot", "beat_count", ">=1", "beat_count",
        _beat if isinstance(_beat, (int, float)) else None,
        "met" if _beat_ok else ("violated" if isinstance(_beat, (int, float)) else "unverifiable"),
        "health_snapshot", _graph_rel, observed_at=_now))
    # 观测源同文件、但字段未暴露 ⇒ 诚实记 unverifiable（不是"没问题"）
    _out.append(_mk(
        "runtime.search_latency.p95", "检索 P95 延迟应 ≤200ms",
        "health_snapshot", "search_latency_p95_ms", "<=200", "search_latency_p95_ms",
        None, "unverifiable", "health_snapshot(字段未暴露)", _graph_rel, observed_at=_now))

    # ---- 源2：alerts（异常占比）----
    _alerts = _read_jsonl(os.path.join(_root, "data", "reports", "alerts.jsonl"))
    _sev: dict[str, int] = {}
    for _r in _alerts:
        _s = str(_r.get("severity") or _r.get("level") or "").upper()
        if _s:
            _sev[_s] = _sev.get(_s, 0) + 1
    _bad = _sev.get("ERROR", 0) + _sev.get("CRITICAL", 0)
    _ratio = round(_bad / float(len(_alerts)), 4) if _alerts else None
    if _ratio is None:
        _v = "unverifiable"
    else:
        _v = "met" if _ratio <= 0.05 else "violated"
    _out.append(_mk(
        "alerts.error_ratio.window", "P0 之外 ERROR+CRITICAL 占全部告警比例应 ≤5%",
        "alerts", "error_ratio", "<=0.05", "error_ratio", _ratio, _v,
        "alerts.jsonl", _graph_rel, observed_at=_now))

    # ---- 源3：pulse_pairs（当日脉冲对是否有落盘）----
    _day = time.strftime("%Y%m%d", time.localtime(_now))
    _pp = os.path.join(_root, "data", "monitor", "pulse_pairs_%s.jsonl" % _day)
    _pp_rows = _read_jsonl(_pp) if os.path.exists(_pp) else []
    _out.append(_mk(
        "pulse_pairs.today.present", "当日脉冲对应有落盘（≥1 条），否则脉冲观测面空白",
        "pulse_pairs", "today_rows", ">=1", "today_rows",
        len(_pp_rows) if os.path.exists(_pp) else None,
        "met" if _pp_rows else ("violated" if os.path.exists(_pp) else "unverifiable"),
        "pulse_pairs_%s.jsonl" % _day, _graph_rel, observed_at=_now))

    # ---- 源4：调用图 stats（解析质量）----
    _pe = _gstats.get("parse_errors")
    _out.append(_mk(
        "call_graph.parse_errors.zero", "调用图解析错误应为 0（图可信才能当判据）",
        "call_graph", "parse_errors", "==0", "parse_errors",
        _pe, ("met" if _pe == 0 else ("violated" if isinstance(_pe, int) else "unverifiable")),
        "call_graph_graph_stats", _graph_rel, observed_at=_now))

    # ---- 316 刀1 A4 断言接实测（顺带完成 G-6 口径钉死）----
    _cls_nodes = len([n for n in _nodes if isinstance(n, dict)
                      and n.get("cls") == "PulseInnerWorld"])
    if _cls_nodes:
        _gap = abs(_A4_MANUAL_BASELINE - _cls_nodes)
        _out.append(_mk(
            "mastery.PulseInnerWorld.a4_gap",
            "A4 覆盖：说明书方法数(455, 5文件) 应等于 图类簇节点数（口径 317.a4.v1）",
            "CallGraphAnalyzer.mastery_assertion_check", "a4_gap", "==0", "a4_gap",
            _gap, ("met" if _gap == 0 else "violated"),
            "call_graph_graph_nodes", _graph_rel, observed_at=_now))
    return _out


def sweep(project_root: str | None = None) -> dict:
    """跑一次台账（幂等：同一 assertion_id 同日只记首次），返回统计。"""
    _root = project_root or os.getcwd()
    _p = ledger_path(_root)
    _rows = collect_assertions(_root)

    _existing: set[tuple[str, str]] = set()
    _total = 0
    for _r in _read_jsonl(_p):
        _total += 1
        _day = time.strftime("%Y-%m-%d", time.localtime(float(_r.get("measured", {})
                                                              .get("observed_at") or 0)))
        _existing.add((str(_r.get("assertion_id")), _day))

    _today = time.strftime("%Y-%m-%d", time.localtime())
    _added = 0
    _verdicts: dict[str, int] = {}
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8", newline="\n") as _f:
            for _r in _rows:
                _verdicts[_r["verdict"]] = _verdicts.get(_r["verdict"], 0) + 1
                _key = (str(_r.get("assertion_id")), _today)
                if _key in _existing:
                    continue
                _f.write(json.dumps(_r, ensure_ascii=False) + "\n")
                _existing.add(_key)
                _added += 1
                _total += 1
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger.sweep")
        return {"added": 0, "total": _total, "verdicts": _verdicts, "error": type(_e).__name__}

    _module_logger.info(
        "[317刀4][ledger] 新增 %d 条 / 累计 %d 条 verdict=%s（只观测不决策）",
        _added, _total, _verdicts)
    return {"added": _added, "total": _total, "verdicts": _verdicts, "path": _p}
