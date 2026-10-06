# -*- coding: utf-8 -*-
"""报告 → ReportEnvelope 适配器（主线第50批 T1，P0-1）

问题（P0-1：自认知闭环断裂）
---------------------------
第47批建好了 ReportBus（统一契约 + 分发层 + 2 个消费者），但**零接入** ——
报告仍然「生成了没人读」。本模块把**现有报告**包装成 ``ReportEnvelope``
并 ``publish``，**不替换**任何既有落盘/输出逻辑（只加一条额外通道）。

接入点（第50批，≥3 处生产调用）
--------------------------------
* ``SelfAwarenessEngine.generate_report`` → ``publish_self_cognition``
* ``SelfAwarenessDailyScheduler.run_once`` → 健康 / 自认知 / 污染 / 补丁质量 / 数据质量
* ``FrameworkDiagnostics.get_health_summary`` → ``publish_health``

设计约束（★硬性）
------------------
1. **绝不抛异常**：报告发布失败不得影响任何既有链路（全部 try/except + 日志）。
2. **开关可控**：``ENABLE_REPORT_BUS`` 关闭时全部函数立即返回 None（零副作用）。
3. **不写业务数据**：只经 ReportBus（其落点 ``data/reports/`` 为审计目录）。
4. **可观测**：返回值携带 ``report_id`` / ``consumers`` / ``persisted``，
   便于「消费率」统计（P0-1 的核心度量）。

核心接口
--------
* :func:`publish_health` —— 健康诊断 → 信封 → 总线（订阅者：健康异常消费者）
* :func:`publish_pollution` —— 污染报告 → 总线（订阅者：污染异常消费者）
* :func:`publish_self_cognition` —— 自认知报告 → 总线
* :func:`bus_enabled` —— 读开关 ``ENABLE_REPORT_BUS``
* :func:`health_anomalies` / :func:`pollution_anomalies` /
  :func:`self_cognition_anomalies` —— 报告 → ``Anomaly`` 列表的纯函数

使用示例
--------
发布一份健康报告（失败不抛异常，返回 ``None``）::

    from nucleus.reporting.publishers import publish_health
    out = publish_health(health_score=55.0, summary="器官可用率下降")
    out["report_id"], out["consumers"], out["persisted"]

开关关闭时（``ENABLE_REPORT_BUS=False``）全部函数**立即返回 None**，
对既有链路零副作用::

    from nucleus.reporting.publishers import bus_enabled
    if bus_enabled():
        publish_pollution(pollution_rate=0.77)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .report_bus import get_report_bus
from .report_envelope import (ACT_ALERT, ACT_CLEAN_DATA, ACT_LOG_ONLY,
                              SEV_P0, SEV_P1, TYPE_EVOLUTION, TYPE_GENERIC,
                              TYPE_HEALTH, TYPE_POLLUTION, TYPE_SELF_COGNITION,
                              Anomaly, make_envelope)
from nucleus._silent_except import silent_exc

_LOG = logging.getLogger("ReportPublishers")
# 生产侧类型白名单拒写（烛微分诊口径）：这些类型为测试夹具/分诊占位，
# 不得进入生产队列（data/reports），避免污染生产。
PRODUCTION_REJECT_TYPES = frozenset({"H_P0", "HEALTH_P0"})

__all__ = [
    "bus_enabled", "publish_health", "publish_pollution",
    "publish_self_cognition", "publish_patch_quality", "publish_data_quality",
    "publish_generic", "health_anomalies", "pollution_anomalies",
    "self_cognition_anomalies",
]


# ---------------------------------------------------------------- 开关 / 工具
def bus_enabled() -> bool:
    """``ENABLE_REPORT_BUS``（默认 True）。关闭 → 全部发布函数 no-op。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_REPORT_BUS", True))
    except Exception as e:
        silent_exc(e, where="nucleus.reporting.publishers::bus_enabled L74")
        return True


def _thr(name: str, default: float) -> float:
    try:
        import config as _c
        return float(getattr(_c, name, default))
    except Exception:
        return default


