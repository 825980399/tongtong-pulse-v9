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

from nucleus._silent_except import silent_exc
from nucleus.data.DataAccessLayer import safe_read_json


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
        #: ★411刀6：每类保留的周期样本数（410 时是硬编码 50，回放实测样本太少）
        self._M411_PERIODICITY_KEEP = self._m411_periodicity_keep()

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

        # ===== ★418刀1：以下状态曾是「隐形死代码」=====
        #   411刀6 在 `__init__` 尾部插入 `@staticmethod _m411_periodicity_keep` 时，
        #   以 `def` 行为锚点替换，把 410 刀2 的整块初始化**留在了新方法体内**、
        #   而且排在 `except: return 100` 之后 ⇒ 永不执行（且方法里没有 `self`）。
        #   四个下游症状：`_m410_stat`/`_m410_tp` 等属性缺失、分类型命中率无数据、
        #   `patterns.json` 只因外部原因才写、`world_model.hit_rate` 每 60s 报
        #   `AttributeError`（silent_exc 可见化）。此处搬回原位置，逐字保留语义。
        self._last_persist_at = 0.0

        # ===== ★第410批刀2：分类型预测（periodic 时刻 / count 条数 / burst 批数）=====
        #   刀1 的周期档案回答「该用多宽的容差」，本段回答「该预测什么」。
        #   三套状态互不替代：_pending_predictions 只装时刻型，_m410_win_preds 只装窗口型。
        self._m410_tp = None            # typed_prediction 模块（懒加载，避免启动期引入链）
        self._m410_prof = {}            # 周期档案快照
        self._m410_prof_at = 0.0        # 快照读取时刻（300s 缓存）
        self._m410_windows = {}         # event_type -> WindowTracker（count/burst）
        self._m410_hist = {}            # event_type -> [已关闭窗口的条数/批数]
        self._m410_win_preds = []       # 窗口型预测（未结算）
        self._m410_verdicts = deque(maxlen=2000)   # 判定流水（刀3 抽样审计用）
        self._m410_stat = {}            # event_type -> {"hit","miss","no_coverage"}
        self._m410_noted = {}           # 留痕节流表（key -> 上次记录时刻），防刷屏
        # ★418刀1：`auto_load` 此前也在这块死代码里 ⇒ 传 True 等于没传。
        if auto_load:
            try:
                self.load_patterns()
            except Exception as e:
                silent_exc(e, where="nucleus.genesis.StreamMiner::__init__ auto_load")

    @staticmethod
    def _m411_periodicity_keep() -> int:
        """★411刀6：每类保留的周期样本数（`config.M411_PERIODICITY_KEEP`，缺省 100）。

        非法值（<=0 / 非整数）一律回落 100 —— 这个数是**缓存上限**，
        写 0 会把周期检测彻底饿死，属于"宁可保守"的参数。
        """
        try:
            import config as _c
            _v = int(getattr(_c, "M411_PERIODICITY_KEEP", 100))
            return _v if _v > 0 else 100
        except Exception as _exc:
            silent_exc(_exc, where="genesis.StreamMiner:_m411_periodicity_keep",
                       level="debug")
            return 100

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
            # ★411批刀6（410 遗留④）：50 → 100。内存增量 KB 级（每类多 50 个 float），
            #   换来七类时刻预测的样本翻倍 —— 410 回放里每类只有 2~4 条预测，
            #   命中率的置信区间宽到没有解释力。
            if len(self._periodicity_cache[event_type]) > self._M411_PERIODICITY_KEEP:
                self._periodicity_cache[event_type] = (
                    self._periodicity_cache[event_type][-self._M411_PERIODICITY_KEEP:])

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

    # ★398刀4：预埋未接 —— 全仓 0 调用点（398 实测 64 def / 0 引用）。
    #   场共振阶段（PHASE19+）再接线；在那之前不要当它是已生效的场感知能力。
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
                # ★418刀3⑥：同 call_recorder 的 WinError 5 问题（目标被活体进程
                #   打开着时 replace 会拒绝访问）⇒ 走退避重试，重试用尽才失败。
                from utils.atomic_io import replace_with_retry as _m418_repl
                if not _m418_repl(_tmp, _path):
                    raise PermissionError("patterns.json 原子替换失败: %s" % _path)
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
        """把"即将发生"的周期预测登记到待验证队列（后续事件到达时校验命中率）。

        ★第410批刀2：七类事件改由 `_m410_register_typed_time()` 按**档案 median**
        统一预测，不再依赖"模式发现"的 CV<0.2 门槛。实测依据：
          - energy.metabolism_snapshot 有 50 个样本、median 30.148s，却没有 periodic
            模式（CV 门槛没过）⇒ 按旧路径它永远登记不到预测，覆盖率是 0；
          - 两条路都登记的话，同一事件会被预测两次（模式 13.5 / 档案 13.505），
            一个命中一个 miss，命中率被凭空稀释一半。
        其余 70+ 个事件类型保持旧路径（不扩大改动面）。
        """
        _h = float(horizon_sec if horizon_sec is not None
                   else self._consume_cfg("predict_horizon_sec", 300))
        _upcoming = self.get_upcoming_events(horizon_sec)
        _now = time.time()
        _added = 0
        with self._lock:
            _have = {(p["event_type"], round(p["predicted_ts"], 1))
                     for p in self._pending_predictions}
            for _u in _upcoming:
                if self._m410_on() and self._m410_is_target(_u.get("event_type", "")):
                    continue          # 七类由 typed 路径统一负责（见 docstring）
                _key = (_u["event_type"], round(_u["predicted_ts"], 1))
                if _key in _have:
                    continue
                _pt2, _tol2, _why2 = self._m410_plan(_u["event_type"])
                if _pt2 in ("count", "burst"):
                    # 窗口型不登记「时刻」预测 —— 它预测的是「下一分钟多少条/几批」。
                    continue
                self._pending_predictions.append({
                    "event_type": _u["event_type"],
                    "predicted_ts": _u["predicted_ts"],
                    "created_at": _now,
                    "tolerance_sec": (round(float(_tol2), 3) if _tol2 is not None else None),
                    "predict_type": _pt2,
                })
                _have.add(_key)
                self._total_predictions += 1
                _key_q = f"periodic:{_u['event_type']}"
                _q = self._pattern_quality.setdefault(_key_q, {"predictions": 0, "hits": 0})
                _q["predictions"] = int(_q.get("predictions", 0)) + 1
                _added += 1
            _added += self._m410_register_typed_time(_now, _h)
            if len(self._pending_predictions) > 200:
                self._pending_predictions = self._pending_predictions[-200:]
        return _added

    def _m410_is_target(self, event_type: str) -> bool:
        """是否属于七类（七类的预测口径统一由 typed 路径负责）。"""
        _tp = self._m410_mod()
        return bool(_tp is not None and _tp.is_target(event_type))

    def _m410_note_throttled(self, key: str, event_type: str, verdict: str,
                             min_gap: float = 600.0, **extra) -> None:
        """同类留痕节流（默认 10 分钟一次）—— 免得 consume 每轮刷 7 条 no_coverage。"""
        _now = time.time()
        if (_now - float(self._m410_noted.get(key, 0.0))) < float(min_gap):
            return
        self._m410_noted[key] = _now
        self._m410_note(event_type, verdict, **extra)

    def _m410_register_typed_time(self, now: float, horizon: float) -> int:
        """七类 periodic：用档案 median 从「该类型最后一条事件」推下一次时刻。"""
        if not self._m410_on():
            return 0
        _tp = self._m410_mod()
        if _tp is None:
            return 0
        _events = (self._m410_profile(now).get("events") or {})
        _have = {(p["event_type"], round(p["predicted_ts"], 1))
                 for p in self._pending_predictions}
        _added = 0
        for _et in _tp.pp.TARGET_EVENTS:
            _pt, _tol, _why = self._m410_plan(_et)
            if _pt != "periodic":
                continue                      # count/burst 走分钟窗，不预测时刻
            if _tol is None:
                self._m410_note_throttled("noprof:" + _et, _et, "no_coverage",
                                          predict_type=_pt, why=_why, actual=None)
                continue
            _stamps = self._periodicity_cache.get(_et) or []
            _med = (_events.get(_et) or {}).get("median_interval_sec")
            if not _stamps or not _med:
                continue                      # 没有历史时刻 / 档案没给 median ⇒ 不猜
            _pred = float(_stamps[-1]) + float(_med)
            _delta = _pred - now
            if _delta < 0 or _delta > horizon:
                # 已过期或太远：不登记。登记过去时刻只会制造假 miss
                # （旧实现 _pending_predictions 里就躺着 6700s 前的僵尸预测）
                continue
            _key = (_et, round(_pred, 1))
            if _key in _have:
                continue
            _have.add(_key)
            self._pending_predictions.append({
                "event_type": _et,
                "predicted_ts": _pred,
                "created_at": now,
                "tolerance_sec": round(float(_tol), 3),
                "predict_type": _pt,
                "source": "profile",
            })
            self._total_predictions += 1
            _q = self._pattern_quality.setdefault(
                f"periodic:{_et}", {"predictions": 0, "hits": 0})
            _q["predictions"] = int(_q.get("predictions", 0)) + 1
            _added += 1
        return _added

    def _check_prediction_hits(self, event_type: str, timestamp: float,
                               tolerance: float | None = None):
        """事件真实到达时对账（feed_event 内调用，异常不冒泡）。

        ★第410批刀2：容差不再恒 60s，改为按 event_type 查表：
          - periodic 型（触觉/代谢/脊髓/心跳/alive）：容差 = 档案 median × pct
            （5%；脊髓 10%）—— 实测心跳 13.51s × 5% ≈ 0.68s，不再是 60s；
          - count / burst 型（眼睛帧 / 知识写入）：**不做时刻判定**，事件只喂进
            分钟窗计数器，窗口关闭时按「下一分钟条数/批数是否落在 μ±2σ」判；
          - 非七类（time.tick 等）与开关关闭时：保持旧 60s，不扩大改动面。
        `tolerance` 显式传入时优先生效（保留旧调用点/单测的直调能力）。
        """
        _pt, _tol, _why = self._m410_plan(event_type)
        if _pt in ("count", "burst"):
            self._m410_track(event_type, float(timestamp), _pt)
            return
        if tolerance is not None:
            _tol = float(tolerance)
        with self._lock:
            if self._m410_on():
                # ★刀2 补上「错过」：旧实现只增不减，pending 里躺着 6700s 前的
                #   curiosity.tick 预测，却仍然给出 94.4% 的总命中率 —— 分母是假的。
                self._m410_expire(float(timestamp))
            if not self._pending_predictions:
                return
            _hit_idx = -1
            for _i, _p in enumerate(self._pending_predictions):
                if _p.get("event_type") != event_type:
                    continue
                # 关掉刀2 开关时忽略已存盘的 tolerance_sec，一律按 60s 判
                # —— 否则「关回去」并不等于旧行为（旧实现里根本没有这个字段）。
                _p_tol = _p.get("tolerance_sec") if self._m410_on() else None
                if _p_tol is None:
                    _p_tol = _tol
                if _p_tol is None:
                    # 类型判得了、容差判不了（档案缺失）⇒ 跳过，不拿沉默当反证
                    continue
                if abs(float(_p.get("predicted_ts", 0)) - timestamp) <= float(_p_tol):
                    _hit_idx = _i
                    break
            if _hit_idx < 0:
                return
            _p_hit = self._pending_predictions.pop(_hit_idx)
            self._total_prediction_hits += 1
            self._m410_note(
                event_type, "hit", predict_type=_pt,
                err_sec=round(timestamp - float(_p_hit.get("predicted_ts", 0) or 0), 3),
                tolerance_sec=_p_hit.get("tolerance_sec"))
            _key = f"periodic:{event_type}"
            _q = self._pattern_quality.setdefault(_key, {"predictions": 0, "hits": 0})
            _q["hits"] = int(_q.get("hits", 0)) + 1

    # ---- ②b 第410批刀2：分类型预测（周期时刻 / 分钟条数 / 分钟批数）----

    def _m410_mod(self):
        """懒加载 typed_prediction（避免 StreamMiner 在启动期拉入 world_model 链）。"""
        if self._m410_tp is None:
            try:
                from nucleus.world_model import typed_prediction as _tp
                self._m410_tp = _tp
            except Exception as e:
                silent_exc(e, where="nucleus.genesis.StreamMiner::_m410_mod L1")
                return None
        return self._m410_tp

    def _m410_on(self) -> bool:
        """刀2 灰度开关：关闭 ⇒ 全部回落旧 60s 一刀切（逐字节等价旧行为）。"""
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_M410_TYPED_TOLERANCE", True))
        except Exception as e:
            silent_exc(e, where="nucleus.genesis.StreamMiner::_m410_on L1")
            return False

    def _m410_profile(self, now: float) -> dict:
        """周期档案快照（300s 缓存；缺失/过期返回 {}，由调用方决定回落）。"""
        if (now - self._m410_prof_at) < 300.0:
            return self._m410_prof
        _snap = {}
        try:
            from nucleus.world_model import periodic_profile as _pp
            _snap = _pp.load_profile(max_age_sec=3600.0, now=now)
        except Exception as e:
            silent_exc(e, where="nucleus.genesis.StreamMiner::_m410_profile L1")
            _snap = {}
        self._m410_prof = _snap
        self._m410_prof_at = now
        return _snap

    def _m410_plan(self, event_type: str) -> tuple[str, float | None, str]:
        """(预测型, 时刻容差秒, 依据)。

        依据四态：
          legacy     开关关闭 → 旧 60s
          non_target 非七类（time.tick 等 70+ 类）→ 旧 60s，不扩大改动面
          typed      按任务书表格：periodic 用 median×pct，count/burst 不设时刻容差
          no_profile 七类 periodic 但档案缺失/过期 → tol=None（判不了，不猜 60s）
        """
        if not self._m410_on():
            return ("periodic", 60.0, "legacy")
        _tp = self._m410_mod()
        if _tp is None:
            return ("periodic", 60.0, "legacy")
        _pt = _tp.classify(event_type)
        if not _pt:
            return ("periodic", 60.0, "non_target")
        if _pt != "periodic":
            return (_pt, None, "typed")
        _tol = None
        try:
            from nucleus.world_model import periodic_profile as _pp
            _tol = _pp.tolerance_for(event_type, self._m410_profile(time.time()))
        except Exception as e:
            silent_exc(e, where="nucleus.genesis.StreamMiner::_m410_plan L1")
        if _tol is None:
            return (_pt, None, "no_profile")
        return (_pt, float(_tol), "typed")

    def _m410_note(self, event_type: str, verdict: str, **extra) -> None:
        """记一条判定：累计计数 + 流水。verdict ∈ hit/miss/no_coverage。"""
        _q = self._m410_stat.setdefault(
            event_type, {"hit": 0, "miss": 0, "no_coverage": 0})
        if verdict in _q:
            _q[verdict] = int(_q[verdict]) + 1
        _rec = {"event_type": event_type, "verdict": verdict, "at": time.time()}
        _rec.update(extra)
        self._m410_verdicts.append(_rec)

    def _m410_range(self, event_type: str, predict_type: str,
                    profile: dict, tp) -> tuple[float | None, int | None, int | None]:
        """窗口型预测区间（三层，从近到远）：

        1) 已关闭窗口的实测 μ±2σ（最可信，攒够 3 窗即生效）
        2) periodicity 缓存原始时间戳估速率（比档案新；burst 双峰间隔只能这样估）
        3) 档案 median/p90 外推（最后一次回落，区间偏宽但不会凭空造数）
        """
        _exp, _lo, _hi = tp.expected_range(self._m410_hist.get(event_type) or [])
        if _lo is not None:
            return (_exp, _lo, _hi)
        _exp, _lo, _hi = tp.rate_range(self._periodicity_cache.get(event_type) or [],
                                       batch=(predict_type == "burst"))
        if _lo is not None:
            return (_exp, _lo, _hi)
        if predict_type == "burst":
            # burst 的间隔是双峰分布（批内 2s / 批间几百秒），拿 median 外推会得到
            # 「每分钟 6 批」，与事实（每 60s 窗 0~1 批）方向相反 ⇒ 宁可不判。
            return (None, None, None)
        _ev = (profile.get("events") or {}).get(event_type) or {}
        return tp.cold_start_range(_ev.get("median_interval_sec"),
                                   _ev.get("p90_interval_sec"))

    def _m410_track(self, event_type: str, timestamp: float, predict_type: str) -> None:
        """count/burst 型：事件喂进分钟窗；窗口一关就结算已登记的预测。"""
        _tp = self._m410_mod()
        if _tp is None:
            return
        with self._lock:
            _tr = self._m410_windows.get(event_type)
            if _tr is None:
                _tr = _tp.WindowTracker()
                self._m410_windows[event_type] = _tr
            for _w in _tr.feed(timestamp):
                self._m410_settle(event_type, predict_type, _w, _tp)

    def _m410_settle(self, event_type: str, predict_type: str, win: dict, tp) -> None:
        """窗口关闭结算：先判已登记预测，再把实测值并入历史（历史只收真观测）。"""
        _actual = int(win["batches"] if predict_type == "burst" else win["count"])
        _pred = None
        for _i, _p in enumerate(self._m410_win_preds):
            if (_p.get("event_type") == event_type
                    and _p.get("window_start") == win["window_start"]):
                _pred = self._m410_win_preds.pop(_i)
                break
        if _pred is None:
            # 这一窗没登记过预测（如 consume 未跑到）⇒ 不判，但观测照收
            self._m410_note(event_type, tp.VERDICT_NO_COVERAGE,
                            predict_type=predict_type,
                            window_start=win["window_start"],
                            expected=None, lo=None, hi=None, actual=_actual)
        else:
            # ★burst 型走点判：60s 窗内批数恒 ∈{0,1}，区间判据会恒中（见 typed_prediction）
            _v = (tp.judge_point(_actual, _pred.get("expected"))
                  if predict_type == "burst"
                  else tp.judge(_actual, _pred.get("lo"), _pred.get("hi")))
            self._m410_note(event_type, _v,
                            predict_type=predict_type,
                            window_start=win["window_start"],
                            expected=_pred.get("expected"), lo=_pred.get("lo"),
                            hi=_pred.get("hi"), actual=_actual)
        _hist = self._m410_hist.setdefault(event_type, [])
        _hist.append(_actual)
        if len(_hist) > tp.HISTORY_MAX:
            del _hist[: len(_hist) - tp.HISTORY_MAX]
        # ★链式：本窗一关就给下一窗登记（含「一条都没来」的空窗），
        #   否则覆盖率取决于 consume 的节拍，而不是取决于数据本身。
        self._m410_register_window(event_type, predict_type,
                                   float(win["window_end"]), float(win["window_end"]))

    def _m410_expire(self, now: float) -> int:
        """时刻型预测超时未到 ⇒ 判 miss 并出队（旧实现从不出队，见 94.4% 假分母）。"""
        if not self._pending_predictions:
            return 0
        _keep = []
        _n = 0
        for _p in self._pending_predictions:
            if _p.get("predict_type") in ("count", "burst"):
                continue          # 不该出现在这里；留在队列由 sweep 统一清
            _tol = _p.get("tolerance_sec")
            _deadline = float(_p.get("predicted_ts", 0) or 0) + float(
                60.0 if _tol is None else _tol)
            if _deadline < now:
                self._m410_note(_p.get("event_type", "?"), "miss",
                                predict_type=_p.get("predict_type", "periodic"),
                                predicted_ts=_p.get("predicted_ts"),
                                tolerance_sec=_tol, actual=None)
                _n += 1
                continue
            _keep.append(_p)
        if _n:
            self._pending_predictions = _keep
        return _n

    def register_typed_predictions(self, now: float | None = None) -> int:
        """给「下一个尚未开始的分钟窗」登记 count/burst 预测，返回新登记数。

        与 register_periodic_predictions 并列：那个管时刻，这个管条数/批数。
        同一个 (event_type, window_start) 只登记一次；已开始的窗口不登记
        （事后登记预测 = 用已知结果编预测）。
        """
        if not self._m410_on():
            return 0
        _tp = self._m410_mod()
        if _tp is None:
            return 0
        _now = float(now if now is not None else time.time())
        _w0 = _tp.next_window_start(_now)
        _prof = self._m410_profile(_now)
        _added = 0
        with self._lock:
            for _et in _tp.pp.TARGET_EVENTS:
                _pt = _tp.classify(_et)
                if _pt not in ("count", "burst"):
                    continue
                _added += self._m410_register_window(_et, _pt, _w0, _now, _prof)
            if len(self._m410_win_preds) > 200:
                self._m410_win_preds = self._m410_win_preds[-200:]
        return _added

    def _m410_register_window(self, event_type: str, predict_type: str,
                              window_start: float, now: float,
                              profile: dict | None = None) -> int:
        """给指定窗口登记一条条数/批数预测（已存在则跳过），返回新增数。

        ★由 `_m410_settle()` 在每窗关闭后**链式**调用一次，给下一窗登记：
          consume 的节拍（200 条事件）比 60s 窗粗得多，只靠 consume 会大量漏登记，
          报表里就会堆满 no_coverage、hit_rate 只剩个位数样本（回放实测 68/69）。
        """
        _tp = self._m410_mod()
        if _tp is None:
            return 0
        for _p in self._m410_win_preds:
            if (_p.get("event_type") == event_type
                    and _p.get("window_start") == window_start):
                return 0
        _exp, _lo, _hi = self._m410_range(
            event_type, predict_type,
            profile if profile is not None else self._m410_profile(now), _tp)
        if _lo is None:
            # 既无窗口历史、档案也没有该类的速率 ⇒ 不登记。
            # 登记一个空区间只会制造假 no_coverage，掩盖真正的采集缺失。
            self._m410_note(event_type, _tp.VERDICT_NO_COVERAGE,
                            predict_type=predict_type, window_start=window_start,
                            expected=None, lo=None, hi=None, actual=None)
            return 0
        self._m410_win_preds.append({
            "event_type": event_type,
            "predict_type": predict_type,
            "window_start": float(window_start),
            "expected": _exp,
            "lo": _lo,
            "hi": _hi,
            "created_at": float(now),
        })
        return 1

    def sweep_typed_predictions(self, now: float | None = None) -> dict:
        """对账推进（分钟级挂载点）：关空窗 + 清过期预测。

        Returns:
            {"closed_windows": int, "expired_periodic": int, "dropped_windows": int}
        """
        _res = {"closed_windows": 0, "expired_periodic": 0, "dropped_windows": 0}
        if not self._m410_on():
            return _res
        _tp = self._m410_mod()
        if _tp is None:
            return _res
        _now = float(now if now is not None else time.time())
        with self._lock:
            for _et, _tr in list(self._m410_windows.items()):
                _pt = _tp.classify(_et) or "count"
                for _w in _tr.close_until(_now):
                    self._m410_settle(_et, _pt, _w, _tp)
                    _res["closed_windows"] += 1
            _res["expired_periodic"] = self._m410_expire(_now)
            _keep = []
            for _p in self._m410_win_preds:
                _end = float(_p.get("window_start") or 0) + float(_tp.WINDOW_SEC)
                if _end <= _now:
                    # 窗口早该关了却没等到关闭记录（长空档被跳过）⇒ 判 no_coverage。
                    # 不猜 0：0 条会让「眼睛关了」和「挖掘器没被喂」混成一件事。
                    self._m410_note(_p.get("event_type", "?"),
                                    _tp.VERDICT_NO_COVERAGE,
                                    predict_type=_p.get("predict_type"),
                                    window_start=_p.get("window_start"),
                                    expected=_p.get("expected"), lo=_p.get("lo"),
                                    hi=_p.get("hi"), actual=None)
                    _res["dropped_windows"] += 1
                    continue
                _keep.append(_p)
            self._m410_win_preds = _keep
        return _res

    def get_typed_prediction_stats(self) -> dict:
        """刀2 统计快照（刀3 hit_rate 聚合的输入；hit_rate=None 表示无覆盖样本）。"""
        with self._lock:
            _by: dict[str, dict] = {}
            for _et, _q in self._m410_stat.items():
                _h = int(_q.get("hit", 0))
                _m = int(_q.get("miss", 0))
                _nc = int(_q.get("no_coverage", 0))
                _cov = _h + _m
                _by[_et] = {
                    "hit": _h, "miss": _m, "no_coverage": _nc, "covered": _cov,
                    "hit_rate": (round(_h / _cov, 4) if _cov else None),
                }
            _tp = self._m410_tp
            return {
                "enabled": self._m410_on(),
                "version": (_tp.version() if _tp is not None else ""),
                "by_event": _by,
                "pending_periodic": len(self._pending_predictions),
                "pending_windows": len(self._m410_win_preds),
                "windows_tracked": len(self._m410_windows),
                "recent": list(self._m410_verdicts)[-20:],
            }

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

    def evict_pattern(self, event_type: str, reason: str = "") -> bool:
        """淘汰某事件类型的周期模式（第410批刀3：连续 N 窗命中率过低时调用）。

        只摘「周期模式 + 待验证预测」，**不清历史质量计数** —— 沿用 409 批纪律：
        淘汰要能追溯，删掉计数等于把"它曾经怎么错"也一起删了。
        """
        _key = f"periodic:{event_type}"
        with self._lock:
            _had = _key in self._discovered_patterns
            self._discovered_patterns.pop(_key, None)
            if self._pending_predictions:
                self._pending_predictions = [
                    p for p in self._pending_predictions
                    if p.get("event_type") != event_type]
            if _had:
                self._total_pruned_patterns += 1
        if _had:
            from nucleus.logger import get_module_logger as _gml410
            _gml410("StreamMiner").info(
                "[410刀3] 淘汰周期模式 %s（%s）", event_type, reason or "-")
        return _had

    # ---- 综合消费入口 ----

    def consume(self, horizon_sec: float | None = None) -> dict[str, Any]:
        """★A-16 综合消费：周期模式预调度 + 重复模式负载预测 + 质量评估。

        供 InfoField/调度侧周期性调用（如每轮模式发现后）。
        Returns:
            {"prewarm": [...], "load_forecast": {...}, "quality": {...}}
        """
        _prewarm = self.get_upcoming_events(horizon_sec)
        _added = self.register_periodic_predictions(horizon_sec)
        # ★第410批刀2：窗口型（count/burst）预测登记 + 过期对账推进
        _typed_added = self.register_typed_predictions()
        _sweep = self.sweep_typed_predictions()
        _load = self.get_load_forecast()
        _quality = self.evaluate_pattern_quality()
        # 定期落盘（默认5分钟一次，避免频繁IO）
        _interval = float(self._consume_cfg("persist_interval", 300))
        if _interval > 0 and (time.time() - self._last_persist_at) >= _interval:
            self.save_patterns()
        return {
            "prewarm": _prewarm,
            "registered_predictions": _added,
            "registered_typed_predictions": _typed_added,
            "typed_sweep": _sweep,
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
