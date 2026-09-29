# -*- coding: utf-8 -*-
"""
probe_registry.py —— 探测器注册表

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 硬件探测器的注册与发现机制
机制: 基于Probe类实现，包含8个核心方法
定位: 硬件抽象层
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypedDict

from nucleus.probe_types import ProbeIssue  # noqa: F401



class Probe(TypedDict):
    """探查器契约（注册表内登记单元）。"""
    name: str                          # 探查器唯一名称（如 'tool_channel'）
    channel: str                       # 所属通道类别 tool/rule/llm/custom
    callable: Callable[..., Any]       # 探针函数：返回 issue 列表或 llm 报告 dict
    priority: int                      # 执行优先级（低值先执行）
    enabled: bool                      # 是否启用（False 则 run_all 跳过）
    takes_inspector: bool              # 是否接收 self_inspector 参数


class ProbeRegistry:
    """探查器注册表（模块级单例）。

    用法:
        _reg = get_probe_registry()
        _reg.register("my_probe", "custom", my_probe_fn, priority=10)
        _results = _reg.run_all(self_inspector)   # -> [(name, channel, result), ...]
    """

    def __init__(self) -> None:
        self._probes: dict[str, Probe] = {}
        self._lock = threading.Lock()

    def register(self, name: str, channel: str, fn: Callable[..., Any],
                 priority: int = 50, enabled: bool = True,
                 takes_inspector: bool = False) -> bool:
        """注册一个探查器。

        Args:
            name: 唯一名称（重复注册返回 False，拒绝覆盖）。
            channel: 通道类别（tool/rule/llm/custom）。
            fn: 探针可调用对象。
            priority: 执行优先级（数值越小越先执行）。
            enabled: 是否启用。
            takes_inspector: fn 是否接收 self_inspector 参数。

        Returns:
            True 注册成功；False 名称已存在或参数非法。
        """
        if not name or not callable(fn):
            return False
        with self._lock:
            if name in self._probes:
                return False
            self._probes[name] = {
                "name": name,
                "channel": channel,
                "callable": fn,
                "priority": priority,
                "enabled": enabled,
                "takes_inspector": takes_inspector,
            }
        return True

    def unregister(self, name: str) -> bool:
        """注销一个探查器。返回是否成功移除。"""
        with self._lock:
            if name in self._probes:
                del self._probes[name]
                return True
        return False

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """启用/停用一个探查器（不改动其余属性）。返回是否命中。"""
        with self._lock:
            if name in self._probes:
                self._probes[name]["enabled"] = bool(enabled)
                return True
        return False

    def list_probes(self) -> list[dict[str, Any]]:
        """返回当前登记探查器的只读快照（按 priority 排序）。"""
        with self._lock:
            _items = [
                {
                    "name": _p["name"],
                    "channel": _p["channel"],
                    "priority": _p["priority"],
                    "enabled": _p["enabled"],
                    "takes_inspector": _p["takes_inspector"],
                }
                for _p in self._probes.values()
            ]
        return sorted(_items, key=lambda x: x["priority"])

    def run_all(self, self_inspector=None) -> list[tuple[str, str, Any]]:
        """按 priority 顺序执行所有启用的探查器。

        Returns:
            [(name, channel, result), ...]，单个探查器异常不中断其余（隔离失败）。
        """
        # 一次性加锁取完整快照（含 priority），避免二次加锁竞态
        with self._lock:
            _snapshot = [
                (_p["name"], _p["channel"], _p["callable"], _p["takes_inspector"], _p["priority"])
                for _p in self._probes.values() if _p["enabled"]
            ]
        _ordered = sorted(_snapshot, key=lambda x: x[4])
        _results: list[tuple[str, str, Any]] = []
        for _name, _channel, _fn, _takes_inspector, _priority in _ordered:
            try:
                _out = _fn(self_inspector) if _takes_inspector else _fn()
                _results.append((_name, _channel, _out))
            except Exception:
                # 单探查器失败隔离：记录空结果，不中断其余
                _results.append((_name, _channel, []))
        return _results


# 模块级单例（双检锁）
_registry: ProbeRegistry | None = None
_registry_lock = threading.Lock()


def get_probe_registry() -> ProbeRegistry:
    """获取探查器注册表单例。"""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ProbeRegistry()
    return _registry


def reset_probe_registry() -> None:
    """★器官零状态：复位注册表单例（停机时调用，供下次重建全新实例）。"""
    global _registry
    _registry = None


__all__ = [
    "Probe",
    "ProbeRegistry",
    "get_probe_registry",
    "reset_probe_registry",
]
