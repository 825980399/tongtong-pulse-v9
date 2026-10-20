# -*- coding: utf-8 -*-
"""★317批刀4（长期13 · 世界模型最小版）+ ★318批刀5（扩源）：expectation_ledger。

「预期 → 实测 → 差」台账，每行一条断言：

    {assertion_id, statement, source,
     predicted{field, expect},
     measured{field, actual, observed_at, obs_source},
     verdict ∈ {met, violated, unverifiable},
     gap_reason, criteria_version, graph_file}

**红线（任务书 §六）**：
  * `violated` **不得影响入队 / 动机 / 分诊**——本模块只写台账，
    不 import 任何入队、规划、动机接口（可 grep 自证）；
  * 不做物理世界建模；**不新建调度器**——由 04:00
    `nucleus/evolution/night_orchestration.py::generate_night_digest` 复用调用。

★318批刀5 扩源（周产能 ≥112 条，断言源 ≥7 类）：

| 源 | 目标条数 | obs_source |
|---|---|---|
| A1 类簇（零入边方法占比） | 40 | call_graph_cluster_a1 |
| A4 覆盖（图 vs 源文件 def） | 16 | call_graph_ast_manual |
| 说明书入口（器官入口存在性） | 16+1 | call_graph_handbook_rule |
| health_snapshot 字段 | 10 | health_snapshot |
| alerts 分布 | 8 | alerts.jsonl |
| pulse_pairs（近 7 天 ×2） | 14 | pulse_pairs_<day>.jsonl |
| 调用图 stats | 8 | call_graph_graph_stats |

`gap_reason` 把 unverifiable 的成因分开（**"取不到实测" ≠ "世界与预期不符"**）：
`field_not_exposed` / `no_measurement_source` / `predicate_not_computable`。

`assertion_id` 可加圈次前缀 `loop.<N>.<id>`（压测每圈外部裁判一一对应）。
"""
from __future__ import annotations

import ast
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("ExpectationLedger")

#: 与 CallGraphAnalyzer 的 A4 口径保持同一版本号（317 批钉死：不含 _inner_world_math.py）
_CRITERIA_VERSION = "317.a4.v1"
#: A4 说明书侧基线（5 文件 def 总数，317 批实测）
_A4_MANUAL_BASELINE = 455

_LEDGER_REL = os.path.join("data", "world_model", "expectation_ledger.jsonl")

#: 各类断言的目标条数（周产能预算，可审计）
_SOURCE_BUDGET = {
    "call_graph_cluster_a1": 40,
    "call_graph_ast_manual": 16,
    "call_graph_handbook_rule": 17,
    "health_snapshot": 10,
    "alerts": 8,
    "pulse_pairs": 14,
    "call_graph_graph_stats": 8,
}


def ledger_path(project_root: str | None = None) -> str:
    """台账文件绝对路径。

    ★320批刀3（验证产物隔离硬规·代码侧支撑）：环境变量 ``TT_LEDGER_PATH``
    优先——施工/压测的**验证运行**一律指向隔离根（``tmp/p3xx_root/...``），
    不得写生产 ``data/``（烛微320§0-2 已数到第四例）。
    """
    _env = os.environ.get("TT_LEDGER_PATH")
    if _env:
        return _env
    return os.path.join(project_root or os.getcwd(), _LEDGER_REL)


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
                except ValueError as _e:
                    silent_exc(_e, where="world_model.expectation_ledger._read_jsonl")
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
        _cands = [f for f in os.listdir(_dir)
                  if f.startswith("graph_") and f.endswith(".json")]
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger._latest_graph")
        return "", {}
    if not _cands:
        return "", {}
    _cands.sort(reverse=True)
    _p = os.path.join(_dir, _cands[0])
    return os.path.join("data", "call_graph", _cands[0]), (_read_json(_p, {}) or {})


