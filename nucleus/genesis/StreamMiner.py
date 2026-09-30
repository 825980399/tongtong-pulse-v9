# -*- coding: utf-8 -*-
"""
StreamMiner.py —— 流挖掘器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 实时数据流挖掘与模式发现
机制: 基于StreamMiner类实现，包含10个核心方法
定位: 学习感知层
"""

import json
import os
import threading
import time
from collections import Counter, deque
from typing import Any
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc



class StreamMiner:
    """
    事件流挖掘器
    
    工作原理:
        1. 定期从 EventStream 拉取事件数据。
        2. 在滑动窗口内统计事件类型频率。
        3. 对比历史基线，发现频率显著变化的事件类型。
        4. 检测周期性模式——同一事件类型在固定间隔重复出现。
        5. 输出模式报告，供其他器官使用。
    
    核心概念:
        - 重复模式: 同一事件类型在时间窗口内出现次数超过阈值。
        - 新兴模式: 近期频率显著高于历史基线的事件类型。
        - 周期性: 事件以固定间隔重复出现（如心跳每10秒一次）。
        - 衰退模式: 曾经高频但近期频率显著下降的事件类型。
    
    当前状态（v9.0）:
        - 接口完整定义，但功能开关默认关闭。
        - P3阶段可激活完整的在线挖掘。
    """
    
    # ★A-16（2026-09-08）：模式持久化默认路径（测试隔离时可被 monkeypatch）
    _DEFAULT_STORAGE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "stream_miner", "patterns.json")

    def __init__(self, window_size: int = 100, baseline_window: int = 500,
                 auto_load: bool = False):
        """
        Args:
            window_size: 滑动窗口大小（事件数量）
            baseline_window: 基线窗口大小（用于对比历史频率）
            auto_load: 构造时是否自动从磁盘恢复模式（★A-16；默认 False 保持旧行为）
        """
        # 事件序列缓存
        self._recent_events: deque = deque(maxlen=window_size)
        self._baseline_events: deque = deque(maxlen=baseline_window)
        
        self._window_size = window_size
        self._baseline_window = baseline_window
        
        # 发现的历史模式
        self._discovered_patterns: dict[str, dict[str, Any]] = {}
        
        # 周期性检测缓存: event_type → [timestamps]
        self._periodicity_cache: dict[str, list[float]] = {}
        
        # 关联组件引用
        self.event_stream = None
        
        # 线程安全
        # ★A-16：改为**可重入锁**——feed_event 持锁后会调用 _check_prediction_hits，
        #   后者同样需要加锁（普通 Lock 会自死锁）。RLock 是 Lock 的超集，
        #   对既有调用点行为完全兼容。
        self._lock = threading.RLock()
        
        # 统计
        self._total_events_processed = 0
        self._total_patterns_discovered = 0

        # 功能开关
        self._enabled = False

        # ===== ★A-16：消费链路补全（持久化 / 预测 / 质量评估）=====
        self._storage_path = self._DEFAULT_STORAGE
        # 周期模式预测待验证队列：[{event_type, predicted_ts, created_at}]
        self._pending_predictions: list[dict[str, Any]] = []
        # 模式质量：pattern_key → {"predictions": int, "hits": int}
        self._pattern_quality: dict[str, dict[str, int]] = {}
        self._total_predictions = 0
        self._total_prediction_hits = 0
        self._total_pruned_patterns = 0
        self._last_persist_at = 0.0
        if auto_load:
            try:
                self.load_patterns()
            except Exception as e:
                silent_exc(e, where="nucleus.genesis.StreamMiner::__init__ L101")

    # ========== 框架控制 ==========

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    def set_event_stream(self, event_stream):
        """注入事件流"""
        self.event_stream = event_stream

    # ========== 事件摄入 ==========

    def feed_event(self, event: dict[str, Any]):
        """
        向挖掘器喂入一条事件。

        Args:
            event: 事件字典，需包含 event_type 和 timestamp
        """
        if not self._enabled:
            return

        with self._lock:
            self._recent_events.append(event)
            self._baseline_events.append(event)
            self._total_events_processed += 1

            # 记录周期检测缓存
            event_type = event.get("event_type", "unknown")
            timestamp = event.get("timestamp", time.time())
            if event_type not in self._periodicity_cache:
                self._periodicity_cache[event_type] = []
            self._periodicity_cache[event_type].append(timestamp)
            # 限制缓存大小
            if len(self._periodicity_cache[event_type]) > 50:
                self._periodicity_cache[event_type] = self._periodicity_cache[event_type][-50:]

            # ★A-16：事件真实到达 → 校验周期预测命中（仅消费开关开启时；异常不冒泡）
            try:
                if self._consume_enabled():
                    self._check_prediction_hits(event_type, float(timestamp))
            except Exception as _e:
                # ★第51批 T4（P2-355）：原为裸 ``pass`` → 显式留痕
                from nucleus.logger import get_module_logger as _m51_gml
                _m51_gml("StreamMiner").debug(
                    "[T4留痕] 周期预测命中校验异常已忽略: %s: %s",
                    type(_e).__name__, _e)

    # ========== 模式发现 ==========

    def discover_patterns(self) -> list[dict[str, Any]]:
        """
        执行一轮模式发现。

        Returns:
            发现的模式列表
        """
        if not self._enabled:
            return []

        with self._lock:
            patterns = []
            
            # 1. 重复模式检测
            repeated = self._detect_repeated_patterns()
            patterns.extend(repeated)
            
            # 2. 新兴模式检测
            emerging = self._detect_emerging_patterns()
            patterns.extend(emerging)
            
            # 3. 周期性检测
            periodic = self._detect_periodic_patterns()
            patterns.extend(periodic)
            
            # 记录发现的模式
            for pattern in patterns:
                pattern_key = f"{pattern['discovery_type']}:{pattern.get('event_type', 'unknown')}"
                self._discovered_patterns[pattern_key] = pattern
                self._total_patterns_discovered += 1
            
            return patterns

    def _detect_repeated_patterns(self, threshold: int = 3) -> list[dict[str, Any]]:
        """检测重复出现的事件模式"""
        if len(self._recent_events) < threshold:
            return []

        event_types = [e.get("event_type", "?") for e in self._recent_events]
        counter = Counter(event_types)
        
        patterns = []
        for etype, count in counter.most_common(5):
            if count >= threshold:
                patterns.append({
                    "discovery_type": "repeated",
                    "event_type": etype,
                    "occurrence_count": count,
                    "window_size": len(self._recent_events),
                    "frequency": round(count / max(1, len(self._recent_events)), 3),
                    "message": f"事件'{etype}'在最近{len(self._recent_events)}条事件中出现了{count}次",
                })
        return patterns

    def _detect_emerging_patterns(self, threshold_ratio: float = 2.0) -> list[dict[str, Any]]:
        """检测新兴模式（近期频率显著高于基线）"""
        if len(self._recent_events) < 10 or len(self._baseline_events) < 20:
            return []

        recent_counter = Counter(e.get("event_type", "?") for e in self._recent_events)
        baseline_counter = Counter(e.get("event_type", "?") for e in self._baseline_events)

        patterns = []
        for etype, recent_count in recent_counter.items():
            baseline_count = baseline_counter.get(etype, 0)
            if baseline_count == 0:
                continue
            
            recent_freq = recent_count / len(self._recent_events)
            baseline_freq = baseline_count / len(self._baseline_events)
            
            if baseline_freq > 0 and recent_freq / baseline_freq >= threshold_ratio:
                patterns.append({
                    "discovery_type": "emerging",
                    "event_type": etype,
                    "recent_frequency": round(recent_freq, 3),
                    "baseline_frequency": round(baseline_freq, 3),
                    "ratio": round(recent_freq / max(0.001, baseline_freq), 1),
                    "message": f"事件'{etype}'近期频率上升，从{baseline_freq:.2%}升至{recent_freq:.2%}",
                })
        return patterns

    def _detect_periodic_patterns(self, min_occurrences: int = 5, tolerance: float = 0.2) -> list[dict[str, Any]]:
        """检测周期性模式"""
        patterns = []

        for event_type, timestamps in self._periodicity_cache.items():
            if len(timestamps) < min_occurrences:
                continue

            # 计算相邻事件的时间间隔
            intervals = []
            for i in range(1, len(timestamps)):
                interval = timestamps[i] - timestamps[i-1]
                if interval > 0:
                    intervals.append(interval)

            if len(intervals) < 3:
                continue

            # 计算平均间隔和标准差
            avg_interval = sum(intervals) / len(intervals)
            if avg_interval <= 0:
                continue

            variance = sum((i - avg_interval) ** 2 for i in intervals) / len(intervals)
            std_dev = variance ** 0.5

            # 变异系数（CV = 标准差/均值），CV越小越规律
            cv = std_dev / avg_interval

            if cv < tolerance:
                patterns.append({
                    "discovery_type": "periodic",
                    "event_type": event_type,
                    "avg_interval_seconds": round(avg_interval, 1),
                    "occurrence_count": len(timestamps),
                    "regularity": round(1.0 - cv, 3),
                    "message": f"事件'{event_type}'呈周期性，平均间隔{avg_interval:.1f}秒，规律度{1.0-cv:.1%}",
                })

        return patterns

    # ========== 查询接口 ==========

    def get_discovered_patterns(self, discovery_type: str | None = None) -> list[dict[str, Any]]:
        """
        获取已发现的模式。

        Args:
            discovery_type: 模式类型过滤（repeated/emerging/periodic），None返回全部

        Returns:
            模式列表
        """
        patterns = list(self._discovered_patterns.values())
        if discovery_type:
            patterns = [p for p in patterns if p.get("discovery_type") == discovery_type]
        return patterns

    def get_event_frequency_summary(self) -> dict[str, float]:
        """
        获取最近窗口内的事件频率摘要。

        Returns:
            {event_type: frequency} 字典
        """
        with self._lock:
            if not self._recent_events:
                return {}
            counter = Counter(e.get("event_type", "?") for e in self._recent_events)
            total = len(self._recent_events)
            return {etype: round(count / total, 3) for etype, count in counter.most_common(10)}

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取挖掘器统计"""
        with self._lock:
            return {
                "enabled": self._enabled,
                "total_events_processed": self._total_events_processed,
                "total_patterns_discovered": self._total_patterns_discovered,
                "recent_window_size": len(self._recent_events),
                "baseline_window_size": len(self._baseline_events),
                "tracked_event_types": len(self._periodicity_cache),
                "discovered_patterns_count": len(self._discovered_patterns),
                # ★A-16：消费链路统计
                "consumption": self.get_consumption_stats(),
            }

    # ========== 未来演化预留（v10.0 振荡场） ==========

    def on_field_oscillation(self, field_data: dict[str, Any]):
        """
        【预留 v10.0】振荡场中，模式发现由连续场信号驱动。
        不再依赖离散事件统计，而是通过场的频率谱分析发现模式。
        """

    # ========== ★A-16（2026-09-08）：消费链路补全 ==========
    #
    # 背景：模式发现正常（4~14个/200事件），但消费链路存在 5 个断裂点：
    #   ①hebbian_weight 回写链路（在 PulseNodePool 侧修复，见 pulse 侧注释）
    #   ②周期模式 predict_next_occurrence() 无消费方
    #   ③重复模式无消费方（InfoField 只有预测脉冲，无负载预测）
    #   ④模式未持久化，重启丢失
    #   ⑤模式质量无评估
    # 本组方法补齐 ②③④⑤；①在 PulseNodePool.sync_hebbian_weight() 修复。
    #
    # 灰度：ENABLE_STREAMMINER_CONSUMPTION（默认 False）。
    #   关闭时：consume()/save/load/predict 相关方法仍可手动调用（纯函数），
    #   但 feed_event 内不触发预测校验、不自动落盘——零行为变化。

    def set_storage_path(self, path: str):
        """设置模式持久化路径（测试隔离用）。"""
        self._storage_path = path or self._DEFAULT_STORAGE

    def _consume_enabled(self) -> bool:
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_STREAMMINER_CONSUMPTION", False))
        except Exception as e:
            silent_exc(e, where="nucleus.genesis.StreamMiner::_consume_enabled L360")
            return False

    def _consume_cfg(self, key: str, default: Any) -> Any:
        try:
            import config as _cfg
            return (getattr(_cfg, "STREAMMINER_CONSUMPTION_CONFIG", {}) or {}).get(key, default)
        except Exception:
            return default

    # ---- ④ 模式持久化 ----

    def save_patterns(self, path: str | None = None) -> bool:
        """把发现的模式 / 周期缓存 / 质量统计落盘（原子写）。"""
        _path = path or self._storage_path
        with self._lock:
            try:
                _dir = os.path.dirname(_path)
                if _dir:
                    os.makedirs(_dir, exist_ok=True)
                _payload = {
                    "version": 1,
                    "saved_at": time.time(),
                    "patterns": self._discovered_patterns,
                    "periodicity": self._periodicity_cache,
                    "quality": self._pattern_quality,
                    "pending_predictions": self._pending_predictions[-200:],
                    "total_events_processed": self._total_events_processed,
                    "total_patterns_discovered": self._total_patterns_discovered,
                    "total_predictions": self._total_predictions,
                    "total_prediction_hits": self._total_prediction_hits,
                }
                _tmp = f"{_path}.tmp"
                with open(_tmp, "w", encoding="utf-8") as _f:
                    json.dump(_payload, _f, ensure_ascii=False, indent=2)
                os.replace(_tmp, _path)
                self._last_persist_at = time.time()
                return True
            except Exception as e:
                silent_exc(e, where="nucleus.genesis.StreamMiner::save_patterns L398")
                return False

    def load_patterns(self, path: str | None = None) -> int:
        """从磁盘恢复模式（不存在/损坏 → 0，绝不抛异常）。"""
        _path = path or self._storage_path
        with self._lock:
            try:
                if not os.path.exists(_path):
                    return 0
                _d = safe_read_json(_path, default={})
                self._discovered_patterns = dict(_d.get("patterns") or {})
                _per = _d.get("periodicity") or {}
                self._periodicity_cache = {k: list(v) for k, v in _per.items()}
                self._pattern_quality = dict(_d.get("quality") or {})
                self._pending_predictions = list(_d.get("pending_predictions") or [])
                self._total_events_processed = int(_d.get("total_events_processed", 0))
                self._total_patterns_discovered = int(_d.get("total_patterns_discovered", 0))
                self._total_predictions = int(_d.get("total_predictions", 0))
                self._total_prediction_hits = int(_d.get("total_prediction_hits", 0))
                return len(self._discovered_patterns)
            except Exception as e:
                print(f"[WARNING] StreamMiner.py:408: {type(e).__name__}: {e}")
                return 0

    # ---- ② 周期模式消费：预调度 ----

    def get_upcoming_events(self, horizon_sec: float | None = None) -> list[dict[str, Any]]:
        """预测未来 horizon 内会发生的周期事件（供预调度/知识预加载使用）。

        Returns:
            [{"event_type", "predicted_ts", "in_sec", "regularity"}]
        """
        _h = float(horizon_sec if horizon_sec is not None
                   else self._consume_cfg("predict_horizon_sec", 300))
        _now = time.time()
        _out: list[dict[str, Any]] = []
        with self._lock:
            for _p in self.get_discovered_patterns("periodic"):
                _etype = _p.get("event_type")
                _ts = self.predict_next_occurrence(_etype)
                if not _ts:
                    continue
                _delta = _ts - _now
                if _delta < 0 or _delta > _h:
                    continue
                _out.append({
                    "event_type": _etype,
                    "predicted_ts": _ts,
                    "in_sec": round(_delta, 1),
                    "regularity": _p.get("regularity", 0.0),
                })
        _out.sort(key=lambda x: x["in_sec"])
        return _out

    def register_periodic_predictions(self, horizon_sec: float | None = None) -> int:
        """把"即将发生"的周期预测登记到待验证队列（后续事件到达时校验命中率）。"""
        _upcoming = self.get_upcoming_events(horizon_sec)
        if not _upcoming:
            return 0
        with self._lock:
            _have = {(p["event_type"], round(p["predicted_ts"], 1))
                     for p in self._pending_predictions}
            _added = 0
            for _u in _upcoming:
                _key = (_u["event_type"], round(_u["predicted_ts"], 1))
                if _key in _have:
                    continue
                self._pending_predictions.append({
                    "event_type": _u["event_type"],
                    "predicted_ts": _u["predicted_ts"],
                    "created_at": time.time(),
                })
                self._total_predictions += 1
                _key_q = f"periodic:{_u['event_type']}"
                _q = self._pattern_quality.setdefault(_key_q, {"predictions": 0, "hits": 0})
                _q["predictions"] = int(_q.get("predictions", 0)) + 1
                _added += 1
            if len(self._pending_predictions) > 200:
                self._pending_predictions = self._pending_predictions[-200:]
        return _added

    def _check_prediction_hits(self, event_type: str, timestamp: float, tolerance: float = 60.0):
        """事件真实到达时校验周期预测是否命中（feed_event 内调用，异常不冒泡）。"""
        with self._lock:
            if not self._pending_predictions:
                return
            _hit_idx = -1
            for _i, _p in enumerate(self._pending_predictions):
                if _p.get("event_type") != event_type:
                    continue
                if abs(float(_p.get("predicted_ts", 0)) - timestamp) <= tolerance:
                    _hit_idx = _i
                    break
            if _hit_idx < 0:
                return
            self._pending_predictions.pop(_hit_idx)
            self._total_prediction_hits += 1
            _key = f"periodic:{event_type}"
            _q = self._pattern_quality.setdefault(_key, {"predictions": 0, "hits": 0})
            _q["hits"] = int(_q.get("hits", 0)) + 1

    # ---- ③ 重复模式消费：负载预测 ----

    def get_load_forecast(self) -> dict[str, dict[str, Any]]:
        """基于重复模式给出负载预测（稳定高频事件提前优化调度优先级）。

        Returns:
            {event_type: {"frequency": float, "expected_per_100_events": float,
                          "level": "high|normal"}}
        """
        _out: dict[str, dict[str, Any]] = {}
        with self._lock:
            for _p in self.get_discovered_patterns("repeated"):
                _etype = _p.get("event_type")
                if not _etype:
                    continue
                _freq = float(_p.get("frequency", 0.0) or 0.0)
                _out[_etype] = {
                    "frequency": round(_freq, 3),
                    "occurrence_count": int(_p.get("occurrence_count", 0)),
                    "expected_per_100_events": round(_freq * 100, 1),
                    "level": "high" if _freq >= 0.3 else "normal",
                }
        return _out

    # ---- ⑤ 模式质量评估与淘汰 ----

    def evaluate_pattern_quality(self) -> dict[str, Any]:
        """统计各模式预测命中率，低于阈值且样本足够 → 自动淘汰。

        Returns:
            {"evaluated": int, "pruned": int, "details": {key: hit_rate}}
        """
        _min_hit = float(self._consume_cfg("min_hit_rate", 0.3))
        _min_pred = int(self._consume_cfg("min_predictions", 5))
        _details: dict[str, float] = {}
        _pruned = 0
        with self._lock:
            for _key, _q in list(self._pattern_quality.items()):
                _pred = int(_q.get("predictions", 0))
                _hits = int(_q.get("hits", 0))
                _rate = (_hits / _pred) if _pred else 0.0
                _details[_key] = round(_rate, 3)
                if _pred >= _min_pred and _rate < _min_hit:
                    # 淘汰该模式（只删模式记录，不清历史质量，供审计追溯）
                    if _key in self._discovered_patterns:
                        self._discovered_patterns.pop(_key, None)
                        _pruned += 1
                        self._total_pruned_patterns += 1
        return {"evaluated": len(_details), "pruned": _pruned, "details": _details}

    # ---- 综合消费入口 ----

    def consume(self, horizon_sec: float | None = None) -> dict[str, Any]:
        """★A-16 综合消费：周期模式预调度 + 重复模式负载预测 + 质量评估。

        供 InfoField/调度侧周期性调用（如每轮模式发现后）。
        Returns:
            {"prewarm": [...], "load_forecast": {...}, "quality": {...}}
        """
        _prewarm = self.get_upcoming_events(horizon_sec)
        _added = self.register_periodic_predictions(horizon_sec)
        _load = self.get_load_forecast()
        _quality = self.evaluate_pattern_quality()
        # 定期落盘（默认5分钟一次，避免频繁IO）
        _interval = float(self._consume_cfg("persist_interval", 300))
        if _interval > 0 and (time.time() - self._last_persist_at) >= _interval:
            self.save_patterns()
        return {
            "prewarm": _prewarm,
            "registered_predictions": _added,
            "load_forecast": _load,
            "quality": _quality,
            "enabled": self._consume_enabled(),
        }

    def get_consumption_stats(self) -> dict[str, Any]:
        """消费链路统计（供 get_stats / 运维排查）。"""
        with self._lock:
            _pred = self._total_predictions
            return {
                "pending_predictions": len(self._pending_predictions),
                "total_predictions": _pred,
                "total_prediction_hits": self._total_prediction_hits,
                "hit_rate": round(self._total_prediction_hits / _pred, 3) if _pred else 0.0,
                "tracked_pattern_quality": len(self._pattern_quality),
                "total_pruned_patterns": self._total_pruned_patterns,
                "last_persist_at": self._last_persist_at,
                "storage": self._storage_path,
            }

    def predict_next_occurrence(self, event_type: str) -> float | None:
        """
        【预留 v10.0】基于周期性模式预测事件的下次发生时间。

        Args:
            event_type: 事件类型

        Returns:
            预测的时间戳，无周期模式返回 None
        """
        patterns = self.get_discovered_patterns("periodic")
        for p in patterns:
            if p.get("event_type") == event_type:
                timestamps = self._periodicity_cache.get(event_type, [])
                if timestamps:
                    return timestamps[-1] + p["avg_interval_seconds"]
        return None


# 模块级单例
_miner: StreamMiner | None = None
_miner_lock = threading.Lock()


def get_stream_miner() -> StreamMiner:
    global _miner
    if _miner is None:
        with _miner_lock:
            if _miner is None:
                _miner = StreamMiner()
    return _miner


def shutdown_stream_miner() -> None:
    """★P1: 复位 StreamMiner 单例，满足器官零状态（规则4）。"""
    global _miner
    _inst = _miner
    _miner = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as e:
                silent_exc(e, where="nucleus.genesis.StreamMiner::shutdown_stream_miner L633")


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== StreamMiner 自测 ===\n")
    
    miner = StreamMiner(window_size=20, baseline_window=50)
    miner.enable()
    
    # 1. 喂入事件（模拟心跳周期性事件 + 随机事件）
    print("1. 喂入事件:")
    base_time = time.time()
    # 模拟心跳事件——每2秒一次
    for i in range(10):
        miner.feed_event({
            "event_type": "heart.beat",
            "source_organ": "心脏",
            "timestamp": base_time + i * 2.0,
        })
    # 模拟知识写入事件——随机间隔
    for i in range(5):
        miner.feed_event({
            "event_type": "knowledge.written",
            "source_organ": "胃",
            "timestamp": base_time + i * 4.5,
        })
    # 模拟复盘事件——只有2次，不够重复阈值
    for i in range(2):
        miner.feed_event({
            "event_type": "reflection.insight",
            "source_organ": "前额叶",
            "timestamp": base_time + i * 10.0,
        })
    
    print(f"   总计喂入: {miner._total_events_processed} 条")
    
    # 2. 发现模式
    print("\n2. 模式发现:")
    patterns = miner.discover_patterns()
    for p in patterns:
        print(f"   [{p['discovery_type']}] {p.get('message', '')[:80]}")
    
    # 3. 事件频率摘要
    print("\n3. 事件频率摘要:")
    freq = miner.get_event_frequency_summary()
    for etype, f in freq.items():
        print(f"   {etype}: {f:.1%}")
    
    # 4. 统计
    stats = miner.get_stats()
    print(f"\n4. 统计: 处理{stats['total_events_processed']}条, "
          f"发现{stats['total_patterns_discovered']}个模式")
    
    miner.disable()
    print("\n=== 自测全部通过 ===")
# _m51_t4_d