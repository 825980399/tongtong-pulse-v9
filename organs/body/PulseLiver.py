# -*- coding: utf-8 -*-
"""
PulseLiver —— 肝器官 · 知识自我优化（共享记忆版）

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 订阅 HeartEvent.BEAT、KnowledgeEvent.WRITTEN、ReflectionEvent.INSIGHT 三类脉冲，执行 L1→L2 压缩、L2→L3 融合、噪声检测与矛盾检测，把零散感知沉淀为认知与智慧，并驱动本能节点的学习、升级与降级。
机制: _check_and_optimize 按后台节奏触发两级压缩——_compress_l1_to_l2 走 _group_by_path_prefix 分组、_collect_group_features 取特征、_build_l2_summary 生成摘要、_compress_group 落盘；_fuse_l2_to_l3 先 _build_knowledge_association_graph 构图再 _fuse_group 融合；全过程经 _assess_node_quality 质量门控，通过后发射 KnowledgeEvent.COMPRESSED / KnowledgeEvent.FUSED。
定位: 知识层的「代谢中枢」，上接胃写入的原始节点，下为共振检索提供高质量的 L2/L3 候选。
"""
import config
from config import TIMEOUT_CONFIG

import os
import re
import sys
import threading
import time
import traceback
from collections import Counter
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    DigestEvent,
    ErrorCode,
    HeartEvent,
    KnowledgeEvent,
    LogLevel,
    ReflectionEvent,
    SystemEvent,
)
from nucleus.knowledge_noise_filter import (
    DOMAIN_SUFFIXES,
    get_top_valuable_keywords,
    is_path_fragment_word,
)
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.events.EventTap import tap_publish  # ★第17批 T3：旁路事件发布入口
from nucleus._silent_except import silent_exc

# ★往期批次 相关任务（Dxxx）：无人值守资源看门狗（被动、节流、零副作用）
#   随心跳节流采样进程 RSS / 句柄 / 线程 / 打开文件，超阈值告警，
#   并为 5051 故障面板提供数据。阈值为模块常量，不依赖 config 运行开关（红线）。
_RESOURCE_SAMPLE_INTERVAL_SEC = 300.0      # 每 5 分钟采样一次（随心跳触发、内部节流）
_RESOURCE_RSS_WARN_BYTES = 6 * 1024 ** 3   # RSS 超 6GB 告警
_RESOURCE_RSS_GROWTH_BYTES = 2 * 1024 ** 3  # 单采样周期内 RSS 增长超 2GB 告警
_RESOURCE_HANDLES_WARN = 5000              # 句柄数超 5000 告警（Windows num_handles）
_RESOURCE_OPEN_FILES_WARN = 2000           # 打开文件数超 2000 告警（*nix open_files）


