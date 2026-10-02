# -*- coding: utf-8 -*-
"""
runtime_metrics.py —— 运行时指标

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架运行时性能指标采集与统计
机制: 基于RuntimeMetrics类实现，包含10个核心方法
定位: 监控层
"""

from nucleus._silent_except import silent_exc  # 主线第78批 T2：静默异常可见化
from nucleus.logging.sanitizer import sanitize
from config import TIMEOUT_CONFIG
import queue
import threading
import time
import logging
import traceback
from collections import deque
from typing import Any



class RuntimeMetrics:
    """运行时指标采集器（零阻塞异步聚合）。

    ★主线B（可观测性与运维）增强：
      - B1：pulse_avg_ms 从「累计加权平均」改为「滑动窗口平均」，避免长时间
        运行后新样本权重趋近 0 导致指标「冻结」（累计 count 越来越大，
        单个新样本对平均值的贡献 → 0，无法反映近期状态）。
      - B2：新增时间序列环形缓冲（_history deque），按采样间隔记录指标快照，
        供健康面板展示历史趋势、供自适应调度看近期变化。
      - B3：新增告警阈值联动——pulse_errors 突增 / lock_wait 过高 /
        queue_depth 过深时，主动写 WARNING/ERROR 告警日志并记入快照。
    """

    def __init__(self):
        self._queue: queue.Queue = queue.Queue(maxsize=5000)
        self._lock = threading.Lock()
        self._metrics: dict[str, Any] = {
            "pulse_count": 0,
            "pulse_errors": 0,
            "pulse_avg_ms": 0.0,
            "pulse_max_ms": 0.0,
            "lock_wait_total_ms": 0.0,
            "lock_wait_count": 0,
            "queue_max_depth": 0,
            "thread_count": 0,
            "reentry_count": 0,
            "error_snapshots": [],
            # ★B3：最近告警（含触发时间/类型/阈值/实际值），供面板展示
            "alerts": [],
        }
        self._max_snapshots = 20
        self._running = False
        self._thread: threading.Thread | None = None
        # ★B3：最近一次瞬时队列深度（区别于 queue_max_depth 的峰值），
        #   用于告警判断——峰值不会下降，若用峰值做阈值会「一旦超阈值就持续告警」。
        self._last_queue_depth = 0
        # ★主线第78批 T1：队列深度趋势窗口（每采样点记录一次瞬时深度，供趋势告警）
        self._queue_depth_window: list = []
        self._queue_depth_window_max = 12
        # ★主线第78批 T3：内存占用趋势窗口（每采样点记录一次 mem_percent，供趋势/高水位告警）
        self._mem_percent_window: list = []
        self._mem_percent_window_max = 12
        # ★主线第78批 T3：内存高水位阈值（>=80% 触发告警，呼应任务书验收<80%）
        self._mem_percent_alert = 80.0
        self._mem_percent_trend_delta = 10.0  # 窗口内涨幅>=10pct 且逼近阈值即趋势告警

        # ===== ★B1：滑动窗口平均（deque maxlen，只保留近期样本）=====
        # 累计平均 bug：pulse_count 单调递增，长时间运行后新样本权重→0。
        # 改为只保留最近 N 个脉冲耗时，pulse_avg_ms 反映「近期」而非「累计」。
        self._pulse_durations: deque = deque(maxlen=500)  # 最近 500 次脉冲耗时
        self._lock_waits: deque = deque(maxlen=500)        # 最近 500 次锁等待

        # ===== ★B2：时间序列环形缓冲（供历史趋势展示）=====
        self._history: deque = deque(maxlen=720)  # 默认保留 720 个采样点
        self._sample_interval = 5.0               # 每 5 秒采样一次（可配）
        self._last_sample_at = 0.0

        # ===== ★B3：告警阈值（走 config 可配，这里设默认值）=====
        self._alert_thresholds = {
            "pulse_errors_per_sample": 3,   # 单个采样周期内错误数 ≥3 → 告警
            "lock_wait_avg_ms": 50.0,       # 平均锁等待 ≥50ms → 告警
            "queue_max_depth": 1000,        # 队列峰值深度 ≥1000 → 告警
        }
        self._max_alerts = 20               # 最近告警保留条数
        self._last_alert_at = 0.0           # 告警去抖时间戳
        self._alert_cooldown = 30.0         # 告警冷却（秒），避免刷屏

    def start(self):
        """启动聚合线程。"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._aggregate_loop, daemon=True, name="RuntimeMetricsAggregator"
        )
        self._thread.start()

    def stop(self):
        self._running = False

    def record_pulse(self, duration_ms: float, lock_wait_ms: float = 0.0, queue_depth: int = 0):
        """记录一次脉冲处理（无锁投递，不阻塞主链路）。"""
        try:
            self._queue.put_nowait(("pulse", duration_ms, lock_wait_ms, queue_depth))
        except queue.Full as _se:
            silent_exc(_se, "runtime_metrics.py:101")

    def record_error(self, pulse_type: str, error: str, traceback_text: str, lock_held: bool = False):
        """记录异常现场快照（脉冲类型 + 错误 + 调用栈 + 锁状态）。"""
        try:
            self._queue.put_nowait(("error", pulse_type, error, traceback_text, lock_held))
        except queue.Full as _se:
            silent_exc(_se, "runtime_metrics.py:108")

    def record_reentry(self, task_name: str):
        """记录周期任务重入触发。"""
        try:
            self._queue.put_nowait(("reentry", task_name))
        except queue.Full as _se:
            silent_exc(_se, "runtime_metrics.py:115")

    def _aggregate_loop(self):
        """独立聚合线程：批量消费队列，更新指标（避免主链路被埋点拖慢）。"""
        while self._running:
            try:
                _item = self._queue.get(timeout=TIMEOUT_CONFIG['queue_get'])
            except queue.Empty:
                # 队列空时仍推进时间序列采样（保证低流量下也有趋势点）
                self._maybe_sample_history()
                continue
            _kind = _item[0]
            with self._lock:
                if _kind == "pulse":
                    _, _dur, _lock_wait, _depth = _item
                    self._metrics["pulse_count"] += 1
                    # ★B1：滑动窗口平均（只保留最近 N 个样本，反映近期状态）
                    self._pulse_durations.append(_dur)
                    self._metrics["pulse_avg_ms"] = round(
                        sum(self._pulse_durations) / len(self._pulse_durations), 3
                    )
                    self._metrics["pulse_max_ms"] = max(self._metrics["pulse_max_ms"], _dur)
                    if _lock_wait > 0:
                        self._metrics["lock_wait_total_ms"] += _lock_wait
                        self._metrics["lock_wait_count"] += 1
                        self._lock_waits.append(_lock_wait)
                    self._metrics["queue_max_depth"] = max(self._metrics["queue_max_depth"], _depth)
                    self._last_queue_depth = _depth
                    self._metrics["thread_count"] = threading.active_count()
                elif _kind == "error":
                    _, _ptype, _err, _tb, _lock_held = _item
                    self._metrics["pulse_errors"] += 1
                    _snap = {
                        "pulse_type": _ptype,
                        "error": _err[:200],
                        "traceback": _tb[:1500],
                        "lock_held": _lock_held,
                        "thread_count": threading.active_count(),
                        "timestamp": time.time(),
                    }
                    self._metrics["error_snapshots"].append(_snap)
                    if len(self._metrics["error_snapshots"]) > self._max_snapshots:
                        self._metrics["error_snapshots"] = self._metrics["error_snapshots"][-self._max_snapshots:]
                elif _kind == "reentry":
                    self._metrics["reentry_count"] += 1
            # 每次消费后推进时间序列采样 + 告警检测
            self._maybe_sample_history()

    def _maybe_sample_history(self) -> None:
        """★B2：按采样间隔把当前指标快照写入时间序列环形缓冲；并做 B3 告警检测。"""
        _now = time.time()
        if _now - self._last_sample_at < self._sample_interval:
            return
        self._last_sample_at = _now
        with self._lock:
            # ★B1一致性：lock_wait_avg_ms 也走滑动窗口（用 _lock_waits deque），
            #   避免与 pulse_avg_ms 一样出现「累计平均冻结」问题。
            _lock_wait_avg = (
                round(sum(self._lock_waits) / len(self._lock_waits), 3)
                if self._lock_waits else 0.0
            )
            _point = {
                "ts": _now,
                "pulse_count": self._metrics["pulse_count"],
                "pulse_errors": self._metrics["pulse_errors"],
                "pulse_avg_ms": self._metrics["pulse_avg_ms"],
                "pulse_max_ms": self._metrics["pulse_max_ms"],
                "lock_wait_avg_ms": _lock_wait_avg,
                "queue_max_depth": self._metrics["queue_max_depth"],
                "thread_count": self._metrics["thread_count"],
                "reentry_count": self._metrics["reentry_count"],
                # ★主线第66批 T1.4：内存时序（供增长率趋势与泄漏定位）
                "mem_rss_mb": self.get_memory_usage().get("rss_mb", 0.0),
                "mem_percent": self.get_memory_usage().get("percent", 0.0),
            }
            # ★主线第78批 T1：记录瞬时队列深度进趋势窗口
            self._queue_depth_window.append(self._last_queue_depth)
            if len(self._queue_depth_window) > self._queue_depth_window_max:
                self._queue_depth_window.pop(0)
            # ★主线第78批 T3：记录内存占用进趋势窗口
            self._mem_percent_window.append(_point.get("mem_percent", 0.0))
            if len(self._mem_percent_window) > self._mem_percent_window_max:
                self._mem_percent_window.pop(0)
            self._history.append(_point)
        # ★B156-5 票①：降频闭环生产者——周期性评估负载等级并下发到自适应控制器。
        #   assess_load_level 汇总队列深度/CPU/内存 → LOW/MEDIUM/HIGH/CRITICAL，
        #   经 set_level 推给 AdaptiveFrequencyController，使已接线的 5 个 should_execute
        #   消费点（胃/器官扫描/代码学习/经验库清理/冷存 compaction）真正随负载降频。
        #   等级恒 LOW 时因子 1.0，与改造前行为完全一致（防御性能力）。
        self._sync_adaptive_level()
        # ★B3：告警检测（在锁外做，避免阻塞）
        self._check_alerts(_point)

    def _sync_adaptive_level(self) -> None:
        """★B156-5 票①：降频闭环生产者——采集当前负载并下发等级到自适应控制器。

        每采样周期（_maybe_sample_history）调用一次。读取实时队列深度 / CPU / 内存，
        经 assess_load_level 评估等级后 set_level 推给控制器。等级恒 LOW 时与改造前一致。
        """
        try:
            _sys = self.get_system_load()
            _lv = assess_load_level(
                queue_depth=self.get_queue_depth(),
                cpu_percent=_sys.get("cpu_percent"),
                memory_percent=_sys.get("memory_percent"),
                snapshot_saving=False,
            )
            get_adaptive_controller().set_level(_lv)
        except Exception as _se:
            silent_exc(_se, "runtime_metrics.py:_sync_adaptive_level")

    def _check_alerts(self, point: dict[str, Any]) -> None:
        """★B3：告警阈值联动——异常指标主动写告警日志，并记入快照。"""
        _now = time.time()
        # 告警冷却：避免同一异常持续刷屏
        if _now - self._last_alert_at < self._alert_cooldown:
            return
        _alerts = []
        _thr = self._alert_thresholds

        # 用「上一采样点 vs 当前」估算单采样周期内新增错误数
        _prev_errors = 0
        if len(self._history) >= 2:
            _prev_errors = self._history[-2].get("pulse_errors", 0)
        _new_errors = point.get("pulse_errors", 0) - _prev_errors
        if _new_errors >= _thr["pulse_errors_per_sample"]:
            _alerts.append({
                "type": "pulse_errors_spike",
                "level": "ERROR",
                "message": f"脉冲错误突增: 近{self._sample_interval:.0f}秒新增{_new_errors}个错误",
                "threshold": _thr["pulse_errors_per_sample"],
                "actual": _new_errors,
                "timestamp": _now,
            })

        _lock_wait = point.get("lock_wait_avg_ms", 0.0)
        if _lock_wait >= _thr["lock_wait_avg_ms"]:
            # ★PHASE13（2026-09-07）：告警附带「热点锁」定位。
            #   原告警只有「平均 70ms」，运维无从下手（星轨据此误判为 node_pool
            #   的全局锁，实际是 InfoField._lock 在单条 publish 上被抢 4 次）。
            #   此处延迟读取 InfoField 的按锁名累计器（reset=True 取本窗口增量），
            #   把耗时最高的锁及其占比拼进消息，让告警自带答案。
            _hot_lock_msg = ""
            try:
                from nucleus.field.InfoField import get_lock_wait_by_name

                _by_name = get_lock_wait_by_name(reset=True)
                if _by_name:
                    _total = sum(_by_name.values()) or 1.0
                    _top = sorted(_by_name.items(), key=lambda kv: -kv[1])[:2]
                    _parts = [f"{_n} {_v:.0f}ms({_v / _total * 100:.0f}%)"
                              for _n, _v in _top]
                    _hot_lock_msg = f"，主要来源: {' | '.join(_parts)}"
            except Exception as _se:
                silent_exc(_se, "runtime_metrics.py:241")
            _alerts.append({
                "type": "lock_wait_high",
                "level": "WARNING",
                "message": f"锁等待过高: 平均{_lock_wait:.1f}ms{_hot_lock_msg}",
                "threshold": _thr["lock_wait_avg_ms"],
                "actual": round(_lock_wait, 1),
                "timestamp": _now,
            })

        _depth = self._last_queue_depth  # 用瞬时值而非峰值，避免超阈值后持续告警
        if _depth >= _thr["queue_max_depth"]:
            _alerts.append({
                "type": "queue_depth_high",
                "level": "WARNING",
                "message": f"队列深度过高: {_depth}",
                "threshold": _thr["queue_max_depth"],
                "actual": _depth,
                "timestamp": _now,
            })
        # ★主线第78批 T1：队列深度趋势告警（早期预警）。
        #   绝对阈值只能在「已破阈」后告警；趋势告警在深度持续爬升、尚未破阈时
        #   即提前示警，给运维缓冲窗口。窗口来自每采样点记录的 _last_queue_depth。
        _w = self._queue_depth_window
        if len(_w) >= 6:
            _rising = all(_w[i] <= _w[i + 1] for i in range(len(_w) - 1))
            _delta = _w[-1] - _w[0]
            # 仅当：持续上升 + 涨幅显著 + 尚未触发绝对阈值告警 时示警
            if _rising and _delta >= 800 and 0 < _w[-1] < _thr["queue_max_depth"]:
                _alerts.append({
                    "type": "queue_depth_trend_rising",
                    "level": "WARNING",
                    "message": (f"队列深度持续上升(趋势告警): 近{len(_w)}采样点从"
                                f"{_w[0]}升至{_w[-1]}(+{_delta})"),
                    "threshold": _thr["queue_max_depth"],
                    "actual": _w[-1],
                    "timestamp": _now,
                })

        # ★主线第78批 T3：内存占用告警（呼应任务书验收「内存<80%」）。
        #   (a) 高水位：mem_percent 越过阈值即告警，给运维明确「已超验收线」信号；
        #   (b) 趋势：窗口内持续上升且逼近阈值但未破阈时提前示警，避免陡增措手不及。
        _mp = point.get("mem_percent", 0.0)
        if _mp >= self._mem_percent_alert:
            _alerts.append({
                "type": "mem_percent_high",
                "level": "WARNING",
                "message": f"内存占用过高: {_mp:.1f}%(阈值{self._mem_percent_alert:.0f}%)",
                "threshold": self._mem_percent_alert,
                "actual": round(_mp, 1),
                "timestamp": _now,
            })
        _mw = self._mem_percent_window
        if len(_mw) >= 6:
            _m_rising = all(_mw[i] <= _mw[i + 1] for i in range(len(_mw) - 1))
            _m_delta = _mw[-1] - _mw[0]
            if _m_rising and _m_delta >= self._mem_percent_trend_delta and 0 < _mw[-1] < self._mem_percent_alert:
                _alerts.append({
                    "type": "mem_percent_trend_rising",
                    "level": "WARNING",
                    "message": (f"内存占用持续上升(趋势告警): 近{len(_mw)}采样点从"
                                f"{_mw[0]:.1f}%升至{_mw[-1]:.1f}%(+{_m_delta:.1f})"),
                    "threshold": self._mem_percent_alert,
                    "actual": round(_mw[-1], 1),
                    "timestamp": _now,
                })

        if not _alerts:
            return
        self._last_alert_at = _now
        with self._lock:
            for _a in _alerts:
                self._metrics["alerts"].append(_a)
                if len(self._metrics["alerts"]) > self._max_alerts:
                    self._metrics["alerts"] = self._metrics["alerts"][-self._max_alerts:]
        # 写告警日志（用标准 logging，挂 pulse.module.runtime_metrics）
        try:
            import logging
            _logger = logging.getLogger("pulse.module.runtime_metrics")
            for _a in _alerts:
                if _a["level"] == "ERROR":
                    _logger.error(_a["message"])
                else:
                    _logger.warning(_a["message"])
        except Exception as _se:
            silent_exc(_se, "runtime_metrics.py:298")

    def get_queue_depth(self) -> int:
        """★第64批 T4：获取当前瞬时队列深度（供自适应缓存 TTL 参考）。

        返回 _last_queue_depth（最近一次记录的队列瞬时深度），区别于 queue_max_depth 峰值。
        B3 告警已改用瞬时值避免「超阈值后持续告警」，此处复用同一字段保证自适应口径一致。
        """
        with self._lock:
            return int(self._last_queue_depth)

    # _m64_t4_get_queue_depth_done

    def get_system_load(self) -> dict[str, Any]:
        """★主线第65批 T2/P1：统一系统负载查询接口（供各模块自适应调用）。

        返回 cpu_percent / queue_depth / memory_percent / load_level(low|medium|high|critical)。
        ★优先 psutil；不可用时安全降级（cpu/memory 记 0.0，load_level 仅由 queue_depth 推断）。
        口径与任务书一致：CPU 40%/70% 与队列 2000/5000 为 medium/high 分界。
        """
        _qd = self.get_queue_depth()
        _cpu = 0.0
        _mem = 0.0
        try:
            import psutil
            _cpu = float(psutil.cpu_percent(interval=None))
            _mem = float(psutil.virtual_memory().percent)
        except Exception as _se:
            silent_exc(_se, "runtime_metrics.py:326")
        _level = "low"
        if _cpu > 85.0 or _qd > 10000:
            _level = "critical"
        elif _cpu > 70.0 or _qd > 5000:
            _level = "high"
        elif _cpu > 40.0 or _qd > 2000:
            _level = "medium"
        return {
            "cpu_percent": round(_cpu, 1),
            "queue_depth": int(_qd),
            "memory_percent": round(_mem, 1),
            "load_level": _level,
        }

    def get_memory_usage(self) -> dict[str, Any]:
        """★主线第65批 T5/P2：进程内存占用快照（排查内存增长用）。

        返回 rss_mb / vms_mb / percent（psutil 可用时）；否则降级为 0。
        """
        _rss = 0.0
        _vms = 0.0
        _pct = 0.0
        try:
            import psutil
            _p = psutil.Process()
            _mi = _p.memory_info()
            _rss = _mi.rss / (1024.0 * 1024.0)
            _vms = _mi.vms / (1024.0 * 1024.0)
            _pct = float(_p.memory_percent())
        except Exception as _se:
            silent_exc(_se, "runtime_metrics.py:357")
        return {"rss_mb": round(_rss, 1), "vms_mb": round(_vms, 1),
                "percent": round(_pct, 1)}

    def check_memory_alarm(self, threshold_percent: float = 80.0) -> bool:
        """★主线第65批 T5：内存超阈值告警（True=触发）。调用方自行决定降载/GC。

        ★主线第66批 T1.4：保留原签名/返回（bool），行为不变；
        增强版见 check_memory_alarm_detailed（返回结构化字典 + 增长率 + 可选自动GC）。
        """
        import logging
        _logger = logging.getLogger("pulse.module.runtime_metrics")
        _pct = self.get_memory_usage().get("percent", 0.0)
        if _pct >= float(threshold_percent):
            try:
                _logger.warning(
                    f"[运行时指标] 内存使用率 {_pct:.1f}% ≥ 阈值 {threshold_percent:.1f}%，"
                    f"建议降载/触发 GC")
            except Exception as _se:
                silent_exc(_se, "runtime_metrics.py:376")
            return True
        return False

    def get_memory_growth_rate(self, window_samples: int = 60) -> float:
        """★主线第66批 T1.4：估算内存增长率（MB/分钟）。

        取最近 window_samples 个含内存快照的历史点，用 (末RSS-首RSS)/跨度分钟。
        无足够样本或 psutil 不可用时安全返回 0.0（绝不抛异常）。
        """
        try:
            _hist = list(self._history)
            _mem_pts = [p for p in _hist if p.get("mem_rss_mb")]
            if len(_mem_pts) < 2:
                return 0.0
            _win = _mem_pts[-window_samples:]
            _span_s = _win[-1]["ts"] - _win[0]["ts"]
            if _span_s <= 0:
                return 0.0
            _delta_mb = _win[-1]["mem_rss_mb"] - _win[0]["mem_rss_mb"]
            return _delta_mb / (_span_s / 60.0)
        except Exception as e:
            silent_exc(e, where="nucleus.runtime_metrics::get_memory_growth_rate L440")
            return 0.0

    def check_memory_alarm_detailed(self, threshold_percent: float = 80.0,
                                    auto_gc: bool = False) -> dict[str, Any]:
        """★主线第66批 T1.4：内存告警增强——同时监控使用率与增长率，可选自动GC。

        返回 {triggered, percent, rss_mb, growth_mb_per_min, gc_triggered}。
        ★auto_gc 受 ENABLE_MEMORY_AUTO_GC 闸门控制（默认关 → 零回归，不自动回收）；
          增长率阈值 MEMORY_GROWTH_ALARM_MB_PER_MIN（默认 10.0）。
        ★增长率超阈值即判定「疑似内存泄漏」，即便使用率尚低也告警。
        """
        import gc
        import logging
        _logger = logging.getLogger("pulse.module.runtime_metrics")
        try:
            import config as _c
            _enable_gc = bool(getattr(_c, "ENABLE_MEMORY_AUTO_GC", False))
            _growth_thr = float(getattr(_c, "MEMORY_GROWTH_ALARM_MB_PER_MIN", 10.0))
        except Exception:
            _enable_gc, _growth_thr = False, 10.0
        _u = self.get_memory_usage()
        _pct = _u.get("percent", 0.0)
        _rss = _u.get("rss_mb", 0.0)
        _growth = self.get_memory_growth_rate()
        _triggered = _pct >= float(threshold_percent) or _growth >= _growth_thr
        _gc_done = False
        if _triggered:
            try:
                if _pct >= float(threshold_percent):
                    _logger.warning(
                        "[运行时指标] 内存使用率 %.1f%% ≥ 阈值 %.1f%%，RSS=%.1fMB，"
                        "增长率 %.2fMB/分钟", _pct, threshold_percent, _rss, _growth)
                else:
                    _logger.warning(
                        "[运行时指标] 内存增长率 %.2fMB/分钟 ≥ 阈值 %.1fMB/分钟（疑似内存泄漏），"
                        "RSS=%.1fMB 使用率=%.1f%%", _growth, _growth_thr, _rss, _pct)
            except Exception as _se:
                silent_exc(_se, "runtime_metrics.py:435")
            if auto_gc and _enable_gc:
                try:
                    gc.collect()
                    _gc_done = True
                except Exception as _se:
                    silent_exc(_se, "runtime_metrics.py:441")
        return {"triggered": _triggered, "percent": _pct, "rss_mb": _rss,
                "growth_mb_per_min": round(_growth, 3), "gc_triggered": _gc_done}

    # _m65_t2_get_system_load_done

    def get_snapshot(self) -> dict[str, Any]:
        """获取当前指标快照（供代码学习/健康报告查询）。"""
        with self._lock:
            _s = dict(self._metrics)
            _s["error_snapshots"] = list(self._metrics["error_snapshots"])
            _s["alerts"] = list(self._metrics["alerts"])
            _s["thread_count"] = threading.active_count()
            # ★B1一致性：直接提供滑动窗口的锁等待平均（避免消费方各自用累计值计算）
            _s["lock_wait_avg_ms"] = (
                round(sum(self._lock_waits) / len(self._lock_waits), 3)
                if self._lock_waits else 0.0
            )
            return _s

    def get_history(self, limit: int = 120) -> list[dict[str, Any]]:
        """★B2：获取时间序列历史（供健康面板展示趋势、自适应调度看近期变化）。"""
        with self._lock:
            return list(self._history)[-limit:]

    def update_alert_thresholds(self, thresholds: dict[str, float]) -> None:
        """★B3：运行时更新告警阈值（供 config 热加载）。"""
        with self._lock:
            for _k, _v in thresholds.items():
                if _k in self._alert_thresholds and isinstance(_v, (int, float)):
                    self._alert_thresholds[_k] = float(_v)


# 模块级单例（双检锁）
_metrics: RuntimeMetrics | None = None
_metrics_lock = threading.Lock()


def get_runtime_metrics() -> RuntimeMetrics:
    """获取 RuntimeMetrics 单例。"""
    global _metrics
    if _metrics is None:
        with _metrics_lock:
            if _metrics is None:
                _metrics = RuntimeMetrics()
                _metrics.start()
    return _metrics


def shutdown_runtime_metrics() -> None:
    """★P0批次3：停止并复位 RuntimeMetrics 单例，满足器官零状态（规则4）。

    原停机流程只调用 get_runtime_metrics().stop() 释放线程，但全局变量 _metrics 仍持有
    已停止的实例，重启时 get_runtime_metrics() 会复用旧实例而非重建。此处显式置空，
    使下次获取重建全新零状态实例。
    """
    global _metrics
    _inst = _metrics
    _metrics = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as _se:
                silent_exc(_se, "runtime_metrics.py:506")


# ===== ★主线第67批 T4/P1：队列深度自适应降频机制 =====
#   背景：高负载时非关键操作（胃消化 / 器官扫描 / 代码学习 / 经验库清理 / 冷存 compaction）
#   仍按原频率执行，会与关键操作争抢 CPU 与 IO，形成「越忙越积压」的恶性循环。
#   机制：按负载等级自动拉长非关键操作的执行间隔；关键操作（对话 / 心跳 / 快照保存）
#   走白名单，任何等级下都不降频。
#   ★T0偏差：实测 L3 队列 0~52/300，任务书「队列深度 2000+」前提不成立；
#     本机制作为防御性能力实现，负载正常时完全不改变任何行为（LOW 系数 1.0）。

ADAPTIVE_FREQ_WHITELIST = ("对话响应", "心跳", "生命体征", "快照保存",
                           "chat", "heartbeat", "snapshot_save")

# 操作名 -> 正常间隔（秒）
ADAPTIVE_FREQ_BASE = {
    "stomach_digest": 30.0,
    "organ_scan": 300.0,
    "code_learning": 60.0,
    "experience_cleanup": 300.0,
    "cold_compaction": 600.0,
}

# 负载等级 -> 间隔放大系数；None 表示暂停（不执行）
ADAPTIVE_FREQ_FACTOR = {"LOW": 1.0, "MEDIUM": 1.3, "HIGH": 2.0, "CRITICAL": None}
_VALID_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def _adaptive_enabled() -> bool:
    """自适应降频总开关（默认开）。关闭 → should_execute 恒 True，零行为变化。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_ADAPTIVE_FREQUENCY", True))
    except Exception as e:
        silent_exc(e, where="nucleus.runtime_metrics::_adaptive_enabled L582")
        return True


