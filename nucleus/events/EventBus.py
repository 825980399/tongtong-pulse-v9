# -*- coding: utf-8 -*-
"""
EventBus.py —— 轻量级事件总线

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 提供统一的事件发布/订阅基础设施——同步发布、异步优先级队列投递、
      订阅过滤（精确名 + 分段通配符）、事件溯源历史（供 LogAnalyzer 分析）。
机制: 基于 EventBus 单例类实现。订阅表按 sub_id 组织为 dict，投递时按
      （优先级, 注册时间, sub_id）稳定排序回调；同步事件在发布线程内**直接**
      调用处理器（实测 <1ms），异步事件写入 PriorityQueue 由单一 daemon
      工作线程出队投递；每次发布都写入有界环形历史（deque(maxlen=…)，
      默认上限 10000 条），可通过 get_history()/export_history() 做事件溯源。
定位: 骨架夯实层（P2-63）——为后续器官解耦与 PHASE18 事件驱动重构提供地基。
      ★红线：**不改变器官现有通信方式**（InfoField / 直接调用照旧），
      本模块只**新增**能力，默认开关 ENABLE_EVENT_BUS=False，无任何生产接线，
      因此对现存运行路径零副作用。

通配符规则（按 `.` 分段匹配，非 fnmatch 字符匹配，行为可预测）：
    "heart.beat"       精确匹配
    "heart.*"          匹配 heart 下任意一层，如 heart.beat / heart.stop
    "heart.**"         匹配 heart 及其任意深度子孙，如 heart / heart.a / heart.a.b
    "**"               匹配全部事件
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Self

from nucleus.logger import get_module_logger

_logger = get_module_logger("EventBus")

#: 事件处理器签名：接收一个 Event，返回值被忽略。
EventHandler = Callable[["Event"], Any]

#: 同步发布延迟（毫秒）超过该阈值时打 DEBUG 提示（性能回归哨兵）。
_SYNC_LATENCY_WARN_MS = 5.0


class EventPriority(IntEnum):
    """事件优先级：**数值越小越优先**（与 PriorityQueue 的最小堆语义一致）。"""

    CRITICAL = 0
    HIGH = 10
    NORMAL = 20
    LOW = 30


def _match(pattern: str, name: str) -> bool:
    """按 `.` 分段做通配符匹配。

    Args:
        pattern: 订阅模式，支持 `*`（单段）与 `**`（零或多段）。
        name: 事件名。

    Returns:
        是否匹配。空 pattern 视为不匹配（避免误订阅全量）。
    """
    if not pattern:
        return False
    if pattern == name:
        return True
    ps = pattern.split(".")
    ns = name.split(".")
    # dp[i][j]：pattern 前 i 段能否匹配 name 前 j 段
    dp = [[False] * (len(ns) + 1) for _ in range(len(ps) + 1)]
    dp[0][0] = True
    for i in range(1, len(ps) + 1):
        seg = ps[i - 1]
        if seg == "**":
            dp[i][0] = dp[i - 1][0]
        for j in range(1, len(ns) + 1):
            if seg == "**":
                dp[i][j] = dp[i - 1][j] or dp[i][j - 1]
            elif seg == "*":
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = dp[i - 1][j - 1] and seg == ns[j - 1]
    return dp[len(ps)][len(ns)]


@dataclass
class Event:
    """一次事件发布的数据载体。"""

    name: str
    payload: Any = None
    priority: EventPriority = EventPriority.NORMAL
    source: str = ""
    event_id: str = ""
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        name: str,
        payload: Any = None,
        priority: EventPriority = EventPriority.NORMAL,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Event:
        """构造事件并补齐 event_id / timestamp（未显式提供时自动生成）。"""
        return cls(
            name=str(name),
            payload=payload,
            priority=priority,
            source=str(source or ""),
            event_id=uuid.uuid4().hex[:16],
            timestamp=time.time(),
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """转可序列化字典（事件溯源 / LogAnalyzer 消费口径）。"""
        return {
            "event_id": self.event_id,
            "name": self.name,
            "payload": self.payload,
            "priority": int(self.priority),
            "priority_name": self.priority.name,
            "source": self.source,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


@dataclass
class Subscription:
    """一条订阅记录。"""

    sub_id: str
    pattern: str
    handler: EventHandler
    priority: EventPriority = EventPriority.NORMAL
    once: bool = False
    created_at: float = 0.0
    invoked: int = 0


class EventBus:
    """线程安全的事件总线（可多实例，生产建议用 get_event_bus() 单例）。"""

    def __init__(self, async_enabled: bool = True, max_history: int = 10000) -> None:
        """
        Args:
            async_enabled: 是否启用异步队列 + 后台工作线程。
                False 时 publish_async 退化为同步投递（不创建任何线程）。
            max_history: 事件溯源历史上限（环形，超出丢弃最旧）。
        """
        self._lock = threading.RLock()
        self._subs: dict[str, Subscription] = {}
        self._history: deque[Event] = deque(maxlen=max(1, int(max_history)))
        self._max_history = max(1, int(max_history))
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._async_enabled = bool(async_enabled)
        self._closed = False
        # 统计
        self._published = 0
        self._delivered = 0
        self._failed = 0
        self._dropped = 0
        self._sync_ms_max = 0.0
        self._last_error = ""
        self._zero_match_events = 0

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------
    def subscribe(
        self,
        pattern: str,
        handler: EventHandler,
        priority: EventPriority = EventPriority.NORMAL,
        once: bool = False,
    ) -> str:
        """注册订阅。

        Args:
            pattern: 事件名或通配模式（`*` 单段、`**` 多段）。
            handler: 处理器，签名 `(Event) -> Any`，返回值忽略。
            priority: 同一事件多个处理器的调用顺序（数值小者先）。
            once: 为 True 时触发一次后自动退订。

        Returns:
            订阅 ID（用于 unsubscribe）。

        Raises:
            TypeError: handler 不可调用。
            ValueError: pattern 为空。
        """
        if not callable(handler):
            raise TypeError("handler 必须是可调用对象")
        if not pattern:
            raise ValueError("pattern 不能为空")
        sub = Subscription(
            sub_id=uuid.uuid4().hex[:16],
            pattern=str(pattern),
            handler=handler,
            priority=priority,
            once=bool(once),
            created_at=time.time(),
        )
        with self._lock:
            self._subs[sub.sub_id] = sub
        return sub.sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """取消订阅。返回是否确有该订阅。"""
        with self._lock:
            return self._subs.pop(sub_id, None) is not None

    def subscription_count(self, pattern: str | None = None) -> int:
        """订阅数；给定 pattern 时只统计订阅该模式的条目。"""
        with self._lock:
            if pattern is None:
                return len(self._subs)
            return sum(1 for s in self._subs.values() if s.pattern == pattern)

    def clear_subscriptions(self) -> int:
        """清空全部订阅，返回清除数量。"""
        with self._lock:
            n = len(self._subs)
            self._subs.clear()
            return n

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------
    def publish(
        self,
        name: str = "",
        payload: Any = None,
        priority: EventPriority = EventPriority.NORMAL,
        source: str = "",
        event: Event | None = None,
    ) -> int:
        """**同步**发布：在当前线程内立即回调所有匹配处理器。

        Args:
            name: 事件名（给了 event 时忽略）。
            payload: 事件负载。
            priority: 优先级。
            source: 事件来源标识（如器官名）。
            event: 直接传入 Event 对象（给定时其余参数忽略）。

        Returns:
            成功调用的处理器数量。
        """
        ev = event if event is not None else Event.create(
            name, payload=payload, priority=priority, source=source
        )
        t0 = time.perf_counter()
        self._record(ev)
        n = self._deliver(ev)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        with self._lock:
            self._published += 1
            self._delivered += n
            self._sync_ms_max = max(self._sync_ms_max, elapsed_ms)
        if elapsed_ms > _SYNC_LATENCY_WARN_MS:
            _logger.debug(
                "同步事件耗时偏高: %s %.2fms (%d 处理器)", ev.name, elapsed_ms, n
            )
        return n

    def publish_async(
        self,
        name: str,
        payload: Any = None,
        priority: EventPriority = EventPriority.NORMAL,
        source: str = "",
    ) -> bool:
        """**异步**发布：入优先级队列，由后台线程投递。

        异步能力未启用（async_enabled=False）时退化为同步发布，
        不创建线程、不改变可观测结果。

        Returns:
            True 表示已入队（或已同步投递）；队列满丢弃时返回 False。
        """
        ev = Event.create(name, payload=payload, priority=priority, source=source)
        self._record(ev)
        if not self._async_enabled:
            self._deliver(ev)
            with self._lock:
                self._published += 1
            return True
        self._ensure_worker()
        try:
            self._queue.put_nowait((int(ev.priority), next(self._seq), ev))
        except queue.Full:
            with self._lock:
                self._dropped += 1
            _logger.warning("事件队列已满，丢弃事件: %s", ev.name)
            return False
        return True

    def _deliver(self, event: Event) -> int:
        """把事件投递给全部匹配订阅（按优先级 → 注册时间 → sub_id 稳定排序）。"""
        with self._lock:
            matched = [s for s in self._subs.values() if _match(s.pattern, event.name)]
            matched.sort(key=lambda s: (int(s.priority), s.created_at, s.sub_id))
            if not matched:
                self._zero_match_events += 1
        delivered = 0
        for sub in matched:
            if sub.once:
                with self._lock:
                    # 仅当仍是同一条订阅时才移除（防重复退订踩踏）
                    if self._subs.get(sub.sub_id) is sub:
                        self._subs.pop(sub.sub_id, None)
            try:
                sub.handler(event)
                delivered += 1
                sub.invoked += 1
            except Exception as e:  # 单个处理器失败不得影响其余订阅
                with self._lock:
                    self._failed += 1
                    self._last_error = f"{type(e).__name__}: {e}"
                _logger.warning(
                    "事件处理器异常: pattern=%s event=%s handler=%s err=%s: %s",
                    sub.pattern, event.name, getattr(sub.handler, "__name__", "?"),
                    type(e).__name__, e,
                )
        return delivered

    # ------------------------------------------------------------------
    # 异步工作线程
    # ------------------------------------------------------------------
    def _ensure_worker(self) -> None:
        """惰性启动后台工作线程（幂等、线程安全）。"""
        if not self._async_enabled:
            return
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._worker_loop, name="EventBusWorker", daemon=True
            )
            self._worker.start()

    def _worker_loop(self) -> None:
        """后台投递循环：按优先级出队 → 投递；stop() 后退出。"""
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                _, _, ev = item
                n = self._deliver(ev)
                with self._lock:
                    self._published += 1
                    self._delivered += n
            finally:
                self._queue.task_done()

    def drain(self, timeout: float | None = 5.0) -> bool:
        """等待异步队列排空。

        Args:
            timeout: 最长等待秒数；None 表示无限等待。

        Returns:
            True 表示已排空，False 表示超时。
        """
        deadline = None if timeout is None else time.time() + float(timeout)
        while True:
            if self._queue.unfinished_tasks == 0:
                return True
            if deadline is not None and time.time() > deadline:
                return False
            time.sleep(0.005)

    def stop(self, timeout: float = 2.0) -> bool:
        """停止后台工作线程。

        Returns:
            True 表示线程已退出（或本就没有线程）。
        """
        with self._lock:
            worker = self._worker
        if worker is None:
            return True
        self._stop.set()
        worker.join(timeout=timeout)
        alive = worker.is_alive()
        if not alive:
            with self._lock:
                self._worker = None
        return not alive

    # ------------------------------------------------------------------
    # 事件溯源
    # ------------------------------------------------------------------
    def _record(self, event: Event) -> None:
        with self._lock:
            self._history.append(event)

    def get_history(
        self,
        limit: int | None = None,
        name: str | None = None,
        pattern: str | None = None,
        as_dict: bool = False,
    ) -> list[Any]:
        """读取事件历史（最近优先）。

        Args:
            limit: 最多返回条数（None = 全部）。
            name: 只返回该精确事件名。
            pattern: 只返回匹配该通配模式的（与 name 互斥，name 优先）。
            as_dict: True 时返回 `Event.to_dict()` 列表（可序列化）。

        Returns:
            最近优先的事件列表。
        """
        with self._lock:
            events = list(self._history)
        if name is not None:
            events = [e for e in events if e.name == name]
        elif pattern is not None:
            events = [e for e in events if _match(pattern, e.name)]
        events.reverse()
        if limit is not None and limit >= 0:
            events = events[: int(limit)]
        return [e.to_dict() for e in events] if as_dict else events

    def clear_history(self) -> int:
        """清空历史，返回清除条数。"""
        with self._lock:
            n = len(self._history)
            self._history.clear()
            return n

    @property
    def history_size(self) -> int:
        """当前历史条数。"""
        with self._lock:
            return len(self._history)

    @property
    def max_history(self) -> int:
        """历史上限。"""
        return self._max_history

    def export_history(self, path: str) -> int:
        """把历史导出为 JSON 文件（事件溯源离线分析）。

        Args:
            path: 目标路径（父目录需已存在）。

        Returns:
            导出的条数。
        """
        import json
        rows = self.get_history(as_dict=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2, default=str)
        return len(rows)

    def summary(self, limit: int = 20) -> dict[str, Any]:
        """事件溯源摘要（按事件名计数），供 LogAnalyzer 快速消费。"""
        with self._lock:
            events = list(self._history)
        counts: dict[str, int] = {}
        for e in events:
            counts[e.name] = counts.get(e.name, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[: int(limit)]
        return {
            "total": len(events),
            "distinct_names": len(counts),
            "top_names": [{"name": n, "count": c} for n, c in top],
            "latest": events[-1].to_dict() if events else None,
        }

    # ------------------------------------------------------------------
    # 自省
    # ------------------------------------------------------------------
    def get_stats(self) -> dict[str, Any]:
        """总线运行统计（供自省/诊断消费）。"""
        with self._lock:
            return {
                "async_enabled": self._async_enabled,
                "worker_alive": bool(self._worker and self._worker.is_alive()),
                "subscriptions": len(self._subs),
                "history_size": len(self._history),
                "max_history": self._max_history,
                "queue_size": self._queue.qsize(),
                "published": self._published,
                "delivered": self._delivered,
                "failed": self._failed,
                "dropped": self._dropped,
                "zero_match_events": self._zero_match_events,
                "sync_ms_max": round(self._sync_ms_max, 4),
                "last_error": self._last_error,
            }

    # 向后兼容别名（既有测试 test_event_bus_m14.py 仍调用 stats()）
    stats = get_stats

    def reset(self) -> None:
        """清空订阅、历史与统计（**不停止**后台线程）；供测试使用。"""
        self.stop()
        with self._lock:
            self._subs.clear()
            self._history.clear()
            self._published = 0
            self._delivered = 0
            self._failed = 0
            self._dropped = 0
            self._sync_ms_max = 0.0
            self._last_error = ""
            self._zero_match_events = 0

    # 支持 with 语法（退出时停线程）
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        self.stop()
        return False


# ======================================================================
# 单例
# ======================================================================
_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """返回全局事件总线单例。

    异步能力与历史上限由 config 决定：
        ENABLE_EVENT_BUS      总开关，**默认 False**（本批不接任何生产链路）
        EVENT_BUS_CONFIG.max_history / async_enabled

    ★该单例创建后不会随 config 热更新改变；如需切换请 reset_event_bus() 后重取。
    """
    global _bus
    if _bus is not None:
        return _bus
    with _bus_lock:
        if _bus is None:
            enabled = False
            max_history = 10000
            async_enabled = True
            try:
                import config
                enabled = bool(getattr(config, "ENABLE_EVENT_BUS", False))
                cfg = getattr(config, "EVENT_BUS_CONFIG", {}) or {}
                max_history = int(cfg.get("max_history", 10000))
                async_enabled = bool(cfg.get("async_enabled", True))
            except Exception as e:  # config 不可用时退化为默认值
                _logger.debug("读取事件总线配置失败，使用默认值: %s", e)
            _bus = EventBus(
                async_enabled=(enabled and async_enabled),
                max_history=max_history,
            )
    return _bus


def reset_event_bus() -> None:
    """销毁单例（测试隔离用），并停止其后台线程。"""
    global _bus
    with _bus_lock:
        if _bus is not None:
            try:
                _bus.stop()
            except Exception as e:  # 复位不应抛出
                _logger.debug("停止事件总线失败: %s", e)
        _bus = None