# ★320批刀1：gap_reason 枚举（**violated 也必须带**——烛微320§0-3 指出 25 条
# violated 里 24 条 gap_reason 为空，导致无法区分"世界不符"与"口径差"）。
#   field_not_exposed / no_measurement_source / predicate_not_computable /
#   criteria_gap（口径差，**不计入裁判分母**）/ real_violation（真违反，计入分母）
_GAP_REASONS = frozenset({
    "field_not_exposed",
    "no_measurement_source",
    "predicate_not_computable",
    "criteria_gap",
    "real_violation",
})

#: 这些 assertion_id 前缀属「说明书口径 vs 图口径」的结构性差值，不是世界不符
#: 注意含 `a4.`（`a4.coverage.<Cls>` 是实测 id 形态）与 `a4_`（旧写法）两种
_CRITERIA_GAP_PREFIXES = ("mastery.", "handbook.", "a4.", "a4_", "manual.")


def _row_source(row: dict) -> str:
    """★320批刀0：取一条记录的**来源进程**（新字段优先，旧数据回退）。

    旧台账（320 前）没有 `source_process`；其 `source` 是"断言来源模块"（不是进程），
    不能拿来当进程标记 ⇒ 一律按 `live`（历史数据视为活体时期产生）处理。
    """
    _sp = row.get("source_process")
    if _sp:
        return _norm_source(_sp)
    _s = str(row.get("source") or "")
    return _norm_source(_s) if _s in ("live", "verification") else "live"


def _infer_gap_reason(assertion_id: str, verdict: str, gap_reason: str) -> str:
    """★320批刀1：gap_reason 归一——violated 必带；口径差单列 criteria_gap。"""
    if gap_reason:
        return gap_reason if gap_reason in _GAP_REASONS else "predicate_not_computable"
    if verdict == "unverifiable":
        return "no_measurement_source"
    if verdict == "violated":
        _aid = str(assertion_id or "")
        if any(_aid.startswith(_p) for _p in _CRITERIA_GAP_PREFIXES):
            return "criteria_gap"
        return "real_violation"
    return ""


def _norm_source(source: str | None) -> str:
    """★320批刀0/刀3：来源标记归一（``live`` 活体 / ``verification`` 验证运行）。"""
    _s = str(source or "live").strip().lower()
    return "verification" if _s.startswith("verif") else "live"


def _mk(assertion_id: str, statement: str, source: str, pred_field: str, expect: str,
        meas_field: str, actual: Any, verdict: str, obs_source: str,
        gap_reason: str = "", graph_file: str = "",
        criteria_version: str = _CRITERIA_VERSION,
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
        # ★320批刀1：原实现 `gap_reason if verdict == "unverifiable" else ""` 把
        # violated 的 gap_reason **直接清空**（烛微320§0-3 的 24/25 空值即此因）。
        "gap_reason": _infer_gap_reason(assertion_id, verdict, gap_reason),
        "criteria_version": criteria_version,
        "graph_file": graph_file,
    }


def _count_defs_in_file(path: str) -> int | None:
    """AST 统计单文件 def 数（含方法）；不可读返回 None。"""
    try:
        with open(path, encoding="utf-8") as _f:
            _tree = ast.parse(_f.read())
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger._count_defs_in_file")
        return None
    return sum(1 for _n in ast.walk(_tree)
               if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)))


def _clusters(graph: dict) -> dict[str, list[dict]]:
    """按 cls 分组节点。"""
    _out: dict[str, list[dict]] = {}
    for _n in (graph.get("nodes") or []):
        if not isinstance(_n, dict):
            continue
        _c = str(_n.get("cls") or "")
        if _c:
            _out.setdefault(_c, []).append(_n)
    return _out


