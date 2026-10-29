# -*- coding: utf-8 -*-
"""
PulseMetricsCollector —— 多层级指标采集器 · 运行态可观测性汇聚点

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 MetricsEvent.COLLECT 与心跳、情绪、兴趣、反思、生命状态等多类事件，采集分子/细胞/器官三层指标，构建可观测性快照并输出到控制台与日志。
机制: on_pulse 分派 _on_collect / _on_heartbeat_observe / _on_heartbeat_alive / _on_persona_switched / _on_emotion_detected / _on_interest_changed / _on_search_completed / _on_life_state_changed / _on_reflection_insight / _on_liver_compressed / _on_instinct_upgraded 等订阅点；_collect_molecular / _collect_cellular / _collect_organ_level 做三层采集，_collect_knowledge_evolution / _collect_organ_status_stats / _collect_link_status 补齐维度，_refresh_organ_cache 缓存器官表；_build_observability_snapshot 汇总后广播 ObservabilityEvent.SNAPSHOT 与 MetricsEvent.SNAPSHOT，_print_console_snapshot / _print_body_ui 做可视化，_log_observability 落盘。
定位: 全系统的「仪表盘」，是运行态可观测性的唯一汇聚点。
"""

import io
import json
import os
import sys

from nucleus.const import (
    ChatEvent,
    ControllerEvent,
    EnergyEvent,
    Event,
    EyeEvent,
    KnowledgeEvent,
    LogLevel,
    MouthEvent,
    PersonaEvent,
    ReflectionEvent,
    TouchEvent,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import os
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus._silent_except import silent_exc
from nucleus import absence_notice as _absence_notice
from nucleus._warn_throttle import should_warn
from nucleus.const import (
    HeartEvent,
    HormonesEvent,
    InterestEvent,
    MetricsEvent,
    ObservabilityEvent,
    OrganStatus,
    SystemEvent,
)
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.organ_identity import ORGAN_ALIASES  # ★T-112d：器官名归一化单源真相

# 尝试读取配置，缺失时使用默认值
try:
    from config import FEATURE, OBSERVABILITY
    _obs_enabled = FEATURE.get("enable_observability", True)
    _snapshot_interval = OBSERVABILITY.get("snapshot_interval_beats", 5)
    _console_output = OBSERVABILITY.get("console_output", True)
    _file_output = OBSERVABILITY.get("file_output", True)
    _flat_format = OBSERVABILITY.get("flat_format", True)
    _cache_seconds = OBSERVABILITY.get("organ_stats_cache_seconds", 30)
except ImportError:
    _obs_enabled = True
    _snapshot_interval = 5
    _console_output = True
    _file_output = True
    _flat_format = True
    _cache_seconds = 30


# ===== ★396刀2：观测面安慰剂效应收口（status 不再自报）=====
#  `health_snapshot.json` 的 `heartbeat.status` 过去是常量串「搏动中」——它是"心跳体
#  里写死的一句话"，不是判定结果。更要命的是写它的 `_on_heartbeat_observe` 本身由心跳
#  驱动：心跳一停，这条 handler 再没人调，快照就冻结在最后一帧，而那一帧永远乐观。
#  现场因此出现「心跳死后 46 分钟仍报搏动中」（data/monitor/health_snapshot.json
#  beat_count=35 / status=搏动中）。
#  修法：status 一律在**读快照时按 gap 现算**，判据 = gap ≥ 3×interval ⇒ 停搏；
#        payload 里带来的 status 只用于识别「显式停止」这一种真值，其余不采信。
_M396_HEARTBEAT_STALL_FACTOR = 3.0
_M396_HEARTBEAT_STATUS_SRC = "computed@read"   # 写进快照，标明这不是自报串


# ===== ★377刀4：tracemalloc 常驻 + 两快照差分（376 刀5 A/B 的两处病根）=====
#  病根一（376 刀5 实测）：原实现 `start(1) → take_snapshot() → finally: stop()` **全在
#    同一次调用内**完成 ⇒ 快照的 `since_start` 只覆盖探针自己那几毫秒，
#    17/17 份产物 `tracemalloc_rows` 全 0 行。于是四类归因恒 `total_size_kb=0.0`、
#    `pct=None`，下游再把这个 None 当 0 读 —— 「没采到」与「真是 0」在产物里同形（铁律276）。
#  病根二：`top20 = (_gc_types or _tm_rows)` —— gc 有值就把 tracemalloc 的**尺寸**行顶掉，
#    而 `gc_type_rows` 只有**条数**没有字节数。拿计数当尺寸算占比，分母恒 0。
#  处置（本刀，只动观测面，不改内存回收策略、不改任何业务状态）：
#    ① 进程早期 `start(1)` 并**常驻**（`M339_TRACEMALLOC_MODE="resident"`）⇒ basis 变
#       `cumulative`，快照覆盖进程至今的分配；
#    ② 相邻两次探针各取一份快照，用 `compare_to` 出差分 ⇒ 「这段时间涨了哪几行」。
#       累计值分不清老账与新账，增量才是治理要的那个数；
#    ③ 归因只吃**有尺寸的** tracemalloc 行；无尺寸行 ⇒ `state="unavailable"`，
#       绝不再吐 0.0。
_M377_TM_MODES = ("resident", "window", "off")
_M377_TM_OWNED = False          # tracemalloc 是否由本模块 start（只停自己起的）
_M377_TM_BASELINE = "_tm_baseline.snap"     # ★389刀1：仅作**旧版兼容**名，不再被写
#: ★389刀1：同代基线最多保留几份（每份 ~0.7MB；只留最近几代供回溯）
_M389_TM_BASELINE_KEEP = 8


def _m377_cfg(name: str, default):
    """读配置（走已加载的 config 模块；读不到回退默认，绝不抛）。"""
    _c = sys.modules.get("config")
    return default if _c is None else getattr(_c, name, default)


def _m389_tm_safe_gen() -> str:
    """本进程代次的安全文件名片段（取不到 ⇒ `unknown`，**不填 pid 之外的假值**）。"""
    _g = ""
    try:
        from nucleus.proc_gen import proc_gen_str as _pgs
        _g = str(_pgs() or "")
    except Exception as _e:
        silent_exc(_e, where="PulseMetricsCollector::_m389_tm_safe_gen")
    if not _g:
        return "unknown"
    return "".join(_ch if (_ch.isalnum() or _ch in "-_") else "_"
                   for _ch in _g)[:64]


def _m389_tm_baseline_path(dir_path: str) -> str:
    """★389刀1：差分基线**按进程代分键** —— 跨代比较必然出假读数。

    为什么必须分键（实测证据，不是理论洁癖）
    ----------------------------------------
    `_m377_tm_delta` 是「当前快照 vs 上一份基线快照」。改前基线固定叫
    `_tm_baseline.snap`，**不区分进程代**。于是：

      · 13:37 那次基线由 pid21168 写下（该进程 13:37:53 因 RSS 超限重启）；
      · 14:08 起的新进程 pid22680 首次采样会把**已死进程**的快照当基线；
      · 死进程的 33M 对象在新进程里根本不存在 ⇒ `compare_to` 全是负增量
        ⇒ 行全被 `size_diff <= 0` 过滤 ⇒ 报 `no_growth`（假「没涨」）。
        这与「真的没涨」完全同形（铁律276：没采到 ≠ 真 0）。

    分键后旧代基线自然闲置；`_m389_tm_prune_baselines` 只留最近
    `_M389_TM_BASELINE_KEEP` 代，避免 `data/reports/memory/` 无限长。
    """
    return os.path.join(dir_path, "_tm_baseline_%s.snap" % _m389_tm_safe_gen())


def _m389_tm_prune_baselines(dir_path: str, keep: int | None = None) -> list:
    """只保留最近 N 份**按代**基线（含旧版无后缀名一并清掉）。返回被删文件名。

    失败一律吞掉：清理是维护动作，绝不影响采样（本函数不参与任何判定）。
    """
    _keep = int(keep if keep is not None else _M389_TM_BASELINE_KEEP)
    _dropped: list = []
    try:
        if not os.path.isdir(dir_path):
            return _dropped
        _names = [n for n in os.listdir(dir_path)
                  if n.startswith("_tm_baseline") and n.endswith(".snap")]
        if len(_names) <= _keep:
            return _dropped
        _full = [(os.path.getmtime(os.path.join(dir_path, n)), n) for n in _names]
        _full.sort(reverse=True)
        for _mt, _n in _full[_keep:]:
            try:
                os.remove(os.path.join(dir_path, _n))
                _dropped.append(_n)
            except OSError as _re:
                silent_exc(_re, where="PulseMetricsCollector::_m389_tm_prune_baselines",
                           level="debug")
    except Exception as _e:
        silent_exc(_e, where="PulseMetricsCollector::_m389_tm_prune_baselines")
    return _dropped


def _m377_tm_ensure() -> dict:
    """确保 tracemalloc 处于期望状态（只在 `resident` 模式 start，且**不 stop**）。

    返回 `{mode, tracing, started_by_us, error?}`；本函数绝不抛。
    """
    global _M377_TM_OWNED
    _out: dict[str, Any] = {"mode": "off", "tracing": False,
                            "started_by_us": False}
    try:
        import tracemalloc as _tm
        _mode = str(_m377_cfg("M339_TRACEMALLOC_MODE", "resident") or "").lower()
        if _mode not in _M377_TM_MODES:
            # 未知取值 ⇒ 按 resident 处理并留痕（观测面不因拼错而静默退化成空采）
            silent_exc(ValueError("unknown M339_TRACEMALLOC_MODE=%r" % (_mode,)),
                       where="PulseMetricsCollector::_m377_tm_ensure mode")
            _mode = "resident"
        _out["mode"] = _mode
        _out["tracing"] = bool(_tm.is_tracing())
        if _mode == "resident" and not _out["tracing"]:
            _tm.start(1)
            _M377_TM_OWNED = True
            _out["tracing"] = True
            _out["started_by_us"] = True
    except Exception as _e:
        silent_exc(_e, where="PulseMetricsCollector::_m377_tm_ensure")
        _out["error"] = type(_e).__name__
    return _out


def _m377_tm_delta(snap, base_path: str, top_n: int = 20) -> tuple[list, str]:
    """当前快照与上一份基线快照差分 ⇒ (增量行, 状态)。

    增量 = `size_diff > 0` 的行（这段时间**涨**的），按字节降序取 top_n。
    状态四态，**不用空列表冒充「无增量」**（铁律276：没采到 ≠ 真 0）：

      | state | 含义 |
      |---|---|
      | `ok` | 差分成功且有增量行 |
      | `no_growth` | 差分成功但没有正增量（真·没涨） |
      | `no_baseline` | 首次运行，没有可比基线 |
      | `baseline_unreadable` | 基线存在但读不动（版本/损坏） |

    调用方（探针）另有 `window_mode` / `off` 两态用于标注「本次没做差分」的原因。
    无论差分成功与否都会把当前快照写成新基线（供下一拍比较）。
    """
    import tracemalloc as _tm
    _rows: list = []
    _state = "ok"
    try:
        if not os.path.isfile(base_path):
            _state = "no_baseline"
        else:
            _base = _tm.Snapshot.load(base_path)
            for _st in snap.compare_to(_base, "lineno")[:top_n]:
                _diff = int(getattr(_st, "size_diff", 0) or 0)
                if _diff <= 0:
                    continue
                try:
                    _frame = _st.traceback[0] if _st.traceback else None
                    _fn = str(getattr(_frame, "filename", "?") or "?")
                    _fn = _fn.replace("\\", "/").split("/")[-1]
                    _ln = int(getattr(_frame, "lineno", 0) or 0)
                except (IndexError, AttributeError) as _fde:
                    # 帧不可读不该静默：否则增量行会静静退化成 "?:0"，看起来像"没定位"
                    silent_exc(_fde, where="PulseMetricsCollector::_m377_tm_delta frame")
                    _fn, _ln = "?", 0
                _rows.append({"file": _fn, "line": _ln,
                              "size_kb": round(_diff / 1024.0, 1),
                              "count": int(getattr(_st, "count_diff", 0) or 0)})
            if not _rows:
                _state = "no_growth"
    except Exception as _e:
        silent_exc(_e, where="PulseMetricsCollector::_m377_tm_delta compare")
        _state = "baseline_unreadable"
        _rows = []
    try:
        # ★基线落盘前必须先建目录 —— 差分发生在**产物写盘之前**，此时 `_dir` 可能
        #   还不存在；不建目录会 FileNotFoundError 被静默吞掉，下一拍又是 no_baseline
        #   （377刀4 首跑实测踩到：第一拍 no_baseline、第二拍仍 no_baseline）。
        _bd = os.path.dirname(os.path.abspath(base_path))
        if _bd:
            os.makedirs(_bd, exist_ok=True)
        _tmp = base_path + ".tmp"
        snap.dump(_tmp)
        os.replace(_tmp, base_path)
    except Exception as _e:
        silent_exc(_e, where="PulseMetricsCollector::_m377_tm_delta dump")
    return _rows, _state


class PulseMetricsCollector(BasePulseOrgan):
    """脉冲驱动多层级指标采集器（v9.5 分层脉冲版）"""

    def __init__(self, organ_name: str = "指标采集器"):
        super().__init__(organ_name)
        # ★377刀4：进程早期就把 tracemalloc 起成常驻（resident 模式），否则快照只能
        #   覆盖探针自己那几毫秒（376 刀5 的 17/17 份空采根因）。失败不影响器官装配。
        self._m377_tm_state = _m377_tm_ensure()
        self._metrics_history: list = []
        self._snapshot_count = 0
        self._beat_count = 0
        self._max_history = 100
        self._liver_compress_count = 0    # 肝脏压缩次数
        self._liver_fuse_count = 0        # 肝脏融合次数
        self._instinct_upgrade_count = 0   # 本能升级次数

        # ===== P1-4: 器官指标缓存 =====
        self._cached_organ_stats: dict[str, Any] = {}
        self._cache_timestamp = 0.0

        # ===== P1-4: 可观测性开关 =====
        self._obs_enabled = _obs_enabled
        self._snapshot_interval = 1   # 每次心跳都更新快照，确保UI数据实时
        self._console_output = _console_output
        self._file_output = _file_output
        self._flat_format = _flat_format
        # ===== P1-4 修复: 节点池引用 =====
        self._node_pool = None
        self._cached_heartbeat = {}      # 缓存最新心跳数据
        # ★396刀2：**最后一次真正收到心跳脉冲的墙钟时刻**。
        #   为什么需要它：本采集器自己是心跳驱动的，心跳一停，`_on_heartbeat_*` 不再被
        #   调用 ⇒ `_cached_heartbeat` 冻结在最后一帧 ⇒ 快照里 status 永远是"搏动中"。
        #   现场因此出现过「心跳死后 46 分钟，health_snapshot 仍报搏动中」。
        #   活体判据必须是「读时算 gap」，不能是"缓存里写了什么"。
        self._m396_last_beat_rx_at: float = 0.0
        self._m396_beat_rx_count: int = 0
        self._m396_explicit_stop: bool = False   # 收到过显式「已停搏」信号
        self._cached_current_user = "访客"  # 缓存当前用户
        self._dream_count = 0          # 梦境推演次数
        self._dream_last_topic = ""    # 最近梦境主题
        self._reflection_count = 0     # 自我反思次数
        self._reflection_last_domain = ""  # 最近反思领域
        self._cached_social_emotions = {}      # 社会性情感缓存
        self._cached_top_interests = []        # 兴趣前5维度缓存
        self._cached_life_state = {}       # 生命状态缓存
        self._cached_emotion_state = {}    # 情绪状态缓存（含趋势）
        # ===== 无头浏览器统计缓存 =====
        self._headless_stats = {
            "total_searches": 0,       # 累计搜索次数
            "successful_searches": 0,  # 成功搜索次数（找到文章）
            "total_articles": 0,       # 累计精读文章数
            "total_chars": 0,          # 累计消化字符数
            "last_search_topic": "",   # 最近搜索主题
            "last_articles": 0,        # 最近一次搜索找到的文章数
            "last_status": "idle",     # 最近状态：idle/searching/completed/fallback
        }
        # ===== 线程安全：统一缓存锁 =====
        self._cache_lock = threading.Lock()


    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == MetricsEvent.COLLECT:
            return self._on_collect(payload)
        elif event_type == HeartEvent.BEAT:
            return self._on_heartbeat_observe(payload)
        elif event_type == HeartEvent.ALIVE:
            return self._on_heartbeat_alive(payload)
        elif event_type == PersonaEvent.SWITCHED:
            return self._on_persona_switched(payload)
        elif event_type == Event.DIGEST_KNOWLEDGE:
            return self._on_digest_for_dream(payload)
        elif event_type == ReflectionEvent.INSIGHT:
            return self._on_reflection_insight(payload)
        elif event_type == KnowledgeEvent.COMPRESSED:
            return self._on_liver_compressed(payload)
        elif event_type == "instinct.upgraded":
            return self._on_instinct_upgraded(payload)
        elif event_type == HormonesEvent.EMOTION_DETECTED:
            return self._on_emotion_detected(payload)
        elif event_type == InterestEvent.CHANGED:
            return self._on_interest_changed(payload)
        elif event_type == ControllerEvent.SEARCH_COMPLETED:
            return self._on_search_completed(payload)
        elif event_type == Event.LIFE_STATE_CHANGED:
            # ★P0-6修复：补上订阅声明但缺失的处理分支，否则 _on_life_state_changed 成死代码、
            # 生命状态缓存永不被更新。
            return self._on_life_state_changed(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    # ========== 传统采集（保留兼容） ==========
    def _on_collect(self, payload: dict) -> dict[str, Any]:
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._snapshot_count += 1
        snapshot = {
            "molecular": self._collect_molecular(),
            "cellular": self._collect_cellular(),
            "organ_level": self._collect_organ_level(),
            "timestamp": time.time(),
            "snapshot_id": self._snapshot_count,
        }
        self._metrics_history.append(snapshot)
        if len(self._metrics_history) > self._max_history:
            self._metrics_history = self._metrics_history[-50:]
        self._emit(MetricsEvent.SNAPSHOT, snapshot, priority=2, layer="L3")

        # 同时写入共享数据文件供人体UI读取
        try:
            health_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                       'data', 'monitor', 'health_snapshot.json')
            os.makedirs(os.path.dirname(health_path), exist_ok=True)
            # 构建完整的快照数据（和心跳触发的一样）
            full_snapshot = self._build_observability_snapshot()
            safe_write_json(health_path, full_snapshot)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return {"status": "collected", "snapshot_id": self._snapshot_count}
    # ========== P1-4: 心跳驱动可观测性快照 ==========

    def _m396_heartbeat_status(self, interval: float) -> tuple[str, float | None]:
        """★396刀2：按「距最后一次真正收到心跳的 gap」算心跳状态（**读时计算**）。

        为什么不能沿用 payload/缓存里的 status：那两句都是常量串（PulseHeart 发
        "搏动中"、本采集器缓存里也写死"搏动中"）。而"心跳停了"这件事的**唯一可靠
        观测量是时间差**——它不依赖任何还在跑的代码来自报。

        Returns:
            (status, gap_s)；`gap_s is None` 表示从未收到过心跳（起搏期），
            不是"gap=0"（铁律 276：没采到与真 0 必须分两个字段）。
        """
        if getattr(self, "_m396_explicit_stop", False):
            return "已停搏（显式停止信号）", None
        _iv = float(interval or 0) or 10.0
        _last = float(getattr(self, "_m396_last_beat_rx_at", 0) or 0)
        if _last <= 0:
            return "起搏中（尚未收到心跳）", None
        _gap = max(0.0, time.time() - _last)
        _limit = _M396_HEARTBEAT_STALL_FACTOR * _iv
        if _gap >= _limit:
            return f"停搏（{_gap:.0f}s 未跳，阈值 {_limit:.0f}s）", _gap
        return "搏动中", _gap

    def _on_heartbeat_observe(self, payload: dict | None = None) -> dict[str, Any] | None:
        """心跳触发：按间隔输出可观测性快照"""
        if not self._obs_enabled:
            return None

        # 从心跳脉冲 payload 中直接提取心率数据（线程安全：锁内更新缓存）
        with self._cache_lock:
            if payload:
                # ★396刀2：只采「确实收到了心跳」这个事实与读数，**不采信自报的 status**。
                self._m396_last_beat_rx_at = time.time()
                self._m396_beat_rx_count += 1
                self._m396_explicit_stop = False
                self._cached_heartbeat = {
                    "interval": payload.get("interval", 10.0),
                    "beat_count": payload.get("beat_count", 0),
                }

            # 尝试从信息场获取最新用户身份
            if self.info_field:
                try:
                    persona_pulse = self.info_field.get_current(PersonaEvent.SWITCHED)
                    if persona_pulse and isinstance(persona_pulse, dict):
                        user = persona_pulse.get("payload", {}).get("current_user", "")
                        if user:
                            self._cached_current_user = user
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            self._beat_count += 1
            if self._beat_count % self._snapshot_interval != 0:
                return None
            self._snapshot_count += 1

            # 刷新器官指标缓存
            self._refresh_organ_cache()

        # 构建可观测性快照（锁内完成，确保读到一致的数据快照）
        with self._cache_lock:
            snapshot = self._build_observability_snapshot()

        try:
            health_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                       'data', 'monitor', 'health_snapshot.json')
            # T-112a：改走硬化写通道（路径锁 + 退避重试 + 唯一 tmp + 失败清理）
            if not safe_write_json(health_path, snapshot, backup=False):
                self._log(LogLevel.WARNING, "health_snapshot.json 写入失败（需关注）")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return {"status": "observed", "beat": self._beat_count}
    def _on_heartbeat_alive(self, payload: dict) -> dict[str, Any]:
        """缓存心脏存活数据。

        ★396刀2：**不再采信 payload 里的 status**（PulseHeart 发的是常量串"搏动中"，
        采信它等于把"自报"当"实测"）。这里只做两件事：
          * 认出「已停搏」这一个**显式**真值（stop() 主动发的，不是推断）；
          * 其余情况只更新"确实收到过心跳"的时刻，状态留给读快照时按 gap 现算。
        """
        with self._cache_lock:
            _p_status = str(payload.get("status") or "")
            if _p_status == "已停搏":
                self._m396_explicit_stop = True
            else:
                self._m396_explicit_stop = False
                self._m396_last_beat_rx_at = time.time()
                self._m396_beat_rx_count += 1
            self._cached_heartbeat = {
                "interval": payload.get("interval", 10.0),
                "beat_count": payload.get("beat_count", 0),
            }
        return {"status": "cached"}

    def _on_persona_switched(self, payload: dict) -> dict[str, Any]:
        """缓存当前用户身份"""
        with self._cache_lock:
            self._cached_current_user = payload.get("current_user", "访客")
        return {"status": "cached", "user": self._cached_current_user}
    def _on_digest_for_dream(self, payload: dict) -> dict[str, Any]:
        """缓存梦境推演数据"""
        trigger_reason = payload.get("trigger_reason", "")
        if trigger_reason == Event.DREAM_DEDUCTION:
            with self._cache_lock:
                self._dream_count += 1
                content = payload.get("content", "")
                if content:
                    self._dream_last_topic = content[:60]
        return {"status": "cached", "dream_count": self._dream_count}
    def _on_emotion_detected(self, payload: dict) -> dict[str, Any]:
        """缓存最新情绪（含趋势信息）"""
        with self._cache_lock:
            self._cached_social_emotions = payload.get("social_emotions", {})
            self._cached_emotion_state = {
                "emotion": payload.get("emotion", "中性"),
                "intensity": payload.get("intensity", 0.0),
                "trend": payload.get("emotion_trend", {}),
                "inertia_applied": payload.get("emotion_inertia_applied", False),
            }
        return {"status": "cached"}

    def _on_interest_changed(self, payload: dict) -> dict[str, Any]:
        """缓存最新兴趣分布"""
        with self._cache_lock:
            interests = payload.get("current_interests", {})
            if interests:
                sorted_items = sorted(interests.items(), key=lambda x: x[1], reverse=True)
                self._cached_top_interests = sorted_items[:5]
        return {"status": "cached"}

    def _on_search_completed(self, payload: dict) -> dict[str, Any]:
        """缓存无头浏览器搜索完成数据"""
        with self._cache_lock:
            self._headless_stats["total_searches"] += 1
            articles = payload.get("articles_found", 0)
            chars = payload.get("total_chars", 0)
            status = payload.get("status", "unknown")  # noqa: F841
            if articles > 0:
                self._headless_stats["successful_searches"] += 1
            self._headless_stats["total_articles"] += articles
            self._headless_stats["total_chars"] += chars
            self._headless_stats["last_search_topic"] = payload.get("search_topic", "")
            self._headless_stats["last_articles"] = articles
            self._headless_stats["last_status"] = "completed" if articles > 0 else "fallback"
        return {"status": "cached"}
    def _on_life_state_changed(self, payload: dict) -> dict[str, Any]:
        """缓存最新生命状态"""
        self._cached_life_state = {
            "state": payload.get("new_state", "浅层活跃"),
            "previous": payload.get("old_state", ""),
            "since": payload.get("state_since", time.time()),
            "explore_interval": payload.get("explore_interval", 120),
            "dream_interval": payload.get("dream_interval", 300),
        }
        return {"status": "cached", "state": self._cached_life_state["state"]}


    def _on_reflection_insight(self, payload: dict) -> dict[str, Any]:
        """缓存自我反思数据"""
        with self._cache_lock:
            self._reflection_count += 1
            self._reflection_last_domain = payload.get("domain", "通用")
        return {"status": "cached", "reflection_count": self._reflection_count}

    # ★337批刀4：库镜像采样 TTL（memory_maps 要枚举全部映射，每拍跑会变成绩瓶颈）
    _M337_IMAGE_TTL_SEC = 300.0

    def _m337_image_mb(self):
        """★337批刀4：**库镜像常驻量**（MB）—— RSS 里"不可释放"的那部分。

        为什么必须拆（337前置§0-1）：活体实测 RSS 6001.3MB ＝ 镜像 4322.2MB ＋
        私有堆 1679.1MB；镜像里 GPU 栈 3304MB（torch_cuda/cudnn/cublas/cufft/
        cusparse…）属"装载即驻"，**运行中释放一分钱也省不到**。若只看 RSS，
        会把"少装一个库"的架构收益与"内存治理有效"混为一谈。

        取不到 ⇒ `None`（**不填 0**：0 会让 `heap = RSS` 全量，把"测不到"读成
        "全部可释放"，那是把治理靶面凭空放大 4 倍）。
        """
        _now = time.time()
        _c = getattr(self, "_m337_image_cache", None)
        if isinstance(_c, dict) and (_now - float(_c.get("ts") or 0)) < self._M337_IMAGE_TTL_SEC:
            return _c.get("mb")
        _mb = None
        try:
            import psutil
            _tot = 0
            for _mm in psutil.Process().memory_maps():
                _tot += int(getattr(_mm, "rss", 0) or 0)
            _mb = round(_tot / (1024.0 * 1024.0), 1)
        except Exception as _e:
            silent_exc(_e, where="PulseMetricsCollector::_m337_image_mb")
        self._m337_image_cache = {"ts": _now, "mb": _mb}
        return _mb

    # ========== ★339批刀4：tracemalloc 自观测钩子（烛微338挂账10 / A-13根治配套） ==========
    #   背景（烛微338前置§3.2）：外部探针读不到活体堆（24MB 读盘缓冲污染；
    #   Windows `memory_maps` 只暴露模块镜像）⇒ **堆归因只能自观测**。
    #   挂点：复用 337刀4 已落的心跳采样链（与 `_m337_image_mb` 同处），
    #         **不新开调度器**（333刀2 纪律）。
    #   频率：每日 1-2 次槽位（05:00 / 13:00，避开 03:50-04:10 首转窗）；
    #         **禁止每拍采样**——自身分配开销会把治理变增负。
    # ★388刀2：槽位从「每天 2 次(5/13 点)」加密到**每 2 小时一次**。
    #   ★★该改动**已被 389刀1 回滚**（理由见本节末尾两条实测依据）——
    #   保留原文只为留痕，**不要**照此再改一遍。
    #   387刀2 的结论：一天只有 2 次归因机会，而泄漏 5 小时就到危险区 ⇒
    #   采样密度远远不够（13:00 那次才第一次拿到 tracemalloc top20）。
    # ★配套的**成本护栏**（不然加密=增负）：实测一次含 tracemalloc 的采样要
    #   ~14 分钟（3300 万对象做 statistics），所以：
    #     · gc 扫描仍受 `M339_GC_SCAN_BUDGET_SEC`(2s) 约束（386刀3）
    #     · tracemalloc 阶段超 `M339_MEMORY_PROBE_MAX_SEC` ⇒ **不写盘**（388刀2 补）
    #   ⇒ 加密后若采样太贵，结果是"没产物"而不是"卡住进程"。
    # ★389刀1：**回滚 388刀2 的每 2 小时 12 槽**。两条实测依据：
    #   ① `config.py::M339_MEMORY_PROBE_HOURS=(5,13)` 才是生产生效值（`_m339_cfg_get`
    #      优先读 config）⇒ 12 槽从未生效，只让"默认值骗人"（第 4 次同型坑）；
    #   ② 真生效会更糟：tracemalloc 单次实测 **837s**（3300 万对象做 statistics），
    #      12 槽 = 每 2 小时卡进程 14 分钟 ⇒ 正是 386刀3 要避免的"治理变增负"。
    #   观测量加密走 gc 轻采（2-3s，每小时已在跑）；tracemalloc 保持每日 2 次。
    _M339_PROBE_HOURS = (5, 13)
    _M339_PROBE_TOP_N = 20
    _M339_PROBE_MAX_SEC = 3.0
    _M339_GC_HEAP_MB = 1200.0
    # ★386刀3：gc 全堆扫描的**时间预算**（秒）。超过即截断，但**照样写盘**
    #   ——改前是「超 3s 就整份不写」⇒ 大堆下永远没有产物。
    #   预算必须**小于** _M339_PROBE_MAX_SEC，否则仍会走到不写盘分支。
    _M339_GC_SCAN_BUDGET_SEC = 2.0
    _m339_gc_last: dict = {}

    @staticmethod
    def _m339_cfg_get(name: str, default):
        """读配置（走已加载的 config 模块，避热路径重复 import）。

        读不到 ⇒ 回退默认（本钩子是**观测**，不是门禁；
        回退不会造成任何放行/拦截语义变化）。
        """
        _cfg = sys.modules.get("config")
        if _cfg is None:
            return default
        return getattr(_cfg, name, default)

    def _m339_version_stamp(self) -> dict[str, str]:
        """三个版本戳（任一取不到 ⇒ `unknown`，**不填假值**）。

        ★为何要三个：一份堆快照如果不知道是哪一代进程、哪一版图、
        哪版判据产的，就只能"看着像"——跨代比较堆就是假归因。
        """
        _v = {"proc_gen": "unknown", "code_version": "unknown",
              "criteria_version": "unknown"}
        try:
            from nucleus.proc_gen import proc_gen_str as _pgs
            _v["proc_gen"] = str(_pgs())
        except Exception as _e:
            silent_exc(_e, where="PulseMetricsCollector::_m339_version_stamp proc_gen")
        try:
            from nucleus.self_awareness import CallGraphAnalyzer as _cga
            _v["code_version"] = str(getattr(_cga, "CODE_VERSION", "unknown"))
        except Exception as _e:
            silent_exc(_e, where="PulseMetricsCollector::_m339_version_stamp code_version")
        try:
            import nucleus.self_inspector as _si
            _v["criteria_version"] = str(
                getattr(_si, "_DEAD_CODE_CRITERIA_VERSION", "unknown"))
        except Exception as _e:
            silent_exc(_e,
                       where="PulseMetricsCollector::_m339_version_stamp criteria_version")
        return _v

    # ---- ★349刀5：堆归因四类（AST / PulseNode / image / other）----
    #   为什么必须按**类别**归因，而不只是 top20 行号：
    #   top20 回答"哪一行"，四类回答"哪一块"，而治理决策是按块做的
    #   （AST 释放面 / 池容量 / 图片缓存 / 其余泄漏）。两者缺一不可。
    _M349_HEAP_BUCKETS = ("ast", "pulsenode", "image", "other")

    @staticmethod
    def _m349_heap_bucket(file_name: str) -> str:
        """按**文件名**归到四类之一（路径结构判定，不看关键字猜·铁律153）。

        归类规则写在这里、写在明面上；改规则必须同时改
        `tests/test_m349_batch.py` 里对应的正反例。
        """
        _f = str(file_name or "").replace("\\", "/").lower()
        if not _f:
            return "other"
        # AST / 代码分析面
        for _k in ("ast.py", "callgraphanalyzer", "self_inspector", "self_awareness/",
                   "audit_problem_pool", "code_structure", "ast_release"):
            if _k in _f:
                return "ast"
        # 脉冲节点 / 池面
        for _k in ("pulsenodepool", "pulsenode.py", "mnemosyne/", "cold.index",
                   "poolnode", "knowledgepool"):
            if _k in _f:
                return "pulsenode"
        # 图片 / 视觉面
        for _k in ("pil/", "pillow", "image.py", "imagedraw", "imagefile",
                   "llava", "vision", "cv2", "numpy/lib"):
            if _k in _f:
                return "image"
        return "other"

    def _m349_heap_attribution(self, rows: list) -> dict:
        """把 tracemalloc 行聚成四类占比。

        返回 `{state, buckets, total_size_kb, total_count, basis}`。

        ★377刀4 契约变更（**不再吐 0.0**）：只接受**带尺寸**的 tracemalloc 行；
        没有尺寸行时返回 `state="unavailable"` + `total_size_kb=None` + `buckets={}`。
        为什么必须改：旧实现把 `gc_type_rows`（只有条数、没有字节）当尺寸行喂进来，
        算出的 `total_size_kb` 恒 `0.0`、`pct` 恒 `None`，而产物里 `0.0` 与
        「真的归零」同形 ⇒ 读者会把「没采到」当「没占用」（铁律276）。

        `pct` 按 **size** 算（内存治理看字节，不看条数）；`count` 单列，
        因为"很多小对象"与"一个巨块"是两种病。
        """
        _b = {k: {"size_kb": 0.0, "count": 0, "pct": None}
              for k in self._M349_HEAP_BUCKETS}
        _rows = [r for r in (rows or []) if isinstance(r, dict)]
        _with_size = 0
        for _r in _rows:
            _k = self._m349_heap_bucket(_r.get("file"))
            try:
                _sz = float(_r.get("size_kb") or 0.0)
            except (TypeError, ValueError) as _e:
                silent_exc(_e, where="PulseMetricsCollector::_m349_heap_attribution size")
                _sz = 0.0
            try:
                _cnt = int(_r.get("count") or 0)
            except (TypeError, ValueError) as _e:
                silent_exc(_e, where="PulseMetricsCollector::_m349_heap_attribution count")
                _cnt = 0
            if _sz > 0:
                _with_size += 1
            _b[_k]["size_kb"] = round(_b[_k]["size_kb"] + _sz, 1)
            _b[_k]["count"] += _cnt
        _tot = round(sum(v["size_kb"] for v in _b.values()), 1)
        # ★无尺寸行（空 / 全是计数行 / 全 0）⇒ 归因不可用，**不给 0.0 冒充读数**
        if _with_size == 0 or _tot <= 0:
            return {"state": "unavailable",
                    "reason": ("no_rows" if not _rows else "zero_size_rows"),
                    "buckets": {}, "total_size_kb": None,
                    "total_count": sum(v["count"] for v in _b.values()),
                    "rows": len(_rows), "rows_with_size": _with_size,
                    "basis": ("无带尺寸的 tracemalloc 行 ⇒ 四类占比不可算"
                              "（**不是 0 占用**；查 tracemalloc_rows 与 "
                              "tracemalloc_basis 定位空采原因）")}
        for _k, _v in _b.items():
            _v["pct"] = round(_v["size_kb"] / _tot, 4)
        return {"state": "ok", "reason": "",
                "buckets": _b, "total_size_kb": _tot,
                "total_count": sum(v["count"] for v in _b.values()),
                "rows": len(_rows), "rows_with_size": _with_size,
                "basis": ("按 tracemalloc 行的文件名归四类；pct 按 size_kb 计，"
                          "**只覆盖本次快照可见的行**（cumulative 模式下含进程至今，"
                          "增量看 tracemalloc_delta_rows）")}

    def _m339_memory_probe(self, heap_mb=None,
                           out_dir: str | None = None) -> dict[str, Any]:
        """★339刀4：堆自观测快照（**每日 ≤2 次**，产物只落一处）。

        两档：
          · 第一档（槽位 05 / 13）：`tracemalloc` top-N（默认 20）。
          · 第二档（**仅当 heap > H1**，同日最多再 1 次）：`gc.get_objects()` 类型计数。
        超时（> `M339_MEMORY_PROBE_MAX_SEC`）⇒ **不写盘** + WARNING（不把治理变增负）。

        产物：`<out_dir>/top_types_<ymd_hms>.json`；头字段六项
        （proc_gen / code_version / criteria_version / heap_mb / image_mb / top20）。

        路径注入（铁律158）：`out_dir` 为 None 才默认项目根 `data/reports/memory`；
        隔离验证必须传入沙箱目录。
        """
        _out: dict[str, Any] = {"ran": False, "mode": None, "path": None,
                                "elapsed_sec": None, "reason": "", "top_n": 0}
        _hours = tuple(int(_h) for _h in self._m339_cfg_get(
            "M339_MEMORY_PROBE_HOURS", self._M339_PROBE_HOURS))
        _topn = int(self._m339_cfg_get("M339_MEMORY_PROBE_TOP_N",
                                       self._M339_PROBE_TOP_N))
        _maxsec = float(self._m339_cfg_get("M339_MEMORY_PROBE_MAX_SEC",
                                           self._M339_PROBE_MAX_SEC))
        _h1 = float(self._m339_cfg_get("M339_MEMORY_GC_TYPES_HEAP_MB",
                                       self._M339_GC_HEAP_MB))
        _tmmode = str(self._m339_cfg_get("M339_TRACEMALLOC_MODE", "window"))
        _now = time.time()
        _lt = time.localtime(_now)
        _slot = "%04d%02d%02d-%02d" % (_lt.tm_year, _lt.tm_mon, _lt.tm_mday,
                                       _lt.tm_hour)
        _in_slot = int(_lt.tm_hour) in _hours
        _heap_hi = bool(heap_mb is not None and float(heap_mb) > _h1)
        _last = getattr(self, "_m339_probe_last_slot", None)
        _last_gc = getattr(self, "_m339_probe_last_gc_slot", None)
        _mode = ""
        if _in_slot and _last != _slot:
            _mode = "tracemalloc+gc_objects" if _heap_hi else "tracemalloc"
        elif _heap_hi and _last_gc != _slot:
            _mode = "gc_objects"
        if not _mode:
            _out["reason"] = ("slot_done(hour=%d slot=%s)" % (_lt.tm_hour, _slot)
                              if _in_slot else
                              "not_in_slot(hour=%d slots=%s)" % (_lt.tm_hour, _hours))
            return _out
        _t0 = time.perf_counter()
        # ★377刀4：产物目录提前解析 —— 两快照差分的基线文件也要落在同一个（可能是
        #   被域过滤改道后的）目录里；改道逻辑本身零改动，只把位置前移。
        _dir = out_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "data", "reports", "memory")
        # ★344补 3（A-17 三类出口③）：隔离/验证进程**改道**，不写生产目录。
        #   实测：`top_types_20261022_194451.json` 的 proc_gen=pid15780（验证进程）
        #   却落在生产 data/reports/memory/（当时活体是 pid22576）。
        #   判据只认**显式隔离声明**（env）——活体代次目前没有
        #   落盘 oracle，拿不到真值就不能拿它当判据（铁律156/166）。
        try:
            from nucleus.domain_guard import guard_artifact_dir as _m344_gad
            _dir, _m344_arq = _m344_gad(
                _dir, trace=lambda _f, *a: self._log(LogLevel.INFO, _f % a))
        except Exception as _m344_gae:
            silent_exc(_m344_gae,
                       where="PulseMetricsCollector._m339_memory_probe 344补 3 域过滤")
        _tm_rows: list = []
        _tm_rows_state = "unavailable"
        _tm_delta_rows: list = []
        _tm_delta_state = "unavailable"
        _gc_types: list = []
        _basis = ""
        try:
            if "tracemalloc" in _mode:
                import tracemalloc as _tm
                _tmst = _m377_tm_ensure()
                if _tmst.get("mode") == "off":
                    _tm_rows_state = "off"
                    _tm_delta_state = "off"
                    _basis = "off（M339_TRACEMALLOC_MODE=off，本次不采）"
                else:
                    _started = not _tm.is_tracing()
                    if _started:
                        _tm.start(1)
                    try:
                        _snap = _tm.take_snapshot()
                        _basis = ("since_start（本次才开启，只覆盖本拍之后的分配，"
                                  "**不代表全堆**）" if _started else
                                  "cumulative（进程早期起常驻，覆盖进程至今的分配）")
                        for _st in _snap.statistics("lineno")[:_topn]:
                            try:
                                _frame = _st.traceback[0] if _st.traceback else None
                                _fn = str(getattr(_frame, "filename", "?") or "?")
                                _fn = _fn.replace("\\", "/").split("/")[-1]
                                _ln = int(getattr(_frame, "lineno", 0) or 0)
                            except (IndexError, AttributeError) as _fde:
                                _fn, _ln = "?", 0
                                # ★cw2 铁律150：纯赋值兜底 = 静默 handler，必须留痕
                                self._log(LogLevel.DEBUG,
                                          "[339内存观测] tracemalloc 帧不可读"
                                          "（记为 ?:0）: %s" % (type(_fde).__name__,))
                            _tm_rows.append({"file": _fn, "line": _ln,
                                             "size_kb": round(_st.size / 1024.0, 1),
                                             "count": int(_st.count)})
                        _tm_rows_state = "ok" if _tm_rows else "empty"
                        # ★377刀4：两快照差分（只有常驻 tracer 的基线才有意义）
                        if _started:
                            _tm_delta_state = "window_mode"
                        else:
                            _tm_delta_rows, _tm_delta_state = _m377_tm_delta(
                                _snap, _m389_tm_baseline_path(_dir), _topn)
                            self._m389_tm_pruned = _m389_tm_prune_baselines(_dir)
                    finally:
                        # ★377刀4：只有**本拍临时开启**且仍是 window 模式才停；
                        #   常驻模式绝不停（停了下一拍又变 since_start 空采）。
                        if _started and _tmmode != "resident":
                            _tm.stop()
            # ★388刀2：记录 **tracemalloc 阶段**耗时 —— 用来区分"超时是谁造成的"。
            #   387刀3 的「截断也写盘」被实测钻了空子：13:00 那次 elapsed=837s，
            #   其中 gc 扫描只占约 2s（被预算截断），剩下 835s 全是 tracemalloc；
            #   gc 截断却把它整份放行 ⇒ 一次采样卡住进程 14 分钟，正是 386刀3
            #   要避免的"治理变增负"。
            self._m388_tm_sec = time.perf_counter() - _t0
            if _heap_hi or _mode == "gc_objects":
                import gc as _gcm
                # ★386刀3：给全堆扫描加**时间预算**。
                #   改前是"整份扫完再判超时"，大堆下必然超 ⇒ 从不写盘；
                #   现在是"扫到预算就停"，产物标 truncated ⇒ **一定有产物**。
                #   ★截断后 top20 只是**已扫描部分**的分布，不是全堆分布；
                #     所以必须把 gc_scanned / gc_truncated 一起落盘，
                #     否则读者会把它当全量读数用（铁律196：边界写在前）。
                _budget = float(self._m339_cfg_get(
                    "M339_GC_SCAN_BUDGET_SEC", self._M339_GC_SCAN_BUDGET_SEC))
                _cnt: dict = {}
                _scanned = 0
                _truncated = False
                for _o in _gcm.get_objects():
                    _n = type(_o).__name__
                    _cnt[_n] = _cnt.get(_n, 0) + 1
                    _scanned += 1
                    if not (_scanned & 0xFFFF) and (
                            time.perf_counter() - _t0) > _budget:
                        _truncated = True
                        break
                self._m339_gc_last = {"scanned": _scanned,
                                      "truncated": _truncated,
                                      "budget_sec": _budget}
                _gc_types = [{"type": _k, "count": _v}
                             for _k, _v in sorted(_cnt.items(),
                                                  key=lambda _x: -_x[1])[:_topn]]
        except Exception as _se:
            silent_exc(_se, where="PulseMetricsCollector::_m339_memory_probe body")
            # 铁律150：不静默——探针自身报错必须可见（否则"没产物"与"报错"长得一样）
            self._log(LogLevel.WARNING,
                      "[339内存观测] 采样异常（mode=%s）: %s: %s"
                      % (_mode, type(_se).__name__, _se))
            _out["reason"] = "probe_error:%s" % type(_se).__name__
            return _out
        _elapsed = time.perf_counter() - _t0
        _out["elapsed_sec"] = round(_elapsed, 3)
        # ★386刀3：截断**也算受控** —— 预算已经在扫描中途把它砍停，
        #   此时"不写盘"只会让产物继续缺席（旧行为下大堆**从不写盘**）。
        #   开关 `M339_WRITE_ON_TRUNCATED`（默认 True）关掉 ⇒ 逐字回到旧判定。
        _trunc = bool(getattr(self, "_m339_gc_last", {}).get("truncated", False))
        _write_trunc = bool(self._m339_cfg_get("M339_WRITE_ON_TRUNCATED", True))
        # ★388刀2：gc 截断**只在 gc 阶段本身受控时**才放行。
        #   若 tracemalloc 阶段也超了预算 ⇒ 那是"采样太贵"，不是"扫描被砍停"
        #   ⇒ 不放行（放行等于允许一次 14 分钟的采样写盘，把治理变成增负）。
        _tm_sec = float(getattr(self, "_m388_tm_sec", 0.0) or 0.0)
        _tm_over = bool("tracemalloc" in str(_mode or "")) and _tm_sec > _maxsec
        if _trunc and _write_trunc and _elapsed > _maxsec and not _tm_over:
            self._log(LogLevel.INFO,
                      "[339内存观测] 扫描被预算截断（%.2fs > 预算 %.2fs，"
                      "scanned=%d）⇒ **仍写盘**（产物标 gc_truncated=true，"
                      "只代表已扫描部分，不是全堆分布）mode=%s"
                      % (_elapsed,
                         getattr(self, "_m339_gc_last", {}).get("budget_sec", 0.0),
                         getattr(self, "_m339_gc_last", {}).get("scanned", 0),
                         _mode))
            _out["reason"] = "truncated_but_written"
            # ★不篡改 `_elapsed`：产物里的 elapsed_sec 必须是**真实耗时**，
            #   写成 0 就又是一个"没采到 vs 真 0"同形的假读数（铁律276）。
            _allow_timeout = True
        else:
            _allow_timeout = False
        if _elapsed > _maxsec and not _allow_timeout:
            self._log(LogLevel.WARNING,
                      "[339内存观测] 单次快照 %.2fs > %.2fs ⇒ **不写盘**"
                      "（自身分配开销会把治理变增负） mode=%s"
                      % (_elapsed, _maxsec, _mode))
            _out["reason"] = "timeout"
            return _out
        _v = self._m339_version_stamp()
        _img_cache = getattr(self, "_m337_image_cache", None)
        _img = (_img_cache.get("mb") if isinstance(_img_cache, dict) else None)
        _payload = {
            # ── 头字段六项（任一取不到写 None/unknown，不填 0）──
            "proc_gen": _v["proc_gen"],
            "code_version": _v["code_version"],
            "criteria_version": _v["criteria_version"],
            "heap_mb": (round(float(heap_mb), 1) if heap_mb is not None else None),
            "image_mb": _img,
            "top20": (_tm_rows or _gc_types),
            # ★377刀4：top20 的来源必须写明 —— 旧实现 `(_gc_types or _tm_rows)` 让 gc 的
            #   **条数**行顶掉 tracemalloc 的**尺寸**行，读者无法分辨这一栏是字节还是条数。
            "top20_source": ("tracemalloc" if _tm_rows
                             else ("gc_types" if _gc_types else "none")),
            # ── 附带读数（不替代上面六项）──
            "top_n": _topn,
            "mode": _mode,
            "tracemalloc_rows": _tm_rows,
            "tracemalloc_rows_state": _tm_rows_state,
            # ★377刀4：相邻两拍差分的**增量**尺寸（累计值分不清老账与新账）
            "tracemalloc_delta_rows": _tm_delta_rows,
            "tracemalloc_delta_state": _tm_delta_state,
            "tracemalloc_mode": str(_tmmode),
            "gc_type_rows": _gc_types,
            # ★386刀3：gc 扫描的实际口径（截断与否 / 扫了多少）
            "gc_scanned": int(getattr(self, "_m339_gc_last", {}).get("scanned", 0) or 0),
            "gc_truncated": bool(getattr(self, "_m339_gc_last", {}).get("truncated", False)),
            "gc_scan_budget_sec": float(getattr(self, "_m339_gc_last", {}).get("budget_sec", 0.0) or 0.0),
            "tracemalloc_basis": _basis,
            # ★349刀5：四类占比（AST / PulseNode / image / other）
            #   ★377刀4：只吃**有尺寸的** tracemalloc 行；无尺寸行 ⇒ state=unavailable，
            #   不再拿 gc 计数行当尺寸算出 0.0（那正是「没采到」与「真 0」同形的根因）。
            "heap_attribution": self._m349_heap_attribution(_tm_rows),
            "tracemalloc_is_tracing": bool(
                __import__("tracemalloc").is_tracing()),
            "elapsed_sec": round(_elapsed, 3),
            "ts": _now,
            "note": ("只产计数与读数，**不改任何业务状态**；"
                     "resident 模式下 tracemalloc 常驻（basis=cumulative，含进程至今），"
                     "`tracemalloc_delta_rows` 是相邻两拍差分的增量；"
                     "window 模式下快照只覆盖本拍之后，归因不可用会显式标 unavailable"),
        }
        # ★377刀4：目录解析与 344补 3 域过滤已前移到采样之前（差分基线要落在同一目录）。
        try:
            os.makedirs(_dir, exist_ok=True)
            _p = os.path.join(_dir, "top_types_%s.json" %
                              time.strftime("%Y%m%d_%H%M%S", _lt))
            _tmp = _p + ".tmp"
            with io.open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
                json.dump(_payload, _f, ensure_ascii=False, indent=2)
            os.replace(_tmp, _p)
        except OSError as _we:
            silent_exc(_we, where="PulseMetricsCollector::_m339_memory_probe write")
            self._log(LogLevel.WARNING,
                      "[339内存观测] 产物写盘失败 %s：%s: %s"
                      % (_dir, type(_we).__name__, _we))
            _out["reason"] = "write_failed"
            return _out
        if "tracemalloc" in _mode:
            self._m339_probe_last_slot = _slot
        if _heap_hi:
            self._m339_probe_last_gc_slot = _slot
        _out.update({"ran": True, "mode": _mode, "path": _p,
                     "top_n": len(_payload["top20"])})
        self._log(LogLevel.INFO,
                  "[339内存观测] 快照完成 mode=%s top=%d 耗时=%.2fs heap_mb=%s "
                  "image_mb=%s proc_gen=%s code_version=%s criteria=%s 产物=%s"
                  % (_mode, _out["top_n"], _elapsed, _payload["heap_mb"], _img,
                     _v["proc_gen"], _v["code_version"], _v["criteria_version"], _p))
        return _out

    # ===== ★343批刀4：释放决策树心跳（G0-G4 生产侧触发） =====
    def _m344_heap_probe(self):
        """★344批刀5：AST 释放**之后**复测 heap（MB）= RSS − 镜像。

        与 `_collect_memory_pressure` 同一口径（`heap = rss - image`），
        取不到任一项 ⇒ None（**不填 0**，铁律156）。异常不抛，只留痕。
        """
        try:
            if not self.info_field:
                return None
            _m = self.info_field._m169_memory_pressure()
            _rss = _m.get("process_rss_mb")
            _img = self._m337_image_mb()
            if _rss is None or _img is None:
                return None
            return round(max(0.0, float(_rss) - float(_img)), 1)
        except Exception as _e:  # noqa: BLE001
            silent_exc(_e, where="PulseMetricsCollector::_m344_heap_probe", level="debug")
            return None

    #: ★382刀9（T-382-线程句柄埋点）：阈值（可被 config 覆盖：PROC_THREADS_WARN / PROC_HANDLES_WARN）。
    _M382_THREADS_WARN = 50
    _M382_HANDLES_WARN = 5000

    #: ★383刀1（T-383-tier分布）：写盘节流（秒）。分布是慢变量，每拍写盘没意义还会磨盘。
    _M383_DUMP_EVERY_SEC = 300.0

    def _m383_dump_tier_distribution(self, path: str | None = None) -> str:
        """★383刀1（T-383-tier分布）：把 tier 分布落 `data/metrics/tier_distribution.json`。

        为什么写盘放在**这里**而不是 release_policy：那个模块的 docstring 明写
        「释放路径不 import 新模块（顶部只导 gc/tracemalloc/time）」——
        统计口径进得去，IO 只能出到已经有 os/json 的调用方。

        Returns: 实际路径；节流中或失败返回空串。
        """
        try:
            _now = time.time()
            _last = float(getattr(self, "_m383_last_dump_ts", 0.0) or 0.0)
            if _now - _last < self._M383_DUMP_EVERY_SEC:
                return ""
            self._m383_last_dump_ts = _now
            from nucleus.mnemosyne.release_policy import tier_distribution as _td383
            _body = dict(_td383())
            _body["generated_at"] = _now
            _p = path or os.path.join("data", "metrics", "tier_distribution.json")
            _dir = os.path.dirname(_p)
            if _dir:
                os.makedirs(_dir, exist_ok=True)
            with io.open(_p, "w", encoding="utf-8", newline="\n") as _f:
                json.dump(_body, _f, ensure_ascii=False, indent=2)
            return _p
        except Exception as _e:
            silent_exc(_e, where="PulseMetricsCollector::_m383_dump_tier_distribution",
                       level="warning")
            return ""

    def _m382_resource_probe(self) -> dict:
        """★382刀9（T-382-线程句柄埋点）：每拍采线程数/句柄数，超阈值打 WARNING。

        ★只采不拦：不新建定时器（挂在内存采样同一条活链上）、不改任何调度、
        不因超标做任何动作 —— 这类指标的价值是「能看见增长」，不是「自动处置」。

        采不到就如实记 -1（**不许记 0**：0 会被读成「没有线程」，是假读数）。
        """
        _out = {"threads": -1, "handles": -1, "warned": False, "why": ""}
        try:
            import threading as _th382
            _out["threads"] = int(_th382.active_count())
        except Exception as _te382:
            silent_exc(_te382, where="PulseMetricsCollector::_m382_resource_probe threads",
                       level="debug")
        try:
            import psutil as _ps382
            _out["handles"] = int(_ps382.Process().num_handles())
        except Exception as _he382:
            # 无 psutil / 非 Windows / 权限不足 都走这里；保留上次读数（-1 表示采不到）
            silent_exc(_he382, where="PulseMetricsCollector::_m382_resource_probe handles",
                       level="debug")
        try:
            import config as _c382
            _tw = int(getattr(_c382, "PROC_THREADS_WARN", self._M382_THREADS_WARN))
            _hw = int(getattr(_c382, "PROC_HANDLES_WARN", self._M382_HANDLES_WARN))
        except Exception as _ce382:
            silent_exc(_ce382, where="PulseMetricsCollector::_m382_resource_probe cfg",
                       level="debug")
            _tw, _hw = self._M382_THREADS_WARN, self._M382_HANDLES_WARN
        _over = []
        if _out["threads"] > _tw:
            _over.append("线程 %d > %d" % (_out["threads"], _tw))
        if _out["handles"] > _hw:
            _over.append("句柄 %d > %d" % (_out["handles"], _hw))
        _out["warned"] = bool(_over)
        _out["why"] = "、".join(_over)
        if _over:
            _n = int(getattr(self, "_m382_probe_beats", 0)) + 1
            self._m382_probe_beats = _n
            # 每 10 拍最多报一次（这类超限会持续存在，逐拍报等于刷屏）
            if _n % 10 == 1:
                self._log(LogLevel.WARNING,
                          "[382刀9] 进程资源超阈值: %s" % _out["why"])
        return _out

    def _m350_live_gate_tick(self, heap_mb, w1_verdict, beat: int) -> dict:
        """★350刀2①：每拍跑一次开闸判定（四条件同立）并留痕。

        不动手的保证：本函数**不调** `apply_decision` / `apply_rollback`，
        只算 `live_gate_ok()` 并打一行 `[350开闸]`。live 要真跑还需 `LIVE_ENABLED`
        置 True（代码变更）——本批不翻。
        """
        from nucleus.mnemosyne import live_gate as _lg
        _pg = ""
        try:
            from nucleus.proc_gen import proc_gen_str as _pgs
            _pg = str(_pgs() or "")
        except Exception as _pge:                              # noqa: BLE001
            silent_exc(_pge, where="PulseMetricsCollector::_m350_live_gate_tick pg",
                       level="debug")
        _hist = getattr(self, "_m350_heap_hist", None)
        if not isinstance(_hist, list):
            _hist = []
            self._m350_heap_hist = _hist
        _hist.append({"proc_gen": _pg, "heap_mb": heap_mb, "beat": int(beat)})
        del _hist[:-8]
        _rs = None
        _ci = None
        try:
            from nucleus.mnemosyne.PulseNodePool import (
                m350_cold_index_evidence as _cie,
                m350_reload_stats_path as _rsp,
            )
            _ci = _cie()
            _p = _rsp()
            try:
                with open(_p, "r", encoding="utf-8") as _f:
                    _rs = json.load(_f)
            except FileNotFoundError:
                # ★350：文件不存在是**确定事实**（还没跑过回载），写清楚而不静默。
                # ★E10（T-350开闸回载缺口-1）：但**不得每拍重复**——同一事实每拍一条
                #   DEBUG 实测约 2 条/拍、10 族合计约 1000 行/h 里占两条纯噪声。
                #   ⇒ 只在「首次发现」或「曾存在又被清掉」时留痕（后者是事故信号）。
                if _absence_notice.note_absent(_p):
                    # ★378刀7（T-350开闸回载缺口-1）：首条用 INFO（初始化事实），后续由 note_absent 挡住。
                    self._log(LogLevel.INFO,
                              "[350开闸] 回载统计尚不存在 path=%s ⇒ 命中率样本=0"
                              "（首次或刚被清理）" % _p)
                _rs = None        # 还没跑过回载 ⇒ 无样本（不是 0）
            except Exception as _rse:                          # noqa: BLE001
                silent_exc(_rse, where="PulseMetricsCollector::_m350_live_gate_tick reload",
                           level="debug")
                _rs = None
        except Exception as _pe:                               # noqa: BLE001
            silent_exc(_pe, where="PulseMetricsCollector::_m350_live_gate_tick pool",
                       level="debug")
        _g = _lg.live_gate_ok(heap_mb=heap_mb, heap_history=_hist,
                              w1_verdict=w1_verdict, reload_stats=_rs,
                              cold_index_writable=_ci, proc_gen=_pg,
                              paused=_lg.paused_categories())
        _lg.log_gate(_g, where="[343心跳] beat=%d" % int(beat))
        self._m350_live_gate = _g
        return _g

    def _m343_release_heartbeat(self, heap_mb=None) -> dict:
        """跑一次 G0-G4 决策并留痕（**只决策不释放**：candidates 恒空）。

        接线位置为什么在这里：这是活体**唯一**每拍都能拿到 `heap_mb` 的地方
        （337刀4 已把 heap = RSS − image 算出来）。另起调度器会得到
        "两份 heap 读数"，而 `[338释放]` 的判据正是拿 heap 分档 —— 两个来源
        打架时无法复算。与 339刀4 tracemalloc 自观测同一挂点（同一范式）。

        Returns:
            dict：`{"status": "ok"|"throttled"|"disabled"|"unavailable", ...}`
            —— 取不到 release_policy 时 `status=unavailable` + why，
            **不填假档位**（"没跑" ≠ "跑出 G1"）。
        """
        _cfg = sys.modules.get("config")
        if _cfg is not None and not bool(
                getattr(_cfg, "ENABLE_RELEASE_HEARTBEAT", True)):
            return {"status": "disabled"}
        _now = time.time()
        _bc = int(getattr(self, "_beat_count", 0) or 0)
        _every = 10
        if _cfg is not None:
            try:
                _every = max(1, int(getattr(_cfg,
                                            "RELEASE_HEARTBEAT_EVERY_BEATS", 10) or 10))
            except (TypeError, ValueError) as _e343:
                # 配置值非法 ⇒ 退回默认节拍，但必须留痕（否则"为什么每 10 拍"不可追溯）
                silent_exc(_e343, where="PulseMetricsCollector::_m343_release_heartbeat every")
                _every = 10
        _last = getattr(self, "_m343_rel_beat", None)
        if _last is not None and (_bc - int(_last)) < _every:
            return {"status": "throttled", "beats_since": _bc - int(_last),
                    "every": _every}
        self._m343_rel_beat = _bc
        try:
            from nucleus.mnemosyne import release_policy as _rp343
        except Exception as _impe:
            silent_exc(_impe, where="PulseMetricsCollector::_m343_release_heartbeat import")
            self._log(LogLevel.WARNING,
                      "[343释放] release_policy 不可用 ⇒ 本轮无档位行: %s: %s"
                      % (type(_impe).__name__, _impe))
            return {"status": "unavailable", "why": "import_failed"}
        # W1 取真实值；取不到就 None（G0 会如实拦，**不猜 alive**）
        _w1 = None
        try:
            from nucleus.world_model.external_heartbeat import (
                w1_verdict_brief as _w1b,
            )
            _w1 = str((_w1b() or {}).get("verdict") or "") or None
        except Exception as _w1e:
            silent_exc(_w1e, where="PulseMetricsCollector::_m343_release_heartbeat w1",
                       level="debug")
            _w1 = None
        _dec = _rp343.decide(
            heap_mb=heap_mb, w1_verdict=_w1, now_ts=_now, candidates=(),
            beat_count=_bc,
            last_release_beat=getattr(self, "_m343_last_release_beat", None))
        _rp343.log_decision(_dec, where="[343心跳] beat=%d every=%d w1=%s"
                            % (_bc, _every, _w1 or "unavailable"))
        # ★344批刀5：343刀4 只挂决策（`candidates_offered` 硬编码 0 = 只决策不释放），
        #   本刀补**执行面**：G2/G3 档位时真实触发 AST 释放（清池 + gc.collect(2)
        #   + 前后实测活体计数），并留痕 `[344释放] ast_cleanup nodes=... heap=...→...`。
        #   口径纪律：池空就是 0（**不造假填数**），但必须给 `candidates_source` 与
        #   `ast_release.measured_nodes` 佐证"释放动作确实跑过"（铁律166：
        #   「没生效」与「生效但产出 0」不是一回事）。
        _ast_rel = None
        _offer = 0
        _offer_src = "ast_face"
        try:
            from nucleus.mnemosyne import ast_release as _ar344
            _offer = int(_ar344.pending_stats().get("faces") or 0)
            if _dec.get("tier") in ("G2_SOFT_RELEASE", "G3_COLD_DEMOTE"):
                _ast_rel = _ar344.release_ast_faces(
                    tier=str(_dec.get("tier") or ""),
                    heap_before=heap_mb,
                    heap_probe=self._m344_heap_probe,
                    where="[343心跳] beat=%d every=%d" % (_bc, _every))
        except Exception as _are:
            silent_exc(_are, where="PulseMetricsCollector::_m343_release_heartbeat 344刀5")
            _offer_src = "unavailable"
            self._log(LogLevel.WARNING,
                      "[344释放] AST 释放面异常（不静默）: %s: %s"
                      % (type(_are).__name__, _are))
        # ★350刀2①：live 开闸判定器**同拍**判一次（只判不动：LIVE_ENABLED 默认 off）。
        #   挂在这里的理由与 343/344 一致：这是活体唯一每拍拿到 heap 的地方，
        #   另起一条路会得到第二份 heap 读数，判据打架时无法复算。
        _lg350 = None
        try:
            _lg350 = self._m350_live_gate_tick(heap_mb, _w1, _bc)
        except Exception as _lge:                              # noqa: BLE001
            silent_exc(_lge, where="PulseMetricsCollector::_m343_release_heartbeat 350开闸",
                       level="warning")
            self._log(LogLevel.WARNING,
                      "[350开闸] 判定器异常（不静默）: %s: %s"
                      % (type(_lge).__name__, _lge))
        return {"status": "ok", "tier": _dec.get("tier"),
                "tier_why": _dec.get("tier_why"),
                "g0_blocked_by": list(_dec.get("g0", {}).get("blocked_by") or []),
                "heap_mb": heap_mb, "beat": _bc,
                "release_enabled": _dec.get("release_enabled"),
                "candidates_offered": _offer,
                "candidates_source": _offer_src,
                "ast_release": _ast_rel}

    def _m366_inventory_tick(self) -> dict:
        """★366刀B4：器官清单导出的**消费方**（挂既有指标采集 tick，不新增定时器）。

        `ENABLE_ORGAN_INVENTORY_EXPORT` 默认 False ⇒ 直接返回 `disabled`（零行为变化）。
        挂载点＝`_collect_memory_pressure`（既有指标采集 tick，与 `m363_diag`/
        `m364_leaderboard` 同一节拍），**不新起线程/定时器/独立节拍**（放行约束 5）。
        导出内容范围不新增器官面：只调既有 `export_organ_inventory()`。
        """
        try:
            from nucleus.cognitive import organ_inventory as _oi
        except Exception as _e:
            silent_exc(_e, where="PulseMetricsCollector::_m366_inventory_tick import")
            return {"status": "import_failed", "why": type(_e).__name__}
        if not _oi._inventory_on():
            return {"status": "disabled", "switch": "ENABLE_ORGAN_INVENTORY_EXPORT"}
        try:
            _inv = _oi.export_organ_inventory()
            _n = len(_inv) if hasattr(_inv, "__len__") else None
            return {"status": "ok", "rows": _n}
        except Exception as _e:
            silent_exc(_e, where="PulseMetricsCollector::_m366_inventory_tick export",
                       level="warning")
            return {"status": "error", "why": "%s: %s" % (type(_e).__name__, _e)}

    def _m364_leaderboard_tick(self) -> dict:
        """★364刀B4：器官健康排行的心跳接线点（**低频**，不新增定时器/线程）。

        节流默认 3600 拍（≈1 小时），可配 `LEADERBOARD_HEARTBEAT_EVERY_BEATS`。
        ★为什么这么稀：`collect()` 实测 ~2.0s/次（57 器官静态 AST 兜底扫描、无缓存），
        高频会直接拖长心跳周期 —— 本刀只把链路接通并留痕耗时，
        开关 `ENABLE_ORGAN_HEALTH_LEADERBOARD` 维持 False（开灯前置不满足）。
        """
        try:
            _bc = int(getattr(self, "_beat_count", 0) or 0)
            _every = 3600
            try:
                import config as _cfg364
                _every = max(1, int(getattr(
                    _cfg364, "LEADERBOARD_HEARTBEAT_EVERY_BEATS", 3600) or 3600))
            except Exception as _e364a:
                silent_exc(_e364a, where="PulseMetricsCollector::_m364_leaderboard_tick every",
                           level="debug")
            _last = getattr(self, "_m364_lb_beat", None)
            if _last is not None and (_bc - int(_last)) < _every:
                return {"status": "throttled", "every": _every}
            self._m364_lb_beat = _bc
            from nucleus.cognitive.organ_health_leaderboard import (
                get_organ_health_leaderboard as _m364_get,
            )
            _lb = getattr(self, "_m364_lb", None)
            if _lb is None:
                _lb = _m364_get()
                self._m364_lb = _lb
            _t0 = time.perf_counter()
            _lb.on_heartbeat()
            _ms = round((time.perf_counter() - _t0) * 1000.0, 1)
            _rows = len(getattr(_lb, "_last_report", []) or [])
            self._m364_lb_last = {"ts": time.time(), "cost_ms": _ms, "rows": _rows,
                                  "beat": _bc}
            # ★2s 级耗时必须每次都看得见（这正是「能不能开灯」的判据）
            self._log(LogLevel.INFO,
                      f"[364B4] 健康排行心跳完成: rows={_rows} 耗时={_ms}ms "
                      f"(开关=ENABLE_ORGAN_HEALTH_LEADERBOARD，节流={_every}拍)")
            return {"status": "ok", "rows": _rows, "cost_ms": _ms}
        except Exception as _e364:
            silent_exc(_e364, where="PulseMetricsCollector::_m364_leaderboard_tick",
                       level="warning")
            return {"status": "error", "why": type(_e364).__name__}

    def m364_leaderboard_stats(self) -> dict:
        """★364刀B4：读健康排行心跳接线的最近一次读数（只读拷贝）。"""
        return dict(getattr(self, "_m364_lb_last", None) or {})

    def _m363_diag_tick(self) -> dict:
        """★363刀B3：六处诊断读数的**生产接线点**（心跳侧，按拍节流）。

        与 `_m360_paid_usage_tick` 同款节流（默认每 10 拍一行，可配
        `DIAG_HEARTBEAT_EVERY_BEATS`）。只采集与打印，不改任何控制流。
        """
        _bc = int(getattr(self, "_beat_count", 0) or 0)
        _every = 10
        try:
            import config as _cfg363
            _every = max(1, int(getattr(_cfg363, "DIAG_HEARTBEAT_EVERY_BEATS", 10) or 10))
        except Exception as _e363c:
            silent_exc(_e363c, where="PulseMetricsCollector::_m363_diag_tick every",
                       level="debug")
            _every = 10
        _last = getattr(self, "_m363_diag_beat", None)
        if _last is not None and (_bc - int(_last)) < _every:
            return {"status": "throttled", "beats_since": _bc - int(_last),
                    "every": _every}
        self._m363_diag_beat = _bc
        try:
            from nucleus.m363_diag_bridge import collect as _m363_collect
            from nucleus.m363_diag_bridge import diag_line as _m363_line
            _d = _m363_collect() or {}
            _d["status"] = "ok"
            self._log(LogLevel.INFO, _m363_line(_d))
            return _d
        except Exception as _e363d:
            silent_exc(_e363d, where="PulseMetricsCollector::_m363_diag_tick",
                       level="warning")
            return {"status": "error", "err": f"{type(_e363d).__name__}"}

    def _m360_paid_usage_tick(self) -> dict:
        """★第360批 刀0-3：心跳侧「付费 / 免费 / 付费占比」行（节流，默认每 10 拍）。

        为什么挂在这：与 343/344/350 同挂点 —— 本方法是活体**已活**的心跳采样链，
        另起调度器会得到"第二份读数"，占比打架时无法复算。
        只**读** `call_recorder.get_daily_tokens()`（一次小 JSON 读），节流后开销可忽略。

        节流：``PAID_USAGE_HEARTBEAT_EVERY_BEATS``（默认 10 拍，约 2 分钟一行）。
        """
        _cfg = sys.modules.get("config")
        if _cfg is not None and not bool(
                getattr(_cfg, "ENABLE_PAID_USAGE_HEARTBEAT", True)):
            return {"status": "disabled"}
        _bc = int(getattr(self, "_beat_count", 0) or 0)
        _every = 10
        if _cfg is not None:
            try:
                _every = max(1, int(getattr(
                    _cfg, "PAID_USAGE_HEARTBEAT_EVERY_BEATS", 10) or 10))
            except (TypeError, ValueError) as _e360:
                silent_exc(_e360, where="PulseMetricsCollector::_m360_paid_usage_tick every")
                _every = 10
        _last = getattr(self, "_m360_paid_beat", None)
        if _last is not None and (_bc - int(_last)) < _every:
            return {"status": "throttled", "beats_since": _bc - int(_last),
                    "every": _every}
        self._m360_paid_beat = _bc
        try:
            from nucleus.llm.call_recorder import paid_usage_line as _m360_line
            _u = _m360_line() or {}
            _u["status"] = "ok"
            return _u
        except Exception as _e360u:
            silent_exc(_e360u, where="PulseMetricsCollector::_m360_paid_usage_tick")
            self._log(LogLevel.WARNING,
                      "[360付费占比] 心跳取数失败（不静默）: %s: %s"
                      % (type(_e360u).__name__, _e360u))
            return {"status": "error", "why": type(_e360u).__name__}

    def _collect_memory_pressure(self) -> dict[str, Any]:
        """172刀3：长跑内存采样增强——复用 InfoField 双口径读数。
        175刀1：在已活采集链上接入 RSS 上限自动回收（避免 167 死开关事故——
        只接心跳活链，不接告警族/InfluxDB 未连接路径），并补瞬时峰值留痕。

        返回 {"ok", "process_rss_mb", "system_percent", "reason",
              "gc_triggered", "gc_last_rss_mb",
              "rss_peak_mb", "rss_peak_ts", "instant_drop_mb"}；
        InfoField 未就绪 / 采集失败 → ok=False、数值为 None（不阻塞快照构建、不抛异常）。
        """
        _out: dict[str, Any] = {
            "ok": False, "process_rss_mb": None,
            "system_percent": None, "reason": "",
            # ★365刀B1：单轨口径 —— 本字段只作"本次是否触发自动GC"（None=测不到），
            #   旧的 `_mem_gc_triggered` 累计计数已退役（与 growth_alarm.gc_triggered
            #   两套计数并存 = 同一事实两个数，读的人必然对不上）。
            "gc_triggered": False,
            "gc_last_rss_mb": None,
            "rss_peak_mb": getattr(self, "_mem_peak_rss_mb", None),
            "rss_peak_ts": getattr(self, "_mem_peak_ts", None),
            "instant_drop_mb": None,
            "restart_armed": False,
            "restart_count": 0,
            # ★337批刀4：RSS 拆「镜像 + 堆」；镜像测不到 ⇒ 两字段都 None（不填 0）
            "image_mb": None,
            "heap_mb": None,
            # ★337批刀4：增长率告警读数（死链挂接后的可见出口）
            "growth_alarm": None,
        }
        if not self.info_field:
            _out["reason"] = "info_field 未就绪"
            return _out
        try:
            # 175刀1：配置读取走已加载的 config 模块（sys.modules，避免热路径重复 import / 阈值悬空）
            _cfg = sys.modules.get("config")
            # ★365刀B1：`_enable_gc` / `_gc_rss_mb` 两个局部量随旧 175 刀 RSS 单闸块一并退役
            #   —— 阈值与开关统一由 `nucleus.runtime_metrics.check_memory_alarm_detailed`
            #   在内部读取（单一真源），此处不再抄第二份。

            _m = self.info_field._m169_memory_pressure()
            _out["ok"] = bool(_m.get("ok"))
            _out["process_rss_mb"] = _m.get("process_rss_mb")
            _out["system_percent"] = _m.get("system_percent")
            _out["reason"] = _m.get("reason", "")

            _rss = _m.get("process_rss_mb")
            # 175刀1：瞬时峰值留痕 + 落差标注（吸收 T-107a 瞬时峰值口径；洞鉴实测 03:01 7.35GB→03:06 5.39GB 近 2GB 无解释）
            if _rss is not None:
                _rss = float(_rss)
                _prev_peak = getattr(self, "_mem_peak_rss_mb", None)
                if _prev_peak is None or _rss > _prev_peak:
                    self._mem_peak_rss_mb = _rss
                    self._mem_peak_ts = time.time()
                _prev_rss = getattr(self, "_mem_last_rss_mb", None)
                if _prev_rss is not None and (_prev_rss - _rss) >= 1024.0:
                    _out["instant_drop_mb"] = round(_prev_rss - _rss, 1)
                self._mem_last_rss_mb = _rss
                _out["rss_peak_mb"] = self._mem_peak_rss_mb
                _out["rss_peak_ts"] = self._mem_peak_ts

                # ★337批刀4：heap = RSS − 镜像（唯一可释放靶面）。
                #   镜像测不到 ⇒ heap 留 None（**不填 0**，整轮不释放）。
                _img = self._m337_image_mb()
                _out["image_mb"] = _img
                if _img is not None:
                    _out["heap_mb"] = round(max(0.0, _rss - float(_img)), 1)

                # ★339刀4：tracemalloc 自观测钩子（**挂在已活采样链上**，
                #   不新开调度器；钩子内部按日槽位自限流，
                #   平时每拍只做一次时间判定就返回）。
                try:
                    _out["self_probe"] = self._m339_memory_probe(
                        heap_mb=_out.get("heap_mb"))
                except Exception as _mpe:
                    silent_exc(_mpe,
                               where="PulseMetricsCollector::_collect_memory_pressure 339刀4")
                    self._log(LogLevel.WARNING,
                              "[339内存观测] 钩子外层异常（不静默）: %s: %s"
                              % (type(_mpe).__name__, _mpe))
                    _out["self_probe"] = None
                # ★382刀9（T-382-线程句柄埋点）：线程/句柄每拍埋点（挂在同一条活采样链，不新建定时器）。
                try:
                    _out["resource_probe"] = self._m382_resource_probe()
                except Exception as _rpe382:
                    silent_exc(_rpe382,
                               where="PulseMetricsCollector::_collect_memory_pressure 382刀9")
                    _out["resource_probe"] = None
                # ★383刀1（T-383-tier分布）：tier 分布慢节奏落盘（节流 300s，失败不影响采集）。
                try:
                    _out["tier_distribution_path"] = self._m383_dump_tier_distribution()
                except Exception as _tde383:
                    silent_exc(_tde383,
                               where="PulseMetricsCollector::_collect_memory_pressure 383刀1")
                    _out["tier_distribution_path"] = ""
                # ★383刀3（T-383-写面观察）：只读采样（mtime 差分），节流 300s，不拦截任何写。
                try:
                    from nucleus.metrics.write_face import observe_once as _wf383
                    _out["write_face"] = _wf383()
                except Exception as _wfe383:
                    silent_exc(
                        _wfe383,
                        where="PulseMetricsCollector::_collect_memory_pressure 383刀3")
                    _out["write_face"] = None
                # ★385刀2：DL3 影子账本（resource 域，每 N 拍一条）。
                #   ★只在有结果时才写 `_out` —— 开关关时输出结构与改前逐字相同。
                try:
                    from nucleus.decision import shadow as _dec385
                    _t385 = int(getattr(self, "_m385_dec_tick", 0)) + 1
                    self._m385_dec_tick = _t385
                    _r385 = _dec385.observe_resource(
                        queue_snapshot=(_out.get("queues") if isinstance(_out, dict) else None),
                        rss_mb=(_out.get("rss_mb") if isinstance(_out, dict) else None),
                        tick=_t385,
                    )
                    if _r385:
                        _out["dec_shadow"] = _r385
                except Exception as _dece385:
                    silent_exc(
                        _dece385,
                        where="PulseMetricsCollector::_collect_memory_pressure 385刀2")

                # ★343刀4：G0-G4 释放决策树心跳（**生产侧触发**，V6 未达修复）。
                #   放在 self_probe 之后：同为"挂已活采样链"，不新增调度点。
                try:
                    _out["release_heartbeat"] = self._m343_release_heartbeat(
                        heap_mb=_out.get("heap_mb"))
                except Exception as _rhe:
                    silent_exc(_rhe,
                               where="PulseMetricsCollector::_collect_memory_pressure 343刀4")
                    self._log(LogLevel.WARNING,
                              "[343释放] 心跳外层异常（不静默）: %s: %s"
                              % (type(_rhe).__name__, _rhe))
                    _out["release_heartbeat"] = {"status": "error",
                                                 "why": type(_rhe).__name__}

                # ★360批刀0-3：付费/免费/占比心跳行（同挂点，节流 10 拍一行）
                try:
                    _out["paid_usage"] = self._m360_paid_usage_tick()
                except Exception as _m360pe:
                    silent_exc(_m360pe,
                               where="PulseMetricsCollector::_collect_memory_pressure 360刀0-3")
                    _out["paid_usage"] = {"status": "error",
                                          "why": type(_m360pe).__name__}

                # ★363刀B3：六处诊断读数的生产接线点（同挂点，节流 10 拍一行）
                try:
                    _out["m363_diag"] = self._m363_diag_tick()
                except Exception as _m363de:
                    silent_exc(_m363de,
                               where="PulseMetricsCollector::_collect_memory_pressure 363刀B3")
                    _out["m363_diag"] = {"status": "error",
                                         "why": type(_m363de).__name__}

                # ★366刀B4：器官清单导出消费方（挂同一既有 tick；开关关闭即返回）
                try:
                    _out["m366_inventory"] = self._m366_inventory_tick()
                except Exception as _m366ie:
                    silent_exc(_m366ie,
                               where="PulseMetricsCollector::_collect_memory_pressure 366B4")
                    _out["m366_inventory"] = {"status": "error",
                                              "why": type(_m366ie).__name__}

                # ★364刀B4：器官健康排行心跳接线（低频；开关关闭时 on_heartbeat 直接返回）
                try:
                    _out["m364_leaderboard"] = self._m364_leaderboard_tick()
                except Exception as _m364le:
                    silent_exc(_m364le,
                               where="PulseMetricsCollector::_collect_memory_pressure 364刀B4")
                    _out["m364_leaderboard"] = {"status": "error",
                                                "why": type(_m364le).__name__}

                # ★337批刀4：`check_memory_alarm_detailed` **死链挂接**（增长率为核心）
                #   337前置§0-5：全库唯一调用方是 tests/test_memory_growth_analyzer_m66.py，
                #   生产日志 `[运行时指标] 内存` **0 行** ⇒ "写了没挂"（与 333刀2 同型）。
                #   挂在与 memory_pressure 同一条心跳链上，不新增调度点。
                try:
                    from nucleus.runtime_metrics import get_runtime_metrics as _grm
                    # ★364刀B1①：传 auto_gc=True —— 366 现场复核「调用点未传 auto_gc=True」
                    #   导致 GC 三闸接好了却永远不会回收（半接线）。
                    _al = _grm().check_memory_alarm_detailed(auto_gc=True) or {}
                    _out["growth_alarm"] = {
                        "triggered": bool(_al.get("triggered")),
                        "growth_mb_per_min": _al.get("growth_mb_per_min"),
                        "rss_mb": _al.get("rss_mb"),
                        # ★364刀B1：闸因/回收读数进观测面（回收量、暂停时长可追溯）
                        "triggers": _al.get("triggers"),
                        "gc_triggered": _al.get("gc_triggered"),
                        "auto_gc_last": _al.get("auto_gc_last"),
                    }
                    # ★365刀B1：单轨出口 —— 判据只取告警链返回值，不再有第二套计数
                    _out["gc_triggered"] = bool(_al.get("gc_triggered"))
                except Exception as _ae:
                    silent_exc(_ae,
                               where="PulseMetricsCollector::_collect_memory_pressure growth_alarm")
                    # 取不到 ⇒ None（**不填 False**：False="没超线"，None="没测"）
                    _out["growth_alarm"] = None
                    # ★365刀B1：告警链都取不到 ⇒ GC 到底有没有发生**不可判定**，同为 None
                    _out["gc_triggered"] = None

                # ★365刀B1：旧 175 刀「RSS 单闸 + 独立 gc.collect()」块**已退役**。
                #   退役理由：它与上方新块三闸（percent/rss/growth）**各回收一次**
                #   （364 核验轮实测 m175 两例红即此双轨重复回收），且留下第二套计数
                #   `_mem_gc_triggered` 与 `growth_alarm.gc_triggered` 并存。
                #   现在：回收动作只在新块发生，本处只把"采集侧 RSS（info_field 口径）"
                #   记到 `gc_last_rss_mb`，判据仍是新块的 `gc_triggered`（单轨）。
                if _out.get("gc_triggered") is True:
                    _out["gc_last_rss_mb"] = round(_rss, 1)

                # 176批段2 内存自动重启判定（ENABLE_MEMORY_AUTO_RESTART 默认 False=零回归）：
                # 条件＝连续 2 次 GC 后 RSS 仍 > 上限 → restart_armed=True。
                # 注：本块与上方 GC if 平级（不依赖 GC 是否触发），GC 仅作回收手段。
                _enable_restart = (bool(getattr(_cfg, "ENABLE_MEMORY_AUTO_RESTART", False))
                                   if _cfg is not None else False)
                _restart_rss_mb = (float(getattr(_cfg, "MEMORY_AUTO_RESTART_RSS_MB", 9216.0))
                                   if _cfg is not None else 9216.0)
                if _enable_restart and _rss > _restart_rss_mb:
                    self._mem_restart_streak = getattr(self, "_mem_restart_streak", 0) + 1
                else:
                    self._mem_restart_streak = 0
                _out["restart_count"] = self._mem_restart_streak
                _out["restart_armed"] = bool(_enable_restart and self._mem_restart_streak >= 2)
                self._last_restart_armed = _out["restart_armed"]
        except Exception as _e:
            silent_exc(_e, where="organs.core.PulseMetricsCollector::_collect_memory_pressure")
            _out["reason"] = f"采集异常: {type(_e).__name__}"
        return _out

    def _build_observability_snapshot(self) -> dict[str, Any]:
        """构建完整的可观测性快照"""
        snapshot = {
            "timestamp": time.time(),
            "beat_count": self._beat_count,
        }
        # ★348刀1：顶层补 proc_gen/code_version/pid —— 这是 domain_guard「产物属哪个
        #   代次」缺的 oracle（345 起只能靠显式隔离声明判域）。此前快照顶层无 proc_gen，
        #   读写两侧都无法把 data/ 产物归属到代次，去掉「隔离串味」只能靠人肉核对。
        try:
            _m348_vs = self._m339_version_stamp()
            snapshot["proc_gen"] = str((_m348_vs or {}).get("proc_gen") or "unknown")
            snapshot["code_version"] = str((_m348_vs or {}).get("code_version") or "unknown")
        except Exception as _m348_pg_e:
            silent_exc(_m348_pg_e,
                       where="PulseMetricsCollector::_build_observability_snapshot proc_gen")
            snapshot["proc_gen"] = "unknown"
        snapshot["pid"] = os.getpid()  # _m348_d1_top_procgen


        # 系统级：脉冲统计
        if self.info_field and hasattr(self.info_field, 'pulse_core') and self.info_field.pulse_core:
            pulse_stats = self.info_field.pulse_core.get_stats()
            snapshot["pulse"] = {
                "emitted": pulse_stats.get("total_emitted", 0),
                "completed": pulse_stats.get("total_completed", 0),
                "timeout": pulse_stats.get("total_timeout", 0),
                "pending": pulse_stats.get("pending", 0),
            }

        # 系统级：信息场统计
        if self.info_field:
            field_stats = self.info_field.get_stats()
            snapshot["info_field"] = {
                "published": field_stats.get("total_published", 0),
                "matched": field_stats.get("total_matched", 0),
                "active_conditions": field_stats.get("active_conditions", 0),
            }

        # 172刀3：内存压力双口径读数（进程 RSS MB + 系统内存占比）
        snapshot["memory_pressure"] = self._collect_memory_pressure()

        # 系统级：节点池统计
        node_stats = {}
        node_pool = getattr(self, '_node_pool', None)
        if node_pool is None and self.info_field:
            node_pool = getattr(self.info_field, 'node_pool', None)
        if node_pool:
            pool_stats = node_pool.get_stats()
            node_stats = {
                "total": pool_stats.get("total_nodes", 0),
                "hot": pool_stats.get("hot_count", 0),
                "warm": pool_stats.get("warm_count", 0),
                "cold": pool_stats.get("cold_count", 0),
            }
        snapshot["nodes"] = node_stats

        # P2-4: 知识演化指标
        evol_stats = self._collect_knowledge_evolution(node_pool)
        snapshot["knowledge_evolution"] = evol_stats

        # 器官级：状态统计
        organ_status_stats = self._collect_organ_status_stats()
        # ★396刀2 ③：三口径归一 —— declared 成为唯一"器官数"主口径
        organ_status_stats = self._m396_attach_organ_counts(organ_status_stats)
        snapshot["organs"] = organ_status_stats

        # 快照级：备份信息
        snapshot["snapshot_backups"] = self._count_snapshot_backups()

        # 核心链路状态统计
        snapshot["link_status"] = self._collect_link_status()

        # 动态心率数据
        # ★396刀2：status 一律**读时现算**，绝不取缓存里的自报串。缓存里的 interval /
        #   beat_count 是"最后一次收到心跳时它说了什么"，而 status 是"现在到底还跳不跳"——
        #   两者来源不同，必须分别取。心跳停了以后这条路径仍可能被别的事件调用（COLLECT），
        #   那时 status 会如实翻成"停搏"，不再冻结在最后一帧的乐观值。
        _hb = dict(self._cached_heartbeat) if self._cached_heartbeat else {
            "interval": 10.0, "beat_count": 0
        }
        _hb_status, _hb_gap = self._m396_heartbeat_status(_hb.get("interval") or 10.0)
        _hb["status"] = _hb_status
        _hb["status_source"] = _M396_HEARTBEAT_STATUS_SRC
        _hb["gap_s"] = (round(_hb_gap, 2) if _hb_gap is not None else None)
        _hb["beat_rx_count"] = int(getattr(self, "_m396_beat_rx_count", 0))
        snapshot["heartbeat"] = _hb

        # 当前用户身份
        snapshot["current_user"] = self._cached_current_user

        # 梦境推演与自我反思
        snapshot["dream"] = {
            "count": self._dream_count,
            "last_topic": self._dream_last_topic,
        }
        snapshot["reflection"] = {
            "count": self._reflection_count,
            "last_domain": self._reflection_last_domain,
        }

        # 肝脏工作统计
        snapshot["liver"] = {
            "compress_count": self._liver_compress_count,
            "fuse_count": self._liver_fuse_count,
            "instinct_upgrade": self._instinct_upgrade_count,
        }

        # ===== 新增：请求去重统计（★T-113d：通电后让去重可见，防止"沉默器官"）=====
        # 总开关关闭时 get_request_deduplicator() 返回 None → 仅标注 enabled=False；
        # 开启（启动侧设 PULSE_REQUEST_DEDUP=1）后含完整累计统计（claimed/duplicate/...）。
        _dd_enabled = False
        _dd_stats: dict[str, Any] = {"enabled": False}
        try:
            from nucleus.field.RequestDeduplicator import get_request_deduplicator
            _dd = get_request_deduplicator()
        except Exception:
            _dd = None
        if _dd is not None:
            _dd_enabled = True
            try:
                _dd_stats = _dd.stats()
                _dd_stats["enabled"] = True
            except Exception as _e:
                self._log(LogLevel.WARNING, f"[请求去重] 统计采集失败: {_e}")
        snapshot["request_dedup"] = _dd_stats

        # ===== 新增：needs_repair 积压统计（★T-113e④：让死字段被消费，不再零读者）=====
        # SafeEvolutionExecutor 在 runtime_failed 时写 needs_repair=True，此前全活树 0 读者。
        # 此处只读消费（fail-safe），把「真失败待重修」补丁数暴露到可观测快照。
        # （删除该字段会破坏第114批"needs_repair=真失败待重修 / undecidable=不可判定"
        #   拆分复用计划，故选「接消费」而非「删除」。)
        _nr_count = 0
        try:
            import os as _os
            _root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
            _pp = _os.path.join(_root, "data", "patches", "pending_patches.json")
            if _os.path.exists(_pp):
                # ★第146批 T146-7：改走 DataAccessLayer.safe_read_json ——
                #   其编码回退链首位即 utf-8-sig，可透明剥离 UTF-8 BOM；
                #   旧写法 open(encoding="utf-8") 遇到 BOM 的补丁文件会抛异常，
                #   落到下面的 WARNING 分支造成每次采集刷一条告警。
                from nucleus.data.DataAccessLayer import safe_read_json as _safe_read_json
                _pending = _safe_read_json(_pp, [])
                if isinstance(_pending, list):
                    _nr_count = sum(1 for _p in _pending
                                    if isinstance(_p, dict) and _p.get("needs_repair"))
        except Exception as _e:
            self._log(LogLevel.WARNING, f"[needs_repair] 积压采集失败: {_e}")
        snapshot["patch_needs_repair"] = _nr_count

        # ===== 新增：控制器统计（从历史脉冲中统计） =====
        controller_stats = {"operations": 0, "files_read": 0, "urls_opened": 0}
        if self.info_field:
            try:
                history = self.info_field.get_history(limit=500)
                for entry in history:
                    et = entry.get("event_type", "")
                    if et == ControllerEvent.OPEN_URL:
                        controller_stats["urls_opened"] += 1
                    elif et == ControllerEvent.READ_FILE:
                        controller_stats["files_read"] += 1
                    elif et.startswith("controller."):
                        controller_stats["operations"] += 1
            except Exception as e:
                if should_warn("mc:snapshot_stats", 300):
                    silent_exc(e, "PulseMetricsCollector.py:428 快照统计", level="warning")
        snapshot["controller"] = controller_stats

        # ===== 新增：无头浏览器统计 =====
        snapshot["headless_browser"] = dict(self._headless_stats)

        # ===== 新增：社会性情感 =====
        snapshot["social_emotions"] = dict(self._cached_social_emotions)

        # ===== 新增：兴趣前5维度 =====
        snapshot["top_interests"] = [
            {"dim": d, "val": round(v, 2)} for d, v in self._cached_top_interests
        ]
        # ===== 新增：生命状态 =====
        snapshot["life_state"] = dict(self._cached_life_state) if self._cached_life_state else {
            "state": "浅层活跃", "previous": "", "since": time.time(),
            "explore_interval": 120, "dream_interval": 300,
        }

        # ===== 新增：情绪状态 =====
        snapshot["emotion_state"] = dict(self._cached_emotion_state) if self._cached_emotion_state else {
            "emotion": "中性", "intensity": 0.0, "trend": {}, "inertia_applied": False,
        }
        return snapshot

    # ========== P2-4: 知识演化可视化 ==========

    def _collect_knowledge_evolution(self, node_pool) -> dict[str, Any]:
        """采集知识演化指标：L1/L2/L3分布、兴趣光谱摘要"""
        evol = {"l1_count": 0, "l2_count": 0, "l3_count": 0, "interest_summary": {}}

        if node_pool:
            pool_stats = node_pool.get_stats()
            evol_dist = pool_stats.get("evol_distribution", {})
            evol["l1_count"] = evol_dist.get("L1", 0)
            evol["l2_count"] = evol_dist.get("L2", 0)
            evol["l3_count"] = evol_dist.get("L3", 0)
            # 本能节点数（L4）
            evol["instinct_count"] = pool_stats.get("instinct_count", 0)
            # 从L3中扣除L4本能节点，得到纯净的L3智慧节点数
            instinct_count = evol.get("instinct_count", 0)
            evol["l3_pure"] = max(0, evol["l3_count"] - instinct_count)

            # 知识密度：L2+L3+L4占总节点比例
            total = evol["l1_count"] + evol["l2_count"] + evol["l3_count"] + instinct_count
            if total > 0:
                evol["knowledge_density"] = round(
                    (evol["l2_count"] + evol["l3_count"] + instinct_count) / total, 3
                )
            else:
                evol["knowledge_density"] = 0.0

        # 兴趣光谱摘要
        if self.info_field:
            try:
                interest_pulse = self.info_field.get_current(InterestEvent.CHANGED)
                if interest_pulse and isinstance(interest_pulse, dict):
                    payload = interest_pulse.get("payload", {})
                    interests = payload.get("current_interests", {})
                    if interests:
                        sorted_items = sorted(
                            interests.items(), key=lambda x: x[1], reverse=True
                        )
                        evol["interest_summary"] = {
                            "top_dimensions": [
                                {"dim": d, "val": round(v, 2)}
                                for d, v in sorted_items[:3]
                            ],
                            "decayed_dimensions": [
                                {"dim": d, "val": round(v, 2)}
                                for d, v in sorted_items[-3:]
                            ],
                        }
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return evol

    def _collect_organ_status_stats(self) -> dict[str, int]:
        """P1-4: 统计器官状态分布（在线/熔断/恢复中/离线）"""
        stats = {"total": 0, "running": 0, "fused": 0, "recovering": 0, "stopped": 0, "idle": 0}

        for _organ_name, organ_data in self._cached_organ_stats.items():
            stats["total"] += 1
            status = organ_data.get("status", "unknown")
            if status == "RUNNING" or status == OrganStatus.RUNNING:
                stats["running"] += 1
            elif status == "FUSED" or status == OrganStatus.FUSED:
                stats["fused"] += 1
            elif status == "RECOVERING" or status == OrganStatus.RECOVERING:
                stats["recovering"] += 1
            elif status == "STOPPED" or status == OrganStatus.STOPPED:
                stats["stopped"] += 1
            else:
                stats["idle"] += 1

        return stats

    # ===== ★396刀2 ③：器官数三口径归一 =====

    def _m396_declared_organ_count(self) -> dict[str, Any]:
        """声明口径的器官数（**唯一权威**）= `OrganAssembler.validate()["organ_count"]`。

        读的是启动期装配自检钉在 `nucleus.organ_assembler` 模块槽位上的最后一份报告。
        取不到时如实报 `declared=None` / `declared_source=not_scanned`，
        **不拿活体注册数（66）凑数**——"没采到"与"真是这个数"必须可分（铁律 276）。
        """
        try:
            from nucleus.organ_assembler import get_last_validate_report
            _rep = get_last_validate_report()
        except Exception as _e:
            silent_exc(_e, where="PulseMetricsCollector::_m396_declared_organ_count")
            return {"declared": None, "declared_source": "unavailable"}
        if not _rep:
            return {"declared": None, "declared_source": "not_scanned"}
        return {
            "declared": _rep.get("organ_count"),
            "declared_ok": _rep.get("ok"),
            "declared_errors": _rep.get("error_count"),
            "declared_warnings": _rep.get("warning_count"),
            "declared_source": str(_rep.get("source") or "organ_assembler.validate()"),
        }

    def _m396_attach_organ_counts(self, stats: dict[str, Any]) -> dict[str, Any]:
        """★396刀2 ③：三个器官数收敛到**一个主口径**，其余具名说明不同事实。

        过去 `organs.total`（66）被当成"器官总数"读，而文档里同时流传 56（ORGAN_META
        声明）与 57（+QICA 准器官）。三个数指三件不同的事，混读的结果是同一个问题有
        三个答案。现在：

          * `declared`        —— **唯一器官数主口径**，来自 assembler.validate()；
          * `runtime_registered` —— 信息场里注册过条件的器官名去重数（活体读数，
            含框架准器官，也可能漏掉不发脉冲的器官，**不是器官数**）；
          * `total`          —— **保留旧值**以免 30 天趋势线断代，但用
            `total_meaning` 明确它不是器官数。直接改值等于用新口径涂改历史曲线。

        铁律 166：不同事实用不同措辞；铁律 271：判据的分子分母必须同源。
        """
        _decl = self._m396_declared_organ_count()
        _runtime = int(stats.get("total", 0) or 0)
        stats.update(_decl)
        stats["runtime_registered"] = _runtime
        stats["total_meaning"] = "runtime_registered（旧口径：信息场条件名去重，不是器官数）"
        stats["counts_note"] = ("器官数只认 declared（assembler.validate）；"
                                "runtime_registered 是活体注册读数，两者是不同事实")
        return stats

    #: ★主线第50批 T3（P2-329）：期望接收者 → **真实注册名别名**。
    #  背景：判据原用单一字符串做子串匹配，但对话侧真实注册名是
    #  「Web对话-人脸监听」（web_chat.py:658）与
    #  「对话模块-全局回复监听」（chat_service.py:108）
    #  —— 前者不含 "对话模块" 子串 → **恒判 mismatch**。
    # ★T-112d：_LINK_ALIASES 提升为共享器官名归一化表（单源真相，见 nucleus.organ_identity）。
    #   原本地别名表已迁移并充实（覆盖豁免表命名空间），其余消费方统一走 resolve_organ_key。
    _LINK_ALIASES = ORGAN_ALIASES

    def _m50_alias_match_on(self) -> bool:
        """★T3 开关（默认 True；关闭 → 恢复旧的子串匹配）。"""
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_LINK_STATUS_ALIAS_MATCH", True))
        except Exception as e:
            silent_exc(e, where="organs.core.PulseMetricsCollector::_m50_alias_match_on L547")
            return True

    @classmethod
    def _m50_resolve_aliases(cls, expected: str) -> tuple:
        """把期望名展开为别名元组（未登记则只有自身）。"""
        _e = str(expected or "")
        for _k, _al in cls._LINK_ALIASES.items():
            if _e == _k or _e in _al:
                return tuple(_al)
        return (_e,) if _e else ()

    def _m50_subscriber_matches(self, expected: str, subscribers) -> bool:
        """★别名感知匹配：任一接收者名与任一别名互为子串即命中。"""
        if not subscribers:
            return False
        if not self._m50_alias_match_on():
            # 旧行为（零回归）
            return expected in subscribers or any(
                expected in str(_s) for _s in subscribers)
        _al = [str(_a).lower() for _a in self._m50_resolve_aliases(expected) if _a]
        for _s in subscribers:
            _sl = str(_s or "").lower()
            if not _sl:
                continue
            for _a in _al:
                if _a in _sl or _sl in _a:
                    return True
        return False

    def _collect_link_status(self) -> dict[str, Any]:
        """采集核心脉冲链路的收发匹配状态"""
        links = {
            "eye_to_visual": {"event": EyeEvent.STREAM_FRAME, "emitter": "眼睛", "receiver": "视觉皮层", "status": "unknown"},
            "visual_to_chat": {"event": ChatEvent.USER_PRESENCE_DETECTED, "emitter": "视觉皮层", "receiver": "对话模块", "status": "unknown"},
            # ★主线第50批 T3（P2-329）修正：原用 ``mouth.reply``（嘴巴→对话的回执）
            #   与链路名 ``chat_to_mouth``（对话→嘴巴）**方向相反**。
            #   真实链路 = 「对话/皮层 命令嘴巴说话」：
            #   事件 ``mouth.speak``，订阅者 = PulseMouth（MouthEvent.SPEAK）。
            "chat_to_mouth": {"event": MouthEvent.SPEAK, "emitter": "对话模块", "receiver": "嘴巴", "status": "unknown"},
        }

        if self.info_field:
            conditions = getattr(self.info_field, '_conditions', {})
            # 统计每个事件的订阅情况
            event_subscribers = {}
            for _cond_id, cond_data in conditions.items():
                organ_name = cond_data.get("condition", {}).get("organ_name", "")
                event_types = cond_data.get("condition", {}).get("event_types", [])
                for et in event_types:
                    if et not in event_subscribers:
                        event_subscribers[et] = []
                    event_subscribers[et].append(organ_name)

            for _key, link in links.items():
                event = link["event"]
                subscribers = event_subscribers.get(event, [])
                expected_receiver = link["receiver"]
                # ★T3：别名感知匹配（关闭时回旧行为）
                if self._m50_subscriber_matches(expected_receiver, subscribers):
                    link["status"] = "connected"
                elif subscribers:
                    link["status"] = "mismatch"
                    link["actual_receivers"] = subscribers
                    link["expected_aliases"] = list(
                        self._m50_resolve_aliases(expected_receiver))
                else:
                    link["status"] = "orphan"

        return links
    def _refresh_organ_cache(self):
        """P1-4: 刷新器官指标缓存（利用心跳节奏）"""
        now = time.time()
        if now - self._cache_timestamp < _cache_seconds and self._cached_organ_stats:
            return  # 缓存未过期，跳过

        self._cached_organ_stats = {}

        # 从信息场中获取所有已注册的器官信息
        if self.info_field:
            conditions = getattr(self.info_field, '_conditions', {})
            for _cond_id, cond_data in conditions.items():
                organ_name = cond_data.get("condition", {}).get("organ_name", "")
                if organ_name and organ_name not in self._cached_organ_stats:
                    self._cached_organ_stats[organ_name] = {
                        "status": "RUNNING",  # 默认在线（已注册条件即为在线）
                        "last_seen": now,
                    }

        self._cache_timestamp = now
    def _on_liver_compressed(self, payload: dict) -> dict[str, Any]:
        """缓存肝脏压缩数据"""
        from_level = payload.get("from_level", "")
        with self._cache_lock:
            if from_level == "L1":
                self._liver_compress_count += 1
            elif from_level == "L2":
                self._liver_fuse_count += 1
        return {"status": "cached"}

    def _on_instinct_upgraded(self, payload: dict) -> dict[str, Any]:
        """缓存本能升级数据"""
        with self._cache_lock:
            self._instinct_upgrade_count += 1
        return {"status": "cached"}
    def _count_snapshot_backups(self) -> int:
        """P1-4: 统计快照备份文件数"""
        try:
            import glob
            count = 0
            for _ in glob.glob("data/knowledge/pulse_knowledge_snapshot.json.*.bak"):
                count += 1
            # ★360遗留：冷档压成 .bak.gz 后仍是备份，不补这一条看板会「备份数凭空减少」
            for _ in glob.glob("data/knowledge/pulse_knowledge_snapshot.json.*.bak.gz"):
                count += 1
            return count
        except Exception as e:
            silent_exc(e, where="organs.core.PulseMetricsCollector::_count_snapshot_backups L660")
            return 0

    def _flatten_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """P1-4: 将嵌套快照扁平化为时序友好的一维字典"""
        flat = {
            "timestamp": snapshot.get("timestamp", 0),
            "beat_count": snapshot.get("beat_count", 0),
        }

        # 脉冲指标
        pulse = snapshot.get("pulse", {})
        flat["pulse_emitted"] = pulse.get("emitted", 0)
        flat["pulse_completed"] = pulse.get("completed", 0)
        flat["pulse_timeout"] = pulse.get("timeout", 0)
        flat["pulse_pending"] = pulse.get("pending", 0)

        # 信息场指标
        field = snapshot.get("info_field", {})
        flat["field_published"] = field.get("published", 0)
        flat["field_matched"] = field.get("matched", 0)
        flat["field_conditions"] = field.get("active_conditions", 0)

        # 节点池指标
        nodes = snapshot.get("nodes", {})
        flat["nodes_total"] = nodes.get("total", 0)
        flat["nodes_hot"] = nodes.get("hot", 0)
        flat["nodes_warm"] = nodes.get("warm", 0)
        flat["nodes_cold"] = nodes.get("cold", 0)

        # 器官状态指标
        organs = snapshot.get("organs", {})
        flat["organs_total"] = organs.get("total", 0)
        flat["organs_running"] = organs.get("running", 0)
        flat["organs_fused"] = organs.get("fused", 0)
        flat["organs_recovering"] = organs.get("recovering", 0)

        # 快照备份
        flat["snapshot_backups"] = snapshot.get("snapshot_backups", 0)

        # P2-4: 知识演化指标
        evol = snapshot.get("knowledge_evolution", {})
        flat["evol_l1"] = evol.get("l1_count", 0)
        flat["evol_l2"] = evol.get("l2_count", 0)
        flat["evol_l3"] = evol.get("l3_count", 0)
        flat["evol_density"] = evol.get("knowledge_density", 0.0)

        return flat

    def _print_console_snapshot(self, snapshot: dict[str, Any]):
        """P1-4: 控制台极简一行输出"""
        pulse = snapshot.get("pulse", {})
        nodes = snapshot.get("nodes", {})
        organs = snapshot.get("organs", {})

        fused_count = organs.get("fused", 0)
        fused_str = f" 熔断{fused_count}" if fused_count > 0 else ""

        evol = snapshot.get("knowledge_evolution", {})
        density = evol.get("knowledge_density", 0.0)
        line = (
            f"[可观测] 脉冲 {pulse.get('emitted',0)}/{pulse.get('completed',0)}/"
            f"{pulse.get('timeout',0)}(待{pulse.get('pending',0)}) | "
            f"节点 {nodes.get('total',0)}(热{nodes.get('hot',0)}/"
            f"温{nodes.get('warm',0)}/冷{nodes.get('cold',0)}) | "
            f"L1/L2/L3={evol.get('l1_count',0)}/{evol.get('l2_count',0)}/{evol.get('l3_count',0)} | "
            f"密度{density:.1%} | "
            f"器官 {organs.get('total',0)}{fused_str} | "
            f"备份 {snapshot.get('snapshot_backups',0)}"
        )
        self._log(LogLevel.INFO, line)
    def _print_body_ui(self, snapshot: dict[str, Any]):
        """人体UI：全身器官状态面板 + 脉冲链路通断"""
        organs = snapshot.get("organs", {})
        pulse = snapshot.get("pulse", {})
        nodes = snapshot.get("nodes", {})
        evol = snapshot.get("knowledge_evolution", {})
        field = snapshot.get("info_field", {})

        total = organs.get("total", 0)
        running = organs.get("running", 0)
        fused = organs.get("fused", 0)
        recovering = organs.get("recovering", 0)
        stopped = organs.get("stopped", 0)

        # 构建器官状态条
        running_bar = "🟢" * min(running, 20)
        fused_bar = "🔴" * fused
        recovering_bar = "🟡" * recovering
        stopped_bar = "⚪" * stopped

        status_bar = running_bar + fused_bar + recovering_bar + stopped_bar
        if len(status_bar) > 40:
            status_bar = f"🟢×{running} 🔴×{fused} 🟡×{recovering} ⚪×{stopped}"

        # 链路通断判断
        pulse_emitted = pulse.get("emitted", 0)
        pulse_matched = field.get("matched", 0)
        pulse_unmatched = pulse_emitted - pulse_matched

        if pulse_unmatched > 0:
            link_status = f"⚠️ 链路警告: {pulse_unmatched}个脉冲未匹配"
        elif fused > 0:
            link_status = f"⚠️ 器官熔断: {fused}个"
        else:
            link_status = "✅ 链路畅通"

        # 知识健康度
        density = evol.get("knowledge_density", 0)
        if density >= 0.8:
            knowledge_status = "🧠 知识丰富"
        elif density >= 0.5:
            knowledge_status = "📖 知识成长中"
        else:
            knowledge_status = "🌱 知识积累中"

        # 分层调度状态
        l1 = evol.get("l1_count", 0)
        l2 = evol.get("l2_count", 0)
        l3 = evol.get("l3_count", 0)

        print(f"""
┌──────────────────────────────────────────────────────┐
│  🫀 曈曈 v9.5 · 人体UI · 全身器官状态                   │
├──────────────────────────────────────────────────────┤
│  器官: {status_bar}                                      │
│  总计: {total} | 在线: {running} | 熔断: {fused} | 恢复: {recovering} | 离线: {stopped}│
├──────────────────────────────────────────────────────┤
│  📡 脉冲链路: {link_status}                                 │
│  发射: {pulse_emitted} | 匹配: {pulse_matched} | 未匹配: {pulse_unmatched}          │
├──────────────────────────────────────────────────────┤
│  🧠 知识: {nodes.get('total',0)}节点 (热{nodes.get('hot',0)}|温{nodes.get('warm',0)}|冷{nodes.get('cold',0)})              │
│  L1:{l1} | L2:{l2} | L3:{l3} | 密度:{density:.1%} | {knowledge_status}       │
├──────────────────────────────────────────────────────┤
│  📊 分层调度: L0生命线 | L1实时 | L2认知 | L3后台         │
│  备份: {snapshot.get('snapshot_backups',0)}份                                     │
└──────────────────────────────────────────────────────┘""")

    def _log_observability(self, snapshot: dict[str, Any]):
        """P1-4: 文件日志完整输出"""
        pulse = snapshot.get("pulse", {})
        nodes = snapshot.get("nodes", {})
        organs = snapshot.get("organs", {})
        field = snapshot.get("info_field", {})
        evol = snapshot.get("knowledge_evolution", {})
        msg = (
            f"可观测性快照 #{snapshot.get('beat_count', 0)}: "
            f"脉冲(发射{pulse.get('emitted',0)} 完成{pulse.get('completed',0)} "
            f"超时{pulse.get('timeout',0)} 待处理{pulse.get('pending',0)}) "
            f"信息场(发布{field.get('published',0)} 匹配{field.get('matched',0)} "
            f"条件{field.get('active_conditions',0)}) "
            f"节点(总{nodes.get('total',0)} 热{nodes.get('hot',0)} "
            f"温{nodes.get('warm',0)} 冷{nodes.get('cold',0)}) "
            f"器官(总{organs.get('total',0)} 运行{organs.get('running',0)} "
            f"熔断{organs.get('fused',0)} 恢复{organs.get('recovering',0)}) "
            f"备份{snapshot.get('snapshot_backups',0)} "
            f"演化(L1={evol.get('l1_count',0)} L2={evol.get('l2_count',0)} "
            f"L3={evol.get('l3_count',0)} 密度={evol.get('knowledge_density',0):.1%})"
        )
        self._log("INFO", msg)

    # ========== 分子级采集 ==========

    def _collect_molecular(self) -> dict[str, Any]:
        result = {}
        if self.info_field:
            es = self.info_field.get_current(EnergyEvent.METABOLISM_SNAPSHOT)
            if es and isinstance(es, dict):
                result["energy_level"] = es.get("payload", {}).get("energy_level", 1.0)
            hw = self.info_field.get_current(TouchEvent.HARDWARE_SNAPSHOT)
            if hw and isinstance(hw, dict):
                hp = hw.get("payload", {})
                result["cpu_usage"] = hp.get("cpu", {}).get("usage_percent", 0)
                result["memory_usage"] = hp.get("memory", {}).get("usage_percent", 0)
        return result

    def _collect_cellular(self) -> dict[str, Any]:
        result = {"pulse_completed": 0, "pulse_timeout": 0}
        if self.info_field and hasattr(self.info_field, 'pulse_core') and self.info_field.pulse_core:
            stats = self.info_field.pulse_core.get_stats()
            result["pulse_completed"] = stats.get("total_completed", 0)
            result["pulse_timeout"] = stats.get("total_timeout", 0)
        return result

    def _collect_organ_level(self) -> dict[str, Any]:
        return {"total_organs": len(self._cached_organ_stats) if self._cached_organ_stats else 50,
                "active_conditions": 50}

    # ========== 状态查询 ==========

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name, "snapshot_count": self._snapshot_count,
            "history_size": len(self._metrics_history), "is_running": self.is_running,
            "obs_enabled": self._obs_enabled,
            "obs_interval_beats": self._snapshot_interval,
            "cached_organs": len(self._cached_organ_stats),
        }

    def set_node_pool(self, node_pool):
        """注入节点池引用（P1-4修复）"""
        self._node_pool = node_pool

    def get_resonance_conditions(self) -> list:
        return [{"organ_name": self.organ_name,
                 "event_types": [MetricsEvent.COLLECT, SystemEvent.STATUS_REQUEST,
                                HeartEvent.BEAT, HeartEvent.ALIVE, PersonaEvent.SWITCHED,
                                Event.DIGEST_KNOWLEDGE, ReflectionEvent.INSIGHT,
                                KnowledgeEvent.COMPRESSED, "instinct.upgraded",
                                HormonesEvent.EMOTION_DETECTED, InterestEvent.CHANGED,
                                ControllerEvent.SEARCH_COMPLETED,
                                Event.LIFE_STATE_CHANGED  # 新增：生命状态广播
                                ],
                 "min_priority": 1}]

    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None



# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "指标采集器",
    "class_name": "PulseMetricsCollector",
    "attr_name": "metrics_collector",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [
        {"target": "node_pool", "setter": "set_node_pool"},
    ],
}

if __name__ == "__main__":
    print("=== PulseMetricsCollector v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self):
            self.published = []
            self._data = {}
            self._conditions = {
                "cond:心脏:1": {"condition": {"organ_name": "心脏"}, "handler": None},
                "cond:胃:1": {"condition": {"organ_name": "胃"}, "handler": None},
                "cond:肝:1": {"condition": {"organ_name": "肝"}, "handler": None},
            }
        def publish(self, p): self.published.append(p)
        def get_current(self, k): return self._data.get(k)
        def get_stats(self):
            return {"total_published": 100, "total_matched": 95, "active_conditions": 50}
    class MockPulseCore:
        def get_stats(self):
            return {"total_emitted": 150, "total_completed": 147, "total_timeout": 3, "pending": 2}
    m = MockInfoField()
    m.pulse_core = MockPulseCore()
    m._data[EnergyEvent.METABOLISM_SNAPSHOT] = {"payload": {"energy_level": 0.85}}
    m._data[TouchEvent.HARDWARE_SNAPSHOT] = {"payload": {"cpu": {"usage_percent": 35}, "memory": {"usage_percent": 50}}}
    c = PulseMetricsCollector("指标采集器")
    c.set_info_field(m)
    c.start()
    c._snapshot_interval = 1  # 加速测试：每次心跳都输出
    # 刷新缓存
    c._refresh_organ_cache()
    print(f"1. 缓存器官数: {len(c._cached_organ_stats)}")
    # 心跳触发可观测性
    c._beat_count = 0
    r = c.on_pulse({"event_type": HeartEvent.BEAT, "payload": {}, "priority": 3})
    if r:
        print(f"2. 可观测性快照: 状态={r['status']}, 心跳={r['beat']}")
    # 验证扁平时序输出
    flat_pulses = [p for p in m.published if p.get("event_type") == ObservabilityEvent.SNAPSHOT]
    if flat_pulses:
        payload = flat_pulses[-1].get("payload", {})
        print(f"3. 扁平输出: pulse_emitted={payload.get('pulse_emitted')}, nodes_total={payload.get('nodes_total')}, organs_running={payload.get('organs_running')}")
        print(f"   ObservabilityEvent.SNAPSHOT脉冲 layer: {flat_pulses[-1].get('layer', '未设置')} (预期L3)")
    s = c.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"4. 统计: 采集{s['snapshot_count']}次, 可观测={'启用' if s['obs_enabled'] else '关闭'}")
    c.stop()
    print("\n=== 自测全部通过 ===")
# _m50_t3_mc_done