def _adaptive_threshold(name: str, default: float) -> float:
    try:
        import config
        return float(getattr(config, name, default) or default)
    except Exception:
        return default


def assess_load_level(queue_depth=None, cpu_percent=None, memory_percent=None,
                      snapshot_saving: bool = False) -> str:
    """★T4：评估系统负载等级 -> LOW / MEDIUM / HIGH / CRITICAL。
    各维度取最高等级（保守优先）：任一维度告急即整体升级。"""
    _med = _adaptive_threshold("QUEUE_DEPTH_THRESHOLD_MEDIUM", 100)
    _high = _adaptive_threshold("QUEUE_DEPTH_THRESHOLD_HIGH", 200)
    _crit = _adaptive_threshold("QUEUE_DEPTH_THRESHOLD_CRITICAL", 300)
    _level = "LOW"

    if queue_depth is not None:
        try:
            _q = float(queue_depth)
            if _q >= _crit:
                _level = "CRITICAL"
            elif _q >= _high:
                _level = "HIGH"
            elif _q >= _med:
                _level = "MEDIUM"
        except (TypeError, ValueError) as _se:
            silent_exc(_se, "runtime_metrics.py:570")

    if cpu_percent is not None:
        try:
            _c = float(cpu_percent)
            if _c > 85:
                _level = "CRITICAL"
            elif _c > 70 and _level != "CRITICAL":
                _level = "HIGH"
            elif _c > 50 and _level == "LOW":
                _level = "MEDIUM"
        except (TypeError, ValueError) as _se:
            silent_exc(_se, "runtime_metrics.py:582")

    if memory_percent is not None:
        try:
            _m = float(memory_percent)
            if _m > 90 and _level != "CRITICAL":
                _level = "CRITICAL"
        except (TypeError, ValueError) as _se:
            silent_exc(_se, "runtime_metrics.py:590")

    # 快照保存中：至少视为 MEDIUM（保存本身吃 IO，不宜再叠加非关键操作）
    if snapshot_saving and _level == "LOW":
        _level = "MEDIUM"
    return _level


