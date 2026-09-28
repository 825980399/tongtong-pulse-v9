# -*- coding: utf-8 -*-
"""
channel_health.py —— 渠道健康度

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: LLM渠道健康监测与熔断管理
机制: 基于ChannelHealthTracker类实现，包含9个核心方法
定位: LLM治理层
"""

from __future__ import annotations

import threading
import time


class ChannelHealthTracker:
    """渠道健康度跟踪器（内存态、线程安全）。"""

    def __init__(
        self,
        window_size: int = 10,
        circuit_break_threshold: int = 3,
        circuit_break_seconds: float = 300.0,
    ) -> None:
        self.window_size = window_size
        self.circuit_break_threshold = circuit_break_threshold
        self.circuit_break_seconds = circuit_break_seconds
        self._lock = threading.Lock()
        # name -> {"success": int, "fail": int, "consecutive_fail": int,
        #          "total_latency": float, "calls": int, "open_until": float}
        self._stats: dict[str, dict] = {}

    # ---------- 内部 ----------
    def _slot(self, name: str) -> dict:
        return self._stats.setdefault(
            name,
            {
                "success": 0,
                "fail": 0,
                "consecutive_fail": 0,
                "total_latency": 0.0,
                "calls": 0,
                "open_until": 0.0,
            },
        )

    # ---------- 写 ----------
    def record(self, name: str, success: bool, latency: float = 0.0) -> None:
        """记录一次调用结果并更新健康度。"""
        with self._lock:
            s = self._slot(name)
            s["calls"] += 1
            s["total_latency"] += max(0.0, float(latency or 0.0))
            if success:
                s["success"] += 1
                s["consecutive_fail"] = 0
                s["open_until"] = 0.0
            else:
                s["fail"] += 1
                s["consecutive_fail"] += 1
                if s["consecutive_fail"] >= self.circuit_break_threshold:
                    s["open_until"] = time.time() + self.circuit_break_seconds

    # ---------- 读 ----------
    def is_available(self, name: str) -> bool:
        """渠道当前是否可用（未熔断或熔断已到期）。"""
        with self._lock:
            s = self._stats.get(name)
            if not s:
                return True
            return time.time() >= s.get("open_until", 0.0)

    def success_rate(self, name: str) -> float:
        """成功率（无调用记录时返回 1.0，视为健康）。"""
        with self._lock:
            s = self._stats.get(name)
            if not s or s["calls"] == 0:
                return 1.0
            return s["success"] / s["calls"]

    def avg_latency(self, name: str) -> float:
        """平均延迟（秒）。"""
        with self._lock:
            s = self._stats.get(name)
            if not s or s["calls"] == 0:
                return 0.0
            return s["total_latency"] / s["calls"]

    def consecutive_fails(self, name: str) -> int:
        with self._lock:
            s = self._stats.get(name)
            return int(s["consecutive_fail"]) if s else 0

    def snapshot(self) -> dict:
        """全量健康度快照（日志 / 诊断用）。"""
        with self._lock:
            return {
                k: {
                    "success_rate": (v["success"] / v["calls"]) if v["calls"] else 1.0,
                    "avg_latency": (v["total_latency"] / v["calls"]) if v["calls"] else 0.0,
                    "consecutive_fail": v["consecutive_fail"],
                    "calls": v["calls"],
                    "available": time.time() >= v.get("open_until", 0.0),
                }
                for k, v in self._stats.items()
            }

    def reset(self, name: str | None = None) -> None:
        """清零指定渠道（或全部）健康度。"""
        with self._lock:
            if name is None:
                self._stats.clear()
            else:
                self._stats.pop(name, None)
