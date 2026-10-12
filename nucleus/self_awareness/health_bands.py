# -*- coding: utf-8 -*-
"""层②分级带 单一真源（★199刀2）。

把「综合评分 → 健康等级」的档位定义集中到本模块，消除 engine 与 v2 质量分各自
硬编码档位导致的双词面。本模块零依赖（仅标准库），可被 engine /
quality_score_v2 安全引用，不引入循环依赖。

提供两套口径（均为历史与现行并存的真值，集中一处即「单一真源」）：

* V1（legacy engine，SelfAwarenessEngine._health_level 沿用）：
    ≥80 healthy / 60-79 moderate / 40-59 concerning / <40 critical
  —— 保留 v1 词面与阈值，零行为。既有 ``_health_level`` 测试（test_profile_overall_m38）
      与 PulseCortex 质量闸（检查 {concerning, critical}）依赖 concerning/critical，
      本刀不改，仅把硬编码档位上提为本表常量。

* V2（新口径，quality_score_v2.quality_level 沿用，任务书主口径）：
    <40 critical / <70 warning / <90 attention / ≥90 healthy
  —— critical<40 / warning<70 / attention<90 / healthy≥90。
"""
from typing import Optional


# ---- V2 新口径边界（任务书主口径）----
V2_CRITICAL_MAX = 40   # score < 40 -> critical
V2_WARNING_MAX = 70    # score < 70 -> warning
V2_ATTENTION_MAX = 90  # score < 90 -> attention；>=90 healthy

# ---- V1 legacy 边界（engine._health_level 沿用，零行为）----
V1_HEALTHY_MIN = 80    # score >= 80 -> healthy
V1_MODERATE_MIN = 60   # score >= 60 -> moderate
V1_CONCERNING_MIN = 40 # score >= 40 -> concerning；< 40 critical

# 规范化词表（供断言/文档引用）
V2_LEVELS = ("critical", "warning", "attention", "healthy")
V1_LEVELS = ("healthy", "moderate", "concerning", "critical")


def level_v1(score) -> str:
    """V1 健康等级（legacy engine 口径，零行为保留）。"""
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return "unknown"
    if score >= V1_HEALTHY_MIN:
        return "healthy"
    if score >= V1_MODERATE_MIN:
        return "moderate"
    if score >= V1_CONCERNING_MIN:
        return "concerning"
    return "critical"


def level_v2(score: Optional[float]) -> str:
    """V2 健康等级（新口径，critical<40/warning<70/attention<90/healthy≥90）。"""
    if score is None:
        return "unknown"
    if score < V2_CRITICAL_MAX:
        return "critical"
    if score < V2_WARNING_MAX:
        return "warning"
    if score < V2_ATTENTION_MAX:
        return "attention"
    return "healthy"
