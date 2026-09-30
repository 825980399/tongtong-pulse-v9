"""LLM 调用模式分析器 —— 依赖度治理（主线第43批 T2 / P0-250）。

背景
----
第41批实测：大模型依赖度 **96.96%**，其中 **98.8%** 的调用来自自主进化循环。
要降低依赖度，先要看**哪些调用是重复/冗余的、可以合并或消除**。
本模块只做**离线分析 + 建议输出**，**不改动任何调用逻辑**（L1 观测级）。

分析维度
--------
1. **分布**：场景（``origin``）/ 渠道 / 模型 / 状态
2. **重复模式**：同一 ``prompt`` 出现频次（Top-N）—— 高频即"可缓存/可合并"候选
3. **失败模式**：``status=failed`` 占比、失败时 ``error`` 是否为空（**无因失败不可诊断**）、
   同一 prompt 反复失败（**重试无去重**）
4. **字段缺失**：``duration``/``tokens``/``prompt_version`` 覆盖率
5. **污染**：非生产记录（测试桩）占比 —— 复用 ``data_quality_evaluator.classify_record``

输出
----
``data/llm_traces/call_pattern_analysis.json``，含 ``patterns``（可优化模式清单）、
``findings``（结论文本）与 ``recommendations``（按预期收益排序的建议）。
"""
from __future__ import annotations
from nucleus._silent_except import silent_exc

import hashlib
import io
import json
import os
import sys
import time
from collections import Counter
from typing import Any

