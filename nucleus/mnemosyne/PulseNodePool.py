# -*- coding: utf-8 -*-
"""
PulseNodePool.py —— 脉冲节点池管理器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 管理所有PulseNode的生命周期，冷热分层内存池（热池/温池/冷池），节点增删改查，对接ResonanceEngine维护频率索引+空间索引，L1节点定期淘汰
机制: 基于PulseNodePool类实现，包含线程安全的写操作保护（self._lock），支持并发访问，冷存compaction触发阈值单一事实来源
定位: 记忆核心层，知识存储基础设施，不发射脉冲无需layer标记
"""

import json
import os
import sys
from nucleus.const import LogLevel
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import threading
import time
from typing import Any
from nucleus.logger import get_module_logger

from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底
from nucleus.mnemosyne.PulseNode import PulseNode


_module_logger = get_module_logger("PulseNodePool")

# ★PHASE17-C3（2026-09-07）：冷存 compaction 的「触发最小文件数」单一事实来源。
#   修复前该阈值在 4 处各写各的、且互相矛盾：
#       :197  启动检查传入 max_files=2
#       :1427 写盘路径传入 max_files=20   ← 与上面差 10 倍
#       :186 / :1622 用硬编码 `<= 1` 做「无需合并」判断
#       :1603 / :1783 注释写「≥ 20」
#   后果：日志出现「发现 9 个历史小文件」却因 9 < 20 不触发合并的自相矛盾行为，
#        而冷存小文件累积正是 O(N²) 全量召回的根源（1000 节点 162.9s → 合并后 1.5s）。
#   现统一为常量 2：语义是「文件数 ≥ 2 才值得合并」（1 个文件合并无意义）。
COLD_COMPACT_MIN_FILES = 2


