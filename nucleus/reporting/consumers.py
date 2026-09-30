# -*- coding: utf-8 -*-
"""内置报告消费者（主线第47批 T3，P0-1）

★**只做"动作建议"，不自动执行不可逆动作** —— 符合框架「不可逆清单」原则。

两个内置消费者：
  * ``health_anomaly_consumer``    —— 订阅 ``health``，P0 异常 → 告警
  * ``pollution_anomaly_consumer`` —— 订阅 ``pollution``，污染率超阈 → 清洗建议

消费者契约：
  输入 ``ReportEnvelope``，返回 **真值** 表示"已认领并完成动作"，
  返回假值表示"未触发动作"。抛异常不影响其他消费者（由 ReportBus 捕获）。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .report_envelope import ConsumeResult, SEV_P0, SEV_P1, ReportEnvelope
from nucleus._silent_except import silent_exc

#: 告警落盘位置（相对于项目根）
ALERT_FILE = os.path.join("data", "reports", "alerts.jsonl")
#: 待办落盘位置
TODO_FILE = os.path.join("data", "reports", "todo.jsonl")

#: 污染率告警阈值（第46批实测基线 77.7%）
POLLUTION_THRESHOLD = 0.50

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _append_jsonl(rel_path: str, record: dict[str, Any]) -> bool:
    """追加一行 JSONL（受 write_guard 约束；失败返回 False，不抛异常）。"""
    _p = os.path.join(_PROJECT_ROOT, rel_path)
    try:
        try:
            from nucleus.data.write_guard import guard_write as _gw
            # ★第50批（P2-339）：参数名修正为 ``explicit``。
            #   消费者的落点是固定的项目 ``data/reports/``（非注入）
            #   → 应受写盘守卫约束（pytest 下不写生产，生产进程不受影响）。
            if not _gw(os.path.abspath(_p), explicit=False,
                       component="ReportConsumers"):
                return False
        except Exception as _gw_e:
            _log("[ReportConsumers] 写盘守卫不可用（按允许处理）: %s"
                 % type(_gw_e).__name__)
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except (OSError, IOError, TypeError, ValueError) as e:
        silent_exc(e, where="nucleus.reporting.consumers::_append_jsonl L55")
        return False


def _log(msg: str, level: str = "warning") -> None:
    try:
        from nucleus.logger import get_module_logger
        _lg = get_module_logger("ReportConsumers")
        getattr(_lg, level, _lg.warning)(msg)
    except Exception:
        print("[ReportConsumers] %s" % msg)


# ==================== 健康异常消费者 ====================

def health_anomaly_consumer(envelope: ReportEnvelope) -> bool:
    """订阅 ``health`` 类型：发现 P0 异常时告警并**标记需人工介入**。

    动作（均为可逆/非破坏性）：
      1. 写告警日志
      2. 追加一条告警记录到 ``data/reports/alerts.jsonl``
      3. 在信封上登记 ``actions_triggered``

    ★不自动生成补丁、不自动改代码 —— 不可逆动作交人工决策。
    """
    if envelope.report_type not in ("health", "*"):
        return ConsumeResult(consumer="health_anomaly_consumer",
                             report_id=envelope.report_id, accepted=False)
    _p0 = [a for a in envelope.anomalies if a.severity == SEV_P0]
    if not _p0 and not envelope.has_p0():
        return ConsumeResult(consumer="health_anomaly_consumer",
                             report_id=envelope.report_id, accepted=False)

    for _a in _p0 or envelope.anomalies:
        _msg = ("[自认知·P0告警] %s | %s | 建议动作=%s | target=%s"
                % (_a.type, _a.description, _a.suggested_action, _a.target))
        _log(_msg, "error")
        _append_jsonl(ALERT_FILE, {
            "ts": time.time(),
            "ts_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "report_id": envelope.report_id,
            "report_type": envelope.report_type,
            "anomaly_type": _a.type,
            "severity": _a.severity,
            "description": _a.description,
            "suggested_action": _a.suggested_action,
            "target": _a.target,
            "needs_human": True,
        })
        envelope.actions_triggered.append("alert:%s" % _a.type)
    return ConsumeResult(consumer="health_anomaly_consumer",
                         report_id=envelope.report_id, accepted=True,
                         action_taken=True, action_ref=ALERT_FILE,
                         note="P0 告警已写入 %s" % ALERT_FILE)


# ==================== 污染异常消费者 ====================

def pollution_anomaly_consumer(envelope: ReportEnvelope) -> bool:
    """订阅 ``pollution`` 类型：污染率超阈时写入**清洗建议待办**。

    ★不自动清洗（清洗需停机窗口，且不可逆）→ 只写待办交人工/后续批次。
    """
    if envelope.report_type not in ("pollution", "*"):
        return ConsumeResult(consumer="pollution_anomaly_consumer",
                             report_id=envelope.report_id, accepted=False)

    _rate = None
    _c = envelope.content or {}
    for _k in ("pollution_rate", "rate", "ratio"):
        if isinstance(_c.get(_k), (int, float)):
            _rate = float(_c[_k])
            break
    if _rate is None:
        for _a in envelope.anomalies:
            if _a.metric_value is not None:
                _rate = float(_a.metric_value)
                break
    if _rate is None:
        return ConsumeResult(consumer="pollution_anomaly_consumer",
                             report_id=envelope.report_id, accepted=False)
    if _rate <= POLLUTION_THRESHOLD:
        return ConsumeResult(consumer="pollution_anomaly_consumer",
                             report_id=envelope.report_id, accepted=False)

    _msg = ("[自认知·污染告警] 污染率 %.1f%% 超过阈值 %.0f%% → "
            "建议安排停机窗口执行 SERP 清洗（本消费者不会自动清洗）"
            % (_rate * 100, POLLUTION_THRESHOLD * 100))
    _log(_msg)
    _append_jsonl(TODO_FILE, {
        "ts": time.time(),
        "ts_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "report_id": envelope.report_id,
        "kind": "serp_clean_suggestion",
        "pollution_rate": _rate,
        "threshold": POLLUTION_THRESHOLD,
        "needs_downtime": True,
        "auto_executed": False,
    })
    envelope.actions_triggered.append("clean_suggestion")
    return ConsumeResult(consumer="pollution_anomaly_consumer",
                         report_id=envelope.report_id, accepted=True,
                         action_taken=True, action_ref=TODO_FILE,
                         note="污染清洗建议已写入 %s" % TODO_FILE)


# ==================== 自认知消费者（★第55批 T2） ====================

#: 自认知综合评分告警阈值（与 publishers.SELF_COGNITION 阈值口径一致）
SELF_COGNITION_SCORE_WARN = 60.0
#: 补丁真实修复率告警阈值（0% 即 P0-2 补丁验证空转）
PATCH_FIX_RATE_WARN = 0.10


def _cfg_f(name: str, default: float) -> float:
    """读配置阈值（读不到用默认值；不抛异常）。"""
    try:
        import config as _c
        return float(getattr(_c, name, default))
    except Exception:
        return default


def self_cognition_consumer(envelope: ReportEnvelope) -> bool:
    """订阅 ``self_cognition``：有首要问题 / 评分偏低 / P0 异常 → 写入工跟进待办。

    ★此前该类型**已发布但无任何订阅者** → ``consumed_by`` 恒空 → 消费率 0%
      （第55批实测：5 份生产报告中 4 份无人认领）。
    ★只写待办 + 日志，不自动执行任何修复动作。
    """
    if envelope.report_type not in ("self_cognition", "*"):
        return ConsumeResult(consumer="self_cognition_consumer",
                             report_id=envelope.report_id, accepted=False)
    _c = envelope.content or {}
    if not isinstance(_c, dict):
        return ConsumeResult(consumer="self_cognition_consumer",
                             report_id=envelope.report_id, accepted=False)
    _head = str(_c.get("headline_issue") or "").strip()
    _score = None
    for _k in ("overall_score", "score"):
        _v = _c.get(_k)
        if isinstance(_v, (int, float)) and not isinstance(_v, bool):
            _score = float(_v)
            break
    _p0 = [a for a in envelope.anomalies if a.severity == SEV_P0]
    _thr = _cfg_f("REPORT_BUS_HEALTH_SCORE_WARN", SELF_COGNITION_SCORE_WARN)
    _need = bool(_p0) or bool(_head) or (
        _score is not None and _score < _thr)
    if not _need:
        return ConsumeResult(consumer="self_cognition_consumer",
                             report_id=envelope.report_id, accepted=False)
    _log("[自认知·待办] 报告 %s 需人工跟进：首要问题=%s 评分=%s"
         % (envelope.report_id, _head[:60] or "(无)", _score))
    _append_jsonl(TODO_FILE, {
        "ts": time.time(),
        "ts_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "report_id": envelope.report_id,
        "kind": "self_cognition_followup",
        "headline_issue": _head[:200],
        "overall_score": _score,
        "worst_dimension": _c.get("worst_dimension"),
        "severity": envelope.max_severity,
        "auto_executed": False,
    })
    envelope.actions_triggered.append("self_cognition_followup")
    return ConsumeResult(consumer="self_cognition_consumer",
                         report_id=envelope.report_id, accepted=True,
                         action_taken=True, action_ref=TODO_FILE,
                         note="自认知待办已写入 %s" % TODO_FILE)


def evolution_anomaly_consumer(envelope: ReportEnvelope) -> bool:
    """订阅 ``evolution``：补丁真实修复率异常 → 告警（★P0-2 空转的可见化）。

    ★配合第55批 T2 的 ``_pick_metric`` 修复：此前 ``real_fix_rate``
      取不到 → 无异常 → 该 P0 问题在总线上隐身；修复后此处可稳定拿到值。
    """
    if envelope.report_type not in ("evolution", "*"):
        return ConsumeResult(consumer="evolution_anomaly_consumer",
                             report_id=envelope.report_id, accepted=False)
    _c = envelope.content or {}
    if not isinstance(_c, dict):
        return ConsumeResult(consumer="evolution_anomaly_consumer",
                             report_id=envelope.report_id, accepted=False)
    _rate = None
    for _k in ("real_fix_rate", "fix_rate", "effectiveness"):
        _v = _c.get(_k)
        if isinstance(_v, (int, float)) and not isinstance(_v, bool):
            _rate = float(_v)
            break
    _hits = [a for a in envelope.anomalies if a.severity in (SEV_P0, SEV_P1)]
    if _rate is None and not _hits:
        return ConsumeResult(consumer="evolution_anomaly_consumer",
                             report_id=envelope.report_id, accepted=False)
    _thr = _cfg_f("REPORT_BUS_PATCH_FIX_RATE_WARN", PATCH_FIX_RATE_WARN)
    if _rate is not None and _rate > _thr:
        return ConsumeResult(consumer="evolution_anomaly_consumer",
                             report_id=envelope.report_id, accepted=False)
    _msg = ("[自认知·补丁告警] 真实修复率 %s 低于阈值 %.0f%% → "
            "补丁验证仍处空转（P0-2），本消费者只告警不自动修复"
            % ("未知" if _rate is None else "%.1f%%" % (_rate * 100),
               _thr * 100))
    _log(_msg, "error")
    _append_jsonl(ALERT_FILE, {
        "ts": time.time(),
        "ts_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "report_id": envelope.report_id,
        "report_type": envelope.report_type,
        "anomaly_type": _hits[0].type if _hits else "PATCH_REAL_FIX_RATE_LOW",
        "severity": _hits[0].severity if _hits else SEV_P0,
        "description": _msg,
        "real_fix_rate": _rate,
        "threshold": _thr,
        "needs_human": True,
    })
    envelope.actions_triggered.append("patch_quality_alert")
    return ConsumeResult(consumer="evolution_anomaly_consumer",
                         report_id=envelope.report_id, accepted=True,
                         action_taken=True, action_ref=ALERT_FILE,
                         note="补丁质量告警已写入 %s" % ALERT_FILE)


# ==================== 注册助手 ====================

def register_builtin_consumers(bus) -> list[str]:
    """把内置消费者注册到指定 ReportBus，返回订阅的类型列表。

    ★第55批 T2：新增 ``self_cognition`` / ``evolution`` 两个消费者 ——
      此前这两类报告**已发布但无订阅者**（消费率仅 20%）。
      受灰度 ``ENABLE_REPORT_EXT_CONSUMERS`` 控制（关闭 → 只注册原有两个）。
    """
    bus.subscribe("health", health_anomaly_consumer)
    bus.subscribe("pollution", pollution_anomaly_consumer)
    _types = ["health", "pollution"]
    try:
        import config as _c
        _ext = bool(getattr(_c, "ENABLE_REPORT_EXT_CONSUMERS", True))
    except Exception:
        _ext = True
    if _ext:
        bus.subscribe("self_cognition", self_cognition_consumer)
        bus.subscribe("evolution", evolution_anomaly_consumer)
        _types += ["self_cognition", "evolution"]
    return _types