def _emit(envelope, _tag: str) -> dict[str, Any] | None:
    """统一发布出口：**绝不抛异常**。"""
    try:
        # ★163批 刀2：生产侧类型白名单拒写（烛微分诊口径）。
        #   H_P0/HEALTH_P0 等为测试夹具/分诊占位，不得进入生产队列，
        #   避免污染生产；被拒类型打隔离标注后从待发布异常中剔除。
        _rej = [a for a in (envelope.anomalies or [])
                if getattr(a, "type", None) in PRODUCTION_REJECT_TYPES]
        if _rej:
            for _a in _rej:
                setattr(_a, "_isolated", True)
                setattr(_a, "_cleanup_ticket", _tag)
            envelope.anomalies = [a for a in (envelope.anomalies or [])
                                  if getattr(a, "type", None)
                                  not in PRODUCTION_REJECT_TYPES]
            _LOG.debug("[M163-刀2] 生产侧白名单拒写 %d 条(type=%s)，不落生产队列",
                       len(_rej),
                       sorted({getattr(a, "type", "") for a in _rej}))
        _r = get_report_bus().publish(envelope)
        return {
            "report_id": envelope.report_id,
            "report_type": envelope.report_type,
            "max_severity": envelope.max_severity,
            "persisted": bool(_r.get("persisted")),
            "consumers": list(_r.get("consumers") or []),
            "actions": list(envelope.actions_triggered),
        }
    except Exception as _e:                       # 发布失败不得影响调用方
        try:
            from nucleus.logger import get_module_logger
            get_module_logger("ReportPublishers").debug(
                "[M50-T1] %s 发布失败（已忽略）: %s: %s",
                _tag, type(_e).__name__, _e)
        except Exception as _log_e:
            # ★日志本身也可能失败（如日志系统未初始化）。
            #   不使用裸 ``except: pass``（项目约定）：力求**可观测**。
            print("[ReportPublishers] %s 发布失败（日志不可用）: %s"
                  % (_tag, type(_log_e).__name__))
        return None


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _pick_metric(result, keys, nested=("summary",)):
    """★第55批 T2：先查顶层、再查 ``result['summary']`` 取值。

    背景：``DailyScheduler`` 传的是 ``evaluate_and_report()`` 的返回值
    ``{"summary": {...}, ...}`` —— 真实指标在 ``summary`` 下。原实现只取顶层
    → 恒为 ``None`` → 异常列表为空 → **P0-2（真实修复率 0%）在总线上隐身**。
    """
    if not isinstance(result, dict):
        return None
    for _k in keys:
        _v = result.get(_k)
        if isinstance(_v, (int, float)) and not isinstance(_v, bool):
            return float(_v)
    for _n in nested:
        _sub = result.get(_n)
        if isinstance(_sub, dict):
            for _k in keys:
                _v = _sub.get(_k)
                if isinstance(_v, (int, float)) and not isinstance(_v, bool):
                    return float(_v)
    return None


def _merge_dict(dst: dict, src) -> dict:
    """★仅合并**映射**：非 dict 入参（如 list/str/None）一律忽略。

    ★缺陷记录：原实现直接 ``_content.update(summary or {})``
    —— 若上游传入 list，``dict.update(list)`` 会抛
    ``ValueError: dictionary update sequence element #0 has length 1``，
    违反「发布不得抛异常」硬性约束。
    """
    if isinstance(src, dict):
        dst.update(src)
    return dst


