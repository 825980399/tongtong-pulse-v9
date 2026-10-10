# -*- coding: utf-8 -*-
"""
nucleus.self_awareness —— PHASE18 阶段一：自我认知引擎包

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

导出:
    SelfAwarenessEngine / get_self_awareness_engine / reset_self_awareness_engine
    SelfAwarenessProfile
    ProductionConsumptionMatcher
    FakeLoopDetector
    CallGraphAnalyzer
    SelfAwarenessDailyScheduler / start_daily_schedule   # ★主线第37批 T1（P2-210）
"""

from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer

# _m37_t1_scheduler_wired
from nucleus.self_awareness.DailyScheduler import (
    SelfAwarenessDailyScheduler,
    get_daily_scheduler,
    start_daily_schedule,
    stop_daily_schedule,
)
from nucleus.self_awareness.FakeLoopDetector import FakeLoopDetector
from nucleus.self_awareness.ProductionConsumptionMatcher import (
    ProductionConsumptionMatcher,
)
from nucleus.self_awareness.SelfAwarenessEngine import (
    SelfAwarenessEngine,
    SelfAwarenessProfile,
    get_self_awareness_engine,
    reset_self_awareness_engine,
)

__all__ = [
    "SelfAwarenessEngine",
    "SelfAwarenessProfile",
    "get_self_awareness_engine",
    "reset_self_awareness_engine",
    "ProductionConsumptionMatcher",
    "FakeLoopDetector",
    "CallGraphAnalyzer",
    "SelfAwarenessDailyScheduler",
    "get_daily_scheduler",
    "start_daily_schedule",
    "stop_daily_schedule",
]
