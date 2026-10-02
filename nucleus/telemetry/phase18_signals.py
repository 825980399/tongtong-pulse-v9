# -*- coding: utf-8 -*-
"""
phase18_signals.py —— PHASE18信号

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: PHASE18进化阶段的信号采集与传递
机制: 基于Phase18Signals类实现，包含10个核心方法
定位: 进化监测层
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any, Optional

from nucleus._silent_except import silent_exc


# 每类信号最近事件上限，防止长生命周期进程内存膨胀
_MAX_EVENTS = 200


class Phase18Signals:
    """进程内轻量信号采集器（线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._enabled = False
        self._reasoning_modes: dict[str, dict[str, float]] = defaultdict(
            lambda: {"count": 0, "success": 0,
                     "total_elapsed": 0.0, "max_elapsed": 0.0}
        )
        self._strategy_events: deque = deque(maxlen=_MAX_EVENTS)
        self._knowledge_events: deque = deque(maxlen=_MAX_EVENTS)
        self._l3_events: deque = deque(maxlen=_MAX_EVENTS)
        self._evolution_events: deque = deque(maxlen=_MAX_EVENTS)
        self._qica_events: deque = deque(maxlen=_MAX_EVENTS)
        # B156-7：snapshot() 消费方注册表（PHASE18 阶段二前置接入点）
        self._snapshot_consumers: list = []

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def enable(self, enabled: bool = True) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # 1. 推理模式使用数据
    # ------------------------------------------------------------------
    def record_reasoning_mode(self, mode: str, success: bool = True,
                              elapsed: Optional[float] = None) -> None:
        """记录一次推理模式使用。

        Args:
            mode: 推理模式标识（如 cognitive_compute / rule_reason）。
            success: 本次使用是否成功（默认 True）。
            elapsed: 耗时（秒）；不传记 0.0。
        """
        if not self._enabled or not mode:
            return
        _el = float(elapsed) if elapsed is not None else 0.0
        with self._lock:
            rec = self._reasoning_modes[mode]
            rec["count"] += 1
            if success:
                rec["success"] += 1
            rec["total_elapsed"] += _el
            rec["max_elapsed"] = max(rec["max_elapsed"], _el)

    # ------------------------------------------------------------------
    # 2. 策略选择数据
    # ------------------------------------------------------------------
    def record_strategy_selection(self, intent: str, strategy: str,
                                 basis: Optional[str] = None,
                                 confidence: Optional[float] = None) -> None:
        """记录一次大脑皮层策略选择。"""
        if not self._enabled or not strategy:
            return
        with self._lock:
            self._strategy_events.append({
                "intent": intent,
                "strategy": strategy,
                "basis": basis,
                "confidence": confidence,
                "ts": time.time(),
            })

    # ------------------------------------------------------------------
    # 3. 知识质量数据（矛盾消解结果）
    # ------------------------------------------------------------------
    def record_knowledge_quality(self, event_type: str, **fields: Any) -> None:
        """记录一次知识质量事件（如矛盾消解）。"""
        if not self._enabled or not event_type:
            return
        with self._lock:
            evt = {"event_type": event_type, "ts": time.time()}
            evt.update(fields)
            self._knowledge_events.append(evt)

    # ------------------------------------------------------------------
    # 4. L3 背压数据（生命形体指数）
    # ------------------------------------------------------------------
    def record_l3_backpressure(self, depth: int, limit: int, workers: int,
                              producer_top5: Optional[list] = None,
                              consumer_rate_per_sec: Optional[float] = None) -> None:
        """记录一次 L3 队列背压快照（供 PHASE18 生命形体指数使用）。

        Args:
            depth: 当前 L3 队列深度。
            limit: L3 队列硬上限。
            workers: 当前 L3 worker 数。
            producer_top5: [(organ, count), ...] 生产者 TOP5。
            consumer_rate_per_sec: L3 消费速率（脉冲/秒，近 60s 滑窗）。
        """
        if not self._enabled:
            return
        with self._lock:
            self._l3_events.append({
                "depth": int(depth),
                "limit": int(limit),
                "workers": int(workers),
                "ratio": round(depth / limit, 3) if limit else 0.0,
                "producer_top5": list(producer_top5 or []),
                "consumer_rate_per_sec": float(consumer_rate_per_sec or 0.0),
                "ts": time.time(),
            })

    # ------------------------------------------------------------------
    # 5. 自主进化数据（自我认知引擎）
    # ------------------------------------------------------------------
    def record_evolution(self, **fields: Any) -> None:
        """记录一次自主进化循环快照（修复率/审批率/待审批数等，供 PHASE18 自我认知引擎）。

        字段自由承载：approval_rate / fix_rate / pending_count / auto_approved / by_decision ...
        """
        if not self._enabled:
            return
        with self._lock:
            fields["ts"] = time.time()
            self._evolution_events.append(dict(fields))

    # ------------------------------------------------------------------
    # 6. QICA 8 通道贡献度数据（器官关联图谱）
    # ------------------------------------------------------------------
    def record_qica_channel_contrib(self, contributions: dict[str, float],
                                   top_intent: str | None = None,
                                   top_score: float | None = None,
                                   extra: Optional[dict] = None) -> None:
        """记录一次 QICA 分类的 8 通道贡献度分布（供 PHASE18 器官关联图谱使用）。

        Args:
            contributions: {通道名: 贡献占比(0~1，求和=1)}，如
                {"semantic": 0.31, "keyword": 0.24, ...}
            top_intent: 本次分类得到的意图。
            top_score: 融合 top 分。
            extra: 其他上下文（如 gold/correct）。
        """
        if not self._enabled or not contributions:
            return
        with self._lock:
            _rec = {
                "contributions": dict(contributions),
                "top_intent": top_intent,
                "top_score": top_score,
                "ts": time.time(),
            }
            if extra:
                _rec.update(extra)
            self._qica_events.append(_rec)

    # ------------------------------------------------------------------
    # 快照（供 PHASE18 消费）
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        """返回 JSON 可序列化的三类信号快照。"""
        with self._lock:
            modes: dict[str, dict] = {}
            for k, v in self._reasoning_modes.items():
                cnt = v["count"] or 1
                modes[k] = {
                    "count": v["count"],
                    "success_rate": round(v["success"] / cnt, 4),
                    "avg_elapsed": round(v["total_elapsed"] / cnt, 6),
                    "max_elapsed": round(v["max_elapsed"], 6),
                }
            return {
                "enabled": self._enabled,
                "reasoning_mode": modes,
                "strategy_selection": list(self._strategy_events),
                "knowledge_quality": list(self._knowledge_events),
                "l3_backpressure": list(self._l3_events),
                "evolution": list(self._evolution_events),
                "qica_channel": list(self._qica_events),
            }

    def reset(self) -> None:
        """清空所有采集数据（保留 enabled 状态）。"""
        with self._lock:
            self._reasoning_modes.clear()
            self._strategy_events.clear()
            self._knowledge_events.clear()
            self._l3_events.clear()
            self._evolution_events.clear()
            self._qica_events.clear()

    # ------------------------------------------------------------------
    # 快照消费方注册（PHASE18 阶段二前置 · B156-7 接入点）
    # ------------------------------------------------------------------
    def register_snapshot_consumer(self, callback: Any) -> None:
        """注册一个 ``snapshot()`` 消费方回调。

        callback 签名：``callback(snapshot: dict) -> None``。
        PHASE18 阶段二消费方（器官关联图谱 / 自我认知画像）通过此接口订阅
        ``snapshot()`` 产出。当前无自动派发，消费方需显式调用
        ``dispatch_snapshot()`` 或自行轮询 ``snapshot()``。

        零消费方时采集器行为不变（record_* 仍受开关门控）。
        """
        if callback is None:
            return
        with self._lock:
            if callback not in self._snapshot_consumers:
                self._snapshot_consumers.append(callback)

    @property
    def snapshot_consumer_count(self) -> int:
        """已注册消费方数量（供测试与零消费方告警判定）。"""
        return len(self._snapshot_consumers)

    def dispatch_snapshot(self) -> int:
        """将当前 ``snapshot()`` 派发给所有已注册消费方，返回成功派发数。

        仅在显式调用时触发，不改变既有采集/快照语义（默认零副作用）。
        单个消费方异常不污染采集器，仅静默吞掉。
        """
        snap = self.snapshot()
        sent = 0
        with self._lock:
            consumers = list(self._snapshot_consumers)
        for cb in consumers:
            try:
                cb(snap)
                sent += 1
            except Exception as _e:  # 消费方异常不应影响采集器
                silent_exc(_e, where="nucleus.telemetry.phase18_signals.dispatch_snapshot")
        return sent


_phase18_signals: Optional[Phase18Signals] = None


def get_phase18_signals() -> Phase18Signals:
    """进程级单例；首次访问时惰性读取 config.ENABLE_PHASE18_SIGNALS 初始化开关。"""
    global _phase18_signals
    if _phase18_signals is None:
        _phase18_signals = Phase18Signals()
        try:
            import config
            _phase18_signals.enable(getattr(config, "ENABLE_PHASE18_SIGNALS", False))
        except Exception:
            _phase18_signals.enable(False)
    return _phase18_signals


def register_phase18_snapshot_consumer(callback: Any) -> None:
    """模块级便捷入口：向进程级单例注册 ``snapshot()`` 消费方（B156-7）。

    PHASE18 阶段二消费方（器官关联图谱 / 自我认知画像）调用本函数订阅采集快照。
    注册表随单例进程级存活；零消费方时采集器行为不变。
    """
    get_phase18_signals().register_snapshot_consumer(callback)