# ---------------------------------------------------------------- 健康
def health_anomalies(diagnosis: dict | None) -> list:
    """从 ``FrameworkDiagnostics`` 诊断结果中抽取可消费异常。"""
    if not isinstance(diagnosis, dict):
        return []
    _out: list = []
    _level = str(diagnosis.get("overall_health") or "unknown")
    _metrics = diagnosis.get("metrics") or {}
    _issues = diagnosis.get("issues") or []
    _warns = diagnosis.get("warnings") or []
    _score = None
    _hs = diagnosis.get("health_score")
    if isinstance(_hs, (int, float)):
        _score = float(_hs)
    else:
        for _k in ("score", "overall_score"):
            _v = _metrics.get(_k)
            if isinstance(_v, (int, float)):
                _score = float(_v)
                break
    if _level in ("critical", "unhealthy", "error"):
        _desc = "框架整体健康=%s（严重问题 %d 项）" % (_level, len(_issues))
        if _issues:
            # ★163批 刀5：落盘行补「问题明细」，使事后可分诊。
            _detail = "; ".join(str(_i) for _i in _issues[:5])
            _desc += "：" + _detail
            if len(_issues) > 5:
                _desc += " 等%d项" % len(_issues)
        _out.append(Anomaly(
            type="HEALTH_CRITICAL", severity=SEV_P0,
            description=_desc,
            source="diagnostics", suggested_action=ACT_ALERT,
            metric_value=_score, target="framework"))
    elif _level in ("warning", "warn", "degraded"):
        _out.append(Anomaly(
            type="HEALTH_WARNING", severity=SEV_P1,
            description="框架整体健康=%s（告警 %d 项）" % (_level, len(_warns)),
            source="diagnostics", suggested_action=ACT_ALERT,
            metric_value=_score, target="framework"))
    _thr_warn = _thr("REPORT_BUS_HEALTH_SCORE_WARN", 60.0)
    if _score is not None and _score < _thr_warn and not any(
            a.type == "HEALTH_CRITICAL" for a in _out):
        _out.append(Anomaly(
            type="HEALTH_SCORE_LOW",
            severity=SEV_P0 if _score < _thr_warn * 0.5 else SEV_P1,
            description="健康分 %.1f 低于阈值 %.1f" % (_score, _thr_warn),
            source="diagnostics", suggested_action=ACT_ALERT,
            metric_value=_score, threshold=_thr_warn, target="framework"))
    if _issues and not any(a.severity == SEV_P0 for a in _out):
        _out.append(Anomaly(
            type="HEALTH_ISSUES_PRESENT", severity=SEV_P1,
            description="存在 %d 项待处理问题" % len(_issues),
            source="diagnostics", suggested_action=ACT_ALERT,
            metric_value=float(len(_issues)), target="framework"))
    return _out


def publish_health(diagnosis: dict | None, generator: str = "diagnostics",
                   extra: dict | None = None) -> dict | None:
    """发布健康报告（订阅者：``health_anomaly_consumer``）。"""
    if not bus_enabled():
        return None
    _anoms = health_anomalies(diagnosis or {})
    _content: dict[str, Any] = {}
    if isinstance(diagnosis, dict):
        _content["overall_health"] = diagnosis.get("overall_health")
        _content["issues"] = len(diagnosis.get("issues") or [])
        _content["warnings"] = len(diagnosis.get("warnings") or [])
        _hs = diagnosis.get("health_score")
        if isinstance(_hs, (int, float)):
            _content["health_score"] = float(_hs)
    _merge_dict(_content, extra)
    _env = make_envelope(TYPE_HEALTH, generator, content=_content,
                         anomalies=_anoms)
    return _emit(_env, "health")


# ---------------------------------------------------------------- 污染
def pollution_anomalies(report: dict | None) -> list:
    """从污染分析结果中抽取异常（阈值默认 0.50，与消费者一致）。"""
    if not isinstance(report, dict):
        return []
    # ★不得与模块级 ``_thr()`` 同名（会遮蔽 → F823/UnboundLocalError）
    _warn = _thr("REPORT_BUS_POLLUTION_WARN", 0.50)
    _rate = None
    for _k in ("pollution_rate", "rate", "ratio"):
        _v = report.get(_k)
        if isinstance(_v, (int, float)):
            _rate = float(_v)
            break
    if _rate is None:
        for _k in ("polluted", "pollution_count"):
            _v = report.get(_k)
            if isinstance(_v, (int, float)):
                _total = report.get("total") or 0
                if _total:
                    _rate = float(_v) / float(_total)
                break
    if _rate is None:
        return []
    if _rate <= _warn:
        return []
    return [Anomaly(
        type="EXPERIENCE_POLLUTION_HIGH",
        severity=SEV_P1 if _rate < 0.9 else SEV_P0,
        description="经验库污染率 %.1f%% 超过阈值 %.1f%%" % (_rate * 100, _warn * 100),
        source="pollution", suggested_action=ACT_CLEAN_DATA,
        metric_value=_rate, threshold=_warn, target="experience_pool")]