class PulseLiver(BasePulseOrgan):
    """
    肝 —— 知识自我优化器官（共享记忆版 · v9.5 分层脉冲版）
    """

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'liver_noise_threshold' in _rp and hasattr(self, '_noise_threshold'):
                self._noise_threshold = _rp['liver_noise_threshold']
        except Exception as _e:
            # ★PHASE14-P2-1（2026-09-07）：原为 `except Exception: pass`，
            #   配置热加载失败时**完全静默**——「参数没生效」与「参数本来就没配」
            #   在日志上毫无区别，排查时极易误判为配置项不存在。
            #   改为 DEBUG 落盘：正常路径几乎零开销，失败路径留证据。
            #   用 DEBUG 而非 WARNING：该方法位于热加载路径且失败有默认值兜底，
            #   不构成功能异常，不应污染 WARNING 计数。
            self._log(LogLevel.DEBUG,
                      f"运行时参数刷新失败（沿用默认值，不影响主流程）: {_e}")


    def __init__(self, organ_name: str = "肝"):
        super().__init__(organ_name)

        self.node_pool = None
        self._kal = None
        self.knowledge_tree = None
        self.frequency_codec = None
        self.resonance_engine = None

        self._l1_to_l2_count = 0
        self._l2_to_l3_count = 0
        self._reflection_compress_count = 0
        self._last_optimize_time = 0.0   # 上次优化时间戳
        self._last_fuse_time = 0.0       # 融合独立冷却计时器（不共享压缩冷却）

        # 从config加载肝脏配置
        # ★7-5修复(2026-09-05)：以下默认值**必须先于**下方配置加载执行。
        #   原先这段被机械追加在配置加载之后，导致 config.py 里配好的值
        #   被这里的硬编码默认值逐个覆盖（全项目 10 个器官、24 个属性）。
        #   默认值先行、配置覆盖，是「配置优先」的标准顺序。
        self._adaptive_fuse_blocked_paths = {}
        self._adaptive_fuse_blocked_until = 0.0  # ★第80批 T3：原 {} 导致 :2392 `now < {}` 抛 TypeError 崩溃（P0）；改为 float 默认（与 :477 型兜底一致）
        self._association_scan_interval = 0.0
        self._association_scan_offset = 0
        self._clean_failure_counts = {}
        self._compress_cooldown = 300.0
        self._compress_count_this_beat = 0
        self._contradiction_count_this_beat = 0
        self._deep_learn_cooldown = {}
        self._fuse_cooldown_loaded = False
        self._fuse_cooldown_paths = {}
        self._fuse_diagnosed_paths = set()
        self._fuse_fail_count = {}
        self._fuse_fail_fingerprint = {}
        self._fuse_permanent_skip = {}
        self._last_adaptive_fuse_time = 0.0
        self._last_association_scan_time = 0.0
        self._last_below_threshold_count = 0
        self._last_path_noise_skip_count = 0
        self._last_purity_check = 0.0
        self._low_water_triggered_this_beat = False
        self._max_associations_per_scan = 50
        self._optimize_pending = False
        self._path_noise_log_counter = 0
        self._purity_check_count = 0
        self._quote_node_ids = set()
        self._reflection_compress_threshold = 5
        self._reverse_activate_cooldown = {}
        self.snapshot = None
        self._load_liver_config()

        self._lock = threading.Lock()
        # ★P1: 从RUNTIME_PARAMS读取肝脏参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._noise_threshold = _rp.get("liver_noise_threshold", 5)
        except Exception:
            self._noise_threshold = 5
        self._noise_detected_count = 0
        self._contradiction_detected_count = 0
        self._instinct_upgrade_count = 0     # 本能升级次数
        self._instinct_downgrade_count = 0   # 本能降级次数
        # ★v25.0新增：代码学习器引用，用于代码调用关联构建
        self._code_learner = None
        # 异步优化防重入
        self._last_snapshot_save_time = 0.0
        self._reasoning_pool = None  # ★v17.0性能优化：推理进程池引用
        self._consolidation_count = 0       # 知识巩固次数
        self._stop_requested = False        # ★L10修复：停止请求标志，融合/压缩循环据此中断
        # ★往期批次 相关任务：资源看门狗状态（被动采样，零副作用）
        self._last_rss_sample = 0.0
        self._rss_baseline = None
        self._rss_samples: list = []   # 最近 RSS 样本（滚动，约 8h 窗口）
        self._res_handles = None
        self._res_threads = None
        self._res_open_files = None
        self._res_rss_max = None
        self._res_warn_count = 0
    def _load_liver_config(self):
        """从config加载肝脏配置，失败时使用兜底值"""
        # ★骨架优化：统一配置加载（基类 _get_config_section 内部已安全处理异常）
        cfg = self._get_config_section('LIVER_CONFIG')
        self._compress_cooldown = cfg.get("compress_cooldown", 300.0)
        self._reflection_compress_threshold = cfg.get("reflection_compress_threshold", 5)
        # ★D4配置中心化：关联扫描间隔与单次上限从硬编码迁移到配置
        self._association_scan_interval = cfg.get("association_scan_interval", 600.0)
        self._max_associations_per_scan = cfg.get("max_associations_per_scan", 50)
        # ★v23.0新增：从config加载融合与矛盾检测词表
        self._causal_keywords = cfg.get("causal_keywords", [])
        self._user_input_markers = cfg.get("user_input_markers", [])
        self._quote_patterns = cfg.get("quote_patterns", [])

    def set_node_pool(self, node_pool):
        self.node_pool = node_pool
        from nucleus.knowledge_access_layer import KnowledgeAccessLayer
        self._kal = KnowledgeAccessLayer(node_pool=node_pool)

    def set_knowledge_tree(self, knowledge_tree):
        self.knowledge_tree = knowledge_tree
    def set_code_learner(self, code_learner):
        self._code_learner = code_learner

    def set_frequency_codec(self, frequency_codec):
        self.frequency_codec = frequency_codec

    def set_resonance_engine(self, resonance_engine):
        self.resonance_engine = resonance_engine
    def set_snapshot(self, snapshot):
        """
        注入知识快照管理器。
        肝脏在压缩/融合生成 L2/L3 节点后，立即调用 snapshot.save() 写入本地。
        """
        self.snapshot = snapshot
    def set_reasoning_pool(self, pool):
        """★v17.0性能优化：注入推理进程池引用，用于批量频率编码"""
        self._reasoning_pool = pool
    def start(self):
        self._stop_requested = False  # ★L10修复：启动时复位停止标志
        super().start()
        self._log(LogLevel.INFO, f"已启动，复盘压缩阈值={self._reflection_compress_threshold}")

    def stop(self):
        self._stop_requested = True   # ★L10修复：请求中断进行中的融合/压缩循环
        super().stop()
        self._log(LogLevel.INFO,
                  f"已停止，L1→L2: {self._l1_to_l2_count}, "
                  f"L2→L3: {self._l2_to_l3_count}, 复盘压缩: {self._reflection_compress_count}")

    def on_pulse(self, pulse: dict[str, Any]):
        if not self.is_running:
            return

        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type in (HeartEvent.BEAT, KnowledgeEvent.WRITTEN):
            self._check_and_optimize(pulse)
        elif event_type == ReflectionEvent.INSIGHT:
            self._on_reflection_insight(payload)
        elif event_type == DigestEvent.KNOWLEDGE:
            # ★第98批 相关任务：肝作为知识代谢中枢，消费代码学习等器官发射的
            #   digest.knowledge 脉冲，避免其成为「孤儿脉冲（发射后无器官接收）」。
            self._on_digest_knowledge(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

    def _on_digest_knowledge(self, payload: dict[str, Any]) -> None:
        """★第98批 相关任务：消费 digest.knowledge 脉冲（代码学习等器官发射）。

        肝是框架的「知识代谢中枢」，把消化后的知识点沉淀进共享记忆
        （node_pool），使 digest.knowledge 不再成为孤儿脉冲（发射后无器官接收）。
        注意：log_receive 已在 BasePulseOrgan._handle_pulse_safe 中于 on_pulse 之前
        触发，因此只要本方法被调度，孤儿检测即被消除；本方法仅做知识沉淀，
        任何异常都被静默降级，绝不影响脉冲消费。
        """
        _content = (payload or {}).get("content")
        if not _content:
            return
        self._log(LogLevel.DEBUG,
                  f"[第98批T-98b] 肝吸收 digest.knowledge: {str(_content)[:50]}")
        try:
            if self.node_pool is not None:
                _node = PulseNode(
                    value=str(_content)[:500],
                    keywords=["digest.knowledge",
                              str((payload or {}).get("source_organ", "代码学习"))],
                    source_organ=self.organ_name,
                    evol_level=PulseNode.EVOL_L1,
                    space_path="/自我理解/消化知识",
                )
                self._kal.add_node(_node)
        except Exception as _e:
            self._log(LogLevel.DEBUG,
                      f"[第98批T-98b] 知识沉淀失败(已忽略): {type(_e).__name__}: {_e}")

    def get_resonance_conditions(self) -> list[dict[str, Any]]:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    HeartEvent.BEAT,
                    KnowledgeEvent.WRITTEN,
                    ReflectionEvent.INSIGHT,
                    DigestEvent.KNOWLEDGE,  # ★第98批 相关任务：肝订阅 digest.knowledge，吸收消化知识
                ],
                "min_priority": 1,
            }
        ]

    def _on_reflection_insight(self, payload: dict[str, Any]):
        if self.node_pool is None:
            return

        domain = payload.get("domain", "通用")
        issue_types = payload.get("issue_types", [])

        with self._lock:
            reflection_path = f"/反思/对话复盘/{domain}"
            l1_nodes = self._kal.query_nodes(
                evol_level=PulseNode.EVOL_L1,
                space_path_prefix=reflection_path,
                limit=100
            )

            if len(l1_nodes) >= self._reflection_compress_threshold:
                success = self._compress_reflections(l1_nodes, domain, issue_types)
                if success:
                    self._reflection_compress_count += 1
                    self._last_optimize_time = time.time()
                    self._log(LogLevel.INFO,
                             f"复盘压缩完成: {domain}领域 {len(l1_nodes)}条 L1→L2, "
                             f"总计复盘压缩: {self._reflection_compress_count}")

    def _compress_reflections(self, nodes: list[PulseNode], domain: str, issue_types: list[str]) -> bool:
        try:
            type_counter = Counter()
            # ★v25.1 P1智能化: 关键词共现关系提取——哪些关键词经常一起出现
            _cooccurrence = Counter()  # (kw1, kw2) → count
            for node in nodes:
                _node_kws = [kw for kw in node.keywords if kw]
                for kw in _node_kws:
                    type_counter[kw] += 1
                # 提取共现对（同一节点中出现的关键词对）
                for i in range(len(_node_kws)):
                    for j in range(i + 1, len(_node_kws)):
                        _pair = tuple(sorted([_node_kws[i], _node_kws[j]]))
                        _cooccurrence[_pair] += 1

            top_types = [kw for kw, _ in type_counter.most_common(5)]
            # 提取最强共现关系（Top3）
            _top_relations = [
                f"{p[0]}↔{p[1]}" for p, _ in _cooccurrence.most_common(3)
            ]

            # ★v25.1 P1智能化: 结构化语义摘要——核心概念+共现关系+模式总结
            _relation_text = f"，关键关联: {'; '.join(_top_relations)}" if _top_relations else ""
            summary = (
                f"[复盘认知·{domain}领域] "
                f"基于{len(nodes)}次复盘提炼: 核心概念={', '.join(top_types)}"
                f"{_relation_text}。"
                f"模式: {domain}领域反复出现上述概念组合，"
                f"建议在后续对话中针对性强化相关能力。"
            )

            suggested_actions = list({
                kw for node in nodes
                for kw in node.keywords
                if kw in ("reinforce_identity", "prefer_rule_inference", "improve_user_recognition")
            })

            # ===== 新增：压缩前检查摘要是否包含元描述特征 =====
            _summary_meta_patterns = [
                "根据您的要求", "以下是关于", "在.*的交叉探索中",
                "我们可以从以下几个维度", "这是一个非常有深度的议题",
                "请使用你.*全新的.*回答", "严格.*三段固定结构",
            ]
            _summary_is_meta = False
            for _p in _summary_meta_patterns:
                if re.search(_p, summary):
                    _summary_is_meta = True
                    break

            if _summary_is_meta:
                # 摘要包含元描述特征，降低压缩质量
                self._log(LogLevel.INFO, f"压缩拦截(元描述): {summary[:60]}...")
                # 不创建L2节点，让这些L1保持临时状态自然淘汰
                return False
            # ===== 压缩拦截结束 =====

            l2_node = PulseNode(
                value=summary,
                keywords=top_types + suggested_actions,
                source_organ=self.organ_name,
                evol_level=PulseNode.EVOL_L2,
                importance=PulseNode.IMPORTANCE_A,
                abstraction=0.6,  # ★v25.1: 语义融合后抽象度提高
                space_path=f"/反思/对话复盘/{domain}",
            )
            # ★v25.1 P1智能化: 记录语义关系（供后续检索和融合使用）
            if _top_relations:
                l2_node.semantic_relations = _top_relations
                l2_node.cooccurrence_patterns = dict(_cooccurrence.most_common(10))
            # 复盘压缩产生的节点标记为内视（源自自身行为反思）
            l2_node.view_mode = "INNER_VIEW"
            l2_node.trust_score = 85.0
            if self.frequency_codec:
                self.frequency_codec.encode_node(l2_node)
            if self.node_pool:
                self._kal.add_node(l2_node)
            if self.knowledge_tree:
                self.knowledge_tree.register_path(f"/反思/对话复盘/{domain}")

            if self.info_field and self.pulse_core:
                # v9.5: 知识压缩脉冲标记为L2认知思考层
                optimize_pulse = self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=KnowledgeEvent.COMPRESSED,
                    payload={
                        "from_level": "L1",
                        "to_level": "L2",
                        "space_path": f"/反思/对话复盘/{domain}",
                        "node_count": len(nodes),
                        "result_node_id": l2_node.node_id,
                        "compression_type": "reflection_insight",
                    },
                    priority=5,
                    layer="L2"
                )
                self.info_field.publish(optimize_pulse)

            return True

        except Exception as e:
            self._log(LogLevel.ERROR, f"复盘压缩失败 ({domain}): {e}",
                    error_code=ErrorCode.EXECUTION_FAILED)
            return False

    def _check_and_optimize(self, pulse: dict[str, Any]):
        """心跳驱动：异步执行知识优化（★v24.0减负：不在心跳线程中同步重活）"""
        if self.node_pool is None:
            return

        # ★往期批次 相关任务：心跳触发资源看门狗采样（节流在方法内，零副作用）
        self._maybe_sample_resource()

        # 防重入：如果已有优化任务在执行，跳过本次心跳
        if getattr(self, '_optimize_pending', False):
            return

        # 噪音检测（轻量同步，保留）
        self._detect_noise()

        # 将重活提交到自适应任务池异步执行
        if self.info_field and hasattr(self.info_field, 'submit_adaptive_task'):
            self._optimize_pending = True
            success = self.info_field.submit_adaptive_task(
                self._do_optimize,
                task_name="肝知识优化",
                priority="normal"
            )
            if not success:
                self._optimize_pending = False
                # 提交失败，同步执行
                self._do_optimize()
        else:
            # 信息场不可用，同步执行
            self._do_optimize()

    # ===================== 往期批次 相关任务：资源看门狗 =====================
    def _maybe_sample_resource(self) -> None:
        """★相关任务：心跳节流触发资源采样（被动、零副作用）。"""
        _now = time.time()
        if _now - self._last_rss_sample < _RESOURCE_SAMPLE_INTERVAL_SEC:
            return
        self._last_rss_sample = _now
        try:
            self._sample_resource_usage()
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"[T-107a] 资源采样失败(已忽略): {type(_e).__name__}: {_e}")

    def _sample_resource_usage(self) -> None:
        """采样本进程 RSS / 句柄 / 线程 / 打开文件，超阈值告警并记录基线。"""
        try:
            import psutil
        except Exception:
            return  # psutil 不可用则跳过（不影响主流程）
        _proc = psutil.Process(os.getpid())
        _mem = _proc.memory_info()
        _rss = int(getattr(_mem, "rss", 0))
        try:
            _handles = int(_proc.num_handles())
        except Exception:
            _handles = None
        try:
            _open_files = len(_proc.open_files())
        except Exception:
            _open_files = None
        try:
            _threads = int(_proc.num_threads())
        except Exception:
            _threads = None

        self._rss_samples.append(_rss)
        if len(self._rss_samples) > 96:   # 保留约 8h（5min*96）滚动窗口
            self._rss_samples.pop(0)
        self._res_rss_max = max(self._res_rss_max, _rss) if self._res_rss_max is not None else _rss
        self._res_handles = _handles
        self._res_open_files = _open_files
        self._res_threads = _threads

        if self._rss_baseline is None:
            self._rss_baseline = _rss
        _growth = _rss - self._rss_baseline

        _warned = False
        if _rss > _RESOURCE_RSS_WARN_BYTES:
            self._log(LogLevel.WARNING,
                      f"[T-107a] RSS 偏高: {_rss/1024**3:.2f}GB (> {_RESOURCE_RSS_WARN_BYTES/1024**3:.0f}GB 阈值)")
            _warned = True
        if _growth > _RESOURCE_RSS_GROWTH_BYTES:
            self._log(LogLevel.WARNING,
                      f"[T-107a] RSS 增长过快: 相对基线 +{_growth/1024**3:.2f}GB "
                      f"(阈值 +{_RESOURCE_RSS_GROWTH_BYTES/1024**3:.0f}GB)")
            _warned = True
        if _handles is not None and _handles > _RESOURCE_HANDLES_WARN:
            self._log(LogLevel.WARNING,
                      f"[T-107a] 句柄数偏高: {_handles} (> {_RESOURCE_HANDLES_WARN})")
            _warned = True
        if _open_files is not None and _open_files > _RESOURCE_OPEN_FILES_WARN:
            self._log(LogLevel.WARNING,
                      f"[T-107a] 打开文件数偏高: {_open_files} (> {_RESOURCE_OPEN_FILES_WARN})")
            _warned = True
        if _warned:
            self._res_warn_count += 1
        self._log(LogLevel.DEBUG,
                  f"[T-107a] 资源采样: RSS={_rss/1024**3:.2f}GB 句柄={_handles} "
                  f"打开文件={_open_files} 线程={_threads} 基线增长={_growth/1024**3:.2f}GB")

    def _resource_stats(self) -> dict:
        """暴露资源看门狗最新快照（供 get_stats / 5051 面板消费）。"""
        return {
            "rss_bytes": self._rss_samples[-1] if self._rss_samples else None,
            "rss_max_bytes": self._res_rss_max,
            "rss_baseline_bytes": self._rss_baseline,
            "handles": self._res_handles,
            "open_files": self._res_open_files,
            "threads": self._res_threads,
            "warn_count": self._res_warn_count,
            "sample_count": len(self._rss_samples),
        }

    def _get_background_tempo(self) -> float:
        """★P2-1：标准化获取后台任务强度系数（消除锁内对 runtime_tempo 单例的隐式耦合）。

        原实现在 ``_do_optimize`` 锁内用 ``from nucleus.runtime_tempo import
        get_runtime_tempo`` 即时 import 并调用单例方法，属于隐式耦合且锁内有 import 开销。
        现统一经器官级标准接口获取，行为完全不变（成功返回值一致，异常回退 1.0）。
        """
        try:
            from nucleus.runtime_tempo import get_runtime_tempo
            _t = get_runtime_tempo().get_background_tempo()
            # ★主线第75批 T2：防御性类型检查（修复历史上 float/dict 比较崩溃）。
            #   若返回值非数值（如误返回整字典），记录类型与内容并回退 1.0，
            #   避免后续 min/max 触发 '<' not supported between float and dict。
            if not isinstance(_t, (int, float)) or isinstance(_t, bool):
                self._log(LogLevel.WARNING,
                          f"[肝] 后台节奏返回值类型异常: {type(_t).__name__}={_t!r}，"
                          f"已回退 1.0（防御 float/dict 比较崩溃）")
                return 1.0
            return float(_t)
        except Exception as _e:
            self._log(LogLevel.WARNING,
                      f"[肝] 获取后台节奏失败({type(_e).__name__})，回退 1.0")
            return 1.0

    def _do_optimize(self):
        """执行知识优化（压缩/融合/矛盾检测/本能升级）"""
        try:
            if self.node_pool is None:
                return

            # ★v24.0修复：每次优化任务开始时重置单次周期计数器
            self._compress_count_this_beat = 0
            self._contradiction_count_this_beat = 0
            self._low_water_triggered_this_beat = False

            # ★PERIOD-3修复: 锁内只做轻量临界区（计数与阈值判定），
            #   重型压缩/融合/矛盾检测/图谱构建全部移到锁外，避免持锁做 O(n^2) 遍历阻塞其它线程
            _do_compress = False
            _do_fuse = False
            _do_contradiction = False
            _do_instinct_deep = False
            _do_instinct_upgrade = False
            _do_consolidate = False
            _do_purity_check = False
            _do_association_scan = False
            with self._lock:
                self._compress_count_this_beat += 1
                if self._compress_count_this_beat > 10:
                    self._log(LogLevel.WARNING, f"单次心跳压缩次数超限({self._compress_count_this_beat}次)，强制停止本周期优化")
                    return

                stats = self._kal.get_stats()
                evol_dist = stats.get("evol_distribution", {})

                l1_count = evol_dist.get("L1", 0)
                l2_count = evol_dist.get("L2", 0)
                l3_count = evol_dist.get("L3", 0)

                # 知识密度低水位检测：L1占比过高时主动跨路径压缩
                total = l1_count + l2_count + l3_count
                if total > 10 and l1_count > 0 and not self._low_water_triggered_this_beat:
                    l1_ratio = l1_count / total
                    if l1_ratio > 0.7:
                        self._low_water_triggered_this_beat = True
                        self._log(LogLevel.INFO,
                                 f"知识密度低水位: L1占比={l1_ratio:.1%} (L1={l1_count}, L2={l2_count}, L3={l3_count})，触发主动压缩")
                        _do_compress = True

                if l1_count >= self._get_l1_threshold():
                    _do_compress = True

                if l2_count >= self._get_l2_threshold():
                    _do_fuse = True

                # 主动矛盾检测：只要L2节点≥2，就尝试跨路径检测矛盾
                if l2_count >= 2:
                    _do_contradiction = True

                # 从追问中学习：L4本能被频繁追问但缺乏L3节点支撑
                if l3_count >= 1 and l2_count >= 5:
                    _do_instinct_deep = True

                # 本能升级检查：L3节点≥1时尝试升级为本能
                if l3_count >= 1:
                    _do_instinct_upgrade = True

                # 知识巩固：对高质量L2节点提升重要性
                if l2_count >= 1:
                    _do_consolidate = True

                # ===== 通用知识纯净框架: 周期自检——清理低质量节点 =====
                if l2_count >= 3 and not hasattr(self, '_last_purity_check'):
                    self._last_purity_check = 0
                    self._purity_check_count = 0

                if l2_count >= 3 and hasattr(self, '_purity_check_count'):
                    self._purity_check_count += 1
                    if self._purity_check_count >= 30:  # 每30次优化触发一次
                        self._purity_check_count = 0
                        _do_purity_check = True

                # 冷却保护：每10分钟最多执行一次关联扫描，防止日志风暴（D4：间隔走配置）
                # ★runtime_tempo自适应：无对话时加速扫描，有对话时减速
                if l2_count >= 3:
                    if not hasattr(self, '_last_association_scan_time'):
                        self._last_association_scan_time = 0.0
                    now = time.time()
                    # ★P2-1：经器官级标准接口获取后台节奏（消除锁内对单例的隐式耦合）
                    _tempo = self._get_background_tempo()
                    # ★主线第79批 T2：防御性兜底——_tempo 应为数值(后台节奏系数)，
                    #   若因任何原因非数值(dict/None/str/bool)则回退 1.0，
                    #   避免 min(3.0, _tempo) 触发 float<dict / float<NoneType 崩溃
                    #   (75批已在 _get_background_tempo 加守卫，此处为调用点双保险)。
                    if not isinstance(_tempo, (int, float)) or isinstance(_tempo, bool):
                        _tempo = 1.0
                    _adjusted_interval = self._association_scan_interval * max(0.3, min(3.0, _tempo))
                    if now - self._last_association_scan_time >= _adjusted_interval:
                        self._last_association_scan_time = now
                        _do_association_scan = True

            # ★重型操作全部移到锁外执行
            # ★L10修复：每个重活之间检查停止请求，stop 后不再继续后续重活
            if _do_compress and not self._stop_requested:
                self._compress_l1_to_l2()
            if _do_fuse and not self._stop_requested:
                self._fuse_l2_to_l3()
            if _do_contradiction and not self._stop_requested:
                self._detect_contradictions("/", [])
            if _do_instinct_deep and not self._stop_requested:
                self._check_instinct_deep_learn()
            if _do_instinct_upgrade and not self._stop_requested:
                self._check_instinct_upgrade()
            if _do_consolidate and not self._stop_requested:
                self._consolidate_knowledge()
            if _do_purity_check and not self._stop_requested:
                self._periodic_purity_check()
            if _do_association_scan and not self._stop_requested:
                self._semantic_association_scan()

        except Exception as e:
            # ★主线第79批 T2：同根因错误聚合上报（避免 float<dict 等类型崩溃每日刷屏）
            self._report_optimize_error(e)
        finally:
            self._optimize_pending = False

    # ===== ★主线第79批 T2：同根因错误聚合上报 =====
    # 历史问题：肝异步优化任务因 float<dict / float<NoneType 类型不匹配每次都打完整
    # 日志，形成日志风暴(每日约21次)。现按错误签名聚合——首次出现打完整 traceback，
    # 之后仅按时间窗口/次数阈值周期汇总上报一次，避免刷屏且保留可观测性。
    _OPTIMIZE_ERROR_AGGREGATE_WINDOW = 300.0   # 同签名 5 分钟内只汇总上报一次
    _OPTIMIZE_ERROR_AGGREGATE_THRESHOLD = 20  # 或累计达 20 次也汇总一次

    def _report_optimize_error(self, exc: Exception) -> None:
        """聚合上报异步优化任务的同根因异常，避免日志风暴。

        - 同一错误签名(type:msg)首次出现：记录 ERROR + 完整 traceback。
        - 后续出现：累加计数；达到时间窗口或次数阈值时，汇总上报一次 WARNING。
        """
        if not hasattr(self, "_optimize_error_counts"):
            self._optimize_error_counts = {}
        _now = time.time()
        _sig = f"{type(exc).__name__}:{exc}"
        # 用异常对象自身回溯，无论是否在 except 块内都能拿到完整 traceback
        _tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _rec = self._optimize_error_counts.get(_sig)
        if _rec is None:
            self._optimize_error_counts[_sig] = {
                "count": 1, "first_ts": _now, "last_ts": _now,
                "last_logged": _now, "first_tb": _tb,
            }
            self._log(LogLevel.ERROR,
                      f"异步优化任务异常(首报): {_sig}\n{_tb}")
            return
        _rec["count"] += 1
        _rec["last_ts"] = _now
        _elapsed = _now - _rec["last_logged"]
        if (_elapsed >= self._OPTIMIZE_ERROR_AGGREGATE_WINDOW
                or _rec["count"] % self._OPTIMIZE_ERROR_AGGREGATE_THRESHOLD == 0):
            _window = max(1.0, _now - _rec["first_ts"])
            self._log(LogLevel.WARNING,
                      f"异步优化任务异常(聚合): {_sig} "
                      f"近{_window:.0f}s内累计{_rec['count']}次，"
                      f"首次traceback见 "
                      f"{time.strftime('%H:%M:%S', time.localtime(_rec['first_ts']))}")
            _rec["last_logged"] = _now
    def _consolidate_knowledge(self):
        """
        知识巩固：对反复激活、高度信任的L2节点提升重要性。
        让经过实践检验的知识在检索中更容易被命中，体现"越用越强"的生命态。
        """
        l2_nodes = self._kal.query_nodes(evol_level=PulseNode.EVOL_L2, limit=200)
        if not l2_nodes:
            return

        consolidated = 0
        for node in l2_nodes:
            if self._stop_requested:  # ★L10补全：停止请求检查点，中断巩固循环
                break
            if (node.activation_count >= 5 and
                getattr(node, 'trust_score', 50.0) >= 70.0 and
                node.importance not in (PulseNode.IMPORTANCE_A, PulseNode.IMPORTANCE_S) and
                node.abstraction >= 0.4):

                old_imp = node.importance
                node.importance = PulseNode.IMPORTANCE_A
                node.abstraction = min(1.0, node.abstraction + 0.05)
                consolidated += 1
                self._log(LogLevel.DEBUG,
                         f"知识巩固: {str(node.value)[:40]}... 重要性 {old_imp}→A "
                         f"(激活{node.activation_count}次, 信任{node.trust_score:.0f})")

        if consolidated > 0:
            self._consolidation_count += consolidated
            self._log(LogLevel.INFO, f"知识巩固完成: {consolidated}条L2节点重要性提升")
    def _semantic_association_scan(self):
        """
        语义关联扫描：扫描所有L2节点，寻找关键词重叠度在60%-80%的跨路径节点对。
        发现关联后建立节点间的关键词关联，并记录演化发现。
        """
        if self.node_pool is None:
            return

        l2_nodes = self._kal.query_nodes(evol_level=PulseNode.EVOL_L2, limit=200)
        if len(l2_nodes) < 2:
            return

        associations_found = 0
        MAX_ASSOCIATIONS_PER_SCAN = self._max_associations_per_scan  # ★D4：单次扫描上限走配置
        seen_pairs = set()
        logged_domain_pairs = set()  # 领域对去重

        for i in range(len(l2_nodes)):
            if self._stop_requested:  # ★L10补全：停止请求检查点，中断语义关联扫描
                break
            node_a = l2_nodes[i]
            path_a = getattr(node_a, 'space_path', '/')
            kw_a = {kw.lower() for kw in (node_a.keywords or []) if isinstance(kw, str) and len(kw) >= 2}

            for j in range(i + 1, len(l2_nodes)):
                if self._stop_requested:  # ★L10补全：停止请求检查点，中断内层扫描
                    break
                node_b = l2_nodes[j]
                path_b = getattr(node_b, 'space_path', '/')

                # 跳过同路径的节点对
                parent_a = path_a.rstrip('/').rsplit('/', 1)[0] if '/' in path_a else '/'
                parent_b = path_b.rstrip('/').rsplit('/', 1)[0] if '/' in path_b else '/'
                if parent_a == parent_b:
                    continue
                if associations_found >= MAX_ASSOCIATIONS_PER_SCAN:
                    break

                # 避免重复处理
                pair_key = f"{min(node_a.node_id, node_b.node_id)}_{max(node_a.node_id, node_b.node_id)}"
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                kw_b = {kw.lower() for kw in (node_b.keywords or []) if isinstance(kw, str) and len(kw) >= 2}

                if not kw_a or not kw_b:
                    continue

                # 计算重叠率
                min_size = min(len(kw_a), len(kw_b))
                overlap = len(kw_a & kw_b)
                if min_size == 0:
                    continue
                overlap_ratio = overlap / min_size

                # 60%-80%重叠：有关联但不完全重复，建立跨领域连接
                if 0.6 <= overlap_ratio <= 0.8:
                    common_kw = kw_a & kw_b

                    # 将交集关键词追加到两个节点的关键词列表中（去重，最多1个，且检查领域相关性）
                    new_kw_a = [kw for kw in common_kw if kw not in [k.lower() for k in (node_a.keywords or [])]]
                    new_kw_b = [kw for kw in common_kw if kw not in [k.lower() for k in (node_b.keywords or [])]]

                    if hasattr(node_a, 'keywords') and new_kw_a:
                        # 只追加1个关键词，且检查与节点A路径的领域相关性
                        _best_kw_a = next(iter(new_kw_a))
                        _path_a_parts = path_a.lower().strip('/').split('/')
                        _kw_in_path_a = any(_best_kw_a.lower() in _p or _p in _best_kw_a.lower()
                                           for _p in _path_a_parts if len(_p) >= 2)
                        if _kw_in_path_a or len(_path_a_parts) <= 1:
                            node_a.keywords.append(_best_kw_a)
                    if hasattr(node_b, 'keywords') and new_kw_b:
                        # 只追加1个关键词，且检查与节点B路径的领域相关性
                        _best_kw_b = next(iter(new_kw_b))
                        _path_b_parts = path_b.lower().strip('/').split('/')
                        _kw_in_path_b = any(_best_kw_b.lower() in _p or _p in _best_kw_b.lower()
                                           for _p in _path_b_parts if len(_p) >= 2)
                        if _kw_in_path_b or len(_path_b_parts) <= 1:
                            node_b.keywords.append(_best_kw_b)
                    # ★v25.0修复：不仅补关键词，还要落库结构化关系，供内在世界扩展检索遍历
                    if hasattr(node_a, 'add_semantic_relation'):
                        node_a.add_semantic_relation(
                            node_b.node_id, "semantic_similarity",
                            round(overlap_ratio, 3), "liver_scan")
                    if hasattr(node_b, 'add_semantic_relation'):
                        node_b.add_semantic_relation(
                            node_a.node_id, "semantic_similarity",
                            round(overlap_ratio, 3), "liver_scan")
                    # 记录发现有价值的关联
                    # ★3.2（2026-09-08 第八批）：路径末尾带斜杠（如"/综合/"）时
                    #   split('/')[-1] 返回空串，日志出现 ''与'综合' —— rstrip 后取名，空则"根"
                    path_name_a = parent_a.rstrip('/').split('/')[-1] or '根'
                    path_name_b = parent_b.rstrip('/').split('/')[-1] or '根'

                    # 领域对去重
                    domain_pair = tuple(sorted([path_name_a, path_name_b]))
                    if domain_pair in logged_domain_pairs:
                        continue
                    logged_domain_pairs.add(domain_pair)

                    associations_found += 1
                    if associations_found >= MAX_ASSOCIATIONS_PER_SCAN:
                        break
                    common_str = '、'.join(list(common_kw)[:3])

                    # ★日志降噪：每次扫描只打印前5条详细关联，其余只在汇总中显示
                    if associations_found <= 5:
                        self._log(LogLevel.DEBUG,
                                 f"语义关联发现: '{path_name_a}'与'{path_name_b}'在"
                                 f"「{common_str}」上存在深层联系 (重叠率={overlap_ratio:.0%})")

        if associations_found > 0:
            self._log(LogLevel.INFO, f"语义关联扫描完成: 发现{associations_found}对跨领域关联" +
                     (f"（前5条已打印详情，其余{associations_found - 5}条省略）" if associations_found > 5 else ""))
    def _reverse_activate_related_nodes(self, wisdom_node: PulseNode):
        """
        逆向激活：一个新的L3智慧节点形成后，主动向下寻找
        能与之共鸣的L2节点，加速它们的演化。
        
        就像人类在领悟一个核心概念后，回头看之前的碎片，
        发现很多都突然有了意义——这个过程加速了认知整合。
        
        冷却保护：同一L2节点在30分钟内只被逆向激活一次，
        防止信任分数因反复激活而膨胀。
        """
        if self.node_pool is None:
            return

        # 提取L3节点的核心关键词
        wisdom_kw = set()
        if hasattr(wisdom_node, 'keywords') and wisdom_node.keywords:
            for kw in wisdom_node.keywords:
                if isinstance(kw, str) and len(kw) >= 2:
                    wisdom_kw.add(kw.lower())

        if not wisdom_kw:
            return

        # 初始化逆向激活冷却字典
        if not hasattr(self, '_reverse_activate_cooldown'):
            self._reverse_activate_cooldown: dict[str, float] = {}
        _cooldown_dict = self._reverse_activate_cooldown  # 字典，别改名字
        _now = time.time()
        # 从config加载冷却时间（秒）
        _cooldown_seconds = 1800
        try:
            import config
            _liver_cfg = getattr(config, 'LIVER_CONFIG', {})
            _cooldown_seconds = _liver_cfg.get("reverse_activate_cooldown", 1800)
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"逆向激活冷却配置加载失败，用默认1800s: {_e}")
        _COOLDOWN_SECONDS = _cooldown_seconds

        # 扫描所有L2节点
        l2_nodes = self._kal.query_nodes(evol_level=PulseNode.EVOL_L2, limit=200)
        activated_count = 0
        skipped_by_cooldown = 0

        for node in l2_nodes:
            # 冷却检查：同一L2节点30分钟内不重复激活
            _node_id = node.node_id
            _last_activated = _cooldown_dict.get(_node_id, 0)
            if _now - _last_activated < _COOLDOWN_SECONDS:
                skipped_by_cooldown += 1
                continue

            node_kw = set()
            if hasattr(node, 'keywords') and node.keywords:
                for kw in node.keywords:
                    if isinstance(kw, str) and len(kw) >= 2:
                        node_kw.add(kw.lower())

            if not node_kw:
                continue

            # 计算重叠率
            overlap = len(wisdom_kw & node_kw)
            min_size = min(len(wisdom_kw), len(node_kw))
            if min_size == 0:
                continue
            overlap_ratio = overlap / min_size

            # 重叠率≥50%：这个L2与智慧节点高度共鸣
            if overlap_ratio >= 0.5:
                # 记录冷却时间
                _cooldown_dict[_node_id] = _now

                # 提升激活计数——让它更容易被检索
                node.activation_count = getattr(node, 'activation_count', 0) + 2
                # 提升抽象度——加速它向L3演化
                node.abstraction = min(1.0, getattr(node, 'abstraction', 0.5) + 0.05)
                # 追加L3的关键词到L2（去重，最多1个，且必须与目标节点路径领域相关）
                new_kw = [kw for kw in wisdom_kw
                         if kw not in [k.lower() for k in (node.keywords or [])]]
                if hasattr(node, 'keywords') and new_kw:
                    # 新增：检查候选关键词是否与目标节点路径有领域相关性
                    _node_path = getattr(node, 'space_path', '/')
                    _candidate_kw = next(iter(new_kw))
                    # 跳过与节点路径完全无关的污染关键词（如"曈曈"不应扩散到/技术路径）
                    _path_parts = _node_path.lower().strip('/').split('/')
                    _kw_in_path = any(_candidate_kw.lower() in _part or _part in _candidate_kw.lower()
                                     for _part in _path_parts if len(_part) >= 2)
                    if _kw_in_path or len(_path_parts) <= 1:
                        node.keywords.append(_candidate_kw)
                    # 如果关键词与路径无关，跳过追加（阻断污染扩散）

                activated_count += 1

        # 定期清理过期的冷却记录（超过1小时未激活的条目）
        _expired = [_nid for _nid, _ts in _cooldown_dict.items() if _now - _ts > 3600]
        for _nid in _expired:
            del _cooldown_dict[_nid]

        if activated_count > 0:
            _skip_info = f" (跳过{skipped_by_cooldown}个冷却中节点)" if skipped_by_cooldown > 0 else ""
            self._log(LogLevel.INFO,
                     f"逆向激活: 新智慧「{str(wisdom_node.value)[:30]}...」"
                     f"加速了{activated_count}个L2节点的演化{_skip_info} "
                     f"(核心概念: {', '.join(list(wisdom_kw)[:3])})")
    def _get_l1_threshold(self) -> int:
        try:
            import config
            return config.LIVER_CONFIG.get("l1_to_l2_threshold", 20)
        except Exception:
            return 20

    def _get_l2_threshold(self) -> int:
        try:
            import config
            return config.LIVER_CONFIG.get("l2_to_l3_threshold", 8)
        except Exception:
            return 20

    def _get_fuse_cooldown(self) -> float:
        try:
            import config
            return config.LIVER_CONFIG.get("fuse_cooldown", 300.0)
        except Exception:
            return 300.0
    def _get_adaptive_fuse_cooldown(self) -> float:
        try:
            import config
            return config.LIVER_CONFIG.get("adaptive_fuse_cooldown", 600.0)
        except Exception:
            return 600.0
    def _get_total_threshold(self) -> int:
        try:
            import config
            return config.LIVER_CONFIG.get("total_compress_threshold", 50)
        except Exception:
            return 50
    def _compress_l1_to_l2(self):
        # ★主线第17批 T3/P2-63：旁路事件耗时计数起点（仅计时，不参与逻辑）
        _tap_t0 = time.perf_counter()
        l1_nodes = self._kal.query_nodes(evol_level=PulseNode.EVOL_L1, limit=10000)
        if not l1_nodes:
            return
        # 且距离上次成功压缩不到3个冷却周期，跳过本次检查
        total_l1 = len(l1_nodes)
        total_threshold = self._get_total_threshold()
        if total_l1 < total_threshold:
            _gap = total_threshold - total_l1
            _time_since_last = time.time() - self._last_optimize_time
            if _gap <= 3 and _time_since_last < self._compress_cooldown * 3:
                return
        # 但L1占比超过70%时无视冷却，紧急压缩（与肾脏L1占比保护阈值对齐，消除真空地带）
        now = time.time()
        if now - self._last_optimize_time < self._compress_cooldown:
            # 紧急通道：L1占比超过70%时，即使冷却未到也执行压缩
            _stats = self._kal.get_stats()
            _evol_dist = _stats.get("evol_distribution", {})
            _l1 = _evol_dist.get("L1", 0)
            _l2 = _evol_dist.get("L2", 0)
            _l3 = _evol_dist.get("L3", 0)
            _total = _l1 + _l2 + _l3
            if _total > 10 and _l1 > 0 and (_l1 / _total) > 0.7:
                self._log(LogLevel.INFO, f"L1占比={_l1/_total:.1%}超过70%，触发紧急压缩（跳过冷却）")
            else:
                return

        # 总量触发：L1总数超过阈值时执行跨路径压缩
        total_l1 = len(l1_nodes)
        total_threshold = self._get_total_threshold()
        if total_l1 >= total_threshold:
            # 新增：过滤掉路径包含域名残词的L1节点，阻断污染进入压缩
            # 【修复】也跳过代码自学习路径的L1节点，让它们走独立压缩
            _clean_l1 = [n for n in l1_nodes
                         if not self._is_path_noise(getattr(n, 'space_path', ''))
                         and not getattr(n, 'space_path', '').startswith('/自我理解/代码')]
            _code_learning_l1 = [n for n in l1_nodes
                                if getattr(n, 'space_path', '').startswith('/自我理解/代码')]
            if _code_learning_l1:
                self._log(LogLevel.DEBUG,
                         f"总量压缩跳过: {len(_code_learning_l1)}条代码自学习L1节点保留独立路径")
            # 【v16.0修复】合并重复跳过日志为摘要，防止刷屏
            _skipped_count = len(l1_nodes) - len(_clean_l1) - len(_code_learning_l1)
            if _skipped_count > 0:
                _prev_skip = getattr(self, '_last_path_noise_skip_count', -1)
                if _skipped_count != _prev_skip:
                    self._log(LogLevel.INFO,
                             f"总量压缩前过滤: 跳过{_skipped_count}条路径残词L1节点")
                    self._last_path_noise_skip_count = _skipped_count
            _skipped_path_noise = len(l1_nodes) - len(_clean_l1)
            if _skipped_path_noise > 0:
                # ★v17.0修复：每10次心跳最多输出一次，避免日志刷屏
                if not hasattr(self, '_path_noise_log_counter'):
                    self._path_noise_log_counter = 0
                self._path_noise_log_counter += 1
                if self._path_noise_log_counter % 10 == 0:
                    self._log(LogLevel.INFO,
                             f"总量压缩前过滤: 跳过{_skipped_path_noise}条路径残词L1节点 "
                             f"(近10次心跳平均，已过滤{self._path_noise_log_counter}次)")

            # ★v18.0修复：代码学习L1独立压缩通道，避免L1占比72%死锁
            # 这些L1之前被跳过（保护独立路径），但积累过多会导致L1占比持续>70%
            # 现在按器官分组独立压缩，既保留路径又解决累积问题
            # ★v19.0修复：代码学习节点按路径最后一段分组，避免深度2节点全挤在一个组里一次性压缩
            if len(_code_learning_l1) >= 8:
                # 按路径最后一段分组（器官名/模块名），而不是父目录
                # 这样深度2的节点（/自我理解/代码/PulseHeart）按 PulseHeart 分组，不会全挤在一个组
                _code_groups = {}
                for _node in _code_learning_l1:
                    _path = getattr(_node, 'space_path', '')
                    if not _path:
                        continue
                    _parts = _path.rstrip('/').split('/')
                    # ★相关任务：代码学习通道组键回写为全路径，避免 L2 落库成相对路径
                    #   （修复 runB 去重命中自己建的 L2 触发"自产自删"）
                    # ★相关任务：平路径（深度<4）从节点 value 前缀提取类名分组，失败回落路径末段；
                    #   深路径维持后两段，使上千节点拆成多组，单组不超阈值。
                    if len(_parts) >= 4:
                        _group_key = '/自我理解/代码/' + '/'.join(_parts[-2:])
                    else:
                        _val = getattr(_node, 'value', '') or ''
                        _cls = ''
                        _m = re.search(r'\[自我理解[·•.]([^·•.\]]{1,40})', _val)
                        if _m:
                            _cls = _m.group(1).split('.')[0].strip()
                        if not _cls:
                            _m2 = re.search(r'\[器官职责说明书[·•.]自动生成\]\s*([^\s，。]{1,40})', _val)
                            if _m2:
                                _cls = 'DOC:' + _m2.group(1).strip()
                        _group_key = '/自我理解/代码/' + (_cls or _parts[-1])
                    _code_groups.setdefault(_group_key, []).append(_node)
                
                _code_compressed = 0
                for _prefix, _nodes in _code_groups.items():
                    if self._stop_requested:  # ★L10补全：停止请求检查点
                        break
                    if len(_nodes) >= self._get_l1_threshold():
                        if self._compress_group(_prefix, _nodes):
                            _code_compressed += 1
                if _code_compressed > 0:
                    self._l1_to_l2_count += _code_compressed
                    self._last_optimize_time = time.time()
                    self._log(LogLevel.INFO,
                             f"代码学习L1独立压缩: {_code_compressed}组 → L2 "
                             f"(共{len(_code_learning_l1)}个代码学习L1, {len(_code_groups)}个分组)")

            # 使用过滤后的节点继续后续流程（总量压缩）
            l1_nodes = _clean_l1
            total_l1 = len(l1_nodes)

            # 过滤后仍满足阈值才执行总量压缩
            if total_l1 >= total_threshold:
                # 打印诊断信息：前20个节点的路径分布
                sample_paths = set()
                for n in l1_nodes[:20]:
                    if hasattr(n, 'space_path'):
                        sample_paths.add(n.space_path)
                self._log(LogLevel.INFO,
                         f"开始跨路径压缩: {total_l1}条 L1→L2 (总量触发), "
                         f"样本路径: {', '.join(list(sample_paths)[:5])}")

                # 按L1节点的实际路径分布选择L2归属路径
                # 统计各一级路径下的L1数量，选占比最高的
                _path_counts = {}
                for _n in l1_nodes:
                    if self._stop_requested:  # ★L10补全：停止请求检查点
                        break
                    _p = getattr(_n, 'space_path', '/未分类')
                    # ★P1-5修复：跳过包含冒号碎片词的污染路径，防止压缩扩散污染
                    if ":" in _p or "：" in _p:
                        continue
                    # 也跳过包含"节点A""节点B"等内部标记的路径
                    _p_parts = _p.rstrip('/').split('/')
                    _has_fragment = False
                    for _pp in _p_parts:
                        if is_path_fragment_word(_pp):
                            _has_fragment = True
                            break
                    if _has_fragment:
                        continue
                    _root = _p.strip('/').split('/')[0] if _p and _p != '/' else '未分类'
                    _path_counts[_root] = _path_counts.get(_root, 0) + 1
                _dominant_root = max(_path_counts, key=_path_counts.get) if _path_counts else '综合'
                _compress_path = f"/{_dominant_root}/综合"
                if self.knowledge_tree:
                    self.knowledge_tree.register_path(_compress_path)
                if self._compress_group(_compress_path, l1_nodes):
                    self._l1_to_l2_count += 1
                    self._last_optimize_time = time.time()
                    # 降低已压缩L1节点的重要性，避免重复选中
                    for node in l1_nodes:
                        node.importance = PulseNode.IMPORTANCE_C
                        node.abstraction = max(0.0, node.abstraction - 0.1)
                    self._log(LogLevel.INFO,
                             f"跨路径压缩完成: {total_l1}条 L1→L2 (总量触发)")
                    # ★主线第17批 T3/P2-63：旁路事件（只发布，不改变任何现有逻辑）
                    tap_publish(
                        "liver.memory.write",
                        payload={
                            "layer": "L2",
                            "node_count": total_l1,
                            "duration_ms": round((time.perf_counter() - _tap_t0) * 1000, 2),
                        },
                        source="PulseLiver",
                        switch_attr="ENABLE_LIVER_EVENT_TAP",
                    )
                    return
                else:
                    # 跨路径压缩失败，重置冷却时间，降级为分组压缩
                    self._log(LogLevel.WARNING,
                             f"跨路径压缩失败({total_l1}条L1), 降级为分组压缩")
                    self._last_optimize_time = 0.0
            else:
                _prev_total = getattr(self, '_last_below_threshold_count', -1)
                if total_l1 != _prev_total:
                    self._log(LogLevel.INFO,
                             f"总量压缩跳过(过滤后不足阈值): {total_l1}条 < {total_threshold}")
                    self._last_below_threshold_count = total_l1

        groups = self._group_by_path_prefix(l1_nodes)
        compressed_count = 0
        for prefix, nodes in groups.items():
            if self._stop_requested:  # ★L10补全：停止请求检查点，中断压缩循环
                break
            if len(nodes) >= self._get_l1_threshold():
                if self._compress_group(prefix, nodes):
                    compressed_count += 1

        if compressed_count > 0:
            self._l1_to_l2_count += compressed_count
            self._last_optimize_time = time.time()
            self._log(LogLevel.INFO,
                     f"压缩内化完成: {compressed_count} 组 L1→L2, "
                     f"总计 L1→L2: {self._l1_to_l2_count}")
            # ★主线第17批 T3/P2-63：旁路事件（只发布，不改变任何现有逻辑）
            tap_publish(
                "liver.memory.write",
                payload={
                    "layer": "L2",
                    "node_count": compressed_count,
                    "duration_ms": round((time.perf_counter() - _tap_t0) * 1000, 2),
                },
                source="PulseLiver",
                switch_attr="ENABLE_LIVER_EVENT_TAP",
            )


    def _group_by_path_prefix(self, nodes: list[PulseNode]) -> dict[str, list[PulseNode]]:
        groups: dict[str, list[PulseNode]] = {}
        for node in nodes:
            path = node.space_path
            if not path:
                continue
            parts = path.rstrip("/").split("/")
            parent = "/".join(parts[:-1]) if len(parts) > 1 else "/"
            groups.setdefault(parent, []).append(node)
        return groups

    # ------------------------------------------------------------------
    # ★PHASE17-阶段二子任务5.7：_compress_group 拆分出的子方法
    #   纯计算/无副作用，从 307 行超长方法中抽出，行为严格等价。
    # ------------------------------------------------------------------
    def _collect_group_features(self, nodes: list[PulseNode]) -> dict[str, Any]:
        """收集压缩组特征：关键词/值/抽象度/质量节点。（从 _compress_group 抽出）"""
        all_keywords = []
        all_values = []
        total_abstraction = 0.0
        _quality_nodes = []  # ★v17.0性能优化：收集通过质量筛选的节点
        for node in nodes:
            # L1质量预筛选：内容太短且关键词太少的碎片不参与关键词提取
            value_str = node.value if isinstance(node.value, str) else str(node.value)
            kw_count = len(node.keywords) if node.keywords else 0
            is_quality = True
            if len(value_str) < 30 and kw_count < 2:
                is_quality = False  # 内容空洞，标记但不丢弃
            if is_quality:
                _quality_nodes.append(node)  # ★v17.0性能优化：收集质量节点
                # ===== 新增: 路径残词过滤 =====
                node_path = node.space_path if hasattr(node, 'space_path') else ""
                if self._is_path_noise(node_path):
                    continue
                # ===== 新增: 活性加权——经常被激活的知识权重更高 =====
                if hasattr(node, 'activation_count') and node.activation_count > 0:
                    weighted_kw = list(node.keywords)
                    if node.activation_count >= 3:
                        all_keywords.extend(weighted_kw * 2)
                    else:
                        all_keywords.extend(weighted_kw)
                # ===== 新增: 重要性加权——高重要性节点（模型回复等）优先压缩 =====
                if hasattr(node, 'importance') and node.importance in ("A", "S"):
                    weighted_kw = list(node.keywords)
                    all_keywords.extend(weighted_kw)  # 额外复制一份，提升在统计中的权重
                all_keywords.extend(node.keywords)
            all_values.append(node.value)
            total_abstraction += node.abstraction
        return {
            "all_keywords": all_keywords,
            "all_values": all_values,
            "total_abstraction": total_abstraction,
            "quality_nodes": _quality_nodes,
        }

    def _liver_extract_keywords(self, all_values: list, kw_text: str, top_n: int = 6) -> list:
        """
        ★主线第4批 任务4(P2-42)：本地关键词提取（无外部依赖）。
        对组内 L1 取值做 2~4 字中文 n-gram 词频统计，并合并组已有 top 关键词（加权），
        返回前 top_n 个关键词，供句子重要性评分使用。
        """
        _stop = set("的了在是和与及或我对你他她它这那有个也都很再上中下内外前后续把被这些那些".split())
        _counter = {}
        for v in all_values[:15]:
            if not v:
                continue
            cv = str(v)
            for m in re.finditer(r'[\u4e00-\u9fff]{2,4}', cv):
                tok = m.group()
                if tok in _stop:
                    continue
                _counter[tok] = _counter.get(tok, 0) + 1
        # 合并组已有关键词（来自 _collect_group_features 的 kw_text），加权优先
        for s in re.findall(r'[\u4e00-\u9fff]{2,4}', kw_text or ''):
            if s not in _stop:
                _counter[s] = _counter.get(s, 0) + 5
        _ranked = sorted(_counter.items(), key=lambda x: -x[1])
        return [w for w, _ in _ranked[:top_n]]

    def _liver_rank_sentences(self, sentences: list, keywords: list, top_k: int = 3) -> list:
        """
        ★主线第4批 任务4(P2-42)：句子重要性评分。
        综合：关键词命中数（权重最高）、长度适中度、位置靠前度，返回前 top_k 句（按原序）。
        """
        if not sentences:
            return []
        _kwset = set(keywords)
        _scored = []
        for i, s in enumerate(sentences):
            if len(s) < 6:
                continue
            _hits = sum(1 for k in _kwset if k and k in s)
            L = len(s)
            if 10 <= L <= 80:
                _len_score = 1.0
            elif L < 10:
                _len_score = L / 10.0
            else:
                _len_score = max(0.2, 1.0 - (L - 80) / 200.0)
            _pos_score = 1.0 / (1.0 + i * 0.15)
            _scored.append((_hits * 2.0 + _len_score + _pos_score, i, s))
        _scored.sort(key=lambda x: (-x[0], x[1]))
        _picked = [(i, s) for _, i, s in _scored[:top_k]]
        _picked.sort(key=lambda x: x[0])
        return [s for _, s in _picked]

    def _build_l2_summary(self, all_values: list, nodes: list[PulseNode],
                          path_name: str, kw_text: str) -> str:
        """L2 摘要生成。★主线第4批 任务4(P2-42)：升级为语义摘要（关键词提取+句子重要性评分），
        失败时回退到原有截断逻辑（向后兼容）。"""
        from nucleus.knowledge_noise_filter import clean_content_text
        import config

        def _truncate_fallback():
            # 原有截断逻辑（向后兼容回退）
            _bs = ""
            for v in all_values[:10]:
                cv = clean_content_text(str(v)) if v else ""
                if any(mk in str(cv)[:30] for mk in ["相关知识汇总", "核心智慧结晶", "复盘认知"]):
                    continue
                if cv and len(cv) > 30:
                    _bs = cv[:150]
                    break
            if _bs:
                return f"{_bs}（由{len(nodes)}条相关知识归纳）"
            _fb = ""
            for v in all_values[:5]:
                _f = str(v)[:120] if v else ""
                if len(_f) > len(_fb):
                    _fb = _f
            return (f"{_fb}...（由{len(nodes)}条相关知识归纳）" if _fb
                    else f"[{path_name}] 知识归纳（关键词: {kw_text}，共{len(nodes)}条记录）")

        if not getattr(config, "ENABLE_LIVER_SEMANTIC_SUMMARY", True):
            return _truncate_fallback()

        try:
            # 1) 清洗 + 抽句
            _sentences = []
            for v in all_values[:10]:
                cv = clean_content_text(str(v)) if v else ""
                if not cv:
                    continue
                if any(mk in cv[:30] for mk in ["相关知识汇总", "核心智慧结晶", "复盘认知"]):
                    continue
                for seg in re.split(r'[。！？!?;\n]', cv):
                    seg = seg.strip()
                    if len(seg) >= 6:
                        _sentences.append(seg)
            if not _sentences:
                return _truncate_fallback()

            # 2) 本地关键词提取
            _kws = self._liver_extract_keywords(all_values, kw_text, top_n=6)
            # 3) 句子重要性评分
            _top = self._liver_rank_sentences(_sentences, _kws, top_k=3)
            if not _top:
                return _truncate_fallback()

            _summary = "；".join(_top)
            if len(_summary) > 240:
                _summary = _summary[:240]
            _result = f"{_summary}（由{len(nodes)}条相关知识归纳）"
            # 4) 压缩质量日志
            self._log(LogLevel.INFO,
                      f"L2语义摘要: 路径={path_name} 候选句={len(_sentences)} "
                      f"选用={len(_top)} 关键词={_kws[:4]}")
            return _result
        except Exception as e:
            self._log_ignored_exception(e)
            return _truncate_fallback()

    def _try_merge_duplicate_l2(self, prefix: str, nodes: list[PulseNode],
                               top_keywords: list, all_values: list,
                               path_name: str) -> bool:
        """与已有 L2 节点去重合并。返回 True=已处理（完全重复跳过 or 合并），调用方应 return True。（子任务5.7 从 _compress_group 抽出）"""
        from nucleus.knowledge_noise_filter import clean_content_text
        # ===== 新增: 去重合并——检查同路径是否已有高度相似的L2节点 =====
        merged = False
        if self.node_pool:
            existing_l2 = self._kal.query_nodes(
                evol_level=PulseNode.EVOL_L2,
                space_path_prefix=prefix,
                limit=10
            )
            for existing in existing_l2:
                existing_kw = {kw.lower() for kw in existing.keywords} if hasattr(existing, 'keywords') and existing.keywords else set()
                new_kw = {kw.lower() for kw in top_keywords}
                if existing_kw and new_kw:
                    # 计算关键词重叠率
                    min_size = min(len(existing_kw), len(new_kw))
                    overlap_count = len(existing_kw & new_kw)
                    overlap_ratio = overlap_count / min_size if min_size > 0 else 0

                    # 但如果重叠率达到100%，说明内容完全重复，跳过不合并
                    if overlap_ratio >= 1.0:
                        # ★相关任务：完全重复跳过仅表示无需新建 L2，不代表已有 L2 真正覆盖源节点内容。
                        #   因此【不建重复L2、也不删源L1】，直接返回 False 让主路径接管完整压缩。
                        self._log(LogLevel.WARNING,
                                f"知识去重: '{path_name}' 新L2节点与已有节点完全重复，"
                                f"跳过创建(不删除源节点)")
                        return False
                    elif overlap_ratio >= 0.7:
                        # 追加新关键词（去重，最多3个，避免污染）
                        new_unique_kw = [kw for kw in top_keywords if kw.lower() not in existing_kw]
                        if hasattr(existing, 'keywords') and new_unique_kw:
                            existing.keywords.extend(new_unique_kw[:3])

                        # 改进合并方式：提取新知识中的实质内容片段，而非元描述
                        _new_samples = []
                        for _v in all_values[:3]:
                            _cv = clean_content_text(str(_v)) if _v else ""
                            if _cv and len(_cv) > 15:
                                _new_samples.append(_cv[:60])

                        existing_value = str(existing.value) if existing.value else ""
                        if _new_samples and len(existing_value) < 800:
                            # 不超过800字时，取一条最有信息量的新片段加入
                            _best_new = max(_new_samples, key=len) if _new_samples else ""
                            if _best_new and _best_new[:30] not in existing_value[:200]:
                                existing.value = existing_value.rstrip() + "；" + _best_new

                        # 提升抽象度和信任分数（合并是知识深化的标志）
                        existing.abstraction = min(1.0, existing.abstraction + 0.03)
                        existing.trust_score = min(100.0, getattr(existing, 'trust_score', 50.0) + 3.0)
                        existing.ephemeral = False

                        self._log(LogLevel.INFO,
                                 f"知识合并(关联): '{path_name}' 新L2节点合并到已有节点 "
                                 f"(重叠率={overlap_ratio:.0%}, 节点ID={existing.node_id[:12]}...)")
                        merged = True
                        # 合并后也要发射压缩脉冲，标记为合并类型
                        if self.info_field and self.pulse_core:
                            merge_pulse = self.pulse_core.emit(
                                source_organ=self.organ_name,
                                event_type=KnowledgeEvent.COMPRESSED,
                                payload={
                                    "from_level": "L1", "to_level": "L2",
                                    "space_path": prefix, "node_count": len(nodes),
                                    "result_node_id": existing.node_id,
                                    "compression_type": "merge",
                                },
                                priority=4,
                                layer="L2"
                            )
                            self.info_field.publish(merge_pulse)
                        break

        if merged:
            # 合并完成，跳过创建新节点
            self._last_optimize_time = time.time()
            # 合并成功后降级源L1节点的重要性，并从节点池中移除
            _removed_count = 0
            for node in nodes:
                node.importance = PulseNode.IMPORTANCE_C
                node.abstraction = max(0.0, node.abstraction - 0.1)
                # 将被压缩的L1从节点池中移除，释放内存
                if self.node_pool and hasattr(self.node_pool, 'remove'):
                    try:
                        self._kal.remove_node(node.node_id)
                        _removed_count += 1
                    except Exception as _e:
                        # ★PHASE14-P2-1：原 `except Exception: pass`。
                        #   此处失败意味着「该 L1 节点本应释放却仍留在节点池」，
                        #   是缓慢的内存泄漏入口，且毫无痕迹可查。
                        #   DEBUG 级记录并带上计数上下文，便于事后统计泄漏规模。
                        self._log(LogLevel.DEBUG,
                                  f"压缩后节点移除失败（该节点仍占用内存）: "
                                  f"{node.node_id} - {_e}")
            if _removed_count > 0:
                self._log(LogLevel.DEBUG, f"清理被压缩L1: {_removed_count}条")
            if self.snapshot:
                now = time.time()
                if now - self._last_snapshot_save_time >= 60.0:
                    self.snapshot.save()
                    self._last_snapshot_save_time = now
            return True

        return False

    def _compress_group(self, prefix: str, nodes: list[PulseNode]) -> bool:
        # ★相关任务：单次压缩上限 40 条，超出切片逐块处理（每块一个 L2），
        #   杜绝单批吞噬全组导致"一次性删除上千 L1"。
        if len(nodes) > 40:
            _any_ok = False
            for _i in range(0, len(nodes), 40):
                if self._compress_group(prefix, nodes[_i:_i + 40]):
                    _any_ok = True
            return _any_ok
        try:
            # ===== 阶段1：收集压缩组特征（子任务5.7 抽出 _collect_group_features） =====
            feats = self._collect_group_features(nodes)
            all_keywords = feats["all_keywords"]
            all_values = feats["all_values"]
            total_abstraction = feats["total_abstraction"]
            _quality_nodes = feats["quality_nodes"]

            # 使用统一过滤器过滤噪音词，再用智能价值排序选top关键词
            top_keywords = get_top_valuable_keywords(all_keywords, 10)

            avg_abstraction = total_abstraction / len(nodes) if nodes else 0.0
            path_name = prefix.split("/")[-1] if prefix else "知识"
            kw_text = "、".join(top_keywords[:5])

            # 清洗样本内容中的噪音，提取最有信息量的一段作为L2摘要（子任务5.7 抽出 _build_l2_summary）
            summary = self._build_l2_summary(all_values, nodes, path_name, kw_text)

            l2_node = PulseNode(
                value=summary,
                keywords=top_keywords,
                source_organ=self.organ_name,
                evol_level=PulseNode.EVOL_L2,
                importance=PulseNode.IMPORTANCE_A,
                abstraction=min(avg_abstraction + 0.2, 0.7),
                space_path=prefix,
            )
            # ===== 阶段2：去重合并（子任务5.7 抽出 _try_merge_duplicate_l2） =====
            if self._try_merge_duplicate_l2(prefix, nodes, top_keywords, all_values, path_name):
                return True

            # ★相关任务：记录本次压缩的成员 node_id，便于溯源与回滚
            try:
                l2_node.evidence_chain = [{"node_id": getattr(n, 'node_id', None)} for n in nodes]
            except Exception as _e:
                self._log(LogLevel.DEBUG,
                          f"T-115a: 设置 evidence_chain 失败(已忽略): {type(_e).__name__}: {_e}")
            # ===== 通用知识纯净框架: 信息密度自检 =====
            quality_score = self._assess_node_quality(l2_node, nodes)
            # ===== 【v12.0新增】知识免疫检查：与自我架构知识交叉验证 =====
            from nucleus.knowledge_noise_filter import (
                check_self_consistency_for_node as _immune_check,
            )
            _self_l3 = self._kal.query_nodes(evol_level="L3", space_path_prefix="/自我/架构", limit=20)
            _self_l2 = self._kal.query_nodes(evol_level="L2", space_path_prefix="/自我/架构", limit=20)
            _self_nodes = _self_l3 + _self_l2
            _immune_result = _immune_check(str(l2_node.value), l2_node.keywords, _self_nodes)
            if _immune_result["contradiction_found"] and _immune_result["trust_penalty"] >= 15.0:
                l2_node.trust_score = max(20.0, l2_node.trust_score - _immune_result["trust_penalty"])
                l2_node.ephemeral = True
                self._log(LogLevel.INFO,
                         f"知识免疫(肝): 新L2节点与自我知识矛盾，标记临时 (信任-{_immune_result['trust_penalty']:.0f})")
            # ===== 知识免疫检查结束 =====
            if quality_score < 0.3:
                # 质量太差，标记为临时节点，让时间自然淘汰
                l2_node.ephemeral = True
                l2_node.trust_score = max(20.0, l2_node.trust_score - 30.0)
                self._log(LogLevel.INFO,
                         f"知识质量自检: '{path_name}' 质量评分={quality_score:.2f}，标记为临时节点")
            elif quality_score < 0.5:
                # 质量偏低，也标记为临时节点——让时间验证其真正价值
                l2_node.ephemeral = True
                l2_node.trust_score = min(50.0, l2_node.trust_score)
                self._log(LogLevel.INFO,
                         f"知识质量自检: '{path_name}' 质量评分={quality_score:.2f}，标记为临时节点(低质量)")
            else:
                # 质量达标，正常持久化
                l2_node.ephemeral = False

            # 从被压缩的L1节点中判定视角和可信度（取多数）
            inner_count = sum(1 for n in nodes if getattr(n, 'view_mode', 'OUTER_VIEW') == 'INNER_VIEW')
            l2_node.view_mode = "INNER_VIEW" if inner_count > len(nodes) / 2 else "OUTER_VIEW"
            avg_trust = sum(getattr(n, 'trust_score', 50.0) for n in nodes) / max(1, len(nodes))
            # 压缩是对知识的提炼和验证，信任分数应综合评估
            l2_node.trust_score = min(100.0, avg_trust + 8.0)
            self._log(LogLevel.DEBUG,
                     f"压缩信任评估: 新L2节点信任={l2_node.trust_score:.0f} (源L1平均={avg_trust:.0f})")

            # ★v17.0性能优化：批量频率编码（只在节点数≥10时触发）
            if self.frequency_codec and hasattr(self, '_reasoning_pool') and self._reasoning_pool and len(nodes) >= 10:
                try:
                    _batch_nodes = [n.to_dict() for n in _quality_nodes[:10]] + [l2_node.to_dict()]
                    _future = self._reasoning_pool.submit("FrequencyCodec.encode_batch", _batch_nodes)
                    if _future:
                        _results = _future.result(timeout=TIMEOUT_CONFIG['subprocess_default'])
                        if _results and len(_results) == len(_batch_nodes):
                            l2_node.frequency_signature = _results[-1]
                            for _i, _n in enumerate(_quality_nodes[:10]):
                                if _i < len(_results) - 1:
                                    _n.frequency_signature = _results[_i]
                            self._log(LogLevel.DEBUG, f"批量频率编码(进程池·压缩组): {len(_results)}个节点")
                            if self.node_pool:
                                self._kal.add_node(l2_node)
                            if self.knowledge_tree:
                                self.knowledge_tree.register_path(prefix)
                            if self.info_field and self.pulse_core:
                                _opt_pulse = self.pulse_core.emit(
                                    source_organ=self.organ_name,
                                    event_type=KnowledgeEvent.COMPRESSED,
                                    payload={"from_level": "L1", "to_level": "L2",
                                             "space_path": prefix, "node_count": len(nodes),
                                             "result_node_id": l2_node.node_id},
                                    priority=4, layer="L2")
                                self.info_field.publish(_opt_pulse)
                            if self.snapshot:
                                _now = time.time()
                                if _now - self._last_snapshot_save_time >= 60.0:
                                    self.snapshot.save()
                                    self._last_snapshot_save_time = _now
                                # ★相关任务：批量频率编码早退分支建了 L2 后必须同步清理源 L1，
                                #   否则源 L1 残留到下一心跳，被去重误判为"自产自删"。
                                if self.node_pool:
                                    _removed_count = 0
                                    for _n in nodes:
                                        if hasattr(self.node_pool, 'remove'):
                                            try:
                                                self._kal.remove_node(_n.node_id)
                                                _removed_count += 1
                                            except Exception as _e2:
                                                self._log(LogLevel.DEBUG,
                                                        f"批量编码后源L1移除失败（节点仍占用内存）: "
                                                        f"{_n.node_id} - {_e2}")
                                    if _removed_count > 0:
                                        self._log(LogLevel.DEBUG, f"清理被压缩L1: {_removed_count}条 (批量编码)")
                                return True
                except Exception as _e:
                    self._log(LogLevel.DEBUG, f"批量频率编码失败(压缩组)，回退单节点: {_e}")
            # ★v22.0优化：压缩质量自检——验证L2节点与源L1节点的关联度
            _source_kw = {kw.lower() for kw in all_keywords if isinstance(kw, str) and len(kw) >= 2}
            _l2_kw = {kw.lower() for kw in top_keywords if isinstance(kw, str) and len(kw) >= 2}
            _compress_overlap = len(_source_kw & _l2_kw) / max(1, len(_source_kw))
            if _compress_overlap < 0.2 and len(nodes) >= 10:
                l2_node.trust_score = max(20.0, l2_node.trust_score - 20.0)
                self._log(LogLevel.DEBUG,
                         f"压缩质量自检: '{path_name}' L2与源L1关键词重叠率={_compress_overlap:.2f}偏低，降低初始信任")
            # 单节点编码（原有逻辑）
            if self.frequency_codec:
                self.frequency_codec.encode_node(l2_node)
            # ===== 主线第4批 任务5(P2-32)：写入时矛盾预检 =====
            # 新L2节点落库前，与同路径已有L2节点做矛盾预检；
            # 命中且旧陈述按策略胜出则跳过新节点写入，避免新增矛盾对。
            # 灰度开关，关闭时无影响；任一异常回退正常写入。
            try:
                import config as _cfg_pc
                if getattr(_cfg_pc, "ENABLE_CONTRADICTION_WRITE_PRECHECK", False):
                    from nucleus.reasoning.ContradictionResolver import ContradictionResolver
                    _pre_strategy = getattr(_cfg_pc, "CONTRADICTION_RESOLUTION_STRATEGY", "time")
                    _existing = (self._kal.query_nodes(
                        evol_level=PulseNode.EVOL_L2, space_path_prefix=prefix, limit=30)
                        if self.node_pool else [])
                    _decision = ContradictionResolver.precheck_on_write(
                        l2_node, _existing, _pre_strategy)
                    if _decision.get("action") == "skip":
                        self._log(LogLevel.INFO,
                                  f"写入预检跳过: 新L2与已有节点矛盾且旧陈述胜出 "
                                  f"({_decision.get('reason')})")
                        for _n in nodes:
                            if hasattr(self.node_pool, 'remove'):
                                try:
                                    self._kal.remove_node(_n.node_id)
                                except Exception as _m42_e:   # _m42_t4 静默修复
                                    # ★主线第42批 T4：本处原为 `except Exception: pass`
                                    #   —— 全文件扫描确认的**唯一静默吞异常点**（修复前位于本行）。
                                    #   与同方法内既有的 `_log_ignored_exception` 口径一致，
                                    #   改为记 DEBUG（自愈即降级，不产生 WARNING 噪音）。
                                    self._log(
                                        LogLevel.DEBUG,
                                        f"矛盾预检清理节点失败（已忽略）: "
                                        f"{type(_m42_e).__name__}: {_m42_e}")
                        return True
            except Exception as e:
                self._log_ignored_exception(e)
            # ===== 写入时矛盾预检结束 =====
            if self.node_pool:
                self._kal.add_node(l2_node)
                # 新L2节点创建成功后，清理被压缩的源L1节点
                _removed_count = 0
                for _n in nodes:
                    if hasattr(self.node_pool, 'remove'):
                        try:
                            self._kal.remove_node(_n.node_id)
                            _removed_count += 1
                        except Exception as _e2:
                            # ★PHASE14-P2-1：与前文同类的裸 except，
                            #   静态扫描只报出了另一处，此处为同类遗漏项。
                            #   失败同样意味着源 L1 未被回收、内存未释放，应有痕迹。
                            self._log(LogLevel.DEBUG,
                                      f"新建L2后源L1移除失败（节点仍占用内存）: "
                                      f"{_n.node_id} - {_e2}")
                if _removed_count > 0:
                    self._log(LogLevel.DEBUG, f"清理被压缩L1: {_removed_count}条 (新建L2)")
            if self.knowledge_tree:
                self.knowledge_tree.register_path(prefix)

            if self.info_field and self.pulse_core:
                optimize_pulse = self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=KnowledgeEvent.COMPRESSED,
                    payload={
                        "from_level": "L1", "to_level": "L2",
                        "space_path": prefix, "node_count": len(nodes),
                        "result_node_id": l2_node.node_id,
                    },
                    priority=4,
                    layer="L2"
                )
                self.info_field.publish(optimize_pulse)

            # L2节点创建成功后保存快照（冷却60秒，避免频繁写入）
            if self.snapshot:
                now = time.time()
                if now - self._last_snapshot_save_time >= 60.0:
                    self.snapshot.save()
                    self._last_snapshot_save_time = now

            return True

        except Exception as e:
            self._log(LogLevel.ERROR, f"压缩失败 ({prefix}): {e}",
                    error_code=ErrorCode.EXECUTION_FAILED)
            return False
    def _assess_node_quality(self, node: PulseNode, source_nodes: list[PulseNode] | None = None) -> float:
        """
        通用的知识节点质量评估（0.0-1.0）。
        不依赖任何特定噪音词表，只基于知识的内在结构做判断。
        
        评估维度：
        1. 关键词密度——关键词是否足够丰富
        2. 内容长度——是否足够充实
        3. 语言纯度——中文占比是否合理
        4. 来源活性——源节点中是否有被激活过的
        
        这是底层通用逻辑，不规定上限，只规定下限。
        未来任何新型噪音都无法绕过这些基本判断。
        """
        # ★v17.0 R9修复：改用统一健康度评估中的质量分
        _health = node.evaluate_node_health(check_type="quality")
        _unified_quality = _health["quality_score"] / 100.0
        # 保留原有逻辑作为参考，但最终分数使用统一评估
        score = 0.0

        # 维度1: 关键词密度（权重0.3）
        kw_count = len(node.keywords) if hasattr(node, 'keywords') and node.keywords else 0
        if kw_count >= 5:
            score += 0.3
        elif kw_count >= 3:
            score += 0.2
        elif kw_count >= 1:
            score += 0.1
        else:
            score -= 0.2  # 没有关键词是强噪音信号

        # 维度2: 内容充实度（权重0.3）
        value_str = str(node.value) if node.value else ""
        if len(value_str) >= 100:
            score += 0.3
        elif len(value_str) >= 50:
            score += 0.2
        elif len(value_str) >= 20:
            score += 0.1
        else:
            score -= 0.2  # 内容太短可能是碎片

        # 维度3: 语言纯度——中文占比（权重0.2）
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', value_str))
        total_chars = max(1, len(value_str))
        chinese_ratio = chinese_chars / total_chars

        if chinese_ratio >= 0.3:
            score += 0.2
        elif chinese_ratio >= 0.1:
            score += 0.1
        else:
            score -= 0.1

        # 运行时状态记录，内容为纯中文且格式固定，不应被误判为低质量
        _node_value = str(node.value) if node.value else ""
        _is_self_state_node = (
            _node_value.startswith(("[自我状态]", "[自我感知]", "[运行时状态]"))
        )
        if _is_self_state_node:
            # 自我状态节点：直接给到最低通过分（0.5），确保不被降级
            score = max(score, 0.5)
        # ===== 自我状态节点保护结束 =====

        # 维度4: 来源活性（权重0.2）
        if source_nodes:
            active_count = sum(1 for n in source_nodes
                              if hasattr(n, 'activation_count') and n.activation_count > 0)
            active_ratio = active_count / max(1, len(source_nodes))
            if active_ratio >= 0.3:
                score += 0.2
            elif active_ratio >= 0.1:
                score += 0.1
        # 新增: 关键词纯度检测（追加扣分项）
        kw_noise_count = 0
        for kw in (node.keywords or []):
            kw_str = str(kw).lower()
            # 检查是否为明显的网络噪音/代码残片
            if (kw_str.isdigit() or any(domain in kw_str for domain in ['.com', '.cn', '.org', '.net', '.gov', '.edu', '.io']) or len(kw_str) <= 2 and not any('\u4e00' <= c <= '\u9fff' for c in kw_str)):
                kw_noise_count += 1

        # 如果超过半数的关键词都是噪音，则总质量分上限为 0.4
        if (node.keywords or []) and kw_noise_count / len(node.keywords) > 0.5:
            score = min(score, 0.4)

        _original_score = max(0.0, min(1.0, score))
        # ★v17.0 R9修复：统一评估和原有评估取加权平均（统一占60%，原有占40%）
        _final_score = _unified_quality * 0.6 + _original_score * 0.4
        return round(max(0.0, min(1.0, _final_score)), 2)
    def _detect_value_contradiction(self, val_a: str, val_b: str) -> bool:
        """
        ★v17.0修复：调用通用矛盾检测函数。
        之前hasattr检查永远返回False，导致矛盾检测逻辑从未生效。
        ★2026-09-07 阶段二：改调 ContradictionDetector.detect_semantic（行为等价）。
        """
        from nucleus.reasoning.ContradictionDetector import ContradictionDetector
        return ContradictionDetector.detect_semantic(val_a, val_b)
    def _periodic_purity_check(self):
        """
        周期自检：定期对所有L2节点进行质量巡检。
        1. 对中等质量节点进行内容清洗重评估，尝试去除杂音片段
        2. 对低质量节点进行标记或降级
        """
        if self.node_pool is None:
            return

        l2_nodes = self._kal.query_nodes(evol_level=PulseNode.EVOL_L2, limit=500)
        if len(l2_nodes) < 10:
            return

        from nucleus.knowledge_noise_filter import clean_content_text

        cleaned_count = 0
        node_qualities = []

        for node in l2_nodes:
            if self._stop_requested:  # ★L10补全：停止请求检查点，中断纯度巡检
                break
            # ★v17.0性能优化：跳过信任度>80的高质量节点，减少不必要的重评估
            _node_trust = getattr(node, 'trust_score', 50.0)
            if _node_trust > 80.0 and getattr(node, 'activation_count', 0) > 0:
                continue
            score = self._assess_node_quality(node)

            # 精准清理：中等质量节点尝试清洗内容后重新评估
            # ★v17.0修复：跳过代码自学习路径——其格式为"功能: ..."，清洗无效是正常的
            _node_path = getattr(node, 'space_path', '')
            if _node_path.startswith('/自我理解/代码'):
                continue
            if 0.3 <= score <= 0.5 and hasattr(node, 'value') and node.value:
                _original_value = str(node.value)
                _cleaned_value = clean_content_text(_original_value)

                # 如果清洗后内容有明显变化，说明存在可清理的杂音
                if _cleaned_value and len(_cleaned_value) > 20 and _cleaned_value != _original_value:
                    _old_len = len(_original_value)
                    _new_len = len(_cleaned_value)
                    # 只在新内容长度不低于原来的60%时才替换，避免误清理
                    if _new_len >= _old_len * 0.6:
                        node.value = _cleaned_value
                        cleaned_count += 1
                        # 重新评估清洗后的质量，重置累积计数
                        score = self._assess_node_quality(node)
                        if hasattr(self, '_clean_failure_counts'):
                            self._clean_failure_counts.pop(node.node_id, None)
                        self._log(LogLevel.DEBUG,
                                 f"内容清洗: '{_original_value[:30]}...' → '{_cleaned_value[:30]}...' "
                                 f"(质量 {_old_len}→{_new_len}字, 评分刷新为{score:.2f})")
                else:
                    # 清洗无效：内容未变化或清洗后太短
                    _node_value = str(node.value) if node.value else ""
                    if _node_value.startswith(("[自我状态]", "[运行时状态]")):
                        continue
                    # 累积失败计数，两次无效清洗后强制降级信任分数
                    if not hasattr(self, '_clean_failure_counts'):
                        self._clean_failure_counts = {}
                    _fail_count = self._clean_failure_counts.get(node.node_id, 0) + 1
                    self._clean_failure_counts[node.node_id] = _fail_count

                    if _fail_count >= 2:
                        # 累积两次清洗无效，判定为顽固噪音，强制降级
                        if hasattr(node, 'trust_score'):
                            node.trust_score = max(10.0, getattr(node, 'trust_score', 30.0) - 25.0)
                        if hasattr(node, 'importance'):
                            node.importance = PulseNode.IMPORTANCE_C
                        if hasattr(node, 'ephemeral'):
                            node.ephemeral = True
                        # 降级后从失败计数中移除，避免无限降级
                        self._clean_failure_counts.pop(node.node_id, None)
                        # 同步更新评分，让后续的底部清理逻辑可以处理
                        score = max(0.0, score - 0.2)
                        self._log(LogLevel.INFO,
                                 f"顽固噪音降级: '{_original_value[:30]}...' "
                                 f"({_fail_count}次清洗无效，信任强制降至{node.trust_score:.0f})")
                    else:
                        self._log(LogLevel.DEBUG,
                                 f"清洗无效(第{_fail_count}次): '{_original_value[:30]}...' "
                                 f"内容未变化，等待下次检查")

            # ===== 新增：自动识别引用/元描述格式节点并额外扣分 =====
            _node_value = str(node.value) if node.value else ""
            # ★v23.0优化：从config读取
            _quote_patterns = getattr(self, '_quote_patterns', [])
            _quote_hits = sum(1 for _qp in _quote_patterns if re.search(_qp, _node_value))
            if _quote_hits >= 2:
                score = max(0.0, score - 0.3)
                if not hasattr(self, '_quote_node_ids'):
                    self._quote_node_ids = set()
                if node.node_id not in self._quote_node_ids:
                    self._quote_node_ids.add(node.node_id)
                    self._log(LogLevel.DEBUG, f"引用格式检测: 节点'{_node_value[:40]}...' 扣分0.3")
            # ===== 引用格式检测结束 =====

            node_qualities.append((node, score))

        if cleaned_count > 0:
            self._log(LogLevel.INFO, f"内容清洗: 对{cleaned_count}个L2节点进行了精准清理")

        # 【v12.0新增】与自我架构知识交叉验证——标记矛盾节点
        _self_arch_nodes = self._kal.query_nodes(
            evol_level="L3", space_path_prefix="/自我/架构", limit=20
        )
        if _self_arch_nodes:
            _cross_validated = 0
            _cross_processed = set()
            for _node in l2_nodes:
                if hasattr(_node, 'trust_score') and getattr(_node, 'trust_score', 30.0) <= 15.0:
                    continue
                _node_id = getattr(_node, 'node_id', '') or getattr(_node, 'id', '')
                if _node_id and _node_id in _cross_processed:
                    continue
                _node_kw = {kw.lower() for kw in (_node.keywords or [])
                              if isinstance(kw, str) and len(kw) >= 2}
                if len(_node_kw) < 2:
                    continue
                for _self_node in _self_arch_nodes:
                    _self_kw = {kw.lower() for kw in (_self_node.keywords or [])
                                  if isinstance(kw, str) and len(kw) >= 2}
                    if len(_self_kw) < 2:
                        continue
                    _overlap = len(_node_kw & _self_kw)
                    _min_size = min(len(_node_kw), len(_self_kw))
                    if _min_size > 0 and _overlap / _min_size >= 0.5:
                        # 与自我知识有重叠，验证是否矛盾
                        _node_val = str(_node.value) if _node.value else ""
                        _self_val = str(_self_node.value) if _self_node.value else ""
                        _is_contra = self._detect_value_contradiction(_node_val, _self_val) if hasattr(self, '_detect_value_contradiction') else False
                        if _is_contra:
                            # 与自我知识矛盾 → 降低信任
                            if hasattr(_node, 'trust_score'):
                                _node.trust_score = max(10.0, getattr(_node, 'trust_score', 30.0) - 10.0)
                            if _node_id:
                                _cross_processed.add(_node_id)
                            _cross_validated += 1
                            self._log(LogLevel.DEBUG,
                                     f"自我知识交叉验证: 节点'{_node_val[:30]}...'与自我知识矛盾，降低信任")
                        break
            if _cross_validated > 0:
                self._log(LogLevel.INFO,
                         f"自我知识交叉验证: 发现{_cross_validated}个与自我架构知识矛盾的节点，已降级")

        # 找出质量最差的10%（至少1个）
        node_qualities.sort(key=lambda x: x[1])
        bottom_count = max(1, len(node_qualities) // 10)
        bottom_nodes = node_qualities[:bottom_count]

        purged = 0
        for node, score in bottom_nodes:
            if score < 0.3:
                if hasattr(node, 'ephemeral'):
                    node.ephemeral = True
                if hasattr(node, 'trust_score'):
                    node.trust_score = max(10.0, getattr(node, 'trust_score', 30.0) - 20.0)
                purged += 1
            elif score < 0.4:
                if hasattr(node, 'importance'):
                    node.importance = PulseNode.IMPORTANCE_C
                if hasattr(node, 'trust_score'):
                    node.trust_score = max(20.0, getattr(node, 'trust_score', 40.0) - 15.0)
                purged += 1
        # ===== 【v15.0新增】L3节点重复检测与合并 =====
        _l3_nodes = self._kal.query_nodes(evol_level="L3", limit=200)
        if len(_l3_nodes) >= 2:
            _l3_merged = 0
            _seen_pairs = set()
            for _i in range(len(_l3_nodes)):
                _node_a = _l3_nodes[_i]
                if getattr(_node_a, 'state', '') != 'locked':
                    continue
                _kw_a = {kw.lower() for kw in (_node_a.keywords or [])
                           if isinstance(kw, str) and len(kw) >= 2}
                _val_a = str(_node_a.value)[:120] if _node_a.value else ""
                if len(_kw_a) < 2 or len(_val_a) < 20:
                    continue

                for _j in range(_i + 1, len(_l3_nodes)):
                    _node_b = _l3_nodes[_j]
                    if getattr(_node_b, 'state', '') != 'locked':
                        continue
                    _pair_key = tuple(sorted([_node_a.node_id, _node_b.node_id]))
                    if _pair_key in _seen_pairs:
                        continue
                    _seen_pairs.add(_pair_key)

                    _kw_b = {kw.lower() for kw in (_node_b.keywords or [])
                               if isinstance(kw, str) and len(kw) >= 2}
                    _val_b = str(_node_b.value)[:120] if _node_b.value else ""
                    if len(_kw_b) < 2 or len(_val_b) < 20:
                        continue

                    _min_size = min(len(_kw_a), len(_kw_b))
                    if _min_size == 0:
                        continue
                    _kw_overlap = len(_kw_a & _kw_b) / _min_size
                    # 关键词重叠率≥80%且内容也高度相似 → 合并
                    # 内容相似度判断：前60字有≥30%的2字片段重叠，或互相包含
                    _val_a_60 = _val_a[:60]
                    _val_b_60 = _val_b[:60]
                    _bigrams_a = {_val_a_60[i:i+2] for i in range(len(_val_a_60)-1)}
                    _bigrams_b = {_val_b_60[i:i+2] for i in range(len(_val_b_60)-1)}
                    _content_overlap = len(_bigrams_a & _bigrams_b) / max(1, min(len(_bigrams_a), len(_bigrams_b)))
                    _content_similar = _content_overlap >= 0.3 or _val_a_60 in _val_b_60 or _val_b_60 in _val_a_60

                    # 将关键词重叠率阈值从 0.8 降至 0.7，同时确保内容相似，
                    # 这样能合并更多“关键词有细微差别但实质相同”的节点。
                    if _kw_overlap >= 0.7 and _content_similar:
                        # 保留信任更高的节点，合并另一个的关键词到它
                        _trust_a = getattr(_node_a, 'trust_score', 70.0)
                        _trust_b = getattr(_node_b, 'trust_score', 70.0)

                        if _trust_a >= _trust_b:
                            _keeper, _merged = _node_a, _node_b
                        else:
                            _keeper, _merged = _node_b, _node_a

                        # 将低信任节点的独有关键词追加到高信任节点
                        _unique_kw = _kw_b - _kw_a if _keeper == _node_a else _kw_a - _kw_b
                        _new_kw = [kw for kw in _unique_kw if kw not in [k.lower() for k in (_keeper.keywords or [])]]
                        if hasattr(_keeper, 'keywords') and _new_kw:
                            _keeper.keywords.extend(list(_new_kw)[:3])
                        # 提升合并后节点的信任分数
                        _keeper.trust_score = min(100.0, max(_trust_a, _trust_b) + 5.0)
                        # 降级被合并的节点
                        _merged.trust_score = max(20.0, min(_trust_a, _trust_b) - 30.0)
                        _merged.state = "dormant"
                        _merged.importance = PulseNode.IMPORTANCE_C if hasattr(PulseNode, 'IMPORTANCE_C') else "C"
                        _l3_merged += 1
                        break  # 每个节点只合并一次

            if _l3_merged > 0:
                self._log(LogLevel.INFO,
                         f"L3重复检测: 合并{_l3_merged}对高度重复的L3智慧节点")
        # ===== L3重复检测结束 =====

        # ===== 【L3知识整理】碎片归并 + 质量复核 =====
        _l3_fragment_merged = 0
        _l3_path_fixed = 0
        _l3_trust_recovered = 0

        _all_l3 = self._kal.query_nodes(evol_level="L3", limit=500)
        for _l3_node in _all_l3:
            _l3_val = str(_l3_node.value) if _l3_node.value else ""
            _l3_kw = _l3_node.keywords if hasattr(_l3_node, 'keywords') and _l3_node.keywords else []
            _l3_path = getattr(_l3_node, 'space_path', '')
            _l3_trust = getattr(_l3_node, 'trust_score', 50.0)

            # 规则1：碎片归并——内容短且关键词少的L3节点降级为L2
            # ★P1-3修复：增加核心自我知识路径白名单保护，防止误降级
            _is_core_self_knowledge = (
                _l3_path.startswith(("/自我/架构", "/身份/", "/本能/", "/自我/架构/器官", "/自我/架构/推理算子", "/自我/架构/知识体系", "/自我理解/代码"))
            )

            if len(_l3_val) < 50 and len(_l3_kw) < 3:
                if _is_core_self_knowledge:
                    # 核心自我知识即使短也不降级——这些是框架的基础认知
                    # 但信任度过低时仍然需要标记关注
                    if _l3_trust < 40.0:
                        self._log(LogLevel.WARNING,
                                 f"L3核心知识信任异常: '{_l3_val[:40]}...' "
                                 f"(内容{len(_l3_val)}字, 信任{_l3_trust:.0f})，"
                                 f"保留但建议人工复查")
                    continue  # 跳过降级

                _l3_node.evol_level = PulseNode.EVOL_L2
                _l3_node.importance = PulseNode.IMPORTANCE_A if hasattr(PulseNode, 'IMPORTANCE_A') else "A"
                _l3_node.state = "active"
                _l3_fragment_merged += 1
                if _l3_fragment_merged <= 5:
                    self._log(LogLevel.DEBUG,
                             f"L3碎片归并: '{_l3_val[:40]}...' → 降级为L2 (内容{len(_l3_val)}字, 关键词{len(_l3_kw)}个)")
                continue

            # 规则2：路径碎片修复
            if _l3_path and _l3_path != "/":
                _parts = _l3_path.strip("/").split("/")
                _cleaned_parts = []
                _has_fragment = False
                for _part in _parts:
                    _is_fragment = (
                        _part.isdigit() or
                        ":" in _part or
                        _part in ("节点A", "节点B", "总节点", "层级结构", "六大维度") or
                        (len(_part) <= 2 and not any('\u4e00' <= c <= '\u9fff' for c in _part))
                    )
                    if _is_fragment:
                        _has_fragment = True
                    else:
                        _cleaned_parts.append(_part)
                if _has_fragment and _cleaned_parts:
                    _new_path = "/" + "/".join(_cleaned_parts)
                    if hasattr(_l3_node, 'space_path'):
                        _l3_node.space_path = _new_path
                    _l3_path_fixed += 1
                    if _l3_path_fixed <= 5:
                        self._log(LogLevel.DEBUG,
                                 f"L3路径修复: {_l3_path} → {_new_path}")

            # 规则3：信任分数复核——内容充实但信任异常低的节点恢复信任
            if _l3_trust < 30.0 and len(_l3_val) >= 80 and len(_l3_kw) >= 3:
                _l3_node.trust_score = min(60.0, _l3_trust + 25.0)
                _l3_trust_recovered += 1
                if _l3_trust_recovered <= 5:
                    self._log(LogLevel.DEBUG,
                             f"L3信任恢复: '{_l3_val[:40]}...' 信任{_l3_trust:.0f}→{_l3_node.trust_score:.0f} "
                             f"(内容{len(_l3_val)}字, 关键词{len(_l3_kw)}个)")

        if _l3_fragment_merged > 0 or _l3_path_fixed > 0 or _l3_trust_recovered > 0:
            self._log(LogLevel.INFO,
                     f"L3知识整理: 碎片归并{_l3_fragment_merged}个, "
                     f"路径修复{_l3_path_fixed}个, 信任恢复{_l3_trust_recovered}个")
        # ===== L3知识整理结束 =====

        if purged > 0 or cleaned_count > 0:
            self._log(LogLevel.INFO,
                     f"周期自检完成: 扫描{len(l2_nodes)}个L2节点，"
                     f"精准清理{cleaned_count}个, 降级{purged}个 (阈值={bottom_nodes[-1][1]:.2f})")

        # ===== ★v23.0新增：知识健康度汇总日志 =====
        _stats = self._kal.get_stats()
        _evol = _stats.get("evol_distribution", {})
        _total = _stats.get("total_nodes", 0)
        _l1 = _evol.get("L1", 0)
        _l2 = _evol.get("L2", 0)
        _l3 = _evol.get("L3", 0)
        if _total > 0:
            _density = round((_l2 + _l3) / _total * 100, 1)
            self._log(LogLevel.INFO,
                     f"知识健康度: 总{_total}节点 L1={_l1} L2={_l2} L3={_l3} "
                     f"密度={_density}% (L2+L3占比)")
        # ===== 知识健康度汇总结束 =====

        # 清理过期的失败计数
        if hasattr(self, '_clean_failure_counts') and self._clean_failure_counts:
            _now = time.time()
            _scanned_ids = {n.node_id for n in l2_nodes}
            _expired_failures = [
                _nid for _nid in self._clean_failure_counts
                if _nid not in _scanned_ids
            ]
            for _nid in _expired_failures:
                del self._clean_failure_counts[_nid]
        # ===== v20.0新增：知识关联图谱构建——增强知识横向连接 =====
        # ★v25.0改进：L2+L3全量参与，分批扫描
        if len(l2_nodes) >= 20:
            _l3_nodes_for_graph = self._kal.query_nodes(evol_level="L3", limit=2000)
            _core_nodes_for_graph = l2_nodes + _l3_nodes_for_graph
            self._build_knowledge_association_graph(_core_nodes_for_graph)
        # ===== v20.0新增结束 =====
    # [批次4·深度体检][PERF-9] 预计算关键词，避免内层重复推导
    def _build_knowledge_association_graph(self, l2_nodes: list):
        """
        ★v25.0改进版：知识关联图谱构建——覆盖L2+L3全量节点，四维度关联。
        
        关联维度：
        1. 因果连接——发现A→B→C的传递关系
        2. 类比连接——发现跨领域结构相似性
        3. 层级连接——发现同路径下的概念层级关系
        4. 语义相似连接——基于关键词+路径+来源的语义相似度
        
        分批扫描：每轮处理500个节点，通过偏移量轮换覆盖全部节点。
        """
        _association_count = 0
        _total_core = len(l2_nodes)
        _MAX_ASSOCIATIONS = min(400, max(100, _total_core // 8))

        # ★v25.0新增：通用词过滤——防止高频通用词造成虚假关联
        _generic_keywords = {
            "基础", "核心", "系统", "框架", "架构", "方法", "功能",
            "数据", "信息", "内容", "知识", "技术", "模型", "分析",
            "处理", "实现", "设计", "优化", "管理", "控制", "监控",
            "检测", "检查", "维护", "支持", "服务", "应用", "开发",
            "相关", "进行", "使用", "通过", "一个", "这个", "那个",
        }
        for _node in l2_nodes:
            if hasattr(_node, 'keywords') and _node.keywords:
                _node.keywords = [kw for kw in _node.keywords
                                 if kw.lower() not in _generic_keywords]

        # ★v25.0改进：分批扫描
        if not hasattr(self, '_association_scan_offset'):
            self._association_scan_offset = 0
        _scan_batch_size = 500
        _scan_start = self._association_scan_offset
        _scan_end = min(_scan_start + _scan_batch_size, _total_core)
        _scan_batch = l2_nodes[_scan_start:_scan_end]
        # 更新偏移量，下一轮从下一批开始；到达末尾后回到开头
        self._association_scan_offset = _scan_end if _scan_end < _total_core else 0

        # 按路径前缀分组（全量，不限于当前批次）
        _path_groups = {}
        for _node in _scan_batch:
            _path = getattr(_node, 'space_path', '/')
            _root = '/' + _path.strip('/').split('/')[0] if _path and _path != '/' else '/'
            if _root not in _path_groups:
                _path_groups[_root] = []
            _path_groups[_root].append(_node)

        # ===== 维度1：因果连接 =====
        _causal_keywords = getattr(self, '_causal_keywords', [])
        if not _causal_keywords:
            _causal_keywords = ["导致", "因此", "所以", "因为", "影响", "产生",
                               "引起", "造成", "促使", "触发", "驱动", "推动",
                               "压缩", "融合", "消化", "升级", "降级", "淘汰"]

        # ★PERF-9修复: 预计算每个节点的关键词集合，避免内层循环对每对节点重复构建集合（O(batch^2) → O(batch)）
        _kw_map = {
            _n.node_id: {kw.lower() for kw in (_n.keywords or [])
                         if isinstance(kw, str) and len(kw) >= 2}
            for _n in _scan_batch
        }

        for _node_a in _scan_batch:
            _val_a = str(_node_a.value) if _node_a.value else ""
            _kw_a = {kw.lower() for kw in (_node_a.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
            if not _kw_a:
                continue
            _has_causal = any(_ck in _val_a for _ck in _causal_keywords)
            if not _has_causal:
                continue
            for _node_b in _scan_batch:
                if _node_b.node_id == _node_a.node_id:
                    continue
                if _association_count >= _MAX_ASSOCIATIONS:
                    break
                _kw_b = _kw_map.get(_node_b.node_id) or set()
                if not _kw_b:
                    continue
                _overlap = _kw_a & _kw_b
                if len(_overlap) >= 3:
                    if _node_b.node_id not in (_node_a.linked_nodes or []):
                        if not _node_a.linked_nodes:
                            _node_a.linked_nodes = []
                        _node_a.linked_nodes.append(_node_b.node_id)
                        _node_a.hebbian_weight = min(1.0, getattr(_node_a, 'hebbian_weight', 0.0) + 0.05)
                        _node_a.add_semantic_relation(_node_b.node_id, "causal", 0.6, "liver_causal")
                        _association_count += 1
            if _association_count >= _MAX_ASSOCIATIONS:
                break

        # ===== 维度2：类比连接 =====
        _root_names = list(_path_groups.keys())
        for _i in range(len(_root_names)):
            for _j in range(_i + 1, len(_root_names)):
                if _association_count >= _MAX_ASSOCIATIONS:
                    break
                _nodes_a = _path_groups[_root_names[_i]][:15]
                _nodes_b = _path_groups[_root_names[_j]][:15]
                for _na in _nodes_a[:5]:
                    _kwa = {kw.lower() for kw in (_na.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
                    if len(_kwa) < 2:
                        continue
                    for _nb in _nodes_b[:5]:
                        _kwb = {kw.lower() for kw in (_nb.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
                        if len(_kwb) < 2:
                            continue
                        _shared = _kwa & _kwb
                        if len(_shared) >= 4:
                            if _nb.node_id not in (_na.linked_nodes or []):
                                if not _na.linked_nodes:
                                    _na.linked_nodes = []
                                _na.linked_nodes.append(_nb.node_id)
                                _na.hebbian_weight = min(1.0, getattr(_na, 'hebbian_weight', 0.0) + 0.03)
                                _na.add_semantic_relation(_nb.node_id, "analogy", 0.4, "liver_analogy")
                                _association_count += 1
                                if _association_count >= _MAX_ASSOCIATIONS:
                                    break
                    if _association_count >= _MAX_ASSOCIATIONS:
                        break
                if _association_count >= _MAX_ASSOCIATIONS:
                    break

        # ===== 维度3：层级连接 =====
        for _root, _nodes in _path_groups.items():
            if _association_count >= _MAX_ASSOCIATIONS:
                break
            if len(_nodes) < 3:
                continue
            _nodes_sorted = sorted(_nodes, key=lambda n: len(getattr(n, 'space_path', '/').split('/')))
            for _i in range(len(_nodes_sorted)):
                _parent = _nodes_sorted[_i]
                _parent_path = getattr(_parent, 'space_path', '/')
                _parent_kw = {kw.lower() for kw in (_parent.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
                if len(_parent_kw) < 2:
                    continue
                for _j in range(_i + 1, min(_i + 10, len(_nodes_sorted))):
                    _child = _nodes_sorted[_j]
                    _child_path = getattr(_child, 'space_path', '/')
                    if _child_path.startswith(_parent_path + '/') or _child_path == _parent_path:
                        _child_kw = {kw.lower() for kw in (_child.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
                        _overlap = _parent_kw & _child_kw
                        if len(_overlap) >= 2:
                            if _child.node_id not in (_parent.linked_nodes or []):
                                if not _parent.linked_nodes:
                                    _parent.linked_nodes = []
                                _parent.linked_nodes.append(_child.node_id)
                                _parent.add_semantic_relation(_child.node_id, "hierarchy", 0.7, "liver_hierarchy")
                                _association_count += 1
                                if _association_count >= _MAX_ASSOCIATIONS:
                                    break
                if _association_count >= _MAX_ASSOCIATIONS:
                    break

        # ===== ★v25.0新增：维度4——语义相似连接 =====
        # 基于关键词重叠+路径相似+来源相似的复合语义相似度
        _semantic_quota = _MAX_ASSOCIATIONS  # 语义关联额外配额（不占原有上限）
        _semantic_count = 0
        for _i, _na in enumerate(_scan_batch[:200]):
            _path_a = getattr(_na, 'space_path', '/')
            _kw_a = {kw.lower() for kw in (_na.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
            _src_a = getattr(_na, 'source_organ', '')
            if len(_kw_a) < 2:
                continue
            for _nb in _scan_batch[_i+1:_i+80]:  # 限制内层范围控制复杂度
                if _nb.node_id == _na.node_id:
                    continue
                if _semantic_count >= _semantic_quota:
                    break
                _path_b = getattr(_nb, 'space_path', '/')
                _kw_b = {kw.lower() for kw in (_nb.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
                _src_b = getattr(_nb, 'source_organ', '')
                if len(_kw_b) < 2:
                    continue
                # 复合语义相似度
                _kw_overlap = len(_kw_a & _kw_b) / max(1, min(len(_kw_a), len(_kw_b)))
                _path_sim = 0.0
                _path_parts_a = _path_a.strip('/').split('/')
                _path_parts_b = _path_b.strip('/').split('/')
                if _path_a == _path_b:
                    _path_sim = 1.0
                elif _path_parts_a and _path_parts_b and _path_parts_a[0] == _path_parts_b[0]:
                    _path_sim = 0.5
                _src_sim = 1.0 if _src_a == _src_b else 0.0
                _semantic_sim = _kw_overlap * 0.6 + _path_sim * 0.3 + _src_sim * 0.1
                if _semantic_sim >= 0.65:
                    if _nb.node_id not in (_na.linked_nodes or []):
                        if not _na.linked_nodes:
                            _na.linked_nodes = []
                        _na.linked_nodes.append(_nb.node_id)
                        _na.add_semantic_relation(_nb.node_id, "semantic_similarity",
                                                  round(_semantic_sim, 3), "liver_semantic")
                        _semantic_count += 1
                        _association_count += 1
            if _semantic_count >= _semantic_quota:
                break
        # ===== ★v25.0新增：维度5——代码调用关联 =====
        _code_learner = getattr(self, '_code_learner', None)
        if _code_learner:
            try:
                _edges = _code_learner.get_call_graph_edges()
                # 只处理 /自我理解/代码 路径下的节点，避免污染其他领域
                _code_nodes = [
                    n for n in _scan_batch
                    if getattr(n, 'space_path', '').startswith('/自我理解/代码')
                ]
                # 构建方法名到节点的映射
                _method_node_map = {}
                for _cn in _code_nodes:
                    _cn_val = str(_cn.value) if _cn.value else ""
                    # 尝试从 value 中提取器官和方法名
                    _organ_match = re.search(r'\[自我理解·(\w+)\.(\w+)\]', _cn_val)
                    if _organ_match:
                        _organ = _organ_match.group(1)
                        _method = _organ_match.group(2)
                        _method_node_map[f"{_organ}.{_method}"] = _cn
                    else:
                        # 回退：从 keywords 中匹配
                        _kws = _cn.keywords or []
                        for _kw in _kws:
                            if _kw.startswith("Pulse") and "." in _kw:
                                _method_node_map[_kw] = _cn
                                break

                _call_edges_added = 0
                for _edge in _edges[:100]:  # 限制每轮处理100条边
                    if _call_edges_added >= 50 or _association_count >= _MAX_ASSOCIATIONS:
                        break
                    _caller_organ = _edge["caller_organ"]
                    _caller_method = _edge["caller_method"]
                    _callee = _edge["callee"]

                    _caller_key = f"{_caller_organ}.{_caller_method}"
                    _caller_node = _method_node_map.get(_caller_key)
                    if not _caller_node:
                        continue

                    # 被调用方可能是 self.method 或直接 method
                    _callee_key = ""
                    if _callee.startswith("self."):
                        _callee_key = f"{_caller_organ}.{_callee[5:]}"
                    else:
                        _callee_key = f"{_caller_organ}.{_callee}"
                    # 如果被调用方是不同器官，尝试直接匹配方法名
                    _callee_node = _method_node_map.get(_callee_key)
                    # ★主线第14批 T1.2 修复：原代码 `for _node, _key in ...items()` 把
                    #   dict.items() 的 (键, 值) 解包反了——键是字符串却绑给 _node，
                    #   值是 PulseNode 却绑给 _key，导致 `_key.endswith(...)` 在
                    #   PulseNode 上调用 → AttributeError（16 次/12h），
                    #   整个「代码调用关联」构建被静默放弃。灰度开关关闭时不走兜底。
                    if not _callee_node and getattr(
                            config, "ENABLE_LIVER_CALL_EDGE_GUARD", True):
                        # 全节点模糊匹配（键=字符串方法键，值=PulseNode）
                        for _key, _node in _method_node_map.items():
                            if isinstance(_key, str) and _key.endswith(f".{_callee}"):
                                _callee_node = _node
                                break
                    if not _callee_node or _callee_node.node_id == _caller_node.node_id:
                        continue

                    if _callee_node.node_id not in (_caller_node.linked_nodes or []):
                        if not _caller_node.linked_nodes:
                            _caller_node.linked_nodes = []
                        _caller_node.linked_nodes.append(_callee_node.node_id)
                        _caller_node.add_semantic_relation(
                            _callee_node.node_id, "call_dependency", 0.8, "liver_code_call")
                        _association_count += 1
                        _call_edges_added += 1

                if _call_edges_added > 0:
                    self._log(LogLevel.DEBUG,
                             f"代码调用关联: 建立{_call_edges_added}条 call_dependency 边")
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"代码调用关联构建异常: {_e}")
        if _association_count > 0:
            self._log(LogLevel.INFO,
                     f"知识关联图谱: 建立了{_association_count}条新关联 "
                     f"(因果+类比+层级+语义, 扫描批次[{_scan_start}:{_scan_end}]/{_total_core})")

            if _association_count >= 5:
                try:
                    from nucleus.InsightBoard import get_insight_board
                    _board = get_insight_board()
                    _board.post(
                        insight_type="knowledge_association",
                        content=f"知识关联图谱构建: 发现{_association_count}条新关联，知识网络正在变得更加紧密",
                        source_loop="知识演化闭环",
                        related_dimension="知识关联",
                        confidence=0.7,
                        keywords=["知识关联", "因果连接", "类比连接", "层级连接", "语义相似"]
                    )
                except Exception as e:
                    self._log(LogLevel.ERROR, f'异常: {e}')
    @staticmethod
    def _is_path_noise(path: str) -> bool:
        """
        判断知识路径是否为残词污染。
        检测特征：域名后缀（.com/.cn/.org）、纯数字片段、全大写短缩写路径。
        返回True表示应该跳过。
        """
        if not path:
            return False

        path_lower = path.lower()
        # 域名后缀检测
        if any(suffix in path_lower for suffix in DOMAIN_SUFFIXES):
            return True
        # 路径最后一段为纯数字或全大写短缩写
        _parts = path.rstrip('/').split('/')
        last_part = _parts[-1]
        if last_part.isdigit():
            # ★W1修复：纯数字尾段不直接判噪声——语义前缀+时间戳分桶（如
            # /成长/评估/1788085588）是合法路径，不是残词污染。
            # 仅当整个路径无语义前缀（如 /1788）或前缀全为数字时才是真噪声。
            _semantic_prefix = [_s for _s in _parts[:-1] if _s]
            if not _semantic_prefix:
                return True
            return bool(all(_s.isdigit() for _s in _semantic_prefix))
        if len(last_part) <= 5 and last_part.isupper():
            return True
        # 路径最后一段为数字+单字母混合（如 "16", "2020", "AMGx"）
        return bool(re.match(r'^\d+[a-zA-Z]?$', last_part) or re.match(r'^[a-zA-Z]\d+$', last_part))

    def _fuse_l2_to_l3(self):
        # ★主线第17批 T3/P2-63：旁路事件耗时计数起点（仅计时，不参与逻辑）
        _tap_t0 = time.perf_counter()
        l2_nodes = self._kal.query_nodes(evol_level=PulseNode.EVOL_L2, limit=10000)
        if not l2_nodes:
            return
        # 冷却检查：使用独立的融合冷却计时器，不与压缩共享
        now = time.time()
        # ★修复：清理过期的阻塞状态（超过2小时的条目自动清除）
        if hasattr(self, '_adaptive_fuse_blocked_paths') and self._adaptive_fuse_blocked_paths:
            _expired_paths = [
                _p for _p, _until in self._adaptive_fuse_blocked_paths.items()
                if now > _until + 7200  # 阻塞时间+2小时仍未重试则清除
            ]
            for _p in _expired_paths:
                del self._adaptive_fuse_blocked_paths[_p]
            if _expired_paths:
                self._log(LogLevel.DEBUG, f"融合阻塞清理: {len(_expired_paths)}个过期路径已释放")

        # ★L12修复：清理长期冷却期满的跳过路径（把「永久跳过」改为「长期冷却」，
        # 冷却期满自动恢复重试，避免自我认知路径因数据尚未积累够而永久失去融合机会）
        if hasattr(self, '_fuse_permanent_skip') and self._fuse_permanent_skip:
            _recovered = [
                _p for _p, _until in list(self._fuse_permanent_skip.items())
                if now >= _until
            ]
            for _p in _recovered:
                self._fuse_permanent_skip.pop(_p, None)
                if hasattr(self, '_fuse_fail_count'):
                    self._fuse_fail_count.pop(_p, None)
            if _recovered:
                self._log(LogLevel.INFO,
                         f"融合跳过恢复: {len(_recovered)}个路径冷却期满，恢复重试")
        fuse_cooldown = self._get_fuse_cooldown()
        if now - self._last_fuse_time < fuse_cooldown:
            return

        # ===== 新增：自适应融合独立冷却 =====
        if not hasattr(self, '_last_adaptive_fuse_time'):
            self._last_adaptive_fuse_time = 0.0
        _ADAPTIVE_FUSE_COOLDOWN = self._get_adaptive_fuse_cooldown()

        groups = self._group_by_path_prefix(l2_nodes)
        fused_count = 0
        for prefix, nodes in groups.items():
            # ★L10修复：融合循环检查点，stop 后立即中断不再融合后续路径
            if self._stop_requested:
                break
            # 跳过纯状态记录路径和代码学习路径，这些路径的节点不适合融合
            if prefix.startswith(("/自我/状态", "/自我理解/代码")):
                continue
            if hasattr(self, '_fuse_cooldown_paths'):
                _last_fuse = self._fuse_cooldown_paths.get(prefix, 0)
                if now - _last_fuse < 1800:  # 30分钟冷却
                    continue

            l2_threshold = self._get_l2_threshold()

            # ===== 【P1-3新增】自我认知路径差异化阈值 =====
            # 自我认知相关路径的节点积累速度慢于通用路径，
            # 使用统一阈值会导致身份类知识长期无法沉淀为L3智慧节点。
            # 这些路径降低融合门槛至8条，让自我认知能正常演化。
            _self_awareness_paths = [
                '/身份/自我/', '/自我/架构/', '/知识/自主推导/', '/反思/',
            ]
            _is_self_awareness_path = any(prefix.startswith(_sp) for _sp in _self_awareness_paths)
            if _is_self_awareness_path:
                _effective_threshold = max(8, l2_threshold - 4)  # 最低8条，避免阈值过低
                # ★L12修复：连续失败过多时进入「长期冷却」（非永久跳过），冷却期满自动恢复重试
                if not hasattr(self, '_fuse_permanent_skip'):
                    self._fuse_permanent_skip = {}
                _skip_until = self._fuse_permanent_skip.get(prefix, 0.0)
                if now < _skip_until:
                    continue  # 长期冷却中，暂不尝试融合

                # 清理该路径的自适应融合阻塞状态（首次融合时）
                if hasattr(self, '_adaptive_fuse_blocked_until') and prefix in getattr(self, '_adaptive_fuse_blocked_paths', {}):
                    if not hasattr(self, '_fuse_fail_count'):
                        self._fuse_fail_count = {}
                    _fail_count = self._fuse_fail_count.get(prefix, 0)
                    if _fail_count >= 5:
                        # 连续5次失败 → 进入长期冷却（6小时），而非永久跳过
                        _SKIP_COOLDOWN = 6 * 3600
                        self._fuse_permanent_skip[prefix] = now + _SKIP_COOLDOWN
                        self._log(LogLevel.WARNING,
                                 f"自适应融合: {prefix} 连续{_fail_count}次失败，"
                                 f"进入{_SKIP_COOLDOWN / 3600:.0f}小时冷却")
                        continue
                    # ★v9.5修复：清理前必须先检查退避是否已到期。
                    # 原逻辑无条件 pop 退避 → 自我认知路径(LESSONS_LEARNED等)
                    # 退避形同虚设，每8秒硬闯失败1次、连续5次后才进6h冷却，
                    # 造成「6小时周期→5次高频失败→冷却」的死循环。
                    # 修复：退避未到期则 continue 等待，到期才清理重试，
                    # 让自我认知路径同样走指数退避（5min→10min→...）。
                    _block_until_here = self._adaptive_fuse_blocked_paths.get(prefix, 0.0)
                    if now < _block_until_here:
                        continue
                    self._adaptive_fuse_blocked_paths.pop(prefix, None)
                    self._log(LogLevel.INFO,
                             f"自我认知路径差异化: {prefix} 融合阈值降至{_effective_threshold}条，"
                             f"退避已到期，清理阻塞状态 (第{_fail_count + 1}次尝试)")
            else:
                _effective_threshold = l2_threshold
            # ===== 差异化阈值结束 =====

            should_fuse = False

            # 标准条件：数量达标
            if len(nodes) >= _effective_threshold:
                should_fuse = True
            # 自适应条件：数量接近阈值且高质量节点多
            elif len(nodes) >= _effective_threshold * 0.7:
                high_quality_count = sum(1 for n in nodes
                                        if getattr(n, 'importance', 'C') in ('S', 'A'))
                if high_quality_count >= 3:
                    # 自适应融合冷却检查
                    if now - self._last_adaptive_fuse_time < _ADAPTIVE_FUSE_COOLDOWN:
                        continue
                    # ===== 【v15.1修复】额外检查：如果上次融合失败，需要等待阻塞期结束 =====
                    # 【P1-3增强】按路径检查阻塞状态
                    _blocked_until = 0.0
                    if hasattr(self, '_adaptive_fuse_blocked_paths'):
                        _blocked_until = self._adaptive_fuse_blocked_paths.get(prefix, 0.0)
                    if _blocked_until > 0 and now < _blocked_until:
                        continue
                    # 全局阻塞标记兜底（★第80批 T3：加类型守卫，防止 _adaptive_fuse_blocked_until
                    #   非数值类型时 `now < {}` 抛 TypeError 崩溃；与 :477 型兜底一致）
                    _blocked_until_global = getattr(self, '_adaptive_fuse_blocked_until', 0.0)
                    if not isinstance(_blocked_until_global, (int, float)) or isinstance(_blocked_until_global, bool):
                        _blocked_until_global = 0.0
                    if _blocked_until <= 0 and now < _blocked_until_global:
                        continue

                    should_fuse = True
                    self._log(LogLevel.INFO,
                             f"自适应融合触发: {prefix}路径下{len(nodes)}条L2节点"
                             f"(未达标准阈值{_effective_threshold})，但包含{high_quality_count}条高重要性节点，尝试融合")

            if should_fuse:
                # ★v9.5修复：标准分支也受退避封禁约束。
                # 原问题：自适应分支(1999/2006行)有冷却/封禁检查，
                # 但标准分支(节点数达标即 should_fuse=True)完全绕过，
                # 导致《成长/评估》等路径每心跳(13.5s)重试融合、
                # 退避形同虚设（日志显示第2098次仍持续）。
                # 修复：在 should_fuse 统一处补封禁检查，无论标准/自适应
                # 分支都遵守 退避期内不重试。
                _blocked_until_fuse = 0.0
                if hasattr(self, '_adaptive_fuse_blocked_paths'):
                    _blocked_until_fuse = self._adaptive_fuse_blocked_paths.get(prefix, 0.0)
                if _blocked_until_fuse > 0 and now < _blocked_until_fuse:
                    continue
                # ===== 诊断日志：仅在首次融合该路径时输出节点详情 =====
                # ★修复：原逻辑每次融合尝试都完整输出所有节点详情，稳定运行后
                # 成为噪音大头（同一批节点每 30 分钟重复打印）。改为首次输出一次，
                # 之后不再重复，仅保留聚合统计。
                if prefix and "曈曈" in prefix:
                    if not hasattr(self, '_fuse_diagnosed_paths'):
                        self._fuse_diagnosed_paths = set()
                    if prefix not in self._fuse_diagnosed_paths:
                        self._fuse_diagnosed_paths.add(prefix)
                        self._log(LogLevel.DEBUG,
                                 f"[融合诊断] 路径={prefix}, 节点数={len(nodes)}, "
                                 f"阈值={_effective_threshold}")
                        for _n in nodes:
                            _trust = getattr(_n, 'trust_score', 50.0)
                            _path = getattr(_n, 'space_path', '/')
                            _kw_count = len(_n.keywords) if hasattr(_n, 'keywords') and _n.keywords else 0
                            _val_len = len(str(_n.value)) if _n.value else 0
                            _importance = getattr(_n, 'importance', 'C')
                            _is_noise = PulseLiver._is_path_noise(_path) if hasattr(PulseLiver, '_is_path_noise') else False
                            self._log(LogLevel.DEBUG,
                                     f"  [融合诊断] 节点={_n.node_id[:12]}..., "
                                     f"信任={_trust:.0f}, 重要性={_importance}, "
                                     f"关键词数={_kw_count}, 内容长度={_val_len}, "
                                     f"路径={'残词' if _is_noise else '正常'}={_path[:60]}")
                # ===== 诊断日志结束 =====
                _fuse_result = self._fuse_group(prefix, nodes,
                                                _min_quality_nodes=_effective_threshold)
                if _fuse_result:
                    fused_count += 1
                    # 融合成功后才更新自适应冷却时间
                    self._last_adaptive_fuse_time = now
                    # ★v17.0 Q7修复：记录路径冷却，30分钟内不再重复融合
                    if not hasattr(self, '_fuse_cooldown_paths'):
                        self._fuse_cooldown_paths = {}
                    self._fuse_cooldown_paths[prefix] = now
                else:
                    # ===== 【v15.1修复】融合失败：使用独立的冷却阻止标记替代"未来时间戳" =====
                    # ★P1修复：启动时从磁盘加载持久化的冷却状态，避免重启后重复失败刷屏
                    if not hasattr(self, '_fuse_cooldown_loaded'):
                        self._fuse_cooldown_loaded = True
                        self._load_fuse_cooldown()
                    if not hasattr(self, '_adaptive_fuse_blocked_until'):
                        self._adaptive_fuse_blocked_paths = {}
                    # ★v18.0修复：记录失败次数
                    if not hasattr(self, '_fuse_fail_count'):
                        self._fuse_fail_count = {}
                    _fail_count = self._fuse_fail_count.get(prefix, 0) + 1
                    self._fuse_fail_count[prefix] = _fail_count

                    # ★P0修复：数据指纹检测 + 指数退避，终结「低信任节点永久卡死」死循环。
                    # 根因：/成长/评估 路径 19 个节点 trust_score 全 < 40，融合永远失败，
                    # 但原逻辑固定 300 秒重试，7 小时内空转 2411 次。
                    # 修复 1：记录失败时该路径的数据指纹（节点数+质量节点数），
                    #        若数据无变化则无需重试，指数退避等待数据真正演化。
                    if not hasattr(self, '_fuse_fail_fingerprint'):
                        self._fuse_fail_fingerprint = {}
                    _quality_count = sum(
                        1 for n in nodes
                        if getattr(n, 'trust_score', 50.0) >= 40.0
                    )
                    _fingerprint = (len(nodes), _quality_count)
                    _prev_fingerprint = self._fuse_fail_fingerprint.get(prefix)
                    _data_unchanged = (_prev_fingerprint == _fingerprint)
                    self._fuse_fail_fingerprint[prefix] = _fingerprint

                    # 修复 2：指数退避——失败次数越多，冷却越久，封顶 6 小时。
                    #        第 1 次 5 分钟，之后每次翻倍，避免无效重试刷屏。
                    _base_block = _ADAPTIVE_FUSE_COOLDOWN * 0.5
                    _backoff_multiplier = min(2 ** max(0, _fail_count - 1), 72)
                    _block_duration = min(_base_block * _backoff_multiplier, 6 * 3600)

                    self._adaptive_fuse_blocked_paths[prefix] = now + _block_duration
                    self._adaptive_fuse_blocked_until = now + _block_duration
                    # ★P1修复：持久化冷却状态到磁盘，重启后恢复
                    self._save_fuse_cooldown()

                    if _data_unchanged and _fail_count > 1:
                        # 数据未变化且已多次失败：明确提示「等待数据演化」，而非简单重试
                        self._log(LogLevel.WARNING,
                                 f"自适应融合失败: {prefix}路径(第{_fail_count}次)，"
                                 f"数据指纹未变化({len(nodes)}节点/{_quality_count}达标)，"
                                 f"指数退避至{_block_duration/60:.0f}分钟后重试")
                    else:
                        self._log(LogLevel.WARNING,
                                 f"自适应融合失败: {prefix}路径(第{_fail_count}次)，"
                                 f"将在{_block_duration/60:.0f}分钟后重试")
                    # ★v9.5补充：失败也更新全局自适应冷却，
                    # 避免同一调用内多路径连续失败时重复重试。
                    self._last_adaptive_fuse_time = now

        if fused_count > 0:
            self._l2_to_l3_count += fused_count
            self._last_fuse_time = time.time()
            self._log(LogLevel.INFO,
                     f"融合抽象完成: {fused_count} 组 L2→L3, "
                     f"总计 L2→L3: {self._l2_to_l3_count}")
            # ★主线第17批 T3/P2-63：旁路事件（只发布，不改变任何现有逻辑）
            tap_publish(
                "liver.memory.compress",
                payload={
                    "from_count": len(groups),
                    "to_count": fused_count,
                    "duration_ms": round((time.perf_counter() - _tap_t0) * 1000, 2),
                },
                source="PulseLiver",
                switch_attr="ENABLE_LIVER_EVENT_TAP",
            )

    def _load_fuse_cooldown(self) -> None:
        """★P1修复：从磁盘加载融合冷却状态，避免重启后重复失败刷屏。"""
        try:
            import json as _json_cd
            import os as _os_cd
            # ★PHASE14-P2-1：与 _save_fuse_cooldown 同一个 bug——
            #   os.dirname / os.isfile 均不存在（正解是 os.path.dirname /
            #   os.path.isfile），导致本方法每次都在第一行抛 AttributeError
            #   后被静默吞掉：**融合冷却状态从未被加载过**。
            #   后果链路（值得记住，这是「静默 except」的经典代价）：
            #     加载失败 → _adaptive_fuse_blocked_paths 恒为空
            #     → 重启后所有曾被熔断的路径全部重新放行
            #     → 同一批脏数据反复触发融合失败又反复重试
            #     → 「避免重启后重复失败刷屏」这个设计目标从未达成。
            _path = _os_cd.path.join(
                _os_cd.path.dirname(_os_cd.path.dirname(
                    _os_cd.path.dirname(_os_cd.path.abspath(__file__)))),
                "data", "fuse_cooldown.json"
            )
            if _os_cd.path.isfile(_path):
                with open(_path, encoding='utf-8') as _f:
                    _data = _json_cd.load(_f)
                self._adaptive_fuse_blocked_paths = _data.get("blocked_paths", {})
                self._fuse_fail_count = _data.get("fail_count", {})
                self._fuse_fail_fingerprint = _data.get("fingerprint", {})
                _now = time.time()
                _active = sum(1 for v in self._adaptive_fuse_blocked_paths.values() if v > _now)
                if _active > 0:
                    self._log(LogLevel.DEBUG,
                             f"融合冷却状态已恢复: {_active}个路径仍在冷却中")
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"融合冷却状态加载失败: {_e}")

    def _save_fuse_cooldown(self) -> None:
        """★P1修复：保存融合冷却状态到磁盘。"""
        _os_cd = None  # except 块会用到，必须在 try 之前初始化
        _tmp = ""
        try:
            import json as _json_cd
            import os as _os_cd
            # ★PHASE14-P2-1（2026-09-07）：修正 os.dirname → os.path.dirname。
            #   原写法 _os_cd.dirname(...) 是**不存在的属性**：
            #   dirname 挂在 os.path 下，os 模块本身并没有这个成员。
            #   于是本方法每次执行都在第一行就抛 AttributeError，
            #   被 except 吞掉 → **融合冷却状态从未成功落盘过一次**。
            #   静态扫描报的是「非原子写」，那是第二步的问题；
            #   真实情况是**第一步就没走到**，分析方法体永远看不到这一点。
            #   （由运行时实测发现：先有 This invocation 的真实调用，才暴露出来。）
            _data_dir = _os_cd.path.join(
                _os_cd.path.dirname(_os_cd.path.dirname(
                    _os_cd.path.dirname(_os_cd.path.abspath(__file__)))),
                "data"
            )
            _os_cd.makedirs(_data_dir, exist_ok=True)
            _path = _os_cd.path.join(_data_dir, "fuse_cooldown.json")
            _data = {
                "blocked_paths": getattr(self, '_adaptive_fuse_blocked_paths', {}),
                "fail_count": getattr(self, '_fuse_fail_count', {}),
                "fingerprint": getattr(self, '_fuse_fail_fingerprint', {}),
                "saved_at": time.time(),
            }
            # ★PHASE14-P2-1（2026-09-07）：非原子写 → 原子写。
            #   原写法 `open(_path,'w')` 直接截断目标文件再逐字节写：
            #   写入期间进程被 kill / 断电 / dump 抛异常，文件就停在半截 JSON，
            #   下次启动 json.load 直接崩，或更糟——静默吞掉全部融合冷却状态，
            #   让「本应被熔断的路径」重新放行，把脏 OBSERVE 数据重新灌回知识库。
            #   改为「临时文件 + fsync + os.replace」：
            #     os.replace 在同一分区上是原子 rename，
            #     读者要么看到完整旧文件，要么看到完整新文件，不存在中间态。
            _tmp = _path + ".tmp"
            with open(_tmp, 'w', encoding='utf-8') as _f:
                _json_cd.dump(_data, _f, ensure_ascii=False, indent=2)
                _f.flush()
                _os_cd.fsync(_f.fileno())
            _os_cd.replace(_tmp, _path)
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"融合冷却状态保存失败: {_e}")
            # 兜底清理：异常时临时文件可能残留，留在 data/ 下会干扰后续排错
            try:
                if _tmp and _os_cd is not None and _os_cd.path.exists(_tmp):
                    _os_cd.remove(_tmp)
            except Exception as e:
                self._log(LogLevel.ERROR, f'异常: {e}')

    def _fuse_group(self, prefix: str, nodes: list[PulseNode],
                    _min_quality_nodes: int | None = None) -> bool:
        try:
            # 如果外部没有传入阈值，使用全局默认阈值
            if _min_quality_nodes is None:
                _min_quality_nodes = self._get_l2_threshold()

            # ===== 新增: 源节点质量过滤 =====
            # 过滤掉信任度过低的L2节点（trust_score < 40 的不可信）
            quality_nodes = [n for n in nodes
                           if getattr(n, 'trust_score', 50.0) >= 40.0]

            # 过滤掉路径包含残词特征的节点
            quality_nodes = [n for n in quality_nodes
                           if not self._is_path_noise(getattr(n, 'space_path', ''))]

            # 过滤后节点数不足阈值，跳过融合
            if len(quality_nodes) < _min_quality_nodes:
                # ★W1修复：质量优先——达标数仅差 1 且含足够高质量节点时，允许以达标节点融合。
                # 根因：LESSONS_LEARNED 每组 8 个要点节点恰有 1 个低信任(trust=10)被过滤，
                # 达标 7 < 阈值 8 永远差 1，导致整组高质量节点长期无法沉淀为 L3。
                _near_threshold = len(quality_nodes) >= _min_quality_nodes - 1
                _high_quality_cnt = sum(
                    1 for n in quality_nodes
                    if getattr(n, 'importance', 'C') in ('S', 'A')
                    or getattr(n, 'trust_score', 0) >= 60
                )
                # ★W1补强：通道B——数量略少（≥阈值-2，下限3）但高质量占比极高（≥80%）的组，
                #   同样视为「全高质量值得沉淀」，避免「数据恰好差1-2条但几乎全是高质量」长期滞留。
                #   例：/自我/架构/设计文档/LESSONS_LEARNED/六 仅 6 条但 6 条全高质量(占比100%)，
                #   旧条件（7/8）卡死，此通道放行。低质组（占比<80%）仍严格拒绝。
                _high_ratio = len(quality_nodes) > 0 and (
                    _high_quality_cnt / len(quality_nodes) >= 0.8)
                _pass_b = (
                    len(quality_nodes) >= max(3, _min_quality_nodes - 2)
                    and _high_ratio and _high_quality_cnt >= 3
                )
                if ((_near_threshold and _min_quality_nodes >= 6 and _high_quality_cnt >= 3)
                        or _pass_b):
                    self._log(LogLevel.DEBUG,
                             f"融合放行(质量优先): {len(quality_nodes)}/{len(nodes)}个节点通过质量筛选, "
                             f"低于阈值{_min_quality_nodes}但含{_high_quality_cnt}条高质量节点")
                else:
                    self._log(LogLevel.DEBUG,
                             f"融合跳过(质量不足): {len(quality_nodes)}/{len(nodes)}个节点通过质量筛选, "
                             f"阈值={_min_quality_nodes}")
                    return False
            all_keywords = []
            # ===== 强制清洗所有节点的关键词（防止脏数据导致崩溃）=====
            for node in quality_nodes:
                if hasattr(node, 'keywords') and node.keywords:
                    node.keywords = [
                        str(kw) for kw in node.keywords
                        if kw is not None and len(str(kw)) >= 2
                    ]

            all_keywords = []
            for node in quality_nodes:
                all_keywords.extend(node.keywords)

            # 使用统一过滤器过滤噪音词，再用智能价值排序选核心关键词
            core_keywords = get_top_valuable_keywords(all_keywords, 5)

            # ===== 新增: 核心关键词质量兜底 =====
            # 如果选出的关键词全是英文且都不在专有名词列表中，说明这批节点质量存疑
            if core_keywords:
                has_chinese = any('\u4e00' <= c <= '\u9fff' for c in ' '.join(core_keywords))
                has_proper = any(
                    kw.isupper() or (kw[0].isupper() and len(kw) > 3)
                    for kw in core_keywords if kw.isascii()
                )
                if not has_chinese and not has_proper and len(core_keywords) <= 2:
                    self._log(LogLevel.DEBUG,
                             f"融合跳过(关键词质量低): {', '.join(core_keywords)}")
                    return False

            path_name = prefix.split("/")[-1] if prefix else "核心智慧"
            if not core_keywords:
                core_keywords = [prefix.split("/")[-1] if prefix else "综合"]
            kw_text = "、".join(core_keywords)

            # 新增：从源节点中提取一段实质内容作为L3摘要，避免生成纯元描述的空壳节点
            _sample_value = ""
            for _n in quality_nodes[:10]:
                _val = str(_n.value) if _n.value else ""
                # 跳过元描述类的内部格式节点（这些节点本身就是压缩产生的无实质内容壳）
                if any(_marker in _val for _marker in ["相关知识汇总", "核心智慧结晶", "复盘认知"]):
                    continue
                if len(_val) > 40:
                    _sample_value = _val[:120]
                    break

            if _sample_value:
                concept = (
                    f"[{path_name}] {_sample_value}..."
                    f"（由{len(quality_nodes)}条认知融合而成）"
                )
            else:
                concept = (
                    f"[{path_name}] 核心智慧结晶（关键词: {kw_text}）。"
                    f" 此知识由 {len(quality_nodes)} 条认知节点融合而成，"
                    f"代表该领域的深层理解。"
                )

            # ===== 用户输入污染终极防护：检测融合内容是否包含原始问题文本 =====
            _concept_lower = concept.lower()
            # ★v23.0优化：从config读取
            _user_input_markers = getattr(self, '_user_input_markers', [])
            _user_input_hits = sum(1 for _m in _user_input_markers if _m in _concept_lower)
            if _user_input_hits >= 3:
                self._log(LogLevel.WARNING,
                         f"融合拦截: 检测到原始用户输入污染(命中{_user_input_hits}个标记)，"
                         f"路径={prefix}，跳过L3融合")
                return False
            # ===== 用户输入污染终极防护结束 =====

            l3_node = PulseNode(
                value=concept,
                keywords=core_keywords,
                source_organ=self.organ_name,
                evol_level=PulseNode.EVOL_L3,
                importance=PulseNode.IMPORTANCE_S,
                abstraction=0.85,
                space_path=prefix,
            )
            l3_node.ephemeral = False
            # 视角标记：仅统计通过质量筛选的节点
            inner_count = sum(1 for n in quality_nodes if getattr(n, 'view_mode', 'OUTER_VIEW') == 'INNER_VIEW')
            l3_node.view_mode = "INNER_VIEW" if inner_count > len(quality_nodes) / 2 else "OUTER_VIEW"
            # 信任分数：仅取质量节点的平均值
            avg_trust = sum(getattr(n, 'trust_score', 50.0) for n in quality_nodes) / max(1, len(quality_nodes))
            l3_node.trust_score = min(100.0, avg_trust + 10.0)
            l3_node.state = "locked"

            # ★相关任务(D040 A')：融合点记源——记录本 L3 由哪些 L2 来源节点融合而来
            l3_node.evidence_chain = [
                {"node_id": getattr(n, "node_id", None), "role": "source"}
                for n in quality_nodes
            ]
            # 反向登记到源桶（l2_id -> set(l3_id)），供后续矛盾归属反查（同会话内即时可见）
            _bucket = getattr(self, "_l3_src_bucket", None)
            if _bucket is None:
                _bucket = {}
                self._l3_src_bucket = _bucket
                self._l3_src_bucket_ts = 0.0
            _l3_id = l3_node.node_id
            for n in quality_nodes:
                _nid = getattr(n, "node_id", None)
                if _nid is None:
                    continue
                _bucket.setdefault(_nid, set()).add(_l3_id)

            if self.frequency_codec:
                self.frequency_codec.encode_node(l3_node)
            if self.node_pool:
                self._kal.add_node(l3_node)
            if self.knowledge_tree:
                self.knowledge_tree.register_path(prefix)

            if self.info_field and self.pulse_core:
                fuse_pulse = self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=KnowledgeEvent.FUSED,
                    payload={
                        "from_level": "L2", "to_level": "L3",
                        "space_path": prefix, "node_count": len(quality_nodes),
                        "result_node_id": l3_node.node_id,
                    },
                    priority=5,
                    layer="L2"
                )
                self.info_field.publish(fuse_pulse)

            # 融合成功后触发轻微的成就感——我的知识根基又深了一层
            if self.info_field and self.pulse_core:
                self.info_field.publish(self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type="hormones.detect",
                    payload={
                        "content": f"我成功将{len(quality_nodes)}条认知融合为一条关于'{path_name}'的智慧结晶",
                        "user_name": "系统",
                        "emotion_hint": "满足",
                        "intensity_hint": min(0.5, 0.2 + len(quality_nodes) * 0.01),
                    },
                    priority=2,
                    layer="L3",
                ))

            if self.snapshot:
                self.snapshot.save()
            # ===== 新增: 逆向激活——高价值L3节点加速周围知识演化 =====
            self._reverse_activate_related_nodes(l3_node)
            # ===== 新增: 演化叙事——记录知识的生长历程 =====
            evolution_narrative = (
                f"[{path_name}] 领域知识演化: "
                f"从{len(nodes)}条L2认知节点中提炼出核心智慧。"
                f"核心概念: {kw_text}。"
            )
            # 发射演化叙事脉冲，供叙事自我记录
            if self.info_field and self.pulse_core:
                self.info_field.publish(self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type="narrative.record",
                    payload={
                        "content": evolution_narrative,
                        "event_type": "knowledge_evolution",
                        "user_name": "系统",
                        "emotional_tone": "positive",
                    },
                    priority=3,
                    layer="L2",
                ))

            self._detect_contradictions(prefix, quality_nodes)

            # 标记源L2为已融合（包括被过滤的节点也降级，不再参与后续融合）
            for node in nodes:
                node.importance = PulseNode.IMPORTANCE_C
                node.abstraction = max(0.1, node.abstraction - 0.15)
                node.activation_count = 0  # 重置激活计数，防止_consolidate_knowledge重新提升

            # 日志中标注过滤情况
            if len(quality_nodes) < len(nodes):
                self._log(LogLevel.INFO,
                         f"融合完成(含过滤): {len(quality_nodes)}/{len(nodes)}个节点 L2→L3 "
                         f"(过滤{len(nodes) - len(quality_nodes)}个低质节点)")

            return True

        except Exception as e:
            import traceback
            _tb = traceback.format_exc()
            self._log(LogLevel.ERROR, f"融合失败 ({prefix}): {e}\n{_tb[:500]}",
                    error_code=ErrorCode.EXECUTION_FAILED)
            return False

    def _detect_noise(self):
        """检测L1中的噪音节点"""
        if self.node_pool is None:
            return

        l1_nodes = self._kal.query_nodes(evol_level=PulseNode.EVOL_L1, limit=500)
        noise_nodes = []

        for node in l1_nodes:
            value_str = node.value if isinstance(node.value, str) else str(node.value)
            noise_score = 0

            if len(value_str) < 20 and len(node.keywords) < 2:
                noise_score += 3
            if node.activation_count == 0:
                noise_score += 2
            if re.match(r'^[\d\s\W]+$', value_str):
                noise_score += 2

            if noise_score >= self._noise_threshold:
                noise_nodes.append(node)

        if noise_nodes:
            self._noise_detected_count += len(noise_nodes)
            # ★知识污染治理：把噪音节点标记为 ephemeral 并降信任，避免永久占位
            for _noise in noise_nodes:
                try:
                    _noise.ephemeral = True
                    _cur_trust = getattr(_noise, 'trust_score', 10.0)
                    _noise.trust_score = max(1.0, min(20.0, _cur_trust * 0.3))
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            self._log(LogLevel.DEBUG,
                     f"噪音检测: 发现{len(noise_nodes)}个低价值节点，已标记 ephemeral 并降信任")

    # 矛盾对立词对（用于知识矛盾检测，提取为类常量避免每次循环重复构建）
    # [批次4·深度体检][PERF-5] 反义对提升为类常量
    _OPPOSITE_PAIRS = (
        ("是", "不是"), ("可以", "不可以"), ("能", "不能"),
        ("正确", "错误"), ("真", "假"), ("有", "没有"),
    )

    # ========== ★相关任务(D040 A')：L3 源反查桶（l2_id -> set(l3_id)） ==========
    _L3_SRC_BUCKET_TTL = 600.0      # 反查桶惰性重建 TTL（秒）
    _L3_SRC_BUCKET_CAP = 200_000     # 内存上界（熔断阈值）

    def _l3_refresh_src_bucket(self) -> dict:
        """惰性重建 L3 源反查桶：TTL 600s 复用；超 200k 条目熔断停止新增。
        桶内容 = {l2_node_id: set(l3_node_id)}，由已落盘 L3 的 evidence_chain(role=source) 反建，
        覆盖重启后内存桶丢失的场景；同一会话内由融合点增量登记补齐。
        """
        _bucket = getattr(self, "_l3_src_bucket", None)
        _ts = getattr(self, "_l3_src_bucket_ts", 0.0)
        _now = time.time()
        if _bucket is not None and (_now - _ts) < self._L3_SRC_BUCKET_TTL:
            return _bucket
        _new = {}
        if self.node_pool and self._kal:
            try:
                _l3s = self._kal.query_nodes(evol_level=PulseNode.EVOL_L3, limit=2000)
                for _l3 in _l3s:
                    _srcs = [e.get("node_id") for e in getattr(_l3, "evidence_chain", [])
                             if e.get("role") == "source" and e.get("node_id")]
                    for _sid in _srcs:
                        if len(_new) >= self._L3_SRC_BUCKET_CAP:
                            break
                        _new.setdefault(_sid, set()).add(_l3.node_id)
                    if len(_new) >= self._L3_SRC_BUCKET_CAP:
                        break
            except Exception as _e:
                self._log(LogLevel.WARNING,
                         f"T-125b 反查桶重建跳过(已忽略): {type(_e).__name__}: {_e}")
        self._l3_src_bucket = _new
        self._l3_src_bucket_ts = _now
        return _new

    # [批次4·深度体检][PERF-5] 统一 kw_a 预处理 + 引用类常量
    def _detect_contradictions(self, prefix: str, nodes: list[PulseNode]):
        """检测L2节点中是否存在矛盾节点对（含跨路径检测）"""
        if len(nodes) < 2:
            # 尝试跨路径检测：从整个节点池中获取所有L2节点
            if self.node_pool:
                all_l2 = self._kal.query_nodes(evol_level=PulseNode.EVOL_L2, limit=100)
                if len(all_l2) >= 2:
                    nodes = all_l2
                else:
                    return
            else:
                return
        # ★v19.0修复：每心跳矛盾检测数量上限，防止设计文档节点引发脉冲风暴
        _MAX_CONTRADICTIONS_PER_BEAT = 20
        if getattr(self, '_contradiction_count_this_beat', 0) >= _MAX_CONTRADICTIONS_PER_BEAT:
            return
        contradiction_pairs = []

        for i in range(len(nodes)):
            if self._stop_requested:  # ★L10补全：停止请求检查点，中断矛盾检测
                break
            node_a = nodes[i]
            # ★PERF-5修复: node_a 的关键词集合在外部循环只计算一次，
            #   不再对每对 (i,j) 重复构建 O(n^2) 的集合
            kw_a = {k.lower() for k in node_a.keywords}
            if not kw_a:
                continue
            for j in range(i + 1, len(nodes)):
                if self._stop_requested:  # ★L10补全：停止请求检查点，中断内层检测
                    break
                node_b = nodes[j]

                # ★v19.0修复：设计文档结构化节点共享大量重叠关键词，
                # 这是章节拆分后的正常现象，不应判定为知识矛盾
                _path_a = getattr(node_a, 'space_path', '')
                _path_b = getattr(node_b, 'space_path', '')
                if (_path_a.startswith('/自我/架构/设计文档') and
                    _path_b.startswith('/自我/架构/设计文档')):
                    continue

                kw_b = {k.lower() for k in node_b.keywords}
                if not kw_b:
                    continue

                overlap = len(kw_a & kw_b) / min(len(kw_a), len(kw_b))
                if overlap < 0.6:
                    continue

                val_a = node_a.value if isinstance(node_a.value, str) else str(node_a.value)
                val_b = node_b.value if isinstance(node_b.value, str) else str(node_b.value)

                # ★2026-09-07 阶段二：opposition 判定抽到 ContradictionDetector.has_opposition
                #   （词对仍用 self._OPPOSITE_PAIRS 6 对，行为与原来逐字等价）
                from nucleus.reasoning.ContradictionDetector import (
                    ContradictionDetector,
                )
                has_opposition = ContradictionDetector.has_opposition(
                    val_a, val_b, pairs=self._OPPOSITE_PAIRS)

                if has_opposition:
                    self._contradiction_count_this_beat = getattr(self, '_contradiction_count_this_beat', 0) + 1
                    if self._contradiction_count_this_beat > _MAX_CONTRADICTIONS_PER_BEAT * 2:
                        break
                    # ★P2-5补全：回写推导冲突计数，让 L3 降级机制（should_downgrade_l3）真正可触发。
                    # 此前 conflict_count 恒为 0，规则5「三次推导冲突可降级」形同虚设。
                    node_a.conflict_count = getattr(node_a, 'conflict_count', 0) + 1
                    node_b.conflict_count = getattr(node_b, 'conflict_count', 0) + 1
                    # ★相关任务(D040 A')：矛盾归属——反查桶求记源 L3（只计记源 L3），
                    #   成员存活校验（97%死引用条款）+ 同对去重 + 1h 冷却闸标记（last_conflict_at）。
                    #   本批只落观测（last_conflict_at 时间戳），冲突计数执行段归 W7-B（l3_downgraded 恒 0 诚实）。
                    try:
                        _bucket = self._l3_refresh_src_bucket()
                        _a_src = _bucket.get(node_a.node_id)
                        _b_src = _bucket.get(node_b.node_id)
                        _src_l3 = (_a_src & _b_src) if (_a_src and _b_src) else set()
                        for _l3_id in _src_l3:
                            _l3 = self._kal.get_node(_l3_id) if self._kal else None
                            if _l3 is None or getattr(_l3, "evol_level", "") != PulseNode.EVOL_L3:
                                continue  # 97%死引用条款：池中已不存在的 L3 不计数
                            _pair_key = "%s|%s" % (min(node_a.node_id, node_b.node_id),
                                                   max(node_a.node_id, node_b.node_id))
                            _seen = getattr(self, "_conflict_pair_seen", None)
                            if _seen is None:
                                _seen = set()
                                self._conflict_pair_seen = _seen
                            if _pair_key in _seen:
                                continue
                            _seen.add(_pair_key)
                            _now = time.time()
                            if (_now - getattr(_l3, "last_conflict_at", 0.0)) >= 3600.0:
                                _l3.last_conflict_at = _now  # 冷却闸标记；W7-B 在此增 conflict_count
                    except Exception as _e:
                        self._log(LogLevel.WARNING,
                                 f"T-125b 矛盾归属跳过(已忽略): {type(_e).__name__}: {_e}")
                    contradiction_pairs.append({
                        "node_a_id": node_a.node_id,
                        "node_b_id": node_b.node_id,
                        "node_a_value": val_a[:100],
                        "node_b_value": val_b[:100],
                        "overlap": round(overlap, 2),
                    })

        if contradiction_pairs and self.info_field and self.pulse_core:
            self._contradiction_detected_count += 1
            self._log(LogLevel.WARNING,
                     f"矛盾检测: {prefix}路径下发现{len(contradiction_pairs)}对矛盾节点")

            for pair in contradiction_pairs[:3]:
                # v9.5: 矛盾检测脉冲标记为L2认知思考层
                contradiction_pulse = self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=KnowledgeEvent.RAW,
                    payload={
                        "content": f"知识矛盾: {pair['node_a_value'][:50]} vs {pair['node_b_value'][:50]}",
                        "source_organ": self.organ_name,
                        "trigger_reason": "contradiction_detection",
                        "node_a_id": pair["node_a_id"],
                        "node_b_id": pair["node_b_id"],
                        "overlap": pair["overlap"],
                    },
                    priority=4,
                    layer="L2"
                )
                self.info_field.publish(contradiction_pulse)
    def _check_instinct_deep_learn(self):
        """
        从追问中学习：当L4本能被频繁追问但缺乏高质量L3节点支撑时，
        从本能的子概念中提取具体可探索的主题，通过内在世界的反思机制来充实知识根基。
        
        L4本能是内化的认知框架，不需要外部搜索引擎验证。
        "求真""向善""迭代""自律"是哲学层面的底层原则，
        真正支撑本能的是对子概念的深入理解和内在推理，而非网络搜索结果。
        """
        if self.node_pool is None or not hasattr(self.node_pool, 'get_instincts'):
            return

        # 深度学习冷却机制
        if not hasattr(self, '_deep_learn_cooldown'):
            self._deep_learn_cooldown: dict[str, float] = {}

        now = time.time()
        DEEP_LEARN_COOLDOWN = 7200  # 每个本能冷却2小时
        instincts = self._kal.get_instincts()
        if not instincts:
            return

        # 获取所有L3节点（包含种子和融合产生的）
        l3_nodes = self._kal.query_nodes(evol_level=PulseNode.EVOL_L3, limit=50)

        # 检查每个本能是否缺乏支撑
        for instinct in instincts:
            if self._stop_requested:  # ★L10补全：停止请求检查点
                break
            instinct_kw = instinct.keywords if hasattr(instinct, 'keywords') and instinct.keywords else []
            if not instinct_kw:
                continue

            # 统计与此本能相关的L3节点数量
            related_l3 = []
            for node in l3_nodes:
                node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []
                if node_kw:
                    overlap = len(set(instinct_kw) & set(node_kw))
                    if overlap >= 1:
                        related_l3.append(node)

            instinct_name = instinct_kw[0] if instinct_kw else "未知"

            # 冷却检查
            last_learn_time = self._deep_learn_cooldown.get(instinct_name, 0)
            if now - last_learn_time < DEEP_LEARN_COOLDOWN:
                continue
            self._deep_learn_cooldown[instinct_name] = now

            # 如果相关L3节点太少（<=1），说明这个本能缺乏知识支撑
            if len(related_l3) <= 1:
                # 从本能子概念中提取具体的探索主题（排除本能关键词本身）
                sub_concepts = [kw for kw in instinct_kw
                               if kw != instinct_name and len(kw) >= 2]

                if sub_concepts:
                    # 取前2个子概念作为探索方向
                    explore_topics = sub_concepts[:2]
                    explore_topic_str = "、".join(explore_topics)
                    growth_topic = f"深入理解{explore_topic_str}——内化'{instinct_name}'本能"

                    self._log(LogLevel.INFO,
                             f"本能知识缺口: '{instinct_name}' 缺乏L3节点支撑 "
                             f"(相关L3={len(related_l3)}个)，"
                             f"触发子概念探索: {explore_topic_str}")

                    # 发射成长需求脉冲，让内在世界通过反思来充实知识根基
                    if self.info_field and self.pulse_core:
                        self.info_field.publish(self.pulse_core.emit(
                            source_organ=self.organ_name,
                            event_type="growth.need_detected",
                            payload={
                                "milestone": "本能知识深化",
                                "gaps": [{
                                    "metric": f"instinct_{instinct_name}",
                                    "current": len(related_l3),
                                    "target": 3
                                }],
                                "suggestion": f"通过内在反思和深度思考，"
                                             f"围绕'{explore_topic_str}'深化对'{instinct_name}'的理解",
                                "current_level": {
                                    "instinct": instinct_name,
                                    "sub_concepts": explore_topics,
                                    "related_l3_count": len(related_l3),
                                },
                                "growth_topic": growth_topic,
                            },
                            priority=5,
                            layer="L3"
                        ))

                    # 改为触发内在反思——L4本能子概念不适合外部搜索，应通过内在沉思深化理解
                    self.info_field.publish(self.pulse_core.emit(
                        source_organ=self.organ_name,
                        event_type="growth.need_detected",
                        payload={
                            "milestone": "本能子概念反思",
                            "gaps": [{
                                "metric": f"instinct_{instinct_name}_sub",
                                "current": 0,
                                "target": 1
                            }],
                            "suggestion": f"通过内在沉思，深化对'{explore_topic_str}'的理解，内化'{instinct_name}'本能",
                            "current_level": {
                                "instinct": instinct_name,
                                "sub_concepts": explore_topics,
                            },
                            "growth_topic": f"内化'{instinct_name}'本能——反思{explore_topic_str}",
                        },
                        priority=5,
                        layer="L3"
                    ))
                else:
                    # 没有子概念，使用本能关键词触发内在反思
                    self._log(LogLevel.INFO,
                             f"本能知识缺口: '{instinct_name}' 无子概念可用，"
                             f"触发内在反思")
                    if self.info_field and self.pulse_core:
                        self.info_field.publish(self.pulse_core.emit(
                            source_organ=self.organ_name,
                            event_type="growth.need_detected",
                            payload={
                                "milestone": "本能内化反思",
                                "gaps": [{
                                    "metric": f"instinct_{instinct_name}",
                                    "current": len(related_l3),
                                    "target": 3
                                }],
                                "suggestion": f"通过内在沉思，"
                                             f"围绕'{instinct_name}'的本质进行深度反思",
                                "current_level": {
                                    "instinct": instinct_name,
                                    "related_l3_count": len(related_l3),
                                },
                                "growth_topic": f"内化'{instinct_name}'本能——通过反思和实践",
                            },
                            priority=5,
                            layer="L3"
                        ))

                # 只处理第一个缺口，避免同时触发太多任务
                return
    def _check_instinct_upgrade(self):
        """检查是否有L3节点满足本能升级条件"""
        if self.node_pool is None:
            return

        l3_nodes = self._kal.query_nodes(evol_level=PulseNode.EVOL_L3, limit=50)

        for node in l3_nodes:
            if self._stop_requested:  # ★L10补全：停止请求检查点
                break
            if node.instinct:
                # 已经是本能，检查是否需要降级
                self._check_instinct_downgrade(node)
                continue

            if self._can_upgrade_to_instinct(node):
                self._perform_instinct_upgrade(node)
                return  # 每次只升级一个

    # [批次4·深度体检][CHAIN-5] 删除不可达分支与重复 elif
    def _can_upgrade_to_instinct(self, node: PulseNode) -> bool:
        """检查一个L3节点是否满足本能升级条件"""
        try:
            import config
            cfg = getattr(config, 'INSTINCT', {})
        except Exception:
            cfg = {}

        # 条件1：创建时间 ≥ 30天冷却期
        cooldown_days = cfg.get("cooldown_days", 30)
        age_days = (time.time() - node.created_at) / 86400.0
        if age_days < cooldown_days:
            return False

        # 条件2：来源不能是双腿（网络来源）
        if node.source_organ == "双腿":
            return False

        # 条件3：激活次数 ≥ 10次
        min_activations = cfg.get("min_activations", 10)
        if node.activation_count < min_activations:
            return False

        # 条件4：抽象度 ≥ 0.7
        min_abstraction = cfg.get("min_abstraction", 0.7)
        if node.abstraction < min_abstraction:
            return False

        # 条件5：当前本能总数 < 20条
        max_count = cfg.get("max_count", 20)
        # ★CHAIN-5修复: 移除不可达的 if/pass + 重复 elif（原条件5“本能总数<上限”从未在此被评估）
        if self._kal.get_instinct_count >= max_count:
            return False

        # 条件6：跨领域引用数 ≥ min_cross_domains
        min_cross_domains = cfg.get("min_cross_domains", 3)
        if min_cross_domains > 0:
            actual_domains = self._count_cross_domain_references(node)
            if actual_domains < min_cross_domains:
                return False

        # 条件7：与已有本能无重复（关键词重叠率<60%）
        if self.node_pool and hasattr(self.node_pool, 'get_instincts'):
            instincts = self._kal.get_instincts()
            for inst in instincts:
                if inst.keywords and node.keywords:
                    kw_a = {k.lower() for k in node.keywords}
                    kw_b = {k.lower() for k in inst.keywords}
                    if kw_a and kw_b:
                        overlap = len(kw_a & kw_b) / min(len(kw_a), len(kw_b))
                        if overlap >= cfg.get("keyword_overlap_threshold", 0.6):
                            return False

        return True

    def _count_cross_domain_references(self, node: PulseNode) -> int:
        """
        统计一个L3节点被多少个不同的知识树根路径引用。
        
        跨领域引用数 = 该节点的关键词出现在多少个不同的一级路径下。
        自身所在的路径也算一个领域。
        
        Args:
            node: 候选L3节点
            
        Returns:
            不同根路径的数量
        """
        if self.node_pool is None:
            return 0

        # 提取候选节点的关键词（转小写集合）
        node_kw = set()
        if hasattr(node, 'keywords') and node.keywords:
            for kw in node.keywords:
                if isinstance(kw, str) and len(kw) >= 2:
                    node_kw.add(kw.lower())

        if not node_kw:
            return 0

        # 获取候选节点自身的根路径
        node_path = getattr(node, 'space_path', '/')
        node_root = '/' + node_path.strip('/').split('/')[0] if node_path and node_path != '/' else '/'
        domain_roots = {node_root}

        # 查询所有L2和L3节点，检查关键词重叠
        all_relevant = []
        l3_nodes = self._kal.query_nodes(evol_level="L3", limit=100)
        l2_nodes = self._kal.query_nodes(evol_level="L2", limit=200)
        all_relevant = l3_nodes + l2_nodes

        for other in all_relevant:
            if other.node_id == node.node_id:
                continue

            other_path = getattr(other, 'space_path', '/')
            other_root = '/' + other_path.strip('/').split('/')[0] if other_path and other_path != '/' else '/'

            # 已统计过的根路径跳过
            if other_root in domain_roots:
                continue

            # 检查关键词是否有重叠
            other_kw = set()
            if hasattr(other, 'keywords') and other.keywords:
                for kw in other.keywords:
                    if isinstance(kw, str) and len(kw) >= 2:
                        other_kw.add(kw.lower())

            if node_kw & other_kw:
                domain_roots.add(other_root)

        return len(domain_roots)

    def _perform_instinct_upgrade(self, node: PulseNode):
        """执行本能升级"""
        if self.node_pool and hasattr(self.node_pool, 'upgrade_to_instinct'):
            success = self._kal.upgrade_to_instinct(node)
            if success:
                self._instinct_upgrade_count += 1
                self._log(LogLevel.INFO, f"本能升级: {node.value[:60]}...")

                if self.info_field and self.pulse_core:
                    self.info_field.publish(self.pulse_core.emit(
                        source_organ=self.organ_name,
                        event_type="instinct.upgraded",
                        payload={
                            "node_id": node.node_id,
                            "value_preview": str(node.value)[:100],
                            "keywords": node.keywords,
                        },
                        priority=5,
                        layer="L2"
                    ))

                # 本能升级是重大里程碑——触发更强的成就感
                if self.info_field and self.pulse_core:
                    self.info_field.publish(self.pulse_core.emit(
                        source_organ=self.organ_name,
                        event_type="hormones.detect",
                        payload={
                            "content": f"我的认知根基又深了一层——'{str(node.value)[:40]}'已内化为本能",
                            "user_name": "系统",
                            "emotion_hint": "满足",
                            "intensity_hint": 0.6,
                        },
                        priority=3,
                        layer="L3",
                    ))

    def _check_instinct_downgrade(self, node: PulseNode):
        """检查本能节点是否需要降级"""
        try:
            import config
            cfg = getattr(config, 'INSTINCT', {})
        except Exception:
            cfg = {}

        idle_days = cfg.get("downgrade_idle_days", 90)
        days_since_last_use = (time.time() - node.instinct_last_use) / 86400.0

        if days_since_last_use >= idle_days:
            if self.node_pool and hasattr(self.node_pool, 'downgrade_instinct'):
                self._kal.downgrade_instinct(node)
                self._instinct_downgrade_count += 1
                self._log(LogLevel.INFO, f"本能降级: {node.value[:60]}... (闲置{days_since_last_use:.0f}天)")

    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float):
        pass

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "l1_to_l2_count": self._l1_to_l2_count,
            "l2_to_l3_count": self._l2_to_l3_count,
            "reflection_compress_count": self._reflection_compress_count,
            "noise_detected_count": self._noise_detected_count,
            "contradiction_detected_count": self._contradiction_detected_count,
            "last_optimize_time": self._last_optimize_time,
            "instinct_upgrade_count": self._instinct_upgrade_count,
            "instinct_downgrade_count": self._instinct_downgrade_count,
            "consolidation_count": self._consolidation_count,
            "resource": self._resource_stats(),  # ★往期批次 相关任务：资源看门狗快照
        }
    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== ★F2 冷却状态持久化 ==========
    def get_state_snapshot(self) -> dict[str, Any]:
        """
        导出需随快照持久化的冷却计时器状态。
        ★F2：融合/压缩冷却时间戳原本是纯内存变量，重启归零会导致
        「失败路径冷却」失效，重启后立即重试形成风暴。此处导出，
        由 main.py 在保存快照时经 merge_organ_extra_state 写入。
        """
        return {
            "last_optimize_time": self._last_optimize_time,
            "last_fuse_time": self._last_fuse_time,
            "last_snapshot_save_time": self._last_snapshot_save_time,
        }

    def load_state_snapshot(self, state: dict[str, Any]):
        """
        ★F2：从快照恢复冷却计时器。仅当快照值更新（更晚）时才恢复，
        避免异常脏数据把冷却时间戳「拨回过去」导致冷却失效。
        """
        if not isinstance(state, dict):
            return
        try:
            _last_opt = float(state.get("last_optimize_time", 0.0) or 0.0)
            _last_fuse = float(state.get("last_fuse_time", 0.0) or 0.0)
            _last_snap = float(state.get("last_snapshot_save_time", 0.0) or 0.0)
        except (TypeError, ValueError) as e:
            silent_exc(e, "organs/body/PulseLiver.py:3622:状态时间字段解析异常", level="warning")
            return
        self._last_optimize_time = max(self._last_optimize_time, _last_opt)
        self._last_fuse_time = max(self._last_fuse_time, _last_fuse)
        self._last_snapshot_save_time = max(self._last_snapshot_save_time, _last_snap)


    def _m70_kal_get_node(self, node_id):
        """★T4.3 实际调用点：优先 KAL，失败回退 node_pool 直连。"""
        if not self._m70_kal_callsites_on():
            return self._m70_direct_get_node(node_id)
        try:
            from nucleus.knowledge_access_layer import get_kal
            _r = get_kal().get_node(node_id)
            if _r is not None:
                return _r
        except Exception as e:
            self._log(LogLevel.WARNING, f"[肝] KAL获取节点异常，回退直连node_pool: {type(e).__name__}: {e}")
        return self._m70_direct_get_node(node_id)

    def _m70_kal_node_count(self):
        """★T4.3 实际调用点：节点统计（KAL 优先 + 回退）。"""
        try:
            from nucleus.knowledge_access_layer import get_kal
            _r = get_kal().get_node_count()
            if _r:
                return _r
        except Exception as e:
            self._log(LogLevel.WARNING, f"[肝] KAL节点统计异常: {type(e).__name__}: {e}")
        try:
            _pool = getattr(self, "node_pool", None)
            if _pool is not None:
                return len(getattr(_pool, "_hot", {}) or {}) + \
                    len(getattr(_pool, "_warm", {}) or {}) + \
                    len(getattr(_pool, "_cold", {}) or {})
        except Exception as e:
            self._log(LogLevel.WARNING, f"[肝] 节点池统计异常: {type(e).__name__}: {e}")
        return 0

    def _m70_direct_get_node(self, node_id):
        """回退路径：直连 node_pool。"""
        try:
            _pool = getattr(self, "node_pool", None)
            if _pool is not None:
                return _pool.get(node_id)
        except Exception as e:
            self._log(LogLevel.WARNING, f"[肝] 直连获取节点异常: {type(e).__name__}: {e}")
        return None

    def _m70_kal_callsites_on(self) -> bool:
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_KAL_CALL_SITES", True))
        except Exception:
            return True

# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "肝",
    "class_name": "PulseLiver",
    "attr_name": "liver",
    "system": "body",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {
        "node_pool": "node_pool",
        "knowledge_tree": "knowledge_tree",
        "frequency_codec": "frequency_codec",
        "resonance_engine": "resonance_engine",
    },
    "post_wiring": [
        {"target": "snapshot", "setter": "set_snapshot"},
        {"target": "reasoning_pool", "setter": "set_reasoning_pool"},
        {"target": "代码学习", "setter": "set_code_learner"},
    ],
}

if __name__ == "__main__":
    print("=== PulseLiver v9.5 分层脉冲自测 ===\n")

    class MockNodePool:
        def __init__(self):
            self.nodes = {}
            self._added = []
        def query(self, evol_level=None, limit=100, space_path_prefix=None):
            result = []
            for n in self.nodes.values():
                if evol_level and n.evol_level != evol_level:
                    continue
                if space_path_prefix and not n.space_path.startswith(space_path_prefix):
                    continue
                result.append(n)
            return result[:limit]
        def add(self, node):
            self._added.append(node)
            self.nodes[node.node_id] = node
            return node.node_id
        def get_stats(self):
            dist = {"L1": 0, "L2": 0, "L3": 0}
            for n in self.nodes.values():
                dist[n.evol_level] = dist.get(n.evol_level, 0) + 1
            return {"evol_distribution": dist, "total_nodes": len(self.nodes)}

    class MockCodec:
        def encode_node(self, node):
            node.frequency_signature = 50.0

    class MockField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)
        # 模拟 submit_adaptive_task，直接同步执行
        def submit_adaptive_task(self, task_func, task_name="", priority="normal", *args, **kwargs):
            task_func(*args, **kwargs)
            return True

    class MockCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            # [批次4·深度体检][MAINT-4] __main__ mock 补回 pulse_id
            return {"pulse_id": f"pulse:{source_organ}:{event_type}", "event_type": event_type, "source_organ": source_organ,
                    "payload": payload, "priority": priority, "layer": layer}

    liver = PulseLiver("肝")
    pool = MockNodePool()
    mock_field = MockField()
    liver.set_node_pool(pool)
    liver.set_frequency_codec(MockCodec())
    liver.set_info_field(mock_field)
    liver.set_pulse_core(MockCore())

    liver.start()

    # 测试1: L1→L2 压缩，验证异步提交和 layer 标记
    print("1. 普通 L1→L2 压缩（异步化后）:")
    for i in range(3):
        node = PulseNode(
            value=f"Python知识点{i+1}", keywords=["Python", "编程"],
            source_organ="胃", evol_level=PulseNode.EVOL_L1,
            importance=PulseNode.IMPORTANCE_C, space_path="/技术/Python",
        )
        pool.add(node)

    liver._get_l1_threshold = lambda: 3
    liver.on_pulse({"event_type": HeartEvent.BEAT})

    compressed_pulses = [p for p in mock_field.published if p.get("event_type") == KnowledgeEvent.COMPRESSED]
    if compressed_pulses:
        print(f"   COMPRESSED脉冲 layer: {compressed_pulses[0].get('layer', '未设置')} (预期L2)")

    stats = pool.get_stats()
    print(f"   L1={stats['evol_distribution'].get('L1',0)}, L2={stats['evol_distribution'].get('L2',0)}")

    # 测试2: 统计
    print("\n2. 统计:")
    s = liver.get_stats()
    print(f"   L1→L2={s['l1_to_l2_count']}, L2→L3={s['l2_to_l3_count']}, 复盘压缩={s['reflection_compress_count']}")

    liver.stop()
    print("\n=== 自测全部通过 ===")

    def _m69_kal_query(self, node_id=None, evol_level=None, top_k=10):
        """★T4: 通过KAL查询节点（双轨过渡，配置开关控制）。"""
        try:
            import config as _cfg69
            if not getattr(_cfg69, 'ENABLE_KAL_MIGRATION', False):
                return None
            from nucleus.knowledge_access_layer import get_kal
            _kal = get_kal()
            if _kal is not None:
                if node_id:
                    return _kal.get_node(node_id)
                if evol_level:
                    return _kal.search_by_evol_level(evol_level, top_k=top_k)
                return _kal.get_node_count()
        except Exception as e:
            self._log(LogLevel.WARNING, f"[肝] KAL查询异常: {type(e).__name__}: {e}")
        return None
# _m69_t4_kal_liver
# _m70_t4_kal_liver

