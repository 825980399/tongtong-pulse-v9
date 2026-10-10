# -*- coding: utf-8 -*-
"""
InfoField.py —— 信息场

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 全局信息场域，脉冲信号传播与共振
机制: 大型模块（2326行），包含2个类、10个核心方法，采用分层架构实现
定位: 核心场域层
"""

import os
import random
import sys
import threading
import time
import traceback
from collections import OrderedDict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import (
    get_module_logger,
)
from nucleus.logger import (
    noise_reduction_enabled as _noise_reduce,
)
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底

try:
    from config import CONCURRENT_COMM, PULSE_LAYER, PULSE_PRIORITY
except ImportError:
    PULSE_PRIORITY = {"LIGHT_SPEED": 10, "CRITICAL": 9, "HIGH": 7, "NORMAL": 5, "LOW": 3, "BACKGROUND": 1}
    PULSE_LAYER = {
        "l0_threads": 1, "l1_threads": 4, "l2_threads": 4, "l3_threads": 2,
        "l0_queue_max": 0, "l1_queue_soft_limit": 500, "l2_queue_limit": 200, "l3_queue_hard_limit": 100,
        "l0_enabled": True, "l1_enabled": True, "l2_enabled": True, "l3_enabled": True,
        "high_load_cpu_threshold": 80.0, "high_load_memory_threshold": 85.0,
        "idle_cooldown_seconds": 60.0, "field_mode": "PULSE",
        "l0_l1_use_lockfree_queue": True, "cpu_affinity_enabled": False,
        "cpu_affinity_cores": [], "disable_ttl_for_l0_l1": True,  # type: ignore[possibly-unbound]
        "organ_max_concurrent_pulses": 0, "pulse_storm_threshold": 500,
        "pulse_storm_action": "aggregate",
    }
    CONCURRENT_COMM = {
        "multi_subscribe_enabled": True, "parallel_dispatch_enabled": True,
        "topology_multi_inbound": True, "dual_route_default": "parallel",
        "n_to_one_max_sources": 0, "n_to_one_queue_mode": "concurrent",
    }

import config as _cfg  # ★162批刀1：config 恒可导入（既有 from config import 已证），无条件避免静默 except

# v9.5: 脉冲层级常量
_PULSE_LAYER_L0 = "L0"
_PULSE_LAYER_L1 = "L1"
_PULSE_LAYER_L2 = "L2"
_PULSE_LAYER_L3 = "L3"

# v9.5: 脉冲风暴处理动作
_STORM_AGGREGATE = "aggregate"
_STORM_THROTTLE = "throttle"
_STORM_REJECT = "reject"

# v9.5: 双路由模式
_ROUTE_PARALLEL = "parallel"
_ROUTE_SERIAL = "serial"

# 模块级统一日志器（★FIX: 落盘 pulse.log，替代散落 print）
_module_logger = get_module_logger("InfoField")

# ===== ★PHASE13（2026-09-07）：按锁名的等待时长累计器 =====
# 背景：runtime_metrics 的「锁等待过高」告警只报一个总均值（实测 63.9~78.8ms，
#   持续 11.5 小时），运维看到数字却不知道是哪把锁 —— 内部协作者据此误判为
#   node_pool 的全局锁，实际是 InfoField._lock 在单条 publish 路径上被抢 4 次。
# 方案：publish 路径上的每处加锁点按名字旁路累计，告警时读取 Top1 附带展示。
# 约束：纯旁路、模块级、独立小锁、失败静默 —— 绝不影响主链路（★零侵入）。
_LOCK_WAIT_BY_NAME: dict[str, float] = {}
_LOCK_WAIT_BY_NAME_LOCK = threading.Lock()


def _accumulate_lock_wait_by_name(detail: dict[str, float]) -> None:
    """把单次 publish 的按锁名等待时长并入模块级累计器。"""
    if not detail:
        return
    try:
        with _LOCK_WAIT_BY_NAME_LOCK:
            for _name, _ms in detail.items():
                if _ms > 0:
                    _LOCK_WAIT_BY_NAME[_name] = _LOCK_WAIT_BY_NAME.get(_name, 0.0) + _ms
    except Exception as _exc:
        _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
def get_lock_wait_by_name(reset: bool = False) -> dict[str, float]:
    """返回各锁名的累计等待时长(ms)；reset=True 时读取后清零（用于周期性采样）。

    runtime_metrics 调用方用 `reset=True` 做「每采样窗口的增量快照」，
    这样告警里显示的是本窗口的热点锁，而不是开机以来的历史总和。
    """
    try:
        with _LOCK_WAIT_BY_NAME_LOCK:
            _snapshot = dict(_LOCK_WAIT_BY_NAME)
            if reset:
                _LOCK_WAIT_BY_NAME.clear()
        return _snapshot
    except Exception:
        return {}