class AdaptiveFrequencyController:
    """★T4：统一管理非关键操作的执行频率。
    用法：操作执行前 `if get_adaptive_controller().should_execute("stomach_digest"): ...`
    关键操作（白名单）恒返回 True，绝不被降频。"""

    def __init__(self) -> None:
        self._base = dict(ADAPTIVE_FREQ_BASE)
        self._last_run = {}
        self._level = "LOW"
        self._skipped = {}
        self._executed = {}

    def register(self, name: str, base_interval: float) -> None:
        """注册/覆盖一个非关键操作的正常间隔（秒）。"""
        self._base[str(name)] = float(base_interval or 0.0)

    def set_level(self, level: str) -> None:
        if level in _VALID_LEVELS:
            self._level = level

    def get_level(self) -> str:
        return self._level

    def is_critical_op(self, name: str) -> bool:
        """关键操作判定（白名单，大小写不敏感）。"""
        _n = str(name or "")
        return any(_w.lower() in _n.lower() for _w in ADAPTIVE_FREQ_WHITELIST)

    def get_interval(self, name: str) -> float:
        """当前负载等级下的实际执行间隔；关键操作恒 0（不限流）。"""
        _base = float(self._base.get(str(name), 0.0) or 0.0)
        if self.is_critical_op(name):
            return 0.0
        if not _adaptive_enabled():
            return _base
        _f = ADAPTIVE_FREQ_FACTOR.get(self._level, 1.0)
        if _f is None:
            return float("inf")  # CRITICAL：暂停执行
        return _base * float(_f)

    def should_execute(self, name: str, now=None) -> bool:
        """是否应执行该操作。关键操作恒 True；开关关闭恒 True。"""
        if self.is_critical_op(name):
            return True
        if not _adaptive_enabled():
            return True
        _iv = self.get_interval(name)
        if _iv == float("inf"):
            self._skipped[str(name)] = self._skipped.get(str(name), 0) + 1
            return False
        _now = float(now if now is not None else time.time())
        _last = float(self._last_run.get(str(name), 0.0) or 0.0)
        if _last > 0 and (_now - _last) < _iv:
            self._skipped[str(name)] = self._skipped.get(str(name), 0) + 1
            return False
        self._last_run[str(name)] = _now
        self._executed[str(name)] = self._executed.get(str(name), 0) + 1
        return True

    def get_stats(self) -> dict:
        """控制器状态（等级 / 已注册操作 / 跳过与执行计数）。"""
        return {
            "level": self._level,
            "enabled": _adaptive_enabled(),
            "registered": sorted(self._base.keys()),
            "skipped": dict(self._skipped),
            "executed": dict(self._executed),
            "whitelist": list(ADAPTIVE_FREQ_WHITELIST),
        }


