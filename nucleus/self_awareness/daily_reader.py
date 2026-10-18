# -*- coding: utf-8 -*-
"""★第277批：晨读 reader —— 让"有人读"这件事被记账。

背景（烛微276批前置分析）：跨夜日报 report_*.txt 已有 35 份但全仓读取者=0，
infofield_dead_events.json（262刀1产物）同样无人读。本模块补齐两类"消费方"：

* 刀1：把 report_bus 分型消费率追加进跨夜日报小节并打 INFO 日志；
* 刀3：读最新一份 report_*.txt，只解析【总结】＋【综合评分】两行成结构化 dict；
* 刀4：并读 data/monitor/infofield_dead_events.json，注入 dead_events / ghost_listeners；
* 刀5：每周一 03:00 清理 data/monitor/、data/reports/ 下超过 7 天的 .tmp。

零新依赖、零语义变更：纯只读观测 + 打日志，不改变任何现有分析/发布行为。
本批为运行期热改，明晨重启生效（挂在 DailyScheduler.run_once）。
"""

import io
import json
import os
import re
import time
from datetime import datetime
from typing import Any, Callable

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("DailyReader")

#: 日报解析限长（字符，约 2000，防超大文件拖慢晨读）
_REPORT_MAX_CHARS = 2000
#: 过期线（秒）：generated_at 距今超过即标 stale、不采
_STALE_SECONDS = 24 * 3600
#: .tmp 清理保留期（秒）：只删超过 7 天的，防误删正在写的
_TMP_RETENTION_SECONDS = 7 * 86400
#: 分型消费率报告类型（与 report_envelope 类型常量一致）
_CONSUMPTION_TYPES = ("health", "pollution", "evolution", "self_cognition", "runtime")


def _default_output_dir(project_root: str) -> str:
    return os.path.join(project_root, "data", "self_awareness")


def _fmt_rate(byc: dict, t: str) -> str:
    _r = (byc.get(t) or {}).get("rate")
    if isinstance(_r, (int, float)):
        return f"{_r * 100:.1f}%"
    return "N/A"


# ------------------------------------------------------------------------- 刀1

def append_consumption_section(report_path: str, logger: Any = None) -> dict[str, Any]:
    """刀1：调 report_bus.get_stats()，把分型消费率追加到跨夜日报文件并打 INFO 日志。

    返回分型消费率 dict（{type: {total, consumed, rate}}）；report_bus 读取失败时返回 {}。
    """
    _log = logger or _module_logger
    try:
        from nucleus.reporting import get_report_bus
        _stats = get_report_bus().get_stats()
    except Exception as e:
        silent_exc(e, "daily_reader.append_consumption_section:get_stats", level="warning")
        return {}
    _byc = _stats.get("by_type_consumption") or {}
    _legacy = _stats.get("legacy_without_consumed", 0)
    try:
        _lines = ["", "【分型消费率】"]
        for _t in _CONSUMPTION_TYPES:
            _c = _byc.get(_t) or {}
            _lines.append(
                f"  · {_t}: {_fmt_rate(_byc, _t)}"
                f"（消费 {_c.get('consumed', 0)}/{_c.get('total', 0)}）")
        _lines.append("")
        with open(report_path, "a", encoding="utf-8") as _f:
            _f.write("\n".join(_lines))
    except Exception as e:
        silent_exc(e, "daily_reader.append_consumption_section:写日报小节", level="warning")
    _log.info("[消费率] health=%s self_cognition=%s runtime=%s legacy=%s",
              _fmt_rate(_byc, "health"), _fmt_rate(_byc, "self_cognition"),
              _fmt_rate(_byc, "runtime"), _legacy)
    return _byc


# ----------------------------------------------------------------- 刀3 / 刀4