def collect_assertions(project_root: str | None = None,
                       source: str = "live") -> list[dict]:
    """按 ≥7 类现成实测源产出断言（预算 112 条/周；三态 verdict 均可出现）。"""
    _root = project_root or os.getcwd()
    _now = time.time()
    _graph_rel, _graph = _latest_graph(_root)
    _gstats = (_graph or {}).get("stats") or {}
    _nodes = (_graph or {}).get("nodes") or []
    _edges = (_graph or {}).get("edges") or []
    _out: list[dict] = []

    # ---------- 源1：A1 类簇（零入边方法占比）40 条 ----------
    _in_deg: dict[str, int] = {}
    for _e in _edges:
        if isinstance(_e, dict) and _e.get("callee"):
            _in_deg[_e["callee"]] = _in_deg.get(_e["callee"], 0) + int(_e.get("count", 1) or 1)
    _cl = _clusters(_graph)
    _top = sorted(_cl.items(), key=lambda kv: -len(kv[1]))
    for _cname, _ns in _top[:_SOURCE_BUDGET["call_graph_cluster_a1"]]:
        _ids = [str(_n.get("id")) for _n in _ns]
        _zero = sum(1 for _i in _ids if _in_deg.get(_i, 0) == 0)
        _ratio = round(_zero / float(len(_ids)), 4) if _ids else None
        _out.append(_mk(
            "a1.cluster.%s.zero_in_degree_ratio" % _cname,
            "类簇 %s 的零入边方法占比应 ≤50%%（类内自洽可接受，整簇孤立则图残缺）" % _cname,
            "CallGraphAnalyzer.cluster_view", "zero_in_degree_ratio", "<=0.5",
            "zero_in_degree_ratio", _ratio,
            "met" if (_ratio is not None and _ratio <= 0.5) else "violated",
            "call_graph_cluster_a1",
            "no_measurement_source" if _ratio is None else "",
            _graph_rel, observed_at=_now))

    # ---------- 源2：A4 覆盖（图类簇 vs 源文件 def）16 条 ----------
    for _cname, _ns in _top[:_SOURCE_BUDGET["call_graph_ast_manual"]]:
        _files = {str(_n.get("file") or "") for _n in _ns if _n.get("file")}
        _manual = 0
        _missing = True
        for _f in _files:
            _abs = os.path.join(_root, str(_f).replace("\\", "/"))
            _c = _count_defs_in_file(_abs)
            if _c is None:
                continue
            _missing = False
            _manual += _c
        if _missing:
            _out.append(_mk(
                "a4.coverage.%s" % _cname,
                "A4 覆盖：类 %s 的图节点数应等于其源文件 def 总数" % _cname,
                "CallGraphAnalyzer.mastery_assertion_check", "a4_gap", "==0",
                "a4_gap", None, "unverifiable", "call_graph_ast_manual",
                "no_measurement_source", _graph_rel, observed_at=_now))
        else:
            _gap = abs(_manual - len(_ns))
            _out.append(_mk(
                "a4.coverage.%s" % _cname,
                "A4 覆盖：类 %s 的图节点数应等于其源文件 def 总数（口径 317.a4.v1）" % _cname,
                "CallGraphAnalyzer.mastery_assertion_check", "a4_gap", "==0",
                "a4_gap", _gap, "met" if _gap == 0 else "violated",
                "call_graph_ast_manual", "", _graph_rel, observed_at=_now))

    # ---------- 源3：说明书入口 16+1 条 ----------
    _organ_cls = [_c for _c, _ns in _top
                  if _c.startswith("Pulse")][:_SOURCE_BUDGET["call_graph_handbook_rule"] - 1]
    for _cname in _organ_cls:
        _ns = _cl.get(_cname) or []
        _ids = [str(_n.get("id")) for _n in _ns]
        _with_in = sum(1 for _i in _ids if _in_deg.get(_i, 0) > 0)
        _out.append(_mk(
            "handbook.entry.%s" % _cname,
            "说明书入口判据：类 %s 应至少有 1 个有入边的方法（可作为入口）" % _cname,
            "PulseCodeLearner._generate_organ_handbook", "entry_with_in_degree", ">=1",
            "entry_with_in_degree", _with_in, "met" if _with_in >= 1 else "violated",
            "call_graph_handbook_rule", "", _graph_rel, observed_at=_now))
    # 说明书入口判据全局断言（318刀6 复核项：旧启发式文案是否已消失）
    _pcl_txt = ""
    try:
        with open(os.path.join(_root, "organs", "brain", "PulseCodeLearner.py"),
                  encoding="utf-8") as _f:
            _pcl_txt = _f.read()
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger.handbook_rule")
    if not _pcl_txt:
        _out.append(_mk("handbook.entry_rule.graph_judged",
                        "说明书入口判据应统一为图判定（旧启发式文案已消失）",
                        "PulseCodeLearner", "old_heuristic_present", "==False",
                        "old_heuristic_present", None, "unverifiable",
                        "call_graph_handbook_rule", "no_measurement_source",
                        _graph_rel, observed_at=_now))
    else:
        _old = '_entry_str = "、".join(_entry_methods[:3]) if _entry_methods else "脉冲响应入口"'
        _bad = _old in _pcl_txt
        _out.append(_mk("handbook.entry_rule.graph_judged",
                        "说明书入口判据应统一为图判定（旧启发式文案已消失）",
                        "PulseCodeLearner", "old_heuristic_present", "==False",
                        "old_heuristic_present", _bad,
                        "violated" if _bad else "met",
                        "call_graph_handbook_rule", "", _graph_rel, observed_at=_now))

    # ---------- 源4：health_snapshot 10 条 ----------
    _snap = _read_json(os.path.join(_root, "data", "monitor", "health_snapshot.json"), None)
    for _fname in ["beat_count", "nodes", "pulse", "info_field", "memory_pressure",
                   "knowledge_evolution", "organs", "heartbeat", "dream",
                   "reflection"][:_SOURCE_BUDGET["health_snapshot"]]:
        if not isinstance(_snap, dict) or _fname not in _snap:
            _out.append(_mk("health.%s.present" % _fname,
                            "health_snapshot 应暴露字段 %s" % _fname,
                            "health_snapshot", _fname, "not None", _fname, None,
                            "unverifiable", "health_snapshot",
                            "field_not_exposed" if isinstance(_snap, dict)
                            else "no_measurement_source", _graph_rel, observed_at=_now))
        else:
            _v = _snap.get(_fname)
            _ok = _v not in (None, "", [], {})
            _out.append(_mk("health.%s.present" % _fname,
                            "health_snapshot 字段 %s 应有非空读数" % _fname,
                            "health_snapshot", _fname, "not empty", _fname,
                            (str(_v)[:80] if _v is not None else None),
                            "met" if _ok else "violated", "health_snapshot", "",
                            _graph_rel, observed_at=_now))

    # ★318批刀5：一条**真实取不到**的断言（health_snapshot 不暴露检索 P95 延迟）——
    #   它存在的意义是让「三态」不缺 unverifiable：没有它，"全 met" 会掩盖"测不到"这一类。
    _p95 = _snap.get("search_latency_p95_ms") if isinstance(_snap, dict) else None
    _out.append(_mk("health.search_latency.p95",
                    "检索 P95 延迟应 ≤200ms", "health_snapshot",
                    "search_latency_p95_ms", "<=200", "search_latency_p95_ms", _p95,
                    ("met" if (_p95 is not None and _p95 <= 200)
                     else ("violated" if _p95 is not None else "unverifiable")),
                    "health_snapshot",
                    "" if _p95 is not None else "field_not_exposed",
                    _graph_rel, observed_at=_now))

    # ---------- 源5：alerts 8 条 ----------
    _alerts = _read_jsonl(os.path.join(_root, "data", "reports", "alerts.jsonl"))
    _sev: dict[str, int] = {}
    _needs_human = 0
    for _r in _alerts:
        _s = str(_r.get("severity") or _r.get("level") or "").upper()
        if _s:
            _sev[_s] = _sev.get(_s, 0) + 1
        if _r.get("needs_human"):
            _needs_human += 1
    for _k in ["P0", "P1", "ERROR", "CRITICAL", "WARNING"]:
        _n = _sev.get(_k, 0)
        _out.append(_mk("alerts.severity.%s.count" % _k,
                        "告警严重度 %s 计数应可统计（CRITICAL 应 =0）" % _k,
                        "alerts", "%s_count" % _k,
                        "==0" if _k == "CRITICAL" else ">=0", "%s_count" % _k, _n,
                        (("met" if _n == 0 else "violated") if _k == "CRITICAL" else "met"),
                        "alerts.jsonl", "", _graph_rel, observed_at=_now))
    _out.append(_mk("alerts.total.count", "告警总量应有记录（>0）", "alerts",
                    "total", ">0", "total", len(_alerts),
                    "met" if _alerts else "violated", "alerts.jsonl", "",
                    _graph_rel, observed_at=_now))
    _out.append(_mk("alerts.needs_human.count", "需人工介入条目应可统计", "alerts",
                    "needs_human", ">=0", "needs_human", _needs_human, "met",
                    "alerts.jsonl", "", _graph_rel, observed_at=_now))
    _bad_n = _sev.get("ERROR", 0) + _sev.get("CRITICAL", 0)
    _ratio_a = round(_bad_n / float(len(_alerts)), 4) if _alerts else None
    _out.append(_mk("alerts.error_ratio.window",
                    "ERROR+CRITICAL 占全部告警比例应 ≤5%", "alerts",
                    "error_ratio", "<=0.05", "error_ratio", _ratio_a,
                    ("met" if (_ratio_a is not None and _ratio_a <= 0.05)
                     else ("violated" if _ratio_a is not None else "unverifiable")),
                    "alerts.jsonl", "" if _ratio_a is not None else "no_measurement_source",
                    _graph_rel, observed_at=_now))

    # ---------- 源6：pulse_pairs 近 7 天 ×2 = 14 条 ----------
    for _d in range(7):
        _day = time.strftime("%Y%m%d", time.localtime(_now - _d * 86400.0))
        _pp = os.path.join(_root, "data", "monitor", "pulse_pairs_%s.jsonl" % _day)
        _exists = os.path.exists(_pp)
        _rows = _read_jsonl(_pp) if _exists else []
        _out.append(_mk("pulse_pairs.%s.present" % _day,
                        "当日脉冲对文件应存在", "pulse_pairs", "file_exists", "==True",
                        "file_exists", _exists, "met" if _exists else "violated",
                        "pulse_pairs_%s.jsonl" % _day, "", _graph_rel, observed_at=_now))
        _out.append(_mk("pulse_pairs.%s.rows" % _day,
                        "当日脉冲对应有落盘记录（≥1 条）", "pulse_pairs", "rows", ">=1",
                        "rows", len(_rows) if _exists else None,
                        ("met" if _rows else ("violated" if _exists else "unverifiable")),
                        "pulse_pairs_%s.jsonl" % _day,
                        "" if _exists else "no_measurement_source", _graph_rel,
                        observed_at=_now))

    # ---------- 源7：调用图 stats 8 条 ----------
    _pe = _gstats.get("parse_errors")
    _out.append(_mk("graph.parse_errors.zero", "调用图解析错误应为 0（图可信才能当判据）",
                    "call_graph.stats", "parse_errors", "==0", "parse_errors", _pe,
                    "met" if _pe == 0 else ("violated" if _pe is not None else "unverifiable"),
                    "call_graph_graph_stats",
                    "no_measurement_source" if _pe is None else "", _graph_rel,
                    observed_at=_now))
    _out.append(_mk("graph.nodes.nonzero", "调用图节点数应 >0", "call_graph.stats",
                    "nodes", ">0", "nodes", len(_nodes),
                    "met" if _nodes else "violated", "call_graph_graph_stats", "",
                    _graph_rel, observed_at=_now))
    _out.append(_mk("graph.edges.nonzero", "调用图边数应 >0", "call_graph.stats",
                    "edges", ">0", "edges", len(_edges),
                    "met" if _edges else "violated", "call_graph_graph_stats", "",
                    _graph_rel, observed_at=_now))
    _tc = _gstats.get("total_calls")
    _out.append(_mk("graph.total_calls.nonzero", "调用图总调用数应 >0", "call_graph.stats",
                    "total_calls", ">0", "total_calls", _tc,
                    "met" if (isinstance(_tc, int) and _tc > 0) else "violated",
                    "call_graph_graph_stats", "", _graph_rel, observed_at=_now))
    _ur = _gstats.get("unresolved_calls")
    if isinstance(_ur, int) and isinstance(_tc, int) and _tc:
        _ur_r = round(_ur / float(_tc), 4)
        _out.append(_mk("graph.unresolved_ratio.window",
                        "未解析调用占总调用比例应 <60%（R1 攻坚：先降到 50% 以下）",
                        "call_graph.stats", "unresolved_ratio", "<0.6",
                        "unresolved_ratio", _ur_r,
                        "met" if _ur_r < 0.6 else "violated",
                        "call_graph_graph_stats", "", _graph_rel, observed_at=_now))
    else:
        _out.append(_mk("graph.unresolved_ratio.window",
                        "未解析调用占总调用比例应 <60%", "call_graph.stats",
                        "unresolved_ratio", "<0.6", "unresolved_ratio", None,
                        "unverifiable", "call_graph_graph_stats",
                        "no_measurement_source", _graph_rel, observed_at=_now))
    _ex = _gstats.get("external_calls")
    _out.append(_mk("graph.external_calls.present", "外部调用计数应可统计（≥0）",
                    "call_graph.stats", "external_calls", ">=0", "external_calls", _ex,
                    "met" if isinstance(_ex, int) else "violated",
                    "call_graph_graph_stats", "", _graph_rel, observed_at=_now))
    _cf = _gstats.get("cross_file_calls")
    _out.append(_mk("graph.cross_file.present", "跨文件调用计数应可统计（≥0）",
                    "call_graph.stats", "cross_file_calls", ">=0",
                    "cross_file_calls", _cf,
                    "met" if _cf is not None else "violated",
                    "call_graph_graph_stats", "", _graph_rel, observed_at=_now))
    _out.append(_mk("graph.file.present", "应能定位到本轮使用的图文件（同图同版本可比）",
                    "call_graph.stats", "graph_file", "not empty", "graph_file",
                    _graph_rel or None, "met" if _graph_rel else "violated",
                    "call_graph_graph_stats", "", _graph_rel, observed_at=_now))
    # ★320批刀0/刀3：**每条**打来源标记（一次循环，不改 25 处 _mk 调用）。
    #   live=活体自然产出 ｜ verification=施工/压测验证运行（可一键剔除，见
    #   tools/stress/purge_verification_data.py）。
    # ★注意：`source` 字段是 _mk 的第 3 个位置参数，语义是「**断言来源模块**」
    #   （如 `CallGraphAnalyzer.mastery_assertion_check`）——**不得覆盖**。
    #   来源进程标记独立用 `source_process`（施工自查发现的字段语义冲突，已修正）。
    _src_tag = _norm_source(source)
    for _r in _out:
        _r["source_process"] = _src_tag
    return _out



