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
    # ★第300批刀2：掌握率三源标注（numerator_source / total_source / 进程内计数重启回跳）。
    #   真相：PulseCodeLearner.get_stats 的 understood = max(快照恢复值, 知识库实时计数)，
    #   get_stats **只返回 max 后的值**，日报侧无分项 ⇒ 无法二选一区分
    #   restored_snapshot / kb_live_count，如实标注为 snapshot_vs_kb_max(进程内)。
    _numerator_source = ("snapshot_vs_kb_max(进程内)" if _src != "unavailable" else _src)
    _total_source = ("code_structure.method_count" if total > 0 else "unavailable")
    try:
        _lines = ["", "【掌握率】",
                  f"  · 已理解 {understood}/{total} 个方法（掌握率 {_pct}%）",
                  f"  · numerator_source={_numerator_source} total_source={_total_source}",
                  "  · 本值为进程内计数，重启回跳（非累计总量）", ""]
        with open(report_path, "a", encoding="utf-8") as _f:
            _f.write("\n".join(_lines))
    except Exception as e:
        silent_exc(e, "daily_reader.append_mastery_section:写日报小节", level="warning")
    _log.info("[掌握率] understood=%d total=%d mastery=%.1f%% source=%s",
              understood, total, _pct, _src)
    return {"understood": understood, "total_methods": total,
            "mastery_pct": _pct, "source": _src,
            "numerator_source": _numerator_source,
            "total_source": _total_source,
            "process_local_note": "本值为进程内计数，重启回跳"}