def _read_latest_report(output_dir: str, now_fn: Callable[[], datetime],
                        logger: Any) -> dict[str, Any] | None:
    """刀3：取 output_dir 下最新一份 report_*.txt，只解析【总结】＋【综合评分】两行。"""
    _now = now_fn()
    try:
        if not os.path.isdir(output_dir):
            return None
        _cands = []
        for _fn in os.listdir(output_dir):
            if _fn.startswith("report_") and _fn.endswith(".txt"):
                _p = os.path.join(output_dir, _fn)
                try:
                    _cands.append((os.path.getmtime(_p), _p))
                except OSError as e:
                    silent_exc(e, "daily_reader._read_latest_report:mtime")
        if not _cands:
            return None
        _cands.sort(key=lambda _x: _x[0], reverse=True)
        _path = _cands[0][1]
        with io.open(_path, encoding="utf-8") as _f:
            _text = _f.read(_REPORT_MAX_CHARS)
    except Exception as e:
        silent_exc(e, "daily_reader._read_latest_report:读取", level="warning")
        return None
    _score = None
    _dims = None
    _generated_at = None
    for _line in _text.splitlines():
        _s = _line.strip()
        if _s.startswith("【总结】"):
            _dims = _s[len("【总结】"):].strip()
        elif _s.startswith("【综合评分】"):
            _score = _s[len("【综合评分】"):].strip()
        elif _s.startswith("生成时间:"):
            try:
                _generated_at = datetime.fromisoformat(_s[len("生成时间:"):].strip())
            except ValueError as e:
                silent_exc(e, "daily_reader._read_latest_report:生成时间解析")
                _generated_at = None
    if _score is None and _dims is None:
        logger.warning("[晨读] 日报解析失败（【总结】与【综合评分】均缺失），不注入: %s", _path)
        return None
    _stale = bool(_generated_at is not None
                  and (_now - _generated_at).total_seconds() > _STALE_SECONDS)
    return {
        "score": _score,
        "dims": _dims,
        "generated_at": _generated_at.isoformat() if _generated_at else None,
        "path": _path,
        "stale": _stale,
    }


def _read_dead_events(project_root: str, logger: Any) -> dict[str, Any] | None:
    """刀4：读 data/monitor/infofield_dead_events.json，注入死事件审计数据。"""
    _p = os.path.join(project_root, "data", "monitor", "infofield_dead_events.json")
    try:
        if not os.path.isfile(_p):
            return None
        with io.open(_p, encoding="utf-8") as _f:
            _j = json.load(_f)
        _dead = _j.get("dead_events") or []
        _ghost = _j.get("ghost_listeners") or []
        _total = _j.get("total_conditions")
        logger.info("[晨读·死数据审计] dead_events=%d ghost_listeners=%d total_conditions=%s",
                    len(_dead), len(_ghost), _total)
        return {
            "dead_events": len(_dead),
            "ghost_listeners": len(_ghost),
            "total_conditions": _total,
        }
    except Exception as e:
        silent_exc(e, "daily_reader._read_dead_events", level="warning")
        return None


def read_daily_context(output_dir: str | None = None,
                       project_root: str | None = None,
                       now_fn: Callable[[], datetime] | None = None,
                       logger: Any = None) -> dict[str, Any]:
    """刀3+刀4：晨读 —— 读最新跨夜日报 + dead_events，注入当日上下文。

    返回 {"report": {...}|None, "report_stale": bool, "dead_events": {...}|None}。
    stale>24h 的报告不采（report=None）但保留 stale 标记；解析失败即 WARN 不注入。
    """
    _log = logger or _module_logger
    _root = project_root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    _now_fn = now_fn or datetime.now
    _out = output_dir or _default_output_dir(_root)
    _report = _read_latest_report(_out, _now_fn, _log)
    _dead = _read_dead_events(_root, _log)
    _stale = bool(_report is not None and _report.get("stale"))
    if _stale:
        _log.warning("[晨读] 日报已过期（>24h），不采: %s", _report.get("path"))
    return {
        "report": None if _stale else _report,
        "report_stale": _stale,
        "dead_events": _dead,
    }


# ---------------------------------------------------------------------- 刀5

def cleanup_stale_tmp(project_root: str,
                      now_fn: Callable[[], datetime] | None = None,
                      logger: Any = None) -> dict[str, Any] | None:
    """刀5：每周一 03:00 清理 data/monitor/、data/reports/ 下超过 7 天的 .tmp。

    非周一返回 None（不清理）；返回 {"deleted": N}（无 .tmp 也返回 deleted=0）。
    """
    _log = logger or _module_logger
    _now_fn = now_fn or datetime.now
    if _now_fn().weekday() != 0:  # 周一 = 0
        return None
    _deleted = 0
    _threshold = time.time() - _TMP_RETENTION_SECONDS
    _targets = (
        os.path.join(project_root, "data", "monitor"),
        os.path.join(project_root, "data", "reports"),
    )
    for _d in _targets:
        try:
            if not os.path.isdir(_d):
                continue
            for _fn in os.listdir(_d):
                if not _fn.endswith(".tmp"):
                    continue
                _p = os.path.join(_d, _fn)
                try:
                    if os.path.getmtime(_p) < _threshold:
                        os.remove(_p)
                        _deleted += 1
                except OSError as e:
                    silent_exc(e, "daily_reader.cleanup_stale_tmp:删除")
        except Exception as e:
            silent_exc(e, "daily_reader.cleanup_stale_tmp:目录")
    _log.info("[清理·临时文件] 删除%d个.tmp文件", _deleted)
    return {"deleted": _deleted}


