# -*- coding: utf-8 -*-
"""
RuntimeTrajectory.py —— 运行轨迹

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架运行轨迹记录与回放
机制: 基于RuntimeTrajectory类实现，包含10个核心方法
定位: 运行时监测层
"""

from __future__ import annotations
from nucleus._silent_except import silent_exc

import json
import os
import threading
import time
from collections import deque
from typing import Any, ClassVar



class RuntimeTrajectory:
    """运行轨迹记录引擎。"""

    # 事件类型分类
    _EVENT_TYPES: ClassVar[set[str]] = {
        "conversation",      # 对话处理
        "reasoning",         # 推理决策
        "self_correction",   # 自我纠错
        "output_verification",  # 输出验证
        "knowledge_retrieval",  # 知识检索
        "organ_activity",    # 器官活动
        "pulse",             # 脉冲传播
        "error",             # 错误异常
        "state_change",      # 状态变化
        "learning",          # 学习/进化
        "system",            # 系统事件（启动/关闭/配置变更）
        "other",             # 其他
    }

    def __init__(self, max_records: int = 10000,
                 persist_interval: float = 300.0,
                 persist_dir: str | None = None,
                 enabled: bool = True) -> None:
        """
        Args:
            max_records: 内存环形缓冲最大记录数（压力均衡）
            persist_interval: 持久化间隔秒数（0=不持久化）
            persist_dir: 持久化目录（默认 data/runtime_trajectory）
            enabled: 开关
        """
        self._max_records = max(100, int(max_records))
        self._persist_interval = max(0.0, float(persist_interval))
        self._enabled = enabled
        self._lock = threading.RLock()

        # 内存环形缓冲
        self._records: deque[dict[str, Any]] = deque(maxlen=self._max_records)

        # 持久化
        _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._persist_dir = persist_dir or os.path.join(_project_root, "data", "runtime_trajectory")
        self._last_persist_time = 0.0
        self._persist_count = 0
        # 增量持久化指针：已写入 active 文件的记录数，下次只追加其后的新记录
        self._persisted_total = 0
        # 上次 compact 时间戳（每小时一次）
        self._last_compact_time = 0.0

        # 统计
        self._stats: dict[str, int] = {t: 0 for t in self._EVENT_TYPES}
        self._total_records = 0
        self._started_at = time.time()

    # ========== 核心：记录 ==========

    def record(self, event_type: str, source: str,
               content: str = "", result: str = "",
               reason: str = "", duration_ms: float = 0.0,
               metadata: dict[str, Any] | None = None) -> str | None:
        """记录一条运行轨迹。

        Args:
            event_type: 事件类型（conversation/reasoning/self_correction/...）
            source: 来源模块（如 PulseCortex/SelfCorrector）
            content: 内容摘要（建议≤200字符）
            result: 结果（success/failed/skipped/delegated 等）
            reason: 原因（为什么做这件事）
            duration_ms: 耗时毫秒
            metadata: 附加信息

        Returns:
            轨迹ID；禁用或节流时返回 None
        """
        if not self._enabled:
            return None

        # 标准化事件类型
        _etype = event_type if event_type in self._EVENT_TYPES else "other"

        _record = {
            "trajectory_id": f"trj_{int(time.time() * 1000)}_{self._total_records}",
            "timestamp": time.time(),
            "datetime": time.strftime('%Y-%m-%d %H:%M:%S'),
            "event_type": _etype,
            "source": source,
            "content": str(content)[:500],
            "result": str(result)[:100],
            "reason": str(reason)[:200],
            "duration_ms": round(float(duration_ms), 1),
            "metadata": metadata or {},
        }

        with self._lock:
            self._records.append(_record)
            self._total_records += 1
            self._stats[_etype] = self._stats.get(_etype, 0) + 1

        # 检查是否需要持久化
        if self._persist_interval > 0:
            _now = time.time()
            if (_now - self._last_persist_time) >= self._persist_interval:
                self._persist()

        return _record["trajectory_id"]

    # ========== 查询 ==========

    def query(self, event_type: str | None = None,
              source: str | None = None,
              keyword: str | None = None,
              start_time: float | None = None,
              end_time: float | None = None,
              limit: int = 100) -> list[dict[str, Any]]:
        """按条件查询运行轨迹。

        Args:
            event_type: 按事件类型过滤
            source: 按来源模块过滤
            keyword: 按内容/原因关键词过滤
            start_time: 起始时间戳
            end_time: 结束时间戳
            limit: 返回最大条数

        Returns:
            轨迹记录列表（按时间倒序）
        """
        with self._lock:
            _results = list(self._records)

        if event_type:
            _results = [r for r in _results if r["event_type"] == event_type]
        if source:
            _results = [r for r in _results if source in r["source"]]
        if keyword:
            _kw = keyword.lower()
            _results = [r for r in _results
                        if _kw in r["content"].lower() or _kw in r["reason"].lower()]
        if start_time:
            _results = [r for r in _results if r["timestamp"] >= start_time]
        if end_time:
            _results = [r for r in _results if r["timestamp"] <= end_time]

        # 按时间倒序
        _results.sort(key=lambda r: r["timestamp"], reverse=True)
        return _results[:max(1, int(limit))]

    def get_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近的运行轨迹。"""
        with self._lock:
            return list(self._records)[-max(1, int(limit)):]

    def get_stats(self) -> dict[str, Any]:
        """获取轨迹统计。"""
        with self._lock:
            _uptime = time.time() - self._started_at
            return {
                "enabled": self._enabled,
                "total_records": self._total_records,
                "buffer_size": len(self._records),
                "buffer_max": self._max_records,
                "uptime_seconds": round(_uptime, 1),
                "event_type_counts": dict(self._stats),
                "persist_count": self._persist_count,
                "records_per_minute": round(self._total_records / max(1, _uptime / 60), 2),
            }

    # ========== 持久化 ==========

    def _ensure_dir(self) -> None:
        """确保持久化目录存在（lazy 创建，仅启用时产生目录）。"""
        try:
            os.makedirs(self._persist_dir, exist_ok=True)
        except Exception:
            pass

    def load(self) -> int:
        """启动回读：从持久化目录加载历史轨迹，返回加载条数。

        仅当 enabled 时生效；按 trajectory_id 去重，受 max_records 环形限制。
        失败降级为空启动，不影响主流程。
        """
        if not self._enabled:
            return 0
        try:
            self._ensure_dir()
            if not os.path.isdir(self._persist_dir):
                return 0
            _loaded: list[dict[str, Any]] = []
            _seen: set[str] = set()
            _files = [
                os.path.join(self._persist_dir, fn)
                for fn in os.listdir(self._persist_dir)
                if fn.endswith(".jsonl")
            ]
            _files.sort()  # active 文件名较新，排在后面
            for _fp in _files:
                try:
                    with open(_fp, encoding="utf-8") as f:
                        for _line in f:
                            _line = _line.strip()
                            if not _line:
                                continue
                            try:
                                _rec = json.loads(_line)
                            except Exception as e:
                                silent_exc(e, "RuntimeTrajectory.py:229:load", level="warning")
                                continue
                            _tid = _rec.get("trajectory_id")
                            if _tid in _seen:
                                continue
                            _seen.add(_tid)
                            _loaded.append(_rec)
                except Exception as e:
                    silent_exc(e, "RuntimeTrajectory.py:236:load", level="warning")
                    continue
            # 按时间排序，截断到最近 max_records 条
            _loaded.sort(key=lambda r: r.get("timestamp", 0))
            if len(_loaded) > self._max_records:
                _loaded = _loaded[-self._max_records:]
            with self._lock:
                self._records = deque(_loaded, maxlen=self._max_records)
                self._total_records = len(_loaded)
                self._persisted_total = self._total_records
                for _r in _loaded:
                    _et = _r.get("event_type", "other")
                    self._stats[_et] = self._stats.get(_et, 0) + 1
            return len(_loaded)
        except Exception:
            return 0

    def _persist(self) -> None:
        """将内存缓冲增量追加到 active 文件（压力均衡）。

        仅追加 _persisted_total 之后的新记录；每次持久化后维护指针。
        持久化失败不影响主流程。
        """
        try:
            self._ensure_dir()
            _active = os.path.join(self._persist_dir, "trajectory_active.jsonl")
            with self._lock:
                _all = list(self._records)
                _new = _all[self._persisted_total:]
                if not _new:
                    self._last_persist_time = time.time()
                    return
                _snapshot_len = len(_all)
            with open(_active, "a", encoding="utf-8") as f:
                for r in _new:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            with self._lock:
                self._persisted_total = _snapshot_len
                self._last_persist_time = time.time()
                self._persist_count += 1
            self._maybe_compact()
        except Exception:
            pass  # 持久化失败不影响主流程

    def _maybe_compact(self) -> None:
        """每小时合并+去重+截断最近7天，写回 active（避免文件无限增长）。"""
        _now = time.time()
        if (_now - self._last_compact_time) < 3600.0:
            return
        try:
            self._last_compact_time = _now
            self._ensure_dir()
            _active = os.path.join(self._persist_dir, "trajectory_active.jsonl")
            _loaded: list[dict[str, Any]] = []
            _seen: set[str] = set()
            if os.path.isfile(_active):
                with open(_active, encoding="utf-8") as f:
                    for _line in f:
                        _line = _line.strip()
                        if not _line:
                            continue
                        try:
                            _rec = json.loads(_line)
                        except Exception:
                            continue
                        _tid = _rec.get("trajectory_id")
                        if _tid in _seen:
                            continue
                        _seen.add(_tid)
                        _loaded.append(_rec)
            # 截断最近 7 天
            _cutoff = _now - 7 * 86400.0
            _loaded = [r for r in _loaded if r.get("timestamp", 0) >= _cutoff]
            _loaded.sort(key=lambda r: r.get("timestamp", 0))
            with open(_active, "w", encoding="utf-8") as f:
                for r in _loaded:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            with self._lock:
                self._records = deque(_loaded[-self._max_records:], maxlen=self._max_records)
                self._persisted_total = len(_loaded)
        except Exception:
            pass

    def export(self, filepath: str,
               event_type: str | None = None,
               limit: int = 1000) -> str:
        """导出轨迹到文件。"""
        _records = self.query(event_type=event_type, limit=limit)
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            for r in _records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return filepath

    def clear(self) -> None:
        """清空内存缓冲（不清空持久化文件）。"""
        with self._lock:
            self._records.clear()


# ========== 便捷函数 ==========

_trajectory: RuntimeTrajectory | None = None


def get_runtime_trajectory() -> RuntimeTrajectory:
    """获取 RuntimeTrajectory 单例（首次构建时绑定灰度开关并 lazy-load 历史）。"""
    global _trajectory
    if _trajectory is None:
        _enabled = False
        try:
            import config as _cfg
            _enabled = getattr(_cfg, "ENABLE_RUNTIME_TRAJECTORY_PERSIST", False)
        except Exception:
            _enabled = False
        _trajectory = RuntimeTrajectory(enabled=_enabled)
        try:
            _trajectory.load()  # lazy-load 历史轨迹（启用时）；未启用则直接空启动
        except Exception:
            pass
    return _trajectory


def set_runtime_trajectory(trajectory: RuntimeTrajectory) -> None:
    """设置 RuntimeTrajectory 单例（用于依赖注入/测试）。"""
    global _trajectory
    _trajectory = trajectory


if __name__ == "__main__":
    # 自测
    trj = RuntimeTrajectory(max_records=100, persist_interval=0)
    # 记录一些事件
    trj.record("conversation", "PulseCortex", "用户提问：什么是脉冲架构", "success", "正常对话", 150.0)
    trj.record("reasoning", "PulseInnerWorld", "规则推理匹配", "success", "知识命中", 80.0)
    trj.record("self_correction", "SelfCorrector", "输出相关性低，调整检索参数", "success", "相关性0.3→0.7", 200.0)
    trj.record("error", "PatchManager", "JSON加载失败", "failed", "文件为空", 0.0)

    print(f"stats: {trj.get_stats()}")
    print("\n最近2条:")
    for r in trj.get_recent(2):
        print(f"  [{r['datetime']}] {r['event_type']}/{r['source']}: {r['content'][:40]} → {r['result']}")
    print("\n查询 self_correction:")
    for r in trj.query(event_type="self_correction"):
        print(f"  {r['content']} (原因: {r['reason']})")
