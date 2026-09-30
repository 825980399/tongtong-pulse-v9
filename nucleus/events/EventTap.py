# -*- coding: utf-8 -*-
"""
EventTap.py —— 事件总线旁路监听器（P2-63 试点 / PHASE18 铺垫）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 以 **只读旁路** 方式订阅事件总线的全部事件（`"**"`），做纯统计与留痕：
      按事件名/来源/优先级分组计数、总数、最近事件环形缓存、事件到达间隔统计，
      并支持按需导出 JSON 供 PHASE18 器官关联图谱使用。
机制: 单例 EventTap 在首次 `get_event_tap()` 时创建并以 `EventPriority.LOW`
      订阅 `"**"`（低优先级确保业务处理器先执行）；`_on_event` 只做加锁计数、
      **绝不执行 IO / 绝不调用任何器官方法 / 绝不修改任何状态**；统计字典设
      种类上限（超出归入 `__other__` 并告警一次），环形缓存有界，故无内存泄漏。
定位: 骨架夯实层——第14批建成 EventBus 后，"事件总线真正投入运行"的第一步。
      ★红线：旁路监听**不改变器官现有通信方式**（InfoField / 直接调用照旧），
      仅为后续事件驱动重构提供可观测地基。开关 `ENABLE_EVENT_BUS_TAP` 关闭时
      `_on_event` 直接返回，零统计开销。

开关（config）:
    ENABLE_EVENT_BUS_TAP      旁路监听总开关（默认 True）
    EVENT_TAP_HISTORY_LIMIT   环形缓存上限（默认 1000）
    EVENT_TAP_MAX_EVENT_TYPES 事件名种类上限（默认 500，超出归入 __other__）
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any

from nucleus.events.EventBus import EventPriority, get_event_bus
from nucleus.logger import get_module_logger

_logger = get_module_logger("EventTap")

#: 事件名种类溢出时的归并桶名
_OTHER_BUCKET = "__other__"


def _tap_enabled() -> bool:
    """读取 EventTap 总开关（默认 True）。config 不可用时按开启处理。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_EVENT_BUS_TAP", True))
    except Exception as e:  # 开关读取不得影响业务
        _logger.debug("读取 EventTap 开关失败，按开启处理: %s: %s", type(e).__name__, e)
        return True


def _payload_summary(payload: Any) -> str:
    """payload 的轻量摘要（类型 [+ 长度]），避免把大对象写入环形缓存。"""
    try:
        if payload is None:
            return "None"
        _t = type(payload).__name__
        if isinstance(payload, (str, bytes, bytearray, list, tuple, dict, set)):
            return "%s(len=%d)" % (_t, len(payload))  # noqa: UP031
        return _t
    except Exception as e:  # 摘要失败不得影响计数
        _logger.debug("payload 摘要失败: %s: %s", type(e).__name__, e)
        return "?"