# ----------------------------------------------------------------- 278批刀1

def _parse_score(s):
    """从【综合评分】文本（如 "86.65 / 100"）提取浮点分数。"""
    if not s:
        return None
    _m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(_m.group(1)) if _m else None


def _list_reports(output_dir):
    """按 mtime 降序返回 output_dir 下所有 report_*.txt 路径。"""
    _out = []
    try:
        if not os.path.isdir(output_dir):
            return []
        for _fn in os.listdir(output_dir):
            if _fn.startswith("report_") and _fn.endswith(".txt"):
                _p = os.path.join(output_dir, _fn)
                try:
                    _out.append((os.path.getmtime(_p), _p))
                except OSError as e:
                    silent_exc(e, "daily_reader._list_reports:mtime")
    except Exception as e:
        silent_exc(e, "daily_reader._list_reports")
        return []
    _out.sort(key=lambda _x: _x[0], reverse=True)
    return [_p for _, _p in _out]


def _read_report_score(path):
    """读单份日报的【综合评分】分数；失败返回 None。"""
    try:
        with io.open(path, encoding="utf-8") as _f:
            _text = _f.read(_REPORT_MAX_CHARS)
    except Exception as e:
        silent_exc(e, "daily_reader._read_report_score:读取")
        return None
    for _line in _text.splitlines():
        _s = _line.strip()
        if _s.startswith("【综合评分】"):
            return _parse_score(_s[len("【综合评分】"):].strip())
    return None


def get_trend_signal(output_dir=None, project_root=None, now_fn=None, logger=None):
    """278批刀1：读最新两份跨夜日报【综合评分】score 变化，映射 trend_factor ∈ [0.85, 1.15]。

    选A（更保守）：score 降⇒trend_factor<1；score 升⇒trend_factor>1。
    stale（最新日报 generated_at 距今>24h）⇒ trend_factor=1（不修正）。
    返回 {"trend_factor", "score_delta", "stale", "report_id", "as_of"}。
    """
    _log = logger or _module_logger
    _root = project_root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    _now_fn = now_fn or datetime.now
    _out = output_dir or _default_output_dir(_root)
    _fallback = {"trend_factor": 1.0, "score_delta": None, "stale": False,
                 "report_id": None, "as_of": None}
    _latest = _read_latest_report(_out, _now_fn, _log)
    if _latest is None:
        return _fallback
    if _latest.get("stale"):
        return {**_fallback, "stale": True, "report_id": _latest.get("path"),
                "as_of": _latest.get("generated_at")}
    _latest_score = _parse_score(_latest.get("score"))
    if _latest_score is None:
        return _fallback
    _prev_score = None
    _reports = _list_reports(_out)
    if len(_reports) >= 2:
        _prev_score = _read_report_score(_reports[1])
    _delta = (_latest_score - _prev_score) if _prev_score is not None else 0.0
    _factor = max(0.85, min(1.15, 1.0 + _delta / 100.0))
    return {"trend_factor": round(_factor, 4), "score_delta": round(_delta, 2),
            "stale": False, "report_id": _latest.get("path"),
            "as_of": _latest.get("generated_at")}


# ----------------------------------------------------------------- 282批刀3

def read_phase18_signals(logger: Any = None) -> dict[str, Any]:
    """刀3：读 get_phase18_signals().snapshot() 四类结果（reasoning/strategy/knowledge/evolution）。

    不翻 ENABLE_PHASE18_SIGNALS 采集闸；即使采集器未开（snapshot 返回空结构）也安全返回。
    """
    _log = logger or _module_logger
    try:
        from nucleus.telemetry.phase18_signals import get_phase18_signals
        _snap = get_phase18_signals().snapshot()
    except Exception as e:
        silent_exc(e, "daily_reader.read_phase18_signals", level="warning")
        return {}
    return {
        "reasoning_mode": _snap.get("reasoning_mode") or {},
        "strategy_selection": _snap.get("strategy_selection") or [],
        "knowledge_quality": _snap.get("knowledge_quality") or [],
        "evolution": _snap.get("evolution") or [],
    }