def _read_last_queue_stats(output_dir: str) -> dict[str, Any] | None:
    """第300批刀4：读上一份日报的【入队哨兵】四数作为跨重启累计基线（只读，不新落盘）。

    解析最近一份 ``report_*.txt`` 里「累计(含历史基线)」行的三个数；找不到 → None。
    """
    import glob
    import re as _re
    try:
        if not output_dir or not os.path.isdir(output_dir):
            return None
        _cands = sorted(glob.glob(os.path.join(output_dir, "report_*.txt")),
                        key=os.path.getmtime, reverse=True)
        for _p in _cands[:3]:
            try:
                _text = io.open(_p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for _ln in _text.splitlines():
                if "累计(含历史基线)" not in _ln:
                    continue
                _mm = _re.search(r"新补丁=(\d+)", _ln)
                _rm = _re.search(r"入队被拦=(\d+)", _ln)
                _km = _re.search(r"M92拒绝=(\d+)", _ln)
                if _mm and _rm and _km:
                    return {"new_patches": int(_mm.group(1)),
                            "shape_rejected": int(_rm.group(1)),
                            "m92_rejected": int(_km.group(1))}
    except Exception as e:
        silent_exc(e, "daily_reader._read_last_queue_stats", level="warning")
    return None


def append_queue_stats_section(report_path: str, logger: Any = None) -> dict[str, Any]:
    """第298批刀2 + 第300批刀4：日报固定输出入队哨兵四数（本周期 + 跨重启累计）。

    新补丁=NN｜入队被拦=MM｜M92拒绝=KK｜pending=PP（NN/MM/KK 读 PatchManager 类属性，
    PP 读 pending_patches.json 长度）。读法：MM/KK>0 且 PP=0 ⇒ 治好了；NN=0 ⇒ 停一停。
    ★刀4：累计 = 上一份日报四数（历史基线，只读） + 本周期增量 ⇒ 跨重启不归零。
    """
    _log = logger or _module_logger
    _nn = _mm = _kk = 0
    try:
        from nucleus.reasoning.PatchManager import PatchManager
        _nn = int(PatchManager._enqueue_count or 0)
        _mm = int(PatchManager._shape_rejected_count or 0)
        _kk = int(PatchManager._m92_rejected_count or 0)
    except Exception as e:
        silent_exc(e, "daily_reader.append_queue_stats_section:counters", level="warning")
    _pp = 0
    try:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _pend = os.path.join(_root, "data", "patches", "pending_patches.json")
        if os.path.isfile(_pend):
            _pp = len(json.load(io.open(_pend, encoding="utf-8")))
    except Exception as e:
        silent_exc(e, "daily_reader.append_queue_stats_section:pending", level="warning")
    # ★刀4：历史基线（上一份日报）→ 跨重启累计
    _base = _read_last_queue_stats(os.path.dirname(os.path.abspath(report_path)))
    _cum = {
        "new_patches": _nn + int((_base or {}).get("new_patches", 0) or 0),
        "shape_rejected": _mm + int((_base or {}).get("shape_rejected", 0) or 0),
        "m92_rejected": _kk + int((_base or {}).get("m92_rejected", 0) or 0),
    }
    try:
        _lines = ["", "【入队哨兵】",
                  f"  · 本周期：新补丁={_nn}｜入队被拦={_mm}｜M92拒绝={_kk}｜pending={_pp}",
                  f"  · 累计(含历史基线)：新补丁={_cum['new_patches']}｜"
                  f"入队被拦={_cum['shape_rejected']}｜M92拒绝={_cum['m92_rejected']}", ""]
        with open(report_path, "a", encoding="utf-8") as _f:
            _f.write("\n".join(_lines))
    except Exception as e:
        silent_exc(e, "daily_reader.append_queue_stats_section:写日报小节", level="warning")
    _log.info("[入队哨兵] 本周期 新补丁=%d 被拦=%d M92=%d pending=%d | 累计 新补丁=%d 被拦=%d M92=%d",
              _nn, _mm, _kk, _pp, _cum["new_patches"], _cum["shape_rejected"],
              _cum["m92_rejected"])
    return {"new_patches": _nn, "shape_rejected": _mm, "m92_rejected": _kk,
            "pending": _pp, "cumulative": _cum, "baseline": _base}


def append_remediation_stats_section(report_path: str, logger: Any = None) -> dict[str, Any]:
    """第300批刀3：日报输出补救链上游三数（与298四数同构）。

    进入校验=N｜判定阻断=M｜attempt=K。读法：N=0⇒无输入(无对话)；
    N>0且M=0⇒没有病可补(健康，值得记功)；M>0且K<M⇒中间有闸(被拦)★唯一需修；
    K>0且success不涨⇒补救本身失败。
    """
    _log = logger or _module_logger
    _n = _m = _k = 0
    try:
        from nucleus.LLMDependencyMetrics import get_dependency_snapshot
        _snap = get_dependency_snapshot() or {}
        _rem = (_snap.get("counters", {}) or {}).get("remediation", {}) or {}
        _n = int(_rem.get("entered_verification", 0) or 0)
        _m = int(_rem.get("blocked", 0) or 0)
        _k = int(_rem.get("attempt", 0) or 0)
    except Exception as e:
        silent_exc(e, "daily_reader.append_remediation_stats_section:counters", level="warning")
    try:
        _lines = ["", "【补救链·三数】",
                  f"  · 进入校验={_n} | 判定阻断={_m} | attempt={_k}", ""]
        with open(report_path, "a", encoding="utf-8") as _f:
            _f.write("\n".join(_lines))
    except Exception as e:
        silent_exc(e, "daily_reader.append_remediation_stats_section:写日报小节", level="warning")
    _log.info("[补救链·三数] 进入校验=%d 判定阻断=%d attempt=%d", _n, _m, _k)
    return {"entered_verification": _n, "blocked": _m, "attempt": _k}

# ----------------------------------------------------------------- 313批刀1

def read_global_awareness(logger: Any = None) -> dict[str, Any]:
    """★第313批 刀1：读 InfoField 的**全局认知快照**（纯内存，绝不跨器官现调）。

    307 批的层次纪律：跨器官取数只发生在 `integrate_global_awareness()`；
    本函数只调 `get_global_awareness()`（读缓存 + 深拷贝），不触发任何器官干活。
    读不到（InfoField 不可用 / 未刷新）如实返回空 dict，不猜默认值。
    """
    try:
        from nucleus.field.InfoField import get_info_field
        _if = get_info_field()
        if _if is None or not hasattr(_if, "get_global_awareness"):
            return {}
        _snap = _if.get_global_awareness()
    except Exception as e:
        silent_exc(e, "daily_reader.read_global_awareness", level="warning")
        return {}
    return dict(_snap) if isinstance(_snap, dict) else {}


def _m313_fmt_gaps(gaps: Any) -> tuple[str, int]:
    """把 `gaps` 渲染成 `[]` / `['a', 'b']`，并返回条数（非 list 如实标 unavailable）。"""
    if not isinstance(gaps, list):
        return "unavailable", 0
    return "[" + ", ".join(repr(str(_g)) for _g in gaps) + "]", len(gaps)


def _m313_fmt_as_of(as_of: Any) -> str:
    """epoch 秒 → 可读 ISO；0 / 非数值 ⇒ `未刷新`（不伪造时间）。"""
    if isinstance(as_of, bool) or not isinstance(as_of, (int, float)) or as_of <= 0:
        return "未刷新"
    try:
        return datetime.fromtimestamp(float(as_of)).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError) as e:
        silent_exc(e, "daily_reader._m313_fmt_as_of")
        return "非法时间戳(%s)" % as_of


