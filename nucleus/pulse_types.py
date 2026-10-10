# -*- coding: utf-8 -*-
"""
pulse_types.py —— 脉冲类型

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 脉冲信号数据类型与结构定义
机制: 基于TimeDim类实现，包含0个核心方法
定位: 核心数据层
"""

from __future__ import annotations

from typing import Any, TypedDict

# ========================================================================
# 脉冲五维信息子结构（PulseCore.emit 预留字段）
# ========================================================================

class TimeDim(TypedDict):
    """时间维：脉冲产生的时间信息。"""
    created_at: float  # time.time() 浮点时间戳


class SpaceDim(TypedDict):
    """空间维：脉冲关联的知识空间路径。"""
    path: str  # 知识树路径，如 '/身份/自我'


class StateDim(TypedDict):
    """状态维：脉冲的生命周期状态。"""
    state: str  # 'created' / 'active' / 'done' 等


class LogicDim(TypedDict):
    """逻辑维：脉冲的调用链，用于追溯推理来源。"""
    call_chain: list[str]  # 来源器官调用链


class MemoryDim(TypedDict):
    """记忆维：脉冲与记忆系统的关联（频率签名 + 赫布权重）。"""
    frequency_signature: float  # 频率签名
    linked_nodes: list[str]  # 关联节点 ID 列表
    hebbian_weight: float  # 赫布学习权重


# ========================================================================
# 脉冲 payload（通用外壳）
# ========================================================================

class PulsePayload(TypedDict, total=False):
    """脉冲载荷的通用契约（total=False：字段按需出现）。

    各事件类型可在此基础扩展专属字段；核心通用字段如下:
    - content: 文本内容（对话/知识/推理结果）
    - source_organ: 载荷内标注的来源器官（部分事件在 payload 内重复标注）
    - trigger_reason: 触发原因
    - user_name: 关联用户名
    - emotional_tone: 情绪基调（positive/neutral/negative）
    - event_type: 载荷内标注的事件类型（部分事件重复标注）
    """
    content: str
    source_organ: str
    trigger_reason: str
    user_name: str
    emotional_tone: str
    event_type: str
    priority: int


# ========================================================================
# 脉冲完整结构（PulseCore.emit 权威返回）
# ========================================================================

class Pulse(TypedDict):
    """完整脉冲字典契约（对应 PulseCore.emit 的返回 dict）。

    字段与 PulseCore.emit 第 150-169 行逐一对应，是跨器官脉冲传递的
    唯一权威结构。任何新增字段须先在此声明，再改 emit。
    """
    pulse_id: str                    # 唯一脉冲 ID：pulse:{source}:{event}:{ts}:{seq}
    source_organ: str                # 来源器官名（中文或英文类名）
    event_type: str                  # 事件类型（如 'heart.beat'）
    priority: int                    # 优先级 0-10
    layer: str                       # 层级 L0/L1/L2/L3
    intent: str                      # 意图标记 request/suggest/alert/notify/question
    timestamp_ns: int                # 纳秒时间戳
    time_dim: TimeDim                # 时间维
    space_dim: SpaceDim              # 空间维
    state_dim: StateDim              # 状态维
    logic_dim: LogicDim              # 逻辑维
    memory_dim: MemoryDim            # 记忆维
    payload: PulsePayload            # 载荷
    ttl_ns: int                      # 有效期（纳秒）
    status: str                      # 生命周期状态 'created' 等


# ========================================================================
# 事件专属 payload 契约（按需扩展，本文件先覆盖高频核心事件）
# ========================================================================

class HeartBeatPayload(PulsePayload):
    """心跳脉冲载荷。"""
    beat_count: int
    task: str


class ChatMessagePayload(PulsePayload):
    """对话消息脉冲载荷。"""
    message: str
    session_id: str


# ========================================================================
# 类型别名（方便消费方 import）
# ========================================================================

# 脉冲处理器签名：接收 pulse，可选返回 dict
PulseHandler = Any  # 保留为 Any，避免与 Callable 泛型在 3.11 的兼容问题

__all__ = [
    "ChatMessagePayload",
    "HeartBeatPayload",
    "LogicDim",
    "MemoryDim",
    "Pulse",
    "PulseHandler",
    "PulsePayload",
    "SpaceDim",
    "StateDim",
    "TimeDim",
]
