# -*- coding: utf-8 -*-
"""ReportBus —— 自认知报告统一分发层（主线第47批 T3，P0-1）

职责
----
把"报告生成"与"报告消费"解耦：

* ``publish()``   —— 发布（**自动落盘** ``data/reports/{type}/{id}.json``）
* ``subscribe()`` —— 消费者订阅某类型报告
* ``get_unconsumed()`` / ``mark_consumed()`` —— 消费状态跟踪
* ``get_stats()`` —— 分发统计（总数/已消费/未消费/按类型分布/消费率）

设计约束
--------
* **新增通道、不替换旧通道**：旧的"直接落盘"方式仍然可用，零回归。
* **落盘受写盘守卫约束**（第44批 ``write_guard``）：测试环境不写生产 ``data/``。
* 保留最近 ``MAX_REPORTS``（100）份，自动清理旧报告。
* 线程安全（``threading.RLock``）。
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import re
from typing import Any, Callable

from .report_envelope import (ConsumeResult, SEV_P0, SEV_P1,  # noqa: F401
                              ReportEnvelope, make_envelope)

MAX_REPORTS = 100
# ★主线第51批 T5（P2-357）：**磁盘**保留份数（原 MAX_REPORTS 仅约束内存）。
MAX_REPORTS_ON_DISK = 100
#: 重要报告类型（保留阈值更高，防误删关键证据）
IMPORTANT_REPORT_TYPES = ("health", "pollution")
_ON_DISK_IMPORTANT_MULTIPLIER = 2
DEFAULT_ROOT = os.path.join("data", "reports")

#: 异常码含 LOW -> 低即差（best-effort 方向推断，见 _is_breaching）
_LOW_HINT_RE = re.compile(r"LOW", re.I)

#: 项目根（由本文件位置反推：nucleus/reporting/report_bus.py → 上三级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _is_explicit_base_dir(base_dir: str) -> bool:
    """★第50批：调用方是否**显式注入**了落盘目录。

    显式注入（如测试传沙箱目录）→ 视为隔离 → 放行；
    默认目录（项目 ``data/reports/``）→ 未注入 → 受写盘守卫约束。
    """
    try:
        return os.path.abspath(str(base_dir)) != os.path.abspath(
            os.path.join(_PROJECT_ROOT, DEFAULT_ROOT))
    except Exception:
        return True          # 无法判定→按已注入处理（与改造前行为一致）


# ==================== 优先级路由（★第111批 T-111c） ====================

def _as_consume_result(res: Any, name: str,
                       envelope: "ReportEnvelope") -> "ConsumeResult":
    """把消费者返回值统一规约为 ``ConsumeResult``（兼容旧 ``bool`` 返回）。"""
    if isinstance(res, ConsumeResult):
        if not res.consumer:
            res.consumer = name
        if not res.report_id:
            res.report_id = envelope.report_id
        return res
    return ConsumeResult(consumer=name, report_id=envelope.report_id,
                         accepted=bool(res))


class PriorityRouter:
    """★第111批 T-111c：按报告严重度排定消费者调用顺序。

    设计约束（防新增死键）：
      * **只排序、不增删**被调用的消费者集合 → 完全行为守恒；
      * 排序决策写入 ``envelope.routing_order``，并由 ``get_stats`` 暴露
        （``envelopes_routed``）→ 该能力有真实读取点，非死键。
    排序规则（稳定排序）：
      1. 专属订阅（订阅键 == 报告类型）优先于通配订阅（``"*"``）；
      2. 严重度高（P0/P1）时，名称含告警/alert/p0/health 语义的消费者再前置
         —— 高优告警先落地。
    """

    @staticmethod
    def _name_priority(envelope: "ReportEnvelope", fn: Any) -> int:
        _n = getattr(fn, "__name__", "").lower()
        if envelope.max_severity in (SEV_P0, SEV_P1):
            if any(_k in _n for _k in ("alert", "p0", "health")):
                return 0
        return 1

    @staticmethod
    def order(envelope: "ReportEnvelope",
              subscribers: list[tuple[Any, bool]
              ]) -> list[Any]:
        """返回排好序的消费者函数列表。

        Args:
            subscribers: ``(fn, is_wildcard)`` 元组列表（由 ``_dispatch`` 构造）。
        """
        _ranked = sorted(
            subscribers,
            key=lambda st: (1 if st[1] else 0,
                            PriorityRouter._name_priority(envelope, st[0])))
        return [st[0] for st in _ranked]


class ReportBus:
    """报告分发总线。"""

    def __init__(self, base_dir: str = DEFAULT_ROOT,
                 max_reports: int = MAX_REPORTS,
                 persist: bool = True,
                 max_reports_on_disk: int | None = None) -> None:
        self._base_dir = base_dir
        # ★第51批 T5（P2-357）：磁盘回收阈值（None → 读 config，再兜默认）
        if max_reports_on_disk is None:
            try:
                import config as _cfg
                max_reports_on_disk = getattr(
                    _cfg, "MAX_REPORTS_ON_DISK", MAX_REPORTS_ON_DISK)
            except Exception:
                max_reports_on_disk = MAX_REPORTS_ON_DISK
        self._max_on_disk = int(max_reports_on_disk or MAX_REPORTS_ON_DISK)
        self._disk_prune_total = 0
        # ★第52批 T1（P2-366）：历史报告（消费状态不可恢复）计数
        self._legacy_without_consumed = 0
        # ★第50批（P2-339）：显式注入即隔离 → 传给写盘守卫。
        #   原实现传 `explicit_base_dir=True`（**无此参数**）→ TypeError
        #   被紧跟的裸 ``except: pass`` 吞掉 → **守卫从未生效**（假防护）。
        self._explicit_base_dir = _is_explicit_base_dir(base_dir)
        self._max_reports = int(max_reports or MAX_REPORTS)
        self._persist = bool(persist)
        self._lock = threading.RLock()
        self._envelopes: dict[str, ReportEnvelope] = {}
        self._subscribers: dict[str, list[Callable[[ReportEnvelope], Any]]] = {}
        self._publish_count = 0
        self._last_error: str = ""
        # ★第111批 T-111a：消费者存在性机检
        self._last_consumed_at: dict[str, float] = {}  # 消费者名 -> 最后被 dispatch 调用时间
        self._published_types: set[str] = set()       # 总线实际产出过的报告类型
        # 陈旧阈值(秒)：订阅类型已产出但消费者超此时间未被调用 -> WARNING
        try:
            import config as _cfg
            self._consumer_staleness_threshold = float(getattr(
                _cfg, "REPORT_CONSUMER_STALENESS_SECONDS", 3600))
        except Exception:
            self._consumer_staleness_threshold = 3600.0
        self._last_staleness_warn_at: float = 0.0
        # ★第111批 T-111b：闭环解决率（问题解决率）best-effort 跟踪
        self._issues: dict[str, dict] = {}            # key=类型::异常码 -> 问题状态
        self._resolution_events: int = 0              # 状态翻转(问题->已解决)事件计数
        # 近窗(秒)：仅统计该窗口内登记的问题；默认 30 天
        try:
            import config as _cfg
            self._resolution_window = float(getattr(
                _cfg, "REPORT_RESOLUTION_WINDOW_SECONDS", 2592000))
        except Exception:
            self._resolution_window = 2592000.0

    # ---------- 落盘 ----------

    def _report_path(self, env: ReportEnvelope) -> str:
        return os.path.join(self._base_dir, str(env.report_type),
                            "%s.json" % env.report_id)

    def _write(self, env: ReportEnvelope) -> bool:
        """落盘一份报告（受 write_guard 约束）。"""
        if not self._persist:
            return False
        _p = self._report_path(env)
        try:
            # ★与第44批 write_guard 协同：测试环境不得写生产 data/。
            # ★第50批（P2-339）：参数名修正为 ``explicit``（原写
            #   ``explicit_base_dir`` → TypeError 被吞 → 守卫从未生效）。
            try:
                from nucleus.data.write_guard import guard_write as _gw
                if not _gw(os.path.abspath(_p),
                           explicit=self._explicit_base_dir,
                           component="ReportBus"):
                    self._last_error = "write_guard 拒绝写入: %s" % _p
                    return False
            except Exception as _gw_e:
                # 守卫不可用时按原行为继续（不使用裸 pass）
                self._last_error = "write_guard 不可用: %s" % type(_gw_e).__name__
            os.makedirs(os.path.dirname(_p), exist_ok=True)
            with io.open(_p, "w", encoding="utf-8") as _f:
                _f.write(json.dumps(env.to_dict(), ensure_ascii=False, indent=2))
            return True
        except (OSError, IOError, TypeError, ValueError) as _e:
            self._last_error = "%s: %s" % (type(_e).__name__, _e)
            return False

    # ---------- 发布 / 订阅 ----------

    def publish(self, envelope: ReportEnvelope,
                dispatch: bool = True) -> dict[str, Any]:
        """发布报告：登记 + 落盘 +（可选）分发给订阅者。

        Returns:
            ``{report_id, persisted, dispatched, consumers, errors}``
        """
        with self._lock:
            self._envelopes[envelope.report_id] = envelope
            self._published_types.add(envelope.report_type)  # ★T-111a
            self._publish_count += 1

            _consumers: list[str] = []
            _errors: list[str] = []

            # ★第52批 T1（P2-366）：**先 dispatch 再落盘**。
            #   原顺序为 ``_write()`` → dispatch，导致 ``consumed_by`` 在落盘时
            #   仍为空（第51批实测：磁盘 137 份报告 consumed_by 全部为空）
            #   → 重启后 ``load_from_disk()`` 恢复的报告全显示"未消费"
            #   → 消费率统计失真。
            #   ★行为等价性：``_persisted`` 只进返回值，不影响是否 dispatch
            #   → 顺序调整不改变对外行为，仅让"落盘内容"带上消费标记。
            _dsp_first = self._dispatch_before_write()
            if dispatch and _dsp_first:
                self._dispatch(envelope, _consumers, _errors)

            _persisted = self._write(envelope)
            self._trim()
            # ★第51批 T5（P2-357）：内存回收后同步做磁盘回收
            self._prune_disk()
            # ★第111批 T-111b：闭环解决率跟踪（在锁内，读 self._issues）
            self._track_resolution(envelope)

            # 灰度：开关关闭 → 退回旧顺序（零回归）
            if dispatch and not _dsp_first:
                self._dispatch(envelope, _consumers, _errors)

            self._maybe_warn_staleness()  # ★T-111a 节流式消费者陈旧 WARNING
            return {"report_id": envelope.report_id,
                    "persisted": _persisted,
                    "dispatched": bool(dispatch),
                    "consumers": _consumers,
                    "errors": _errors}

    def _dispatch_before_write(self) -> bool:
        """▲第52批 T1（P2-366）：dispatch 是否在落盘**之前**执行。

        * ``True``（默认）：先 dispatch（填充 ``consumed_by``）再落盘
          → **落盘报告带消费标记**（修复 P2-366）；
        * ``False``：退回第51批顺序（先落盘再 dispatch）→ 零回归。

        开关：``config.ENABLE_REPORT_DISPATCH_BEFORE_WRITE``。
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_REPORT_DISPATCH_BEFORE_WRITE",
                                True))
        except Exception:
            return True

    def _dispatch(self, envelope: ReportEnvelope,
                  consumers: list[str], errors: list[str]) -> None:
        """把报告分发给订阅者；返回值非空的订阅者登记进 ``envelope.consumed_by``。

        Args:
            envelope: 待分发报告。
            consumers: **出参** —— 追加"已认领"的消费者名（供 publish 返回值）。
            errors: **出参** —— 追加单个消费者失败信息（不影响其他消费者）。

        ★单个消费者异常被隔离，不中断其余消费者；**不得静默**
          （异常信息进 ``errors`` 并由 publish 返回）。
        """
        _type_subs = [(f, False) for f in
                      self._subscribers.get(envelope.report_type, [])]
        _wild_subs = [(f, True) for f in
                      self._subscribers.get("*", [])]
        # ★第111批 T-111c：PriorityRouter 按严重度排定调用顺序（仅排序，不增删）
        _ordered = PriorityRouter.order(envelope, _type_subs + _wild_subs)
        envelope.routing_order = [getattr(_f, "__name__", str(_f))
                                  for _f in _ordered]
        for _fn in _ordered:
            _name = getattr(_fn, "__name__", str(_fn))
            try:
                # ★第111批 T-111a：记录消费者被 dispatch 调用的踪迹
                #   （证明"总线真的把报告送到了消费者"，是消费存在性机检基础）。
                self._last_consumed_at[_name] = time.time()
                _res = _fn(envelope)
                # ★第111批 T-111c：统一收纳结构化消费结果（兼容旧 bool）
                _cr = _as_consume_result(_res, _name, envelope)
                envelope.record_consume_result(_cr)
                if _cr.accepted:
                    consumers.append(_name)
                    # ★消费者返回真值 = 已认领 → 自动登记 consumed_by，
                    #   否则 get_stats().consumption_rate 恒为 0
                    #   （那正是 P0-1 要解决的问题本身）。
                    if _name not in envelope.consumed_by:
                        envelope.consumed_by.append(_name)
            except Exception as _e:      # 单个消费者失败不影响其他
                errors.append("%s: %s" % (_name, _e))
                envelope.record_consume_result(
                    ConsumeResult(consumer=_name, report_id=envelope.report_id,
                                  accepted=False,
                                  note="dispatch exception: %s" % _e))

    def subscribe(self, report_type: str,
                  consumer: Callable[[ReportEnvelope], Any]) -> None:
        """订阅某类型报告（``"*"`` 表示订阅全部）。"""
        with self._lock:
            self._subscribers.setdefault(report_type, []).append(consumer)

    def unsubscribe(self, report_type: str,
                    consumer: Callable[[ReportEnvelope], Any]) -> bool:
        with self._lock:
            _lst = self._subscribers.get(report_type, [])
            if consumer in _lst:
                _lst.remove(consumer)
                return True
        return False

    # ---------- 消费跟踪 ----------

    def get_unconsumed(self, report_type: str | None = None
                       ) -> list[ReportEnvelope]:
        """尚未被任何消费者认领的报告。"""
        with self._lock:
            return [e for e in self._envelopes.values()
                    if not e.consumed_by
                    and (report_type is None or e.report_type == report_type)]

    def mark_consumed(self, report_id: str, consumer: str) -> bool:
        with self._lock:
            _e = self._envelopes.get(report_id)
            if _e is None:
                return False
            if consumer not in _e.consumed_by:
                _e.consumed_by.append(consumer)
            return True

    def get(self, report_id: str) -> ReportEnvelope | None:
        with self._lock:
            return self._envelopes.get(report_id)

    def all(self) -> list[ReportEnvelope]:
        with self._lock:
            return list(self._envelopes.values())

    # ---------- 统计 ----------

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            _all = list(self._envelopes.values())
            _n = len(_all)
            _consumed = [e for e in _all if e.consumed_by]
            _by_type: dict[str, int] = {}
            _by_sev: dict[str, int] = {}
            for _e in _all:
                _by_type[_e.report_type] = _by_type.get(_e.report_type, 0) + 1
                _s = _e.max_severity
                _by_sev[_s] = _by_sev.get(_s, 0) + 1
            _anom = sum(len(e.anomalies) for e in _all)
            _acted = sum(len(e.actions_triggered) for e in _all)
            # ★第111批 T-111c：消费结果与路由决策的读取点（防死键）
            _cr_total = 0
            _cr_accepted = 0
            _cr_action = 0
            _routed = 0
            for _e in _all:
                _cr_total += len(_e.consume_results)
                for _r in _e.consume_results:
                    if _r.accepted:
                        _cr_accepted += 1
                    if _r.action_taken:
                        _cr_action += 1
                if _e.routing_order:
                    _routed += 1
            return {
                "total": _n,
                "published_total": self._publish_count,
                "consumed": len(_consumed),
                "unconsumed": _n - len(_consumed),
                "consumption_rate": round(len(_consumed) / _n, 4) if _n else 0.0,
                "by_type": _by_type,
                "by_severity": _by_sev,
                "anomaly_count": _anom,
                "actions_triggered": _acted,
                "subscribers": {k: len(v) for k, v in self._subscribers.items()},
                "max_reports": self._max_reports,
                "max_reports_on_disk": self._max_on_disk,
                "disk_pruned_total": self._disk_prune_total,
                # ★第52批 T1（P2-366）
                "dispatch_before_write": self._dispatch_before_write(),
                "legacy_without_consumed": self._legacy_without_consumed,
                "last_error": self._last_error,
                # ★第111批 T-111a：消费者调用踪迹（机检读取点）
                "consumer_last_invoked": dict(self._last_consumed_at),
                "published_types": sorted(self._published_types),
                "consumer_staleness_threshold": self._consumer_staleness_threshold,
                # ★第111批 T-111b：闭环解决率（机检读取点）
                "resolution_rate": self._resolution_rate(),
                "resolution_events": self._resolution_events,
                # ★第111批 T-111c：消费结果 / 路由可见化（读取点）
                "consume_results_total": _cr_total,
                "consume_results_accepted": _cr_accepted,
                "consume_results_action_taken": _cr_action,
                "envelopes_routed": _routed,
            }

    # ---------- 消费者存在性机检（★第111批 T-111a） ----------

    def get_consumer_health(self) -> dict[str, Any]:
        """每消费者消费健康：最后被调用时间、是否陈旧。

        判定口径：订阅的「报告类型」若曾被产出（``_published_types`` 命中），
        但消费者超阈值未被 dispatch 调用 -> 视为陈旧（dispatch/订阅链路可能断裂）。
        从未被产出的类型不计入陈旧（属正常无流量，非缺陷）。
        """
        with self._lock:
            _now = time.time()
            _thr = self._consumer_staleness_threshold
            _subs: dict[str, list[str]] = {}
            for _t, _lst in self._subscribers.items():
                for _fn in _lst:
                    _subs.setdefault(getattr(_fn, "__name__", str(_fn)), []).append(_t)
            _out: dict[str, Any] = {}
            for _name, _types in sorted(_subs.items()):
                _last = self._last_consumed_at.get(_name)
                _types_pub = [t for t in _types if t in self._published_types]
                if not _types_pub:
                    _stale = False
                elif _last is None:
                    _stale = True
                else:
                    _stale = (_now - _last) > _thr
                _out[_name] = {
                    "subscribed_types": _types,
                    "published_types": _types_pub,
                    "last_invoked_at": _last,
                    "seconds_since": round(_now - _last, 1) if _last is not None else None,
                    "stale": _stale,
                    "threshold_seconds": _thr,
                }
            return _out

    def check_consumer_staleness(self, threshold: float | None = None
                                 ) -> list[str]:
        """机检消费者陈旧性：超阈值未调用的消费者记 WARNING 并返回名单。

        Returns:
            超阈值消费者名列表（空 = 全部健康）。
        """
        _thr = float(threshold) if threshold is not None else self._consumer_staleness_threshold
        _stale = [n for n, _info in self.get_consumer_health().items() if _info["stale"]]
        if _stale:
            try:
                from nucleus.logger import get_module_logger
                get_module_logger("ReportBus").warning(
                    "[消费者陈旧机检] %d 个消费者超阈值(%.0fs)未被调用: %s"
                    % (len(_stale), _thr, ", ".join(_stale)))
            except Exception as _le:
                sys.stderr.write("[ReportBus] 陈旧机检日志失败: %s\n" % type(_le).__name__)
        return _stale

    def _maybe_warn_staleness(self) -> None:
        """★第111批 T-111a：节流式陈旧 WARNING（每阈值窗口最多一次）。"""
        _now = time.time()
        if _now - self._last_staleness_warn_at < self._consumer_staleness_threshold:
            return
        self._last_staleness_warn_at = _now
        self.check_consumer_staleness()

    # ---------- 闭环解决率（★第111批 T-111b） ----------

    @staticmethod
    def _is_breaching(metric_value, threshold, anomaly_type: str = "") -> bool:
        """异常是否仍越阈（★best-effort 方向推断）。

        默认「高即差」：``metric > threshold`` 越阈；
        异常码含 ``LOW`` → 「低即差」：``metric < threshold`` 越阈
        （如 ``SELF_COGNITION_SCORE_LOW``：评分低于阈值=问题）。
        """
        if metric_value is None or threshold is None:
            return False
        try:
            _mv = float(metric_value)
            _th = float(threshold)
        except (TypeError, ValueError):
            return False
        if _LOW_HINT_RE.search(anomaly_type or ""):
            return _mv < _th
        return _mv > _th

    def _track_resolution(self, envelope: ReportEnvelope) -> None:
        """★第111批 T-111b：跟踪异常「问题→已解决」状态翻转。

        每个异常(有 metric/threshold)登记为 open 问题；后续同类报告若不再越阈
        → 判定已解决，resolution_events +1（即一个「报告→消费→状态翻转」事件对）。
        best-effort（内存态，重启不恢复），口径详见设计文档七。
        """
        if not envelope.anomalies:
            return
        _now = time.time()
        for _a in envelope.anomalies:
            # 需同时具备 metric/threshold 才能判定越阈（best-effort，默认「高即差」）
            if _a.metric_value is None or _a.threshold is None:
                continue
            _key = "%s::%s" % (envelope.report_type, _a.type)
            _breaching = self._is_breaching(_a.metric_value, _a.threshold, _a.type)
            _iss = self._issues.get(_key)
            if _iss is not None and not _iss.get("resolved"):
                if not _breaching:
                    _iss["resolved"] = True
                    _iss["resolved_at"] = _now
                    self._resolution_events += 1   # 一个「报告→消费→状态翻转」事件对
                else:
                    _iss["last_metric"] = _a.metric_value
                    _iss["last_threshold"] = _a.threshold
            elif _breaching:
                # 新问题且当前越阈 → 登记为未解决（非越阈观察不计入分母）
                self._issues[_key] = {
                    "opened_at": _now,
                    "report_id": envelope.report_id,
                    "metric_value": _a.metric_value,
                    "threshold": _a.threshold,
                    "target": _a.target,
                    "resolved": False,
                    "resolved_at": None,
                    "last_metric": _a.metric_value,
                }
        self._prune_issues(_now)

    def _prune_issues(self, now: float) -> None:
        """清理已解决且超出窗口的问题，并对总量做硬上限。"""
        _drop = [k for k, v in self._issues.items()
                 if v.get("resolved") and (now - v["opened_at"]) > self._resolution_window]
        for _k in _drop:
            self._issues.pop(_k, None)
        if len(self._issues) > 2000:
            _old = sorted(self._issues.items(), key=lambda kv: kv[1]["opened_at"])
            for _k, _ in _old[:len(self._issues) - 2000]:
                self._issues.pop(_k, None)

    def _resolution_rate(self) -> float:
        """近窗内问题解决率 = 已解决数 / 登记数（best-effort）。"""
        _now = time.time()
        _recent = [v for v in self._issues.values()
                   if (_now - v["opened_at"]) <= self._resolution_window]
        if not _recent:
            return 0.0
        _resolved = sum(1 for v in _recent if v.get("resolved"))
        return round(_resolved / len(_recent), 4)

    # ---------- 容量 ----------

    def _trim(self) -> int:
        """超出上限时丢弃**最旧**的报告（仅内存；磁盘文件保留供追溯）。"""
        with self._lock:
            if len(self._envelopes) <= self._max_reports:
                return 0
            _ordered = sorted(self._envelopes.values(),
                              key=lambda e: e.generated_at)
            _drop = len(self._envelopes) - self._max_reports
            for _e in _ordered[:_drop]:
                self._envelopes.pop(_e.report_id, None)
            return _drop

    # ---------- 磁盘回收（★第51批 T5，P2-357） ----------

    def _disk_prune_enabled(self) -> bool:
        """磁盘回收开关（``ENABLE_REPORT_DISK_PRUNE``，默认 True）。

        ★显式注入目录（测试沙箱）不做回收 —— 与写盘守卫同源语义。
        """
        if self._explicit_base_dir:
            return False
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_REPORT_DISK_PRUNE", True))
        except Exception:
            return True

    def _prune_disk(self) -> dict[str, Any]:
        """磁盘回收：**每个类型子目录**按 mtime 保留最新 N 份，删最旧。

        * 重要类型（``IMPORTANT_REPORT_TYPES``）阈值 ×2；
        * 开关关闭 / 显式注入目录 → 不回收（零回归）；
        * **永不抛异常**（回收失败不得影响发布）。

        Returns:
            ``{"pruned": int, "kept": int, "errors": [str]}``
        """
        _out: dict[str, Any] = {"pruned": 0, "kept": 0, "errors": []}
        try:
            if not self._disk_prune_enabled():
                return _out
            if not os.path.isdir(self._base_dir):
                return _out
            _kept_names: list[str] = []
            for _t in os.listdir(self._base_dir):
                _tdir = os.path.join(self._base_dir, _t)
                if not os.path.isdir(_tdir) or _t.startswith("_"):
                    continue
                _limit = self._max_on_disk
                if _t in IMPORTANT_REPORT_TYPES:
                    _limit *= _ON_DISK_IMPORTANT_MULTIPLIER
                _files = []
                try:
                    for _fn in os.listdir(_tdir):
                        if not _fn.endswith(".json"):
                            continue
                        _fp = os.path.join(_tdir, _fn)
                        if os.path.isfile(_fp):
                            _files.append((os.path.getmtime(_fp), _fp))
                except OSError as _e:
                    _out["errors"].append("%s: %s" % (_t, _e))
                    continue
                _files.sort()                       # 旧 → 新
                _excess = len(_files) - _limit
                if _excess <= 0:
                    _out["kept"] += len(_files)
                    continue
                for _mt, _fp in _files[:_excess]:
                    try:
                        os.remove(_fp)
                        _out["pruned"] += 1
                        _kept_names.append(os.path.basename(_fp))
                    except OSError as _e:
                        _out["errors"].append("%s: %s" % (_fp, _e))
                _out["kept"] += min(_limit, len(_files))
            if _out["pruned"]:
                self._disk_prune_total += _out["pruned"]
                try:
                    from nucleus.logger import get_module_logger
                    get_module_logger("ReportBus").info(
                        "[报告磁盘回收] 删除 %d 份最旧报告（阈值=%d/类型，"
                        "累计=%d）；示例: %s"
                        % (_out["pruned"], self._max_on_disk,
                           self._disk_prune_total, ", ".join(_kept_names[:3])))
                except Exception as _le:
                    sys.stderr.write("[ReportBus] 回收日志失败: %s\n"
                                     % type(_le).__name__)
        except Exception as _e:                      # 回收失败不影响发布
            _out["errors"].append("%s: %s" % (type(_e).__name__, _e))
        return _out

    def get_disk_stats(self) -> dict[str, Any]:
        """磁盘占用统计（各类型目录的文件数/总字节）。"""
        _res: dict[str, Any] = {"by_type": {}, "total_files": 0, "total_bytes": 0}
        try:
            if not os.path.isdir(self._base_dir):
                return _res
            for _t in os.listdir(self._base_dir):
                _tdir = os.path.join(self._base_dir, _t)
                if not os.path.isdir(_tdir):
                    continue
                _n = 0
                _b = 0
                for _fn in os.listdir(_tdir):
                    _fp = os.path.join(_tdir, _fn)
                    if os.path.isfile(_fp):
                        _n += 1
                        _b += os.path.getsize(_fp)
                _res["by_type"][_t] = {"files": _n, "bytes": _b}
                _res["total_files"] += _n
                _res["total_bytes"] += _b
        except Exception as _e:
            _res["error"] = "%s: %s" % (type(_e).__name__, _e)
        return _res

    # ---------- 便捷构造 ----------

    def publish_simple(self, report_type: str, generator: str,
                       content: dict[str, Any] | None = None,
                       anomalies: list | None = None,
                       priority: str | None = None) -> dict[str, Any]:
        """一步发布（内部构造信封）。"""
        return self.publish(make_envelope(
            report_type=report_type, generator=generator,
            content=content, anomalies=anomalies, priority=priority))

    # ---------- 从磁盘恢复 ----------

    def load_from_disk(self, report_type: str | None = None) -> int:
        """从 ``data/reports/`` 载入已落盘的报告（用于重启后恢复统计）。

        ★第52批 T1（P2-366）两点改进：

        * 跳过 ``_`` 前缀目录（**归档区**，如 ``_archive_test_20260914``）
          —— 与 ``_prune_disk()`` 口径一致；
        * 统计 ``consumed_by`` 为空的**历史报告**（P2-366 修复前落盘的），
          记入 ``self._legacy_without_consumed`` 并经 ``get_stats()`` 暴露
          —— 这些报告的消费状态**已不可恢复**，需与"真正未消费"区分。
        """
        self._legacy_without_consumed = 0
        if not os.path.isdir(self._base_dir):
            return 0
        _n = 0
        _types = ([report_type] if report_type
                  else [d for d in os.listdir(self._base_dir)
                        if os.path.isdir(os.path.join(self._base_dir, d))
                        and not d.startswith("_")])
        for _t in _types:
            _d = os.path.join(self._base_dir, str(_t))
            if not os.path.isdir(_d):
                continue
            for _fn in sorted(os.listdir(_d)):
                if not _fn.endswith(".json"):
                    continue
                try:
                    _j = json.load(io.open(os.path.join(_d, _fn),
                                           encoding="utf-8"))
                    _e = ReportEnvelope.from_dict(_j)
                except (ValueError, OSError, TypeError):
                    continue
                with self._lock:
                    self._envelopes[_e.report_id] = _e
                if not _e.consumed_by:
                    # ★第52批 T1：历史报告（落盘时消费标记尚未填写）
                    self._legacy_without_consumed += 1
                _n += 1
        self._trim()
        return _n


# ==================== 进程内单例 ====================

_bus: ReportBus | None = None
_bus_lock = threading.RLock()


def get_report_bus(base_dir: str = DEFAULT_ROOT,
                   persist: bool = True) -> ReportBus:
    """获取进程内共享的 ReportBus（懒加载）。"""
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = ReportBus(base_dir=base_dir, persist=persist)
        return _bus


def reset_report_bus() -> None:
    """重置单例（**仅供测试使用**）。"""
    global _bus
    with _bus_lock:
        _bus = None

# _m51_t5_rb_impsys
# _m51_t5_rb_2
# _m51_t5_rb_11
# _m51_t5_rb_12
# _m51_t5_rb_14
# _m51_t5_rb_stats
# _m52_t1_init
# _m52_t1_publish
# _m52_t1_load
# _m52_t1_load2
# _m52_t1_stats