# -*- coding: utf-8 -*-
"""自认知报告统一契约（主线第47批 T3，P0-1）

背景（第46批实测）
------------------
框架**不缺自认知能力**（12 小时里生成 1108 行报告日志，还精确诊断出
"补丁有效率 3%" 与 "经验库污染 79.5%"）。缺的是**从诊断到动作的那一跳**：

* ``generate_report(output_path=None)`` **默认不落盘**（只返回字符串）
* 无注册表、无统一格式、无分发通道
* 只有"恰好知道路径"的叙事自我吃到报告（日志：``生命故事整合: 从6份报告中提炼故事``）

本模块定义**统一信封**，让报告可被机器消费、可被分发、可被统计。

设计要点
--------
* ``Anomaly`` 是**可触发动作的原子单元**。现有报告只有"数据"、
  没有"这条数据意味着什么问题"，没有它消费者只能靠正则硬解析文本 ——
  这正是消费率 0% 的技术根因。
* 严重度分 P0/P1/P2，供分级消费（P0 立即告警、P1 入队、P2 仅记录）。
* 纯数据类 + 序列化，**不依赖任何框架模块**，便于独立测试。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

#: 严重度取值（与第46批《自认知报告消费机制》一致）
SEV_P0 = "P0"
SEV_P1 = "P1"
SEV_P2 = "P2"
SEVERITIES = (SEV_P0, SEV_P1, SEV_P2)

#: 内置报告类型
TYPE_HEALTH = "health"
TYPE_POLLUTION = "pollution"
TYPE_EVOLUTION = "evolution"
TYPE_SELF_COGNITION = "self_cognition"
TYPE_KNOWLEDGE = "knowledge"
TYPE_RUNTIME = "runtime"
TYPE_GENERIC = "generic"

#: 建议动作
ACT_ALERT = "alert"                  # 告警（写入日志 + 标记需人工介入）
ACT_GENERATE_PATCH = "generate_patch"
ACT_ENQUEUE_TASK = "enqueue_task"
ACT_CLEAN_DATA = "clean_data"
ACT_LOG_ONLY = "log_only"

SCHEMA_VERSION = "1.0"


def new_report_id(prefix: str = "rep") -> str:
    """生成报告唯一 ID。"""
    return "%s_%s_%s" % (prefix, time.strftime("%Y%m%d%H%M%S"),
                         uuid.uuid4().hex[:8])


@dataclass
class Anomaly:
    """一条**可被消费**的异常（报告里"哪里有问题"的结构化表达）。

    Attributes:
        type:             异常编码（稳定字符串，如 ``PATCH_BASELINE_ZERO``）
        severity:         ``P0`` / ``P1`` / ``P2``
        description:      人类可读描述
        source:           来源（模块名 / 报告类型）
        suggested_action: 建议动作（``alert`` / ``generate_patch`` /
                          ``enqueue_task`` / ``clean_data`` / ``log_only``）
        metric_value:     当前指标值（可选）
        threshold:        阈值（可选）
        target:           关联对象（文件 / 模块 / 记录 id，可选）
    """
    type: str
    severity: str = SEV_P2
    description: str = ""
    source: str = ""
    suggested_action: str = ACT_LOG_ONLY
    metric_value: float | None = None
    threshold: float | None = None
    target: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            self.severity = SEV_P2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Anomaly":
        _known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (d or {}).items() if k in _known})


@dataclass
class ReportEnvelope:
    """所有自认知报告的统一外层。"""
    report_id: str = field(default_factory=new_report_id)
    report_type: str = TYPE_GENERIC
    generated_at: float = field(default_factory=time.time)
    generator: str = ""
    priority: str = SEV_P2
    content: dict[str, Any] = field(default_factory=dict)
    anomalies: list[Anomaly] = field(default_factory=list)
    consumed_by: list[str] = field(default_factory=list)
    actions_triggered: list[str] = field(default_factory=list)
    # ★第111批 T-111c：消费结果 + 路由决策（被 ReportBus.get_stats 读取，非死键）
    consume_results: list[ConsumeResult] = field(default_factory=list)
    routing_order: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.priority not in SEVERITIES:
            self.priority = SEV_P2
        # anomalies 可能以 dict 形式传入（反序列化场景）
        _norm: list[Anomaly] = []
        for _a in self.anomalies or []:
            _norm.append(_a if isinstance(_a, Anomaly) else Anomaly.from_dict(_a))
        self.anomalies = _norm
        # ★第111批 T-111c：消费结果反序列化（dict -> ConsumeResult）
        _cr_norm: list[ConsumeResult] = []
        for _cr in self.consume_results or []:
            _cr_norm.append(_cr if isinstance(_cr, ConsumeResult)
                            else ConsumeResult.from_dict(_cr))
        self.consume_results = _cr_norm

    # ---------- 派生属性 ----------

    @property
    def generated_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.generated_at))

    @property
    def max_severity(self) -> str:
        """报告的实际最高严重度：优先取异常中的最高级，回退 priority。"""
        _order = {SEV_P0: 0, SEV_P1: 1, SEV_P2: 2}
        _best = self.priority
        for _a in self.anomalies:
            if _order.get(_a.severity, 9) < _order.get(_best, 9):
                _best = _a.severity
        return _best

    def has_p0(self) -> bool:
        return self.max_severity == SEV_P0

    def is_consumed(self) -> bool:
        return bool(self.consumed_by)

    # ---------- 消费结果 / 路由（★第111批 T-111c） ----------

    def record_consume_result(self, result: "ConsumeResult") -> None:
        """记录一个消费者的结构化结果（供 ``get_stats`` 读取，闭环可见化）。"""
        if not isinstance(result, ConsumeResult):
            return
        if not result.consumer:
            return
        self.consume_results.append(result)

    # ---------- 序列化 ----------

    def to_dict(self) -> dict[str, Any]:
        _d = asdict(self)
        _d["generated_str"] = self.generated_str
        _d["max_severity"] = self.max_severity
        return _d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReportEnvelope":
        _known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        _kw = {k: v for k, v in (d or {}).items() if k in _known}
        return cls(**_kw)


@dataclass
class ConsumeResult:
    """★第111批 T-111c：消费者返回的**结构化结果**（此前只返回 bool）。

    保留与旧 ``bool`` 的兼容性：``__bool__`` 取 ``accepted``，
    因此 ``ReportBus._dispatch`` 中 ``if _res:`` 仍成立。
    ``action_ref`` / ``note`` 仅在动作有意义时填写（防只写不读死键）。
    """
    consumer: str = ""                 # 消费者名
    report_id: str = ""                # 关联报告 id
    accepted: bool = False             # 是否认领（= 旧 bool 真值）
    action_taken: bool = False         # 是否实际执行了动作（落盘/告警等）
    action_ref: str | None = None      # 动作落点引用（文件/记录 id），无意义=None
    note: str = ""                     # 人类可读备注

    def __bool__(self) -> bool:
        return self.accepted

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConsumeResult":
        _known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (d or {}).items() if k in _known})


def make_envelope(report_type: str, generator: str,
                  content: dict[str, Any] | None = None,
                  anomalies: list[Anomaly] | None = None,
                  priority: str | None = None,
                  report_id: str | None = None) -> ReportEnvelope:
    """便捷构造：优先级可省略（自动取异常中的最高级）。"""
    _env = ReportEnvelope(
        report_id=report_id or new_report_id(report_type),
        report_type=report_type,
        generator=generator,
        content=content or {},
        anomalies=anomalies or [],
    )
    if priority:
        _env.priority = priority if priority in SEVERITIES else SEV_P2
    else:
        _env.priority = _env.max_severity if _env.anomalies else SEV_P2
    return _env