__all__ = [
    "DEFAULT_REPORT_PATH",
    "analyze_records",
    "analyze_day",
    "save_report",
    "analyze_and_report",
    "format_summary_line",
    "analyzer_enabled",
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 模式分析报告输出路径。
DEFAULT_REPORT_PATH = os.path.join(_PROJECT_ROOT, "data", "llm_traces",
                                   "call_pattern_analysis.json")

#: 判定"高频重复"的阈值（同一 prompt 出现 >= N 次即计入重复模式）。
REPEAT_MIN = 5
#: 判定"失败重试无去重"的阈值（同一 prompt 失败 >= N 次）。
FAIL_REPEAT_MIN = 3


def analyzer_enabled() -> bool:
    """灰度开关（默认 True，L1 仅观测）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_LLM_CALL_PATTERN_ANALYSIS", True))
    except Exception as e:
        silent_exc(e, where="nucleus.llm.call_pattern_analyzer::analyzer_enabled L62")
        return True


def _in_test_env() -> bool:
    try:
        return ("pytest" in sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception as e:
        silent_exc(e, where="nucleus.llm.call_pattern_analyzer::_in_test_env L69")
        return False


def _is_production_path(path: str) -> bool:
    try:
        _p = os.path.abspath(path).replace("\\", "/").lower()
        _r = os.path.abspath(_PROJECT_ROOT).replace("\\", "/").lower()
        return _p.startswith(_r + "/data/")
    except Exception as e:
        silent_exc(e, where="nucleus.llm.call_pattern_analyzer::_is_production_path L78")
        return True


def _pkey(prompt: Any) -> str:
    return hashlib.md5(str(prompt or "").encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------------ 主分析
def analyze_records(records: list, *, include_suspect: bool = True) -> dict:
    """分析调用记录，返回分布 / 重复模式 / 失败模式 / 可优化清单。"""
    from nucleus.llm.data_quality_evaluator import classify_record, channel_whitelist
    _all = [r for r in (records if isinstance(records, (list, tuple)) else [])
            if isinstance(r, dict)]
    _wl = channel_whitelist()
    _rc: Counter = Counter(_pkey(r.get("prompt")) for r in _all)
    _cls = Counter(classify_record(r, _wl, Counter(
        str(r.get("prompt") or "") for r in _all)) for r in _all)

    _scope = _all
    if not include_suspect:
        _scope = [r for r in _all
                  if classify_record(r, _wl, Counter(
                      str(x.get("prompt") or "") for x in _all)) == "production"]

    _n = len(_scope)
    if not _n:
        return {"status": "empty", "total": len(_all), "scored": 0,
                "patterns": [], "findings": ["无可用记录"], "recommendations": []}

    _origin = Counter(str(r.get("origin") or "<空>") for r in _scope)
    _channel = Counter(str(r.get("channel") or "<空>") for r in _scope)
    _model = Counter(str(r.get("model") or "<空>") for r in _scope)
    _status = Counter(str(r.get("status") or "<空>") for r in _scope)
    _failed = [r for r in _scope if str(r.get("status")) == "failed"]
    _no_reason = sum(1 for r in _failed if not str(r.get("error") or "").strip())

    # ---- 重复模式（按 prompt 频次）
    _pcount: Counter = Counter()
    _sample: dict = {}
    for _r in _scope:
        _k = _pkey(_r.get("prompt"))
        _pcount[_k] += 1
        _sample.setdefault(_k, str(_r.get("prompt"))[:120])
    _repeats = [(k, v) for k, v in _pcount.items() if v >= REPEAT_MIN]
    _repeats.sort(key=lambda x: -x[1])
    _redundant = sum(v - 1 for _k, v in _repeats)

    # ---- 失败重试无去重
    _fail_by_prompt: Counter = Counter()
    for _r in _failed:
        _fail_by_prompt[_pkey(_r.get("prompt"))] += 1
    _fail_repeat = [(k, v) for k, v in _fail_by_prompt.items() if v >= FAIL_REPEAT_MIN]
    _fail_repeat.sort(key=lambda x: -x[1])

    # ---- 覆盖率
    _dur_ok = sum(1 for r in _scope
                  if isinstance(r.get("duration"), (int, float)) and r.get("duration")) / _n
    _tok_ok = sum(1 for r in _scope
                  if isinstance(r.get("tokens"), (int, float)) and r.get("tokens")) / _n
    _pv_ok = sum(1 for r in _scope if str(r.get("prompt_version") or "").strip()) / _n

    _patterns = []
    _recs_out = []

    if _repeats:
        _patterns.append({
            "name": "repeat_prompt",
            "label": "重复 prompt 调用",
            "count": sum(v for _k, v in _repeats),
            "redundant": _redundant,
            "top": [{"prompt": _sample.get(k, ""), "times": v} for k, v in _repeats[:5]],
        })
        _recs_out.append({
            "action": "为高频 prompt 增加**结果复用/缓存**（如意图分类这类确定性任务）",
            "expected_gain": "最多可减少 %d 次调用（占样本 %.1f%%）"
                             % (_redundant, 100.0 * _redundant / _n),
            "risk": "低（幂等任务可安全复用）",
        })

    if _fail_repeat:
        _patterns.append({
            "name": "fail_retry_no_dedup",
            "label": "失败重试无去重",
            "count": len(_fail_repeat),
            "redundant": sum(v - 1 for _k, v in _fail_repeat),
            "top": [{"prompt": _sample.get(k, ""), "fails": v} for k, v in _fail_repeat[:5]],
        })
        _recs_out.append({
            "action": "失败调用加入**短期熔断/退避**，同一 prompt 连续失败后停止重试",
            "expected_gain": "减少 %d 次无效重试"
                             % sum(v - 1 for _k, v in _fail_repeat),
            "risk": "低（仅影响失败路径）",
        })

    if _failed and _no_reason / max(1, len(_failed)) > 0.5:
        _patterns.append({
            "name": "failed_without_reason",
            "label": "失败调用未记录原因",
            "count": len(_failed),
            "no_reason": _no_reason,
            "top": [],
        })
        _recs_out.append({
            "action": "在失败路径补记 ``error``（异常类型 + 简要原因）",
            "expected_gain": "使 %d 条失败可诊断（间接降低重复试错）" % _no_reason,
            "risk": "极低（仅日志字段）",
        })

    if _pv_ok < 0.5:
        _patterns.append({
            "name": "missing_prompt_version",
            "label": "prompt_version 缺失",
            "count": _n,
            "coverage": round(_pv_ok, 4),
            "top": [],
        })
        _recs_out.append({
            "action": "调用点补 ``prompt_version``（提示词版本号）",
            "expected_gain": "可按提示词版本回溯效果差异（为后续精简提供基线）",
            "risk": "极低",
        })

    if _tok_ok < 0.5 or _dur_ok < 0.5:
        _patterns.append({
            "name": "usage_not_captured",
            "label": "用量/延迟采集不全",
            "count": _n,
            "duration_coverage": round(_dur_ok, 4),
            "token_coverage": round(_tok_ok, 4),
            "top": [],
        })
        _recs_out.append({
            "action": "渠道响应解析补 ``usage``（tokens）与端到端 ``duration``",
            "expected_gain": "使消耗可量化 → 精简效果可度量",
            "risk": "低",
        })

    if len(_channel) > 3 and _n >= 20:
        _patterns.append({
            "name": "channel_scatter",
            "label": "渠道/模型选择分散",
            "count": len(_channel),
            "channels": dict(_channel.most_common()),
            "models": dict(_model.most_common(8)),
            "top": [],
        })
        _recs_act = {
            "action": "按任务类型**收敛渠道/模型**（确定性任务用最便宜的渠道）",
            "expected_gain": "降低平均成本与延迟（当前 %d 个渠道 / %d 个模型）"
                             % (len(_channel), len(_model)),
            "risk": "中（需按任务分类，见设计文档）",
        }
        _recs_out.append(_recs_act)

    _findings = []
    if _repeats:
        _findings.append("★最高频 prompt 出现 %d 次（`%s`）—— 属确定性任务，"
                         "可结果复用" % (_repeats[0][1], _sample.get(_repeats[0][0], "")[:60]))
    _findings.append("场景分布：%s" % dict(_origin.most_common(5)))
    _findings.append("渠道分布：%s" % dict(_channel.most_common(5)))
    if _failed:
        _findings.append("失败 %d/%d（%.1f%%），其中 %d 条**未记录原因**"
                         % (len(_failed), _n, 100.0 * len(_failed) / _n, _no_reason))
    if _cls.get("suspect"):
        _findings.append("★非生产记录 %d 条（测试污染）—— 已在 T1 加测试环境防御"
                         % _cls["suspect"])

    return {
        "status": "ok",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(_all),
        "scored": _n,
        "include_suspect": bool(include_suspect),
        "distribution": {
            "origin": dict(_origin.most_common()),
            "channel": dict(_channel.most_common()),
            "model": dict(_model.most_common()),
            "status": dict(_status.most_common()),
        },
        "coverage": {"duration": round(_dur_ok, 4), "tokens": round(_tok_ok, 4),
                     "prompt_version": round(_pv_ok, 4)},
        "patterns": _patterns,
        "findings": _findings,
        "recommendations": _recs_out,
    }


def analyze_day(day: str | None = None, trace_dir: str | None = None,
                *, include_suspect: bool = True) -> dict:
    """分析某一天的留存文件。"""
    from nucleus.llm.data_quality_evaluator import DEFAULT_TRACE_DIR, load_records, _trace_dir
    _d = trace_dir or _trace_dir() or DEFAULT_TRACE_DIR
    _path = os.path.join(_d, "calls_%s.jsonl" % (day or time.strftime("%Y%m%d")))
    _recs = load_records(_path)
    _rep = analyze_records(_recs, include_suspect=include_suspect)
    _rep["source_file"] = _path
    _rep["file_exists"] = os.path.isfile(_path)
    return _rep


def format_summary_line(report: dict) -> str:
    _p = report.get("patterns", []) or []
    _names = ",".join(x.get("name", "?") for x in _p)
    return "[调用模式] 样本=%s 可优化模式=%d（%s）建议=%d" % (
        report.get("scored", 0), len(_p), _names or "-", len(report.get("recommendations", [])))


def save_report(report: dict, path: str | None = None) -> str | None:
    """写分析报告；测试环境 + 生产路径 → 拒写。"""
    _p = path or DEFAULT_REPORT_PATH
    if path is None and _in_test_env() and _is_production_path(_p):
        return None
    try:
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
        with io.open(_p, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(report, ensure_ascii=False, indent=2))
        return _p
    except OSError as e:
        silent_exc(e, "call_pattern_analyzer.py:297:save_report", level="warning")
        return None


def analyze_and_report(day: str | None = None, trace_dir: str | None = None,
                       report_path: str | None = None, logger: Any = None,
                       *, include_suspect: bool = True) -> dict | None:
    """离线分析入口（L1 仅观测）。"""
    if not analyzer_enabled():
        return None
    _rep = analyze_day(day, trace_dir, include_suspect=include_suspect)
    _rep["report_path"] = save_report(_rep, report_path)
    if logger is not None:
        try:
            logger.info(format_summary_line(_rep))
            for _f in _rep.get("findings", []):
                logger.info("[调用模式] %s", _f)
        except Exception as _e:
            print("[调用模式] 日志输出失败: %s: %s" % (type(_e).__name__, _e), file=sys.stderr)
    return _rep