def publish_pollution(report: dict | None, generator: str = "experience_retriever",
                      extra: dict | None = None) -> dict | None:
    """发布污染报告（订阅者：``pollution_anomaly_consumer``）。

    ★消费者要求 ``content["pollution_rate"]`` 为数值 —— 此处保证键名稳定。
    """
    if not bus_enabled():
        return None
    _anoms = pollution_anomalies(report or {})
    _content: dict[str, Any] = {}
    if isinstance(report, dict):
        for _k in ("total", "polluted", "clean", "pollution_rate",
                   "summarized", "raw_summary_count", "marked_not_cleaned"):
            if _k in report:
                _content[_k] = report[_k]
    _merge_dict(_content, extra)
    if _content.get("pollution_rate") is None and _anoms:
        _content["pollution_rate"] = _anoms[0].metric_value
    _env = make_envelope(TYPE_POLLUTION, generator, content=_content,
                         anomalies=_anoms)
    return _emit(_env, "pollution")


# ---------------------------------------------------------------- 自认知
def self_cognition_anomalies(summary: dict | None) -> list:
    """从自我认知画像摘要中抽取异常。"""
    if not isinstance(summary, dict):
        return []
    _out: list = []
    _level = str(summary.get("health_level") or "")
    _score = _num(summary.get("overall_score"))
    _head = str(summary.get("headline_issue") or "")
    _thr_warn = _thr("REPORT_BUS_HEALTH_SCORE_WARN", 60.0)
    if _level in ("critical", "严重", "危急"):
        _out.append(Anomaly(
            type="SELF_COGNITION_CRITICAL", severity=SEV_P0,
            description="自我认知健康等级=%s" % _level,
            source="self_awareness", suggested_action=ACT_ALERT,
            metric_value=_score, target="self_cognition"))
    if _score is not None and _score < _thr_warn:
        _out.append(Anomaly(
            type="SELF_COGNITION_SCORE_LOW",
            severity=SEV_P0 if _score < _thr_warn * 0.5 else SEV_P1,
            description="自我认知综合评分 %.1f 低于阈值 %.1f" % (_score, _thr_warn),
            source="self_awareness", suggested_action=ACT_ALERT,
            metric_value=_score, threshold=_thr_warn, target="self_cognition"))
    if _head and not _out:
        _out.append(Anomaly(
            type="SELF_COGNITION_HEADLINE_ISSUE", severity=SEV_P1,
            description="首要问题: %s" % _head[:120],
            source="self_awareness", suggested_action=ACT_LOG_ONLY,
            target="self_cognition"))
    return _out


def publish_self_cognition(text: str, summary: dict | None = None,
                           generator: str = "SelfAwarenessEngine",
                           extra: dict | None = None) -> dict | None:
    """发布自认知报告 —— **P0-1 的头号修复点**（此前「生成了没人读」）。"""
    if not bus_enabled():
        return None
    _content: dict[str, Any] = {
        "text_length": len(text or ""),
        "text_head": (text or "")[:400],
    }
    _merge_dict(_content, summary)
    _merge_dict(_content, extra)
    _env = make_envelope(TYPE_SELF_COGNITION, generator, content=_content,
                         anomalies=self_cognition_anomalies(summary))
    return _emit(_env, "self_cognition")


# ---------------------------------------------------------------- 其他报告
def publish_patch_quality(result: dict | None, generator: str = "patch_quality_evaluator",
                          extra: dict | None = None) -> dict | None:
    """发布补丁质量报告（真实修复率 0% 等结论由此进入闭环）。"""
    if not bus_enabled():
        return None
    _anoms: list = []
    # ★第55批 T2：兼容 ``{"summary": {...}}`` 结构（DailyScheduler 传入）
    _rate = _pick_metric(result, ("real_fix_rate", "fix_rate", "effectiveness"))
    _warn = _thr("REPORT_BUS_PATCH_FIX_RATE_WARN", 0.10)
    if _rate is not None and _rate < _warn:
        _anoms.append(Anomaly(
            type="PATCH_REAL_FIX_RATE_LOW",
            severity=SEV_P1 if _rate > 0 else SEV_P0,
            description="补丁真实修复率 %.1f%% 低于阈值 %.1f%%" % (_rate * 100, _warn * 100),
            source="evolution", suggested_action=ACT_LOG_ONLY,
            metric_value=_rate, threshold=_warn, target="patch_history"))
    _content: dict[str, Any] = {}
    if isinstance(result, dict):
        for _k in ("summary", "real_fix_rate", "no_regression_rate",
                   "problem_fixed_rate", "verifiable_rate"):
            if _k in result:
                _content[_k] = result[_k]
        # ★第55批 T2：把 summary 里的指标**归一到 content 顶层** ——
        #   消费者只读 content 顶层，若不归一则 real_fix_rate 等指标
        #   对消费者不可见（evolution_anomaly_consumer 会拿不到值）。
        _s = result.get("summary")
        if isinstance(_s, dict):
            for _k in ("real_fix_rate", "no_regression_rate",
                       "problem_fixed_rate", "verifiable_rate",
                       "claimed_effectiveness_avg", "avg_score",
                       "applied", "verified", "unverifiable",
                       "fake_pass", "good", "mediocre", "bad"):
                if _k in _s:
                    _content.setdefault(_k, _s[_k])
            _content.setdefault("summary", _s)
    _merge_dict(_content, extra)
    return _emit(make_envelope(TYPE_EVOLUTION, generator, content=_content,
                               anomalies=_anoms), "patch_quality")