_ADAPTIVE_CONTROLLER = None


def get_adaptive_controller() -> AdaptiveFrequencyController:
    """AdaptiveFrequencyController 全局单例。"""
    global _ADAPTIVE_CONTROLLER
    if _ADAPTIVE_CONTROLLER is None:
        _ADAPTIVE_CONTROLLER = AdaptiveFrequencyController()
    return _ADAPTIVE_CONTROLLER

# _m67_t4_adaptive_done


# ===== ★主线第104批 T-104c（D169）：logger.ERROR -> error_snapshots 桥接 =====
# 现状偏差：此前仅 BasePulseOrgan._handle_pulse 的脉冲异常路径调用 record_error，
#   大量 logging.error(...) 仍不进 error_snapshots（"9类故障面板全盲"部分成立）。
# 机制：在框架根 logger("pulse") 挂一个 Handler，ERROR/CRITICAL 记录自动转成 error_snapshots，
#   面板/HTTP 即可见（health_ui 已在读 error_snapshots）。
# 安全：silent_exc 仅打 DEBUG 且不走 ERROR，本 Handler 不会形成递归。

class ErrorCaptureHandler(logging.Handler):
    """★T-104c：把 pulse.* 命名空间下 ERROR/CRITICAL 日志自动记入 error_snapshots。"""

    def __init__(self, level: int = logging.ERROR):
        super().__init__(level=level)
        self._reentrant = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._reentrant:
            return
        try:
            self._reentrant = True
            _rt = get_runtime_metrics()
            _tb = ""
            if record.exc_info:
                _tb = sanitize("".join(traceback.format_exception(*record.exc_info)))
            _rt.record_error(
                pulse_type=record.name or "log",
                error=sanitize(record.getMessage()),
                traceback_text=_tb,
                lock_held=False,
            )
        except Exception as _e:
            # 桥接失败绝不影响主链路；打节流 DEBUG，不递归 ERROR handler
            logging.getLogger("pulse.silent_except").debug("error_snapshot 桥接失败: %s", _e)
        finally:
            self._reentrant = False


_error_capture_installed = False


def install_error_capture(logger_name: str = "pulse") -> bool:
    """★T-104c：在框架根 logger 上安装 ErrorCaptureHandler（幂等）。返回是否本次新安装。"""
    global _error_capture_installed
    if _error_capture_installed:
        return False
    try:
        _lg = logging.getLogger(logger_name)
        for _h in _lg.handlers:
            if isinstance(_h, ErrorCaptureHandler):
                _error_capture_installed = True
                return False
        _h = ErrorCaptureHandler(level=logging.ERROR)
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        _lg.addHandler(_h)
        _error_capture_installed = True
        return True
    except Exception as _e:
        logging.getLogger("pulse.framework").warning("安装 ERROR 捕获失败: %s", _e)
        return False


# _m104c_error_capture_done