def append_global_awareness_section(report_path: str, logger: Any = None) -> dict[str, Any]:
    """★第313批 刀1（P0）：日报输出 global_awareness 的**实际值**。

    背景（烛微 313 批前置分析）：308 批把分析器点亮进活体后，日报【其他】里只有一行
    `global_awareness: {as_of, source, learning_goal, …}` —— **只有键名摘要**，
    `status` 与 `gaps` 的实值一个都没打印 ⇒「status=fresh 且 gaps=[]」这条验收
    在产物层面**永远是哑的**（不是没跑，是读不出来）。

    本函数把三件事落到日报（纯只读，零语义变更）：
      * `status=` fresh / stale / not_refreshed / unavailable；
      * `gaps=` 的**实际清单**（`[]` 或具体缺口）＋条数；
      * 三源计数：learning_goal / exploration_clues / narrative_clues 各几条。

    任何异常都只 warning，绝不影响调度主流程。
    """
    _log = logger or _module_logger
    _snap = read_global_awareness(_log)
    _status = str(_snap.get("status") or "unavailable")
    _source = str(_snap.get("source") or "（无）")
    _as_of_txt = _m313_fmt_as_of(_snap.get("as_of"))
    _stale = _snap.get("stale_sec")
    _stale_txt = ("%.1fs" % float(_stale)) if isinstance(_stale, (int, float)) \
        and not isinstance(_stale, bool) else "None"
    _gaps_txt, _gaps_n = _m313_fmt_gaps(_snap.get("gaps"))
    _goal = _snap.get("learning_goal")
    _goal_n = 1 if isinstance(_goal, dict) and _goal else 0
    _clues = _snap.get("exploration_clues")
    _clues_n = len(_clues) if isinstance(_clues, list) else 0
    _narr = _snap.get("narrative_clues")
    _narr_n = len(_narr) if isinstance(_narr, list) else 0
    _goal_txt = "（无）"
    if _goal_n:
        try:
            _goal_txt = json.dumps(_goal, ensure_ascii=False)[:200]
        except (TypeError, ValueError) as e:
            silent_exc(e, "daily_reader.append_global_awareness_section:goal")
            _goal_txt = str(_goal)[:200]
    try:
        _lines = ["", "【全局认知】",
                  "  · status=%s  source=%s" % (_status, _source),
                  "  · as_of=%s  stale_sec=%s" % (_as_of_txt, _stale_txt),
                  "  · gaps=%s（%d 条）" % (_gaps_txt, _gaps_n),
                  "  · 三源计数: learning_goal=%d 条 | exploration_clues=%d 条 | "
                  "narrative_clues=%d 条" % (_goal_n, _clues_n, _narr_n),
                  "  · learning_goal=%s" % _goal_txt,
                  ""]
        with open(report_path, "a", encoding="utf-8") as _f:
            _f.write("\n".join(_lines))
    except Exception as e:
        silent_exc(e, "daily_reader.append_global_awareness_section:写日报小节",
                   level="warning")
    _log.info("[全局认知] status=%s gaps=%s 三源(goal/clues/narrative)=%d/%d/%d source=%s",
              _status, _gaps_txt, _goal_n, _clues_n, _narr_n, _source)
    return {"status": _status, "gaps": _snap.get("gaps") if isinstance(
        _snap.get("gaps"), list) else [], "gaps_count": _gaps_n,
        "learning_goal_count": _goal_n, "exploration_clues_count": _clues_n,
        "narrative_clues_count": _narr_n, "source": _source,
        "as_of": _as_of_txt, "stale_sec": _stale}

