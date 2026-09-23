# -*- coding: utf-8 -*-
"""
ResourceBudget.py —— 资源预算

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: CPU/内存/IO资源预算管理与配额控制
机制: 基于ResourceBudget类实现，包含10个核心方法
定位: 资源管理层
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, ClassVar



# ★superseded(2026-09-02): 本模块为早期「通用资源闸门」设计，能力已被
#   nucleus/parallel_scheduler.py 的「硬件自适应 + 双向平滑升降级 + CPU核物理约束」
#   完整取代（该实现更先进且已在主链路运行）。为避免双闸门冲突，**勿再接线本模块**，
#   保留仅作历史参考；如需资源预算查询，应使用 parallel_scheduler 的并行度/压力接口。
class ResourceBudget:
    """全局资源预算中枢（单例由 main.py 注入，器官通过 get_resource_budget 获取）。"""

    # 负载等级 → 允许的累计权重上限（高负载收紧预算）
    _BUDGET_BY_LOAD: ClassVar[dict[str, float]] = {
        "light": 100.0,    # 轻负载：预算充足
        "medium": 60.0,    # 中负载：收紧
        "heavy": 25.0,     # 高负载：大幅收紧
        "critical": 8.0,   # 临界：几乎不允许高耗能消费
    }

    def __init__(self, load_probe: Callable[[], str] | None = None,
                 budget_table: dict[str, float] | None = None) -> None:
        """
        Args:
            load_probe: 返回负载等级的可调用对象（如 lambda: infofield.get_load_level()）。
                        None 时默认返回 "light"（预算充足，不拦截）。
            budget_table: 自定义负载→预算上限表（覆盖默认）。
        """
        self._load_probe = load_probe or (lambda: "light")
        self._budget_table = dict(budget_table or self._BUDGET_BY_LOAD)
        self._lock = threading.Lock()
        self._consumers: dict[str, dict[str, Any]] = {}   # name → {"weight", "desc"}
        self._active: dict[str, int] = {}                 # name → 当前活跃数
        self._blocked: dict[str, int] = {}                # name → 累计被闸门拦截次数
        self._acquired: dict[str, int] = {}               # name → 累计获取次数
        self._created_at = time.time()
        self._logger = None  # ★v9.5日志注入点（main.py 装配时注入）

    # ========== 消费者注册 ==========

    def register_consumer(self, name: str, weight: float, desc: str = "") -> None:
        """注册一个资源消费者（高耗能操作）。weight 越大，高负载时越容易被闸门拦截。"""
        with self._lock:
            self._consumers[name] = {
                "weight": max(0.0, float(weight)),
                "desc": desc,
                "registered_at": time.time(),
            }
            self._active.setdefault(name, 0)
            self._blocked.setdefault(name, 0)
            self._acquired.setdefault(name, 0)

    def unregister_consumer(self, name: str) -> bool:
        with self._lock:
            if name not in self._consumers:
                return False
            del self._consumers[name]
            self._active.pop(name, None)
            return True

    # ========== 日志注入 ==========

    def set_logger(self, logger) -> None:
        """注入统一日志器（非器官模块用 get_module_logger）。零侵入：不注入则静默。"""
        self._logger = logger

    # ========== 预算闸门 ==========

    def acquire(self, name: str) -> bool:
        """
        尝试获取资源配额。返回 True 表示允许执行。
        高负载时，若当前活跃消费的累计权重已达预算上限，则拦截（返回 False）。
        """
        with self._lock:
            _info = self._consumers.get(name)
            if _info is None:
                # 未注册的消费者：默认允许（零侵入原则）
                return True
            _weight = _info["weight"]
            _level = self._load_probe() or "light"
            _limit = self._budget_table.get(_level, 60.0)
            _used = sum(
                self._consumers[n]["weight"] * self._active.get(n, 0)
                for n in self._consumers
            )
            # 本次执行是否超预算
            if _used + _weight > _limit and _weight > 0:
                self._blocked[name] = self._blocked.get(name, 0) + 1
                if self._logger is not None:
                    self._logger.warning(
                        "资源闸门拦截: 消费者[%s]权重%.1f 超预算(负载=%s 上限=%.1f 已用=%.1f 累计拦截%d)",
                        name, _weight, _level, _limit, _used,
                        self._blocked.get(name, 0))
                return False
            self._active[name] = self._active.get(name, 0) + 1
            self._acquired[name] = self._acquired.get(name, 0) + 1
            return True

    def release(self, name: str) -> None:
        """释放资源配额（与 acquire 成对调用）。"""
        with self._lock:
            self._active[name] = max(0, self._active.get(name, 0) - 1)

    # ========== 查询 ==========

    def is_high_load(self) -> bool:
        return (self._load_probe() or "light") in ("heavy", "critical")

    def get_current_load(self) -> str:
        return self._load_probe() or "light"

    # ========== 报告 ==========

    def get_budget_report(self) -> dict[str, Any]:
        """返回预算报告（各消费者占用/节流/累计，供健康面板与日志消费）。"""
        with self._lock:
            _consumers = {
                n: {
                    "weight": c["weight"],
                    "desc": c["desc"],
                    "active": self._active.get(n, 0),
                    "acquired": self._acquired.get(n, 0),
                    "blocked": self._blocked.get(n, 0),
                }
                for n, c in self._consumers.items()
            }
            return {
                "load_level": self._load_probe() or "light",
                "budget_limit": self._budget_table.get(self._load_probe() or "light"),
                "consumers": _consumers,
                "total_consumers": len(_consumers),
                "uptime_seconds": time.time() - self._created_at,
            }


# 全局单例（由 main.py 注入 load_probe 后使用）
_instance: ResourceBudget | None = None
_instance_lock = threading.Lock()


def get_resource_budget() -> ResourceBudget:
    """获取全局资源预算单例（懒初始化，默认 light 不拦截）。"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ResourceBudget()
    return _instance


def set_resource_budget(instance: ResourceBudget | None) -> None:
    """注入全局单例（main.py 装配时调用）。"""
    global _instance
    with _instance_lock:
        _instance = instance