class EventTap:
    """事件总线旁路监听器（只记录、不产副作用）。"""

    def __init__(self, history_limit: int = 1000, max_event_types: int = 500,
                 bus: Any = None, auto_start: bool = True) -> None:
        """
        Args:
            history_limit: 最近事件环形缓存上限（超出丢弃最旧）。
            max_event_types: 事件名种类上限；超出后新种类归入 `__other__`。
            bus: 指定总线（默认用 `get_event_bus()` 单例）。便于测试注入。
            auto_start: 构造后是否立即订阅 `"**"`（默认 True）。
                监听器的存在意义即为订阅，故默认自动；测试需要先配置时传 False。
        """
        self._lock = threading.Lock()
        self._history_limit = max(1, int(history_limit))
        self._max_event_types = max(1, int(max_event_types))
        self._recent: deque[dict[str, Any]] = deque(maxlen=self._history_limit)
        self._by_name: dict[str, int] = {}
        self._by_source: dict[str, int] = {}
        self._by_priority: dict[str, int] = {}
        self._total = 0
        self._filtered_off = 0
        self._overflow_warned = False
        self._overflow_types = 0
        self._last_ts = 0.0
        self._interval_min: float | None = None
        self._interval_max = 0.0
        self._interval_sum = 0.0
        self._interval_n = 0
        self._bus = bus
        self._sub_id: str | None = None
        self._started = False
        self._export_count = 0
        if auto_start:
            self.start()

    # ------------------------------------------------------------------
    # 订阅生命周期
    # ------------------------------------------------------------------
    def start(self) -> str | None:
        """以 LOW 优先级订阅 `"**"` 全部事件（幂等）。返回订阅 ID。"""
        if self._started:
            return self._sub_id
        _bus = self._bus if self._bus is not None else get_event_bus()
        self._sub_id = _bus.subscribe("**", self._on_event, priority=EventPriority.LOW)
        self._started = True
        return self._sub_id

    def stop(self) -> bool:
        """解除订阅（幂等）。返回是否确有订阅被移除。"""
        _bus = self._bus if self._bus is not None else get_event_bus()
        _removed = False
        if self._sub_id:
            try:
                _removed = bool(_bus.unsubscribe(self._sub_id))
            except Exception as e:  # 解订失败不应抛出
                _logger.debug("EventTap 解除订阅失败: %s: %s", type(e).__name__, e)
        self._sub_id = None
        self._started = False
        return _removed

    @property
    def started(self) -> bool:
        """是否已订阅。"""
        return self._started

    @property
    def sub_id(self) -> str | None:
        """当前订阅 ID。"""
        return self._sub_id

    # ------------------------------------------------------------------
    # 事件回调（只计数）
    # ------------------------------------------------------------------
    def _on_event(self, event: Any) -> None:
        """旁路回调：仅做轻量计数，绝不做 IO、绝不抛出。"""
        if not _tap_enabled():
            with self._lock:
                self._filtered_off += 1
            return
        try:
            self._count(event)
        except Exception as e:  # 监听器异常绝不影响发布方
            _logger.debug("EventTap 计数异常: %s: %s", type(e).__name__, e)

    def _count(self, event: Any) -> None:
        """执行一次加锁计数（统计口径的唯一实现）。"""
        _name = getattr(event, "name", "") or "?"
        _source = getattr(event, "source", "") or "?"
        _prio_obj = getattr(event, "priority", None)
        try:
            _prio = int(_prio_obj)
        except Exception:  # 非预期优先级退化为 NORMAL
            _prio = int(EventPriority.NORMAL)
        _prio_name = getattr(_prio_obj, "name", None) or str(_prio)
        _ts = float(getattr(event, "timestamp", 0.0) or 0.0)

        _warn_overflow = False
        with self._lock:
            self._total += 1

            # 事件名种类无界增长保护（__other__ 本身不占种类额度）
            if _name in self._by_name:
                _key = _name
            else:
                _used = len(self._by_name) - (1 if _OTHER_BUCKET in self._by_name else 0)
                if _used >= self._max_event_types:
                    _key = _OTHER_BUCKET
                    self._overflow_types += 1
                    if not self._overflow_warned:
                        self._overflow_warned = True
                        _warn_overflow = True
                else:
                    _key = _name
            self._by_name[_key] = self._by_name.get(_key, 0) + 1
            self._by_source[_source] = self._by_source.get(_source, 0) + 1
            self._by_priority[_prio_name] = self._by_priority.get(_prio_name, 0) + 1

            # 到达间隔统计
            if self._last_ts > 0 and _ts > 0:
                _gap = _ts - self._last_ts
                if _gap >= 0:
                    self._interval_sum += _gap
                    self._interval_n += 1
                    if self._interval_min is None or _gap < self._interval_min:
                        self._interval_min = _gap
                    self._interval_max = max(self._interval_max, _gap)
            if _ts > 0:
                self._last_ts = _ts

            self._recent.append({
                "name": _name,
                "source": _source,
                "priority": _prio,
                "priority_name": _prio_name,
                "timestamp": _ts,
                "payload": _payload_summary(getattr(event, "payload", None)),
            })

        if _warn_overflow:
            _logger.warning(
                "EventTap 事件种类已达上限 %d，新增种类归入 %s（后续统计按桶合并）",
                self._max_event_types, _OTHER_BUCKET)

    # ------------------------------------------------------------------
    # 统计读取 / 导出
    # ------------------------------------------------------------------
    def get_stats(self) -> dict[str, Any]:
        """返回当前统计快照（深拷贝，调用方可安全修改）。"""
        with self._lock:
            _avg = (self._interval_sum / self._interval_n) if self._interval_n else 0.0
            return {
                "enabled": _tap_enabled(),
                "started": self._started,
                "sub_id": self._sub_id,
                "total": self._total,
                "distinct_names": len([k for k in self._by_name if k != _OTHER_BUCKET]),
                "overflow_types": self._overflow_types,
                "max_event_types": self._max_event_types,
                "history_limit": self._history_limit,
                "recent_size": len(self._recent),
                "by_name": dict(self._by_name),
                "by_source": dict(self._by_source),
                "by_priority": dict(self._by_priority),
                "interval": {
                    "min": self._interval_min,
                    "max": self._interval_max,
                    "avg": _avg,
                    "samples": self._interval_n,
                },
                "filtered_off": self._filtered_off,
                "export_count": self._export_count,
            }

    def get_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """返回最近事件（时间升序）；`limit<0` 表示全部。"""
        with self._lock:
            _rows = list(self._recent)
        if limit is not None and limit >= 0:
            _rows = _rows[-int(limit):] if limit else []
        return _rows

    def reset_stats(self) -> None:
        """清空统计与环形缓存（保留订阅）。"""
        with self._lock:
            self._recent.clear()
            self._by_name.clear()
            self._by_source.clear()
            self._by_priority.clear()
            self._total = 0
            self._filtered_off = 0
            self._overflow_warned = False
            self._overflow_types = 0
            self._last_ts = 0.0
            self._interval_min = None
            self._interval_max = 0.0
            self._interval_sum = 0.0
            self._interval_n = 0

    def export_stats(self, path: str) -> int:
        """把统计 + 最近事件导出为 JSON（默认不自动导出，由调用方触发）。

        Args:
            path: 目标文件路径（父目录不存在时自动创建）。

        Returns:
            写入的最近事件条数。
        """
        _rows = self.get_recent(self._history_limit)
        _payload = {
            "stats": self.get_stats(),
            "recent": _rows,
            "exported_at": time.time(),
        }
        _dir = os.path.dirname(os.path.abspath(path))
        if _dir and not os.path.isdir(_dir):
            os.makedirs(_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_payload, f, ensure_ascii=False, indent=2, default=str)
        with self._lock:
            self._export_count += 1
        return len(_rows)