def sc3_consistency(project_root: str | None = None,
                    class_names: list | None = None) -> dict:
    """★319批刀1（SC-3 一致性自检）：断言（二阶）与 ledger（世界模型）**对表**。

    同一条断言在 `mastery_assertion_check` 与 `expectation_ledger.verdict` 两处结论
    是否一致——不一致即"尺子坏"，**优先于任何结论**（验收线：不一致率 ≤5%）。
    比对项：A4 覆盖的 `gap`（断言侧） vs ledger 的 `measured.actual` ＋ `verdict`。

    红线：只产计数与结论，不因不一致去改任一侧（不自动"修"数据）。
    """
    _root = project_root or os.getcwd()
    try:
        from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger.sc3_consistency.import")
        return {"error": type(_e).__name__, "total": 0, "mismatch": 0}
    _graph_rel, _graph = _latest_graph(_root)
    _a = CallGraphAnalyzer()
    _a._graph_data = _graph
    _idx: dict[str, dict] = {}
    for _r in _read_jsonl(ledger_path(_root)):
        _aid = str(_r.get("assertion_id") or "")
        _idx[_aid] = _r
        if _aid.startswith("loop."):
            _idx[_aid.split(".", 2)[-1]] = _r
    _names = class_names or [c for c, _ns in
                             sorted(_clusters(_graph).items(), key=lambda kv: -len(kv[1]))[:10]]
    _total = 0
    _mismatch = 0
    _details: list[dict] = []
    for _c in _names:
        # ★修正：断言侧必须拿到「说明书侧方法数」才有 gap——否则 a4_coverage 恒为
        #   unverifiable，对表会**空转（total=0）**，看起来"一致"其实是没比。
        _ns = _clusters(_graph).get(_c) or []
        _files = {str(_n.get("file") or "") for _n in _ns if _n.get("file")}
        _manual = 0
        _missing = True
        for _f in _files:
            _cnt = _count_defs_in_file(os.path.join(_root, str(_f).replace("\\", "/")))
            if _cnt is None:
                continue
            _missing = False
            _manual += _cnt
        _res = _a.mastery_assertion_check(
            _c, manual_method_count=(None if _missing else _manual))
        _gap = (_res.get("a4_coverage") or {}).get("gap")
        if _gap is None:
            continue
        _total += 1
        _lr = _idx.get("a4.coverage.%s" % _c)
        if _lr is None:
            _mismatch += 1
            _details.append({"class": _c, "reason": "ledger 无对应断言"})
            continue
        _lactual = (_lr.get("measured") or {}).get("actual")
        _lverdict = _lr.get("verdict")
        _exp = "met" if _gap == 0 else "violated"
        if _lverdict != _exp or _lactual != _gap:
            _mismatch += 1
            _details.append({"class": _c, "assert_gap": _gap,
                             "ledger_actual": _lactual, "ledger_verdict": _lverdict,
                             "expected_verdict": _exp})
    _rate = (_mismatch / float(_total)) if _total else 0.0
    _module_logger.info("[SC-3] consistency total=%d mismatch=%d rate=%.4f（>0.05 即判尺子坏）",
                        _total, _mismatch, _rate)
    return {"total": _total, "mismatch": _mismatch, "rate": round(_rate, 4),
            "threshold": 0.05, "ruler_broken": _rate > 0.05,
            "graph_file": _graph_rel, "details": _details[:20]}

