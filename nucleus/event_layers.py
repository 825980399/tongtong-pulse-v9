# -*- coding: utf-8 -*-
"""484-2 事件分层注册表：事件 → 层级的**单一真源**。

★T0 偏差 D6：以 PulseCore._DEFAULT_LAYER_MAP 全量为基线逐条吸收（保持原层），
只按任务书覆盖 heart.beat=L3 / interest.changed=L2，并新增 status.request=L2
与 organ_handbook_updated=L2（D7 主动补登记）。
★D14：全部 key 用 const.py 类属性，绝不写裸点分事件名（event-string 门禁）。
"""
from nucleus.const import (
    ChatEvent,
    EarEvent,
    EnergyEvent,
    Event,
    HeartEvent,
    HormonesEvent,
    InterestEvent,
    KnowledgeEvent,
    MetricsEvent,
    MouthEvent,
    NarrativeEvent,
    ProprioceptionEvent,
    ReflectionEvent,
    SpinalCordEvent,
    SubconsciousEvent,
    SystemEvent,
    TouchEvent,
    VascularEvent,
)

#: 未登记事件的兜底层（任务书要求 L3，不是 L1）。
UNKNOWN_LAYER = "L3"

EVENT_LAYER_MAP = {
    # ── L0：丢了会瞎（生命线）──
    SystemEvent.BOOT: "L0",
    SystemEvent.STOP: "L0",
    SystemEvent.ALARM: "L0",
    SystemEvent.ERROR: "L0",
    HeartEvent.ALIVE: "L0",
    VascularEvent.SILENT_ORGAN: "L0",
    # ── L1：有人正在等结果（实时对话）──
    Event.CHAT_MESSAGE: "L1",
    ChatEvent.INITIATIVE: "L1",
    ChatEvent.SILENCE_TIMEOUT: "L1",
    MouthEvent.SPEAK: "L1",
    MouthEvent.REPLY: "L1",
    EarEvent.HEARD: "L1",
    EarEvent.INTENT_DETECTED: "L1",
    ReflectionEvent.INSIGHT: "L1",
    # ── L2：影响下一次思考（认知）──
    SystemEvent.STATUS_REQUEST: "L2",
    Event.DIGEST_KNOWLEDGE: "L2",
    KnowledgeEvent.WRITTEN: "L2",
    KnowledgeEvent.COMPRESSED: "L2",
    KnowledgeEvent.RAW: "L2",
    KnowledgeEvent.FUSED: "L2",
    InterestEvent.CHANGED: "L2",
    NarrativeEvent.UPDATED: "L2",
    NarrativeEvent.RECORD: "L2",
    HormonesEvent.DETECT: "L2",
    HormonesEvent.EMOTION_DETECTED: "L2",
    Event.ORGAN_HANDBOOK_UPDATED: "L2",
    # ── L3：周期 tick / snapshot / report（后台自主）──
    HeartEvent.BEAT: "L3",
    Event.CURIOSITY_TICK: "L3",
    SubconsciousEvent.CURIOSITY_TICK: "L3",
    SubconsciousEvent.EXPLORE: "L3",
    EnergyEvent.METABOLISM_SNAPSHOT: "L3",
    TouchEvent.HARDWARE_SNAPSHOT: "L3",
    SpinalCordEvent.INSPECTION_REPORT: "L3",
    Event.LEGS_LEARN_NOW: "L3",
    ProprioceptionEvent.REPORT: "L3",
    MetricsEvent.SNAPSHOT: "L3",
    "purge.check": "L3",
    "purge.result": "L3",
}

__all__ = ["EVENT_LAYER_MAP", "UNKNOWN_LAYER"]