# ============================================================ 主线第57批 T4（P2-394）：L2 队列配置解析（纯函数）
def resolve_l2_config(cpu_count=8, layer_config=None) -> dict:
    """从配置解析 L2 有效队列上限 / worker 数 / 告警比例。

    纯函数，便于单测与热重载复用。
    """
    _cfg = layer_config if layer_config is not None else PULSE_LAYER
    _limit = int(_cfg.get("l2_queue_limit", 200) or 200)
    _cfg_threads = int(_cfg.get("l2_threads", 0) or 0)
    _workers = _cfg_threads if _cfg_threads > 0 else max(4, (cpu_count or 8) // 2)
    _warn_ratio = float(_cfg.get("l2_warn_ratio", 0.8) or 0.8)
    return {"queue_limit": _limit, "workers": _workers, "warn_ratio": _warn_ratio}


class InfoField(SilentLogMixin):
    """
    全局信息场（v9.5 分层异步调度 · 终极完整版）
    
    所有器官共享此实例。
    发布脉冲 → 幂等检查 → TTL校验 → 匹配共振条件 → 风暴检测 → 按 layer 异步分发到独立线程池。
    """
    
    def __init__(self, max_history: int = 1000):
        """
        Args:
            max_history: 脉冲历史最大保留条数
        """
        # 共振条件注册表
        self._conditions: dict[str, dict[str, Any]] = {}
        self._condition_counter = 0

        # ★主线第25批 T1/P2-160：重复事件遥测状态（仅 DEBUG_DUP_EVENT_TRACE=True 时被写入，
        #   关闭时该 dict 恒空、不取锁、不建调用栈 → 零副作用）
        self._dup_trace_seen: dict[str, tuple] = {}
        self._dup_trace_lock = threading.Lock()
        self._dup_trace_count = 0
        
        # 脉冲历史记录（环形缓冲）
        self._history: deque = deque(maxlen=max_history)
        
        # 线程安全锁
        # ★A6【P2】锁序一致性诊断结论（2026-09-06，只诊断不重构）：
        #   经静态扫描全部 with self._lock: 块，未发现「锁内调用另一个也加 self._lock 的方法」的嵌套锁，
        #   故不存在锁序不一致导致的死锁风险。具体证据：
        #   1) _is_duplicate(:703 内部加锁) 仅在 :375 锁外调用 → 无嵌套；
        #   2) _mark_processed(:717 未加锁) 仅在 :424 已持 self._lock 时调用，无锁外调用点 → 安全；
        #   3) _organ_concurrent_lock 相关方法(:1237/:1245) 在 :466/:488 调用时主锁 self._lock 已释放
        #      （:425 退出 with 块后才进入分发循环）→ 无交叉锁。
        #   其余 _hardware_lock/_task_count_lock 均为独立短临界区，锁内不调用其他加锁方法。
        #   若未来新增「锁内调用加锁方法」，需在此重新评估锁序。
        self._lock = threading.Lock()
        # ★PHASE14-锁优化（2026-09-07）：为 _total_handled 单独立一把小锁。
        #   背景：_total_handled 的自增发生在 **每个 handler 执行完毕时**
        #   （_dispatch_handler 的 finally 块）。框架有几十个器官并发收脉冲，
        #   于是这行 `+= 1` 让所有器官在收工瞬间都去抢同一把 self._lock，
        #   而 _lock 同时还被 publish 主流程（幂等/历史/统计）使用——
        #   后台那几十次自增，和前台每一次脉冲发布，挤在同一把锁上。
        #   这与 PHASE13 实测吻合：锁等待 70ms 且随知识节点增长而升高。
        #   （内部协作者此前猜的是 node_pool.query 的全局锁，方向不对。）
        #
        #   解耦后：handler 自增只与「另一个 handler 的自增」竞争，
        #   不再和 publish 主流程互相阻塞。临界区只有一次加法，持有时间极短。
        #
        #   ★关键约束：读写必须用同一把锁。get_total_handled 读端已同步改为
        #   使用本锁（见 :1541），否则读写不对称，假死探测器会读到撕裂值。
        self._handled_lock = threading.Lock()

        # ★主线第63批 T1/P0：统计专用细粒度锁，解耦 publish 步骤7 与主流程锁 self._lock。
        #   背景：锁等待 100% 来自 publish.step7_stats（self._lock 保护简单计数器
        #   _total_matched/_total_unmatched/_event_publish_count 等），高并发（队列深度7000+）
        #   下所有 publish 线程抢同一把 self._lock，等待飙升（实测平均300ms，单次最高3840ms）。
        #   解耦后步骤7 只与「另一个步骤7」竞争，不再和 publish 幂等/历史/分发等临界区互阻塞。
        #   灰度开关 ENABLE_PUBLISH_STATS_FINE_LOCK（默认开）：关闭时步骤7 退回用 self._lock（原行为）。
        self._stats_lock = threading.Lock()
        self._use_stats_fine_lock = True
        try:
            import config as _m63_cfg
            self._use_stats_fine_lock = bool(getattr(_m63_cfg, "ENABLE_PUBLISH_STATS_FINE_LOCK", True))
        except Exception as _m63_e:
            self._use_stats_fine_lock = True
            _module_logger.debug(f"[统计锁] 灰度开关读取失败，使用默认开启: {type(_m63_e).__name__}: {_m63_e}")

        # 关联组件（由 main.py 注入）
        self.pulse_core = None
        self.resonance_engine = None
        self.oscillon_field = None
        self.stream_miner = None
        
        # 统计
        self._total_published = 0
        self._total_handled = 0  # ★P1: 已完成（handler 执行完毕）的脉冲计数，供假死探测器判断框架是否真正在运转
        self._total_matched = 0
        self._total_unmatched = 0
        self._total_duplicated = 0   # v9.5新增: 幂等拦截计数
        self._total_expired = 0      # v9.5新增: TTL过期计数
        self._total_storm_blocked = 0  # v9.5新增: 风暴拦截计数

        # ★登顶路线图-协调层：死数据/幽灵监听审计统计（零侵入，仅计数不改分发）
        self._cond_trigger_count: dict[str, int] = {}      # cond_id → 触发次数（幽灵监听判定）
        self._event_publish_count: dict[str, int] = {}     # event_type → 发布次数（死数据判定）
        self._event_unmatched_count: dict[str, int] = {}   # event_type → 无条件匹配次数
        
        # ===== v9.5: 分层异步调度线程池 =====
        self._layer_pools: dict[str, ThreadPoolExecutor] = {}
        self._layer_disabled: dict[str, bool] = {}        
        # ★主线第33批 T4（P3）：分层队列上限（0=无上限）默认值。
        #   ★必须在 _init_layer_pools() **之前**赋值 —— 该方法会按 config.PULSE_LAYER
        #   覆盖本字典（L3 由 config 提供 300）。历史缺陷：默认值写在它**之后**，
        #   于是硬编码 L3=100 把 config 的 300 覆盖回去，使背压实际按 100 生效。
        #   保底作用不变：确保 _admit_layer_task 在池初始化前调用不 KeyError。
        self._layer_queue_limits = {
            _PULSE_LAYER_L0: 0,      # 生命线：永不丢弃
            _PULSE_LAYER_L1: 500,    # 软上限
            _PULSE_LAYER_L2: 200,    # 标准上限
            _PULSE_LAYER_L3: 100,    # 硬上限（仅 config 未提供时兜底）
        }
        self._init_layer_pools()
        
        # ===== v9.5: 高负载状态标记 =====
        self._high_load = False
        self._high_load_since = 0.0
        self._last_load_check = 0.0
        self._load_check_interval = 5.0  # 每5秒检查一次系统负载
        
        # ===== v9.5: 场域模式 =====
        self._field_mode = PULSE_LAYER.get("field_mode", "PULSE")
        
        # ===== v9.5: 幂等防重放缓存 =====
        self._processed_pulses: OrderedDict = OrderedDict()
        self._max_processed_cache = 5000
        self._processed_ttl_seconds = 5.0
        
        # ===== v9.5: 脉冲风暴检测 =====
        self._pulse_rate_window: deque = deque()
        self._storm_threshold = PULSE_LAYER.get("pulse_storm_threshold", 500)
        self._storm_action = PULSE_LAYER.get("pulse_storm_action", _STORM_AGGREGATE)
        self._in_storm = False
        
        # ===== v9.5: 器官并发计数 =====
        self._organ_concurrent_count: dict[str, int] = {}
        self._organ_concurrent_lock = threading.Lock()
        self._organ_max_concurrent = PULSE_LAYER.get("organ_max_concurrent_pulses", 0)
        # ★主线第13批 P2-87：按层·按器官的在途任务计数。
        #   publish 提交任务时 +1，_dispatch_handler 完成/兜底时 -1。
        #   池重建时据此精确算出「被 cancel_futures 取消的数量」，
        #   从而精准递减 _organ_concurrent_count，而非暴力 clear。
        #   结构：{layer_tag: {organ_name: count}}
        self._layer_organ_inflight: dict[str, dict[str, int]] = {}
        
        # ===== v9.5: CPU亲和预埋 =====
        self._cpu_affinity_enabled = PULSE_LAYER.get("cpu_affinity_enabled", False)
        self._cpu_affinity_cores = PULSE_LAYER.get("cpu_affinity_cores", [])  # type: ignore[possibly-unbound]
        
        # ===== 自适应并行调度：硬件负载感知缓存 =====
        self._hardware_snapshot = {
            "cpu_usage": 0.0,
            "mem_usage": 0.0,
            "gpu_usage": 0.0,
            "gpu_available": False,
            "gpu_memory_mb": 0,
            "gpu_memory_usage": 0.0,   # 修正：GPU显存使用率（百分比）
            "updated_at": 0.0,
        }
        self._hardware_lock = threading.Lock()
        self._shutting_down = False     # 关闭标志，阻止新任务提交        
        # ===== 自适应任务线程池 =====
        self._adaptive_task_pool = None
        self._adaptive_pool_size = 0
        self._compute_level = "medium"
        self._max_adaptive_pool = 4
        self._active_task_count = 0
        self._task_count_lock = threading.Lock()
        self._task_pool_ready = False
        # ★主线第33批 T4（P3）：此处原为硬编码队列上限默认值，会覆盖
        #   _init_layer_pools() 按 config 设置的值（L3 300 → 100）。已上移到
        #   _init_layer_pools() 之前。灰度开关关闭时保留旧覆盖行为（零回归）。
        if not self._m33_queue_limit_order_fix_on():
            self._layer_queue_limits = {
                _PULSE_LAYER_L0: 0,      # 生命线：永不丢弃
                _PULSE_LAYER_L1: 500,    # 软上限
                _PULSE_LAYER_L2: 200,    # 标准上限
                _PULSE_LAYER_L3: 100,    # 硬上限
            }
        self._layer_rejected_count = {  # 被背压拒绝的任务计数（每层）
            _PULSE_LAYER_L0: 0, _PULSE_LAYER_L1: 0,
            _PULSE_LAYER_L2: 0, _PULSE_LAYER_L3: 0,
        }
        # ★P2-31：超限后「降级放行」的计数（高优先级脉冲在队列满时仍被放行）
        self._layer_overflow_allowed = {
            _PULSE_LAYER_L0: 0, _PULSE_LAYER_L1: 0,
            _PULSE_LAYER_L2: 0, _PULSE_LAYER_L3: 0,
        }
        # ★P2-31：降级放行阈值 —— priority 数值越小优先级越高，
        #   小于等于该值的脉冲在队列满时放行（默认 3，即高/紧急级）
        self._overflow_priority_allow = 3
        # ★三期：硬件自适应智能化（开关关闭时全部保持原行为，零回归）
        self._adaptive_tuning_enabled = self._read_adaptive_tuning_flag()
        self._hardware_tier = "high"          # 硬件 tier（high/standard/minimal），默认 high
        self._hardware_cores = 0              # 逻辑核数（0=未知）  # type: ignore[possibly-unbound]
        self._hardware_memory_gb = 0.0        # 总内存 GB（0=未知）
        self._load_threshold_hysteresis = 5.0  # 滞回幅度（%）：升级/降级阈值差 5%，防边界震荡
        # R5 例外突破机制：高优先级任务临时突破并行度上限
        self._override_deadline = 0.0         # 突破到期时间戳（0=未突破）
        self._override_max_duration = 300.0   # 突破最大时长 300 秒
        self._override_extra_parallelism = 0  # 突破额外并行度
        # ★P0-3修复: 在途分发计数（已提交未完成），用于估算四层线程池积压，
        # 替代 SimpleQueue.qsize()（Python 3.9+ 无此方法导致深度恒 0）。
        self._inflight_dispatch_count = 0

        # ★主线第5批 P1-42：L3 队列深度驱动动态扩缩容 + 可观测性状态
        self._l3_dynamic_enabled = self._read_l3_dynamic_scaling_flag()
        self._l3_depth_history = []            # [(ts, depth, limit)] 每分钟采样，保留约 2h
        self._l3_producer_counts = {}         # organ_name -> 累计 L3 脉冲数
        self._l3_completion_window = []       # L3 处理完成时间戳（滑窗，60s）
        # ★主线第16批 T5/P2-100：L3 生产端过滤与背压统计
        self._l3_filter_stats: dict[str, Any] = {
            "low_value": 0, "dedup": 0, "backpressure": 0,
            "backpressure_streak_peak": 0,
        }
        self._l3_dedup_seen: dict[str, float] = {}   # 载荷指纹 → 最近出现时间
        self._l3_backpressure_streak = 0
        self._l3_last_scale_ts = 0.0
        self._l3_scale_cooldown = 15.0        # 秒，缩放冷却防抖
        self._l3_max_dynamic_workers = 5      # 动态扩缩上限
        self._l3_base_workers = 2             # moderate 基准（与 _resize_layer_pools_by_level 对齐）

        # ★主线第6批 P1-42续：细化扩缩容参数（从 config.FEATURE 读取）
        try:
            from config import FEATURE as _FEAT
        except Exception:
            _FEAT = {}
        # ★主线第75批 T1：扩缩稳定性配置（滞回+冷却+平滑），从 config.L3_SCALING_STABILITY 读取
        #   （铁律C1：该块以 append 形式置于 config.py 末尾，不改 FEATURE 字面量）
        try:
            from config import L3_SCALING_STABILITY as _L3STAB
        except Exception:
            _L3STAB = {}
        _scale_cooldown = float(_L3STAB.get("scale_cooldown_sec", 60.0))
        self._l3_scale_up_cooldown = _scale_cooldown        # 扩容冷却（统一≥60s）
        self._l3_scale_down_cooldown = _scale_cooldown      # 缩容冷却（统一≥60s）
        self._l3_scale_step = max(1, int(_L3STAB.get("scale_step", 1)))  # 单次调整 worker 数（平滑±1）
        self._l3_hysteresis_down_ratio = float(_L3STAB.get("hysteresis_down_ratio", 0.50))
        self._l3_hysteresis_enabled = bool(_L3STAB.get("hysteresis_enabled", True))
        self._l3_scale_up_thresholds = list(_FEAT.get("l3_scale_up_thresholds", [0.70, 0.85, 0.95]))
        self._l3_burst_detection_enabled = bool(_FEAT.get("l3_burst_detection_enabled", True))
        self._l3_burst_growth_threshold = float(_FEAT.get("l3_burst_growth_threshold", 0.50))
        # ★主线第7批 P1-67：最小 worker + 缩容延迟缓冲
        self._l3_min_workers = int(_FEAT.get("l3_min_workers", 3))
        self._l3_scale_down_buffer_sec = float(_FEAT.get("l3_scale_down_buffer_sec", 30.0))
        self._l3_base_workers = max(self._l3_base_workers, self._l3_min_workers)
        self._l3_below_half_since = 0.0
        self._l3_burst_window = []
        self._l3_scale_up_count = 0
        self._l3_scale_down_count = 0
        self._l3_last_scale_reason = ""
        self._l3_demand_start_ts = 0.0
        self._l3_response_times = []
        self._l3_depth_sample_ts = 0.0
        # ★P1修复：器官接收脉冲活跃追踪（供血管沉默检测合并）
        self._organ_last_active: dict[str, float] = {}
        
        # ===== 统一负载等级 =====
        self._load_level = "light"
        # ★P0-7修复：外部强制等级的过期时间戳（紧急处理器 set_load_level 时设置，
        # 过期后内部 _check_high_load 接管，避免 heavy 无出口卡死）
        self._external_level_expiry = 0.0
        self._cpu_usage = 0.0
        self._mem_usage = 0.0
        self._gpu_usage = 0.0
        # ★162批刀1·A1链A：硬件探针健康度（无数据时置 False，对外状态可观测）
        self._hardware_probe_ok = True
        self._load_probe_failed = False
        self._load_probe_fail_count = 0
        
        # ===== 持续时间保护相关 =====
        self._high_load_since_level = None
        self._high_load_since_time = 0.0
        
        # ===== 线程池调整记录 =====
        self._last_resized_level = "light"   # 上次调整四层池时的负载等级

        # ===== L0生命线看门狗（P0-1修复）：L0为单线程，任一处理器阻塞将冻结心跳/告警 =====
        self._l0_timeout = float(PULSE_LAYER.get("l0_processor_timeout", 8.0))  # ★相关任务：超时阈值改读 config.PULSE_LAYER.l0_processor_timeout（默认 8.0s），消除硬编码漂移
        self._l0_inflight: dict = {}    # pulse_id -> (入队时间戳, 订阅器官名, 处理线程标识)
        self._l0_watchdog = threading.Thread(
            target=self._l0_watchdog_loop, name="L0-Watchdog", daemon=True
        )
        self._l0_watchdog.start()
        # ★v9.x TimeCore：注册全局引用，使 TimeCore 内核单例可获取本信息场实例（无需修改 main.py）
        global _THE_INFO_FIELD
        _THE_INFO_FIELD = self
    def _init_layer_pools(self):
        """v9.5: 初始化四层独立线程池 + 自适应任务线程池（硬件自适应）"""
        _cpu_count = os.cpu_count() or 8
        
        # 根据 CPU 核心数动态分配线程池
        # L0 必须保持单线程
        _l0_workers = 1
        # L1/L2 各分配 CPU 核心数的 50%
        _l1_workers = max(4, _cpu_count // 2)
        # ★主线第57批 T4（P2-394）：L2 worker 数改由 config.PULSE_LAYER.l2_threads 驱动
        # （此前 l2_threads 配置项从未被读取，长期被 cpu 公式覆盖）。l2_threads<=0 回退 cpu 公式。
        _l2_cfg_threads = int(PULSE_LAYER.get("l2_threads", 0) or 0)
        _l2_workers = _l2_cfg_threads if _l2_cfg_threads > 0 else max(4, _cpu_count // 2)
        # L3 分配 CPU 核心数的 25%
        _l3_workers = max(2, _cpu_count // 4)
        # 自适应任务池分配 CPU 核心数的 50%
        _adaptive_workers = max(4, _cpu_count // 2)
        
        layer_config = PULSE_LAYER

        # ★P0-4修复（第十二批）：让 config.PULSE_LAYER 的队列上限键真正生效。
        #   此前这 7 个键（l0_queue_max / l1_queue_soft_limit / l2_queue_limit /
        #   l3_queue_hard_limit）全项目零处读取，而 ThreadPoolExecutor 内部队列是
        #   无界 SimpleQueue —— 稳态规则 7「风暴队列上限」在实现层是空的，
        #   脉冲风暴时任务在堆内存无限堆积，存在 OOM 风险。
        #   语义（沿用 config.py 原注释）：
        #     L0 = 0     → 生命线，永不丢弃（0 表示无上限）
        #     L1 = 500   → 软上限：超限先告警并限流，达 2 倍才拒绝（限流不丢包）
        #     L2 = 200   → 标准上限：超限拒绝
        #     L3 = 100   → 硬上限：超限拒绝（后台自主层，丢弃代价最低）
        self._layer_queue_limits = {
            _PULSE_LAYER_L0: self._as_int(layer_config.get("l0_queue_max", 0), 0),
            _PULSE_LAYER_L1: self._as_int(layer_config.get("l1_queue_soft_limit", 500), 500),
            _PULSE_LAYER_L2: self._as_int(layer_config.get("l2_queue_limit", 200), 200),
            _PULSE_LAYER_L3: self._as_int(layer_config.get("l3_queue_hard_limit", 100), 100),
        }
        _module_logger.info(
            f"[背压] 分层队列上限已生效: "
            f"L0={self._layer_queue_limits[_PULSE_LAYER_L0] or '无上限(生命线)'}, "
            f"L1={self._layer_queue_limits[_PULSE_LAYER_L1]}(软), "
            f"L2={self._layer_queue_limits[_PULSE_LAYER_L2]}, "
            f"L3={self._layer_queue_limits[_PULSE_LAYER_L3]}")

        if layer_config.get("l0_enabled", True):
            self._layer_pools[_PULSE_LAYER_L0] = ThreadPoolExecutor(
                max_workers=_l0_workers,
                thread_name_prefix="L0-Life"
            )
        
        if layer_config.get("l1_enabled", True):
            self._layer_pools[_PULSE_LAYER_L1] = ThreadPoolExecutor(
                max_workers=_l1_workers,
                thread_name_prefix="L1-RealTime"
            )
        
        if layer_config.get("l2_enabled", True):
            self._layer_pools[_PULSE_LAYER_L2] = ThreadPoolExecutor(
                max_workers=_l2_workers,
                thread_name_prefix="L2-Cognitive"
            )
        
        if layer_config.get("l3_enabled", True):
            self._layer_pools[_PULSE_LAYER_L3] = ThreadPoolExecutor(
                max_workers=_l3_workers,
                thread_name_prefix="L3-Autonomous"
            )
        
        self._adaptive_pool_size = _adaptive_workers
        self._adaptive_task_pool = ThreadPoolExecutor(
            max_workers=self._adaptive_pool_size,
            thread_name_prefix="AdaptiveTask"
        )
        self._task_pool_ready = True
        
        _module_logger.info(f"线程池已初始化 (CPU={_cpu_count}核): "
              f"L0={_l0_workers}, L1={_l1_workers}, L2={_l2_workers}, "
              f"L3={_l3_workers}, Adaptive={_adaptive_workers}")
    
    # ========== 框架注入接口 ==========
    
    def set_pulse_core(self, pulse_core):
        self.pulse_core = pulse_core
        pulse_core.set_info_field(self)
        
    def set_resonance_engine(self, resonance_engine):
        self.resonance_engine = resonance_engine
        
    def set_oscillon_field(self, oscillon_field):
        self.oscillon_field = oscillon_field

    def set_stream_miner(self, stream_miner):
        self.stream_miner = stream_miner
    
    def propagate_oscillon(self, frequency: float, amplitude: float, 
                            phase: float) -> dict[str, Any]:
        return {}

    # ========== 脉冲发布 ==========
    def publish(self, pulse: dict[str, Any]) -> dict[str, Any]:
        # ★P1 运行时埋点: 记录脉冲处理耗时起点（零阻塞，仅在结尾投递）
        _rt_start = time.time()
        _lock_wait_ms = 0.0  # ★P1续: 累计本脉冲路径上的锁等待时长
        # ★PHASE13（2026-09-07）：按锁名分桶累计，供 runtime_metrics 告警时
        #   回答「到底是哪把锁」。原实现只报一个总均值，运维看到 70ms 却无从下手
        #   （内部协作者据此猜错方向，以为是 node_pool 的全局锁）。
        #   此处仅做旁路累计，不改动任何调用签名与既有字段（★零侵入）。
        _lock_wait_by_name: dict[str, float] = {}
        pulse_id = pulse.get("pulse_id", "?")
        event_type = pulse.get("event_type", "?")
        priority = pulse.get("priority", PULSE_PRIORITY.get("NORMAL", 5))
        layer = pulse.get("layer", _PULSE_LAYER_L1)
        ttl_ns = pulse.get("ttl_ns", 5_000_000_000)
        timestamp_ns = pulse.get("timestamp_ns", time.time_ns())
        route_mode = pulse.get("route_mode", CONCURRENT_COMM.get("dual_route_default", _ROUTE_PARALLEL))

        # ★主线第25批 T1/P2-160：重复事件遥测（灰度；关闭时函数首行即返回）
        self._dup_trace_check(pulse, event_type)
        
        # 缓存设备能力更新
        if event_type == "device.capability_update":
            caps = pulse.get("payload", {}).get("capabilities", {})
            if caps:
                new_level = caps.get("compute.level", "medium")
                if new_level != self._compute_level:
                    old_level = self._compute_level
                    self._compute_level = new_level
                    _module_logger.info(f"计算能力更新: {old_level} → {new_level}")
                    self._adjust_adaptive_pool()

        # 缓存硬件快照
        if event_type == "touch.hardware_snapshot":
            with self._hardware_lock:
                payload_data = pulse.get("payload", {})
                cpu_info = payload_data.get("cpu", {})
                mem_info = payload_data.get("memory", {})
                gpu_info = payload_data.get("gpu", {})
                
                self._hardware_snapshot["cpu_usage"] = cpu_info.get("usage_percent", 0)
                self._hardware_snapshot["mem_usage"] = mem_info.get("usage_percent", 0)
                self._hardware_snapshot["gpu_available"] = gpu_info.get("available", False)
                self._hardware_snapshot["gpu_memory_mb"] = gpu_info.get("memory_mb", 0)
                self._hardware_snapshot["updated_at"] = time.time()
                self._hardware_snapshot["gpu_usage"] = gpu_info.get("usage_percent", 0.0)
                
                # 安全解析 GPU 显存使用率（可能为空字符串或无效值）
                mem_usage_raw = gpu_info.get("memory_usage_percent", 0.0)
                try:
                    self._hardware_snapshot["gpu_memory_usage"] = float(mem_usage_raw) if mem_usage_raw else 0.0
                except (ValueError, TypeError):
                    self._hardware_snapshot["gpu_memory_usage"] = 0.0
                
                # 同步变量
                self._cpu_usage = self._hardware_snapshot["cpu_usage"]
                self._mem_usage = self._hardware_snapshot["mem_usage"]
                self._gpu_usage = self._hardware_snapshot.get("gpu_usage", 0.0)
                
                # ★三期：注入硬件能力快照（tier/cores/memory），供运行时自适应调度消费。
                # 仅在开关开启时生效；开关关闭时这些字段保持默认（high/0/0），不影响原行为。
                if self._adaptive_tuning_enabled:
                    _cores = int(cpu_info.get("cores", 0) or 0)
                    _mem_gb = float(mem_info.get("total_gb", 0.0) or 0.0)
                    _has_gpu = bool(gpu_info.get("available", False))
                    if _cores > 0:  # type: ignore[possibly-unbound]
                        self._hardware_cores = _cores  # type: ignore[possibly-unbound]
                    if _mem_gb > 0:
                        self._hardware_memory_gb = _mem_gb
                    # 用一/二期同款评分逻辑反推 tier（与 hardware_probe 语义一致）
                    self._hardware_tier = self._infer_tier(self._hardware_cores, self._hardware_memory_gb, _has_gpu)  # type: ignore[possibly-unbound]
        
        # === 步骤0: TTL校验 ===
        now_ns = time.time_ns()
        if ttl_ns > 0 and (now_ns - timestamp_ns) > ttl_ns:
            if not (PULSE_LAYER.get("disable_ttl_for_l0_l1", True) and layer in (_PULSE_LAYER_L0, _PULSE_LAYER_L1)):
                with self._lock:
                    self._total_expired += 1
                return {"status": "expired", "pulse_id": pulse_id,
                        "reason": f"脉冲已过期 (TTL={ttl_ns}ns, 已过{now_ns - timestamp_ns}ns)"}
        
        # === 步骤1: 幂等检查（业务指纹去重） ===
        # ★FIX: 原实现直接用唯一 pulse_id 去重，永不命中；改用业务指纹，
        #   使 source+event+payload 相同的重复业务事件能被真正去重
        _dedup_key = pulse_id
        if self.pulse_core is not None and pulse.get("payload"):
            try:
                _dedup_key = self.pulse_core.business_fingerprint(pulse)
            except Exception:
                _dedup_key = pulse_id
        if self._is_duplicate(_dedup_key):
            with self._lock:
                self._total_duplicated += 1
            return {"status": "duplicate", "pulse_id": pulse_id, "reason": "已处理过"}
        
        # === 步骤2: 脉冲风暴检测 ===
        storm_result = self._check_pulse_storm()
        if storm_result["in_storm"]:
            action = self._storm_action
            if action == _STORM_REJECT:
                with self._lock:
                    self._total_storm_blocked += 1
                return {"status": "storm_blocked", "pulse_id": pulse_id, "reason": "脉冲风暴，已拒绝"}
            elif action == _STORM_AGGREGATE:
                pulse["storm_aggregated"] = True
                pulse["priority"] = max(1, priority - 3)
                # ★P1-3修复：聚合模式下对非 L0/L1 脉冲概率性降载（约 30%），
                # 原实现只降 priority，而 priority 不影响实际吞吐，属空操作。
                if layer not in (_PULSE_LAYER_L0, _PULSE_LAYER_L1) and random.random() < 0.3:
                    with self._lock:
                        self._total_storm_blocked += 1
                    return {"status": "storm_blocked", "pulse_id": pulse_id, "reason": "风暴聚合降载"}
            elif action == _STORM_THROTTLE:
                pulse["storm_throttled"] = True
                # ★P1-3修复：限流模式下对非 L0/L1 脉冲丢弃约 50%
                if layer not in (_PULSE_LAYER_L0, _PULSE_LAYER_L1) and random.random() < 0.5:
                    with self._lock:
                        self._total_storm_blocked += 1
                    return {"status": "storm_blocked", "pulse_id": pulse_id, "reason": "风暴限流降载"}

        # === 步骤3: 高负载自适应 ===
        self._check_high_load()

        # === 步骤4: 记录历史 + 幂等标记 ===
        _lock_acquire_start = time.time()
        with self._lock:
            _lock_wait_ms += (time.time() - _lock_acquire_start) * 1000.0
            _lock_wait_by_name["publish.step4_history"] = (
                _lock_wait_by_name.get("publish.step4_history", 0.0)
                + (time.time() - _lock_acquire_start) * 1000.0)
            self._history.append({
                "pulse_id": pulse_id,
                "event_type": event_type,
                "source_organ": pulse.get("source_organ", "?"),
                "priority": priority,
                "layer": layer,
                "timestamp_ns": timestamp_ns,
                "ttl_ns": ttl_ns,
                "payload_keys": list(pulse.get("payload", {}).keys()),
                "payload": dict(pulse.get("payload", {})),   # ★FIX(H1): 存储完整 payload 供 get_current 读取
            })
            self._total_published += 1
            self._mark_processed(_dedup_key)
            conditions_snapshot = tuple(self._conditions.items())  # ★PERF-2修复: 轻量元组快照替代整字典拷贝

        # ★FIX: 喂入事件流挖掘器，并周期性发现模式
        if self.stream_miner is not None and self.stream_miner.is_enabled():
            try:
                self.stream_miner.feed_event({
                    "event_type": event_type,
                    "source_organ": pulse.get("source_organ", "?"),
                    "timestamp": time.time(),
                })
                if self._total_published % 200 == 0:
                    _patterns = self.stream_miner.discover_patterns()
                    if _patterns:
                        _module_logger.info(f"事件模式发现: {len(_patterns)} 个模式")
                        # ★P1补强：模式应用——高频模式发射预测信号
                        self._apply_discovered_patterns(_patterns)
            except Exception as _exc:
                _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
        # === 步骤5: 匹配条件 ===
        matched_organs = []
        matched_cond_ids = []  # ★登顶路线图-协调层：触发计数记录
        dispatches = []

        # ★P1-2修复：按条件的 min_priority 降序排序，使只接受高优先级脉冲的关键器官
        # 优先分发（原实现按注册顺序，优先级字段完全不参与调度）。
        # 注：跨脉冲的严格优先级需线程池级优先队列，属 P3 架构改造，此处先闭合单脉冲内排序。
        _sorted_conditions = sorted(
            conditions_snapshot,
            key=lambda _kv: (_kv[1].get("condition", {}) or {}).get("min_priority", 0),
            reverse=True,
        )

        for cond_id, cond_data in _sorted_conditions:
            condition = cond_data.get("condition", {})
            handler = cond_data.get("handler")
            if self._match_condition(pulse, condition):
                organ_name = condition.get("organ_name", "unknown")
                matched_organs.append(organ_name)
                matched_cond_ids.append(cond_id)  # ★审计：记录触发的条件
                if handler is not None:
                    if self._check_organ_concurrency(organ_name):
                        dispatches.append((layer, handler, organ_name, route_mode))

        # ★相关任务：boot 脉冲派发时把「心脏」置顶——抢占 L0 唯一 worker，
        #   先于 L0 看门狗超时重建(cancel_futures)完成起搏，避免心脏 future 被连坐取消。
        if event_type == "system.boot":
            _heart_idx = next((i for i, d in enumerate(dispatches)
                               if d[2] == "心脏"), None)
            if _heart_idx is not None and _heart_idx != 0:
                dispatches.insert(0, dispatches.pop(_heart_idx))

        # === 步骤6: 按layer异步分发 ===
        _boot_items: list = []
        for layer_tag, handler, organ_name, mode in dispatches:
            pool = self._layer_pools.get(layer_tag)
            # 懒重建：如果池缺失但该层未被禁用，则重建一个默认大小的池
            if pool is None and not self._layer_disabled.get(layer_tag, False):
                if PULSE_LAYER.get(f"{layer_tag.lower()}_enabled", True):
                    _default_workers = {
                        _PULSE_LAYER_L0: 1,
                        _PULSE_LAYER_L1: max(4, (os.cpu_count() or 8) // 2),
                        _PULSE_LAYER_L2: max(4, (os.cpu_count() or 8) // 2),
                        _PULSE_LAYER_L3: max(2, (os.cpu_count() or 8) // 4),
                    }.get(layer_tag, 2)
                    pool = ThreadPoolExecutor(
                        max_workers=_default_workers,
                        thread_name_prefix=f"{layer_tag}-Rebuilt"
                    )
                    self._layer_pools[layer_tag] = pool
            
            if pool is not None and not self._layer_disabled.get(layer_tag, False):
                # ★P0-4（第十二批）：分层队列有界背压准入。
                #   放在 _increment_organ_concurrency 之前——被拒绝时不做任何自增，
                #   因此不存在并发计数泄漏，无需额外的回滚分支。
                #   L0 上限为 0（无上限），生命线脉冲永不被拒绝。
                _pri = pulse.get("priority", 5) if isinstance(pulse, dict) else 5
                # ★主线第16批 T5/P2-100：L3 生产端过滤（重复/低价值不占队列）
                if layer_tag == _PULSE_LAYER_L3 and self._should_filter_l3_pulse(
                        pulse, organ_name):
                    continue
                if not self._admit_layer_task(layer_tag, pool, organ_name, _pri):
                    if layer_tag == _PULSE_LAYER_L3:
                        self.note_l3_backpressure(layer_tag)
                    continue
                if layer_tag == _PULSE_LAYER_L3:
                    self.note_l3_admitted()
                self._increment_organ_concurrency(organ_name)
                try:
                    # ★P0-1/INFRA-3修复: 池可能在看门狗重建瞬间被关闭，submit 会抛 RuntimeError，
                    # 此处捕获并降级为同步执行，避免脉冲丢失与崩溃
                    # ★7-1/P1-13修复(2026-09-05): 计数器原子化。
                    #   本处位于多线程脉冲分发核心路径，裸 `+= 1` 存在竞态：
                    #   `+=` 非原子（读-改-写三步），并发下发时计数会偏小，
                    #   继而影响依赖该值的在途任务判断。
                    #   刻意**只包住自增本身、不包住 pool.submit()**——
                    #   若把 submit 也锁进去，等于把并发提交串行化，
                    #   四层池的吞吐优势会被这一把锁吃掉。
                    with self._task_count_lock:
                        self._inflight_dispatch_count += 1
                    # ★P2-87：记录该层·该器官的在途任务（池重建时据此精确递减）
                    self._track_layer_inflight(layer_tag, organ_name, +1)
                    _fut = pool.submit(self._dispatch_handler, pulse, handler, organ_name, mode, layer_tag)
                    if event_type == "system.boot":
                        _boot_items.append((organ_name, _fut))
                    if layer_tag == _PULSE_LAYER_L3:
                        self._l3_producer_counts[organ_name] = self._l3_producer_counts.get(organ_name, 0) + 1
                except RuntimeError:
                    with self._task_count_lock:
                        self._inflight_dispatch_count = max(0, self._inflight_dispatch_count - 1)
                    # ★P2-87：submit 失败 → 在途计数回滚（该任务不会执行）
                    self._track_layer_inflight(layer_tag, organ_name, -1)
                    if event_type == "system.boot":
                        _boot_items.append((organ_name, "handled_sync"))
                    # ★S5修复：同步兜底路径不经过_dispatch_handler，
                    # 其finally中的_decrement_organ_concurrency不会被执行，
                    # 器官并发计数将永久+1。累积达_organ_max_concurrent上限后，
                    # _check_organ_concurrency会永久拒收该器官的脉冲。
                    # 故此处用try/finally保证任何情况下都递减。
                    try:
                        self._call_handler_safe(pulse, handler)
                    finally:
                        self._decrement_organ_concurrency(organ_name)
                        # ★P2-8修复：同步兜底路径也计入「已处理」，避免假死探测器误报
                        # ★7-1/P1-13：_total_handled 是假死探测器
                        #   （main.py:3007 `_handled == _last`）的唯一数据源。
                        #   裸自增丢一次增量，就可能让「其实在正常处理」的框架
                        #   被误判为冻结并 dump 堆栈。与读端 get_total_handled
                        #   （:1278）使用同一把 self._lock，保证读写对称。
                        # ★PHASE14-锁优化：同步兜底路径同样改抢 _handled_lock，
                        #   该路径下锁等待埋点（dispatch.total_handled）保留，
                        #   改测 _handled_lock 的等待，语义不变、仍可观测。
                        _lock_acquire_start = time.time()
                        with self._handled_lock:
                            _lock_wait_ms += (time.time() - _lock_acquire_start) * 1000.0
                            _lock_wait_by_name["dispatch.total_handled"] = (
                                _lock_wait_by_name.get("dispatch.total_handled", 0.0)
                                + (time.time() - _lock_acquire_start) * 1000.0)
                            self._total_handled += 1
                        # ★R7修复：同步兜底路径同样必须刷新器官活跃时间。
                        #   _organ_last_active 是血管沉默检测的唯一数据源
                        #   （PulseBloodVessel.py:186-195 合并后用于判定），
                        #   原实现只在 _dispatch_handler(:591) 中更新，
                        #   本兜底路径不经该方法 → 走兜底的器官活跃时间不刷新
                        #   → L0看门狗重建线程池期间（每条脉冲都走兜底）
                        #     全部器官被误判为沉默。
                        try:
                            self._organ_last_active[organ_name] = time.time()
                        except Exception as _exc:
                            _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
                except BaseException:
                    # ★S5修复：submit抛出RuntimeError之外的异常时同样必须递减，
                    # 否则并发计数泄漏（原实现此处无任何decrement）
                    self._inflight_dispatch_count = max(0, self._inflight_dispatch_count - 1)
                    self._decrement_organ_concurrency(organ_name)
                    raise
            else:
                # 池缺失时同步执行（兜底）
                self._call_handler_safe(pulse, handler)
                # ★P2-8修复：同步兜底路径也计入「已处理」
                # ★7-1/P1-13：同上，与读端同锁（详见 :514 处注释）
                # ★PHASE14-锁优化：池缺失兜底路径同样改抢 _handled_lock
                _lock_acquire_start = time.time()
                with self._handled_lock:
                    _lock_wait_ms += (time.time() - _lock_acquire_start) * 1000.0
                    _lock_wait_by_name["dispatch.total_handled"] = (
                        _lock_wait_by_name.get("dispatch.total_handled", 0.0)
                        + (time.time() - _lock_acquire_start) * 1000.0)
                    self._total_handled += 1
                # ★R7修复：同 RuntimeError 兜底路径，此处也需刷新器官活跃时间，
                #   否则池缺失期间所有器官的 _organ_last_active 停滞，
                #   会被血管沉默检测误判（详见上方同标记注释）。
                try:
                    self._organ_last_active[organ_name] = time.time()
                except Exception as _exc:
                    _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
        # ★相关任务：boot 完整率审计（堵 P2-87 黑洞）。后台守护线程等待各订阅者
        #   future 终态，枚举 handled/cancelled，确认心脏等生命线器官确实起搏。
        if event_type == "system.boot" and _boot_items:
            threading.Thread(
                target=self._audit_boot_completeness,
                args=(list(_boot_items),),
                name="boot-audit", daemon=True,
            ).start()

        # === 步骤7: 更新统计 ===
        # ★主线第63批 T1/P0：用细粒度 _stats_lock 替代全局 self._lock（解耦步骤7与publish主流程）。
        #   灰度关闭时退回 self._lock，与改造前完全一致（零回归）。
        _stats_lock = self._stats_lock if getattr(self, "_use_stats_fine_lock", True) else self._lock
        _lock_acquire_start = time.time()
        with _stats_lock:
            _lock_wait_ms += (time.time() - _lock_acquire_start) * 1000.0
            _lock_wait_by_name["publish.step7_stats"] = (
                _lock_wait_by_name.get("publish.step7_stats", 0.0)
                + (time.time() - _lock_acquire_start) * 1000.0)
            if matched_organs:
                self._total_matched += 1
            else:
                self._total_unmatched += 1
            # ★登顶路线图-协调层：审计统计（零侵入，仅计数）
            self._event_publish_count[event_type] = self._event_publish_count.get(event_type, 0) + 1
            if not matched_organs:
                self._event_unmatched_count[event_type] = self._event_unmatched_count.get(event_type, 0) + 1
            for _cid in matched_cond_ids:
                self._cond_trigger_count[_cid] = self._cond_trigger_count.get(_cid, 0) + 1

        # === 步骤8: 立即返回 ===
        # ★P1 运行时埋点: 投递脉冲处理耗时与队列深度（零阻塞 put_nowait）
        try:
            from nucleus.runtime_metrics import get_runtime_metrics
            _rt = get_runtime_metrics()
            # ★P0-3修复: SimpleQueue 无 qsize()，原实现深度恒为 0。
            # 改用自维护的在途分发计数（提交+1/完成-1）估算四层线程池积压。
            _depth = self._inflight_dispatch_count
            # ★PHASE13：旁路汇总「按锁名的等待时长」，供告警定位热点锁。
            #   纯旁路、失败即忽略，不影响主埋点投递（★零侵入）。
            _accumulate_lock_wait_by_name(_lock_wait_by_name)
            _rt.record_pulse(
                duration_ms=(time.time() - _rt_start) * 1000.0,
                lock_wait_ms=_lock_wait_ms,
                queue_depth=_depth,
            )
        except Exception as _exc:
            _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
        return {
            "status": "published",
            "pulse_id": pulse_id,
            "matched_organs": matched_organs,
            "total_conditions": len(conditions_snapshot),
            "matched_count": len(matched_organs),
            "dispatched_async": True,
            "layer": layer,
            "in_storm": storm_result["in_storm"],
            "high_load": self._high_load,
            "load_probe_ok": self._hardware_probe_ok,
        }
    
    # ========== ★相关任务：boot 完整率审计 ==========
    def _audit_boot_completeness(self, items):
        """后台守护线程：等待 boot 脉冲各订阅者 future 终态，枚举 handled/cancelled。

        目的：堵住 P2-87 黑洞——L0 看门狗超时重建池(cancel_futures=True)会静默
        取消仍排队的 boot future（此前心脏因此永不起搏）。本审计显式枚举每个订阅者
        的终态，使「心脏是否被取消」一目了然。
        """
        _results = []
        for _org, _v in items:
            if _v == "handled_sync":
                _results.append((_org, "handled"))
                continue
            _fut = _v
            try:
                _fut.result(timeout=20.0)
                _results.append((_org, "handled"))
            except Exception as _e:  # noqa: BLE001
                _name = type(_e).__name__
                if _name == "CancelledError":
                    _results.append((_org, "cancelled"))
                elif _name == "TimeoutError":
                    _results.append((_org, "timeout"))
                else:
                    _results.append((_org, f"error:{_name}"))
        _handled = sum(1 for _, _s in _results if _s == "handled")
        _cancelled = sum(1 for _, _s in _results if _s == "cancelled")
        _module_logger.info(
            f"[boot完整率] 订阅者={len(items)} handled={_handled} cancelled={_cancelled} | "
            + " ".join(f"{_o}={_s}" for _o, _s in _results)
        )

    def _dispatch_handler(self, pulse: dict[str, Any], handler: Callable, 
                          organ_name: str, route_mode: str, layer_tag: str | None = None):
        """
        分发脉冲到单个订阅者的处理函数。
        
        route_mode 说明：
        - parallel（默认）：直接调用 handler，所有订阅者并行执行
        - serial（预留）：链式调用，前一个订阅者的返回值传递给下一个
          当前版本 serial 与 parallel 执行相同逻辑，因为框架的串行链式调用
          需要完整的调用链管理和错误传播机制，待后续版本实现。
          CONCURRENT_COMM.dual_route_default 配置项已预埋，当前仅 parallel 生效。
        """
        _pid = pulse.get("pulse_id") if isinstance(pulse, dict) else None
        _is_l0 = (layer_tag == _PULSE_LAYER_L0)
        if _is_l0 and _pid is not None:
            # ★P0-2修复（第十批）：额外记录处理线程标识，供看门狗超时后
            #   用 sys._current_frames() 精确定位卡死线程的调用栈。
            self._l0_inflight[_pid] = (time.time(), organ_name, threading.get_ident())
        try:
            # ★P1-2修复：明确标注串行路由为预留功能，当前版本统一使用并行分发
            self._call_handler_safe(pulse, handler)
        finally:
            if _is_l0 and _pid is not None:
                self._l0_inflight.pop(_pid, None)
            # ★P2-87：在途计数 -1（与 publish 提交处的 +1 配对）
            if layer_tag is not None:
                self._track_layer_inflight(layer_tag, organ_name, -1)
            self._decrement_organ_concurrency(organ_name)
            # ★P0-1修复：无论 handler 是否抛异常，都必须计入“已分发/已处理”，
            # 供假死探测器（main.py:_liveness_watchdog）判断框架是否仍在运转。
            # 原实现放在 finally 之后，异常路径会跳过该行，导致计数恒为 0，
            # 看门狗因此每 30 秒误报“框架疑似冻结”。
            # ★7-1/P1-13：同上，与读端同锁。
            # ★PHASE14-锁优化：改抢 _handled_lock，与主锁解耦。
            #   这是全框架最热的一处自增——每个器官每处理完一个脉冲都要走一次。
            with self._handled_lock:
                self._total_handled += 1
            # ★P1修复：记录器官接收脉冲活跃时间（供血管沉默检测）
            try:
                self._organ_last_active[organ_name] = time.time()
            except Exception as _ola_e:
                _module_logger.debug(f"[活跃时间] 记录器官活跃时间异常(已忽略): {_ola_e}")
            # ★P0修复：通知 PulseCore 脉冲完成（原 _total_completed 恒为0）
            if self.pulse_core is not None:
                try:
                    self.pulse_core.notify_completed(pulse)
                except Exception as _nc_e:
                    _module_logger.debug(f"[脉冲完成] notify_completed 异常(已忽略): {_nc_e}")
            # ★主线第78批 T1/P0：在途分发计数减一（与 publish 提交时的 +1 对应），
            #   供运行时埋点估算四层线程池积压。
            #   ★修复竞态漂移：原实现此处「-1」未加锁，而 publish 提交处的「+1」
            #   （:724）使用 self._task_count_lock；±不对称锁在多线程下导致读-改-写
            #   互相覆盖、部分 -1 丢失 → 计数只增不减、队列深度指标持续偏高
            #   （内部协作者 2026-09-18 指认「:712 有锁 / :898 无锁，指标漂移」）。
            #   此处补锁使 +1/-1 对称，从根上消除漂移。
            with self._task_count_lock:
                self._inflight_dispatch_count = max(0, self._inflight_dispatch_count - 1)
            if layer_tag == _PULSE_LAYER_L3:
                self._record_l3_completion()
    
    def _call_handler_safe(self, pulse: dict[str, Any], handler: Callable):
        # ★P0-1修复：_handle_pulse_safe 分支原先无异常保护，异常会上抛导致
        # _total_handled 计数被跳过且异常被 pool.submit 的未来对象静默吞掉。
        # 这里统一 try/except，既保证异常可见（记日志），又不向外抛。
        try:
            if hasattr(handler, '__self__') and hasattr(handler.__self__, '_handle_pulse_safe'):
                handler.__self__.handle_pulse_safe(pulse)
            else:
                handler(pulse)
        except Exception as e:
            _module_logger.error(f"handler 异常: {e}")

    # ========== L0生命线看门狗（P0-1修复） ==========

    def _l0_watchdog_loop(self):
        """L0生命线看门狗：检测L0处理器超时（单线程阻塞）。

        由于Python无法中断运行中的线程，看门狗在发现超时后重建L0池
        （丢弃占用唯一worker的旧池及卡死线程），从而避免心跳/告警永久冻结。
        """
        while not self._shutting_down:
            # ★P0-2修复（第十二批）：循环体整体 try 保护。
            #   实测事故链：handler卡死 → 并发计数残留 → _resize_layer_pool 内
            #   self._log 抛 AttributeError → except 内再次 self._log → 二次抛出 →
            #   本线程（无保护）当场死亡 → L0 生命线永久失去超时保护，且日志已打印
            #   「重建L0池」造成假成功。看门狗是最后一道防线，它必须先保证自己活着。
            try:
                time.sleep(0.5)
                _now = time.time()
                _stuck = []
                with self._lock:
                    for _pid, _entry in list(self._l0_inflight.items()):
                        _ts = _entry[0]
                        _org = _entry[1]
                        if _now - _ts > self._l0_timeout:
                            _stuck.append((_pid, _org, _entry))
                if _stuck:
                    # ★P0-2修复（第十批）：dump 卡死线程调用栈，定位真实阻塞点。
                    #   此前只报「超时」无堆栈，内部协作者本地（Windows）偶发心脏失联告警
                    #   触发 L0 卡顿 8s，但无法定位卡死位置。此处用 sys._current_frames()
                    #   提取卡死线程的栈帧（不中断、不重启，仅采集诊断信息）。
                    try:
                        _frames = sys._current_frames()
                    except Exception:
                        _frames = {}
                    for _pid, _org, _entry in _stuck:
                        _tid = _entry[2] if len(_entry) >= 3 else None
                        _stack_lines = []
                        if _tid is not None and _tid in _frames:
                            _frame = _frames[_tid]
                            _stack_lines = traceback.format_stack(_frame)
                        _stack_snippet = "\n".join(_stack_lines[-12:]) if _stack_lines else "（无可用堆栈）"
                        _module_logger.error(
                            f"L0看门狗处理器超时({self._l0_timeout}s): "
                            f"器官={_org} pulse={_pid} —— 重建L0池以隔离卡死线程\n"
                            f"[P0-2诊断] 卡死线程(tid={_tid})调用栈:\n{_stack_snippet}")
                    # ★C1【P0】重建L0池前输出全局线程快照，辅助定位「是否有其他线程
                    #   连带卡死/资源争抢」。仅在看门狗超时（罕见）时打印，无常态性能开销。
                    try:
                        _snapshot = []
                        for _t in threading.enumerate():
                            _alive = _t.is_alive()
                            _daemon = _t.daemon
                            _snapshot.append(
                                f"  name={_t.name} ident={_t.ident} alive={_alive} daemon={_daemon}")
                        _module_logger.warning(
                            f"[C1诊断] 重建L0池前线程快照（共{len(_snapshot)}线程）:\n"
                            + "\n".join(_snapshot[:40]))
                    except Exception as _exc:
                        _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
                    # 重建L0池：丢弃占用唯一worker的旧池（卡死线程随之被废弃）
                    # ★FIX: force=True 强制重建，避免 current==target 时早退导致看门狗空转
                    self._resize_layer_pool(_PULSE_LAYER_L0, 1, force=True)
                    with self._lock:
                        for _pid, _org, _entry in _stuck:
                            self._l0_inflight.pop(_pid, None)
            except Exception as _wd_e:
                # 看门狗自身异常绝不能导致线程死亡——吞掉并继续下一轮
                try:
                    _module_logger.error(
                        f"[L0看门狗] 循环异常(已捕获,线程继续存活): {_wd_e}", exc_info=True)
                except Exception as _exc:
                    _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
                time.sleep(1.0)   # 异常时略微降频，避免疯狂刷日志

    # ========== v9.5: 幂等防重放 ==========
    
    def _is_duplicate(self, pulse_id: str) -> bool:
        now = time.time()
        with self._lock:
            _ts = self._processed_pulses.get(pulse_id)
            if _ts is None:
                return False
            # ★P1-1修复：TTL 过期视为非重复并清理，避免 5000 条容量窗口内误杀
            # 同业务事件（原 TTL 5 秒定义从未生效，导致长窗口内被误判重复）
            if now - _ts > self._processed_ttl_seconds:
                self._processed_pulses.pop(pulse_id, None)
                return False
            return True
    
    
    def _mark_processed(self, pulse_id: str):
        now = time.time()
        self._processed_pulses[pulse_id] = now
        # ★PERF-1修复: 移除每次 publish 的全量过期扫描（O(缓存大小) 热路径开销）→ 仅保留容量上限淘汰，
        #   过期项会在超过 _max_processed_cache 时由 popitem 自然淘汰，避免每脉冲无意义遍历
        while len(self._processed_pulses) > self._max_processed_cache:
            self._processed_pulses.popitem(last=False)
    
    # ========== v9.5: 脉冲风暴检测 ==========
    
    def _check_pulse_storm(self) -> dict[str, Any]:
        now = time.time()
        # ★P1-1修复：加锁保护脉冲速率窗口的并发读写
        with self._lock:
            self._pulse_rate_window.append(now)
            while self._pulse_rate_window and self._pulse_rate_window[0] < now - 1.0:
                self._pulse_rate_window.popleft()
            rate = len(self._pulse_rate_window)
            if rate >= self._storm_threshold:
                if not self._in_storm:
                    self._in_storm = True
                    _module_logger.warning(f"脉冲风暴检测: {rate}脉冲/秒, 动作={self._storm_action}")
                return {"in_storm": True, "rate": rate}
            else:
                if self._in_storm and rate < self._storm_threshold * 0.5:
                    self._in_storm = False
                    _module_logger.info(f"脉冲风暴解除: {rate}脉冲/秒")
                return {"in_storm": self._in_storm, "rate": rate}
    
    # ========== v9.5: 高负载自适应 ==========
    
    # ============ ★第169批 C8：内存压力（双口径 + 可配置阈值） ============
    def _m169_mem_thresholds(self) -> dict[str, float]:
        """内存告警阈值（可配置，不写死）。

        口径说明：系统内存占比（``psutil.virtual_memory().percent``）与
        进程 RSS 占比是**两个不同量纲** —— 进程 RSS 上限远低于物理内存，
        因此两者各自设阈值，任一越阈即视为内存压力成立。
        """
        try:
            _lay = getattr(_cfg, "PULSE_LAYER", {}) or {}
            _sys_thr = float(_lay.get("high_load_memory_threshold", 85.0))
        except Exception as _e:
            silent_exc(_e, where="nucleus.field.InfoField::_m169_mem_thresholds")
            _sys_thr = 85.0
        return {
            "system_percent": _sys_thr,
            "process_rss_mb": float(getattr(
                _cfg, "HIGH_LOAD_PROCESS_RSS_MB", 6144.0)),
        }

    def _m169_memory_pressure(self) -> dict[str, Any]:
        """内存压力读数：**系统占比 + 进程 RSS** 双口径。

        ``ok=False`` 表示采集失败（**不得**据此判低负载，也不得判高压）。
        """
        _thr = self._m169_mem_thresholds()
        _out = {"ok": False, "system_percent": None,
                "process_rss_mb": None, "breached": False, "reason": ""}
        try:
            import psutil
            _out["system_percent"] = float(psutil.virtual_memory().percent)
            _proc = psutil.Process()
            _out["process_rss_mb"] = round(
                float(_proc.memory_info().rss) / 1048576.0, 1)
            _out["ok"] = True
        except Exception as _e:
            silent_exc(_e, where="nucleus.field.InfoField::_m169_memory_pressure")
            _out["reason"] = "采集失败: %s" % type(_e).__name__
            return _out
        _sys_b = _out["system_percent"] > _thr["system_percent"]
        _rss_b = _out["process_rss_mb"] > _thr["process_rss_mb"]
        _out["breached"] = bool(_sys_b or _rss_b)
        _out["reason"] = ("系统内存 %.1f%% > %.1f%%" % (
            _out["system_percent"], _thr["system_percent"])) if _sys_b else (
            "进程 RSS %.1fMB > %.1fMB" % (
                _out["process_rss_mb"], _thr["process_rss_mb"])) if _rss_b else "正常"
        return _out

    def _m169_log_probe_failure(self) -> None:
        """采集失败留痕（★不得静默，也不得据此改判负载等级）。"""
        try:
            _module_logger.warning(
                "[C8] 硬件采样失败：本轮不据此判轻负载，保持当前等级=%s"
                % getattr(self, "_load_level", "?"))
        except Exception as _e:
            silent_exc(_e, where="nucleus.field.InfoField::_m169_log_probe_failure")

    def _check_high_load(self):
        """检查系统负载，高负载时自动降低各层线程池活跃度（增强版：持续时间保护 + 线程池延迟调整）"""
        now = time.time()
        if now - self._last_load_check < self._load_check_interval:
            return
        
        self._last_load_check = now
        
        # ★P0-7修复：外部强制等级在 60 秒有效期内，内部检测不覆盖（尊重紧急降级）；
        # 过期后内部检测接管，可立即降回 light，避免 hardware_warning→heavy 无出口卡死。
        if self._external_level_expiry:
            if time.time() < self._external_level_expiry:
                return
            self._external_level_expiry = 0.0
        
        # 优先使用触觉缓存的硬件数据
        with self._hardware_lock:
            cpu_percent = self._hardware_snapshot["cpu_usage"]
            mem_percent = self._hardware_snapshot["mem_usage"]
        
        # 如果缓存数据为空或过期，回退到直接检测
        if cpu_percent == 0 and mem_percent == 0:
            try:
                import psutil
                cpu_percent = psutil.cpu_percent(interval=0.1)
                mem_percent = psutil.virtual_memory().percent
            except ImportError:
                cpu_percent = 0
                mem_percent = 0
            except Exception as _probe_err:
                # ★162批刀1·A1链A：psutil 已装但运行时异常（OSError/PermissionError 等）
                # 须兜底捕获并标记，否则会穿出 _check_high_load 到 publish() 热路径。
                silent_exc(_probe_err, where="nucleus.field.InfoField::_check_high_load psutil fallback")
                cpu_percent = 0
                mem_percent = 0
            # ★第169批 C8：采集可用性判定 —— **刻意不改动既有 except 体**
            #   （改体会让既有静默 handler 指纹漂移，被 cw2 判为「新增」）
            try:
                import psutil as _ps_probe  # noqa: F401
                self._m169_hw_probe_ok = True
            except Exception as _pe:
                silent_exc(_pe, where="nucleus.field.InfoField::_check_high_load psutil probe")
                self._m169_hw_probe_ok = False
        # 获取GPU使用率（使用正确的字段名）
        with self._hardware_lock:
            gpu_percent = self._hardware_snapshot.get("gpu_usage", 0.0)
            gpu_mem_percent = self._hardware_snapshot.get("gpu_memory_usage", 0.0)
            if not isinstance(gpu_mem_percent, (int, float)) or gpu_mem_percent < 0:
                gpu_mem_percent = 0.0

        if cpu_percent == 0 and mem_percent == 0:
            # ★第169批 C8：**采集失败 ≠ 真实低负载** —— 若本轮 psutil 采样失败，
            #   不得据此降级为 light（那会让 heavy/critical 告警静默失效），
            #   改为保持当前等级 + 留痕。也不产生伪 heavy/critical。
            if getattr(self, "_m169_hw_probe_ok", True) is False:
                self._m169_log_probe_failure()
                self._m169_hw_probe_ok = True
                return
            # 无硬件数据时，视为轻负载，确保异步任务能正常提交
            if self._load_level != "light":
                self._load_level = "light"
                self._high_load = False
                self._adjust_adaptive_pool()
                self._resize_layer_pools_by_level()
            # ★162批刀1·A1链A：探针失败可观测标记（降级必带标记，避免静默失效）。
            # 不得改成"无数据即视为 heavy"（反向故障）；仅置标记+限次告警。
            self._hardware_probe_ok = False
            if getattr(_cfg, "ENABLE_INFOFIELD_LOAD_PROBE_MARKER", True):
                self._load_probe_failed = True
                self._load_probe_fail_count += 1
                if self._load_probe_fail_count <= 1:
                    _module_logger.warning(
                        "硬件探针无数据(0/0)：负载按 light 处理，降级标记已置 load_probe_ok=False"
                    )
            return

        load_level = self._get_load_level(cpu_percent, mem_percent, gpu_percent, gpu_mem_percent)
        
        was_high_load = self._high_load
        self._high_load = load_level in ("heavy", "critical")
        
        if self._high_load and not was_high_load:
            self._high_load_since = now
        elif not self._high_load and was_high_load:
            self._high_load_since = 0.0
        
        # ===== 持续时间保护：等级升高需持续 30 秒才生效，降低立即生效 =====
        if load_level != self._load_level:
            if self._is_higher_level(load_level, self._load_level):
                # 等级升高：需要持续 45 秒才切换
                if self._high_load_since_level != load_level:
                    self._high_load_since_level = load_level
                    self._high_load_since_time = now
                elif now - self._high_load_since_time >= 45.0:
                    # 持续 45 秒，正式升级
                    self._load_level = load_level
                    self._high_load_since_level = None
                    self._high_load_since_time = 0.0
                    # 等级变化，调整所有线程池
                    self._adjust_adaptive_pool()
                    self._resize_layer_pools_by_level()
                # 未满 30 秒，保持当前等级，不调整线程池
            else:
                # 等级降低：立即生效
                self._load_level = load_level
                self._high_load_since_level = None
                self._high_load_since_time = 0.0
                self._adjust_adaptive_pool()
                self._resize_layer_pools_by_level()
        # 等级不变，清除升高追踪
        elif self._high_load_since_level is not None:
            self._high_load_since_level = None
            self._high_load_since_time = 0.0

        # ★三期：突破期间额外监控——critical 负载下立即强制回收例外突破额度
        if self._adaptive_tuning_enabled and self._load_level == "critical":
            self._release_override_if_critical()
        # ★主线第5批 P1-42：L3 队列深度驱动动态扩缩容（独立于负载等级，按队列压力自愈）
        self._auto_scale_l3_by_depth()
    
    # ========== 统一负载管理 ==========
    
    def update_hardware_snapshot(self, snapshot: dict[str, Any]):
        """
        接收触觉的硬件快照，更新本地负载缓存。
        不再直接修改负载等级，等级由 _check_high_load 统一管理。
        """
        cpu_info = snapshot.get("cpu", {})
        mem_info = snapshot.get("memory", {})
        gpu_info = snapshot.get("gpu", {})
        
        self._cpu_usage = cpu_info.get("usage_percent", 0.0)
        self._mem_usage = mem_info.get("usage_percent", 0.0)
        self._gpu_usage = gpu_info.get("usage_percent", 0.0)
        
        with self._hardware_lock:
            self._hardware_snapshot["cpu_usage"] = self._cpu_usage
            self._hardware_snapshot["mem_usage"] = self._mem_usage
            self._hardware_snapshot["gpu_usage"] = self._gpu_usage
            self._hardware_snapshot["updated_at"] = time.time()
    
    def set_load_level(self, level: str):
        """
        公开方法：外部（如紧急处理）强制设置负载等级。
        立即生效，跳过持续时间保护。
        """
        if level not in ("critical", "heavy", "moderate", "normal", "light"):
            return
        # ★FIX(H2): 兼容旧值 normal → moderate，避免被当作 critical 关闭自适应池
        if level == "normal":
            level = "moderate"
        self._load_level = level
        # ★P0-7修复：外部强制等级 60 秒后过期，之后内部 _check_high_load 接管
        self._external_level_expiry = time.time() + 60.0
        # 重置持续时间追踪
        self._high_load_since_level = None
        self._high_load_since_time = 0.0
        # 强制调整线程池
        self._adjust_adaptive_pool()
        self._resize_layer_pools_by_level()
        _module_logger.info(f"负载等级外部设置为: {level}")
    
    def _is_higher_level(self, new_level: str, old_level: str) -> bool:
        order = {"light": 0, "moderate": 1, "heavy": 2, "critical": 3}
        return order.get(new_level, 0) > order.get(old_level, 0)
    
    def _get_load_level(self, cpu: float, mem: float, gpu: float = 0.0, gpu_mem: float = 0.0) -> str:
        """综合评估硬件负载等级（含GPU）。

        ★三期：阈值动态化 + 滞回。开关开启时，基础阈值按硬件能力微调——
        低配（minimal）机器阈值下调（更早降载保命），高配（high）机器阈值上调（充分用满资源）；
        且升级阈值与降级阈值相差 _load_threshold_hysteresis（5%），避免边界震荡。
        开关关闭时保持原固定阈值（95/98 critical、80/85 heavy、50/65 moderate），零回归。
        """
        # ★三期：动态阈值偏移（开关开启时按 tier 调整）
        _offset = 0.0
        if self._adaptive_tuning_enabled:
            if self._hardware_tier == "minimal":
                _offset = -5.0   # 低配更早降载
            elif self._hardware_tier == "standard":
                _offset = 0.0
            else:  # high
                _offset = 3.0    # 高配更晚降载（充分用满）

        _hyst = self._load_threshold_hysteresis if self._adaptive_tuning_enabled else 0.0

        # ★162批刀1·N1：内存阈值配置化（默认=原硬编码 95/85/65，零回归；改 config.MEMORY_LOAD_* 即改分级边界）
        _mem_crit = getattr(_cfg, "MEMORY_LOAD_CRITICAL_PCT", 95.0) if _cfg is not None else 95.0
        _mem_heavy = getattr(_cfg, "MEMORY_LOAD_HEAVY_PCT", 85.0) if _cfg is not None else 85.0
        _mem_mod = getattr(_cfg, "MEMORY_LOAD_MODERATE_PCT", 65.0) if _cfg is not None else 65.0
        # 熔断红线（永久保留，不随 offset 变化——安全兜底）
        if cpu > 95 or mem > _mem_crit or gpu > 98 or gpu_mem > 98:
            return "critical"
        elif cpu > (80 + _offset) or mem > (_mem_heavy + _offset) or gpu > 90 or gpu_mem > 90:
            return "heavy"
        elif cpu > (50 + _offset + _hyst) or mem > (_mem_mod + _offset + _hyst) or gpu > 70 or gpu_mem > 70:
            return "moderate"
        return "light"
    
    def _resize_layer_pools_by_level(self):
        """根据当前 _load_level 调整四层线程池大小（仅在实际变化时调用）"""
        level = self._load_level
        if level == self._last_resized_level:
            return
        self._last_resized_level = level
        
        if level == "critical":
            self._resize_layer_pool(_PULSE_LAYER_L1, 1)
            self._resize_layer_pool(_PULSE_LAYER_L2, 0)
            self._resize_layer_pool(_PULSE_LAYER_L3, 0)
        elif level == "heavy":
            self._resize_layer_pool(_PULSE_LAYER_L1, 2)
            self._resize_layer_pool(_PULSE_LAYER_L2, 1)
            self._resize_layer_pool(_PULSE_LAYER_L3, 1)
        elif level == "moderate":
            self._resize_layer_pool(_PULSE_LAYER_L1, 4)
            self._resize_layer_pool(_PULSE_LAYER_L2, 2)
            # ★P2-31：moderate 曾是 1 个 worker，L3（后台自主层）生产者不停 →
            #   消费跟不上，队列恒满。适度提到 2，缓解积压（critical 仍为 0，属有意保护）。
            self._resize_layer_pool(_PULSE_LAYER_L3, 2)
        else:  # light
            # ★P3-11修复：light 级别 worker 数参考全局并行调度器的建议并行度（硬件自适应），
            # 使 InfoField 分层池真正接入「统一并行调度」，而非各自硬编码。
            _ps = 8
            try:
                from nucleus.parallel_scheduler import get_parallel_scheduler
                _ps = get_parallel_scheduler().get_parallelism()
            except Exception as _ps_e:
                _module_logger.debug(f"[并行度] 获取自适应并行度异常(已忽略): {_ps_e}")
            self._resize_layer_pool(_PULSE_LAYER_L1, max(4, _ps))
            self._resize_layer_pool(_PULSE_LAYER_L2, max(2, _ps // 2))
            self._resize_layer_pool(_PULSE_LAYER_L3, max(1, _ps // 4))
    
    def _adjust_adaptive_pool(self):
        """根据负载等级动态调整自适应线程池大小。

        ★三期：_compute_level 死值改为「按硬件 tier 动态映射」，仅在开关开启时生效；
        开关关闭时保持原固定映射（high→8/medium→4/basic→2），零回归。
        """
        # ★三期：tier → compute_level 动态映射（开关开启时）
        _actual_compute = self._compute_level
        if self._adaptive_tuning_enabled:
            _tier = self._hardware_tier
            _cores = self._hardware_cores
            # 按 tier 校准 compute 档位：minimal→basic、standard→medium、high→high
            if _tier == "minimal":
                _actual_compute = "basic"
            elif _tier == "standard":
                _actual_compute = "medium"
            else:  # high
                _actual_compute = "high"

        level_max_map = {"high": 8, "medium": 4, "basic": 2}
        actual_max = level_max_map.get(_actual_compute, 4)

        # ★三期：去封顶——high 档位在开关开启时，按硬件核数动态放大上限（无人工封顶 8）
        if self._adaptive_tuning_enabled and _actual_compute == "high" and _cores > 16:  # type: ignore[possibly-unbound]
            # 核数超过 16 时，动态提升：每 4 核 +1，熔断上限 32（保留安全红线）
            actual_max = min(8 + (_cores - 16) // 4, 32)  # type: ignore[possibly-unbound]

        # ★三期：例外突破（高优先级任务临时突破，到期自动回落）
        if self._override_deadline and time.time() < self._override_deadline:
            actual_max = actual_max + self._override_extra_parallelism
        elif self._override_deadline:
            # 突破到期，自动回收
            self._override_deadline = 0.0
            self._override_extra_parallelism = 0
            _module_logger.info("R5 例外突破到期，并行度上限自动回落")

        if self._load_level == "light":
            new_size = actual_max
        elif self._load_level == "moderate":
            new_size = max(2, actual_max // 2)
        elif self._load_level == "heavy":
            new_size = max(2, actual_max // 2)   # 至少2个，保持基本吞吐
        else:  # critical
            new_size = 0
        
        if new_size != self._adaptive_pool_size:
            old_size = self._adaptive_pool_size
            self._adaptive_pool_size = new_size
            if self._adaptive_task_pool:
                try:
                    self._adaptive_task_pool.shutdown(wait=False)
                except Exception as _exc:
                    _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
            if new_size > 0:
                self._adaptive_task_pool = ThreadPoolExecutor(
                    max_workers=new_size,
                    thread_name_prefix="Adaptive"
                )
            else:
                self._adaptive_task_pool = None
            if old_size != new_size:
                _module_logger.info(f"自适应线程池调整: {old_size} → {new_size} (负载={self._load_level}, 硬件tier={self._hardware_tier}, compute={_actual_compute})")

    @staticmethod
    def _read_adaptive_tuning_flag() -> bool:
        """读取 use_runtime_adaptive_tuning 开关（保守兜底 False）。"""
        try:
            from config import FEATURE
            return bool(FEATURE.get("use_runtime_adaptive_tuning", False))
        except Exception as e:
            silent_exc(e, where="nucleus.field.InfoField::_read_adaptive_tuning_flag L1361")
            return False

    @staticmethod
    def _infer_tier(cores: int, memory_gb: float, has_gpu: bool) -> str:
        """★三期：从硬件能力快照反推 tier（与 hardware_probe 评分语义一致）。

        评分：核心数(0-3) + 内存(0-4) + GPU(0-2)，≥7=high、≥4=standard、否则 minimal。
        GPU 是加分项而非必选项。
        """
        score = 0
        if cores >= 16:
            score += 3
        elif cores >= 8:
            score += 2
        elif cores >= 4:
            score += 1

        if memory_gb >= 32:
            score += 4
        elif memory_gb >= 16:
            score += 3
        elif memory_gb >= 8:
            score += 2
        elif memory_gb >= 4:
            score += 1

        if has_gpu:
            score += 2

        if score >= 7:
            return "high"
        elif score >= 4:
            return "standard"
        return "minimal"

    def request_parallelism_override(self, extra_parallelism: int = 4, duration_seconds: float = 300.0) -> bool:
        """★三期 R5 例外突破机制：高优先级任务（全量快照/全量索引重建）临时突破并行度上限。

        仅在开关开启时生效；突破有最大时长（默认 300 秒），到期自动回落；
        突破期间触发额外监控，超过硬件红线（critical 负载）立即强制回收。

        返回 True 表示突破已生效，False 表示拒绝（开关关闭/已在突破中）。
        """
        if not self._adaptive_tuning_enabled:
            return False
        with self._task_count_lock:
            if self._override_deadline and time.time() < self._override_deadline:
                return False  # 已在突破中，避免叠加
            self._override_deadline = time.time() + min(duration_seconds, self._override_max_duration)
            self._override_extra_parallelism = max(1, int(extra_parallelism))
        _module_logger.info(
            f"R5 例外突破生效：并行度上限临时 +{self._override_extra_parallelism}，"
            f"到期时间 {self._override_deadline:.0f}"
        )
        self._adjust_adaptive_pool()
        return True

    def _release_override_if_critical(self):
        """突破期间触发额外监控：critical 负载下立即强制回收突破额度。"""
        if self._override_deadline and self._load_level == "critical":
            self._override_deadline = 0.0
            self._override_extra_parallelism = 0
            _module_logger.warning("R5 例外突破因 critical 负载被强制回收")
    
    # ========== 统一异步任务提交入口 ==========
    
    def submit_adaptive_task(self, task_func: Callable, task_name: str = "",
                             priority: str = "normal", *args, **kwargs) -> bool:
        if self._shutting_down:
            return False
        # ★D1修复（P1，2026-09-05）：负载门控的**静默丢弃**必须留痕。
        #
        # 原实现在此处直接 `return False`，不产生任何日志；
        # 而调用方 PulseCodeLearner._maybe_run_periodic_tests
        # （PulseCodeLearner.py:348）也**不检查返回值**。
        #
        # 实测后果（pulse.log，2026-09-05 10:38-11:31，约 600 次代码学习心跳）：
        #   - 定期测试以 priority="low" 提交（PulseCodeLearner.py:351）
        #   - 心跳计数 400/500/600 均满足 `_heartbeat_count % 100 == 0` 触发条件
        #   - 但全文「定期测试」字样出现 **0 次**，归档目录为空
        #   → 该机制实际从未执行，且因三层静默而无从察觉：
        #       ① 调度器初始化失败 → except 置 None（PulseCodeLearner.py:121-128）
        #       ② 负载门控丢弃     → 此处静默 return False
        #       ③ 任务执行异常     → 吞为 DEBUG（PulseCodeLearner.py:344-345）
        #
        # 此处补 DEBUG 日志让「被丢弃」可见；按任务名做 60 秒节流，
        # 避免高频调用时刷屏。
        def _log_gate_drop(reason: str) -> bool:
            _label = task_name or getattr(task_func, "__name__", "?")
            _now = time.time()
            _at = getattr(self, "_task_gate_log_at", None)
            if _at is None:
                _at = {}
                self._task_gate_log_at = _at
            if _now - _at.get(_label, 0.0) < 60.0:
                return False
            _at[_label] = _now
            _module_logger.debug(
                f"自适应任务被负载门控丢弃(未执行): task={_label}, "
                f"priority={priority}, load_level={self._load_level}, reason={reason}")
            return False

        if self._load_level == "critical":
            if priority not in ("critical",):
                return _log_gate_drop("critical 负载下仅 critical 优先级可执行")
        elif self._load_level == "heavy":
            if priority not in ("critical", "high"):
                return _log_gate_drop("heavy 负载下仅 critical/high 优先级可执行")

        # 移除基于计数的硬性拒绝，依赖线程池自身队列管理。
        # 如果线程池已满，submit 会自然排队，不会丢失任务。

        _pool = self._adaptive_task_pool
        if self._task_pool_ready and _pool is not None:
            with self._task_count_lock:
                self._active_task_count += 1

            _label = task_name or getattr(task_func, "__name__", repr(task_func))

            def _wrapped_task():
                try:
                    task_func(*args, **kwargs)
                except Exception:
                    # ★INFRA-7修复: 不再静默吞掉异常，输出完整堆栈以便排查
                    _module_logger.error(f"submit_adaptive_task 执行失败: {_label}\n"
                                         f"{traceback.format_exc()}")
                finally:
                    with self._task_count_lock:
                        self._active_task_count = max(0, self._active_task_count - 1)

            try:
                _pool.submit(_wrapped_task)
                return True
            except Exception:
                with self._task_count_lock:
                    self._active_task_count = max(0, self._active_task_count - 1)
                try:
                    task_func(*args, **kwargs)
                    return True
                except Exception as _sync_e:
                    _module_logger.error(f"submit_adaptive_task 同步兜底失败: "
                                         f"{_label}: {_sync_e}\n{traceback.format_exc()}")
                    return False

        if self._load_level != "critical":
            try:
                task_func(*args, **kwargs)
                return True
            except Exception as _e:
                _module_logger.error(f"submit_adaptive_task 同步执行失败: "
                                     f"{task_name}: {_e}\n{traceback.format_exc()}")
                return False
        return False
    
    def get_load_level(self) -> str:
        return self._load_level
    
    def is_high_load(self) -> bool:
        return self._load_level in ("heavy", "critical")
    
    def get_adaptive_tuning_state(self) -> dict[str, Any]:
        """★三期观测接口：暴露运行时自适应智能化状态（供日志/健康面板消费）。"""
        return {
            "enabled": self._adaptive_tuning_enabled,
            "hardware_tier": self._hardware_tier,
            "hardware_cores": self._hardware_cores,  # type: ignore[possibly-unbound]
            "hardware_memory_gb": self._hardware_memory_gb,
            "compute_level": self._compute_level,
            "load_threshold_hysteresis": self._load_threshold_hysteresis,
            "override_active": bool(self._override_deadline and time.time() < self._override_deadline),
            "override_extra_parallelism": self._override_extra_parallelism,
        }
    
    # ========== ★P0-4：分层队列有界背压（稳态规则7「风暴队列上限」落地） ==========

    @staticmethod
    def _as_int(value, default: int) -> int:
        """配置值安全转 int——坏值退回默认，绝不抛异常（配置不该有能力搞挂框架）。"""
        try:
            if value is None:
                return default
            _v = int(value)
            return _v if _v >= 0 else default
        except Exception:
            return default

    # ==================================================================
    # ★主线第16批 T5/P2-100：L3 队列背压优化（生产端过滤 + 告警升级 + 统计）
    #   注：任务书要求的「提前扩容」与「水位告警」**已存在**（70/85/95 分档扩容 +
    #   80% 水位 WARNING），本批不重复实现，只补真实缺口。
    # ==================================================================
    #: 事件类型白名单：纯状态汇报类，L3 层可只计数不投递（opt-in，需同时带标记）
    _L3_LOW_VALUE_EVENT_HINTS = (
        "status_report", "heartbeat_report", "debug_report", "metric_report",
        "status_response", "self_report",
    )

    def _l3_optimize_config(self) -> tuple[bool, float, int]:
        """读取 L3 背压优化配置：(开关, 去重窗口秒, 连续背压告警阈值)。"""
        enabled, window, warn_every = True, 30.0, 3
        try:
            import config as _cfg
            enabled = bool(getattr(_cfg, "ENABLE_L3_BACKPRESSURE_OPTIMIZE", True))
            window = float(getattr(_cfg, "L3_DEDUP_WINDOW_SEC", 30) or 30.0)
            warn_every = int(getattr(_cfg, "L3_BACKPRESSURE_WARN_EVERY", 3) or 3)
        except Exception as _e:
            _module_logger.debug(
                      f"L3 背压优化配置读取失败，使用默认值: {type(_e).__name__}: {_e}")
        return enabled, max(0.0, window), max(1, warn_every)

    def _should_filter_l3_pulse(self, pulse, organ_name: str) -> bool:
        """L3 入队前过滤：命中则**不占队列**，只累计计数。

        Returns: True=应过滤（不投递）；False=正常放行。
        """
        _enabled, _window, _ = self._l3_optimize_config()
        if not _enabled:
            return False
        try:
            _evt = ""
            _payload = pulse
            if isinstance(pulse, dict):
                _evt = str(pulse.get("event", "") or pulse.get("type", "") or "")
                _payload = pulse.get("payload", pulse)
            # (a) 显式低价值标记（opt-in，由生产方主动声明，不误伤业务消息）
            _is_low_value = False
            _etype = ""
            if isinstance(_payload, dict):
                _is_low_value = bool(_payload.get("diagnostic") or _payload.get("debug_only"))
                _etype = str(_payload.get("event_type", "") or "")
            if _is_low_value or any(h in _etype.lower() for h in self._L3_LOW_VALUE_EVENT_HINTS):
                self._l3_filter_stats["low_value"] += 1
                return True
            # (b) 幂等去重：同 (器官, 事件, 载荷指纹) 在窗口内重复 → 丢弃
            if _window > 0:
                import hashlib as _hl
                _sig = _hl.md5(
                    f"{organ_name}|{_evt}|{repr(_payload)[:400]}".encode(
                        "utf-8", errors="replace")).hexdigest()
                _now = time.time()
                _seen = self._l3_dedup_seen
                _last = _seen.get(_sig, 0.0)
                if _now - _last < _window:
                    self._l3_filter_stats["dedup"] += 1
                    return True
                _seen[_sig] = _now
                if len(_seen) > 2000:      # 防止无界增长
                    _cut = _now - _window
                    for _k in [k for k, v in _seen.items() if v < _cut]:
                        _seen.pop(_k, None)
            return False
        except Exception as _e:
            # 过滤逻辑异常绝不能影响投递 → 保守放行
            _module_logger.debug(
                      f"L3 生产端过滤异常（已放行）: {type(_e).__name__}: {_e}")
            return False

    def note_l3_backpressure(self, layer: str = "L3") -> None:
        """记录一次 L3 背压；连续达到阈值时升级为 WARNING。"""
        try:
            self._l3_backpressure_streak += 1
            self._l3_filter_stats["backpressure"] += 1
            _st = self._l3_filter_stats
            if self._l3_backpressure_streak > _st.get("backpressure_streak_peak", 0):
                _st["backpressure_streak_peak"] = self._l3_backpressure_streak
            _, _, _warn_every = self._l3_optimize_config()
            if self._l3_backpressure_streak % _warn_every == 0:
                _module_logger.warning(
                          f"[背压·升级] {layer} 层连续 {self._l3_backpressure_streak} 次触发背压，"
                          f"累计背压 {_st['backpressure']} 次；"
                          f"生产端已过滤 低价值{_st['low_value']} / 重复{_st['dedup']} 条")
        except Exception as _e:
            _module_logger.debug(
                      f"背压计数异常（已忽略）: {type(_e).__name__}: {_e}")

    def note_l3_admitted(self) -> None:
        """L3 正常放行时复位连续背压计数（仅统计语义，不影响准入判定）。"""
        try:
            self._l3_backpressure_streak = 0
        except Exception as _e:
            _module_logger.debug(
                      f"背压计数复位异常（已忽略）: {type(_e).__name__}: {_e}")

    def get_l3_backpressure_stats(self) -> dict[str, Any]:
        """L3 背压与生产端过滤统计（供自省/诊断消费）。"""
        _enabled, _window, _warn_every = self._l3_optimize_config()
        _st = getattr(self, "_l3_filter_stats", {}) or {}
        return {
            "optimize_enabled": _enabled,
            "dedup_window_sec": _window,
            "warn_every": _warn_every,
            "filtered_low_value": _st.get("low_value", 0),
            "filtered_dedup": _st.get("dedup", 0),
            "filtered_total": _st.get("low_value", 0) + _st.get("dedup", 0),
            "backpressure_count": _st.get("backpressure", 0),
            "backpressure_streak": getattr(self, "_l3_backpressure_streak", 0),
            "backpressure_streak_peak": _st.get("backpressure_streak_peak", 0),
            "l3_rejected": self._layer_rejected_count.get(_PULSE_LAYER_L3, 0),
        }

    def _admit_layer_task(self, layer: str, pool, organ_name: str = "",
                          priority: int = 5) -> bool:
        """分层队列准入判定（有界背压）。

        返回 True 允许提交，False 表示队列已满、本次分发应被拒绝。

        设计取舍 —— 为什么不用「阻塞式有界队列」：
            ThreadPoolExecutor 换成 queue.Queue(maxsize=N) 后，队列满时
            `pool.submit()` 会**阻塞 publish 线程**。而 publish 正运行在
            脉冲分发主链路上，一旦被 L2/L3 的积压堵住，连 L0 生命线脉冲
            都发不出去 —— 保护机制反而制造了死锁。
            因此这里采用**准入拒绝 + 可观测告警**：宁可丢弃后台层脉冲，
            也绝不阻塞主链路。这与宪法规则 7「过载降频、异常隔离」一致。

        策略：
            L0：limit=0 → 永不限流（生命线，心跳/告警不可丢）
            L1：软上限 → 超限告警但放行；达 2 倍软上限才拒绝（限流不丢包）
            L2/L3：达到上限即拒绝

        ★主线第35批 T5：补齐参数/返回/示例三段（原 docstring 只有设计说明）。

        参数:
            layer: 分层标识（``"L0"`` / ``"L1"`` / ``"L2"`` / ``"L3"``），
                用于查 :attr:`_layer_queue_limits` 中的上限；未知层取 0（= 不限流）。
            pool: 目标线程池（``ThreadPoolExecutor``）；``None`` 时直接放行。
            organ_name: 提交方器官名，**仅用于告警文案**（可空串）。
            priority: 脉冲优先级，**不参与准入判定**（保留形参以兼容调用约定）。

        返回:
            True  = 允许提交；
            False = 拒绝本次分发（已达上限；L1 需达到 **2 倍**软上限才拒绝）。

        示例:
            # L3 上限 300、当前深度 299 → 放行
            info_field._admit_layer_task("L3", pool, "PulseCortex")  ->  True
            # L3 深度已达 300 → 拒绝（不阻塞主链路，仅丢弃本条脉冲）
            info_field._admit_layer_task("L3", pool, "PulseCortex")  ->  False
            # L0 生命线永不限流
            info_field._admit_layer_task("L0", pool)                 ->  True

        """
        _limit = self._layer_queue_limits.get(layer, 0)
        if _limit <= 0:
            return True                      # 0 = 无上限（L0 生命线）
        if pool is None:
            return True
        try:
            _depth = pool._work_queue.qsize()   # SimpleQueue/Queue 均支持 qsize
        except Exception as e:
            silent_exc(e, where="nucleus.field.InfoField::_admit_layer_task L1710")
            return True                      # 拿不到深度就放行，不做无谓拦截
        # ★P2-31 水位预警：达上限 80% 即告警，不必等队列满（提前发现积压趋势）
        _warn_ratio = float(PULSE_LAYER.get("l2_warn_ratio", 0.8)) if layer == _PULSE_LAYER_L2 else 0.8
        if _depth >= _limit * _warn_ratio and _depth < _limit:
            if _depth % max(1, _limit // 10) == 0:
                _module_logger.warning(
                    f"[背压·水位] {layer}层队列深度 {_depth}/{_limit} "
                    f"({_depth * 100 // _limit}%)，接近上限"
                    + (f" 器官={organ_name}" if organ_name else ""))

        if _depth < _limit:
            return True

        # —— 超限 ——
        _soft = (layer == _PULSE_LAYER_L1)
        if _soft and _depth < _limit * 2:
            # L1 软上限区间：限流但不丢包，仅告警（节流告警，避免刷屏）
            if _depth % max(1, _limit // 10) == 0:
                _module_logger.warning(
                    f"[背压] {layer}层队列已达软上限({_depth}/{_limit})，"
                    f"限流告警（仍放行，达{_limit * 2}才拒绝）")
            return True

        # ★P2-31 降级放行：队列满时不再一刀切拒绝。
        #   高优先级脉冲（priority 数值越小越优先）仍放行，仅低优先级被丢弃，
        #   避免后台自主层的关键脉冲长期饥饿。
        if priority <= self._overflow_priority_allow:
            self._layer_overflow_allowed[layer] = self._layer_overflow_allowed.get(layer, 0) + 1
            _allowed = self._layer_overflow_allowed[layer]
            if _allowed == 1 or _allowed % 100 == 0:
                _module_logger.warning(
                    f"[背压·超限放行] {layer}层队列已满，但脉冲优先级={priority} "
                    f"<={self._overflow_priority_allow}，放行（累计{_allowed}次）"
                    + (f" 器官={organ_name}" if organ_name else ""))
            return True

        # —— 拒绝 ——
        self._layer_rejected_count[layer] = self._layer_rejected_count.get(layer, 0) + 1
        _rejected = self._layer_rejected_count[layer]
        # 节流：每 100 次打一条，避免风暴时日志本身成为负担
        if _rejected == 1 or _rejected % 100 == 0:
            _module_logger.warning(
                f"[背压] {layer}层队列已满(深度={_depth}, 上限={_limit})，"
                f"拒绝本次分发以保护内存（累计拒绝={_rejected}）"
                f"{'' if not organ_name else f'，器官={organ_name}'}。"
                f"提示：若频繁出现，请调高 config.PULSE_LAYER 对应层上限，"
                f"或排查该层处理器是否阻塞")
        return False

    def reload_l2_config(self) -> dict:
        """★主线第57批 T4（P2-394）：运行时热重载 L2 队列上限与 worker 数（无需重启框架）。

        从 config.PULSE_LAYER 重新读取 l2_queue_limit / l2_threads / l2_warn_ratio，
        更新内存中的队列上限与线程池 max_workers。
        """
        _cfg = resolve_l2_config(os.cpu_count() or 8, PULSE_LAYER)
        self._layer_queue_limits[_PULSE_LAYER_L2] = _cfg["queue_limit"]
        _pool = self._layer_pools.get(_PULSE_LAYER_L2)
        if _pool is not None:
            try:
                _pool._max_workers = _cfg["workers"]
            except Exception as _e:
                # ★主线第62批 T2/P1：核心门禁修复——静默 except 改为日志记录
                #   旧行为：except Exception: pass（违反核心7文件门禁，
                #           test_robustness_m7::test_core_files_no_silent_except_pass 失败）
                #   新行为：记录 warning（配置设置失败不影响主流程，故非 error）
                _module_logger.warning(
                    f"[InfoField] L2线程池max_workers设置失败: "
                    f"type={type(_e).__name__} err={_e} workers={_cfg.get('workers')}"
                )
        return _cfg

    def get_backpressure_stats(self) -> dict:
        """★P0-4：背压可观测端点数据（供 health_ui / 埋点消费）。"""
        _out = {}
        for _layer in (_PULSE_LAYER_L0, _PULSE_LAYER_L1, _PULSE_LAYER_L2, _PULSE_LAYER_L3):
            _pool = self._layer_pools.get(_layer)
            _depth = -1
            if _pool is not None:
                try:
                    _depth = _pool._work_queue.qsize()
                except Exception:
                    _depth = -1
            _out[_layer] = {
                "queue_depth": _depth,
                "limit": self._layer_queue_limits.get(_layer, 0),
                "rejected": self._layer_rejected_count.get(_layer, 0),
            }
        return _out

    # ========== ★主线第5批 P1-42：L3 队列深度驱动动态扩缩容 + 可观测性 ==========

    @staticmethod
    def _m33_queue_limit_order_fix_on() -> bool:
        """★主线第33批 T4（P3）：分层队列上限初始化顺序修复是否生效（默认 True）。

        返回:
            True  = 默认值在 _init_layer_pools() 之前赋值 → config 的 L3 上限生效；
            False = 复现修复前行为（硬编码默认值覆盖 config 值）。

        示例:
            InfoField._m33_queue_limit_order_fix_on()  ->  True
            （关闭 config.ENABLE_L3_QUEUE_LIMIT_ORDER_FIX 后返回 False）
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_L3_QUEUE_LIMIT_ORDER_FIX", True))
        except Exception as e:
            silent_exc(e, where="nucleus.field.InfoField::_m33_queue_limit_order_fix_on L1818")
            return True

    @staticmethod
    def _read_l3_dynamic_scaling_flag() -> bool:
        """读取 l3_dynamic_scaling_enabled 开关（灰度：默认 True=修复生效，False=退回固定 2 worker）。"""
        try:
            from config import FEATURE
            return bool(FEATURE.get("l3_dynamic_scaling_enabled", True))
        except Exception as e:
            silent_exc(e, where="nucleus.field.InfoField::_read_l3_dynamic_scaling_flag L1827")
            return True

    def _auto_scale_l3_by_depth(self):
        """★主线第5/6批 P1-42 L3 队列深度驱动动态扩缩容（细化版）。

        根因：原 L3 worker 只随机器负载等级变化，与队列深度脱节。
        第6批优化：扩容冷却缩短至5s、缩容冷却拓30s；提前阈值
        0.70/0.85/0.95→+1/+2/+3；突发检测（10s 内深度增长>30% 直扩上限）。

        ★主线第34批 T5：补齐参数/返回/示例（方法体本身未改动）。

        参数:
            无（仅 ``self``）。判定所需状态全部取自实例：
            ``_l3_dynamic_enabled`` / ``_layer_pools[L3]`` /
            ``_layer_queue_limits[L3]`` / ``_l3_*`` 系列阈值与冷却字段。

        返回:
            None。本方法是**副作用式**的 —— 命中扩缩条件时调用
            ``_resize_layer_pool(L3, target)`` 原地调整线程池大小，并写 INFO 日志
            ``[L3动态扩缩] 原因=… 队列=深度/上限(百分比) → worker 旧→新``。
            以下任一情况直接返回（不产生副作用）：
              · 动态扩缩关闭 / L3 池缺失 / 取不到队列深度 / 上限 ≤ 0；
              · 未命中任何扩缩条件；或仍在冷却期内（突发扩容不受冷却限制）。

        示例:
            >>> iw._layer_queue_limits[_PULSE_LAYER_L3] = 300      # doctest: +SKIP
            >>> iw._auto_scale_l3_by_depth()                       # doctest: +SKIP
            # 深度 50/300（burst，10s 内增长 > 30%）→ 日志：
            # [L3动态扩缩] 原因=burst 队列=50/300(17%) → worker 3→5
        """
        if not self._l3_dynamic_enabled:
            return
        _pool = self._layer_pools.get(_PULSE_LAYER_L3)
        if _pool is None:
            return
        try:
            _depth = _pool._work_queue.qsize()
        except Exception as e:
            silent_exc(e, where="nucleus.field.InfoField::_auto_scale_l3_by_depth L1865")
            return
        _limit = self._layer_queue_limits.get(_PULSE_LAYER_L3, 0)
        if _limit <= 0:
            return
        _ratio = _depth / _limit
        _now = time.time()

        # 健壮的冷却读取（兼容第5批单冷却字段）
        _up_cd = getattr(self, "_l3_scale_up_cooldown", None)
        if _up_cd is None:
            _up_cd = getattr(self, "_l3_scale_cooldown", 5.0)
        _down_cd = getattr(self, "_l3_scale_down_cooldown", None)
        if _down_cd is None:
            _down_cd = getattr(self, "_l3_scale_cooldown", 30.0)
        _up_thresholds = getattr(self, "_l3_scale_up_thresholds", [0.70, 0.85, 0.95])
        _lowest_up = _up_thresholds[0] if _up_thresholds else 0.70

        # 需求标记（响应时间指标）：深度首次跨过最低扩容阈值
        if _ratio >= _lowest_up and getattr(self, "_l3_demand_start_ts", 0.0) == 0.0:
            self._l3_demand_start_ts = _now
        elif _ratio < _lowest_up:
            self._l3_demand_start_ts = 0.0
        # ★主线第7批 P1-67：队列深度低于缩容阈值的持续计时（延迟缩容防震荡）
        if _ratio <= 0.50:
            if getattr(self, "_l3_below_half_since", 0.0) == 0.0:
                self._l3_below_half_since = _now
        else:
            self._l3_below_half_since = 0.0

        # 每分钟采样深度时序 + 写入 PHASE18 信号层
        if _now - getattr(self, "_l3_depth_sample_ts", 0.0) >= 60.0:
            self._l3_depth_sample_ts = _now
            self._l3_depth_history.append((_now, _depth, _limit))
            if len(self._l3_depth_history) > 120:
                self._l3_depth_history = self._l3_depth_history[-120:]
            self._record_phase18_l3(_depth, _limit, _pool._max_workers)

        # 突发窗口维护（10s）
        _burst_win = getattr(self, "_l3_burst_window", [])
        _burst_win.append((_now, _depth))
        _burst_win[:] = [(t, d) for (t, d) in _burst_win if _now - t <= 10.0]
        self._l3_burst_window = _burst_win

        # 突发检测（紧急，绕过扩容冷却）
        _burst_triggered = False
        _burst_enabled = getattr(self, "_l3_burst_detection_enabled", True)
        if _burst_enabled and _depth > 0 and _burst_win:
            _min_depth = min(d for (_, d) in _burst_win)
            if _min_depth > 0:
                _growth = (_depth - _min_depth) / _min_depth
                if _growth > getattr(self, "_l3_burst_growth_threshold", 0.30):
                    _burst_triggered = True

        _cur = _pool._max_workers
        _base = self._l3_base_workers
        _action = None
        _target = _cur
        _reason = ""
        _cd = _up_cd

        if _burst_triggered and _cur < self._l3_max_dynamic_workers:
            _action = "up"
            # ★主线第75批 T1：平滑突发，单次只 +step（默认1），且同样受统一冷却约束
            _target = min(_cur + getattr(self, "_l3_scale_step", 1), self._l3_max_dynamic_workers)
            _reason = "burst"
            _cd = _up_cd  # 突发也受冷却（≥60s），避免刚扩完又触发
        else:
            # 阈值驱动：找出最高跨越的阈值→ +1/+2/+3
            _delta = 0
            for _i, _th in enumerate(_up_thresholds):
                if _ratio >= _th:
                    _delta = _i + 1
                else:
                    break
            if _delta > 0:
                _action = "up"
                # ★主线第75批 T1：平滑——单次最多调整 scale_step（默认1）个 worker
                _target = min(_cur + min(_delta, getattr(self, "_l3_scale_step", 1)),
                              self._l3_max_dynamic_workers)
                _reason = "threshold"
                _cd = _up_cd
            elif getattr(self, "_l3_hysteresis_enabled", True) and _ratio <= getattr(self, "_l3_hysteresis_down_ratio", 0.50) \
                    and _cur > self._l3_min_workers and \
                    getattr(self, "_l3_below_half_since", 0.0) > 0.0 and \
                    (_now - self._l3_below_half_since) >= self._l3_scale_down_buffer_sec:
                _action = "down"
                _target = max(_cur - 1, self._l3_min_workers)
                _reason = "threshold"
                _cd = _down_cd

        if _action is None:
            return
        if _now - getattr(self, "_l3_last_scale_ts", 0.0) < _cd:
            return

        if _target != _cur and _target >= 1:
            self._l3_last_scale_ts = _now
            if _action == "up":
                self._l3_scale_up_count = getattr(self, "_l3_scale_up_count", 0) + 1
                _ds = getattr(self, "_l3_demand_start_ts", 0.0)
                if _ds > 0:
                    _rts = getattr(self, "_l3_response_times", [])
                    _rts.append(_now - _ds)
                    self._l3_response_times = _rts
                self._l3_demand_start_ts = 0.0
            else:
                self._l3_scale_down_count = getattr(self, "_l3_scale_down_count", 0) + 1
            self._l3_last_scale_reason = _reason
            _module_logger.info(
                f"[L3动态扩缩] 原因={_reason} "
                f"队列={_depth}/{_limit}({_ratio*100:.0f}%) "
                f"→ worker {_cur}→{_target}")
            self._resize_layer_pool(_PULSE_LAYER_L3, _target)

    def get_l3_scaling_stats(self) -> dict:
        """★主线第6批 P1-42续 可观测：扩缩容次数/
        拒绝次数/平均响应时间/触发原因。"""
        _rts = getattr(self, "_l3_response_times", [])
        _avg = (sum(_rts) / len(_rts)) if _rts else 0.0
        return {
            "scale_up_count": getattr(self, "_l3_scale_up_count", 0),
            "scale_down_count": getattr(self, "_l3_scale_down_count", 0),
            "rejected": self._layer_rejected_count.get(_PULSE_LAYER_L3, 0),
            "avg_response_time_sec": round(_avg, 3),
            "last_scale_reason": getattr(self, "_l3_last_scale_reason", ""),
            "up_cooldown": getattr(self, "_l3_scale_up_cooldown", 5.0),
            "down_cooldown": getattr(self, "_l3_scale_down_cooldown", 30.0),
            "burst_enabled": getattr(self, "_l3_burst_detection_enabled", True),
            "min_workers": getattr(self, "_l3_min_workers", 3),
            "scale_down_buffer_sec": getattr(self, "_l3_scale_down_buffer_sec", 30.0),
        }

    def _record_l3_completion(self):
        """L3 处理完成时间戳入滑窗（供消费速率计算）。"""
        _now = time.time()
        self._l3_completion_window.append(_now)
        _cut = _now - 60.0
        while self._l3_completion_window and self._l3_completion_window[0] < _cut:
            self._l3_completion_window.pop(0)

    def _record_phase18_l3(self, depth: int, limit: int, workers: int):
        """★预埋 PHASE18：L3 背压快照写入信号层（关闭时 no-op）。"""
        try:
            from config import ENABLE_PHASE18_SIGNALS
            if not ENABLE_PHASE18_SIGNALS:
                return
            from nucleus.telemetry.phase18_signals import get_phase18_signals
            _top5 = sorted(self._l3_producer_counts.items(),
                           key=lambda kv: kv[1], reverse=True)[:5]
            _rate = len(self._l3_completion_window) / 60.0
            get_phase18_signals().record_l3_backpressure(
                depth=depth, limit=limit, workers=workers,
                producer_top5=_top5, consumer_rate_per_sec=_rate)
        except Exception as _exc:
            _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
    def get_l3_telemetry(self) -> dict:
        """★主线第5批 P1-42 可观测端点：L3 队列深度时序 / 生产者 TOP5 / 消费速率。"""
        _pool = self._layer_pools.get(_PULSE_LAYER_L3)
        _depth = -1
        _workers = -1
        if _pool is not None:
            try:
                _depth = _pool._work_queue.qsize()
                _workers = _pool._max_workers
            except Exception as _exc:
                _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
        _top5 = sorted(self._l3_producer_counts.items(),
                       key=lambda kv: kv[1], reverse=True)[:5]
        _rate = len(self._l3_completion_window) / 60.0
        return {
            "current_depth": _depth,
            "limit": self._layer_queue_limits.get(_PULSE_LAYER_L3, 0),
            "current_workers": _workers,
            "depth_history": [
                {"ts": int(t), "depth": d, "limit": l,
                 "ratio": round(d / l, 3) if l else 0}
                for (t, d, l) in self._l3_depth_history[-30:]
            ],
            "producer_top5": [{"organ": k, "count": v} for (k, v) in _top5],
            "consumer_rate_per_sec": round(_rate, 3),
            "rejected": self._layer_rejected_count.get(_PULSE_LAYER_L3, 0),
            "dynamic_enabled": self._l3_dynamic_enabled,
        }

    def _concurrency_recovery_enabled(self) -> bool:
        """★P2-87 开关（config.FEATURE['enable_concurrency_count_recovery']，默认 True）。

        关闭时退回「池重建即 clear 全局计数」的旧行为（零回归）。
        """
        try:
            from config import FEATURE as _FEATURE
            return bool(_FEATURE.get("enable_concurrency_count_recovery", True))
        except Exception as e:
            silent_exc(e, where="nucleus.field.InfoField::_concurrency_recovery_enabled L2058")
            return True

    def _track_layer_inflight(self, layer_tag: str, organ_name: str, delta: int):
        """★P2-87：按层·按器官维护在途任务计数（+1 提交 / -1 完成）。"""
        with self._organ_concurrent_lock:
            _d = self._layer_organ_inflight.get(layer_tag)
            if _d is None:
                _d = {}
                self._layer_organ_inflight[layer_tag] = _d
            _v = _d.get(organ_name, 0) + delta
            if _v <= 0:
                _d.pop(organ_name, None)
            else:
                _d[organ_name] = _v

    def _check_concurrency_consistency(self, layer_tag: str | None = None) -> dict:
        """★P2-87：并发计数一致性校验。

        对比 `_organ_concurrent_count`（全局器官计数）与 `_layer_organ_inflight`
        （按层在途计数）的总和。理论上二者应完全一致；出现偏差说明存在
        「自增后未配对递减」的新泄漏路径。

        Returns:
            {"consistent": bool, "organ_count_total": int, "inflight_total": int,
             "diff": int, "detail": {...}}
        """
        with self._organ_concurrent_lock:
            _organ_total = sum(v for v in self._organ_concurrent_count.values() if v > 0)
            if layer_tag is not None:
                _inflight_total = sum(
                    v for v in self._layer_organ_inflight.get(layer_tag, {}).values() if v > 0)
            else:
                _inflight_total = sum(
                    v for _ld in self._layer_organ_inflight.values()
                    for v in _ld.values() if v > 0)
            _diff = _organ_total - _inflight_total
            return {
                "consistent": _diff == 0,
                "organ_count_total": _organ_total,
                "inflight_total": _inflight_total,
                "diff": _diff,
                "detail": {k: v for k, v in self._organ_concurrent_count.items() if v > 0},
            }

    def _resize_layer_pool(self, layer: str, target_workers: int, force: bool = False):
        """动态调整指定层级的线程池大小（线程安全，永不摘除池）"""
        if layer not in self._layer_pools:
            return
        
        pool = self._layer_pools[layer]
        current = pool._max_workers
        
        if not force and current == target_workers:
            return
        
        # 当目标 worker 数为 0 或负时，保持池存在但缩小到 1 worker。
        # ★P2-8修复：不再标记 _layer_disabled 强制走同步执行——同步兜底会阻塞
        # publish 线程、且不经过 _dispatch_handler（导致 _total_handled 停增、
        # 假死探测器误报「疑似冻结」、框架整体降速）。改为保留 1 个 worker 走异步，
        # 既降载又不阻塞主链路。
        if target_workers <= 0:
            target_workers = 1
        # 清理可能残留的禁用标记（历史版本遗留），确保异步路径始终可用
        if hasattr(self, '_layer_disabled') and layer in self._layer_disabled:
            self._layer_disabled[layer] = False
        
        new_pool = ThreadPoolExecutor(
            max_workers=target_workers,
            thread_name_prefix=f"{layer}-Adaptive"
        )
        old_pool = self._layer_pools[layer]
        self._layer_pools[layer] = new_pool
        
        # ★P2-9修复：原实现 spawn 一个 daemon 线程执行 old_pool.shutdown(wait=True)，
        # 若旧池 worker 卡死（如锁死锁），wait=True 永不返回，每次 resize 都泄漏
        # 一个线程 + 卡死 worker，累积成数百线程。改为 wait=False + cancel_futures，
        # 立即返回、取消排队任务，不阻塞不泄漏（卡死线程仍无法被 Python 强制终止，
        # 但不再新增 shutdown 线程）。
        try:
            old_pool.shutdown(wait=False, cancel_futures=True)
            # ★S5修复：cancel_futures=True 会取消旧池中排队尚未执行的future，
            # 被取消的future不会运行_dispatch_handler，其finally里的
            # _decrement_organ_concurrency也就永远不会执行 → 并发计数只增不减。
            #
            # ★P2-87（第十三批）：精确递减替代暴力 clear。
            #   开关开启时：用「被取消的在途任务数」逐器官精确递减
            #   （_layer_organ_inflight 记录该层每个器官的在途数，池重建即全部作废）；
            #   开关关闭时：退回原 clear() 全局归零行为（零回归）。
            _recover = self._concurrency_recovery_enabled()
            with self._organ_concurrent_lock:
                _leaked = {k: v for k, v in self._organ_concurrent_count.items() if v > 0}
                if _leaked:
                    if _recover:
                        # 精确递减：只处理「本层」作废的在途器官，绝不牵连其他层。
                        #   关键：不能全局 clear——其他层（L1/L2）仍有真实在途任务，
                        #   全局归零会把它们的合法计数一并抹掉，造成新的计数偏差。
                        _layer_pending = dict(self._layer_organ_inflight.get(layer, {}))
                        # ★主线第78批 T1/P0：池重建取消旧池排队任务（cancel_futures=True），
                        # 被取消任务永不进入 _dispatch_handler，其 finally 中的 -1（:898）不执行
                        # → _inflight_dispatch_count 永久泄漏、队列深度指标只增不减。
                        # 本层在途任务数即被取消任务数，按此精确递减（与 ±1 配对，零回归）。
                        _cancelled = sum(_layer_pending.values())
                        if _cancelled > 0:
                            with self._task_count_lock:
                                self._inflight_dispatch_count = max(
                                    0, self._inflight_dispatch_count - _cancelled)
                        for _org, _n in _layer_pending.items():
                            _cur = self._organ_concurrent_count.get(_org, 0)
                            if _cur > 0:
                                self._organ_concurrent_count[_org] = max(0, _cur - _n)
                        # 本层作废后，逐器官检查残留（仅限本层出现过的器官），
                        #   残留说明存在「本层自增后未配对」的路径 → 归零并告警。
                        _still = {k: self._organ_concurrent_count[k]
                                  for k in _layer_pending
                                  if self._organ_concurrent_count.get(k, 0) > 0}
                        self._layer_organ_inflight.pop(layer, None)
                        for _k in _still:
                            self._organ_concurrent_count.pop(_k, None)
                        if _still:
                            # ★第22批 T5/P2-123：残留计数已被自动归零（异常路径自愈），
                            #   属正常恢复行为、不需要每次告警 → DEBUG；关开关时回 WARNING。
                            (_module_logger.debug if _noise_reduce()
                             else _module_logger.warning)(
                                f"[P2-87]池重建层内残留已归零(异常路径): {_still}")
                    else:
                        self._organ_concurrent_count.clear()
                        # ★P0-2修复（第十二批）：原为 self._log("WARNING", ...)，
                        #   而 InfoField 从未定义 _log → AttributeError。
                        #   该异常被下方 except 捕获后，except 内再次 self._log 二次抛出，
                        #   最终逃逸出本方法杀死 L0 看门狗线程（P0-2 事故链的根因）。
                        #   改用模块级 logger——InfoField 内部统一用 _module_logger。
                        # ★第22批 T5/P2-123：cancel_futures 导致无法递减 → 计数已重置，
                        #   归零后系统自愈，属正常兜底路径 → DEBUG；关开关时回 WARNING。
                        (_module_logger.debug if _noise_reduce()
                         else _module_logger.warning)(
                            f"[S5]池重建重置器官并发计数(cancel_futures导致无法递减): {_leaked}")
        except Exception as _rsz_e:
            # ★P0-2修复：原为 self._log（幽灵方法，二次抛出）。改为模块 logger，
            #   且此处必须「吞掉」——池重建失败不能打断调用方（L0 看门狗）。
            _module_logger.debug(f"[池重建] 旧池shutdown异常(已忽略): {_rsz_e}")
    # ========== v9.5: 器官并发控制 ==========
    
    def _check_organ_concurrency(self, organ_name: str) -> bool:
        max_concurrent = self._organ_max_concurrent
        if max_concurrent <= 0:
            return True
        with self._organ_concurrent_lock:
            current = self._organ_concurrent_count.get(organ_name, 0)
            return current < max_concurrent
    
    def _increment_organ_concurrency(self, organ_name: str):
        with self._organ_concurrent_lock:
            self._organ_concurrent_count[organ_name] = self._organ_concurrent_count.get(organ_name, 0) + 1
    
    def _decrement_organ_concurrency(self, organ_name: str):
        with self._organ_concurrent_lock:
            current = self._organ_concurrent_count.get(organ_name, 0)
            if current > 0:
                self._organ_concurrent_count[organ_name] = current - 1
    
    # ========== 共振条件注册 ==========
    
    def _dup_trace_check(self, pulse: dict[str, Any], event_type: str) -> None:
        """★主线第25批 T1/P2-160：重复事件遥测（诊断用，默认关闭）。

        对「同一 event_type + 同一 correlation_id」在窗口内的重复发布记 WARNING，
        并附**两条调用栈**，用于定位「一次用户输入触发两次下游调用」的源头。
        ★关闭时首行返回：不取锁、不建栈、不写内存 → 与改造前完全一致。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, "DEBUG_DUP_EVENT_TRACE", False):
                return
            _watch = getattr(_cfg, "DUPLICATE_EVENT_WATCH", None) or []
            if _watch and not any(_w in event_type for _w in _watch):
                return
            _window = float(getattr(_cfg, "DUPLICATE_EVENT_WINDOW_SEC", 5.0) or 5.0)
            _depth = int(getattr(_cfg, "DUPLICATE_EVENT_STACK_DEPTH", 12) or 12)
        except Exception as e:
            silent_exc(e, where="nucleus.field.InfoField::_dup_trace_check L2244")
            return
        try:
            _payload = pulse.get("payload", {}) or {}
            _cid = str(_payload.get("correlation_id", "") or "")
            _frag = str(_payload.get("question", _payload.get("prompt", "")) or "")[:60]
            _key = f"{event_type}|{_cid}" if _cid else f"{event_type}|{_frag}"
            _now = time.time()
            _stack = "".join(traceback.format_stack(limit=_depth))
            _pid = pulse.get("pulse_id", "?")
            _src = pulse.get("source_organ", "?")
            with self._dup_trace_lock:
                self._dup_trace_seen = {
                    _k: _v for _k, _v in self._dup_trace_seen.items()
                    if _now - _v[0] < _window
                }
                _prev = self._dup_trace_seen.get(_key)
                self._dup_trace_seen[_key] = (_now, _pid, _src, _stack)
                self._dup_trace_count += 1
                _seq = self._dup_trace_count
            if _prev is not None:
                _module_logger.warning(
                    f"[重复事件遥测#{_seq}] event={event_type} cid={_cid or '(无)'} "
                    f"frag={_frag!r}\n"
                    f"  ── 本次 pulse_id={_pid} source={_src}\n{_stack}"
                    f"  ── 上次 pulse_id={_prev[1]} source={_prev[2]} "
                    f"({_now - _prev[0]:.3f}s 前)\n{_prev[3]}"
                )
        except Exception as _exc:
            _module_logger.debug(f"[重复事件遥测] 异常已忽略: {type(_exc).__name__}: {_exc}")

    def register_condition(self, organ_name: str, 
                           event_types: list[str] | None = None,
                           space_paths: list[str] | None = None,
                           intent_labels: list[str] | None = None,
                           min_priority: int = 0,
                           handler: Callable | None = None) -> str:
        with self._lock:
            self._condition_counter += 1
            cond_id = f"cond:{organ_name}:{self._condition_counter}"
            self._conditions[cond_id] = {
                "condition": {
                    "organ_name": organ_name,
                    "event_types": event_types or [],
                    "space_paths": space_paths or [],
                    "intent_labels": intent_labels or [],
                    "min_priority": min_priority,
                },
                "handler": handler,
            }
            return cond_id
    
    def unregister_condition(self, cond_id: str) -> bool:
        with self._lock:
            if cond_id in self._conditions:
                del self._conditions[cond_id]
                return True
            return False
    
    # ========== 脉冲历史查询 ==========
    
    def get_history(self, limit: int = 20, event_type: str | None = None) -> list[dict]:
        with self._lock:
            history = list(self._history)
        if event_type:
            history = [h for h in history if h.get("event_type") == event_type]
        return list(reversed(history))[-limit:]
    
    def get_current(self, key: str) -> Any | None:
        now_ns = time.time_ns()
        with self._lock:
            for pulse in reversed(self._history):
                if pulse.get("event_type") == key:
                    ttl_ns = pulse.get("ttl_ns", 5_000_000_000)
                    ts_ns = pulse.get("timestamp_ns", 0)
                    if ttl_ns <= 0 or (now_ns - ts_ns) <= ttl_ns:
                        return pulse
        return None
    
    # ========== 统计信息 ==========
    
    def get_total_handled(self) -> int:
        """公开只读访问已处理脉冲计数，供假死探测器使用（规则14）"""
        # ★PHASE14-锁优化：读端必须与写端同一把锁，改用 _handled_lock。
        #   若此处仍用 self._lock 而写端用 _handled_lock，读写不对称，
        #   假死探测器可能读到撕裂/过期值——那比锁竞争更危险。
        with self._handled_lock:
            return self._total_handled

    def get_stats(self) -> dict[str, Any]:
        layer_stats = {}
        for layer_tag, pool in self._layer_pools.items():
            layer_stats[layer_tag] = {
                "max_workers": pool._max_workers,
                "active": True,
            }
        
        with self._lock:
            return {
                "total_published": self._total_published,
                "total_matched": self._total_matched,
                "total_unmatched": self._total_unmatched,
                "total_duplicated": self._total_duplicated,
                "total_expired": self._total_expired,
                "total_storm_blocked": self._total_storm_blocked,
                "active_conditions": len(self._conditions),
                "history_size": len(self._history),
                "has_pulse_core": self.pulse_core is not None,
                "has_resonance_engine": self.resonance_engine is not None,
                "layer_pools": layer_stats,
                "field_mode": self._field_mode,
                "high_load": self._high_load,
                "in_storm": self._in_storm,
                "load_level": self._load_level,
                "cpu_usage": self._cpu_usage,
                "mem_usage": self._mem_usage,
                "gpu_usage": self._gpu_usage,
                "adaptive_pool_size": self._adaptive_pool_size,
                "active_tasks": self._active_task_count,
                "processed_cache_size": len(self._processed_pulses),
            }

    # ========== ★登顶路线图-协调层：死数据/幽灵监听审计 ==========

    def audit_ghost_listeners(self, min_publish_threshold: int = 0) -> dict[str, Any]:
        """
        审计信息场中的“死数据”与“幽灵监听”。

        - 幽灵监听：注册了共振条件但从未触发过的歇器官
          （cond_trigger_count == 0 且 已注册超过某时长）。
        - 死数据：发布了但无任何条件匹配的 event_type。

        返回结构：
        {
          "ghost_listeners": [{"cond_id", "organ", "event_types"}, ...],
          "dead_events": [{"event_type", "published", "unmatched"}, ...],
          "total_conditions": int,
          "total_event_types": int,
          "generated_at": float,
        }
        """
        with self._lock:
            _conds = dict(self._conditions)
            _triggers = dict(self._cond_trigger_count)
            _events = dict(self._event_publish_count)
            _unmatched = dict(self._event_unmatched_count)

        _ghosts = []
        for _cid, _cd in _conds.items():
            if _triggers.get(_cid, 0) == 0:
                _cond = _cd.get("condition", {}) or {}
                _ghosts.append({
                    "cond_id": _cid,
                    "organ": _cond.get("organ_name", "unknown"),
                    "event_types": _cond.get("event_types", []),
                })

        _dead = []
        for _et, _cnt in _events.items():
            _unc = _unmatched.get(_et, 0)
            # 发布超过阈值且全部未匹配，认定为死数据
            if _unc >= _cnt >= max(1, min_publish_threshold) and _unc > 0:
                _dead.append({"event_type": _et, "published": _cnt, "unmatched": _unc})

        # 按未匹配次数降序
        _dead.sort(key=lambda x: x["unmatched"], reverse=True)
        return {
            "ghost_listeners": _ghosts,
            "dead_events": _dead,
            "total_conditions": len(_conds),
            "total_event_types": len(_events),
            "generated_at": time.time(),
        }

    def get_global_state(self) -> dict[str, Any]:
        """
        获取全局状态快照，供所有器官在做外部操作决策时感知全局。
        
        返回的信息包括：
        - 系统负载等级与高负载标记
        - 外部操作调度器的活跃任务数
        - 各器官可自行根据这些信息决定是否执行、何时执行
        
        设计原则：
        - 不替代任何器官做决策，只提供感知能力
        - 各器官自行判断，保持去中心化协同
        """
        state = {
            "load_level": self._load_level,
            "is_high_load": self._load_level in ("heavy", "critical"),
            "active_external_ops": 0,
            "max_concurrent_ops": 2,
            "timestamp": time.time(),
        }
        
        # 获取外部操作调度器状态
        try:
            from nucleus.external_executor import get_external_executor
            executor = get_external_executor()
            stats = executor.get_stats()
            state["active_external_ops"] = stats.get("active_count", 0)
            state["max_concurrent_ops"] = stats.get("max_concurrent", 2)
        except Exception as _ee_e:
            # ★P0-1修复（第十二批）：原为 self._log（幽灵方法）→ 改用模块 logger
            _module_logger.debug(f"[状态采集] 外部执行器状态获取异常(已忽略): {_ee_e}")
        
        return state
    # ========== 内部方法：条件匹配 ==========
    
    def _match_condition(self, pulse: dict[str, Any], condition: dict[str, Any]) -> bool:
        event_type = pulse.get("event_type", "")
        priority = pulse.get("priority", 5)
        if priority < condition.get("min_priority", 0):
            return False
        event_types = condition.get("event_types", [])
        if not event_types:
            return True
        return any(self._match_event(event_type, pattern) for pattern in event_types)
    
    def _apply_discovered_patterns(self, patterns: list):
        """★P1补强：应用发现的事件模式——高频模式发射预测信号。"""
        try:
            for pattern in patterns:
                ptype = pattern.get('discovery_type', '')
                etype = pattern.get('event_type', '')
                freq = pattern.get('frequency', 0)
                count = pattern.get('occurrence_count', 0)

                # ★P1热加载: 从config读取模式频率阈值
                try:
                    from config import RUNTIME_PARAMS as _RP_pat
                    _pat_freq_thresh = _RP_pat.get("infofield_pattern_freq_threshold", 0.3)
                    _pat_count_thresh = _RP_pat.get("infofield_pattern_count_threshold", 5)
                except Exception:
                    _pat_freq_thresh, _pat_count_thresh = 0.3, 5
                # 只处理高频重复模式
                if ptype != 'repeated' or freq < _pat_freq_thresh or count < _pat_count_thresh:
                    continue

                # 发射预测信号脉冲（低优先级，不干扰主流程）
                try:
                    # ★PHASE17-B1修复（2026-09-07）：属性名拼写错误导致预测脉冲永远不发射。
                    #   注入接口 set_pulse_core() 存的是公共属性 self.pulse_core（见 :375-377），
                    #   此处却检查私有属性 self._pulse_core —— 该属性从未被赋值，
                    #   hasattr 恒为 False，pattern_prediction 事件自上线起从未产生过。
                    #   最小侵入修复：改用 getattr 兼容两种命名，优先公共属性。
                    _pc = getattr(self, "pulse_core", None) or getattr(self, "_pulse_core", None)
                    if _pc is not None:
                        _pc.emit(
                            source_organ="InfoField",
                            event_type="pattern_prediction",
                            payload={
                                "predicted_event": etype,
                                "frequency": freq,
                                "occurrence": count,
                                "confidence": min(0.9, freq * count / 10),
                                "message": pattern.get('message', ''),
                            },
                            priority=1,
                            layer="L1",
                        )
                        _module_logger.debug(
                            f"模式预测信号: {etype} (频率={freq}, 次数={count})")
                except Exception as _pe:
                    _module_logger.debug(f"模式预测信号发射失败: {_pe}")

                # 记录模式应用历史
                if not hasattr(self, '_pattern_application_history'):
                    self._pattern_application_history = []
                self._pattern_application_history.append({
                    'event_type': etype,
                    'frequency': freq,
                    'count': count,
                    'timestamp': time.time(),
                })
                if len(self._pattern_application_history) > 50:
                    self._pattern_application_history = self._pattern_application_history[-50:]

            # ===== ★A-16（2026-09-08）：周期模式消费 = 预调度 =====
            #   预测到周期性事件即将发生（默认5分钟内）→ 发射 pattern_prewarm 脉冲，
            #   消费方可据此预加载相关知识/预留资源。此前 predict_next_occurrence()
            #   完全无人调用（模式发现了但不产生任何价值）。
            #   灰度 ENABLE_STREAMMINER_CONSUMPTION 默认 False；关闭时整段跳过。
            try:
                import config as _cfg_a16
                if not getattr(_cfg_a16, "ENABLE_STREAMMINER_CONSUMPTION", False):
                    return
                _consume = self.stream_miner.consume()
                _prewarm = _consume.get("prewarm") or []
                if _prewarm:
                    _pc2 = (getattr(self, "pulse_core", None)
                            or getattr(self, "_pulse_core", None))
                    if _pc2 is not None:
                        for _u in _prewarm[:3]:
                            _pc2.emit(
                                source_organ="InfoField",
                                event_type="pattern_prewarm",
                                payload={
                                    "predicted_event": _u.get("event_type"),
                                    "in_sec": _u.get("in_sec"),
                                    "predicted_ts": _u.get("predicted_ts"),
                                    "regularity": _u.get("regularity"),
                                    "action": "preload_knowledge",
                                },
                                priority=1,
                                layer="L1",
                            )
                    # ★主线第3批 任务4(P1-18)：将即将发生的周期事件真正交给
                    #   消费方执行「知识预加载」——此前 pattern_prewarm 仅被发射、无人消费。
                    try:
                        from nucleus.genesis.PatternPrewarmConsumer import get_pattern_prewarm_consumer
                        _loaded = get_pattern_prewarm_consumer().on_prewarm(_prewarm)
                        if _loaded:
                            _module_logger.info(
                                f"周期模式知识预加载: 已预热 {_loaded} 个事件的知识线索")
                    except Exception as _pwce:
                        _module_logger.debug(f"知识预加载消费异常(已忽略): {_pwce}")
                    _module_logger.info(
                        f"周期模式预调度: {len(_prewarm)}个事件将发生"
                        f"（最早 {_prewarm[0].get('in_sec')}秒后 "
                        f"{_prewarm[0].get('event_type')}）")
                _load = _consume.get("load_forecast") or {}
                if _load:
                    _high = [k for k, v in _load.items() if v.get("level") == "high"]
                    _module_logger.info(
                        f"重复模式负载预测: {len(_load)}类高频事件"
                        f"（高负载{len(_high)}类{'：' + ', '.join(_high[:3]) if _high else ''}）")
                _q = _consume.get("quality") or {}
                if _q.get("pruned"):
                    _module_logger.info(
                        f"模式质量评估: 淘汰{_q['pruned']}个低命中率模式"
                        f"（评估{_q.get('evaluated', 0)}个）")
            except Exception as _a16e:
                _module_logger.debug(f"A-16模式消费异常(已忽略): {_a16e}")
        except Exception as e:
            _module_logger.debug(f"模式应用异常: {e}")

    @staticmethod
    def _match_event(event_type: str, pattern: str) -> bool:
        if pattern == "*" or pattern == event_type:
            return True
        pattern_parts = pattern.split(".")
        event_parts = event_type.split(".")
        if len(pattern_parts) != len(event_parts):
            return False
        return all(not (pp != "*" and pp != ep) for pp, ep in zip(pattern_parts, event_parts))
    
    # ========== v9.5: 分层调度管理 ==========
    
    def get_layer_pool(self, layer: str) -> ThreadPoolExecutor | None:
        return self._layer_pools.get(layer)
    
    def set_field_mode(self, mode: str):
        if mode in ("PULSE", "OSCILLATION", "MIXED"):
            self._field_mode = mode
    
    def get_field_mode(self) -> str:
        return self._field_mode
    
    def set_storm_threshold(self, threshold: int): 
        self._storm_threshold = threshold
    
    def set_storm_action(self, action: str):
        if action in (_STORM_AGGREGATE, _STORM_THROTTLE, _STORM_REJECT):
            self._storm_action = action
    
    def shutdown(self):
        self._shutting_down = True
        # ★v17.0修复：所有线程池统一使用wait=False快速关闭
        for pool in self._layer_pools.values():
            try:
                pool.shutdown(wait=False)
            except Exception as _exc:
                _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
        if self._adaptive_task_pool:
            try:
                self._adaptive_task_pool.shutdown(wait=False)
            except Exception as _exc:
                _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
        self._task_pool_ready = False

# ========== 自测 ==========
# ========== TimeCore 模块级单例支持（v9.x） ==========
_THE_INFO_FIELD = None


def get_info_field() -> "InfoField | None":
    """★v9.x TimeCore：获取主信息场单例。

    由 main.py 创建 InfoField 实例时自动注册（见 InfoField.__init__），
    供非器官组件（如 TimeCore 内核单例）获取，无需修改 main.py。
    """
    return _THE_INFO_FIELD


if __name__ == "__main__":
    print("=== InfoField v9.5 终极版自测 ===\n")
    
    class MockOrgan:
        def __init__(self, name):
            self.name = name
            self.received = []
            self._error_count = 0
            self._fuse_threshold = 3
            self.status = "running"
            self.organ_name = name
            self._lock = __import__('threading').Lock()
        
        def on_pulse(self, pulse):
            self.received.append(pulse["event_type"])
            return {"status": "ok"}
        
        def _handle_pulse_safe(self, pulse):
            if self.status == "fused":
                return None
            try:
                return self.on_pulse(pulse)
            except Exception:
                with self._lock:
                    self._error_count += 1
                    if self._error_count >= self._fuse_threshold:
                        self.status = "fused"
                return None
    
    field = InfoField(max_history=100)
    heart = MockOrgan("心脏")
    lung = MockOrgan("肺")
    
    field.register_condition("心脏", event_types=["heart.*", "system.*", "test.*"], handler=heart.on_pulse)
    field.register_condition("肺", event_types=["lung.*", "system.*", "test.*"], handler=lung.on_pulse)
    
    print("1. 基础异步分发:")
    result = field.publish({
        "pulse_id": "p1", "source_organ": "main",
        "event_type": "test.normal", "layer": "L1",
        "priority": 5, "timestamp_ns": time.time_ns(), "payload": {}
    })
    print(f"   publish返回: {result['status']}, 异步={result.get('dispatched_async')}")
    
    time.sleep(0.3)
    print(f"   心脏收到: {heart.received}")
    print(f"   肺收到: {lung.received}")
    
    print("\n2. 幂等防重放:")
    result2 = field.publish({
        "pulse_id": "p1", "source_organ": "main",
        "event_type": "test.duplicate", "layer": "L1",
        "priority": 5, "timestamp_ns": time.time_ns(), "payload": {}
    })
    print(f"   重复pulse_id: {result2['status']} (预期duplicate)")
    
    print("\n3. N对一并发:")
    heart.received.clear()
    for i in range(5):
        field.publish({
            "pulse_id": f"p_multi_{i}", "source_organ": f"器官{i}",
            "event_type": "test.concurrent", "layer": "L1",
            "priority": 5, "timestamp_ns": time.time_ns(), "payload": {"index": i}
        })
    time.sleep(0.5)
    print(f"   心脏收到并发脉冲: {len([e for e in heart.received if e == 'test.concurrent'])} 条 (预期5)")
    
    print("\n4. 分层线程池:")
    for layer in ["L0", "L1", "L2", "L3"]:
        pool = field.get_layer_pool(layer)
        print(f"   {layer}: {'✅' if pool else '❌'}")
    
    stats = field.get_stats()
    print(f"\n5. 统计: 发布{stats['total_published']} 匹配{stats['total_matched']} "
          f"重复{stats['total_duplicated']} 风暴{stats['total_storm_blocked']} "
          f"缓存{stats['processed_cache_size']}")
    print(f"   负载等级: {stats['load_level']}, CPU: {stats['cpu_usage']}, MEM: {stats['mem_usage']}")
    print(f"   自适应线程池: {stats['adaptive_pool_size']} 线程, 活跃任务: {stats['active_tasks']}")
    
    print("\n6. 自适应任务提交:")
    submitted = field.submit_adaptive_task(
        lambda msg: print(f"   [异步任务] {msg}"),
        task_name="测试任务",
        priority="normal",
        msg="Hello from adaptive task"
    )
    time.sleep(0.2)
    print(f"   任务提交: {'✅' if submitted else '❌'}")
    
    print("\n7. 外部负载设置:")
    field.set_load_level("critical")
    print(f"   当前负载等级: {field.get_load_level()}")
    print(f"   自适应线程池: {field._adaptive_pool_size}")
    
    field.set_load_level("light")
    print(f"   恢复后负载等级: {field.get_load_level()}")
    print(f"   自适应线程池: {field._adaptive_pool_size}")
    
    field.shutdown()
    print("\n8. 线程池已关闭")
    print("\n=== 自测全部通过 ===")
    