def publish_patch_verification_failed(summary: str,
                                      generator: str = "PatchManager._run_regression_tests",
                                      severity: str = SEV_P0,
                                      extra: dict | None = None) -> dict | None:
    """★B156-4（补丁验证空转·断链点②出口）：补丁验证脚本失败（缺失/崩溃/无关回归）
    时，发出 ``PATCH_REAL_FIX_RATE_LOW`` 异常。

    该异常类型在 ``consumers.evolution_anomaly_consumer`` 中被显式识别为
    「补丁验证仍处空转（P0-2）」，会写 ``data/reports/alerts.jsonl``（``needs_human=True``）。
    原实现在回归失败分支只记 warning、不改判 ``result['passed']``
    （即"0通过/1失败 仅记录不影响判定"），导致验证工具损坏/缺失也被静默放行 →
    补丁在**未经真实验证**下被批准，与 ``_clean_llm_code`` 38 次语法错、
    ``avg_fix_rate=0.0039`` 共同构成"进化空转"。
    此出口让验证失败从静默日志变为可观测告警（人工可介入）。
    """
    if not bus_enabled():
        return None
    _anom = Anomaly(
        type="PATCH_REAL_FIX_RATE_LOW",
        severity=severity if severity in (SEV_P0, SEV_P1) else SEV_P0,
        description="补丁验证脚本失败，验证链路空转: %s" % (summary or "")[:200],
        source="evolution", suggested_action=ACT_LOG_ONLY,
        metric_value=None, threshold=None, target="patch_history")
    _content: dict[str, Any] = {"verification": "regression_failed", "detail": summary}
    if isinstance(extra, dict):
        _content.update(extra)
    return _emit(make_envelope(TYPE_EVOLUTION, generator, content=_content,
                               anomalies=[_anom]), "patch_verification_failed")


def publish_data_quality(result: dict | None, generator: str = "data_quality_evaluator",
                         extra: dict | None = None) -> dict | None:
    """发布 LLM 留存数据质量报告。"""
    if not bus_enabled():
        return None
    _anoms: list = []
    _score = None
    if isinstance(result, dict):
        for _k in ("score", "quality_score"):
            _v = result.get(_k)
            if isinstance(_v, (int, float)):
                _score = float(_v)
                break
    _warn = _thr("REPORT_BUS_DATA_QUALITY_WARN", 0.6)
    if _score is not None and _score < _warn:
        _anoms.append(Anomaly(
            type="LLM_DATA_QUALITY_LOW", severity=SEV_P1,
            description="LLM 留存数据质量 %.2f 低于阈值 %.2f" % (_score, _warn),
            source="data_quality", suggested_action=ACT_LOG_ONLY,
            metric_value=_score, threshold=_warn, target="llm_traces"))
    _content: dict[str, Any] = {}
    if isinstance(result, dict):
        for _k in ("score", "scored_records", "purity", "diversity"):
            if _k in result:
                _content[_k] = result[_k]
    _merge_dict(_content, extra)
    return _emit(make_envelope(TYPE_EVOLUTION, generator, content=_content,
                               anomalies=_anoms), "data_quality")


def publish_generic(report_type: str, generator: str, content: dict | None = None,
                    anomalies: list | None = None) -> dict | None:
    """通用发布口（供后续批次接入任意报告）。"""
    if not bus_enabled():
        return None
    return _emit(make_envelope(report_type or TYPE_GENERIC, generator,
                               content=content or {},
                               anomalies=anomalies or []), "generic")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