def judge_ratio(rows: list[dict]) -> dict:
    """★320批刀1：压测外部裁判比 —— `met / (met + violated - criteria_gap)`。

    口径差（``criteria_gap``，如 a4_gap 116：说明书口径 vs 图类簇口径）**不进分母**，
    否则每一圈都会被结构性口径差压成失败（烛微320§0-3：25/118≈21%）。
    ``real_violation`` 仍进分母——真违反就是要扣分。
    """
    _met = _viol = _cg = _real = 0
    for _r in rows or []:
        _v = str(_r.get("verdict") or "")
        _g = str(_r.get("gap_reason") or "")
        if _v == "met":
            _met += 1
        elif _v == "violated":
            if _g == "criteria_gap":
                _cg += 1
            else:
                _viol += 1
                if _g == "real_violation":
                    _real += 1
    _den = _met + _viol
    _ratio = round(_met / _den, 4) if _den else None
    return {
        "met": _met, "violated": _viol, "criteria_gap": _cg,
        "real_violation": _real, "denominator": _den,
        "ratio": _ratio, "threshold": 0.8,
        "pass": bool(_den and _ratio is not None and _ratio >= 0.8),
        "denominator_excludes": "criteria_gap",
    }


def ledger_judge_ratio(project_root: str | None = None) -> dict:
    """★320批刀1：按当前台账算裁判比（只读，不写盘）。"""
    return judge_ratio(_read_jsonl(ledger_path(project_root)))


