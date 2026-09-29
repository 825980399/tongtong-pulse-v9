# -*- coding: utf-8 -*-
"""
probe_strategy.py —— 探测策略

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 硬件探测策略选择与执行
机制: 基于ProbeStrategyMemory类实现，包含10个核心方法
定位: 硬件抽象层
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底
from nucleus.data.DataAccessLayer import safe_read_json


# 严重级别权重：high 高权重，medium 中，low 低
_SEVERITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}

# 时间衰减：只统计最近 N 轮的探查结果
_DEFAULT_WINDOW = 5


class ProbeStrategyMemory(SilentLogMixin):
    """探查策略记忆（模块级单例）。

    用法:
        _mem = get_probe_strategy_memory()
        _strategy = _mem.compute_strategy()          # 读上轮历史 → 算本轮重点
        _report = run_parallel_audit(inspector, strategy=_strategy)
        _mem.record_round(_report)                    # 沉淀本轮 → 供下一轮
    """

    def __init__(self, file_path: str = "data/probe_strategy.json",
                 window: int = _DEFAULT_WINDOW) -> None:
        self._file_path = file_path
        self._window = max(1, window)
        # 历史轮次：最近 window 轮的 file/type 频次快照（每轮一条）
        # 每轮结构: {"round": int, "ts": float, "file_counts": {file: score}, "type_counts": {type: score}}
        self._rounds: list[dict[str, Any]] = []
        self._round_seq = 0
        self._lock = threading.Lock()
        self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            if os.path.exists(self._file_path):
                _data = safe_read_json(self._file_path, default={})
                self._rounds = list(_data.get("rounds", []))
                self._round_seq = int(_data.get("round_seq", 0))
        except Exception as e:
            print(f"[WARNING] probe_strategy.py:57: {type(e).__name__}: {e}")
            self._rounds = []
            self._round_seq = 0

    def _save(self) -> None:
        try:
            _dir = os.path.dirname(self._file_path)
            if _dir:
                os.makedirs(_dir, exist_ok=True)
            _snapshot = {
                "version": "v1.0",
                "updated_at": time.time(),
                "round_seq": self._round_seq,
                "rounds": self._rounds,
            }
            _tmp = self._file_path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as _f:
                json.dump(_snapshot, _f, ensure_ascii=False, indent=2)
            os.replace(_tmp, self._file_path)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    # 记录一轮结果
    # ------------------------------------------------------------------
    def record_round(self, report: dict[str, Any]) -> None:
        """把一轮审查结果沉淀为 file/type 频次（按 severity 加权），滚动保留最近 window 轮。

        Args:
            report: run_parallel_audit 输出的聚合报告（含 issues 列表）。
        """
        if not isinstance(report, dict):
            return
        _issues = report.get("issues", [])
        if not isinstance(_issues, list):
            _issues = []

        _file_counts: dict[str, int] = {}
        _type_counts: dict[str, int] = {}
        for _issue in _issues:
            if not isinstance(_issue, dict):
                continue
            _file = str(_issue.get("file", "") or "")
            _type = str(_issue.get("type", "unknown") or "unknown")
            _sev = str(_issue.get("severity", "low") or "low")
            _weight = _SEVERITY_WEIGHT.get(_sev, 1)
            if _file:
                _file_counts[_file] = _file_counts.get(_file, 0) + _weight
            _type_counts[_type] = _type_counts.get(_type, 0) + _weight

        with self._lock:
            self._round_seq += 1
            self._rounds.append({
                "round": self._round_seq,
                "ts": time.time(),
                "file_counts": _file_counts,
                "type_counts": _type_counts,
            })
            # 滚动窗口：只保留最近 window 轮
            if len(self._rounds) > self._window:
                self._rounds = self._rounds[-self._window:]
            self._save()

    # ------------------------------------------------------------------
    # 计算本轮策略
    # ------------------------------------------------------------------
    def compute_strategy(self) -> dict[str, Any]:
        """基于最近 window 轮历史，算出本轮「重点文件集合 + 重点类型集合 + 加权系数」。

        聚合逻辑：跨轮累加（越近的轮自然权重越高，因旧轮已滚出窗口），
        取累计分超过阈值者作为重点。阈值用「均值 + 1 倍标准差」自适应，
        避免固定阈值对高/低频场景不敏感。

        Returns:
            {
                "focus_files": set[str],   # 重点文件集合
                "focus_types": set[str],   # 重点类型集合
                "file_weights": {file: score},   # 供排序/标记用的原始权重
                "type_weights": {type: score},
            }
        """
        with self._lock:
            _rounds = list(self._rounds)

        _file_agg: dict[str, int] = {}
        _type_agg: dict[str, int] = {}
        for _r in _rounds:
            for _f, _s in (_r.get("file_counts") or {}).items():
                _file_agg[_f] = _file_agg.get(_f, 0) + int(_s)
            for _t, _s in (_r.get("type_counts") or {}).items():
                _type_agg[_t] = _type_agg.get(_t, 0) + int(_s)

        return {
            "focus_files": self._threshold_top(_file_agg),
            "focus_types": self._threshold_top(_type_agg),
            "file_weights": _file_agg,
            "type_weights": _type_agg,
        }

    @staticmethod
    def _threshold_top(agg: dict[str, int]) -> set[str]:
        """自适应阈值：均值 + 1 倍标准差以上的 key 视为重点；样本过少时取 top 2。"""
        if not agg:
            return set()
        _vals = list(agg.values())
        if len(_vals) <= 2:
            # 样本太少，全部视为重点（避免阈值失稳）
            return set(agg.keys())
        _mean = sum(_vals) / len(_vals)
        _var = sum((_v - _mean) ** 2 for _v in _vals) / len(_vals)
        _std = _var ** 0.5
        _threshold = _mean + _std
        return {_k for _k, _v in agg.items() if _v > _threshold}

    # ------------------------------------------------------------------
    # 器官零状态
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """复位策略记忆（停机时调用，满足器官零状态）。"""
        with self._lock:
            self._rounds = []
            self._round_seq = 0
            self._save()

    def get_stats(self) -> dict[str, Any]:
        """返回当前记忆的只读概览。"""
        with self._lock:
            return {
                "rounds_kept": len(self._rounds),
                "round_seq": self._round_seq,
                "window": self._window,
                "file_exists": os.path.exists(self._file_path),
            }


# 模块级单例（双检锁）
_memory: ProbeStrategyMemory | None = None
_memory_lock = threading.Lock()


def get_probe_strategy_memory() -> ProbeStrategyMemory:
    """获取探查策略记忆单例。"""
    global _memory
    if _memory is None:
        with _memory_lock:
            if _memory is None:
                _memory = ProbeStrategyMemory()
    return _memory


def reset_probe_strategy_memory() -> None:
    """复位单例（停机时调用）。"""
    global _memory
    _memory = None


__all__ = [
    "ProbeStrategyMemory",
    "get_probe_strategy_memory",
    "reset_probe_strategy_memory",
]