# ======================================================================
# 单例
# ======================================================================
_tap: EventTap | None = None
_tap_lock = threading.Lock()


def get_event_tap() -> EventTap:
    """返回全局 EventTap 单例（首次调用即创建并订阅 `"**"`）。

    配置来自 config：
        ENABLE_EVENT_BUS_TAP     总开关（默认 True）
        EVENT_TAP_HISTORY_LIMIT  环形缓存上限（默认 1000）
        EVENT_TAP_MAX_EVENT_TYPES 事件名种类上限（默认 500）

    ★该单例创建后不随 config 热更新改变规模；如需切换请 reset_event_tap()。
    """
    global _tap
    if _tap is not None:
        return _tap
    with _tap_lock:
        if _tap is None:
            _history_limit = 1000
            _max_types = 500
            try:
                import config
                _history_limit = int(getattr(config, "EVENT_TAP_HISTORY_LIMIT", 1000))
                _max_types = int(getattr(config, "EVENT_TAP_MAX_EVENT_TYPES", 500))
            except Exception as e:  # config 不可用时用默认值
                _logger.debug("读取 EventTap 配置失败，使用默认值: %s: %s",
                              type(e).__name__, e)
            _tap = EventTap(history_limit=_history_limit, max_event_types=_max_types)
            _tap.start()
    return _tap


def reset_event_tap() -> None:
    """销毁单例（测试隔离用），并解除其订阅。"""
    global _tap
    with _tap_lock:
        if _tap is not None:
            try:
                _tap.stop()
            except Exception as e:  # 复位不应抛出
                _logger.debug("停止 EventTap 失败: %s: %s", type(e).__name__, e)
        _tap = None


# ======================================================================
# 器官侧发布便捷入口
# ======================================================================
def tap_publish(name: str, payload: Any = None, source: str = "",
                switch_attr: str = "", priority: EventPriority = EventPriority.LOW
                ) -> bool:
    """旁路事件发布便捷入口（器官统一调用点）。

    设计目标：把「开关判定 + 异常兜底 + 发布」收敛为一行，
    使器官侧插入点保持**最小侵入**（每处 1 行，无 try/except 展开）。

    Args:
        name: 事件名，规范 `器官名.动作.阶段`（如 `cortex.dialog.start`）。
        payload: 轻量可序列化负载（字符串/数字/布尔/字典/列表）。
        source: 事件来源标识（器官名）。
        switch_attr: 该器官的独立开关名（如 `ENABLE_CORTEX_EVENT_TAP`）；
            空串表示只受总开关约束。
        priority: 发布优先级，默认 LOW（旁路不抢占业务）。

    Returns:
        True 表示已发布；False 表示被开关拦截或发布异常（**均不影响业务**）。
    """
    try:
        import config
        if not bool(getattr(config, "ENABLE_EVENT_BUS_TAP", True)):
            return False
        if switch_attr and not bool(getattr(config, switch_attr, True)):
            return False
    except Exception as e:  # 开关不可判读时保守跳过
        _logger.debug("读取旁路开关失败，跳过发布 %s: %s: %s",
                      name, type(e).__name__, e)
        return False
    try:
        get_event_bus().publish(name, payload=payload, source=source,
                                priority=priority)
        return True
    except Exception as e:  # 发布异常绝不影响业务
        _logger.debug("旁路发布失败 %s: %s: %s", name, type(e).__name__, e)
        return False