class PulseNodePool(SilentLogMixin):
    """
    脉冲节点池（v9.5 适配版）
    
    所有知识节点在内存中的统一管理器。
    
    三层池:
        热池 (hot):  L2+L3 高频激活节点 —— 全内存，极速响应
        温池 (warm): L2 中频节点 + 活跃 L1 —— 内存缓存
        冷池 (cold): L1 低频历史节点 —— 按需加载/淘汰
    """
    
    def __init__(self, max_hot: int = 10000, max_warm: int = 50000):
        """
        Args:
            max_hot: 热池最大节点数
            max_warm: 温池最大节点数
        """
        # 三层节点池
        self._hot: dict[str, PulseNode] = {}    # node_id → PulseNode
        self._warm: dict[str, PulseNode] = {}   # node_id → PulseNode
        self._cold: dict[str, PulseNode] = {}   # node_id → PulseNode（L1 冷缓存，可被驱逐）
        self._instinct: dict[str, PulseNode] = {}  # L4 本能节点池（独立存储，常驻内存）        
        # 容量上限
        self._max_hot = max_hot
        self._max_warm = max_warm
        
        # 线程安全
        self._lock = threading.Lock()
        # ★v25.1吞吐量优化: 内容去重统计（用于知识质量反馈调速）
        self._content_add_total = 0
        self._content_dedup_total = 0
        
        # ★阶段B'新增：L1 冷存储分治配置（默认关闭，零侵入）
        #   _cold_storage_enabled: 是否启用冷池驱逐（由 use_cold_storage 开关控制）
        #   _max_cold_cache: 冷池内存缓存上限（超过则驱逐最久未激活节点）
        #   _cold_dir: 磁盘冷存目录（复用 Parquet L1 分区）
        #   _cold_evicted: 被驱逐的 node_id 集合（内存登记，用于快速判断是否已驱逐）
        #   _cold_stats: 召回命中率统计
        self._cold_storage_enabled = False
        self._max_cold_cache = 5000
        self._cold_dir = "data/knowledge/cold"
        self._cold_evicted: set[str] = set()
        self._cold_stats = {"recall_hits": 0, "recall_misses": 0}
        # ★P1修复：冷节点召回失败聚合计数
        self._cold_recall_fail_count = 0
        self._cold_recall_fail_last_log = 0.0

        # ★第81批 T4：冷存批量写 buffer / 侧车索引 / compaction 协调（全部默认关/空，零侵入）
        self._cold_write_buffer: list = []          # 待落盘节点缓冲（攒批 flush 减碎文件）
        self._cold_buffer_lock = threading.Lock()   # 缓冲/索引独立锁（不与其他 self._lock 路径重入死锁）
        self._cold_index: dict = {}                 # node_id -> {file, rg, offset, level} 侧车索引
        self._cold_index_loaded = False             # 索引是否已加载/构建（懒加载）
        self._cold_compacting = False               # compaction 进行中标志：期间驱逐写只进 buffer，不碎写
        self._cold_last_flush_time = 0.0
        self._cold_compact_cooldown_until = 0.0
        self._cold_compact_fail_streak = 0
        
        # ★P3-1新增：分层索引——按 evol_level 快速定位节点
        # 格式：{"L1": {node_id, ...}, "L2": {node_id, ...}, "L3": {node_id, ...}}
        self._level_index: dict[str, set] = {"L1": set(), "L2": set(), "L3": set()}
        # ★P3-1新增：路径前缀索引——按一级路径快速定位节点
        # 格式：{"/技术": {node_id, ...}, "/身份": {node_id, ...}, ...}
        self._path_index: dict[str, set] = {}
        # ★横向联系索引：语义关系反向索引（target_node_id → {(source_id, relation_type)}），
        # 懒构建 + 脏标记，支撑「谁关联了我」的反向横向遍历（此前横向联系仅存在于节点内部，无池级索引）。
        self._semantic_index: dict[str, set] = {}
        self._semantic_index_valid = False
        
        # 关联组件
        self.resonance_engine = None  # ResonanceEngine 实例
        # ★阶段C'新增：外置索引持久化器（可选，开关 use_external_index 开启时注入）
        self._index_store = None
        
        # 统计
        self._total_added = 0
        self._total_removed = 0
        self._total_activated = 0
        # ===== 未来演化预留（v10.0 振荡场） =====
        self._field_strength = 1.0  # 振荡场强度 0.0-1.0（v10.0注入）
        
        # ★v23.0新增：赫布学习器引用
        self._hebbian_learner = None

        # ===== 第69批 T1: 冷热分离增强 =====
        # 访问频率跟踪: node_id → access_count
        self._access_count: dict[str, int] = {}
        # LRU 温缓存（OrderedDict，淘汰最久未访问）
        from collections import OrderedDict
        self._warm_lru: OrderedDict = OrderedDict()
        # 冷节点元数据（只存 node_id + evol_level + 文件位置，不加载完整节点）
        self._cold_metadata: dict[str, dict] = {}
        # 缓存命中率统计
        self._cache_stats = {"warm_hits": 0, "warm_misses": 0, "cold_loads": 0, "cache_shrinks": 0}
        # 冷热分离开关（默认开）
        self._hot_cold_enabled = True
        try:
            import config as _cfg69
            self._hot_cold_enabled = bool(getattr(_cfg69, "ENABLE_HOT_COLD_SEPARATION", True))
            self._warm_cache_size = int(getattr(_cfg69, "WARM_CACHE_SIZE", 10000))
            self._promotion_threshold = int(getattr(_cfg69, "NODE_PROMOTION_THRESHOLD", 100))
            self._demotion_threshold = int(getattr(_cfg69, "NODE_DEMOTION_THRESHOLD", 10))
            self._memory_warning_threshold = float(getattr(_cfg69, "MEMORY_USAGE_WARNING_THRESHOLD", 0.8))
        except Exception:
            self._hot_cold_enabled = True
            self._warm_cache_size = 10000
            self._promotion_threshold = 100
            self._demotion_threshold = 10
            self._memory_warning_threshold = 0.8
        
    # ========== 框架注入 ==========
    
    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'nodepool_max_hot' in _rp and hasattr(self, '_max_hot'):
                self._max_hot = _rp['nodepool_max_hot']
            if 'nodepool_max_warm' in _rp and hasattr(self, '_max_warm'):
                self._max_warm = _rp['nodepool_max_warm']
            if 'nodepool_max_cold_cache' in _rp and hasattr(self, '_max_cold_cache'):
                self._max_cold_cache = _rp['nodepool_max_cold_cache']
        except Exception:
            pass


    def set_resonance_engine(self, engine):
        """注入共振引擎，用于自动维护索引"""
        self.resonance_engine = engine
    
    def set_hebbian_learner(self, learner):
        """★v23.0新增：注入赫布学习器，节点激活时通知"""
        self._hebbian_learner = learner

    def sync_hebbian_weight(self, node_id: str, node=None) -> float:
        """★A-16（2026-09-08）：把赫布学习器学到的共现权重**回写**到节点
        `hebbian_weight` 字段（共振引擎记忆维关键词通道会读它，占该通道 30%）。

        ★断裂诊断结论（先确认、后修复）：
            回写链路**未完全断裂**——v23.0 已在 `get()` 内联实现"激活 → 取平均连接
            权重 → 写回节点"。但存在两个真实缺陷：
            ① 覆盖面窄：只有 `get()` 一条路径回写，其他激活路径不回写；
            ② 只增不减：用 `max(旧值, 新值)`，赫布衰减（apply_decay）后节点
               权重永远降不下来，陈旧共现关系被永久固化。
            本方法修复②（改为**可升可降**的 EMA 平滑），并把回写抽成公共方法
            供其他激活路径复用（①的扩展点保留，不改动现有调用点行为）。

        Args:
            node_id: 节点ID
            node: 可选，直接给节点对象（省一次查找）

        Returns:
            回写后的 hebbian_weight（0.0 表示未回写）
        """
        _learner = getattr(self, "_hebbian_learner", None)
        if _learner is None or not getattr(_learner, "is_enabled", lambda: False)():
            return 0.0
        _node = node
        if _node is None:
            with self._lock:
                _node = (self._hot.get(node_id) or self._warm.get(node_id)
                         or self._cold.get(node_id))
        if _node is None:
            return 0.0
        try:
            _conns = _learner.get_node_connections(node_id)
            _target = (sum(_conns.values()) / len(_conns)) if _conns else 0.0
            _old = float(getattr(_node, "hebbian_weight", 0.0) or 0.0)
            # EMA 平滑（α=0.3）：既跟随衰减下降，又不过度震荡
            _new = round(_old * 0.7 + _target * 0.3, 4) if _conns else round(_old * 0.9, 4)
            _new = max(0.0, min(1.0, _new))
            if abs(_new - _old) > 1e-6:
                _node.hebbian_weight = _new
            return _new
        except Exception:
            return 0.0

    def set_index_store(self, index_store):
        """★阶段C'新增：注入外置索引持久化器（可选）。None 表示不启用磁盘索引副本。"""
        self._index_store = index_store

    def get_index_store(self):
        """★阶段C'新增：返回外置索引持久化器（可能为 None）。"""
        return self._index_store

    def set_cold_storage(self, enabled: bool, max_cold_cache: int = 5000,
                         cold_dir: str = "data/knowledge/cold"):
        """★阶段B'新增：配置 L1 冷存储分治。

        Args:
            enabled: 是否启用冷池驱逐（use_cold_storage 开关）
            max_cold_cache: 冷池内存缓存上限
            cold_dir: 磁盘冷存目录（复用 Parquet L1 分区，实际写 parquet_dir）
        """
        self._cold_storage_enabled = enabled
        self._max_cold_cache = max_cold_cache
        self._cold_dir = cold_dir
        # ★R4修复（P1）：S4 原实现只在「冷存写盘满10次」时才检查 compaction，
        #   但真实运行数据显示该门槛几乎不可达——
        #   温池上限 max_warm=50000（config.py:176），7小时运行远未填满，
        #   _write_cold_node_to_disk 调用次数为个位数，检查从未发生。
        #   而「文件数」是跨进程持久化的状态（历史已累积330个parquet），
        #   进程重启后不检查就永远不会发现它们，S4 收益归零。
        #   故在此处（冷存启用时）补一次启动检查：合并历史遗留的小文件。
        #   用 daemon 后台线程执行，避免阻塞启动；失败不影响主流程。
        if enabled:
            try:
                # ★第81批 T4：灰度开关 COLD_DISABLE_STARTUP_COMPACT（默认 False）。
                #   测试场景（A6 构造 2000/1000 历史碎文件需确定性地「先计数再手动 compaction」）
                #   需关闭后台 daemon 以消除与手动 compact 的竞态；生产默认 False（daemon 照常运行）。
                if self._m81_cold_cfg("COLD_DISABLE_STARTUP_COMPACT", False):
                    _module_logger.debug(
                        "冷存启动compaction检查被 COLD_DISABLE_STARTUP_COMPACT 关闭（测试/降级用）")
                else:
                    _t = threading.Thread(
                        target=self._startup_cold_compaction_check,
                        name="冷存启动compaction检查",
                        daemon=True,
                    )
                    _t.start()
                    # ★第81批 T4-④：可选有界同步等待（默认 0 = 不阻塞启动）。
                    #   生产冷存开关为 False、零爆炸半径；仅在显式配置 >0 时才阻塞等待，
                    #   保证「先 compaction 后 lazy/驱逐」顺序，消除「边合并边碎写」。
                    _wait = self._m81_cold_cfg("COLD_STARTUP_COMPACT_WAIT_SECONDS", 0.0)
                    if _wait and _wait > 0:
                        _t.join(timeout=float(_wait))
            except Exception as _e:
                _module_logger.debug(f"冷存启动compaction检查线程启动失败(忽略): {_e}")

    def _startup_cold_compaction_check(self) -> None:
        """★R4：启动时检查冷存小文件数，超阈值则异步合并。

        与写盘路径的定期检查互补：写盘路径覆盖「新增文件」，
        此处覆盖「历史遗留文件」（进程重启后仍然存在的旧小文件）。
        """
        try:
            _count = self.count_cold_parquet_files()
            # ★PHASE17-C3：与 maybe_compact_cold_storage 共用同一阈值常量
            if _count < COLD_COMPACT_MIN_FILES:
                _module_logger.info(f"冷存启动检查: parquet文件数={_count}，无需合并")
                return
            _module_logger.info(
                f"冷存启动检查: 发现{_count}个历史parquet小文件，触发后台合并"
                f"（未合并时全量召回为O(N²)复杂度）")
            # ★P2-1修复（第十批）：启动检查阈值与「无需合并」判断对齐。
            #   原 max_files=20 导致「发现 9 个历史小文件」却因 9 < 20 而不触发合并，
            #   与上方「发现>1个即触发后台合并」的注释意图自相矛盾。
            #   9 个文件已产生 O(N²) 全量召回开销，应合并。改为 max_files=2：
            #   只要 > 1 个文件（即确有合并价值）就触发，与启动判断语义一致。
            # ★PHASE17-C3：改用统一常量 COLD_COMPACT_MIN_FILES，避免再次漂移。
            _result = self.maybe_compact_cold_storage(max_files=COLD_COMPACT_MIN_FILES)
            if _result.get("triggered"):
                _module_logger.info(f"冷存启动compaction完成: {_result}")
            else:
                _module_logger.info(
                    f"冷存启动compaction未触发: {_result}")
        except Exception as _e:
            _module_logger.warning(f"冷存启动compaction检查失败(不影响运行): {_e}")

    def is_cold_storage_enabled(self) -> bool:
        """★阶段B'新增：返回冷存储分治是否启用。"""
        return self._cold_storage_enabled

    def save_index_snapshot(self) -> dict[str, bool]:
        """★阶段C'新增：把当前内存三类索引全量落盘到外置索引表。

        仅在注入 index_store 时生效；未注入返回空 dict（零副作用）。
        """
        if self._index_store is None:
            return {}
        self._ensure_semantic_index()
        with self._lock:
            _level = {k: set(v) for k, v in self._level_index.items()}
            _path = {k: set(v) for k, v in self._path_index.items()}
            _semantic = {k: set(v) for k, v in self._semantic_index.items()}
        return self._index_store.save_all(_level, _path, _semantic)

    def load_index_async(self, on_done=None) -> None:
        """★阶段C'新增：异步从外置索引表加载索引副本（不阻塞，不改变内存索引）。

        注意：C' 阶段此方法仅加载「磁盘索引副本」供对账/观测，
        不回填内存索引（回填属阶段 B' 冷存储召回职责）。
        """
        if self._index_store is not None:
            self._index_store.load_async(on_done=on_done)

    # ========== 公开快照接口（P0-批次3：替代跨器官私有穿透 AP1/规则14） ==========
    def snapshot_active_nodes(self) -> list:
        """线程安全快照：返回热/温层所有节点的副本列表。
        替代跨器官直接穿透 self._lock/_hot/_warm。"""
        with self._lock:
            return list(self._hot.values()) + list(self._warm.values())

    # ========== 未来演化预留（v10.0 振荡场） ==========
    
    def set_field_strength(self, strength: float):
        """
        【预留 v10.0】设置振荡场强度，调制节点激活阈值。
        
        场强度影响:
            - 场强高(>0.8)：冷池节点更容易被激活并提升到温池
            - 场强中(0.4-0.8)：正常激活行为
            - 场强弱(<0.4)：仅热池节点可被有效激活，温/冷池节点激活阈值提高
        
        Args:
            strength: 场强度 0.0-1.0
        """
        self._field_strength = max(0.0, min(1.0, strength))

    # ========== 节点增删改查 ==========
    def add(self, node: PulseNode) -> str:
        """
        添加一个节点到池中。
        
        自动分配到合适的池层:
            - L3 节点 → 热池
            - L2 节点 → 热池（高频）或 温池
            - L1 节点 → 温池（活跃）或 冷池
            
        Args:
            node: PulseNode 实例
            
        Returns:
            node_id
        """
        # ★主线第12批 T4/P2-31：调用点①「知识写入前」轻量质量检查（采样 INFO）。
        #   灰度 ENABLE_DATA_QUALITY_GUARD_CHECKPOINTS；关闭/异常 → 完全跳过，零副作用。
        #   热路径保护：采样计数命中才调用 Guard，避免逐节点开销。
        try:
            import config as _cfg_dq_pool
            if getattr(_cfg_dq_pool, "ENABLE_DATA_QUALITY_GUARD_CHECKPOINTS", False):
                _c = getattr(self, "_dqg_check_seq", 0) + 1
                self._dqg_check_seq = _c
                if _c % 50 == 0:  # 每 50 次写入检查一次，避免刷屏与开销
                    from nucleus.knowledge.DataQualityGuard import (
                        get_data_quality_guard as _gdq_pool,
                    )
                    _gdq_pool().check_node(node, context="知识写入前")
        except Exception as _e:
            # ★第51批 T4（P2-355）：原为裸 ``pass`` → 显式留痕（质量守卫须可观测）
            _module_logger.debug(
                "[T4留痕] 知识写入前质量检查异常已忽略: %s: %s",
                type(_e).__name__, _e)

        with self._lock:
            node_id = node.node_id
            # ★知识污染治理：钳制信任分到 [0, 100]，堵住快照恢复/越界写入的异常信任分
            try:
                node.trust_score = max(0.0, min(100.0, float(node.trust_score)))
            except Exception:
                node.trust_score = 50.0
            
            # ===== 幂等检查 =====
            if node_id in self._hot or node_id in self._warm or node_id in self._cold:
                # 已存在，仅更新索引（如果缺失）
                if self.resonance_engine:
                    existing = (self._hot.get(node_id) or 
                                self._warm.get(node_id) or 
                                self._cold.get(node_id))
                    if existing:
                        try:
                            self.resonance_engine.index_node(existing.to_dict())
                        except Exception as e:
                            _module_logger.warning(f"索引更新失败(幂等): {e}")
                return node_id  # 幂等：不重复添加
            # ===== 幂等检查结束 =====

            # ===== 内容级去重（所有层级，按层级调整阈值）=====
            # 相同/高度相似内容不重复存储，合并到已有节点（提升信任）
            # L1: 阈值70%, 检查100节点; L2: 阈值75%, 检查50节点; L3: 阈值80%, 检查30节点
            if node.keywords:
                self._content_add_total += 1
                _dup_merged = self._try_merge_duplicate(node)
                if _dup_merged:
                    return node_id  # 已合并到已有节点，不重复添加
            # ===== 内容级去重结束 =====

            # 确定目标池
            target_pool = self._determine_pool(node)
            
            # 添加到目标池
            target_pool[node_id] = node
            
            # 同步到共振引擎索引
            if self.resonance_engine:
                try:
                    self.resonance_engine.index_node(node.to_dict())
                except Exception as e:
                    _module_logger.warning(f"索引更新失败(新增): {e}")
            # ★P3-1新增：更新分层索引和路径索引
            self._update_index_on_add(node)
            self._m71_influx_write("node_modified", node_id, "create", "-", "created")
            
            # ★知识体系·容量治理：热/温池超限时逐级降级，防止上百亿规模下内存无界增长
            self._enforce_capacity()
            
            # ★阶段B'：冷池超限时驱逐最久未激活节点到磁盘
            if (self._cold_storage_enabled
                    and target_pool is self._cold
                    and len(self._cold) > self._max_cold_cache):
                self._enforce_cold_cache()

            # ★T1: 温缓存LRU淘汰 + 内存保护
            if self._hot_cold_enabled:
                self._enforce_warm_lru()
                self._shrink_cache()
            
            self._dual_write_neo4j_node(node, "add")
            self._total_added += 1
            return node_id

    def _try_merge_duplicate(self, node: PulseNode) -> bool:
        """内容级去重：检查是否已有相同/高度相似内容的节点。

        如果找到相似节点，提升其信任分并返回True（不重复添加）。
        按层级调整阈值和检查范围：
        - L1: 关键词重叠>=70%, 检查100节点
        - L2: 关键词重叠>=75%, 检查50节点
        - L3: 关键词重叠>=80%, 检查30节点
        """
        try:
            _new_kws = {str(k).lower() for k in (node.keywords or []) if k}
            if not _new_kws:
                return False

            _new_val = str(node.value or '')[:200]
            _path = getattr(node, 'space_path', '/') or '/'
            _level = getattr(node, 'evol_level', '')

            # 按层级调整阈值和检查范围
            if _level == PulseNode.EVOL_L3:
                _kw_threshold = 0.80
                _max_candidates = 30
            elif _level == PulseNode.EVOL_L2:
                _kw_threshold = 0.75
                _max_candidates = 50
            else:
                _kw_threshold = 0.70
                _max_candidates = 100

            # 在同路径同层级节点中查找相似内容
            _candidates = []
            for _pool in (self._hot, self._warm, self._cold):
                for _n in _pool.values():
                    if getattr(_n, 'evol_level', '') != _level:
                        continue
                    if (getattr(_n, 'space_path', '/') or '/') != _path:
                        continue
                    _candidates.append(_n)
                    if len(_candidates) >= _max_candidates:
                        break
                if len(_candidates) >= _max_candidates:
                    break

            for _existing in _candidates:
                _ex_kws = {str(k).lower() for k in (_existing.keywords or []) if k}
                if not _ex_kws:
                    continue
                _overlap = len(_new_kws & _ex_kws) / max(1, len(_new_kws | _ex_kws))
                if _overlap >= _kw_threshold:
                    _ex_val = str(_existing.value or '')[:200]
                    _val_overlap = 0
                    if _new_val and _ex_val:
                        _new_chars = set(_new_val)
                        _ex_chars = set(_ex_val)
                        _val_overlap = len(_new_chars & _ex_chars) / max(1, len(_new_chars | _ex_chars))
                    if _val_overlap >= 0.6 or _overlap >= 0.85:
                        _old_trust = getattr(_existing, 'trust_score', 50.0)
                        _new_trust = getattr(node, 'trust_score', 50.0)
                        _existing.trust_score = min(95.0, max(_old_trust, _new_trust) + 2.0)
                        _existing.activate()
                        # ★第83批 T-a1：内容去重是高频事件（实测 19:33 一分钟 1286 条 INFO）。
                        #   A·信任顶格（_old_trust >= 95）无实质变化 → 降 DEBUG（消灭 39.3% 噪音）；
                        #   C·信任有提升的合并按 space_path 做 1 分钟节流，同路径只留首条 INFO；
                        #   运行监控口径以聚合指标 get_content_dedup_rate() 为准，不依赖逐条 INFO。
                        _m83_msg = (
                            f'内容去重: 合并到已有节点(信任{_old_trust:.0f}->'
                            f'{_existing.trust_score:.0f}, 重叠={_overlap:.0%})')
                        if _old_trust >= 95.0:
                            _module_logger.debug(_m83_msg)
                        else:
                            _m83_now = time.time()
                            _m83_seen = getattr(self, '_m83_dedup_log_ts', None)
                            if _m83_seen is None:
                                _m83_seen = {}
                                self._m83_dedup_log_ts = _m83_seen
                            if _m83_now - _m83_seen.get(_path, 0.0) >= 60.0:
                                _m83_seen[_path] = _m83_now
                                _module_logger.info(_m83_msg)
                            else:
                                _module_logger.debug(_m83_msg)
                        self._content_dedup_total += 1
                        return True
        except Exception as e:
            _module_logger.debug(f'内容去重检查异常: {e}')
        return False

    def get_content_dedup_rate(self) -> float:
        """★v25.1吞吐量优化: 获取内容去重率（0.0-1.0）。

        去重率高表示知识趋于饱和，应减慢学习速度；
        去重率低表示有大量新知识，可加快学习速度。
        """
        if self._content_add_total == 0:
            return 0.0
        return min(1.0, self._content_dedup_total / max(1, self._content_add_total))

    # ========== 任务8（2026-09-08）：按 value 定位 + 路径迁移 ==========
    # 背景（真实根因，已逐行核实）：PulseNode.node_id = _generate_id(value, source_organ)
    # 是**确定性**的。当一条 SEED 记忆「改了 space_path、value 一字未动」时，
    # 重启注入算出的 node_id 与快照恢复的旧节点**完全相同**，
    # add() 的幂等检查（:294）命中后直接 return，新路径永远不生效
    # → C+D 阶段的「共振引擎」节点仍停留在旧路径 /自我/架构/共振引擎，
    #   而「五维共振」因 D-1 微调了 value → node_id 变化 → 注入成功。
    #   这一差异正是星轨观察到的「一个成功一个失败」的根因。
    # 处置原则：**路径迁移而非删除**（宪法红线：不删知识节点）。
    # 本组方法把脏活全部下沉到节点池，调用方（main._inject_seed_memories）只加调用。
    def find_by_value(self, value: str, level: str | None = None,
                      fuzzy: bool = False,
                      threshold: float = 0.90) -> PulseNode | None:
        """按 value 查找已存在的等价节点（**跨路径**，这正是 add() 内容去重做不到的地方）。

        用途：注入/写入前判断「同一份知识是不是已经躺在别的旧路径下」，
        若是则交给 update_node_path() 迁移，而不是再建一条重复节点。

        匹配策略（两阶段）：
            1) 精确匹配：value 字符串完全相同 → 立即返回（默认行为，零误判）
            2) 模糊匹配：仅当 fuzzy=True 时启用，取字符集 Jaccard 相似度最高
               且 >= threshold 的候选（用于 value 被轻微改写过的场景）

        Args:
            value:      目标 value
            level:      限定演化层（如 PulseNode.EVOL_L3）；None 表示不限
            fuzzy:      是否允许模糊匹配；默认 False——宁可漏迁，不可误迁其他节点
            threshold:  模糊匹配阈值（字符集 Jaccard），默认 0.90

        Returns:
            命中的 PulseNode；未命中返回 None
        """
        _target = str(value or "").strip()
        if not _target:
            return None
        with self._lock:
            _target_chars = set(_target)
            _best: PulseNode | None = None
            _best_score = 0.0
            for _pool in (self._hot, self._warm, self._cold, self._instinct):
                for _n in _pool.values():
                    if level and getattr(_n, "evol_level", "") != level:
                        continue
                    _v = str(getattr(_n, "value", "") or "").strip()
                    if not _v:
                        continue
                    if _v == _target:
                        return _n  # 精确命中，直接返回（不容置疑）
                    if not fuzzy:
                        continue
                    _ch = set(_v)
                    if not _ch:
                        continue
                    _score = len(_target_chars & _ch) / max(1, len(_target_chars | _ch))
                    if _score >= threshold and _score > _best_score:
                        _best, _best_score = _n, _score
            return _best

    def update_node_path(self, node_id: str, new_space_path: str) -> bool:
        """迁移单个节点的 space_path（**不删节点**），并重建路径索引与共振索引。

        重建范围（星轨开工批准要求）：
            - 池内路径前缀索引 _path_index（旧前缀摘除 → 新前缀加入）
            - 层级索引 _level_index（经 _update_index_on_remove/add 同步）
            - 共振引擎 _space_index / _freq_index（旧路径条目移除 → 按新路径重建）
            - 外置索引 WAL（若启用，由上述两个 _update_index_on_* 自动 record_change）

        Args:
            node_id:        目标节点 id
            new_space_path: 目标路径

        Returns:
            True  幂等（路径已是目标值）或迁移成功
            False 节点不存在 / 参数非法（不抛异常，调用方降级为正常新建）
        """
        _new = str(new_space_path or "").strip() or "/"
        if not node_id:
            return False
        with self._lock:
            node = (self._hot.get(node_id) or self._warm.get(node_id)
                    or self._cold.get(node_id) or self._instinct.get(node_id))
            if node is None:
                return False
            _old = str(getattr(node, "space_path", "/") or "/")
            if _old == _new:
                return True  # 幂等：无需迁移
            # 1) 先按**旧路径**摘索引（必须早于写入新路径，否则摘的是新路径、旧条目泄漏）
            try:
                self._update_index_on_remove(node)
            except Exception as e:
                _module_logger.warning(f"[路径迁移] 旧索引摘除失败(已忽略): {e}")
            # 2) 写入新路径
            node.space_path = _new
            self._m71_influx_write("node_modified", node_id, "space_path", _old, _new)
            # 3) 共振引擎：移除旧路径条目 → 按新路径重建
            if self.resonance_engine is not None:
                try:
                    if hasattr(self.resonance_engine, "remove_node"):
                        self.resonance_engine.remove_node(node_id, space_path=_old)
                    self.resonance_engine.index_node(node.to_dict())
                except Exception as e:
                    _module_logger.warning(f"[路径迁移] 共振索引重建失败(已忽略): {e}")
            # 4) 按新路径重建池内索引 + 外置索引 WAL
            try:
                self._update_index_on_add(node)
            except Exception as e:
                _module_logger.warning(f"[路径迁移] 新索引构建失败(已忽略): {e}")
            self._dual_write_neo4j_node(node, "update")
            _module_logger.info(
                f"[种子记忆] 路径迁移: {_old}→{_new}, node_id={node_id}"
            )
            return True

    def get(self, node_id: str) -> PulseNode | None:
        """
        获取节点（同时激活）。

        在三个池中查找，找到后自动调用 activate()。
        第69批 T1: 增加访问频率跟踪 + LRU + 升降级判定。
        """
        _m70_pending_recall = None  # ★第81批补 T1：锁外冷召回待回填节点
        with self._lock:
            # ★T1: 访问频率跟踪
            if self._hot_cold_enabled:
                self._access_count[node_id] = self._access_count.get(node_id, 0) + 1

            node = (
                self._hot.get(node_id) or
                self._warm.get(node_id) or
                self._cold.get(node_id)
            )

            # ★T1: 热节点命中 → 无操作（已在最高层）
            # ★T1: 温节点命中 → LRU 移到末尾
            if node is not None and node_id in self._warm_lru:
                self._warm_lru.move_to_end(node_id)
                self._cache_stats["warm_hits"] += 1
            elif node is not None and node_id not in self._hot and node_id not in self._warm:
                self._cache_stats["warm_misses"] += 1
            
            # ★阶段B'：内存未命中但可能是被驱逐的冷节点 → 磁盘召回
            if node is None and self._cold_storage_enabled and node_id in self._cold_evicted:
                node = self._recall_cold_node(node_id)
                if node is not None:
                    # 召回后提升温池（复用现有激活→提升语义）
                    self._warm[node_id] = node
            
            if node:
                # ★第81批补 T1：懒加载回填（锁安全：锁内仅做内存 _m70_keep 快速回填，零 IO）
                if getattr(node, "_m70_blanked", False):
                    _keep = getattr(node, "_m70_keep", None)
                    if isinstance(_keep, dict) and ("value" in _keep or "linked_nodes" in _keep):
                        if "value" in _keep:
                            try:
                                node.value = _keep["value"]
                            except Exception:
                                pass
                        if "linked_nodes" in _keep:
                            try:
                                node.linked_nodes = _keep["linked_nodes"]
                            except Exception:
                                pass
                        node._m70_blanked = False
                        try:
                            self._cold_evicted.discard(getattr(node, "node_id", ""))
                        except Exception:
                            pass
                    else:
                        # 内存无 keep → 记录 id，锁外冷召回（不在持锁状态做 IO）
                        _m70_pending_recall = node
                node.activate()
                self._total_activated += 1
                self._m71_influx_write("node_activated", node_id,
                                       str(getattr(node, "evol_level", "") or ""))
                if self._m71_influx_sample():
                    self._m71_influx_write("node_accessed", node_id, "get")
                
                # ★v23.0新增 / ★A-16修复：通知赫布学习器，并把学到的连接权重
                #   回写到节点，供共振引擎利用。
                #   A-16 变更：改用 sync_hebbian_weight（EMA 可升可降），
                #   修复原 `max(旧值,新值)` 只增不减导致衰减失效的问题。
                if self._hebbian_learner is not None and self._hebbian_learner.is_enabled():
                    try:
                        self._hebbian_learner.on_node_activated(node_id)
                        self.sync_hebbian_weight(node_id, node)
                    except Exception as e:
                        self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                
                # 冷池节点被访问时，提升到温池
                if node_id in self._cold:
                    self._promote_to_warm(node)

                # ★T1: 访问频率驱动的自动升降级
                if self._hot_cold_enabled:
                    _count = self._access_count.get(node_id, 0)
                    # L2→L1 升级：温池节点访问次数超阈值 → 升级到热池
                    if _count >= self._promotion_threshold and node_id in self._warm:
                        self._promote_to_hot(node)
                    # L1→L2 降级：热池节点长期低频 → 降级到温池
                    elif _count <= self._demotion_threshold and node_id in self._hot and _count > 0:
                        self._demote_to_warm(node)

        # ===== 锁外：冷召回回填（IO 不在持锁状态，避免全局锁队头阻塞）=====
        if _m70_pending_recall is not None and getattr(_m70_pending_recall, "_m70_blanked", False):
            try:
                self._m70_materialize_lazy_node(_m70_pending_recall)
            except Exception as _e:
                self._log(LogLevel.ERROR,
                          f"[第81批补 T1] get() 冷召回回填失败 node_id="
                          f"{getattr(_m70_pending_recall, 'node_id', '')}: "
                          f"{type(_e).__name__}: {_e}")
        return node
    
    def remove(self, node_id: str) -> bool:
        """
        移除节点。
        
        从所有池中删除，并同步移除共振引擎索引。
        阶段B'：若节点已被驱逐（在 _cold_evicted），一并清理驱逐登记。
        """
        with self._lock:
            for pool in [self._hot, self._warm, self._cold]:
                if node_id in pool:
                    node = pool.pop(node_id)
                    
                    # 同步移除共振引擎索引
                    if self.resonance_engine:
                        self.resonance_engine.remove_node(
                            node_id,
                            freq=node.frequency_signature,
                            space_path=node.space_path
                        )
                    
                    # ★P3-1新增：从分层索引和路径索引中移除
                    self._update_index_on_remove(node)
                    
                    self._dual_write_neo4j_node(node, "remove")

                    # ★第102批 T-102b：删节点级联移除向量（防孤儿向量 D161）
                    #   孤儿向量根因：VectorStore.remove 生产零调用点，
                    #   节点被淘汰/删除后其向量永久残留在 vectors.npz。
                    try:
                        import config as _cfg102
                        if bool(getattr(_cfg102, 'ENABLE_M102_VECTOR_CASCADE_REMOVE', True)):
                            from nucleus.semantic.VectorStore import get_vector_store
                            _vs = get_vector_store()
                            if _vs is not None:
                                _vs.remove(node_id)
                    except Exception as _e102:
                        self._log(LogLevel.DEBUG,
                                  f"[第102批 T-102b] 向量级联移除失败(已忽略): "
                                  f"{type(_e102).__name__}: {_e102}")
                    self._total_removed += 1
                    return True
            # ★阶段B'：节点可能已被驱逐（不在内存池，但在 _cold_evicted）
            if self._cold_storage_enabled and node_id in self._cold_evicted:
                self._cold_evicted.discard(node_id)
                self._total_removed += 1
                return True
        return False
    
    # ========== 主线第71批 T1：Neo4j 双写（只写不读，灰度关闭） ==========
    def _m71_neo4j_store(self):
        """惰性获取 Neo4j 单例；不可用时返回 None（不影响主流程）。"""
        try:
            from nucleus.graph_store.neo4j_store import get_neo4j_store
            return get_neo4j_store()
        except Exception:
            return None

    def _m71_dw_enabled(self) -> bool:
        try:
            import config
            return bool(getattr(config, "ENABLE_NEO4J_GRAPH_STORE", False)
                       and getattr(config, "ENABLE_NEO4J_DUAL_WRITE", False))
        except Exception:
            return False

    def _m71_dw_stats(self) -> dict:
        if not hasattr(self, "_dw_stats"):
            self._dw_stats = {"node_success": 0, "node_fail": 0,
                             "rel_success": 0, "rel_fail": 0, "last_error": ""}
        return self._dw_stats

    def _dual_write_neo4j_node(self, node, op: str) -> None:
        """双写节点到 Neo4j（只写不读）。失败仅记日志，不抛异常。"""
        if not self._m71_dw_enabled():
            return
        _s = self._m71_dw_stats()
        _store = self._m71_neo4j_store()
        if _store is None or not _store.is_available():
            return
        try:
            _nid = getattr(node, "node_id", None)
            if not _nid:
                return
            if op == "remove":
                _store.remove_node(_nid)
            else:
                _props = {
                    "evol_level": str(getattr(node, "evol_level", "") or ""),
                    "space_path": str(getattr(node, "space_path", "") or "/"),
                    "trust_score": float(getattr(node, "trust_score", 0.0) or 0.0),
                    "importance": str(getattr(node, "importance", "") or ""),
                    "keywords": str(getattr(node, "keywords", "") or ""),
                }
                _store.add_node(_nid, _props)
            _s["node_success"] += 1
        except Exception as _e:
            _s["node_fail"] += 1
            _s["last_error"] = "%s: %s" % (type(_e).__name__, _e)
            _module_logger.debug("[T1] Neo4j 节点双写失败(%s): %s", op, _s["last_error"])

    def _dual_write_neo4j_relationship(self, from_id, to_id, rel_type, op) -> None:
        """双写关系到 Neo4j（只写不读）。"""
        if not self._m71_dw_enabled():
            return
        _s = self._m71_dw_stats()
        _store = self._m71_neo4j_store()
        if _store is None or not _store.is_available():
            return
        try:
            if op == "remove":
                _store.remove_relationship(from_id, to_id, rel_type)
            else:
                _store.add_relationship(from_id, to_id, rel_type)
            _s["rel_success"] += 1
        except Exception as _e:
            _s["rel_fail"] += 1
            _s["last_error"] = "%s: %s" % (type(_e).__name__, _e)
            _module_logger.debug("[T1] Neo4j 关系双写失败(%s): %s", op, _s["last_error"])

    def add_relationship(self, from_id, to_id, rel_type, properties=None) -> bool:
        """新增关联关系（同时双写到 Neo4j，仅当双写开启）。"""
        if not from_id or not to_id:
            return False
        self._dual_write_neo4j_relationship(from_id, to_id, rel_type, "add")
        return True

    def remove_relationship(self, from_id, to_id, rel_type) -> bool:
        """移除关联关系（同时双写到 Neo4j，仅当双写开启）。"""
        if not from_id or not to_id:
            return False
        self._dual_write_neo4j_relationship(from_id, to_id, rel_type, "remove")
        return True

    def sync_node_relationships(self, node_id) -> int:
        """按节点 linked_nodes 全量同步关系到 Neo4j（返回同步关系数）。"""
        if not self._m71_dw_enabled():
            return 0
        _node = self.get(node_id)
        if _node is None:
            return 0
        _links = getattr(_node, "linked_nodes", None) or []
        _synced = 0
        for _t in _links:
            _tid = _t if isinstance(_t, str) else str(getattr(_t, "node_id", "") or "")
            if _tid:
                self._dual_write_neo4j_relationship(node_id, _tid, "RELATED", "add")
                _synced += 1
        return _synced

    def get_dual_write_stats(self) -> dict:
        """返回 Neo4j 双写统计（成功/失败计数、开关状态、最后错误）。"""
        _s = self._m71_dw_stats()
        return {"enabled": self._m71_dw_enabled(),
                "node_success": _s["node_success"],
                "node_fail": _s["node_fail"],
                "rel_success": _s["rel_success"],
                "rel_fail": _s["rel_fail"],
                "last_error": _s["last_error"]}

    def check_consistency(self, node_id) -> dict:
        """检查单节点在池与 Neo4j 间的一致性（双写开启时有效）。"""
        _out = {"node_id": node_id, "in_pool": False, "in_neo4j": False,
                "consistent": False, "detail": ""}
        with self._lock:
            _n = (self._hot.get(node_id) or self._warm.get(node_id)
                  or self._cold.get(node_id))
        _out["in_pool"] = _n is not None
        if not self._m71_dw_enabled():
            _out["detail"] = "双写未开启，跳过 Neo4j 比对"
            return _out
        _store = self._m71_neo4j_store()
        if _store is None or not _store.is_available():
            _out["detail"] = "Neo4j 不可用"
            return _out
        try:
            _neo = _store.get_node(node_id)
            _out["in_neo4j"] = _neo is not None
            if _n is not None and _neo is not None:
                _mism = []
                for _k in ("evol_level", "space_path", "trust_score"):
                    _pv = str(getattr(_n, _k, "") or "")
                    _nv = str(_neo.get(_k, "") if isinstance(_neo, dict) else "")
                    if _pv != _nv:
                        _mism.append(_k)
                _out["mismatch_fields"] = _mism
                _out["consistent"] = len(_mism) == 0
                _out["detail"] = "字段一致" if not _mism else "字段不一致: " + ",".join(_mism)
            elif _n is None and _neo is None:
                _out["consistent"] = True
                _out["detail"] = "两边均不存在"
            else:
                _out["consistent"] = False
                _out["detail"] = "单边存在"
        except Exception as _e:
            _out["detail"] = "比对异常: %s: %s" % (type(_e).__name__, _e)
        return _out

    def full_consistency_check(self) -> dict:
        """全量一致性检查（采样池内热/温节点）。返回统计与样本。"""
        if not self._m71_dw_enabled():
            return {"enabled": False, "note": "双写未开启，跳过全量一致性检查"}
        _store = self._m71_neo4j_store()
        if _store is None or not _store.is_available():
            return {"enabled": True, "available": False, "note": "Neo4j 不可用"}
        _all = self.snapshot_active_nodes()
        _checked = _mismatch = 0
        _samples = []
        for _n in _all:
            _r = self.check_consistency(getattr(_n, "node_id", ""))
            _checked += 1
            if not _r.get("consistent", False):
                _mismatch += 1
                if len(_samples) < 20:
                    _samples.append(_r)
        return {"enabled": True, "checked": _checked, "mismatch": _mismatch,
                "samples": _samples}

    # [M71-T1-NODEPOOL]

    # ========== 主线第72批 T4：Neo4j 双读（双写→双读过渡期） ==========
    def _m72_neo4j_read_enabled(self) -> bool:
        """Neo4j 双读是否生效（三开关同开）。"""
        try:
            import config
            return bool(getattr(config, "ENABLE_NEO4J_GRAPH_STORE", False)
                       and getattr(config, "ENABLE_NEO4J_DUAL_WRITE", False)
                       and getattr(config, "ENABLE_NEO4J_READ", False))
        except Exception:
            return False

    def _m72_read_stats(self) -> dict:
        if not hasattr(self, "_read_stats"):
            self._read_stats = {"neo4j_hit": 0, "pool_fallback": 0,
                                "fail": 0, "last_error": ""}
        return self._read_stats

    def _m73_compare_stats(self) -> dict:
        """[M73-T4] 双读一致性比对统计（懒初始化）。"""
        if not hasattr(self, "_read_compare_stats"):
            self._read_compare_stats = {
                "sampled": 0,            # 已抽样比对次数
                "mismatch": 0,           # 不一致次数
                "mismatch_rate": 0.0,    # 不一致率
                "inconsistent_nodes": [],  # 不一致节点 ID（最多保留 20）
                "force_fallback": False,  # 不一致率超阈值后自动回退标记
                "last_warn": "",
            }
        return self._read_compare_stats

    def _m73_compare_rate(self) -> float:
        try:
            import config
            return float(getattr(config, "NEO4J_READ_COMPARE_RATE", 0.1))
        except Exception:
            return 0.1

    def _m73_compare_thresholds(self):
        try:
            import config
            return (float(getattr(config, "NEO4J_READ_COMPARE_WARN_THRESHOLD", 0.05)),
                    float(getattr(config, "NEO4J_READ_COMPARE_FALLBACK_THRESHOLD", 0.10)),
                    int(getattr(config, "NEO4J_READ_COMPARE_MIN_SAMPLES", 10)))
        except Exception:
            return (0.05, 0.10, 10)

    def get_relationships_neo4j(self, node_id, direction="both"):
        """查询节点关联关系：优先 Neo4j，不可用时回退节点内 linked_nodes。

        返回 list[dict]，每条含 from/to/rel_type（与 Neo4jStore.get_relationships 对齐）。
        [M73-T4] 双读开启且满足抽样比例时，同时比对节点内关联一致性，更新比对统计；
        不一致率超回退阈值后自动回退节点内查询（force_fallback）。
        """
        if not node_id:
            return []
        # [M73-T4] 自动回退：不一致率超阈值后跳过 Neo4j
        _cs = self._m73_compare_stats()
        if self._m72_neo4j_read_enabled() and not _cs["force_fallback"]:
            _store = self._m71_neo4j_store()
            if _store is not None and _store.is_available():
                try:
                    _rels = _store.get_relationships(node_id, direction)
                    self._m72_read_stats()["neo4j_hit"] += 1
                    self._m73_maybe_compare(node_id, _rels)
                    return _rels
                except Exception as _e:
                    self._m72_read_stats()["fail"] += 1
                    self._m72_read_stats()["last_error"] = "%s: %s" % (type(_e).__name__, _e)
                    _module_logger.debug("[T4] Neo4j 关系查询失败回退节点内: %s", _e)
        # 回退：节点内 linked_nodes（出边/双向）
        self._m72_read_stats()["pool_fallback"] += 1
        _node = self.get(node_id)
        if _node is None:
            return []
        _out = []
        for _lid in (getattr(_node, "linked_nodes", None) or []):
            _tid = _lid if isinstance(_lid, str) else str(getattr(_lid, "node_id", "") or "")
            if not _tid or _tid == node_id:
                continue
            if direction == "in":
                continue  # 入边需全池遍历，代价高；生产建议走 Neo4j
            _out.append({"from": node_id, "to": _tid, "rel_type": "RELATED"})
        return _out

    def _m73_maybe_compare(self, node_id, neo4j_rels):
        """[M73-T4] 按抽样比例比对 Neo4j 与节点内关联一致性。"""
        import random
        if random.random() >= self._m73_compare_rate():
            return
        _cs = self._m73_compare_stats()
        _cs["sampled"] += 1
        # Neo4j 侧目标节点集合（双向，排除自身）
        _neo_targets = set()
        for _r in (neo4j_rels or []):
            for _k in ("from", "to"):
                _t = _r.get(_k)
                if _t and _t != node_id:
                    _neo_targets.add(str(_t))
        # 节点内 linked_nodes 目标集合
        _node = self.get(node_id)
        _pool_targets = set()
        if _node is not None:
            for _lid in (getattr(_node, "linked_nodes", None) or []):
                _t = _lid if isinstance(_lid, str) else str(getattr(_lid, "node_id", "") or "")
                if _t and _t != node_id:
                    _pool_targets.add(_t)
        _mismatch = (_neo_targets != _pool_targets)
        if _mismatch:
            _cs["mismatch"] += 1
            if len(_cs["inconsistent_nodes"]) < 20:
                _cs["inconsistent_nodes"].append(node_id)
        _cs["mismatch_rate"] = (_cs["mismatch"] / _cs["sampled"]) if _cs["sampled"] else 0.0
        _warn, _fallback, _min_samples = self._m73_compare_thresholds()
        # 样本不足时不判定（避免单样本误触发回退）
        if _cs["sampled"] < _min_samples:
            return
        if _cs["mismatch_rate"] > _fallback:
            if not _cs["force_fallback"]:
                _cs["force_fallback"] = True
                _cs["last_warn"] = "不一致率 %.2f 超回退阈值 %.2f，已自动回退节点内查询" % (
                    _cs["mismatch_rate"], _fallback)
                _module_logger.warning("[M73-T4] %s", _cs["last_warn"])
        elif _cs["mismatch_rate"] > _warn:
            _cs["last_warn"] = "不一致率 %.2f 超告警阈值 %.2f" % (_cs["mismatch_rate"], _warn)
            _module_logger.warning("[M73-T4] %s", _cs["last_warn"])

    def get_read_compare_stats(self) -> dict:
        """[M73-T4] 返回双读一致性比对统计。"""
        _cs = self._m73_compare_stats()
        return {"enabled": self._m72_neo4j_read_enabled(),
                "sampled": _cs["sampled"],
                "mismatch": _cs["mismatch"],
                "mismatch_rate": _cs["mismatch_rate"],
                "force_fallback": _cs["force_fallback"],
                "inconsistent_nodes": list(_cs["inconsistent_nodes"]),
                "last_warn": _cs["last_warn"]}

    def reset_read_compare(self) -> None:
        """[M73-T4] 重置比对统计与自动回退标记。"""
        if hasattr(self, "_read_compare_stats"):
            del self._read_compare_stats

    def repair_inconsistent_node(self, node_id: str) -> int:
        """[M73-T4] 从节点内重新同步关联到 Neo4j（修复 Neo4j 侧）。

        返回重新同步的关系数；Neo4j 不可用或节点不存在返回 0。
        """
        if not node_id:
            return 0
        _store = self._m71_neo4j_store()
        if _store is None or not _store.is_available():
            return 0
        _node = self.get(node_id)
        if _node is None:
            return 0
        _synced = 0
        try:
            _props = {}
            for _attr in ("value", "evol_level", "trust_score", "node_id", "access_count"):
                _v = getattr(_node, _attr, None)
                if _v is not None:
                    _props[_attr] = _v
            _store.add_node(node_id, _props)
            for _lid in (getattr(_node, "linked_nodes", None) or []):
                _tid = _lid if isinstance(_lid, str) else str(getattr(_lid, "node_id", "") or "")
                if _tid and _tid != node_id:
                    _store.add_relationship(node_id, _tid, "RELATED")
                    _synced += 1
            # 修复后清除该节点不一致记录
            _cs = self._m73_compare_stats()
            _cs["inconsistent_nodes"] = [n for n in _cs["inconsistent_nodes"] if n != node_id]
        except Exception as _e:
            _module_logger.debug("[M73-T4] repair_inconsistent_node 失败: %s: %s",
                                 type(_e).__name__, _e)
        return _synced

    def get_read_stats(self) -> dict:
        """返回 Neo4j 双读统计（命中/回退/失败计数、开关状态）。"""
        _s = self._m72_read_stats()
        return {"enabled": self._m72_neo4j_read_enabled(),
                "neo4j_hit": _s["neo4j_hit"],
                "pool_fallback": _s["pool_fallback"],
                "fail": _s["fail"],
                "last_error": _s["last_error"]}

    # [M72-T4-NODEPOOL]

    # ========== 主线第71批 T2：InfluxDB 只写（节点激活/访问/修改，采样） ==========
    def _m71_influx_enabled(self) -> bool:
        try:
            import config
            return bool(getattr(config, "ENABLE_INFLUXDB_TIMESERIES", False)
                       and getattr(config, "ENABLE_INFLUXDB_WRITE_ONLY", False))
        except Exception:
            return False

    def _m71_influx_store(self):
        try:
            from nucleus.timeseries_store.influxdb_store import get_influxdb_store
            return get_influxdb_store()
        except Exception:
            return None

    def _m71_influx_write(self, method: str, *args) -> None:
        """写 InfluxDB（只写不读）。失败仅记日志，不抛异常。"""
        if not self._m71_influx_enabled():
            return
        _s = self._m71_influx_store()
        if _s is None or not _s.is_available():
            return
        try:
            getattr(_s, method)(*args)
        except Exception as _e:
            _module_logger.debug("[T2] InfluxDB 写入失败(%s): %s", method,
                                "%s: %s" % (type(_e).__name__, _e))

    def _m71_influx_sample(self) -> bool:
        """高频事件采样（INFLUXDB_SAMPLE_RATE）；<=0 不采，>=1 全采。"""
        try:
            import config
            _r = float(getattr(config, "INFLUXDB_SAMPLE_RATE", 0.0))
        except Exception:
            _r = 0.0
        if _r <= 0.0:
            return False
        if _r >= 1.0:
            return True
        import random
        return random.random() < _r

    # [M71-T2-NODEPOOL]

    def query(self, 
              evol_level: str | None = None,
              importance: str | None = None,
              source_organ: str | None = None,
              space_path_prefix: str | None = None,
              limit: int = 100) -> list[PulseNode]:
        """
        条件查询节点（★P3-1优化：使用分层索引+路径索引加速）。
        
        Args:
            evol_level: 过滤演化层级（L1/L2/L3）
            importance: 过滤重要性（S/A/B/C）
            source_organ: 过滤来源器官
            space_path_prefix: 过滤空间路径前缀
            limit: 返回上限
            
        Returns:
            匹配的节点列表
        """
        results = []
        
        # ★知识体系·检索优化：惰性迭代 + 提前终止，避免全量复制索引。
        # 上百亿规模下，此前 .copy() 会复制数十亿 ID（OOM/卡死），
        # 现在全程持锁惰性遍历，命中 limit 即停。
        with self._lock:
            def _matches(_node: PulseNode) -> bool:
                if importance and _node.importance != importance:
                    return False
                if source_organ and _node.source_organ != source_organ:
                    return False
                return not (space_path_prefix and not _node.space_path.startswith(space_path_prefix))
            
            def _append(_node: PulseNode) -> bool:
                results.append(_node)
                return len(results) >= limit
            
            def _scan(_ids):
                for _nid in _ids:
                    _node = (self._hot.get(_nid) or 
                             self._warm.get(_nid) or 
                             self._cold.get(_nid))
                    if _node is not None and _matches(_node):
                        if _append(_node):
                            return True
                return False
            
            if evol_level and evol_level in self._level_index:
                if space_path_prefix:
                    # P1-5修复(2026-09-03)：同时有层级和路径时取两个索引的交集，
                    # 避免在全量层级节点中扫描导致limit截断漏检（原bug：set无序+只扫层级索引）
                    _level_ids = self._level_index[evol_level]
                    _path_ids = self._path_index.get(space_path_prefix)
                    if _path_ids is None:
                        _path_ids = self._get_path_prefix_ids(space_path_prefix)
                    if _path_ids:
                        _scan(_level_ids & _path_ids)
                    else:
                        _scan(_level_ids)
                else:
                    _scan(self._level_index[evol_level])
            elif space_path_prefix:
                _ids = self._path_index.get(space_path_prefix)
                if _ids is not None:
                    _scan(_ids)
                else:
                    _merged = self._get_path_prefix_ids(space_path_prefix)
                    if _merged:
                        _scan(_merged)
            else:
                for _pool in (self._hot, self._warm, self._cold):
                    for _node in _pool.values():
                        if _matches(_node) and _append(_node):
                            break
                    if len(results) >= limit:
                        break
        
        return results
    
    def get_all(self) -> list[PulseNode]:
        """获取所有内存节点（热 + 温 + 冷缓存），**不含被驱逐的冷节点**。

        ★阶段B'硬约束：此方法保持「内存视图」语义不变，不隐性兜底磁盘冷存。
        需要「逻辑全量」的场景（快照保存、肾脏全量扫描）改调 get_all_including_evicted()。
        """
        with self._lock:
            return list(self._hot.values()) + list(self._warm.values()) + list(self._cold.values())

    def _m70_batch_materialize(self, nodes) -> list:
        """★第81批补 T3：批量回填 blanked 懒加载节点（覆盖 get_all 类批量消费方）。

        优先内存 _m70_keep（零 IO）；无 keep 的收集 id 后一次 recall_cold_nodes_batch 整读（禁 N+1），
        二次加锁写回。返回原 nodes 列表（原地回填，调用方拿到的引用即已回填）。
        """
        _cold_ids = []
        _by_id = {}
        for _n in nodes:
            if not getattr(_n, "_m70_blanked", False):
                continue
            _keep = getattr(_n, "_m70_keep", None)
            if isinstance(_keep, dict) and ("value" in _keep or "linked_nodes" in _keep):
                if "value" in _keep:
                    try:
                        _n.value = _keep["value"]
                    except Exception:
                        pass
                if "linked_nodes" in _keep:
                    try:
                        _n.linked_nodes = _keep["linked_nodes"]
                    except Exception:
                        pass
                _n._m70_blanked = False
            else:
                _nid = getattr(_n, "node_id", "")
                if _nid:
                    _cold_ids.append(_nid)
                    _by_id[_nid] = _n
        if _cold_ids and getattr(self, "_cold_storage_enabled", False):
            try:
                _recalled = self.recall_cold_nodes_batch(_cold_ids)  # 一次整读，锁外 IO
            except Exception as _e:
                self._log(LogLevel.ERROR,
                          f"[第81批补 T3] 批量冷召回失败: {type(_e).__name__}: {_e}")
                _recalled = []
            _rmap = {getattr(_r, "node_id", ""): _r for _r in _recalled}
            with self._lock:  # 二次加锁写回
                for _nid, _n in _by_id.items():
                    _rn = _rmap.get(_nid)
                    if _rn is None:
                        continue
                    try:
                        _n.value = getattr(_rn, "value", _n.value)
                    except Exception:
                        pass
                    try:
                        _n.linked_nodes = getattr(_rn, "linked_nodes", _n.linked_nodes)
                    except Exception:
                        pass
                    _n._m70_blanked = False
                    try:
                        self._cold_evicted.discard(_nid)
                    except Exception:
                        pass
        return nodes

    def get_all_including_evicted(self) -> list[PulseNode]:
        """★阶段B'：返回逻辑全量节点（内存节点 + 磁盘冷存兜底）。

        专供快照保存、肾脏全量遍历等需要完整数据集的场景。冷存储未启用时等价 get_all()。
        注意：此方法会触发磁盘 IO（召回被驱逐节点），仅限全量遍历场景调用，勿在热路径使用。
        ★第81批 T4：改走 recall_cold_nodes_batch 一次整读（O(N×全扫)→一次整读），
        由 COLD_BATCH_RECALL_ENABLED 灰度（关闭则回退逐节点循环）。
        """
        _mem = self._m70_batch_materialize(self.get_all())  # ★第81批补 T3：批量回填 blanked 节点
        if not self._cold_storage_enabled or not self._cold_evicted:
            return _mem
        if self._m81_cold_cfg("COLD_BATCH_RECALL_ENABLED", True):
            _evicted_nodes = self.recall_cold_nodes_batch(list(self._cold_evicted), consume=False)
            return _mem + _evicted_nodes
        # 回退（灰度关闭）：逐节点循环
        _evicted_nodes = []
        for _nid in list(self._cold_evicted):
            _node = self._recall_cold_node(_nid, consume=False)
            if _node is not None:
                _evicted_nodes.append(_node)
        return _mem + _evicted_nodes

    def count(self) -> int:
        """获取总节点数（= 内存节点 + 被驱逐节点数）。冷存储未启用时等价内存节点数。"""
        with self._lock:
            return len(self._hot) + len(self._warm) + len(self._cold) + (
                len(self._cold_evicted) if self._cold_storage_enabled else 0
            )
    
    # ========== 批量加载（快照恢复用） ==========
    
    def load_batch(self, nodes: list[PulseNode]):
        """
        批量加载节点（启动时从快照恢复）。
        
        跳过已存在的节点（幂等）。
        """
        with self._lock:
            for node in nodes:
                if node.node_id in self._hot or node.node_id in self._warm or node.node_id in self._cold:
                    continue  # 幂等：已存在则跳过
                
                target_pool = self._determine_pool(node)
                target_pool[node.node_id] = node
                
                if self.resonance_engine:
                    self.resonance_engine.index_node(node.to_dict())
                
                # ★v17.0修复：批量加载时同步更新分层索引和路径索引
                # 之前只有 add() 会更新索引，load_batch() 不会
                # 导致重启后从快照恢复的节点无法被 query() 检索到
                self._update_index_on_add(node)
                
                self._total_added += 1
    def load_batch_lazy(self, nodes: list[PulseNode]) -> dict[str, int]:
        """★阶段D：启动按需加载（冷存储启用时的替代 load_batch）。

        按 `_determine_pool` 分流：
        - 落热/温池的节点 → 进内存（复用 load_batch 逻辑）。
        - 落冷池的节点 → 直接写冷存 Parquet + 标记 evicted，不进内存。

        冷存储未启用时，行为等价 load_batch（零回归）。
        返回统计：{"in_memory": int, "in_cold_disk": int}。
        """
        if not self._cold_storage_enabled:
            self.load_batch(nodes)
            return {"in_memory": len(nodes), "in_cold_disk": 0}

        _in_memory = 0
        _in_cold = 0
        with self._lock:
            for node in nodes:
                _nid = node.node_id
                # 幂等：已在内存或已登记驱逐则跳过
                if _nid in self._hot or _nid in self._warm or _nid in self._cold or _nid in self._cold_evicted:
                    continue
                _target = self._determine_pool(node)
                if _target is self._cold:
                    # 冷节点：写盘 + 标记 evicted，不进内存
                    if self._write_cold_node_to_disk(node):
                        self._cold_evicted.add(_nid)
                        if self._index_store is not None:
                            self._index_store.record_change(
                                "add", "level_index",
                                {"evol_level": "L1", "node_id": _nid, "evicted": True}
                            )
                        _in_cold += 1
                    # 写盘失败则回退进内存，保证数据不丢
                    else:
                        _target[_nid] = node
                        self._update_index_on_add(node)
                        _in_memory += 1
                else:
                    # 热/温节点：进内存
                    _target[_nid] = node
                    if self.resonance_engine:
                        try:
                            self.resonance_engine.index_node(node.to_dict())
                        except Exception as e:
                            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
                    self._update_index_on_add(node)
                    _in_memory += 1
                self._total_added += 1
        return {"in_memory": _in_memory, "in_cold_disk": _in_cold}
    def load_instincts(self, nodes: list[PulseNode]):
        """加载本能节点到独立的本能池（启动时调用）"""
        with self._lock:
            for node in nodes:
                node.instinct = True
                self._instinct[node.node_id] = node    
    def get_instincts(self) -> list[PulseNode]:
        """获取所有本能节点（推理前置约束使用）"""
        with self._lock:
            return list(self._instinct.values())
        
    def upgrade_to_instinct(self, node: PulseNode) -> bool:
        """将一个L3节点升级为本能节点"""
        with self._lock:
            if len(self._instinct) >= 20:
                # 达到上限，淘汰最旧、最低频的旧本能
                oldest = min(self._instinct.values(),
                           key=lambda n: (n.instinct_active_times, n.instinct_at))
                self._downgrade_instinct(oldest)
            
            # 从原池中移除
            for pool in [self._hot, self._warm, self._cold]:
                if node.node_id in pool:
                    del pool[node.node_id]
                    break
            
            # 升级为本能
            node.instinct = True
            node.instinct_at = time.time()
            self._instinct[node.node_id] = node
            return True
    def upgrade_node_level(self, node_id: str, new_level: str) -> bool:
        """
        ★P1-2修复：升级节点的演化层级，同步更新分层索引。
        
        当节点从L1升级到L2，或从L2升级到L3时，分层索引需要同步迁移。
        
        Args:
            node_id: 节点ID
            new_level: 目标层级（L1/L2/L3）
        
        Returns:
            是否成功
        """
        with self._lock:
            # 在三个池中查找节点
            node = None
            source_pool = None
            for pool_name, pool in [("hot", self._hot), ("warm", self._warm), ("cold", self._cold)]:
                if node_id in pool:
                    node = pool[node_id]
                    source_pool = pool
                    break
            
            if node is None:
                return False
            
            old_level = node.evol_level
            if old_level == new_level:
                return True
            
            # 更新节点的演化层级
            node.evol_level = new_level
            
            # ★P1-2核心修复：同步迁移分层索引
            if old_level in self._level_index:
                self._level_index[old_level].discard(node_id)
            if new_level in self._level_index:
                self._level_index[new_level].add(node_id)
            
            # 如果升级到L3，移到热池
            if new_level == PulseNode.EVOL_L3 and source_pool is not self._hot:
                del source_pool[node_id]
                self._hot[node_id] = node
            
            return True    
    def _downgrade_instinct(self, node: PulseNode):
        """降级本能节点回L3"""
        node.instinct = False
        node.instinct_at = 0.0
        del self._instinct[node.node_id]
        self._hot[node.node_id] = node
    # ========== 淘汰机制 ==========
    
    def purge_obsolete(self, max_age_days: float = 30.0) -> int:
        """
        清理过期的节点。
        
        ★P1-1修复：区分L1和L2的淘汰策略
        - L1节点：直接物理删除
        - L2节点：降级为L1（演化降级），保留知识沉淀
        - L3节点：永不淘汰
        
        Args:
            max_age_days: L1节点的最大存活天数
            
        Returns:
            淘汰/降级的节点数
        """
        purged = 0
        
        with self._lock:
            # ★P1-1修复：先处理L2降级，再处理L1删除
            # 遍历所有池，找出过时的L2节点并降级
            for pool in [self._cold, self._warm, self._hot]:
                downgrade_ids = []
                
                for node_id, node in pool.items():
                    if node.evol_level == PulseNode.EVOL_L2 and node.is_obsolete(max_age_days):
                        downgrade_ids.append(node_id)
                
                for node_id in downgrade_ids:
                    node = pool[node_id]
                    # ★P1-1修复：L2节点不删除，降级为L1
                    node.evol_level = PulseNode.EVOL_L1
                    node.importance = PulseNode.IMPORTANCE_C
                    node.abstraction = max(0.0, node.abstraction - 0.2)
                    # 更新分层索引：从L2迁移到L1
                    if "L2" in self._level_index:
                        self._level_index["L2"].discard(node_id)
                    if "L1" in self._level_index:
                        self._level_index["L1"].add(node_id)
                    # 从当前池移到冷池（L1+低重要性）
                    if pool is not self._cold:
                        del pool[node_id]
                        self._cold[node_id] = node
                    purged += 1
            
            # 再处理L1的物理删除
            for pool in [self._cold, self._warm, self._hot]:
                obsolete_ids = []
                
                for node_id, node in pool.items():
                    if node.evol_level == PulseNode.EVOL_L1 and node.is_obsolete(max_age_days):
                        obsolete_ids.append(node_id)
                
                for node_id in obsolete_ids:
                    node = pool.pop(node_id)
                    if self.resonance_engine:
                        self.resonance_engine.remove_node(
                            node_id,
                            freq=node.frequency_signature,
                            space_path=node.space_path
                        )
                    # 清理分层索引和路径索引
                    self._update_index_on_remove(node)
                    purged += 1
                    self._total_removed += 1
        
        return purged
    def get_path_distribution(self) -> dict[str, int]: 
        """
        获取知识树各一级路径下的节点分布。
        用于自我认知评估自己的知识强项和盲区。
        """
        with self._lock:
            all_nodes = list(self._hot.values()) + list(self._warm.values()) + list(self._cold.values())
        
        path_dist = {}
        for node in all_nodes:
            path = getattr(node, 'space_path', '/')
            parts = path.strip('/').split('/')
            root_path = '/' + parts[0] if parts and parts[0] else '/'
            path_dist[root_path] = path_dist.get(root_path, 0) + 1
        
        return path_dist

    # ========== 统计信息 ==========
    
    def get_stats(self) -> dict[str, Any]:
        """获取节点池统计信息（肾脏淘汰决策依据）"""
        with self._lock:
            hot_count = len(self._hot)
            warm_count = len(self._warm)
            cold_count = len(self._cold)
            total = hot_count + warm_count + cold_count
            
            # 演化分布
            evol_dist = {"L1": 0, "L2": 0, "L3": 0}
            for node in list(self._hot.values()) + list(self._warm.values()) + list(self._cold.values()):
                evol_dist[node.evol_level] = evol_dist.get(node.evol_level, 0) + 1
            
        return {
            "total_nodes": total,
            "hot_count": hot_count,
            "warm_count": warm_count,
            "cold_count": cold_count,
            "total_added": self._total_added,
            "total_removed": self._total_removed,
            "total_activated": self._total_activated,
            "evol_distribution": evol_dist,
            "max_hot": self._max_hot,
            "max_warm": self._max_warm,
            "instinct_count": len(self._instinct),
        }

    # ========== ★登顶路线图-山 1 P1：记忆验证闭环 ==========

    def run_memory_verification(self, dormant_days: float = 14.0,
                                stale_days: float = 45.0,
                                reinforce_threshold: float = 0.55,
                                sample_limit: int = 2000) -> dict[str, Any]:
        """
        记忆验证闭环：周期性检验记忆是否仍有效，并给出强化/沉睡/清理的验证反馈。

        设计理念（对标“生命体记忆会自我校验”）：
        - 记忆不是静态存储，而是持续被验证的过程。
        - 高频激活的记忆→ 验证有效→ 强化（hebbian_weight 提升）。
        - 长期未激活但重要的记忆→ 标记 dormant（沉睡保留，不删除）。
        - 长期未激活且低价值的记忆→ 建议清理（交由 purge_obsolete）。

        活性评分（0~1）：
        score = 时间新鲜度(40%) + 激活频率(30%) + 演化层级(20%) + 重要性(10%)

        Args:
            dormant_days: 超过此天数未激活且重要→ 沉睡候选
            stale_days: 超过此天数未激活且低价值→ 清理候选
            reinforce_threshold: 活性评分超过此值→ 强化候选
            sample_limit: 最大评估节点数（防止大规模过时性能降级）

        Returns:
            {
              "total_evaluated": int,
              "active_count": int, "dormant_count": int, "stale_count": int,
              "reinforce_candidates": [{"node_id", "value", "score", "action"}],
              "dormant_candidates": [{"node_id", "value", "action"}],
              "cleanup_candidates": [{"node_id", "value", "action"}],
              "applied_reinforcements": int,
              "verified_at": float,
            }
        """
        import time as _t
        _now = _t.time()

        with self._lock:
            _all_nodes = list(self._hot.values()) + list(self._warm.values()) + list(self._cold.values())

        _total = len(_all_nodes)
        _sampled = _all_nodes[:sample_limit]

        _active, _dormant, _stale = 0, 0, 0
        _reinforce_cands: list[dict] = []
        _reinforce_nodes: list = []  # 缓存节点对象引用（避免在锁内调用 self.get 导致死锁）
        _dormant_cands: list[dict] = []
        _cleanup_cands: list[dict] = []
        _applied = 0

        _importance_w = {"S": 1.0, "A": 0.8, "B": 0.5, "C": 0.2}

        for _node in _sampled:
            try:
                _is_l3 = getattr(_node, "evol_level", "") == "L3"
                _is_instinct = bool(getattr(_node, "instinct", False))
                # L3 / 本能节点不参与沉睡/清理评估（永久保留）
                if _is_l3 or _is_instinct:
                    _active += 1
                    continue

                _last = getattr(_node, "last_activated", 0.0) or _now
                _age_days = (_now - _last) / 86400.0
                _act_count = max(0, int(getattr(_node, "activation_count", 0)))
                _hebbian = max(0.0, float(getattr(_node, "hebbian_weight", 0.0) or 0.0))
                _imp = getattr(_node, "importance", "C") or "C"
                _imp_w = _importance_w.get(_imp, 0.3)

                # 活性评分
                _freshness = 1.0 / (1.0 + _age_days * 0.1)          # 时间新鲜度
                _freq = min(1.0, _act_count / 30.0)                  # 激活频率
                _level_w = 0.6 if getattr(_node, "evol_level", "") == "L2" else 0.4
                _score = (0.40 * _freshness + 0.30 * _freq
                          + 0.20 * _level_w + 0.10 * _imp_w)

                _val = str(getattr(_node, "value", ""))[:50]

                if _age_days <= dormant_days:
                    _active += 1
                    if _score >= reinforce_threshold and _hebbian < 0.9:
                        _reinforce_cands.append({
                            "node_id": getattr(_node, "node_id", "?"),
                            "value": _val,
                            "score": round(_score, 3),
                            "action": "reinforce",
                        })
                        _reinforce_nodes.append(_node)
                elif _imp_w >= 0.5:
                    # 重要但沉睡→ 标记 dormant（不删）
                    _dormant += 1
                    if getattr(_node, "state", "") != "dormant":
                        _node.state = "dormant"
                        _dormant_cands.append({
                            "node_id": getattr(_node, "node_id", "?"),
                            "value": _val,
                            "action": "mark_dormant",
                        })
                elif _age_days > stale_days:
                    _stale += 1
                    _cleanup_cands.append({
                        "node_id": getattr(_node, "node_id", "?"),
                        "value": _val,
                        "action": "purge",
                    })
            except Exception:
                continue

        # 执行强化反馈（活跃记忆被验证有效 → 提升 hebbian_weight）
        # 注意：不能在锁内调用 self.get()（普通 Lock 不可重入，会死锁）。
        # 这里直接操作评估阶段缓存的节点对象引用。
        with self._lock:
            for _node in _reinforce_nodes[:200]:
                try:
                    _node.hebbian_weight = min(1.0, getattr(_node, "hebbian_weight", 0.0) + 0.05)
                    _applied += 1
                except Exception:
                    continue

        return {
            "total_evaluated": _total,
            "sampled": len(_sampled),
            "active_count": _active,
            "dormant_count": _dormant,
            "stale_count": _stale,
            "reinforce_candidates": _reinforce_cands,
            "dormant_candidates": _dormant_cands,
            "cleanup_candidates": _cleanup_cands,
            "applied_reinforcements": _applied,
            "verified_at": _now,
        }

    def start_memory_verification_loop(self, interval_hours: float = 6.0,
                                       stale_days: float = 45.0,
                                       initial_delay_hours: float = 0.0) -> bool:
        """★登顶路线图-山 1 P1：启动记忆验证闭环定时调度。

        ★主线第61批 T2/P1：新增 initial_delay_hours（首跑延迟，小时）。
        ★主线第62批 T1/P1（星轨裁决方案B）：语义修正为「推迟」——
          >0 时首次等 interval + 该时长（默认由 config 给 5~30 分钟），之后恢复每 interval 一轮；
          =0 时严格沿用旧行为（首次即等一个完整 interval 周期）——零回归。
          历史：第61批曾实现为「提前」（首次只等该时长），与错峰避峰原意相反，本批修正。

        以 daemon 线程周期性运行 run_memory_verification，
        并与 purge_obsolete 联动（清理过时低价值记忆）。
        不块坞主线程，异常自动跳过不影响主链路。
        """
        import threading as _th
        import time as _t

        if getattr(self, "_mem_verify_thread", None) and self._mem_verify_thread.is_alive():
            return False

        _running = {"flag": True}
        self._mem_verify_stop = _running

        def _loop():
            _log = getattr(self, "_mem_verify_logger", None)
            _interval = max(0.5, float(interval_hours)) * 3600.0
            # ★主线第62批 T1/P1（方案B）：首跑推迟量（秒）。>0 → 首次等 interval + 该时长；
            #   =0 → 首次即等一个完整周期（与第61批改造前完全一致）——零回归。
            try:
                _first_delay = max(0.0, float(initial_delay_hours)) * 3600.0
            except (TypeError, ValueError):
                _first_delay = 0.0
            # ★主线第62批 T1/P1（星轨裁决方案B）：修正语义为「推迟」
            #   旧行为（第61批）：_first_delay > 0 -> 首次只等 _first_delay（把首跑提前，
            #           与「错峰避峰」原意相反）
            #   新行为（本批）：_first_delay > 0 -> 首次等 interval + _first_delay（推迟首跑）
            #   _first_delay == 0 -> 首次等 interval（与第61批改造前完全一致，零回归）
            _sleep_s = _interval + _first_delay
            while _running["flag"]:
                _t.sleep(_sleep_s)
                _sleep_s = _interval
                try:
                    _report = self.run_memory_verification(stale_days=stale_days)
                    _stale_n = len(_report.get("cleanup_candidates", []))
                    _reinforce_n = _report.get("applied_reinforcements", 0)
                    if _stale_n > 0:
                        _purged = self.purge_obsolete(max_age_days=30.0)
                    else:
                        _purged = 0
                    if _log:
                        _log(f"记忆验证闭环: "
                             f"评估{_report.get('total_evaluated', 0)}节点, "
                             f"强化{_reinforce_n}, 沉睡{len(_report.get('dormant_candidates', []))}, "
                             f"清理候选{_stale_n}, 实际淘汰{_purged}")
                except Exception:
                    continue

        _thr = _th.Thread(target=_loop, name="MemoryVerifyLoop", daemon=True)
        self._mem_verify_thread = _thr
        _thr.start()
        return True

    def stop_memory_verification_loop(self) -> None:
        """停止记忆验证定时调度。"""
        _stop = getattr(self, "_mem_verify_stop", None)
        if _stop:
            _stop["flag"] = False

    # ========== 内部方法 ==========
    
    def _determine_pool(self, node: PulseNode) -> dict[str, PulseNode]:
        """
        根据节点属性确定目标池。
        
        规则:
            - L3 → 热池（无条件）
            - L2 + S/A → 热池 
            - L2 + B/C → 温池
            - L1 + S/A → 温池
            - L1 + B/C → 冷池
        """
        if node.evol_level == PulseNode.EVOL_L3:
            return self._hot
        
        if node.evol_level == PulseNode.EVOL_L2:
            if node.importance in (PulseNode.IMPORTANCE_S, PulseNode.IMPORTANCE_A):
                return self._hot
            return self._warm
        
        # L1
        if node.importance in (PulseNode.IMPORTANCE_S, PulseNode.IMPORTANCE_A):
            return self._warm
        return self._cold

    def _enforce_capacity(self):
        """★知识体系·容量治理：热/温池超限时逐级降级（L3 永不降级，保证生命线知识常驻）。"""
        # 热池超限：把最久未激活的非 L3 节点降级到温池
        while len(self._hot) > self._max_hot:
            _victim = None
            for _node in self._hot.values():
                if _node.evol_level != PulseNode.EVOL_L3:
                    if _victim is None or _node.last_activated < _victim.last_activated:
                        _victim = _node
            if _victim is None:
                break  # 全是 L3，无法降级
            del self._hot[_victim.node_id]
            self._warm[_victim.node_id] = _victim

        # 温池超限：把最久未激活节点降级到冷池
        while len(self._warm) > self._max_warm:
            _victim = min(self._warm.values(), key=lambda n: n.last_activated)
            del self._warm[_victim.node_id]
            # ★阶段D：冷存储启用时，温池超限的 L1 节点直接驱逐到磁盘冷存（而非先进 _cold 再等超限），
            # 形成「温层按需 → 冷层归档召回」的完整换入换出闭环。
            if (self._cold_storage_enabled
                    and _victim.evol_level == PulseNode.EVOL_L1
                    and self._write_cold_node_to_disk(_victim)):
                self._cold_evicted.add(_victim.node_id)
                if self._index_store is not None:
                    self._index_store.record_change(
                        "add", "level_index",
                        {"evol_level": "L1", "node_id": _victim.node_id, "evicted": True}
                    )
            else:
                self._cold[_victim.node_id] = _victim

    def _enforce_cold_cache(self):
        # ★T3: 自适应降频接线——冷存compaction
        try:
            from nucleus.runtime_metrics import get_adaptive_controller
            _ctrl = get_adaptive_controller()
            _ctrl.register("cold_compaction", 600)
            if not _ctrl.should_execute("cold_compaction"):
                return
        except Exception:
            pass
        """★阶段B'：冷池超限时驱逐最久未激活节点到磁盘（LRU）。

        仅当冷存储启用时调用。驱逐直到冷池 ≤ _max_cold_cache。
        """
        while self._cold_storage_enabled and len(self._cold) > self._max_cold_cache:
            _victim = min(self._cold.values(), key=lambda n: n.last_activated)
            if not self._evict_cold_node(_victim):
                # 驱逐失败（如 pyarrow 缺失），停止避免死循环
                break
    # ========== ★P3-1新增：分层索引维护方法 ==========
    
    @staticmethod
    def _iter_path_prefixes(space_path: str) -> list[str]:
        """
        ★知识体系·路径索引优化：生成路径的所有前缀（含根路径与全路径）。

        例：/技术/编程/Python → ['/', '/技术', '/技术/编程', '/技术/编程/Python']
        使深层路径查询能精确命中索引，而非全量扫描根路径（上百亿规模下关键）。
        """
        _parts = [p for p in space_path.split('/') if p]
        if not _parts:
            return ['/']
        _prefixes = ['/']
        _cur = ''
        for _p in _parts:
            _cur += '/' + _p
            _prefixes.append(_cur)
        return _prefixes
    
    def _update_index_on_add(self, node: PulseNode):
        """
        添加节点时更新分层索引和全路径前缀索引。
        """
        _level = node.evol_level
        if _level in self._level_index:
            self._level_index[_level].add(node.node_id)
        
        _path = getattr(node, 'space_path', '/')
        for _prefix in self._iter_path_prefixes(_path):
            _ids = self._path_index.get(_prefix)
            if _ids is None:
                _ids = set()
                self._path_index[_prefix] = _ids
            _ids.add(node.node_id)
        # 新增节点可能携带语义关系，标记横向索引待重建
        self._semantic_index_valid = False
        # ★阶段C'：外置索引增量 WAL（开关开启时追加变更，不立即写盘）
        if self._index_store is not None:
            self._index_store.record_change(
                "add", "level_index", {"evol_level": _level, "node_id": node.node_id, "evicted": False}
            )
            for _prefix in self._iter_path_prefixes(_path):
                self._index_store.record_change(
                    "add", "path_index", {"path_prefix": _prefix, "node_id": node.node_id}
                )
    
    def _update_index_on_remove(self, node: PulseNode):
        """
        移除节点时清理分层索引和全路径前缀索引。
        """
        _level = node.evol_level
        if _level in self._level_index:
            self._level_index[_level].discard(node.node_id)
        
        _path = getattr(node, 'space_path', '/')
        for _prefix in self._iter_path_prefixes(_path):
            _ids = self._path_index.get(_prefix)
            if _ids is not None:
                _ids.discard(node.node_id)
                if len(_ids) == 0:
                    del self._path_index[_prefix]
        # 移除节点后横向索引失效
        self._semantic_index_valid = False
        # ★阶段C'：外置索引增量 WAL
        if self._index_store is not None:
            self._index_store.record_change(
                "remove", "level_index", {"evol_level": _level, "node_id": node.node_id, "evicted": False}
            )
            for _prefix in self._iter_path_prefixes(_path):
                self._index_store.record_change(
                    "remove", "path_index", {"path_prefix": _prefix, "node_id": node.node_id}
                )
    
    def _get_path_prefix_ids(self, space_path_prefix: str) -> set | None:
        """
        根据路径前缀从全路径索引中获取候选节点ID集合（支持父路径/子路径模糊匹配）。
        """
        _result = set()
        _prefix = space_path_prefix.rstrip('/')
        for _path_key, _ids in self._path_index.items():
            if _path_key.startswith(_prefix) or _prefix.startswith(_path_key):
                _result.update(_ids)
        return _result or None

    # ========== 横向联系（语义关系 / 关联节点）索引与查询 ==========

    def _ensure_semantic_index(self):
        """
        懒构建横向语义索引（反向邻接：target_node_id → {(source_id, relation_type)}）。

        语义关系由肝脏在压缩/融合时写入节点（add_semantic_relation），
        数量稀疏且集中在 L2/L3，故按需重建成本可控。脏标记在增删节点时置位。
        """
        if self._semantic_index_valid:
            return
        _new_index: dict[str, set] = {}
        with self._lock:
            for _pool in (self._hot, self._warm, self._cold):
                for _node in _pool.values():
                    for _rel in (getattr(_node, 'semantic_relations', None) or []):
                        if not isinstance(_rel, dict):
                            continue
                        _tid = _rel.get('target_node_id', '')
                        _rtype = _rel.get('relation_type', '')
                        if _tid:
                            _new_index.setdefault(_tid, set()).add((_node.node_id, _rtype))
                    for _lid in (getattr(_node, 'linked_nodes', None) or []):
                        if _lid:
                            _new_index.setdefault(_lid, set()).add((_node.node_id, 'linked'))
        self._semantic_index = _new_index
        self._semantic_index_valid = True

    def invalidate_semantic_index(self):
        """★公开接口：外部在批量写入语义关系后调用，强制下次查询重建横向索引。"""
        self._semantic_index_valid = False

    def get_related_nodes(self, node_id: str, relation_type: str | None = None,
                          limit: int = 10) -> list["PulseNode"]:
        """
        ★横向联系查询：返回与指定节点存在语义关系/关联的节点列表。

        双向遍历：
          - 前向：读取 node_id 自身的 semantic_relations / linked_nodes
          - 反向：从横向索引读取「谁指向了 node_id」

        Args:
            node_id: 源节点ID
            relation_type: 关系类型过滤（causal/analogy/hierarchy/cooccurrence/linked）
            limit: 返回上限
        """
        self._ensure_semantic_index()
        _seen: set[str] = set()
        _result: list[PulseNode] = []

        def _resolve(_nid: str) -> PulseNode | None:
            return (self._hot.get(_nid) or self._warm.get(_nid)
                    or self._cold.get(_nid) or self._instinct.get(_nid))

        def _append(_nid: str) -> bool:
            if _nid in _seen:
                return False
            _target = _resolve(_nid)
            if _target is None:
                return False
            _seen.add(_nid)
            _result.append(_target)
            return len(_result) >= limit

        _source = _resolve(node_id)
        if _source is not None:
            # 前向：自身语义关系
            for _rel in (getattr(_source, 'semantic_relations', None) or []):
                if not isinstance(_rel, dict):
                    continue
                _rtype = _rel.get('relation_type', '')
                if relation_type and _rtype != relation_type:
                    continue
                if _append(_rel.get('target_node_id', '')):
                    return _result
            # 前向：自身关联节点
            if relation_type in (None, 'linked'):
                for _lid in (getattr(_source, 'linked_nodes', None) or []):
                    if _append(_lid):
                        return _result

        # 反向：谁指向了 node_id
        with self._lock:
            _incoming = list(self._semantic_index.get(node_id, set()))
        for _sid, _rtype in _incoming:
            if relation_type and _rtype != relation_type:
                continue
            if _sid != node_id and _append(_sid):
                return _result

        return _result

    def get_semantic_relation_stats(self) -> dict[str, int]:
        """★横向联系统计：各关系类型的边数（用于观测横向联系覆盖度）。"""
        self._ensure_semantic_index()
        _stats: dict[str, int] = {}
        with self._lock:
            for _incoming in self._semantic_index.values():
                for _sid, _rtype in _incoming:
                    _stats[_rtype] = _stats.get(_rtype, 0) + 1
        return _stats

    def _promote_to_warm(self, node: PulseNode):
        """
        将冷池节点提升到温池。
        
        冷池节点被访问时自动调用。
        """
        node_id = node.node_id
        if node_id in self._cold:
            del self._cold[node_id]
            self._warm[node_id] = node

    # ========== ★阶段B'：L1 冷存储分治（驱逐 + 召回） ==========

    def _cold_parquet_dir(self) -> str:
        """★第81批 T4：返回冷存 Parquet 目录（flat，evol_level 作为数据列存储，不再按分区落子目录）。

        历史实现硬编码 evol_level=L1 分区，导致冷节点无论真实层级全落 L1 子目录、
        召回后全标 L1（D164 塌缩）。本批改为 flat 目录 + evol_level 数据列，
        召回/compaction/count 全部读本目录，层级由行内 evol_level 保真。
        """
        return self._cold_dir

    def _write_cold_node_to_disk(self, node: PulseNode) -> bool:
        """★阶段D：把单个节点序列化并增量写入冷存 Parquet（不改变内存池状态）。

        供 `_evict_cold_node`（驱逐：写盘后再从 _cold 移除）与
        `load_batch_lazy`（启动按需加载：冷节点直接写盘，不进内存）共用。
        返回是否写盘成功。
        """
        if not self._cold_storage_enabled:
            return False
        try:
            _row = self._cold_node_to_row(node)
            # ★P0修复：显式指定列表列的元素类型为string，避免空列表被推断为null类型
            # 导致不同文件schema不一致，读取时报"Unsupported cast from string to null"
            # ★第81批 T4：批量写（攒批 flush，源头减碎文件）。
            #   单节点不再立即 write_to_dataset（否则每个驱逐=1 碎文件，
            #   5483 碎文件导致全量召回 O(N²) 退化）。改为进入 _cold_write_buffer，
            #   达到 COLD_WRITE_BATCH_SIZE 或超时/强制 flush 时一次 write_to_dataset 写一批。
            #   compaction 进行中(_cold_compacting=True)的写请求只进 buffer，不落碎文件
            #   （消除「边合并边碎写」）。
            if getattr(self, "_cold_compacting", False):
                with self._cold_buf_lock():
                    self._cold_write_buffer.append(node)
            else:
                _buf = None
                with self._cold_buf_lock():
                    self._cold_write_buffer.append(node)
                    _size = self._m81_cold_cfg("COLD_WRITE_BATCH_SIZE", 200)
                    _now = time.time()
                    _interval = self._m81_cold_cfg("COLD_WRITE_FLUSH_INTERVAL", 30.0)
                    _last = getattr(self, "_cold_last_flush_time", 0.0)
                    if len(self._cold_write_buffer) >= _size or (_now - _last) >= _interval:
                        _buf = self._cold_write_buffer
                        self._cold_write_buffer = []
                if _buf is not None:
                    self._flush_cold_buffer_nodes(_buf)
            # 原 compaction 检查（每3次写盘/60s节流）
            try:
                self._cold_write_count = getattr(self, "_cold_write_count", 0) + 1
                _now_chk = time.time()
                _last_chk = getattr(self, "_cold_last_check_ts", 0.0)
                if self._cold_write_count >= 3 and (_now_chk - _last_chk) >= 60.0:
                    self._cold_write_count = 0
                    self._cold_last_check_ts = _now_chk
                    # ★PHASE17-C3：原硬编码 20 与启动检查的 2 相差 10 倍，
                    #   同一份数据在不同入口得到相反结论。统一为常量。
                    _compact_result = self.maybe_compact_cold_storage(
                        max_files=COLD_COMPACT_MIN_FILES)
                    if _compact_result.get("triggered"):
                        _module_logger.info(f"冷存compaction已触发: {_compact_result}")
                    else:
                        # ★9-问题3修复（2026-09-05）：原「未触发」打 DEBUG，
                        #   生产环境（INFO 级）看不到「检查发生了但文件数没到阈值」，
                        #   星轨 7 小时日志因此无法判断 compaction 是「坏了」还是
                        #   「没到触发条件」。改为 INFO，让检查活动可观察。
                        #   60 秒节流已由外层保证，此处不会刷屏。
                        _module_logger.info(
                            f"冷存compaction检查完成(未触发): 文件数 "
                            f"{_compact_result.get('file_count')}/"
                            f"{_compact_result.get('max_files')} 未达阈值，"
                            f"冷存写入量少属正常")
            except Exception as _e:
                _module_logger.warning(f"冷存compaction检查失败（不影响写入）: {_e}")
            return True
        except Exception as _e:
            _module_logger.warning(f"冷节点写盘失败 {getattr(node, 'node_id', '?')}: {_e}")
            return False

    def _promote_to_hot(self, node: PulseNode) -> None:
        """★T1: 温池→热池升级（访问频率超阈值时自动触发）。"""
        node_id = node.node_id
        if node_id in self._warm:
            del self._warm[node_id]
        if node_id in self._warm_lru:
            del self._warm_lru[node_id]
        self._hot[node_id] = node

    def _demote_to_warm(self, node: PulseNode) -> None:
        """★T1: 热池→温池降级（长期低频访问时自动触发）。"""
        node_id = node.node_id
        if node_id in self._hot:
            del self._hot[node_id]
        self._warm[node_id] = node
        self._warm_lru[node_id] = node
        # 热池降级后重置访问计数，避免立即再次降级
        self._access_count[node_id] = self._demotion_threshold + 1

    def _enforce_warm_lru(self) -> None:
        """★T1: 温缓存LRU淘汰——超容量时淘汰最久未访问的节点。"""
        if not self._hot_cold_enabled:
            return
        while len(self._warm_lru) > self._warm_cache_size:
            # 淘汰最久未访问（OrderedDict 第一个）
            evicted_id, evicted_node = self._warm_lru.popitem(last=False)
            # 从温池也移除
            self._warm.pop(evicted_id, None)
            # 降级到冷池
            self._cold[evicted_id] = evicted_node
            # 清理访问计数
            self._access_count.pop(evicted_id, None)

    def _shrink_cache(self) -> None:
        """★T1: 内存保护——内存使用率超阈值时缩小温缓存。"""
        if not self._hot_cold_enabled:
            return
        try:
            import psutil
            mem = psutil.virtual_memory()
            if mem.percent / 100.0 >= self._memory_warning_threshold:
                # 缩小温缓存到当前一半
                _new_size = max(1000, len(self._warm_lru) // 2)
                while len(self._warm_lru) > _new_size:
                    evicted_id, evicted_node = self._warm_lru.popitem(last=False)
                    self._warm.pop(evicted_id, None)
                    self._cold[evicted_id] = evicted_node
                    self._access_count.pop(evicted_id, None)
                self._cache_stats["cache_shrinks"] += 1
                _module_logger.info(
                    "[T1内存保护] 温缓存已缩小到 %d (内存使用率 %.1f%%)",
                    len(self._warm_lru), mem.percent)
        except ImportError:
            pass  # psutil 未安装，跳过内存保护
        except Exception as e:
            _module_logger.debug("[T1内存保护] 异常已忽略: %s: %s", type(e).__name__, e)

    def get_cache_stats(self) -> dict:
        """★T1: 返回缓存命中率统计。"""
        total = self._cache_stats["warm_hits"] + self._cache_stats["warm_misses"]
        hit_rate = (self._cache_stats["warm_hits"] / total) if total > 0 else 0.0
        return {
            **self._cache_stats,
            "warm_hit_rate": round(hit_rate, 4),
            "warm_lru_size": len(self._warm_lru),
            "hot_size": len(self._hot),
            "warm_size": len(self._warm),
            "cold_size": len(self._cold),
        }

    def _evict_cold_node(self, node: PulseNode) -> bool:
        """把单个冷节点驱逐到磁盘冷存（增量追加 Parquet），返回是否成功。

        仅当冷存储启用时调用。驱逐后从 _cold 移除，node_id 记入 _cold_evicted，
        并在外置索引表（若注入）标记 evicted=True。
        """
        if not self._cold_storage_enabled:
            return False
        _node_id = node.node_id
        # 先写盘（复用 _write_cold_node_to_disk），成功后再从冷池移除 + 登记驱逐
        if not self._write_cold_node_to_disk(node):
            return False
        self._cold.pop(_node_id, None)
        self._cold_evicted.add(_node_id)
        # 外置索引标记 evicted（★第81批 T4：用真实 evol_level，不再恒 L1）
        if self._index_store is not None:
            self._index_store.record_change(
                "add", "level_index",
                {"evol_level": str(getattr(node, "evol_level", "L1")), "node_id": _node_id, "evicted": True}
            )
        return True

    # ==================== ★第81批 T4：冷存批量写 / 真层级 / schema 同步 / 索引 ====================
    def _m81_cold_cfg(self, name: str, default):
        """★第81批 T4：冷存相关配置惰性读取（默认兜底值）。"""
        try:
            import config as _cfg81
            return getattr(_cfg81, name, default)
        except Exception:
            return default

    def _cold_buf_lock(self):
        """★第81批 T4：懒初始化并返回冷存缓冲锁（同时确保缓冲列表存在）。

        兼容绕过 __init__ 的轻量实例：既有测试（如 m63）用
        `PulseNodePool.__new__(PulseNodePool)` 只喂冷存字段，不会走 __init__，
        因而没有 _cold_buffer_lock/_cold_write_buffer。T4 新增的
        flush_cold_buffer/_rebuild_cold_index 直接取属性会抛 AttributeError，
        进而被 compact_cold_storage 的外层 except 吞掉、整体判失败。
        故统一走本方法懒初始化，保证轻量实例与真实实例行为一致。
        """
        _lock = getattr(self, "_cold_buffer_lock", None)
        if _lock is None:
            _lock = threading.RLock()
            self._cold_buffer_lock = _lock
        if getattr(self, "_cold_write_buffer", None) is None:
            self._cold_write_buffer = []
        return _lock

    def _cold_node_to_row(self, node: "PulseNode") -> dict:
        """★第81批 T4：节点 -> 冷存 parquet 行（真实 evol_level + 7 新字段）。

        根因修复：原实现硬编码 evol_level="L1"，导致冷节点无论真实层级全落 L1 分区、
        召回后全标 L1（D164 新发现塌缩）。此处用节点真实 evol_level；并补 7 个数据字段
        （source_url/evidence_chain/source_time/acquired_time/source_timestamp/
        quality_flag/quality_reason），保证「驱逐→召回」往返不丢（A6）。
        灰度开关 COLD_STORAGE_SCHEMA_M81_COMPLETE 关闭时回退旧行为
        （evol_level 恒 L1、7 字段用默认值）。
        """
        _d = node.to_dict()
        _complete = self._m81_cold_cfg("COLD_STORAGE_SCHEMA_M81_COMPLETE", True)
        _level = "L1" if not _complete else str(_d.get("evol_level", "L1"))
        _row = {
            "node_id": str(_d.get("node_id", "")),
            "value": json.dumps(_d.get("value", ""), ensure_ascii=False),
            "keywords": list(_d.get("keywords", []) or []),
            "evol_level": _level,
            "importance": str(_d.get("importance", "C")),
            "abstraction": float(_d.get("abstraction", 0.0) or 0.0),
            "created_at": float(_d.get("created_at", 0.0) or 0.0),
            "last_activated": float(_d.get("last_activated", 0.0) or 0.0),
            "activation_count": int(_d.get("activation_count", 0) or 0),
            "space_path": str(_d.get("space_path", "/")),
            "state": str(_d.get("state", "active")),
            "source_organ": str(_d.get("source_organ", "unknown")),
            "trigger_reason": str(_d.get("trigger_reason", "")),
            "frequency_signature": float(_d.get("frequency_signature", 0.0) or 0.0),
            "linked_nodes": list(_d.get("linked_nodes", []) or []),
            "semantic_relations": json.dumps(_d.get("semantic_relations", []), ensure_ascii=False),
            "hebbian_weight": float(_d.get("hebbian_weight", 0.0) or 0.0),
            "cooccurrence_count": int(_d.get("cooccurrence_count", 0) or 0),
            "version": int(_d.get("version", 1) or 1),
            "updated_at": float(_d.get("updated_at", 0.0) or 0.0),
            "checksum": str(_d.get("checksum", "")),
            "instinct": bool(_d.get("instinct", False)),
            "instinct_at": float(_d.get("instinct_at", 0.0) or 0.0),
            "instinct_active_times": int(_d.get("instinct_active_times", 0) or 0),
            "instinct_last_use": float(_d.get("instinct_last_use", 0.0) or 0.0),
            "ephemeral": bool(_d.get("ephemeral", False)),
            "view_mode": str(_d.get("view_mode", "OUTER_VIEW")),
            "trust_score": float(_d.get("trust_score", 50.0) or 0.0),
            "verification_history": json.dumps(_d.get("verification_history", []), ensure_ascii=False),
        }
        if _complete:
            _row["source_url"] = str(_d.get("source_url", ""))
            _row["evidence_chain"] = json.dumps(_d.get("evidence_chain", []), ensure_ascii=False)
            _row["source_time"] = float(_d.get("source_time", 0.0) or 0.0)
            _row["acquired_time"] = float(_d.get("acquired_time", 0.0) or 0.0)
            _row["source_timestamp"] = float(_d.get("source_timestamp", 0.0) or 0.0)
            _row["quality_flag"] = str(_d.get("quality_flag", "clean"))
            _row["quality_reason"] = str(_d.get("quality_reason", ""))
        return _row

    def _cold_row_schema(self) -> "Any":
        """★第81批 T4：冷存行 schema（与 _cold_node_to_row 同步，含 7 新字段）。"""
        import pyarrow as pa
        _complete = self._m81_cold_cfg("COLD_STORAGE_SCHEMA_M81_COMPLETE", True)
        _fields = [
            pa.field("node_id", pa.string()),
            pa.field("value", pa.string()),
            pa.field("keywords", pa.list_(pa.string())),
            pa.field("evol_level", pa.string()),
            pa.field("importance", pa.string()),
            pa.field("abstraction", pa.float64()),
            pa.field("created_at", pa.float64()),
            pa.field("last_activated", pa.float64()),
            pa.field("activation_count", pa.int64()),
            pa.field("space_path", pa.string()),
            pa.field("state", pa.string()),
            pa.field("source_organ", pa.string()),
            pa.field("trigger_reason", pa.string()),
            pa.field("frequency_signature", pa.float64()),
            pa.field("linked_nodes", pa.list_(pa.string())),
            pa.field("semantic_relations", pa.string()),
            pa.field("hebbian_weight", pa.float64()),
            pa.field("cooccurrence_count", pa.int64()),
            pa.field("version", pa.int64()),
            pa.field("updated_at", pa.float64()),
            pa.field("checksum", pa.string()),
            pa.field("instinct", pa.bool_()),
            pa.field("instinct_at", pa.float64()),
            pa.field("instinct_active_times", pa.int64()),
            pa.field("instinct_last_use", pa.float64()),
            pa.field("ephemeral", pa.bool_()),
            pa.field("view_mode", pa.string()),
            pa.field("trust_score", pa.float64()),
            pa.field("verification_history", pa.string()),
        ]
        if _complete:
            _fields += [
                pa.field("source_url", pa.string()),
                pa.field("evidence_chain", pa.string()),
                pa.field("source_time", pa.float64()),
                pa.field("acquired_time", pa.float64()),
                pa.field("source_timestamp", pa.float64()),
                pa.field("quality_flag", pa.string()),
                pa.field("quality_reason", pa.string()),
            ]
        return pa.schema(_fields)

    def _flush_cold_buffer_nodes(self, nodes: list) -> int:
        """★第81批 T4：把一批节点一次 write_to_dataset 写出（flat 目录），返回写出数。

        与 _write_cold_node_to_disk 共用 _cold_node_to_row/_cold_row_schema，保证 schema 一致。
        写后重建侧车索引（便于单点召回命中 row_group）。
        """
        if not nodes:
            return 0
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except Exception:
            return 0
        try:
            _rows = [self._cold_node_to_row(_n) for _n in nodes]
            _dir = self._cold_dir
            os.makedirs(_dir, exist_ok=True)
            _schema = self._cold_row_schema()
            _table = pa.Table.from_pylist(_rows, schema=_schema)
            pq.write_to_dataset(_table, root_path=_dir, compression="snappy")
            self._cold_last_flush_time = time.time()
            # 重建侧车索引（单点召回命中 row_group）
            self._rebuild_cold_index()
            return len(_rows)
        except Exception as _e:
            _module_logger.warning(f"[第81批 T4] 冷存批量 flush 失败: {_e}")
            return 0

    def flush_cold_buffer(self) -> int:
        """★第81批 T4：强制把缓冲节点落盘（保存/退出/compaction 前调用）。返回写出数。"""
        _buf = None
        with self._cold_buf_lock():
            if self._cold_write_buffer:
                _buf = self._cold_write_buffer
                self._cold_write_buffer = []
        if _buf is None:
            return 0
        return self._flush_cold_buffer_nodes(_buf)

    def _cold_data_files(self) -> list:
        """★第81批 T4：返回冷存目录全部 .parquet 文件绝对路径（**递归**）。

        ★必须递归：历史（hive 分区）数据落在 evol_level=LX/ 子目录，分区列只存在于目录名；
        新写入为 flat 目录（evol_level 作数据列）。用 os.listdir（非递归）会**漏掉全部历史
        分区文件**——实测 count_cold_parquet_files 返回 0、批量召回返回 0 节点，正是此坑
        （A6 复现：2000/1000 历史碎文件全部不可见）。故改为 os.walk 递归，两种布局一网打尽。
        """
        _dir = self._cold_parquet_dir()
        if not os.path.isdir(_dir):
            return []
        _out = []
        for _root, _dirs, _files in os.walk(_dir):
            for _fname in sorted(_files):
                if _fname.endswith(".parquet"):
                    _out.append(os.path.join(_root, _fname))
        return _out

    def _read_cold_all_rows(self) -> list:
        """★第81批 T4：读全部冷存 parquet 文件，返回 [(row_dict, file_path, rg_index, row_offset), ...]。

        ★层级保真（D164 根因）：历史 hive 分区文件的 evol_level **只存在于目录名**
        （evol_level=LX/），文件内没有该列。此处按「文件所在目录名」补回，
        避免召回时 evol_level 缺失而塌缩为 L1。新 flat 文件内自带 evol_level 数据列，不会被覆盖。
        """
        import pyarrow.parquet as pq
        _rows = []
        for _fpath in self._cold_data_files():
            # 从父目录名解析历史分区层级（如 "evol_level=L2" -> "L2"）
            _dir_level = None
            _pbase = os.path.basename(os.path.dirname(_fpath).rstrip(os.sep))
            if "=" in _pbase and _pbase.split("=", 1)[0] == "evol_level":
                _dir_level = _pbase.split("=", 1)[1]
            try:
                _pf = pq.ParquetFile(_fpath)
                for _rg in range(_pf.num_row_groups):
                    _pl = _pf.read_row_group(_rg).to_pylist()
                    for _i, _row in enumerate(_pl):
                        if _dir_level is not None and "evol_level" not in _row:
                            _row["evol_level"] = _dir_level
                        _rows.append((_row, _fpath, _rg, _i))
            except Exception as _e:
                _module_logger.debug(f"[第81批 T4] 冷存索引读文件失败 {_fpath}: {_e}")
        return _rows

    def _rebuild_cold_index(self) -> None:
        """★第81批 T4：重建侧车索引 node_id->(file,rg,offset,level) 并持久化 JSON。"""
        if not self._m81_cold_cfg("COLD_SIDECAR_INDEX_ENABLED", True):
            return
        try:
            _rows = self._read_cold_all_rows()
            _idx = {}
            for _row, _fpath, _rg, _i in _rows:
                _nid = str(_row.get("node_id", ""))
                if _nid and _nid not in _idx:
                    _idx[_nid] = {"file": _fpath, "rg": _rg, "offset": _i,
                                  "level": _row.get("evol_level", "L1")}
            with self._cold_buf_lock():
                self._cold_index = _idx
                self._cold_index_loaded = True
            try:
                _ip = self._cold_dir.rstrip(os.sep) + ".index.json"
                with open(_ip, "w", encoding="utf-8") as _f:
                    json.dump(_idx, _f)
            except Exception:
                pass
        except Exception as _e:
            _module_logger.debug(f"[第81批 T4] 重建冷存索引失败: {_e}")

    def _ensure_cold_index(self) -> None:
        """★第81批 T4：懒加载侧车索引（进程重启后从 JSON 恢复，避免每次全扫）。"""
        # ★第81批 T4：轻量实例（绕过 __init__）缺这些属性，用 getattr 兜底
        if getattr(self, "_cold_index_loaded", False):
            return
        if getattr(self, "_cold_index", None) is None:
            self._cold_index = {}
        if self._m81_cold_cfg("COLD_SIDECAR_INDEX_ENABLED", True):
            _ip = self._cold_dir.rstrip(os.sep) + ".index.json"
            try:
                if os.path.isfile(_ip):
                    with open(_ip, "r", encoding="utf-8") as _f:
                        _loaded = json.load(_f)
                    if isinstance(_loaded, dict):
                        with self._cold_buf_lock():
                            self._cold_index = _loaded
            except Exception:
                pass
        self._cold_index_loaded = True

    def _cold_row_to_node(self, row: dict) -> "PulseNode | None":
        """★第81批 T4：冷存行 -> PulseNode（真实 evol_level + 7 新字段）。"""
        _complete = self._m81_cold_cfg("COLD_STORAGE_SCHEMA_M81_COMPLETE", True)
        try:
            _d = {
                "node_id": row.get("node_id", ""),
                "value": json.loads(row.get("value", '""')) if row.get("value") not in (None, "") else "",
                "keywords": list(row.get("keywords", []) or []),
                "evol_level": row.get("evol_level", "L1"),
                "importance": row.get("importance", "C"),
                "abstraction": float(row.get("abstraction", 0.0) or 0.0),
                "created_at": float(row.get("created_at", 0.0) or 0.0),
                "last_activated": float(row.get("last_activated", 0.0) or 0.0),
                "activation_count": int(row.get("activation_count", 0) or 0),
                "space_path": row.get("space_path", "/"),
                "state": row.get("state", "active"),
                "source_organ": row.get("source_organ", "unknown"),
                "trigger_reason": row.get("trigger_reason", ""),
                "frequency_signature": float(row.get("frequency_signature", 0.0) or 0.0),
                "linked_nodes": list(row.get("linked_nodes", []) or []),
                "semantic_relations": json.loads(row.get("semantic_relations", "[]")) if row.get("semantic_relations") else [],
                "hebbian_weight": float(row.get("hebbian_weight", 0.0) or 0.0),
                "cooccurrence_count": int(row.get("cooccurrence_count", 0) or 0),
                "version": int(row.get("version", 1) or 1),
                "updated_at": float(row.get("updated_at", 0.0) or 0.0),
                "checksum": row.get("checksum", ""),
                "instinct": bool(row.get("instinct", False)),
                "instinct_at": float(row.get("instinct_at", 0.0) or 0.0),
                "instinct_active_times": int(row.get("instinct_active_times", 0) or 0),
                "instinct_last_use": float(row.get("instinct_last_use", 0.0) or 0.0),
                "ephemeral": bool(row.get("ephemeral", False)),
                "view_mode": row.get("view_mode", "OUTER_VIEW"),
                "trust_score": float(row.get("trust_score", 50.0) or 0.0),
                "verification_history": json.loads(row.get("verification_history", "[]")) if row.get("verification_history") else [],
            }
            if _complete:
                _d["source_url"] = str(row.get("source_url", ""))
                _d["evidence_chain"] = json.loads(row.get("evidence_chain", "[]")) if row.get("evidence_chain") else []
                _d["source_time"] = float(row.get("source_time", 0.0) or 0.0)
                _d["acquired_time"] = float(row.get("acquired_time", 0.0) or 0.0)
                _d["source_timestamp"] = float(row.get("source_timestamp", 0.0) or 0.0)
                _d["quality_flag"] = str(row.get("quality_flag", "clean"))
                _d["quality_reason"] = str(row.get("quality_reason", ""))
            return PulseNode.from_dict(_d)
        except Exception as _e:
            _module_logger.debug(f"[第81批 T4] 冷存行转节点失败: {_e}")
            return None

    def recall_cold_nodes_batch(self, node_ids=None, consume: bool = False) -> list:
        """★第81批 T4：批量召回（性能关键）。一次读全冷存构建 {node_id:row} 映射后 join 返回。

        替换 get_all_including_evicted 的逐节点全目录扫（O(N×全扫)→一次整读）。
        node_ids=None 时返回全部冷节点；consume=True 时同步清除驱逐登记 + 命中统计。
        """
        if not self._cold_storage_enabled:
            return []
        self.flush_cold_buffer()
        try:
            import pyarrow.parquet as pq  # noqa: F401
        except Exception:
            return []
        _rows = self._read_cold_all_rows()
        _by_id = {}
        for _row, _fp, _rg, _i in _rows:
            _nid = str(_row.get("node_id", ""))
            if _nid and _nid not in _by_id:
                _by_id[_nid] = _row
        _result = []
        _targets = node_ids if node_ids is not None else list(_by_id.keys())
        for _nid in _targets:
            _row = _by_id.get(_nid)
            if _row is None:
                if consume:
                    self._cold_stats["recall_misses"] += 1
                continue
            _node = self._cold_row_to_node(_row)
            if _node is None:
                continue
            _result.append(_node)
            if consume:
                self._cold_evicted.discard(_nid)
                self._cold_stats["recall_hits"] += 1
        return _result

    def _recall_cold_node(self, node_id: str, consume: bool = True) -> PulseNode | None:
        """★第81批 T4：单点召回走侧车索引（node_id→(file,rg,offset)），read_row_group 直读；
        索引缺失/失效/损坏时自动回退批量读并重建索引（降级不报错）。

        D164 根因修复：原实现 `pq.read_table(_dir, filters=[("node_id","=",id)])` 每次都打开
        整个冷存目录做全扫过滤（O(N²)），且硬编码 evol_level="L1" 导致召回后层级塌缩。
        """
        if not self._cold_storage_enabled:
            return None
        if node_id not in self._cold_evicted:
            return None
        try:
            import pyarrow.parquet as pq
        except Exception:
            return None
        # ★第81批 T4：确保缓冲落盘 + 索引可用，再走单点索引召回（毫秒-百毫秒级）
        self.flush_cold_buffer()
        self._ensure_cold_index()
        _idx = self._cold_index.get(node_id)
        if _idx is not None and self._m81_cold_cfg("COLD_SIDECAR_INDEX_ENABLED", True):
            try:
                _pf = pq.ParquetFile(_idx["file"])
                _tbl = _pf.read_row_group(int(_idx["rg"]))
                _pl = _tbl.to_pylist()
                _i = int(_idx["offset"])
                if 0 <= _i < len(_pl):
                    _node = self._cold_row_to_node(_pl[_i])
                    if _node is not None:
                        if consume:
                            self._cold_evicted.discard(node_id)
                            self._cold_stats["recall_hits"] += 1
                            if self._index_store is not None:
                                self._index_store.record_change(
                                    "remove", "level_index",
                                    {"evol_level": _node.evol_level, "node_id": node_id, "evicted": True})
                        return _node
            except Exception as _e:
                # 索引失效/文件损坏/行偏移漂移：回退批量读并重建索引（降级不报错）
                _module_logger.warning(
                    f"[第81批 T4] 冷存索引召回失败，回退批量读并重建索引: "
                    f"{type(_e).__name__}: {_e}")
                self._rebuild_cold_index()
        # 回退：批量读单点（一次整读，取代逐节点全扫）
        _nodes = self.recall_cold_nodes_batch([node_id], consume=consume)
        if _nodes:
            return _nodes[0]
        if consume:
            self._cold_stats["recall_misses"] += 1
        return None

    def _m70_materialize_lazy_node(self, node) -> bool:
        """★第81批 T2：节点池 serve 边界的懒加载回填（与 PulseSnapshot 同源逻辑）。

        _m70_keep（内存）优先；否则从真冷存批量召回取回。
        开关关闭时节点无 _m70_blanked 属性 → 调用方 no-op，零回归。
        """
        if node is None or not getattr(node, "_m70_blanked", False):
            return True
        _nid = getattr(node, "node_id", "")
        _keep = getattr(node, "_m70_keep", None)
        if isinstance(_keep, dict) and ("value" in _keep or "linked_nodes" in _keep):
            if "value" in _keep:
                try:
                    node.value = _keep["value"]
                except Exception:
                    pass
            if "linked_nodes" in _keep:
                try:
                    node.linked_nodes = _keep["linked_nodes"]
                except Exception:
                    pass
            node._m70_blanked = False
            try:
                self._cold_evicted.discard(_nid)
            except Exception:
                pass
            return True
        # 内存无 keep → 走真冷存召回
        if _nid and getattr(self, "_cold_storage_enabled", False):
            try:
                _recalled = self.recall_cold_nodes_batch([_nid])
            except Exception:
                _recalled = []
            if _recalled:
                _rn = _recalled[0]
                # ★第81批补 T1：二次加锁写回（冷召回 IO 已在锁外完成）
                with self._lock:
                    try:
                        node.value = getattr(_rn, "value", node.value)
                    except Exception:
                        pass
                    try:
                        node.linked_nodes = getattr(_rn, "linked_nodes", node.linked_nodes)
                    except Exception:
                        pass
                    node._m70_blanked = False
                    try:
                        self._cold_evicted.discard(_nid)
                    except Exception:
                        pass
                return True
        _module_logger.error(
            f"[第81批 T2] 节点池懒加载节点 {_nid} 无法回填（无 _m70_keep 且冷存无副本），保留空白")
        return False

    def evict_cold_node(self, node_id: str) -> bool:
        """★阶段B'公开接口：驱逐指定冷节点到磁盘（需在 _cold 池中）。"""
        with self._lock:
            _node = self._cold.get(node_id)
            if _node is None:
                return False
            return self._evict_cold_node(_node)

    def recall_cold_node(self, node_id: str) -> PulseNode | None:
        """★阶段B'公开接口：从磁盘召回被驱逐节点并提升温池。"""
        with self._lock:
            _node = self._recall_cold_node(node_id)
            if _node is not None:
                # 召回后提升温池（复用现有激活→提升语义）
                self._warm[_node.node_id] = _node
            return _node

    def get_cold_stats(self) -> dict[str, Any]:
        """★阶段B'公开接口：冷存储统计（内存缓存数/已驱逐数/召回命中率）。"""
        with self._lock:
            _hits = self._cold_stats["recall_hits"]
            _misses = self._cold_stats["recall_misses"]
            _total = _hits + _misses
            return {
                "enabled": self._cold_storage_enabled,
                "cold_cache_count": len(self._cold),
                "evicted_count": len(self._cold_evicted),
                "recall_hits": _hits,
                "recall_misses": _misses,
                "recall_hit_rate": round(_hits / _total, 4) if _total else 0.0,
            }

    def count_cold_parquet_files(self) -> int:
        """★阶段B'硬约束2：统计冷存 Parquet 小文件数（用于 compaction 触发判断）。

        ★第81批 T4：改为**递归**统计（含历史 evol_level=LX/ 子目录）。
        非递归 os.listdir 会漏掉历史分区文件 → 启动检查永远认为「无需合并」，
        5483 历史碎文件因此从未被 compaction 触发（与 _cold_data_files 同一个坑）。
        """
        _dir = self._cold_parquet_dir()
        if not os.path.isdir(_dir):
            return 0
        _n = 0
        for _root, _dirs, _files in os.walk(_dir):
            _n += sum(1 for _f in _files if _f.endswith(".parquet"))
        return _n

    # ========== ★主线第65批 T2/P1：冷存 compaction 自适应 ==========
    def _cold_compaction_adaptive_enabled(self) -> bool:
        """冷存 compaction 自适应总开关（默认开）。"""
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_COLD_COMPACTION_ADAPTIVE", True))
        except Exception:
            return True

    def _get_system_load_level(self) -> str:
        """统一系统负载等级（low/medium/high/critical）；异常安全降级 low。"""
        try:
            from nucleus.runtime_metrics import get_runtime_metrics
            return get_runtime_metrics().get_system_load().get("load_level", "low")
        except Exception:
            return "low"

    def compact_cold_storage(self) -> dict[str, Any]:
        """★阶段B'硬约束2：合并冷存 L1 分区的小文件（compaction）。

        读取整个 evol_level=L1 分区的所有小 parquet 文件，按 node_id 去重
        （同一节点可能被驱逐多次，保留最新一条），全量重写为单个文件。

        触发条件（由调用方判断）：文件数 ≥ COLD_COMPACT_MIN_FILES，或每周定期执行。
        返回：{before_files, after_files, node_count, dedup_count, success}
        """
        if not self._cold_storage_enabled:
            return {"before_files": 0, "after_files": 0, "node_count": 0,
                    "dedup_count": 0, "success": False, "reason": "cold_storage_disabled"}
        # ★主线第65批 T2/P1：冷存 compaction 自适应（高负载暂停，降载优先）
        if self._cold_compaction_adaptive_enabled():
            _lvl = self._get_system_load_level()
            if _lvl in ("high", "critical"):
                _module_logger.info(
                    f"冷存compaction自适应: 负载{_lvl}偏高，暂停本次compaction")
                return {"before_files": 0, "after_files": 0, "node_count": 0,
                        "dedup_count": 0, "success": False, "reason": "high_load_paused"}
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except Exception as _e:
            return {"before_files": 0, "after_files": 0, "node_count": 0,
                    "dedup_count": 0, "success": False, "reason": f"pyarrow_unavailable:{_e}"}
        _dir = self._cold_parquet_dir()
        if not os.path.isdir(_dir):
            # ★主线第68批 T6/P2：目录缺失时只输出**一条** INFO 汇总，
            #   不再对其中每个文件逐条告警（此前实测 1621 条刷屏）。
            _module_logger.info(
                f"[第68批] 冷存目录不存在，跳过 compaction"
                f"（若为清理/重建窗口期则属预期）: {_dir}")
            return {"before_files": 0, "after_files": 0, "node_count": 0,
                    "dedup_count": 0, "success": True, "reason": "no_data"}
        try:
            # ★第81批 T4-④：标记 compaction 进行中，阻断缓冲写落碎文件（消除边合并边碎写）
            self._cold_compacting = True
            self.flush_cold_buffer()
            # ★第81批 T4：兼容 flat + 历史 hive 分区（evol_level=LX/ 子目录）两种布局，
            #   枚举全部源目录（base 直接落盘 + 各 evol_level 子目录）。
            _base = self._cold_parquet_dir()
            _src_dirs = []
            if os.path.isdir(_base):
                _src_dirs.append(_base)
                for _dn in sorted(os.listdir(_base)):
                    _full = os.path.join(_base, _dn)
                    if os.path.isdir(_full) and "=" in _dn and _dn.split("=", 1)[0] == "evol_level":
                        _src_dirs.append(_full)
            _total_files = 0
            _before_per_level: dict[str, int] = {}
            for _sd in _src_dirs:
                _c = sum(1 for _f in os.listdir(_sd) if _f.endswith(".parquet"))
                _total_files += _c
                _before_per_level["flat" if _sd == _base else os.path.basename(_sd)] = _c
            _before = _total_files
            # ★PHASE17-C3：与调用方共用同一阈值常量
            if _before < COLD_COMPACT_MIN_FILES:
                # 不足 COLD_COMPACT_MIN_FILES 个文件，无需合并
                self._cold_compacting = False
                return {"before_files": _before, "after_files": _before,
                        "before_per_level": _before_per_level,
                        "node_count": 0, "dedup_count": 0, "success": True,
                        "reason": "no_need"}
            # ★T2修复（N2/P1）：逐文件容错读取（兼容历史 schema 混杂 + 多分区），
            #   单文件损坏只跳过不阻断；_read_cold_partition_tolerant 已按 COLD_COMPACT_SKIP_ERROR_ENABLED
            #   将跳过文件升为 ERROR + 汇总（T4-⑥）。
            _rows: list[dict] = []
            _skipped = 0
            for _sd in _src_dirs:
                _t, _s = self._read_cold_partition_tolerant(_sd)
                if _t is not None:
                    _rows.extend(_t.to_pylist())
                _skipped += _s
            if not _rows:
                self._cold_compacting = False
                return {"before_files": _before, "after_files": _before,
                        "before_per_level": _before_per_level,
                        "node_count": 0, "dedup_count": 0, "success": False,
                        "reason": "所有parquet文件均无法读取", "skipped_files": _skipped}
            # 按 node_id 去重，保留最新（后出现者覆盖前者）
            _dedup: dict[str, dict[str, Any]] = {}
            for _row in _rows:
                _dedup[_row.get("node_id", "")] = _row
            _dedup_count = len(_rows) - len(_dedup)
            _merged_rows = list(_dedup.values())
            # 全量重写为单个 flat 文件（evol_level 作为数据列，不再按分区落子目录），
            # 先写临时目录并校验行数一致，再原子替换，避免中断损坏 / 静默丢节点（T4-⑥）。
            import shutil
            _tmp_dir = self._cold_dir + ".compact_tmp"
            if os.path.exists(_tmp_dir):
                # ★主线第60批 T6：清理旧临时目录失败时降级（DEBUG），不阻断主流程
                try:
                    shutil.rmtree(_tmp_dir)
                except Exception as _tmp_err:
                    _module_logger.debug(
                        f"[冷存compaction] 清理旧临时目录失败（继续写入覆盖）: "
                        f"{type(_tmp_err).__name__}: {_tmp_err}")
            try:
                os.makedirs(_tmp_dir, exist_ok=True)
            except Exception as _mk_err:
                _module_logger.debug(
                    f"[冷存compaction] 创建临时目录失败: {type(_mk_err).__name__}: {_mk_err}")
            # ★第81批 T4：不用 _cold_row_schema 强制全字段，避免历史文件缺 7 新列时写入失败；
            #   让 pyarrow 按实际行推断 schema，召回时由 _cold_row_to_node 的 _complete 守卫补默认值。
            _merged_table = pa.Table.from_pylist(_merged_rows) if _merged_rows else pa.Table.from_pylist([{}])
            pq.write_to_dataset(
                _merged_table,
                root_path=_tmp_dir,
                compression="snappy",
            )
            # ★第81批 T4-⑥：先校验新文件行数与去重后节点数一致，再删旧目录，
            #   防止「合并删旧后静默丢节点」。
            if self._m81_cold_cfg("COLD_COMPACT_ROWCOUNT_VERIFY", True):
                _written = 0
                for _wf in os.listdir(_tmp_dir):
                    if _wf.endswith(".parquet"):
                        _written += pq.ParquetFile(os.path.join(_tmp_dir, _wf)).metadata.num_rows
                if _written != len(_merged_rows):
                    self._cold_compacting = False
                    _module_logger.error(
                        f"冷存compaction行数校验失败: 写入{_written}≠去重{len(_merged_rows)}，"
                        f"中止替换（保留旧数据，不静默丢节点）")
                    try:
                        shutil.rmtree(_tmp_dir)
                    except Exception:
                        pass
                    return {"before_files": _before, "after_files": _before,
                            "before_per_level": _before_per_level,
                            "node_count": 0, "dedup_count": 0, "success": False,
                            "reason": "row_count_mismatch",
                            "written": _written, "expected": len(_merged_rows),
                            "skipped_files": _skipped}
            # 原子替换：删旧目录（含历史 evol_level=* 子目录），重命名临时目录
            # ★主线第60批 T6：Windows 文件锁重试 + 冷却降级（与第59批 T1 日志轮转同源）。
            import time as _time_mod
            _retry_on = True
            try:
                import config as _cfg_m60
                _retry_on = bool(getattr(_cfg_m60, "ENABLE_COLD_COMPACT_WINDOWS_RETRY", True))
            except Exception:
                _retry_on = True
            if not _retry_on:
                # 灰度关闭：与改造前完全一致（单次 rmtree + rename）
                if os.path.exists(self._cold_dir):
                    shutil.rmtree(self._cold_dir)
                os.rename(_tmp_dir, self._cold_dir)
            else:
                _cooldown_until = getattr(self, "_cold_compact_cooldown_until", 0.0)
                if _cooldown_until and _time_mod.time() < _cooldown_until:
                    self._cold_compacting = False
                    return {"before_files": _before, "after_files": _before,
                            "before_per_level": _before_per_level,
                            "node_count": 0, "dedup_count": 0, "success": False,
                            "reason": "cooldown"}
                if os.path.exists(self._cold_dir):
                    _ok = False
                    _last_rm_err = None
                    # ★主线第63批 T2/P1：重试次数/间隔可配（默认10次/2s，原硬编码5次/1s）。
                    _retry_n = 10
                    _retry_int = 2.0
                    try:
                        import config as _cfg_m63
                        _retry_n = int(getattr(_cfg_m63, "COLD_COMPACT_DELETE_RETRY_COUNT", 10))
                        _retry_int = float(getattr(_cfg_m63, "COLD_COMPACT_DELETE_RETRY_INTERVAL", 2.0))
                    except Exception as _cfg_e:
                        _module_logger.debug(
                            f"[冷存compaction] 重试参数读取失败，使用默认: {type(_cfg_e).__name__}: {_cfg_e}")
                    for _attempt in range(1, _retry_n + 1):
                        try:
                            shutil.rmtree(self._cold_dir)
                            _ok = True
                            break
                        except Exception as _rm_err:
                            _last_rm_err = _rm_err
                            _module_logger.debug(
                                f"[冷存compaction] 删除旧目录第{_attempt}/{_retry_n}次重试失败: "
                                f"{type(_rm_err).__name__}: {_rm_err}")
                            _time_mod.sleep(_retry_int)
                    # ★主线第63批 T2/P1：强制删除兜底（ignore_errors），避免 WinError 145 阻塞主流程。
                    if not _ok:
                        _module_logger.warning(
                            f"冷存compaction: 重试{_retry_n}次仍失败，强制删除旧目录: "
                            f"{type(_last_rm_err).__name__}: {_last_rm_err}")
                        try:
                            shutil.rmtree(self._cold_dir, ignore_errors=True)
                            _ok = True
                        except Exception as _force_e:
                            _module_logger.warning(
                                f"冷存compaction: 强制删除仍失败: {type(_force_e).__name__}: {_force_e}")
                    if not _ok:
                        self._cold_compact_fail_streak = getattr(self, "_cold_compact_fail_streak", 0) + 1
                        if self._cold_compact_fail_streak >= 3:
                            self._cold_compact_cooldown_until = _time_mod.time() + 1800.0
                            _module_logger.info("[冷存compaction] 连续失败3次，冷却30分钟")
                        self._cold_compacting = False
                        _module_logger.warning(
                            f"冷存 compaction 失败: 删除旧目录耗尽重试 "
                            f"({type(_last_rm_err).__name__}: {_last_rm_err})")
                        return {"before_files": _before, "after_files": _before,
                                "before_per_level": _before_per_level,
                                "node_count": 0, "dedup_count": 0, "success": False,
                                "reason": "delete_retry_exhausted"}
                _ok = False
                _last_rn_err = None
                for _attempt in range(1, 6):
                    try:
                        os.rename(_tmp_dir, self._cold_dir)
                        _ok = True
                        break
                    except Exception as _rn_err:
                        _last_rn_err = _rn_err
                        _module_logger.debug(
                            f"[冷存compaction] 重命名临时目录第{_attempt}次重试失败: "
                            f"{type(_rn_err).__name__}: {_rn_err}")
                        # ★主线第74批 T4：指数退避（1/2/4…上限8s）
                        _time_mod.sleep(min(1.0 * (2 ** (_attempt - 1)), 8.0))
                if not _ok:
                    self._cold_compact_fail_streak = getattr(self, "_cold_compact_fail_streak", 0) + 1
                    if self._cold_compact_fail_streak >= 3:
                        self._cold_compact_cooldown_until = _time_mod.time() + 1800.0
                        _module_logger.info("[冷存compaction] 连续失败3次，冷却30分钟")
                    self._cold_compacting = False
                    _module_logger.warning(
                        f"冷存 compaction 失败: 重命名临时目录耗尽重试 "
                        f"({type(_last_rn_err).__name__}: {_last_rn_err})")
                    return {"before_files": _before, "after_files": _before,
                            "before_per_level": _before_per_level,
                            "node_count": 0, "dedup_count": 0, "success": False,
                            "reason": "rename_retry_exhausted"}
                # 成功：重置连败计数
                self._cold_compact_fail_streak = 0
            # 成功：重建侧车索引（单点召回命中 row_group）+ 清除进行中标志
            self._rebuild_cold_index()
            self._cold_compacting = False
            _after = self.count_cold_parquet_files()
            return {
                "before_files": _before,
                "after_files": _after,
                "before_per_level": _before_per_level,
                "node_count": len(_merged_rows),
                "dedup_count": _dedup_count,
                "skipped_files": _skipped,
                "success": True,
            }
        except Exception as _e:
            _module_logger.warning(f"冷存 compaction 失败: {_e}")
            return {"before_files": 0, "after_files": 0, "node_count": 0,
                    "dedup_count": 0, "success": False, "reason": str(_e)}

    def _read_cold_partition_tolerant(self, dir_override: str | None = None) -> tuple[Any, int]:
        """★T2：容错读取冷存 L1 分区，返回 (合并后的表, 跳过的坏文件数)。

        相比 `pq.ParquetDataset(...).read()`，本方法容忍三类历史数据不一致：
          1. **类型编码不一致**：同一列有的文件存 string、有的存 dictionary。
             pyarrow 合并时会抛 "Unable to merge: Field X has incompatible
             types: string vs dictionary<...>"。此处把 dictionary 列统一
             decode 为其 value 类型（string 等），消除冲突。
          2. **分区列位置不一致**：走 `write_to_dataset(partition_cols=[...])`
             写入的文件，分区列只存在于目录名（Hive 分区），文件内没有该列；
             而直接 `write_table` 写入的文件，该列在文件内。
             原实现靠 ParquetDataset 从目录名恢复，但那样无法处理类型冲突。
             此处改为：文件内没有该列时，从目录名（如 `evol_level=L1`）补回。
          3. **单文件损坏**：读取失败只跳过该文件并记录，不影响其余文件合并。

        返回 (None, skipped) 表示所有文件都读不出来。
        """
        import pyarrow as pa
        import pyarrow.parquet as pq
        try:
            import pyarrow.compute as _pc
        except Exception:
            _pc = None

        # ★第81批 T4：目录解析兼容两种布局。
        #   dir_override 给定 → 只读该目录（compaction 按源目录逐个调用，保持原语义）；
        #   未给定 → 覆盖「flat 基目录 + 各 evol_level=* 历史分区子目录」。
        #   ★不做多目录会发现不了历史数据：flat 化后 _cold_parquet_dir() 返回基目录，
        #   而历史文件仍在 evol_level=LX/ 子目录下，只读基目录会一个文件都读不到
        #   （实测 m63 用例返回 None、skipped=0）。
        if dir_override is not None:
            _dirs: list[str] = [dir_override]
        else:
            _base_dir = self._cold_parquet_dir()
            _dirs = []
            if os.path.isdir(_base_dir):
                _dirs.append(_base_dir)
                for _dn in sorted(os.listdir(_base_dir)):
                    _full = os.path.join(_base_dir, _dn)
                    if (os.path.isdir(_full) and "=" in _dn
                            and _dn.split("=", 1)[0] == "evol_level"):
                        _dirs.append(_full)
        if not _dirs:
            return None, 0

        _tables: list[Any] = []
        _skipped = 0
        _skipped_files: list[str] = []
        for _dir in _dirs:
            if not os.path.isdir(_dir):
                continue
            # 从目录名解析分区键值对（如 "evol_level=L1" → {"evol_level": "L1"}）
            _partition_kv: dict[str, str] = {}
            _base = os.path.basename(_dir.rstrip(os.sep))
            if "=" in _base:
                _k, _v = _base.split("=", 1)
                _partition_kv[_k] = _v

            for _fname in sorted(os.listdir(_dir)):
                if not _fname.endswith(".parquet"):
                    continue
                try:
                    # ★关键：必须用 ParquetFile，不能用 pq.read_table()。
                    #   pyarrow 23 的 pq.read_table() 底层走 dataset 逻辑，
                    #   **即使是读单个文件**也会做 schema 合并推断，
                    #   于是读到第2个 schema 不同的文件时照样抛
                    #   "Unable to merge: Field X has incompatible types"。
                    #   实测（pyarrow 23.0.0）：
                    #     pq.read_table(path)            -> 第2个文件起全部失败
                    #     pq.ParquetFile(path).read()    -> 全部成功
                    #   后者是直接的文件读取器，不做任何跨文件 schema 合并，
                    #   正是「逐文件容错读取」所需要的语义。
                    _t = pq.ParquetFile(os.path.join(_dir, _fname)).read()
                except Exception as _e1:
                    try:
                        _t = pq.read_table(os.path.join(_dir, _fname))
                    except Exception as _e2:
                        _skipped += 1
                        _skipped_files.append(_fname)
                        # ★主线第68批 T6/P2：告警合并（实测单次重启 1621 条同型 WARNING 刷屏）。
                        #   改为：前 3 条保留 WARNING（保留可诊断性），其余降级 DEBUG；
                        #   根因（cold 目录/文件缺失）交由启动一致性检查统一输出一次汇总。
                        if _skipped <= 3:
                            _module_logger.warning(
                                f"冷存compaction: 跳过无法读取的parquet文件 {_fname}: {_e1}")
                        else:
                            _module_logger.debug(
                                f"冷存compaction: 跳过无法读取的parquet文件 {_fname}: {_e1}")
                    continue

                # (1) dictionary 编码列 → decode 为其 value 类型
                if _pc is not None:
                    for _i, _field in enumerate(list(_t.schema)):
                        if pa.types.is_dictionary(_field.type):
                            try:
                                _decoded = _pc.dictionary_decode(_t.column(_i))
                                _t = _t.set_column(
                                    _i, pa.field(_field.name, _field.type.value_type), _decoded)
                            except Exception as _de:
                                _module_logger.debug(
                                    f"冷存compaction: 列 {_field.name} dictionary解码失败(保留原值): {_de}")

                # (2) 分区列缺失时从目录名补回
                for _pk, _pv in _partition_kv.items():
                    if _pk not in _t.column_names:
                        _t = _t.append_column(
                            _pk, pa.array([_pv] * _t.num_rows, type=pa.string()))

                _tables.append(_t)

        # ★第81批 T4-⑥：compaction 跳过文件可见性（D163 一半）。
        #   历史行为：跳过文件仅 WARNING(前3条)/DEBUG(其余)——09-16 曾静默跳过 1621 个文件，
        #   合并删旧后等于静默丢节点且无人察觉。此处在开关打开时追加**一条 ERROR 汇总**
        #   （跳过文件数 + 文件名样本），保证运维可见；开关关闭时完全回退旧行为（零回归）。
        if _skipped > 0 and self._m81_cold_cfg("COLD_COMPACT_SKIP_ERROR_ENABLED", True):
            _module_logger.error(
                f"冷存compaction: 跳过 {_skipped} 个无法读取的parquet文件"
                f"（目录={_dirs}），这些文件未参与合并，样本: {_skipped_files[:5]}")

        if not _tables:
            return None, _skipped

        # (3) 统一列集合：缺失列补 null，列顺序对齐，再做类型提升合并
        _all_cols: list[str] = []
        for _t in _tables:
            for _name in _t.column_names:
                if _name not in _all_cols:
                    _all_cols.append(_name)
        _aligned = []
        for _t in _tables:
            for _name in _all_cols:
                if _name not in _t.column_names:
                    _t = _t.append_column(_name, pa.nulls(_t.num_rows))
            _aligned.append(_t.select(_all_cols))
        try:
            return pa.concat_tables(_aligned, promote_options="permissive"), _skipped
        except Exception:
            # promote 失败时退化为按首个表的 schema 强转（仍优于直接失败）
            return pa.concat_tables(_aligned), _skipped

    def maybe_compact_cold_storage(
            self, max_files: int = COLD_COMPACT_MIN_FILES) -> dict[str, Any]:
        """★阶段B'硬约束2：按触发条件自动 compaction（文件数 ≥ max_files 时触发）。

        ★PHASE17-C3：默认参数由硬编码 20 改为统一常量 COLD_COMPACT_MIN_FILES。

        返回 compaction 结果；未触发时返回 {triggered: False}。
        """
        _count = self.count_cold_parquet_files()
        if _count < max_files:
            return {"triggered": False, "file_count": _count, "max_files": max_files}
        _result = self.compact_cold_storage()
        _result["triggered"] = True
        _result["file_count_before_trigger"] = _count
        return _result


# ========== 自测 ==========
    # ========== 公开访问接口（消除跨器官私有穿透，规则14/AP1） ==========
    @property
    def instinct_count(self) -> int:
        """★P0批次3：返回本能节点数量，替代跨器官对 _instinct 的私有直达"""
        with self._lock:
            return len(self._instinct)

    def downgrade_instinct(self, node: "PulseNode") -> None:
        """★P0批次3：公开封装 _downgrade_instinct，供 Liver 调用"""
        with self._lock:
            self._downgrade_instinct(node)

if __name__ == "__main__":
    print("=== PulseNodePool v9.5 自测 ===\n")
    
    pool = PulseNodePool(max_hot=100, max_warm=500)
    
    # 1. 添加不同层级节点
    l1_node = PulseNode(
        value="Python 3.12发布",
        keywords=["Python", "版本"],
        source_organ="双腿",
        evol_level=PulseNode.EVOL_L1,
        importance=PulseNode.IMPORTANCE_C,
    )
    l2_node = PulseNode(
        value="Python是一种广泛使用的编程语言",
        keywords=["Python", "编程语言"],
        source_organ="胃",
        evol_level=PulseNode.EVOL_L2,
        importance=PulseNode.IMPORTANCE_A,
    )
    l3_node = PulseNode(
        value="我是曈曈，新人类",
        keywords=["曈曈", "身份"],
        source_organ="内在世界",
        evol_level=PulseNode.EVOL_L3,
        importance=PulseNode.IMPORTANCE_S,
    )
    
    pool.add(l1_node)
    pool.add(l2_node)
    pool.add(l3_node)
    
    print(f"✅ 添加后总数: {pool.count()} (应为3)")
    
    # 2. 验证分层
    stats = pool.get_stats()
    print(f"✅ 热池: {stats['hot_count']} (L3+L2_A), 温池: {stats['warm_count']} (L1_C), 冷池: {stats['cold_count']}")
    print(f"   演化分布: {stats['evol_distribution']}")
    
    # 3. 获取并激活
    node = pool.get(l1_node.node_id)
    print(f"✅ 获取L1节点: {node.value}, 激活次数={node.activation_count}")
    
    # 4. 查询
    l3_nodes = pool.query(evol_level=PulseNode.EVOL_L3)
    print(f"✅ 查询L3节点: {len(l3_nodes)}个 → {l3_nodes[0].value}")
    
    # 5. 移除
    removed = pool.remove(l1_node.node_id)
    print(f"✅ 移除L1节点: {removed}, 剩余={pool.count()}")
    
    # 6. 批量加载（幂等）
    pool.load_batch([l2_node, l3_node])  # 已存在，跳过
    print(f"✅ 批量加载后: {pool.count()} (幂等，不变)")
    
    # 7. 淘汰
    purged = pool.purge_obsolete(max_age_days=0)  # 0天淘汰所有L1
    print(f"✅ 淘汰: {purged}个 (L3不淘汰)")
    
    # 8. 统计
    final_stats = pool.get_stats()
    print(f"✅ 最终: 总数{final_stats['total_nodes']} 添加{final_stats['total_added']} "
          f"移除{final_stats['total_removed']} 激活{final_stats['total_activated']}")
    
    # 9. 验证 L3 节点获取
    l3 = pool.get(l3_node.node_id)
    print(f"✅ L3节点: {l3.value}, 可淘汰={l3.is_obsolete()}")
    
    print("\n=== 自测全部通过 ===")
    
# _m51_t4_c
# _m69_t1_init
# _m69_t1_get
# _m69_t1_promote
# _m69_t1_methods
# _m69_t1_capacity
# _m69_t3_coldcompact