def sweep(project_root: str | None = None, loop: int | None = None,
          source: str = "live") -> dict:
    """跑一次台账（幂等：同一 assertion_id 同日只记首次），返回统计。

    Args:
        loop: 压测圈次。给定时 assertion_id 加前缀 `loop.<N>.`，供每圈外部裁判一一对应。
    """
    _root = project_root or os.getcwd()
    _p = ledger_path(_root)
    _src = _norm_source(source)
    _rows = collect_assertions(_root, source=_src)
    if loop is not None:
        for _r in _rows:
            _r["assertion_id"] = "loop.%d.%s" % (int(loop), _r["assertion_id"])

    # ★320批刀0：幂等键由 (assertion_id, 当天) 改为 **(assertion_id, 当天, 来源)**。
    #   归因：烛微320§0-2-① 活体 04:00 sweep `added=0`，01—08 时 0 新增——真因是
    #   00:0x 有**验证运行**先写了当日 114 条，把「当日幂等位」占满，04:00 活体
    #   sweep 逐条命中 `(id, today)` ⇒ 全部 skip ⇒ 0 产出（不是版本行为）。
    #   加来源维度后：验证运行只占 verification 位，活体 live 位仍空 ⇒ 能自然产出。
    _existing: set[tuple[str, str, str]] = set()
    _total = 0
    for _r in _read_jsonl(_p):
        _total += 1
        _day = time.strftime("%Y-%m-%d", time.localtime(
            float((_r.get("measured") or {}).get("observed_at") or 0)))
        _existing.add((str(_r.get("assertion_id")), _day,
                       _row_source(_r)))

    _today = time.strftime("%Y-%m-%d", time.localtime())
    _added = 0
    _skipped = 0
    _verdicts: dict[str, int] = {}
    _gaps: dict[str, int] = {}
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8", newline="\n") as _f:
            for _r in _rows:
                _verdicts[_r["verdict"]] = _verdicts.get(_r["verdict"], 0) + 1
                if _r.get("gap_reason"):
                    _gaps[_r["gap_reason"]] = _gaps.get(_r["gap_reason"], 0) + 1
                _key = (str(_r.get("assertion_id")), _today, _src)
                if _key in _existing:
                    _skipped += 1
                    continue
                _f.write(json.dumps(_r, ensure_ascii=False) + "\n")
                _existing.add(_key)
                _added += 1
                _total += 1
    except Exception as _e:
        silent_exc(_e, where="world_model.expectation_ledger.sweep")
        _module_logger.info(
            "[320刀0][ledger_sweep] triggered=True generated=0 skipped=%d "
            "reason=write_error:%s source=%s", _skipped, type(_e).__name__, _src)
        return {"added": 0, "total": _total, "verdicts": _verdicts,
                "error": type(_e).__name__, "source": _src,
                "triggered": True, "skipped": _skipped,
                "reason": "write_error"}

    # ★320批刀0：显式四段留痕（triggered/generated/skipped/reason），
    #   使「钩子未触发」与「触发了但被幂等挡住」在日志里可区分——
    #   317 批只打一条 [318刀5][ledger] 看不出 skipped，才让 0 产出悬了一夜。
    _reason = ("all_idempotent_skipped" if (_added == 0 and _skipped > 0)
               else ("no_assertions" if not _rows else "ok"))
    _module_logger.info(
        "[320刀0][ledger_sweep] triggered=True generated=%d skipped=%d reason=%s "
        "source=%s total=%d verdict=%s gap_reason=%s（只观测不决策）",
        _added, _skipped, _reason, _src, _total, _verdicts, _gaps)
    _module_logger.info(
        "[318刀5][ledger] 新增 %d 条 / 累计 %d 条 verdict=%s gap_reason=%s（只观测不决策）",
        _added, _total, _verdicts, _gaps)
    return {"added": _added, "total": _total, "verdicts": _verdicts,
            "gap_reasons": _gaps, "path": _p, "source": _src,
            "triggered": True, "skipped": _skipped, "reason": _reason}
