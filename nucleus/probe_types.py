# -*- coding: utf-8 -*-
"""
probe_types.py —— 探测类型

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 硬件探测数据类型定义
机制: 基于ProbeIssue类实现，包含0个核心方法
定位: 硬件抽象层
"""

from __future__ import annotations

from typing import TypedDict



class ProbeIssue(TypedDict, total=False):
    """探查 issue 的统一契约（三通道归一后的权威字段）。

    必读字段（聚合层依赖）:
    - file: 文件路径
    - line: 行号（int）
    - type: 问题类型（如 syntax_error / lint_F401 / silent_exception）
    - severity: 严重级别 high / medium / low
    - message: 问题描述（归一层的唯一「描述」字段，由 description 映射而来）

    溯源/扩展字段（按需出现）:
    - tool: 来源工具 compileall / ruff / mypy / bandit（工具通道专属）
    - organ: 所属器官（类名）
    - method: 方法名
    - description: 原始描述（与 message 等价，保留兼容旧消费方）
    - suggestion: 改进建议（规则通道专属）
    - lifecycle: 生命周期状态 new/persistent/resolved/legacy/reopened
    - learned_fix_strategy: 已蒸馏的 LLM 修复策略
    - confidence: 置信度（可选，供回流学习枢纽用）
    """
    file: str
    line: int
    type: str
    severity: str
    message: str
    tool: str
    organ: str
    method: str
    description: str
    suggestion: str
    lifecycle: str
    learned_fix_strategy: str
    confidence: float


# ========================================================================
# 聚合报告契约
# ========================================================================

class AuditReport(TypedDict):
    """`aggregate_audit_results` 输出的统一审查报告结构。"""
    total_issues: int
    severity_counts: dict[str, int]
    type_counts: dict[str, int]
    issues: list[ProbeIssue]
    llm_review: dict[str, object]


__all__ = [
    "AuditReport",
    "ProbeIssue",
]
