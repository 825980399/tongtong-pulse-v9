# -*- coding: utf-8 -*-
"""nucleus.reporting —— 自认知报告统一契约与分发层

主线第47批 T3（P0-1）。

组成：
  * ``report_envelope`` —— ``ReportEnvelope`` / ``Anomaly`` 统一契约
  * ``report_bus``      —— ``ReportBus`` 分发层（发布/订阅/消费/统计）
  * ``consumers``       —— 内置消费者（健康异常 / 污染异常）

设计原则：
  **新增通道、不替换旧通道。** 旧的报告生成与直接落盘方式**保持可用**，
  ReportBus 只是一条**额外的**分发通道 → 向后兼容、零回归。

★本包在 import 时**不创建任何目录、不做任何 IO**，便于安全导入。
"""

# ★主线第50批 T1（P0-1）：报告 → 信封适配器
from . import publishers
from .publishers import (
                         publish_data_quality,
                         publish_generic,
                         publish_health,
                         publish_patch_quality,
                         publish_pollution,
                         publish_self_cognition,
)
from .report_bus import MAX_REPORTS, ReportBus, get_report_bus, reset_report_bus
from .report_envelope import (
                         ACT_ALERT,
                         ACT_CLEAN_DATA,
                         ACT_ENQUEUE_TASK,
                         ACT_GENERATE_PATCH,
                         ACT_LOG_ONLY,
                         SEV_P0,
                         SEV_P1,
                         SEV_P2,
                         SEVERITIES,
                         TYPE_EVOLUTION,
                         TYPE_GENERIC,
                         TYPE_HEALTH,
                         TYPE_KNOWLEDGE,
                         TYPE_POLLUTION,
                         TYPE_RUNTIME,
                         TYPE_SELF_COGNITION,
                         Anomaly,
                         ReportEnvelope,
                         make_envelope,
                         new_report_id,
)

__all__ = [
    "ReportEnvelope", "Anomaly", "make_envelope", "new_report_id",
    "ReportBus", "get_report_bus", "reset_report_bus", "MAX_REPORTS",
    "SEV_P0", "SEV_P1", "SEV_P2", "SEVERITIES",
    "ACT_ALERT", "ACT_GENERATE_PATCH", "ACT_ENQUEUE_TASK",
    "ACT_CLEAN_DATA", "ACT_LOG_ONLY",
    "TYPE_HEALTH", "TYPE_POLLUTION", "TYPE_EVOLUTION", "TYPE_SELF_COGNITION",
    "TYPE_KNOWLEDGE", "TYPE_RUNTIME", "TYPE_GENERIC",
    # ★第50批 T1：发布适配器
    "publishers", "publish_health", "publish_pollution",
    "publish_self_cognition", "publish_patch_quality",
    "publish_data_quality", "publish_generic",
]
# _m50_t1_init_done