def append_phase18_section(report_path: str, logger: Any = None) -> dict[str, Any]:
    """刀3：把【PHASE18信号】节（reasoning/strategy/knowledge 三类）追加到日报 + 打 INFO 日志。

    返回四类信号 dict；日报节只聚焦三类（与任务书 INFO 行口径一致）。
    """
    _log = logger or _module_logger
    _sig = read_phase18_signals(_log)
    _rm = _sig.get("reasoning_mode") or {}
    _st = _sig.get("strategy_selection") or []
    _kq = _sig.get("knowledge_quality") or []
    _log.info("[PHASE18信号] reasoning_mode=%s strategy=%d knowledge=%d",
              (",".join(sorted(_rm.keys())) or "-"), len(_st), len(_kq))
    try:
        _lines = ["", "【PHASE18信号】"]
        _lines.append("  · reasoning_mode: %s" % (",".join(sorted(_rm.keys())) or "（无）"))
        _lines.append("  · strategy_selection: %d 条" % len(_st))
        _lines.append("  · knowledge_quality: %d 条" % len(_kq))
        _lines.append("")
        with open(report_path, "a", encoding="utf-8") as _f:
            _f.write("\n".join(_lines))
    except Exception as e:
        silent_exc(e, "daily_reader.append_phase18_section:写日报小节", level="warning")
    return _sig


def append_mastery_section(report_path: str, logger: Any = None) -> dict[str, Any]:
    """第294批刀1：装掌握率分母，日报出「掌握率=understood/total」。

    背景（烛微291批）：已理解 6677 个方法，但仓内缺稳定 total_methods 出口，
    「掌握率 = 6677/?」无法计算。本函数补齐分母并落到日报：

      * 分母 total = 全仓方法总数（``get_organ_code_structure()`` 各器官
        ``method_count`` 求和，AST 静态扫描，稳定口径）；
      * 分子 understood = 已理解方法数（从信息场 ``code_learner.stats`` 尽力取，
        取不到则 0 并如实标注 source=unavailable）。

    纯只读观测 + 追加日报一行 + 打 INFO，零语义变更。
    """
    _log = logger or _module_logger
    total = 0
    understood = 0
    _src = "unavailable"
    # 分母：全仓方法总数（稳定，AST 静态扫描每器官 method_count 求和）
    try:
        from nucleus.self_inspector import get_self_inspector
        _cs = get_self_inspector().get_organ_code_structure() or {}
        total = sum(int(_v.get("method_count", 0))
                    for _v in _cs.values() if isinstance(_v, dict))
    except Exception as e:
        silent_exc(e, "daily_reader.append_mastery_section:total", level="warning")
    # 分子：已理解方法数（信息场 code_learner.stats，防御性多 key 读取）
    try:
        from nucleus.field.InfoField import get_info_field
        _if = get_info_field()
        _st = _if.get_current("code_learner.stats") if _if is not None else None
        if isinstance(_st, dict):
            _u = _st.get("understood")
            if isinstance(_u, (int, float)):
                understood = int(_u)
                _src = "info_field.code_learner.stats"
            if total == 0:
                _t = _st.get("total_methods")
                if isinstance(_t, (int, float)):
                    total = int(_t)
    except Exception as e:
        silent_exc(e, "daily_reader.append_mastery_section:understood", level="warning")
    _pct = round(min(100.0, understood / max(1, total) * 100), 1) if total > 0 else 0.0
    try:
        _lines = ["", "【掌握率】",
                  f"  · 已理解 {understood}/{total} 个方法（掌握率 {_pct}%）", ""]
        with open(report_path, "a", encoding="utf-8") as _f:
            _f.write("\n".join(_lines))
    except Exception as e:
        silent_exc(e, "daily_reader.append_mastery_section:写日报小节", level="warning")
    _log.info("[掌握率] understood=%d total=%d mastery=%.1f%% source=%s",
              understood, total, _pct, _src)
    return {"understood": understood, "total_methods": total,
            "mastery_pct": _pct, "source": _src}
