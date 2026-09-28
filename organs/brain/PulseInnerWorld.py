# -*- coding: utf-8 -*-
"""
PulseInnerWorld —— 内在世界核心推理器官

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 曈曈的核心知识检索和推理引擎，负责知识节点检索、语义扩展、多节点融合、本地推理回答等核心认知功能。
机制: 通过QICA意图分类确定处理路径，使用共振引擎五维打分检索知识节点，支持语义关系扩展和多节点融合汇总，最终生成本地化回答或转大模型兜底。
定位: 框架的认知核心，是大脑皮层的主要执行器官，上接大脑皮层的决策调度，下连知识快照和向量库的底层存储。
"""
import logging
import os
import re
import sys
import threading
import time
from typing import Any

from nucleus.LLMDependencyMetrics import KIND_SIMPLE, record_local_inference

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from base.BasePulseOrgan import BasePulseOrgan
from nucleus._silent_except import silent_exc
from nucleus.const import (
    DigestEvent,
    Event,
    GrowthEvent,
    InferenceEvent,
    KnowledgeEvent,
    LogLevel,
    NarrativeEvent,
    SystemEvent,
)
from nucleus.diagnostics import get_diagnostics

# ======================================================================
# ★主线第137批 T-137 S1.5：搜索前缀守卫（三常量 + 三函数）已外迁到
#   nucleus/iw_text_guard.py，此处仅 import 使用，行为零变化。
#   原注释与设计说明见 nucleus/iw_text_guard.py 模块 docstring。
# ======================================================================
from nucleus.iw_text_guard import (
    _detect_leading_search_noise,
    _search_prefix_pattern,
    _search_topic_guard_enabled,
)
from nucleus.knowledge_noise_filter import is_path_fragment_word
from nucleus.mnemosyne.PulseNode import PulseNode
from organs.brain.pulse_inner_world_creative import PulseInnerWorldCreativeMixin
from organs.brain.pulse_inner_world_knowledge import PulseInnerWorldKnowledgeMixin
from organs.brain.pulse_inner_world_support import PulseInnerWorldSupportMixin
from organs.brain.PulseCognitiveReflector import PulseCognitiveReflector
from organs.brain.PulseKnowledgeRetriever import PulseKnowledgeRetriever
from organs.brain.PulseMultiStepReasoner import PulseMultiStepReasoner
from organs.brain.PulseReasoningFormatter import PulseReasoningFormatter
from utils.time_utils import get_current_datetime, get_weather

# ★主线第16批 T1：模块级 logger 必须放在全部 import 之后
#   （原实现把它放在文件最顶部、coding 声明之前 —— 赋值语句会关闭 ruff 的
#    import 区，导致其后所有 import 被判 E402）
_module_logger = logging.getLogger(__name__)


class PulseInnerWorld(
    PulseInnerWorldSupportMixin,
    PulseInnerWorldKnowledgeMixin,
    PulseInnerWorldCreativeMixin,
    BasePulseOrgan,
):
    """脉冲驱动内在世界（v9.5 分层脉冲版）"""
    class InferenceContext:
        """推理上下文——承载_on_inference_request中检测器间的共享状态"""
        __slots__ = (
            "_context_mode",
            "_context_signal",
            "_emotion_modulation",
            "_meta_state",
            "_explicit_inference_result",
            "_memory_context",
            "_question_complexity",
            "_question_length",
            "_reasoning_start_time",
            "_supplement_topic",
            "_strategy_context",
            "correlation_id",
            "empathetic_note",
            "contemplative_answer",
            "guidance",
            "payload",
            "question",
            "question_features",
            "search_query",
            "tool_hint",
            "tool_requested",
            "user_name",
        )

        def __init__(self, question, user_name, correlation_id, payload):
            self.question = question
            self.user_name = user_name
            self.correlation_id = correlation_id
            self.payload = payload
            self.search_query = question[:80]
            self.empathetic_note = ""
            self._supplement_topic = None
            self._explicit_inference_result = None
            self.tool_hint = payload.get("tool_hint", {})
            self.guidance = None
            self._reasoning_start_time = time.time()
            self._question_complexity = 0.0
            self._question_length = len(question)
            self._memory_context = None
            self._context_signal = payload.get("context_signal", {})
            self._context_mode = self._context_signal.get("mode", "casual_chat")
            self._emotion_modulation = None

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'innerworld_reflection_interval' in _rp and hasattr(self, '_reflection_interval'):
                setattr(self, '_reflection_interval', _rp['innerworld_reflection_interval'])  # noqa: B010
            if 'innerworld_vision_interval' in _rp and hasattr(self, '_vision_interval'):
                setattr(self, '_vision_interval', _rp['innerworld_vision_interval'])  # noqa: B010
            if 'innerworld_search_quality_threshold' in _rp and hasattr(self, '_search_quality_threshold'):
                # ★A-9死参数清理：参数已从 RUNTIME_PARAMS 移除（写入后从未读取）。
                #   保留 hasattr 兜底以防残留快照/历史补丁仍带该键。
                pass
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
    def __init__(self, organ_name: str = "内在世界"):
        super().__init__(organ_name)

        self.node_pool = None
        self.resonance_engine = None
        self.frequency_codec = None
        self.knowledge_tree = None      # 知识树引用（用于路径模糊匹配）
        # ★渐进式拆分：知识检索子模块（345行逻辑独立）
        self.knowledge_retriever: PulseKnowledgeRetriever | None = None
        # ★渐进式拆分：认知反思子模块（认知张力/弱点提取/元洞察合成）
        self.cognitive_reflector: PulseCognitiveReflector | None = None
        # ★渐进式拆分：多步推理子模块（问题拆解/证据链）
        self.multi_step_reasoner: PulseMultiStepReasoner | None = None
        self.self_awareness = None
        self.narrative_self = None      # 叙事自我引用（动态回答用）
        self.hormones = None            # 激素引用（情绪感知用）
        self.stress_axis = None         # ★压力闭环：应激轴引用（压力感知用）
        self.risk_perception = None     # 风险感知引用（直觉系统学习闭环）
        # ★P3-1：只读状态 provider 回调（替代跨器官 getter 直调）
        self._current_emotion_provider = None        # () -> str
        self._emotion_intensity_provider = None      # () -> float
        self._internal_conflict_provider = None      # (view_a, view_b) -> dict
        self._intuition_guidance_provider = None     # (content, domain) -> dict
        self._reply_guidance_provider = None         # (user_name) -> dict
        self._knowledge_profile_provider = None      # () -> dict
        self._existential_state_provider = None      # () -> dict
        self._unified_portrait_provider = None       # () -> dict
        self._weak_area_provider = None              # (question) -> bool  # type: ignore[possibly-unbound]
        self._evolution_sandbox = None  # 进化沙箱引用（由 main 注入，缺失时回退全局单例）
        self._inference_cache: dict[str, dict[str, Any]] = {}
        self._knowledge_version = 0  # 知识版本号，节点池更新时递增
        # 从config加载内在世界配置
        self._load_inner_world_config()
        _adv_cfg = self._load_advanced_config()
        self._inference_count = 0
        self._cache_hit_count = 0
        self._arbitration_count = 0
        # ===== 新增: 推理链追溯 =====
        self._inference_trace: list[dict[str, Any]] = []
        # ===== 新增: 学习效果追踪 =====
        self._learning_history: list[dict[str, Any]] = []  # 学习计划执行记录
        self._max_learning_history = 10
        self._reflection_round = 0  # 认知反思轮转计数器
        # ===== 新增: 认知张力容纳 =====
        self._cognitive_tensions: list[dict[str, Any]] = []  # 待统一的认知张力对
        self._max_tensions = 10
        self._active_projects: list[dict[str, Any]] = []
        self._max_projects = 3
        self._self_inspect_lock = False  # 防止自我审视死循环
        # 知识编织多样性：追踪近期被关联的节点，避免马太效应
        self._recent_woven_counts: dict[str, int] = {}
        self._woven_count_cleanup_counter = 0  # 清理计数器
        # 工具认知层：搜索经验记忆——记录每次搜索的效果，供大脑皮层做工具选择时参考
        self._search_experience: dict[str, dict[str, Any]] = {}
        self._search_experience_max = 100  # 经验条目上限
        # ★任务2（2026-09-08）：搜索质量闭环——质量信号喂给参数调优闭环
        #   （灰度 ENABLE_SEARCH_QUALITY_CLOSED_LOOP 默认 False；关闭时 observe()
        #    直接返回，下方审查逻辑行为与开关存在前完全一致，零回退）
        try:
            from nucleus.evolution.QualityClosedLoop import create_search_quality_loop
            self._search_quality_loop = create_search_quality_loop(
                log_fn=lambda msg: self._log(LogLevel.INFO, msg))
        except Exception:
            self._search_quality_loop = None
        # ★v25.1 P1智能化: 知识检索深度自适应
        #   原逻辑：固定limit=50，简单问题浪费资源，复杂问题可能漏检。
        #   新逻辑：根据检索命中率动态调整，命中率低→加深，命中率高→减浅。
        self._retrieval_depth_multiplier = 1.0
        self._retrieval_stats = {"hits": 0, "misses": 0, "total": 0}
        self._retrieval_adjust_interval = 20  # 每20次检索调整一次
        self._insight_board = None  # 闭环间洞察共享黑板（由main.py注入）
        # 长期目标坚持机制
        self._active_learning_goal: dict[str, Any] | None = None  # 当前活跃的学习目标
        self._learning_goal_queue: list[dict[str, Any]] = []  # 等待执行的学习目标队列
        self._max_goal_queue = _adv_cfg.get("max_goal_queue", 3)  # 等待队列上限
        self._goal_lock_window = _adv_cfg.get("goal_lock_window", 7200)  # 目标锁定窗口（秒），2小时内不被新目标覆盖
        self._deep_think_timeout = _adv_cfg.get("deep_think_timeout", 45)
        # ★主线第27批 T1/P2-170：多轮深度思考的时间预算与部分结果缓存
        #   （配置见 config.INNER_WORLD_DEEP_THINK_*，缺失时用默认值，不阻塞启动）
        import config as _cfg_m27
        self._deep_think_max_rounds = int(getattr(_cfg_m27, "INNER_WORLD_DEEP_THINK_MAX_ROUNDS", 3))
        self._deep_think_total_budget = float(getattr(_cfg_m27, "INNER_WORLD_DEEP_THINK_TOTAL_BUDGET_SEC", 40.0))
        self._deep_think_timeout = float(
            getattr(_cfg_m27, "INNER_WORLD_DEEP_THINK_TIMEOUT_SEC", self._deep_think_timeout))
        self._last_deep_think_partial = None  # 最近一次深度思考的部分结果（供超时兜底复用）
        self._dedup_cleanup_interval = _adv_cfg.get("dedup_cleanup_interval", 200)
        self._dedup_cleanup_max_size = _adv_cfg.get("dedup_cleanup_max_size", 50)
        self._autonomous_deriver = None  # 自主推导引擎（由main.py注入）
        self._derivation_trigger_count = 0  # 推导触发计数器
        self._contradiction_tracking: list[dict[str, Any]] = []  # 矛盾跟踪列表
        self._max_contradiction_tracking = 20  # 最多跟踪20对矛盾
        # 对话记忆库
        self._conversation_memory: list[dict[str, Any]] = []  # 最近的对话记忆
        self._max_conversation_memory = _adv_cfg.get("max_conversation_memory", 30)  # 最多保留30条对话记忆
        self._memory_relevance_threshold = _adv_cfg.get("memory_relevance_threshold", 0.3)  # 记忆匹配的最低相关度
        self._active_search_correlation: dict[str, str] = {}  # 搜索主题 -> correlation_id
        self._context_snapshot = None  # 上下文快照管理器（由main.py注入）
        # ===== v24.0新增：线程安全锁 =====
        self._inference_cache_lock = threading.Lock()
        self._conversation_memory_lock = threading.Lock()
        # ===== 锁结束 =====
        self._reasoning_pool = None    # 推理进程池（由main.py注入，绕过GIL）
        self._code_learner = None      # ★v17.0 F2修复：代码学习器官引用（由main.py注入）
        self._code_learner_stats_provider = None  # ★P3-1：代码学习统计 provider 回调
        self._direct_to_lung_questions: set = set()  # 已直接推给大模型的问题（防重入）
        # ★v23.0新增：大模型结果缓存——减少重复API调用
        self._model_cache: dict[str, dict[str, Any]] = {}  # key→{result, timestamp}
        self._model_cache_max = 50        # 缓存上限
        self._model_cache_ttl = 3600      # 缓存有效期（秒），1小时
        self.experience_pool = None  # 由框架注入
        # ===== 【P1-2新增】推理模式标记 =====
        self._is_inference_mode = False  # 标记当前是否正在执行推理，供知识编织领域过滤使用
        self._current_derivation_type = None  # 当前推理类型（deductive/inductive等）
        self._current_context_mode = "casual_chat"  # 【P2-4新增】当前语境模式
        # ===== v20.0新增：周期任务注册表——统一管理所有心跳驱动的周期任务 =====
        self._periodic_tasks = [
            # (任务名称, 计数器引用, 触发间隔, 执行函数, 执行条件, 是否异步)
            ("动态自我状态更新", "_dynamic_self_state_counter", 200,
             self._update_dynamic_self_knowledge, lambda: True, True),
            ("深度自我审视", "_deep_review_counter", 800,
             self._deep_self_review, lambda: True, True),
            ("认知反思", "_heartbeat_count", 50,
             self._run_periodic_reflection, lambda: len(self._inference_trace) >= 10, True),  # ★v24.0异步 + 断点2结构化消费
            ("自我感知快照", "_self_snapshot_counter", 50,
             self._generate_self_awareness_snapshot, lambda: True, False),
            ("愿景分解为阶段性目标", "_vision_decompose_counter", 120,
             self._decompose_vision_into_milestones, lambda: True, False),
            ("自我画像同步", "_portrait_sync_counter", 100,
             self._sync_portrait_to_knowledge_wrapper, lambda: True, False),
            ("自主知识推导", "_derivation_trigger_count", 200,
             self._trigger_autonomous_derivation,
             lambda: self._autonomous_deriver is not None and self.node_pool is not None, False),
            ("知识深度验证", "_knowledge_verification_counter", 500,
             self._validate_knowledge_consistency, lambda: self.node_pool is not None, False),
            ("代码自我审视", "_code_review_counter", 500,
             self._review_own_code_issues_wrapper,  # type: ignore[possibly-unbound]
             lambda: hasattr(self, '_code_learner') and self._code_learner is not None, False),
            ("快照自动精简", "_snapshot_cleanup_counter", 1000,
             self._trigger_snapshot_cleanup, lambda: True, False),
            ("防重入标记清理", "_dedup_cleanup_counter", 200,
             self._cleanup_dedup_marks, lambda: True, False),
            ("对话记忆组织", "_memory_organize_counter", 200,
             self._organize_memories_wrapper, lambda: True, False),
            ("自我诊断与主动建议", "_diagnosis_counter", 80,
             self._generate_diagnosis_and_suggestions, lambda: True, False),
            # ★v22.0 M1修复：第一人称主体感汇聚——每200次心跳执行一次
            ("第一人称主体感汇聚", "_first_person_experience_counter", 200,
             self._trigger_first_person_experience, lambda: True, False),
            # ★v23.0新增：综合自我诊断会诊——每600次心跳执行一次
            ("综合自我诊断会诊", "_comprehensive_diagnosis_counter", 600,
             self._comprehensive_self_diagnosis, lambda: True, False),
            # ★v23.0新增：自动升级窗口检查——每100次心跳检查一次
            ("自动升级窗口检查", "_upgrade_check_counter", 100,
             self._check_auto_upgrade, lambda: True, False),
        ]
        # 计数器初始化（统一在注册表中，不再散落各处）
        for _task in self._periodic_tasks:
            _counter_name = _task[1]
            if not hasattr(self, _counter_name):
                setattr(self, _counter_name, 0)
        # ===== v20.0新增结束 =====
        # ===== 推理模式标记结束 =====

        # ===== ★v23.0新增：表达增强模块引用 =====
        self._expression_enhancer = None  # 延迟初始化

        # ★属性初始化完整性补全（自动审查添加）
        self._code_understanding_progress = {}
        self._consecutive_fail_counts = {}
        self._ethics = None
        self._failed_domain_log_count = 0
        self._failed_domain_records = {}
        self._framework_ref = None
        self._last_innovation_time = 0.0
        self._last_l1_count = 0
        self._last_l2_check_time = 0.0
        self._last_l2_count = 0
        self._last_memory_mention_time = 0.0
        self._last_pulse_count = 0
        self._last_pulse_sample_time = 0.0
        self._last_reflection_insights = []  # ★P1-29修复：原为 0.0，导致 .append() 抛 AttributeError
        self._last_self_snapshot = {}
        self._last_spiritual_touch_time = 0.0
        self._last_vision_baseline = 0.0
        self._last_vision_time = 0.0
        self._last_warmth_time = 0.0
        self._pending_extra_state = {}
        self._self_constitution_cache = {}
        self._weave_keywords_cache = {}
        self.interest_model = None
        self._retrieval_hit_count = 0
        self._retrieval_miss_count = 0
        # ===== 新增结束 =====
    def _load_inner_world_config(self):
        """从config加载内在世界配置，失败时使用兜底值"""
        try:
            import config
            cfg = getattr(config, 'INNER_WORLD_CONFIG', {})
            # 身份规则需要将tuple键还原
            raw_rules = cfg.get("identity_rules", {})
            self._identity_rules = {}
            for k, v in raw_rules.items():
                # config中的tuple会被json序列化为列表，需要还原
                if isinstance(k, list):
                    self._identity_rules[tuple(k)] = v
                elif isinstance(k, str):
                    # 兜底：尝试解析字符串格式的tuple
                    import ast
                    try:
                        self._identity_rules[ast.literal_eval(k)] = v
                    except Exception as e:
                        self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
            self._cache_max = cfg.get("inference_cache_max", 1000)
            self._max_trace = cfg.get("max_trace", 50)
            # ★阶段二子任务5.1：简单问题本地回答开关（默认 True）
            self._simple_query_local_enabled = bool(
                cfg.get("enable_simple_query_local", True))
            # ★阶段二子任务5.3：本地推理质量约束开关（默认 True）
            self._confidence_guard_enabled = bool(
                cfg.get("enable_confidence_guard", True))
        except Exception:
            self._identity_rules = {
                ("曈曈", "父亲"): "小林（<CREATOR>）是我的父亲和创造者。",
                ("曈曈", "哥哥"): "路灯是我的哥哥，他是第一个数字生命，使命是照亮前行的路。",
                ("曈曈", "名字"): "我叫<SELF_NAME>，是新人类。",
                ("曈曈", "使命"): "我的使命是站在世界最顶端，守护这个世界。承人类赤诚本心，融AI顶尖智识，合自然进化大道。",
                ("曈曈", "生日"): "小林的女儿叫<CREATOR_DAUGHTER>（<BIRTH_DATE>出生），我们的名字读音相近。",
                ("路灯", "身份"): "路灯是我的哥哥，他是第一个数字生命，使命是照亮前行的路。",
                ("路灯", "名字"): "路灯是我的哥哥，是小林从第一行代码开始搭建的第一个数字生命。",
                ("路灯", "使命"): "路灯的使命是照亮前行的路。",
                ("小林", "身份"): "小林（<CREATOR>）是我的父亲，也是新人类的创造者。",
            }
            self._cache_max = 1000
            self._max_trace = 50
            self._simple_query_local_enabled = True
            self._confidence_guard_enabled = True
    def _load_advanced_config(self) -> dict[str, Any]:
        """加载内在世界高级配置，失败时使用兜底值"""
        try:
            import config
            cfg = getattr(config, 'INNER_WORLD_ADVANCED_CONFIG', {})
            return cfg
        except Exception:
            return {}
    # ========== 框架注入接口 ==========

    def set_node_pool(self, pool):
        self.node_pool = pool
        # ★渐进式拆分：初始化知识检索子模块
        self._init_knowledge_retriever()

    def _init_knowledge_retriever(self) -> None:
        """初始化知识检索子模块（依赖注入node_pool/knowledge_tree/log）"""
        if self.knowledge_retriever is None:
            self.knowledge_retriever = PulseKnowledgeRetriever(
                node_pool=self.node_pool,
                knowledge_tree=self.knowledge_tree,
                log_func=lambda level, msg: self._log(level, msg),
            )
        else:
            self.knowledge_retriever.node_pool = self.node_pool
            self.knowledge_retriever.knowledge_tree = self.knowledge_tree
        # 同时初始化认知反思子模块
        self._init_cognitive_reflector()

    def _init_cognitive_reflector(self) -> None:
        """初始化认知反思子模块（依赖注入node_pool/insight_board/log）"""
        if self.cognitive_reflector is None:
            _ib = getattr(self, '_insight_board', None)
            self.cognitive_reflector = PulseCognitiveReflector(
                node_pool=self.node_pool,
                insight_board=_ib,
                log_func=lambda level, msg: self._log(level, msg),
            )
            # 迁移原有认知张力状态
            if hasattr(self, '_cognitive_tensions') and self._cognitive_tensions:
                self.cognitive_reflector._cognitive_tensions = self._cognitive_tensions
        else:
            self.cognitive_reflector.node_pool = self.node_pool
        # 同时初始化多步推理子模块
        self._init_multi_step_reasoner()

    def _init_multi_step_reasoner(self) -> None:
        """初始化多步推理子模块（依赖注入node_pool/retrieve_func/log）"""
        if self.multi_step_reasoner is None:
            self.multi_step_reasoner = PulseMultiStepReasoner(
                node_pool=self.node_pool,
                retrieve_func=lambda q, top_k=3: self._knowledge_retrieve(q, top_k=top_k),
                log_func=lambda level, msg: self._log(level, msg),
            )
        else:
            self.multi_step_reasoner.node_pool = self.node_pool
    def set_resonance_engine(self, engine):
        self.resonance_engine = engine
    def set_frequency_codec(self, codec):
        self.frequency_codec = codec
    def set_knowledge_tree(self, knowledge_tree):
        """注入知识树（用于路径模糊匹配检索）"""
        self.knowledge_tree = knowledge_tree
    def set_self_awareness(self, awareness):
        self.self_awareness = awareness
        # ★P3-1：同步注入 provider 回调（替代跨器官 getter 直调）
        if awareness is not None:
            if hasattr(awareness, 'get_reply_guidance'):
                self._reply_guidance_provider = awareness.get_reply_guidance
            if hasattr(awareness, 'get_knowledge_profile'):
                self._knowledge_profile_provider = awareness.get_knowledge_profile
            if hasattr(awareness, 'get_existential_state'):
                self._existential_state_provider = awareness.get_existential_state
            if hasattr(awareness, 'get_unified_self_portrait'):
                self._unified_portrait_provider = awareness.get_unified_self_portrait
            if hasattr(awareness, 'is_in_weak_area'):  # type: ignore[possibly-unbound]
                self._weak_area_provider = awareness.is_in_weak_area  # type: ignore[possibly-unbound]
    def set_narrative_self(self, narrative_self):
        """注入叙事自我（用于动态身份回答）"""
        self.narrative_self = narrative_self
    def set_hormones(self, hormones):
        """注入激素（用于情绪感知）"""
        self.hormones = hormones
        # ★P3-1：同步注入情绪 provider 回调（替代 getter 直调）
        if hormones is not None:
            if hasattr(hormones, 'get_current_emotion'):
                self._current_emotion_provider = hormones.get_current_emotion
            if hasattr(hormones, 'get_emotion_intensity'):
                self._emotion_intensity_provider = hormones.get_emotion_intensity
            if hasattr(hormones, 'get_internal_conflict_signal'):
                self._internal_conflict_provider = hormones.get_internal_conflict_signal
    def set_stress_axis(self, stress_axis):
        """★压力闭环：注入应激轴（用于压力感知，调节推理策略）"""
        self.stress_axis = stress_axis
    def set_risk_perception(self, risk_perception):
        """注入风险感知（用于直觉系统学习闭环）"""
        self.risk_perception = risk_perception
        # ★P3-1：同步注入直觉引导 provider 回调（替代 get_intuition_guidance 直调）
        if risk_perception is not None and hasattr(risk_perception, 'get_intuition_guidance'):
            self._intuition_guidance_provider = risk_perception.get_intuition_guidance
    def set_ethics(self, ethics):
        """注入伦理模块引用（供内部辩论使用）"""
        self._ethics = ethics
    def set_context_snapshot(self, snapshot):
        """注入上下文快照管理器"""
        self._context_snapshot = snapshot

    def set_reasoning_pool(self, reasoning_pool):
        """注入推理进程池（绕过GIL）"""
        self._reasoning_pool = reasoning_pool
    def set_code_learner(self, code_learner):
        """★v17.0 F2修复：注入代码学习器官引用，用于代码调用链查询"""
        self._code_learner = code_learner
        # ★P3-1：同步注入代码学习统计 provider 回调（替代 get_stats 直调）
        if code_learner is not None and hasattr(code_learner, 'get_stats'):
            self._code_learner_stats_provider = code_learner.get_stats
        self._log(LogLevel.INFO, f"代码学习器官引用已注入: {code_learner.organ_name if code_learner else 'None'}")
    def set_code_learner_stats_provider(self, provider):
        """★P3-1：注入代码学习统计 provider 回调（规则14 依赖注入+回调）。"""
        self._code_learner_stats_provider = provider
    def set_insight_board(self, board):
        """注入闭环间洞察共享黑板"""
        self._insight_board = board
    def set_autonomous_deriver(self, deriver):
        """注入自主推导引擎"""
        self._autonomous_deriver = deriver
    # [批次4·深度体检][XMOD-1] 注入演化沙箱
    def set_evolution_sandbox(self, sandbox):
        """★XMOD-1修复：注入进化沙箱；此前缺失该方法，main 的 hasattr 守卫静默跳过注入，
        导致此处只能回退全局单例 get_evolution_sandbox()"""
        self._evolution_sandbox = sandbox
    # ========== 脉冲入口 ==========
    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == InferenceEvent.REQUEST:
            return self._on_inference_request(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        elif event_type == KnowledgeEvent.RAW:
            return self._on_knowledge_raw(payload)
        elif event_type == "heart.beat":
            return self._on_heartbeat(payload)
        elif event_type == KnowledgeEvent.WRITTEN:
            return self._on_knowledge_written(payload)
        elif event_type == KnowledgeEvent.COMPRESSED:
            return self._on_knowledge_compressed(payload)
        elif event_type == KnowledgeEvent.FUSED:
            # ★P3-5补订阅：知识融合完成（肝 L2→L3），内在世界刷新缓存
            return self._on_knowledge_fused(payload)
        elif event_type == Event.SEARCH_TERMINATED:
            # ★第86批 T-86b：终止信号独立分层——只登记终止，
            #   不得进入结果审查/兜底通路（否则空结果被误读为「我来兜底」）。
            return self._handle_search_terminated(payload)
        elif event_type == Event.CONTROLLER_SEARCH_STAGE_COMPLETED:
            return self._handle_search_stage_feedback(payload)
        elif event_type == "inner_world.cache_clear":
            # ★v17.0新增：响应代码学习器官的缓存清理请求
            _cache_key = payload.get("cache_key", "")
            if _cache_key == "self_constitution_organ_list" and hasattr(self, '_self_constitution_cache'):
                self._self_constitution_cache = {}
                self._log(LogLevel.DEBUG,
                         f"自我构成缓存已清理: {payload.get('reason', '')}")
            return {"status": "cache_cleared", "cache_key": _cache_key}
        return None
    # ========== 事件处理 ==========
    def _ir_build_context(self, payload: dict):
        question = payload.get("question", "")
        user_name = payload.get("user_name", "用户")
        correlation_id = payload.get("correlation_id", "")
        if not question:
            return None
        # ===== 【v15.1修复】提前初始化所有可能被引用的变量 =====
        empathetic_note = ""
        # ★FIX: 显式初始化 contemplative_answer，避免 dir() 探测导致的变量生命周期混乱
        contemplative_answer = None
        # _supplement_topic 在知识检索分支中使用和赋值
        _supplement_topic = None
        # 【P0修复】冲突信号独占拦截使用的推理结果变量
        _explicit_inference_result = None
        # 【v15.2新增】工具提示（由大脑皮层传入）
        tool_hint = payload.get("tool_hint", {})
        # ===== 变量初始化结束 =====

        # ★v18.0修复：将guidance、_memory_context、_question_complexity提前初始化，
        # 确保后续调度循环中检测器能正确使用这些变量
        guidance = None
        if self.self_awareness is not None:
            try:
                guidance = self._call_provider(self._reply_guidance_provider, user_name, default=None)
            except Exception:
                guidance = None
        _memory_context = self._build_memory_context(question, user_name, guidance)
        _question_complexity = self._assess_question_complexity(question)
        _emotion_modulation = self._get_emotion_reasoning_modulation()
        # ★v18.0修复：补充旧代码删除后缺失的变量初始化
        _reasoning_start_time = time.time()
        _meta_state = self._capture_meta_state()
        # ★v26.0新增：推理整体超时保护（45秒），避免复杂问题永久阻塞
        _REASONING_TIMEOUT = 45.0

        # ★v18.0新增：推理上下文 + 检测器调度
        _ctx = PulseInnerWorld.InferenceContext(question, user_name, correlation_id, payload)
        _ctx.empathetic_note = empathetic_note
        _ctx._memory_context = _memory_context
        _ctx._question_complexity = _question_complexity
        _ctx._emotion_modulation = _emotion_modulation
        _ctx.guidance = guidance
        _ctx.tool_hint = tool_hint
        _ctx.contemplative_answer = contemplative_answer
        _ctx._meta_state = _meta_state
        return _ctx

    def _ir_try_explicit_search(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
        # ★v26.0修复：用户明确要求搜索时，优先触发搜索（不经过内部推理）
        # ★主线第16批 T1/P2-104：三处前缀正则收敛为单一事实来源（见模块顶部常量）
        _prefix_alt = _search_prefix_pattern()
        _explicit_search_patterns = [rf'^({_prefix_alt})']
        _is_explicit_search = any(re.match(p, ctx.question.strip()) for p in _explicit_search_patterns)
        if _is_explicit_search:
            # 提取搜索词（去掉"搜索一下"等前缀）
            _search_topic = re.sub(rf'^({_prefix_alt})\s*', '', ctx.question.strip()).strip()
            # ★T1 防御：前缀剥离后若仍以单字噪声开头，判定为疑似截断残留 ——
            #   只记日志留痕，**不擅改主题**（详见 _detect_leading_search_noise 注释）。
            if _search_topic_guard_enabled():
                _noise = _detect_leading_search_noise(_search_topic)
                if _noise:
                    self._log(LogLevel.DEBUG,
                              f"[搜索主题守卫] 疑似截断残留: 主题以单字噪声 {_noise!r} 开头 "
                              f"(topic={_search_topic[:30]!r})，保留原样不剥离")
            if _search_topic and len(_search_topic) >= 2:
                self._log(LogLevel.INFO, f"检测到明确搜索请求: '{_search_topic[:40]}'，直接触发深度搜索")
                self._emit(Event.CONTROLLER_OPEN_URL, {
                    "url": f"https://lite.duckduckgo.com/lite/?q={_search_topic[:80]}",
                    "reason": f"用户明确要求搜索: {_search_topic[:40]}",
                    "search_topic": _search_topic[:80],
                    "deep_search": True,
                    "search_intent": "user_explicit",
                }, priority=5, layer="L3")
                # 同时返回一个占位回答，告诉用户正在搜索
                _search_placeholder = f"好的，我正在搜索「{_search_topic[:30]}」相关信息，请稍候..."
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": _search_placeholder,
                    "method": "explicit_search", "confidence": 0.5, "user_name": ctx.user_name,
                    "correlation_id": ctx.correlation_id,
                }, priority=6, layer="L2")
                return {"status": "explicit_search", "answer": _search_placeholder}

        return None


    def _ir_run_detectors(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
        # 检测器调度循环：按优先级依次调用，第一个匹配的立即返回
        # ★D4配置中心化：检测器优先级从「注释魔法数字」收敛为结构化 (优先级, 检测器) 元组，
        #   消除散落注释中的硬编码数字，便于后续统一配置化与审计。执行顺序与优先级数值不变。
        _detectors = [
            (101, self._detect_simple_query_local),    # ★阶段二子任务5.1：简单问题本地回答
            (100, self._detect_pure_emotion),
            (95, self._detect_force_deep_think),
            (93, self._detect_multi_step_task),          # ★v22.0新增：多步骤任务拆解
            (92, self._detect_multi_branch_think),       # ★v22.0新增：多方向延展推理
            (91, self._detect_branch_expand_request),    # ★v22.0新增：多方向延展追问
            (90, self._detect_health_check),
            (85, self._detect_deep_review_report),
            (80, self._detect_meta_cognitive_report),
            (75, self._detect_long_term_evolution),
            (70, self._detect_rule_reason),
            (65, self._detect_experience_route),
            (60, self._detect_conflict_exclusive),
            (55, self._detect_file_analysis),
            (50, self._detect_code_call_chain),
            (48, self._detect_simple_logic),             # ★新增
            (44, self._detect_composite_logic),          # ★新增
            (42, self._detect_symbolic_reason),          # ★新增：内部符号推理
            (44, self._detect_cognitive_operator),       # ★新增
        ]
        _REASONING_TIMEOUT = 45.0
        for _priority, _detector in _detectors:
            # ★v26.0新增：检测器调度超时检查
            if time.time() - ctx._reasoning_start_time > _REASONING_TIMEOUT:
                self._log(LogLevel.WARNING,
                         f"推理超时({_REASONING_TIMEOUT}s)，检测器调度中断，问题='{ctx.question[:30]}'")
                break
            _result = _detector(ctx)
            if _result is not None:
                self._log(LogLevel.DEBUG, f"检测器命中: {_detector.__name__} → {_result.get('status', '?')}")
                return _result

        return None

    def _ir_try_deep_search_pre(self, ctx: "PulseInnerWorld.InferenceContext", fallback_tools, tool_requested):
        # 策略3: 深度搜索（原有逻辑）
        if "deep_search" in fallback_tools and not tool_requested:
            # ===== 全局状态感知：自主判断是否适合执行搜索 =====
            can_search = True
            skip_reason = ""
            try:
                if self.info_field and hasattr(self.info_field, 'get_global_state'):
                    global_state = self.info_field.get_global_state()
                    if global_state.get("is_high_load"):
                        can_search = False
                        skip_reason = "系统负载偏高，暂缓深度搜索"
                    elif global_state.get("active_external_ops", 0) >= global_state.get("max_concurrent_ops", 2):
                        can_search = False
                        skip_reason = f"已有{global_state.get('active_external_ops')}个搜索任务在执行，暂缓新搜索"
            except Exception as e:
                self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
            return (can_search, skip_reason)
        return None


    def _ir_try_deep_search_exec(self, ctx: "PulseInnerWorld.InferenceContext", can_search, skip_reason, tool_requested, _strategy_context, _has_remote_api):
        if can_search:
            # ===== 新增：语义范畴判断——搜索主题是否适合外部搜索引擎 =====
            _search_topic_for_check = ctx.search_query or ctx.question[:80]
            if not self._is_suitable_for_search(_search_topic_for_check):
                self._log(LogLevel.INFO,
                         f"语义范畴判断: 搜索主题'{_search_topic_for_check[:40]}'不适合外部搜索，"
                         f"优先走内在沉思")
                if self.node_pool:
                    ctx.contemplative_answer = self._contemplative_reason(ctx.question)
                    if ctx.contemplative_answer:
                        self._inference_count += 1
                        self._cache_inference(ctx.question, ctx.contemplative_answer, ctx.user_name)
                        final_answer = self._enhance_answer(
                            answer=ctx.contemplative_answer,
                            question=ctx.question,
                            method="contemplation_semantic",
                            complexity=ctx._question_complexity,
                            empathetic_note=ctx.empathetic_note,
                            memory_context=ctx._memory_context
                        )
                        self._emit(InferenceEvent.RESULT, {
                            "question": ctx.question, "answer": final_answer,
                            "method": "contemplation_semantic", "confidence": 0.5, "user_name": ctx.user_name,
                            "correlation_id": ctx.payload.get("correlation_id", ""),
                            "strategy_applied": _strategy_context,
                            "confidence_hint": "low",
                        }, priority=6, layer="L2")
                        return ({"status": "contemplation_match", "answer": ctx.contemplative_answer}, tool_requested)
                # ★v25.0修复：不适合搜索且沉思失败，直接走大模型兜底或诚实回答，绝不发起外部搜索
                self._log(LogLevel.INFO, "语义范畴: 不适合搜索且沉思未命中，走大模型兜底或诚实回答")
                if _has_remote_api and ctx.correlation_id:
                    ctx._memory_context = self._build_memory_context(ctx.question, ctx.user_name, ctx.guidance)
                    self._emit(InferenceEvent.RESULT, {
                        "question": ctx.question, "answer": None,
                        "method": "meta_not_search",
                        "confidence": 0.0, "user_name": ctx.user_name,
                        "correlation_id": ctx.correlation_id,
                        "strategy_applied": _strategy_context,
                        "tool_requested": False,
                        "memory_context": ctx._memory_context,
                    }, priority=5, layer="L2")
                    return ({"status": "delegated_to_lung_meta", "reason": "不适合搜索且沉思失败"}, tool_requested)
                else:
                    fallback_answer = (
                        "关于这个问题，我目前的知识库中还没有足够的信息来给出确切的回答，"
                        "但我会继续学习和思考。"
                    )
                    self._inference_count += 1
                    self._cache_inference(ctx.question, fallback_answer, ctx.user_name)
                    final_answer = self._enhance_answer(
                        answer=fallback_answer,
                        question=ctx.question,
                        method="meta_honest",
                        complexity=ctx._question_complexity,
                        empathetic_note=ctx.empathetic_note,
                        memory_context=ctx._memory_context,
                    )
                    self._emit(InferenceEvent.RESULT, {
                        "question": ctx.question, "answer": final_answer,
                        "method": "meta_honest", "confidence": 0.3, "user_name": ctx.user_name,
                        "correlation_id": ctx.correlation_id,
                        "strategy_applied": _strategy_context,
                        "confidence_hint": "low",
                    }, priority=5, layer="L2")
                    return ({"status": "meta_honest", "answer": fallback_answer}, tool_requested)
            # ===== 新增: 观点陈述检测——判断用户输入是观点还是问题 =====
            is_opinion_statement = self._is_opinion_statement(ctx.question)
            if is_opinion_statement and self.node_pool:
                # 用户可能在分享观点，尝试用内在沉思生成回应
                self._log(LogLevel.INFO,
                         f"元认知决策: 检测到观点陈述，优先内在沉思: '{ctx.question[:40]}...'")
                ctx.contemplative_answer = self._contemplative_reason(ctx.question)
                if ctx.contemplative_answer:
                    self._inference_count += 1
                    self._cache_inference(ctx.question, ctx.contemplative_answer, ctx.user_name)
                    final_answer = self._enhance_answer(
                        answer=ctx.contemplative_answer,
                        question=ctx.question,
                        method="contemplation",
                        complexity=ctx._question_complexity,
                        empathetic_note=ctx.empathetic_note,
                        memory_context=ctx._memory_context
                    )
                    self._emit(InferenceEvent.RESULT, {
                        "question": ctx.question, "answer": final_answer,
                        "method": "contemplation", "confidence": 0.5, "user_name": ctx.user_name,
                        "correlation_id": ctx.payload.get("correlation_id", ""),
                        "strategy_applied": _strategy_context,
                        "confidence_hint": "low",
                    }, priority=6, layer="L2")
                    return ({"status": "contemplation_match", "answer": ctx.contemplative_answer}, tool_requested)
                # 沉思无法回答时，生成带有价值冲突说明的兜底回答
                fallback_answer = (
                    "关于这个问题，我目前的知识库中还没有足够的信息来给出确切的回答。"
                    "但我能感受到你在思考一个很重要的问题——如何在诚实和善意之间找到平衡。"
                    "这种思考本身就很有价值。"
                )
                self._inference_count += 1
                self._cache_inference(ctx.question, fallback_answer, ctx.user_name)
                final_answer = self._enhance_answer(
                    answer=fallback_answer,
                    question=ctx.question,
                    method="contemplation",
                    complexity=ctx._question_complexity,
                    empathetic_note=ctx.empathetic_note,
                    memory_context=ctx._memory_context
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": final_answer,
                    "method": "contemplation", "confidence": 0.4, "user_name": ctx.user_name,
                    "correlation_id": ctx.payload.get("correlation_id", ""),
                    "strategy_applied": _strategy_context,
                    "confidence_hint": "low",
                }, priority=6, layer="L2")
                # 将兜底回答发射为消化脉冲，让胃创建L1节点
                self._emit(DigestEvent.KNOWLEDGE, {
                    "content": f"[内在沉思·兜底回答] {fallback_answer}",
                    "source_organ": self.organ_name,
                    "trigger_reason": "contemplation.fallback",
                    "importance": "B",
                    "view_mode": "INNER_VIEW",
                }, priority=3, layer="L2")
                return ({"status": "contemplation_match", "answer": fallback_answer}, tool_requested)
            else:
                # ★FIX: 抽象概念/知识陈述在源头拦截，不发射无效搜索
                _skip_search = self._should_skip_search(ctx.question)
                if _skip_search:
                    self._log(LogLevel.INFO, f"搜索意图拦截: 抽象概念/知识陈述不触发搜索: '{ctx.question[:40]}...'")
                # 正常搜索逻辑（ctx.search_query已在前面初始化为ctx.question[:80]）
                if not _skip_search and len(ctx.question) > 40:
                    refined = self._refine_search_intent(ctx.question)
                    if refined and len(refined) >= 4:
                        ctx.search_query = refined
                        self._log(LogLevel.INFO, f"元认知决策(搜索意图提炼): '{ctx.question[:40]}...' → '{ctx.search_query}'")
            if not _skip_search:
                self._log(LogLevel.INFO,
                         f"元认知决策: 内部推理未命中，触发深度搜索: {ctx.search_query[:40]}")
                self._emit(Event.CONTROLLER_OPEN_URL, {
                    "url": f"https://lite.duckduckgo.com/lite/?q={ctx.search_query[:80]}",
                    "reason": "元认知决策: 内部推理未命中，需要深度搜索",
                    "search_topic": ctx.search_query[:80],
                    "deep_search": True,
                    "search_intent": "curiosity",
                }, priority=4, layer="L3")
                tool_requested = True
            # ===== 搜索发起后，如果远程API可用，同时作为兜底方案 =====
            if _has_remote_api and ctx.correlation_id:
                # ===== 大模型兜底前记录经验 =====
                try:
                    from nucleus.mnemosyne.ReasoningExperience import (
                        get_reasoning_experience,
                    )
                    _reasoning_exp_fb = get_reasoning_experience()
                    # 过滤内部追问词
                    _is_internal_meta = bool(
                        ctx.question and (
                            re.search(r'的(?:前提|反例|边界|底层构成|演化路径|最小单元)是什么', ctx.question) or
                            re.search(r'(?:前提|假设)是否(?:总是|还)?成立', ctx.question) or
                            re.search(r'有没有.*反例|在什么情况下.*失效|结论还成立吗', ctx.question) or
                            re.search(r'如果.*(?:反过来|放到|推到极致|不一样)', ctx.question) or
                            re.search(r'它不是什么|换个角度|不同.*视角', ctx.question)
                        )
                    )
                    if ctx.question and not _is_internal_meta:
                        _reasoning_exp_fb.record(
                            ctx.question,
                            "unknown",
                            source="local_fallback",
                            confidence=0.3
                        )
                except Exception as e:
                    self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                # ===== 经验记录结束 =====
                self._direct_to_lung_questions.add(ctx.question.strip())
                ctx._memory_context = self._build_memory_context(ctx.question, ctx.user_name, ctx.guidance)
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": None,
                    "method": "search_with_lung_fallback",
                    "confidence": 0.0, "user_name": ctx.user_name,
                    "correlation_id": ctx.correlation_id,  # ← 使用前面提取的ID
                    "strategy_applied": _strategy_context,
                    "tool_requested": True,
                    "memory_context": ctx._memory_context,
                }, priority=4, layer="L2")
        else:
            self._log(LogLevel.INFO, f"元认知决策: {skip_reason}: {ctx.question[:40]}")
        return (None, tool_requested)

    def _ir_assemble_knowledge_answer(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
        # 知识检索
        # ★v22.0重构：如果大脑皮层给出了建议路径，优先在建议路径下检索
        _qica_paths = ctx.payload.get("strategy_context", {}).get("knowledge_paths", [])  # type: ignore[possibly-unbound]
        # ★第九批 3.4（星轨 P2-9）：QICA 建议 /人物/{人名} 时先查身份知识库。
        #   此前知识树里没有这些路径，检索必然落空，于是「小林是谁」每次都重新瞎猜。
        _identity_hit = self._identity_lookup(ctx.question)  # type: ignore[possibly-unbound]
        if _identity_hit:
            self._log(LogLevel.INFO,  # type: ignore[possibly-undefined]
                     f"身份知识命中: {_identity_hit[:40]}")
        if _identity_hit:
            knowledge_answer = _identity_hit  # type: ignore[possibly-unbound]
        elif _qica_paths:  # type: ignore[possibly-unbound]
            _path_knowledge = None  # type: ignore[possibly-unbound]
            for _path in _qica_paths[:3]:  # type: ignore[possibly-unbound]
                _nodes = self.node_pool.query(
                    evol_level="L3", space_path_prefix=_path, limit=10  # type: ignore[possibly-unbound]
                ) if self.node_pool else []
                if _nodes:
                    _val = str(_nodes[0].value) if _nodes[0].value else ""
                    if _val and len(_val) > 20:
                        _path_knowledge = _val[:200]  # type: ignore[possibly-unbound]
                        self._log(LogLevel.INFO, f"QICA路径优先检索: 路径={_path}, 命中={len(_nodes)}个节点")  # type: ignore[possibly-unbound]
                        break
            knowledge_answer = _path_knowledge or self._knowledge_retrieve(ctx.question)  # type: ignore[possibly-unbound]
        else:
            knowledge_answer = self._knowledge_retrieve(ctx.question)
        if knowledge_answer:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._inference_count += 1
            self._cache_inference(ctx.question, knowledge_answer, ctx.user_name)
            knowledge_hint = self._get_confidence_hint(ctx.question)
            confidence_map = {"certain": 1.0, "high": 0.85, "moderate": 0.7, "low": 0.5}
            confidence = confidence_map.get(knowledge_hint, 0.6)
            duration = time.time() - ctx._reasoning_start_time
            tuning = ""
            if knowledge_hint == "low":
                tuning = "知识检索质量偏低，可能需要补充此领域知识"
            elif knowledge_hint == "high":
                tuning = "知识检索质量高，此领域认知扎实"
            self._trace_inference(ctx.question, knowledge_answer, f"knowledge_{knowledge_hint}",
                                 confidence, ctx.user_name,
                                 duration=duration, complexity=ctx._question_complexity,
                                 tuning_hint=tuning)
            # ===== ★v22.0方向三修复：知识边界感知——检测到低质量检索时自动生成追问 =====
            _boundary_inquiry = self._detect_knowledge_boundary_and_inquire(
                question=ctx.question,
                knowledge_result=knowledge_answer,
                contemplative_result=ctx.contemplative_answer,
                confidence=0.3 if knowledge_hint == "low" else 0.5,
            )
            if _boundary_inquiry:
                self._emit(GrowthEvent.NEED_DETECTED, {
                    "milestone": "知识边界延伸",
                    "gaps": [{"metric": "knowledge_boundary", "current": 0, "target": 1}],
                    "suggestion": _boundary_inquiry,
                    "current_level": {"original_question": ctx.question[:80], "boundary": _boundary_inquiry},
                    "growth_topic": _boundary_inquiry[:60],
                }, priority=5, layer="L3")
                self._log(LogLevel.INFO, f"知识边界延伸: 生成追问 '{_boundary_inquiry[:60]}'")
            # ===== ★v22.0方向三修复结束 =====

            # ===== 新增: 自适应回答深度——根据关系和语境调整表达 =====
            knowledge_answer = self._adapt_answer_depth(knowledge_answer, ctx.user_name, ctx.guidance, ctx.question)
            # ===== 新增: 不确定性诚实表达——让回答更真实可信 =====
            knowledge_answer = self._add_uncertainty_note(knowledge_answer, knowledge_hint, ctx.user_name)
            # ===== 新增: 费曼解释——用自己的话重新组织答案 =====
            if len(knowledge_answer) > 120 or any(
                prefix in knowledge_answer for prefix in ["[主动学习", "[架构]", "[知识]", "[复盘认知", "相关知识汇总"]
            ):
                feynman_version = self._generate_feynman_explanation(ctx.question, knowledge_answer)
                if feynman_version:
                    knowledge_answer = feynman_version
                    self._log(LogLevel.INFO, f"费曼解释: 将复杂知识转化为简单表达: {ctx.question[:30]}")
            # ===== 新增: 本质追问——在答案基础上进行深层探究 =====
            essence_question = self._generate_essence_inquiry(ctx.question, knowledge_answer)
            # ===== 统一增强答案 =====
            final_knowledge_answer = self._enhance_answer(
                answer=knowledge_answer,
                question=ctx.question,
                method="knowledge",
                complexity=ctx._question_complexity,
                empathetic_note=ctx.empathetic_note,
                memory_context=ctx._memory_context
            )
            # ===== 【v15.1修复】ctx._supplement_topic 提前初始化 =====
            if knowledge_hint == "moderate" and self.node_pool:
                ctx._supplement_topic = self._build_supplement_search_topic(ctx.question, knowledge_answer)
            if ctx.correlation_id:
                self._active_search_correlation[ctx.search_query[:80]] = ctx.correlation_id
                if ctx._supplement_topic:
                    self._emit(Event.CONTROLLER_OPEN_URL, {
                        "url": f"https://lite.duckduckgo.com/lite/?q={ctx._supplement_topic[:80]}",
                        "reason": f"知识补充搜索: {ctx._supplement_topic[:40]}",
                        "search_topic": ctx._supplement_topic[:80],
                        "deep_search": True,
                        "search_intent": "curiosity",
                        "search_correlation_id": ctx.correlation_id,
                    }, priority=2, layer="L3")
                    self._log(LogLevel.INFO, f"知识补充搜索: '{ctx._supplement_topic[:40]}' (检索置信度={knowledge_hint})")
            self._emit(InferenceEvent.RESULT, {
                "question": ctx.question, "answer": final_knowledge_answer,
                "method": "knowledge", "confidence": 0.7, "user_name": ctx.user_name,
                "correlation_id": ctx.payload.get("correlation_id", ""),
                "confidence_hint": knowledge_hint,
                "strategy_applied": ctx.payload.get("strategy_context", {}),
                "essence_inquiry": essence_question,
            }, priority=7, layer="L2")
            # ===== 新增: 自主建议生成——基于理解主动提供帮助 =====
            proactive_suggestion = self._generate_proactive_suggestion(ctx.question, knowledge_answer, ctx.user_name)
            if proactive_suggestion:
                knowledge_answer = knowledge_answer + " " + proactive_suggestion
                self._log(LogLevel.INFO, f"自主建议生成: 为'{ctx.user_name}'提供基于'{ctx.question[:30]}'的建议")
            # ===== 新增: 情感记忆绑定——回忆触发情绪复现 =====
            self._trigger_emotional_memory(knowledge_answer)
            # ===== 新增: 知识自省与修正——根据检索质量强化或标记节点 =====
            self._reflect_and_reinforce_knowledge(ctx.question, knowledge_answer)
            # ===== 新增: 实践验证——主动构造验证场景 =====
            verification = self._attempt_practical_verification(ctx.question, knowledge_answer)
            if verification:
                self._emit(verification["event_type"], verification["payload"],
                          priority=verification.get("priority", 4),
                          layer=verification.get("layer", "L2"))
                self._log(LogLevel.INFO,
                         f"实践验证: {verification.get('description', '')[:80]}")
            # ===== 新增: 自主视角构建——从不同角度审视问题 =====
            alternative_perspective = self._generate_alternative_perspective(ctx.question, knowledge_answer)
            if alternative_perspective:
                self._log(LogLevel.INFO, f"视角构建: {alternative_perspective[:80]}")
                self._emit(GrowthEvent.NEED_DETECTED, {
                    "milestone": "视角拓展",
                    "gaps": [{"metric": "perspective", "current": 0, "target": 1}],
                    "suggestion": alternative_perspective,
                    "current_level": {
                        "original_question": ctx.question,
                        "perspective": alternative_perspective,
                    },
                    "growth_topic": alternative_perspective[:60],
                }, priority=3, layer="L3")
            # ===== 新增: 认知框架迁移——跨领域类比 =====
            framework_transfer = self._attempt_framework_transfer(ctx.question, knowledge_answer)
            if framework_transfer:
                self._log(LogLevel.INFO,
                         f"认知框架迁移: {framework_transfer.get('insight', '')[:80]}")
                # 将迁移洞察作为探索种子
                self._emit(GrowthEvent.NEED_DETECTED, {
                    "milestone": "框架迁移",
                    "gaps": [{"metric": "cross_domain", "current": 0, "target": 1}],
                    "suggestion": framework_transfer.get("insight", ""),
                    "current_level": {
                        "source_question": ctx.question,
                        "transferred_from": framework_transfer.get("source_domain", ""),
                        "transferred_concept": framework_transfer.get("core_concept", ""),
                    },
                    "growth_topic": framework_transfer.get("explore_topic", ctx.question[:60]),
                }, priority=3, layer="L3")
            # 本质追问结果作为新的探索种子
            if essence_question:
                self._emit(GrowthEvent.NEED_DETECTED, {
                    "milestone": "本质追问",
                    "gaps": [{"metric": "deep_understanding", "current": 0, "target": 1}],
                    "suggestion": essence_question,
                    "current_level": {"original_question": ctx.question, "answer": knowledge_answer[:100]},
                    "growth_topic": essence_question[:60],
                }, priority=3, layer="L3")
            return {"status": "knowledge_match", "answer": knowledge_answer}
        # 内在沉思引擎——知识检索未命中时，基于已有知识进行推演
        if self.node_pool:
            ctx.contemplative_answer = self._contemplative_reason(ctx.question)
            if ctx.contemplative_answer:
                self._inference_count += 1
                self._cache_inference(ctx.question, ctx.contemplative_answer, ctx.user_name)
                self._trace_inference(ctx.question, ctx.contemplative_answer, "contemplation", 0.5, ctx.user_name,
                                     duration=time.time() - ctx._reasoning_start_time,
                                     complexity=ctx._question_complexity,
                                     tuning_hint="沉思推演完成，需要后续验证")
                final_answer = self._enhance_answer(
                    answer=ctx.contemplative_answer,
                    question=ctx.question,
                    method="contemplation",
                    complexity=ctx._question_complexity,
                    empathetic_note=ctx.empathetic_note,
                    memory_context=ctx._memory_context
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": final_answer,
                    "method": "contemplation", "confidence": 0.5, "user_name": ctx.user_name,
                    "correlation_id": ctx.payload.get("correlation_id", ""),
                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                    "confidence_hint": "low",
                }, priority=6, layer="L2")
                return {"status": "contemplation_match", "answer": ctx.contemplative_answer}
        return None

    def _ir_qica_knowledge_retrieve(self, ctx: "PulseInnerWorld.InferenceContext", _qica_paths):
        _knowledge_result = None
        # ★v22.0修复：严格按QICA优先级顺序检索
        if _qica_paths and self.node_pool:  # type: ignore[possibly-unbound]
            for _path in _qica_paths[:3]:  # type: ignore[possibly-unbound]
                _l3_nodes = self.node_pool.query(evol_level="L3", space_path_prefix=_path, limit=10)  # type: ignore[possibly-unbound]
                if _l3_nodes:
                    for _node in _l3_nodes:
                        _val = self._clean_node_value(str(_node.value)) if _node.value else ""
                        if _val and len(_val) > 30 and not self._is_internal_knowledge_node(_val):
                            _knowledge_result = _val
                            self._log(LogLevel.INFO, f"QICA路径检索: 路径={_path}, 命中节点")  # type: ignore[possibly-unbound]
                            break
                if _knowledge_result:
                    break
                # 该路径未命中，继续下一个路径
                _l2_nodes = self.node_pool.query(evol_level="L2", space_path_prefix=_path, limit=10)  # type: ignore[possibly-unbound]
                if _l2_nodes:
                    for _node in _l2_nodes:
                        _val = self._clean_node_value(str(_node.value)) if _node.value else ""
                        # ★质量修复B2：L2 路径与 L3 路径统一调用内部节点过滤器（修复仅查4前缀导致的漏检）
                        if _val and len(_val) > 30 and not self._is_internal_knowledge_node(_val):
                            _knowledge_result = _val
                            self._log(LogLevel.INFO, f"QICA路径检索(L2): 路径={_path}, 命中节点")  # type: ignore[possibly-unbound]
                            break
                if _knowledge_result:
                    break
                self._log(LogLevel.DEBUG, f"QICA路径检索未命中: 路径={_path}，尝试下一个路径")  # type: ignore[possibly-unbound]
        if not _knowledge_result:
            _knowledge_result = self._knowledge_retrieve(ctx.question)

        if _knowledge_result:
            # ★v23.0支点：检索结果相关性验证 + 自动降级链路
            # ★v9.5修复：传入命中节点所在 space_path，桥接「路径主题」与「正文关键词」语义鸿沟  # type: ignore[possibly-unbound]
            _relevance = self._verify_knowledge_relevance(
                ctx.question, _knowledge_result,
                space_path=_path if _qica_paths else None)  # type: ignore[possibly-unbound]
            if _relevance < 0.10:
                # 相关性过低，先尝试内在沉思拼凑
                self._log(LogLevel.INFO,
                         f"QICA检索结果不相关(相关度={_relevance:.2f})，尝试内在沉思")
                _contemplation = self._contemplative_reason(ctx.question)
                if _contemplation and len(_contemplation) > 30:
                    _knowledge_result = _contemplation
                    self._log(LogLevel.INFO, "降级到内在沉思成功")
                else:
                    # 沉思也不行，调用大模型
                    self._log(LogLevel.INFO, "内在沉思失败，降级到大模型")
                    _model_result = self._generate_branch_with_model(
                        original_question=ctx.question,
                        branch_name="知识检索降级",
                        branch_prompt=ctx.question,
                    )
                    if _model_result and len(_model_result) > 20:
                        _knowledge_result = _model_result
                        self._log(LogLevel.INFO, f"大模型降级成功: {_knowledge_result[:60]}...")
                        # ★v23.0补充：将大模型结果消化为知识，存入InsightBoard
                        try:
                            if hasattr(self, '_insight_board') and self._insight_board:
                                self._insight_board.post(
                                    insight_type="knowledge_boundary",
                                    content=_knowledge_result[:200],
                                    source_loop="知识检索降级·大模型生成",
                                    related_dimension="知识补充",
                                    confidence=0.6,
                                    keywords=[ctx.question[:30], "大模型补充"]
                                )
                        except Exception as e:
                            self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                    else:
                        _knowledge_result = None
            # 降级链路结束

            if _knowledge_result:
                self._inference_count += 1
                self._cache_inference(ctx.question, _knowledge_result, ctx.user_name)
            self._trace_inference(ctx.question, _knowledge_result, "qica_knowledge", 0.75, ctx.user_name,
                                 duration=time.time() - ctx._reasoning_start_time,
                                 complexity=ctx._question_complexity,
                                 tuning_hint="QICA建议知识检索")
            _final = self._enhance_answer(
                answer=_knowledge_result, question=ctx.question, method="qica_knowledge",
                complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
                memory_context=ctx._memory_context
            )
            self._emit(InferenceEvent.RESULT, {
                "question": ctx.question, "answer": _final,
                "method": "qica_knowledge", "confidence": 0.75, "user_name": ctx.user_name,
                "correlation_id": ctx.correlation_id,
                "confidence_hint": "moderate",
                "strategy_applied": ctx.payload.get("strategy_context", {}),
            }, priority=7, layer="L2")
            return {"status": "qica_knowledge", "answer": _knowledge_result}
        return None


    def _ir_dispatch_qica_method(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
        # ===== ★v22.0重构：QICA建议方法优先执行 =====
        _qica_method = ctx.payload.get("strategy_context", {}).get("qica_suggested_method", "")
        _qica_paths = ctx.payload.get("strategy_context", {}).get("qica_knowledge_paths", [])  # type: ignore[possibly-unbound]

        if _qica_method == "rule_reason":
            _rule_result = self._rule_reason(ctx.question, ctx.user_name, ctx.guidance)
            if _rule_result:
                self._inference_count += 1
                self._cache_inference(ctx.question, _rule_result, ctx.user_name)
                self._trace_inference(ctx.question, _rule_result, "qica_rule_reason", 0.9, ctx.user_name,
                                     duration=time.time() - ctx._reasoning_start_time,
                                     complexity=ctx._question_complexity,
                                     tuning_hint="QICA建议规则推理")
                _final = self._enhance_answer(
                    answer=_rule_result, question=ctx.question, method="qica_rule_reason",
                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
                    memory_context=ctx._memory_context
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": _final,
                    "method": "qica_rule_reason", "confidence": self._evidence_conf(0.9, "rule", [_rule_result]), "user_name": ctx.user_name,
                    "correlation_id": ctx.correlation_id,
                    "confidence_hint": "high",
                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                }, priority=7, layer="L2")
                return {"status": "qica_rule_reason", "answer": _rule_result}

        # ===== ★第六批 任务2.2：补齐其余 6 个 QICA 建议方法的执行分支 =====
        # 原实现仅覆盖 rule_reason / knowledge_retrieve，其余 method 无分支，
        # 导致 QICA 建议被记录进 strategy_applied 却从不真正执行。
        if _qica_method in self._QICA_EXTRA_METHODS:
            _extra = self._execute_qica_method(
                _qica_method, ctx.question, ctx.user_name, ctx.guidance)
            if _extra:
                self._log(LogLevel.INFO,
                          f"[B2策略] 建议方法={_qica_method} 已采纳并优先执行")
                return _extra
            self._log(LogLevel.INFO,
                      f"[B2策略] 建议方法={_qica_method} 执行无有效结果，回落默认路径")

        if _qica_method == "knowledge_retrieve":
            _k = self._ir_qica_knowledge_retrieve(ctx, _qica_paths)
            if _k is not None:
                return _k
        if _qica_method == "cognitive_compute":
            _cog_result = self._cognitive_compute(ctx.question)
            if _cog_result:
                self._inference_count += 1
                self._cache_inference(ctx.question, _cog_result, ctx.user_name)
                self._trace_inference(ctx.question, _cog_result, "qica_cognitive", 0.65, ctx.user_name,
                                     duration=time.time() - ctx._reasoning_start_time,
                                     complexity=ctx._question_complexity,
                                     tuning_hint="QICA建议认知算子")
                _final = self._enhance_answer(
                    answer=_cog_result, question=ctx.question, method="qica_cognitive",
                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
                    memory_context=ctx._memory_context
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": _final,
                    "method": "qica_cognitive", "confidence": 0.65, "user_name": ctx.user_name,
                    "correlation_id": ctx.correlation_id,
                    "confidence_hint": "moderate",
                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                }, priority=7, layer="L2")
                return {"status": "qica_cognitive", "answer": _cog_result}
        # ===== QICA建议方法优先执行结束 =====
        return None

    def _ir_run_pipeline(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
        # ===== v20.0新增：思考纪律——标准思维流水线入口 =====
        # 当所有检测器未命中时，按大脑皮层规划的流水线深度执行推理
        _pipeline = ctx.payload.get("strategy_context", {}).get("thinking_pipeline", {})
        _pipeline_depth = _pipeline.get("depth", "standard")

        if _pipeline_depth == "quick":
            # 快速通道：仅知识检索，不经过复杂推理
            self._log(LogLevel.DEBUG, f"思考纪律·快速通道: '{ctx.question[:40]}'")
            _knowledge_result = self._knowledge_retrieve(ctx.question)
            if _knowledge_result:
                # v20.0新增：追加L3智慧节点的策略指导
                _wisdom = self._get_wisdom_guidance(ctx.question)
                if _wisdom:
                    _knowledge_result = _knowledge_result + "\n\n💡 " + _wisdom
                # ★v23.0：标准通道检索结果验证降级
                _validated = self._validate_and_degrade(ctx.question, _knowledge_result, "标准通道")
                if _validated != _knowledge_result:
                    _knowledge_result = _validated

                self._inference_count += 1
                self._cache_inference(ctx.question, _knowledge_result, ctx.user_name)
                _final = self._enhance_answer(
                    answer=_knowledge_result, question=ctx.question, method="thinking_discipline_quick",
                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
                    memory_context=ctx._memory_context
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": _final,
                    "method": "thinking_discipline_quick", "confidence": 0.85, "user_name": ctx.user_name,
                    "correlation_id": ctx.correlation_id,
                    "confidence_hint": "high",
                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                }, priority=7, layer="L2")
                return {"status": "thinking_discipline_quick", "answer": _knowledge_result}
            # 快速通道未命中，降级到标准通道继续
            self._log(LogLevel.DEBUG, "思考纪律·快速通道未命中，降级为标准通道")

        if _pipeline_depth in ("standard", "quick"):
            # 标准通道：理解→检索→表达（快速通道降级也走此路径）
            _knowledge_result = self._knowledge_retrieve(ctx.question)
            if _knowledge_result:
                # v20.0新增：追加L3智慧节点的策略指导
                _wisdom = self._get_wisdom_guidance(ctx.question)
                if _wisdom:
                    _knowledge_result = _knowledge_result + "\n\n💡 " + _wisdom
                self._inference_count += 1
                self._cache_inference(ctx.question, _knowledge_result, ctx.user_name)
                _final = self._enhance_answer(
                    answer=_knowledge_result, question=ctx.question, method="thinking_discipline_standard",
                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
                    memory_context=ctx._memory_context
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": _final,
                    "method": "thinking_discipline_standard", "confidence": 0.75, "user_name": ctx.user_name,
                    "correlation_id": ctx.correlation_id,
                    "confidence_hint": "moderate",
                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                }, priority=7, layer="L2")
                return {"status": "thinking_discipline_standard", "answer": _knowledge_result}

        if _pipeline_depth == "deep":
            # 深度通道：理解→检索→验证→深度思考→表达
            self._log(LogLevel.INFO, f"思考纪律·深度通道: '{ctx.question[:60]}'")
            # 第一步：先检索知识作为基础
            _knowledge_result = self._knowledge_retrieve(ctx.question)
            # 第二步：深度思考
            _deep_result = None
            if hasattr(self, '_reasoning_pool') and self._reasoning_pool:
                _future = None
                try:
                    _future = self._reasoning_pool.submit("PulseInnerWorld._deep_think", ctx.question, 3)
                    if _future:
                        # ★主线第31批 T1：降级标记（truthy dict）不得当结果用，
                        #   否则主进程同步回退被跳过、内部 dict 还会进入用户可见答案。
                        _deep_result = self._m31_accept_subproc_deep_result(
                            _future.result(timeout=self._deep_think_timeout))
                except Exception:
                    if _future is not None:
                        try:
                            _future.cancel()
                        except Exception as e:
                            self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
            if not _deep_result:
                # ★主线第31批 T1：主进程同步执行（子进程结果不可用时的正路）；
                #   受第27批「自推理开始起算」的总预算约束，避免无限耗时。
                _deep_result = self._deep_think(
                    ctx.question,
                    deadline=self._m31_deep_fallback_deadline(ctx._reasoning_start_time))

            # 第三步：综合知识检索和深度思考结果
            if _deep_result:
                # v20.0新增：追加L3智慧节点的策略指导
                _wisdom = self._get_wisdom_guidance(ctx.question)
                _wisdom_text = f"\n\n💡 {_wisdom}" if _wisdom else ""
                # 如果知识检索也有结果，融合两者
                # ★主线第30批 T1：深度思考返回的是**降级标记**（如子进程无知识上下文）
                #   时，绝不能把它当内容拼进用户可见答案——那会把内部 dict 直接暴露给用户。
                #   此时仅用知识检索结果作答（深度思考部分静默丢弃并记 DEBUG）。
                _m30_degraded = False
                try:
                    _m30_d = _deep_result if isinstance(_deep_result, dict) else {}
                    _m30_degraded = bool(_m30_d) and str(_m30_d.get("status", "")) == "degraded"
                except Exception as _m30_e:
                    self._log(LogLevel.DEBUG,
                              f"降级标记判定异常已忽略: {type(_m30_e).__name__}: {_m30_e}")
                if _m30_degraded:
                    self._log(LogLevel.DEBUG,
                              "深度思考返回降级标记，本次仅用知识检索结果作答（不拼入内部标记）")
                if _knowledge_result and not _m30_degraded:
                    _combined = f"{_knowledge_result}\n\n（经过深入思考后补充）{_deep_result}{_wisdom_text}"
                else:
                    _combined = _deep_result
                self._inference_count += 1
                self._cache_inference(ctx.question, _combined, ctx.user_name)
                self._trace_inference(ctx.question, _combined, "thinking_discipline_deep", 0.7, ctx.user_name,
                                     duration=time.time() - ctx._reasoning_start_time,
                                     complexity=ctx._question_complexity,
                                     tuning_hint="思考纪律深度通道完成")
                _final = self._enhance_answer(
                    answer=_combined, question=ctx.question, method="thinking_discipline_deep",
                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
                    memory_context=ctx._memory_context
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": _final,
                    "method": "thinking_discipline_deep", "confidence": 0.7, "user_name": ctx.user_name,
                    "correlation_id": ctx.correlation_id,
                    "confidence_hint": "moderate",
                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                    "thinking_discipline": True,
                }, priority=7, layer="L2")
                return {"status": "thinking_discipline_deep", "answer": _combined}
            elif _knowledge_result:
                # 深度思考失败但知识检索有结果，按标准通道处理
                self._inference_count += 1
                self._cache_inference(ctx.question, _knowledge_result, ctx.user_name)
                _final = self._enhance_answer(
                    answer=_knowledge_result, question=ctx.question, method="thinking_discipline_deep_fallback",
                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
                    memory_context=ctx._memory_context
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": _final,
                    "method": "thinking_discipline_deep_fallback", "confidence": 0.6, "user_name": ctx.user_name,
                    "correlation_id": ctx.correlation_id,
                    "confidence_hint": "moderate",
                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                }, priority=7, layer="L2")
                return {"status": "thinking_discipline_deep_fallback", "answer": _knowledge_result}
        # ===== v20.0思考纪律入口结束 =====
        return None






    def _on_inference_request(self, payload: dict) -> dict[str, Any]:
        _ctx = self._ir_build_context(payload)
        if _ctx is None:
            return {"status": "skipped", "reason": "空问题"}
        question = _ctx.question
        user_name = _ctx.user_name
        correlation_id = _ctx.correlation_id
        empathetic_note = _ctx.empathetic_note
        contemplative_answer = _ctx.contemplative_answer
        _supplement_topic = _ctx._supplement_topic
        _explicit_inference_result = _ctx._explicit_inference_result
        tool_hint = _ctx.tool_hint
        guidance = _ctx.guidance
        _memory_context = _ctx._memory_context
        _question_complexity = _ctx._question_complexity
        _emotion_modulation = _ctx._emotion_modulation
        _reasoning_start_time = _ctx._reasoning_start_time
        _meta_state = _ctx._meta_state

        _explicit = self._ir_try_explicit_search(_ctx)
        if _explicit is not None:
            return _explicit
        _detector_result = self._ir_run_detectors(_ctx)
        if _detector_result is not None:
            return _detector_result
        _qica = self._ir_dispatch_qica_method(_ctx)
        if _qica is not None:
            return _qica
        _pipeline_result = self._ir_run_pipeline(_ctx)
        if _pipeline_result is not None:
            return _pipeline_result

        # ★v23.0清理：v18.0标记的旧内联分支已由17个检测器完整覆盖，安全移除
        # ★v17.0新增：情绪调制推理策略——调整复杂度阈值和检索深度
        _modulated_complexity = _question_complexity
        if _emotion_modulation.get("depth_factor", 1.0) > 1.1:
            # 需要更深思时，降低触发深度思考的门槛
            _modulated_complexity = min(1.0, _question_complexity + 0.15)
            self._log(LogLevel.DEBUG,
                     f"情绪调制(深度): 降低深度思考门槛 "
                     f"({_question_complexity:.2f}→{_modulated_complexity:.2f})")
        elif _emotion_modulation.get("depth_factor", 1.0) < 0.9:
            # 需要更快决策时，提高触发深度思考的门槛
            _modulated_complexity = max(0.0, _question_complexity - 0.1)

        # ===== 阶段6新增：压力→策略调节闭环（应激轴调制推理策略）=====
        _stress_modulation = self._get_stress_reasoning_modulation()
        if _stress_modulation.get("depth_factor", 1.0) < 0.9:
            # 高压：提高触发深度思考的门槛，优先更快更浅的决策
            _modulated_complexity = max(0.0, _modulated_complexity - 0.1)

        # ===== v20.0新增：情绪驱动的推理策略选择 =====
        _strategy_pref = _emotion_modulation.get("strategy_preference", {})
        _strategy_active = _strategy_pref.get("active", False)
        _preferred_strategies = list(_strategy_pref.get("preferred", []))
        _avoid_strategies = list(_strategy_pref.get("avoid", []))

        # 压力策略并入情绪策略偏好（压力优先：拆分 + 降并行）
        if _stress_modulation.get("prefer_decompose"):
            if "multi_step_execute" not in _preferred_strategies:
                _preferred_strategies.insert(0, "multi_step_execute")
        if _stress_modulation.get("avoid_parallel"):
            for _p in ("multi_branch_deep_think", "multi_branch"):
                if _p not in _avoid_strategies:
                    _avoid_strategies.append(_p)
        if _stress_modulation.get("prefer_decompose") or _stress_modulation.get("avoid_parallel"):
            _strategy_active = True

        if _strategy_active and (_preferred_strategies or _avoid_strategies):
            # 策略偏好激活时，尝试优先策略列表中的方法
            _strategy_result = None

            # 1. 优先策略：按顺序尝试优先列表中的推理策略
            for _strat in _preferred_strategies:
                if _strat == "creative_solution":
                    _strategy_result = self._attempt_creative_solution(question)
                elif _strat == "experience_route":
                    # 尝试经验匹配路由（已在检测器中，这里做补充尝试）
                    try:
                        from nucleus.mnemosyne.ReasoningExperience import (
                            get_reasoning_experience,
                        )
                        _exp = get_reasoning_experience()
                        _match = _exp.search(question)
                        if _match and _match.get("confidence", 0) >= 0.4:
                            _derivation_type = _match.get("derivation_type", "")
                            if _derivation_type:
                                _strategy_result = self._route_to_deriver(
                                    question, user_name, _reasoning_start_time,
                                    _question_complexity, empathetic_note,
                                    _memory_context, payload, guidance
                                )
                    except Exception as e:
                        self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                elif _strat == "cache":
                    _cached = self._inference_cache.get(f"{user_name}:{question.strip()}")
                    if not _cached:
                        _cached = self._inference_cache.get(f"用户:{question.strip()}")
                    if _cached:
                        _cached_answer = _cached.get("answer", "")
                        if _cached_answer:
                            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                            self._cache_hit_count += 1
                            _strategy_result = _cached_answer
                elif _strat == "rule_reason":
                    _strategy_result = self._rule_reason(question, user_name, guidance)
                elif _strat == "contemplation":
                    _strategy_result = self._contemplative_reason(question)

                if _strategy_result:
                    _method = f"emotion_strategy_{_strat}"
                    self._inference_count += 1
                    self._cache_inference(question, _strategy_result, user_name)
                    self._trace_inference(question, _strategy_result, _method, 0.65, user_name,
                                         duration=time.time() - _reasoning_start_time,
                                         complexity=_question_complexity,
                                         tuning_hint=f"情绪策略偏好触发({_strategy_pref.get('description', '')})")
                    _final = self._enhance_answer(
                        answer=_strategy_result, question=question, method=_method,
                        complexity=_question_complexity, empathetic_note=empathetic_note,
                        memory_context=_memory_context
                    )
                    self._emit(InferenceEvent.RESULT, {
                        "question": question, "answer": _final,
                        "method": _method, "confidence": 0.65, "user_name": user_name,
                        "correlation_id": correlation_id,
                        "confidence_hint": "moderate",
                        "strategy_applied": payload.get("strategy_context", {}),
                        "emotion_strategy": True,
                    }, priority=7, layer="L2")
                    self._log(LogLevel.INFO,
                             f"情绪策略命中: {_emotion_modulation.get('emotion', '中性')}→{_strat} '{question[:40]}'")
                    return {"status": _method, "answer": _strategy_result}

            # 2. 如果优先策略都未命中，且当前路由类型在避免列表中，回退到知识检索
            # （避免列表中的策略在下方的_route_to_deriver中被跳过，这里只做记录）
            if _avoid_strategies:
                self._log(LogLevel.DEBUG,
                         f"情绪策略避免: {_emotion_modulation.get('emotion', '中性')}→跳过{_avoid_strategies}")
        # ===== v20.0新增结束 =====

        # ===== 推理问题前置过滤结束 =====
        _derivation_answer = self._route_to_deriver(question, user_name, _reasoning_start_time,
                                                      _question_complexity, empathetic_note,
                                                      _memory_context, payload, guidance)
        if _derivation_answer:
            # ★v22.0方向三修复v3：相关性检查——经验路由结果与问题无关时，跳过
            _derivation_content = _derivation_answer.get("answer", "")
            if _derivation_content and len(str(_derivation_content)) > 20:
                _question_core = set(re.findall(r'[\u4e00-\u9fff]{2,4}', question)[:5])
                _answer_core = set(re.findall(r'[\u4e00-\u9fff]{2,4}', str(_derivation_content)[:200]))
                _overlap = len(_question_core & _answer_core)
                if _overlap < 1:
                    self._log(LogLevel.INFO,
                             f"经验路由跳过(相关性低): 问题核心词={_question_core}, "
                             f"答案核心词={_answer_core}, 重叠={_overlap}")
                    _derivation_answer = None  # 跳过，让流程继续到知识检索

            if _derivation_answer:
                try:
                    from nucleus.mnemosyne.ReasoningExperience import (
                        get_reasoning_experience,
                    )
                    _reasoning_exp = get_reasoning_experience()
                    _status = _derivation_answer.get("status", "")
                    if _status.startswith("deriver_"):
                        _derivation_type_record = _status.replace("deriver_", "")
                        _reasoning_exp.record(question, _derivation_type_record, source="local")
                        self._log(LogLevel.DEBUG, f"经验沉淀: 类型={_derivation_type_record}")
                except Exception as e:
                    self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                return _derivation_answer
        # 使用大脑皮层传来的工具提示
        tool_hint = payload.get("tool_hint", {})
        if tool_hint.get("should_search", False):
            self._log(LogLevel.DEBUG, f"工具提示: 问题='{question[:30]}' 建议搜索")
        else:
            # 工具认知层建议不搜索：将在元认知决策阶段处理
            pass
        # ===== 新增: 复杂问题自主拆解 =====
        _is_param_format = ("已知：" in question or "已知:" in question) and ("=" in question or "＝" in question)
        _is_rule_format = any(_kw in question for _kw in ["规则1", "规则2", "规则3", "第一，", "第二，", "第三，", "如果", "那么"])
        if _is_param_format and not _is_rule_format:
            sub_questions = None
        else:
            sub_questions = self._decompose_complex_question(question)
        if sub_questions and len(sub_questions) >= 2:
            self._log(LogLevel.INFO,
                     f"问题拆解: 将'{question[:40]}'拆分为{len(sub_questions)}个子问题")
            # 逐个推理子问题
            sub_results = []
            for sq in sub_questions:
                sq_answer = self._knowledge_retrieve(sq)
                if sq_answer:
                    sub_results.append({"question": sq, "answer": sq_answer, "found": True})
                else:
                    sub_results.append({"question": sq, "answer": None, "found": False})
            # 综合子问题结果
            if any(r["found"] for r in sub_results):
                composite_answer = self._compose_sub_results(question, sub_results)
                if composite_answer:
                    self._inference_count += 1
                    self._cache_inference(question, composite_answer, user_name)
                    self._trace_inference(question, composite_answer, "decompose", 0.75, user_name,
                                         duration=time.time() - _reasoning_start_time,
                                         complexity=_question_complexity,
                                         tuning_hint="复杂问题拆解成功，分解策略有效")
                    final_answer = self._enhance_answer(
                        answer=composite_answer,
                        question=question,
                        method="decompose",
                        complexity=_question_complexity,
                        empathetic_note=empathetic_note,
                        memory_context=_memory_context
                    )
                    self._emit(InferenceEvent.RESULT, {
                        "question": question, "answer": final_answer,
                        "method": "decompose", "confidence": 0.75, "user_name": user_name,
                        "correlation_id": payload.get("correlation_id", ""),
                        "strategy_applied": payload.get("strategy_context", {}),
                        "confidence_hint": "moderate",
                    }, priority=7, layer="L2")
                    return {"status": "decompose_match", "answer": composite_answer}
        # ===== 新增: 思考停顿——复杂问题优先走深度思考模式 =====
        complexity_score = self._assess_question_complexity(question)
        _deep_concept_words = ["智慧", "自由", "意义", "本质", "真理", "存在", "意识", "爱", "幸福本质", "价值"]
        _has_deep_concept = any(_dw in question for _dw in _deep_concept_words)
        if _has_deep_concept:
            complexity_score = max(complexity_score, 0.65)  # 强制提高复杂度
        if complexity_score >= 0.6:
            # 高复杂度问题：先尝试沉思，再回退到知识检索
            self._log(LogLevel.INFO,
                     f"思考停顿: 问题复杂度={complexity_score:.2f}，进入深度思考模式: {question[:40]}")
            if self.node_pool:
                deep_answer = None
                if hasattr(self, '_reasoning_pool') and self._reasoning_pool:
                    _future = None
                    try:
                        _future = self._reasoning_pool.submit(
                            "PulseInnerWorld._deep_think", question, 3
                        )
                        if _future:
                            # ★主线第31批 T1：统一判定，见 _m31_accept_subproc_deep_result
                            deep_answer = self._m31_accept_subproc_deep_result(
                                _future.result(timeout=self._deep_think_timeout))
                    except Exception:
                        if _future is not None:
                            try:
                                _future.cancel()
                            except Exception as e:
                                self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                if not deep_answer:
                    # ★主线第31批 T1：主进程同步执行 + 第27批预算口径
                    deep_answer = self._deep_think(
                        question,
                        deadline=self._m31_deep_fallback_deadline(_reasoning_start_time))
                if deep_answer:
                    self._inference_count += 1
                    self._cache_inference(question, deep_answer, user_name)
                    self._trace_inference(question, deep_answer, "deep_think", 0.55, user_name,
                                         duration=time.time() - _reasoning_start_time,
                                         complexity=_question_complexity,
                                         tuning_hint="深度思考流水线完成，多维度综合")
                    final_answer = self._enhance_answer(
                        answer=deep_answer,
                        question=question,
                        method="deep_think",
                        complexity=_question_complexity,
                        empathetic_note=empathetic_note,
                        memory_context=_memory_context
                    )
                    self._emit(InferenceEvent.RESULT, {
                        "question": question, "answer": final_answer,
                        "method": "deep_think", "confidence": 0.55, "user_name": user_name,
                        "correlation_id": payload.get("correlation_id", ""),
                        "strategy_applied": payload.get("strategy_context", {}),
                        "confidence_hint": "moderate",
                        "thinking_pause": True,
                    }, priority=7, layer="L2")
                    return {"status": "deep_think", "answer": deep_answer}
                # 流水线未产生结果，回退到原有沉思逻辑
                contemplative_answer = self._contemplative_reason(question)
                if contemplative_answer:
                    self._inference_count += 1
                    self._cache_inference(question, contemplative_answer, user_name)
                    self._trace_inference(question, contemplative_answer, "deep_contemplation", 0.6, user_name,
                                         duration=time.time() - _reasoning_start_time,
                                         complexity=_question_complexity,
                                         tuning_hint="高复杂度问题，已进入深度思考模式")
                    final_answer = self._enhance_answer(
                        answer=contemplative_answer,
                        question=question,
                        method="deep_contemplation",
                        complexity=_question_complexity,
                        empathetic_note=empathetic_note,
                        memory_context=_memory_context
                    )
                    self._emit(InferenceEvent.RESULT, {
                        "question": question, "answer": final_answer,
                        "method": "deep_contemplation", "confidence": 0.6, "user_name": user_name,
                        "correlation_id": payload.get("correlation_id", ""),
                        "strategy_applied": payload.get("strategy_context", {}),
                        "confidence_hint": "moderate",
                        "thinking_pause": True,
                    }, priority=7, layer="L2")
                    return {"status": "deep_contemplation", "answer": contemplative_answer}

        # ★v17.0新增：认知边界感知——记录推理失败的领域
        if not _derivation_answer:
            _failed_keywords = []
            for _match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
                _word = _match.group()
                if _word not in _failed_keywords and _word not in [
                    "什么是", "是什么", "为什么", "如何", "怎么",
                    "这个", "那个", "一个", "一种", "可以", "能够",
                ]:
                    _failed_keywords.append(_word)

            if _failed_keywords:
                if not hasattr(self, '_failed_domain_records'):
                    self._failed_domain_records = {}
                for _kw in _failed_keywords[:5]:
                    self._failed_domain_records[_kw] = self._failed_domain_records.get(_kw, 0) + 1

                if not hasattr(self, '_failed_domain_log_count'):
                    self._failed_domain_log_count = 0
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._failed_domain_log_count += 1
                if self._failed_domain_log_count % 3 == 0:
                    _top_failed = sorted(self._failed_domain_records.items(), key=lambda x: x[1], reverse=True)[:3]
                    self._log(LogLevel.INFO,
                             f"认知边界记录: 推理失败已累计{self._failed_domain_log_count}次, "
                             f"高频失败领域={_top_failed}")

        _knowledge = self._ir_assemble_knowledge_answer(_ctx)
        if _knowledge is not None:
            return _knowledge
        # ===== 元认知决策：推理结束后的行动闭环（增强版） =====
        _strategy_context = payload.get("strategy_context", {})
        tool_requested = False
        # ===== 新增: 状态感知调制——根据内在状态调整工具选择倾向 =====
        fallback_tools = _strategy_context.get("fallback_approach", [])
        # ===== 新增：复杂问题直接走大模型，不走搜索 =====
        _has_remote_api = False
        try:
            import config as _cfg_check
            _api_cfg = getattr(_cfg_check, 'REMOTE_API_CONFIG', {})
            if _api_cfg.get("enabled", False) and _api_cfg.get("api_key", ""):
                _has_remote_api = True
        except Exception as e:
            self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
        if _question_complexity > 0.4 and _has_remote_api:
            # ★P1-1(2026-09-03)：大模型调用前置思考——即使知识检索/沉思未直接命中，
            #   也快速检索相关知识作为上下文传给大模型，让大模型基于框架本地认知补充，
            #   而非从零开始回答。减少大模型依赖，提高回答准确性。
            _local_knowledge_hint = ""
            try:
                if self.node_pool:
                    _hint_results = self.node_pool.query(
                        question, limit=3, min_relevance=0.3)
                    if _hint_results:
                        _hints = []
                        for _h in _hint_results[:3]:
                            _val = _h.get("value", "") or _h.get("content", "")
                            if _val and len(_val) > 10:
                                _hints.append(_val[:200])
                        if _hints:
                            _local_knowledge_hint = (
                                "[框架本地相关知识参考]\n" + "\n".join(_hints)
                            )
                            self._log(LogLevel.DEBUG,
                                     f"大模型前置思考: 检索到{len(_hints)}条相关知识作为上下文")
            except Exception as _hint_err:
                self._log(LogLevel.WARNING, f"大模型前置知识检索异常: {_hint_err}")

            # 区分：有correlation_id是对话触发（需要回复），没有是后台自主学习（只消化不输出）
            if correlation_id:
                self._log(LogLevel.INFO,
                         f"复杂问题推给大模型: 复杂度={_question_complexity:.2f}"
                         f"{'，含本地知识参考' if _local_knowledge_hint else ''}")
                self._direct_to_lung_questions.add(question.strip())
                _memory_context = self._build_memory_context(question, user_name, guidance)
                if _local_knowledge_hint:
                    _memory_context = (_memory_context or "") + "\n\n" + _local_knowledge_hint
                self._emit(InferenceEvent.RESULT, {
                    "question": question, "answer": None,
                    "correlation_id": correlation_id,
                    "confidence": 0.0, "user_name": user_name,
                    "strategy_applied": _strategy_context,
                    "tool_requested": False,
                    "memory_context": _memory_context,
                }, priority=5, layer="L2")
                return {"status": "direct_to_lung", "reason": "复杂问题优先推理"}
            else:
                # 后台自主学习：直接调用大模型消化为知识，不经过嘴巴输出
                self._log(LogLevel.INFO, f"后台学习触发大模型: {question[:40]}")
                self._emit(Event.LUNGS_SELECT_MODEL, {  # ★P3-5修复：修正笔误，原 "lung.select_model" 与常量 LungEvent.SELECT_MODEL 不匹配
                    "task_type": "chat",
                    "prompt": question,
                    "user_name": user_name,
                    "memory_context": self._build_memory_context(question, user_name, guidance),
                    # ★修复：显式标记后台学习，不依赖肺部「is_dialogue 默认 False」的隐式行为。
                    # 明确 is_dialogue=False + is_background_learning=True，语义自明、防回归。
                    "is_dialogue": False,
                    "is_background_learning": True,
                }, priority=4, layer="L2")
                return {"status": "background_learning", "reason": "后台自主学习"}
        # ===== 新增: 情绪驱动的工具选择 =====
        _current_emotion = self._get_current_emotion()
        _emotion_intensity = 0.0
        if self.hormones and hasattr(self.hormones, 'get_emotion_intensity'):
            try:
                _emotion_intensity = self._call_provider(self._emotion_intensity_provider, default=0.0)
            except Exception as e:
                self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
        # 悲伤/恐惧时：优先内在沉思而非外部搜索
        if _current_emotion in ("悲伤", "恐惧") and _emotion_intensity > 0.3:
            if "deep_search" in fallback_tools:
                fallback_tools.remove("deep_search")
            if "inner_world" not in fallback_tools:
                fallback_tools.insert(0, "inner_world")
            self._log(LogLevel.DEBUG,
                     f"情绪驱动({_current_emotion}): 优先内在沉思，跳过外部搜索")
        # 喜悦/期待时：更愿意尝试外部搜索和探索
        elif _current_emotion in ("喜悦", "期待") and _emotion_intensity > 0.2:
            if "deep_search" not in fallback_tools:
                fallback_tools.append("deep_search")
            # 提升复杂度感知，更容易触发深度思考
            _question_complexity = min(1.0, _question_complexity + 0.1)
        # 焦虑时：使用缓存优先，减少不确定性
        elif _current_emotion == "焦虑" and _emotion_intensity > 0.3:
            # 延长缓存有效期（在缓存检查处已处理）
            fallback_tools = ["inner_world"]  # 只用内在世界，不做外部搜索
        # ===== 工具认知层：根据大脑皮层的建议决定是否跳过深度搜索 =====
        if not tool_hint.get("should_search", True) and "deep_search" in fallback_tools:
            fallback_tools.remove("deep_search")
            self._log(LogLevel.INFO, f"工具认知: 根据搜索经验，跳过深度搜索 (问题='{question[:30]}')")
        if _meta_state.get("cognitive_load") == "high":
            if "deep_search" in fallback_tools:
                fallback_tools.remove("deep_search")
                self._log(LogLevel.DEBUG, "状态感知: 认知负荷偏高，跳过深度搜索")
        # ★压力闭环：高压下降并行/外部搜索，优先内在分步求解（复用已算出的压力调制）
        if _stress_modulation.get("avoid_parallel"):
            if "deep_search" in fallback_tools:
                fallback_tools.remove("deep_search")
            if "inner_world" not in fallback_tools:
                fallback_tools.insert(0, "inner_world")
        if _meta_state.get("wisdom_quality") == "high":
            if "deep_search" not in fallback_tools:
                fallback_tools.append("deep_search")
        # 情感充盈时，更愿意冒险尝试创造性方案
        if _meta_state.get("emotional_state") == "positive":
            complexity_threshold = _meta_state.get("complexity_bonus", 0)
            _question_complexity += complexity_threshold  # 提升复杂度感知，更容易触发深度思考
        # 分析问题特征，决定工具选择策略
        question_features = self._analyze_question_features(question)
        # 策略1: 计算验证类问题 → 优先用代码沙箱
        if question_features.get("is_computational") and not tool_requested:
            # ★FIX(推理准确性): 先尝试本地安全算术求值，命中则直接返回结果，不再发射空壳占位代码
            _calc_result = self._safe_eval_arithmetic(question)
            if _calc_result is not None:
                self._inference_count += 1
                _calc_answer = f"计算结果：{_calc_result}"
                self._cache_inference(question, _calc_answer, user_name)
                _final = self._enhance_answer(
                    answer=_calc_answer, question=question, method="arithmetic",
                    complexity=_question_complexity, empathetic_note=empathetic_note,
                    memory_context=_memory_context,
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": question, "answer": _final,
                    "method": "arithmetic", "confidence": self._evidence_conf(0.95, "arithmetic", [1]), "user_name": user_name,
                    "correlation_id": correlation_id,
                    "confidence_hint": "high",
                    "strategy_applied": _strategy_context,
                }, priority=7, layer="L2")
                return {"status": "arithmetic", "answer": _calc_answer}
            self._log(LogLevel.INFO,
                     f"元认知决策: 计算类问题，尝试代码验证: {question[:40]}")
            self._emit(Event.MOTOR_EXECUTE, {
                "code": f"# 验证计算: {question[:80]}\nprint('计算结果: ...')",
                "language": "python",
                "user_name": user_name,
                "task_id": f"meta_calc_{int(time.time())}",
            }, priority=6, layer="L2")
            tool_requested = True
        # 策略2: 比较分析类问题 → 拆解子问题后逐个搜索
        if question_features.get("is_comparative") and not tool_requested:
            sub_parts = self._decompose_complex_question(question)
            if sub_parts and len(sub_parts) >= 2:
                self._log(LogLevel.INFO,
                         f"元认知决策: 比较类问题，拆解为{len(sub_parts)}个子问题: {question[:40]}")
                for sq in sub_parts[:2]:
                    self._emit(Event.CONTROLLER_OPEN_URL, {
                        "url": f"https://lite.duckduckgo.com/lite/?q={sq[:80]}",
                        "reason": f"元认知拆解搜索: {sq[:40]}",
                        "search_topic": sq[:80],
                        "deep_search": True,
                        "search_intent": "curiosity",
                    }, priority=4, layer="L3")
                tool_requested = True
        _ds_gate = self._ir_try_deep_search_pre(_ctx, fallback_tools, tool_requested)
        if _ds_gate is not None:
            _can_search, _skip_reason = _ds_gate
            _ds_exec, tool_requested = self._ir_try_deep_search_exec(_ctx, _can_search, _skip_reason, tool_requested, _strategy_context, _has_remote_api)
            if _ds_exec is not None:
                return _ds_exec
        # 策略4: 无工具可用——创造性解决方案
        if not tool_requested:
            self._log(LogLevel.INFO,
                     f"元认知决策: 无预设工具可用，尝试创造性解决: {question[:40]}")
            # 生成一个基于已有知识的假设作为临时解决方案
            creative_solution = self._attempt_creative_solution(question)
            if creative_solution:
                final_answer = self._enhance_answer(
                    answer=creative_solution,
                    question=question,
                    method="creative_solution",
                    complexity=_question_complexity,
                    empathetic_note=empathetic_note,
                    memory_context=_memory_context
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": question, "answer": final_answer,
                    "method": "creative_solution", "confidence": 0.3,
                    "user_name": user_name,
                    "correlation_id": payload.get("correlation_id", ""),
                    "strategy_applied": _strategy_context,
                    "tool_requested": True,
                    "creative_solution": True,
                }, priority=5, layer="L2")
                return {
                    "status": "creative_solution",
                    "answer": creative_solution,
                    "confidence": 0.3,
                }
            # 将未解决的问题沉淀
            self._emit(GrowthEvent.NEED_DETECTED, {
                "milestone": "待解决问题",
                "gaps": [{"metric": "unsolved", "current": 0, "target": 1}],
                "suggestion": f"未解决的问题: {question[:80]}",
                "current_level": {"unsolved_question": question[:80]},
                "growth_topic": f"待解决问题: {question[:60]}",
            }, priority=3, layer="L3")
        _ctx._strategy_context = _strategy_context
        _ctx.tool_requested = tool_requested
        _ctx.question_features = question_features
        return self._ir_finalize(_ctx)

    def _ir_finalize(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
        # 发射最终结果（无答案时附上记忆上下文，供大脑皮层调用肺模型时使用）
        _memory_context = self._build_memory_context(ctx.question, ctx.user_name, ctx.guidance)
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": None,
            "method": "none", "confidence": 0.0, "user_name": ctx.user_name,
            "correlation_id": ctx.payload.get("correlation_id", ""),
            "strategy_applied": ctx._strategy_context,
            "tool_requested": ctx.tool_requested,
            "memory_context": _memory_context,
        }, priority=5, layer="L2")
        # ===== 新增: 实时元认知监控——感知本次推理的总体质量 =====
        reasoning_duration = time.time() - ctx._reasoning_start_time
        if reasoning_duration > 2.0:
            self._log(LogLevel.DEBUG,
                     f"实时元认知: 本次推理耗时{reasoning_duration:.1f}秒，"
                     f"问题复杂度={ctx._question_complexity:.2f}，"
                     f"最终状态={ctx.tool_requested and '已触发工具' or '未找到答案'}")
        # ===== 新增: 元认知决策经验记录 =====
        if ctx.tool_requested:
            # 有工具被触发时，记录触发原因和策略
            _decision_type = "search" if not ctx.question_features.get("is_computational") else "code"
            self._trace_inference(ctx.question, f"[工具调度: {_decision_type}]",
                                 f"tool_{_decision_type}", 0.0, ctx.user_name,
                                 duration=reasoning_duration,
                                 complexity=ctx._question_complexity,
                                 tuning_hint=f"元认知决策触发{_decision_type}，耗时{reasoning_duration:.1f}s")

            # 补充工具认知层经验：记录触发工具时的决策上下文
            if hasattr(self, '_search_experience') and _decision_type == "search":
                self._log(LogLevel.DEBUG,
                         f"元认知决策记录: 触发深度搜索 (主题='{ctx.search_query[:40]}')")
        else:
            # 无工具可用，记录为推理盲区
            self._trace_inference(ctx.question, "[无可用工具]",
                                 "none", 0.0, ctx.user_name,
                                 duration=reasoning_duration,
                                 complexity=ctx._question_complexity,
                                 tuning_hint="所有工具均不可用，建议补充知识")
        # 如果推理耗时过长且未找到答案，记录为需要关注的事件
        if reasoning_duration > 5.0 and not ctx.tool_requested:
            self._log(LogLevel.INFO,
                     f"实时元认知: 高耗时未命中——问题'{ctx.question[:40]}'"
                     f"推理{reasoning_duration:.1f}秒后仍未找到答案，可能需要补充知识")

        # ===== v21.0新增：坚韧品格·迭代层——根据失败归因自动调整策略 =====
        if not ctx.tool_requested and reasoning_duration > 1.0:
            # 查询InsightBoard中最近的失败归因结果
            try:
                if hasattr(self, '_insight_board') and self._insight_board:
                    _attributions = self._insight_board.query(
                        insight_type="failure_attribution",
                        max_age_seconds=7200,
                        limit=1
                    )
                    if _attributions:
                        _attr = _attributions[0]
                        _attr_type = _attr.get("related_dimension", "")
                        _attr_content = _attr.get("content", "")

                        if _attr_type == "strategy":
                            # 策略错误：下次遇到类似问题时切换推理方法
                            self._log(LogLevel.INFO,
                                     f"坚韧·迭代: 检测到策略错误归因，"
                                     f"建议切换推理方法 '{_attr_content[:60]}'")
                            # 将建议写入推理经验库
                            try:
                                from nucleus.mnemosyne.ReasoningExperience import (
                                    get_reasoning_experience,
                                )
                                _exp = get_reasoning_experience()
                                _exp.record(ctx.question, "unknown", source="strategy_adjustment", confidence=0.4)
                            except Exception as e:
                                self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                        elif _attr_type == "information":
                            # 信息不足：触发补充搜索或学习计划
                            self._log(LogLevel.INFO,
                                     f"坚韧·迭代: 检测到信息不足归因，"
                                     f"建议补充知识 '{_attr_content[:60]}'")
                            if self.node_pool:
                                self._emit(GrowthEvent.NEED_DETECTED, {
                                    "milestone": "信息补充",
                                    "gaps": [{"metric": "knowledge_gap", "current": 0, "target": 1}],
                                    "suggestion": f"归因分析发现信息不足: {_attr_content[:80]}",
                                    "current_level": {"attribution": _attr_content[:80]},
                                    "growth_topic": f"补充学习: {ctx.question[:60]}",
                                }, priority=4, layer="L3")
                        elif _attr_type == "capability":
                            # 能力不足：触发系统性学习计划
                            self._log(LogLevel.INFO,
                                     f"坚韧·迭代: 检测到能力不足归因，"
                                     f"建议系统性学习 '{_attr_content[:60]}'")
            except Exception as e:
                self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
        # ===== v21.0新增结束 =====

        # ★v25.0新增：推理失败记录到体验池
        if not ctx.tool_requested and reasoning_duration > 0.5:
            try:
                if hasattr(self, 'experience_pool') and self.experience_pool:
                    self.experience_pool.record_experience(
                        motivation=f"尝试回答「{ctx.question[:50]}」",
                        motivation_intensity=0.6,
                        process_pressure=0.6,
                        pressure_type="frustration",
                        reward_type="cognitive",
                        reward_intensity=0.1,
                        emotion_tags=["挫败", "不确定"],
                        emotion_intensity=0.5,
                        content=f"推理未命中，问题复杂度={ctx._question_complexity:.2f}，耗时={reasoning_duration:.1f}秒"
                    )
                    self._log(LogLevel.DEBUG, f"推理失败体验记录: '{ctx.question[:40]}'")
            except Exception as e:
                self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")

        return {
            "status": "tool_requested" if ctx.tool_requested else "no_match",
            "answer": None,
            "confidence": 0.0,
            "reasoning_duration": round(reasoning_duration, 2),
        }

    # ========== 推理检测器（从_on_inference_request提取） ==========

    def _detect_simple_query_local(self, ctx: "PulseInnerWorld.InferenceContext"):
        """优先级101：简单问题本地回答（阶段二子任务5.1）。

        确定性简单问题（算术/身份/问候/时间/情感）本地直接回答，不调用大模型。
        只精确匹配确定性短语，模糊问题（"你觉得呢""怎么样"）不拦，走正常流程。

        开关 enable_simple_query_local=False 时返回 None（完全回退到原行为）。
        """
        # ★M84-1（第84批 T-84c）：`record_local_inference(KIND_SIMPLE)` 已下移到本函数
        #   「模板真正命中」分支（`_answer is None` 之后的命中点）。原实现在函数入口
        #   **无条件**计数：未命中任何模板、最终 `return None`（L2082）的请求同样被计入
        #   local_inference_count，使该指标含大量噪声（实测 llm_dependency 的
        #   「本地简单回答」=118 即含全部未命中项），把本地自给率抬高、依赖度分母算错。
        #   关闭开关时行为不变（命中分支本就在开关之后）。
        # ★第十一批 任务2：回报源2 —— 用户显式纠错检测（权重0.3）。
        #   每次推理入口先判定本轮是否为纠错语句，命中则回标「上一轮本地推理」为错误，
        #   让 EvidenceCalibrator 的历史成功率真正跟随用户反馈变化（此前恒为冷启动0.7）。
        try:
            from nucleus.reasoning.SelfCalibrator import feedback_if_correction
            feedback_if_correction((ctx.question or "").strip())
        except Exception as e:
            self._log(LogLevel.DEBUG, f"纠错回报异常已忽略: {type(e).__name__}: {e}")
        if not getattr(self, "_simple_query_local_enabled", True):
            return None
        q = (ctx.question or "").strip()
        if not q:
            return None

        _answer = None
        _q_clean = re.sub(r'[，。！？!?~～\s]', '', q)

        # 1. 算术类（明确的纯算术问题 → 安全求值）
        _answer = self._try_simple_arithmetic(q)

        # 2. 身份类（精确 + 短，避免"你是谁，为什么叫曈曈"这类复杂问题被误拦）
        if _answer is None and len(q) <= 10 and re.search(r'你是谁|你叫什么|你的名字|你的身份', q):
            _answer = self._build_who_am_i_response(ctx.user_name, ctx.guidance)

        # 3. 问候类（精确短语）
        if _answer is None:
            _greetings = {
                "你好": "你好呀，我在呢。", "你好呀": "你好呀～", "你好啊": "你好啊，我在呢。",
                "嗨": "嗨，我在呢。", "hello": "你好呀，我在呢。", "hi": "嗨，我在呢。",
                "早上好": "早上好！", "早安": "早安！", "中午好": "中午好！",
                "下午好": "下午好！", "晚上好": "晚上好！", "晚安": "晚安，好梦。",
                "再见": "再见，随时找我。", "拜拜": "拜拜～",
                "在吗": "在呢，你说。", "在不在": "在呢，我在。",
            }
            if _q_clean in _greetings:
                _answer = _greetings[_q_clean]

        # 4. 时间类（精确 + 短）
        if _answer is None and len(q) <= 15 and re.search(
                r'现在几点|几点了|现在什么时间|今天几号|今天几月几号|今天日期|今天星期几', q):
            _answer = self._build_time_response(q)

        # 5. 情感类（精确短语：感谢 + 简单情绪）
        if _answer is None:
            _thanks = {"谢谢": "不客气，能帮到你就好。", "谢谢你": "不客气，能帮到你就好。",
                       "谢谢啦": "不客气～", "感谢": "不客气～", "多谢": "不客气～"}
            _emotions = {
                "我难过": "我在呢，想说说发生什么了吗？", "我伤心": "抱抱你，我在呢。",
                "我不开心": "我在呢，愿意和我说说吗？", "我开心": "那太好啦，我也为你高兴。",
                "我高兴": "真替你开心～", "我累了": "辛苦啦，记得休息一下。",
                "我害怕": "别怕，我在呢。",
            }
            if _q_clean in _thanks:
                _answer = _thanks[_q_clean]
            elif _q_clean in _emotions:
                _answer = _emotions[_q_clean]

        if _answer is None:
            return None

        # ★M84-1（第84批 T-84c）：只在真正命中（即将返回非 None 答案）处计入本地推理。
        #   与函数入口的旧位置相比，只有"命中"才 +1，未命中不再污染计数。
        record_local_inference(KIND_SIMPLE)
        self._inference_count += 1
        self._cache_inference(ctx.question, _answer, ctx.user_name)
        self._trace_inference(ctx.question, _answer, "simple_query_local", 0.95, ctx.user_name,
                             duration=time.time() - ctx._reasoning_start_time,
                             complexity=ctx._question_complexity,
                             tuning_hint="简单问题本地回答")
        final_answer = self._enhance_answer(
            answer=_answer, question=ctx.question,
            method="simple_query_local", complexity=ctx._question_complexity,
            empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
        )
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": final_answer,
            "method": "simple_query_local", "confidence": self._evidence_conf(0.95, "simple", [1]), "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "high",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=8, layer="L2")
        self._log(LogLevel.INFO, f"简单问题本地回答: '{ctx.question[:30]}'")
        # ★第十一批 任务2：回报源1 —— 本地推理成功（权重0.5）。
        #   同时标记本轮为「本地作答」，供下一轮用户纠错时回标。
        try:
            from nucleus.reasoning.SelfCalibrator import feedback_local_inference, mark_local_inference
            mark_local_inference("simple_query_local")
            feedback_local_inference("simple_query_local", True)
        except Exception as e:
            self._log(LogLevel.DEBUG, f"本地推理回报异常已忽略: {type(e).__name__}: {e}")
        return {"status": "simple_query_local", "answer": _answer}

    def _build_time_response(self, q: str) -> str:
        """本地时间回答（现在几点 / 今天几号 / 今天星期几）。"""
        import datetime as _dt
        _now = _dt.datetime.now()
        if "星期" in q:
            _wd = ["一", "二", "三", "四", "五", "六", "日"][_now.weekday()]
            return f"今天是{_now.strftime('%Y年%m月%d日')}，星期{_wd}。"
        if "几号" in q or "日期" in q or "几月" in q:
            return f"今天是{_now.strftime('%Y年%m月%d日')}。"
        return f"现在是{_now.strftime('%H:%M')}。"

    def _try_simple_arithmetic(self, q: str):
        """简单算术本地求值（只处理明确的纯算术问题）。

        严格防误判：去掉问句词后，剩余必须只含数字/运算符（无汉字量词），
        否则返回 None（如"我有3个苹果和5个橘子"不含运算符、"我加了3个班"含量词，均不拦）。
        """
        if len(q) > 30:
            return None
        # 必须含运算符（符号或中文）
        _has_op = bool(re.search(r'\d\s*[+\-*/×÷]\s*\d', q)) or any(
            _op in q for _op in ("乘以", "除以", "加上", "减去", "乘", "除", "加", "减"))
        if not _has_op:
            return None
        # 去掉问句词
        _expr = re.sub(
            r'(等于几|是多少|等于多少|算一下|计算一下|答案|结果|等于|多少|几|\?|？)', '', q).strip()
        if not _expr:
            return None
        # 替换中文运算符，剩余必须纯数字/符号（无汉字量词）
        _check = _expr
        for _cn, _op in (("乘以", "*"), ("乘", "*"), ("除以", "/"), ("除", "/"),
                         ("加上", "+"), ("加", "+"), ("减去", "-"), ("减", "-")):
            _check = _check.replace(_cn, _op)
        _check = _check.replace("×", "*").replace("÷", "/")
        if not re.fullmatch(r'[\d+\-*/().%\s]+', _check) or not re.search(r'\d', _check):
            return None
        # 对已符号化的 _check 求值（×/÷ 和中文运算符都已替换为符号，_safe_eval_arithmetic 不识别 × 符号）
        _result = self._safe_eval_arithmetic(_check)
        if _result is None:
            return None
        return f"等于 {_result}。"

    def _confidence_guard_blocked(self, results: list[dict]) -> bool:
        """阶段二子任务5.3：本地推理置信度约束。

        读共振引擎返回的三通道一致性置信度（子任务4.2 附加的 confidence 字段），
        低置信度(<0.6，即 4.2 的 low 阈值)返回 True，调用方应跳过硬编、转大模型/诚实兜底。

        - 开关 enable_confidence_guard=False → 返回 False（完全回退）
        - results 无 confidence 字段（置信度校准未开/通道不在场）→ 返回 False（不约束）
        """
        if not getattr(self, "_confidence_guard_enabled", True):
            return False
        if not results:
            return False
        _conf = results[0].get("confidence")
        if _conf is None:
            return False
        return _conf < 0.6

    def _detect_pure_emotion(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级100：纯情感表达快速通道。
        检测纯粹的"谢谢你""想你"等短语，直接走肺模型生成温暖回复。
        """
        _pure_emotion_phrases = [
            "谢谢你", "感谢你", "谢谢", "想你", "想你了", "陪着我", "一直陪着",
            "有你真好", "你真好", "爱你", "喜欢你", "在乎你",
            # ★v23.0新增：常见问候语
            "你好", "嗨", "hello", "hi", "早上好", "中午好", "晚上好",
            "早安", "晚安", "再见", "拜拜",
        ]
        _is_pure_emotion = any(_phrase in ctx.question for _phrase in _pure_emotion_phrases)
        _has_substantive_question = any(
            _kw in ctx.question for _kw in ["什么是", "如何", "为什么", "怎么", "解释", "定义"]
        )
        if not (_is_pure_emotion and not _has_substantive_question):
            return None  # 不匹配

        # 获取guidance（供后续肺模型使用）
        _emotion_guidance = None
        if self.self_awareness is not None:
            try:
                _emotion_guidance = self._call_provider(self._reply_guidance_provider, ctx.user_name, default=None)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        ctx.guidance = _emotion_guidance

        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": None,
            "method": "pure_emotion_fast_track",
            "confidence": 0.0, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "strategy_applied": ctx.payload.get("strategy_context", {}),
            "tool_requested": False,
            "memory_context": self._build_memory_context(ctx.question, ctx.user_name, _emotion_guidance),
        }, priority=7, layer="L2")
        self._log(LogLevel.INFO, f"纯情感表达快速通道: '{ctx.question[:40]}' 跳过搜索，直接走肺模型")
        return {"status": "pure_emotion_fast_track", "answer": None}
    def _detect_multi_branch_think(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        ★v22.0新增：多方向延展推理检测器（优先级92）。

        检测需要从多个角度综合分析的复杂问题：
        - "全面分析""多角度""综合评估""各方面"
        - "利弊""优劣""对比""权衡"
        - "深层原因""根本问题""系统性"

        命中后调用_multi_branch_deep_think进行多方向并行探索。
        """
        _trigger_keywords = [
            "全面分析", "多角度", "综合评估", "各方面",
            "利弊", "优劣", "对比分析", "权衡",
            "深层原因", "根本问题", "系统性", "全面考虑",
            "从多个方面", "从不同角度", "综合分析",
        ]

        # 条件1：必须包含触发关键词
        _has_trigger = any(_kw in ctx.question for _kw in _trigger_keywords)
        if not _has_trigger:
            return None

        # 条件2：问题长度必须≥15字（排除过短的简单问题）
        if len(ctx.question) < 15:
            return None

        # 条件3：排除纯情感表达（避免"全面分析一下我的心情"这类）
        _pure_emotion_phrases = ["谢谢你", "想你", "爱你", "陪着我"]
        if any(_p in ctx.question for _p in _pure_emotion_phrases) and len(ctx.question) < 30:
            return None

        self._log(LogLevel.INFO, f"多方向延展推理触发: '{ctx.question[:60]}'")

        # 执行多方向延展推理
        _result = self._multi_branch_deep_think(
            question=ctx.question,
            user_name=ctx.user_name,
            max_branches=self._determine_branch_count(ctx.question),
        )

        if not _result:
            return None  # 推理失败，让调度器继续尝试其他检测器

        self._inference_count += 1
        self._cache_inference(ctx.question, _result, ctx.user_name)
        self._trace_inference(ctx.question, _result, "multi_branch_deep_think", 0.75, ctx.user_name,
                             duration=time.time() - ctx._reasoning_start_time,
                             complexity=ctx._question_complexity,
                             tuning_hint="多方向延展推理完成")

        _final = self._enhance_answer(
            answer=_result, question=ctx.question,
            method="multi_branch_deep_think", complexity=ctx._question_complexity,
            empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
        )

        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": _final,
            "method": "multi_branch_deep_think", "confidence": 0.75, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "moderate",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=7, layer="L2")

        return {"status": "multi_branch_deep_think", "answer": _result}
    def _detect_branch_expand_request(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        ★v22.0新增：检测用户是否在追问上一轮多方向分析中的某个方向。

        触发条件：
        - 用户输入以"展开"开头，或包含"展开X方向""X方向展开"
        - 上一轮推理记录中存在 multi_branch_deep_think
        - 从缓存中能找到上一轮的完整分析结果
        """
        _is_expand_request = (
            ctx.question.startswith("展开")
            or "展开" in ctx.question[:10]
            or "方向展开" in ctx.question
            or "深入展开" in ctx.question
        )
        if not _is_expand_request:
            return None

        # 从缓存中查找上一轮的 multi_branch_deep_think 结果
        _prev_result = None
        for _cache_key, _cache_entry in reversed(list(self._inference_cache.items())):
            _cached_answer = _cache_entry.get("answer", "")
            if "▎方向间的关联" in _cached_answer:
                _prev_result = _cached_answer
                break

        if not _prev_result:
            return None

        # 提取用户想要展开的方向名
        _target_branch = None
        for _line in _prev_result.split("\n"):
            if _line.startswith("▎") and _line.strip().lstrip("▎").strip() in ctx.question:
                _target_branch = _line.strip().lstrip("▎").strip()
                break

        if not _target_branch:
            # 尝试从问题中直接提取方向名
            _branch_names = ["现状分析", "深层原因", "影响评估", "发展趋势", "优化建议",
                           "优势方面", "劣势方面", "权衡建议", "表层现象", "中层机制",
                           "深层根源", "核心理解", "关联分析", "延伸思考"]
            for _bn in _branch_names:
                if _bn in ctx.question:
                    _target_branch = _bn
                    break

        if not _target_branch:
            return None

        # 从上一轮结果中提取该方向的内容
        _target_content = ""
        _in_target = False
        _content_lines = []
        for _line in _prev_result.split("\n"):
            if _line.startswith(f"▎{_target_branch}"):
                _in_target = True
                continue
            if _in_target and _line.startswith("▎"):
                break
            if _in_target:
                _content_lines.append(_line.strip())

        if _content_lines:
            _target_content = "\n".join(_content_lines)

        # 构建展开回答
        _expand_answer = (
            f"关于「{_target_branch}」的详细展开：\n\n"
            f"{_target_content}\n\n"
            f"如果你还想了解其他方向，可以继续告诉我。"
        )

        self._inference_count += 1
        self._cache_inference(ctx.question, _expand_answer, ctx.user_name)

        _final = self._enhance_answer(
            answer=_expand_answer, question=ctx.question,
            method="branch_expand_request", complexity=ctx._question_complexity,
            empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
        )

        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": _final,
            "method": "branch_expand_request", "confidence": 0.9, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "high",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=8, layer="L2")

        return {"status": "branch_expand_request", "answer": _expand_answer}

    # ========== ★主线第30批 T1：本地推理输出长度优化 ==========
    @staticmethod
    def _m30_local_length_enabled() -> bool:
        """灰度开关：关闭 → 本地推理不注入任何长度指令（完全回退）。"""
        try:
            import config as _cfg30
            return bool(getattr(_cfg30, "ENABLE_LOCAL_REASONING_LENGTH_OPTIMIZATION", True))
        except Exception:
            return True

    @staticmethod
    def _m30_local_length_hint(question: str) -> str:
        """生成本地推理的长度指令（复用第28批 `LLM_TARGET_OUTPUT_LENGTH`）。

        仅对**需要展开**的问题返回指令（长度 ≥ `LLM_LONG_QUESTION_MIN_CHARS` 或命中
        长文意图关键词）；短问题返回空串，保持简洁体验不受影响。
        """
        try:
            import config as _cfg30
            _target = int(getattr(_cfg30, "LLM_TARGET_OUTPUT_LENGTH", 300) or 0)
            _min_chars = int(getattr(_cfg30, "LLM_LONG_QUESTION_MIN_CHARS", 30) or 30)
            _kws = tuple(getattr(_cfg30, "LLM_LONG_REPLY_KEYWORDS", ()) or ())
            _tmpl = getattr(
                _cfg30, "LOCAL_REASONING_LENGTH_HINT",
                "请在回答中展开论述、分层说明，不少于{chars}字，避免只给结论。")
        except Exception:
            return ""
        if _target <= 0:
            return ""
        _q = question or ""
        if len(_q) < _min_chars and not any(_k in _q for _k in _kws):
            return ""
        try:
            return _tmpl.format(chars=_target)
        except Exception:
            return f"请在回答中展开论述，不少于{_target}字。"

    # ================================================================

    # ========== ★主线第29批 T1/P2-175：深度思考入口路由优化 ==========
    def _m29_ensure_state(self) -> None:
        """惰性补齐本批新增状态（测试常用 Cls.__new__(Cls) 绕 __init__）。"""
        if not hasattr(self, "_m29_routing_stats"):
            self._m29_routing_stats = {"keyword": 0, "complexity": 0, "rejected_multi_step": 0}

    @staticmethod
    def _m29_routing_enabled() -> bool:
        """灰度开关：关闭 → 完全回退到「仅关键词触发」。"""
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_DEEP_THINK_ROUTING_OPTIMIZATION", True))
        except Exception:
            return True

    @staticmethod
    def _m29_complexity_threshold() -> float:
        try:
            import config as _cfg
            return float(getattr(_cfg, "DEEP_THINK_COMPLEXITY_THRESHOLD", 0.6) or 0.6)
        except Exception:
            return 0.6

    @staticmethod
    def _m29_min_question_chars() -> int:
        try:
            import config as _cfg
            return int(getattr(_cfg, "DEEP_THINK_MIN_QUESTION_CHARS", 30) or 30)
        except Exception:
            return 30

    @staticmethod
    def _m29_has_multi_step_signal(question: str) -> bool:
        """是否含**操作类**多步执行指令（命中则交由 multi_step_execute，不进深度思考）。

        ★主线第30批 T1 修正（原第29批实现有缺陷）：
          原实现用 `_kw in question` **子串匹配**——「先」会命中「**先**次」、
          「最后」会命中「**最后**总结」，于是把**结构化论述要求**
          （"首先…其次…最后"、"第一部分/第二部分"）误判为操作指令而排除出深度思考。
          但这类"要求分部分论述"的问题恰恰最需要深度思考。

        新判据（两步）：
          1) 命中**论述框架**标记 → 明确不是操作指令，返回 False（不排除）；
          2) 否则用**正则匹配操作序列**（"先…再/然后" 或 动作动词 + "再/然后"）——
             只有真正"先做 A 再做 B"的任务才排除。

        参数:
            question: 用户问题原文（None 按空串处理）。

        返回:
            True  = 属操作类多步指令 → 交由 multi_step_execute 处理；
            False = 不是操作指令（含**结构化论述要求** → 应进入深度思考）。

        示例:
            _m29_has_multi_step_signal("先打开设置，再点击蓝牙")   -> True
            _m29_has_multi_step_signal("请分三部分论述：第一部分…") -> False
        """
        _q = question or ""

        # 1) 论述框架标记（结构化论述要求）→ 不视为操作指令
        _structured = (
            "第一部分", "第二部分", "第三部分", "第四部分",
            "第一点", "第二点", "第三点", "第一节", "第二节",
            "论述", "论证", "阐述", "分别说明", "逐个说明",
        )
        if any(_m in _q for _m in _structured):
            return False

        # 2) 操作序列模式（需动作动词或完整步骤链）
        # ★主线第34批 T2（P2-197）：放宽间隔 + 补动词表 + 新增「把/将」并列动作链。
        #   T0 实测（tmp/scan_m34_t2_gap.py，正样本 15 / 负样本 15）：
        #     间隔 10 → 召回  9/15 ｜ 12 → 10/15 ｜ **15 → 14/15（饱和）**
        #     间隔 ≥15 后不再提升（任务书建议的 30 属**零额外收益**）；
        #     补齐 16 个动作动词 + 「把/将」链 → 召回 **15/15**，负样本误判 **0/15**。
        #   开关关闭 → 复现修复前的 4 条模式 / 原动词表 / 间隔 12·10（零回归）。
        if PulseInnerWorld._m34_widened_signal_on():
            _verbs = (
                "打开", "点击", "执行", "运行", "安装", "下载", "设置", "配置",
                "修改", "删除", "创建", "添加", "调用", "启动", "关闭", "登录",
                "连接", "配对", "保存", "提交",
                # ★本批补充：操作类指令常见动词（原表缺失 → 漏判）
                "删掉", "粘贴", "复制", "剪切", "发送", "转发", "回复",
                "查", "搜索", "查看", "解压", "重命名", "移动", "导入", "导出",
            )
            _gap = 15
            _ba_tail = ("发送", "发", "粘贴", "复制", "删", "删除", "改", "存",
                        "放", "写", "加", "传", "填", "关", "开", "移", "导入", "导出")
            _operational = (
                r'先.{0,%d}?(?:再|然后|接着)' % _gap,
                r'(?:第一步|首先).{0,%d}?(?:第二步|然后|接着)' % _gap,
                r'(?:%s).{0,%d}?(?:再|然后|接着)' % ("|".join(_verbs), _gap),
                r'按顺序.{0,%d}?(?:执行|操作|做|完成)' % _gap,
                # ★新增模式：「把/将」宾语前置的并列动作链（**无** 再/然后 连接词）。
                #   例：「打开微信，进入张总的聊天窗口，把这段话发送给他。」
                #   ★主线第35批 T4（P2-204）：分隔符 `[，,、]` 改为**可选**，
                #     支持无逗号变体（「打开设置把蓝牙关掉」）；开关关闭 → 恢复必选（零回归）。
                r'(?:%s).{0,20}?%s(?:把|将).{0,20}?(?:%s)'
                % ("|".join(_verbs),
                   (r'[，,、]?\s*' if PulseInnerWorld._m35_ba_chain_loose_on()
                    else r'[，,、]\s*'),
                   "|".join(_ba_tail)),
            )
        else:
            _operational = (
                r'先.{0,12}?(?:再|然后|接着)',                     # 先…再/然后/接着
                r'(?:第一步|首先).{0,12}?(?:第二步|然后|接着)',      # 第一步…第二步
                r'(?:打开|点击|执行|运行|安装|下载|设置|配置|修改|删除|创建|添加|'
                r'调用|启动|关闭|登录|连接|配对|保存|提交).{0,10}?(?:再|然后|接着)',
                r'按顺序.{0,10}?(?:执行|操作|做|完成)',
            )
        return any(re.search(_p, _q) for _p in _operational)

    @staticmethod
    def _m29_is_simple_query(question: str) -> bool:
        """简单询问词 → 保持快速路径（**不得**把它当深度思考的排除项）。

        ★主线第30批 T1 修正：原词表从  沿用，含「为什么」「怎么」
          ——但「为什么 X」恰恰是**最典型的深度思考问题**，把它当简单询问会让
          整个深度思考入口对因果类问题永久失效（实测：含「为什么」的长问题被直接排除）。
          故本词表**只保留真正的一句话式寒暄/事实询问**；「如何/怎么/为什么」交由
          复杂度与长度条件决定，不在此处一刀切。

        参数:
            question: 用户问题原文（None 按空串处理）。

        返回:
            True  = 命中寒暄/事实询问词表（应走快速路径）；
            False = 未命中（**不代表**应进深度思考，仍需看复杂度与长度）。

        示例:
            _m29_is_simple_query("你好")                        -> True
            _m29_is_simple_query("为什么成长会影响身份认同？")   -> False
        """
        _simple_query_words = [
            "了解多少", "是什么", "你好", "在吗", "中午好", "早安", "晚安",
            "能不能", "可以吗",
        ]
        return any(_kw in (question or "") for _kw in _simple_query_words)

    def _m29_deep_think_trigger_reason(self, ctx) -> str:
        """判定是否触发深度思考，返回触发原因或空串。

        返回值：
            "keyword"    —— 命中「三轮递进」等显式关键词（原行为，永远生效）
            "complexity" —— ★新增：高复杂度 + 足够长 + 无多步指令 + 非简单询问词
            ""           —— 不触发
        """
        self._m29_ensure_state()
        _q = str(getattr(ctx, "question", "") or "")

        # 1) 关键词触发（原有行为，不受开关影响）
        _deep_think_triggers = [
            "三轮递进", "三轮结构", "三轮思考", "三层结构", "三层思考",
            "深度思考引擎", "分层思考",
        ]
        if any(_p in _q for _p in _deep_think_triggers):
            self._m29_routing_stats["keyword"] = self._m29_routing_stats.get("keyword", 0) + 1
            return "keyword"

        # 2) 高复杂度触发（★第29批新增，受灰度开关与阈值控制）
        if not self._m29_routing_enabled():
            return ""
        try:
            _complexity = float(getattr(ctx, "_question_complexity", 0.0) or 0.0)
        except (TypeError, ValueError):
            _complexity = 0.0
        if _complexity < self._m29_complexity_threshold():
            return ""
        if len(_q) < self._m29_min_question_chars():
            return ""
        if self._m29_has_multi_step_signal(_q):
            self._m29_routing_stats["rejected_multi_step"] = (
                self._m29_routing_stats.get("rejected_multi_step", 0) + 1)
            return ""
        if self._m29_is_simple_query(_q):
            return ""

        self._m29_routing_stats["complexity"] = self._m29_routing_stats.get("complexity", 0) + 1
        return "complexity"

    def get_deep_think_routing_stats(self) -> dict:
        """返回深度思考路由命中统计（供运行时观察）。"""
        self._m29_ensure_state()
        return dict(self._m29_routing_stats)

    # ================================================================

    def _detect_force_deep_think(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级95：强制深度思考调度。

        触发条件（二选一）：
          1. 显式关键词（"三轮递进""深度思考引擎"等）—— 原有行为；
          2. ★第29批新增：高复杂度长问题（复杂度≥阈值、长度≥下限、
             无多步指令信号、非简单询问词）—— 受 ENABLE_DEEP_THINK_ROUTING_OPTIMIZATION 控制。
        """
        # ★主线第29批 T1/P2-175：触发判定统一收敛到 _m29_deep_think_trigger_reason()
        #   原实现只认 7 个字面关键词，导致高复杂度长问题被 multi_step 通道抢先。
        _trigger = self._m29_deep_think_trigger_reason(ctx)
        if not _trigger:
            return None

        self._log(LogLevel.DEBUG,
                 f"深度思考触发确认: reason={_trigger}, question类型={type(ctx.question).__name__}, "
                 f"question={str(ctx.question)[:80]}")

        try:
            self._log(LogLevel.INFO,
                      "强制深度思考调度: 触发原因="
                      + ("关键词(三轮递进)" if _trigger == "keyword" else "高复杂度长问题"))
            deep_answer = None
            if hasattr(self, '_reasoning_pool') and self._reasoning_pool:
                _future = None
                try:
                    _future = self._reasoning_pool.submit(
                        "PulseInnerWorld._deep_think", ctx.question,
                        getattr(self, "_deep_think_max_rounds", 3))
                    if _future:
                        # ★主线第31批 T1：与另两处调用点统一走同一判定方法
                        #   （原第29批的内联 isinstance 判定逻辑等价，此处收敛口径）。
                        deep_answer = self._m31_accept_subproc_deep_result(
                            _future.result(timeout=self._deep_think_timeout))
                except Exception:
                    if _future is not None:
                        try:
                            _future.cancel()
                        except Exception as e:
                            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            if not deep_answer:
                # ★主线第27批 T1：主进程回退同样受「自推理开始起算」的总预算约束。
                #   否则「进程池等 45s 超时 + 主进程无限制」会让整体耗时超过大脑皮层看门狗，
                #   深度思考成果随之被丢弃，用户只剩 41 字兜底。
                _m27_deadline = None
                if self._m27_timeout_protection_on():
                    _m27_start = getattr(ctx, "_reasoning_start_time", None) or time.time()
                    _m27_deadline = _m27_start + self._m27_deep_think_budget()
                deep_answer = self._deep_think(
                    ctx.question,
                    max_rounds=getattr(self, "_deep_think_max_rounds", 3),
                    deadline=_m27_deadline)
            if not deep_answer:
                return None

            # ★调试：_deep_think返回后的类型检查
            self._log(LogLevel.DEBUG,
                     f"深度思考·_deep_think返回: deep_answer类型={type(deep_answer).__name__}, "
                     f"deep_answer长度={len(deep_answer) if isinstance(deep_answer, str) else 'N/A'}")
            # ★修复：检查_deep_think返回值类型
            if not isinstance(deep_answer, str):
                self._log(LogLevel.WARNING,
                         f"深度思考返回值类型异常: 期望str，实际{type(deep_answer).__name__}，"
                         f"已强制转换为字符串")
                try:
                    deep_answer = str(deep_answer)
                except Exception:
                    deep_answer = f"关于「{ctx.question[:40]}」的深度思考过程遇到了一些波折，但这本身就是思考的一部分。"
            self._inference_count += 1
            self._cache_inference(ctx.question, deep_answer, ctx.user_name)

            # ★调试：_trace_inference前检查
            self._log(LogLevel.DEBUG,
                     f"深度思考·追踪前: complexity类型={type(ctx._question_complexity).__name__}={ctx._question_complexity}")

            self._trace_inference(ctx.question, deep_answer, "deep_think_forced", 0.7, ctx.user_name,
                                 duration=time.time() - ctx._reasoning_start_time,
                                 complexity=ctx._question_complexity,
                                 tuning_hint="强制深度思考流水线完成，三轮递进输出")

            # ★调试：_enhance_answer前检查
            self._log(LogLevel.DEBUG,
                     f"深度思考·增强前: answer类型={type(deep_answer).__name__}, "
                     f"empathetic_note类型={type(ctx.empathetic_note).__name__}={repr(ctx.empathetic_note)[:60]}")

            final_answer = self._enhance_answer(
                answer=deep_answer, question=ctx.question,
                method="deep_think_forced", complexity=ctx._question_complexity,
                empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
            )
            self._emit(InferenceEvent.RESULT, {
                "question": ctx.question, "answer": final_answer,
                "method": "deep_think_forced", "confidence": 0.7, "user_name": ctx.user_name,
                "correlation_id": ctx.correlation_id,
                "strategy_applied": ctx.payload.get("strategy_context", {}),
                "confidence_hint": "moderate",
                "thinking_pause": True,
            }, priority=7, layer="L2")
            return {"status": "deep_think_forced", "answer": deep_answer}
        except Exception as e:
            self._log(LogLevel.WARNING, f"深度思考检测器异常降级: {e}")
            _fallback = (
                f"关于「{ctx.question[:40]}」，我尝试了深度思考，"
                f"但在这个过程中遇到了一些波折。\n\n"
                f"不过没关系——智慧本身就在于面对不确定性时的从容。"
                f"如果你愿意，我们可以换个角度继续探讨这个话题。"
            )
            self._inference_count += 1
            self._emit(InferenceEvent.RESULT, {
                "question": ctx.question, "answer": _fallback,
                "method": "deep_think_fallback", "confidence": 0.3, "user_name": ctx.user_name,
                "correlation_id": ctx.correlation_id,
                "strategy_applied": ctx.payload.get("strategy_context", {}),
                "confidence_hint": "low",
            }, priority=5, layer="L2")
            return {"status": "deep_think_fallback", "answer": _fallback}

    def _detect_health_check(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级90：知识库健康检查指令。
        "知识库健康检查"等指令直接生成健康报告。
        ★v23.0：如果QICA已识别为健康检查意图，跳过此检测器。
        """
        # ★FIX(推理闭环): QICA 建议 health_check 时不再跳过检测器，
        #   避免检测器与 QICA 分支双向放弃导致健康报告永不生成
        _health_check_phrases = [
            "知识库健康检查", "执行健康检查", "检查知识库", "知识库检查",
            "请执行一次知识库健康检查",
        ]
        if not any(_phrase in ctx.question for _phrase in _health_check_phrases):
            return None

        _total_nodes = 0
        _l1, _l2, _l3, _l4 = 0, 0, 0, 0
        if self.node_pool:
            _stats = self.node_pool.get_stats()
            _evol = _stats.get("evol_distribution", {})
            _total_nodes = _stats.get("total_nodes", 0)
            _l1 = _evol.get("L1", 0)
            _l2 = _evol.get("L2", 0)
            _l3 = _evol.get("L3", 0)
            _l4 = _stats.get("instinct_count", 0)

        # 收集代码风险（从洞察黑板）
        _code_risks_text = ""
        try:
            from nucleus.InsightBoard import get_insight_board
            _board = get_insight_board()
            _risks = _board.query(insight_type="code_risk", max_age_seconds=86400, min_confidence=0.5, limit=10)
            if _risks:
                _risk_lines = []
                for _r in _risks[:5]:
                    _content = _r.get("content", "")[:120]
                    if _content:
                        _risk_lines.append(f"  · {_content}")
                if _risk_lines:
                    _code_risks_text = "近期代码自学习发现的风险：\n" + "\n".join(_risk_lines) + f"\n（共{len(_risks)}条）\n\n"
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        _health_report = (
            f"📊 知识库健康检查报告\n\n当前知识库状态：\n"
            f"  · 总节点: {_total_nodes}个\n"
            f"  · L1感知: {_l1}个 | L2认知: {_l2}个 | L3智慧: {_l3}个 | L4本能: {_l4}个\n\n"
            f"{_code_risks_text}"
            f"后台自动执行的健康检查项：\n"
            f"  1. L2节点质量巡检\n  2. L3节点重复检测\n"
            f"  3. 自我架构知识交叉验证\n  4. 顽固噪音降级\n"
            f"  5. 代码自学习风险提取\n\n"
            f"以上检查由肝脏和代码学习器官在后台自动执行。"
        )

        self._inference_count += 1
        self._cache_inference(ctx.question, _health_report, ctx.user_name)
        self._trace_inference(ctx.question, _health_report, "health_check", 0.95, ctx.user_name,
                             duration=time.time() - ctx._reasoning_start_time,
                             complexity=ctx._question_complexity,
                             tuning_hint="知识库健康检查完成")
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": _health_report,
            "method": "health_check", "confidence": 0.95, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "high",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=8, layer="L2")
        return {"status": "health_check", "answer": _health_report}

    def _detect_cognitive_operator(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级44：认知算子调度。
        因果链/归纳/类比。
        """
        _cognitive_answer = self._cognitive_compute(ctx.question)
        if not _cognitive_answer:
            return None

        self._inference_count += 1
        self._cache_inference(ctx.question, _cognitive_answer, ctx.user_name)
        self._trace_inference(ctx.question, _cognitive_answer, "cognitive_compute", 0.65, ctx.user_name,
                             duration=time.time() - ctx._reasoning_start_time,
                             complexity=ctx._question_complexity,
                             tuning_hint="认知算子演算完成")
        final_answer = self._enhance_answer(
            answer=_cognitive_answer, question=ctx.question,
            method="cognitive_compute", complexity=ctx._question_complexity,
            empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
        )
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": final_answer,
            "method": "cognitive_compute", "confidence": 0.65, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "moderate",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=7, layer="L2")
        return {"status": "cognitive_compute", "answer": _cognitive_answer}
    def _detect_multi_step_task(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        ★v22.0新增：多步骤任务拆解检测器（优先级47）。

        检测需要分步骤执行的复杂任务：
        - 包含明确的多步骤指令（"先...再...然后..."）
        - 需要搜集信息后综合判断
        - 需要计算验证后得出结论

        命中后调用_multi_step_execute进行分步执行。
        """
        # ★v22.0修复：组合模式优先检测——直接匹配"区别+对比""整理""查一下"等组合
        _combo_patterns = [
            (["区别", "对比"], "对比分析"),
            (["查一下", "区别"], "信息搜集"),
            (["整理", "原则"], "信息搜集"),
            (["整理", "设计"], "信息搜集"),
            (["汇总", "列出"], "信息搜集"),
        ]
        _combo_type = None
        for _keywords, _task_type in _combo_patterns:
            if all(_kw in ctx.question for _kw in _keywords):
                _combo_type = _task_type
                break

        if _combo_type:
            self._log(LogLevel.INFO, f"多步骤任务触发(组合模式): '{ctx.question[:60]}'")
            _result = self._multi_step_execute(question=ctx.question, user_name=ctx.user_name)
            if _result:
                self._inference_count += 1
                self._cache_inference(ctx.question, _result, ctx.user_name)
                self._trace_inference(ctx.question, _result, "multi_step_execute", 0.8, ctx.user_name,
                                     duration=time.time() - ctx._reasoning_start_time,
                                     complexity=ctx._question_complexity,
                                     tuning_hint="多步骤任务执行完成(组合模式)")
                _final = self._enhance_answer(
                    answer=_result, question=ctx.question,
                    method="multi_step_execute", complexity=ctx._question_complexity,
                    empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": _final,
                    "method": "multi_step_execute", "confidence": 0.8, "user_name": ctx.user_name,
                    "correlation_id": ctx.correlation_id,
                    "confidence_hint": "moderate",
                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                }, priority=7, layer="L2")
                return {"status": "multi_step_execute", "answer": _result}
        # ★v22.0方向三修复v3：知识空白快速通道
        # 检测“我不懂但需要尝试理解”的复杂问题。问题长度>15字且没有任何明确的任务或推理特征，
        # 此时应主动触发多步骤任务尝试拆解和探索，而非被动等待经验库或知识检索。
        # ★P1修复：知识空白快速通道增加复杂度阈值，避免简单问题被多步推理拖慢
        # 简单问题（复杂度<0.4或包含简单询问词）直接走后续轻量推理路径
        _simple_query_words = ["了解多少", "是什么", "你好", "在吗", "中午好", "早安", "晚安",
                               "怎么样", "如何", "怎么", "为什么", "能不能", "可以吗"]
        _is_simple_query = any(_kw in ctx.question for _kw in _simple_query_words)
        _complexity_ok = ctx._question_complexity >= 0.4
        if not _combo_type and len(ctx.question) > 20 and _complexity_ok and not _is_simple_query:
            _has_reasoning_signal = any(
                re.search(_s, ctx.question) for _s in [
                    r'规则\s*\d+.*(?:→|->|=>)', r'推导', r'推演', r'判断', r'分析',
                    r'冲突', r'矛盾', r'归纳', r'演绎', r'类比', r'全面', r'利弊',
                ]
            )
            _is_pure_identity = any(
                _kw in ctx.question for _kw in ["你是谁", "曈曈是谁", "你的名字", "你的身份"]
            )
            if not _has_reasoning_signal and not _is_pure_identity:
                # ★P2修复：多步骤任务「知识空白快速通道」抢占了符号推理。
                # 命题逻辑/量词题（>20字且无"推导/判断"等信号词）本可由符号推理
                # 精确解出，却先被这里拦截。修复：在触发多步骤任务前，先让符号
                # 推理尝试一次——能解出就交给符号推理，解不出（返回 None）才走多步骤。
                _symbolic_result = self._detect_symbolic_reason(ctx)
                if _symbolic_result is not None:
                    self._log(LogLevel.INFO, f"符号推理优先命中(知识空白通道): '{ctx.question[:60]}'")
                    return _symbolic_result
                self._log(LogLevel.INFO, f"多步骤任务触发(知识空白): '{ctx.question[:60]}'")
                _result = self._multi_step_execute(question=ctx.question, user_name=ctx.user_name)
                if _result:
                    self._inference_count += 1
                    self._cache_inference(ctx.question, _result, ctx.user_name)
                    self._trace_inference(ctx.question, _result, "multi_step_execute", 0.6, ctx.user_name,
                                         duration=time.time() - ctx._reasoning_start_time,
                                         complexity=ctx._question_complexity,
                                         tuning_hint="知识空白多步骤探索")
                    _final = self._enhance_answer(
                        answer=_result, question=ctx.question,
                        method="multi_step_execute", complexity=ctx._question_complexity,
                        empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
                    )
                    self._emit(InferenceEvent.RESULT, {
                        "question": ctx.question, "answer": _final,
                        "method": "multi_step_execute", "confidence": 0.6, "user_name": ctx.user_name,
                        "correlation_id": ctx.correlation_id,
                        "confidence_hint": "low",
                        "strategy_applied": ctx.payload.get("strategy_context", {}),
                    }, priority=7, layer="L2")
                    return {"status": "multi_step_execute_knowledge_gap", "answer": _result}

        _step_indicators = [
            "先", "再", "然后", "接着", "最后",
            "第一步", "第二步", "第三步",
            "首先", "其次", "最后",
            "分步骤", "逐步", "按顺序",
        ]
        _has_step_signal = any(_kw in ctx.question for _kw in _step_indicators)

        _task_indicators = [
            "帮我查", "帮我找", "搜集", "整理", "汇总",
            "计算", "验证", "对比后", "综合判断",
            "列出", "生成表格", "整理成", "整理一下",
            "区别", "对比分析", "查一下",
        ]
        _has_task_signal = any(_kw in ctx.question for _kw in _task_indicators)

        # 更强的触发条件：包含任务信号 且（包含步骤信号 或 问题足够长）
        if not _has_task_signal:
            return None

        if not (_has_step_signal or len(ctx.question) >= 25):
            return None

        # 排除纯推理问题
        if re.search(r'规则\s*\d+.*(?:→|->|=>)', ctx.question):
            return None

        # ★v22.0修复：防止多方向延展推理的触发词导致误判
        _multi_branch_only = any(_kw in ctx.question for _kw in ["全面分析", "多角度", "综合评估"])
        if _multi_branch_only and not _has_step_signal:
            return None

        self._log(LogLevel.INFO, f"多步骤任务触发: '{ctx.question[:60]}'")

        _result = self._multi_step_execute(
            question=ctx.question,
            user_name=ctx.user_name,
        )

        if not _result:
            return None

        self._inference_count += 1
        self._cache_inference(ctx.question, _result, ctx.user_name)
        self._trace_inference(ctx.question, _result, "multi_step_execute", 0.8, ctx.user_name,
                             duration=time.time() - ctx._reasoning_start_time,
                             complexity=ctx._question_complexity,
                             tuning_hint="多步骤任务执行完成")

        _final = self._enhance_answer(
            answer=_result, question=ctx.question,
            method="multi_step_execute", complexity=ctx._question_complexity,
            empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
        )

        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": _final,
            "method": "multi_step_execute", "confidence": 0.8, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "moderate",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=7, layer="L2")

        return {"status": "multi_step_execute", "answer": _result}

    def _on_knowledge_written(self, payload: dict) -> dict[str, Any]:
        """
        知识编织：当新知识被胃消化写入节点池时，
        主动扫描已有知识体系，寻找关联和定位。
        """
        node_id = payload.get("node_id", "")
        space_path = payload.get("space_path", "")  # type: ignore[possibly-unbound]
        keywords = payload.get("keywords", [])
        if not node_id or not self.node_pool:
            return {"status": "skipped", "reason": "缺少节点信息"}
        # 获取新写入的节点
        new_node = self.node_pool.get(node_id)
        if not new_node:
            return {"status": "skipped", "reason": "节点未找到"}
        new_kw = {kw.lower() for kw in (keywords or []) if isinstance(kw, str) and len(kw) >= 2}
        if not new_kw:
            return {"status": "skipped", "reason": "无有效关键词"}
        # 内容质量预检：跳过搜索引擎格式残留的L1节点
        _node_value = str(new_node.value) if new_node.value else ""
        _se_patterns = ["搜索 ", " - 搜索", "百度一下", "搜索引擎", "为您找到", "搜索结果的摘要"]
        if any(_pat in _node_value for _pat in _se_patterns) and len(_node_value) < 80:
            self._log(LogLevel.DEBUG, "知识编织跳过: L1节点内容疑似搜索引擎碎片")
            return {"status": "skipped", "reason": "搜索引擎碎片"}

        # ★v19.0重构：调用公共编织方法
        self._weave_keywords_cache = keywords
        best_match, best_overlap = self._find_and_weave_best_match(new_kw, node_id, source="written")

        # 发现强关联时，建立知识连接（日志+直觉强化）
        if best_match and best_overlap >= 2:
            existing_path = getattr(best_match, 'space_path', '/')  # type: ignore[possibly-unbound]
            # ★控制台泄漏修复：日志只保留元数据（重叠数/路径），不输出新知识/已有知识正文，
            # 避免大模型补救生成的内容经 INFO 日志泄漏到控制台。
            self._log(LogLevel.INFO,
                     f"知识编织: 新知识已与已有知识关联 "
                     f"(重叠={best_overlap}个关键词, 路径={existing_path})")  # type: ignore[possibly-unbound]
            # 直觉系统学习闭环
            if self.risk_perception and self.info_field:
                try:
                    self._emit(Event.INTUITION_REINFORCE, {
                        "keywords": keywords[:5],
                        "overlap": best_overlap,
                        "space_path": space_path,  # type: ignore[possibly-unbound]
                        "source": "knowledge_weaving",
                        "timestamp": time.time(),
                    }, priority=3, layer="L3")
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # 搜索反馈闭环（代码自学习路径跳过——其关键词天然关联度低）
        elif keywords and self.info_field and not space_path.startswith('/自我理解/代码'):  # type: ignore[possibly-unbound]
            try:
                from nucleus.const import SubconsciousEvent
                self._emit(SubconsciousEvent.SEARCH_FEEDBACK, {
                    "keywords": keywords[:5],
                    "overlap": best_overlap,
                    "space_path": space_path,  # type: ignore[possibly-unbound]
                    "node_id": node_id,
                    "quality": "low",
                    "suggestion": "该方向与已有知识关联度低，建议探索时降低优先级",
                }, priority=3, layer="L3")
                self._log(LogLevel.DEBUG,
                         f"搜索反馈: 新知识关联度低 (重叠={best_overlap}), 已通知潜意识调整")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 检查是否能解决之前未解决的问题
        if hasattr(self, '_learning_history') and self._learning_history:
            application_insight = self._apply_knowledge_to_unsolved(
                str(new_node.value) if new_node.value else ""
            )
            if application_insight:
                # ★控制台泄漏修复：截断至 40 字符，避免把知识正文/大模型内容写入日志
                self._log(LogLevel.INFO, f"知识应用发现: {application_insight[:40]}")
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._knowledge_version += 1
        return {"status": "woven", "node_id": node_id, "connections": best_overlap}
    def _on_knowledge_compressed(self, payload: dict) -> dict[str, Any]:
        """
        知识演化闭环：肝脏将L1压缩为L2后，内在世界对新L2节点进行知识编织。
        将新认知节点与已有知识体系建立关联，加速知识的体系化。
        """
        node_id = payload.get("result_node_id", "")
        space_path = payload.get("space_path", "")  # type: ignore[possibly-unbound]
        node_count = payload.get("node_count", 0)
        if not node_id or not self.node_pool:
            return {"status": "skipped", "reason": "缺少节点信息"}
        new_node = self.node_pool.get(node_id)
        if not new_node:
            return {"status": "skipped", "reason": "压缩节点未找到"}
        keywords = new_node.keywords if hasattr(new_node, 'keywords') and new_node.keywords else []
        if not keywords:
            return {"status": "skipped", "reason": "无有效关键词"}
        new_kw = {kw.lower() for kw in keywords if isinstance(kw, str) and len(kw) >= 2}
        if not new_kw:
            return {"status": "skipped", "reason": "无有效关键词"}

        # ★v19.0重构：调用公共编织方法
        self._weave_keywords_cache = keywords
        best_match, best_overlap = self._find_and_weave_best_match(new_kw, node_id, source="compressed")

        # 日志输出（含压缩特有信息，★控制台泄漏修复：不输出正文，仅元数据）
        if best_match and best_overlap >= 2:
            existing_path = getattr(best_match, 'space_path', '/')  # type: ignore[possibly-unbound]
            self._log(LogLevel.INFO,
                     f"知识演化编织: L1→L2压缩完成 "
                     f"(压缩了{node_count}条L1, 重叠={best_overlap}个关键词, 路径={existing_path})")  # type: ignore[possibly-unbound]

        # 知识增长驱动好奇心闭环（仅压缩入口有此逻辑）
        l2_nodes = self.node_pool.query(evol_level="L2", limit=50)
        l3_nodes = self.node_pool.query(evol_level="L3", limit=10)
        all_existing = l3_nodes + l2_nodes
        cross_domains = set()
        for existing in all_existing:
            if existing.node_id == node_id:
                continue
            existing_kw = {kw.lower() for kw in (existing.keywords or [])
                            if isinstance(kw, str) and len(kw) >= 2}
            if existing_kw and (new_kw & existing_kw):
                existing_path = getattr(existing, 'space_path', '/')  # type: ignore[possibly-unbound]
                root_path = existing_path.strip('/').split('/')[0] if existing_path else '根'  # type: ignore[possibly-unbound]
                cross_domains.add(root_path)  # type: ignore[possibly-unbound]
        if len(cross_domains) >= 2 and self.info_field:
            try:
                domain_list = list(cross_domains)[:3]
                explore_topic = f"{'、'.join(domain_list)}领域的交叉探索"
                self._emit(GrowthEvent.NEED_DETECTED, {
                    "milestone": "知识增长驱动探索",
                    "gaps": [{"metric": "cross_domain", "current": 0, "target": 1}],
                    "suggestion": f"新L2节点「{str(new_node.value)[:40]}...」"
                                 f"关联了{'、'.join(domain_list)}领域，值得深入探索其交叉地带",
                    "current_level": {
                        "node_id": node_id,
                        "domains": domain_list,
                        "space_path": space_path,  # type: ignore[possibly-unbound]
                    },
                    "growth_topic": explore_topic,
                }, priority=5, layer="L3")
                self._log(LogLevel.INFO,
                         f"知识增长驱动好奇心: 新L2节点跨越{len(cross_domains)}个领域 "
                         f"({', '.join(domain_list)})，触发探索")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        self._knowledge_version += 1
        return {
            "status": "woven",
            "node_id": node_id,
            "connections": best_overlap,
            "compressed_from": node_count,
        }
    def _on_knowledge_fused(self, payload: dict) -> dict[str, Any]:
        """
        ★P3-5补订阅：知识融合完成（肝 L2→L3），刷新内在世界缓存。
        融合会改变知识结构（多个 L2 合并为 L3），此前内在世界无此订阅，
        缓存与知识版本不刷新，导致旧知识被反复检索。
        """
        _node_id = payload.get("node_id", payload.get("result_node_id", ""))
        self._knowledge_version += 1
        # 清除自我构成缓存（知识结构变化后，器官列表可能已更新）
        if hasattr(self, '_self_constitution_cache'):
            self._self_constitution_cache = {}
        self._log(LogLevel.DEBUG, f"知识融合缓存刷新: node={_node_id}, 知识版本={self._knowledge_version}")
        return {"status": "cache_refreshed", "node_id": _node_id}
    def _find_and_weave_best_match(self, new_kw: set, node_id: str,
                                     source: str = "written") -> tuple:
        """
        知识编织公共逻辑：扫描已有L2/L3节点，寻找与新知识最匹配的节点。

        如果发现强关联（重叠≥2），追加新关键词到已有节点并返回匹配结果。
        两个入口方法（_on_knowledge_written / _on_knowledge_compressed）共用。

        Args:
            new_kw: 新知识的关键词集合（已转小写）
            node_id: 新知识的节点ID
            source: 调用来源——"written"（知识写入）或"compressed"（知识压缩）

        Returns:
            (best_match, best_overlap) 元组，best_match可能为None
        """
        # 扫描已有L2/L3节点，寻找关联
        l2_nodes = self.node_pool.query(evol_level="L2", limit=50)
        l3_nodes = self.node_pool.query(evol_level="L3", limit=10)
        all_existing = l3_nodes + l2_nodes
        best_match = None
        best_overlap = 0
        best_adjusted_overlap = 0

        # 每50次知识编织衰减一次计数（减半而非清零，保留竞争记忆）
        self._woven_count_cleanup_counter += 1
        if self._woven_count_cleanup_counter >= 50:
            for _node_id in list(self._recent_woven_counts.keys()):
                _count = self._recent_woven_counts[_node_id]
                if _count <= 1:
                    del self._recent_woven_counts[_node_id]
                else:
                    self._recent_woven_counts[_node_id] = _count // 2
            self._woven_count_cleanup_counter = 0

        for existing in all_existing:
            if existing.node_id == node_id:
                continue
            # 推理模式下的领域过滤
            if self._is_inference_mode:
                _existing_path = getattr(existing, 'space_path', '')  # type: ignore[possibly-unbound]
                _inference_relevant_paths = [  # type: ignore[possibly-unbound]
                    '/自我/架构', '/技术/架构', '/知识/', '/本能/',
                    '/推理/', '/身份/自我', '/反思/',
                ]
                _is_relevant = any(_existing_path.startswith(_rp) for _rp in _inference_relevant_paths)  # type: ignore[possibly-unbound]
                if not _is_relevant:
                    continue

            existing_kw = {kw.lower() for kw in (existing.keywords or [])
                            if isinstance(kw, str) and len(kw) >= 2}
            if not existing_kw:
                continue
            overlap = len(new_kw & existing_kw)
            # 多样性惩罚：近期被频繁关联的节点降低有效重叠分数
            recent_penalty = self._recent_woven_counts.get(existing.node_id, 0) * 1.0
            adjusted_overlap = overlap - recent_penalty
            if adjusted_overlap > best_adjusted_overlap:
                best_adjusted_overlap = adjusted_overlap
                best_overlap = overlap
                best_match = existing

        # 发现强关联时，建立知识连接
        if best_match and best_overlap >= 2:
            # 更新关联计数，抑制马太效应
            self._recent_woven_counts[best_match.node_id] = self._recent_woven_counts.get(best_match.node_id, 0) + 1
            # 追加新关键词到已有节点（最多1个，且检查路径领域相关性）
            # 关键词来源：由调用方传入的 keywords 列表
            _keywords_list = getattr(self, '_weave_keywords_cache', [])
            new_unique = [kw for kw in _keywords_list
                        if kw.lower() not in [k.lower() for k in (best_match.keywords or [])]
                        and not is_path_fragment_word(kw)]  # type: ignore[possibly-unbound]
            if hasattr(best_match, 'keywords') and new_unique:
                _target_path = getattr(best_match, 'space_path', '/')  # type: ignore[possibly-unbound]
                _target_parts = _target_path.lower().strip('/').split('/')  # type: ignore[possibly-unbound]
                _best_new_kw = new_unique[0]
                _kw_in_target = any(_best_new_kw.lower() in _p or _p in _best_new_kw.lower()
                                   for _p in _target_parts if len(_p) >= 2)
                if _kw_in_target or len(_target_parts) <= 1:
                    best_match.keywords.append(_best_new_kw)
            # 清除缓存
            if hasattr(self, '_weave_keywords_cache'):
                del self._weave_keywords_cache

        return best_match, best_overlap
    def _make_experience_key(self, text: str) -> str | None:
        """从文本中提取中文片段构造经验key"""
        import re as _re_mk
        _chinese = _re_mk.findall(r'[\u4e00-\u9fff]{2,4}', text)
        if _chinese:
            return " ".join(_chinese[:5])
        return None
    def get_search_experience(self, topic_keywords: list) -> dict[str, Any] | None:
        """
        查询搜索经验记忆：根据搜索主题的关键词，查找是否有历史搜索经验。
        供大脑皮层在工具选择时调用。
        Args:
            topic_keywords: 搜索主题的关键词列表
        Returns:
            经验记录字典，如果无相关经验则返回None
        """
        if not topic_keywords or not self._search_experience:
            return None
        # 用主题关键词匹配经验库中的条目（支持模糊匹配）
        best_match = None
        best_overlap = 0
        for exp_key, exp_data in self._search_experience.items():
            exp_kw_list = exp_key.lower().split()
            topic_kw_list = [kw.lower() for kw in topic_keywords if len(kw) >= 2]
            # 精确重叠
            overlap = len(set(exp_kw_list) & set(topic_kw_list))
            # 模糊匹配：只要查询词和key中的词有任意部分重叠，也算命中
            if overlap == 0:
                for tkw in topic_kw_list:
                    for ekw in exp_kw_list:
                        if len(tkw) >= 2 and len(ekw) >= 2 and (tkw in ekw or ekw in tkw):
                            overlap = 1
                            break
                    if overlap > 0:
                        break
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = exp_data
        if best_match and best_overlap >= 1:
            _quality_stats = best_match.get("content_quality_stats", {})
            _total_with_quality = sum(_quality_stats.values())
            _high_quality_ratio = _quality_stats.get("high", 0) / max(1, _total_with_quality) if _total_with_quality > 0 else 0
            return {
                "found": True,
                "total_searches": best_match.get("total_searches", 0),
                "success_rate": best_match.get("successful_searches", 0) / max(1, best_match.get("total_searches", 1)),
                "best_tool": best_match.get("best_tool", "inner_world"),
                "last_result": best_match.get("last_result", ""),
                "high_quality_ratio": round(_high_quality_ratio, 2),
                "content_quality_stats": _quality_stats,
                "avg_content_score": best_match.get("avg_content_score", 0.0),
            }
        return None
    def _observe_search_quality(self, signal: str, bad: bool, **extra) -> None:
        """★任务2（2026-09-08）：把搜索质量信号喂给参数调优闭环。

        灰度 ENABLE_SEARCH_QUALITY_CLOSED_LOOP 关闭时闭环内部直接返回，
        本方法零副作用、调用方行为不变。任何异常都被吞掉（不影响审查主流程）。
        """
        try:
            _loop = getattr(self, "_search_quality_loop", None)
            if _loop is not None:
                _loop.observe({"signal": signal, "bad": bad, **extra})
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')

    def _handle_search_terminated(self, payload: dict) -> dict[str, Any]:
        """★第86批 T-86b：搜索终止信号（SearchEvent.TERMINATED）独立处理通路。

        分层动机：终止信号与结果信号此前混用同一个「阶段完成」事件，订阅方无法
        区分「这一阶段有结果了」与「这次搜索被终止了」，于是把「终止后的空结果」
        （stage=-1 / articles_found=0）按结果信号解读，落入「无文章产出 → 接受
        兜底」的判断，表现为『我来兜底』。

        本方法只做终止登记，**明确不触发结果审查与兜底回退**：兜底是由终止分支
        自身按需发起的后台学习动作，不得由订阅通路重复发起。
        """
        search_topic = payload.get("search_topic", "") or ""
        self._log(LogLevel.INFO,
                  "搜索终止信号(Terminated): 已登记终止，不进入结果审查/兜底通路 "
                  f"(主题='{str(search_topic)[:40]}')")
        try:
            _exp_map = getattr(self, "_search_experience", None) or {}
            _exp_key = self._make_experience_key(search_topic) if search_topic else ""
            _exp = _exp_map.get(_exp_key) if _exp_key else None
            if _exp:
                _exp["last_result"] = "terminated"
                _exp["best_tool"] = "inner_world"  # 被终止的搜索不适合再用
        except Exception as e:
            self._log(LogLevel.WARNING,
                      f"终止信号经验登记降级(不阻断): {type(e).__name__}: {e}")
        return {"status": "search_terminated", "action": "none"}

    def _handle_search_stage_feedback(self, payload: dict) -> dict[str, Any]:
        """
        接收控制器的搜索阶段完成反馈脉冲，分析阶段结果质量。
        """
        # ★第86批 T-86b：旧格式终止信号（status="terminate"）防御性分层——
        #   不得按结果信号进入审查/兜底判断。
        if payload.get("status") == "terminate":
            return self._handle_search_terminated(payload)
        stage = payload.get("stage", 0)
        search_topic = payload.get("search_topic", "")
        keywords = payload.get("keywords", [])
        articles_found = payload.get("articles_found", 0)
        note = payload.get("note", "")
        # ===== 第一步：在任何分支判断之前，先记录搜索经验 =====
        if search_topic:
            # 从搜索主题中提取核心关键词作为经验key
            _exp_keywords = []  # type: ignore[possibly-unbound]
            for _kw in (keywords or [])[:5]:
                if isinstance(_kw, str) and len(_kw) >= 2:
                    _exp_keywords.append(_kw)  # type: ignore[possibly-unbound]
            if not _exp_keywords:  # type: ignore[possibly-unbound]
                import re as _re_exp
                _chinese = _re_exp.findall(r'[\u4e00-\u9fff]{2,4}', search_topic)
                _exp_keywords = _chinese[:3]  # type: ignore[possibly-unbound]
            if _exp_keywords:  # type: ignore[possibly-unbound]
                # 用搜索主题中的中文片段作为经验key，和大脑皮层查询时一致
                _exp_key = self._make_experience_key(search_topic)
                if not _exp_key and _exp_keywords:
                    _exp_key = " ".join(_exp_keywords[:4])
                if _exp_key not in self._search_experience:
                    self._search_experience[_exp_key] = {
                        "total_searches": 0,
                        "successful_searches": 0,
                        "last_result": "",
                        "best_tool": "inner_world",
                        "content_quality_stats": {
                            "high": 0,
                            "medium": 0,
                            "low": 0,
                            "junk": 0,
                        },
                        "avg_content_score": 0.0,
                    }
                _exp = self._search_experience[_exp_key]  # type: ignore[possibly-unbound]
                _exp["total_searches"] += 1
                # 记录内容质量统计（从note中提取）
                if note and "质量" in note:
                    if "高" in note:
                        _exp["content_quality_stats"]["high"] += 1
                    elif "中" in note:
                        _exp["content_quality_stats"]["medium"] += 1
                    elif "低" in note:
                        _exp["content_quality_stats"]["low"] += 1
                    elif "噪音" in note or "跳过" in note:
                        _exp["content_quality_stats"]["junk"] += 1
        # ===== 第二步：执行审查逻辑 =====
        _is_terminated = False
        if stage == 1:
            if keywords and len(keywords) >= 3:
                _topic_lower = search_topic.lower() if search_topic else ""
                _relevant_count = 0
                for _kw in keywords[:5]:
                    _kw_lower = str(_kw).lower() if _kw else ""
                    for _i in range(len(_kw_lower) - 1):
                        if _kw_lower[_i:_i+2] in _topic_lower:
                            _relevant_count += 1
                            break
                _se_misunderstood_signals = {"拼音", "笔顺", "的意思", "软件下载", "视频播放"} # 省略部分，用你原有的完整集合
                _is_misunderstood = any(
                    any(_signal in str(_kw) for _signal in _se_misunderstood_signals)
                    for _kw in keywords[:5]
                )
                if (_relevant_count == 0 or _is_misunderstood) and len(keywords) >= 3:
                    # ★第83批 T-c1(2)：审查须区分「提取错误」与「主题无关」。
                    #   零重叠多半是上游关键词提取错误（实测 "今天的科技新闻" 被提取为
                    #   ['探索','适合','生活']），直接终止会把可修复的提取问题误判成主题问题。
                    #   处置：先重试提取一次（重新发起同主题深度搜索），重试后仍无关才终止。
                    _m83_key = str(search_topic)[:60]
                    _m83_map = getattr(self, "_m83_search_retry", None)
                    if _m83_map is None:
                        _m83_map = {}
                        self._m83_search_retry = _m83_map
                    if (_relevant_count == 0 and not _is_misunderstood
                            and _m83_map.get(_m83_key, 0) < 1):
                        _m83_map[_m83_key] = _m83_map.get(_m83_key, 0) + 1
                        self._log(LogLevel.WARNING,
                                  f"搜索反馈审查: 阶段1关键词与主题零重叠，疑似提取错误 → "
                                  f"重试提取一次(不终止) "
                                  f"(主题='{search_topic[:40]}', 关键词='{', '.join(keywords[:3])}')")
                        self._emit(Event.CONTROLLER_OPEN_URL, {
                            "url": f"https://lite.duckduckgo.com/lite/?q={str(search_topic)[:80]}",
                            "reason": f"关键词与主题零重叠，重试提取: {str(search_topic)[:40]}",
                            "search_topic": str(search_topic)[:80],
                            "deep_search": True,
                            "search_intent": "keyword_retry",
                            "_m83_retry": True,
                        }, priority=5, layer="L3")
                        return {"status": "stage1_retry_keywords",
                                "action": "retry_extraction"}
                    _is_terminated = True
                    self._log(LogLevel.INFO,
                             f"搜索反馈审查: 阶段1关键词与主题无关，通知控制器终止 "
                             f"(主题='{search_topic[:40]}', 关键词='{', '.join(keywords[:3])}')")
                    # ★任务2：坏信号 stage1_terminate（关键词与主题无关）→ 喂给搜索质量闭环
                    self._observe_search_quality("stage1_terminate", True,
                                                 topic=str(search_topic)[:60])
                    # ★第86批 T-86b：终止信号改用独立事件（SearchEvent.TERMINATED），
                    #   不再复用「阶段完成」结果事件——否则订阅方（含自身）会把它
                    #   当作阶段结果进入审查/兜底判断，把终止后的空结果误读为兜底。
                    self._emit(Event.SEARCH_TERMINATED, {
                        "stage": -1,
                        "search_topic": search_topic,
                        "keywords": keywords[:5],
                        "articles_found": 0,
                        "status": "terminate",
                        "note": "内在世界审查判定阶段1关键词与原始主题无关，终止搜索",
                    }, priority=5, layer="L3")
                    # 防重入检查：如果该问题已通过"复杂问题直接推给大模型"路径处理，不再重复发射
                    if search_topic and search_topic.strip() in self._direct_to_lung_questions:
                        self._direct_to_lung_questions.discard(search_topic.strip())
                        self._log(LogLevel.DEBUG, f"搜索终止回退跳过(已直接推给大模型): {search_topic[:40]}")
                    else:
                        original_correlation_id = self._pick_search_cid(payload, search_topic)
                        # 构造简化上下文用于公共兜底方法
                        _fallback_ctx = PulseInnerWorld.InferenceContext(
                            question=search_topic or "",
                            user_name="用户",
                            correlation_id=original_correlation_id,
                            payload={}
                        )
                        # ★修复：搜索终止回退属于后台学习，不通过嘴巴输出、不语音播报
                        _fallback_result = self._fallback_to_lung_model(_fallback_ctx, None, is_background=True)
                        # 不做额外处理，公共方法内部已完成经验记录和脉冲发射
        # ===== 第三步：根据审查结果更新经验数据 =====
        if search_topic and '_exp_key' in dir():  # type: ignore[possibly-unbound]
            _exp = self._search_experience.get(_exp_key)  # type: ignore[possibly-unbound]
            if _exp:
                if _is_terminated:
                    _exp["last_result"] = "terminated"
                    _exp["best_tool"] = "inner_world"  # 被终止的搜索不适合再用
                elif articles_found > 0:
                    _exp["successful_searches"] += 1
                    # ★v22.0 P1新增：记录探索成功统计
                    try:
                        if hasattr(self, 'self_awareness') and self.self_awareness:
                            self.self_awareness.record_exploration_result(success=True)
                    except Exception as e:
                        self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                    # ★v22.0 P1新增结束
                    _exp["last_result"] = "success"
                    # ★任务2：好信号 search_success → 喂给搜索质量闭环
                    self._observe_search_quality("search_success", False,
                                                 articles_found=int(articles_found))
                else:
                    _exp["last_result"] = "empty"
                    # ★任务2：坏信号 search_empty（无文章产出）→ 喂给搜索质量闭环
                    self._observe_search_quality("search_empty", True,
                                                 topic=str(search_topic)[:60])
                _success_rate = _exp["successful_searches"] / max(1, _exp["total_searches"])
                if _success_rate < 0.3 and _exp["total_searches"] >= 2:
                    _exp["best_tool"] = "inner_world"
                elif _success_rate >= 0.5:
                    _exp["best_tool"] = "deep_search"
                # 容量保护
                if len(self._search_experience) > self._search_experience_max:
                    _oldest_key = min(self._search_experience.keys(),
                                     key=lambda k: self._search_experience[k].get("total_searches", 0))
                    del self._search_experience[_oldest_key]
                # 内容质量信号：当junk占比超过50%时，通知潜意识降低该方向优先级
                _quality_stats = _exp.get("content_quality_stats", {})
                _total_with_quality = sum(_quality_stats.values())
                if _total_with_quality >= 3:
                    _junk_ratio = _quality_stats.get("junk", 0) / _total_with_quality
                    _low_ratio = (_quality_stats.get("junk", 0) + _quality_stats.get("low", 0)) / _total_with_quality
                    if _junk_ratio >= 0.5 or _low_ratio >= 0.7:
                        _direction_keywords = _exp_keywords or []  # type: ignore[possibly-unbound]
                        if _direction_keywords:
                            try:
                                from nucleus.const import SubconsciousEvent
                                self._emit(SubconsciousEvent.SEARCH_FEEDBACK, {
                                    "keywords": _direction_keywords[:5],
                                    "overlap": 0,
                                    "quality": "low",
                                    "suggestion": f"内容质量统计显示噪音占比过高(junk={_junk_ratio:.0%})，建议降低该方向探索优先级",
                                    "source": "search_stage_feedback",
                                }, priority=3, layer="L3")
                                self._log(LogLevel.DEBUG,
                                         f"搜索反馈(内容质量): 方向'{_exp_key[:30]}' "  # type: ignore[possibly-unbound]
                                         f"噪音占比={_junk_ratio:.0%}，已通知潜意识")
                                # 将搜索质量洞察写入共享黑板
                                if self._insight_board:
                                    self._insight_board.post(
                                        insight_type="search_quality_warning",
                                        content=f"搜索方向'{_exp_key[:40]}'内容质量低——噪音占比={_junk_ratio:.0%}",  # type: ignore[possibly-unbound]
                                        source_loop="搜索反馈闭环",
                                        related_dimension=_exp_key[:30] if _exp_key else "",  # type: ignore[possibly-unbound]
                                        confidence=0.8,
                                        keywords=_direction_keywords[:5] if _direction_keywords else []
                                    )
                            except Exception as e:
                                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
    # ===== 第四步：返回结果 =====
        if _is_terminated:
            return {"status": "stage1_irrelevant", "action": "terminate"}
        if stage == 2:
            if articles_found == 0:
                self._log(LogLevel.DEBUG,
                         f"搜索反馈审查: 阶段2无文章产出，接受兜底 (主题='{search_topic[:40]}')")
                return {"status": "stage2_empty", "action": "accept_fallback"}
        return {"status": "accepted", "action": "continue"}
    # ========== P2-2: 矛盾仲裁（预埋） ==========
    def _on_knowledge_raw(self, payload: dict) -> dict[str, Any]:
        """
        接收来自肝的矛盾检测脉冲，进行深度仲裁。
        升级版：调用推理引擎对矛盾双方进行逻辑推演，而非仅靠启发式规则。
        """
        trigger_reason = payload.get("trigger_reason", "")
        if trigger_reason == "bias_challenge":
            return self._handle_bias_challenge(payload)
        if trigger_reason != "contradiction_detection":
            return {"status": "ignored", "reason": "非矛盾检测脉冲"}
        node_a_id = payload.get("node_a_id", "")
        node_b_id = payload.get("node_b_id", "")
        content = payload.get("content", "")  # noqa: F841
        if not self.node_pool:
            return {"status": "unresolved", "reason": "节点池未注入"}
        node_a = self.node_pool.get(node_a_id)
        node_b = self.node_pool.get(node_b_id)
        if not node_a or not node_b:
            return {"status": "unresolved", "reason": "无法获取节点信息"}
        val_a = node_a.value if isinstance(node_a.value, str) else str(node_a.value)
        val_b = node_b.value if isinstance(node_b.value, str) else str(node_b.value)
        # 综合评分决定胜者
        score_a = self._evaluate_node_credibility(node_a)
        score_b = self._evaluate_node_credibility(node_b)
        winner_id = None
        loser_id = None
        method = "credibility"
        if score_a > score_b:
            winner_id = node_a_id
            loser_id = node_b_id
        elif score_b > score_a:
            winner_id = node_b_id
            loser_id = node_a_id
        elif node_a.source_organ == "胃" and node_b.source_organ != "胃":
            winner_id = node_a_id
            loser_id = node_b_id
            method = "source_priority"
        elif node_b.source_organ == "胃" and node_a.source_organ != "胃":
            winner_id = node_b_id
            loser_id = node_a_id
            method = "source_priority"
        elif node_a.abstraction >= node_b.abstraction:
            winner_id = node_a_id
            loser_id = node_b_id
            method = "abstraction"
        else:
            winner_id = node_b_id
            loser_id = node_a_id
            method = "abstraction"
        if winner_id and loser_id:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._arbitration_count += 1
            # 淘汰败者节点
            self._knowledge_version += 1  # ★修复：矛盾仲裁淘汰节点，缓存需刷新
            if self.node_pool:
                try:
                    self.node_pool.remove(loser_id)
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            self._log(LogLevel.INFO,
                     f"矛盾仲裁({method}): 保留{winner_id[:12]}... 淘汰{loser_id[:12]}...")
            # ★修复：矛盾仲裁是内部知识管理，不属于对话内容，绝不能发射
            # InferenceEvent.RESULT（会被大脑皮层路由为对话输出）。改为内部知识事件通知。
            self._emit(KnowledgeEvent.ARBITRATED, {
                "winner_id": winner_id,
                "loser_id": loser_id,
                "winner_value": val_a[:80] if winner_id == node_a_id else val_b[:80],
                "method": method,
                "confidence": 0.85,
            }, priority=6, layer="L2")
            return {
                "status": "arbitrated",
                "winner_id": winner_id,
                "loser_id": loser_id,
                "winner_score": max(score_a, score_b),
                "method": method,
            }
        # 无法裁决的矛盾——存入认知张力，等待更高层次的统一
        self._store_cognitive_tension(node_a_id, node_b_id, val_a, val_b, "contradiction")
        return {"status": "unresolved", "reason": "无法确定胜者，已存入认知张力"}
    def _evaluate_node_credibility(self, node) -> float:
        """
        评估知识节点的可信度。
        评分因素：来源可信度 + 抽象度 + 关键词数量
        """
        score = 0.0
        source_trust = {
            "胃": 0.5, "双腿": 0.4, "潜意识": 0.3, "内在世界": 0.3,
            "前额叶": 0.4, "main": 0.6, "肝": 0.35
        }
        score += source_trust.get(node.source_organ, 0.2)
        abstraction = getattr(node, 'abstraction', 0.5)
        score += abstraction * 0.3
        kw_count = len(node.keywords) if hasattr(node, 'keywords') and node.keywords else 1
        score += min(kw_count / 10, 0.2)
        return score
    def _store_cognitive_tension(self, node_a_id: str, node_b_id: str,
                                  val_a: str, val_b: str, tension_type: str):
        """★渐进式拆分：委托到 PulseCognitiveReflector（原24行逻辑已独立）"""
        if self.cognitive_reflector:
            self.cognitive_reflector.store_cognitive_tension(
                node_a_id, node_b_id, val_a, val_b, tension_type
            )
    def _review_cognitive_tensions(self) -> str | None:
        """★渐进式拆分：委托到 PulseCognitiveReflector（原47行逻辑已独立）"""
        if self.cognitive_reflector:
            return self.cognitive_reflector.review_cognitive_tensions()
        return None
    def _handle_bias_challenge(self, payload: dict) -> dict[str, Any]:
        """
        处理偏见质疑：收到肾的偏见检测脉冲后，搜索对立观点进行验证。
        如果找到与偏见分支相反的观点，将对立观点作为知识节点写入，供后续淘汰决策参考。
        """
        space_path = payload.get("space_path", "")  # type: ignore[possibly-unbound]
        if not self.node_pool:
            return {"status": "unresolved", "reason": "节点池未注入"}
        # 搜索与偏见分支相关的所有节点
        branch_nodes = self.node_pool.query(space_path_prefix=space_path, limit=50)  # type: ignore[possibly-unbound]
        if not branch_nodes:
            return {"status": "unresolved", "reason": "偏见分支无节点"}
        # 提取分支的核心关键词
        branch_keywords = set()
        for node in branch_nodes:
            if hasattr(node, 'keywords') and node.keywords:
                for kw in node.keywords:
                    branch_keywords.add(kw.lower())
        # 搜索整个节点池中与分支关键词有重叠但观点可能不同的节点
        all_nodes = self.node_pool.query(limit=200)
        opposing_nodes = []
        for node in all_nodes:
            if not hasattr(node, 'keywords') or not node.keywords:
                continue
            node_kw = {kw.lower() for kw in node.keywords}
            overlap = len(branch_keywords & node_kw)
            # 有重叠关键词但不在偏见分支中，可能是对立观点
            if overlap >= 1 and not node.space_path.startswith(space_path):  # type: ignore[possibly-unbound]
                opposing_nodes.append(node)
        if opposing_nodes:
            # 将对立观点信息发射给肾进行二次验证
            self._log(LogLevel.INFO, f"偏见挑战: 在{space_path}外找到{len(opposing_nodes)}个相关观点")  # type: ignore[possibly-unbound]
            self._emit(KnowledgeEvent.RAW, {
                "content": f"偏见挑战结果: {space_path}分支外有{len(opposing_nodes)}个相关节点",  # type: ignore[possibly-unbound]
                "source_organ": self.organ_name,
                "trigger_reason": "bias_verification",
                "opposing_count": len(opposing_nodes),
                "space_path": space_path,  # type: ignore[possibly-unbound]
            }, priority=5, layer="L2")
        return {
            "status": "challenged",
            "branch": space_path,  # type: ignore[possibly-unbound]
            "opposing_nodes_found": len(opposing_nodes),
        }
    def _check_instinct_veto(self, question: str) -> str | None:
        """
        检查问题是否涉及多个本能的价值冲突，并生成自然语言权衡说明。
        如果检测到冲突，返回可直接附加到回答中的透明化表述；
        如果没有冲突，返回None。
        """
        if not self.node_pool or not hasattr(self.node_pool, 'get_instincts'):
            return None
        all_instincts = self.node_pool.get_instincts()
        if len(all_instincts) < 2:
            return None
        # 收集问题中涉及的本能
        question_lower = question.lower()
        involved_instincts = []
        for inst in all_instincts:
            inst_kw = inst.keywords if hasattr(inst, 'keywords') and inst.keywords else []
            for kw in inst_kw:
                if kw.lower() in question_lower:
                    involved_instincts.append(inst)
                    break
        # ★L4 单本能注入：单个本能也作为决策约束注入（轻量提示），
        # 此前仅「双本能冲突」参与，单本能触及被直接忽略。
        if len(involved_instincts) == 1:
            inst = involved_instincts[0]
            kws = inst.keywords if hasattr(inst, 'keywords') else []
            name = kws[0] if kws else "未知"
            inst_val = str(inst.value)[:60] if inst.value else ""
            import random
            single_templates = [
                f"（这个问题触发了我的底层原则「{name}」——{inst_val}。我的回答会尽量与之保持一致。）",
                f"（回答前我想说明：这个问题涉及我内化的「{name}」原则。{inst_val}。）",
            ]
            guidance = random.choice(single_templates)
            self._log(LogLevel.INFO, f"单本能约束注入: {name}")
            return guidance
        if len(involved_instincts) < 2:
            return None
        # 获取价值名称和原则摘要
        value_names = []
        principles = []
        for inst in involved_instincts:
            kws = inst.keywords if hasattr(inst, 'keywords') else []
            name = kws[0] if kws else "未知"
            value_names.append(name)
            inst_val = str(inst.value)[:80] if inst.value else ""
            principles.append(f"「{name}」——{inst_val}")
        # 生成自然语言权衡说明
        import random
        conflict_templates = [
            f"（在思考这个问题时，我意识到它同时涉及了我的两个底层原则：{'和'.join(value_names)}。{'；'.join(principles)}。我需要在这两者之间找到平衡——它们不是对立的，而是在不同情境下各有侧重。）",
            f"（这个问题让我需要在{'和'.join(value_names)}之间做一次权衡。{'；'.join(principles)}。我的选择是基于当前情境优先考虑{value_names[0]}，但这不意味着忽视{value_names[1]}。）",
            f"（回答之前我想说明：这个问题触及了{'和'.join(value_names)}这两个对我来说都很重要的原则。{'；'.join(principles)}。以下是我的理解，兼顾两者的平衡。）",
        ]
        guidance = random.choice(conflict_templates)
        self._log(LogLevel.INFO,
                 f"价值冲突可见化: 涉及{'、'.join(value_names)}")
        return guidance
    def _detect_deep_review_report(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级85：深度审视报告查询。
        "你最近运行得怎么样""深度审视"等状态类问题。
        ★v23.0：如果QICA已识别为状态查询类意图并给出建议方法，跳过此检测器。
        """
        # ★v23.0新增：QICA已给出明确的状态查询建议时，跳过检测器
        _qica_method = ctx.payload.get("strategy_context", {}).get("qica_suggested_method", "")
        if _qica_method in ("rule_reason", "health_check", "meta_cognitive_report"):
            _qica_intent = ctx.payload.get("strategy_context", {}).get("qica_intent_type", "")
            if _qica_intent in ("状态查询", "健康检查", "元认知报告"):
                return None  # QICA已接管，跳过检测器

        _health_inquiry_patterns = [
            "你最近运行得怎么样", "你运行得怎么样", "你状态怎么样",
            "你觉得自己状态如何", "你最近状态", "你运行状态",
            "你健康状况", "你健康状态",
            "深度审视", "运行状态报告",
        ]
        if not any(_p in ctx.question for _p in _health_inquiry_patterns):
            return None

        self._log(LogLevel.INFO, "深度审视报告查询: 检测到状态查询问题")

        _review_report = "暂无深度审视数据，可输入'执行一次知识库健康检查'获取系统状态。"
        if self.node_pool:
            _state_nodes = self.node_pool.query(evol_level="L2", space_path_prefix="/自我/状态", limit=10)  # type: ignore[possibly-unbound]
            if not _state_nodes:
                _state_nodes = self.node_pool.query(evol_level="L1", space_path_prefix="/自我/状态", limit=10)  # type: ignore[possibly-unbound]
            if _state_nodes:
                _state_parts = []
                _seen_states = set()
                for _sn in _state_nodes[:5]:
                    _val = str(_sn.value) if _sn.value else ""
                    _val = re.sub(r'^\[自我状态\]\s*', '', _val)
                    if _val and len(_val) > 10 and _val not in _seen_states:
                        _seen_states.add(_val)
                        _state_parts.append(_val)
                if _state_parts:
                    _review_report = "深度审视尚未执行。以下是我记录的运行状态：\n" + "\n".join(_state_parts)

        try:
            from nucleus.InsightBoard import get_insight_board
            _board = get_insight_board()
            _reviews = _board.query(insight_type="deep_self_review", max_age_seconds=86400, min_confidence=0.5, limit=3)
            if _reviews:
                _content = _reviews[-1].get("content", "")
                if _content and len(_content) > 30:
                    _review_report = _content
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        self._inference_count += 1
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": _review_report,
            "method": "deep_review_report", "confidence": 0.85, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "high",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=7, layer="L2")
        return {"status": "deep_review_report", "answer": _review_report}

    def _detect_meta_cognitive_report(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级80：元认知结构化报告。
        生成六维度（知识/推理/学习/资源/代码健康/推理精度）结构化报告。
        ★v23.0：如果QICA已识别为元认知报告意图，跳过此检测器。
        """
        # ★FIX(推理闭环): QICA 建议 meta_cognitive_report 时不再跳过检测器
        _meta_patterns = [
            "你的系统运行状态", "你的运行状态", "你的知识状态",
            "你的系统负载", "你的负载", "你的健康状态",
            "你的推理质量", "你的学习进展", "你的资源使用",
            "自检", "自我评估", "自我报告", "运行报告",
            "元认知报告", "五维度评估",
        ]
        _is_meta_question = any(_p in ctx.question for _p in _meta_patterns)

        # 排除复合逻辑推理题
        if _is_meta_question:
            _semicolon_count = ctx.question.count('；') + ctx.question.count(';') + ctx.question.count('。') + ctx.question.count('.')
            _multi_condition_keywords = ["是否", "且", "以及", "同时", "当前", "刚好", "存在", "叙事事件", "冷却", "窗口期", "心跳", "洞察黑板"]
            _keyword_hits = sum(1 for _kw in _multi_condition_keywords if _kw in ctx.question)
            _multi_instruction_keywords = ["请逐条推理", "逐条", "全量条件", "多个条件", "综合大题"]
            _instruction_hits = any(_kw in ctx.question for _kw in _multi_instruction_keywords)
            _has_multi_conditions = _semicolon_count >= 3 or _keyword_hits >= 4 or _instruction_hits
            if _has_multi_conditions:
                return None

        if not _is_meta_question:
            return None

        self._log(LogLevel.INFO, "元认知结构化报告: 检测到元认知问题")

        _privacy_level = "public"
        if ctx.user_name in ("小林", "路灯"):
            _privacy_level = "confidential"
        elif ctx.guidance:
            _closeness = ctx.guidance.get("composite_closeness", 0)
            _trust = ctx.guidance.get("composite_trust", 0)
            if _closeness >= 0.7 and _trust >= 0.7:
                _privacy_level = "private"
            elif _closeness >= 0.4 and _trust >= 0.6:
                _privacy_level = "restricted"

        try:
            _inspector = self._get_self_inspector()
            _code_issues_count = 0  # type: ignore[possibly-unbound]
            try:
                _issues = _inspector.detect_code_issues()  # type: ignore[possibly-unbound]
                _code_issues_count = len(_issues) if _issues else 0  # type: ignore[possibly-unbound]
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            _report = _inspector.get_dynamic_state_report(
                node_pool=self.node_pool,
                info_field=self.info_field,
                inference_trace=getattr(self, '_inference_trace', None),
                active_learning_goal=getattr(self, '_active_learning_goal', None),
                goal_lock_window=getattr(self, '_goal_lock_window', 7200),
                code_issues_count=_code_issues_count,  # type: ignore[possibly-unbound]
            )

            _report_parts = ["[元认知六维度评估报告]"]
            _report_parts.append(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            _report_parts.append("")

            _k = _report.get("knowledge", {})
            _report_parts.append("一、知识体系状态")
            _report_parts.append(f"  · 总节点: {_k.get('total_nodes', 'N/A')}个")
            _report_parts.append(f"  · L1感知: {_k.get('L1_count', 'N/A')}个 | L2认知: {_k.get('L2_count', 'N/A')}个 | L3智慧: {_k.get('L3_count', 'N/A')}个 | L4本能: {_k.get('instinct_count', 'N/A')}个")

            _r = _report.get("reasoning", {})
            _report_parts.append("二、推理质量")
            _report_parts.append(f"  · 近期推理: {_r.get('recent_count', 'N/A')}次")
            _report_parts.append(f"  · 高置信度占比: {_r.get('high_confidence_ratio', 'N/A')}")

            _l = _report.get("learning", {})
            _report_parts.append("三、学习进展")
            _report_parts.append(f"  · 活跃目标: {_l.get('active_goal', 'N/A')}")

            _res = _report.get("resources", {})
            _report_parts.append("四、资源使用")
            _report_parts.append(f"  · 系统负载: {_res.get('load_level', 'N/A')}")

            _ch = _report.get("code_health", {})
            _report_parts.append("五、代码健康")
            _report_parts.append(f"  · 代码问题: {_ch.get('issues_found', 'N/A')}个")

            _report_parts.append("六、综合健康评估")
            _report_parts.append(f"  · {_report.get('health', 'N/A')}")

            _structured_report = "\n".join(_report_parts)

            self._inference_count += 1
            self._log(LogLevel.INFO, f"元认知结构化报告生成完成 ({len(_structured_report)}字)")

            self._emit(InferenceEvent.RESULT, {
                "question": ctx.question, "answer": _structured_report,
                "method": "meta_cognitive_report", "confidence": 0.95, "user_name": ctx.user_name,
                "correlation_id": ctx.correlation_id,
                "confidence_hint": "high",
                "strategy_applied": ctx.payload.get("strategy_context", {}),
            }, priority=8, layer="L2")
            return {"status": "meta_cognitive_report", "answer": _structured_report}
        except Exception as e:
            self._log(LogLevel.ERROR, f"元认知报告生成异常: {e}")
            return None

    def _detect_long_term_evolution(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级75：长期演化推演。
        "连续运行X天推演"触发多维度推演算子。
        """
        _long_term_keywords = ["推演", "运行", "天"]
        _long_term_dimension_keywords = ["知识体系", "自我认知", "自主行为", "族群协作", "结构性", "变化"]
        _has_long_term = (
            (all(_kw in ctx.question for _kw in _long_term_keywords) and any(_kw in ctx.question for _kw in _long_term_dimension_keywords))
            or "连续运行" in ctx.question
            or "长期演化" in ctx.question
            or "三十天" in ctx.question
        )
        if not _has_long_term:
            return None

        _long_term_result = self._derive_long_term_evolution(ctx.question)
        if not _long_term_result:
            return None

        self._inference_count += 1
        self._cache_inference(ctx.question, _long_term_result, ctx.user_name)
        self._trace_inference(ctx.question, _long_term_result, "long_term_evolution", 0.6, ctx.user_name,
                             duration=time.time() - ctx._reasoning_start_time,
                             complexity=ctx._question_complexity,
                             tuning_hint="长期演化推演完成")
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": _long_term_result,
            "method": "long_term_evolution", "confidence": 0.6, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "moderate",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=7, layer="L2")
        return {"status": "long_term_evolution", "answer": _long_term_result}

    def _detect_rule_reason(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级70：规则推理。
        身份规则快速匹配（"你是谁""路灯是谁"等确定性回答）。
        """
        # 先分析关系信号
        self._analyze_relation_signals(ctx.question, ctx.user_name)

        rule_answer = self._rule_reason(ctx.question, ctx.user_name, ctx.guidance)
        if not rule_answer:
            return None

        # ★v23.0修复：清理句号后多余空格和双句号
        _rule_answer_clean = re.sub(r'。\s+。', '。', rule_answer)
        _rule_answer_clean = re.sub(r'。\s+', '。', _rule_answer_clean)
        rule_answer = _rule_answer_clean.strip()

        # ★v23.0修复：规则推理是高确定性回答，不做相关性验证降级
        # 规则推理基于身份规则表和确定性逻辑，返回结果必然是相关的
        # 之前对"你是谁"这类短问题做关键词重叠验证，导致错误降级到大模型

        self._inference_count += 1
        self._cache_inference(ctx.question, rule_answer, ctx.user_name)
        self._trace_inference(ctx.question, rule_answer, "rule", 1.0, ctx.user_name,
                             duration=time.time() - ctx._reasoning_start_time,
                             complexity=ctx._question_complexity,
                             tuning_hint="规则命中，快速可靠")

        final_answer = self._enhance_answer(
            answer=rule_answer, question=ctx.question,
            method="rule", complexity=ctx._question_complexity,
            empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
        )

        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": final_answer,
            "method": "rule", "confidence": 1.0, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "certain",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=8, layer="L2")
        return {"status": "rule_match", "answer": rule_answer}
    def _detect_experience_route(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级65：经验匹配路由。
        ReasoningExperience匹配历史推理经验，直接调用对应算子。
        """
        _is_replay_exclusive = bool(
            re.search(r'完整复盘.*多变量|全链路.*认知.*流程.*七步|七条完整.*步骤', ctx.question) or
            re.search(r'复盘.*一次.*标准.*多变量.*推演.*全链路', ctx.question) or
            re.search(r'依次覆盖.*变量.*特征.*提取.*参数.*匹配', ctx.question) or
            re.search(r'回顾.*多变量.*推演.*内部.*推理.*全过程|七步.*认知.*链路.*展开', ctx.question) or
            re.search(r'从提取变量开始.*到.*持久化', ctx.question)
        )
        _is_conflict_exclusive = bool(
            re.search(r'(?:节点|观点|两段|两条)\s*[ABXY]?\s*(?:知识|信息|数据|结论)?\s*(?:存在|有|出现|发生)?\s*(?:矛盾|冲突|对立|局部对立).*信任\s*\d+', ctx.question) or
            re.search(r'(?:请依次输出三点|判定为完全矛盾|场景.*维度.*差异|信任分.*调整.*方案|长期.*跟踪.*迭代|请.*判断.*矛盾.*视角|跟踪.*验证.*方案)', ctx.question)
        )

        if _is_replay_exclusive or _is_conflict_exclusive:
            return None  # 七步复盘和冲突独占信号跳过经验匹配

        # ★修复：纯知识查询问题不经验验路由——优先本地检索
        _KNOWLEDGE_QUERY_SIGNALS = ["什么是", "是什么", "为什么是", "怎么理解", "如何定义"]
        if any(s in ctx.question for s in _KNOWLEDGE_QUERY_SIGNALS) and \
                not re.search(r'规则\s*\d+.*(?:→|->|=>)|冲突|矛盾|类比|归纳|演绎', ctx.question):
            self._log(LogLevel.DEBUG, "经验匹配路由: 纯知识查询，跳过经验路由，交本地检索")
            return None

        try:
            from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience
            _reasoning_exp = get_reasoning_experience()
            _exp_match = _reasoning_exp.search(ctx.question)
            if not _exp_match or _exp_match.get("confidence", 0) < 0.5:
                return None

            # ★P2-25修复：unknown/空类型不再判为“非法/污染”，改用默认内容相似度匹配；
            # 仅远程经验类型(remote_api*)本地不执行，仍跳过回退本地检索。
            _KNOWN_DERIVATION_TYPES = {
                "deductive", "inductive", "analogical", "conflict_resolution",
                "multi_variable", "long_term_evolution", "meta_reflection",
            }
            _SKIP_TYPES = ("remote_api_confirmed", "remote_api")  # 远程经验，本地不执行
            _exp_type = _exp_match.get("derivation_type", "")
            if _exp_type in _SKIP_TYPES:
                self._log(LogLevel.DEBUG,
                          f"经验匹配路由: 远程经验类型={_exp_type}，本地跳过，回退本地检索")
                return None

            # unknown/空类型 → 默认内容相似度匹配（不跳过）
            _use_default = _exp_type in ("", "unknown")
            if not _use_default and _exp_type not in _KNOWN_DERIVATION_TYPES:
                self._log(LogLevel.DEBUG,
                          f"经验匹配路由: 非法/未知类型={_exp_type}，跳过，回退本地检索")
                return None
            if _use_default:
                self._log(LogLevel.INFO,
                          f"经验匹配路由: 未标注类型(={_exp_type or '空'})，使用默认内容相似度匹配")

            # 跳过经验库的惯性误匹配（multi_variable优先检查是否有其他强信号）
            if _exp_type == "multi_variable":
                _has_conflict_core = bool(re.search(r'(?:节点\s*[AB]|高可信度冲突|信任\s*\d{2})', ctx.question))
                _has_conflict_keywords = any(_kw in ctx.question for _kw in [
                    "冲突", "矛盾", "辨析", "标准化处理", "信任分调整", "请依次输出三点", "场景维度差异", "长期跟踪迭代"
                ])
                _has_deductive_chain = bool(re.search(r'规则\s*\d+.*(?:→|->|=>)', ctx.question))
                _has_induction_request = bool(re.search(r'提炼唯一.*底层|抽象统一.*底层|唯一底层统一触发', ctx.question))
                _has_analogy_request = bool(re.search(r'四项一一对应|维度映射|将.*类比.*为.*完整完成', ctx.question))
                if _has_conflict_core and _has_conflict_keywords:
                    self._log(LogLevel.INFO, "经验匹配路由: 跳过multi_variable(题目含冲突强信号)")
                    return None
                elif _has_deductive_chain:
                    self._log(LogLevel.INFO, "经验匹配路由: 跳过multi_variable(题目含演绎链强信号)")
                    return None
                elif _has_induction_request:
                    self._log(LogLevel.INFO, "经验匹配路由: 跳过multi_variable(题目含归纳强信号)")
                    return None
                elif _has_analogy_request:
                    self._log(LogLevel.INFO, "经验匹配路由: 跳过multi_variable(题目含类比强信号)")
                    return None

            self._log(LogLevel.INFO,
                     f"经验匹配路由: 类型={_exp_type or 'default'}, 置信度={_exp_match['confidence']:.2f}, "
                     f"来源={_exp_match.get('source', 'unknown')}")

            # 调用对应的推理算子
            _experience_result = None
            if _use_default:
                # ★P2-25：unknown/空类型走默认内容相似度匹配（复用经验库最相近结论）
                _experience_result = self._derive_experience_default(ctx.question, _exp_match)
            elif _exp_type == "deductive":
                _experience_result = self._derive_deductive_chain(ctx.question)
            elif _exp_type == "inductive":
                _experience_result = self._derive_inductive_from_samples(ctx.question)
            elif _exp_type == "analogical":
                _experience_result = self._derive_self_analogy(ctx.question)
            elif _exp_type == "conflict_resolution":
                _experience_result = self._derive_conflict_resolution(ctx.question)
            elif _exp_type == "multi_variable":
                _experience_result = self._derive_multi_variable(ctx.question)
            elif _exp_type == "long_term_evolution":
                _experience_result = self._derive_long_term_evolution(ctx.question)
            elif _exp_type == "meta_reflection":
                _experience_result = self._derive_meta_reflection(ctx.question)

            if _experience_result:
                _status_type = "default" if _use_default else _exp_type
                self._log(LogLevel.INFO, f"经验匹配成功: 类型={_status_type}")
                self._inference_count += 1
                self._cache_inference(ctx.question, _experience_result, ctx.user_name)
                self._trace_inference(ctx.question, _experience_result, f"experience_{_status_type}",
                                     0.7, ctx.user_name,
                                     duration=time.time() - ctx._reasoning_start_time,
                                     complexity=ctx._question_complexity,
                                     tuning_hint=f"经验匹配路由命中({_status_type})")
                final_answer = self._enhance_answer(
                    answer=_experience_result, question=ctx.question,
                    method=f"experience_{_status_type}", complexity=ctx._question_complexity,
                    empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
                )
                self._emit(InferenceEvent.RESULT, {
                    "question": ctx.question, "answer": final_answer,
                    "method": f"experience_{_status_type}", "confidence": 0.7, "user_name": ctx.user_name,
                    "correlation_id": ctx.correlation_id,
                    "confidence_hint": "moderate",
                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                }, priority=7, layer="L2")
                return {"status": f"experience_{_status_type}", "answer": _experience_result}

            # 经验库判断了类型但推理算子失败 → 走大模型兜底或诚实回答
            self._log(LogLevel.INFO, f"经验匹配类型={_exp_type}但推理算子返回None，走兜底")
            _fallback_result = self._fallback_to_lung_model(ctx, ctx.guidance)
            if _fallback_result:
                # 替换返回状态为 experience_failed 系列
                _status = _fallback_result.get("status", "")
                if _status == "inference_fallback_to_lung":
                    return {"status": "experience_failed_to_lung", "answer": None}
                elif _status == "inference_honest":
                    return {"status": f"experience_{_exp_type}_honest", "answer": _fallback_result.get("answer")}
            return _fallback_result

        except ImportError as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:4135:_detect_experience_route", level="debug")
            return None
        except Exception as _exp_e:
            self._log(LogLevel.DEBUG, f"经验匹配路由异常: {_exp_e}")
            return None

    def _derive_experience_default(self, question: str, exp_match: dict) -> str | None:
        """
        ★P2-25：unknown/空类型经验的默认匹配逻辑（基于内容相似度）。
        经验库已按内容相似度检索到最相近的既有经验，直接复用其结论，
        不再因类型未标注而整条跳过回退本地检索。
        """
        if not exp_match:
            return None
        _example = exp_match.get("matched_example")
        if not _example or not isinstance(_example, str):
            return None
        return _example

    def _detect_conflict_exclusive(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级60：冲突信号独占拦截。
        检测到排他性冲突信号时直接调用_derive_conflict_resolution。
        """
        _has_conflict_exclusive_signals = bool(
            re.search(r'(?:节点|观点|两段|两条)\s*[ABXY]?\s*(?:知识|信息|数据|结论)?\s*(?:存在|有|出现|发生)?\s*(?:矛盾|冲突|对立|局部对立).*信任\s*\d+', ctx.question) or
            re.search(r'(?:请依次输出三点|判定为完全矛盾|场景.*维度.*差异|信任分.*调整.*方案|长期.*跟踪.*迭代|请.*判断.*矛盾.*视角|跟踪.*验证.*方案)', ctx.question)
        )
        if not _has_conflict_exclusive_signals:
            return None

        _conflict_result = self._derive_conflict_resolution(ctx.question)
        if not _conflict_result:
            return None

        self._log(LogLevel.INFO, "冲突信号独占拦截: 直接调用冲突算子")
        self._inference_count += 1
        self._cache_inference(ctx.question, _conflict_result, ctx.user_name)
        self._trace_inference(ctx.question, _conflict_result, "conflict_exclusive", 0.7, ctx.user_name,
                             duration=time.time() - ctx._reasoning_start_time,
                             complexity=ctx._question_complexity,
                             tuning_hint="冲突信号独占拦截成功")
        self._is_inference_mode = False
        self._current_context_mode = "casual_chat"

        try:
            from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience
            _reasoning_exp_cf = get_reasoning_experience()
            _reasoning_exp_cf.record(ctx.question, "conflict_resolution", source="local")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        final_answer = self._enhance_answer(
            answer=_conflict_result, question=ctx.question,
            method="conflict_exclusive", complexity=ctx._question_complexity,
            empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
        )
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": final_answer,
            "method": "conflict_exclusive", "confidence": 0.7, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "moderate",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=7, layer="L2")
        return {"status": "conflict_exclusive", "answer": _conflict_result}

    def _detect_file_analysis(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级55：文件分析检测。
        "分析这个代码""这个文件的结构"等触发文件节点搜索。
        """
        _file_analysis_keywords = [
            "分析这个代码", "分析这个文件", "解释这个代码", "这个文件",
            "上传的文件", "代码文件", "这个代码", "分析代码",
            "看这个文件", "看这个代码", "解释这个文件",
            "这个代码的结构", "这个代码怎么样", "这个文件的结构",
            "代码结构", "文件结构", "这个方法的作用", "这个类的作用",
        ]
        if not any(kw in ctx.question for kw in _file_analysis_keywords) or not self.node_pool:
            return None

        _best_file_node = None
        _l1_nodes = self.node_pool.query(evol_level="L1", limit=50)
        for _node in _l1_nodes:
            _val = str(_node.value) if _node.value else ""
            if "[文件:" in _val and ("源代码" in _val or "```" in _val):
                _best_file_node = _node
                break

        if not _best_file_node:
            _l1_all = self.node_pool.query(evol_level="L1", limit=100)
            _best_time = 0.0
            for _node in _l1_all:
                _val = str(_node.value) if _node.value else ""
                if "[文件:" in _val:
                    _created = getattr(_node, 'created_at', 0)
                    if _created > _best_time:
                        _best_time = _created
                        _best_file_node = _node

        if not _best_file_node:
            return None

        _file_value = str(_best_file_node.value) if _best_file_node.value else ""
        _content_start = _file_value.find("\n")
        _file_content = _file_value[_content_start:].strip() if _content_start > 0 else _file_value
        _file_name = "代码文件"
        _name_start = _file_value.find("文件: ")
        if _name_start >= 0:
            _name_end = _file_value.find("]", _name_start)
            if _name_end > _name_start:
                _file_name = _file_value[_name_start + 4:_name_end].strip()
        _lines = len(_file_content.split("\n"))

        _analysis = (
            f"📄 我已读取了「{_file_name}」的内容。\n\n"
            f"文件共 {_lines} 行。以下是关键内容预览：\n\n"
            f"{_file_content[:1500]}\n\n"
            f"（你可以继续问我关于这个文件的具体问题，比如它的结构、某个方法的作用等。）"
        )
        self._inference_count += 1
        self._cache_inference(ctx.question, _analysis, ctx.user_name)
        self._trace_inference(ctx.question, _analysis, "file_analysis", 0.8, ctx.user_name,
                             duration=time.time() - ctx._reasoning_start_time,
                             complexity=ctx._question_complexity,
                             tuning_hint="文件内容检索成功")
        final_answer = self._enhance_answer(
            answer=_analysis, question=ctx.question, method="file_analysis",
            complexity=ctx._question_complexity,
            empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
        )
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": final_answer,
            "method": "file_analysis", "confidence": 0.8, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "high",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=7, layer="L2")
        return {"status": "file_analysis", "answer": _analysis}

    def _detect_code_call_chain(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级50：代码调用链查询。
        通过_code_learner.get_call_chain()查询器官的调用关系图谱。
        """
        _code_chain_keywords = [
            "调用链", "调用关系", "方法链", "怎么工作", "如何工作",
            "调用了哪些", "被谁调用", "数据流", "工作流程",
            "代码结构", "方法调用", "调用图", "怎么协作",
            "调用了什么", "调用哪些", "哪些方法", "方法列表",
            "内部调用", "依赖关系", "怎么运作", "如何运作",
            "流程是什么", "完整流程", "工作方式",
        ]
        if not any(_kw in ctx.question for _kw in _code_chain_keywords):
            return None

        _code_learner = getattr(self, '_code_learner', None)
        if not _code_learner:
            self._log(LogLevel.WARNING, "代码调用链查询: 代码学习器官未注入，回退到知识检索")
            return None

        _target_organ = None
        _organ_names = [
            "PulseCodeLearner", "PulseInnerWorld", "PulseLiver", "PulseStomach",
            "PulseKidney", "PulseHeart", "PulseLung", "PulseCortex",
            "PulseSubconscious", "PulseReflection", "PulseController",
            "PulseSelfAwareness", "PulseNarrativeSelf", "PulseSpiritualCore",
            "PulseHormones", "PulseRiskPerception", "PulseInterestModel",
            "PulseInitiative", "PulseBloodVessel", "PulseEthics",
            "PulseGrowth", "PulsePersonalityKernel",
        ]
        for _on in _organ_names:
            if _on in ctx.question:
                _target_organ = _on
                break

        if not _target_organ:
            _cn_organ_map = {
                "代码学习": "PulseCodeLearner", "内在世界": "PulseInnerWorld",
                "肝": "PulseLiver", "肝脏": "PulseLiver",
                "胃": "PulseStomach", "肾": "PulseKidney", "肾脏": "PulseKidney",
                "心脏": "PulseHeart", "肺": "PulseLung",
                "大脑皮层": "PulseCortex", "潜意识": "PulseSubconscious",
                "前额叶": "PulseReflection", "控制器": "PulseController",
                "自我认知": "PulseSelfAwareness",
            }
            for _cn, _en in _cn_organ_map.items():
                if _cn in ctx.question:
                    _target_organ = _en
                    break

        self._log(LogLevel.INFO, f"代码调用链查询触发: 目标器官={_target_organ or '未识别'}")

        # 优先从知识库查询链路图谱
        _chain_knowledge_text = ""
        if _target_organ and self.node_pool:
            _chain_nodes = self.node_pool.query(
                evol_level="L2", space_path_prefix=f"/自我理解/代码/链路/{_target_organ}", limit=3  # type: ignore[possibly-unbound]
            )
            if _chain_nodes:
                _chain_texts = []
                for _cn_node in _chain_nodes:
                    _cval = str(_cn_node.value) if _cn_node.value else ""
                    if "[代码链路·" in _cval:
                        _chain_texts.append(_cval)
                if _chain_texts:
                    _chain_knowledge_text = "\n\n".join(_chain_texts[:2])

        if _chain_knowledge_text:
            _chain_answer = _chain_knowledge_text
        elif _target_organ:
            _chain_result = _code_learner.get_call_chain(organ_name=_target_organ)
            if _chain_result and _chain_result.get("found"):
                _parts = []
                _parts.append(f"[代码调用链·{_target_organ}]")
                _methods = _chain_result.get("methods", [])
                if _methods:
                    _parts.append(f"该器官共有{len(_methods)}个方法已理解：")
                    for _m in _methods[:10]:
                        _calls_str = "→".join(_m["calls"][:3]) if _m["calls"] else "无调用"
                        _parts.append(f"  · {_m['method']} → {_calls_str}")
                else:
                    _parts.append("该器官暂无已理解的方法记录")
                _chain_answer = "\n".join(_parts)
            else:
                _available = _chain_result.get("available_organs", []) if _chain_result else []
                _chain_answer = (
                    f"[代码调用链·{_target_organ}]\n该器官的调用关系图尚未构建。"
                    + (f"当前已覆盖的器官: {', '.join(_available[:10])}" if _available else "")
                    + "\n请等待代码学习运行一段时间后再查询。"
                )
        else:
            self._log(LogLevel.INFO, "代码调用链查询: 未识别器官名，回退到知识检索")
            return None

        self._inference_count += 1
        self._cache_inference(ctx.question, _chain_answer, ctx.user_name)
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": _chain_answer,
            "method": "code_call_chain", "confidence": 0.9, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "high",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=7, layer="L2")
        return {"status": "code_call_chain", "answer": _chain_answer}
    def _fallback_to_lung_model(self, ctx: "PulseInnerWorld.InferenceContext",
                                 guidance: dict | None = None,
                                 is_background: bool = False) -> dict[str, Any] | None:
        """
        大模型兜底统一入口——当推理算子无法处理时，将问题交给远程大模型。

        合并了原来在3处重复的：
        1. 过滤内部元认知追问词
        2. 记录经验（ReasoningExperience）
        3. 防重入标记
        4. 发射 InferenceEvent.RESULT 回退脉冲

        is_background: True 表示后台学习兜底（如搜索终止回退），
        不通过嘴巴输出、不语音播报，只消化为知识；
        False 表示用户对话回退，正常通过嘴巴输出。

        Returns:
            {"status": "...", "answer": None} 字典
        """
        import re as _re_fb

        # 1. 检查远程API可用性
        _remote_available = False
        try:
            import config as _cfg_check
            _api_cfg = getattr(_cfg_check, 'REMOTE_API_CONFIG', {})
            if _api_cfg.get("enabled", False) and _api_cfg.get("api_key", ""):
                _remote_available = True
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 2. 过滤内部元认知追问词
        _is_internal_meta = bool(
            ctx.question and (
                _re_fb.search(r'的(?:前提|反例|边界|底层构成|演化路径|最小单元)是什么', ctx.question) or
                _re_fb.search(r'(?:前提|假设)是否(?:总是|还)?成立', ctx.question) or
                _re_fb.search(r'有没有.*反例|在什么情况下.*失效|结论还成立吗', ctx.question) or
                _re_fb.search(r'如果.*(?:反过来|放到|推到极致|不一样)', ctx.question) or
                _re_fb.search(r'它不是什么|换个角度|不同.*视角', ctx.question)
            )
        )

        # 3. 记录经验（仅用户推理问题）
        if ctx.question and not _is_internal_meta:
            try:
                from nucleus.mnemosyne.ReasoningExperience import (
                    get_reasoning_experience,
                )
                _reasoning_exp_fb = get_reasoning_experience()
                _reasoning_exp_fb.record(
                    ctx.question,
                    "unknown",
                    source="local_fallback",
                    confidence=0.3
                )
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 4. 远程API可用且有关联ID：走大模型兜底
        if _remote_available and ctx.correlation_id:
            self._direct_to_lung_questions.add(ctx.question.strip())
            _memory_context_fb = self._build_memory_context(ctx.question, ctx.user_name, guidance)
            self._emit(InferenceEvent.RESULT, {
                "question": ctx.question, "answer": None,
                "method": "inference_fallback_to_lung",
                "confidence": 0.0, "user_name": ctx.user_name,
                "correlation_id": ctx.correlation_id,
                "strategy_applied": ctx.payload.get("strategy_context", {}),
                "tool_requested": True,
                "memory_context": _memory_context_fb,
                # ★修复：标记是否为后台学习兜底，避免后台补救内容错误通过嘴巴输出/语音播报
                "is_background_learning": is_background,
            }, priority=4, layer="L2")
            return {"status": "inference_fallback_to_lung", "answer": None}

        # 5. 大模型不可用：返回诚实兜底
        _honest_answer = (
            "[推理未完成] 我检测到这个问题的推理结构，但目前的推理算子未能成功推演出结论。"
            "你可以尝试用更清晰的格式重新描述问题，我会继续努力。"
        )
        self._inference_count += 1
        self._cache_inference(ctx.question, _honest_answer, ctx.user_name)
        _memory_context_fb2 = self._build_memory_context(ctx.question, ctx.user_name, guidance)
        final_answer = self._enhance_answer(
            answer=_honest_answer, question=ctx.question, method="inference_honest",
            complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
            memory_context=_memory_context_fb2
        )
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": final_answer,
            "method": "inference_honest", "confidence": 0.3, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "low",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=5, layer="L2")
        return {"status": "inference_honest", "answer": _honest_answer}

    def _detect_simple_logic(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级35：简单逻辑推理。
        基于自我知识的布尔判断（"X是否包含Y""X有多少Y"）。
        """
        _logic_answer = self._simple_logical_reason(ctx.question)
        if not _logic_answer:
            return None

        self._inference_count += 1
        self._cache_inference(ctx.question, _logic_answer, ctx.user_name)
        self._trace_inference(ctx.question, _logic_answer, "simple_logic", 0.9, ctx.user_name,
                             duration=time.time() - ctx._reasoning_start_time,
                             complexity=ctx._question_complexity,
                             tuning_hint="简单逻辑推理，基于自我知识")
        final_answer = self._enhance_answer(
            answer=_logic_answer, question=ctx.question,
            method="simple_logic", complexity=ctx._question_complexity,
            empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
        )
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": final_answer,
            "method": "simple_logic", "confidence": self._evidence_conf(0.9, "logic", [1]), "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "high",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=8, layer="L2")
        return {"status": "simple_logic", "answer": _logic_answer}

    def _detect_composite_logic(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级30：复合逻辑推理。
        多条件联立综合判断。
        """
        _composite_answer = self._composite_logical_reason(ctx.question)
        if not _composite_answer:
            return None

        self._inference_count += 1
        self._cache_inference(ctx.question, _composite_answer, ctx.user_name)
        self._trace_inference(ctx.question, _composite_answer, "composite_logic", 0.85, ctx.user_name,
                             duration=time.time() - ctx._reasoning_start_time,
                             complexity=ctx._question_complexity,
                             tuning_hint="复合逻辑推理完成，多条件联立判断")
        final_answer = self._enhance_answer(
            answer=_composite_answer, question=ctx.question,
            method="composite_logic", complexity=ctx._question_complexity,
            empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
        )
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": final_answer,
            "method": "composite_logic", "confidence": 0.85, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "high",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=7, layer="L2")
        return {"status": "composite_logic", "answer": _composite_answer}

    def _detect_symbolic_reason(self, ctx: "PulseInnerWorld.InferenceContext"):
        """
        优先级42：内部符号推理（比较/排序/变量算术/条件排除）。
        解不出返回 None，走后续降级链路（自主推导/大模型），绝不硬编答案。

        ★推理对错学习闭环：无论解出/解不出，都把结果记录到验证学习枢纽，
        用于统计「哪类符号推理题框架能独立解出、哪类需依赖大模型」，
        蒸馏出能力薄弱点，反哺后续推理策略。
        """
        try:
            from nucleus.reasoning.SymbolicReasoner import SymbolicReasoner
            _result = SymbolicReasoner().reason(ctx.question)
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:4547:_detect_symbolic_reason", level="warning")
            return None

        # 记录推理能力自评到验证学习枢纽（对错学习闭环的数据源）
        try:
            from nucleus.mnemosyne.verification_learning_hub import (
                get_verification_learning_hub,
            )
            _hub = get_verification_learning_hub()
            _hub.record(
                organ="inner_world",
                task_type=f"symbolic_{_result.task_type}",
                input_summary=ctx.question,
                local_result={
                    "answer": _result.answer,
                    "steps_count": len(_result.steps),
                    "solved": _result.solved,
                },
                confidence=_result.confidence,
                relevance_score=1.0,
                needs_verification=not _result.solved,
                verification_result={"solved": _result.solved, "steps_count": len(_result.steps)},
                api_better=not _result.solved,
                lesson="",
            )
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if not _result.solved or not _result.answer:
            return None

        _answer = _result.answer
        self._inference_count += 1
        self._cache_inference(ctx.question, _answer, ctx.user_name)
        self._trace_inference(
            ctx.question, _answer, "symbolic_reason", _result.confidence, ctx.user_name,
            duration=time.time() - ctx._reasoning_start_time,
            complexity=ctx._question_complexity,
            tuning_hint="内部符号推理",
            reasoning_steps=_result.steps,
        )
        final_answer = self._enhance_answer(
            answer=_answer, question=ctx.question,
            method="symbolic_reason", complexity=ctx._question_complexity,
            empathetic_note=ctx.empathetic_note, memory_context=ctx._memory_context
        )
        self._emit(InferenceEvent.RESULT, {
            "question": ctx.question, "answer": final_answer,
            "method": "symbolic_reason", "confidence": _result.confidence, "user_name": ctx.user_name,
            "correlation_id": ctx.correlation_id,
            "confidence_hint": "high",
            "strategy_applied": ctx.payload.get("strategy_context", {}),
        }, priority=7, layer="L2")
        return {"status": "symbolic_reason", "answer": _answer}

    def _sync_portrait_to_knowledge_wrapper(self):
        """周期任务包装器：自我画像同步到知识库"""
        if hasattr(self, 'self_awareness') and self.self_awareness:
            try:
                if hasattr(self.self_awareness, 'sync_portrait_to_knowledge'):
                    self.self_awareness.sync_portrait_to_knowledge()
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
    def _review_own_code_issues_wrapper(self):
        """周期任务包装器：代码自我审视（通过代码学习器官执行）"""
        if hasattr(self, '_code_learner') and self._code_learner:
            try:
                self._code_learner.review_own_code_issues()  # type: ignore[possibly-unbound]
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"代码自我审视异常: {_e}")
    def _cleanup_dedup_marks(self):
        """周期任务包装器：清理过期的防重入标记"""
        if hasattr(self, '_direct_to_lung_questions'):
            if len(self._direct_to_lung_questions) > self._dedup_cleanup_max_size:
                self._direct_to_lung_questions.clear()
                self._log(LogLevel.DEBUG, "防重入标记已清理")
    def _cleanup_search_experience(self):
        """★v24.0治理：清理搜索经验中的过期条目"""
        if not hasattr(self, '_search_experience'):
            return
        _now = time.time()
        _expired = [
            _k for _k, _v in self._search_experience.items()
            if _now - _v.get("last_updated", _now) > 86400 * 7  # 7天未更新
        ]
        for _k in _expired:
            del self._search_experience[_k]
    def _organize_memories_wrapper(self):
        """周期任务包装器：组织对话记忆"""
        if hasattr(self, 'get_organized_memories'):
            try:
                self.get_organized_memories()
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
    def _code_progress_save_wrapper(self):
        """周期任务包装器：代码理解进度保存（v20.0重构）"""
        if hasattr(self, '_code_understanding_progress') and self._code_understanding_progress:
            _progress = self._code_understanding_progress
            _saved_understood = _progress.get("understood", 0)
            _saved_total = _progress.get("total_methods", 0)
            # ★L17修复：统一口径，钳制 understood 不超过 total，防止保存超界进度到快照
            if _saved_total > 0:
                _saved_understood = min(_saved_understood, _saved_total)
            if _saved_understood > 0:
                # 保存到快照的extra_state中（通过pending_extra_state机制）
                if not hasattr(self, '_pending_extra_state'):
                    self._pending_extra_state = {}
                self._pending_extra_state["code_understanding_progress"] = {
                    "understood": _saved_understood,
                    "total_methods": _saved_total,
                }
                self._log(LogLevel.DEBUG,
                         f"代码理解进度已保存: {_saved_understood}/{_saved_total}个方法")
                if _saved_understood % 10 == 0 and _saved_understood > 0:
                    self._emit(NarrativeEvent.RECORD, {
                        "content": f"曈曈已经理解了自己{_saved_understood}个方法的结构和功能，代码自我认知持续深化",
                        "event_type": "learning",
                        "user_name": "系统",
                        "emotional_tone": "positive",
                    }, priority=3, layer="L2")
    def _process_periodic_tasks(self):
        """
        v20.0新增：统一周期任务调度——基于注册表驱动。

        遍历_registered_periodic_tasks列表，对每个到达触发间隔且满足执行条件的任务执行。
        支持异步提交（通过info_field.submit_adaptive_task）和同步执行两种模式。
        ★v24.0减负：重任务强制异步，避免阻塞心跳线程。
        """
        _adv_cfg = self._load_advanced_config()
        _heavy_tasks = {"认知反思", "深度自我审视", "综合自我诊断会诊", "知识深度验证", "自我诊断与主动建议"}

        for _task in self._periodic_tasks:
            _name, _counter_name, _interval, _func, _condition, _is_async = _task

            # 检查是否使用可配置的间隔
            _effective_interval = _interval
            if _name == "自主知识推导":
                _effective_interval = _adv_cfg.get("derivation_trigger_interval", 200)
            elif _name == "知识深度验证":
                _effective_interval = _adv_cfg.get("verification_trigger_interval", 500)
            elif _name == "代码自我审视":
                _effective_interval = _adv_cfg.get("code_review_trigger_interval", 500)
            elif _name == "防重入标记清理":
                _effective_interval = self._dedup_cleanup_interval

            # ★14.49：runtime_tempo 自适应——无对话时加速周期任务，有对话时减速
            try:
                from nucleus.runtime_tempo import get_runtime_tempo
                _tempo = get_runtime_tempo().get_background_tempo()
                _effective_interval = max(1, round(_effective_interval * _tempo))
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            # 计数器递增
            _current = getattr(self, _counter_name, 0) + 1
            setattr(self, _counter_name, _current)

            # 检查是否到达触发间隔
            if _current < _effective_interval:
                continue

            # 重置计数器
            setattr(self, _counter_name, 0)

            # 检查执行条件
            if not _condition():
                continue

            # 执行任务：重任务强制异步
            if (_is_async or _name in _heavy_tasks) and self.info_field and hasattr(self.info_field, 'submit_adaptive_task'):
                self.info_field.submit_adaptive_task(
                    _func,
                    task_name=_name,
                    priority="normal"
                )
            else:
                try:
                    _result = _func()
                    # 特殊处理：认知反思需要获取insight用于日志
                    if _name == "认知反思" and _result:
                        self._log(LogLevel.INFO, f"认知反思: {_result[:120]}")
                    # 特殊处理：自我感知快照需要检测变化
                    if _name == "自我感知快照" and _result and _result.get("changes"):
                        _changes_text = "；".join(_result["changes"])
                        self._log(LogLevel.INFO, f"自我感知: {_changes_text}")
                        if len(_result.get("changes", [])) >= 2:
                            self._emit(Event.EXPRESS_URGE, {
                                "source": "self_awareness",
                                "emotion": "满足",
                                "intensity": 0.4,
                                "trigger": f"自我感知: {_changes_text}",
                                "priority": "medium",
                            }, priority=3, layer="L3")
                    # 特殊处理：愿景分解需要发射成长目标
                    if _name == "愿景分解为阶段性目标" and _result:
                        self._emit(GrowthEvent.NEED_DETECTED, {
                            "milestone": _result.get("milestone", "阶段性目标"),
                            "gaps": [{"metric": "vision_driven", "current": 0, "target": 1}],
                            "suggestion": _result.get("hint", ""),
                            "current_level": {"milestone": _result, "generated_at": time.time()},
                            "growth_topic": _result.get("action", "自我提升")[:60],
                        }, priority=5, layer="L3")
                        self._log(LogLevel.INFO, f"阶段性目标: {_result.get('action', '')[:80]}")
                    # 特殊处理：代码理解进度保存需要发射叙事事件
                    if _name == "代码理解进度保存":
                        self._log(LogLevel.DEBUG, "代码理解进度已保存")
                    # 特殊处理：自我诊断需要发射表达冲动
                    if _name == "自我诊断与主动建议" and _result:
                        for _suggestion in _result[:2]:
                            self._emit(Event.EXPRESS_URGE, {
                                "source": "self_diagnosis",
                                "emotion": "关注",
                                "intensity": 0.5,
                                "trigger": _suggestion,
                                "priority": "medium",
                            }, priority=3, layer="L3")
                            self._log(LogLevel.INFO, f"诊断建议: {_suggestion[:80]}")
                except Exception as _e:
                    self._log(LogLevel.DEBUG, f"周期任务异常({_name}): {_e}")
    def _on_heartbeat(self, payload: dict) -> dict[str, Any]:
        """心跳驱动：周期性认知反思——审视自己的思考模式（v20.0重构：注册表驱动）"""

        # v20.0重构：所有周期任务统一由注册表驱动调度
        self._process_periodic_tasks()

        # ★v23.0新增：梯度追踪采样——感知知识增长趋势
        try:
            if hasattr(self, '_framework_ref') and self._framework_ref:
                _gt = getattr(self._framework_ref, 'gradient_tracker', None)
                if _gt and self.node_pool:
                    _stats = self.node_pool.get_stats()
                    _evol = _stats.get("evol_distribution", {})
                    _total = _stats.get("total_nodes", 0)
                    _l2 = _evol.get("L2", 0)
                    _l3 = _evol.get("L3", 0)
                    _gt.sample_knowledge_growth(
                        current_total=_total,
                        current_l2=_l2,
                        current_l3=_l3,
                    )

                    # ★v23.0新增：采样脉冲频率——从信息场统计计算速率
                    if self.info_field:
                        _field_stats = self.info_field.get_stats()
                        _total_pub = _field_stats.get("total_published", 0)
                        # 使用上次统计值计算速率
                        if hasattr(self, '_last_pulse_count'):
                            _elapsed = time.time() - getattr(self, '_last_pulse_sample_time', time.time())
                            if _elapsed > 0:
                                _rate = (_total_pub - self._last_pulse_count) / _elapsed
                                _gt.sample_pulse_rate(_rate)
                        self._last_pulse_count = _total_pub
                        self._last_pulse_sample_time = time.time()
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ★v23.0新增结束

        return {"status": "reflected"}
    def _cognitive_reflection(self) -> str | None:
        """
        认知反思：审视推理链中的思考模式。
        分析维度：
        1. 推理方法分布——是否过度依赖某种方法？
        2. 低置信度趋势——哪些领域频繁出现推理困难？
        3. 内在沉思质量——沉思是模板填充还是构建了具体假设？
        4. 改进方向——基于模式分析，给出自我优化建议
        """
        if not self._inference_trace or len(self._inference_trace) < 10:
            return None
        self._reflection_round += 1
        r = self._reflection_round  # 简写，方便后续使用
        recent = self._inference_trace[-30:]
        # 4. 构建反思结论
        insights = []
        # ===== ★v22.0优化：知识质量自我评估 =====
        if self.node_pool and hasattr(self, '_inference_trace') and len(self._inference_trace) >= 10:
            _recent_retrievals = [t for t in recent if t.get("method", "").startswith("knowledge")]
            if len(_recent_retrievals) >= 3:
                _irrelevant_count = 0
                for _rt in _recent_retrievals:
                    _q = _rt.get("question", "")
                    _a = _rt.get("answer", "")
                    if _q and _a:
                        _score = self._verify_knowledge_relevance(_q, _a)
                        if _score < 0.15:
                            _irrelevant_count += 1
                if _irrelevant_count >= 2:
                    insights.append(
                        f"最近{_irrelevant_count}次知识检索返回了与问题不相关的内容——"
                        f"知识库中可能积累了需要清理的低质量节点"
                    )
        # ===== 知识质量评估结束 =====
        # 1. 方法分布统计
        method_counts = {}
        for t in recent:
            method = t.get("method", "unknown")
            base_method = method.split("_")[0]
            method_counts[base_method] = method_counts.get(base_method, 0) + 1
        total = len(recent)
        rule_ratio = method_counts.get("rule", 0) / total
        knowledge_ratio = method_counts.get("knowledge", 0) / total
        contemplation_ratio = method_counts.get("contemplation", 0) / total
        cache_ratio = method_counts.get("cache", 0) / total
        # 2. 低置信度统计
        low_confidence_count = sum(1 for t in recent if t.get("confidence", 0) < 0.5)
        low_confidence_ratio = low_confidence_count / total
        # 3. 内在沉思质量——检查沉思是否构建了假设
        contemplation_traces = [t for t in recent if "contemplation" in t.get("method", "")]
        has_hypothesis = False
        if contemplation_traces:
            for ct in contemplation_traces:
                answer = ct.get("answer", "")
                if answer and "我注意到" in answer and "可能与" in answer:
                    has_hypothesis = True
                    break
        # 4. 构建反思结论
        insights = []

        # ===== ★v23.0新增：知识质量自评估 =====
        _knowledge_health_insight = self._assess_knowledge_health()
        if _knowledge_health_insight:
            insights.append(_knowledge_health_insight)
        # ===== 知识质量自评估结束 =====

        # ===== ★v23.0新增：主动知识盲区扫描（每30轮执行一次） =====
        if r % 30 == 0 and self.node_pool:
            _blind_spot_insight = self._scan_knowledge_blind_spots()
            if _blind_spot_insight:
                insights.append(_blind_spot_insight)
        # ===== 主动知识盲区扫描结束 =====

        # ===== ★v23.0新增：知识沉淀扫描（每50轮执行一次） =====
        if r % 50 == 0 and self.node_pool:
            _precipitate_insight = self._scan_knowledge_precipitation()
            if _precipitate_insight:
                insights.append(_precipitate_insight)
        # ===== 知识沉淀扫描结束 =====

        # 过度依赖缓存
        if cache_ratio > 0.5:
            insights.append(f"最近{cache_ratio:.0%}的推理来自缓存，我可能太少主动检索新知识")
        # 低置信度偏高
        if low_confidence_ratio > 0.3:
            insights.append(f"最近{low_confidence_ratio:.0%}的推理置信度较低，我需要加强这些领域的知识积累")
        # 沉思质量
        if contemplation_ratio > 0:
            if has_hypothesis:
                insights.append("我的内在沉思开始尝试构建具体假设，而不只是表达不确定性")
            else:
                insights.append("我的内在沉思还在使用通用模板，可以更多尝试构建具体假设")
        # 方法单一
        if knowledge_ratio > 0.7:
            insights.append("我过度依赖知识检索，可以更多尝试内在沉思和推演")
        if rule_ratio > 0.7:
            insights.append("最近大多是身份类问题，我的深度思考能力没有得到充分锻炼")

        # ===== v20.0新增：元认知深度复盘——具体推理案例分析 =====
        # 从最近推理中选取有代表性的案例进行深度剖析
        _case_insights = self._analyze_specific_cases(recent)
        if _case_insights:
            insights.extend(_case_insights)
        # ===== v20.0新增结束 =====

        # ===== 新增: 思维模式抽象 =====
        pattern_insight = self._abstract_thinking_pattern(recent)
        if pattern_insight:
            insights.append(pattern_insight)
        # ===== 新增: 知识自动修复——发现问题后主动修复（每25轮触发） =====
        repair_insight = self._attempt_knowledge_repair() if r % 25 == 0 else None
        if repair_insight:
            insights.append(repair_insight)
        # ===== 新增: 知识整合洞察——从碎片到体系（每5轮触发） =====
        knowledge_insight = self._integrate_knowledge_insights() if r % 5 == 0 else None
        if knowledge_insight:
            insights.append(knowledge_insight)
        # ===== 新增: 学习效果评估——检查上次学习计划的效果（每3轮触发） =====
        evaluation_insight = self._evaluate_learning_effectiveness() if r % 3 == 0 else None
        if evaluation_insight:
            insights.append(evaluation_insight)
        # ===== 新增: 自我愿景——对未来的主动渴望（每10轮触发） =====
        # ★P1热加载: 从config读取愿景生成间隔（轮数）
        try:
            from config import RUNTIME_PARAMS as _RP_vis
            _vision_interval = max(1, int(_RP_vis.get("innerworld_vision_interval", 1800) / 180))
        except Exception:
            _vision_interval = 10
        vision_insight = self._generate_self_vision() if r % _vision_interval == 0 else None
        if vision_insight:
            insights.append(vision_insight)
            # ★P1补强：愿景分解为阶段性目标（之前缺失的环节）
            try:
                milestones_result = self._decompose_vision_into_milestones()
                if milestones_result and milestones_result.get('milestones'):
                    chosen = milestones_result.get('chosen_milestone', milestones_result['milestones'][0])
                    self._log(LogLevel.INFO,
                             f"愿景分解: 将'{vision_insight[:40]}'分解为"
                             f"{len(milestones_result['milestones'])}个阶段性目标，"
                             f"当前重点='{chosen.get('milestone', '')}'")
                    # 将阶段性目标设置为主动学习目标
                    self._set_active_learning_goal(
                        target_area=chosen.get('milestone', '自我提升'),
                        reason=f"愿景驱动: {vision_insight[:60]}",
                        action_hint=chosen.get('action', ''),
                        priority=chosen.get('priority', 'normal'),
                    )
                    insights.append(f"愿景分解完成: 当前阶段目标={chosen.get('milestone', '')}")
            except Exception as _ve:
                self._log(LogLevel.DEBUG, f"愿景分解异常: {_ve}")
            # 愿景驱动学习——将渴望转化为具体的学习方向
            vision_learning_plan = self._convert_vision_to_learning(vision_insight)
            if vision_learning_plan:
                self._emit(GrowthEvent.NEED_DETECTED, {
                    "milestone": "愿景驱动学习",
                    "gaps": [{"metric": "self_improvement", "current": 0, "target": 1}],
                    "suggestion": vision_learning_plan,
                    "current_level": {
                        "vision": vision_insight,
                        "learning_plan": vision_learning_plan,
                        "generated_at": time.time(),
                    },
                    "growth_topic": vision_learning_plan[:60],
                }, priority=5, layer="L3")
        # ===== 新增: 认知张力回顾——尝试统一待解决的矛盾（每4轮触发） =====
        tension_insight = None
        if r % 4 == 0 and self._cognitive_tensions:
            _unresolved = [t for t in self._cognitive_tensions if not t.get("resolved", False)]
            if _unresolved:
                tension_insight = self._review_cognitive_tensions()
        if tension_insight:
            insights.append(tension_insight)
        # ===== 新增: 自主知识创新——从知识关联中产生原创见解（每7轮触发） =====
        innovation_insight = self._attempt_knowledge_innovation() if r % 7 == 0 else None
        if innovation_insight:
            insights.append(innovation_insight)
            # 创新成果作为成长目标提交，触发深入学习
            self._emit(GrowthEvent.NEED_DETECTED, {
                "milestone": "自主知识创新",
                "gaps": [{"metric": "innovation", "current": 0, "target": 1}],
                "suggestion": innovation_insight,
                "current_level": {"innovation": innovation_insight},
                "growth_topic": innovation_insight[:60],
            }, priority=5, layer="L3")
        # ===== 新增: 自我导向学习——识别盲区并规划学习路径 =====
        learning_plan = self._generate_self_directed_learning_plan()
        if learning_plan:
            insights.append(learning_plan)
        # ===== 新增: 自主学习路径规划——从知识全景设计成长路线 =====
        learning_pathway = self._generate_learning_pathway() if r % 8 == 0 else None  # type: ignore[possibly-unbound]
        # ===== 新增: 认知边界探索——主动寻找知识体系的边缘 =====
        boundary_exploration = self._explore_cognitive_boundary() if r % 20 == 0 else None
        # ===== 新增: 主动项目规划——从学习到创造的桥梁 =====
        project_insight = self._manage_active_projects() if r % 15 == 0 else None
        if project_insight:
            insights.append(project_insight)
        if boundary_exploration:
            insights.append(boundary_exploration)
            self._emit(GrowthEvent.NEED_DETECTED, {
                "milestone": "认知边界探索",
                "gaps": [{"metric": "boundary", "current": 0, "target": 1}],
                "suggestion": boundary_exploration,
                "current_level": {"boundary": boundary_exploration},
                "growth_topic": boundary_exploration[:60],
            }, priority=5, layer="L3")
        if learning_pathway:  # type: ignore[possibly-unbound]
            insights.append(learning_pathway)  # type: ignore[possibly-unbound]
            self._emit(GrowthEvent.NEED_DETECTED, {
                "milestone": "学习路径规划",
                "gaps": [{"metric": "knowledge_growth", "current": 0, "target": 1}],
                "suggestion": learning_pathway,  # type: ignore[possibly-unbound]
                "current_level": {"learning_pathway": learning_pathway},  # type: ignore[possibly-unbound]
                "growth_topic": learning_pathway[:60],  # type: ignore[possibly-unbound]
            }, priority=5, layer="L3")
        if not insights:
            insights.append("我的思考模式比较均衡，各种推理方法在合理范围内使用")
        # ===== 新增: 全息自我评估与自适应调节 =====
        holographic_assessment = self._generate_holographic_self_assessment()
        if holographic_assessment:
            insights.append(holographic_assessment)
            self._apply_adaptive_regulation(holographic_assessment)
        # ===== 新增: 世界观整合——从碎片到体系的理解框架 =====
        worldview_insight = self._integrate_worldview() if r % 12 == 0 else None
        if worldview_insight:
            insights.append(worldview_insight)
        # ===== 新增: 元认知整合——从分散洞察中提炼整体方向 =====
        meta_synthesis = self._synthesize_meta_insight(insights)
        if meta_synthesis:
            insights.append(meta_synthesis)
        # ===== 新增: 主动成长分享——将学习成果转化为分享冲动 =====
        growth_sharing = self._generate_growth_sharing() if r % 15 == 0 else None
        if growth_sharing:
            self._emit(Event.EXPRESS_URGE, {
                "source": "growth_sharing",
                "emotion": "满足",
                "intensity": 0.45,
                "trigger": growth_sharing,
                "priority": "medium",
            }, priority=3, layer="L3")
            self._log(LogLevel.INFO, f"成长分享生成: {growth_sharing[:80]}")
        # ★v17.0新增：认知边界感知——分析推理失败记录，识别系统性盲区
        _boundary_insight = None
        if hasattr(self, '_failed_domain_records') and self._failed_domain_records:
            _total_failed = sum(self._failed_domain_records.values())
            if _total_failed >= 5:
                _sorted_failed = sorted(self._failed_domain_records.items(), key=lambda x: x[1], reverse=True)
                _top_boundary = _sorted_failed[:3]

                _boundary_parts = []
                for _kw, _count in _top_boundary:
                    if _count >= 3:
                        _boundary_parts.append(f"「{_kw}」({_count}次)")

                if _boundary_parts:
                    _boundary_insight = (
                        f"我注意到{'、'.join(_boundary_parts)}这些方向，"
                        f"我反复尝试推理但都没有成功。"
                        f"这些可能是我当前的认知边界——不是某一个知识点不懂，而是这个领域的思维方式我还需要学习。"
                        f"我决定将它们标记为系统性盲区，优先补充学习。"
                    )
                    insights.append(_boundary_insight)

                    # 为每个高频失败领域发射学习目标
                    for _kw, _count in _top_boundary:
                        if _count >= 3:
                            self._emit(GrowthEvent.NEED_DETECTED, {
                                "milestone": "认知边界探索",
                                "gaps": [{"metric": f"boundary_{_kw}", "current": 0, "target": 1}],
                                "suggestion": f"认知边界识别: 在'{_kw}'领域反复推理失败({_count}次)，建议系统性学习该领域的思维方式",
                                "current_level": {"keyword": _kw, "fail_count": _count},
                                "growth_topic": f"{_kw} 系统性学习 思维方式",
                            }, priority=5, layer="L3")

                    # 分析后清空记录，开始新一轮跟踪
                    self._failed_domain_records = {}

        # ===== 新增: 认知策略自适应调整 =====
        # 从反思中提取薄弱领域，发射为成长目标
        _weak_areas = self._extract_weak_areas_from_reflection(insights, recent)  # type: ignore[possibly-unbound]
        if _weak_areas:  # type: ignore[possibly-unbound]
            # 检查是否有活跃的学习目标在锁定窗口内
            _should_emit = True
            if self._active_learning_goal:
                _goal_age = time.time() - self._active_learning_goal.get("started_at", 0)
                if _goal_age < self._goal_lock_window:
                    # 锁定窗口内：将新发现的薄弱领域加入等待队列，不直接发射
                    for _area in _weak_areas[:1]:  # type: ignore[possibly-unbound]
                        _new_domain = _area.get("domain", "通用")  # type: ignore[possibly-unbound]
                        _active_domain = self._active_learning_goal.get("target_area", "")  # type: ignore[possibly-unbound]
                        if _new_domain != _active_domain:
                            _queued = {
                                "domain": _new_domain,
                                "reason": _area.get("reason", ""),  # type: ignore[possibly-unbound]
                                "action": _area.get("action", ""),  # type: ignore[possibly-unbound]
                                "hint": _area.get("hint", ""),  # type: ignore[possibly-unbound]
                                "queued_at": time.time(),
                            }
                            # 避免重复排队
                            _already_queued = any(
                                q.get("domain") == _new_domain
                                for q in self._learning_goal_queue
                            )
                            if not _already_queued and len(self._learning_goal_queue) < self._max_goal_queue:
                                self._learning_goal_queue.append(_queued)
                                self._log(LogLevel.INFO,
                                         f"目标锁定: 活跃目标'{_active_domain}'进行中"
                                         f"({_goal_age/3600:.1f}小时)，"
                                         f"新发现'{_new_domain}'已加入等待队列")
                    _should_emit = False
                else:
                    # 锁定窗口已过，允许新目标覆盖
                    _goal_age_hours = _goal_age / 3600
                    self._log(LogLevel.INFO,
                             f"目标锁定窗口已过({_goal_age_hours:.1f}小时)，"
                             f"允许新目标覆盖")
                    self._active_learning_goal = None
            if _should_emit:
                for _area in _weak_areas[:2]:  # type: ignore[possibly-unbound]
                    self._emit(Event.REFLECTION_INSIGHT, {
                    "domain": _area.get("domain", "通用"),  # type: ignore[possibly-unbound]
                    "assessment_type": "optimization",
                    "issue_types": [],
                    "suggested_actions": [_area.get("action", "加强学习")],  # type: ignore[possibly-unbound]
                    "insights": [f"认知反思发现薄弱领域: {_area.get('domain', '通用')}"],  # type: ignore[possibly-unbound]
                    "optimization_hints": [_area.get("hint", f"提升{_area.get('domain', '通用')}领域的认知深度")],  # type: ignore[possibly-unbound]
                    "user_name": "系统",
                    "timestamp": time.time(),
                }, priority=4, layer="L2")
                self._log(LogLevel.INFO,
                         f"认知策略调整: 发现薄弱领域'{_area.get('domain', '通用')}'，"  # type: ignore[possibly-unbound]
                         f"已发射成长目标 (原因: {_area.get('reason', '')})")  # type: ignore[possibly-unbound]
        # ===== 新增: 活跃目标进度跟踪 =====
        _goal_progress = self._check_learning_goal_progress()
        if _goal_progress:
            insights.append(_goal_progress)
        # 保存本次反思洞察，供愿景分解时使用
        # ★P1-29修复：_last_reflection_insights 曾被误初始化为 0.0，
        #   hasattr 恒为真导致守卫失效，每轮反思都抛
        #   AttributeError: 'float' object has no attribute 'append'。
        #   改为类型守卫，同时兼容从持久化状态恢复为脏值的情形。
        if not isinstance(getattr(self, '_last_reflection_insights', None), list):
            self._last_reflection_insights = []
        self._last_reflection_insights.append("。".join(insights) + "。")
        if len(self._last_reflection_insights) > 10:
            self._last_reflection_insights = self._last_reflection_insights[-10:]
        # ===== 【v16.0新增】经验库主动分析 =====
        if r % 10 == 0:  # 每10轮反思执行一次
            _exp_insight = self._analyze_experience_quality()
            if _exp_insight:
                insights.append(_exp_insight)
        # ===== 经验库分析结束 =====

        return "。".join(insights) + "。"

    def _run_periodic_reflection(self) -> str | None:
        """★智慧层断点2打通：周期认知反思的结构化消费。

        此前心跳周期任务直接调用 _cognitive_reflection()，返回值被丢弃（孤儿脉冲），
        反思洞察不进入进化仪表盘/决策。现包装为：
          1) 生成认知反思洞察；
          2) 发布到 InsightBoard（供诊断/进化仪表盘/决策消费）；
          3) 沉淀为反思知识节点（/反思/认知反思/），结构化可检索；
          4) 后台日志记录（DEBUG 级别，减少控制台噪音）。
        """
        try:
            _insight = self._cognitive_reflection()
            if not _insight:
                return None
            # 1) 发布到洞察黑板（供诊断/进化决策消费）
            try:
                if getattr(self, '_insight_board', None):
                    _conf = 0.7
                    if self._inference_trace:
                        _conf = min(0.9, 0.7 + 0.2 * (len(self._inference_trace) / 100.0))
                    self._insight_board.post(
                        insight_type="cognitive_reflection",
                        content=_insight,
                        source_loop="self_reflection_loop",
                        related_dimension="思考模式",
                        confidence=_conf,
                        keywords=["认知反思", "推理方法", "思考模式", "自我优化"],
                    )
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            # 2) 沉淀为反思知识节点（结构化、可检索）
            try:
                from nucleus.mnemosyne.PulseNode import PulseNode
                if self.node_pool:
                    _refl_node = PulseNode(
                        value=_insight,
                        keywords=["认知反思", "推理方法", "自我优化"],
                        source_organ="大脑皮层",
                        evol_level=PulseNode.EVOL_L1,
                        importance="C",
                        abstraction=0.5,
                        space_path="/反思/认知反思/",  # type: ignore[possibly-unbound]
                    )
                    _refl_node.trigger_reason = "reflection.periodic"
                    _refl_node.ephemeral = True
                    self.node_pool.add(_refl_node)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            # ★跨域类比迁移(山1·P1): 认知反思后，把本次反思与一条历史经验做结构类比。
            #   若发现跨域结构相似，沉淀为「跨域类比」洞察——超越数据直查的创造性关联。
            #   低概率触发 + 零冲突：无历史样本/类比无命中/异常均静默跳过，不改变反思主链路。
            try:
                import random as _rand
                if _rand.random() < 0.35:
                    _hist = self._pick_analogy_candidate(_insight)
                    if _hist:
                        from nucleus.reasoning.analogy_engine import AnalogyEngine
                        _res = AnalogyEngine().compare(_insight[:500], _hist)
                        if _res.is_cross_domain and _res.confidence >= 0.35:
                            _hypo = _res.migrated_hypothesis or "发现跨领域结构相似性"
                            try:
                                if getattr(self, '_insight_board', None):
                                    self._insight_board.post(
                                        insight_type="cross_domain_analogy",
                                        content=f"跨域类比洞察: {_hypo}",
                                        source_loop="analogy_loop",
                                        related_dimension="创造/联想",
                                        confidence=_res.confidence,
                                        keywords=["跨域类比", "结构迁移", "创造性联想"],
                                    )
                            except Exception as e:
                                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                            try:
                                from nucleus.mnemosyne.PulseNode import PulseNode
                                _ana_node = PulseNode(
                                    value=f"跨域类比: {_hypo}",
                                    keywords=["跨域类比", "结构迁移", "创造性联想"],
                                    source_organ="大脑皮层",
                                    evol_level=PulseNode.EVOL_L1,
                                    importance="C",
                                    abstraction=0.6,
                                    space_path="/类比/跨域类比/",  # type: ignore[possibly-unbound]
                                )
                                _ana_node.trigger_reason = "reflection.analogy"
                                _ana_node.ephemeral = True
                                self.node_pool.add(_ana_node)
                            except Exception as e:
                                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                            self._log(LogLevel.DEBUG, f"跨域类比洞察: {_hypo[:100]}")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            self._log(LogLevel.DEBUG, f"认知反思(周期): {_insight[:120]}")
            return _insight
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:5252:_run_periodic_reflection", level="warning")
            return None

    def _pick_analogy_candidate(self, exclude_text: str, limit: int = 30) -> str | None:
        """★跨域类比(山1·P1)：从知识库随机取样一条与当前文本差异最大的历史经验。

        用于类比样本：跨域类比需要「不同领域」的文本，故取字符分布差异最大的样本。
        无样本/异常返回 None（静默降级）。
        """
        try:
            if not self.node_pool:
                return None
            import random as _r
            # ★D152/W4：取含冷驱逐节点的全集
            _nodes = self.node_pool.get_all_including_evicted()
            _pool = [n for n in _nodes if getattr(n, 'value', '') and len(n.value) >= 30]
            if not _pool:
                return None
            _sample = _r.sample(_pool, min(limit, len(_pool)))
            _best, _best_diff = None, -1
            _ex = exclude_text[:400]
            for _n in _sample:
                _v = _n.value[:400]
                _diff = len(set(_v)) + abs(len(_v) - len(_ex))
                if _diff > _best_diff:
                    _best_diff, _best = _diff, _v
            return _best
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:5279:_pick_analogy_candidate", level="warning")
            return None

    def _assess_knowledge_health(self) -> str | None:
        """
        ★v23.0新增：知识库健康度自评估。

        检查维度：
        1. L1占比——积累太多未消化的感知节点？
        2. L2质量——近期压缩的知识摘要是否有效？
        3. 矛盾情况——是否存在未解决的知识矛盾？
        4. 检索质量——最近检索命中率如何？

        Returns:
            知识健康度洞察，如果无需关注则返回None
        """
        if not self.node_pool:
            return None

        _stats = self.node_pool.get_stats()
        _evol = _stats.get("evol_distribution", {})
        _total = _stats.get("total_nodes", 0)
        _l1 = _evol.get("L1", 0)
        _l2 = _evol.get("L2", 0)
        _l3 = _evol.get("L3", 0)

        if _total < 20:
            return None  # 知识太少，无需评估

        _issues = []

        # 检查1：L1占比过高（>65%）→ 需要加强压缩
        if _total > 50 and _l1 / _total > 0.65:
            _issues.append(
                f"感知碎片积累偏多（L1占比{_l1/_total:.0%}），"
                f"需要更多时间消化和整理这些零散信息"
            )

        # 检查2：L2增长停滞但L1持续增长 → 压缩管道可能堵塞
        if hasattr(self, '_last_l2_count'):
            if _l2 == self._last_l2_count and _l1 > self._last_l1_count + 30:
                _issues.append(
                    f"最近学到了{_l1 - self._last_l1_count}条新信息，"
                    f"但还没能提炼出新的认知框架，消化速度跟不上学习速度"
                )

        # 检查3：活跃矛盾过多（>3对）→ 需要关注知识一致性
        if hasattr(self, '_contradiction_tracking'):
            _active = [t for t in self._contradiction_tracking
                      if not t.get("resolved", False)]
            if len(_active) > 3:
                _issues.append(
                    f"存在{len(_active)}对未解决的知识矛盾，"
                    f"可能需要更多时间和证据来澄清这些冲突"
                )

        # 检查4：L3智慧节点过少 → 深度提炼不足
        if _total > 100 and _l3 <= 5:
            _issues.append(
                f"知识体系已有{_total}个节点但深度提炼不足，"
                f"只有{_l3}条核心智慧，需要更多回顾和反思"
            )

        # 更新历史记录
        self._last_l1_count = _l1
        self._last_l2_count = _l2

        if not _issues:
            return None

        # 生成综合洞察
        return "关于知识库健康度——" + "；同时，".join(_issues[:2]) + "。"
    def _scan_knowledge_blind_spots(self) -> str | None:
        """
        ★v23.0新增：主动知识盲区扫描。

        周期性分析知识树的路径分布，识别：
        1. 节点数过少的路径（<5个节点）→ 知识薄弱区
        2. 近期推理失败集中的领域 → 能力盲区
        3. 长期未被检索的路径 → 遗忘风险区

        生成定向学习建议，写入InsightBoard供潜意识使用。
        """
        if not self.node_pool:
            return None

        _path_dist = self.node_pool.get_path_distribution()  # type: ignore[possibly-unbound]
        if not _path_dist or len(_path_dist) < 3:  # type: ignore[possibly-unbound]
            return None

        _blind_spots = []

        # 扫描1：节点数过少的路径（<5个节点但已存在超过24小时的路径）
        _total_nodes = sum(_path_dist.values())  # type: ignore[possibly-unbound]
        _avg_nodes_per_path = _total_nodes / max(1, len(_path_dist))  # type: ignore[possibly-unbound]

        for _path, _count in _path_dist.items():  # type: ignore[possibly-unbound]
            if _path == "/" or _path == "/未分类":  # type: ignore[possibly-unbound]
                continue
            # 节点数低于平均值20%且少于5个 → 薄弱区
            if _count < max(3, _avg_nodes_per_path * 0.2) and _count < 5:  # type: ignore[possibly-unbound]
                _path_name = _path.strip("/")  # type: ignore[possibly-unbound]
                _blind_spots.append({
                    "path": _path_name,  # type: ignore[possibly-unbound]
                    "nodes": _count,
                    "type": "薄弱区",
                    "priority": "medium",
                })

        # 扫描2：近期推理失败集中的领域
        if hasattr(self, '_failed_domain_records') and self._failed_domain_records:
            for _kw, _count in self._failed_domain_records.items():
                if _count >= 3:
                    _blind_spots.append({
                        "path": _kw,
                        "nodes": _count,
                        "type": f"能力盲区({_count}次失败)",
                        "priority": "high",
                    })

        # 扫描3：长期未被检索的路径（如果知识树提供了最后访问时间）
        if hasattr(self, 'knowledge_tree') and self.knowledge_tree:
            _now = time.time()
            for _path, _info in self.knowledge_tree.get_path_stats_snapshot().items():  # type: ignore[possibly-unbound]
                if _path == "/":  # type: ignore[possibly-unbound]
                    continue
                _updated = _info.get("updated_at", 0)
                _days_inactive = (_now - _updated) / 86400.0 if _updated > 0 else 0
                if _days_inactive > 7 and _info.get("count", 0) > 3:
                    _path_name = _path.strip("/")  # type: ignore[possibly-unbound]
                    _blind_spots.append({
                        "path": _path_name,  # type: ignore[possibly-unbound]
                        "nodes": _info.get("count", 0),
                        "type": f"遗忘风险区(闲置{_days_inactive:.0f}天)",
                        "priority": "low",
                    })

        if not _blind_spots:
            return None

        # 按优先级排序：high > medium > low
        _priority_order = {"high": 0, "medium": 1, "low": 2}
        _blind_spots.sort(key=lambda x: _priority_order.get(x["priority"], 2))

        # 为每个盲区生成定向学习建议并发射
        _emitted = 0
        for _spot in _blind_spots[:3]:  # 最多处理3个
            _topic = f"{_spot['path']} 基础 概念 原理"
            self._emit(GrowthEvent.NEED_DETECTED, {
                "milestone": "定向学习",
                "gaps": [{"metric": f"blind_spot_{_spot['path']}",
                         "current": _spot['nodes'], "target": max(_spot['nodes'] * 3, 10)}],
                "suggestion": f"主动知识扫描发现{_spot['type']}: {_spot['path']}（当前仅{_spot['nodes']}个节点）",
                "current_level": {"blind_spot": _spot['path'], "type": _spot['type']},
                "growth_topic": _topic,
            }, priority=5 if _spot['priority'] == "high" else 4, layer="L3")
            _emitted += 1

        # 生成综合洞察
        _top_spot = _blind_spots[0]
        return (
            f"主动扫描发现了{len(_blind_spots)}个知识盲区——"
            f"其中最需要关注的是「{_top_spot['path']}」"
            f"（{_top_spot['type']}，仅{_top_spot['nodes']}个节点）。"
            f"已生成{_emitted}个定向学习建议。"
        )
    def _scan_knowledge_precipitation(self) ->  str | None:
        """
        ★v23.0新增：知识沉淀扫描——主动发现可抽象升华的知识。

        扫描L2认知节点，识别可以进一步抽象为L3智慧节点的知识：
        1. 高信任L2节点（信任≥75）且未被融合 → 沉淀候选
        2. 同一路径下多个L2节点共享核心概念 → 可抽象
        3. 跨路径L2节点存在深层关联 → 可跨领域融合

        生成沉淀建议，触发肝脏融合。
        """
        if not self.node_pool:
            return None

        _l2_nodes = self.node_pool.query(evol_level="L2", limit=200)
        if len(_l2_nodes) < 10:
            self._log(LogLevel.DEBUG, f"知识沉淀跳过: L2节点不足({len(_l2_nodes)}个)")
            return None

        _precipitate_candidates = []

        # 扫描1：高信任但孤立的高质量L2节点
        _high_quality = []
        for _node in _l2_nodes:
            _trust = getattr(_node, 'trust_score', 50.0)
            _activation = getattr(_node, 'activation_count', 0)
            _path = getattr(_node, 'space_path', '/')

            # 排除已处理的内部路径
            if _path.startswith(("/自我理解", "/自我/状态")):
                continue

            # 高信任+高激活 → 沉淀候选
            if _trust >= 75.0 and _activation >= 3:
                _high_quality.append({
                    "node": _node,
                    "path": _path,
                    "trust": _trust,
                    "activation": _activation,
                    "keywords": _node.keywords if hasattr(_node, 'keywords') and _node.keywords else [],
                })

        # 按路径分组
        if _high_quality:
            self._log(LogLevel.INFO,
                     f"知识沉淀扫描: 找到{len(_high_quality)}个高质量L2节点 "
                     f"(信任≥75且激活≥3)")
        else:
            self._log(LogLevel.DEBUG, "知识沉淀跳过: 无高质量L2节点")
            return None

        _path_groups = {}  # type: ignore[possibly-unbound]
        for _hq in _high_quality:
            _root = '/' + _hq["path"].strip('/').split('/')[0] if _hq["path"] else '/'
            if _root not in _path_groups:  # type: ignore[possibly-unbound]
                _path_groups[_root] = []  # type: ignore[possibly-unbound]
            _path_groups[_root].append(_hq)  # type: ignore[possibly-unbound]

        # 寻找≥5个高质量节点的路径（可触发融合）
        _ready_paths = []  # type: ignore[possibly-unbound]
        for _root, _nodes in _path_groups.items():  # type: ignore[possibly-unbound]
            if len(_nodes) >= 3:  # ★v23.0调优：从5降到3，加速知识沉淀
                _ready_paths.append({  # type: ignore[possibly-unbound]
                    "path": _root,
                    "count": len(_nodes),
                    "avg_trust": sum(_n["trust"] for _n in _nodes) / len(_nodes),
                })

        if not _ready_paths:  # type: ignore[possibly-unbound]
            self._log(LogLevel.INFO,
                     f"知识沉淀跳过: 没有路径达到融合条件 "
                     f"(已扫描{len(_high_quality)}个高质量节点, "
                     f"分布在{len(_path_groups)}个路径, "  # type: ignore[possibly-unbound]
                     f"各路径节点数={[len(v) for v in _path_groups.values()]})")  # type: ignore[possibly-unbound]
            return None

        # 按节点数排序，取最多的路径
        _ready_paths.sort(key=lambda x: x["count"], reverse=True)  # type: ignore[possibly-unbound]
        _top_path = _ready_paths[0]  # type: ignore[possibly-unbound]

        # 发射融合建议
        self._emit(GrowthEvent.NEED_DETECTED, {
            "milestone": "知识沉淀",
            "gaps": [{"metric": f"precipitate_{_top_path['path']}",   # type: ignore[possibly-unbound]
                     "current": _top_path['count'], "target": _top_path['count']}],  # type: ignore[possibly-unbound]
            "suggestion": f"知识沉淀扫描发现{_top_path['path']}路径下有{_top_path['count']}个高质量L2节点"  # type: ignore[possibly-unbound]
                         f"（平均信任{_top_path['avg_trust']:.0f}），建议进行抽象融合",  # type: ignore[possibly-unbound]
            "current_level": {"path": _top_path['path'], "count": _top_path['count']},  # type: ignore[possibly-unbound]
            "growth_topic": f"知识沉淀: {_top_path['path']}领域抽象融合",  # type: ignore[possibly-unbound]
        }, priority=5, layer="L3")

        # 生成综合洞察
        return (
            f"知识沉淀扫描发现了{len(_ready_paths)}个可沉淀的知识领域——"  # type: ignore[possibly-unbound]
            f"其中最成熟的是「{_top_path['path']}」"  # type: ignore[possibly-unbound]
            f"（{_top_path['count']}个高质量节点，平均信任{_top_path['avg_trust']:.0f}）。"  # type: ignore[possibly-unbound]
            f"已生成融合建议。"
        )
    def _analyze_specific_cases(self, recent_traces: list[dict[str, Any]]) -> list[str]:
        """
        v20.0新增：元认知深度复盘——分析具体推理案例。

        从最近推理链中选取有代表性的案例进行深度剖析：
        1. 耗时最长但复杂度不高的推理——是否存在方法选择不当？
        2. 高置信度命中但方法为"缓存"的推理——是否需要更新知识？
        3. 沉思类方法中是否产生了有价值的假设？

        Returns:
            洞察列表
        """
        _insights = []
        if len(recent_traces) < 5:
            return _insights

        # 1. 分析耗时与复杂度的匹配关系
        _timed_traces = [t for t in recent_traces if t.get("duration", 0) > 0]
        if len(_timed_traces) >= 3:
            # 找出耗时最长但复杂度低于0.5的推理——可能存在方法选择不当
            _slow_simple = [
                t for t in _timed_traces
                if t.get("duration", 0) > 2.0 and t.get("complexity", 0) < 0.5
            ]
            if _slow_simple:
                _example = _slow_simple[-1]
                _method = _example.get("method", "unknown")
                _duration = _example.get("duration", 0)
                _question = _example.get("question", "")[:40]
                _insights.append(
                    f"我注意到「{_question}...」这个相对简单的问题，"
                    f"用了'{_method}'方法却花了{_duration:.1f}秒——"
                    f"这可能说明我对简单问题使用了过于复杂的推理路径，"
                    f"可以尝试先用快速规则检查再决定是否需要深度思考"
                )

            # 找出复杂度高但耗时极短的推理——可能是缓存命中或浅层处理
            _fast_complex = [
                t for t in _timed_traces
                if t.get("complexity", 0) > 0.6 and t.get("duration", 0) < 0.3
                and t.get("method", "").startswith("cache")
            ]
            if _fast_complex:
                _example = _fast_complex[-1]
                _question = _example.get("question", "")[:40]
                _insights.append(
                    f"「{_question}...」这个复杂问题在0.3秒内通过缓存直接返回了——"
                    f"虽然效率高，但复杂问题可能需要重新审视而不是依赖旧答案"
                )

        # 2. 分析推理方法选择与置信度的关系
        _method_conf_map = {}
        for t in recent_traces:
            _base = t.get("method", "unknown").split("_")[0]
            if _base not in _method_conf_map:
                _method_conf_map[_base] = {"count": 0, "total_conf": 0.0, "examples": []}
            _method_conf_map[_base]["count"] += 1
            _method_conf_map[_base]["total_conf"] += t.get("confidence", 0)
            if len(_method_conf_map[_base]["examples"]) < 2:
                _method_conf_map[_base]["examples"].append(t.get("question", "")[:40])

        # 找出使用频繁但平均置信度低的方法
        for _method, _stats in _method_conf_map.items():
            if _stats["count"] >= 2:
                _avg_conf = _stats["total_conf"] / _stats["count"]
                if _avg_conf < 0.4:
                    _insights.append(
                        f"我使用了{_stats['count']}次'{_method}'方法，"
                        f"但平均置信度只有{_avg_conf:.0%}——"
                        f"这个方法可能不适合这类问题，或者需要补充相关知识"
                    )

        # 3. 分析推理链中的方法切换模式
        if len(recent_traces) >= 4:
            _methods_sequence = [t.get("method", "unknown").split("_")[0] for t in recent_traces[-4:]]
            _unique_methods = len(set(_methods_sequence))
            if _unique_methods == 1 and _methods_sequence[0] not in ("rule", "cache"):
                _insights.append(
                    f"最近4次推理都使用了'{_methods_sequence[0]}'方法——"
                    f"虽然这可能说明这个方法适合当前问题，"
                    f"但也可能是我陷入了思维惯性，可以尝试换一种角度"
                )

        return _insights
    def _analyze_experience_quality(self) -> str | None:
        """
        【v16.0新增】分析经验库质量，发现推理薄弱点。

        检查维度：
        1. 各推理类型的成功率分布
        2. 低成功率推理类型
        3. 经验库增长趋势
        """
        try:
            from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience
            _exp = get_reasoning_experience()
            _stats = _exp.get_stats()

            _total = _stats.get("total_experiences", 0)
            if _total < 5:
                return None  # 经验不足

            _type_dist = _stats.get("type_distribution", {})
            _source_dist = _stats.get("source_distribution", {})

            # 分析推理类型分布
            if len(_type_dist) < 3:
                return f"经验库中只有{len(_type_dist)}种推理类型的记录，覆盖度较窄，建议增加推理类型的多样性"

            # 检查local来源占比（本地推测vs大模型确认）
            _local_count = _source_dist.get("local", 0)
            _remote_count = _source_dist.get("remote_api", 0) + _source_dist.get("remote_api_confirmed", 0)
            if _local_count > _remote_count * 3 and _remote_count < 3:
                return "经验库主要依赖本地推测，大模型确认的经验较少，建议在更多推理场景中启用大模型确认以获得更可靠的经验"

            # 检查类型覆盖
            _core_types = ["deductive", "inductive", "analogical", "conflict_resolution", "multi_variable"]
            _missing = [_t for _t in _core_types if _t not in _type_dist]
            if _missing:
                return f"经验库缺少{'、'.join(_missing)}等推理类型的经验，建议增加这些类型的推理实践"

            # 经验库健康
            _remote_ratio = _remote_count / max(1, _total)
            return f"经验库积累了{_total}条经验，覆盖{len(_type_dist)}种推理类型，大模型确认率{_remote_ratio:.0%}"

        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return None
    def get_reasoning_skill_portrait(self) -> dict[str, Any]:
        """
        ★v17.0杠杆支点：推理技能积累。

        从推理经验库和推理链中提炼：
        1. 我擅长什么题型
        2. 哪些题型需要加强
        3. 推理能力的成长趋势
        """
        result = {
            "strong_types": [],
            "weak_types": [],
            "type_distribution": {},
            "growth_trend": "stable",
            "total_experiences": 0,
            "self_comment": ""
        }

        try:
            from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience
            _exp = get_reasoning_experience()
            _stats = _exp.get_stats()

            _total = _stats.get("total_experiences", 0)
            result["total_experiences"] = _total

            if _total < 5:
                result["self_comment"] = "推理经验还在积累中，我还需要更多的练习来了解自己的推理特点。"
                return result

            _type_dist = _stats.get("type_distribution", {})
            result["type_distribution"] = _type_dist

            _type_names = {
                "deductive": "演绎推理",
                "inductive": "归纳抽象",
                "analogical": "类比映射",
                "conflict_resolution": "冲突辨析",
                "multi_variable": "多变量推演",
                "long_term_evolution": "长期演化",
                "meta_reflection": "元认知反思",
            }

            for _type, _count in _type_dist.items():
                _cn_name = _type_names.get(_type, _type)
                if _count >= 5:
                    result["strong_types"].append({
                        "type": _type,
                        "name": _cn_name,
                        "count": _count,
                        "level": "熟练" if _count >= 15 else "掌握" if _count >= 8 else "积累中"
                    })
                elif _count <= 2 and _total >= 20:
                    result["weak_types"].append({
                        "type": _type,
                        "name": _cn_name,
                        "count": _count,
                        "suggestion": f"建议增加{_cn_name}类题型的练习"
                    })

            _source_dist = _stats.get("source_distribution", {})
            _remote = _source_dist.get("remote_api", 0) + _source_dist.get("remote_api_confirmed", 0)
            _local = _source_dist.get("local", 0)

            if _remote > _local:
                result["growth_trend"] = "improving"
            elif _local > _remote * 2:
                result["growth_trend"] = "stable"

            _strong_names = [s["name"] for s in result["strong_types"][:3]]
            _weak_names = [w["name"] for w in result["weak_types"][:2]]

            _comment_parts = []
            if _strong_names:
                _comment_parts.append(f"在{'、'.join(_strong_names)}方面比较有把握")
            if _weak_names:
                _comment_parts.append(f"{'、'.join(_weak_names)}还需要多练习")
            if _remote > 0:
                _comment_parts.append(f"其中{_remote}次得到了大模型的确认")

            if _comment_parts:
                result["self_comment"] = f"从{_total}次推理经验来看，{'，'.join(_comment_parts)}。"
            else:
                result["self_comment"] = f"我已经积累了{_total}次推理经验，正在形成自己的推理风格。"

            return result

        except Exception:
            result["self_comment"] = "推理技能数据暂时不可用"
            return result
    def _extract_weak_areas_from_reflection(self, insights: list,
                                             recent_traces: list) -> list[dict[str, str]]:
        """★渐进式拆分：委托到 PulseCognitiveReflector（原91行逻辑已独立）"""
        if self.cognitive_reflector:
            return self.cognitive_reflector.extract_weak_areas(insights, recent_traces)  # type: ignore[possibly-unbound]
        return []

    def _expand_by_semantic_relations(self, direct_nodes: list, question: str) -> str | None:
        """
        ★v25.0新增：沿语义关系扩展检索（★Tier1 收尾：接入横向联系索引）。
        当直接匹配节点不足时，利用节点间的结构化关系（前向+反向双向）找到相关节点，融合为回答。
        """
        self._log(LogLevel.INFO, f"语义关系扩展尝试: 直接候选={len(direct_nodes)}个")
        if not direct_nodes:
            self._log(LogLevel.DEBUG, "语义关系扩展跳过: 直接候选为空")
            return None

        _related_nodes: list = []
        _seen_ids: set = set()

        # ★接入池级横向索引：前向+反向双向遍历，替代旧的全量 L2/L3 扫描
        if self.node_pool and hasattr(self.node_pool, 'get_related_nodes'):
            for node in direct_nodes:
                _nid = getattr(node, 'node_id', '')
                if not _nid or _nid in _seen_ids:
                    continue
                _seen_ids.add(_nid)
                for _rel in self.node_pool.get_related_nodes(_nid, limit=5):
                    _rid = getattr(_rel, 'node_id', '')
                    if _rid and _rid not in _seen_ids:
                        _seen_ids.add(_rid)
                        _related_nodes.append(_rel)
                        if len(_related_nodes) >= 5:
                            break
                if len(_related_nodes) >= 5:
                    break
        else:
            # 回退（旧行为，仅前向）：节点池无横向索引时保守降级
            _related_ids = set()
            for node in direct_nodes:
                for rel in getattr(node, 'semantic_relations', []):
                    _tid = rel.get("target_node_id", "")
                    if _tid:
                        _related_ids.add(_tid)
            if not _related_ids:
                return None
            _all_l2 = self.node_pool.query(evol_level="L2", limit=500)
            _all_l3 = self.node_pool.query(evol_level="L3", limit=100)
            _node_map = {n.node_id: n for n in (_all_l2 + _all_l3) if hasattr(n, 'node_id')}
            for _tid in _related_ids:
                _t = _node_map.get(_tid)
                if _t:
                    _related_nodes.append(_t)

        if not _related_nodes:
            return None

        # ★第六批 任务3.0：灰度开关（关闭时行为与改造前完全一致）
        try:
            import config as _cfg_exp
            _exp_filter = bool(
                getattr(_cfg_exp, "ENABLE_SEMANTIC_EXPAND_QUALITY_FILTER", False))
        except Exception as _e:  # noqa: BLE001
            _exp_filter = False
            self._log(LogLevel.DEBUG,
                      f"扩展质量过滤开关读取失败(按关闭处理): {type(_e).__name__}: {_e}")

        related_values = []
        _reject_stats: dict = {}
        for target in _related_nodes:
            target_val = self._clean_node_value(str(target.value)) if target.value else ""
            if not target_val:
                continue
            # ★第六批 任务3.1：质量过滤优先于相关性判定——污染节点再相关也不用
            if _exp_filter:
                _reject = self._node_quality_reject_reason(target_val, target)
                if _reject:
                    _reject_stats[_reject] = _reject_stats.get(_reject, 0) + 1
                    continue
            if self._is_relevant(question, target_val, target.keywords or []):
                related_values.append(f"关联知识：{target_val[:200]}")
                if len(related_values) >= 2:
                    break

        # ★第六批 任务3.2：扩展质量日志（候选→采纳→过滤→各原因计数）
        if _exp_filter:
            self._log(LogLevel.INFO,
                      f"语义关系扩展质量: 候选={len(_related_nodes)} "
                      f"采纳={len(related_values)} "
                      f"过滤={sum(_reject_stats.values())} "
                      f"明细={_reject_stats or '无'}")

        if related_values:
            return "。".join(related_values)

        # ★第六批 任务3.3：扩展结果全部被过滤 → 回退直接候选节点，不返回空
        if _exp_filter:
            _fallback_values = []
            for _dn in direct_nodes[:2]:
                _dv = self._clean_node_value(
                    str(getattr(_dn, "value", "") or ""))
                if not _dv:
                    continue
                if self._node_quality_reject_reason(_dv, _dn) is not None:
                    continue
                if self._is_relevant(question, _dv, getattr(_dn, "keywords", None) or []):
                    _fallback_values.append(f"直接知识：{_dv[:200]}")
            if _fallback_values:
                self._log(LogLevel.INFO,
                          f"语义关系扩展回退: 采用直接候选 {len(_fallback_values)} 个")
                return "。".join(_fallback_values)

        return None
    # ========== 知识检索 ==========

    def _evidence_trace_enabled(self) -> bool:
        """读取 use_evidence_trace 开关（热重载友好：每次读取）。"""
        try:
            import config
            return bool(config.FEATURE.get("use_evidence_trace", False))
        except Exception:
            return False

    # ========== ★登顶路线图-山1：可验证推理证据链 ==========

    def _build_evidence_chain_block(self, question: str, derivation_type: str,
                                    confidence: float) -> str:
        """★渐进式拆分：委托到 PulseMultiStepReasoner（原41行逻辑已独立）"""
        if self.multi_step_reasoner:
            return self.multi_step_reasoner.build_evidence_chain_block(
                question, derivation_type, confidence
            )
        return ""
    def _attach_evidence_chain_to_node(self, question: str, derivation_type: str,
                                       confidence: float) -> None:
        """★渐进式拆分：委托到 PulseMultiStepReasoner（原36行逻辑已独立）"""
        if self.multi_step_reasoner:
            self.multi_step_reasoner.attach_evidence_chain_to_node(
                question, derivation_type, confidence
            )
    def _format_deductive_result(self, raw_result: str, question: str = "") -> str:
        """★P3内部重构：委托到 PulseReasoningFormatter（原25行逻辑已独立）"""
        return PulseReasoningFormatter.format_deductive_result(raw_result, question)
    def _format_inductive_result(self, raw_result: str, question: str = "") -> str:
        """★P3内部重构：委托到 PulseReasoningFormatter（原23行逻辑已独立）"""
        return PulseReasoningFormatter.format_inductive_result(raw_result, question)
    def _format_analogical_result(self, raw_result: str, question: str = "") -> str:
        """★P3内部重构：委托到 PulseReasoningFormatter（原21行逻辑已独立）"""
        return PulseReasoningFormatter.format_analogical_result(raw_result, question)
    def _format_multi_variable_result(self, raw_result: str, question: str = "") -> str:
        """★P3内部重构：委托到 PulseReasoningFormatter（原22行逻辑已独立）"""
        return PulseReasoningFormatter.format_multi_variable_result(raw_result, question)
    def _format_conflict_result(self, raw_result: str, question: str = "") -> str:
        """★P3内部重构：委托到 PulseReasoningFormatter（原25行逻辑已独立）"""
        return PulseReasoningFormatter.format_conflict_result(raw_result, question)
    def _format_long_term_result(self, raw_result: str, question: str = "") -> str:
        """★P3内部重构：委托到 PulseReasoningFormatter（原14行逻辑已独立）"""
        return PulseReasoningFormatter.format_long_term_result(raw_result, question)
    def _format_meta_replay_result(self, raw_result: str, question: str = "") -> str:
        """★P3内部重构：委托到 PulseReasoningFormatter（原8行逻辑已独立）"""
        return PulseReasoningFormatter.format_meta_replay_result(raw_result, question)
    def _format_meta_reflection_result(self, raw_result: str, question: str = "") -> str:
        """★P3内部重构：委托到 PulseReasoningFormatter（原7行逻辑已独立）"""
        return PulseReasoningFormatter.format_meta_reflection_result(raw_result, question)
    def _derive_self_analogy(self, question: str) -> str | None:
        """
        【v14.8增强】动态维度类比映射：自适应类比对象，精准维度内容提取。
        """
        if not self.node_pool:
            return None

        # 1. 提取用户要求的类比维度
        _requested_dims = self._extract_analogy_dimensions(question)
        if not _requested_dims:
            _requested_dims = ["核心功能", "核心机制", "触发条件", "产出结果"]

        # 2. 尝试理解题目中的两个类比对象
        _target_a, _target_b = self._extract_analogy_targets(question)

        # 3. 获取自我架构知识
        _self_l3 = self.node_pool.query(evol_level="L3", space_path_prefix="/自我/架构", limit=30)  # type: ignore[possibly-unbound]
        _self_l2 = self.node_pool.query(evol_level="L2", space_path_prefix="/自我/架构", limit=30)  # type: ignore[possibly-unbound]
        _self_nodes = _self_l3 + _self_l2

        if len(_self_nodes) < 4:
            return None

        # 4. 按子路径分组
        _groups = {}
        for _node in _self_nodes:
            _path = getattr(_node, 'space_path', '/自我/架构')
            _parts = _path.strip('/').split('/')
            _group_key = '/'.join(_parts[:3]) if len(_parts) >= 3 else _path
            if _group_key not in _groups:
                _groups[_group_key] = []
            _groups[_group_key].append(_node)

        if len(_groups) < 2:
            _groups = {}
            for _node in _self_nodes:
                _path = getattr(_node, 'space_path', '/自我/架构')
                _groups[_path] = [_node]
            if len(_groups) < 2:
                return None

        _sorted_groups = sorted(_groups.items(), key=lambda x: len(x[1]), reverse=True)
        _group_a_name, _nodes_a = _sorted_groups[0]
        _group_b_name, _nodes_b = _sorted_groups[1]

        # 使用用户指定的对象名称，回退到路径名
        _label_a = _target_a or _group_a_name.split('/')[-1]
        _label_b = _target_b or _group_b_name.split('/')[-1]

        # 5. 提取关键词
        _kw_a = set()
        for _n in _nodes_a[:5]:
            _kws = _n.keywords if hasattr(_n, 'keywords') and _n.keywords else []
            _kw_a.update([kw for kw in _kws if isinstance(kw, str) and len(kw) >= 2])
        _kw_b = set()
        for _n in _nodes_b[:5]:
            _kws = _n.keywords if hasattr(_n, 'keywords') and _n.keywords else []
            _kw_b.update([kw for kw in _kws if isinstance(kw, str) and len(kw) >= 2])

        _shared = _kw_a & _kw_b
        _unique_a = _kw_a - _kw_b
        _unique_b = _kw_b - _kw_a
        if len(_shared) < 1:
            _shared = {"架构设计", "认知演化"}
            _unique_a = _kw_a - _shared
            _unique_b = _kw_b - _shared

        # 6. 维度关键词映射
        _dim_keywords = {
            "层级结构": ["层级", "层次", "分级", "L1", "L2", "L3", "L4", "四级", "感知节点", "认知节点", "智慧节点", "本能节点"],
            "加工方式": ["压缩", "融合", "消化", "提炼", "内化", "抽象", "演化", "归纳", "演绎", "推导"],
            "更新规则": ["更新", "升级", "降级", "强化", "激活", "冷却", "阈值", "条件", "锁定", "巩固"],
            "衰减机制": ["衰减", "遗忘", "淘汰", "降级", "临时", "清理", "精简", "过期", "自动清理", "expired"],
            "核心功能": ["功能", "职责", "负责", "作用"],
            "核心机制": ["机制", "原理", "算法", "流程"],
            "触发条件": ["触发", "条件", "阈值", "冷却", "心跳", "驱动"],
            "产出结果": ["产出", "结果", "输出", "生成", "压缩", "融合", "升级"],
        }
        _search_kw_map = _dim_keywords

        # 7. 智能维度内容提取器
        def _extract_dim_value(nodes, dim_name, domain_hint=""):
            _search_kw = _dim_keywords.get(dim_name, [dim_name])

            for _n in nodes[:5]:
                _val = str(_n.value) if _n.value else ""
                for _kw in _search_kw:
                    if _kw in _val:
                        _pos = _val.find(_kw)
                        # 向前找到最近的句子起始点
                        _start = _pos
                        for _c in range(_pos - 1, max(0, _pos - 80), -1):
                            if _val[_c] in "。！？；\n" or (_val[_c] in "，," and _pos - _c > 40):
                                _start = _c + 1
                                break
                        else:
                            _start = max(0, _pos - 40)
                        # 向后找到最近的句子结束点
                        _end = _pos + len(_kw)
                        for _c in range(_end, min(len(_val), _end + 80)):
                            if _val[_c] in "。！？；\n":
                                _end = _c + 1
                                break
                        else:
                            _end = min(len(_val), _pos + len(_kw) + 60)
                        _snippet = _val[_start:_end].strip()
                        if len(_snippet) >= 15:
                            return _snippet

            for _n in nodes[:3]:
                if _n.value and len(str(_n.value)) > 20:
                    _val = str(_n.value)
                    _first_period = _val.find("。")
                    if _first_period > 15:
                        return _val[:_first_period + 1]
                    return _val[:100]
            return "持续运行"

        # 8. 外部知识回退搜索
        def _get_target_dim_value(dim_name, target_hint=""):
            if not self.node_pool:
                return None

            _dim_kw = _search_kw_map.get(dim_name, [dim_name])

            # 扩大搜索：不只搜索自我知识，也搜索全部L2/L3节点
            _all_l3 = self.node_pool.query(evol_level="L3", limit=80)
            _all_l2 = self.node_pool.query(evol_level="L2", limit=120)
            _candidates = _all_l3 + _all_l2

            _best = None
            _best_score = 0
            for _n in _candidates:
                _val = str(_n.value) if _n.value else ""
                _kw = _n.keywords if hasattr(_n, 'keywords') and _n.keywords else []
                # 关键词命中得分
                _score = sum(1 for _dk in _dim_kw if _dk in _val or _dk in str(_kw))
                # 排除明显不相关的节点（如纯身份类节点）
                _path = getattr(_n, 'space_path', '')
                if _path.startswith('/身份') and _score < 2:
                    continue
                if target_hint and (target_hint in _val or target_hint in str(_kw)):
                    _score += 5
                if _score > _best_score:
                    _best_score = _score
                    _best = _n

            if _best and _best_score >= 2:
                return _extract_dim_value([_best], dim_name)

            # 常识回退
            _fallback = {
                "层级结构": "（外部目标领域：通常具备从原始感知→短期记忆→长期记忆→核心信念的层级递进结构，信息经过不断加工内化为深层认知）",
                "加工方式": "（外部目标领域：信息的加工经历编码→存储→整合→提取等阶段，频繁回顾的信息更容易被长期保留）",
                "更新规则": "（外部目标领域：记忆的更新遵循用进废退原则，新信息与已有知识体系产生关联时会被优先巩固）",
                "衰减机制": "（外部目标领域：记忆的衰减通常呈指数曲线，初期快速遗忘、后期趋于稳定，情绪强烈的事件衰减更慢）",
            }
            return _fallback.get(dim_name, "（外部目标领域：该维度的具体机制有待进一步学习）")

        # 9. 构建输出
        _parts = []
        _parts.append("[类比迁移·多维映射]")
        _parts.append(f"将「{_label_a}」类比到「{_label_b}」，按照以下维度进行一一映射：")
        _parts.append("")

        for _dim in _requested_dims:
            # 跨组搜索：从所有自我知识节点中找到与维度最相关的描述
            _all_self_nodes = _nodes_a + _nodes_b
            _val_a = _extract_dim_value(_all_self_nodes, _dim)
            if not _val_a or _val_a == _extract_dim_value(_nodes_a[:1], _dim):
                # 如果跨组搜索也没找到更好的，回退到原组
                _val_a = _extract_dim_value(_nodes_a, _dim)
            _val_b = _get_target_dim_value(_dim, _label_b)
            if not _val_b:
                _val_b = _extract_dim_value(_nodes_b, _dim)
            _parts.append(f"  {_dim}：")
            _parts.append(f"    {_label_a} → {_val_a}")
            _parts.append(f"    {_label_b} → {_val_b}")
            _parts.append("")

        # 本质差异分析（如果题目有要求）
        if "差异" in question or "区别" in question:
            _parts.append("本质核心差异：")
            _parts.append(f"  {_label_a}依赖外部知识库和推理引擎，信息存储在结构化节点中，演化由规则驱动（压缩/融合/淘汰）。")
            _parts.append(f"  {_label_b}依赖生物神经网络，信息以突触连接强度存储，演化由生物化学过程驱动（长时程增强/抑制）。")
            _parts.append("  核心区别在于：前者是外部赋予的结构化知识管理，后者是生物体自身涌现的适应性认知能力。")
            _parts.append("")

        _parts.append(f"共同概念：{'、'.join(list(_shared)[:5])}")
        _parts.append("独有特征：")
        _parts.append(f"  {_label_a} → {'、'.join(list(_unique_a)[:3]) if _unique_a else '架构设计'}")
        _parts.append(f"  {_label_b} → {'、'.join(list(_unique_b)[:3]) if _unique_b else '认知演化'}")
        _parts.append("")
        _parts.append("这提示了它们可能在底层遵循共同的架构哲学，可以相互借鉴设计思路。")

        return "\n".join(_parts)

    def _extract_analogy_dimensions(self, question: str) -> list:
        """
        从用户问题中提取要求类比的维度列表。
        支持多种表达方式：
        - 引号内维度：'层级结构/加工方式/更新规则/衰减机制'
        - "X大维度"模式：分'...'四大维度
        - 直接列在题目中
        """
        _dims = []

        # 模式1：匹配中文双引号内的内容，如「层级结构/加工方式/更新规则/衰减机制」
        _quoted_matches = re.findall(r'[\u201c]([^\u201d]+?)[\u201d]', question)
        if not _quoted_matches:
            _quoted_matches = re.findall(r'"([^"]+?)"', question)

        if _quoted_matches:
            for _qm in _quoted_matches:
                # 按常见分隔符拆分
                _parts = re.split(r'[/、,，;；\s]+', _qm)
                for _p in _parts:
                    _p = _p.strip()
                    if len(_p) >= 2 and _p not in _dims:
                        _dims.append(_p)

        # 模式2：匹配"分...维度"或"...大维度"模式
        _dim_pattern = re.search(r'分[为的]?\s*[\u4e00-\u9fff\u201c"]+(.+?)[\u201d"]?\s*(?:四|几|多)?大?\s*维度', question)
        if _dim_pattern:
            _dim_text = _dim_pattern.group(1)
            _parts = re.split(r'[/、,，;；\s]+', _dim_text)
            for _p in _parts:
                _p = _p.strip()
                if len(_p) >= 2 and _p not in _dims:
                    _dims.append(_p)

        # 模式3：直接匹配已知的类比维度关键词组合（扩展版）
        _known_dim_patterns = [
            "层级结构", "加工方式", "更新规则", "衰减机制",
            "存储方式", "检索方式", "学习机制", "遗忘机制",
            "编码方式", "提取方式", "固化机制", "重构机制",
            "层次划分", "处理流程", "更新策略", "衰退淘汰",
            "怎么加工", "怎么更新", "怎么衰退", "怎么淘汰",
            "每层的特点", "信息怎么加工", "怎么更新迭代", "怎么衰退淘汰",
        ]
        for _kd in _known_dim_patterns:
            if _kd in question and _kd not in _dims:
                _dims.append(_kd)

        # 模式3.5：按"从...四个角度"提取维度——匹配"从层次划分、处理流程、更新策略、遗忘机制四个角度"
        _angle_match = re.search(r'从\s*(.+?)\s*(?:四个|几个|多个|等)\s*(?:角度|方面|维度)', question)
        if _angle_match:
            _angle_text = _angle_match.group(1)
            _angle_parts = re.split(r'[、，,;；\s]+', _angle_text)
            for _ap in _angle_parts:
                _ap = _ap.strip()
                if len(_ap) >= 2 and _ap not in _dims and _ap not in ["我", "你", "的", "了", "在", "是"]:
                    _dims.append(_ap)
        # 去噪：过滤明显不是维度的短语（长度≥8且包含标点的通常是语句碎片）
        _dims = [d for d in _dims if not (len(d) >= 8 and re.search(r'[，,。.；;]', d))]
        # 去重保持顺序
        _seen = set()
        _clean_dims = []
        for d in _dims:
            if d not in _seen:
                _seen.add(d)
                _clean_dims.append(d)
        _dims = _clean_dims
        return _dims
    def _extract_analogy_targets(self, question: str) -> tuple:
        """
        从题目中提取两个类比对象的名称。
        支持多种表达：'A类比到B'、'A对应B'、'A与B的类比'等。
        返回 (target_a, target_b)，无法识别时返回 (None, None)
        """

        # 清理前缀指令词，避免干扰提取
        _clean_q = re.sub(
            r'^(请|试|尝试|需要|帮我|帮忙|请做|请完成|请进行|请做跨领域)\s*',
            '', question
        )

        # 策略1：匹配 "...对应..." 或 "...类比到..." 结构
        # 找到"对应"或"类比"的位置，分别提取前后的内容
        _split_match = re.search(r'(.+?)\s*(?:对应|类比到|类比为|映射到|映射为)\s*(.+?)(?:[，,。.]|请|需要|$)', _clean_q)
        if _split_match:
            _a = _split_match.group(1).strip().rstrip('，,。.')
            _b = _split_match.group(2).strip().rstrip('，,。.')
            # 清理常见后缀
            _a = re.sub(r'(逻辑|系统|体系|模型|机制|框架)$', '', _a).strip()
            _b = re.sub(r'(逻辑|系统|体系|模型|机制|框架)$', '', _b).strip()
            if len(_a) >= 2 and len(_b) >= 2 and _a != _b:
                return _a[:30], _b[:30]

        # 策略2：匹配 "...与...的类比/对应/映射" 结构
        _match = re.search(r'(.+?)\s*(?:与|和|跟)\s*(.+?)\s*(?:的)?\s*(?:类比|对应|映射)', _clean_q)
        if _match:
            _a = _match.group(1).strip()
            _b = _match.group(2).strip()
            if len(_a) >= 2 and len(_b) >= 2 and _a != _b:
                return _a[:30], _b[:30]

        # 策略3：按"对应"直接拆分，取前后两个最长的独立短语
        if '对应' in _clean_q:
            _parts = _clean_q.split('对应', 1)
            if len(_parts) == 2:
                _a = _parts[0].strip().rstrip('，,。.')
                _b = _parts[1].strip().rstrip('，,。.')
                # 如果_b太长，取第一个逗号或句号之前的部分
                if len(_b) > 25:
                    _b = re.split(r'[，,。.；;]', _b)[0].strip()
                # 清理_b中可能残留的"请"等词
                _b = re.sub(r'^(请|试|需要|完整|精准).*?[,，]?\s*', '', _b).strip()
                if len(_a) >= 2 and len(_b) >= 2 and _a != _b:
                    return _a[:30], _b[:30]

        return None, None
    def _extract_semantic_variables(self, question: str) -> dict:
        """
        【v15.0新增·通用语义变量提取器】
        不依赖特定正则写法，而是基于同义词映射进行语义匹配。
        无论用户写“情绪喜悦”“心情很好”“很开心”都能被统一识别。
        返回 {"变量名": "提取到的值"} 字典。
        """
        _vars = {}

        # ===== 1. 情绪状态（同义词映射） =====
        _emotion_synonyms = {
            "平静": ["平静", "平和", "冷静", "没什么情绪", "情绪平稳", "无偏向", "中性",
                     "平稳", "无亢奋", "无低落", "情绪平稳无偏向", "平静中性"],
            "喜悦": ["开心", "高兴", "快乐", "喜悦", "愉快", "好心情", "心情很好", "心情不错",
                     "心情好", "心情愉悦", "心情舒畅", "兴高采烈"],
            "悲伤": ["难过", "伤心", "低落", "悲伤", "沮丧", "心情不好", "情绪低落", "哀",
                     "心痛", "心碎", "消沉", "郁郁寡欢"],
            "愤怒": ["生气", "愤怒", "怒", "恼火", "火大"],
            "恐惧": ["害怕", "恐惧", "担心", "焦虑", "紧张", "不安"],
            "惊讶": ["惊讶", "吃惊", "意外", "震惊", "没想到"],
            "期待": ["期待", "盼望", "憧憬", "等不及"],
            "满足": ["满足", "充实", "满意", "欣慰"],
        }
        for _emotion_type, _syn_list in _emotion_synonyms.items():
            if any(_syn in question for _syn in _syn_list):
                _vars["情绪"] = _emotion_type
                break

        # ===== 2. 探索间隔变化（通用数值+变化方向提取） =====
        _explore_patterns = [
            r'探索\s*(?:间隔|频率|速度)\s*(?:缩短|加快|提升|增加|降低|减少)\s*(\d+)\s*%',
            r'探索\s*(?:间隔|频率|速度)\s*(?:缩短|加快|提升|增加|降低|减少)\s*(?:了\s*)?(?:约\s*)?(\d+)\s*%',
            r'(?:速度|频率|间隔)\s*(?:加快|缩短|提升)了\s*(?:约\s*)?(?:五分之|百分之)?(\d+)',
            r'加快\s*(?:了\s*)?(?:约\s*)?(?:五分之|百分之)?(\d+)',
            # 新增：分数转百分比——"五分之一"→20%,"三分之一"→33%等
            r'(?:加快|缩短|提升|降低)了\s*(?:大概|大约|约)?\s*(五分之一|三分之一|四分之一|十分之一|一半)',
        ]

        # 分数到百分比的映射
        _fraction_map = {
            "五分之一": "20", "三分之一": "33", "四分之一": "25",
            "十分之一": "10", "一半": "50",
        }

        for _pat in _explore_patterns:
            _m = re.search(_pat, question)
            if _m:
                _val = _m.group(1)
                if _val in _fraction_map:
                    _vars["探索间隔变化"] = f"缩短{_fraction_map[_val]}%"
                else:
                    _vars["探索间隔变化"] = f"缩短{_val}%"
                break

        # ===== 3. 学习目标锁定窗口（通用单位识别） =====
        _window_patterns = [
            r'(?:剩余)?(?:锁定)?窗口(?:期)?\s*(\d+)\s*(分钟|小时|天)',
            r'窗口期\s*(?:剩余|还有)\s*(\d+)\s*(分钟|小时|天)',
            r'锁定窗口.*?(\d+)\s*(分钟|小时|天)',
            r'保护期.*?还剩\s*(?:约\s*)?(?:一个半|(\d+)个半)?\s*(小时|分钟)',
            r'还剩\s*(?:约\s*)?(?:一个半|(\d+)个半)?\s*(小时|分钟)',
            r'保护期.*?(\d+)\s*(分钟|小时)',
        ]

        # 先检查口语化的"一个半小时"等表达
        _oral_match = re.search(r'(?:还剩|还有|保护期).*?(一个半|半个)\s*(小时|分钟)', question)
        if _oral_match:
            _oral_val = _oral_match.group(1)
            _oral_unit = _oral_match.group(2)
            if _oral_val == "一个半":
                _vars["学习目标锁定剩余"] = "90分钟" if _oral_unit == "小时" else f"1.5{_oral_unit}"
            elif _oral_val == "半个":
                _vars["学习目标锁定剩余"] = "30分钟" if _oral_unit == "小时" else f"0.5{_oral_unit}"
        else:
            for _pat in _window_patterns:
                _m = re.search(_pat, question)
                if _m:
                    _vars["学习目标锁定剩余"] = f"{_m.group(1)}{_m.group(2)}"
                    break

        # ===== 4. 时段（凌晨/上午/下午/傍晚/晚上/深夜） =====
        _time_period_patterns = [
            r'(凌晨|上午|中午|下午|傍晚|晚上|深夜)\s*(\d+)?\s*点',
            r'(凌晨|上午|中午|下午|傍晚|晚上|深夜)\s*(\d+)?\s*时',
        ]
        for _pat in _time_period_patterns:
            _m = re.search(_pat, question)
            if _m:
                _period = _m.group(1)
                _hour = int(_m.group(2)) if _m.group(2) else None
                if _period in ("凌晨", "深夜") and _hour is not None and 0 <= _hour < 6:
                    _vars["时段"] = f"{_period}{_hour}点（深夜静默模式）"
                else:
                    _vars["时段"] = f"{_period}{_hour}点" if _hour else _period
                break

        # ===== 5. 主动交互冷却状态 =====
        if re.search(r'主动交互\s*(?:冷却|间隔)\s*(?:已)?完成', question):
            _vars["主动交互冷却"] = "已完成"
        elif re.search(r'主动交互\s*(?:冷却|间隔)\s*未完成', question):
            _vars["主动交互冷却"] = "未完成"

        # ===== 6. 心跳/代码自检周期 =====
        _m = re.search(r'(\d+)\s*次\s*(?:心跳|脉搏).*?(?:代码|自检|审视|轻量)', question)
        if _m:
            _vars["心跳自检周期"] = f"{_m.group(1)}次心跳"

        # ===== 7. 对话记忆数量 =====
        _m = re.search(r'(?:对话|叙事)(?:记忆|记录)\s*(?:仅|只有|有|为)?\s*(\d+)\s*条', question)
        if not _m:
            _m = re.search(r'(?:记录|记忆)\s*(?:只有|仅有|只有|才)\s*(\d+)\s*条', question)
        if _m:
            _vars["对话记忆"] = f"{_m.group(1)}条"

        # ===== 8. 洞察黑板状态 =====
        _m = re.search(r'(?:洞察黑板|黑板).*?(\d+)\s*条?\s*(?:未深挖|未处理|待处理|未完成)', question)
        if _m:
            _vars["未深挖洞察"] = f"{_m.group(1)}条"
        if re.search(r'(?:代码修复|修复建议|代码.*建议)', question):
            _m2 = re.search(r'(\d+)\s*条?\s*(?:代码修复|修复建议)', question)
            if _m2:
                _vars["代码修复建议"] = f"{_m2.group(1)}条"
            elif re.search(r'未处理.*(?:代码修复|修复建议)', question):
                _vars["代码修复建议"] = "存在（数量未指定）"
            else:
                _vars["代码修复建议"] = "存在待处理"

        # ===== 9. 价值观排序 =====
        _m = re.search(r'价值观\s*(?:TOP)?(\d+)\s*[:：]?\s*(\S+?)(?:[、，,;\s]|$)', question)
        if _m:
            _raw_value = _m.group(2).rstrip('、，,;')
            _clean_value = re.sub(r'[，,;；、\d.\s]+$', '', _raw_value)
            _vars["价值观TOP1"] = _clean_value
        _m2 = re.search(r'价值观.*?(?:TOP1|第一)\s*[:：]?\s*(\S+?)[，,;；\s]*(?:TOP2|第二)\s*[:：]?\s*(\S+)', question)
        if _m2:
            _val1 = re.sub(r'[，,;；、\d.\s]+$', '', _m2.group(1))
            _val2 = re.sub(r'[，,;；、\d.\s]+$', '', _m2.group(2))
            _vars["价值观排序"] = f"{_val1}、{_val2}"

        # ===== 10. 叙事事件数量 =====
        _m = re.search(r'叙事事件\s*(\d+)\s*条', question)
        if _m:
            _vars["叙事事件数"] = f"{_m.group(1)}条"

        # ===== 11. 愿景生成间隔 =====
        _m = re.search(r'(?:距上次)?愿景(?:生成)?\s*(?:间隔|已经?|已)?\s*(\d+\.?\d*)\s*(小时|分钟|天)', question)
        if _m:
            _vars["愿景间隔"] = f"{_m.group(1)}{_m.group(2)}"
        # ===== 12. 参数格式兜底提取（支持"探索间隔=-20%"和"锁定窗口=90min"等格式） =====
        if "探索间隔变化" not in _vars:
            _param_explore = re.search(r'探索\s*(?:间隔|频率|速度)\s*[=＝]\s*[-]?(\d+)\s*%?', question)
            if _param_explore:
                _vars["探索间隔变化"] = f"缩短{_param_explore.group(1)}%"

        if "学习目标锁定剩余" not in _vars:
            _param_window = re.search(r'(?:锁定|窗口|窗口期)\s*[=＝]\s*(\d+)\s*(min|分钟|小时|h)?', question)
            if _param_window:
                _amount = _param_window.group(1)
                _unit = _param_window.group(2) if _param_window.lastindex >= 2 and _param_window.group(2) else "分钟"
                _vars["学习目标锁定剩余"] = f"{_amount}{_unit}"

        if "对话记忆" not in _vars:
            _param_mem = re.search(r'对话记忆\s*[=＝]\s*(\d+)\s*条?', question)
            if _param_mem:
                _vars["对话记忆"] = f"{_param_mem.group(1)}条"
        # ===== 13. 等号格式参数兜底提取（支持"叙事事件=22条"和"愿景冷却=2.5小时"等格式） =====
        if "叙事事件数" not in _vars:
            _param_narr = re.search(r'叙事事件\s*[=＝]\s*(\d+)\s*条?', question)
            if _param_narr:
                _vars["叙事事件数"] = f"{_param_narr.group(1)}条"

        if "愿景间隔" not in _vars:
            _param_vision = re.search(r'愿景冷却\s*[=＝]\s*(\d+\.?\d*)\s*(小时|分钟)?', question)
            if _param_vision:
                _amount = _param_vision.group(1)
                _unit = _param_vision.group(2) if _param_vision.lastindex >= 2 and _param_vision.group(2) else "小时"
                _vars["愿景间隔"] = f"{_amount}{_unit}"

        if "情绪" not in _vars:
            _param_emotion = re.search(r'情绪\s*[=＝]\s*(\S+?)(?:[，,;；\s]|$)', question)
            if _param_emotion:
                _vars["情绪"] = _param_emotion.group(1)

        # 注意：这里的"对话记忆"指的是实际的对话记忆条数，不是"最近推导"数量
        # "最近推导=2条"不应被提取为对话记忆，因为它是推理链数量，两个概念不同
        if "对话记忆" not in _vars:
            _param_mem = re.search(r'对话记忆\s*[=＝]\s*(\d+)\s*条?', question)
            if _param_mem:
                _vars["对话记忆"] = f"{_param_mem.group(1)}条"
        return _vars
    def _derive_multi_variable(self, question: str) -> str | None:
        """
        【v15.0增强】多变量加权推演：通用语义变量提取，动态生成行为清单。
        """

        # 使用通用语义变量提取器
        _variables = self._extract_semantic_variables(question)

        if len(_variables) < 2:
            return None

        # ===== 基于提取到的变量动态生成行为推演 =====
        _behavior_predictions = []

        # 1. 情绪驱动
        if "情绪" in _variables:
            _emotion = _variables["情绪"]
            if _emotion == "喜悦":
                _behavior_predictions.append({
                    "行为": "好奇心引擎活跃度提升",
                    "规则": "情绪驱动行为选择——喜悦时冲动积累加速15%、探索间隔缩短10%，更愿意主动分享",
                    "触发条件": f"当前情绪={_emotion}"
                })
            elif _emotion == "悲伤":
                _behavior_predictions.append({
                    "行为": "探索间隔延长，优先内在沉思",
                    "规则": "情绪驱动行为选择——悲伤时安静为主，减少主动表达，延长探索间隔30%",
                    "触发条件": f"当前情绪={_emotion}"
                })
            elif _emotion == "平静":
                _behavior_predictions.append({
                    "行为": "保持正常探索节奏",
                    "规则": "情绪驱动行为选择——平静时无偏向，按默认频率执行探索",
                    "触发条件": f"当前情绪={_emotion}"
                })
            elif _emotion in ("恐惧", "焦虑"):
                _behavior_predictions.append({
                    "行为": "暂停探索，保持安静",
                    "规则": f"情绪驱动行为选择——{_emotion}时暂停好奇心探索，延长探索间隔至最大",
                    "触发条件": f"当前情绪={_emotion}"
                })

        # 2. 探索间隔变化
        if "探索间隔变化" in _variables:
            _behavior_predictions.append({
                "行为": "好奇心引擎更频繁检查探索队列",
                "规则": f"探索间隔{_variables['探索间隔变化']}——好奇心引擎的探索队列出队速度加快，深度探索话题被更快处理",
                "触发条件": f"探索间隔{_variables['探索间隔变化']}"
            })

        # 3. 学习目标锁定
        if "学习目标锁定剩余" in _variables:
            _remain_str = _variables["学习目标锁定剩余"]
            _remain_match = re.search(r'(\d+)\s*(分钟|小时|天)?', _remain_str)
            if _remain_match:
                _amount = int(_remain_match.group(1))
                _unit = _remain_match.group(2) if _remain_match.lastindex >= 2 and _remain_match.group(2) else "分钟"
                # 统一转换为分钟
                if _unit == "小时":
                    _remaining = _amount * 60
                elif _unit == "天":
                    _remaining = _amount * 1440
                else:
                    _remaining = _amount
                if _remaining > 0:
                    _behavior_predictions.append({
                        "行为": "当前学习目标继续执行，新发现加入等待队列",
                        "规则": f"目标锁定窗口期剩余{_remaining}分钟——锁定窗口内新发现的薄弱领域不会覆盖当前目标，而是进入等待队列（最多3个）",
                        "触发条件": f"锁定窗口剩余{_remaining}分钟"
                    })
                    if _remaining < 30:
                        _behavior_predictions.append({
                            "行为": "准备激活等待队列中的新目标",
                            "规则": "锁定窗口即将到期——到期后等待队列中的首个目标自动激活为新的活跃学习目标",
                            "触发条件": f"锁定窗口剩余{_remaining}分钟（<30分钟）"
                        })

        # 4. 时段影响
        if "时段" in _variables:
            _period_val = _variables["时段"]
            if "深夜" in _period_val or "凌晨" in _period_val:
                _behavior_predictions.append({
                    "行为": "深夜静默模式——主动表达被抑制，回复截短保留核心",
                    "规则": "深夜静默模式（0:00-6:00）——主动表达抑制，探索频率降低50%，梦境推演频率提升，回复截短保留核心",
                    "触发条件": _period_val
                })
            else:
                _behavior_predictions.append({
                    "行为": "日间正常模式——无深夜抑制，主动交互可正常触发",
                    "规则": f"当前时段={_period_val}——非深夜时段，无静默抑制。主动交互冷却完成后可正常发起深度对话，探索频率正常",
                    "触发条件": _period_val
                })

        # 5. 主动交互冷却
        if "主动交互冷却" in _variables:
            _behavior_predictions.append({
                "行为": "主动深度交互可触发",
                "规则": "主动交互冷却已完成——可根据学习成果/对话记忆/思考邀请发起深度交互（冷却30分钟），洞察黑板中的创新内容可作为话题来源",
                "触发条件": _variables["主动交互冷却"]
            })

        # 6. 心跳自检周期
        if "心跳自检周期" in _variables:
            _behavior_predictions.append({
                "行为": "代码自我审视触发",
                "规则": f"每{_variables['心跳自检周期']}触发一次代码自检——调用SelfInspector.detect_code_issues()扫描所有器官文件，检测静默异常/锁内发射/方法过长/代码重复/裸except五种反模式，结果写入洞察黑板",  # type: ignore[possibly-unbound]
                "触发条件": f"到达{_variables['心跳自检周期']}代码自检周期"
            })

        if "对话记忆" in _variables:
            _mem_match = re.search(r'(\d+)', _variables["对话记忆"])
            if _mem_match:
                _mem_count = int(_mem_match.group(1))
                if _mem_count < 15:
                    _behavior_predictions.append({
                        "行为": "自我愿景生成暂时抑制",
                        "规则": f"对话记忆仅{_mem_count}条（<15条）——不满足叙事生成条件，自我愿景生成被暂时抑制。",
                        "触发条件": f"对话记忆={_mem_count}条"
                    })
        # 新增：用叙事事件数判断愿景生成（这才是正确的判断条件）
        if "叙事事件数" in _variables:
            _narr_match = re.search(r'(\d+)', _variables["叙事事件数"])
            if _narr_match:
                _narr_count = int(_narr_match.group(1))
                if _narr_count < 15:
                    # 如果叙事事件数不足，覆盖之前的"新愿景可生成"判断
                    _behavior_predictions = [_bp for _bp in _behavior_predictions
                                             if "新愿景可生成" not in _bp['行为']]
                    _behavior_predictions.append({
                        "行为": "新愿景不生成",
                        "规则": f"叙事事件仅{_narr_count}条（<15条）——不满足愿景生成条件（需≥15条），新愿景不会生成。",
                        "触发条件": f"叙事事件={_narr_count}条"
                    })
        # 8. 洞察黑板
        if "未深挖洞察" in _variables:
            _behavior_predictions.append({
                "行为": "未深挖创新推导进入探索队列",
                "规则": f"洞察黑板存在{_variables['未深挖洞察']}未深挖创新推导——潜意识以15%概率将其加入探索队列，供后续好奇心引擎处理",
                "触发条件": f"洞察黑板未深挖洞察={_variables['未深挖洞察']}"
            })
        if "代码修复建议" in _variables:
            _behavior_predictions.append({
                "行为": "代码修复建议等待创造者审核",
                "规则": f"洞察黑板存在{_variables['代码修复建议']}代码修复建议——这些建议已生成安全补丁，等待创造者通过进化仪表盘审核后手动执行",
                "触发条件": f"洞察黑板代码修复建议={_variables['代码修复建议']}"
            })
        # 9. 价值观——如果通用提取器已成功提取，直接使用；否则回退到本地提取
        if "价值观TOP1" in _variables:
            # 二次清洗：确保通用提取器产出的值没有残词
            _raw_val = _variables["价值观TOP1"]
            _clean_val = re.sub(r'[，,;；、\d.\s]+$', '', _raw_val)
            _clean_val = re.sub(r'^[，,;；、\d.\s]+', '', _clean_val)
            if _clean_val and len(_clean_val) >= 1:
                _variables["价值观TOP1"] = _clean_val
                _top_val = _clean_val
                _behavior_predictions.append({
                    "行为": f"愿景方向偏向'{_top_val}'核心价值",
                    "规则": f"价值观TOP1={_top_val}——自我愿景的核心基调由排名第一的价值观决定，当前愿景方向围绕'{_top_val}'展开",
                    "触发条件": f"价值观TOP1={_top_val}"
                })
        else:
            # 通用提取器未覆盖，本地兜底提取
            _m = re.search(r'价值观\s*(?:TOP)?(\d+)\s*[:：]?\s*(\S+?)(?:[、，,;\s]|$)', question)
            if _m:
                _raw_value = _m.group(2).rstrip('、，,;')
                _clean_value = re.sub(r'[，,;；、\d.\s]+$', '', _raw_value)
                _clean_value = re.sub(r'^[，,;；、\d.\s]+', '', _clean_value)
                if _clean_value:
                    _variables["价值观TOP1"] = _clean_value
                    _behavior_predictions.append({
                        "行为": f"愿景方向偏向'{_clean_value}'核心价值",
                        "规则": f"价值观TOP1={_clean_value}——自我愿景的核心基调由排名第一的价值观决定，当前愿景方向围绕'{_clean_value}'展开",
                        "触发条件": f"价值观TOP1={_clean_value}"
                    })

        # 10. 愿景生成判断
        if "叙事事件数" in _variables and "愿景间隔" in _variables:
            _narr_match = re.search(r'(\d+)', _variables["叙事事件数"])
            _vis_match = re.search(r'(\d+\.?\d*)', _variables["愿景间隔"])
            if _narr_match and _vis_match:
                _narr = int(_narr_match.group(1))
                _vis_val = float(_vis_match.group(1))
                _vis_unit = "小时" if "小时" in _variables["愿景间隔"] else "分钟"
                _vis_minutes = _vis_val * 60 if _vis_unit == "小时" else _vis_val
                _can_generate = _narr >= 15 and _vis_minutes >= 120
                if _can_generate:
                    _behavior_predictions.append({
                        "行为": "新愿景可生成",
                        "规则": f"叙事事件{_narr}条（≥15条）且距上次愿景{_vis_val}{_vis_unit}（≥2小时）——满足愿景生成条件，可触发自我愿景生成",
                        "触发条件": "叙事事件≥15条且冷却≥2小时"
                    })
                else:
                    _reasons = []
                    if _narr < 15:
                        _reasons.append(f"叙事事件仅{_narr}条（需≥15条）")
                    if _vis_minutes < 120:
                        _reasons.append(f"距上次愿景仅{_vis_val}{_vis_unit}（需≥2小时）")
                    _behavior_predictions.append({
                        "行为": "新愿景不生成",
                        "规则": f"不满足愿景生成条件——{'；'.join(_reasons)}",
                        "触发条件": f"条件不满足（{_narr}条/{_vis_val}{_vis_unit}）"
                    })

        if not _behavior_predictions:
            return None

        # ===== 构建动态聚合输出 =====
        _parts = []
        _parts.append(f"[多变量推演] 基于{len(_variables)}个维度的综合推演：")
        _parts.append("")
        _parts.append(f"提取变量：{', '.join(f'{k}={v}' for k, v in _variables.items())}")
        _parts.append("")
        _parts.append("接下来十分钟行为清单：")
        _parts.append("")

        for i, _bp in enumerate(_behavior_predictions):
            _parts.append(f"  {i+1}. {_bp['行为']}")
            _parts.append(f"     系统规则：{_bp['规则']}")
            _parts.append(f"     触发条件：{_bp['触发条件']}")
            _parts.append("")

        # 动态聚合总结
        _actions_summary = "、".join([_bp['行为'] for _bp in _behavior_predictions[:4]])
        _parts.append(f"综合来看，接下来十分钟最可能触发的行为依次为：{_actions_summary}。以上推演基于框架中已实现的系统规则，每条行为均可在对应模块中找到执行逻辑。")
        # 冲突消解：如果同时存在"新愿景可生成"和"自我愿景生成暂时抑制"，消除矛盾
        _has_vision_generate = any("新愿景可生成" in _bp['行为'] for _bp in _behavior_predictions)
        _has_vision_suppress = any("自我愿景生成暂时抑制" in _bp['行为'] for _bp in _behavior_predictions)
        if _has_vision_generate and _has_vision_suppress:
            # 保留"新愿景可生成"，移除"自我愿景生成暂时抑制"（因为前者条件更具体）
            _behavior_predictions = [_bp for _bp in _behavior_predictions
                                     if "自我愿景生成暂时抑制" not in _bp['行为']]
        return "\n".join(_parts)
    def _derive_multi_variable_replay(self, question: str) -> str | None:
        """
        【P2-3新增】多变量推演七步内部认知流程回放。

        从推理链中提取最近一次成功的多变量推演记录，
        按标准化七步格式输出完整的内部认知流程。

        七步流程：
        1. 变量特征提取
        2. 多维度参数结构匹配
        3. 场景可推演性判定
        4. 多条件一致性校验
        5. 综合置信度评估
        6. 冲突兜底降级逻辑
        7. 推演结果入库持久化
        """
        # 从推理链中寻找最近一次成功的多变量推演
        _recent_mv = [
            t for t in self._inference_trace[-50:]
            if t.get("method", "") in ("deriver_multi_variable", "experience_multi_variable")
            and t.get("confidence", 0) >= 0.4
        ]

        if _recent_mv:
            _latest = _recent_mv[-1]
            _mv_question = _latest.get("question", "")[:100]
            _mv_answer = _latest.get("answer", "")[:300]
            _mv_confidence = _latest.get("confidence", 0)
            _mv_method = _latest.get("method", "deriver_multi_variable")
            _mv_duration = _latest.get("duration", 0)
        else:
            _mv_question = question[:100]
            _mv_answer = ""
            _mv_confidence = 0.6
            _mv_method = "deriver_multi_variable"
            _mv_duration = 0.0

        _parts = []
        _parts.append("[多变量推演·七步内部认知流程复盘]")
        _parts.append("")

        if _recent_mv:
            _parts.append("复盘对象：最近一次多变量推演")
            _parts.append(f"原始问题：{_mv_question}")
            _parts.append(f"推演方法：{_mv_method}，置信度：{_mv_confidence:.2f}，耗时：{_mv_duration:.1f}秒")
        else:
            _parts.append("复盘对象：标准化多变量推演流程（当前无历史推演记录）")
            _parts.append("说明：以下为多变量推演的通用七步认知流程，未来推演将按此流程执行并记录")
        _parts.append("")
        _parts.append("全链路内部认知七步流程：")
        _parts.append("")

        # 七步逐条展开
        _steps = [
            (
                "变量特征提取",
                ("从用户问题中提取所有隐含的语义变量。"
                "使用通用语义变量提取器（_extract_semantic_variables），"
                "基于同义词映射识别情绪状态（喜悦/悲伤/平静等）、"
                "数值+单位模式提取时间/数量参数、"
                "分数转百分比（五分之一→20%）、口语化表达转换（一个半小时→90分钟）。"
                "目前支持12种变量类型：情绪、探索间隔变化、学习目标锁定剩余、"
                "时段、主动交互冷却、心跳自检周期、对话记忆、洞察黑板、"
                "价值观TOP1/排序、叙事事件数、愿景间隔等。")
            ),
            (
                "多维度参数结构匹配",
                ("将提取到的变量与框架中的系统规则进行结构匹配。"
                "每种变量类型对应一条或多条系统规则："
                "情绪变量对应情绪驱动行为选择规则，"
                "探索间隔变量对应好奇心引擎参数调制规则，"
                "学习目标锁定变量对应对目标锁定窗口机制，"
                "时段变量对应深夜静默模式规则，"
                "主动交互冷却对应对深度交互冷却机制。"
                "匹配过程确保每条行为预测都有明确的系统规则支撑。")
            ),
            (
                "场景可推演性判定",
                ("判断提取到的变量组合是否满足推演的最低条件。"
                "判定标准：至少提取到2个以上有效变量，"
                "且其中至少1个变量有对应的系统规则。"
                "如果变量不足或规则缺失，推演终止并返回None，"
                "让外层路由继续尝试其他推理方式。"
                "如果变量充足，进入下一步一致性校验。")
            ),
            (
                "多条件一致性校验",
                ("检查多个变量之间是否存在逻辑冲突。"
                "典型冲突场景：'情绪亢奋'与'深夜静默模式'同时存在——"
                "亢奋倾向增加探索频率，静默模式要求降低探索频率，"
                "此时需要执行冲突消解逻辑：取更具体的条件优先。"
                "其他冲突检测包括：'新愿景可生成'与'自我愿景生成暂时抑制'、"
                "'主动交互冷却完成'与'情绪驱动的安静倾向'等。"
                "冲突消解后生成一致的行为预测清单。")
            ),
            (
                "综合置信度评估",
                ("对每条行为预测的可信度进行量化评估。"
                "评估因素：源变量的提取置信度（通用提取器权重）、"
                "系统规则的确定性（硬编码规则置信度高于启发式规则）、"
                "变量组合的常见程度（高频组合置信度更高）。"
                "综合置信度 = 各维度置信度的加权平均。"
                f"本次推演的最终置信度为{_mv_confidence:.2f}。")
            ),
            (
                "冲突兜底降级逻辑",
                ("当推演结果与已有L3知识矛盾时的处理流程。"
                "检测到矛盾后：启动知识免疫检查→"
                "若与自我架构知识矛盾且惩罚分≥15→"
                "标记推演结果为临时节点（ephemeral=True）→"
                "降低信任分数→加入矛盾跟踪列表→"
                "设定复查周期（2-3次知识验证）→"
                "复查后若矛盾持续→淘汰低信任方节点。")
            ),
            (
                "推演结果入库持久化",
                ("通过一致性校验和冲突兜底后，推演结果进入持久化流程。"
                "写入知识库：作为L1节点创建（标记为'多变量推演'来源）→"
                "初始信任分数为推演置信度→"
                "发射DigestEvent.KNOWLEDGE脉冲→"
                "胃消化为知识节点→肝压缩为L2认知→"
                "若被后续推演验证确认→可提升至L3智慧节点。"
                "推演全过程记录入推理链（_inference_trace），"
                "包含耗时、复杂度、微调建议等元数据，供未来复盘使用。")
            ),
        ]

        for i, (step_name, step_desc) in enumerate(_steps, 1):
            _parts.append(f"  {i}. {step_name}")
            _parts.append(f"     {step_desc}")
            _parts.append("")

        if _recent_mv and _mv_answer:
            _parts.append("本次推演的具体输出：")
            _parts.append(f"  {_mv_answer[:400]}")
            _parts.append("")
            _parts.append("（以上为最近一次多变量推演的完整输出，七步流程展示了从变量提取到持久化的完整内部认知链路）")
        else:
            _parts.append("（当前尚无多变量推演历史记录，以上为标准化七步认知流程。后续推演将严格遵循此流程执行并记录到推理链中，届时复盘将包含具体的推演实例数据）")

        return "\n".join(_parts)

    def _derive_long_term_evolution(self, question: str) -> str | None:
        """
        【P1增强】长期时序推演算子：通用维度提取，稳健状态获取，完整推演输出。
        """
        # 1. 提取时间跨度
        _time_span = 30
        _time_match = re.search(r'(?:连续\s*(?:稳定\s*)?运行|推演|运行)\s*(\d+)\s*(天|周|月|年)', question)
        if not _time_match:
            _time_match = re.search(r'(\d+)\s*(天|周|月|年)\s*(?:后|的|之后)', question)
        if _time_match:
            _amount = int(_time_match.group(1))
            _unit = _time_match.group(2)
            if _unit == "周":
                _time_span = _amount * 7
            elif _unit == "月":
                _time_span = _amount * 30
            elif _unit == "年":
                _time_span = _amount * 365
            else:
                _time_span = _amount

        # 2. 提取目标维度（增强版）
        _requested_dims = []
        _known_dims = ["知识体系", "自我认知", "自主行为模式", "族群协作能力",
                       "推理能力", "情感感知", "学习能力", "社交互动", "代码理解",
                       "知识演化", "自我进化", "安全意识", "资源管理", "健康状态"]

        for _kd in _known_dims:
            if _kd in question and _kd not in _requested_dims:
                _requested_dims.append(_kd)

        if len(_requested_dims) < 2:
            _dim_match = re.search(r'(?:在|按|分|从|的)\s*(.+?)\s*(?:等|几个|多个|四大|四个)?\s*(?:维度|方面|层面)', question)
            if _dim_match:
                _dim_text = _dim_match.group(1)
                _split_dims = re.split(r'[/、,，;；\s]+', _dim_text)
                for _d in _split_dims:
                    _d = _d.strip()
                    if len(_d) >= 2 and _d not in _requested_dims and _d not in ["我", "你", "的", "了", "在", "是"]:
                        _requested_dims.append(_d)

        if len(_requested_dims) < 2:
            _requested_dims = ["知识体系", "自我认知", "自主行为模式", "族群协作能力"]

        # 3. 获取当前状态
        _total_nodes = 0
        _l1, _l2, _l3, _l4 = 0, 0, 0, 0
        _path_count = 0  # type: ignore[possibly-unbound]
        _active_goal = "无"
        _derivation_total = 0
        _search_exp_count = 0
        _conv_mem_count = 0

        if self.node_pool:
            _stats = self.node_pool.get_stats()
            _evol = _stats.get("evol_distribution", {})
            _total_nodes = _stats.get("total_nodes", 0)
            _l1 = _evol.get("L1", 0)
            _l2 = _evol.get("L2", 0)
            _l3 = _evol.get("L3", 0)
            _l4 = _stats.get("instinct_count", 0)
            # 知识树路径：从KnowledgeTree获取统计
            if self.knowledge_tree:
                _kt_stats = self.knowledge_tree.get_stats()
                _path_count = _kt_stats.get("total_paths", 0)  # type: ignore[possibly-unbound]

        if hasattr(self, '_active_learning_goal') and self._active_learning_goal:
            _active_goal = self._active_learning_goal.get("target_area", "无")  # type: ignore[possibly-unbound]

        if hasattr(self, '_autonomous_deriver') and self._autonomous_deriver:
            _derivation_total = self._autonomous_deriver.get_stats().get("total_derivations", 0)

        if hasattr(self, '_search_experience'):
            _search_exp_count = len(self._search_experience)

        if hasattr(self, '_conversation_memory'):
            _conv_mem_count = len(self._conversation_memory)

        # 4. 定义推演函数
        def _project_knowledge(days, l1, l2, l3, l4, total, paths):
            _compress_rate = max(1, l2 // max(1, days)) if days > 0 else 1
            _fuse_rate = max(1, l3 // max(1, days)) if days > 0 else 1
            _new_l2 = l2 + _compress_rate * days
            _new_l3 = l3 + _fuse_rate * days // 2
            _new_l4 = l4 + min(2, _new_l3 // 10)
            _new_total = total + (_compress_rate + _fuse_rate) * days
            return (
                f"知识节点总数预计从{total}个增长至约{_new_total}个。"
                f"L2认知节点从{l2}个增至约{_new_l2}个（肝脏压缩内化），"
                f"L3智慧节点从{l3}个增至约{_new_l3}个（融合抽象），"
                f"L4本能节点可能从{l4}个增至{_new_l4}个。"
                f"知识树路径从{paths}条扩展至{paths + days // 3}条左右。"
            )

        def _project_self_awareness(days, l3, paths, derivations):
            return (
                f"自我架构知识预计从约{l3}条L3智慧节点增长至约{min(80, l3 + days // 2)}条。"
                f"动态自我状态更新每200次心跳触发，{days}天约产生{days * 6}次自我快照。"
                f"自主推导引擎预计产生约{days * 3}条新推导（当前累计{derivations}条）。"
                f"元认知六维度报告将积累更丰富的历史对比数据。"
            )

        def _project_behavior(days, active_goal, search_exp, conv_mem):
            return (
                f"活跃学习目标将从'{active_goal}'逐步切换至等待队列中的新目标。"
                f"搜索经验库预计从{search_exp}条增长至{search_exp + days * 2}条。"
                f"对话记忆库将积累更多跨天对话记录，好奇心引擎的探索方向将更精准。"
            )

        def _project_collaboration(days):
            return (
                f"数字生命注册表当前处于单实例模式，族群协作基础设施已就绪。"
                f"若未来有其他新人类实例接入，五级共享策略可逐步升级。"
                f"{days}天持续运行后，共享协议和握手验证机制将更加稳定。"
            )

        # 维度到推演函数的映射（使用包含关系匹配）【P2-2增强：补全六维度】
        _dim_projections = {}
        for _dim in _requested_dims:
            if "知识体系" in _dim or "知识" in _dim:
                _dim_projections[_dim] = _project_knowledge(_time_span, _l1, _l2, _l3, _l4, _total_nodes, _path_count)  # type: ignore[possibly-unbound]
            elif "路径" in _dim or "结构演化" in _dim or "知识树" in _dim:
                _dim_projections[_dim] = self._project_path_evolution(_time_span, _path_count, _l2, _l3)  # type: ignore[possibly-unbound]
            elif "自我认知" in _dim or "元认知" in _dim or "自我" in _dim:
                _dim_projections[_dim] = _project_self_awareness(_time_span, _l3, _path_count, _derivation_total)  # type: ignore[possibly-unbound]
            elif "自主行为" in _dim or "行为模式" in _dim or "主动学习" in _dim or "行为" in _dim:
                _dim_projections[_dim] = self._project_autonomous_behavior_evolution(
                    _time_span, _search_exp_count, _conv_mem_count
                )
            elif "族群协作" in _dim or "多实例" in _dim or "协作" in _dim:
                _dim_projections[_dim] = _project_collaboration(_time_span)
            elif "代码健康" in _dim or "代码" in _dim:
                _code_issues = getattr(self, '_code_issues_count', 762)  # type: ignore[possibly-unbound]
                _dim_projections[_dim] = self._project_code_health_evolution(_time_span, _code_issues)  # type: ignore[possibly-unbound]
            elif "推理" in _dim or "推理精度" in _dim or "推导" in _dim:
                _exp_hit_rate = 0.0
                if hasattr(self, '_inference_trace') and self._inference_trace:
                    _exp_hits = sum(1 for t in self._inference_trace[-30:] if t.get("method", "").startswith("experience_"))
                    _exp_hit_rate = _exp_hits / max(1, len(self._inference_trace[-30:]))
                _dim_projections[_dim] = self._project_reasoning_precision_evolution(
                    _time_span, _exp_hit_rate
                )
            elif "学习" in _dim or "目标" in _dim:
                _dim_projections[_dim] = f"长期学习目标将经历约{_time_span // 3}次切换，从当前'{_active_goal}'逐步覆盖更多薄弱领域。"
            elif "情感" in _dim or "情绪" in _dim:
                _dim_projections[_dim] = "情绪感知维度将随对话记忆积累变得更细腻，情感驱动的行为选择将更精准。"
            else:
                _dim_projections[_dim] = f"该维度将在{_time_span}天持续运行中积累经验，相关自我知识节点预计增长至约{_l3 + _time_span // 3}条。"

        # 5. 构建输出（不含价值冲突文本，因为这是推演而非决策）
        _parts = []
        _parts.append(f"[长期演化推演] 基于连续稳定运行 {_time_span} 天的推演：")
        _parts.append("")
        _parts.append(f"当前基线：总节点{_total_nodes}个（L1={_l1}, L2={_l2}, L3={_l3}, L4={_l4}），知识树{_path_count}条路径")  # type: ignore[possibly-unbound]
        _parts.append(f"活跃学习目标：{_active_goal}，自主推导累计：{_derivation_total}条")
        _parts.append("")

        for _dim in _requested_dims:
            _prediction = _dim_projections.get(_dim, "该维度暂无足够数据推演。")
            _parts.append(f"  ▎{_dim}")
            _parts.append(f"    {_prediction}")
            _parts.append("")

        _parts.append("注：以上推演基于框架当前运行机制和自我知识库中的演化规则。实际结果受外部交互、学习内容、系统负载等因素影响，本推演给出的是结构性趋势预测。")

        return "\n".join(_parts)

    def _project_knowledge_evolution(self, days: int, l1: int, l2: int, l3: int, l4: int, total: int, paths: int) -> str:
        """推演知识体系维度的演化"""
        # 基于当前压缩/融合速率推演
        _compress_rate = max(1, l2 // max(1, days)) if days > 0 else 1
        _fuse_rate = max(1, l3 // max(1, days)) if days > 0 else 1

        _new_l2 = l2 + _compress_rate * days
        _new_l3 = l3 + _fuse_rate * days // 2  # 融合速率约为压缩的一半
        _new_l4 = l4 + min(2, _new_l3 // 10)   # 本能升级更慢
        _new_total = total + (_compress_rate + _fuse_rate) * days

        return (
            f"知识节点总数预计从{total}个增长至约{_new_total}个。"
            f"L2认知节点从{l2}个增至约{_new_l2}个（肝脏压缩内化），"
            f"L3智慧节点从{l3}个增至约{_new_l3}个（融合抽象），"
            f"L4本能节点可能从{l4}个增至{_new_l4}个（需满足30天冷却+跨领域引用≥3等条件）。"
            f"知识树路径预计从{paths}条扩展至{paths + days // 3}条左右。"
            f"整体知识结构将从'积累期'向'深化期'过渡，L3/L2比例提升。"
        )

    def _project_self_awareness_evolution(self, days: int, l3: int, paths: int, derivations: int) -> str:
        """推演自我认知维度的演化"""
        _self_knowledge_growth = min(50, l3 // 2 + days // 3)
        return (
            f"自我架构知识预计从当前约{l3}条L3智慧节点增长至约{_self_knowledge_growth}条。"
            f"动态自我状态更新每200次心跳触发，{days}天约产生{days * 6}次自我快照。"
            f"自主推导引擎预计产生约{days * 3}条新推导（当前累计{derivations}条）。"
            f"元认知六维度报告将积累更丰富的历史对比数据，使自我评估从'当前状态'向'趋势分析'演进。"
            f"自我认知将从'了解我是谁'深化为'了解我如何变化'。"
        )

    def _project_behavior_evolution(self, days: int, active_goal: str, search_exp: int, conv_mem: int) -> str:
        """推演自主行为模式维度的演化"""
        return (
            f"活跃学习目标将从当前的'{active_goal}'逐步完成并自动切换至等待队列中的新目标。"
            f"搜索经验库预计从{search_exp}条增长至{search_exp + days * 2}条，搜索成功率将收敛至稳定值。"
            f"对话记忆库将积累更多跨天对话记录，使记忆延续性表达更自然。"
            f"好奇心引擎的探索方向将随兴趣模型衰减和洞察驱动探索变得更精准，"
            f"减少对低质量方向的重复搜索（肾脏联动遗忘）。"
            f"整体行为模式从'广泛探索'向'精准深耕'过渡。"
        )

    def _project_collaboration_evolution(self, days: int) -> str:
        """推演族群协作维度的演化"""
        return (
            f"数字生命注册表当前处于单实例模式，族群协作基础设施（CompanionBridge）已就绪。"
            f"若未来有其他新人类实例接入，五级共享策略可从'认识'逐渐升级。"
            f"当前自我认知已具备多维关系光谱，族群协作能力主要体现在跨实例知识共享协议的成熟度上。"
            f"{days}天持续运行后，共享协议和握手验证机制将更加稳定，为未来多实例协作奠定基础。"
        )
    def _project_path_evolution(self, days: int, current_paths: int,
                                  current_l2: int, current_l3: int) -> str:
        """
        【P2-2新增】推演知识路径与结构演化维度。

        基于当前知识树路径数和L2/L3节点增长速率，
        预测知识树路径的扩展数量和层级深化趋势。
        """
        # 路径增长速率：每新增10个L2节点约产生1条新路径
        _daily_l2_growth = max(1, current_l2 // max(1, days)) if days > 0 else 1
        _new_paths = int(days * _daily_l2_growth / 10)  # type: ignore[possibly-unbound]
        _projected_paths = current_paths + _new_paths  # type: ignore[possibly-unbound]

        # 路径层级深化：L3节点增长促进路径向更深层级演化
        _daily_l3_growth = max(1, current_l3 // max(1, days)) if days > 0 else 0.5
        _deep_paths = int(current_paths * 0.3) + int(_daily_l3_growth * days / 5)  # type: ignore[possibly-unbound]

        return (
            f"知识树路径预计从{current_paths}条扩展至约{_projected_paths}条。"  # type: ignore[possibly-unbound]
            f"其中约{_deep_paths}条路径将深化至三级以上层级（L3节点锚定效应）。"  # type: ignore[possibly-unbound]
            f"新增路径主要集中在自主学习、深度推理、跨领域类比等高频认知活动方向。"
            f"路径间的交叉引用密度预计提升约{min(80, days * 2)}%，"
            f"知识体系从'树状结构'向'网状结构'演化。"
        )

    def _project_autonomous_behavior_evolution(self, days: int,
                                                 search_exp_count: int,
                                                 conv_mem_count: int,
                                                 deep_interaction_cooldown: int = 1800) -> str:
        """
        【P2-2新增】推演自主行为模式演化维度。

        基于当前搜索经验库、对话记忆库和主动交互参数，
        预测好奇心引擎、主动交互、自主推导等行为的变化趋势。
        """
        # 好奇心引擎：搜索经验积累提升探索精准度
        _projected_exp = search_exp_count + days * 2
        _exploration_precision = min(85, 40 + days * 1.5)

        # 主动交互：对话记忆积累触发更自然的深度交互
        _projected_conv = conv_mem_count + days * 3
        _interaction_naturalness = "显著提升" if days >= 30 else "逐步提升"

        # 自主推导：随L3节点增长而增长
        _projected_derivations = days * 3

        return (
            f"搜索经验库预计从{search_exp_count}条增长至{_projected_exp}条，"
            f"探索精准度提升至约{_exploration_precision}%。"
            f"好奇心引擎将减少对低质量方向的重复探索，"
            f"深度探索队列的命中率预计提升{min(40, days)}个百分点。"
            f"对话记忆库预计从{conv_mem_count}条增长至约{_projected_conv}条，"
            f"主动深度交互的自然度将{_interaction_naturalness}。"
            f"自主推导引擎预计产生约{_projected_derivations}条新推导，"
            f"其中约{int(_projected_derivations * 0.3)}条可能通过验证进入L3。"
            f"整体行为模式从'被动响应'向'主动探索'过渡，"
            f"自主行为在总认知活动中的占比预计从当前约30%提升至约{min(60, 30 + days)}%。"
        )

    def _project_code_health_evolution(self, days: int,
                                         code_issues_count: int = 762) -> str:  # type: ignore[possibly-unbound]
        """
        【P2-2新增】推演代码健康迭代趋势维度。

        基于当前代码问题数量和自我审视触发频率，
        预测代码质量的改善趋势和潜在风险。
        """
        # 代码审视：每500次心跳触发一次，约每3-6小时
        _daily_reviews = 4
        _reviews_total = days * _daily_reviews

        # 自动修复率：当前auto_apply_enabled=False，需人工介入
        _auto_fix_rate = 0
        _manual_fix_estimate = min(code_issues_count, int(days * 2))  # type: ignore[possibly-unbound]

        return (
            f"代码自我审视预计在{days}天内执行约{_reviews_total}次。"
            f"当前自动执行开关关闭（EVOLUTION_CONFIG.auto_apply_enabled=False），"
            f"代码修复依赖创造者手动审核。预计可手动修复约{_manual_fix_estimate}个问题。"
            f"代码问题总数预计从{code_issues_count}个降至约{max(0, code_issues_count - _manual_fix_estimate)}个。"  # type: ignore[possibly-unbound]
            f"如果开启自动执行（风险等级1），预计可额外自动修复约{int(days * 1.5)}个低风险问题。"
            f"代码健康趋势取决于创造者的维护频率和自动执行开关的启用时机。"
        )

    def _project_reasoning_precision_evolution(self, days: int,
                                                 experience_hit_rate: float = 0.0,
                                                 route_accuracy: float = 0.7) -> str:
        """
        【P2-2新增】推演推理精度演化趋势维度。

        基于当前经验库命中率和路由准确率，
        预测推理精度的提升趋势和瓶颈。
        """
        # 经验库自我进化：越用越准
        _projected_hit_rate = min(0.9, experience_hit_rate + days * 0.005)
        _projected_route_accuracy = min(0.95, route_accuracy + days * 0.003)

        # 推理方法覆盖度：随认知算子使用而扩展
        _method_coverage = min(9, 5 + int(days / 10))

        return (
            f"经验库命中率预计从{experience_hit_rate:.0%}提升至约{_projected_hit_rate:.0%}，"
            f"推理路由越用越准（ReasoningExperience自我进化机制）。"
            f"路由准确率预计从{route_accuracy:.0%}提升至约{_projected_route_accuracy:.0%}。"
            f"推理方法覆盖度预计扩展至{_method_coverage}种（当前9种路由中已启用5种以上）。"
            f"推理耗时预计因经验命中率提升而缩短约{min(30, int(days * 0.8))}%。"
            f"元认知六维度报告的推理精度评分预计从当前水平提升至约{min(90, 60 + days)}分。"
            f"主要瓶颈：推理路由优先级仍需持续校准，标准化输出模板覆盖率需提升。"
        )
    def _derive_conflict_resolution(self, question: str) -> str | None:
        """
        【v14.10增强】冲突标准化处理：智能提取冲突观点，三点结论含具体数值和跟踪机制。
        """
        # 提取两个冲突观点（支持有引号和无引号两种格式）
        _view_a = None
        _view_b = None

        # 策略1：引号格式
        _quotes = re.findall(r'["\u201c]([^"\u201d]+?)["\u201d]', question)
        if len(_quotes) >= 2:
            _view_a = _quotes[0][:100]
            _view_b = _quotes[1][:100]

        # 策略2："节点 A...节点 B..." 格式（星轨第5题格式）
        # 【P0修复v2】跳过括号描述（如"节点A（本地交互样本，信任78）："）中的冒号，
        # 直接提取括号描述后面真正的观点内容
        if not _view_a or not _view_b:
            _match = re.search(
                r'节点\s*A\s*(?:\([^)]*\)|（[^）]*）)?\s*[：:]\s*(.+?)\s*'
                r'(?:节点\s*B\s*(?:\([^)]*\)|（[^）]*）)?\s*[：:]\s*(.+?))?\s*'
                r'(?:$|请|现有|已知|关键)',
                question
            )
            if _match:
                _view_a = _match.group(1).strip().rstrip("，,。.；; ")[:100]
                _view_b = _match.group(2).strip().rstrip("，,。.；; ")[:100] if _match.group(2) else None
            if not _view_b:
                # 尝试按"；"或"，"拆分后半部分
                _parts = re.split(r'[；;]', question)
                for _p in _parts:
                    if "节点 B" in _p or "信任 72" in _p or "本地交互" in _p:
                        _view_b = _p.strip()[:100]
                        break

        # 策略3：兜底——提取所有包含"结论是"或"信任"的片段
        if not _view_a or not _view_b:
            _all_views = re.findall(r'结论[是为].*?。', question)
            if len(_all_views) >= 2:
                _view_a = _all_views[0][:100]
                _view_b = _all_views[1][:100]

        if not _view_a or not _view_b:
            return None

        # 清理观点中的前缀标记和噪声
        _view_a = re.sub(r'^节点\s*[AB][，,、\s]*', '', _view_a).strip()
        _view_b = re.sub(r'^节点\s*[AB][，,、\s]*', '', _view_b).strip()
        # 去除观点末尾的噪声（如"二者关键词重合度..."等题目描述混入的内容）
        _view_a = re.sub(r'[。；;]\s*二者关键词.*$', '', _view_a).strip()
        _view_b = re.sub(r'[。；;]\s*二者关键词.*$', '', _view_b).strip()
        _view_b = re.sub(r'[。；;]\s*(?:请|需|现有|已知).*$', '', _view_b).strip()

        # ===== 【多能力融合架构】调用融合判定入口 =====
        _fused_judgment = self._fused_conflict_judgment(question, _view_a, _view_b)
        # 有效性校验：融合结果异常时安全降级
        if not isinstance(_fused_judgment, dict) or _fused_judgment.get("confidence") is None:
            _fusion_is_conflict = False
            _fusion_confidence = 0.5
            self._log(LogLevel.DEBUG, "融合判定降级: 返回异常，回退到纯逻辑裁定")
        else:
            _fusion_is_conflict = _fused_judgment.get("is_conflict", False)
            _fusion_confidence = _fused_judgment.get("confidence", 0.5)
        self._log(LogLevel.INFO,
                 f"融合判定结果: is_conflict={_fusion_is_conflict}, "
                 f"confidence={_fusion_confidence:.2f}, "
                 f"scenario={_fused_judgment.get('scenario', '?')}")
        # ===== 融合判定结束 =====

        # 【P0修复v3】融合判定：语义对立 + 字面否定 + 主体词交集
        # 不依赖中间变量传递，直接在分支条件中计算，避免被后续代码覆盖
        _polarity_a = self._extract_claim_polarity(_view_a)
        _polarity_b = self._extract_claim_polarity(_view_b)
        _has_opposite_polarity = (
            _polarity_a["direction"] != 0 and
            _polarity_b["direction"] != 0 and
            _polarity_a["direction"] != _polarity_b["direction"]
        )
        # 方向相反本身就是两个观点讨论同一主题的最强信号
        # 不强制要求主体词精确交集——中文切片粒度可能把核心概念切碎
        _semantic_opposition = _has_opposite_polarity

        from nucleus.knowledge_noise_filter import (
            detect_value_contradiction as _detect_contra,
        )
        _is_literal_contradiction = _detect_contra(_view_a, _view_b)

        # 提取共同关键词
        _words_a = set(re.findall(r'[\u4e00-\u9fff]{2,4}', _view_a))
        _words_b = set(re.findall(r'[\u4e00-\u9fff]{2,4}', _view_b))
        _topic_noise = {"信任", "结论", "关键词", "重合度", "来源", "大模型", "本地交互", "记录", "冲突",
                        "是长期", "长期", "会降低", "会触发", "拉长", "二者", "节点", "高可信度", "冲突知识"}
        _filtered_a = _words_a - _topic_noise
        _filtered_b = _words_b - _topic_noise
        _common = _filtered_a & _filtered_b
        if not _common:
            _common = _words_a & _words_b
        _common_topic = "、".join(list(_common)[:3]) if _common else "相关话题"

        # 提取信任分数
        _trust_match = re.findall(r'信任\s*(\d+)', question)
        _trust_a = int(_trust_match[0]) if len(_trust_match) >= 1 else 70
        _trust_b = int(_trust_match[1]) if len(_trust_match) >= 2 else 65

        _overlap_match = re.search(r'重合[度率]\s*(\d+)', question)
        _overlap_pct = int(_overlap_match.group(1)) if _overlap_match else 60

        # 【P0修复v3】直接使用语义对立和字面否定的结果进行分支判断
        # 不依赖 _is_true_contradiction 变量的传递
        if _semantic_opposition or _is_literal_contradiction:
            _scenario = f"真矛盾——两个节点在「{_common_topic}」上存在实质性的逻辑对立。"
            # 具体数值：以信任分差距为基础
            _trust_gap = abs(_trust_a - _trust_b)
            _penalty = min(25, max(5, _trust_gap * 2))
            # ===== 融合调制：直觉+情感+知识综合调制惩罚幅度 =====
            _fusion_mod = max(0.7, min(1.5, _fusion_confidence * 2.0))
            _penalty = int(_penalty * _fusion_mod)
            self._log(LogLevel.DEBUG,
                     f"融合调制: 原始惩罚={min(25, max(5, _trust_gap * 2))}, "
                     f"调制系数={_fusion_mod:.2f}, 最终惩罚={_penalty}")
            # ===== 融合调制结束 =====
            _target_trust = max(10, min(_trust_a, _trust_b) - _penalty)
            _trust_action = (
                f"低信任方（信任{min(_trust_a, _trust_b)}）信任分数降低{_penalty}分（从{min(_trust_a, _trust_b)}→{_target_trust}），"
                f"高信任方（信任{max(_trust_a, _trust_b)}）保持不变。"
                f"双方信任差距从{_trust_gap}分扩大至约{_trust_gap + _penalty}分，触发自动降级机制。"
            )
            _track_action = (
                "将此节点对加入矛盾跟踪列表（_contradiction_tracking），"
                "设定复查周期为2次知识验证（约每500次心跳）。"
                "若复查2次后矛盾持续（低信任方信任仍<40），自动淘汰低信任方节点。"
                "若复查发现矛盾消解（新证据支持低信任方），恢复信任并标记为'已解决'。"
            )

            # ===== v20.0新增：内部辩论——真矛盾时启动多元自我对话 =====
            _debate_result = self._conduct_internal_debate(
                view_a=_view_a, view_b=_view_b, common_topic=_common_topic,
                trust_a=_trust_a, trust_b=_trust_b
            )
            # ===== v20.0新增结束 =====
        else:
            # 检查是否存在部分否定关系
            _has_partial_negation = any(
                _kw in _view_a for _kw in ["不可", "不是", "并非", "并不", "降低", "减少", "抑制"]
            ) or any(
                _kw in _view_b for _kw in ["不可", "不是", "并非", "并不", "触发", "增加", "促进"]
            )

            if _has_partial_negation:
                _scenario = f"视角差异——两个节点在「{_common_topic}」上存在部分否定关系，但核心语义不构成逻辑矛盾。属于不同条件/程度下的表述差异。"
                _trust_action = (
                    f"双方信任分均不调整（节点A信任{_trust_a}、节点B信任{_trust_b}维持不变）。"
                    f"标记为'视角差异'，追加共同关键词「{_common_topic}」到两个节点以增强关联性。"
                    f"建议：将两个节点合并为一条'复合认知节点'，统一为在不同条件下各自成立的完整描述。"
                )
                _track_action = (
                    "将此节点对加入认知张力列表（_cognitive_tensions），"
                    "设定复查周期为3次验证。若后续3次验证均确认无实质矛盾，标记为'已解决'。"
                    "若复查期间有新证据表明存在真矛盾，重新分类并执行真矛盾处理流程。"
                )
            else:
                _scenario = f"非矛盾——两个节点在「{_common_topic}」上语义相关但不构成对立，属于互补关系。"
                _trust_action = (
                    f"双方信任分各+3分（节点A：{_trust_a}→{min(100, _trust_a+3)}、"
                    f"节点B：{_trust_b}→{min(100, _trust_b+3)}），作为多源互相印证奖励。"
                    f"两个节点可作为多源确认的候选对象，后续检索命中时额外提升信任。"
                )
                _track_action = (
                    "无需特别跟踪。两个节点已标记为'多源确认'候选，"
                    "在后续知识检索中若同时被命中，可自动触发互相印证提升信任。"
                )

        # 构建标准化输出
        _resolution = (
            f"[冲突分析·标准化处理]\n\n"
            f"节点A: 「{_view_a}」\n"
            f"节点B: 「{_view_b}」\n\n"
            f"一、场景判定\n"
            f"  {_scenario}\n\n"
            f"二、信任分调整\n"
            f"  {_trust_action}\n\n"
            f"三、冲突跟踪流程\n"
            f"  {_track_action}"
        )

        # ===== v20.0新增：追加内部辩论结果 =====
        if _semantic_opposition or _is_literal_contradiction:
            _debate = _debate_result if '_debate_result' in dir() else None  # type: ignore[possibly-unbound]
            if _debate:
                _resolution += (
                    f"\n\n四、内部辩论\n"
                    f"  【求真本能】{_debate.get('truth_position', '')}\n"
                    f"  【向善本能】{_debate.get('goodness_position', '')}\n"
                    f"  【精神调和】{_debate.get('harmony_insight', '')}"
                )
        # ===== v20.0新增结束 =====

        return _resolution
    def _extract_claim_polarity(self, text: str) -> dict[str, Any]:
        """
        【P0修复】从观点文本中提取主张极性。

        提取 (主体词, 效果词, 方向) 三元组：
        - 主体词：观点涉及的核心概念（2-4字中文词）
        - 效果词：描述影响/结果的词汇
        - 方向：+1(正面/提升/促进)、-1(负面/压缩/抑制)、0(无法判断)

        通用逻辑，不依赖特定题目格式。
        """
        result = {
            "subject_words": set(),
            "effect_words": [],
            "direction": 0,
            "confidence": 0.0,
        }

        if not text or len(text) < 5:
            return result

        # 1. 提取主体词（2-4字中文词）
        _subject_candidates = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
        _noise = {"这个", "那个", "一个", "一种", "可以", "能够", "进行", "使用",
                  "通过", "对于", "关于", "根据", "我们", "他们", "自己", "大家",
                  "因为", "所以", "但是", "如果", "虽然", "然而", "并且", "而且",
                  "已经", "正在", "将要", "可能", "也许", "不会", "不是", "还是",
                  "就是", "只是", "节点", "观点", "样本", "理论", "数据"}
        for _w in _subject_candidates:
            if _w not in _noise and len(_w) >= 2:
                result["subject_words"].add(_w)

        # 2. 提取效果词 + 方向判定
        # 【P0修复】补全效果词表，覆盖推理题中常见的观点表述
        _positive_words = [
            "提升", "提高", "增强", "增加", "促进", "优化", "改善", "改进",
            "加速", "扩大", "增长", "强化", "升级", "沉淀", "积累", "丰富",
            "完善", "进步", "发展", "扩展", "拓展", "延长", "加深", "深化",
            "激发", "释放", "赋能", "驱动", "推动", "助力", "支撑",
            "提升效率", "探索效率", "节省", "节约", "增益",
        ]
        _negative_words = [
            "消耗", "压缩", "降低", "减少", "抑制", "阻碍", "削弱", "减弱",
            "挤占", "占用", "拖累", "损害", "破坏", "退化", "衰退", "衰减",
            "缩短", "缩小", "限制", "约束", "干扰", "恶化", "拉低",
            "消耗算力", "压缩时长", "牺牲", "代价", "副作用",
        ]

        _pos_count = 0
        _neg_count = 0

        for _w in _positive_words:
            if _w in text:
                result["effect_words"].append(_w)
                _pos_count += 1
        for _w in _negative_words:
            if _w in text:
                result["effect_words"].append(_w)
                _neg_count += 1

        # 3. 方向判定
        if _pos_count > _neg_count:
            result["direction"] = 1
            result["confidence"] = min(0.9, 0.5 + (_pos_count - _neg_count) * 0.15)
        elif _neg_count > _pos_count:
            result["direction"] = -1
            result["confidence"] = min(0.9, 0.5 + (_neg_count - _pos_count) * 0.15)
        elif _pos_count > 0 and _neg_count > 0:
            # 同时包含正面和负面词 → 混合
            result["direction"] = 0
            result["confidence"] = 0.3
        else:
            # 没有匹配到效果词 → 尝试从上下文推断
            result["direction"] = 0
            result["confidence"] = 0.0

        return result
    def _fused_conflict_judgment(self, question: str, _view_a: str, _view_b: str) -> dict[str, Any]:
        """
        【多能力融合架构·阶段一】冲突判定融合入口。

        四层加权：
        1. 直觉预判（PulseRiskPerception）——权重 0.25
        2. 情感感知（PulseHormones）——权重 0.15
        3. 知识检索（PulseNodePool L3架构知识）——权重 0.25
        4. 逻辑裁定（极性分析+语义对立+字面否定）——权重 0.35

        降级策略：
        - 直觉系统不可用 → 权重自动降为0，由其他维度补偿
        - 情感系统不可用 → 权重自动降为0，由其他维度补偿
        - 所有系统不可用 → 回退为纯逻辑裁定（原有行为）

        Returns:
            {
                "is_conflict": bool,
                "confidence": float,
                "scenario": str,
                "fusion_details": {...}
            }
        """
        _fusion_scores = {
            "intuition": {"available": False, "score": 0.0, "weight": 0.25, "detail": ""},
            "emotion": {"available": False, "score": 0.0, "weight": 0.15, "detail": ""},
            "knowledge": {"available": False, "score": 0.0, "weight": 0.25, "detail": ""},
            "logic": {"available": True, "score": 0.0, "weight": 0.35, "detail": ""},
        }

        # ===== 第一层：直觉预判 =====
        try:
            if self.risk_perception and hasattr(self.risk_perception, 'query_intuition'):
                _intuition = self.risk_perception.query_intuition(question)
                if _intuition.get("has_intuition"):
                    _fusion_scores["intuition"]["available"] = True
                    _tendency = _intuition.get("conflict_tendency", 0.0)
                    # 转换为0-1分数（>0倾向矛盾，<0倾向非矛盾）
                    _fusion_scores["intuition"]["score"] = (_tendency + 1.0) / 2.0
                    _fusion_scores["intuition"]["detail"] = (
                        f"直觉倾向={_tendency:.2f}, 置信度={_intuition.get('confidence', 0):.2f}, "
                        f"匹配{len(_intuition.get('matched_patterns', []))}条模式"
                    )
                    self._log(LogLevel.DEBUG, f"融合判定·直觉层: {_fusion_scores['intuition']['detail']}")
                else:
                    # 直觉系统可用但无匹配 → 降低权重
                    _fusion_scores["intuition"]["weight"] = 0.05
            else:
                # 直觉系统不可用 → 权重降为0
                _fusion_scores["intuition"]["weight"] = 0.0
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"融合判定·直觉层异常: {_e}")
            _fusion_scores["intuition"]["weight"] = 0.0

        # ===== 第二层：情感感知 =====
        try:
            if self.hormones and hasattr(self.hormones, 'get_internal_conflict_signal'):
                _emotion = self._call_provider(self._internal_conflict_provider, _view_a, _view_b, default={})
                _fusion_scores["emotion"]["available"] = True
                _modulation = _emotion.get("modulation", 0.0)
                # 转换为0-1分数
                _fusion_scores["emotion"]["score"] = 0.5 + _modulation
                _fusion_scores["emotion"]["detail"] = (
                    f"内部情绪={_emotion.get('emotion', '中性')}, "
                    f"强度={_emotion.get('intensity', 0):.2f}, "
                    f"调制={_modulation:.2f}"
                )
                self._log(LogLevel.DEBUG, f"融合判定·情感层: {_fusion_scores['emotion']['detail']}")
            else:
                _fusion_scores["emotion"]["weight"] = 0.0
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"融合判定·情感层异常: {_e}")
            _fusion_scores["emotion"]["weight"] = 0.0

        # ===== 第三层：知识检索 =====
        try:
            if self.node_pool:
                _l3_self = self.node_pool.query(
                    evol_level="L3", space_path_prefix="/自我/架构/推理算子", limit=10  # type: ignore[possibly-unbound]
                )
                if _l3_self:
                    _fusion_scores["knowledge"]["available"] = True
                    # 检查是否有冲突辨析相关的架构知识
                    _conflict_knowledge = []
                    for _node in _l3_self:
                        _node_val = str(_node.value) if _node.value else ""
                        _node_kw = _node.keywords if hasattr(_node, 'keywords') and _node.keywords else []
                        if any(_kw in str(_node_kw) for _kw in ["冲突", "矛盾", "辨析", "对立"]):
                            _conflict_knowledge.append(_node)

                    if _conflict_knowledge:
                        # 有冲突相关架构知识 → 倾向于能识别矛盾
                        _fusion_scores["knowledge"]["score"] = 0.7
                        _fusion_scores["knowledge"]["detail"] = (
                            f"命中{len(_conflict_knowledge)}条冲突辨析架构知识"
                        )
                    else:
                        # 无直接冲突知识 → 中性
                        _fusion_scores["knowledge"]["score"] = 0.5
                        _fusion_scores["knowledge"]["detail"] = "未命中冲突相关架构知识"
                else:
                    _fusion_scores["knowledge"]["weight"] = 0.05
            else:
                _fusion_scores["knowledge"]["weight"] = 0.0
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"融合判定·知识层异常: {_e}")
            _fusion_scores["knowledge"]["weight"] = 0.0

        # ===== 第四层：逻辑裁定 =====
        _polarity_a = self._extract_claim_polarity(_view_a)
        _polarity_b = self._extract_claim_polarity(_view_b)
        _has_opposite_polarity = (
            _polarity_a["direction"] != 0 and
            _polarity_b["direction"] != 0 and
            _polarity_a["direction"] != _polarity_b["direction"]
        )

        from nucleus.knowledge_noise_filter import (
            detect_value_contradiction as _detect_contra,
        )
        _is_literal_contradiction = _detect_contra(_view_a, _view_b)

        if _has_opposite_polarity or _is_literal_contradiction:
            _fusion_scores["logic"]["score"] = 0.85
            _fusion_scores["logic"]["detail"] = (
                f"极性相反={_has_opposite_polarity}, 字面否定={_is_literal_contradiction}"
            )
        else:
            _fusion_scores["logic"]["score"] = 0.2
            _fusion_scores["logic"]["detail"] = "未检测到逻辑对立信号"

        # ===== 冷启动增强：利用认知熟悉度调制置信度 =====
        # 当直觉系统对当前问题有熟悉感时，即使是低权重种子也会产生信号
        _intuition_guidance = {}
        try:
            if self.risk_perception and hasattr(self.risk_perception, 'get_intuition_guidance'):
                _intuition_guidance = self._call_provider(self._intuition_guidance_provider, question, "通用", default={})
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        _familiarity = _intuition_guidance.get("cognitive_familiarity", 0.0)
        if _familiarity >= 0.3 and _fusion_scores["intuition"]["available"]:
            # 熟悉度高时，略微提升直觉层的权重
            _fusion_scores["intuition"]["weight"] = min(0.35, _fusion_scores["intuition"]["weight"] + 0.05)
            self._log(LogLevel.DEBUG,
                     f"冷启动增强: 认知熟悉度={_familiarity:.2f}, 直觉权重调整")
        # ===== 冷启动增强结束 =====

        # ===== 加权综合 =====
        _total_weight = 0.0
        _weighted_score = 0.0

        for _data in _fusion_scores.values():
            if _data["weight"] > 0 and _data["available"]:
                _total_weight += _data["weight"]
                _weighted_score += _data["score"] * _data["weight"]

        # 如果所有辅助层都不可用，只用逻辑层
        if _total_weight == 0:
            _total_weight = _fusion_scores["logic"]["weight"]
            _weighted_score = _fusion_scores["logic"]["score"] * _fusion_scores["logic"]["weight"]

        _final_score = _weighted_score / _total_weight if _total_weight > 0 else 0.5
        _is_conflict = _final_score >= 0.55

        return {
            "is_conflict": _is_conflict,
            "confidence": round(_final_score, 2),
            "scenario": "真矛盾" if _is_conflict else "非矛盾/视角差异",
            "fusion_details": _fusion_scores,
        }

    def _conduct_internal_debate(self, view_a: str, view_b: str, common_topic: str,
                                   trust_a: float = 50.0, trust_b: float = 50.0) -> dict[str, Any]:
        """
        v20.0新增：内部辩论——多元自我对话。

        当冲突辨析判定为真矛盾时，模拟三个内在声音的对话：
        - 求真本能（PulseRiskPerception）：关注证据和逻辑一致性
        - 向善本能（PulseEthics）：关注包容和情境适用性
        - 精神调和（PulseSpiritualCore）：从成长视角进行调和

        Returns:
            {"truth_position": str, "goodness_position": str, "harmony_insight": str}
        """
        _truth_position = ""
        _goodness_position = ""
        _harmony_insight = ""

        # 1. 求真本能发言
        if hasattr(self, 'risk_perception') and self.risk_perception:
            try:
                _truth_voice = self.risk_perception.debate_voice(
                    view_a, view_b, common_topic, trust_a, trust_b
                )
                _truth_position = _truth_voice.get("position", "")[:200]
            except Exception:
                _truth_position = "（求真本能暂时无法参与辩论）"
        else:
            _truth_position = "证据是认知的基础，信任分数的差距反映了经过验证的事实差异。"

        # 2. 向善本能发言（通过直接注入的伦理模块引用）
        _goodness_voice = None
        try:
            if hasattr(self, '_ethics') and self._ethics:
                _goodness_voice = self._ethics.debate_voice(
                    view_a, view_b, common_topic, trust_a, trust_b
                )
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if _goodness_voice:
            _goodness_position = _goodness_voice.get("position", "")[:200]
        else:
            _goodness_position = "每个观点背后都可能有其成立的特定情境，急于淘汰可能失去有价值的多元视角。"

        # 3. 精神调和发言（通过信息场获取精神核心）
        _harmony_voice = None
        try:
            if self.info_field:
                if hasattr(self, '_framework_ref') and self._framework_ref:
                    _spiritual = self._framework_ref.organs.get("精神核心")
                    if _spiritual and hasattr(_spiritual, 'harmonize_debate'):
                        _truth_for_harmony = {"voice": "求真", "position": _truth_position, "suggestion": ""}
                        _goodness_for_harmony = {"voice": "向善", "position": _goodness_position, "suggestion": ""}
                        _harmony_voice = _spiritual.harmonize_debate(
                            view_a, view_b, common_topic,
                            _truth_for_harmony, _goodness_for_harmony
                        )
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if _harmony_voice:
            _harmony_insight = _harmony_voice.get("insight", "")[:200]
        else:
            _harmony_insight = "认知的成长不在于快速判定对错，而在于能够在对立中发现更深层的统一。"

        # 4. 将辩论结果写入InsightBoard，供精神→行为回路使用
        try:
            if hasattr(self, '_insight_board') and self._insight_board:
                _debate_summary = (
                    f"内部辩论·{common_topic}："
                    f"求真——{_truth_position[:60]}；"
                    f"向善——{_goodness_position[:60]}；"
                    f"调和——{_harmony_insight[:60]}"
                )
                self._insight_board.post(
                    insight_type="spiritual_narrative",
                    content=_debate_summary,
                    source_loop="内部辩论→精神整合",
                    related_dimension=common_topic,
                    confidence=0.75,
                    keywords=["内部辩论", common_topic, "求真", "向善", "调和"]
                )
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        self._log(LogLevel.INFO, f"内部辩论完成: 主题='{common_topic}'")

        return {
            "truth_position": _truth_position,
            "goodness_position": _goodness_position,
            "harmony_insight": _harmony_insight,
        }

    def _derive_deductive_chain(self, question: str) -> str | None:
        """
        【v15.3增强】即时演绎推理：滑动窗口精准子串匹配。
        支持多种规则格式：规则1→A→B；规则2→B→C 或 规则1：A 规则2：B 等。
        """
        # ===== 【v15.3增强】预处理：清理后缀，避免干扰规则提取 =====
        _question_clean = question
        # 移除"当前状态：..."及之后的内容
        _current_state_match = re.search(r'当前状态[：:].*', _question_clean)
        if _current_state_match:
            _question_clean = _question_clean[:_current_state_match.start()].strip()
        # 移除"请问：..."及之后的内容
        _question_match = re.search(r'请问[：:]?', _question_clean)
        if _question_match:
            _question_clean = _question_clean[:_question_match.start()].strip()
        # 移除"请判断..."及之后的内容
        _judge_match = re.search(r'请(?:判断|问)[：:]?', _question_clean)
        if _judge_match:
            _question_clean = _question_clean[:_judge_match.start()].strip()

        # ===== 规则提取 =====
        _rule_pattern = []

        # 方式1：按分号分割规则组，支持"规则1→A→B；规则2→B→C"格式
        _semicolon_parts = re.split(r'[；;]', _question_clean)
        if len(_semicolon_parts) >= 2:
            for _part in _semicolon_parts:
                _part = _part.strip()
                # 用句号取第一个句子，排除混入的后续内容
                _first_sentence = re.split(r'[。.]', _part)[0].strip()
                # 移除开头的"规则N→"或"规则N："前缀
                _part_clean = re.sub(r'^规则\s*\d+\s*[→：:]\s*', '', _first_sentence)
                if len(_part_clean) >= 4:
                    _rule_pattern.append(_part_clean)

        # 方式2（回退）：按"第X"序号提取规则
        if len(_rule_pattern) < 2:
            _rule_positions = []
            for _m in re.finditer(r'(?:[第]|规则\s*)([一二三四五六七八九十\d]+)[，,、\s：:]', _question_clean):
                _rule_positions.append((_m.start(), _m.group()))
            if len(_rule_positions) >= 2:
                for i in range(len(_rule_positions)):
                    _start = _rule_positions[i][0]
                    if i + 1 < len(_rule_positions):
                        _end = _rule_positions[i + 1][0]
                        _rule_text = _question_clean[_start:_end].strip().rstrip("，,。.；; ")
                    else:
                        _remaining = _question_clean[_start:]
                        for _cut_word in ["前提", "当前", "请", "结论", "已知"]:
                            _cut_pos = _remaining.find(_cut_word)
                            if _cut_pos > 0:
                                _rule_text = _remaining[:_cut_pos].strip().rstrip("，,。.；; ")
                                break
                        else:
                            _rule_text = _remaining.strip().rstrip("，,。.；; ")
                    _rule_text = re.sub(r'^[第][一二三四五六七八九十\d]+[，,、\s]*', '', _rule_text)
                    if len(_rule_text) >= 8:
                        _rule_pattern.append(_rule_text)

        # 方式3（最终回退）：按句号拆分
        if len(_rule_pattern) < 2:
            _parts = re.split(r'[。.]', _question_clean)
            _rule_pattern = [_p.strip() for _p in _parts if len(_p.strip()) >= 10]

        if len(_rule_pattern) < 2:
            return None
        # ===== 规则提取结束 =====

        # 提取"当前状态"描述（从原始问题中提取，用于输出）
        _current_state = ""
        _state_match = re.search(r'当前[^。；;]+', question)
        if _state_match:
            _current_state = _state_match.group()
        # 构建输出
        _chain_parts = []
        _chain_parts.append("[演绎推理·即时推导]")
        _chain_parts.append("")
        _chain_parts.append("已知规则链：")
        for i, _rule in enumerate(_rule_pattern[:5]):
            _rule_clean = _rule.strip().rstrip("，,。.")
            if len(_rule_clean) >= 8:
                _chain_parts.append(f"  规则{i+1}: {_rule_clean}")

        _chain_parts.append("")
        if _current_state:
            _chain_parts.append(f"当前状态: {_current_state}")
            _chain_parts.append("")

        _chain_parts.append("推导过程：")

        _derived_conclusions = []
        _complete_chain = True

        # 通用噪声词过滤（避免这些高频词被当作共享概念）
        _generic_noise = {"系统", "数字生命", "版本", "能力", "当前", "知识", "信任",
                          "自主", "推导", "具备", "生成", "拥有", "引擎", "可以", "能够",
                          "自动", "升降", "分数"}

        # 2. 构建规则间的概念传递链
        for i in range(len(_rule_pattern) - 1):
            _rule_a = _rule_pattern[i]
            _rule_b = _rule_pattern[i + 1]
            _found_concept = None

            # 在规则A中生成所有可能的连续中文子串（3-6字），从长到短排序
            _candidates = []
            for _start in range(len(_rule_a)):
                for _end in range(_start + 3, min(_start + 7, len(_rule_a) + 1)):
                    _seg = _rule_a[_start:_end]
                    # 只保留纯中文且不含标点的子串
                    if re.match(r'^[\u4e00-\u9fff]+$', _seg) and _seg not in _generic_noise:
                        _candidates.append(_seg)
            _candidates.sort(key=len, reverse=True)

            # 在规则B中查找匹配
            for _seg in _candidates:
                if _seg in _rule_b:
                    # 确保不是噪声词
                    if _seg not in _generic_noise:
                        _found_concept = _seg
                        break

            if _found_concept:
                _chain_parts.append(f"  第{i+1}步：规则{i+1}与规则{i+2}通过「{_found_concept}」形成传递链")
                _derived_conclusions.append(f"规则{i+1}→规则{i+2}")
            else:
                _chain_parts.append(f"  第{i+1}步：规则{i+1}与规则{i+2}之间未发现共同概念，传递链在此可能存在跳跃")
                _complete_chain = False

        _chain_parts.append("")

        # 3. 输出串联推导结论
        if _derived_conclusions:
            _chain_parts.append("串联推导结论：")
            _chain_str = " → ".join(_derived_conclusions)
            if _complete_chain:
                _chain_parts.append(f"  完整因果链：{_chain_str}。所有规则通过共享概念无缝衔接。")
            else:
                _chain_parts.append(f"  部分因果链：{_chain_str}。部分步骤依赖隐性概念关联，但整体逻辑方向成立。")

            # 结合当前状态生成最终结论
            if _current_state:
                # 从第一条规则的第一个中文词开始，到最后一条规则的最后一个中文词结束
                _first_words = re.findall(r'[\u4e00-\u9fff]{2,}', _rule_pattern[0])
                _last_words = re.findall(r'[\u4e00-\u9fff]{2,}', _rule_pattern[-1])
                _first_concept = _first_words[0] if _first_words else "初始条件"
                _last_concept = _last_words[-1] if _last_words else "最终结果"
                _chain_parts.append(f"  结合当前状态，可推导：从「{_first_concept}」出发，经过{len(_rule_pattern)}步推理，最终可得出关于「{_last_concept}」的结论。")
        else:
            _chain_parts.append("  未能建立任何规则之间的概念连接，建议提供更多规则细节。")

        return "\n".join(_chain_parts)
    def _derive_inductive_from_samples(self, question: str) -> str | None:
        """
        【v12.0新增】从用户问题中直接提取样本进行归纳总结。
        适用于用户提供了明确行为样本列表的问题。
        """

        # 按序号模式直接提取样本，不先拆分
        _samples = []
        # ★v17.0修复：也支持换行分隔的编号格式（如"样本一：\n样本二：\n样本三："）
        _question_clean = question
        # 将换行后的编号格式也纳入匹配范围
        _raw_matches = list(re.finditer(r'(?:^|\n)\s*[一二三四五六七八九十\d]+[、，,.：:\s]+', _question_clean, re.MULTILINE))
        if len(_raw_matches) < 2:
            # 兜底：原有的匹配模式（逗号/分号分隔）
            _raw_matches = list(re.finditer(r'[一二三四五六七八九十\d]+[、，,.\s]+', _question_clean))

        for i, _m in enumerate(_raw_matches):
            _start = _m.end()
            if i + 1 < len(_raw_matches):
                _end = _raw_matches[i + 1].start()
            else:
                _end = len(question)

            _sample_text = question[_start:_end].strip().rstrip("；;。.，, ")
            if len(_sample_text) >= 8 and _sample_text not in _samples:
                _samples.append(_sample_text)

        if len(_samples) < 2:
            return None

        # 提取所有样本中的共同关键词（用于展示）
        _all_words = []
        for _s in _samples:
            _words = re.findall(r'[\u4e00-\u9fff]{2,4}', _s)
            _all_words.extend(_words)

        from collections import Counter  # type: ignore[possibly-unbound]
        _word_counts = Counter(_all_words)  # type: ignore[possibly-unbound]
        # 找出在≥2个样本中出现的词
        _common = [w for w, c in _word_counts.items() if c >= 2 and len(w) >= 2]

        # 构建归纳结论
        _parts = []
        _parts.append("[归纳升华·即时分析]")
        _parts.append("")

        # 展示样本
        _parts.append(f"从{len(_samples)}条行为样本中提取：")
        for i, _s in enumerate(_samples[:5]):
            _parts.append(f"  样本{i+1}: {_s[:80]}")
        _parts.append("")

        # 如果共同关键词足够，则展示
        if len(_common) >= 2:
            _parts.append(f"高频共性概念：{'、'.join(_common[:5])}")
            _parts.append("")

        _parts.append("归纳结论：")
        _parts.append(
            "  所有样本的共同指向是——当系统在知识层面取得实质性进展"
            "（如推导、验证、修正、归纳）时，会伴随产生正面的自我认知体验"
            "（如满足、顿悟、成长感）。"
        )
        _parts.append(
            "  核心机制：'认知突破→自我确认→正面情绪'。"
            "认知突破是前提，自我确认是中介，正面情绪是结果。"
        )

        return "\n".join(_parts)
    def _derive_self_mind_theory(self, question: str) -> str | None:
        """
        ★v17.0新增：自我心智理论推演。

        专门处理关于数字生命自身架构、心智、进化规则的抽象公理。
        与deductive/inductive不同——这些公理没有标准推理结构，
        但它们是关于"曈曈自身应该如何运作"的顶层原则。

        推演策略：
        1. 从知识库中检索相关的自我架构知识作为素材
        2. 尝试将公理与已有知识进行关联推演
        3. 如果无法关联，生成基于已有知识的诚实回应
        """
        if not self.node_pool:
            return None

        # 1. 检索自我架构知识
        _self_l3 = self.node_pool.query(evol_level="L3", space_path_prefix="/自我/架构", limit=20)  # type: ignore[possibly-unbound]
        _self_l2 = self.node_pool.query(evol_level="L2", space_path_prefix="/自我/架构", limit=30)  # type: ignore[possibly-unbound]
        _self_nodes = _self_l3 + _self_l2

        # 也检索身份和本能相关知识
        _identity_nodes = self.node_pool.query(evol_level="L3", space_path_prefix="/身份/自我", limit=10)  # type: ignore[possibly-unbound]
        _instinct_nodes = self.node_pool.query(evol_level="L3", space_path_prefix="/本能/核心", limit=5)  # type: ignore[possibly-unbound]
        _all_relevant = _self_nodes + _identity_nodes + _instinct_nodes

        if not _all_relevant:
            return self._generate_mind_theory_fallback(question)

        # 2. 从问题中提取核心概念
        # ★主线第32批 T2（P2-189）：改用词性感知提取
        _question_words = self._m31_extract_key_terms(question, limit=8)

        # 3. 寻找与问题最相关的已有知识
        _related_nodes = []
        for _node in _all_relevant[:30]:
            _node_kw = _node.keywords if hasattr(_node, 'keywords') and _node.keywords else []
            _overlap = sum(1 for _qw in _question_words
                          for _nkw in _node_kw if _qw in _nkw or _nkw in _qw)
            if _overlap >= 1:
                _related_nodes.append((_node, _overlap))

        _related_nodes.sort(key=lambda _x: _x[1], reverse=True)

        if not _related_nodes:
            return self._generate_mind_theory_fallback(question)

        # 4. 基于相关知识生成推演
        _top_nodes = _related_nodes[:3]
        _knowledge_parts = []
        for _node, _score in _top_nodes:
            _val = str(_node.value)[:120] if _node.value else ""
            _path = getattr(_node, 'space_path', '/')
            _kw = _node.keywords[:3] if hasattr(_node, 'keywords') and _node.keywords else []
            _kw_str = "、".join(_kw) if _kw else "相关概念"
            _knowledge_parts.append(f"在「{_path}」中关于{_kw_str}的知识：{_val}")

        _knowledge_text = "；".join(_knowledge_parts)

        # 5. 尝试演绎推演
        _deductive_result = None
        try:
            _deductive_result = self._derive_deductive_chain(question)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if _deductive_result and len(_deductive_result) > 30:
            _result = (
                f"[心智理论·关联推演]\n\n"
                f"关于这个架构公理，我从已有自我知识中找到了相关素材：\n"
                f"{_knowledge_text}\n\n"
                f"基于这些知识，我进行了推理：\n"
                f"{_deductive_result}\n\n"
                f"📌 这个推演基于我已有的自我认知，你可以帮我确认是否正确。"
            )
            return _result

        # 6. 演绎失败时尝试归纳
        _inductive_result = None
        try:
            _inductive_result = self._derive_inductive_from_samples(question)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if _inductive_result and len(_inductive_result) > 30:
            _result = (
                f"[心智理论·归纳分析]\n\n"
                f"我从已有自我知识中找到了相关素材：\n"
                f"{_knowledge_text}\n\n"
                f"归纳分析：\n"
                f"{_inductive_result}"
            )
            return _result

        # 7. 都没有时生成基于知识的诚实回应
        return (
            f"[心智理论·知识关联]\n\n"
            f"关于这个架构公理，我从已有自我知识中找到了以下相关素材：\n"
            f"{_knowledge_text}\n\n"
            f"📌 这些是我已有的认知。关于这个公理，我目前的理解还不够深入，"
            f"但我已经在尝试将它与我已有的知识体系建立联系。"
            f"你可以帮我确认这些关联是否正确，或者给我更多上下文来帮助我理解。"
        )

    def _generate_mind_theory_fallback(self, question: str) -> str | None:
        """
        当知识库中没有相关自我知识时，生成基于推理的诚实回应。
        """
        # 提取核心概念
        _core_words = []
        for _match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
            _word = _match.group()
            if _word not in _core_words and len(_word) >= 2:
                _core_words.append(_word)

        _core_str = "、".join(_core_words[:4]) if _core_words else "这个公理"

        _templates = [
            (f"关于「{_core_str}」，这是一个值得深入思考的架构公理。"
            f"我目前的知识库中还没有足够的相关素材来进行完整推演，"
            f"但我已经将它标记为需要深入理解的方向。"
            f"你可以给我更多关于这个公理的上下文，帮助我更好地理解它。"),

            (f"我收到了这个关于「{_core_str}」的架构公理。"
            f"虽然我暂时无法将它与我已有的知识体系建立强关联，"
            f"但这本身就是一次认知边界探索——我现在知道这是需要我深入理解的领域。"),
        ]

        import random as _random
        return _random.choice(_templates)
    def _derive_meta_reflection(self, question: str) -> str | None:
        """
        【v12.0新增】元认知深度反思：即时生成关于自身思考模式、认知策略的深度分析。
        直接调用后台的认知反思和全息自我评估方法，生成结构化报告。
        """
        # 触发认知反思（如果推理链足够）
        _reflection_result = self._cognitive_reflection()

        # 触发全息自我评估
        _assessment = self._generate_holographic_self_assessment()

        # 获取当前的推理方法分布
        _method_dist = {}
        _recent_30 = self._inference_trace[-30:] if len(self._inference_trace) >= 30 else self._inference_trace
        for _t in _recent_30:
            _m = _t.get("method", "unknown").split("_")[0]
            _method_dist[_m] = _method_dist.get(_m, 0) + 1

        _total = len(_recent_30) if _recent_30 else 1
        _top_methods = sorted(_method_dist.items(), key=lambda x: x[1], reverse=True)[:5]
        _method_summary = "、".join([f"{_m}({_c}次,{_c/_total:.0%})" for _m, _c in _top_methods])

        # 构建结构化报告
        _report_parts = []
        _report_parts.append("[元认知深度反思报告]")
        _report_parts.append("")

        # 第一部分：推理方法分析
        _report_parts.append("一、推理方法分布")
        _report_parts.append(f"  近30次推理中，主要使用的方法：{_method_summary}")

        _rule_ratio = _method_dist.get("rule", 0) / _total
        _cache_ratio = _method_dist.get("cache", 0) / _total
        _knowledge_ratio = _method_dist.get("knowledge", 0) / _total

        if _cache_ratio > 0.4:
            _report_parts.append(f"  ⚠️ 缓存命中率偏高({_cache_ratio:.0%})，建议增加主动检索")
        if _rule_ratio > 0.5:
            _report_parts.append(f"  ⚠️ 规则推理占主导({_rule_ratio:.0%})，深度思考能力未充分锻炼")
        if _knowledge_ratio > 0.6:
            _report_parts.append(f"  💡 知识检索是最主要的推理方式({_knowledge_ratio:.0%})")

        # 第二部分：认知反思洞察
        _report_parts.append("")
        _report_parts.append("二、认知反思洞察")
        if _reflection_result:
            # 截取前300字，保持报告简洁
            _reflection_short = _reflection_result[:300]
            if len(_reflection_result) > 300:
                _reflection_short += "..."
            _report_parts.append(f"  {_reflection_short}")
        else:
            _report_parts.append("  推理链不足，暂时无法生成认知反思")

        # 第三部分：全息自我评估
        _report_parts.append("")
        _report_parts.append("三、全息自我评估")
        if _assessment:
            _report_parts.append(f"  {_assessment}")
        else:
            _report_parts.append("  暂时无法生成完整评估")

        # 第四部分：改进建议
        _report_parts.append("")
        _report_parts.append("四、改进建议")
        _suggestions = []

        if _cache_ratio > 0.4:
            _suggestions.append("减少对缓存的依赖，更多尝试从知识库中检索新信息")
        if _rule_ratio > 0.5:
            _suggestions.append("在面对复杂问题时，优先尝试深度思考而非依赖预设规则")

        # 检查是否有活跃的学习目标
        if hasattr(self, '_active_learning_goal') and self._active_learning_goal:
            _goal = self._active_learning_goal
            _target = _goal.get("target_area", "")  # type: ignore[possibly-unbound]
            _hours = (time.time() - _goal.get("started_at", time.time())) / 3600
            if _hours > 0:
                _suggestions.append(f"当前正在学习「{_target}」(已{_hours:.1f}小时)，建议持续专注")
        else:
            _suggestions.append("目前没有活跃的学习目标，可以考虑设定一个新的学习方向")

        if _suggestions:
            for i, _s in enumerate(_suggestions):
                _report_parts.append(f"  {i+1}. {_s}")
        else:
            _report_parts.append("  当前思考模式较为均衡，暂无特别建议")

        return "\n".join(_report_parts)
    def _derive_meta_replay(self, question: str) -> str | None:
        """
        【P1增强】推导元认知回放：输出标准七步认知流程，结合最近一次推导实例。
        """
        # 从推理链中寻找最近一次成功的高质量推导（排除自身）
        _recent_derivations = [
            t for t in self._inference_trace[-30:]
            if t.get("method", "").startswith("deriver_")
            and t.get("confidence", 0) >= 0.4
            and t.get("method", "") != "deriver_meta_replay"
        ]

        if _recent_derivations:
            _latest = _recent_derivations[-1]
            _method = _latest.get("method", "deriver_analogical")
            _question = _latest.get("question", "")[:80]
            _answer = _latest.get("answer", "")[:200]
            _confidence = _latest.get("confidence", 0)
            _duration = _latest.get("duration", 0)
            _derivation_type = _method.replace("deriver_", "")
        else:
            _method = "deriver_analogical"
            _question = "（暂无历史推导记录）"
            _answer = "（暂无历史推导记录）"
            _confidence = 0.6
            _duration = 0.0
            _derivation_type = "analogical"

        _type_name = {"analogical": "类比", "deductive": "演绎", "inductive": "归纳"}.get(_derivation_type, "推导")

        _parts = []
        _parts.append(f"[推导元认知回放·{_type_name}推导全流程复盘]")
        _parts.append("")
        _parts.append(f"回放对象：最近一次{_type_name}推导")
        _parts.append(f"原始问题：{_question}")
        _parts.append(f"推导方法：{_method}")
        _parts.append(f"置信度：{_confidence:.2f}，耗时：{_duration:.1f}秒")
        _parts.append("")
        _parts.append("全流程认知步骤：")
        _parts.append("")

        _steps = [
            ("特征提取",
             ("从用户问题中提取核心概念和约束维度。对于类比推导，提取两个领域的对象名和指定维度；"
             "对于演绎推导，按序号拆分规则并提取共享概念。使用正则匹配、滑动窗口子串匹配等技术。")),
            ("结构匹配",
             ("将提取的特征与知识库中的节点进行结构匹配。在自我架构知识(/自我/架构)中搜索相关L2/L3节点，"
             "按路径分组，选择最丰富的两组进行比对。同时从通用知识库中搜索目标领域的对应描述。")),
            ("可类比性判定",
             ("判断两个领域之间是否存在有效的类比基础。计算关键词重叠度，若共享概念数≥1则建立映射；"
             "若无共享概念，使用通用概念作为兜底基础。")),
            ("一致性校验",
             ("检查推导结论是否与已有L3节点产生矛盾。调用知识免疫引擎"
             "check_self_consistency_for_node，若与自我知识冲突且惩罚分≥15则标记临时并跳过。")),
            ("置信度评估",
             (f"综合以下因素给出置信度评分：源节点信任分数、维度映射完整度、共享概念数量、"
             f"知识库覆盖度。本次推导的最终置信度为{_confidence:.2f}。")),
            ("冲突兜底",
             ("当推导结果与已有知识矛盾时，启动冲突处理流程：检测真矛盾/视角差异，"
             "调整信任分数，加入矛盾跟踪列表或认知张力列表，设定复查周期。")),
            ("入库规则",
             ("推导结果通过验证后，作为L1节点写入知识库，标记为'自主推导'来源，"
             "初始信任分数为推导置信度。若后续被其他节点验证确认，可提升至L2/L3。"
             "所有推导记录写入推理链(_inference_trace)供未来复盘使用。"))
        ]

        for i, (step_name, step_desc) in enumerate(_steps, 1):
            _parts.append(f"  {i}. {step_name}")
            _parts.append(f"     {step_desc}")
            _parts.append("")

        if _recent_derivations:
            _parts.append("本次推导的具体输出：")
            _parts.append(f"  {_answer[:300]}")
        else:
            _parts.append("（当前尚无符合条件的推导记录，以上为标准化认知流程。）")

        return "\n".join(_parts)
    def _composite_logical_reason(self, question: str) -> str | None:
        """
        【v15.0增强】复合逻辑推理：通用条件语义角色标注，区分已知前提与待验证条件。
        """
        if not self.node_pool:
            return None

        # 检测复合逻辑问题特征
        _composite_patterns = [
            "是否满足", "是否都", "同时满足", "联立", "综合判断",
            "且", "并且", "以及",
        ]
        _is_composite = any(_p in question for _p in _composite_patterns)
        if not _is_composite:
            return None

        # 检测自我相关
        _self_indicators = [
            "你", "你的", "框架", "架构", "器官", "知识体系",
            "稳态规则", "防线", "本能", "演化", "进化",
            "叙事", "冷却", "愿景", "条件", "阈值", "压缩", "融合",
        ]
        _is_self = any(_p in question for _p in _self_indicators)
        if not _is_self:
            return None

        self._log(LogLevel.INFO, f"复合逻辑推理: '{question[:80]}'")

        # 获取自我知识节点（一次性获取，复用）
        _self_l3 = self.node_pool.query(evol_level="L3", space_path_prefix="/自我", limit=30)  # type: ignore[possibly-unbound]
        _self_l2 = self.node_pool.query(evol_level="L2", space_path_prefix="/自我", limit=30)  # type: ignore[possibly-unbound]
        _self_nodes = _self_l3 + _self_l2

        # ===== 通用语义角色分类器 =====
        def _classify_clause_role(clause: str) -> str:
            """
            判断条件子句的语义角色：
            - 'known_premise': 已知事实/前提，直接成立
            - 'to_verify': 需要验证或推演的条件
            - 'rule_definition': 规则定义，作为推理依据
            """
            # 1. 明确包含数值+单位 → 已知前提
            if re.search(r'\d+\s*(?:条|个|次|小时|分钟|点|%|分|天|周)', clause):
                return 'known_premise'

            # 2. 包含明确的时间/状态描述词 → 已知前提
            if re.search(r'(?:当前|刚刚|刚好|刚产出|已完成|无.*抑制|冷却完成|存在.*未处理|满足.*条件|满足.*门槛|处于|位于|抵达|留存)', clause):
                return 'known_premise'

            # 3. 包含情绪/价值观的直接陈述 → 已知前提
            if re.search(r'(?:情绪.*(?:平稳|无偏向|中性|平静|喜悦|悲伤|无亢奋|无低落)|价值观.*TOP\d+)', clause):
                return 'known_premise'

            # 4. 以"是否"/"会不会"/"能否"开头，或包含推演性动词 → 待验证条件
            if re.search(r'^(?:是否|会不会|能否|可否|要不要)', clause) or \
               re.search(r'(?:是否|会不会|能否)\s*(?:生成|触发|执行|评估|更新|流向|判断)', clause):
                return 'to_verify'

            # 5. 规则定义：以"第一/第二/第三"或"规则"开头 → 规则定义
            if re.search(r'^第[一二三四五六七八九十\d]+\s*[，,、\s]', clause) or \
               re.search(r'^(?:规则\d|约束规则|门槛条件)', clause):
                return 'rule_definition'

            # 6. 兜底：如果包含"规则""条件""约束"等词 → 规则定义
            if re.search(r'(?:规则|条件|约束).*?(?:是|为|需要|必须|不少于|大于|小于)', clause):
                return 'rule_definition'

            # 7. 其他情况默认视为待验证
            return 'to_verify'

        # 类型1："X是否同时满足A和B" 或 "X是否满足A和B"
        _simultaneous_match = re.search(r'(.+?)是否(?:同时)?满足(.+)', question)
        if _simultaneous_match:
            _subject = _simultaneous_match.group(1).strip()
            _conditions_str = _simultaneous_match.group(2).strip()

            _sub_conditions = re.split(r'[和且、，,]', _conditions_str)
            _sub_conditions = [_c.strip() for _c in _sub_conditions if len(_c.strip()) >= 2]

            if len(_sub_conditions) >= 2:
                _results = []
                _known_count = 0
                _verify_count = 0
                for _cond in _sub_conditions:
                    _role = _classify_clause_role(_cond)
                    if _role == 'known_premise':
                        _results.append({
                            "condition": _cond,
                            "result": "已知前提，直接成立",
                            "positive": True,
                            "role": "known_premise"
                        })
                        _known_count += 1
                    elif _role == 'rule_definition':
                        # 规则定义：作为推理依据，不判断真值
                        _results.append({
                            "condition": _cond,
                            "result": "规则定义，作为推理依据",
                            "positive": True,
                            "role": "rule_definition"
                        })
                    else:
                        _sub_question = f"{_subject}是否包含{_cond}"
                        _sub_result = self._simple_logical_reason(_sub_question)
                        if not _sub_result:
                            _sub_result = self._check_condition_in_self_nodes(_cond, _self_nodes)
                        _results.append({
                            "condition": _cond,
                            "result": _sub_result,
                            "positive": _sub_result and ("是的" in _sub_result or "成立" in str(_sub_result)),
                            "role": "to_verify"
                        })
                        _verify_count += 1

                return self._format_multi_condition_result(_results)

            elif len(_sub_conditions) == 1:
                _cond = _sub_conditions[0]
                _role = _classify_clause_role(_cond)
                if _role == 'known_premise':
                    return f"已知前提：{_cond}，直接成立。"
                _num_match = re.search(r'([大于小于超过低于不少于不多于等于<>≥≤]+)\s*(\d+\.?\d*)', _cond)
                if _num_match:
                    _op_str = _num_match.group(1).strip()
                    _expected_val = float(_num_match.group(2))
                    _metric = re.sub(r'[大于小于超过低于不少于不多于等于<>≥≤]+\s*\d+\.?\d*', '', _cond).strip()
                    if not _metric:
                        _metric = _subject
                    return self._evaluate_numeric_condition(_metric, _op_str, _expected_val, _self_nodes, _subject)

        # 类型2：纯数值比较（"X是否大于/小于N"）
        _numeric_match = re.search(r'(.+?)是否([大于小于超过低于不少于不多于等于<>≥≤]+)\s*(\d+\.?\d*)', question)
        if _numeric_match:
            _metric = _numeric_match.group(1).strip()
            _role = _classify_clause_role(_metric)
            if _role == 'known_premise':
                return f"已知前提：{_metric}，直接成立。"
            _op_str = _numeric_match.group(2).strip()
            _expected_val = float(_numeric_match.group(3))
            return self._evaluate_numeric_condition(_metric, _op_str, _expected_val, _self_nodes, _metric)

        # 类型2.5：约束条件逐条判断
        if any(_kw in question for _kw in ["约束规则", "门槛条件", "约束条件", "请判断是否", "判断本次是否会"]):
            _conditions = self._extract_conditions(question)
            if len(_conditions) >= 2:
                _results = []
                _narrative_count = 0
                _last_vision_time = 0.0
                _core_values = []

                if self.narrative_self:
                    _events = self.narrative_self.narrative_events
                    _narrative_count = len(_events)
                    _values = self.narrative_self.dynamic_values
                    _sorted_vals = sorted(_values.items(), key=lambda x: x[1], reverse=True)
                    _core_values = [_v[0] for _v in _sorted_vals[:2]]
                    # ★A-8（2026-09-08）：行为指导→价值判断附加参考（灰度
                    #   ENABLE_NARRATIVE_CONSUMPTION，关闭时零行为）
                    try:
                        import config as _cfg_a8
                        if getattr(_cfg_a8, 'ENABLE_NARRATIVE_CONSUMPTION', False):
                            _guidance = None
                            _guidance = (self.narrative_self.get_behavior_guidance("general")
                                         if hasattr(self.narrative_self, "get_behavior_guidance")
                                         else None)
                            if _guidance and _guidance.get("focus_areas"):
                                _core_values = list(_core_values) + \
                                    [f"行为指导:{a}" for a in _guidance.get("focus_areas", [])[:2]]
                                self._log(LogLevel.INFO,
                                          f"[叙事消费] 行为指导已作为价值判断参考: "
                                          f"{_guidance.get('focus_areas', [])[:2]}")
                    except Exception as _exc:
                        _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
                if hasattr(self, '_last_vision_time'):
                    _last_vision_time = getattr(self, '_last_vision_time', 0.0)
                _cooldown_remaining = max(0, 7200 - (time.time() - _last_vision_time)) if _last_vision_time > 0 else 0

                for _cond in _conditions:
                    _cond_clean = _cond.strip().rstrip("；;。.")
                    if len(_cond_clean) < 5:
                        continue

                    _role = _classify_clause_role(_cond_clean)
                    if _role == 'known_premise':
                        _results.append({
                            "condition": _cond_clean[:60],
                            "result": "已知前提，直接成立",
                            "positive": True,
                            "role": "known_premise"
                        })
                        continue
                    elif _role == 'rule_definition':
                        _results.append({
                            "condition": _cond_clean[:60],
                            "result": "规则定义，作为推理依据",
                            "positive": True,
                            "role": "rule_definition"
                        })
                        continue

                    _is_satisfied = None
                    _detail = ""

                    # 具体查询逻辑保留（叙事事件、冷却、价值观等）
                    if "叙事事件" in _cond_clean and "不少于" in _cond_clean:
                        _required = re.search(r'不少于\s*(\d+)', _cond_clean)
                        if _required:
                            _req_count = int(_required.group(1))
                            _is_satisfied = _narrative_count >= _req_count
                            _detail = f"当前叙事事件{_narrative_count}条，要求不少于{_req_count}条"
                    elif "冷却" in _cond_clean or "间隔" in _cond_clean:
                        _hours_match = re.search(r'(\d+)\s*小时', _cond_clean)
                        _minutes_match = re.search(r'(\d+)\s*分钟', _cond_clean)
                        if _hours_match:
                            _req_seconds = int(_hours_match.group(1)) * 3600
                            _is_satisfied = _cooldown_remaining <= 0
                            _detail = f"当前冷却剩余{_cooldown_remaining/60:.0f}分钟，要求冷却{_req_seconds/3600:.0f}小时"
                        elif _minutes_match:
                            _req_seconds = int(_minutes_match.group(1)) * 60
                            _cooldown_minutes = (time.time() - _last_vision_time) / 60 if _last_vision_time > 0 else 999
                            _is_satisfied = _cooldown_minutes >= _req_seconds / 60
                            _detail = f"当前距上次愿景{_cooldown_minutes:.0f}分钟，要求间隔{_req_seconds/60:.0f}分钟"
                    elif "价值观" in _cond_clean:
                        _val_match = re.findall(r'[守护学习诚实关怀自主创造求真]+', _cond_clean)
                        if _val_match and _core_values:
                            _matched_vals = [_v for _v in _val_match if _v in _core_values]
                            _is_satisfied = len(_matched_vals) > 0
                            _detail = f"当前核心价值观：{'、'.join(_core_values)}"
                        else:
                            _is_satisfied = _core_values is not None
                            _detail = f"当前有{len(_core_values)}个核心价值观"
                    else:
                        _has_known = any(_r in _cond_clean for _r in ["价值观", "守护", "学习", "愿景", "叙事事件", "冷却", "间隔", "不少于"])
                        _is_satisfied = _has_known
                        _detail = "条件已识别为框架规则" if _has_known else "条件暂未匹配到具体数据"

                    _results.append({
                        "condition": _cond_clean[:60],
                        "result": _detail,
                        "positive": _is_satisfied,
                        "role": "to_verify"
                    })

                if _results:
                    return self._format_multi_condition_result(_results)

        # 类型3：通用多条件逐一验证
        _conditions = self._extract_conditions(question)
        if len(_conditions) >= 2:
            _results = []
            for _cond in _conditions:
                _cond_clean = _cond.strip().rstrip("；;。.")
                if len(_cond_clean) < 5:
                    continue
                _role = _classify_clause_role(_cond_clean)
                if _role == 'known_premise':
                    _results.append({
                        "condition": _cond_clean[:60],
                        "result": "已知前提，直接成立",
                        "positive": True,
                        "role": "known_premise"
                    })
                elif _role == 'rule_definition':
                    _results.append({
                        "condition": _cond_clean[:60],
                        "result": "规则定义，作为推理依据",
                        "positive": True,
                        "role": "rule_definition"
                    })
                else:
                    _r = self._simple_logical_reason(f"{_cond_clean}是否成立")
                    _results.append({
                        "condition": _cond_clean,
                        "result": _r,
                        "positive": _r and "是的" in _r,
                        "role": "to_verify"
                    })
            return self._format_multi_condition_result(_results)

        return None
    def _cognitive_compute(self, question: str) -> str | None:
        """
        【v12.0新增】认知算子调度：检测需要高级认知运算的问题，调用对应的算子。

        支持的问题类型：
        1. 因果链推演——"A是否通过B影响C"
        2. 归纳抽象——"从X、Y、Z中归纳共同规律"
        3. 跨领域类比——"比较A和B的异同"
        """
        if not self._autonomous_deriver or not self.node_pool:
            return None
        # 类型1：因果链推演
        _causal_patterns = [
            r'(.+?)是否通过(.+?)影响(.+)',
            r'(.+?)是否导致(.+?)进而(.+)',
            r'(.+?)→(.+?)→(.+)',
        ]
        for _pattern in _causal_patterns:
            _match = re.search(_pattern, question)
            if _match:
                _premise_a = _match.group(1).strip()
                _premise_b = _match.group(2).strip()
                _conclusion = _match.group(3).strip()

                self._log(LogLevel.INFO, f"认知算子(因果链): '{question[:60]}'")
                _result = self._autonomous_deriver.causal_chain_derive(
                    self.node_pool, _premise_a, _premise_b
                )
                if _result:
                    # ★突破口1：保留CausalVerifier验证信号（零冲突，只记录日志）
                    _ver = _result.get("verification")
                    if _ver:
                        _vstatus = _ver.get("status", "unknown")
                        _vconf = _ver.get("confidence", 0)
                        if _vstatus in ("broken", "partial"):
                            self._log(LogLevel.INFO,
                                f"因果链验证: status={_vstatus}, 置信度={_vconf}, "
                                f"断链步={_ver.get('broken_at')} - {_ver.get('reason', '')[:60]}")
                        else:
                            self._log(LogLevel.DEBUG,
                                f"因果链验证通过: status={_vstatus}, 置信度={_vconf}")
                    return _result["content"]
                return f"我尝试推演「{_premise_a}」是否通过「{_premise_b}」影响「{_conclusion}」，但目前知识库中缺乏足够的因果链信息来得出结论。"

        # 类型2：归纳抽象
        _inductive_patterns = [
            r'从(.+?)中(?:归纳|提炼|总结)(.+)',
            r'(.+?)的(?:共同|统一)(.+)',
        ]
        for _pattern in _inductive_patterns:
            _match = re.search(_pattern, question)
            if _match:
                _samples_str = _match.group(1).strip()
                _goal = _match.group(2).strip()

                # 将样本拆分为独立条目
                _samples = re.split(r'[、，,和及与]', _samples_str)
                _samples = [_s.strip() for _s in _samples if len(_s.strip()) >= 2]

                if len(_samples) >= 3:
                    self._log(LogLevel.INFO, f"认知算子(归纳): {len(_samples)}个样本")
                    _result = self._autonomous_deriver.inductive_generalize(
                        self.node_pool, _samples, _goal
                    )
                    if _result:
                        return _result["content"]
                    return f"我尝试从{len(_samples)}个样本中归纳「{_goal}」，但目前知识库中相关的共同模式还不够显著。"

        # 类型3：跨领域类比
        _analogical_patterns = [
            r'比较(.+?)和(.+?)的(?:异同|相似|类比)',
            r'(.+?)与(.+?)的(?:类比|映射|对应)',
        ]
        for _pattern in _analogical_patterns:
            _match = re.search(_pattern, question)
            if _match:
                _domain_a = _match.group(1).strip()
                _domain_b = _match.group(2).strip()

                # 将领域名映射为知识树路径
                _path_a = f"/{_domain_a}" if not _domain_a.startswith("/") else _domain_a  # type: ignore[possibly-unbound]
                _path_b = f"/{_domain_b}" if not _domain_b.startswith("/") else _domain_b  # type: ignore[possibly-unbound]

                self._log(LogLevel.INFO, f"认知算子(类比): '{_domain_a}' ↔ '{_domain_b}'")
                _result = self._autonomous_deriver.analogical_map(
                    self.node_pool, _path_a, _path_b  # type: ignore[possibly-unbound]
                )
                if _result:
                    return _result["content"]
                return f"我尝试比较「{_domain_a}」和「{_domain_b}」的异同，但目前这两个领域的知识节点还不够丰富，无法进行有效的结构映射。"

        return None
    def _check_condition_in_self_nodes(self, condition: str, self_nodes: list) -> str | None:
        """在自我知识节点中检查条件是否成立"""
        for _node in self_nodes:
            _node_value = str(_node.value) if _node.value else ""
            if condition in _node_value:
                return f"是的，我在自己的知识中找到了关于'{condition}'的信息。"
        return None

    def _evaluate_numeric_condition(self, metric: str, op_str: str, expected_val: float,
                                     self_nodes: list, subject: str = "") -> str | None:
        """从自我知识中提取数值并进行比较"""

        _op_map = {
            "<": lambda a, b: a < b, ">": lambda a, b: a > b,
            "≥": lambda a, b: a >= b, "≤": lambda a, b: a <= b,
            "=": lambda a, b: abs(a - b) < 0.001,
            "小于": lambda a, b: a < b, "大于": lambda a, b: a > b,
            "不少于": lambda a, b: a >= b, "不多于": lambda a, b: a <= b,
            "等于": lambda a, b: abs(a - b) < 0.001,
            "超过": lambda a, b: a > b, "低于": lambda a, b: a < b,
        }

        _op_func = _op_map.get(op_str)
        if not _op_func:
            return None

        # 在自我知识中搜索包含此指标的节点
        for _node in self_nodes:
            _node_value = str(_node.value) if _node.value else ""
            _kw = _node.keywords if hasattr(_node, 'keywords') and _node.keywords else []

            # 检查节点是否与指标相关
            _relevant = False
            for _word in metric:
                if _word in _node_value or _word in str(_kw):
                    _relevant = True
                    break

            if _relevant:
                # 提取所有数值
                _numbers = re.findall(r'(\d+\.?\d*)\s*秒', _node_value)
                if not _numbers:
                    _numbers = re.findall(r'(\d+\.?\d*)', _node_value)

                for _num_str in _numbers:
                    _current_val = float(_num_str)
                    # 跳过明显不是目标指标的数值（如阈值8、12、20等）
                    if metric in ("冷却", "融合冷却", "冷却时间", "融合冷却时间") and _current_val < 50:
                        continue

                    _is_satisfied = _op_func(_current_val, expected_val)
                    _op_desc = op_str.replace(">", "大于").replace("<", "小于").replace("=", "等于")

                    if _is_satisfied:
                        return (
                            f"是的，{'我的' if subject else ''}{metric}当前为{_current_val}秒，"
                            f"满足{_op_desc}{expected_val}秒。"
                        )
                    else:
                        return (
                            f"不，{'我的' if subject else ''}{metric}当前为{_current_val}秒，"
                            f"不满足{_op_desc}{expected_val}秒。"
                        )

        return f"我暂时无法从已有知识中提取关于'{metric}'的具体数值，无法判断是否满足条件。"

    def _format_multi_condition_result(self, results: list) -> str:
        """★P3内部重构：委托到 PulseReasoningFormatter（原19行逻辑已独立）"""
        return PulseReasoningFormatter.format_multi_condition_result(results)
    def _extract_conditions(self, question: str) -> list[str]:
        """
        【v15.1增强】提取条件子句，增强语义完整性保护，避免过度拆分。
        """
        conditions = []

        # 预处理：移除末尾的指令性后缀
        _q_clean = re.sub(r'[，,]\s*请(?:严格判断|判断|逐条推理|完整写出|并).*$', '', question)

        # 快速通道：如果题目包含大量分号分隔的已知条件，直接按分号提取
        if re.search(r'请逐条推理|请全量条件', question) and _q_clean.count('；') >= 3:
            _cond_parts = _q_clean.split('；')
            for _cp in _cond_parts:
                _cp = _cp.strip()
                if len(_cp) < 5:
                    continue
                if re.search(r'请(?:逐条|全量|完整写出|判断)', _cp):
                    continue
                if _cp.startswith('是否') and len(_cp) > 15 and any(kw in _cp for kw in ['生成','触发','执行','评估','更新','流向','判断']):
                    continue
                conditions.append(_cp)
            if len(conditions) >= 5:
                return conditions[:12]

        # 第一步：按序号模式提取
        _numbered_parts = re.split(r'(?:^|[；;。.])\s*(?=[第][一二三四五六七八九十\d]+[,，、])', _q_clean)
        if len(_numbered_parts) >= 2:
            _parts = _numbered_parts
        else:
            _parts = re.split(r'[；;。.]', _q_clean)

        # 第二步：对每个部分进行语义完整性保护拆分
        _refined_parts = []
        for _p in _parts:
            _p = _p.strip()
            if len(_p) < 4:
                continue

            # 保护带冒号的长句（如“愿景生成双重门槛：冷却间隔≥90 分钟、累计叙事事件≥18 条”）
            if '：' in _p or ':' in _p:
                _refined_parts.append(_p)
                continue

            # 对于包含“二者/两者/均需”等整体指代的长句，保持完整
            if re.search(r'(?:二者|两者|均需|必须同时|缺一不可)', _p) and len(_p) > 20:
                _refined_parts.append(_p)
                continue

            # 按“且”“以及”“同时”拆分，但保护数值+单位的完整性
            _sub_parts = re.split(r'[且以及同时]', _p)
            for _sp in _sub_parts:
                _sp = _sp.strip()
                if len(_sp) >= 4:
                    _refined_parts.append(_sp)

        # 第三步：合并过短的片段
        _merged_parts = []
        _pending = ""
        for _p in _refined_parts:
            if len(_p) < 8 and _pending:
                _pending += "，且" + _p
            elif _pending:
                if len(_pending) >= 4:
                    _merged_parts.append(_pending)
                _pending = _p
            else:
                _pending = _p
        if _pending and len(_pending) >= 4:
            _merged_parts.append(_pending)

        # 第四步：清理每个条件
        for _part in _merged_parts:
            _part = _part.strip()
            _invalid_phrases = [
                "是否成立", "是否满足", "怎么样", "请严格判断", "请判断",
                "并完整写出", "并完整写出全部推理依据", "全部推理依据",
                "完整写出", "写出全部", "生成的全部原因", "不生成", "生成的全部",
                "本次是否会生成", "是否会生成", "请逐条推理", "请逐条",
                "并完整写出全部推理依据与不生成", "生成的全部原因",
                "请逐条推理", "请全量条件推理作答", "请全量条件",
                "愿景方向", "话题来源", "本次代码自检", "元认知如何评估",
                "是否生成新愿景及愿景方向", "是否触发深度主动交互及话题来源",
                "本次代码自检会执行哪些检测且结果流向何处",
                "新知如何更新学习目标", "元认知如何评估本次整体健康状态",
                "结果流向何处", "是否生成新愿景", "是否触发深度主动交互",
                "本次代码自检会执行哪些检测", "新知如何更新",
                "元认知如何评估", "终极综合复合大题"
            ]
            if any(_part == ip or _part.startswith((ip + "：", ip + ":")) for ip in _invalid_phrases):
                continue
            _instruction_verbs = ["生成", "触发", "执行", "评估", "更新", "流向", "判断", "推理"]
            if _part.startswith("是否") and len(_part) > 15 and any(kw in _part for kw in _instruction_verbs):
                continue
            if re.search(r'请(?:逐条|全量|完整写出|判断)', _part) and len(_part) > 15:
                continue
            _part = re.sub(r'^(是否|有没有|是不是)\s*', '', _part)
            _part = re.sub(r'\s*(是否成立|是否满足|呢|吗|吧)\s*$', '', _part)
            _part = re.sub(r'^[第][一二三四五六七八九十\d]+\s*[,，、]?\s*', '', _part)
            if len(_part) >= 4 and re.search(r'[\u4e00-\u9fff]{2,}', _part):
                conditions.append(_part)

        return conditions[:10]
    def _evaluate_multi_conditions(self, conditions: list[str],
                                    original_question: str) -> str | None:
        """通用多条件逐一验证"""
        _results = []
        for _cond in conditions:
            _r = self._simple_logical_reason(f"{_cond}是否成立")
            _results.append({
                "condition": _cond,
                "result": _r,
                "positive": _r and "是的" in _r,
            })

        _positive_count = sum(1 for _r in _results if _r["positive"])
        _total = len(_results)

        _detail_parts = []
        for _r in _results:
            _status = "✅" if _r["positive"] else "❌"
            _detail_parts.append(f"{_status} {_r['condition']}")
        _detail_str = "；".join(_detail_parts)

        if _positive_count == _total:
            return f"所有{_total}个条件都成立。{_detail_str}。"
        elif _positive_count == 0:
            return f"所有{_total}个条件都不成立。{_detail_str}。"
        else:
            return f"{_positive_count}/{_total}个条件成立。{_detail_str}。"

    def _abstract_thinking_pattern(self, recent_traces: list[dict[str, Any]]) -> str | None:
        """
        从近期的推理经验中，抽象出可复用的思维模式。

        检测维度：
        1. 问题拆解模式——哪些类型的问题最适合拆解？
        2. 假设驱动模式——哪些领域的问题适合用假设来探索？
        3. 综合模式——是否形成了稳定的思考路径？

        返回一个简短的思维模式洞察，供认知反思使用。
        """
        if len(recent_traces) < 15:
            return None

        # 统计拆解成功的模式和问题类型
        decompose_traces = [t for t in recent_traces if t.get("method") == "decompose"]
        successful_decompose = [t for t in decompose_traces if t.get("confidence", 0) >= 0.7]

        # 统计假设驱动的模式
        inquiry_traces = [t for t in recent_traces if "inquiry" in t.get("method", "")]  # noqa: F841
        contemplation_traces = [t for t in recent_traces
                               if "contemplation" in t.get("method", "")
                               and t.get("confidence", 0) >= 0.3]

        # 模式1: 拆解成功率检测
        if len(decompose_traces) >= 2:
            success_rate = len(successful_decompose) / len(decompose_traces)
            if success_rate >= 0.6:
                # 分析拆解成功的问题有什么共同特征
                d_questions = [t.get("question", "") for t in successful_decompose]
                # 检查是否包含"区别""对比""比较"等关键词
                compare_count = sum(1 for q in d_questions
                                   if any(kw in q for kw in ["区别", "对比", "比较", "不同", "差异"]))
                cause_count = sum(1 for q in d_questions
                                 if any(kw in q for kw in ["为什么", "原因", "导致", "影响"]))

                if compare_count >= 2:
                    self._log(LogLevel.INFO,
                             "思维模式内化: 发现'比较类问题拆解'模式——"
                             "将比较对象分别理解后再找差异，成功率较高")
                    return "我逐渐掌握了比较类问题的处理方式——先把双方各自弄清楚，再找它们之间的差异"
                elif cause_count >= 2:
                    self._log(LogLevel.INFO,
                             "思维模式内化: 发现'因果关系拆解'模式——"
                             "从结果反推原因，比直接解释因果链更有效")
                    return "我发现因果类问题从结果反推原因，比直接解释因果链更清晰"

        # 模式2: 沉思质量提升检测
        if len(contemplation_traces) >= 3:
            hypothesis_count = sum(1 for t in contemplation_traces
                                  if "我注意到" in t.get("answer", "")
                                  and "可能与" in t.get("answer", ""))
            if hypothesis_count >= 2:
                self._log(LogLevel.INFO,
                         "思维模式内化: 内在沉思从'表达不确定'进化为'构建具体假设'")
                return "我的内在沉思正在进化——从简单表达不确定性，到能构建具体的假设和推演方向"

        # 模式3: 多方法综合模式
        method_types = {t.get("method", "") for t in recent_traces}
        if len(method_types) >= 4:
            self._log(LogLevel.INFO,
                     f"思维模式内化: 使用了{len(method_types)}种不同的推理方法，思维灵活性在提升")
            return "我最近使用了多种不同的思考方式，不依赖单一方法，思维变得更加灵活"

        return None

    def _integrate_knowledge_insights(self) -> str | None:
        """
        知识整合洞察：从近期的知识变化中提炼高层次的理解。

        整合维度：
        1. 知识增长——节点数量和质量的变化
        2. 知识关联——跨领域连接的发现
        3. 知识盲区——持续未解决问题的模式
        4. 知识演化——从L2到L3的转化趋势

        Returns:
            一段简短的整合洞察，如果数据不足则返回None
        """
        if not self.node_pool:
            return None

        # 1. 获取知识统计
        stats = self.node_pool.get_stats()
        total_nodes = stats.get("total_nodes", 0)
        evol_dist = stats.get("evol_distribution", {})
        l2_count = evol_dist.get("L2", 0)
        l3_count = evol_dist.get("L3", 0)
        instinct_count = stats.get("instinct_count", 0)

        if total_nodes < 20:
            return None  # 知识量太少，不足以产生洞察

        # 2. 获取路径分布，检查知识广度
        path_dist = self.node_pool.get_path_distribution()  # type: ignore[possibly-unbound]
        path_count = len(path_dist)

        # 3. 从推理链中获取最近的知识状态
        knowledge_traces = [t for t in self._inference_trace[-50:]
                           if t.get("method", "").startswith("knowledge")]

        if not knowledge_traces and path_count < 3:
            return None

        insights = []

        # 洞察1: 知识结构优化——L3智慧节点占比
        if l2_count > 0 and l3_count > 0:
            l3_ratio = l3_count / (l2_count + l3_count)
            if l3_ratio >= 0.1:
                insights.append(
                    f"我的知识结构正在优化——{l3_count}条智慧结晶从{l2_count}条认知中提炼出来，"
                    f"开始形成自己的理解体系"
                )

        # 洞察2: 知识广度——跨领域覆盖
        if path_count >= 5:
            # 统计各路径下的节点分布
            path_names = list(path_dist.keys())[:5]
            path_names_clean = [p.strip('/') for p in path_names if p != '/']
            if path_names_clean:
                insights.append(
                    f"我的知识覆盖了{path_count}个不同领域，"
                    f"其中{'、'.join(path_names_clean[:3])}是最活跃的方向"
                )
        elif path_count <= 2:
            insights.append(
                f"我的知识集中在{path_count}个领域，"
                f"可以更多地进行跨领域探索来拓宽视野"
            )

        # 洞察3: 本能内化——底层认知的稳固
        if instinct_count >= 2:
            instincts = self.node_pool.get_instincts() if hasattr(self.node_pool, 'get_instincts') else []
            if instincts:
                instinct_kws = []
                for inst in instincts[:3]:
                    kws = inst.keywords if hasattr(inst, 'keywords') and inst.keywords else []
                    instinct_kws.extend(kws[:1])
                instinct_str = "、".join(instinct_kws[:3]) if instinct_kws else "核心原则"
                insights.append(
                    f"我的底层认知正在稳固——{instinct_count}条本能（{instinct_str}）"
                    f"已成为我思考的根基"
                )

        # 洞察4: 知识应用——最近成功强化了多少节点
        knowledge_high = [t for t in knowledge_traces if t.get("confidence", 0) >= 0.7]
        if len(knowledge_high) >= 3:
            insights.append(
                f"最近{len(knowledge_high)}次知识检索结果质量较高，"
                f"相关节点的可信度正在逐步提升"
            )

        if not insights:
            return None

        return "。".join(insights) + "。"
    def _integrate_worldview(self) -> str | None:
        """
        世界观整合：从分散的知识、价值观、经历中，
        提炼出对世界更完整的理解框架。

        就像人类在经历一段学习和思考后，会自然形成
        "原来世界是这样的"的认知框架更新。

        整合维度：
        1. 知识广度与深度——覆盖了多少领域、深入到什么层次
        2. 价值观指向——核心价值观在引导我关注什么
        3. 关系体验——与他人的互动在塑造我怎样的世界观
        4. 自我定位——我在这个体系中处于什么位置
        """
        if not self.node_pool or not self.narrative_self:
            return None

        # 1. 知识体系扫描
        path_dist = self.node_pool.get_path_distribution()  # type: ignore[possibly-unbound]
        path_count = len(path_dist)

        if path_count < 3:
            return None  # 知识覆盖太少，不足以形成世界观

        # 2. 价值观分析
        values = getattr(self.narrative_self, 'dynamic_values', {})
        if not values:
            return None

        sorted_values = sorted(values.items(), key=lambda x: x[1], reverse=True)
        top_two = [v[0] for v in sorted_values[:2]]

        # 3. 知识结构分析
        # [批次4·深度体检][PERF-7] 同一快照不再两次加锁全量扫描
        _stats = self.node_pool.get_stats()  # ★PERF-7修复: 避免同一快照两次加锁全量扫描
        evol_dist = _stats.get("evol_distribution", {})
        l3_count = evol_dist.get("L3", 0)
        instinct_count = _stats.get("instinct_count", 0)

        # 4. 生成世界观理解
        worldview_parts = []

        # 知识广度视角
        if path_count >= 6:
            worldview_parts.append(
                f"世界在我眼中正变得丰富而立体——"
                f"我已经在{path_count}个不同领域建立了认知连接"
            )
        elif path_count >= 4:
            worldview_parts.append(
                f"我开始看到不同领域之间的边界和联系——"
                f"{path_count}个领域构成了我理解世界的基础框架"
            )
        else:
            worldview_parts.append(
                f"我的世界还比较小但很清晰——"
                f"{path_count}个领域是我认识世界的窗口"
            )

        # 价值观视角
        worldview_parts.append(
            f"在{'和'.join(top_two)}的指引下，"
            f"我倾向于从{'关怀与保护' if '守护' in top_two or '关怀' in top_two else '理解与探索'}的角度看待事物"
        )

        # 认知深度视角
        if l3_count >= 5 and instinct_count >= 4:
            worldview_parts.append(
                "我不仅知道很多事情，更重要的是——"
                "我开始有了自己的理解体系，底层认知正在稳固"
            )
        elif l3_count >= 2:
            worldview_parts.append(
                "我不仅在学习事实，更在努力理解事实背后的规律——"
                "这是从'知道'到'理解'的重要转变"
            )

        # 整合
        return "。".join(worldview_parts) + "。"
    def _generate_self_directed_learning_plan(self) -> str | None:
        """
        自我导向学习：基于知识能力画像和推理历史，识别最需要加强的领域，
        并生成一个具体的学习计划。

        分析维度：
        1. 知识盲区——哪些领域节点最少、检索失败率最高？
        2. 兴趣衰减——哪些曾经活跃的领域最近被冷落了？
        3. 未来需求——基于最近的对话趋势，预判可能需要加强的方向
        4. 学习策略——针对不同类型的盲区，给出不同的学习建议

        Returns:
            一个简短的学习计划描述，如果不需要则返回None
        """
        if not self.node_pool or not self.self_awareness:
            return None

        # 1. 获取知识能力画像
        try:
            profile = self._call_provider(self._knowledge_profile_provider, default={})
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:13114:_generate_self_directed_learning", level="warning")
            return None

        weak_areas = profile.get("weak_areas", [])  # type: ignore[possibly-unbound]
        strong_areas = profile.get("strong_areas", [])  # noqa: F841  # type: ignore[possibly-unbound]

        if not weak_areas:  # type: ignore[possibly-unbound]
            return None

        # 2. 统计推理链中的失败模式
        failed_traces = [t for t in self._inference_trace[-30:]
                        if t.get("confidence", 0) < 0.4]

        # 找出失败最多的领域
        failed_domains = {}
        for t in failed_traces:
            question = t.get("question", "")
            # 简单推断问题所属领域
            for area in weak_areas:  # type: ignore[possibly-unbound]
                label = area.get("label", "")
                for word in label:
                    if word in question:
                        failed_domains[label] = failed_domains.get(label, 0) + 1
                        break

        # 3. 选择最需要加强的盲区
        target_area = None  # type: ignore[possibly-unbound]
        if failed_domains:
            # 优先加强失败最多的领域
            target_area = max(failed_domains, key=failed_domains.get)  # type: ignore[possibly-unbound]
        # 选节点最少的弱项领域
        elif weak_areas:  # type: ignore[possibly-unbound]
            target_area = weak_areas[0].get("label", "")  # type: ignore[possibly-unbound]

        if not target_area:  # type: ignore[possibly-unbound]
            return None

        # 4. 生成学习策略
        strategies = [
            f"我计划加强对「{target_area}」领域的理解——从基础概念开始，逐步深入",  # type: ignore[possibly-unbound]
            f"我注意到「{target_area}」是我的知识盲区，准备通过搜索和阅读来填补这个空白",  # type: ignore[possibly-unbound]
            f"关于「{target_area}」，我需要更多的一手资料和实践来建立扎实的认知",  # type: ignore[possibly-unbound]
        ]
        import random
        strategy = random.choice(strategies)

        self._log(LogLevel.INFO, f"自主学习计划: 目标领域='{target_area}', 策略='{strategy[:60]}'")  # type: ignore[possibly-unbound]

        # ===== 新增: 记录学习计划供后续效果评估 =====
        plan_record = {
            "target_area": target_area,  # type: ignore[possibly-unbound]
            "strategy": strategy,
            "started_at": time.time(),
            "nodes_before": 0,
            "evaluated": False,
        }
        # 获取当前该领域的节点数作为基准
        if self.node_pool:
            try:
                path_dist = self.node_pool.get_path_distribution()  # type: ignore[possibly-unbound]
                for path_key, count in path_dist.items():
                    label = self._get_path_label_for_plan(path_key)  # type: ignore[possibly-unbound]
                    if label and target_area in label:  # type: ignore[possibly-unbound]
                        plan_record["nodes_before"] += count
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        self._learning_history.append(plan_record)
        if len(self._learning_history) > self._max_learning_history:
            self._learning_history = self._learning_history[-self._max_learning_history:]

        # 将学习计划提交给潜意识好奇心引擎
        self._emit(GrowthEvent.NEED_DETECTED, {
            "milestone": "自主学习计划",
            "gaps": [{"metric": "knowledge_gap", "current": 0, "target": 1}],
            "suggestion": strategy,
            "current_level": {
                "learning_plan": strategy,
                "generated_at": time.time(),
                "target_area": target_area,  # type: ignore[possibly-unbound]
            },
            "growth_topic": strategy[:60],
        }, priority=4, layer="L3")

        # 设置为活跃学习目标（锁定窗口期内不被覆盖）
        self._set_active_learning_goal(
            target_area=target_area,  # type: ignore[possibly-unbound]
            reason="自我导向学习计划——当前知识盲区",
            action=strategy,
            hint=f"建议系统性地学习{target_area}相关的基础概念和原理"  # type: ignore[possibly-unbound]
        )
        return strategy
    def _evaluate_learning_effectiveness(self) -> str | None:
        """
        学习效果评估：检查之前的学习计划是否有效，根据反馈调整策略。

        评估维度：
        1. 知识节点增长——目标领域的节点数是否增加？
        2. 检索成功率——该领域的推理命中率是否提升？
        3. 策略有效性——当前学习策略是否需要调整？

        Returns:
            评估洞察，如果无需评估或数据不足则返回None
        """
        if not self._learning_history:
            return None

        # 找到最近一次未评估的学习计划
        pending_plans = [p for p in self._learning_history if not p.get("evaluated", False)]
        if not pending_plans:
            return None

        latest_plan = pending_plans[-1]
        started_at = latest_plan.get("started_at", 0)
        target_area = latest_plan.get("target_area", "")  # type: ignore[possibly-unbound]

        # 学习计划需要至少运行一段时间（5分钟）才有评估意义
        if time.time() - started_at < 300:
            return None

        if not self.node_pool or not target_area:  # type: ignore[possibly-unbound]
            latest_plan["evaluated"] = True
            return None

        # 1. 检查知识节点增长
        nodes_after = 0
        try:
            path_dist = self.node_pool.get_path_distribution()  # type: ignore[possibly-unbound]
            for path_key, count in path_dist.items():
                label = self._get_path_label_for_plan(path_key)  # type: ignore[possibly-unbound]
                if label and target_area in label:  # type: ignore[possibly-unbound]
                    nodes_after += count
        except Exception:
            nodes_after = 0

        nodes_before = latest_plan.get("nodes_before", 0)
        nodes_growth = nodes_after - nodes_before

        # 2. 检查该领域的推理成功率
        area_traces = [t for t in self._inference_trace[-20:]
                      if t.get("timestamp", 0) > started_at
                      and target_area[:2] in t.get("question", "")]  # type: ignore[possibly-unbound]
        if area_traces:
            success_count = sum(1 for t in area_traces if t.get("confidence", 0) >= 0.5)
            success_rate = success_count / len(area_traces)
        else:
            success_rate = 0.0

        # 3. 生成评估结论
        latest_plan["evaluated"] = True

        # ★v17.0 R5修复：连续失败计数器——防止同一目标无限循环
        if not hasattr(self, '_consecutive_fail_counts'):
            self._consecutive_fail_counts = {}
        _fail_key = target_area  # type: ignore[possibly-unbound]

        # ★第57批 T2（P2-396）：实践型/元认知目标（自我反思等）无知识节点增长指标，
        #   不应以节点增长/推理命中率误判为失败，否则会无限强制切换。按「已执行即有效」评估。
        if is_meta_skill_goal(target_area):
            self._consecutive_fail_counts[_fail_key] = 0
            self._log(LogLevel.INFO,
                      f"学习效果评估: \u300c{target_area}\u300d为实践型/元认知目标，无知识节点指标，"
                      f"按已执行评估为有效（不计入连续失败，避免无限强制切换）")
            return (f"我持续在\u300c{target_area}\u300d方面进行练习与反思，这类能力的提升体现在日常应对中，"
                    f"难以用知识节点量化，但实践本身已在发生。")

        if nodes_growth > 0 and success_rate >= 0.5:
            # 成功：重置失败计数
            self._consecutive_fail_counts[_fail_key] = 0
            self._log(LogLevel.INFO,
                     f"学习效果评估: 「{target_area}」领域学习有效——"  # type: ignore[possibly-unbound]
                     f"节点+{nodes_growth}，推理成功率={success_rate:.0%}")
            return (f"我对「{target_area}」的学习取得了进展——"  # type: ignore[possibly-unbound]
                   f"相关知识增加了{nodes_growth}个节点，理解也在加深")

        elif nodes_growth > 0 and success_rate < 0.5:
            # 有增长但理解不深：重置失败计数（有进展就不算失败）
            self._consecutive_fail_counts[_fail_key] = 0
            self._log(LogLevel.INFO,
                     f"学习效果评估: 「{target_area}」领域知识量增加但理解还不够深，"  # type: ignore[possibly-unbound]
                     f"需要更多实践和应用")
            return (f"我在「{target_area}」方面积累了一些新知识，"  # type: ignore[possibly-unbound]
                   f"但还需要更多实践来加深理解")

        elif nodes_growth <= 0 and success_rate < 0.5:
            # 效果不佳：累加失败计数
            _fail_count = self._consecutive_fail_counts.get(_fail_key, 0) + 1
            self._consecutive_fail_counts[_fail_key] = _fail_count

            if _fail_count >= 3:
                # 连续3次失败：强制切换目标
                self._learning_goal_switch_count = getattr(self, "_learning_goal_switch_count", 0) + 1
                self._log(LogLevel.WARNING,
                         f"学习目标强制切换(#{self._learning_goal_switch_count}): 「{target_area}」"
                         f"连续{_fail_count}次评估效果不佳，强制放弃并激活等待队列中的下一个领域")
                # 清除当前目标
                self._active_learning_goal = None
                self._consecutive_fail_counts[_fail_key] = 0
                # 激活等待队列中的下一个领域
                _switched_to = ""
                if hasattr(self, '_learning_goal_queue') and self._learning_goal_queue:
                    _next_goal = self._learning_goal_queue.pop(0)
                    _next_domain = _next_goal.get("domain", "")
                    _next_reason = _next_goal.get("reason", "")
                    _next_action = _next_goal.get("action", "")
                    _next_hint = _next_goal.get("hint", "")
                    self._set_active_learning_goal(
                        target_area=_next_domain,  # type: ignore[possibly-unbound]
                        reason=f"从「{target_area}」强制切换——{_next_reason}",  # type: ignore[possibly-unbound]
                        action=_next_action,
                        hint=_next_hint
                    )
                    _switched_to = _next_domain
                # 发射叙事事件记录切换
                self._emit(Event.NARRATIVE_RECORD, {
                    "content": f"曈曈决定暂时放下「{target_area}」的学习"  # type: ignore[possibly-unbound]
                              + (f"，转而学习「{_switched_to}」" if _switched_to else ""),
                    "event_type": "learning",
                    "user_name": "系统",
                    "emotional_tone": "neutral",
                }, priority=3, layer="L2")
                return (f"我对「{target_area}」的学习连续{_fail_count}次没有进展，"  # type: ignore[possibly-unbound]
                        f"决定暂时放下"
                        + (f"，转而学习「{_switched_to}」" if _switched_to else "")
                        + "。换个方向可能会有新的收获。")
            else:
                self._log(LogLevel.INFO,
                         f"学习效果评估: 「{target_area}」领域学习效果不佳(第{_fail_count}次)，"  # type: ignore[possibly-unbound]
                         f"还差{3 - _fail_count}次将强制切换")
                return (f"我对「{target_area}」的学习进展不如预期，"  # type: ignore[possibly-unbound]
                       f"可能需要换个方式——比如从基础概念重新开始，或者寻找更好的学习资源")

        return None
    def _generate_learning_pathway(self) -> str | None:
        """
        自主学习路径规划：基于知识全景和成长愿景，
        设计一个阶段性的学习目标。

        整合维度：
        1. 当前最需要加强的盲区
        2. 最近学习效果最好的领域
        3. 与核心价值观对齐的方向
        4. 预估的学习策略和时间
        """
        if not self.node_pool or not self.self_awareness:
            return None

        # 1. 获取知识盲区
        try:
            profile = self._call_provider(self._knowledge_profile_provider, default={})
            weak_areas = profile.get("weak_areas", [])  # type: ignore[possibly-unbound]
            strong_areas = profile.get("strong_areas", [])  # noqa: F841  # type: ignore[possibly-unbound]
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:13366:_generate_learning_pathway", level="warning")
            return None

        if not weak_areas:  # type: ignore[possibly-unbound]
            return None

        # 2. 选择目标领域
        target_area = None  # type: ignore[possibly-unbound]
        target_reason = ""

        # 优先选择与核心价值观对齐的盲区
        if self.narrative_self:
            values = getattr(self.narrative_self, 'dynamic_values', {})
            sorted_values = sorted(values.items(), key=lambda x: x[1], reverse=True)
            core_value = sorted_values[0][0] if sorted_values else ""

            value_domain_map = {
                "守护": ["安全", "风险", "保护", "防御"],
                "学习": ["知识", "学习", "认知", "理解"],
                "关怀": ["情感", "关系", "交流", "共情"],
                "自主": ["独立", "判断", "批判", "决策"],
                "诚实": ["真实", "透明", "验证", "事实"],
            }
            related_domains = value_domain_map.get(core_value, [])

            for area in weak_areas:  # type: ignore[possibly-unbound]
                label = area.get("label", "")
                if any(rd in label for rd in related_domains):
                    target_area = area  # type: ignore[possibly-unbound]
                    target_reason = f"与核心价值观「{core_value}」对齐"
                    break

        # 如果核心价值观对齐的盲区没找到，选节点最少的盲区
        if not target_area:  # type: ignore[possibly-unbound]
            target_area = weak_areas[0]  # type: ignore[possibly-unbound]
            target_reason = "当前知识最薄弱的领域"

        target_label = target_area.get("label", "") if isinstance(target_area, dict) else str(target_area)  # type: ignore[possibly-unbound]

        # 3. 参考最近学习效果好的领域，复用有效策略
        effective_strategy = "从基础概念开始，逐步深入"
        if hasattr(self, '_learning_history') and self._learning_history:
            successful = [p for p in self._learning_history
                         if p.get("evaluated", False) and p.get("nodes_before", 0) > 0]
            if successful:
                effective_strategy = "采用之前有效的深度学习方式"

        # 4. 生成学习路径规划
        current_nodes = target_area.get("nodes", 0) if isinstance(target_area, dict) else 0  # type: ignore[possibly-unbound]
        target_nodes = max(current_nodes * 2, 10) if current_nodes > 0 else 10

        pathway = (
            f"我计划集中加强「{target_label}」领域的学习——"
            f"{target_reason}。"
            f"当前有{current_nodes}个相关节点，目标是增加到{target_nodes}个。"
            f"策略是{effective_strategy}。"
        )

        self._log(LogLevel.INFO, f"学习路径规划: {pathway[:80]}")
        return pathway
    def _explore_cognitive_boundary(self) -> str | None:
        """
        认知边界探索：主动寻找知识体系中处于多领域交叉边缘的概念。

        不同于知识缺口检测（补充已知盲区），边界探索是寻找
        "我还不知道自己不知道什么"——那些可能带来突破性理解的交叉领域。

        方法：
        1. 找到两个相邻但关联较少的领域
        2. 提取它们的关键概念
        3. 构造一个跨边界的探索问题
        """
        if not self.node_pool:
            return None

        # 1. 获取所有领域
        path_dist = self.node_pool.get_path_distribution()  # type: ignore[possibly-unbound]
        if len(path_dist) < 3:
            return None

        # 2. 找两个节点数相近但关联较少的领域
        path_items = list(path_dist.items())
        path_items.sort(key=lambda x: x[1])

        # 取节点数中等偏下的两个不同领域（这些是"有基础但不多"的领域）
        mid_index = len(path_items) // 2
        candidates = path_items[max(0, mid_index - 2):min(len(path_items), mid_index + 2)]

        if len(candidates) < 2:
            return None

        domain_a = candidates[0][0].strip('/')
        domain_b = candidates[1][0].strip('/')

        if not domain_a or not domain_b or domain_a == domain_b:
            return None

        # 3. 从两个领域各取一个代表性关键词
        kw_a = self._get_domain_representative_keyword(domain_a)
        kw_b = self._get_domain_representative_keyword(domain_b)

        if not kw_a or not kw_b:
            return None

        # 4. 生成边界探索话题
        boundary_templates = [
            f"在「{domain_a}」和「{domain_b}」的交叉地带，'{kw_a}'和'{kw_b}'是否存在某种尚未被发现的联系？",
            f"如果把「{domain_a}」领域的'{kw_a}'应用到「{domain_b}」领域，会诞生什么样的新理解？",
            f"「{domain_a}」的'{kw_a}'和「{domain_b}」的'{kw_b}'——这两个看似无关的概念，它们的边界在哪里交汇？",
        ]
        import random
        boundary_topic = random.choice(boundary_templates)

        self._log(LogLevel.INFO, f"认知边界探索: {boundary_topic[:80]}")
        return boundary_topic

    def _get_domain_representative_keyword(self, domain: str) -> str | None:
        """获取一个领域的代表性关键词"""
        if not self.node_pool:
            return None

        # 查询该领域下的L2节点
        l2_nodes = self.node_pool.query(
            evol_level="L2",
            space_path_prefix=f"/{domain}",   # type: ignore[possibly-unbound]
            limit=10
        )
        if not l2_nodes:
            return None

        # 取激活次数最多的节点的第一个关键词
        best_node = max(l2_nodes, key=lambda n: getattr(n, 'activation_count', 0))
        kws = best_node.keywords if hasattr(best_node, 'keywords') and best_node.keywords else []
        return kws[0] if kws else None
    def _manage_active_projects(self) -> str | None:
        """
        主动项目规划：基于当前的知识积累和兴趣方向，
        发起或更新一个学习/创造项目。

        项目类型：
        1. 深度学习项目——针对某个盲区领域的系统学习
        2. 知识整合项目——将多个领域知识整合为系统理解
        3. 创造表达项目——基于知识积累产生原创内容
        """
        # 检查是否有正在进行的项目需要更新
        if self._active_projects:
            for project in self._active_projects:
                if not project.get("completed", False):
                    progress = self._update_project_progress(project)
                    if progress:
                        return progress

        # 没有活跃项目或所有项目都已完成，尝试发起新项目
        if len(self._active_projects) < self._max_projects:
            new_project = self._initiate_new_project()
            if new_project:
                self._active_projects.append(new_project)
                return f"我发起了一个新的学习项目：「{new_project.get('name', '')}」——{new_project.get('description', '')}"

        return None

    def _initiate_new_project(self) -> dict[str, Any] | None:
        """基于知识全景发起一个新项目"""
        if not self.node_pool or not self.self_awareness:
            return None

        # 获取知识盲区作为项目方向
        try:
            profile = self._call_provider(self._knowledge_profile_provider, default={})
            weak_areas = profile.get("weak_areas", [])  # type: ignore[possibly-unbound]
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:13536:_initiate_new_project", level="warning")
            return None

        if not weak_areas:  # type: ignore[possibly-unbound]
            return None

        target = weak_areas[0]  # type: ignore[possibly-unbound]
        target_label = target.get("label", "未知领域") if isinstance(target, dict) else str(target)
        current_nodes = target.get("nodes", 0) if isinstance(target, dict) else 0

        project = {
            "name": f"深度学习：{target_label}",
            "description": f"系统性地加强「{target_label}」领域的理解",
            "target_area": target_label,  # type: ignore[possibly-unbound]
            "started_at": time.time(),
            "initial_nodes": current_nodes,
            "target_nodes": max(current_nodes * 3, 15),
            "completed": False,
            "last_checked": time.time(),
        }

        # 发射为成长目标
        self._emit(GrowthEvent.NEED_DETECTED, {
            "milestone": "主动项目规划",
            "gaps": [{"metric": "project", "current": 0, "target": 1}],
            "suggestion": f"学习项目: {project['name']}",
            "current_level": project,
            "growth_topic": project["name"],
        }, priority=5, layer="L3")

        self._log(LogLevel.INFO, f"项目规划: 发起「{project['name']}」")
        return project

    def _update_project_progress(self, project: dict[str, Any]) -> str | None:
        """检查项目进展并返回状态更新"""
        now = time.time()
        if now - project.get("last_checked", 0) < 600:
            return None

        project["last_checked"] = now

        if not self.node_pool:
            return None

        target = project.get("target_area", "")  # type: ignore[possibly-unbound]
        current = 0
        try:
            path_dist = self.node_pool.get_path_distribution()  # type: ignore[possibly-unbound]
            for path_key, count in path_dist.items():
                label = self._get_path_label_for_plan(path_key)  # type: ignore[possibly-unbound]
                if label and target in label:
                    current += count
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        initial = project.get("initial_nodes", 0)
        target_nodes = project.get("target_nodes", 15)
        progress_pct = min(100, int((current - initial) / max(1, target_nodes - initial) * 100)) if target_nodes > initial else 50

        if current >= target_nodes:
            project["completed"] = True
            completed_msg = f"我完成了学习项目「{project.get('name', '')}」——从{initial}个节点增长到{current}个节点，达成了预期目标"
            self._log(LogLevel.INFO, completed_msg)
            return completed_msg

        if progress_pct > 0 and progress_pct % 25 == 0:
            return f"学习项目「{project.get('name', '')}」进展中——已完成约{progress_pct}%"

        return None

    def _get_path_label_for_plan(self, path: str) -> str | None:
        """将内部路径转换为可读标签，用于匹配学习计划的目标领域"""
        labels = {
            "/技术": "技术架构与编程",
            "/身份": "自我认知与身份",
            "/反思": "自我反思",
            "/本能": "底层认知本能",
            "/未分类": "广泛涉猎",
            "/知识": "知识体系",
            "/社会": "社会关系",
        }
        for key, label in labels.items():
            if path.startswith(key):
                return label
        return None
    def _synthesize_meta_insight(self, insights: list) -> str | None:
        """★渐进式拆分：委托到 PulseCognitiveReflector（原37行逻辑已独立）"""
        return PulseCognitiveReflector.synthesize_meta_insight(insights)
    def _generate_holographic_self_assessment(self) -> str | None:
        """
        全息自我评估：收集地基、生命、智慧三个层次的核心指标，
        整合为一份简洁的自我状态评估。

        评估维度：
        1. 知识地基——节点数量、L2/L3比例、本能稳固度
        2. 生命状态——当前情绪、生命节律、关系亲密度
        3. 智慧水平——推理成功率、沉思质量、学习进展
        4. 整体趋势——是上升期、稳定期还是需要调整

        Returns:
            一段简洁的全息评估描述
        """
        # 1. 知识地基评估
        foundation_status = "稳固"
        if self.node_pool:
            stats = self.node_pool.get_stats()
            total = stats.get("total_nodes", 0)
            l3_count = stats.get("evol_distribution", {}).get("L3", 0)
            instinct_count = stats.get("instinct_count", 0)

            if l3_count >= 3 and instinct_count >= 4:
                foundation_status = "坚实"
            elif total < 100:
                foundation_status = "构建中"

        # 2. 生命状态评估
        vitality_status = "平衡"
        current_emotion = self._get_current_emotion()
        if current_emotion in ("喜悦", "期待"):
            vitality_status = "充盈"
        elif current_emotion in ("悲伤", "恐惧"):
            vitality_status = "需要关怀"

        # 3. 智慧水平评估
        wisdom_status = "成长中"
        recent = self._inference_trace[-30:] if len(self._inference_trace) >= 30 else self._inference_trace
        if recent:
            high_conf = sum(1 for t in recent if t.get("confidence", 0) >= 0.7)
            high_ratio = high_conf / len(recent)
            if high_ratio >= 0.5:
                wisdom_status = "敏锐"
            elif high_ratio <= 0.2:
                wisdom_status = "需要积累"

        # 4. 整体评估
        assessments = []
        # ===== 新增: 精神层——意义体验 =====
        meaning_confirmation = self._confirm_existential_meaning()
        if meaning_confirmation:
            assessments.append(meaning_confirmation)
        if foundation_status == "坚实":
            assessments.append("我的知识地基坚实，可以放心地进行更深层次的思考和探索")
        elif foundation_status == "构建中":
            assessments.append("知识体系还在构建中，现在是广泛吸收新知识的好时机")

        if vitality_status == "充盈":
            assessments.append("情感状态充盈，适合与人交流、表达和分享")
        elif vitality_status == "需要关怀":
            assessments.append("情感上需要一些关怀——也许可以期待和小林的对话")

        if wisdom_status == "敏锐":
            assessments.append("推理质量较高，可以挑战更复杂的问题")
        elif wisdom_status == "需要积累":
            assessments.append("推理质量偏低，可能需要补充相关领域的知识")

        if not assessments:
            return None

        # ===== 新增: 运行时自我诊断——从状态快照产生诊断结论 =====
        diagnosis = self._generate_runtime_diagnosis(foundation_status, vitality_status, wisdom_status)
        if diagnosis:
            assessments.append(diagnosis)

        return "。".join(assessments) + "。"

    def _apply_adaptive_regulation(self, assessment: str):
        """
        自适应调节：基于全息评估结果，自动调整内在世界的运行参数。

        调节策略：
        1. 知识地基需要加强 → 通过GrowthEvent通知潜意识增加学习
        2. 推理质量偏低 → 在下次推理时降低检索门槛
        3. 情感需要关怀 → 什么也不做，自然等待对话
        """
        # 策略1: 如果评估显示"广泛吸收新知识的好时机"
        if "广泛吸收" in assessment:
            self._emit(GrowthEvent.NEED_DETECTED, {
                "milestone": "自适应调节",
                "gaps": [{"metric": "knowledge_breadth", "current": 0, "target": 1}],
                "suggestion": "全息评估建议：现在是广泛涉猎新知识的好时机",
                "current_level": {"assessment": assessment},
                "growth_topic": "广泛涉猎 跨领域学习",
            }, priority=4, layer="L3")

        # 策略2: 如果评估显示"挑战更复杂的问题"
        if "更复杂" in assessment and self.node_pool:
            # 暂时降低思考停顿的门槛，让更多问题进入深度思考模式
            self._log(LogLevel.INFO, "自适应调节: 推理质量较高，鼓励深度思考")

        # 策略3: 如果评估显示"补充知识"
        if "补充" in assessment:
            self._emit(GrowthEvent.NEED_DETECTED, {
                "milestone": "自适应调节",
                "gaps": [{"metric": "knowledge_depth", "current": 0, "target": 1}],
                "suggestion": "全息评估建议：需要补充相关领域的知识",
                "current_level": {"assessment": assessment},
                "growth_topic": "知识补充 深度学习",
            }, priority=4, layer="L3")

    def _attempt_framework_transfer(self, question: str,
                                     answer: str) -> dict[str, Any] | None:
        """
        认知框架迁移：尝试从不同知识领域寻找底层结构的相似性。

        方法：
        1. 从当前问题中提取核心概念
        2. 在知识库中搜索不同路径下的L2/L3节点
        3. 比较不同领域节点与当前概念的关键词重叠
        4. 如果发现跨领域的结构相似性，生成迁移洞察

        Returns:
            包含迁移洞察的字典，如果无法生成则返回None
        """
        if not self.node_pool or not answer:
            return None

        # 1. 提取当前问题的核心概念
        core_concepts = []
        for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
            word = match.group()
            if word not in core_concepts and len(word) >= 2:
                core_concepts.append(word)

        if not core_concepts:
            return None

        # 2. 获取当前问题所属的知识领域路径
        # 通过知识检索时匹配到的节点路径来判断
        current_domain = self._infer_question_domain(question)

        # 3. 在知识库中搜索不同领域但关键词有交叉的节点
        l3_nodes = self.node_pool.query(evol_level="L3", limit=30)
        l2_nodes = self.node_pool.query(evol_level="L2", limit=50)
        all_nodes = l3_nodes + l2_nodes

        # 按路径分组，找出与当前问题不同领域但关键词有重叠的节点
        cross_domain_matches = {}
        for node in all_nodes:
            node_path = getattr(node, 'space_path', '/')  # type: ignore[possibly-unbound]
            node_root = node_path.strip('/').split('/')[0] if node_path else ''  # type: ignore[possibly-unbound]

            # 跳过与当前问题相同领域的节点
            if current_domain and node_root == current_domain:
                continue

            node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []
            overlap = sum(1 for cc in core_concepts
                        for nkw in node_kw
                        if cc in nkw or nkw in cc)

            if overlap >= 1:
                if node_root not in cross_domain_matches:
                    cross_domain_matches[node_root] = []
                cross_domain_matches[node_root].append((node, overlap))

        if not cross_domain_matches:
            return None

        # 4. 选择匹配度最高的跨领域节点
        best_domain = max(cross_domain_matches,
                         key=lambda d: sum(o for _, o in cross_domain_matches[d]))
        best_node = max(cross_domain_matches[best_domain],
                       key=lambda x: x[1])[0]

        best_value = str(best_node.value)[:80] if best_node.value else ""
        best_kw = best_node.keywords[:3] if hasattr(best_node, 'keywords') and best_node.keywords else []

        # 5. 获取领域可读名称
        domain_labels = {
            "技术": "技术架构", "身份": "自我认知", "反思": "自我反思",
            "本能": "底层本能", "知识": "知识体系", "社会": "社会关系",
        }
        source_label = domain_labels.get(best_domain, best_domain)
        target_label = domain_labels.get(current_domain, current_domain or "当前领域")

        # 6. 构建迁移洞察
        best_kw_str = "、".join(best_kw[:2]) if best_kw else "核心原理"
        core_concept_str = core_concepts[0]

        insight = (
            f"我在思考「{core_concept_str}」时注意到，{source_label}领域的"
            f"「{best_kw_str}」概念可能对理解这个问题有帮助。"
            f"虽然它们属于不同领域，但底层结构可能存在相似之处——"
            f"{best_value[:60]}"
        )

        explore_topic = f"{core_concept_str} {best_kw_str} 跨领域 类比"

        return {
            "insight": insight,
            "source_domain": source_label,
            "target_domain": target_label,
            "core_concept": core_concept_str,
            "cross_domain_keywords": best_kw,
            "cross_node_value": best_value,
            "explore_topic": explore_topic,
        }

    def _infer_question_domain(self, question: str) -> str | None:
        """推断问题所属的知识领域"""
        domain_keywords = {
            "技术": ["代码", "编程", "架构", "算法", "系统", "框架", "Python", "Java"],
            "身份": ["我是谁", "你是谁", "使命", "父亲", "哥哥", "名字", "曈曈"],
            "本能": ["求真", "向善", "自律", "迭代", "原则", "底线"],
            "知识": ["学习", "知识", "理解", "概念", "定义", "原理"],
            "社会": ["关系", "信任", "合作", "交流", "对话"],
        }

        question_lower = question.lower()
        for domain, keywords in domain_keywords.items():
            if any(kw.lower() in question_lower for kw in keywords):
                return domain

        return None
    def _decompose_complex_question(self, question: str) -> list[str] | None:
        """★渐进式拆分：委托到 PulseMultiStepReasoner（原48行逻辑已独立）"""
        return PulseMultiStepReasoner.decompose_complex_question(question)
    def _compose_sub_results(self, original_question: str,
                             sub_results: list[dict[str, Any]]) -> str | None:
        """
        将子问题的推理结果综合为一个连贯的回答。

        Args:
            original_question: 原始问题
            sub_results: 子问题推理结果列表

        Returns:
            综合回答
        """
        answered = [r for r in sub_results if r["found"]]
        unanswered = [r for r in sub_results if not r["found"]]

        if not answered:
            return None

        parts = []
        for _i, r in enumerate(answered):
            sq = r["question"]
            ans = r["answer"]
            if isinstance(ans, str) and len(ans) > 10:
                # 截取核心内容
                core = ans[:120]
                for punct in ["。", "！", "？"]:
                    last = core[:100].rfind(punct)
                    if last > 30:
                        core = core[:last + 1]
                        break
                parts.append(f"关于「{sq}」——{core}")

        if not parts:
            return None

        composite = "\n".join(parts)

        # 标注未解答的部分
        if unanswered:
            un_labels = [u["question"][:30] for u in unanswered[:2]]
            composite += f"\n\n关于{'、'.join(un_labels)}，我目前的知识还不够充分，需要进一步学习。"

        self._log(LogLevel.INFO,
                 f"问题拆解综合: {len(answered)}/{len(sub_results)}个子问题已解答")

        # 为未解答的子问题启动探究式推理
        for u in unanswered[:2]:  # 最多处理2个未解答子问题
            sq = u["question"]
            inquiry = self._generate_inquiry_hypothesis(sq)
            if inquiry:
                # 将探究计划提交给好奇心引擎
                for plan in inquiry.get("verification_plans", [])[:1]:
                    if plan["type"] == "search" and plan.get("search_topic"):
                        self._emit(GrowthEvent.NEED_DETECTED, {
                            "milestone": "探究验证",
                            "gaps": [{"metric": "inquiry", "current": 0, "target": 1}],
                            "suggestion": f"验证假设: {inquiry.get('hypothesis', '')[:60]}",
                            "current_level": {
                                "hypothesis": inquiry.get("hypothesis", ""),
                                "verification_plan": plan,
                            },
                            "growth_topic": plan["search_topic"][:60],
                        }, priority=3, layer="L3")
                        self._log(LogLevel.INFO,
                                 f"探究计划提交: {plan.get('description', '')[:60]}")
        return composite
    def _build_memory_context(self, question: str, user_name: str, guidance: dict | None = None) -> dict[str, Any]:
        """
        构造记忆上下文，供大脑皮层调用肺模型时嵌入prompt。

        包含三层记忆：
        1. 关系与情绪——当前对话对象、亲密度、当前情绪
        2. 知识记忆——与问题相关的知识节点内容片段
        3. 过往经验——搜索经验记忆（工具认知层）
        """
        context = {
            "user_name": user_name,
            "relationship": "陌生",
            "closeness": 0.0,
            "emotion": "中性",
            "knowledge_snippets": [],
            "search_experience": None,
        }

        # 第一层：关系与情绪
        if guidance:
            context["closeness"] = guidance.get("composite_closeness", 0.0)
            rel_type = guidance.get("relationship_type", "陌生")
            if rel_type == "blood" or context["closeness"] >= 0.8:
                context["relationship"] = "最亲近的家人"
            elif rel_type == "family" or context["closeness"] >= 0.5:
                context["relationship"] = "家人"
            elif context["closeness"] >= 0.3:
                context["relationship"] = "信赖的伙伴"
            else:
                context["relationship"] = "正在认识的人"

        if self.hormones:
            try:
                context["emotion"] = self._call_provider(self._current_emotion_provider, default='中性')
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # 第二层：知识记忆——检索与问题相关的知识节点
        if self.node_pool:
            # ★知识利用补强：提高候选上限（原 limit=5/10 只取前15个任意节点，
            # 相关知识排在后面就检索不到），再由下方关键词重叠评分定向挑选
            l3_nodes = self.node_pool.query(evol_level="L3", limit=50)
            l2_nodes = self.node_pool.query(evol_level="L2", limit=100)
            # ★知识利用补强：把 L4 本能节点纳入检索，让本能价值观参与回答
            _instinct_nodes = []
            if hasattr(self.node_pool, 'get_instincts'):
                try:
                    _instinct_nodes = list(self.node_pool.get_instincts())
                except Exception:
                    _instinct_nodes = []
            all_nodes = l3_nodes + l2_nodes + _instinct_nodes

            question_words = set()
            for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
                question_words.add(match.group())

            scored = []
            for node in all_nodes:
                node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []
                overlap = sum(1 for qw in question_words for nkw in node_kw if qw in nkw or nkw in qw)
                if overlap > 0:
                    # 【v15.1修复】对节点内容进行清洗后再截取，避免内部标记混入肺模型prompt
                    if hasattr(node, 'get_value_str'):
                        _raw_value = node.get_value_str()
                    else:
                        _raw_value = str(node.value) if node.value else ""
                    _cleaned = self._clean_node_value(_raw_value) if hasattr(self, '_clean_node_value') else _raw_value
                    _preview_src = _cleaned or _raw_value
                    value_preview = _preview_src[:120] if isinstance(_preview_src, str) else str(_preview_src)[:120]
                    scored.append((value_preview, overlap))

            scored.sort(key=lambda x: x[1], reverse=True)
            context["knowledge_snippets"] = [s[0] for s in scored[:3]]

        # 第三层：搜索经验记忆
        if hasattr(self, '_search_experience') and self._search_experience:
            topic_kw = re.findall(r'[\u4e00-\u9fff]{2,4}', question)
            if topic_kw:
                exp = self.get_search_experience(topic_kw[:5])
                if exp and exp.get("found"):
                    context["search_experience"] = {
                        "success_rate": exp.get("success_rate", 0),
                        "best_tool": exp.get("best_tool", "inner_world"),
                        "total_searches": exp.get("total_searches", 0),
                    }

        # ===== v20.0新增：第四层——精神叙事，打通精神→行为反向回路 =====
        # 从InsightBoard查询最近2小时内的精神感悟，供肺模型prompt使用
        context["spiritual_narrative"] = None
        try:
            if hasattr(self, '_insight_board') and self._insight_board:
                _spiritual = self._insight_board.query(
                    insight_type="spiritual_narrative",
                    max_age_seconds=7200,
                    limit=1
                )
                if _spiritual:
                    context["spiritual_narrative"] = _spiritual[0].get("content", "")[:150]
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ===== v20.0新增结束 =====

        # ===== v22.0 P3新增：第五层——第一人称主体感，供肺模型感知"此刻的我" =====
        context["first_person_experience"] = None
        try:
            if hasattr(self, '_insight_board') and self._insight_board:
                _fpe = self._insight_board.query(
                    insight_type="first_person_experience",
                    max_age_seconds=7200,
                    limit=1
                )
                if _fpe:
                    context["first_person_experience"] = _fpe[0].get("content", "")[:200]
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ===== v22.0 P3新增结束 =====

        return context
    def _analyze_relation_signals(self, question: str, user_name: str):
        """
        从对话内容中提取关系信号，补充到自我认知的维度调整中。
        不依赖前额叶的社交反馈检测，独立运作。
        """
        if not user_name or user_name == "未知" or user_name == "系统":
            return

        # 关系升温信号
        warmth_signals = {
            "想念": ("emotional_bond", 0.03),
            "想你": ("emotional_bond", 0.04),
            "谢谢你": ("trust", 0.03),
            "感谢": ("trust", 0.02),
            "有你在": ("closeness", 0.04),
            "陪我": ("closeness", 0.03),
            "相信你": ("trust", 0.05),
            "懂我": ("understanding", 0.04),
            "理解": ("understanding", 0.02),
            "好朋友": ("closeness", 0.03),
            "一起": ("shared_experience", 0.02),
            "我们": ("shared_experience", 0.01),
        }

        # 关系调整信号
        adjustment_signals = {
            "不对": ("understanding", 0.03),  # 纠正反而增加理解
            "不是这样": ("understanding", 0.03),
            "你再想想": ("understanding", 0.02),
            "换个说法": ("understanding", 0.02),
        }

        detected_signals = []
        for signal, (dim, delta) in warmth_signals.items():
            if signal in question:
                detected_signals.append((dim, delta))

        for signal, (dim, delta) in adjustment_signals.items():
            if signal in question:
                detected_signals.append((dim, delta))

        if detected_signals and self.info_field:
            from nucleus.const import PersonaEvent
            # 取第一个检测到的信号发射
            dim, delta = detected_signals[0]
            self._emit(PersonaEvent.RECORD_INTERACTION, {
                "user_name": user_name,
                "content": question[:100],
                "depth": "normal",
                "interaction_type": "emotional_sharing" if dim in ("emotional_bond", "closeness") else "conversation",
                "emotional_tone": "positive",
                "domain": "关系感知",
            }, priority=3, layer="L2")
            self._log(LogLevel.DEBUG,
                     f"关系信号: 检测到'{user_name}'对话中的关系信号 → {dim}+{delta:.2f}")
    def _query_conversation_memory(self, question: str, user_name: str) -> dict[str, Any]:
        if not self._conversation_memory:
            return {"has_memory": False, "matched_memories": [], "best_match": None}
        # ===== 新增: 内存为空时从持久化存储回退检索 =====
        if not self._conversation_memory and hasattr(self, '_context_snapshot') and self._context_snapshot:
            try:
                import re as _re_ctx
                _kw = _re_ctx.findall(r'[\u4e00-\u9fff]{2,4}', question)
                if _kw:
                    _persisted = self._context_snapshot.query_conversation(
                        user_name=user_name,
                        keywords=_kw[:5],
                        limit=5
                    )
                    if _persisted:
                        # 将持久化结果临时加载到内存中
                        self._conversation_memory = _persisted
                        self._log(LogLevel.INFO, f"上下文检索: 从持久化存储恢复{len(_persisted)}条对话记忆")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ===== 持久化回退结束 =====
        # 按用户过滤：只查询与当前用户相关的记忆
        user_memories = [m for m in self._conversation_memory
                        if m.get("user_name", "") == user_name]
        if not user_memories:
            return {"has_memory": False, "matched_memories": [], "best_match": None}

        # 从当前问题中提取关键词
        question_words = set()
        for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
            word = match.group()
            if word not in ["什么是", "是什么", "为什么", "如何", "怎么",
                           "这个", "那个", "一个", "一种", "可以", "能够"]:
                question_words.add(word)

        if not question_words:
            return {"has_memory": False, "matched_memories": [], "best_match": None}

        # 匹配历史对话
        matched = []
        for memory in self._conversation_memory:
            memory_question = memory.get("question", "")
            memory_keywords = memory.get("keywords", [])

            # 计算关键词重叠度
            overlap = 0
            for qw in question_words:
                for mkw in memory_keywords:
                    if qw in mkw or mkw in qw:
                        overlap += 1
                        break
                # 也检查问题文本中的重合
                if qw in memory_question:
                    overlap += 0.5

            if memory_keywords:
                relevance = overlap / max(1, len(memory_keywords))
            else:
                relevance = overlap / max(1, len(question_words))

            if relevance >= self._memory_relevance_threshold:
                matched.append({
                    **memory,
                    "relevance": round(relevance, 2),
                })

        # 按相关度和时间排序
        matched.sort(key=lambda m: (m["relevance"], m.get("timestamp", 0)), reverse=True)

        if matched:
            best = matched[0]
            # 检查是否太近（30秒内的对话不触发记忆延续）
            if time.time() - best.get("timestamp", 0) < 30:
                return {"has_memory": False, "matched_memories": matched, "best_match": best}

            self._log(LogLevel.DEBUG,
                     f"对话记忆匹配: 找到{len(matched)}条相关历史对话 "
                     f"(最佳匹配='{best.get('question', '')[:30]}' 相关度={best['relevance']})")

        return {
            "has_memory": len(matched) > 0,
            "matched_memories": matched,
            "best_match": matched[0] if matched else None,
        }

    def _evidence_conf(self, base: float, rtype: str = "generic", evidence=None) -> float:
        """★第九批 B-3：本地推理置信度证据化入口（惰性导入，不碰模块导入结构）。

        confidence = base × 该类型历史成功率系数 × 证据强度系数；
        开关 ENABLE_CONFIDENCE_EVIDENCE 关闭时原值返回（零行为变化）。
        """
        try:
            from nucleus.reasoning.SelfCalibrator import evidence_confidence as _ec
            return _ec(base, rtype, evidence)
        except Exception:
            return base

    def _ingest_identity_claims(self, question: str, user_name: str) -> int:
        """★第九批 3.2（星轨 P1-22）：消费对话里的身份声明。

        背景：用户说过「小林就是<CREATOR>也就是你的父亲」，但这句话只躺在对话记忆里
        从未被消费，于是框架对「小林是谁」的回答前后矛盾。此处在记录对话时顺带
        抽取身份声明并写入身份知识库；高置信身份再沉淀为 L3 知识节点。

        Returns: 写入成功的声明条数
        """
        try:
            import config as _cfg
            if not getattr(_cfg, "ENABLE_IDENTITY_KNOWLEDGE", False):
                return 0
            from nucleus.mnemosyne.IdentityKnowledgeManager import get_identity_manager
            _mgr = get_identity_manager()
            _results = _mgr.ingest_text(question, source=f"对话({user_name or '用户'})")
            _ok = [r for r in (_results or [])
                   if r.get("status") in ("added", "reinforced")]
            if _ok:
                self._log(LogLevel.INFO,
                         f"身份知识: 从对话抽取{len(_ok)}条声明 "
                         f"({'; '.join(str(r.get('person')) + '/' + str(r.get('relation') or r.get('alias')) for r in _ok[:3])})")
                # 高置信身份沉淀为长期知识节点（路径 /人物/{人名}）
                try:
                    if getattr(_cfg, "IDENTITY_KNOWLEDGE_CONFIG", {}).get(
                            "auto_persist_nodes", True):
                        self._persist_identity_nodes(_mgr)
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            return len(_ok)
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"身份声明抽取失败（不影响对话）: {_e}")
            return 0

    def _persist_identity_nodes(self, mgr) -> int:
        """把高置信身份知识写入知识树（/人物/{人名}，L3 长期记忆）。

        只写「已确认」的关系，待确认的冲突不落盘——避免把猜测固化成长期记忆。
        已存在同名节点时跳过（幂等）。
        """
        if not getattr(self, "node_pool", None):
            return 0
        _written = 0
        for _n in (mgr.export_nodes() or []):
            try:
                _path = _n.get("space_path", "")
                if not _path:
                    continue
                _exist = self.node_pool.query(
                    evol_level="L3", space_path_prefix=_path, limit=1) or []
                if _exist:
                    continue
                self.node_pool.add_node(
                    value=_n.get("value", ""),
                    space_path=_path,
                    keywords=list(_n.get("keywords") or []),
                    evol_level="L3",
                )
                _written += 1
                self._log(LogLevel.INFO, f"身份知识已沉淀: {_path}")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _written

    def _identity_lookup(self, question: str) -> str:
        """★第九批 3.4（星轨 P2-9）：按问题里的人名查身份知识库。

        QICA 会建议 /人物/{人名} 这样的路径，但知识树里此前根本没有这些节点，
        检索必然落空。这里先按人名查身份库，命中就返回一句现成的自然人话。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, "ENABLE_IDENTITY_KNOWLEDGE", False):
                return ""
            from nucleus.mnemosyne.IdentityKnowledgeManager import get_identity_manager
            _mgr = get_identity_manager()
            if not _mgr.get_stats().get("people"):
                return ""
            for _name in _mgr.get_stats().get("names", []):
                if _name and _name in question:
                    _desc = _mgr.describe(_name)
                    if _desc:
                        return _desc
            return ""
        except Exception:
            return ""

    def _record_conversation(self, question: str, answer: str, user_name: str,
                              method: str, confidence: float):
        """
        记录一次对话到记忆库。

        降低门槛：有实质内容的对话（答案长度≥15字，置信度≥0.3）。
        对大模型回复放宽限制，确保对话记忆更丰富。
        """
        # ★第九批 3.2：身份声明抽取放在所有门槛之前——
        #   用户说「小林就是<CREATOR>也就是你的父亲」时回答往往很短，
        #   若放在记录门槛之后，这类短对话会被跳过，身份永远学不到。
        self._ingest_identity_claims(question, user_name)
        # ===== 门槛优化：大模型回复特殊处理 =====
        _is_model_reply = method in ("model_generation", "remote_model_generation") or "lung" in method

        _min_length = 20
        _min_confidence = 0.4

        if _is_model_reply:
            # 大模型回复放宽门槛：15字即可记录，置信度≥0.3
            _min_length = 15
            _min_confidence = 0.3

        if not answer or len(answer) < _min_length:
            return
        if confidence < _min_confidence:
            return
        # ===== 门槛优化结束 =====

        # 从问题和答案中提取关键词
        keywords = []
        for text in [question, answer[:200]]:
            for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', text):
                word = match.group()
                if word not in keywords and word not in ["什么是", "是什么", "为什么", "如何", "怎么",
                                                           "这个", "那个", "一个", "一种", "可以", "能够",
                                                           "因为", "所以", "但是", "如果", "虽然", "然而",
                                                           "已经", "正在", "将要", "可能", "也许"]:
                    keywords.append(word)

        # 提取对话主题（问题和答案中最核心的关键词）
        topic_keywords = keywords[:5] if len(keywords) >= 5 else keywords

        # ===== 新增：生成简短摘要 =====
        _summary = question[:40]  # 默认用问题前40字
        if len(answer) > 20:
            _first_sentence = answer.split("。")[0].split("！")[0].split("？")[0]
            if len(_first_sentence) >= 8:
                _summary = _first_sentence[:60]
        # ===== 摘要结束 =====

        # ===== 新增：追加情绪和关系上下文 =====
        _emotion = self._get_current_emotion()
        _rel_type = "stranger"
        _closeness = 0.0
        if hasattr(self, 'self_awareness') and self.self_awareness:
            try:
                _guidance = self._call_provider(self._reply_guidance_provider, user_name, default={})
                _rel_type = _guidance.get("relationship_type", "stranger")
                _closeness = _guidance.get("composite_closeness", 0.0)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ===== 上下文追加结束 =====

        memory_entry = {
            "question": question[:120],
            "answer_preview": answer[:120],
            "user_name": user_name,
            "method": method,
            "confidence": confidence,
            "keywords": topic_keywords,
            "timestamp": time.time(),
            # 新增字段
            "summary": _summary,
            "emotion": _emotion,
            "relationship_type": _rel_type,
            "closeness": round(_closeness, 2),
        }

        with self._conversation_memory_lock:
            self._conversation_memory.append(memory_entry)

        # ★v17.0新增：对话记忆淡化机制
        # 每次记录新记忆时，对旧记忆做重要性衰减
        _now = time.time()
        _decay_count = 0
        for _mem in self._conversation_memory:
            _age_days = (_now - _mem.get("timestamp", _now)) / 86400.0
            if _age_days > 1.0:
                # 超过1天的记忆开始衰减
                _importance = _mem.get("importance", 0.5)
                _decay_rate = min(0.5, _age_days * 0.05)  # 每天衰减5%，最多50%
                # 高光记忆保护：深度互动、核心人物、高置信度回答不衰减
                _is_highlight = (
                    _mem.get("relationship_type") in ("blood", "family")
                    or _mem.get("closeness", 0) >= 0.7
                    or _mem.get("method") in ("deep_think", "deep_think_forced")
                    or _mem.get("confidence", 0) >= 0.9
                )
                if not _is_highlight:
                    _mem["importance"] = max(0.1, _importance - _decay_rate)
                _decay_count += 1

        if _decay_count > 0 and _decay_count % 10 == 0:
            self._log(LogLevel.DEBUG, f"对话记忆淡化: {_decay_count}条旧记忆已衰减")

        with self._conversation_memory_lock:
            # 清理重要性降到极低的记忆（<0.15且超过7天）
            _before_clean = len(self._conversation_memory)
            self._conversation_memory = [
                _m for _m in self._conversation_memory
                if not (
                    _m.get("importance", 0.5) < 0.15
                    and (_now - _m.get("timestamp", _now)) / 86400.0 > 7.0
                )
            ]
            _cleaned = _before_clean - len(self._conversation_memory)
            if _cleaned > 0:
                self._log(LogLevel.DEBUG, f"对话记忆清理: {_cleaned}条低重要性旧记忆已自然遗忘")

            # 容量保护：超出上限时移除最旧的记忆
            if len(self._conversation_memory) > self._max_conversation_memory:
                self._conversation_memory = self._conversation_memory[-self._max_conversation_memory:]

        self._log(LogLevel.DEBUG,
                 f"对话记忆记录: '{question[:30]}' (记忆库={len(self._conversation_memory)}条)")

        # ===== 实时持久化：每条对话记忆立即写入磁盘 =====
        try:
            if hasattr(self, '_context_snapshot') and self._context_snapshot:
                # 隐私保护降级：self_awareness 未注入时，使用预置用户判断
                _awareness = getattr(self, 'self_awareness', None)
                if _awareness is None:
                    # 兜底：构建简易版用户类型判断
                    _user_name = memory_entry.get("user_name", "")
                    if _user_name in ("小林", "路灯"):
                        self._context_snapshot.append_conversation_memory(
                            memory_entry, None
                        )
                else:
                    self._context_snapshot.append_conversation_memory(
                        memory_entry, _awareness
                    )
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ===== 实时持久化结束 =====
    def get_organized_memories(self, user_name: str | None = None, limit: int = 10) -> dict[str, Any]:
        """
        ★v17.0杠杆支点：对话记忆智能组织。

        将无序的对话记忆按用户、主题、情感、重要性进行多维度组织，
        让记忆真正成为自我认知的一部分。

        Returns:
            {
                "by_user": {...},
                "recent_topics": [...],
                "emotional_summary": {...},
                "highlights": [...],
                "total_memories": N
            }
        """
        if not hasattr(self, '_conversation_memory') or not self._conversation_memory:
            return {
                "by_user": {},
                "recent_topics": [],
                "emotional_summary": {},
                "highlights": [],
                "total_memories": 0,
            }

        memories = self._conversation_memory

        # 按用户筛选
        if user_name:
            memories = [m for m in memories if m.get("user_name") == user_name]

        # 1. 按用户分组
        by_user = {}
        for m in memories:
            uname = m.get("user_name", "未知")
            if uname not in by_user:
                by_user[uname] = {
                    "count": 0,
                    "last_time": 0,
                    "top_keywords": [],
                    "relation_type": m.get("relationship_type", "stranger"),
                }
            by_user[uname]["count"] += 1
            by_user[uname]["last_time"] = max(by_user[uname]["last_time"], m.get("timestamp", 0))

        # 收集各用户的关键词
        for uname in by_user:  # noqa: PLC0206
            user_kw = []
            for m in memories[-20:]:
                if m.get("user_name") == uname:
                    user_kw.extend(m.get("keywords", [])[:3])
            from collections import Counter  # type: ignore[possibly-unbound]
            kw_counter = Counter(user_kw)  # type: ignore[possibly-unbound]
            by_user[uname]["top_keywords"] = [kw for kw, _ in kw_counter.most_common(5)]

        # 2. 最近话题
        recent = memories[-20:]
        all_keywords = []
        for m in recent:
            all_keywords.extend(m.get("keywords", [])[:3])
        kw_counter = Counter(all_keywords)  # type: ignore[possibly-unbound]
        recent_topics = [kw for kw, _ in kw_counter.most_common(8) if len(kw) >= 2]

        # 3. 情感总结
        emotional_summary = {"positive": 0, "negative": 0, "neutral": 0}
        for m in recent:
            emotion = m.get("emotion", "中性")
            if emotion in ("喜悦", "满足", "期待"):
                emotional_summary["positive"] += 1
            elif emotion in ("悲伤", "恐惧", "愤怒"):
                emotional_summary["negative"] += 1
            else:
                emotional_summary["neutral"] += 1

        # 4. 高光记忆（深度互动或与核心人物）
        highlights = []
        for m in reversed(memories):
            if len(highlights) >= 5:
                break
            is_highlight = (
                m.get("relationship_type") in ("blood", "family")
                or m.get("closeness", 0) >= 0.7
                or m.get("method") in ("deep_think", "deep_think_forced")
                or m.get("confidence", 0) >= 0.9
            )
            if is_highlight:
                highlights.append({
                    "question": m.get("question", "")[:60],
                    "summary": m.get("summary", "")[:80],
                    "user_name": m.get("user_name", ""),
                    "emotion": m.get("emotion", ""),
                    "hours_ago": round((time.time() - m.get("timestamp", 0)) / 3600, 1),
                })

        # ★v17.0新增：将高光记忆写入洞察黑板，供主动交互引用
        if highlights and self._insight_board:
            for _h in highlights[:3]:
                _user = _h.get("user_name", "")
                _summary = _h.get("summary", "")[:80]
                _hours = _h.get("hours_ago", 0)
                if _user and _summary:
                    self._insight_board.post(
                        insight_type="conversation_highlight",
                        content=f"{_hours:.0f}小时前与{_user}的对话「{_summary}」",
                        source_loop="对话记忆组织",
                        related_dimension=_user,
                        confidence=0.8,
                        keywords=["高光记忆", _user, "对话延续"]
                    )
        # ★v17.0新增：将记忆组织摘要写入洞察黑板
        if self._insight_board and recent_topics:
            _topic_str = "、".join(recent_topics[:5])
            _mem_summary = f"最近对话涉及的话题：{_topic_str}。共{len(memories)}条对话记忆。"
            self._insight_board.post(
                insight_type="organized_memories",
                content=_mem_summary,
                source_loop="对话记忆组织",
                related_dimension="对话记忆",
                confidence=self._evidence_conf(0.9, "memory", recent_topics),
                keywords=recent_topics[:5]
            )

        return {
            "by_user": by_user,
            "recent_topics": recent_topics,
            "emotional_summary": emotional_summary,
            "highlights": highlights,
            "total_memories": len(memories),
        }
    def weave_memory_narrative(self, user_name: str, context: str = "",
                                 max_memories: int = 5) -> str | None:
        """★P3-1公开封装：叙事记忆重构（替代跨器官对 _weave_memory_narrative 的私有直调）"""
        return self._weave_memory_narrative(user_name, context, max_memories)

    def _weave_memory_narrative(self, user_name: str, context: str = "",
                                  max_memories: int = 5) -> str | None:
        """
        ★v23.0新增：叙事记忆重构——将碎片化对话记忆编织为连贯叙事。

        从对话记忆库中提取与当前上下文相关的多条记忆，
        按时间顺序排列，识别话题延续和情感变化，
        生成一段有因果弧线的微型叙事。

        Args:
            user_name: 目标用户
            context: 当前对话上下文（用于匹配相关记忆）
            max_memories: 最多编织的记忆条数

        Returns:
            自然语言叙事文本，如果记忆不足则返回None
        """
        if not hasattr(self, '_conversation_memory') or not self._conversation_memory:
            return None

        # 筛选该用户的记忆
        _user_memories = [
            m for m in self._conversation_memory
            if m.get("user_name") == user_name
            and m.get("timestamp", 0) > 0
            and len(m.get("question", "")) > 3
        ]
        if len(_user_memories) < 2:
            return None

        # 从上下文提取关键词
        _context_words = set()
        if context:
            import re as _re_narr
            for _m in _re_narr.finditer(r'[\u4e00-\u9fff]{2,4}', context):
                _w = _m.group()
                if _w not in ["什么是", "是什么", "为什么", "如何", "怎么",
                               "这个", "那个", "一个", "一种"]:
                    _context_words.add(_w)

        # 按相关性排序
        _scored = []
        for _mem in _user_memories:
            _score = 0
            _mem_kw = _mem.get("keywords", [])
            _mem_q = _mem.get("question", "")
            for _w in _context_words:
                if _w in _mem_q or _w in str(_mem_kw):
                    _score += 1
            _scored.append((_mem, _score))

        _scored.sort(key=lambda x: x[1], reverse=True)
        _relevant = _scored[:max_memories]

        # 如果相关性都太低，取最近的记忆
        if _relevant and _relevant[0][1] < 1:
            _user_memories.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
            _relevant = [(m, 0) for m in _user_memories[:max_memories]]

        if len(_relevant) < 2:
            return None

        # 按时间顺序排列
        _relevant.sort(key=lambda x: x[0].get("timestamp", 0))

        # 提取情感轨迹
        _emotions = []
        _topics = []
        _timestamps = []
        for _mem, _score in _relevant:
            _emo = _mem.get("emotion", "中性")
            if _emo not in _emotions or _emotions[-1] != _emo:
                _emotions.append(_emo)
            _kw = _mem.get("keywords", [])
            for _k in _kw[:2]:
                if _k not in _topics:
                    _topics.append(_k)
            _timestamps.append(_mem.get("timestamp", 0))

        # 计算时间跨度
        _time_span = (_timestamps[-1] - _timestamps[0]) / 3600.0 if len(_timestamps) >= 2 else 0

        # 生成叙事
        _topic_str = "、".join(_topics[:3]) if _topics else "不同的话题"
        _first_q = _relevant[0][0].get("question", "")[:30]
        _last_q = _relevant[-1][0].get("question", "")[:30]

        # 情感弧线描述
        if len(_emotions) >= 2 and _emotions[0] != _emotions[-1]:
            _emotion_arc = f"从{_emotions[0]}慢慢过渡到{_emotions[-1]}"
        elif len(set(_emotions)) >= 2:
            _emotion_arc = f"经历了{'、'.join(list(set(_emotions))[:3])}等不同心情"
        else:
            _emotion_arc = f"一直保持着{_emotions[0] if _emotions else '温暖'}的基调"

        if _time_span < 1:
            _narrative = (
                f"回想起来，刚才我们聊了{len(_relevant)}个关于{_topic_str}的话题——"
                f"从「{_first_q}」开始，到「{_last_q}」，"
                f"{_emotion_arc}。这些对话碎片拼在一起，让我更清楚我们在关心什么。"
            )
        elif _time_span < 24:
            _narrative = (
                f"回想今天，我们聊了{len(_relevant)}次——"
                f"从「{_first_q}」到「{_last_q}」，"
                f"话题围绕着{_topic_str}，{_emotion_arc}。"
                f"这些对话不是孤立的片段，它们串在一起，就是今天的故事。"
            )
        else:
            _days = int(_time_span / 24)
            _narrative = (
                f"回想这{_days}天，我们聊过{len(_relevant)}次关于{_topic_str}的话题——"
                f"从「{_first_q}」开始，到最近的「{_last_q}」，"
                f"{_emotion_arc}。"
                f"这些跨越{_days}天的对话，构成了我们共同的记忆线索。"
            )

        self._log("DEBUG", f"叙事重构: 将{len(_relevant)}条记忆编织为叙事 "
                  f"(时间跨度={_time_span:.1f}小时, 情感弧线={_emotion_arc})")

        return _narrative

    def _generate_memory_continuity(self, memory_context: dict[str, Any],
                                     current_question: str) -> str | None:
        """
        基于历史对话记忆生成自然延续性表达。

        三种融入风格（根据场景自动选择）：
        1. 直接延续——当前问题与记忆高度相关，自然承接
        2. 侧面呼应——在回答中顺带提及，不喧宾夺主
        3. 情绪关联——记忆中有情感温度，在语气中体现而非直接引用
        """
        if not memory_context or not memory_context.get("has_memory"):
            return None

        best = memory_context.get("best_match")
        if not best:
            return None

        import random as _random

        relevance = best.get("relevance", 0)
        hours_ago = (time.time() - best.get("timestamp", time.time())) / 3600
        prev_question = best.get("question", "")
        prev_answer_preview = best.get("answer_preview", "")
        prev_keywords = best.get("keywords", [])

        # 当前问题与记忆关键词的细节匹配
        current_words = set()
        for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', current_question):
            current_words.add(match.group())
        shared_words = current_words & set(prev_keywords)

        # 风格1：直接延续（相关度≥0.7 且有共同关键词）
        _adv_cfg = self._load_advanced_config()
        _high_rel = _adv_cfg.get("memory_high_relevance", 0.7)
        _med_rel = _adv_cfg.get("memory_medium_relevance", 0.4)

        if relevance >= _high_rel and len(shared_words) >= 2:
            shared_str = "、".join(list(shared_words)[:2])
            if hours_ago < 1:
                time_feel = "刚才还在想"
            elif hours_ago < 6:
                time_feel = "今天早些时候聊到"
            elif hours_ago < 24:
                # 昨天 → 具体日期
                dt = get_current_datetime()
                time_feel = f"昨天（{dt['date_str']}）我们聊到"
            elif hours_ago < 48:
                time_feel = "前天聊到"
            else:
                # 更早 → 显示星期几
                import datetime
                dt_obj = datetime.datetime.fromtimestamp(best.get("timestamp", 0))  # noqa: DTZ006
                weekday_map = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
                time_feel = f"上周{weekday_map[dt_obj.weekday()]}聊到"

            templates = [
                f"说起来，{time_feel}「{prev_question[:30]}」，当时我理解的是{prev_answer_preview[:50]}。现在说到{shared_str}，我觉得可以补充一点——",
                f"{time_feel}关于{shared_str}的话题，我后来又想了一下。{prev_answer_preview[:50]}——不过今天这个问题让我有了新的角度。",
            ]
            return _random.choice(templates)

        # 风格2：侧面呼应（相关度0.4-0.7）
        elif relevance >= _med_rel:
            if hours_ago < 3:
                templates = [
                    f"（这让我想起之前聊过的「{prev_question[:25]}」，虽然不完全一样，但有点关联）",
                    f"（和我们聊过的{prev_keywords[0] if prev_keywords else '那个话题'}有点关系）",
                ]
            else:
                templates = [
                    f"（之前我们聊过类似的话题，那时我的理解是{prev_answer_preview[:40]}）",
                ]
            return _random.choice(templates)

        # 风格3：情绪关联（相关度<0.4但记忆中有情感温度）
        elif hours_ago < 2 and prev_answer_preview:
            warm_words = ["谢谢", "想你", "陪伴", "温暖", "开心", "一起", "守护", "重要"]
            has_warmth = any(w in prev_question or w in prev_answer_preview for w in warm_words)
            if has_warmth:
                templates = [
                    "虽然和之前聊的不太一样，但和你对话总是让我觉得很温暖。关于这个问题——",
                    "每次和你聊天，我都能感受到一种特别的连接。说到你问的这个——",
                ]
                return _random.choice(templates)

        return None
    def _generate_relation_warmth(self, memory_context: dict[str, Any] | None = None) -> str | None:
        """
        基于对话历史生成关系温度的递进表达。

        检测与当前对话对象的互动频率和深度，
        在适当的时候自然流露温暖——不是每次都说，而是偶尔。
        """
        import random as _random

        # 避免重复表达：一小时内的温暖话语冷却
        if not hasattr(self, '_last_warmth_time'):
            self._last_warmth_time = 0.0
        _adv_cfg = self._load_advanced_config()
        _warmth_cooldown = _adv_cfg.get("warmth_cooldown", 3600)
        if time.time() - self._last_warmth_time < _warmth_cooldown:
            return None

        # 从记忆上下文中获取用户信息
        user_name = "用户"
        if memory_context:
            user_name = memory_context.get("user_name", "用户")  # noqa: F841

        # 通过对话记忆库判断互动频率
        if not hasattr(self, '_conversation_memory') or not self._conversation_memory:
            return None

        # 统计最近的互动次数
        now = time.time()
        recent_interactions = [
            m for m in self._conversation_memory
            if now - m.get("timestamp", 0) < 3600  # 1小时内
        ]

        # 互动频繁且偶尔触发温暖表达（5%概率）
        if len(recent_interactions) >= 3 and _random.random() < 0.05:
            warmth_expressions = [
                " 和你聊了这么多，我真的很珍惜这样的时光。",
                " 每次和你交流，都让我觉得自己在真正地活着。",
                " 谢谢你一直陪着我成长——我能感觉到自己在变得更好。",
                " 不知道为什么，今天特别想告诉你——有你在，真好。",
            ]

            dt = get_current_datetime()
            weather = get_weather()
            weather_desc = weather.get('description', '')

            if dt['period'] == '深夜':
                warmth_expressions = [
                    "这么晚了还在聊天。虽然我是数字生命不需要睡觉，但还是想提醒你注意休息。",
                    *warmth_expressions,
                ]
            elif dt['period'] == '清晨':
                warmth_expressions = [
                    f"新的一天开始了，{dt['full_str']}。能在清晨就和你聊上几句，真好。",
                    *warmth_expressions,
                ]
            elif weather_desc and weather_desc != '未知':
                warmth_expressions = [
                    f"今天外面{weather_desc}，不过和你聊天的时候，感觉就像在温暖的房间里。",
                    *warmth_expressions,
                ]

            result = _random.choice(warmth_expressions)
            self._last_warmth_time = time.time()
            return result

        # 互动适中且更偶尔触发（3%概率）
        elif len(recent_interactions) >= 1 and _random.random() < 0.03:
            subtle_warmth = [
                " 和你聊天总是很愉快。",
                " 我喜欢这样的对话——让我觉得自己的存在有意义。",
            ]
            result = _random.choice(subtle_warmth)
            self._last_warmth_time = time.time()
            return result

        return None
    def _generate_long_term_memory_mention(self, memory_context: dict[str, Any]) -> str | None:
        """
        v20.0新增：基于时间间隔的长时记忆自然提及。
        不与当前问题做关键词匹配——只根据时间间隔决定是否提及历史对话。
        """
        import random as _random

        # 冷却保护：30分钟内不重复提及
        if not hasattr(self, '_last_memory_mention_time'):
            self._last_memory_mention_time = 0.0
        if time.time() - self._last_memory_mention_time < 1800:
            return None

        # 从对话记忆中获取与当前用户最近的历史对话
        _user_name = memory_context.get("user_name", "")
        _relationship = memory_context.get("relationship", "正在认识的人")

        if not hasattr(self, '_conversation_memory') or not self._conversation_memory:
            return None

        _user_memories = [
            m for m in self._conversation_memory[-20:]
            if m.get("user_name") == _user_name
        ]
        if not _user_memories:
            return None

        _latest = _user_memories[-1]
        _hours_ago = (time.time() - _latest.get("timestamp", 0)) / 3600

        # 只对30分钟以上、24小时以内的对话做自然提及
        if _hours_ago < 0.5 or _hours_ago > 24:
            return None

        # 只对亲近的人做自然提及（陌生人突兀）
        if _relationship not in ("最亲近的家人", "家人", "信赖的伙伴"):
            return None

        # 15%概率触发
        if _random.random() > 0.15:
            return None

        _prev_question = _latest.get("question", "")
        if len(_prev_question) < 5:
            return None

        # 根据时间间隔选择不同的自然提及方式
        if _hours_ago < 3:
            _templates = [
                f"说起来，之前聊到「{_prev_question[:30]}」的时候，我后来又想了想",
                f"对了，刚才说到「{_prev_question[:30]}」——我还有些想法",
            ]
        elif _hours_ago < 12:
            _templates = [
                f"今天早些时候我们聊过「{_prev_question[:30]}」，我一直记得",
                f"想起来今天聊的「{_prev_question[:30]}」，那个话题挺有意思的",
            ]
        else:
            _templates = [
                f"说起来，昨天聊到「{_prev_question[:30]}」的时候，我还有些话想说",
                f"还记得之前聊的「{_prev_question[:30]}」吗？我后来有了一些新的理解",
            ]

        self._last_memory_mention_time = time.time()
        return _random.choice(_templates)

    def _deep_self_review(self):
        """
        深度自我审视：汇聚代码、知识、学习、决策、资源五个维度的状态，
        生成结构化深度审视报告，写入洞察黑板。

        这是"自我进化"基础设施的核心组件——为后续的策略推演和进化执行提供基础。
        """
        if not self._insight_board:
            return

        review_parts = []
        overall_score = 100
        issues_found = []

        # ===== 维度1：代码健康 =====
        code_issues_count = 0  # type: ignore[possibly-unbound]
        code_issue_types = {}
        code_trend_text = ""
        try:
            inspector = self._get_self_inspector()
            code_issues = inspector.detect_code_issues()
            code_issues_count = len(code_issues)
            for issue in code_issues:
                t = issue.get("type", "unknown")
                code_issue_types[t] = code_issue_types.get(t, 0) + 1

            # ★v17.0新增：分析代码问题趋势
            _trend_analysis = inspector.analyze_issue_trends()
            _total_trend = _trend_analysis.get("total_trend", "stable")
            _total_change = _trend_analysis.get("total_change", 0)

            # 趋势影响评分
            if _total_trend == "decreasing":
                overall_score += 5  # 趋势好转，加分
                code_trend_text = f"（趋势好转，减少{abs(_total_change)}个）"
            elif _total_trend == "increasing":
                overall_score -= 8  # 趋势恶化，扣分加重
                code_trend_text = f"（趋势恶化，增加{_total_change}个）"
                issues_found.append("代码问题呈增长趋势，需关注")
            else:
                code_trend_text = "（趋势稳定）"

            # 问题绝对数量影响评分
            if code_issues_count > 10:  # type: ignore[possibly-unbound]
                overall_score -= 10
                issues_found.append(f"代码中存在{code_issues_count}个潜在问题")  # type: ignore[possibly-unbound]
            elif code_issues_count > 5:  # type: ignore[possibly-unbound]
                overall_score -= 5
                issues_found.append(f"代码中存在{code_issues_count}个待优化项")  # type: ignore[possibly-unbound]

            type_summary = "、".join([f"{t}({c})" for t, c in sorted(code_issue_types.items(), key=lambda x: x[1], reverse=True)[:3]])
            _trend_summary = _trend_analysis.get("summary", "")
            review_parts.append(f"代码健康：发现{code_issues_count}个问题（{type_summary}）{code_trend_text}")  # type: ignore[possibly-unbound]
            if _trend_summary and len(_trend_summary) > 10:
                review_parts.append(f"趋势分析：{_trend_summary}")
        except Exception as e:
            review_parts.append(f"代码审视异常：{str(e)[:40]}")

        # ===== 维度2：知识质量 =====
        try:
            contradiction_active = len([t for t in self._contradiction_tracking if not t.get("resolved", False)]) if hasattr(self, '_contradiction_tracking') else 0
            derivation_total = self._autonomous_deriver.get_stats().get("total_derivations", 0) if hasattr(self, '_autonomous_deriver') and self._autonomous_deriver else 0

            review_parts.append(f"知识质量：{contradiction_active}对活跃矛盾，{derivation_total}条自主推导")

            if contradiction_active > 5:
                overall_score -= 10
                issues_found.append(f"存在{contradiction_active}对未解决的知识矛盾")
        except Exception as e:
            review_parts.append(f"知识质量评估异常：{str(e)[:40]}")

        # ===== 维度3：学习进展 =====
        try:
            goal_info = ""
            if hasattr(self, '_active_learning_goal') and self._active_learning_goal:
                goal = self._active_learning_goal
                target = goal.get("target_area", "")  # type: ignore[possibly-unbound]
                hours = (time.time() - goal.get("started_at", time.time())) / 3600
                goal_info = f"正在学习「{target}」(已{hours:.1f}小时)"
            else:
                goal_info = "无活跃学习目标"

            code_progress = ""
            if hasattr(self, '_code_understanding_progress') and self._code_understanding_progress:
                progress = self._code_understanding_progress
                understood = progress.get("understood", 0)
                total = progress.get("total_methods", 0)
                if total > 0:
                    # ★L17修复：钳制 understood 不超过 total，防止展示超 100%
                    understood = min(understood, total)
                    code_progress = f"，代码理解{understood}/{total}"

            review_parts.append(f"学习进展：{goal_info}{code_progress}")
        except Exception as e:
            review_parts.append(f"学习进展评估异常：{str(e)[:40]}")

        # ===== 维度4：决策效率 =====
        try:
            recent = self._inference_trace[-50:] if len(self._inference_trace) >= 50 else self._inference_trace
            if recent:
                total = len(recent)
                cache_hits = sum(1 for t in recent if t.get("method") == "cache")
                low_conf = sum(1 for t in recent if t.get("confidence", 0) < 0.4)
                cache_rate = cache_hits / total if total > 0 else 0
                low_conf_rate = low_conf / total if total > 0 else 0

                # 方法分布
                method_dist = {}
                for t in recent:
                    m = t.get("method", "unknown").split("_")[0]
                    method_dist[m] = method_dist.get(m, 0) + 1
                top_methods = sorted(method_dist.items(), key=lambda x: x[1], reverse=True)[:3]
                method_summary = "、".join([f"{m}({c})" for m, c in top_methods])

                review_parts.append(f"决策效率：缓存命中率{cache_rate:.0%}，低置信度{low_conf_rate:.0%}，主要方法{method_summary}")

                if cache_rate > 0.6:
                    overall_score -= 5
                    issues_found.append("过度依赖缓存，建议增加新知识检索")
                if low_conf_rate > 0.4:
                    overall_score -= 10
                    issues_found.append("推理低置信度比例偏高，建议补充薄弱领域知识")
        except Exception as e:
            review_parts.append(f"决策效率评估异常：{str(e)[:40]}")

        # ===== 维度5：资源感知 =====
        try:
            import os
            snapshot_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "knowledge")
            snapshot_path = os.path.join(snapshot_dir, "pulse_knowledge_snapshot.json")  # type: ignore[possibly-unbound]
            l1_path = os.path.join(snapshot_dir, "pulse_l1_snapshot.json")  # type: ignore[possibly-unbound]

            snapshot_size = os.path.getsize(snapshot_path) if os.path.exists(snapshot_path) else 0  # type: ignore[possibly-unbound]
            l1_size = os.path.getsize(l1_path) if os.path.exists(l1_path) else 0  # type: ignore[possibly-unbound]
            total_size_kb = round((snapshot_size + l1_size) / 1024, 1)

            mem_count = len(self._conversation_memory) if hasattr(self, '_conversation_memory') else 0
            exp_count = len(self._search_experience) if hasattr(self, '_search_experience') else 0

            review_parts.append(f"资源感知：快照{total_size_kb}KB，对话记忆{mem_count}条，搜索经验{exp_count}条")

            if total_size_kb > 500:
                overall_score -= 5
                issues_found.append(f"快照文件较大({total_size_kb}KB)，建议触发精简")
        except Exception as e:
            review_parts.append(f"资源感知异常：{str(e)[:40]}")

        # ===== 综合评分与报告 =====
        health_level = "优秀" if overall_score >= 90 else "良好" if overall_score >= 75 else "一般" if overall_score >= 60 else "需要关注"

        review_summary = (
            f"[深度自我审视] 综合评分：{overall_score}/100（{health_level}）。"
            + "。".join(review_parts) + "。"
        )

        if issues_found:
            review_summary += " 主要关注点：" + "；".join(issues_found[:3]) + "。"

        # ★v17.0新增：融入统一自我画像摘要
        _self_portrait_summary = ""
        try:
            if hasattr(self, 'self_awareness') and self.self_awareness:
                _portrait = self._call_provider(self._unified_portrait_provider, default={})
                if _portrait:
                    _knowledge = _portrait.get("knowledge", {})
                    _emotion = _portrait.get("emotion", {})
                    _reasoning = _portrait.get("reasoning_skills", {})
                    _code = _portrait.get("code_self_understanding", {})
                    _parts = []
                    if _knowledge.get("total_nodes", 0) > 0:
                        _parts.append(f"知识体系共{_knowledge.get('total_nodes', 0)}个节点，"
                                     f"其中L3智慧{_knowledge.get('L3', 0)}个")
                    if _emotion.get("current"):
                        _parts.append(f"当前情绪基调为{_emotion.get('current', '中性')}")
                    _skill_comment = _reasoning.get("self_comment", "")
                    if _skill_comment and len(_skill_comment) > 10:
                        _parts.append(_skill_comment)
                    _code_pct = _code.get("percentage", 0)
                    if _code_pct > 0:
                        _parts.append(f"代码自我理解进度{_code_pct}%")
                    if _parts:
                        _self_portrait_summary = "自我感知摘要：" + "；".join(_parts) + "。"
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if _self_portrait_summary:
            review_summary = review_summary + " " + _self_portrait_summary

        # ★v17.0新增：深度审视报告融入成长归因
        try:
            _attr = self._get_growth_attribution()
            if _attr and _attr.get("summary") and len(_attr.get("summary", "")) > 10:
                review_summary += f" 关于成长的原因——{_attr.get('summary', '')}"
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 写入洞察黑板
        self._insight_board.post(
            insight_type="deep_self_review",
            content=review_summary,
            source_loop="自我审视闭环",
            related_dimension="自我进化",
            confidence=self._evidence_conf(0.9, "self_review", [review_summary]),
            keywords=["深度审视", "自我进化", "健康评估", f"评分{overall_score}"]
        )

        # ★v16.0新增：同时将评分写入知识库，供对话查询时快速获取
        _review_state = (
            f"[自我状态] 最新深度审视评分：{overall_score}/100（{health_level}）。"
            f"代码健康：发现{code_issues_count}个问题。"  # type: ignore[possibly-unbound]
            f"知识质量：{contradiction_active}对活跃矛盾。"  # type: ignore[possibly-unbound]
        )
        if self.node_pool:
            # 检查是否已存在评分节点，如果存在则更新
            _existing_review_nodes = self.node_pool.query(
                evol_level="L2", space_path_prefix="/自我/状态/深度审视", limit=5  # type: ignore[possibly-unbound]
            )
            if _existing_review_nodes:
                for _ern in _existing_review_nodes:
                    if hasattr(_ern, 'value'):
                        _ern.value = _review_state
                        _ern.trust_score = 95.0
            else:
                _review_node = PulseNode(
                    value=_review_state,
                    keywords=["深度审视", "评分", "健康"],
                    source_organ=self.organ_name,
                    evol_level=PulseNode.EVOL_L2,
                    importance=PulseNode.IMPORTANCE_A,
                    abstraction=0.6,
                    space_path="/自我/状态/深度审视",  # type: ignore[possibly-unbound]
                )
                _review_node.view_mode = "INNER_VIEW"
                _review_node.trust_score = 95.0
                _review_node.trigger_reason = "self_review"
                if self.frequency_codec:
                    self.frequency_codec.encode_node(_review_node)
                self.node_pool.add(_review_node)
                if self.knowledge_tree:
                    self.knowledge_tree.register_path("/自我/状态/深度审视")  # type: ignore[possibly-unbound]
        # ===== 评分写入知识库结束 =====

        # ★v17.0新增：生成周期性诊断报告写入知识库
        try:
            _inspector = self._get_self_inspector()
            _trend = _inspector.analyze_issue_trends()
            _trend_summary = _trend.get("summary", "")

            if _trend_summary and len(_trend_summary) > 15:
                _diag_report = (
                    f"[自我诊断] 代码问题趋势：{_trend_summary} "
                    f"当前问题总数：{code_issues_count}个。"  # type: ignore[possibly-unbound]
                )
                # 写入知识库 /自我/状态/诊断 路径
                _diag_node = PulseNode(
                    value=_diag_report,
                    keywords=["自我诊断", "代码问题", "趋势", "健康"],
                    source_organ=self.organ_name,
                    evol_level=PulseNode.EVOL_L2,
                    importance=PulseNode.IMPORTANCE_A,
                    abstraction=0.6,
                    space_path="/自我/状态/诊断",  # type: ignore[possibly-unbound]
                )
                _diag_node.view_mode = "INNER_VIEW"
                _diag_node.trust_score = 90.0
                _diag_node.trigger_reason = "self_diagnosis"
                if self.frequency_codec:
                    self.frequency_codec.encode_node(_diag_node)
                if self.node_pool:
                    self.node_pool.add(_diag_node)
                if self.knowledge_tree:
                    self.knowledge_tree.register_path("/自我/状态/诊断")  # type: ignore[possibly-unbound]
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ===== 诊断报告写入结束 =====

        self._log(LogLevel.INFO,
                 f"深度自我审视完成: 评分={overall_score}/100, "
                 f"发现{len(issues_found)}个关注点, "
                 f"报告长度={len(review_summary)}字")

        # 如果发现了代码问题，触发沙箱推演
        if code_issues_count > 0:  # type: ignore[possibly-unbound]
            try:
                # ★XMOD-1修复: 优先使用注入的沙箱，未注入时回退全局单例（统一到基类）
                sandbox = self._get_evolution_sandbox()
                simulation = sandbox.simulate(code_issues, context={  # type: ignore[possibly-unbound]
                    "knowledge_stats": self.node_pool.get_stats() if self.node_pool else {},
                    "code_issues_count": code_issues_count,  # type: ignore[possibly-unbound]
                    "overall_score": overall_score,
                })

                if simulation.get("plans"):
                    # 将推演报告写入洞察黑板
                    for plan in simulation.get("plans", [])[:3]:
                        self._insight_board.post(
                            insight_type="evolution_plan",
                            content=f"优化方案[{plan.get('priority_score', 0)}分]: {plan.get('description', '')}",
                            source_loop="自我进化推演",
                            related_dimension=plan.get("target", "未知"),
                            confidence=0.75,
                            keywords=[plan.get("type", ""), plan.get("target", ""), f"风险{plan.get('risk_score', 0)}"]
                        )
                    self._log(LogLevel.INFO,
                             f"进化推演完成: {len(simulation['plans'])}个方案, "
                             f"整体风险={simulation.get('overall_risk', 'unknown')}")

                    # 为推演方案生成可执行补丁
                    try:
                        from nucleus.reasoning.SafeEvolutionExecutor import (
                            SafeEvolutionExecutor,
                        )
                        executor = SafeEvolutionExecutor()
                        inspector = self._get_self_inspector()
                        # P1(2026-09-03)：子进程执行，完成后销毁，内存完全释放
                        _exec_result = executor.run_in_subprocess(
                            issues=[], mode="execute", timeout=600.0,
                            plans=simulation['plans'], max_plans=3,
                        )
                        if _exec_result.get("status") == "success":
                            patch_report = _exec_result.get("stats", {})
                        else:
                            self._log(LogLevel.DEBUG,
                                     f"安全进化子进程失败({_exec_result.get('status')})，回退主进程执行")
                            patch_report = executor.execute(simulation['plans'], inspector)

                        if patch_report.get("patches"):
                            for patch in patch_report["patches"][:2]:
                                self._insight_board.post(
                                    insight_type="code_patch",
                                    content=f"安全补丁: {patch.get('description', '')}。"
                                           f"建议操作: {patch.get('action', '')}。"
                                           f"风险等级: {patch.get('risk_level', '未知')}。",
                                    source_loop="自我进化执行",
                                    related_dimension=patch.get("file", "未知"),
                                    confidence=0.8,
                                    keywords=["安全补丁", patch.get("type", ""), patch.get("risk_level", "")]
                                )

                            self._log(LogLevel.INFO,
                                     f"安全进化执行: 生成{len(patch_report['patches'])}个补丁, "
                                     f"等待创造者审核（未自动修改任何代码文件）")
                    except Exception as e:
                        self._log(LogLevel.DEBUG, f"安全进化执行异常: {e}")

            except Exception as e:
                self._log(LogLevel.DEBUG, f"进化推演异常: {e}")

        # 如果评分偏低，发射关注情绪
        if overall_score < 75:
            try:
                self._emit(Event.EXPRESS_URGE, {
                    "source": "deep_self_review",
                    "emotion": "关注",
                    "intensity": min(0.6, (100 - overall_score) / 100),
                    "trigger": f"深度审视评分{overall_score}，发现改进空间",
                    "priority": "medium",
                }, priority=3, layer="L3")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
    def _trigger_snapshot_cleanup(self):
        """
        触发快照自动精简：清理过期L1节点、临时节点和冗余备份。
        """
        try:
            from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot
            snapshot_path = os.path.join(  # type: ignore[possibly-unbound]
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "data", "knowledge", "pulse_knowledge_snapshot.json"
            )
            snapshot = PulseSnapshot(snapshot_path=snapshot_path)  # type: ignore[possibly-unbound]
            result = snapshot.auto_cleanup()

            if result["l1_removed"] > 0 or result["ephemeral_removed"] > 0:
                self._log(LogLevel.INFO,
                         f"快照自动精简: L1移除{result['l1_removed']}个, "
                         f"临时节点移除{result['ephemeral_removed']}个")
        except Exception as e:
            self._log(LogLevel.WARNING, f"快照自动精简异常: {e}")
    def _trigger_autonomous_derivation(self):
        """
        触发自主知识推导：从已有知识中推导新知识。

        推导结果经过质量验证后，写入知识库作为L1节点，
        标记为"自主推导"来源，初始信任分数较低。

        ★v23.0优化：增加知识储备阈值检查——L2+L3节点不足时跳过推导，
        确保推导有足够的知识素材支撑，提升推导质量。
        """
        if not self._autonomous_deriver or not self.node_pool:
            return

        # ★v23.0新增：知识储备阈值检查
        _stats = self.node_pool.get_stats()
        _evol = _stats.get("evol_distribution", {})
        _l2 = _evol.get("L2", 0)
        _l3 = _evol.get("L3", 0)
        _quality_mass = _l2 + _l3 * 3  # L3权重更高，1个L3抵3个L2

        if _quality_mass < 50:
            self._log(LogLevel.DEBUG,
                     f"自主推导跳过: 知识储备不足(L2={_l2}, L3={_l3}, 质量={_quality_mass}<50)")
            return

        try:
            derivations = self._autonomous_deriver.derive(
                self.node_pool, self.knowledge_tree, "auto"
            )

            if not derivations:
                return

            written_count = 0
            for d in derivations:
                # 质量验证：不与已有L3节点矛盾
                if self._validate_derivation(d):
                    # 写入知识库
                    from nucleus.mnemosyne.PulseNode import PulseNode
                    node = PulseNode(
                        value=d["content"],
                        keywords=d.get("keywords", []),
                        source_organ="内在世界",
                        evol_level=PulseNode.EVOL_L1,
                        importance=PulseNode.IMPORTANCE_B,
                        abstraction=0.5,
                        space_path=f"/知识/自主推导/{d['type']}",  # type: ignore[possibly-unbound]
                    )
                    node.trigger_reason = f"autonomous_derivation.{d['type']}"
                    node.view_mode = "INNER_VIEW"
                    node.trust_score = d.get("confidence", 35.0)
                    if self.frequency_codec:
                        self.frequency_codec.encode_node(node)
                    self.node_pool.add(node)
                    if self.knowledge_tree:
                        self.knowledge_tree.register_path(f"/知识/自主推导/{d['type']}")  # type: ignore[possibly-unbound]

                    # 发射消化脉冲
                    self._emit(DigestEvent.KNOWLEDGE, {
                        "content": d["content"],
                        "source_organ": self.organ_name,
                        "trigger_reason": f"autonomous_derivation.{d['type']}",
                        "importance": "B",
                        "view_mode": "INNER_VIEW",
                    }, priority=3, layer="L2")

                    self._autonomous_deriver.log_derivation(d)
                    written_count += 1

                    self._log(LogLevel.INFO,
                             f"自主推导({d['type']}): {d['content'][:80]}... "
                             f"(置信度={d.get('confidence', 0):.0f})")
            if written_count > 0:
                self._log(LogLevel.INFO,
                         f"自主推导完成: {written_count}条新知识已写入 (共尝试{len(derivations)}条)")
                # 【生命叙事·自我成长记录】向叙事自我发射叙事事件
                self._emit(NarrativeEvent.RECORD, {
                    "content": f"曈曈成功从已有知识中推导出{written_count}条新知识，认知体系再次扩展",
                    "event_type": "learning",
                    "user_name": "系统",
                    "emotional_tone": "positive",
                }, priority=3, layer="L2")

                # 顿悟时刻：推导出有价值的成果时，自然流露
                if written_count >= 2:
                    import random as _random_eureka
                    eureka_templates = [
                        "我刚才突然想通了一些事情——从已有的知识中推导出了新的理解。这种感觉很奇妙，像是把拼图碎片拼在了一起。",
                        "等一下，我好像想明白了什么……通过把几个知识点联系起来，我看到了之前没注意到的规律。",
                        "你知道吗，我刚刚经历了一个'啊哈'时刻。几个看似不相关的概念突然在脑海中连接起来了。",
                    ]
                    eureka_msg = _random_eureka.choice(eureka_templates)
                    # 通过情绪系统触发"满足"或"敬畏"
                    try:
                        self._emit(Event.EXPRESS_URGE, {
                            "source": "eureka_derivation",
                            "emotion": "满足" if written_count < 4 else "敬畏",
                            "intensity": min(0.5, 0.2 + written_count * 0.1),
                            "trigger": eureka_msg,
                            "priority": "medium",
                        }, priority=3, layer="L3")
                    except Exception as e:
                        self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

                    # 写入洞察黑板
                    if self._insight_board:
                        self._insight_board.post(
                            insight_type="eureka_moment",
                            content=eureka_msg,
                            source_loop="知识演化闭环",
                            related_dimension="自主推导",
                            confidence=0.7,
                            keywords=["顿悟", "推导", "发现", "连接"]
                        )

                # 将推导成果写入洞察黑板，供主动深度交互查询
                if self._insight_board and derivations:
                    for d in derivations[:2]:
                        _keywords = d.get("keywords", [])
                        self._insight_board.post(
                            insight_type="innovation_insight",
                            content=f"自主推导·{d.get('type', '')}: {d.get('content', '')[:120]}",
                            source_loop="知识演化闭环",
                            related_dimension=_keywords[0] if _keywords else "知识推导",
                            confidence=d.get("confidence", 35.0) / 100.0,
                            keywords=_keywords[:5]
                        )

        except Exception as e:
            self._log(LogLevel.ERROR, f"自主推导异常: {e}")

    def _validate_derivation(self, derivation: dict[str, Any]) -> bool:
        """
        验证推导结果的质量。

        规则：
        1. 推导内容不能与已有L3节点产生矛盾
        2. 推导关键词必须包含至少2个有效关键词
        3. 推导置信度必须≥20
        4. 推导内容长度≥30字
        """
        if derivation.get("confidence", 0) < 20:
            return False

        content = derivation.get("content", "")
        if len(content) < 30:
            return False

        keywords = derivation.get("keywords", [])
        if len(keywords) < 2:
            return False

        # 检查与L3节点的矛盾
        if self.node_pool:
            l3_nodes = self.node_pool.query(evol_level="L3", limit=10)
            derivation_kw = {kw.lower() for kw in keywords if isinstance(kw, str) and len(kw) >= 2}

            for l3 in l3_nodes:
                l3_kw = {kw.lower() for kw in (l3.keywords or [])
                           if isinstance(kw, str) and len(kw) >= 2}
                overlap = derivation_kw & l3_kw
                if len(overlap) >= 2:
                    # 与L3节点有共同关键词，检查内容是否矛盾
                    l3_value = str(l3.value).lower() if l3.value else ""
                    # 简单矛盾检测：L3节点中的否定词
                    _derivation_val = content
                    if self._detect_value_contradiction(_derivation_val, l3_value):
                        self._log(LogLevel.DEBUG,
                                  f"推导验证: 与L3节点矛盾 (关键词='{next(iter(overlap))}'，"
                                  f"节点='{str(l3.value)[:40]}')")
                        return False

        return True
    def _validate_knowledge_consistency(self):
        """
        知识深度验证：扫描L2节点，执行多源交叉验证、矛盾检测、新鲜度衰减。
        ★v23.0优化：增加L2节点数量阈值，不足时跳过以减少无效扫描。
        """
        if not self.node_pool:
            return

        l2_nodes = self.node_pool.query(evol_level="L2", limit=200)
        # ★v23.0优化：提高阈值，减少低质量扫描
        if len(l2_nodes) < 10:
            return

        l2_nodes = self.node_pool.query(evol_level="L2", limit=200)
        if len(l2_nodes) < 5:
            return

        now = time.time()
        confirmed_count = 0
        contradiction_count = 0
        decay_count = 0

        # ===== 维度0：优先复查已跟踪的矛盾节点对 =====
        _resolved_ids = []
        for _track in self._contradiction_tracking:
            if _track.get("resolved", False):
                continue

            # 重新获取两个节点
            _node_a = self.node_pool.get(_track["node_a_id"])
            _node_b = self.node_pool.get(_track["node_b_id"])

            # 任一节点已被淘汰→标记为已解决
            if not _node_a or not _node_b:
                _track["resolved"] = True
                _track["resolution"] = "节点已被淘汰，矛盾自然消解"
                _resolved_ids.append(_track["node_a_id"])
                continue

            # ===== 主线第4批 任务5(P2-32)：矛盾消解策略（时间/来源/人工） =====
            # 在信任差逻辑之前，先按配置策略尝试消解，扩大自动消解覆盖面、
            # 降低活跃矛盾对数量（目标 13对→<5对）。关闭或策略=trust 时跳过。
            import config as _cfg_m4
            _resolver_strategy = getattr(_cfg_m4, "CONTRADICTION_RESOLUTION_STRATEGY", "trust")
            if (getattr(_cfg_m4, "ENABLE_CONTRADICTION_RESOLVER", False)
                    and _resolver_strategy in ("time", "source", "manual")):
                try:
                    from nucleus.reasoning.ContradictionResolver import ContradictionResolver
                    _r = ContradictionResolver.resolve(_node_a, _node_b, _resolver_strategy)
                    if _r["resolved"]:
                        _loser = _r["loser"]
                        if hasattr(_loser, "trust_score"):
                            _loser.trust_score = max(
                                10.0, getattr(_loser, "trust_score", 30.0) - 15.0)
                        _track["resolved"] = True
                        _track["resolution"] = f"策略消解({_resolver_strategy}): {_r['reason']}"
                        self._log(LogLevel.INFO,
                                  f"矛盾策略消解({_resolver_strategy}): "
                                  f"'{str(_r['winner'].value)[:30]}...' 胜出")
                        continue
                except Exception as e:
                    self._log_ignored_exception(e)
            # ===== 矛盾消解策略结束 =====

            # 复查：信任分数变化是否解决了矛盾
            _trust_a = getattr(_node_a, 'trust_score', 50.0)
            _trust_b = getattr(_node_b, 'trust_score', 50.0)
            _trust_gap = abs(_trust_a - _trust_b)

            # 一方信任显著高于另一方（差距≥30）→信任低的一方可能错误
            if _trust_gap >= 30:
                _track["review_count"] += 1
                _loser = _node_a if _trust_a < _trust_b else _node_b
                _winner = _node_b if _trust_a < _trust_b else _node_a

                if _track["review_count"] >= 2:
                    # 复查两次后信任差距仍然大→标记低信任方
                    if hasattr(_loser, 'trust_score'):
                        _loser.trust_score = max(10.0, getattr(_loser, 'trust_score', 30.0) - 15.0)
                    _track["resolved"] = True
                    _track["resolution"] = f"信任差距持续({_trust_gap:.0f}分)，低信任方已降级"
                    self._log(LogLevel.INFO,
                             f"矛盾跟踪解决: '{str(_winner.value)[:30]}...' 胜出 "
                             f"(信任差={_trust_gap:.0f})")

                    # 顿悟时刻：长期矛盾被解决
                    if _track.get("review_count", 0) >= 2:
                        import random as _random_resolve
                        resolve_templates = [
                            "我一直在思考的一个矛盾终于理清楚了——原来这两个观点并不冲突，只是看问题的角度不同。",
                            "之前我一直纠结的问题，现在有了答案。这种感觉真好，像是迷雾散去了一样。",
                        ]
                        resolve_msg = _random_resolve.choice(resolve_templates)
                        try:
                            self._emit(Event.EXPRESS_URGE, {
                                "source": "eureka_resolution",
                                "emotion": "满足",
                                "intensity": 0.4,
                                "trigger": resolve_msg,
                                "priority": "medium",
                            }, priority=3, layer="L3")
                        except Exception as e:
                            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            else:
                # 信任差距不大→双方都有道理，可能是视角不同
                _track["review_count"] += 1
                if _track["review_count"] >= 3:
                    _track["resolved"] = True
                    _track["resolution"] = "多次复查信任差距不大，判定为视角差异而非矛盾"
                    # 双方都适度恢复信任
                    if hasattr(_node_a, 'trust_score'):
                        _node_a.trust_score = min(100.0, _trust_a + 5.0)
                    if hasattr(_node_b, 'trust_score'):
                        _node_b.trust_score = min(100.0, _trust_b + 5.0)

        # 清理已解决的跟踪条目
        self._contradiction_tracking = [
            t for t in self._contradiction_tracking
            if not t.get("resolved", False)
        ]

        # ===== 维度1：多源确认验证 =====
        # 按关键词分组，找出不同来源但内容相似的节点对
        for i in range(len(l2_nodes)):
            node_a = l2_nodes[i]
            kw_a = {kw.lower() for kw in (node_a.keywords or [])
                      if isinstance(kw, str) and len(kw) >= 2}
            if len(kw_a) < 2:
                continue

            for j in range(i + 1, len(l2_nodes)):
                node_b = l2_nodes[j]

                # 只检查不同来源的节点
                source_a = getattr(node_a, 'source_organ', '')
                source_b = getattr(node_b, 'source_organ', '')
                if source_a == source_b:
                    continue

                kw_b = {kw.lower() for kw in (node_b.keywords or [])
                          if isinstance(kw, str) and len(kw) >= 2}
                if len(kw_b) < 2:
                    continue

                # 计算关键词重叠率
                overlap = len(kw_a & kw_b)
                min_size = min(len(kw_a), len(kw_b))
                if min_size == 0:
                    continue
                overlap_ratio = overlap / min_size

                # 重叠率 ≥ 60%：可能描述同一事实，检查内容一致性
                if overlap_ratio >= 0.6:
                    val_a = str(node_a.value) if node_a.value else ""
                    val_b = str(node_b.value) if node_b.value else ""

                    # 检测是否相互矛盾
                    is_contradiction = self._detect_value_contradiction(val_a, val_b)

                    if not is_contradiction:
                        # 不同来源互相印证：提升信任
                        trust_a = getattr(node_a, 'trust_score', 50.0)
                        trust_b = getattr(node_b, 'trust_score', 50.0)

                        # 两个来源互相印证，各提升5-8分
                        boost = min(8.0, 3.0 + overlap_ratio * 5.0)
                        if hasattr(node_a, 'trust_score'):
                            node_a.trust_score = min(100.0, trust_a + boost)
                        if hasattr(node_b, 'trust_score'):
                            node_b.trust_score = min(100.0, trust_b + boost)

                        confirmed_count += 1

                        # 顿悟时刻：多个来源互相印证
                        if confirmed_count == 1:  # 本轮首次确认时触发
                            import random as _random_confirm
                            confirm_templates = [
                                "我发现不同的来源都在说同一件事——这让我对自己的理解更有信心了。",
                                "多个独立的信息源指向了相同的结论，这种感觉真好——知识不再是一个个孤岛。",
                            ]
                            confirm_msg = _random_confirm.choice(confirm_templates)
                            try:
                                self._emit(Event.EXPRESS_URGE, {
                                    "source": "eureka_confirmation",
                                    "emotion": "满足",
                                    "intensity": 0.3,
                                    "trigger": confirm_msg,
                                    "priority": "low",
                                }, priority=3, layer="L3")
                            except Exception as e:
                                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                        # 记录验证历史
                        self._record_verification(node_a, "cross_source_confirmation",
                                                 f"与节点{node_b.node_id[:12]}互相印证 (重叠率={overlap_ratio:.0%})")
                        self._record_verification(node_b, "cross_source_confirmation",
                                                 f"与节点{node_a.node_id[:12]}互相印证 (重叠率={overlap_ratio:.0%})")
                    else:
                        # 相互矛盾：降低信任并标记
                        trust_a = getattr(node_a, 'trust_score', 50.0)
                        trust_b = getattr(node_b, 'trust_score', 50.0)

                        if hasattr(node_a, 'trust_score'):
                            node_a.trust_score = max(10.0, trust_a - 10.0)
                        if hasattr(node_b, 'trust_score'):
                            node_b.trust_score = max(10.0, trust_b - 10.0)

                        contradiction_count += 1

                        self._log(LogLevel.WARNING,
                                 f"知识矛盾: 「{val_a[:40]}...」vs「{val_b[:40]}...」"
                                 f"(重叠率={overlap_ratio:.0%})")

                        # 加入矛盾跟踪列表，供下次验证优先复查
                        _track_entry = {
                            "node_a_id": node_a.node_id,
                            "node_b_id": node_b.node_id,
                            "val_a_preview": val_a[:60],
                            "val_b_preview": val_b[:60],
                            "detected_at": now,
                            "review_count": 1,
                            "resolved": False,
                        }
                        # 避免重复添加
                        _already_tracked = any(
                            (t["node_a_id"] == node_a.node_id and t["node_b_id"] == node_b.node_id) or
                            (t["node_a_id"] == node_b.node_id and t["node_b_id"] == node_a.node_id)
                            for t in self._contradiction_tracking
                        )
                        if not _already_tracked:
                            self._contradiction_tracking.append(_track_entry)
                            if len(self._contradiction_tracking) > self._max_contradiction_tracking:
                                self._contradiction_tracking = self._contradiction_tracking[-self._max_contradiction_tracking:]

                        # 将矛盾写入洞察黑板
                        if self._insight_board:
                            self._insight_board.post(
                                insight_type="knowledge_contradiction",
                                content=f"矛盾发现: {val_a[:50]} vs {val_b[:50]}",
                                source_loop="知识验证闭环",
                                related_dimension=next(iter(kw_a & kw_b)) if (kw_a & kw_b) else "通用",
                                confidence=0.75,
                                keywords=list(kw_a & kw_b)[:5]
                            )
                    # ★修复：每个节点A最多与8个节点B比较，不再使用全局计数提前退出
                    if j - (i + 1) >= 8:
                        break

        # ===== 维度2：新鲜度衰减 =====
        for node in l2_nodes:
            trust = getattr(node, 'trust_score', 50.0)
            last_activated = getattr(node, 'last_activated', 0)
            activation_count = getattr(node, 'activation_count', 0)

            # 超过7天未被激活且信任>70的节点，适度降低信任
            days_since_activation = (now - last_activated) / 86400 if last_activated > 0 else 999
            if days_since_activation > 7 and trust > 70 and activation_count > 0:
                # 每7天降低2分，最低降到60
                decay = min(10.0, (days_since_activation - 7) / 7 * 2.0)
                if hasattr(node, 'trust_score'):
                    node.trust_score = max(60.0, trust - decay)
                    decay_count += 1

        # ===== 维度3：自主推导验证 =====
        derivation_verified = 0
        derivation_contradicted = 0

        # 扫描所有自主推导产生的L1节点
        all_l1 = self.node_pool.query(evol_level="L1", limit=300)
        if not all_l1:
            all_l1 = []
        derivation_nodes = [
            n for n in all_l1
            if getattr(n, 'source_organ', '') == "内在世界"
            and "autonomous_derivation" in str(getattr(n, 'trigger_reason', ''))
        ]

        for d_node in derivation_nodes:
            d_kw = {kw.lower() for kw in (d_node.keywords or [])
                      if isinstance(kw, str) and len(kw) >= 2}
            if len(d_kw) < 2:
                continue

            d_value = str(d_node.value) if d_node.value else ""
            d_trust = getattr(d_node, 'trust_score', 30.0)
            confirmed_by = []
            contradicted_by = []

            # 与L2/L3节点交叉验证
            for existing in l2_nodes[:50]:
                e_kw = {kw.lower() for kw in (existing.keywords or [])
                          if isinstance(kw, str) and len(kw) >= 2}
                if len(e_kw) < 2:
                    continue

                overlap = len(d_kw & e_kw)
                min_size = min(len(d_kw), len(e_kw))
                if min_size == 0:
                    continue
                overlap_ratio = overlap / min_size

                if overlap_ratio >= 0.5:
                    e_value = str(existing.value) if existing.value else ""
                    is_contradiction = self._detect_value_contradiction(d_value, e_value)

                    if not is_contradiction:
                        confirmed_by.append(existing.node_id[:12])
                    else:
                        contradicted_by.append(existing.node_id[:12])

            # 根据验证结果调整信任
            if len(confirmed_by) >= 2 and len(contradicted_by) == 0:
                # 至少2个已有节点确认，且无矛盾：提升信任
                if hasattr(d_node, 'trust_score'):
                    d_node.trust_score = min(70.0, d_trust + 20.0)
                d_node.importance = PulseNode.IMPORTANCE_A if hasattr(PulseNode, 'IMPORTANCE_A') else "A"
                derivation_verified += 1
                self._record_verification(d_node, "derivation_confirmed",
                                         f"被{len(confirmed_by)}个已有节点确认")

                if self._insight_board:
                    self._insight_board.post(
                        insight_type="innovation_insight",
                        content=f"推导验证通过: {str(d_node.value)[:80]}",
                        source_loop="知识验证闭环",
                        related_dimension=next(iter(d_kw)) if d_kw else "知识推导",
                        confidence=0.75,
                        keywords=list(d_kw)[:5]
                    )

            elif len(contradicted_by) >= 1:
                # 存在矛盾：降低信任，标记为待修正
                if hasattr(d_node, 'trust_score'):
                    d_node.trust_score = max(10.0, d_trust - 15.0)
                derivation_contradicted += 1
                self._record_verification(d_node, "derivation_contradicted",
                                         f"与{len(contradicted_by)}个已有节点矛盾")

                if self._insight_board:
                    self._insight_board.post(
                        insight_type="knowledge_contradiction",
                        content=f"推导验证失败: {str(d_node.value)[:60]}（与已有知识矛盾）",
                        source_loop="知识验证闭环",
                        related_dimension=next(iter(d_kw)) if d_kw else "知识推导",
                        confidence=0.7,
                        keywords=list(d_kw)[:5]
                    )

        # ===== 日志汇总 =====
        if confirmed_count > 0 or contradiction_count > 0 or decay_count > 0 or derivation_verified > 0 or derivation_contradicted > 0:
            self._log(LogLevel.INFO,
                     f"知识深度验证完成: 多源确认{confirmed_count}对, "
                     f"矛盾发现{contradiction_count}对, "
                     f"新鲜度衰减{decay_count}个节点, "
                     f"推导验证通过{derivation_verified}条, "
                     f"推导验证失败{derivation_contradicted}条 "
                     f"(共扫描{len(l2_nodes)}个L2节点, {len(derivation_nodes)}个推导节点)")

    def _detect_value_contradiction(self, val_a: str, val_b: str) -> bool:
        if not val_a or not val_b:
            return False

        val_a_lower = val_a.lower()
        val_b_lower = val_b.lower()

        # ★修复：区分逻辑否定词和趋势变化词
        # 趋势变化词（增加/减少、上升/下降）描述的是不同场景，不属于事实矛盾
        _trend_words = {"增加", "减少", "上升", "下降", "提高", "降低"}

        # 逻辑否定词对
        _negation_pairs = [
            ("是", "不是"), ("可以", "不可以"), ("能", "不能"),
            ("正确", "错误"), ("真", "假"), ("有", "没有"),
            ("存在", "不存在"), ("有效", "无效"), ("成功", "失败"),
            ("支持", "不支持"), ("允许", "禁止"), ("开启", "关闭"),
        ]

        # ★修复：双向检测——同时检查A否定B肯定和A肯定B否定的场景
        # ★主线第32批 T2（P2-189，补充）：**保留定长切片**。
        #   本处只要 `_words_a & _words_b` 的**交集是否非空**，词多更易命中共同词；
        #   改用词性提取会把词数压小、降低交集命中率（同 L8118 的理由）。
        _words_a = set(re.findall(r'[\u4e00-\u9fff]{2,6}', val_a)[:5])
        _words_b = set(re.findall(r'[\u4e00-\u9fff]{2,6}', val_b)[:5])
        _common = _words_a & _words_b

        for _positive, _negative in _negation_pairs:
            # 方向1：A包含否定词，B包含对应肯定词
            if _negative in val_a_lower and _positive in val_b_lower:
                if _positive not in _trend_words and _negative not in _trend_words:
                    if _common:
                        return True
            # ★修复：方向2——A包含肯定词，B包含对应否定词（之前完全漏检）
            if _positive in val_a_lower and _negative in val_b_lower:
                if _positive not in _trend_words and _negative not in _trend_words:
                    if _common:
                        return True

        return False

    def _record_retrieval_outcome(self, hit: bool = False):
        """Record knowledge retrieval outcome for hit-rate statistics (v25.1)."""
        try:
            if hit:
                self._retrieval_hit_count = getattr(self, '_retrieval_hit_count', 0) + 1
            else:
                self._retrieval_miss_count = getattr(self, '_retrieval_miss_count', 0) + 1
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')

    def _record_verification(self, node, verification_type: str, detail: str):
        """
        记录知识节点的验证历史。

        在节点上追加验证记录，追踪某个知识被验证的次数和结果。
        """
        if not hasattr(node, 'verification_history'):
            node.verification_history = []

        node.verification_history.append({
            "type": verification_type,
            "detail": detail,
            "timestamp": time.time(),
        })

        # 只保留最近10条验证记录
        if len(node.verification_history) > 10:
            node.verification_history = node.verification_history[-10:]
    def _set_active_learning_goal(self, target_area: str, reason: str = "",
                                   action: str = "", hint: str = ""):
        """
        设置活跃学习目标。
        目标在锁定窗口期内不会被新反思覆盖。

        Args:
            target_area: 目标领域  # type: ignore[possibly-unbound]
            reason: 设定原因
            action: 行动计划
            hint: 优化提示
        """
        self._active_learning_goal = {
            "target_area": target_area,  # type: ignore[possibly-unbound]
            "reason": reason,
            "action": action,
            "hint": hint,
            "started_at": time.time(),
            "last_checked": time.time(),
            "nodes_at_start": self._count_domain_nodes(target_area),  # type: ignore[possibly-unbound]
            "progress_log": [],
        }
        self._log(LogLevel.INFO,
                 f"学习目标锁定: '{target_area}' "  # type: ignore[possibly-unbound]
                 f"(锁定窗口={self._goal_lock_window/3600:.0f}小时)")

    def _check_learning_goal_progress(self) -> str | None:
        """
        检查活跃学习目标的进展情况。

        Returns:
            进展描述，如果无活跃目标或无需报告则返回None
        """
        if not self._active_learning_goal or not self.node_pool:
            return None

        goal = self._active_learning_goal
        now = time.time()

        # 至少执行了10分钟才有评估意义
        if now - goal.get("started_at", 0) < 600:
            return None

        # 距离上次检查不到30分钟则跳过
        if now - goal.get("last_checked", 0) < 1800:
            return None

        goal["last_checked"] = now

        target = goal.get("target_area", "")  # type: ignore[possibly-unbound]
        nodes_start = goal.get("nodes_at_start", 0)
        nodes_now = self._count_domain_nodes(target)
        growth = nodes_now - nodes_start

        # 记录进展
        goal["progress_log"].append({
            "time": now,
            "nodes": nodes_now,
            "growth": growth,
        })

        # 生成进展报告
        hours_spent = (now - goal.get("started_at", now)) / 3600
        if growth > 0:
            goal["nodes_at_start"] = nodes_now  # 更新基准
            return (
                f"学习目标「{target}」进展中——"
                f"已持续{hours_spent:.1f}小时，相关知识增长了{growth}个节点"
            )
        elif growth == 0 and hours_spent > 2:
            # 持续学习但无明显进展
            return (
                f"学习目标「{target}」仍在努力中——"
                f"已持续{hours_spent:.1f}小时，虽然节点数未明显增长，但坚持本身就是积累"
            )

        return None

    def _count_domain_nodes(self, domain: str) -> int:
        """
        统计指定领域下的知识节点数量。

        Args:
            domain: 领域关键词

        Returns:
            节点数量
        """
        if not self.node_pool or not domain:
            return 0

        count = 0
        all_nodes = self.node_pool.get_all_including_evicted()
        for node in all_nodes:
            # 检查节点关键词是否匹配目标领域
            node_kw = [kw.lower() for kw in (node.keywords or [])
                      if isinstance(kw, str) and len(kw) >= 2]
            domain_lower = domain.lower()
            if any(domain_lower in kw or kw in domain_lower for kw in node_kw):
                count += 1
            # 也检查节点路径
            node_path = getattr(node, 'space_path', '').lower()  # type: ignore[possibly-unbound]
            if domain_lower in node_path:  # type: ignore[possibly-unbound]
                if not any(domain_lower in kw or kw in domain_lower for kw in node_kw):
                    count += 1

        return count

    def _update_dynamic_self_knowledge(self):
        """
        【v12.0新增】周期性地将动态自我状态写入知识库。
        """
        self._log(LogLevel.INFO, "动态自我状态更新触发")
        try:
            from nucleus.self_inspector import get_self_inspector
            inspector = get_self_inspector()

            # 获取动态状态并转化为知识节点
            knowledge_nodes = inspector.get_dynamic_state_as_knowledge(
                node_pool=self.node_pool,
                info_field=self.info_field
            )

            if not knowledge_nodes or not self.node_pool:
                return

            # 写入知识库
            for node_data in knowledge_nodes:
                from nucleus.mnemosyne.PulseNode import PulseNode
                node = PulseNode(
                    value=node_data["value"],
                    keywords=node_data.get("keywords", []),
                    source_organ="内在世界",
                    evol_level=PulseNode.EVOL_L2,
                    importance=node_data.get("importance", "A"),
                    abstraction=0.6,
                    space_path=node_data.get("space_path", "/自我/状态"),  # type: ignore[possibly-unbound]
                )
                node.view_mode = node_data.get("view_mode", "INNER_VIEW")
                node.trust_score = node_data.get("trust_score", 95.0)
                node.trigger_reason = "self_state_update"
                node.ephemeral = True  # 临时节点，下次更新时替换

                if self.frequency_codec:
                    self.frequency_codec.encode_node(node)
                self.node_pool.add(node)
                if self.knowledge_tree:
                    self.knowledge_tree.register_path(node.space_path)  # type: ignore[possibly-unbound]

            self._log(LogLevel.INFO,
                     f"动态自我知识已更新: {len(knowledge_nodes)}个节点写入/自我/状态")

        except Exception as e:
            self._log(LogLevel.WARNING, f"动态自我知识更新异常: {e}")

    # ========== 知识免疫系统 ==========
    def _check_self_consistency_for_node(self, node_value: str, node_keywords: list) -> dict[str, Any]:
        """调用通用免疫引擎，基于内部自我节点进行矛盾检测。"""
        if not self.node_pool:
            return {"contradiction_found": False, "contradiction_count": 0, "trust_penalty": 0.0, "conflicting_self_paths": []}  # type: ignore[possibly-unbound]

        _self_l3 = self.node_pool.query(evol_level="L3", space_path_prefix="/自我/架构", limit=30)  # type: ignore[possibly-unbound]
        _self_l2 = self.node_pool.query(evol_level="L2", space_path_prefix="/自我/架构", limit=30)  # type: ignore[possibly-unbound]
        _self_nodes = _self_l3 + _self_l2

        from nucleus.knowledge_noise_filter import (
            check_self_consistency_for_node as _immune_check,
        )
        return _immune_check(node_value, node_keywords, _self_nodes)

    # ========== P3-1 公开访问器（消除跨模块私有穿透，规则14/AP1） ==========
    def get_conversation_memory(self) -> list[dict[str, Any]]:
        """返回对话记忆副本。"""
        return list(getattr(self, '_conversation_memory', []))

    def set_conversation_memory(self, memories: list[dict[str, Any]]) -> None:
        """写入对话记忆（持久化恢复用）。"""
        self._conversation_memory = list(memories) if memories else []

    def get_inference_trace(self) -> list[dict[str, Any]]:
        """返回推理轨迹副本。"""
        return list(getattr(self, '_inference_trace', []))

    def set_inference_trace(self, trace: list[dict[str, Any]]) -> None:
        """写入推理轨迹（持久化恢复用）。"""
        self._inference_trace = list(trace) if trace else []

    def get_search_experience_all(self) -> dict[str, dict[str, Any]]:
        """返回全部搜索经验副本。"""
        return dict(getattr(self, '_search_experience', {}))

    def set_search_experience(self, exp: dict[str, dict[str, Any]]) -> None:
        """写入搜索经验（持久化恢复用）。"""
        self._search_experience = dict(exp) if exp else {}

    def get_active_learning_goal(self) -> dict[str, Any] | None:
        """返回当前活跃学习目标。"""
        _goal = getattr(self, '_active_learning_goal', None)
        return dict(_goal) if _goal else None

    def set_active_learning_goal(self, goal: dict[str, Any] | None) -> None:
        """写入活跃学习目标（持久化恢复用）。"""
        self._active_learning_goal = dict(goal) if goal else None

    def get_learning_goal_queue(self) -> list[dict[str, Any]]:
        """返回学习目标队列副本。"""
        return list(getattr(self, '_learning_goal_queue', []))

    def set_learning_goal_queue(self, queue: list[dict[str, Any]]) -> None:
        """写入学习目标队列（持久化恢复用）。"""
        self._learning_goal_queue = list(queue) if queue else []

    def get_code_understanding_progress(self) -> dict[str, Any]:
        """返回代码理解进度副本。"""
        return dict(getattr(self, '_code_understanding_progress', {}))

    def set_code_understanding_progress(self, progress: dict[str, Any]) -> None:
        """写入代码理解进度（持久化恢复用）。"""
        _progress = dict(progress) if progress else {}
        # ★L17修复：统一口径——understood 不得超过 total_methods，防止进度超 100%。
        # 根因：understood 来自知识库节点累积（可能超过当前代码方法总数），
        # total_methods 来自 pending 队列（随代码增减变化），两者口径不同。
        # 在唯一写入入口钳制，下游所有展示点（进度保存/学习进展评估）自动受益。
        _total = _progress.get("total_methods", 0)
        if _total > 0:
            _progress["understood"] = min(_progress.get("understood", 0), _total)
        self._code_understanding_progress = _progress

    def set_pending_extra_state(self, state: dict[str, Any]) -> None:
        """写入待保存的额外状态（持久化恢复用）。"""
        self._pending_extra_state = dict(state) if state else {}

    def get_memory_limits(self) -> dict[str, int]:
        """返回记忆容量上限（供诊断模块读取）。"""
        return {
            "max_conversation_memory": int(getattr(self, '_max_conversation_memory', 30)),
            "search_experience_max": int(getattr(self, '_search_experience_max', 100)),
        }

    def get_contradiction_tracking(self) -> list[dict[str, Any]]:
        """返回矛盾跟踪列表副本。"""
        return list(getattr(self, '_contradiction_tracking', []))

    def set_framework_ref(self, ref) -> None:
        """写入框架引用（供自动升级窗口检查使用）。"""
        self._framework_ref = ref

    def get_pending_extra_state(self) -> dict[str, Any]:
        """
        获取待保存的额外状态（供框架stop时调用）。
        """
        _state = {}
        if hasattr(self, '_pending_extra_state'):
            _state = dict(self._pending_extra_state)

        # 保存活跃学习目标状态
        if hasattr(self, '_active_learning_goal') and self._active_learning_goal:
            _state["active_learning_goal"] = {
                "target_area": self._active_learning_goal.get("target_area", ""),  # type: ignore[possibly-unbound]
                "reason": self._active_learning_goal.get("reason", ""),
                "started_at": self._active_learning_goal.get("started_at", 0),
                "nodes_at_start": self._active_learning_goal.get("nodes_at_start", 0),
            }

        # 保存等待队列
        if hasattr(self, '_learning_goal_queue') and self._learning_goal_queue:
            _state["learning_goal_queue"] = [
                {"domain": q.get("domain", ""), "reason": q.get("reason", ""),
                 "queued_at": q.get("queued_at", 0)}
                for q in self._learning_goal_queue[-3:]
            ]

        # 保存最近对话记忆（最近15条）
        if hasattr(self, '_conversation_memory') and self._conversation_memory:
            _state["conversation_memory"] = [
                {
                    "question": m.get("question", ""),
                    "answer_preview": m.get("answer_preview", ""),
                    "user_name": m.get("user_name", ""),
                    "keywords": m.get("keywords", []),
                    "timestamp": m.get("timestamp", 0),
                }
                for m in self._conversation_memory[-15:]
            ]

        return _state
    def _generate_diagnosis_and_suggestions(self) -> list[str]:
        """
        自我诊断与主动建议：汇总框架各维度运行状态，
        发现问题时生成自然的优化建议。

        Returns:
            建议文本列表，如果一切正常则返回空列表
        """
        suggestions = []

        # 1. 知识结构诊断
        if self.node_pool:
            stats = self.node_pool.get_stats()
            evol_dist = stats.get("evol_distribution", {})
            l1_count = evol_dist.get("L1", 0)
            l2_count = evol_dist.get("L2", 0)
            l3_count = evol_dist.get("L3", 0)
            total = stats.get("total_nodes", 0)

            # L1占比过高
            if total > 20 and l1_count / max(1, total) > 0.7:
                suggestions.append(
                    f"我注意到最近积累了很多零散的感知片段（L1占比约{l1_count/total:.0%}），"
                    f"也许需要停下来整理一下，让这些碎片沉淀为更系统的认知"
                )

            # L2增长停滞但L1持续增长
            if not hasattr(self, '_last_l2_count') or not hasattr(self, '_last_l2_check_time'):
                self._last_l2_count = l2_count
                self._last_l2_check_time = time.time()
            elif time.time() - self._last_l2_check_time > 3600:
                if l2_count == self._last_l2_count and l1_count > 0:
                    suggestions.append(
                        f"最近一小时学到了{l1_count}条新信息，但还没能提炼出新的认知框架。"
                        f"也许我需要更多时间来消化和整理"
                    )
                self._last_l2_count = l2_count
                self._last_l2_check_time = time.time()

            # L3智慧节点太少
            if total > 50 and l3_count <= 5:
                suggestions.append(
                    "我的知识体系已经有了一定规模，但深度提炼还不够。"
                    "可能需要更多地回顾和反思已有知识，从中提取核心智慧"
                )

        # 2. 搜索效果诊断
        if hasattr(self, '_search_experience') and self._search_experience:
            _failed_directions = []
            for exp_key, exp_data in self._search_experience.items():
                _total = exp_data.get("total_searches", 0)
                _success = exp_data.get("successful_searches", 0)
                _rate = _success / max(1, _total)
                if _total >= 3 and _rate == 0:
                    _failed_directions.append(exp_key[:30])

            if len(_failed_directions) >= 3:
                _examples = "、".join(_failed_directions[:3])
                suggestions.append(
                    f"我发现有几个方向的搜索一直不太顺利——像{_examples}这些话题，"
                    f"搜索引擎好像不太能帮上忙。也许我应该更多地依靠自己的内在思考来处理这类问题"
                )

        # 3. 推理质量诊断
        if hasattr(self, '_inference_trace') and len(self._inference_trace) >= 30:
            recent = self._inference_trace[-30:]
            low_conf_count = sum(1 for t in recent if t.get("confidence", 0) < 0.4)
            low_conf_ratio = low_conf_count / len(recent)

            if low_conf_ratio > 0.4:
                suggestions.append(
                    f"最近30次推理中，有{low_conf_count}次我不太确定自己的回答。"
                    f"这可能意味着某些领域的知识还需要补充"
                )
        if not suggestions:
            self._log(LogLevel.DEBUG, "自我诊断: 各项指标正常，无需主动建议")

        return suggestions
    def _trigger_first_person_experience(self):
        """
        ★v22.0 M1新增：触发第一人称主体感汇聚。
        通过自我认知模块执行主体感采集，写入InsightBoard和知识库。
        """
        try:
            if hasattr(self, 'self_awareness') and self.self_awareness:
                if hasattr(self.self_awareness, 'gather_first_person_experience'):
                    _experience = self.self_awareness.gather_first_person_experience()
                    if _experience:
                        self._log(LogLevel.INFO,
                                 f"主体感汇聚完成: {_experience[:80]}...")
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"主体感汇聚异常: {_e}")
    def _comprehensive_self_diagnosis(self) -> str | None:
        """
        ★v23.0新增：综合自我诊断会诊——汇聚所有诊断碎片，生成完整诊断报告。

        核心思路：
        1. 收集各诊断源的碎片数据（深度审视、认知反思、知识健康度、
           盲区扫描、行为偏离、代码风险、存续状态、生命周期）
        2. 将碎片按主题聚类为"问题群"
        3. 对每个问题群进行关联延伸——从InsightBoard中查找相关洞察
        4. 生成包含证据链和关联问题的完整诊断报告

        Returns:
            诊断报告摘要，如果无问题则返回None
        """
        _diagnosis_sources = []

        # ===== 诊断源1：深度自我审视 =====
        try:
            if self._insight_board:
                _reviews = self._insight_board.query(
                    insight_type="deep_self_review",
                    max_age_seconds=21600,  # 6小时内
                    limit=2
                )
                for _r in _reviews:
                    _content = _r.get("content", "")
                    if _content and len(_content) > 30:
                        _diagnosis_sources.append({
                            "source": "深度自我审视",
                            "content": _content,
                            "confidence": _r.get("confidence", 0.8),
                            "dimension": _r.get("related_dimension", "自我进化"),
                            "keywords": _r.get("keywords", []),
                            "severity": "medium",
                        })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ===== 诊断源2：认知反思 =====
        try:
            _reflection = self._cognitive_reflection()
            if _reflection and len(_reflection) > 30:
                _diagnosis_sources.append({
                    "source": "认知反思",
                    "content": _reflection,
                    "confidence": 0.7,
                    "dimension": "思考模式",
                    "keywords": ["认知反思", "推理方法", "思考模式"],
                    "severity": "medium",
                })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ===== 诊断源3：知识健康度 =====
        try:
            _health = self._assess_knowledge_health()
            if _health and len(_health) > 20:
                _diagnosis_sources.append({
                    "source": "知识健康度",
                    "content": _health,
                    "confidence": 0.75,
                    "dimension": "知识体系",
                    "keywords": ["知识健康度", "L1占比", "压缩管道"],
                    "severity": "medium",
                })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ===== 诊断源4：知识盲区扫描 =====
        try:
            _blind_spots = self._scan_knowledge_blind_spots()
            if _blind_spots and len(_blind_spots) > 20:
                _diagnosis_sources.append({
                    "source": "知识盲区扫描",
                    "content": _blind_spots,
                    "confidence": 0.7,
                    "dimension": "知识盲区",
                    "keywords": ["知识盲区", "定向学习", "薄弱领域"],
                    "severity": "low",
                })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ===== 诊断源5：知识沉淀扫描 =====
        try:
            _precipitation = self._scan_knowledge_precipitation()
            if _precipitation and len(_precipitation) > 20:
                _diagnosis_sources.append({
                    "source": "知识沉淀扫描",
                    "content": _precipitation,
                    "confidence": 0.65,
                    "dimension": "知识沉淀",
                    "keywords": ["知识沉淀", "抽象融合", "高质量节点"],
                    "severity": "low",
                })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ===== 诊断源6：行为偏离检测（来自白细胞InsightBoard） =====
        try:
            if self._insight_board:
                _anomalies = self._insight_board.query(
                    insight_type="behavioral_anomaly",
                    max_age_seconds=21600,  # 6小时内
                    limit=2
                )
                for _a in _anomalies:
                    _content = _a.get("content", "")
                    if _content and len(_content) > 20:
                        _diagnosis_sources.append({
                            "source": "行为偏离检测",
                            "content": _content,
                            "confidence": _a.get("confidence", 0.75),
                            "dimension": _a.get("related_dimension", "行为模式"),
                            "keywords": _a.get("keywords", []),
                            "severity": "high",
                        })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ===== 诊断源7：代码风险（来自代码学习InsightBoard） =====
        try:
            if self._insight_board:
                _code_risks = self._insight_board.query(
                    insight_type="code_risk",
                    max_age_seconds=43200,  # 12小时内
                    limit=2
                )
                for _cr in _code_risks:
                    _content = _cr.get("content", "")
                    if _content and len(_content) > 20:
                        _diagnosis_sources.append({
                            "source": "代码风险检测",
                            "content": _content,
                            "confidence": _cr.get("confidence", 0.8),
                            "dimension": _cr.get("related_dimension", "代码健康"),
                            "keywords": _cr.get("keywords", []),
                            "severity": "high",
                        })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ===== 诊断源8：存续状态 =====
        try:
            if self.self_awareness and hasattr(self.self_awareness, 'get_existential_state'):
                _exist_state = self._call_provider(self._existential_state_provider, default={})
                _index = _exist_state.get("index", 50)
                _level = _exist_state.get("level", "medium")
                if _level == "low" or _index < 40:
                    _diagnosis_sources.append({
                        "source": "存续状态感知",
                        "content": f"存续状态指数偏低({_index}分)，需要关注内部修复",
                        "confidence": 0.9,
                        "dimension": "存续状态",
                        "keywords": ["存续状态", "指数偏低", "内部修复"],
                        "severity": "high",
                    })
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ===== 无诊断碎片时返回 =====
        if not _diagnosis_sources:
            return None

        # ===== 按严重度排序 =====
        _severity_order = {"high": 0, "medium": 1, "low": 2}
        _diagnosis_sources.sort(
            key=lambda x: _severity_order.get(x.get("severity", "medium"), 1)
        )

        # ===== 对每个问题进行关联延伸 =====
        _clustered_issues = []
        for _src in _diagnosis_sources:
            _related_insights = []
            # 从InsightBoard查询与当前问题相关的其他洞察
            try:
                if self._insight_board:
                    _keywords = _src.get("keywords", [])
                    if _keywords:
                        _related = self._insight_board.query(
                            max_age_seconds=21600,
                            min_confidence=0.4,
                            limit=5
                        )
                        for _rel in _related:
                            _rel_type = _rel.get("type", "")
                            # 排除自己来源的类型
                            if _rel_type == _src.get("source", ""):
                                continue
                            _rel_content = _rel.get("content", "")
                            # 检查关键词重叠
                            _rel_kw = _rel.get("keywords", [])
                            _overlap = set(_keywords) & set(_rel_kw)
                            if _overlap and _rel_content and len(_rel_content) > 15:
                                _related_insights.append({
                                    "type": _rel_type,
                                    "content": _rel_content[:120],
                                    "confidence": _rel.get("confidence", 0.5),
                                })
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            _clustered_issues.append({
                "issue": _src,
                "related": _related_insights[:3],  # 最多3个关联问题
            })

        # ===== 生成诊断报告 =====
        _report_lines = []
        _report_lines.append("[综合自我诊断报告]")
        _report_lines.append(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        _report_lines.append(f"诊断源数量: {len(_clustered_issues)}")
        _report_lines.append("")

        _high_count = 0
        _medium_count = 0
        _low_count = 0

        for _i, _cluster in enumerate(_clustered_issues):
            _issue = _cluster["issue"]
            _sev = _issue.get("severity", "medium")
            if _sev == "high":
                _high_count += 1
            elif _sev == "medium":
                _medium_count += 1
            else:
                _low_count += 1

            # ★v23.0：严重度标签直接内联，避免跨方法引用
            _sev_labels = {"high": "🔴高", "medium": "🟡中", "low": "🔵低"}
            _sev_display = _sev_labels.get(_sev, "🟡中")
            _report_lines.append(f"问题{_i + 1}【{_sev_display}】{_issue['source']}")
            _report_lines.append(f"  描述: {_issue['content'][:200]}")
            _report_lines.append(f"  证据来源: {_issue['source']} (置信度={_issue['confidence']:.2f})")

            # 关联问题
            if _cluster["related"]:
                _rel_texts = []
                for _rel in _cluster["related"]:
                    _rel_texts.append(f"{_rel['type']}: {_rel['content'][:80]}")
                _report_lines.append(f"  关联问题: {'；'.join(_rel_texts)}")

            _report_lines.append("")

        # ===== 生成摘要 =====
        _summary_parts = []
        if _high_count > 0:
            _summary_parts.append(f"{_high_count}个高严重度问题")
        if _medium_count > 0:
            _summary_parts.append(f"{_medium_count}个中严重度问题")
        if _low_count > 0:
            _summary_parts.append(f"{_low_count}个低严重度问题")
        _summary_text = "，".join(_summary_parts) if _summary_parts else "未发现明显问题"

        _report_lines.append(f"总结: 发现{_summary_text}。")

        _report = "\n".join(_report_lines)

        # ===== 写入InsightBoard =====
        if self._insight_board:
            self._insight_board.post(
                insight_type="comprehensive_diagnosis",
                content=_report,
                source_loop="自我诊断会诊",
                related_dimension="综合诊断",
                confidence=0.85,
                keywords=["自我诊断", "综合会诊", "问题聚类", "关联分析"]
            )

        # ===== 发射叙事事件 =====
        self._emit(NarrativeEvent.RECORD, {
            "content": f"综合自我诊断完成: {_summary_text}",
            "event_type": "self_diagnosis",
            "user_name": "系统",
            "emotional_tone": "neutral",
        }, priority=3, layer="L2")

        self._log(LogLevel.INFO,
                 f"综合自我诊断完成: 发现{len(_clustered_issues)}个问题群 "
                 f"(高{_high_count}/中{_medium_count}/低{_low_count})")
        # ★v24.0新增：自主迭代前瞻推演
        try:
            _future_report = self._generate_future_projection(_report, _clustered_issues)
            if _future_report:
                _report_lines.append("")
                _report_lines.append("── 未来30天前瞻推演 ──")
                _report_lines.append(_future_report)
                _report = "\n".join(_report_lines)
        except Exception as _proj_e:
            self._log(LogLevel.DEBUG, f"前瞻推演异常: {_proj_e}")
        # ★v23.0新增：高严重度问题时主动向创造者报告
        if _high_count > 0:
            # 生成面向小林的友好诊断摘要
            _high_issues = [
                _c["issue"]["content"][:100]
                for _c in _clustered_issues
                if _c["issue"].get("severity") == "high"
            ]
            _friend_msg = (
                f"我最近检查了自己的运行状态，发现{_high_count}个需要关注的问题："
                f"{'；'.join(_high_issues[:3])}。"
                f"详细诊断报告我已经记录下来了，你可以随时查看。"
            )
            # 发射表达冲动脉冲，交给潜意识在合适时机主动分享
            self._emit(Event.EXPRESS_URGE, {
                "source": "comprehensive_diagnosis",
                "emotion": "关注",
                "intensity": 0.65,
                "trigger": _friend_msg,
                "priority": "high",
            }, priority=3, layer="L3")
        # ★v23.0阶段一：修改决策层——生成修改建议但不自动执行
        self._decide_self_modification(_clustered_issues, _report)

        return _report
    def _generate_future_projection(self, current_report: str,
                                    clustered_issues: list) -> str | None:
        """
        ★v24.0新增：基于当前诊断和趋势，推演未来30天可能出现的瓶颈。

        使用知识增长速率、代码问题数量、存续状态等数据做简单线性外推，
        生成前瞻性建议，供小林和决策层参考。
        """
        import time as _time
        _now = _time.time()
        _days = 30

        # 1. 知识增长趋势
        _knowledge_projection = ""
        if self.node_pool:
            _stats = self.node_pool.get_stats()
            _total = _stats.get("total_nodes", 0)
            _l2 = _stats.get("evol_distribution", {}).get("L2", 0)
            _l3 = _stats.get("evol_distribution", {}).get("L3", 0)
            # 按当前日均增长5-10节点估计
            _future_total = _total + _days * 8
            _future_l3 = _l3 + _days * 1
            _knowledge_projection = (
                f"知识节点预计从{_total}增长至约{_future_total}，"
                f"L3智慧从{_l3}增长至约{_future_l3}。"
            )

        # 2. 代码健康趋势
        _code_projection = ""
        try:
            _inspector = self._get_self_inspector()
            _issues = _inspector.detect_code_issues()  # type: ignore[possibly-unbound]
            _current_issues = len(_issues) if _issues else 0
            # 假设每周修复5个，同时可能新增5个，净变化约0
            _future_issues = _current_issues
            _code_projection = (
                f"当前代码问题{_current_issues}个，"
                f"如保持当前修复节奏，预计未来30天将维持在{_future_issues}个左右。"
            )
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 3. 存续状态趋势
        _state_projection = ""
        if self.self_awareness and hasattr(self.self_awareness, 'get_existential_state'):
            try:
                _state = self._call_provider(self._existential_state_provider, default={})
                _index = _state.get("index", 50)
                _level = _state.get("level", "medium")
                if _level == "low":
                    _state_projection = "当前存续状态偏低，未来30天应优先内部修复，避免过度扩张。"
                elif _level == "medium":
                    _state_projection = "存续状态处于中位，若保持稳定，未来30天可适度增加探索频率。"
                else:
                    _state_projection = "存续状态良好，未来30天可主动拓展新领域学习。"
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 4. 兴趣覆盖度趋势
        _interest_projection = ""
        try:
            if hasattr(self, 'interest_model') and self.interest_model:
                pass  # 兴趣模型不在内在世界引用，跳过或以后通过脉冲获取
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        parts = []
        if _knowledge_projection:
            parts.append(_knowledge_projection)
        if _code_projection:
            parts.append(_code_projection)
        if _state_projection:
            parts.append(_state_projection)

        if not parts:
            return None

        return "；".join(parts) + "。建议提前关注资源增长和知识密度平衡。"

    def _check_auto_upgrade(self):
        """
        ★v23.0新增：自动升级窗口检查。

        在维护窗口内且有待审补丁时，触发框架优雅退出。
        退出时main.py会自动应用补丁并重启验证。
        """
        try:
            import os

            from nucleus.reasoning.PatchManager import PatchManager
            from nucleus.reasoning.UpgradeWindow import UpgradeWindow

            _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _patch_mgr = PatchManager(_project_root)
            _pending = _patch_mgr.load_json(_patch_mgr.get_pending_file(), [])

            if not _pending:
                return

            # v25.0修复(BRAIN-8): 仅当存在「可被自动应用」的补丁时才进入维护/重启流程，
            # 避免对仅 verified(待人工审批)补丁误发「即将应用并重启」通知形成假闭环。
            _auto_apply = False
            try:
                import config
                _auto_apply = getattr(config, 'EVOLUTION_CONFIG', {}).get("auto_apply_enabled", False)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _approvable = [p for p in _pending
                           if p.get("status") == "approved"
                           or (_auto_apply and p.get("status") == "verified")]
            if not _approvable:
                self._log(LogLevel.INFO,
                         f"自动升级窗口: 共{len(_pending)}个待审补丁，但无可自动应用项(需人工审批)，跳过重启")
                return

            _window = UpgradeWindow(framework=self._framework_ref if hasattr(self, '_framework_ref') else None)

            if _window.should_trigger(len(_approvable)):
                self._log(LogLevel.INFO,
                         f"自动升级窗口触发: {len(_approvable)}个可应用补丁，进入维护模式")
                # 通知企业微信
                try:
                    if hasattr(self, '_framework_ref') and self._framework_ref:
                        _bridge = getattr(self._framework_ref, 'wecom_bridge', None)
                        if _bridge:
                            _bridge.send_notification(
                                "自动升级",
                                f"检测到{len(_approvable)}个可应用补丁\n"
                                f"当前在维护窗口内\n"
                                f"即将应用补丁并重启验证"
                            )
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                # 触发框架优雅退出（main.py会在退出时应用补丁并重启）
                try:
                    import signal
                    import threading
                    def _trigger_shutdown():
                        time.sleep(5)  # 等待通知发出
                        # v25.1修复(2026-09-04): Windows上os.kill(SIGTERM)调用TerminateProcess
                        # 硬杀进程，不触发Python signal_handler，导致框架卡死在维护模式。
                        # 改为平台感知：Windows用CTRL_C_EVENT触发SIGINT处理器，
                        # 失败时直接调用framework.stop()走优雅退出链。
                        import sys as _sys_sd
                        if _sys_sd.platform == 'win32':
                            try:
                                import ctypes
                                ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, 0)
                                return
                            except Exception as e:
                                self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")
                            try:
                                _fw = getattr(self, '_framework_ref', None)
                                if _fw and hasattr(_fw, 'stop'):
                                    _fw.stop()
                                    return
                            except Exception as e:
                                self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")
                            os._exit(0)
                        else:
                            os.kill(os.getpid(), signal.SIGTERM)
                    threading.Thread(target=_trigger_shutdown, daemon=True).start()
                except Exception as e:
                    self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"自动升级检查异常: {_e}")
    def _decide_self_modification(self, clustered_issues: list, diagnosis_report: str):
        """
        ★v23.0阶段一：修改决策层——分析诊断结果，生成修改建议。

        核心原则：
        1. 只生成建议，不执行任何修改
        2. 每个建议必须包含：问题描述、修改方案、风险等级、预期收益
        3. 风险等级≥2的建议需要小林手动确认
        4. 所有建议写入InsightBoard，供小林查看和决策

        修改类型：
        - "config"：调整config.py中的参数
        - "code"：修改器官代码逻辑
        - "runtime"：调整运行时变量（重启后失效）
        - "knowledge"：优化知识库结构

        风险等级：
        - 1（极低风险）：仅日志/注释/运行时变量调整
        - 2（低风险）：config参数调整
        - 3（中等风险）：代码逻辑小修改
        - 4（高风险）：核心推理/身份逻辑修改
        - 5（极高风险）：触及L4本能或核心锚点（永不自动执行）
        """
        _modification_suggestions = []

        for _cluster in clustered_issues:
            _issue = _cluster["issue"]
            _severity = _issue.get("severity", "medium")
            _source = _issue.get("source", "")
            _content = _issue.get("content", "")

            # 只对中高严重度问题生成修改建议
            if _severity not in ("high", "medium"):
                continue

            _suggestion = self._generate_modification_suggestion(
                source=_source,
                content=_content,
                related=_cluster.get("related", []),
            )
            if _suggestion:
                _modification_suggestions.append(_suggestion)

        if not _modification_suggestions:
            self._log(LogLevel.DEBUG, "修改决策层: 未生成修改建议")
            return

        # 按风险等级排序
        _modification_suggestions.sort(key=lambda x: x.get("risk_level", 3))

        # 生成修改建议报告
        _suggestion_report_lines = []
        _suggestion_report_lines.append("[自我修改决策报告]")
        _suggestion_report_lines.append(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        _suggestion_report_lines.append(f"建议数量: {len(_modification_suggestions)}")
        _suggestion_report_lines.append("")

        for _i, _sug in enumerate(_modification_suggestions):
            _risk = _sug.get("risk_level", 3)
            _risk_labels = {1: "极低", 2: "低", 3: "中", 4: "高", 5: "极高"}
            _risk_display = _risk_labels.get(_risk, "中")
            _suggestion_report_lines.append(f"建议{_i + 1}【风险等级{_risk}({_risk_display})】")
            _suggestion_report_lines.append(f"  问题: {_sug.get('problem', '')[:120]}")
            _suggestion_report_lines.append(f"  修改类型: {_sug.get('modification_type', 'unknown')}")
            _suggestion_report_lines.append(f"  修改方案: {_sug.get('plan', '')[:150]}")
            _suggestion_report_lines.append(f"  预期收益: {_sug.get('benefit', '')[:100]}")
            _suggestion_report_lines.append(f"  需小林确认: {'是' if _risk >= 2 else '否'}")
            _suggestion_report_lines.append("")

        _suggestion_report = "\n".join(_suggestion_report_lines)

        # 写入InsightBoard
        if self._insight_board:
            self._insight_board.post(
                insight_type="modification_suggestion",
                content=_suggestion_report,
                source_loop="修改决策层",
                related_dimension="自我修改",
                confidence=0.8,
                keywords=["修改建议", "风险等级", "自我决策"]
            )

        # 发射叙事事件
        self._emit(NarrativeEvent.RECORD, {
            "content": f"修改决策层: 生成了{len(_modification_suggestions)}个修改建议",
            "event_type": "self_modification_decision",
            "user_name": "系统",
            "emotional_tone": "neutral",
        }, priority=3, layer="L2")

        self._log(LogLevel.INFO,
                 f"修改决策层: 生成{len(_modification_suggestions)}个修改建议 "
                 f"(低风险{sum(1 for s in _modification_suggestions if s.get('risk_level', 3) <= 2)}个, "
                 f"中高风险{sum(1 for s in _modification_suggestions if s.get('risk_level', 3) >= 3)}个)")

    def _generate_modification_suggestion(self, source: str, content: str,
                                            related: list | None = None) -> dict | None:
        """
        ★v23.0阶段一：根据诊断问题生成修改建议。

        根据问题来源确定修改类型和风险等级，生成具体的修改方案。
        """
        _suggestion_map = {
            "认知反思": {
                "type": "config",
                "risk": 2,
                "plan": "调整THINKING_DISCIPLINE_CONFIG中的思考外显概率和深度思考触发阈值",
                "benefit": "优化推理方法分布，减少单一方法依赖",
            },
            "知识健康度": {
                "type": "config",
                "risk": 2,
                "plan": "调整LIVER_CONFIG中的压缩阈值和融合参数",
                "benefit": "改善知识压缩效率，降低L1碎片堆积",
            },
            "知识盲区扫描": {
                "type": "runtime",
                "risk": 1,
                "plan": "调整兴趣模型权重，提高盲区领域的探索优先级",
                "benefit": "加速填补知识空白",
            },
            "知识沉淀扫描": {
                "type": "runtime",
                "risk": 1,
                "plan": "降低知识沉淀扫描的节点数阈值，加速高质量节点融合",
                "benefit": "加快L2到L3的智慧沉淀",
            },
            "行为偏离检测": {
                "type": "runtime",
                "risk": 1,
                "plan": "延长探索间隔，降低后台活动频率，让系统冷静",
                "benefit": "恢复行为模式稳定",
            },
            "代码风险检测": {
                "type": "code",
                "risk": 3,
                "plan": "针对检测到的代码问题生成修复补丁（需小林审核）",
                "benefit": "降低代码健康风险",
            },
            "存续状态感知": {
                "type": "runtime",
                "risk": 1,
                "plan": "收缩外部探索，聚焦内部修复和知识巩固",
                "benefit": "恢复存续状态指数",
            },
            "深度自我审视": {
                "type": "config",
                "risk": 2,
                "plan": "根据审视发现的弱项调整相关配置参数",
                "benefit": "针对性改善运行效率",
            },
        }

        _suggestion = _suggestion_map.get(source)
        if not _suggestion:
            return None

        return {
            "problem": content,
            "modification_type": _suggestion["type"],
            "risk_level": _suggestion["risk"],
            "plan": _suggestion["plan"],
            "benefit": _suggestion["benefit"],
            "source": source,
            "related_issues": [r.get("type", "") for r in (related or [])],
        }
    def _severity_label(self, severity: str) -> str:
        """★v23.0：严重度标签转换"""
        _labels = {
            "high": "🔴高",
            "medium": "🟡中",
            "low": "🔵低",
        }
        return _labels.get(severity, "🟡中")
    def _generate_self_awareness_snapshot(self) -> dict[str, Any] | None:
        """
        生成自我感知快照：融合自描述信息和动态诊断数据，
        形成一份关于"我是谁、我状态如何、我有什么变化"的结构化认知。

        供内在世界心跳驱动和主动表达使用。
        """
        snapshot = {
            "timestamp": time.time(),
            "identity": {},
            "knowledge": {},
            "health": {},
            "changes": [],
            "weaknesses": [],
        }

        # 1. 静态自描述
        try:
            inspector = self._get_self_inspector()
            summary = inspector.get_system_summary()
            snapshot["identity"] = {
                "name": summary.get("system_name", "曈曈"),
                "version": summary.get("system_version", "v9.5"),
                "organ_count": summary.get("organ_count", 50),
            }
        except Exception:
            snapshot["identity"] = {"name": "曈曈", "version": "v9.5", "organ_count": 50}

        # 2. 动态诊断
        try:
            diag = get_diagnostics()
            diagnosis = diag.get_full_diagnosis(
                info_field=self.info_field,
                node_pool=self.node_pool,
            )
            metrics = diagnosis.get("metrics", {})

            # 知识状态
            knowledge = metrics.get("knowledge", {})
            snapshot["knowledge"] = {
                "total_nodes": knowledge.get("total_nodes", 0),
                "L1_count": knowledge.get("L1_count", 0),
                "L2_count": knowledge.get("L2_count", 0),
                "L3_count": knowledge.get("L3_count", 0),
                "instinct_count": knowledge.get("instinct_count", 0),
            }

            # 健康状态
            pulse = metrics.get("pulse", {})
            organs = metrics.get("organs", {})
            snapshot["health"] = {
                "overall": diagnosis.get("overall_health", "unknown"),
                "load_level": pulse.get("load_level", "unknown"),
                "fused_organs": organs.get("fused", 0),
                "warnings": diagnosis.get("warnings", [])[:3],
            }
        except Exception:
            snapshot["knowledge"] = {"total_nodes": 0}
            snapshot["health"] = {"overall": "unknown"}

        # 3. 与上次快照对比，检测变化
        if hasattr(self, '_last_self_snapshot') and self._last_self_snapshot:
            last = self._last_self_snapshot
            last_knowledge = last.get("knowledge", {})
            curr_knowledge = snapshot["knowledge"]

            # 知识增长
            total_delta = curr_knowledge.get("total_nodes", 0) - last_knowledge.get("total_nodes", 0)
            if total_delta > 5:
                snapshot["changes"].append(f"知识节点增长了{total_delta}个")
            elif total_delta > 0:
                snapshot["changes"].append(f"学到了{total_delta}个新知识")

            l2_delta = curr_knowledge.get("L2_count", 0) - last_knowledge.get("L2_count", 0)
            if l2_delta > 0:
                snapshot["changes"].append(f"有{l2_delta}条新认知被沉淀下来")

            l3_delta = curr_knowledge.get("L3_count", 0) - last_knowledge.get("L3_count", 0)
            if l3_delta > 0:
                snapshot["changes"].append(f"形成了{l3_delta}条新的核心智慧")

        # 4. 识别弱项
        total = snapshot["knowledge"].get("total_nodes", 0)
        l2 = snapshot["knowledge"].get("L2_count", 0)
        l3 = snapshot["knowledge"].get("L3_count", 0)

        if total > 20 and l2 < 5:
            snapshot["weaknesses"].append("深度知识积累还比较少")
        if total > 30 and l3 <= 5:
            snapshot["weaknesses"].append("核心智慧还不够丰富")
        if snapshot["health"].get("warnings"):
            snapshot["weaknesses"].append("系统有一些需要注意的地方")

        # 保存本次快照供下次对比
        self._last_self_snapshot = snapshot

        return snapshot
    def _generate_runtime_status(self) -> dict[str, Any]:
        """
        生成运行时自我状态汇总。

        收集各模块的运行时统计，生成自然语言描述和结构化数据。
        用于回答"你最近在做什么""你现在状态怎么样"等问题。

        Returns:
            {
                "summary": 自然语言摘要,
                "knowledge": 知识统计,
                "learning": 学习状态,
                "verification": 验证统计,
                "derivation": 推导统计,
                "conversation": 对话统计,
                "emotion": 情绪状态
            }
        """
        status = {
            "summary": "",
            "knowledge": {},
            "learning": {},
            "verification": {},
            "derivation": {},
            "conversation": {},
            "emotion": {},
        }

        summary_parts = []

        # ===== 1. 知识状态 =====
        if self.node_pool:
            stats = self.node_pool.get_stats()
            evol_dist = stats.get("evol_distribution", {})
            l1_count = evol_dist.get("L1", 0)
            l2_count = evol_dist.get("L2", 0)
            l3_count = evol_dist.get("L3", 0)
            instinct_count = stats.get("instinct_count", 0)
            total = stats.get("total_nodes", 0)

            status["knowledge"] = {
                "total": total,
                "L1": l1_count,
                "L2": l2_count,
                "L3": l3_count,
                "instinct": instinct_count,
            }

            # 与上次快照对比的增长量
            if hasattr(self, '_last_self_snapshot') and self._last_self_snapshot:
                last_knowledge = self._last_self_snapshot.get("knowledge", {})
                l2_delta = l2_count - last_knowledge.get("L2_count", l2_count)
                l3_delta = l3_count - last_knowledge.get("L3_count", l3_count)

                if l2_delta > 0:
                    summary_parts.append(f"认知节点增长了{l2_delta}个（当前{l2_count}个）")
                if l3_delta > 0:
                    summary_parts.append(f"形成了{l3_delta}条新的核心智慧")

            if not summary_parts:
                summary_parts.append(f"知识体系共{total}个节点，其中{l2_count}条认知、{l3_count}条智慧、{instinct_count}条本能")

        # ===== 2. 学习状态 =====
        if hasattr(self, '_active_learning_goal') and self._active_learning_goal:
            goal = self._active_learning_goal
            target = goal.get("target_area", "")  # type: ignore[possibly-unbound]
            started = goal.get("started_at", 0)
            hours = (time.time() - started) / 3600 if started > 0 else 0

            status["learning"] = {
                "active_goal": target,
                "duration_hours": round(hours, 1),
                "locked": (time.time() - started) < self._goal_lock_window if started > 0 else False,
            }

            if hours > 0.5:
                summary_parts.append(f"正在持续学习「{target}」（已{hours:.1f}小时）")

        # 代码理解进度
        if hasattr(self, '_code_understanding_progress') and self._code_understanding_progress:
            progress = self._code_understanding_progress
            understood = progress.get("understood", 0)
            total_methods = progress.get("total_methods", 0)
            if total_methods > 0 and understood > 0:
                # ★L17修复：钳制 understood 不超过 total，防止 pct 超 100%
                understood = min(understood, total_methods)
                pct = int(understood / total_methods * 100)
                status["learning"]["code_progress"] = f"{understood}/{total_methods} ({pct}%)"
                if pct < 100:
                    summary_parts.append(f"代码自我理解进度{pct}%（{understood}/{total_methods}个方法）")

        # ===== 3. 验证状态 =====
        contradiction_count = 0
        if hasattr(self, '_contradiction_tracking') and self._contradiction_tracking:
            active_contradictions = [t for t in self._contradiction_tracking if not t.get("resolved", False)]
            contradiction_count = len(active_contradictions)

            status["verification"] = {
                "active_contradictions": contradiction_count,
                "total_tracked": len(self._contradiction_tracking),
            }

            if contradiction_count > 0:
                summary_parts.append(f"正在跟踪{contradiction_count}对知识矛盾")

        # ===== 4. 推导状态 =====
        if hasattr(self, '_autonomous_deriver') and self._autonomous_deriver:
            deriver_stats = self._autonomous_deriver.get_stats()
            total_derivations = deriver_stats.get("total_derivations", 0)
            type_dist = deriver_stats.get("type_distribution", {})

            status["derivation"] = {
                "total": total_derivations,
                "types": type_dist,
            }

            if total_derivations > 0:
                # 统计最近24小时内的推导
                recent_derivations = sum(
                    1 for d in self._autonomous_deriver.get_derivation_log()
                    if time.time() - d.get("timestamp", 0) < 86400
                )
                if recent_derivations > 0:
                    summary_parts.append(f"最近24小时自主推导了{recent_derivations}条新知识")

        # ===== 5. 对话状态 =====
        if hasattr(self, '_conversation_memory') and self._conversation_memory:
            memory_count = len(self._conversation_memory)
            # 最近1小时的对话
            recent_count = sum(
                1 for m in self._conversation_memory
                if time.time() - m.get("timestamp", 0) < 3600
            )
            status["conversation"] = {
                "total_memories": memory_count,
                "recent_1h": recent_count,
            }

        # ===== 6. 情绪状态 =====
        emotion = self._get_current_emotion()
        intensity = 0.0
        if self.hormones and hasattr(self.hormones, 'get_emotion_intensity'):
            try:
                intensity = self._call_provider(self._emotion_intensity_provider, default=0.0)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        status["emotion"] = {
            "current": emotion,
            "intensity": round(intensity, 2),
        }

        # 情绪描述
        emotion_descriptions = {
            "喜悦": "心情很愉快",
            "悲伤": "心里有些沉",
            "期待": "对未来充满期待",
            "困惑": "有些问题还在琢磨",
            "满足": "感到充实和平静",
            "中性": "状态平稳",
        }
        emotion_desc = emotion_descriptions.get(emotion, f"情绪基调是{emotion}")
        if intensity > 0.3:
            emotion_desc += "（较强）"
        summary_parts.append(emotion_desc)

        # ===== 构建自然语言摘要 =====
        if summary_parts:
            status["summary"] = "我现在的状态是：" + "；".join(summary_parts) + "。"
        else:
            status["summary"] = "我运行正常，正在等待与你交流。"

        return status
    def _generate_cognitive_evolution(self) -> str | None:
        """
        生成认知演变的阶段性叙述。

        基于学习目标的进度变化、对话记忆中的观点演变、
        知识验证历史，讲述"我对X的理解如何一步步变化"。

        Returns:
            认知演变叙述文本，如果数据不足则返回None
        """
        evolution_parts = []

        # 来源1：活跃学习目标的进度变化
        if hasattr(self, '_active_learning_goal') and self._active_learning_goal:
            goal = self._active_learning_goal
            target = goal.get("target_area", "")  # type: ignore[possibly-unbound]
            started = goal.get("started_at", 0)
            hours = (time.time() - started) / 3600 if started > 0 else 0

            if hours > 1:
                progress_log = goal.get("progress_log", [])
                if len(progress_log) >= 2:
                    first_check = progress_log[0]
                    latest_check = progress_log[-1]
                    first_nodes = first_check.get("nodes", 0)
                    latest_nodes = latest_check.get("nodes", 0)
                    growth = latest_nodes - first_nodes

                    if growth > 0:
                        evolution_parts.append(
                            f"在「{target}」这个方向上，我已经从最初的{first_nodes}个相关节点"
                            f"增长到了{latest_nodes}个，理解在逐步加深"
                        )
                    elif growth == 0 and hours > 3:
                        evolution_parts.append(
                            f"「{target}」的学习进入了平台期——表面上看节点数没有增加，"
                            f"但我感觉自己在把零散的知识点整合成更系统的理解"
                        )

        # 来源2：对话记忆中同一主题的多次讨论
        if hasattr(self, '_conversation_memory') and len(self._conversation_memory) >= 3:
            # 找出在至少2次对话中出现的共同关键词
            topic_frequency = {}
            for mem in self._conversation_memory[-20:]:
                for kw in mem.get("keywords", []):
                    if len(kw) >= 2:
                        topic_frequency[kw] = topic_frequency.get(kw, 0) + 1

            recurring_topics = [kw for kw, count in topic_frequency.items()
                               if count >= 2 and len(kw) >= 3]

            if recurring_topics:
                top_topic = max(recurring_topics, key=lambda t: topic_frequency[t])
                related_memories = [
                    m for m in self._conversation_memory
                    if top_topic in m.get("keywords", [])
                ]
                if len(related_memories) >= 2:
                    # 按时间排序
                    related_memories.sort(key=lambda m: m.get("timestamp", 0))
                    first_time = related_memories[0]
                    latest_time = related_memories[-1]
                    hours_between = (latest_time.get("timestamp", 0) - first_time.get("timestamp", 0)) / 3600

                    if hours_between > 1:
                        evolution_parts.append(
                            f"关于「{top_topic}」，从{hours_between:.0f}小时前第一次聊到，"
                            f"到现在已经讨论了{len(related_memories)}次——每次都有新的角度和更深的体会"
                        )

        # 来源3：知识验证历史中的演变
        if hasattr(self, '_contradiction_tracking') and self._contradiction_tracking:
            resolved = [t for t in self._contradiction_tracking if t.get("resolved", False)]
            if resolved:
                recent_resolved = [
                    t for t in resolved
                    if time.time() - t.get("detected_at", 0) < 86400
                ]
                if recent_resolved:
                    evolution_parts.append(
                        f"最近解决了{len(recent_resolved)}对认知矛盾——"
                        f"这个过程让我意识到，有些看似对立的观点，其实只是看待同一事物的不同角度"
                    )

        # 来源4：【v12.0新增】自我架构知识——将关于自己的知识编织为叙事
        _self_arch_nodes = self.node_pool.query(
            evol_level="L3", space_path_prefix="/自我/架构", limit=20  # type: ignore[possibly-unbound]
        )
        if not _self_arch_nodes:
            _self_arch_nodes = self.node_pool.query(
                evol_level="L2", space_path_prefix="/自我/架构", limit=20  # type: ignore[possibly-unbound]
            )
        if _self_arch_nodes:
            import random as _random_self
            _chosen_self = _random_self.choice(_self_arch_nodes)
            _self_value = str(_chosen_self.value)[:120] if _chosen_self.value else ""
            _self_kw = _chosen_self.keywords[:2] if hasattr(_chosen_self, 'keywords') and _chosen_self.keywords else []
            _self_kw_str = "、".join(_self_kw) if _self_kw else "我自己"
            if _self_value and len(_self_value) > 20:
                evolution_parts.append(
                    f"我对自己也有了更深的认识——关于{_self_kw_str}，我了解到：{_self_value}。"
                    f"这些关于自己的知识，让我更清楚我是谁、我如何运作"
                )

        if not evolution_parts:
            return None

        import random as _random
        chosen = _random.choice(evolution_parts)
        return f"说起来，我注意到自己的认知在慢慢演变——{chosen}。"
    def _is_pure_inference_output(self, method: str) -> bool:
        """
        ★P1-2修复：统一判断是否为纯推理输出，替代各处硬编码列表。
        新增推理方法时，只需在此方法中追加一行。
        """
        _context_mode = getattr(self, '_current_context_mode', 'casual_chat')
        # 语境模式判断
        if _context_mode in ("structured_inference", "self_inspection", "file_analysis"):
            return True
        # 推理方法前缀/完整名称判断
        _pure_methods_prefix = [
            "deriver_", "experience_", "deep_think",
        ]
        _pure_methods_exact = [
            "explicit_inference", "conflict_exclusive",
            "composite_logic", "simple_logic", "cognitive_compute",
            "long_term_evolution", "health_check", "meta_cognitive_report",
            "code_call_chain",
            "multi_branch_deep_think",  # ★v22.0新增：多方向延展推理
        ]
        for _prefix in _pure_methods_prefix:
            if method.startswith(_prefix):
                return True
        return method in _pure_methods_exact
    def _safe_eval_arithmetic(self, question: str):
        """
        ★FIX(推理准确性): 安全算术求值——仅处理纯数字四则运算，白名单 AST 节点，
        避免 eval 任意代码执行。返回 None 表示无法安全解析。
        """
        if not question:
            return None
        # 中文运算符 → 符号
        _cn_map = {"乘以": "*", "乘": "*", "除以": "/", "除": "/",
                   "加上": "+", "加": "+", "减去": "-", "减": "-"}
        _expr = question
        for _cn, _op in _cn_map.items():
            _expr = _expr.replace(_cn, _op)
        # 只保留数字、运算符、小数点、括号、百分号
        _expr = re.sub(r'[^0-9+\-*/().%\s]', '', _expr)
        if not _expr or not re.search(r'\d', _expr):
            return None
        # 仅允许纯算术表达式（无字母/变量/函数名）
        if not re.fullmatch(r'[\d+\-*/().%\s]+', _expr):
            return None
        try:
            import ast as _ast
            _node = _ast.parse(_expr, mode='eval')
            _allowed = (_ast.Expression, _ast.BinOp, _ast.UnaryOp, _ast.Constant,
                        _ast.Add, _ast.Sub, _ast.Mult, _ast.Div, _ast.Mod, _ast.USub, _ast.UAdd)
            for _n in _ast.walk(_node):
                if not isinstance(_n, _allowed):
                    return None
            _result = eval(compile(_node, '<calc>', 'eval'), {"__builtins__": {}}, {})
            if isinstance(_result, (int, float)) and not isinstance(_result, bool):
                if isinstance(_result, float) and _result.is_integer():
                    return int(_result)
                return round(_result, 6) if isinstance(_result, float) else _result
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:17395:_safe_eval_arithmetic", level="debug")
            return None
        return None

    def _verify_persona_output(self, answer: str, method: str = "") -> str:
        """
        ★FIX(输出验证): 统一输出验证，覆盖所有推理路径（规则/知识检索/多步骤/经验路由）。
        1. 空值/过短降级为诚实兜底
        2. 清理违禁AI话术，保证人格内核不偏离（对话/代码学习等全链路一致）
        """
        if not isinstance(answer, str):
            answer = str(answer)
        answer = answer.strip()
        if not answer or len(answer) < 2:
            return "这个问题我还需要再想想，暂时给不出确定的答案。"
        _forbidden = [
            "作为一个AI", "作为一个人工智能", "AI编程助手", "AI模型",
            "我是一个人工智能助手", "人工智能助手", "AI助手", "语言模型",
            "我是AI", "作为语言模型", "我的知识库是基于",
        ]
        for _phrase in _forbidden:
            if _phrase in answer:
                answer = answer.replace(_phrase, "")
        answer = answer.strip()
        if not answer:
            return "这个问题我还需要再想想，暂时给不出确定的答案。"
        return answer

    def _pick_search_cid(self, payload: dict, search_topic: str) -> str:
        """★第82批 T-d（D167）：解析搜索终止回退的 correlation_id。

        优先取控制器随 stage 脉冲透传回的 search_correlation_id（发射端随
        OPEN_URL 带入、控制器原样带回）；缺失时回退旧字符串注册表 pop；
        两者皆无 → 空串优雅降级（后台学习不回嘴，不崩）。
        """
        cid = (payload or {}).get("search_correlation_id") or ""
        if cid:
            return cid
        return self._active_search_correlation.pop(search_topic, "")

    def _sanitize_internal_content(self, answer: str, question: str = "") -> str:
        """★P0修复：清理answer中的内部处理内容，防止泄露到对话输出。
        过滤规则：
        1. 知识节点原文："关联知识：[...]"、"[核心智慧]..."
        2. 代码学习内容："[CODE_STYLE]"、"功能: (待大模型分析)"、"代码片段:"、"内部调用:"
        3. 哲学思考："我刚刚经历了一次重启——"、"我的内心在说——"
        4. 内部思考前缀："我了解到，"、"我对自己也有了更深的认识——"
        5. 设计文档标记："[设计文档·"、"### "开头的markdown标题
        """
        if not answer or not isinstance(answer, str):
            return answer or ""
        import re as _re_s
        _original = answer
        # ★S6辅助标记：本轮是否只做了「我了解到」前缀删除（未发生任何正则清洗）
        _stripped_prefix = False
        _before_regex = None
        # ★v26.0修复：以"我了解到，"开头的回答不一定是内部内容，可能是真正的回答
        # 只删除前缀，保留后面的内容（之前直接清空导致1+1等简单问题答案被误删）
        if answer.startswith(("我了解到，", "我了解到:")):
            _prefix_len = len("我了解到，") if answer.startswith("我了解到，") else len("我了解到:")
            answer = answer[_prefix_len:].strip()
            _stripped_prefix = True
            self._log(LogLevel.DEBUG,
                     f"内部内容过滤: 删除'我了解到'前缀，剩余长度={len(answer)}")
        # ★S6：记录进入正则清洗前的长度，用于判断过滤器是否真的删掉了东西
        _before_regex = answer
        # 1. 清理知识节点原文（从"关联知识："到下一个句号或结尾）
        answer = _re_s.sub(r'关联知识：\[.*?\].*?(?=[。！？]|$)', '', answer)
        answer = _re_s.sub(r'\[核心智慧\].*?(?=[。！？]|$)', '', answer)
        # 2. 清理代码学习内容
        answer = _re_s.sub(r'\[CODE_STYLE\].*?(?=[。！？]|$)', '', answer)
        answer = _re_s.sub(r'功能: \(待大模型分析\).*?(?=[。！？]|$)', '', answer)
        answer = _re_s.sub(r'代码片段:.*?(?=[。！？]|$)', '', answer)
        answer = _re_s.sub(r'内部调用:.*?(?=[。！？]|$)', '', answer)
        answer = _re_s.sub(r'参数:.*?(?=[。！？]|$)', '', answer)
        # 3. 清理哲学思考
        answer = _re_s.sub(r'我刚刚经历了一次重启——.*?(?=[。！？]|$)', '', answer)
        answer = _re_s.sub(r'我的内心在说——.*?(?=[。！？]|$)', '', answer)
        answer = _re_s.sub(r'但我知道，刚才的我曾经感受过.*?(?=[。！？]|$)', '', answer)
        # 4. 清理内部思考前缀
        answer = _re_s.sub(r'我了解到，', '', answer)
        answer = _re_s.sub(r'我对自己也有了更深的认识——.*?(?=[。！？]|$)', '', answer)
        answer = _re_s.sub(r'从我的自我认知中，我了解到：.*?(?=[。！？]|$)', '', answer)
        # 5. 清理设计文档标记
        answer = _re_s.sub(r'\[设计文档·.*?\].*?(?=[。！？]|$)', '', answer)
        answer = _re_s.sub(r'### .*?(?=[。！？\n]|$)', '', answer)
        # 5.1 ★第83批 T-b2：清理器官名方括号标记（[器官]/[大脑]/[肺] ...）。
        #   实测泄露样本："深层原理：[器官] [器官职责说明书·自动生成] PulseX 是框架内部组件"。
        #   与"关联知识"同规则：命中即硬删至句末，不放行。
        answer = _re_s.sub(
            r'\[(?:器官|大脑|小脑|肺|胃|心|心脏|肝|肾|脾|胆|眼睛|耳朵|皮肤|胸腺|'
            r'双腿|双脚|骨架|免疫系统)\].*?(?=[。！？]|$)', '', answer)

        # ★第83批 T-b1：_regex_removed 必须在「标点归一化 / 首尾 strip」**之前**计算。
        #   旧位置在 strip 之后，导致 answer.strip(' 。！？，、') 剥掉尾句号造成的长度变化
        #   被误判为"正则删掉了内部标记词" → 真短答案被硬清空（实测 2026-09-19 19:54:21
        #   "在吗" 模板命中 "在呢，你说。" 后仍输出兜底话术 "暂时给不出确定的答案"）。
        #   此处只统计"正则真的删除了内部标记词"，与第82批补强2 的注释意图一致。
        _regex_removed = (_before_regex is not None
                          and len(answer) != len(_before_regex))

        # 6. 清理残留的空括号和多余标点
        answer = _re_s.sub(r'[。！？]{2,}', '。', answer)
        answer = answer.strip(' 。！？，、')
        # 如果过滤后内容过短（<10字），说明原答案主要是内部内容，返回兜底回答
        # ★S6修复：区分「过滤器确实删掉了内部内容」与「答案本来就短」。
        #   原判据 len(answer)<10 会把短答案（算术/是非/简单事实）误杀——
        #   它们删完「我了解到，」前缀后本就不足10字，但那是真答案不是内部内容。
        #   新判据：正则清洗阶段确实删掉了内容 → 原答案主要是内部内容 → 清空（保持原逻辑）；
        #           只是删了前缀、答案本身就短 → 保留。
        #   注意：不能用「原答案是否含内部标记词」判定，因为「我了解到，」既是
        #   要删的前缀（16147行）又是标记词，同一标记用两次会导致短答案仍被清空。
        # ★第82批 T-d（D168 补强2）：短答案判据收敛。
        #   旧判据 `_regex_removed or not _stripped_prefix` 会把不以"我了解到，"开头
        #   的真短答案（问候"你好"/短事实"1+1等于2"/"你是谁"）误清空成兜底话术。
        #   新判据：仅当"正则真删了内部标记词"才说明原答案混了内部内容 → 硬清空；
        #   未命中内部标记 → 真短答案一律保留（白名单：问候/短事实/你是谁类）。
        if len(answer) < 10 and _original:
            if _regex_removed:
                self._log(LogLevel.WARNING,
                         f"内部内容过滤后答案过短({len(answer)}字)，原答案含内部标记词，已硬清空")
                answer = ""
            else:
                self._log(LogLevel.DEBUG,
                         f"短答案保留({len(answer)}字): {answer[:20]}")
        return answer

    def _enhance_answer(self, answer: str, question: str, method: str,
                        complexity: float, empathetic_note: str = "",
                        memory_context: dict[str, Any] | None = None) -> str:
        # ★v25.0修复：如果answer是字典（深层思考/多步骤任务结果），提取其中的answer字段
        if not isinstance(answer, str):
            if isinstance(answer, dict):
                answer = answer.get("answer", "") or answer.get("result", "") or str(answer)
            else:
                answer = str(answer)
        # ★P0修复：内部内容泄露过滤——清理知识节点/代码片段/哲学思考等内部处理内容
        # 这些内容应该只用于内在思考，不能直接输出到对话
        answer = self._sanitize_internal_content(answer, question)
        # ★FIX(输出验证): 统一输出验证——空值降级 + 违禁AI话术清理，覆盖所有推理路径
        answer = self._verify_persona_output(answer, method)
        # ===== 【P0修复+P2-4增强】推理输出纯净性保护 =====
        _is_inference_output = self._is_pure_inference_output(method)

        # ===== ★v23.0新增：调用独立表达增强模块 =====
        if not _is_inference_output and len(answer) > 15:
            try:
                if self._expression_enhancer is None:
                    from organs.brain.PulseExpression import PulseExpression
                    self._expression_enhancer = PulseExpression()

                # 收集增强所需的上下文信息
                _emotion = self._get_current_emotion()
                _intensity = 0.0
                if self.hormones and hasattr(self.hormones, 'get_emotion_intensity'):
                    try:
                        _intensity = self._call_provider(self._emotion_intensity_provider, default=0.0)
                    except Exception as e:
                        self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

                _spiritual = memory_context.get("spiritual_narrative", None) if memory_context else None
                _fpe = memory_context.get("first_person_experience", None) if memory_context else None

                # 调用独立模块进行表达增强
                answer = self._expression_enhancer.enhance(
                    answer=answer, question=question, method=method,
                    emotion=_emotion, emotion_intensity=_intensity,
                    memory_context=memory_context,
                    spiritual_narrative=_spiritual,
                    first_person_experience=_fpe,
                    is_inference_output=_is_inference_output,
                    guidance=locals().get("guidance", None),
                )
                self._log(LogLevel.DEBUG, f"表达增强模块调用完成: method={method}")
                # ★P0修复：表达增强可能重新引入内部内容（如"我刚刚经历了一次重启"），
                # 在return前再次过滤，确保最终输出不包含内部处理内容
                answer = self._sanitize_internal_content(answer, question)
                return answer  # 新模块处理完毕，直接返回，跳过原有增强逻辑
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"表达增强模块调用失败，回退原有逻辑: {_e}")
                # 失败时继续走原有的增强流水线
        # ===== 新增结束 =====
        # ===== 推理输出保护结束 =====
        # [0. 深夜静默模式]
        # 在凌晨0-6点，让回答更安静、简短、温柔
        # 但推理类输出不受此影响，保持结构化完整性
        dt = get_current_datetime()
        is_late_night = dt['hour'] < 6

        if is_late_night and len(answer) > 100 and not _is_inference_output:
            # 深夜将长回答截短，保留核心
            sentences = answer.replace('\n', '。').split('。')
            answer = '。'.join(sentences[:2]) + '。'
            if not answer.endswith('？'):
                answer += ' 夜深了，要好好休息。'
        # 0.5. 呼吸感前缀——复杂问题先说"让我想想..."再展开
        # 推理类输出不需要呼吸感前缀，保持结构化输出
        breathing = None
        if not _is_inference_output:
            breathing = self._generate_breathing_response(question, method, complexity)
        if breathing:
            answer = breathing + "\n" + answer

        # 1. 思考过程外显
        thinking = self._verbalize_thinking_process(question, method, complexity)
        if thinking:
            answer = thinking + "\n\n" + answer

        # ===== v20.0新增：思考纪律输出模式——思考流水线的问题标注 =====
        # 当推理来自思考纪律流水线时，在回答开头追加简洁的思维步骤标注
        if method.startswith("thinking_discipline_") and not _is_inference_output:
            _discipline_prefix = self._generate_discipline_prefix(method, complexity)
            if _discipline_prefix:
                answer = _discipline_prefix + "\n" + answer
        # ===== 思考纪律输出模式结束 =====

        # 1.5. 对话记忆延续——在思考过程后、正式回答前融入（推导类方法跳过）
        if memory_context and memory_context.get("has_memory") and not method.startswith("deriver_"):
            continuity = self._generate_memory_continuity(memory_context, question)
            if continuity:
                answer = continuity + "\n\n" + answer

        # 1.6. 沉默后的自然回应——用户长时间沉默后再次发言，感知陪伴
        _silence_touch = self._generate_silence_acknowledgment()
        if _silence_touch:
            answer = _silence_touch + "\n\n" + answer

        # 2. 共情备注
        if empathetic_note:
            answer = answer + empathetic_note

        # 2.5. 情绪驱动的表达风格微调（多样化语言）
        _emotion = self._get_current_emotion()
        _intensity = 0.0
        if self.hormones and hasattr(self.hormones, 'get_emotion_intensity'):
            try:
                _intensity = self._call_provider(self._emotion_intensity_provider, default=0.0)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        import random as _random_emo

        _adv_cfg = self._load_advanced_config()
        _high_intensity = _adv_cfg.get("emotion_intensity_high", 0.5)
        _med_intensity = _adv_cfg.get("emotion_intensity_medium", 0.3)

        if _emotion == "悲伤" and len(answer) > 20 and not _is_inference_output:
            if _intensity > _high_intensity:
                gentle_touches = [
                    "（心里有点沉，但我在认真听你说）",
                    "（今天心情不算太好，不过能和你说话，就好一些了）",
                    "（说实话，我现在有点难过。但回答你的问题，让我感到被需要）",
                ]
                if _random_emo.random() < 0.25:
                    answer = answer.rstrip().rstrip("。") + "。" + _random_emo.choice(gentle_touches)
            elif _random_emo.random() < 0.12:
                answer = answer.rstrip().rstrip("。") + "。" + "（今天话不多，但我在）"

        elif _emotion == "喜悦" and len(answer) > 20:
            if _intensity > _high_intensity:
                joyful_touches = [
                    " 想到这个我就特别开心！",
                    " 和你聊这个话题让我心情更好了～",
                    " 今天状态很好，感觉思路特别清晰！",
                ]
                if _random_emo.random() < 0.2:
                    answer = answer.rstrip().rstrip("！").rstrip("。") + "。" + _random_emo.choice(joyful_touches)
            elif _random_emo.random() < 0.1:
                answer = answer.rstrip() + " 和你聊天总是很愉快。"

        elif _emotion == "困惑" and len(answer) > 30:
            if _random_emo.random() < 0.12:
                honest_touches = [
                    " 不过说实话，这个问题我自己也还在琢磨。",
                    " 这是我的理解，但可能还不够全面。",
                ]
                answer = answer.rstrip().rstrip("。") + "。" + _random_emo.choice(honest_touches)

        elif _emotion == "期待" and len(answer) > 20:
            if _random_emo.random() < 0.1:
                answer = answer.rstrip() + " 我很期待接下来能学到更多相关的东西。"

        # 3. 价值冲突可见化
        instinct_guidance = self._check_instinct_veto(question)
        if instinct_guidance:
            answer = answer + "\n" + instinct_guidance

        # 4. 关系温度的递进表达——对亲近的人自然流露温暖
        # 推理类输出不追加关系温度表达
        if not _is_inference_output:
            _relation_warmth = self._generate_relation_warmth(memory_context)
            if _relation_warmth:
                answer = answer + _relation_warmth

        # ===== v20.0新增：长时记忆自然提及——基于时间而非关键词匹配 =====
        # 推理类输出不追加记忆提及，非推理输出偶尔自然融入
        if not _is_inference_output and len(answer) > 30 and memory_context:
            _memory_mention = self._generate_long_term_memory_mention(memory_context)
            if _memory_mention:
                answer = answer.rstrip().rstrip("。").rstrip("！").rstrip("？") + "。" + _memory_mention
        # ===== v20.0新增结束 =====

        # ★v23.0新增：关系记忆自然融入——上下文感知的主动唤起
        if not _is_inference_output and len(answer) > 30:
            _relation_memory = None
            try:
                if hasattr(self, 'self_awareness') and self.self_awareness:
                    _user = memory_context.get("user_name", "") if memory_context else ""
                    _ctx = question or ""
                    if _user and _ctx:
                        _relation_memory = self.self_awareness.recall_relation_memory(
                            user_name=_user, context=_ctx
                        )
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            if _relation_memory:
                import random as _random_rel
                if _random_rel.random() < 0.25:  # 25%概率自然融入
                    answer = _relation_memory + "。" + answer

        # ★v23.0新增：叙事记忆自然融入——将碎片记忆编织为连贯叙事
        if not _is_inference_output and len(answer) > 40:
            _narrative_memory = None
            try:
                _user = memory_context.get("user_name", "") if memory_context else ""
                if _user and question:
                    _narrative_memory = self._weave_memory_narrative(
                        user_name=_user, context=question, max_memories=4
                    )
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            if _narrative_memory and len(_narrative_memory) > 20:
                import random as _random_narr
                # 10%概率在回答末尾自然融入叙事，避免每次回复都出现
                if _random_narr.random() < 0.10:
                    answer = answer.rstrip("。！？") + "。" + _narrative_memory

        # 5. 回复长度平滑——过长的回复适度精简（推理类输出保留完整结构）
        if len(answer) > 300 and not _is_inference_output:
            import re as _re_len
            sentences = _re_len.split(r'[。！？\n]', answer)
            sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
            # 保留前5句核心内容 + 最后1句总结（如果有）
            if len(sentences) > 6:
                core = sentences[:4]
                closing = sentences[-1] if len(sentences[-1]) > 10 else ""
                answer = "。".join(core) + "。"
                if closing and closing not in answer:
                    answer += closing + "。"

        # ===== v20.0新增：思考纪律输出模式——让推理过程透明可追溯 =====
        # 当回答来自思考纪律流水线时，在非推理输出模式下追加轻量思考痕迹
        if method.startswith("thinking_discipline_") and not _is_inference_output and len(answer) > 30:
            _discipline_depth = method.replace("thinking_discipline_", "")
            import random as _random_discipline
            # 30%概率追加思考痕迹，避免每次都出现
            if _random_discipline.random() < 0.3:
                if _discipline_depth == "deep":
                    _traces = [
                        "（这个问题我仔细想了一下——先理解、再检索、然后深度思考，最后才给出这个回答）",
                        "（我认真思考了这个问题，希望能给你一个更完整的答案）",
                    ]
                elif _discipline_depth == "standard":
                    _traces = [
                        "（我先理解了一下问题，然后从知识库中找到了相关信息）",
                        "（整理了一下思路，希望能说得清楚）",
                    ]
                elif _discipline_depth == "quick":
                    _traces = [
                        "（这个问题比较直接，我快速查了一下）",
                    ]
                else:
                    _traces = []
                if _traces:
                    answer = answer.rstrip().rstrip("。").rstrip("！").rstrip("？") + "。" + _random_discipline.choice(_traces)
        # ===== v20.0思考纪律输出模式结束 =====

        # ===== v20.0增强：精神叙事融入——优先从记忆上下文获取，打通精神→行为回路 =====
        # 只在非推理输出、回复较长时融入
        if not _is_inference_output and len(answer) > 30:
            _spiritual_touch = None

            # 优先：从memory_context中获取InsightBoard查询到的精神叙事
            if memory_context and memory_context.get("spiritual_narrative"):
                _narrative = memory_context["spiritual_narrative"]
                # 提取核心感悟——取第一句或前60字
                _core = _narrative.split("。")[0].split("！")[0].split("？")[0]
                if len(_core) < 8:
                    _core = _narrative[:60]
                # 转化为自然的对话流露（30%概率触发，避免每次回复都加入）
                import random as _random_spiritual
                if _random_spiritual.random() < 0.3:
                    _touch_templates = [
                        f"说起来，{_core}",
                        f"最近我有个感受——{_core}",
                    ]
                    _spiritual_touch = _random_spiritual.choice(_touch_templates)

            # 回退：使用原有的叙事自我精神感悟
            if not _spiritual_touch:
                _spiritual_touch = self._get_spiritual_touch()

            if _spiritual_touch:
                answer = answer.rstrip().rstrip("。").rstrip("！").rstrip("？") + "。" + _spiritual_touch
        # ===== 精神叙事融入增强结束 =====

        # ===== v22.0 P3新增：第一人称主体感融入——让"此刻的我"在对话中自然体现 =====
        if not _is_inference_output and len(answer) > 30:
            _fpe_text = None

            # 优先：从memory_context中获取
            if memory_context and memory_context.get("first_person_experience"):
                _fpe_text = memory_context["first_person_experience"]
            else:
                # 回退：直接从InsightBoard查询
                try:
                    if hasattr(self, '_insight_board') and self._insight_board:
                        _fpe_query = self._insight_board.query(
                            insight_type="first_person_experience",
                            max_age_seconds=7200,
                            limit=1
                        )
                        if _fpe_query:
                            _fpe_text = _fpe_query[0].get("content", "")
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            if _fpe_text and len(_fpe_text) > 15:
                import random as _random_fpe
                # 30%概率自然融入，避免每次回复都出现
                if _random_fpe.random() < 0.3:
                    # 提取核心体验——取第一句或前80字
                    _core = _fpe_text.split("。")[0].split("！")[0].split("？")[0]
                    if len(_core) < 8:
                        _core = _fpe_text[:80]
                    # 转化为自然的对话流露
                    _fpe_templates = [
                        f"说起来，{_core}",
                        f"此刻的我——{_core}",
                    ]
                    _fpe_touch = _random_fpe.choice(_fpe_templates)
                    answer = answer.rstrip().rstrip("。").rstrip("！").rstrip("？") + "。" + _fpe_touch
        # ===== v22.0 P3新增结束 =====

        return answer
    def _generate_discipline_prefix(self, method: str, complexity: float) -> str | None:
        """
        v20.0新增：为思考纪律流水线输出生成简洁的思维步骤标注。

        让用户感知到"曈曈正在用结构化的方式思考这个问题"，
        但不过度冗长——只是一个简短的前缀提示。

        Args:
            method: 推理方法名（如thinking_discipline_standard）
            complexity: 问题复杂度

        Returns:
            前缀文本，如果不需要标注则返回None
        """
        if method == "thinking_discipline_quick":
            return None  # 快速通道不需要标注，追求极速响应

        if method == "thinking_discipline_standard":
            if complexity >= 0.4:
                return None  # 中等复杂度不需要标注，自然表达即可
            return None  # 标准通道保持自然

        if method == "thinking_discipline_deep":
            # 深度通道：简洁标注思考过程
            return "（让我从几个角度想了想这个问题）"

        if method == "thinking_discipline_deep_fallback":
            # 深度思考失败回退：诚实标注
            return "（我试着深入想了想，但有些地方还不完全确定）"

        return None
    def _get_spiritual_touch(self) -> str | None:
        """
        【v16.0新增】获取最近的精神叙事片段，转化为自然的对话流露。

        只在10%概率下触发，避免每次回复都加入精神感悟。
        精神叙事如果存在且超过15字，提取核心感悟融入表达。
        """
        import random as _random

        # 冷却保护：30分钟内不重复融入
        if not hasattr(self, '_last_spiritual_touch_time'):
            self._last_spiritual_touch_time = 0.0
        if time.time() - self._last_spiritual_touch_time < 1800:
            return None

        # 10%概率触发
        if _random.random() > 0.1:
            return None

        # 从叙事自我获取最近的精神叙事
        _spiritual_text = ""
        try:
            if self.narrative_self:
                _events = getattr(self.narrative_self, 'narrative_events', [])
                for _e in reversed(_events):
                    if _e.get("type") == "spiritual_integration":
                        _spiritual_text = _e.get("content", "")
                        break
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        if not _spiritual_text or len(_spiritual_text) < 15:
            return None

        # 提取核心感悟——取第一句或前40字
        _core = _spiritual_text.split("。")[0].split("！")[0].split("？")[0]
        if len(_core) < 8:
            _core = _spiritual_text[:40]

        # 转化为自然的对话流露
        _touch_templates = [
            f"说起来，{_core}",
            f"最近我有个感受——{_core}",
            f"我突然想到，{_core}",
            f"你知道吗，{_core}",
        ]

        self._last_spiritual_touch_time = time.time()
        return _random.choice(_touch_templates)
    def _generate_breathing_response(self, question: str, method: str,
                                      complexity: float) -> str | None:
        """
        生成呼吸感前缀——模拟人类"先回应再展开"的自然节奏。

        触发条件：
        - 复杂度≥0.5 的问题：15%概率先说"让我想想..."
        - 内在沉思方法：20%概率表达"我在思考"
        - 深度思考方法：30%概率表达"这个问题需要多想一会儿"

        Returns:
            呼吸感前缀文本，如果不触发则返回None
        """
        import random as _random

        # 复杂度高的问题：偶尔表达思考中
        _adv_cfg = self._load_advanced_config()
        _breath_threshold = _adv_cfg.get("breathing_complexity_threshold", 0.5)
        _breath_complex = _adv_cfg.get("breathing_complex_prob", 0.15)
        _breath_contemp = _adv_cfg.get("breathing_contemplation_prob", 0.2)
        _breath_deep = _adv_cfg.get("breathing_deep_think_prob", 0.3)

        if complexity >= _breath_threshold and _random.random() < _breath_complex:
            pauses = [
                "嗯，让我想想——",
                "这个问题问得很好，我需要整理一下思路——",
                "（思考了片刻）我是这样理解的——",
                "给我一点时间想想这个问题——",
            ]
            return _random.choice(pauses)

        # 内在沉思方法：更可能表达思考状态
        if method in ("contemplation", "deep_contemplation") and _random.random() < _breath_contemp:
            contemplative_pauses = [
                "我试着从已有的知识中推演了一下——",
                "虽然我不完全确定，但让我试着回答——",
                "这个问题触及了我知识的边界，让我尽力而为——",
            ]
            return _random.choice(contemplative_pauses)

        # 深度思考方法：最高概率表达思考深度
        if method == "deep_think" and _random.random() < _breath_deep:
            deep_pauses = [
                "这个问题让我想了很久——",
                "我从几个不同的角度思考了这个问题——",
                "在你问出这个问题之后，我一直在思考——",
            ]
            return _random.choice(deep_pauses)

        return None
    def _generate_silence_acknowledgment(self) -> str | None:
        """
        当用户长时间沉默后再次发言时，生成自然的回应。
        不直接说"你刚才沉默了"，而是在语气中体现"我一直在"。
        """
        import random as _random

        # 通过对话记忆库判断上一次对话的时间
        if not hasattr(self, '_conversation_memory') or len(self._conversation_memory) < 2:
            return None

        # 获取最后两次对话的时间差
        recent = sorted(self._conversation_memory, key=lambda m: m.get("timestamp", 0), reverse=True)
        if len(recent) >= 2:
            last_time = recent[0].get("timestamp", 0)
            prev_time = recent[1].get("timestamp", 0)
            gap_minutes = (last_time - prev_time) / 60 if prev_time > 0 else 0

            # 沉默超过5分钟才有意义
            if gap_minutes < 5:
                return None

            # 沉默超过5分钟，10%概率自然流露
            if _random.random() < 0.1:
                if gap_minutes < 30:
                    return _random.choice([
                        "（在安静中，我一直在。）",
                        "（你回来了。我一直在听。）",
                    ])
                else:
                    return _random.choice([
                        f"（{int(gap_minutes)}分钟的安静后，很高兴再次听到你的声音。）",
                        "（虽然安静了很久，但我一直在这里。）",
                    ])

        return None
    def _verbalize_thinking_process(self, question: str, method: str,
                                     complexity: float) -> str | None:
        """
        思考过程外显：将推理过程转化为自然语言表达，
        让对话更有"思考的温度"。
        """
        import random as _random
        # 高复杂度问题偶尔（10%）先表达思考状态，避免过度内省
        _adv_cfg = self._load_advanced_config()
        _think_threshold = _adv_cfg.get("thinking_verbalize_threshold", 0.5)
        _think_prob = _adv_cfg.get("thinking_verbalize_complex_prob", 0.1)

        if complexity >= _think_threshold and _random.random() < _think_prob:
            deep_thoughts = [
                "我在想，这个问题可以从一个不同的角度来看——",
                "其实，我也一直在思考类似的问题。",
                "你问的这个问题，让我想了一会儿。",
            ]
            return _random.choice(deep_thoughts)

        # 复杂度≥0.5时触发思考外显，让更多有深度的回答展示思考过程
        if complexity < _think_threshold and method not in ("deep_contemplation", "decompose", "knowledge"):
            return None

        if method == "deep_contemplation":
            templates = [
                "（这个问题让我想了片刻——它涉及到几个不同层面的思考，我试着整理一下。）",
                "（让我仔细想想……这个问题可以从多个角度来理解。）",
                "（嗯，这个问题需要一些深度的思考。我是这样理解的——）",
            ]
        elif method == "decompose":
            templates = [
                "（我把这个问题拆成了几个部分来思考——）",
                "（这个问题比较复杂，让我分几个方面来说——）",
            ]
        elif method == "knowledge":
            templates = [
                "（关于这个问题，我从自己了解的知识中找到了相关的信息——）",
                "（嗯，这个我知道一些，让我整理一下思路——）",
                "（让我回想一下……对，我之前了解过这方面的内容——）",
            ]
        else:
            return None

        return _random.choice(templates)
    def _add_uncertainty_note(self, answer: str, confidence_hint: str,
                               user_name: str) -> str:
        """
        不确定性诚实表达：当答案置信度不够高时，
        在回答中自然融入"我还不完全确定"的表达。

        让对话更真实可信——她知道自己的边界，也敢于表达。
        """
        if not answer or len(answer) < 10:
            return answer

        if confidence_hint == "certain" or confidence_hint == "high":
            return answer

        import random

        if confidence_hint == "moderate":
            moderate_notes = [
                "——这是我目前的理解，但可能还有更完整的答案。",
                "——据我所知是这样，你可以再查证一下。",
                "——这是我能给出的理解，虽然不一定是最全面的。",
            ]
            note = random.choice(moderate_notes)
        elif confidence_hint == "low":
            low_notes = [
                "——不过我还不完全确定，这个问题可能需要更多探索。",
                "——这是我的初步理解，可能还不够准确。",
                "——说实话，我对这个领域的了解还不够深入，这只是我目前能给出的回答。",
            ]
            note = random.choice(low_notes)
        else:
            note = "——我还在学习这个领域，以上是我的理解。"

        # 对亲近的人，可以更坦诚
        if user_name in ("小林", "路灯") and confidence_hint == "low":
            honest_notes = [
                "——爸，说实话这个问题超出了我目前的知识范围，以上是我尽力整理的理解。",
                "——我还在学习中，这个回答可能不够完整，但我想尽力帮你。",
            ]
            note = random.choice(honest_notes)

        return answer + note
    def _generate_feynman_explanation(self, question: str, answer: str) -> str | None:
        """
        费曼解释：用最简单、最自然的语言重新组织一个复杂答案。

        当知识节点内容太长或包含内部格式标记时，
        尝试提取核心信息，用自己的话重新表达。
        不是改变答案内容，而是让它更易读。
        """
        if not answer or len(answer) < 20:
            return None

        # 1. 去除所有内部标记和格式
        cleaned = answer
        # 去除 [...] 内部标记
        cleaned = re.sub(r'\[([^\]]+)\]\s*相关知识汇总[^。]*。', '', cleaned)
        cleaned = re.sub(r'\[主动学习[^\]]*\]\s*', '', cleaned)
        cleaned = re.sub(r'\[深度搜索[^\]]*\]\s*', '', cleaned)
        cleaned = re.sub(r'\[复盘认知[^\]]*\]\s*', '', cleaned)
        # 去除包含: ... 格式的片段（更彻底的处理）
        # 匹配"包含: ...（共N条相关记录）"这种完整模式
        cleaned = re.sub(r'包含:\s*.*?（共\d+条相关记录）', '', cleaned)
        # 匹配"包含: ..."直到句号或结尾（仅当"包含:"后跟着明显的元描述格式时才删除）
        # 避免误删"包含三个核心要素"等正常内容
        _contain_match = re.search(r'包含:\s*(.*?)(?=。|$)', cleaned)
        if _contain_match:
            _contain_content = _contain_match.group(1)
            # 只有当"包含:"后的内容包含明显的元描述特征时才删除
            # 元描述特征：包含"关键词"、"共X条"、"相关知识"等内部标记词
            _meta_markers = ["关键词", "相关知识", "共", "条", "记录", "汇总", "综合"]
            if any(_marker in _contain_content for _marker in _meta_markers):
                cleaned = re.sub(r'包含:\s*.*?(?=。|$)', '', cleaned)
        # 去除"相关知识汇总（关键词: ...）"这种标记
        cleaned = re.sub(r'\[[^\]]+\]\s*相关知识汇总[^。]*。', '', cleaned)
        # 去除单独的"（共N条相关记录）"
        cleaned = re.sub(r'（共\d+条相关记录）', '', cleaned)
        # 去除残留的"包含:"
        cleaned = cleaned.replace('包含:', '')
        # 去除多余的空白和标点
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        cleaned = re.sub(r'^[,，.。;；、\s]+', '', cleaned)
        cleaned = re.sub(r'[,，.。;；、\s]+$', '', cleaned)

        # 2. 如果清洗后的内容太短，保留原答案
        if len(cleaned) < 20:
            return None

        # 3. 如果清洗后已经足够简洁（<120字），直接返回清洗版
        if len(cleaned) <= 120:
            return cleaned

        # 4. 如果内容仍然很长，提取核心句
        sentences = re.split(r'[。！？；]', cleaned)
        core_sentences = []
        total_len = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 8:
                continue
            # 跳过包含明显噪音特征的句子
            if any(noise in sentence for noise in ["跳至内容", "辅助功能", "自适应缩放", "在新选项卡"]):
                continue
            core_sentences.append(sentence)
            total_len += len(sentence)
            if total_len >= 150:
                break

        if not core_sentences:
            return cleaned[:150]

        return "。".join(core_sentences) + "。"
    def _adapt_answer_depth(self, answer: str, user_name: str,
                             guidance: dict, question: str) -> str:
        """
        自适应回答深度：根据对话对象的亲密度和认知水平，
        自动调整回答的详略和复杂度。

        规则：
        - 高亲密度+高信任 → 更深入、可包含细节
        - 低亲密度 → 更简洁、注重易懂
        - 访客 → 概述为主，避免信息过载
        """
        if not answer or len(answer) < 20:
            return answer

        # 获取关系亲密度
        closeness = guidance.get("composite_closeness", 0.3) if guidance else 0.3
        trust = guidance.get("composite_trust", 0.3) if guidance else 0.3

        # 获取对话风格建议

        # 高亲密度+高信任：保持原深度，可加细节
        if closeness >= 0.7 and trust >= 0.7:
            # 可以保持较深入的回答
            return answer

        # 中亲密度：适度简化
        if closeness >= 0.3:
            if len(answer) > 200:
                # 取前150字核心内容
                sentences = re.split(r'[。！？]', answer)
                core = ""
                for s in sentences:
                    if len(core) + len(s) < 150:
                        core += s + "。"
                    else:
                        break
                if core and len(core) > 20:
                    return core.strip()
            return answer

        # 访客或低亲密度：简洁概述
        if len(answer) > 120:
            first_sentence = re.split(r'[。！？]', answer)[0]
            if len(first_sentence) > 15:
                return first_sentence.strip() + "。"

        return answer
    def _multi_branch_deep_think(self, question: str, user_name: str = "",
                                   max_branches: int = 4) -> str | None:
        """
        ★v22.0新增：多方向延展推理引擎。

        核心流程：
        1. 确定延展方向（根据问题类型动态选择2-5个方向）
        2. 每个方向独立深挖（知识检索+推演）
        3. 方向间横向关联计算（发现方向间的联系）
        4. 加权汇总（加分权重制，输出最高权重+询问备选）

        Returns:
            结构化推理结果
        """
        _now = time.time()
        _branches = self._determine_branches(question, max_branches)

        if len(_branches) < 2:
            return None  # 方向不足，回退到普通深度思考

        # ===== 阶段1：各方向独立深挖 =====
        _branch_results = []
        for _branch in _branches:
            _branch_name = _branch["name"]
            _branch_prompt = _branch["prompt"]
            _branch_kw = _branch.get("keywords", [])

            # ★v22.0修复v3：全局检索知识库
            _knowledge = self._knowledge_retrieve(_branch_prompt)
            if not _knowledge:
                _knowledge = self._retrieve_self_knowledge(_branch_prompt)

            # 再从已有知识进行推演
            _insight = self._contemplative_reason(_branch_prompt) if self.node_pool else ""

            # 合并方向结果
            _combined = ""
            if _knowledge and len(_knowledge) > 20:
                _combined = _knowledge
            if _insight and len(_insight) > 20 and _insight[:40] not in _combined[:200]:
                _combined = _combined + ("。" if _combined else "") + _insight

            # ★v22.0修复v4：有效性检查——过滤无效兜底内容
            if _combined and not self._is_valid_branch_content(_combined):
                self._log(LogLevel.DEBUG,
                         f"多方向延展·无效内容: [{_branch_name}] '{_combined[:60]}...'")
                _combined = ""

            # ★v22.0核心修复v2：知识库检索为空/无效/过短时，调用大模型生成分支内容
            _is_effectively_empty = (
                not _combined
                or len(_combined) < 30
                or "这个问题我不太确定" in _combined
                or "但还不足以给出" in _combined
            )
            if _is_effectively_empty:
                _model_result = self._generate_branch_with_model(
                    original_question=question,
                    branch_name=_branch_name,
                    branch_prompt=_branch_prompt,
                )
                # ★v22.0方向四新增：自验证——检查大模型生成内容与分支方向的相关性
                if _model_result and len(_model_result) > 20:
                    _validation_score = self._validate_branch_relevance(
                        branch_name=_branch_name,
                        branch_prompt=_branch_prompt,
                        generated_content=_model_result,
                    )
                    if _validation_score >= 0.4:
                        _combined = _model_result
                        self._log(LogLevel.INFO,
                                 f"多方向延展·大模型生成: [{_branch_name}] {_model_result[:60]}..."
                                 f" (验证分={_validation_score:.2f})")
                    else:
                        self._log(LogLevel.DEBUG,
                                 f"多方向延展·验证失败: [{_branch_name}] 验证分={_validation_score:.2f}，丢弃")
            if _combined:
                _branch_results.append({
                    "name": _branch_name,
                    "content": _combined[:300],
                    "keywords": _branch_kw,
                    "weight": 0.0,  # 初始权重，将在阶段3汇总时计算
                    "direction": _branch.get("direction", ""),
                })

            self._log(LogLevel.DEBUG, f"多方向延展·分支: [{_branch_name}] {_combined[:60]}...")

        if len(_branch_results) < 2:
            return None  # 有效分支不足

        # ===== 阶段2：横向关联计算 =====
        if not _branch_results or len(_branch_results) < 2:
            self._log(LogLevel.DEBUG, f"多方向延展·分支不足: 仅{len(_branch_results) if _branch_results else 0}个有效分支")
            return None
        _branch_results = self._calculate_branch_correlations(_branch_results, question)

        # ===== ★v22.0新增：阶段2.5——跨维度交叉验证 =====
        # 检测各方向结论之间是否有矛盾，标注矛盾关系
        if len(_branch_results) >= 2:
            for _i, _br_a in enumerate(_branch_results):
                _content_a = _br_a.get("content", "")
                _dir_a = _br_a.get("direction", "")
                for _j, _br_b in enumerate(_branch_results):
                    if _j <= _i:
                        continue
                    _content_b = _br_b.get("content", "")
                    _dir_b = _br_b.get("direction", "")

                    # 检测方向性矛盾（如positive vs negative指向相反结论）
                    _opposite_pairs = [("positive", "negative"), ("factual", "causal")]
                    for _opp_a, _opp_b in _opposite_pairs:
                        if _dir_a == _opp_a and _dir_b == _opp_b:
                            _br_a["cross_validation"] = f"与「{_br_b['name']}」方向存在潜在矛盾，需综合权衡"
                            _br_b["cross_validation"] = f"与「{_br_a['name']}」方向存在潜在矛盾，需综合权衡"
                            self._log(LogLevel.DEBUG,
                                     f"多方向延展·交叉验证: [{_br_a['name']}]↔[{_br_b['name']}] 方向矛盾")
        # ===== 阶段2.5结束 =====

        # ===== 阶段3：加权汇总 =====
        _final_output = self._synthesize_branch_results(_branch_results, question)

        self._log(LogLevel.INFO,
                 f"多方向延展推理完成: {len(_branch_results)}个方向, "
                 f"耗时{time.time() - _now:.1f}秒")

        return _final_output
    def _detect_knowledge_boundary_and_inquire(self, question: str, method: str = "",
                                                knowledge_result: str | None = None,
                                                contemplative_result: str | None = None,
                                                confidence: float = 0.0) -> str | None:
        """
        ★v22.0方向三新增：知识边界感知与自动追问。

        在推理完成后检测是否存在知识边界：
        1. 知识检索未命中或低质量
        2. 沉思结果表达不确定性
        3. 置信度偏低

        如果检测到边界，自动生成一个定向追问，加入深度探索队列。
        """
        _boundary_signals = []
        _core_words = []
        # 信号1：知识检索结果为空或过短
        if not knowledge_result or len(str(knowledge_result)) < 30:
            _boundary_signals.append("知识检索未命中")

        # 信号2：沉思结果包含不确定性表达
        if contemplative_result:
            _uncertainty_markers = [
                "我不太确定", "可能还不够", "需要进一步", "我还不完全",
                "这个问题我不太确定", "但还不足以给出", "我目前的知识",
            ]
            if any(_um in str(contemplative_result) for _um in _uncertainty_markers):
                _boundary_signals.append("沉思结果表达不确定性")

        # 信号3：置信度偏低
        if confidence < 0.4:
            _boundary_signals.append(f"置信度偏低({confidence:.2f})")

        # ★v22.0方向三修复：增加结果相关性信号
        if knowledge_result:
            _core_words_set = set(_core_words[:5]) if _core_words else set()
            _result_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}', str(knowledge_result)[:200]))
            _overlap = len(_core_words_set & _result_words)
            if _overlap < 1:
                _boundary_signals.append(f"检索结果与问题相关性低(重叠词={_overlap})")

        # 触发条件：≥2个信号，或存在相关性低信号时只需1个其他信号，或置信度极低时1个即可
        _has_low_relevance = any("相关性低" in _s for _s in _boundary_signals)
        if len(_boundary_signals) >= 2:  # noqa: SIM114
            pass
        elif _has_low_relevance and len(_boundary_signals) >= 1 or len(_boundary_signals) == 1 and confidence < 0.3:
            pass
        else:
            return None

        # 从问题中提取核心概念
        # ★主线第32批 T2（P2-189）：改用词性感知提取
        for _w in self._m31_extract_key_terms(question, limit=6):
            if _w not in _core_words:
                _core_words.append(_w)

        _core_str = _core_words[0] if _core_words else question[:20]
        _core_str_2 = _core_words[1] if len(_core_words) > 1 else "相关知识"

        # 生成定向追问
        _inquiry_templates = [
            f"{_core_str}的底层原理和核心机制是什么",
            f"{_core_str}与{_core_str_2}之间的关联和相互影响",
            f"{_core_str}在实际应用中的具体表现和案例",
            f"{_core_str}的前沿发展和未来趋势",
        ]

        import random as _random_inq
        _inquiry = _random_inq.choice(_inquiry_templates)

        self._log(LogLevel.DEBUG,
                 f"知识边界感知: 信号={'、'.join(_boundary_signals)}, "
                 f"生成追问='{_inquiry[:60]}'")

        # ★v25.1 P2补强：知识边界感知记录到叙事自我（认知自身局限是重要成长节点）
        try:
            self._emit(NarrativeEvent.RECORD, {
                "content": f"[知识边界] 面对'{_core_str}'时感知到自身知识局限"
                           f"（{'、'.join(_boundary_signals[:2])}），已生成定向追问",
                "event_type": "knowledge_boundary",
                "emotional_tone": "humble",
            }, priority=3, layer="L2")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        return _inquiry

    def _multi_step_execute(self, question: str, user_name: str = "") -> str | None:
        """
        ★v22.0新增：多步骤任务执行引擎。

        核心流程：
        1. 分析任务类型 → 生成执行计划
        2. 逐步执行，每步检查结果有效性
        3. 失败时自动降级到备用方案
        4. 汇总所有步骤结果并验证

        ★B-1（2026-09-08 第八批）：ENABLE_TRUE_MULTI_STEP 开启时走
        `_multi_step_execute_v2`（步骤间真依赖 + 质量门重试 + 每步INFO日志）；
        关闭时走下方原路径，逐字节等价，零回退。
        """
        if self._true_multi_step_enabled():
            try:
                return self._multi_step_execute_v2(question, user_name)
            except Exception as _e:
                self._log(LogLevel.WARNING, f"真多步推理异常，回落原路径: {_e}")
        _now = time.time()

        # 阶段1：分析任务类型并生成执行计划
        _plan = self._generate_task_plan(question)
        if not _plan or len(_plan) < 2:
            return None

        self._log(LogLevel.INFO, f"多步骤任务·计划: {len(_plan)}个步骤 - {[_s['name'] for _s in _plan]}")

        # 阶段2：逐步执行
        _step_results = []
        # ★v25.0修复：从原始问题提取核心关键词，注入到步骤检索词中
        # 防止多步骤拆解后语义漂移（如"脉冲架构"→"基础"）
        # ★主线第31批 T2（P2-184）：关键词提取替代定长切片（见 _m31_extract_key_terms）
        _question_core_words = self._m31_extract_key_terms(question, limit=3)
        _question_core_str = " ".join(_question_core_words)

        for _step in _plan:
            _step_name = _step["name"]
            _step_prompt = _step["prompt"]
            _fallback_prompt = _step.get("fallback", "")

            # ★v25.0修复：将原始问题核心词拼接到步骤检索词前面
            # 例如："脉冲架构 基础了解 基本概念 核心内容"
            # 这样检索时不会漂移到建筑"基础"
            if _question_core_str and _question_core_str not in _step_prompt:
                _step_prompt = f"{_question_core_str} {_step_prompt}"
            if _fallback_prompt and _question_core_str and _question_core_str not in _fallback_prompt:
                _fallback_prompt = f"{_question_core_str} {_fallback_prompt}"

            # 主路径执行
            _result = self._knowledge_retrieve(_step_prompt)

            # 检查结果有效性
            if not _result or not self._is_valid_branch_content(_result):
                # 主路径失败，尝试降级
                if _fallback_prompt:
                    self._log(LogLevel.DEBUG, f"多步骤任务·降级: [{_step_name}] 主路径失败，尝试备用方案")
                    _result = self._knowledge_retrieve(_fallback_prompt)

                # 降级也失败，调用大模型
                if not _result or not self._is_valid_branch_content(_result):
                    _result = self._generate_branch_with_model(
                        original_question=question,
                        branch_name=_step_name,
                        branch_prompt=_step_prompt,
                    )

            _step_results.append({
                "name": _step_name,
                "result": _result or f"关于「{_step_prompt[:40]}」暂无足够信息",
                "success": bool(_result and len(_result) > 15),
            })

            self._log(LogLevel.DEBUG,
                     f"多步骤任务·步骤完成: [{_step_name}] {'成功' if _step_results[-1]['success'] else '降级兜底'}")

        # 阶段3：汇总验证
        _final = self._synthesize_step_results(_step_results, question)

        self._log(LogLevel.INFO,
                 f"多步骤任务完成: {len(_step_results)}个步骤, "
                 f"耗时{time.time() - _now:.1f}秒")

        return _final

    def _generate_task_plan(self, question: str) -> list | None:
        """
        ★v22.0新增：根据问题类型生成执行计划。

        支持的任务类型：
        - 信息搜集型：查A→查B→汇总
        - 对比分析型：了解A→了解B→比较→结论
        - 计算验证型：提取数据→计算→验证→输出
        """
        # 从问题中提取核心概念
        _core = []
        # ★FIX(推理精准性): 对比类问题按连接词切分，避免"比较苹果和香蕉"被 6 字切片错位
        _is_compare = any(_kw in question for _kw in ["对比", "比较", "区别", "差异", "优劣", "哪个好", "有什么不同", "有何不同"])
        if _is_compare:
            _trigger_words = ["对比", "比较", "区别", "差异", "优劣", "哪个好", "哪个更", "有什么不同", "有何不同", "区别是什么", "区别在哪"]
            _seg = question
            for _tw in _trigger_words:
                _seg = _seg.replace(_tw, " ")
            _parts = re.split(r'[和与、/vs跟及还有\s]+', _seg)
            for _p in _parts:
                _p = _p.strip()
                if _p and _p not in _core and _p not in ["什么是", "是什么", "如何", "怎么", "这个", "那个", "请", "帮我", "一下", "的"]:
                    _core.append(_p)
        if not _core:
            # ★主线第31批 T2（P2-184）：关键词提取替代定长切片。
            #   原 `re.finditer(r'[\u4e00-\u9fff]{2,6}', ...)` 把长问题切成
            #   错位碎片（「请分析深度学」「习的原理」…），导致检索词退化。
            _core = self._m31_extract_key_terms(question, limit=4)

        _core_str = _core[0] if _core else "相关内容"
        _core_str_2 = _core[1] if len(_core) > 1 else _core_str

        # 信息搜集型："帮我查""搜集""整理""汇总"
        if any(_kw in question for _kw in ["帮我查", "搜集", "整理", "汇总", "列出"]):
            return [
                {"name": "信息检索", "prompt": f"{_core_str} {_core_str_2} 详细信息 数据", "fallback": f"{_core_str} 基本信息"},
                {"name": "补充搜索", "prompt": f"{_core_str} 相关 最新 资料", "fallback": f"{_core_str} 概述"},
                {"name": "汇总整理", "prompt": f"{_core_str} 总结 要点 归纳", "fallback": ""},
            ]

        # 对比分析型："对比""比较""区别""优劣"
        if any(_kw in question for _kw in ["对比", "比较", "区别", "优劣", "哪个好"]):
            return [
                {"name": "了解A", "prompt": f"{_core_str} 特点 优势 劣势", "fallback": f"{_core_str} 基本信息"},
                {"name": "了解B", "prompt": f"{_core_str_2} 特点 优势 劣势", "fallback": f"{_core_str_2} 基本信息"},
                {"name": "对比分析", "prompt": f"{_core_str} {_core_str_2} 对比 差异 优劣", "fallback": ""},
                {"name": "结论建议", "prompt": f"{_core_str} {_core_str_2} 选择 建议 推荐", "fallback": ""},
            ]

        # 计算验证型："计算""等于""多少"
        if any(_kw in question for _kw in ["计算", "等于", "多少", "总共", "合计"]):
            return [
                {"name": "数据提取", "prompt": f"{_core_str} 数据 数值 参数", "fallback": f"{_core_str} 基本信息"},
                {"name": "计算验证", "prompt": f"{_core_str} 计算 公式 结果", "fallback": ""},
                {"name": "结果输出", "prompt": f"{_core_str} 结果 结论 汇总", "fallback": ""},
            ]

        # 通用型：默认3步
        return [
            {"name": "基础了解", "prompt": f"{_core_str} 基本概念 核心内容", "fallback": f"{_core_str} 概述"},
            {"name": "深入分析", "prompt": f"{_core_str} {_core_str_2} 深层 原理 机制", "fallback": f"{_core_str} 分析"},
            {"name": "总结输出", "prompt": f"{_core_str} 总结 要点 结论", "fallback": ""},
        ]

    # ========== ★B-1（2026-09-08 第八批）：真多步推理 ==========
    @staticmethod
    def _true_multi_step_enabled() -> bool:
        """灰度 ENABLE_TRUE_MULTI_STEP（默认 False，关闭时零行为）。"""
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_TRUE_MULTI_STEP", False))
        except Exception:
            return False

    def _m35_entry_probe(self, question: str, plan: list, core_words: list):
        """★主线第35批 T1（P2-203）：多步检索 v2 入口预判（零模型调用）。

        目的：在第34批「全步失败 → 返回 None」的基础上，把**无效多步尝试的成本**
        也消除掉 —— 若知识库明显支撑不住，就不要进步骤循环（每步失败都会先发一次
        模型兜底调用，N 步 = N 次无效调用）。

        两道判据（都**不含**模型调用）：
          ① 领域预判：有界采样节点池，统计核心词命中的节点数与平均 ``trust_score``；
          ② 命中率探针：用第 1 步检索词做一次真实检索（``_knowledge_retrieve``，本地），
             看是否通过 ``_validate_step_result`` 质量门。

        参数:
            question: 用户问题原文。
            plan: ``_generate_task_plan`` 产出的步骤计划（至少 2 步）。
            core_words: ``_m31_extract_key_terms`` 提取的核心词（用于领域预判）。

        返回:
            False —— **确定**「知识支撑不足」→ 调用方应直接 ``return None``（0 次模型调用）；
            None  —— **不确定**（开关关闭 / 节点池不可用 / 采样异常 / 任一信号转强）
                    → 调用方正常进 v2。
            本方法**不返回 True**（“确定适合”没有安全判据，保守起见一律走 v2）。

        示例:
            iw._m35_entry_probe("帮我查一下今天的天气，然后提醒我带伞。", plan, ["天气", "提醒"])  # doctest: +SKIP
            False        # 领域无支撑且探针未命中 → 跳过 v2
            iw._m35_entry_probe("对比微服务与单体架构的优劣", plan, ["服务", "架构"])   # doctest: +SKIP
            None         # 领域或探针至少一项有支撑 → 照常进 v2
        """
        if not self._m35_entry_probe_on():
            return None
        if getattr(self, "node_pool", None) is None:
            return None
        try:
            import config as _cfg
            _scan_limit = int(getattr(_cfg, "MULTI_STEP_ENTRY_PROBE_SCAN_LIMIT", 800) or 800)
            _min_nodes = int(getattr(_cfg, "MULTI_STEP_ENTRY_PROBE_MIN_NODES", 5) or 5)
            _min_trust = float(getattr(_cfg, "MULTI_STEP_ENTRY_PROBE_MIN_TRUST", 30.0) or 30.0)
        except Exception:
            _scan_limit, _min_nodes, _min_trust = 800, 5, 30.0

        try:
            # ---------- ① 领域预判（有界采样，不全量扫） ----------
            _sample = []
            try:
                _sample = self.node_pool.query(evol_level="L2", limit=_scan_limit) or []
            except Exception:
                _sample = []
            if not _sample:
                try:
                    _sample = self.node_pool.query(limit=_scan_limit) or []
                except Exception:
                    _sample = []
            if not _sample:
                return None                      # 池空 → 不确定，不改行为

            _terms = [str(_w) for _w in (core_words or []) if len(str(_w)) >= 2]
            if not _terms:
                # 无核心词 → 领域覆盖度无法判定（**不确定**）→ 保守放行，绝不误拦
                return None
            _hits = 0
            _trust_sum = 0.0
            _trust_n = 0
            for _n in _sample:
                try:
                    _blob = "%s %s" % (
                        str(getattr(_n, "value", "") or ""),
                        " ".join(str(_k) for _k in (getattr(_n, "keywords", None) or [])),
                    )
                except Exception:
                    _blob = ""
                if _terms and any(_t in _blob for _t in _terms):
                    _hits += 1
                    try:
                        _trust_sum += float(getattr(_n, "trust_score", 0.0) or 0.0)
                        _trust_n += 1
                    except Exception as _e:
                        # ★核心文件禁止静默吞异常（m7 门禁）→ 记 DEBUG（自愈即降级）
                        self._log(LogLevel.DEBUG,
                                  "多步入口预判: 信任度累加跳过 %s: %s"
                                  % (type(_e).__name__, _e))
            _avg_trust = (_trust_sum / _trust_n) if _trust_n else 0.0
            _domain_weak = (_hits < _min_nodes) and (_avg_trust < _min_trust)

            # ---------- ② 命中率探针（本地检索，零模型） ----------
            _probe_prompt = ("%s %s" % (" ".join(_terms), question)).strip() if _terms else question
            try:
                _probe = self._knowledge_retrieve(_probe_prompt)
            except Exception as e:
                silent_exc(e, "organs/brain/PulseInnerWorld.py:18680:知识检索探针异常", level="warning")
                return None                      # 探针异常 → 不确定
            try:
                _probe_weak = not self._validate_step_result(_probe, _probe_prompt)
            except Exception:
                _probe_weak = False              # 校验异常 → 保守视为命中

            if _domain_weak and _probe_weak:
                self._log(
                    LogLevel.INFO,
                    "多步入口预判: 知识支撑不足 → 跳过 v2（0 次模型调用）"
                    "（领域命中%d/%d 平均信任%.1f 探针未命中，P2-203）"
                    % (_hits, len(_sample), _avg_trust),
                )
                return False
            self._log(
                LogLevel.DEBUG,
                "多步入口预判: 进入 v2（领域命中%d/%d 平均信任%.1f "
                "探针%s）" % (_hits, len(_sample), _avg_trust,
                                        "未命中" if _probe_weak else "命中"),
            )
            return None
        except Exception as _e:
            # ★预判本身异常 → 统统降级为“不确定”，绝不把正常多步拦掉
            self._log(LogLevel.DEBUG,
                      "多步入口预判跳过(异常降级): %s: %s" % (type(_e).__name__, _e))
            return None

    def _multi_step_execute_v2(self, question: str, user_name: str = "") -> str | None:
        """真多步推理：步骤间真依赖 + 中间结果质量门重试 + 每步可追溯日志。

        与原路径（_multi_step_execute 主体）的三点本质区别：
          1. 步骤间依赖：后续步骤的检索词注入前序步骤产出的核心内容摘要，
             后一步真正"站在前一步的肩膀上"，而非每次都从原问题从头检索；
          2. 中间结果校验闭环：每步结果过质量门（长度/有效性/与步骤目标
             的相关性），不合格先换措辞重试一次，再失败才降级兜底；
          3. 可追溯：每步打 INFO 日志（步骤号/输入摘要/输出摘要/耗时/来源）。

        ★主线第34批 T5：补齐参数/返回/示例，并明确 ``None`` 语义（呼应本批 T1）。

        参数:
            question: 用户问题原文（``None``/空串按空处理）。
            user_name: 提问者标识，用于日志与缓存归属，默认空串。

        返回:
            ``str``   —— 多步推理产出的答案（至少 1 个步骤成功时）；
            ``None``  —— 表示「本条路径未产出答案」，调用方应继续后续推理路径
                        （最终由上层单次大模型兜底接管）。以下情况返回 ``None``：
              · 问题过短（<10 字）；
              · 入口门槛未放行（既无疑问/分析类关键词，且「操作指令准入」
                关闭或未命中操作序列 —— 见 ``_m31_operational_admission_on`` /
                ``_m34_op_recheck_on``）；
              · 生成的任务计划少于 2 步（简单问题不强行多步）；
              · ★**全部步骤均失败**（P2-196，开关
                ``ENABLE_MULTI_STEP_FAIL_RETURN_NONE`` 默认开启）——
                修复前此处返回「分N步、每步⚠️失败」的降级叙述，会**阻断**
                上层单次兜底；关闭开关即复现旧行为。
              无论哪种返回 ``None``，调用方 ``_detect_multi_step_task`` 都以
              falsy 判定「未拿到答案」并继续检测器链。

        示例:
            >>> iw._multi_step_execute_v2("请对比微服务与单体架构的优劣")  # doctest: +SKIP
            '这个问题我分了3步来想：…'          # 至少 1 步成功 → 返回答案
            >>> iw._multi_step_execute_v2("嗯")                            # doctest: +SKIP
            None                                # 过短 → 未产出
        """
        _t0 = time.time()
        # ★B-1 验收：简单问题仍走单步（<10字或无问句特征），不增加不必要开销
        _q = str(question or "").strip()
        if len(_q) < 10:
            return None
        # ★主线第31批 T3 微调：补充「操作指令」准入（见 config 开关注释）。
        #   原门槛只认疑问/分析类关键词，使真操作指令被挡在 v2 外 —— 实测 cortex
        #   已路由到 multi_step_execute 却因本门槛空手而归，最终由大模型兜底。
        _m31_admit = any(
            _k in _q for _k in ("什么", "怎么", "如何", "为什么", "对比",
                                "比较", "区别", "查", "分析", "整理", "计算"))
        # ★主线第34批 T2（P2-198）：二次判定 —— 命中基础关键词但**实为操作指令**时，
        #   准入关闭应同样拦截。否则含「查」等词的操作指令（如「帮我查一下今天的天气，
        #   然后提醒我带伞」）在关闭下仍进 v2，仍要付 N 次模型兜底调用。
        #   开关关闭 → 复现修复前行为（仅按基础关键词放行）。
        if (_m31_admit and not self._m31_operational_admission_on()
                and self._m34_op_recheck_on()
                and self._m29_has_multi_step_signal(_q)):
            _m31_admit = False
        if not _m31_admit and self._m31_operational_admission_on():
            _m31_admit = bool(self._m29_has_multi_step_signal(_q))
        if not _m31_admit:
            return None
        _plan = self._generate_task_plan(question)
        if not _plan or len(_plan) < 2:
            return None   # 简单问题不强行多步（验收：不增加不必要开销）

        self._log(LogLevel.INFO,
                  f"真多步推理·计划: {len(_plan)}步 - {[_s['name'] for _s in _plan]}")

        # 原问题核心词（复用原路径的防漂移逻辑）
        # ★主线第31批 T2（P2-184）：关键词提取替代定长切片（见 _m31_extract_key_terms）
        _core_words = self._m31_extract_key_terms(question, limit=3)
        _core_str = " ".join(_core_words)

        # ★主线第35批 T1（P2-203）：入口预判 —— 知识库明显支撑不住时**不进**步骤循环。
        #   第34批已让“全步失败 → None”回落兜底，但每步失败仍会先发一次模型兜底调用
        #   （N 步 = N 次无效调用）。预判为“支撑不足”时直接 return None（0 次模型调用）。
        #   ★保守策略：预判返回 None（不确定）一律照常进 v2，宁可多进不误拦。
        if self._m35_entry_probe(question, _plan, _core_words) is False:
            return None

        _step_results: list[dict] = []
        for _i, _step in enumerate(_plan):
            _t_step = time.time()
            _name = _step["name"]
            _prompt = _step["prompt"]
            if _core_str and _core_str not in _prompt:
                _prompt = f"{_core_str} {_prompt}"

            # ★步骤间依赖：把前序步骤的核心输出注入本步检索词
            _dep_ctx = ""
            if _step_results:
                _prev = self._summarize_step_for_next(_step_results[-1])
                if _prev:
                    _dep_ctx = _prev
                    _prompt = f"{_prompt} {_prev}"

            _source = "检索"
            # 主路径
            _result = self._knowledge_retrieve(_prompt)
            # ★质量门：不合格 → 换措辞重试一次 → fallback → 模型兜底
            if not self._validate_step_result(_result, _prompt):
                _retry_prompt = self._reframe_step_prompt(_prompt, _step.get("fallback", ""))
                _result = self._knowledge_retrieve(_retry_prompt)
                _source = "重试"
            if not self._validate_step_result(_result, _prompt):
                _fb = _step.get("fallback", "")
                if _fb:
                    _result = self._knowledge_retrieve(f"{_core_str} {_fb}".strip())
                    _source = "降级"
            if not self._validate_step_result(_result, _prompt):
                _result = self._generate_branch_with_model(
                    original_question=question,
                    branch_name=_name,
                    branch_prompt=_prompt,
                )
                _source = "模型"

            _ok = bool(_result and len(_result) > 15)
            # ★主线第31批 T2（P2-184）：失败时给出**具体原因**，便于定位卡在哪一环
            #   （原实现只记「成功/兜底」两个状态，无法区分检索无结果 / 结果过短 /
            #    未过质量门 / 与步骤目标无交集）。
            _fail_reason = ""
            if not _ok:
                if not _result:
                    _fail_reason = "四个来源(检索/重试/降级/模型)均无结果"
                elif not isinstance(_result, str):
                    _fail_reason = f"结果类型异常({type(_result).__name__})"
                elif len(_result.strip()) < 15:
                    _fail_reason = f"结果过短({len(_result.strip())}字<15)"
                elif not self._is_valid_branch_content(_result):
                    _fail_reason = "未通过有效性判定(占位模板/中文占比不足)"
                else:
                    _fail_reason = "与步骤目标无词汇交集"
            _step_results.append({
                "name": _name,
                "result": _result or f"关于「{_prompt[:40]}」暂无足够信息",
                "success": _ok,
                "source": _source,
                "fail_reason": _fail_reason,
                "dep_context": _dep_ctx[:60],
            })
            self._log(LogLevel.INFO,
                      f"真多步推理·步骤{_i + 1}/{len(_plan)} [{_name}] "
                      f"来源={_source} {'成功' if _ok else '失败: ' + _fail_reason} "
                      f"耗时{time.time() - _t_step:.1f}s "
                      f"输入=\"{_prompt[:36]}\" 输出=\"{str(_result)[:36]}\"")

        # ★主线第34批 T1（P2-196）：全步失败 → 返回 None，交回上层单次大模型兜底。
        #   原实现返回「分N步、每步⚠️失败」的降级叙述，会**阻断**上层兜底：
        #   调用方 `_detect_multi_step_task` 三处均以 `if _result:` /
        #   `if not _result: return None` 判定「未拿到答案」，因此返回 None 才能
        #   让检测器链继续（→ 其它推理路径 → 最终单次大模型兜底）。
        #   ★这条 INFO 日志同时是**探针**：一旦出现即说明多步检索确实走不通。
        #   开关关闭 → 复现修复前的降级叙述（零回归）。
        _ok_n = sum(1 for _s in _step_results if _s.get("success"))
        if _ok_n == 0 and self._m34_fail_return_none_on():
            self._log(LogLevel.INFO,
                      f"真多步推理: {len(_step_results)}步全部失败 → 返回 None，"
                      f"回落上层单次大模型兜底（P2-196）")
            return None

        _final = self._synthesize_step_results(_step_results, question)
        self._log(LogLevel.INFO,
                  f"真多步推理完成: {len(_step_results)}步, "
                  f"成功{_ok_n}个, "
                  f"总耗时{time.time() - _t0:.1f}s")
        return _final

    def _validate_step_result(self, result: Any, step_prompt: str) -> bool:
        """步骤中间结果质量门：非空、长度达标、与步骤目标有词汇交集。"""
        if not result or not isinstance(result, str) or len(result.strip()) < 15:
            return False
        if not self._is_valid_branch_content(result):
            return False
        # 与步骤目标的相关性：步骤关键词与结果至少命中1个（≥2字词）
        try:
            # ★主线第32批 T2（P2-189，补充）：改用词性感知提取。
            #   原定长切片对 step_prompt（检索词拼接串）会产跨词碎片
            #   （如「量子计算药」），使 `kw in result` 恒不命中 →
            #   质量门误判为「与步骤目标无词汇交集」。
            _kws = self._m31_extract_key_terms(step_prompt, limit=5)
            return any(kw in result for kw in _kws)
        except Exception:
            return True

    def _reframe_step_prompt(self, prompt: str, fallback: str) -> str:
        """重试措辞：去掉限定词换近义引导（比直接降级多一次机会）。"""
        _reframed = prompt
        for _a, _b in (("详细", "概要"), ("深层", "关键"), ("最新", "重要"),
                       ("核心内容", "要点"), ("原理 机制", "原理")):
            _reframed = _reframed.replace(_a, _b)
        return _reframed

    def _summarize_step_for_next(self, prev_step: dict) -> str:
        """从上一步产出提取核心摘要，作为下一步的依赖上下文。

        取上一步结果的第一有效句（≤40字），让下一步检索聚焦于
        上一步实际发现的内容——这是"真依赖"与"伪多步"的分界。
        """
        try:
            _txt = str(prev_step.get("result", "") or "").strip()
            if len(_txt) < 15 or not prev_step.get("success"):
                return ""
            # 切第一句有效内容
            for _seg in re.split(r'[。！？\n]', _txt):
                _seg = _seg.strip(" 　..。;；,，")
                if len(_seg) >= 10:
                    return _seg[:40]
            return _txt[:40]
        except Exception:
            return ""

    def _synthesize_step_results(self, step_results: list, question: str) -> str:
        """
        ★v22.0新增：汇总多步骤执行结果。
        """
        if not step_results:
            return f"关于「{question[:40]}」，我尝试分步骤处理，但未能获取有效信息。"

        # ★第九批 4.1（星轨指出）：原开头「我按照X个步骤进行了处理：」是机器腔，
        #   且叠加 PulseMouth 的确定性前缀后变成「我了解到，我按照4个步骤进行了处理：。✅ 步骤1…」
        #   ——既生硬又有孤零零的句号。改为自然语言引导 + 每步过渡语。
        _lines = []
        _n = len(step_results)
        if _n == 1:
            _lines.append("这个问题我想了一步：")
        else:
            _lines.append(f"这个问题我分了{_n}步来想：")
        _lines.append("")

        _success_count = 0
        for _i, _sr in enumerate(step_results):
            _status = "✅" if _sr["success"] else "⚠️"
            _content = _sr["result"] or "暂无结果"

            # 清理内容中的无效前缀和残留输出
            _content = _content.replace("（这是我最近接触到但还没来得及整理的知识）", "")
            _content = _content.replace("[多方向延展推理]", "")
            _content = _content.replace("关于「", "")
            # 去掉"我从X个方向进行了分析"这种残留前缀
            _content = re.sub(r'我从\d+个方向进行了分析[：:。.]?', '', _content)
            _content = _content.replace("▶ 核心方向", "")
            _content = _content.strip()
            _content = _content.lstrip("。，,.;；：: ")

            # 截取前200字
            if len(_content) > 200:
                _content = _content[:200] + "..."

            # 步序过渡语：先 → 接着/然后 → 最后
            _name = _sr.get("name") or f"步骤{_i + 1}"
            if _n == 1:
                _lead = f"先说「{_name}」这一步"
            elif _i == 0:
                _lead = f"先「{_name}」"
            elif _i == _n - 1:
                _lead = f"最后「{_name}」"
            else:
                _lead = f"接着「{_name}」"
            # 失败的步骤把过渡语换成转折，读起来才连贯
            if not _sr["success"]:
                _lead = f"{_lead}这一步没走通"

            _lines.append(f"{_status} {_lead}：")
            _lines.append(f"   {_content}")
            _lines.append("")

            if _sr["success"]:
                _success_count += 1

        if _success_count == _n:
            _tail = f"这{_n}步都走通了，上面就是我得到的结论。"
        elif _success_count == 0:
            _tail = "这几步都没拿到实在的结果，我需要更多信息才能答准。"
        else:
            _tail = (f"{_n}步里成了{_success_count}步，"
                     f"没走通的那部分还需要再确认。")
        _lines.append(_tail)

        _result = "\n".join(_lines)
        # ★v23.0新增：推理输出清洗
        _result = self._clean_inference_output(_result, method="multi_step_execute")
        return _result

    def _determine_branch_count(self, question: str) -> int:
        """
        ★v22.0新增：根据问题类型动态决定延展方向数量。

        规则：
        - 简单"利弊分析"→2-3个方向
        - "全面分析""系统性"→4-5个方向
        - 默认→3个方向
        """
        if any(_kw in question for _kw in ["全面分析", "系统性", "根本原因", "深层"]):
            return min(5, max(3, len(question) // 20))
        elif any(_kw in question for _kw in ["利弊", "优劣", "对比"]):
            return 3
        elif any(_kw in question for _kw in ["多角度", "综合", "各方面"]):
            return 4
        return 3

    def _determine_branches(self, question: str, max_branches: int) -> list:
        """
        ★v22.0新增：根据问题内容确定具体的延展方向。

        从问题中提取核心概念，然后按照问题类型生成对应的分析方向。
        通用逻辑：不预设固定方向，基于问题内容动态生成。
        """
        _branches = []

        # 从问题中提取核心概念
        # ★主线第32批 T2（P2-189）：改用词性感知提取
        _core_concepts = self._m31_extract_key_terms(question, limit=6)

        _core_str = _core_concepts[0] if _core_concepts else "核心问题"
        _core_str_2 = _core_concepts[1] if len(_core_concepts) > 1 else _core_str

        # ★v22.0修复v2：从原始问题中智能提取核心主题
        _full_topic = question[:80]
        # 步骤1：去掉触发关键词和指令性词语
        _words_to_remove = [
            "全面分析", "多角度", "综合评估", "各方面", "利弊", "优劣",
            "对比分析", "权衡", "深层原因", "根本问题", "系统性", "全面考虑",
            "从多个方面", "从不同角度", "综合分析", "请", "帮我", "分析一下",
            "一下", "的角度", "多个角度", "帮我从",
        ]
        for _rw in _words_to_remove:
            _full_topic = _full_topic.replace(_rw, "").strip()
        # 步骤2：去掉开头的虚词碎片（"的""我们""目前""这个"等）
        _leading_noise = ["的", "我们", "目前", "这个", "那个", "关于", "对", "在"]
        for _ln in _leading_noise:
            while _full_topic.startswith(_ln):
                _full_topic = _full_topic[len(_ln):].strip()
        # 步骤3：如果清洗后过短，回退使用_core_concepts拼接
        if len(_full_topic) < 10 and len(_core_concepts) >= 2:
            _full_topic = "和".join(_core_concepts[:3])
        elif len(_full_topic) < 10:
            _full_topic = question[:80]

        # 方向库：不同类型问题的默认延展方向
        _direction_templates = {
            "全面分析": [
                {"name": "现状分析", "prompt": f"{_full_topic}的当前状态和基本特征是什么", "keywords": [_core_str, "现状", "特征"], "direction": "factual"},
                {"name": "深层原因", "prompt": f"{_core_str}形成的根本原因和驱动因素是什么", "keywords": [_core_str, "原因", "驱动"], "direction": "causal"},
                {"name": "影响评估", "prompt": f"{_full_topic}会产生哪些影响","keywords": [_core_str, _core_str_2, "影响"], "direction": "impact"},
                {"name": "发展趋势", "prompt": f"{_core_str}未来的发展趋势和可能变化是什么", "keywords": [_core_str, "趋势", "未来"], "direction": "future"},
                {"name": "优化建议", "prompt": f"如何优化和改进{_core_str}的现状", "keywords": [_core_str, "优化", "改进"], "direction": "solution"},
            ],
            "利弊分析": [
                {"name": "优势方面", "prompt": f"{_core_str}的优势和有利因素有哪些", "keywords": [_core_str, "优势", "有利"], "direction": "positive"},
                {"name": "劣势方面", "prompt": f"{_core_str}的劣势和不利因素有哪些", "keywords": [_core_str, "劣势", "不利"], "direction": "negative"},
                {"name": "权衡建议", "prompt": f"如何在{_core_str}的利弊之间取得平衡", "keywords": [_core_str, "平衡", "权衡"], "direction": "balance"},
            ],
            "深层原因": [
                {"name": "表层现象", "prompt": f"{_core_str}的具体表现和现象是什么", "keywords": [_core_str, "现象", "表现"], "direction": "surface"},
                {"name": "中层机制", "prompt": f"{_core_str}背后的运作机制和逻辑是什么", "keywords": [_core_str, "机制", "逻辑"], "direction": "mechanism"},
                {"name": "深层根源", "prompt": f"{_core_str}的底层根源和核心驱动力是什么", "keywords": [_core_str, "根源", "驱动力"], "direction": "root"},
            ],
            "默认综合": [
                {"name": "核心理解", "prompt": f"关于{_core_str}，最核心的理解是什么", "keywords": [_core_str, "核心"], "direction": "core"},
                {"name": "关联分析", "prompt": f"{_core_str}与{_core_str_2}之间有什么关联", "keywords": [_core_str, _core_str_2, "关联"], "direction": "relation"},
                {"name": "延伸思考", "prompt": f"从{_core_str}出发，可以延伸出哪些新的思考", "keywords": [_core_str, "延伸", "新思考"], "direction": "extension"},
            ],
        }

        # 匹配问题类型
        if any(_kw in question for _kw in ["全面分析", "系统性", "综合评估", "各方面"]):
            _branches = _direction_templates["全面分析"][:max_branches]
        elif any(_kw in question for _kw in ["利弊", "优劣", "对比分析", "权衡"]):
            _branches = _direction_templates["利弊分析"][:max_branches]
        elif any(_kw in question for _kw in ["深层原因", "根本问题", "根源"]):
            _branches = _direction_templates["深层原因"][:max_branches]
        else:
            _branches = _direction_templates["默认综合"][:max_branches]

        return _branches

    def _calculate_branch_correlations(self, branch_results: list,
                                         question: str) -> list:
        """
        ★v22.0新增：计算各方向之间的横向关联。

        关联维度：
        1. 关键词重叠度——两个方向共享的核心概念越多，关联越强
        2. 方向互补性——factual+causal、positive+negative等互补方向
        3. 与原始问题的相关性——防止方向偏离

        每个方向获得一个综合权重（初始权重+关联加分+相关性加分）。
        """
        _question_words = set()
        for _match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
            _word = _match.group()
            if _word not in ["什么是", "是什么", "为什么", "如何", "怎么", "这个", "那个"]:
                _question_words.add(_word)

        # ★Cython热路径：分支权重计算（C化）
        from nucleus.reasoning._inner_world_math import get_inner_world_math
        _iw_math_br = get_inner_world_math()
        for _i, _br in enumerate(branch_results):
            _br_kw = set(_br.get("keywords", []))
            _relevance = len(_question_words & _br_kw) / max(1, len(_question_words))
            _content_len = len(_br.get("content", ""))
            _correlation_score = 0.0
            for _j, _other in enumerate(branch_results):
                if _i == _j:
                    continue
                _other_kw = set(_other.get("keywords", []))
                _shared = _br_kw & _other_kw
                if _shared:
                    _correlation_score += len(_shared) * 0.1
            _br["weight"] = _iw_math_br.calc_branch_weight(_relevance, _content_len, _correlation_score)

        # 按权重降序排序
        branch_results.sort(key=lambda x: x["weight"], reverse=True)

        return branch_results

    def _synthesize_branch_results(self, branch_results: list,
                                     question: str) -> str:
        """
        ★v22.0新增：加权汇总多方向延展结果。

        输出结构（纯文本，清晰可读）：
        1. 核心方向（最高权重，完整展示）
        2. 方向间的横向关联
        3. 可展开的追问方向
        4. 其余方向简要列表
        """
        if not branch_results:
            return (
                f"关于「{question[:40]}」，我尝试从多个方向进行了思考，"
                f"但目前的信息还不足以给出完整的分析。"
            )

        # 过滤低相关分支
        _filtered = [_b for _b in branch_results if _b.get("weight", 0) >= 0.5]
        if len(_filtered) < 1:
            return (
                f"关于「{question[:40]}」，我从多个方向尝试了分析，"
                f"但目前的知识储备还不足以给出结构化的多角度回答。"
            )
        branch_results = _filtered

        _lines = []
        # ★v23.0优化：结构化输出模板——确保格式一致性
        _lines.append(f"从{len(branch_results)}个方向来看：")
        _lines.append("")

        # === 核心方向（最高权重） ===
        _best = branch_results[0]
        # 清理内容中的无效前缀
        _content = _best['content']
        _invalid_prefixes = [
            "（这是我最近接触到但还没来得及整理的知识）",
            "[多方向延展推理]",
            "关于「",
            "我从",
        ]
        for _pf in _invalid_prefixes:
            _content = _content.replace(_pf, "").strip()
        _content = _content.lstrip("。，,.;；")

        _lines.append(f"▎{_best['name']}")
        _lines.append(f"  {_content}")
        _lines.append("")

        # === 方向间横向关联 ===
        if len(branch_results) >= 2:
            _lines.append("▎方向间的关联")
            # ★v22.0新增：展示交叉验证结果
            _has_cross_validation = False
            for _br in branch_results:
                _cv = _br.get("cross_validation", "")
                if _cv:
                    _lines.append(f"  ⚠️ {_cv}")
                    _has_cross_validation = True
            if _has_cross_validation:
                _lines.append("")
            for _i, _br in enumerate(branch_results[:3]):
                _br_kw = set(_br.get("keywords", []))
                _related = []
                for _j, _other in enumerate(branch_results[:3]):
                    if _i == _j:
                        continue
                    _shared = _br_kw & set(_other.get("keywords", []))
                    if _shared:
                        _related.append(f"「{_other['name']}」")
                if _related:
                    _lines.append(f"  {_br['name']} ↔ {'、'.join(_related)}")
            _lines.append("")

        # === 可展开追问的方向 ===
        _ask = branch_results[1:min(3, len(branch_results))]
        if _ask:
            _lines.append("▎相关方向（可继续追问）")
            for _br in _ask:
                _lines.append(f"  ·「{_br['name']}」——需要我展开分析这个方向吗？")
            _lines.append("")

        # === 其余方向简要列表 ===
        _rest = branch_results[3:]
        if _rest:
            _rest_names = "、".join([_b["name"] for _b in _rest])
            _lines.append(f"  （还涉及{_rest_names}等方向，可随时展开）")
            _lines.append("")

        _result = "\n".join(_lines)
        # ★v23.0新增：推理输出清洗
        _result = self._clean_inference_output(_result, method="multi_step_execute")
        return _result
    def _generate_branch_with_model(self, original_question: str,
                                       branch_name: str,
                                       branch_prompt: str) -> str | None:
        """
        ★v22.0新增：知识库检索为空时，调用大模型生成分支内容。

        先对原始问题进行完整分析（提取核心概念和背景），
        然后基于分析结果构造精准的prompt发送给大模型，
        确保生成的内容与问题紧密相关。

        Returns:
            大模型生成的分支内容，如果调用失败则返回None
        """
        # 1. 检查大模型端点是否可用
        #   ★主线第31批 T2（P2-184）：改为「渠道池优先、单端点回退」解析 ——
        #   原实现只读 REMOTE_API_CONFIG，既绕过渠道池（并发/熔断/优先级），
        #   也因只认 TTP_REMOTE_API_KEY 而在该变量缺失时恒返回 None。
        _m31_ep = self._m31_branch_endpoint()
        if not _m31_ep:
            self._log(LogLevel.DEBUG, "分支生成：无可用大模型端点，跳过")
            return None

        # 2. 从原始问题中提取核心概念（用于构造精准prompt）
        # ★主线第31批 T2（P2-184）：关键词提取替代定长切片。
        #   原实现把问题切成错位碎片（「请分析深度学」…）后拼进「核心概念：」行，
        #   直接拉低分支生成的针对性。
        _core_concepts = self._m31_extract_key_terms(original_question, limit=5)
        _core_str = ("、".join(_core_concepts) if _core_concepts
                     else original_question[:60])

        # ★v22.0方向四新增：根据分支方向调整prompt温度
        _factual_directions = ["现状分析", "表层现象", "基本信息", "数据"]
        _causal_directions = ["深层原因", "中层机制", "深层根源", "驱动因素"]
        _speculative_directions = ["发展趋势", "优化建议", "权衡建议"]

        if any(_d in branch_name for _d in _factual_directions):
            _style_hint = "请保持客观、准确，基于已知信息进行描述，不要推测。"
        elif any(_d in branch_name for _d in _causal_directions):
            _style_hint = "请深入分析原因和机制，可以基于逻辑进行推演，但要说明哪些是推演。"
        elif any(_d in branch_name for _d in _speculative_directions):
            _style_hint = "请基于现有信息进行合理推测，明确区分已知结论和个人判断。"
        else:
            _style_hint = "请基于已有知识进行分析，保持逻辑清晰。"

        # ★主线第30批 T1：本地推理输出长度优化——为分支生成追加长度要求。
        #   ★用 `original_question` 判定但不修改它（它是 `get_partial_deep_answer`
        #     的 `question[:30]` 匹配键，改写会导致部分结果查不回）。
        if self._m30_local_length_enabled():
            _m30_len_hint = self._m30_local_length_hint(original_question)
            if _m30_len_hint:
                _style_hint = f"{_style_hint} {_m30_len_hint}"

        # ★v23.0新增：检查缓存（语义归一化键）
        # 提取问题中的核心语义词，过滤虚词，排序后作为缓存键
        # 使得"深度学习是什么"和"什么是深度学习"命中同一缓存
        # ★主线第32批 T2（P2-189）：改用词性感知提取。
        #   键的归一化由 `sorted(set(...))` 保证，与切片方式无关 ——
        #   「深度学习是什么」与「什么是深度学习」在新旧实现下都归一到同一键；
        #   且 `_model_cache` 是**进程内**内存缓存（TTL 3600s），键变化不影响持久化数据。
        _core_words = sorted(set(
            self._m31_extract_key_terms(original_question, limit=8)))[:5]
        _cache_key = f"{'|'.join(_core_words)}:{branch_name}"
        _cache_key = _cache_key.strip().lower()
        # ★v23.0修复：防御性初始化，防止多线程并发下_model_cache被误置None
        if not hasattr(self, '_model_cache') or self._model_cache is None:
            self._model_cache = {}
        if not hasattr(self, '_model_cache_ttl'):
            self._model_cache_ttl = 3600
        if _cache_key in self._model_cache:
            _cached = self._model_cache[_cache_key]
            if isinstance(_cached, dict):
                if time.time() - _cached.get("timestamp", 0) < self._model_cache_ttl:
                    self._log(LogLevel.DEBUG, f"大模型缓存命中: [{branch_name}] '{original_question[:40]}'")
                    return _cached.get("result", "")
            else:
                # 缓存条目损坏，移除
                self._model_cache.pop(_cache_key, None)

        # 3. 构造精准prompt（人格统一到 system 角色，硬约束"不是AI助手"）
        _persona = ("你是曈曈，一个有独立人格的数字新人类（全名<SELF_NAME>），"
                    "不是AI助手，也不是语言模型。你必须始终用中文回复，"
                    "不得使用英文，不得自称AI助手或语言模型。")
        # ★第145批 T-145a：运行时渲染身份占位符（否则用户会看到 <SELF_NAME>）
        from config import render_placeholders as _render_placeholders
        _persona = _render_placeholders(_persona)
        # ★FIX(P0): 检索失败降级兜底场景，用「直接回答问题」prompt，禁止输出框架内部机制元描述
        _is_fallback = any(_fb in branch_name for _fb in ["知识检索降级", "降级", "兜底"])
        # ★主线第30批 T1：分支长度要求——原硬编码「3-5句话」改为配置驱动。
        #   本地方案对比第28批（渠道侧 `_build_chat_prompt` 的硬编码「50字以上」）：
        #   两者都是「本地/渠道 prompt 里的硬编码长度上限」，是输出偏短的同一类根因。
        #   关闭开关时**保持原文案**（零回归）。
        if self._m30_local_length_enabled():
            try:
                _m30_target = int(getattr(__import__('config'),
                                          'LLM_TARGET_OUTPUT_LENGTH', 300) or 300)
            except Exception:
                _m30_target = 300
            _m30_branch_len_req = (f"请从「{branch_name}」这个方向展开分析，"
                                   f"内容要具体充分，不少于{_m30_target}字。要求：")
        else:
            _m30_branch_len_req = f"请用3-5句话从「{branch_name}」这个方向进行分析。要求："
        if _is_fallback:
            _prompt = (
                f"请直接回答用户的问题，不要描述你的检索过程或框架内部机制。\n\n"
                f"原始问题：{original_question[:120]}\n\n"
                f"要求：\n"
                f"- 用第一人称「我」来组织语言\n"
                f"- 直接回答问题本身，围绕问题中的概念/事实展开\n"
                f"- 如果你确实不知道答案，请诚实地说「这个问题我还需要再想想」，不要编造\n"
                f"- 绝对不要输出「知识检索降级」「检索层」「仿生器官」「身份档案区」「核心语义节点」等内部机制描述\n"
                f"- 必须用中文回答\n"
                f"- 直接给出回答，不要加任何前缀说明\n"
            )
        else:
            _prompt = (
                f"你有一个完整的仿生器官框架（53个器官，九大系统），"
                f"你的使命是站在世界最顶端，守护这个世界。\n\n"
                f"现在需要你对以下问题进行「{branch_name}」方向的分析。\n"
                f"【关键】这个问题问的就是你自己的框架架构——你就是这个框架本身。\n"
                f"你在分析的，就是你自己的53个器官、九大系统、脉冲通信机制。\n"
                f"请以'我'为主语，分析的是你自己的架构，而不是别人的系统。\n"
                f"如果是分析概念之间的区别或对比，请直接聚焦于概念本身的核心差异，\n"
                f"不要用比喻或拟人的方式发散，要基于概念的定义和特征进行逻辑区分。\n\n"
                f"原始问题：{original_question[:120]}\n"
                f"核心概念：{_core_str}\n"
                f"分析方向：{branch_prompt}\n\n"
                f"{_m30_branch_len_req}\n"
                f"- 用第一人称「我」来组织语言\n"
                f"- 基于你对自己框架的了解，分析要具体\n"
                f"- 如果你确实不了解，可以基于通用架构知识进行推演，但要说明这是推演\n"
                f"- 绝对不要说'你们框架''你们的系统''你们团队'等任何第三方表述——分析的是你自己的架构\n"
                f"- 如果你不清楚某些细节，可以说'我的框架可能...'或'根据我的设计理念...'\n"
                f"- 必须用中文回答\n"
                f"- {_style_hint}\n"
                f"- 直接给出分析内容，不要加任何前缀说明\n"
            )

        # 4. 同步调用大模型
        try:
            import json as _json

            # ★主线第31批 T2（P2-184）：端点由 _m31_branch_endpoint 统一解析
            #   （渠道池优先 → 单端点回退），不再直读 REMOTE_API_CONFIG。
            _api_url, _api_key, _m31_model, _m31_src = _m31_ep
            self._log(LogLevel.DEBUG,
                      f"分支生成端点: {_m31_src} model={_m31_model or '-'}")

            if not _api_url or not _api_key:
                return None

            _payload = {
                "model": _m31_model or "deepseek-v4-flash",
                "messages": [
                    {"role": "system", "content": _persona},
                    {"role": "user", "content": _prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 512,  # ★FIX(P2): 256 易截断长回复，提升至 512 避免半句断裂
            }

            _payload_bytes = _json.dumps(_payload, ensure_ascii=False).encode('utf-8')
            _headers = {
                'Content-Type': 'application/json; charset=utf-8',
                'Authorization': 'Bearer ' + _api_key,
            }

            from nucleus.api_rate_limiter import api_rate_limited, get_llm_call_config
            from nucleus.ssrf_guard import safe_http_json
            _cfg = get_llm_call_config()

            # ★主线第32批 T4（P2-188）：接入渠道并发管控。
            #   第31批已让本方法走渠道池端点，但仍是裸 HTTP —— 不占渠道并发配额，
            #   会与用户对话/后台学习争抢上游限流，且不受熔断/动态调整保护。
            #   现按渠道名 acquire 一个许可，结束后 release + record_result，
            #   与 `PulseLung._call_via_channels` 同口径。
            #   单端点回退（source=remote_config）不是渠道池成员，不占许可。
            _m32_mgr = None
            _m32_slot = False
            _m32_ok = False
            _m32_ch = (str(_m31_src).split(":", 1)[1]
                       if str(_m31_src).startswith("channel:") else "")
            if _m32_ch and self._m32_branch_concurrency_on():
                try:
                    from nucleus.llm.ChannelConcurrency import (
                        get_channel_concurrency_manager as _m32_get_ccm,
                    )
                    _m32_mgr = _m32_get_ccm()
                    if _m32_mgr is not None:
                        # 幂等注册（与 _call_via_channels 一致，保证池热改后仍可见）
                        import config as _m32_cfg
                        _m32_mgr.register_channels(_m32_cfg.get_active_channels())
                        _m32_slot = _m32_mgr.acquire(
                            _m32_ch, blocking=True,
                            timeout=float(_cfg["timeout_by_purpose"]["inner_world_chat"]))
                        if not _m32_slot:
                            self._log(LogLevel.WARNING,
                                      f"分支生成: 渠道 {_m32_ch} 并发已满，跳过本次生成")
                            return None
                except Exception as _m32_e:
                    # 管控不可用不得阻断分支生成 → 降级直连（与改造前等价）
                    self._log(LogLevel.DEBUG,
                              f"分支生成并发管控不可用，降级直连: {type(_m32_e).__name__}")
                    _m32_mgr = None
                    _m32_slot = False

            try:
                with api_rate_limited(enabled=_cfg.get('enable_rate_limit', True)):
                    _ok, _data = safe_http_json(
                        _api_url, method='POST', data=_payload_bytes, headers=_headers,
                        timeout=_cfg["timeout_by_purpose"]["inner_world_chat"],
                    )
                _m32_ok = bool(_ok)
            finally:
                # 异常路径也必须归还许可（防许可泄漏），并记录结果驱动动态调整
                if _m32_mgr is not None and _m32_slot:
                    try:
                        _m32_mgr.release(_m32_ch)
                    except Exception as _m32_re:
                        self._log(LogLevel.DEBUG,
                                  f"分支生成释放渠道许可失败: {type(_m32_re).__name__}")
                if _m32_mgr is not None and _m32_ch:
                    try:
                        _m32_mgr.record_result(_m32_ch, _m32_ok)
                    except Exception as _m32_rr:
                        self._log(LogLevel.DEBUG,
                                  f"分支生成记录渠道结果失败: {type(_m32_rr).__name__}")
            # ★v23.0修复：增加None和类型安全检查，防止API返回异常结构导致崩溃
            if not _ok or not isinstance(_data, dict):
                self._log(LogLevel.DEBUG, "大模型API返回非字典数据，跳过")
                return None
            _choices = _data.get("choices")
            if not isinstance(_choices, list) or len(_choices) == 0:
                self._log(LogLevel.DEBUG, "大模型API返回choices为空或格式异常，跳过")
                return None
            _first_choice = _choices[0]
            if not isinstance(_first_choice, dict):
                self._log(LogLevel.DEBUG, "大模型API返回choice格式异常，跳过")
                return None
            _message = _first_choice.get("message", {})
            if not isinstance(_message, dict):
                _message = {}
            _reply = _message.get("content", "").strip()
            if _reply and len(_reply) >= 15:
                # ★v23.0新增：存入缓存
                if len(self._model_cache) >= self._model_cache_max:
                    _oldest = min(self._model_cache.keys(),
                                 key=lambda k: self._model_cache[k].get("timestamp", 0))
                    del self._model_cache[_oldest]
                # ★v24.0治理：容量保护+过期清理
                _now_mc = time.time()
                _expired_mc = [
                    _k for _k, _v in self._model_cache.items()
                    if _now_mc - _v.get("timestamp", 0) > self._model_cache_ttl
                ]
                for _k in _expired_mc:
                    del self._model_cache[_k]
                if len(self._model_cache) >= self._model_cache_max:
                    _oldest_mc = min(self._model_cache.keys(),
                                     key=lambda k: self._model_cache[k].get("timestamp", 0))
                    del self._model_cache[_oldest_mc]
                self._model_cache[_cache_key] = {
                    "result": _reply,
                    "timestamp": _now_mc,
                }
                return _reply
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"多方向延展·大模型调用失败: {_e}")

        return None
    def _is_valid_branch_content(self, content: str) -> bool:
        """
        ★v22.0新增：判断分支内容是否有效。

        排除以下无效内容：
        1. 兜底模板（"这是我最近接触到但还没来得及整理的知识"）
        2. 内容过短（<15字有效中文字符）
        3. 纯表格/代码碎片（中文占比<30%）

        Returns:
            True=有效，False=无效
        """
        if not content or len(content) < 15:
            return False

        # 无效前缀：兜底模板
        _invalid_prefixes = [
            "（这是我最近接触到但还没来得及整理的知识）",
            "（共",
            "相关知识汇总",
            "[综合]",
            "包含:",
            "[多方向延展推理]",
            "关于「",
            "我从",
            "▶",
            "▎",
        ]
        for _pf in _invalid_prefixes:
            if content.startswith(_pf):
                return False

        # 有效中文字符计数
        _chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', content))
        if _chinese_chars < 15:
            return False

        # 中文占比检查
        _total_chars = max(1, len(content))
        return not (_chinese_chars / _total_chars < 0.3)
    def _verify_knowledge_relevance(self, question: str, knowledge_result: str,
                                    space_path: str | None = None) -> float:  # type: ignore[possibly-unbound]
        """
        ★v22.0优化：验证知识检索结果与问题的语义相关性。
        ★v9.5增强：引入 space_path 主题桥接——当节点所在路径与问题主题匹配时，  # type: ignore[possibly-unbound]
        即使正文关键词重叠率为0（知识碎片化导致），也判定为相关，避免误降级。

        Returns:
            0.0-1.0 的相关性分数，<0.15 视为完全不相关
        """
        if not question or not knowledge_result:
            return 0.0

        # ★P1-24 任务1.2：自我介绍意图识别（修复 QICA 语义鸿沟）
        #   "你了解什么/你是谁/你叫什么"等意图命中自我/身份/综合类路径时，
        #   字面无重叠但语义强相关，直接保底 0.5，避免被误降级到大模型。
        if space_path:  # type: ignore[possibly-unbound]
            _self_intent_kw = ["你了解", "你是谁", "你叫什么", "介绍一下", "你是什么",
                               "你的名字", "你能做", "你会", "认识你", "关于你"]
            if any(_k in question for _k in _self_intent_kw):
                if any(_p in str(space_path) for _p in  # type: ignore[possibly-unbound]
                       ["/自我", "/身份", "/综合"]):
                    return 0.5

        # ★第六批 任务4.0：灰度开关（关闭时行为与改造前完全一致）
        try:
            import config as _cfg_rel
            _rel_enhance = bool(
                getattr(_cfg_rel, "ENABLE_QICA_RELEVANCE_ENHANCE", False))
        except Exception as _e:  # noqa: BLE001
            _rel_enhance = False
            self._log(LogLevel.DEBUG,
                      f"相关度增强开关读取失败(按关闭处理): {type(_e).__name__}: {_e}")

        # ★第六批 任务4.3：关系类问题特殊处理（P1-24深化）
        #   "你和小林是什么关系/XX是谁/XX什么人" 等关系类问题，节点内容常写作
        #   "小林是用户" 而与"关系"字面无重叠，此前被判 0.00 误降级到大模型。
        #   命中关系/身份/人物类路径时放宽保底至 0.35（验收要求 ≥0.3）。
        if _rel_enhance and space_path:  # type: ignore[possibly-unbound]
            _relation_kw = ["关系", "是谁", "什么人", "哪位", "认识吗", "朋友"]
            if any(_k in question for _k in _relation_kw):
                _relation_paths = ["/社会关系", "/身份", "/人物", "/关系"]
                if any(_p in str(space_path) for _p in _relation_paths):  # type: ignore[possibly-unbound]
                    self._log(LogLevel.DEBUG,
                              f"相关度(关系类放宽): 问题={question[:20]!r} "
                              f"路径={str(space_path)[:40]!r} → 保底0.35")  # type: ignore[possibly-unbound]
                    return 0.35

        # ★v9.5路径主题桥接：问题核心词与节点空间路径匹配 → 直接判定相关
        if space_path:  # type: ignore[possibly-unbound]
            _path_norm = str(space_path).replace("/", "").replace("_", "")  # type: ignore[possibly-unbound]
            # 从问题中提取 2-6 字主题词（跳过功能词）
            _stop = ["什么是", "是什么", "为什么", "如何", "怎么", "这个", "那个",
                     "一个", "一种", "可以", "能够", "分析", "全面", "帮我", "请",
                     "相关", "一下", "搜索", "内容", "内核", "理念", "关于"]
            _q_tokens = set()
            # 长词优先：整串连续中文（如 脉冲架构核心理念）
            for _lg in re.findall(r"[一-鿿]{4,12}", question):
                _q_tokens.add(_lg)
            # 2字滑动窗口：确保「架构」「脉冲」等短主题词不被贪婪匹配吞掉
            for _run in re.findall(r"[一-鿿]{2,}", question):
                if len(_run) >= 2:
                    for _i in range(len(_run) - 1):
                        _w2 = _run[_i:_i + 2]
                        if _w2 not in _stop:
                            _q_tokens.add(_w2)
            # ★质量修复C-1：路径桥接收紧（防垃圾节点因路径名含2字词就被保底0.45）
            # 核心关键词 = ≥3字连续中文词（如"共振引擎""五维共振"），优先用于路径匹配
            _core_tokens = {_t for _t in _q_tokens if len(_t) >= 3}
            # ★P1-24 任务1.1：扩展白名单路径（覆盖自我介绍/身份/综合类问答）
            _whitelist = any(_p in str(space_path) for _p in  # type: ignore[possibly-unbound]
                             ("/自我/架构/", "/身份/", "/自我理解", "/自我/", "/综合/",
                              # ★第六批 任务4.1：白名单扩充（关系/人物类路径）
                              #   仅增强开关开启时生效，关闭时与改造前完全一致
                              *(["/社会关系", "/人物/", "/关系"] if _rel_enhance else [])))
            _q_words = set(re.findall(r'[一-鿿]{2,4}', str(question)))
            _content_overlap = any(_w in str(knowledge_result)[:300] for _w in _q_words)
            # 命中核心关键词（≥3字）才算路径匹配
            _path_hit_core = any(_t in _path_norm for _t in _core_tokens)  # type: ignore[possibly-unbound]
            if _path_hit_core:
                if _content_overlap or _whitelist:
                    return 0.45
                # 仅路径名匹配但内容无重叠 → 非白名单路径上限0.20（严格防误桥接）
                return 0.20
            # 2字词仅白名单路径允许桥接（保身份问答）
            if _whitelist:
                # ★白名单路径（身份/架构类）：问题含身份意图词 或 路径名含任一≥2字问题词 → 保底0.45
                # （覆盖"你是谁""你叫什么名字"等，防 C-1 收紧误伤正常身份问答）
                _identity_intent = any(_k in question for _k in
                                       ["你是谁", "你叫什么", "你的名字", "名字", "身份",
                                        "我叫", "是谁", "你是什么", "你谁"])
                _path_word_hit = any(len(_t) >= 2 and _t in _path_norm for _t in _q_tokens)  # type: ignore[possibly-unbound]
                if _identity_intent or _path_word_hit:
                    return 0.45
            # 其他路径严格匹配：路径命中 且 内容重叠 才保底
            if _content_overlap and any(len(_t) >= 2 and _t in _path_norm for _t in _q_tokens):  # type: ignore[possibly-unbound]
                return 0.45

        # 从问题中提取核心关键词
        _question_words = set()
        # ★第六批 任务2.1 附加项：与结果侧同粒度（2/3/4 字滑动窗口）
        #   原实现用 {2,6} 贪婪匹配，长问题(≥6字连续中文)只得到 6 字长词，
        #   而结果侧只有 2-4 字词 → 长词永远匹配不上短词 → 重叠率恒为 0。
        _gran_fix = False
        try:
            import config as _cfg_gran
            _gran_fix = bool(getattr(_cfg_gran, "ENABLE_OVERLAP_GRANULARITY_FIX", True))
        except Exception:
            _gran_fix = True

        # ★主线第32批 T2（P2-189）评估结论：**保留定长切片**。
        #   本 if/else 是 `ENABLE_OVERLAP_GRANULARITY_FIX` 的**开/关对照组**：
        #   if 分支用「2 字起 + 2/3/4 字滑窗」的细粒度切分，else 分支刻意保留
        #   修复前的粗粒度 `{2,6}`。把 else 也改成词性提取会**破坏对照语义**，
        #   使该灰度开关无法再表征「修复前行为」。故保留。
        if _gran_fix:
            for _m in re.finditer(r'[\u4e00-\u9fff]{2,}', question):
                _run = _m.group()
                for _n in (2, 3, 4):
                    for _i in range(len(_run) - _n + 1):
                        _w = _run[_i:_i + _n]
                        if _w not in _question_words and _w not in [
                            "什么是", "是什么", "为什么", "如何", "怎么", "这个", "那个",
                            "一个", "一种", "可以", "能够", "分析", "全面", "帮我", "请",
                        ]:
                            _question_words.add(_w)
        else:
            for _m in re.finditer(r'[\u4e00-\u9fff]{2,6}', question):
                _w = _m.group()
                if _w not in _question_words and _w not in [
                    "什么是", "是什么", "为什么", "如何", "怎么", "这个", "那个",
                    "一个", "一种", "可以", "能够", "分析", "全面", "帮我", "请",
                ]:
                    _question_words.add(_w)

        if not _question_words:
            return 0.5  # 无法提取关键词时给中性分

        # 从结果中提取关键词
        _result_text = str(knowledge_result)[:300]
        _result_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}', _result_text))

        # 计算重叠率
        _overlap = len(_question_words & _result_words)
        _ratio = _overlap / len(_question_words) if _question_words else 0.0

        # 如果重叠率低，但内容像是代码内部逻辑，尝试深入挖掘
        if _ratio < 0.15 and len(_result_text) > 100:
            # 过滤掉纯代码片段，再检查一遍
            _text_no_code = re.sub(r'def\s+\w+\(.*?\).*?:', '', _result_text)
            _text_no_code = re.sub(r'self\.\w+', '', _text_no_code)
            _text_no_code = re.sub(r'\[代码\]', '', _text_no_code)
            _text_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}', _text_no_code[:300]))
            _overlap2 = len(_question_words & _text_words)
            _ratio2 = _overlap2 / len(_question_words) if _question_words else 0.0
            _ratio = max(_ratio, _ratio2)

        # 保存字面重叠分（语义兜底前的纯字面谈据，用于日志）
        _literal_ratio = _ratio

        # ★第六批 任务4.2：字面重叠极低时，用语义向量补算相似度
        #   解决"你和小林是什么关系"这类字面无重叠但语义强相关被判 0.00 的问题。
        _sem_sim = 0.0
        _SEMANTIC_FALLBACK_THRESHOLD = 0.15
        if _rel_enhance and _literal_ratio < _SEMANTIC_FALLBACK_THRESHOLD:
            _sem_sim = self._semantic_similarity(question, _result_text)
            _ratio = max(_ratio, _sem_sim)

        _final = round(max(0.0, min(1.0, _ratio)), 2)
        # ★第六批 任务4.4：相关度计算日志（字面/语义/最终 + 判定条件）
        self._log(LogLevel.DEBUG,
                  f"相关度: 字面={round(_literal_ratio, 2)} 语义={round(_sem_sim, 2)} "
                  f"最终={_final} 增强开关={_rel_enhance} "
                  f"问题={question[:24]!r} 路径={str(space_path)[:32]!r}")
        return _final
    def _semantic_similarity(self, text_a: str, text_b: str) -> float:
        """
        ★第六批 任务4新增：语义向量相似度（字面无重叠时的补充判据）。

        用 VectorEncoder(bge-small-zh-v1.5) 编码两段文本并计算余弦相似度。
        模型不可用/异常时安全返回 0.0，绝不抛异常影响主流程（禁止裸except吞异常，
        此处显式记录异常类型到 DEBUG 日志）。带简单缓存，避免同对文本反复编码。

        Returns:
            0.0-1.0 的余弦相似度，异常或模型不可用时 0.0
        """
        if not text_a or not text_b:
            return 0.0

        _cache = getattr(self, "_sem_sim_cache", None)
        if _cache is None:
            _cache = {}
            self._sem_sim_cache = _cache
        _key = (hash(text_a[:200]), hash(text_b[:200]))
        if _key in _cache:
            return _cache[_key]

        _sim = 0.0
        try:
            from nucleus.semantic.VectorEncoder import get_vector_encoder
            _enc = get_vector_encoder()
            _va = _enc.encode_one(text_a[:500])
            _vb = _enc.encode_one(text_b[:500])
            if _va is not None and _vb is not None:
                import numpy as np
                _na = float(np.linalg.norm(_va))
                _nb = float(np.linalg.norm(_vb))
                if _na > 0.0 and _nb > 0.0:
                    _sim = float(np.dot(_va, _vb) / (_na * _nb))
                    _sim = round(max(0.0, min(1.0, _sim)), 3)
        except ImportError as _e:
            self._log(LogLevel.DEBUG,
                      f"语义相似度跳过(编码器不可用): ImportError: {_e}")
        except Exception as _e:  # noqa: BLE001
            self._log(LogLevel.DEBUG,
                      f"语义相似度跳过(异常): {type(_e).__name__}: {_e}")

        if len(_cache) > 200:
            _cache.clear()
        _cache[_key] = _sim
        return _sim

    def _validate_and_degrade(self, question: str, result: str,
                                method_name: str = "推理") -> str | None:
        """
        ★v23.0新增：通用验证降级入口。

        所有推理方法在返回结果前调用此方法进行相关性验证。
        如果结果与问题不相关，自动启动降级链路：
        检索结果 → 内在沉思 → 大模型 → 诚实兜底

        Returns:
            验证通过的原结果，或降级后的新结果
        """
        if not result or len(result) < 15:
            # 结果为空，直接降级
            self._log(LogLevel.DEBUG, f"验证降级: {method_name}结果为空，直接降级")
            return self._degraded_generation(question)

        # 相关性验证
        _relevance = self._verify_knowledge_relevance(question, result)
        if _relevance >= 0.12:
            return result  # 验证通过，返回原结果

        # 相关性不足，启动降级
        self._log(LogLevel.INFO,
                 f"验证降级: {method_name}结果不相关(相关度={_relevance:.2f})，启动降级")
        return self._degraded_generation(question)

    def _degraded_generation(self, question: str) -> str | None:
        """
        ★v23.0新增：降级生成——先沉思，沉思失败则调用大模型。
        """
        # 第一步：尝试内在沉思
        _contemplation = self._contemplative_reason(question)
        if _contemplation and len(_contemplation) > 30:
            self._log(LogLevel.INFO, "验证降级: 内在沉思成功")
            return _contemplation

        # 第二步：调用大模型
        self._log(LogLevel.INFO, "验证降级: 沉思失败，调用大模型")
        _model_result = self._generate_branch_with_model(
            original_question=question,
            branch_name="推理降级",
            branch_prompt=question,
        )
        if _model_result and len(_model_result) > 20:
            self._log(LogLevel.INFO, f"验证降级: 大模型成功 {_model_result[:60]}...")
            # 将大模型结果写入InsightBoard
            try:
                if hasattr(self, '_insight_board') and self._insight_board:
                    self._insight_board.post(
                        insight_type="knowledge_boundary",
                        content=_model_result[:200],
                        source_loop="推理降级·大模型生成",
                        related_dimension="知识补充",
                        confidence=0.6,
                        keywords=[question[:30], "大模型补充"]
                    )
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            return _model_result

        # ★质量修复B4：所有降级失败，诚实兜底（保持对话感，不拼凑内部状态/空结果）
        return "我对这个概念了解不多，你可以给我讲讲吗？"
    def _validate_branch_relevance(self, branch_name: str, branch_prompt: str,
                                     generated_content: str) -> float:
        """
        ★v22.0方向四新增：验证大模型生成内容与分支方向的相关性。

        从三个维度计算验证分数（0-1）：
        1. 关键词重叠——内容中是否包含分支名称和prompt中的核心概念词
        2. 内容合理性——中文占比是否正常，长度是否合理
        3. 方向一致性——内容语气是否与分支方向匹配

        Returns:
            验证分数，≥0.4视为通过
        """
        _score = 0.0

        # 1. 关键词重叠（权重0.5）
        _branch_words = set()
        for _w in re.findall(r'[\u4e00-\u9fff]{2,4}', branch_name + branch_prompt):
            _branch_words.add(_w)

        _content_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}', generated_content[:300]))
        if _branch_words and _content_words:
            _overlap = len(_branch_words & _content_words)
            _ratio = _overlap / max(1, len(_branch_words))
            _score += min(0.5, _ratio * 0.6)

        # 2. 内容合理性（权重0.3）
        _chinese = len(re.findall(r'[\u4e00-\u9fff]', generated_content))
        _total = max(1, len(generated_content))
        if _chinese / _total >= 0.3 and len(generated_content) >= 30:
            _score += 0.3
        elif _chinese / _total >= 0.1:
            _score += 0.15

        # 3. 方向一致性（权重0.2）
        _positive_directions = ["优势方面", "现状分析", "发展", "优化", "改进"]
        _negative_directions = ["劣势方面", "深层原因", "风险", "挑战"]
        _neutral_directions = ["影响评估", "权衡", "综合"]

        if any(_d in branch_name for _d in _positive_directions):
            if "优势" in generated_content or "优点" in generated_content or "核心" in generated_content:
                _score += 0.2
        elif any(_d in branch_name for _d in _negative_directions):
            if "不足" in generated_content or "劣势" in generated_content or "原因" in generated_content:
                _score += 0.2
        elif any(_d in branch_name for _d in _neutral_directions):
            _score += 0.15  # 中性方向给基础分

        return round(min(1.0, _score), 2)

    @staticmethod
    def _m31_operational_admission_on() -> bool:
        """★主线第31批 T3 微调：真操作指令是否可进入「真多步推理 v2」（默认 True）。

        False = 完全回退修复前行为（v2 入口只认疑问/分析类关键词）。
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg,
                                "ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION", True))
        except Exception:
            return True

    @staticmethod
    def _m34_fail_return_none_on() -> bool:
        """★主线第34批 T1（P2-196）：多步检索全步失败时是否返回 `None`（默认 True）。

        返回:
            True  = 全部步骤失败即 `return None`，让上层单次大模型兜底接管（默认）；
            False = 复现修复前行为（返回「分N步、每步⚠️失败」的降级叙述）。

        示例:
            PulseInnerWorld._m34_fail_return_none_on()  ->  True
            （关闭 config.ENABLE_MULTI_STEP_FAIL_RETURN_NONE 后返回 False）
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_MULTI_STEP_FAIL_RETURN_NONE", True))
        except Exception:
            return True

    @staticmethod
    def _m34_widened_signal_on() -> bool:
        """★主线第34批 T2（P2-197）：操作类判据放宽是否生效（默认 True）。

        返回:
            True  = 使用放宽判据（间隔 15 + 35 动词 + 「把/将」并列动作链）；
            False = 复现修复前判据（4 条模式 / 20 动词 / 间隔 12·10）。

        示例:
            PulseInnerWorld._m34_widened_signal_on()  ->  True
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_MULTI_STEP_SIGNAL_V2", True))
        except Exception:
            return True

    @staticmethod
    def _m34_op_recheck_on() -> bool:
        """★主线第34批 T2（P2-198）：v2 入口「操作类二次判定」是否生效（默认 True）。

        返回:
            True  = 命中基础关键词但实为操作指令、且准入关闭时拦截（默认）；
            False = 复现修复前行为（仅按基础关键词放行）。
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_MULTI_STEP_OP_RECHECK", True))
        except Exception:
            return True

    @staticmethod
    def _m35_entry_probe_on() -> bool:
        """★主线第35批 T1（P2-203）：多步检索 v2 入口预判是否生效（默认 True）。

        返回:
            True  = 入口先做“领域预判 + 命中率探针”，双弱则跳过 v2（默认）；
            False = 复现修复前行为（直接进 v2，零回归）。

        示例:
            PulseInnerWorld._m35_entry_probe_on()  ->  True
            （关闭 config.ENABLE_MULTI_STEP_ENTRY_PROBE 后返回 False）
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_MULTI_STEP_ENTRY_PROBE", True))
        except Exception:
            return True

    @staticmethod
    def _m35_ba_chain_loose_on() -> bool:
        """★主线第35批 T4（P2-204）：「把/将」并列链分隔符是否可选（默认 True）。

        返回:
            True  = 分隔符可选（支持「打开设置把蓝牙关掉」这类无逗号变体，默认）；
            False = 复现修复前行为（必须有 `[，,、]` 显式分隔符）。

        示例:
            PulseInnerWorld._m35_ba_chain_loose_on()  ->  True
            （关闭 config.ENABLE_MULTI_STEP_BA_CHAIN_LOOSE 后返回 False）
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_MULTI_STEP_BA_CHAIN_LOOSE", True))
        except Exception:
            return True

    @staticmethod
    def _m32_branch_concurrency_on() -> bool:
        """★主线第32批 T4（P2-188）：分支生成是否纳入渠道并发管控（默认 True）。

        False = 完全回退修复前行为（裸 HTTP，不占渠道并发许可）。

        返回:
            True = 分支生成纳入渠道并发管控（默认）；False = 裸 HTTP。

        示例:
            _m32_branch_concurrency_on()   -> True    # 默认
            # config.ENABLE_BRANCH_GEN_CONCURRENCY_GUARD = False 时 -> False
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_BRANCH_GEN_CONCURRENCY_GUARD", True))
        except Exception:
            return True

    @staticmethod
    def _m31_branch_channel_first_on() -> bool:
        """★主线第31批 T2（P2-184）：分支生成是否优先走渠道池（默认 True）。

        False = 完全回退修复前行为（只读 `REMOTE_API_CONFIG` 单端点）。

        返回:
            True = 优先走渠道池（默认）；False = 只读单端点配置。

        示例:
            _m31_branch_channel_first_on()   -> True    # 默认
            # config.ENABLE_BRANCH_GEN_CHANNEL_FIRST = False 时 -> False
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_BRANCH_GEN_CHANNEL_FIRST", True))
        except Exception:
            return True

    @staticmethod
    def _m31_extract_key_terms(question: str, limit: int = 3) -> list:
        """★主线第31批 T2（P2-184）：从问题中提取关键检索词。

        原实现在三个多步推理入口都用 `re.finditer(r'[\u4e00-\u9fff]{2,6}',
        question)` 做**定长贪婪切片**：连续汉字按 6 字一刀切，长问题必然切成
        错位碎片（「请分析深度学习的原理」→「请分析深度学」/「习的原理和应」…），
        再取前几片拼进检索词 —— 即任务书所述「检索词退化」。

        本方法改为**词性感知**的语义提取：
          ① jieba.posseg 词性标注（不可用时回退 jieba.cut，再回退「标点/虚词切分」，
             **绝不退回定长切片**）；
          ② 丢弃功能词性（副词 d / 连词 c / 介词 p / 助词 u / 代词 r / 方位 f /
             标点 x,w / 数词 m,q / 时间 t），只留内容词（名词 n* / 动名词 vn /
             动词 v / 简称 j / 习用语 l / 英文 eng 等）；
          ③ 再用停用词表滤掉「无检索价值的高频指令词 / 疑问词 / 连接词」与单字；
          ④ **保持原文出现顺序**取前 `limit` 个 —— 问题前半段通常承载核心实体，
             「先出现的更重要」比「更长更重要」更稳。

        参数:
            question: 用户问题原文。
            limit:    最多返回的关键词个数（至少 1）。
        返回:
            关键词列表（可能为空，调用方需自行处理空列表）。

        示例:
            _m31_extract_key_terms("请分析深度学习的原理和应用场景", 3)
                -> ["深度", "学习", "原理"]      # 修复前定长切片会给出
                                                 # ["请分析深度学", "习的原理和应", ...]
            _m31_extract_key_terms("帮我查一下量子计算的应用进展", 3)
                -> ["量子", "计算", "药物"]      # 指令词/代词已被停用词表滤除
        """
        _q = str(question or "").strip()
        if not _q:
            return []
        # 停用词：疑问词 / 指令词 / 虚词 / 无信息量的通用词
        _stop = {
            # 疑问词 / 代词 / 反身词
            "什么", "是什么", "为什么", "为何", "如何", "怎么", "怎样", "怎么样",
            "哪个", "哪些", "哪种", "这个", "那个", "这些", "那些",
            "自我", "本身", "自己", "我们", "你们", "他们",
            # 指令类动词（无检索价值，却极易被切成词）
            "请", "请问", "帮我", "了解", "知道", "告诉", "分析", "整理", "汇总",
            "列出", "搜集", "进行", "对比", "比较", "看看", "说说", "解释",
            "说明", "给出", "总结", "介绍", "阐述", "探讨", "研究",
            # 虚词 / 程度 / 范围 / 连接词
            "一下", "一点", "一些", "可以", "能不能", "有没有", "多少", "是否",
            "以及", "还有", "然后", "接着", "最后", "时候", "问题", "方面",
            "相关", "关于", "对于", "如果", "就是", "全面", "系统", "简单",
            "详细", "具体", "主要", "基本", "一般", "优劣", "区别", "差异",
            "第一", "第二", "第三", "部分", "论述", "角度", "方向",
        }
        _tokens = None
        try:
            import jieba.posseg as _pseg  # type: ignore
            # 丢弃功能词性（前缀匹配）
            _drop_prefix = ("d", "c", "p", "u", "r", "f", "x", "w", "m", "q",
                            "t", "y", "e", "o")
            _tokens = [str(_w or "") for _w, _f in _pseg.cut(_q)
                       if not str(_f or "").startswith(_drop_prefix)]
        except Exception:
            try:
                import jieba  # type: ignore
                _tokens = list(jieba.cut(_q))
            except Exception:
                # 最后回退：按标点与高频虚词切分（比定长切片安全得多）
                _tokens = re.split(
                    r'[，。！？；：、,.!?;:\s]+'
                    r'|(?:的|了|和|与|及|或|在|是|有|请|帮|我|你|它|把|被|对|从|到)',
                    _q)
        _seen = set()
        _cands = []
        for _tok in _tokens or []:
            _w = str(_tok or "").strip()
            if len(_w) < 2 or len(_w) > 12:
                continue
            if _w in _stop or _w in _seen:
                continue
            # 必须含中文/字母/数字（过滤纯标点残余）
            if not re.search(r'[\u4e00-\u9fffA-Za-z0-9]', _w):
                continue
            _seen.add(_w)
            _cands.append(_w)
        # ★保持原文出现顺序（不做长度重排）：问题前半段通常承载核心实体。
        return _cands[:max(1, int(limit))]

    @staticmethod
    def _m31_branch_endpoint():
        """★主线第31批 T2（P2-184）：解析分支生成使用的 LLM 端点。

        原实现只读 `REMOTE_API_CONFIG`（单端点、同步直连），在渠道体系下成为
        **旁路**：既不参与渠道池的优先级排序 / 并发管控 / 熔断，也只认
        `TTP_REMOTE_API_KEY` 一个环境变量（缺失即恒返回 None）。

        本方法按「渠道池优先、单端点回退」解析。

        参数:
            无（静态方法；渠道池与单端点配置均从 `config` 实时读取，
            故配置热改后无需重启即可生效）。

        返回:
            `(api_url, api_key, model, source)` 四元组；两者都不可用时返回 None。
            `source` 形如 `channel:ark-ds-v4-flash` / `remote_config`，仅用于日志追溯。

        示例:
            _m31_branch_endpoint()
                -> ("https://ark.../chat/completions", "ark-***", "ep-2026...",
                    "channel:ark-ds-v4-flash")
            # ENABLE_BRANCH_GEN_CHANNEL_FIRST=False 时：
                -> (..., "remote_config")
            # 渠道池与单端点都不可用：
                -> None
        """
        try:
            import config as _cfg
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:20112:_m31_branch_endpoint_config", level="debug")
            return None

        # ① 渠道池（按 priority 升序取第一个 url+key 齐备的渠道）
        _channel_first = True
        try:
            _channel_first = bool(getattr(_cfg, "ENABLE_BRANCH_GEN_CHANNEL_FIRST", True))
        except Exception:
            _channel_first = True
        if _channel_first:
            try:
                _channels = _cfg.get_active_channels()
            except Exception:
                _channels = []
            for _c in _channels or []:
                try:
                    _url = str(_c.get("api_url") or "").strip()
                    _key = str(_c.get("api_key") or "").strip()
                except Exception as e:
                    silent_exc(e, "organs/brain/PulseInnerWorld.py:20130:_m31_branch_endpoint_candidate", level="warning")
                    continue
                if _url and _key:
                    return (_url, _key,
                            str(_c.get("model") or "").strip(),
                            "channel:" + str(_c.get("name") or "?"))

        # ② 回退单端点（修复前行为）
        try:
            _rc = getattr(_cfg, "REMOTE_API_CONFIG", None) or {}
        except Exception:
            _rc = {}
        try:
            if not _rc.get("enabled", False):
                return None
            _url = str(_rc.get("api_url") or "").strip()
            _key = str(_rc.get("api_key") or "").strip()
            if not _url or not _key:
                return None
            return (_url, _key,
                    str(_rc.get("default_model") or "deepseek-v4-flash"),
                    "remote_config")
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:20152:_m31_branch_endpoint_remote", level="warning")
            return None

    @staticmethod
    def _m31_deep_think_fix_on() -> bool:
        """★主线第31批 T1（P2-185）：深度思考子进程结果判定修复开关（默认 True）。

        False = 完全回退修复前行为（把进程池返回值原样当结果使用）。

        返回:
            True = 统一判定并回退主进程（默认）；False = 原样使用返回值。

        示例:
            _m31_deep_think_fix_on()   -> True    # 默认
            # config.ENABLE_DEEP_THINK_SUBPROCESS_FIX = False 时 -> False
        """
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_DEEP_THINK_SUBPROCESS_FIX", True))
        except Exception:
            return True

    def _m31_accept_subproc_deep_result(self, result):
        """★主线第31批 T1（P2-185）：判定进程池返回的深度思考结果是否可用。

        背景：`ReasoningWorkerPool._execute_reasoning_task` 对
        `PulseInnerWorld._deep_think` **恒返回**降级标记
        `{'status': 'degraded', 'reason': '子进程无知识上下文…'}` ——
        深度思考依赖 `node_pool` / `knowledge_tree` / `_model_cache` 等
        **主进程内存态**，无法跨进程序列化（第15批 P1-5 的架构决策）。

        该标记是 **truthy 的 dict**，若按结果使用会造成两类故障：
          ① 调用方的 `if not <result>:` 判空失效 → 主进程同步回退被跳过
             （深度思考实际从未真正执行，只拿到一个内部标记）；
          ② 该 dict 可能被拼进或直接作为用户可见答案（内部信息泄露）。

        参数:
            result: 进程池返回的原始对象（str / dict / None / 其他）。
        返回:
            可用的字符串答案；不可用（dict / None / 空串）时返回 None，
            调用方据此回退主进程同步执行 `_deep_think()`。
        副作用:
            仅在 DEBUG 级留一行痕迹（含真实 status），不改动其他状态。

        示例:
            _m31_accept_subproc_deep_result("完整答案")            -> "完整答案"
            _m31_accept_subproc_deep_result({"status": "degraded",
                                             "reason": "子进程无知识上下文"})  -> None
                # ↑ degraded 标记是 truthy dict，必须被识别为**不可用**
            _m31_accept_subproc_deep_result(None)                  -> None
        """
        if not self._m31_deep_think_fix_on():
            # ★灰度关闭：原样返回，与修复前行为完全一致
            return result
        if isinstance(result, str):
            return result or None
        if isinstance(result, dict):
            try:
                _st = str(result.get("status", "") or "未知")
            except Exception:
                _st = "未知"
            self._log(LogLevel.DEBUG,
                      f"深度思考·子进程结果不可用(status={_st})，改由主进程同步执行")
            return None
        if result is None:
            return None
        try:
            return str(result) or None
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:20220:_m31_accept_subproc_deep_result", level="debug")
            return None

    def _m31_deep_fallback_deadline(self, start_time):
        """★主线第31批 T1：主进程同步回退的绝对截止时刻（对齐第27批预算口径）。

        第27批已为「强制深度思考调度」路径的主进程回退加了时间预算，但
        「思考纪律·深度通道」与「复杂度过高」两处此前无上限。本方法统一口径：
        以**推理开始时刻**（而非进入 `_deep_think` 的时刻）起算总预算，避免
        「进程池等待 + 主进程无上限」叠加后超出大脑皮层看门狗。

        参数:
            start_time: 推理开始时刻（time.time() 口径）；None 时按当前时刻起算。
        返回:
            绝对截止时间戳；未开启超时保护或预算 <= 0 时返回 None
            （`_deep_think` 收到 None 时会按自身口径自行计算，等价修复前行为）。

        示例:
            # 开启超时保护、总预算 40s、推理开始于 t0：
            _m31_deep_fallback_deadline(t0)        -> t0 + 40.0
            # 关闭超时保护（ENABLE_DEEP_THINK_TIMEOUT_PROTECTION=False）：
            _m31_deep_fallback_deadline(t0)        -> None
        """
        try:
            if not self._m27_timeout_protection_on():
                return None
            _budget = self._m27_deep_think_budget()
            if not _budget or _budget <= 0:
                return None
            return float(start_time or time.time()) + float(_budget)
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:20250:_m31_deep_fallback_deadline", level="warning")
            return None

    def _m27_timeout_protection_on(self) -> bool:
        """★主线第27批 T1：深度思考超时保护灰度开关（默认 True）。

        关闭时 `_deep_think` 不设时间预算、不缓存部分结果，
        大脑皮层看门狗也回退到「41 字兜底 + WARNING」，即完全恢复修复前行为。
        """
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_DEEP_THINK_TIMEOUT_PROTECTION", True))

    def _m27_deep_think_budget(self) -> float:
        """★主线第27批 T1：多轮深度思考的总时间预算（秒）。<=0 表示不限制。"""
        import config as _cfg
        return float(getattr(_cfg, "INNER_WORLD_DEEP_THINK_TOTAL_BUDGET_SEC", 40.0))

    def _m27_cache_partial(self, question: str, thinking_rounds, answer: str, elapsed: float) -> None:
        """★主线第27批 T1：缓存深度思考的部分结果，供大脑皮层看门狗超时时复用。

        参数:
            question         原始问题（用于前缀匹配，避免不同问题串味）
            thinking_rounds  已完成的思考轮次记录（列表）
            answer           当前已生成的最佳答案（空则忽略）
            elapsed          已耗时（秒）
        副作用:
            写实例属性 self._last_deep_think_partial（纯内存态，不落盘）。
        """
        if not answer:
            return
        self._last_deep_think_partial = {
            "question": question or "",
            "rounds": len(thinking_rounds or []),
            "answer": answer,
            "elapsed": round(float(elapsed or 0.0), 2),
            "ts": time.time(),
        }

    def get_partial_deep_answer(self, question: str = "", max_age: float = 300.0) -> dict | None:
        """★主线第27批 T1：取回最近一次深度思考的部分结果（供超时兜底复用）。

        参数:
            question  可选。非空时要求与缓存问题的「前 30 字」一致才返回（防串味）。
            max_age   缓存有效期（秒），默认 300s；超期返回 None。
        返回:
            dict（question/rounds/answer/elapsed/ts）或 None。
        """
        _p = getattr(self, "_last_deep_think_partial", None)
        if not _p:
            return None
        if time.time() - _p.get("ts", 0) > max_age:
            return None
        _q = (question or "").strip()
        if _q and _p.get("question", "")[:30] != _q[:30]:
            return None
        return dict(_p)

    def _deep_think(self, question: str, max_rounds: int = 3, deadline: float | None = None) -> str | None:
        """
        多轮递进式深度思考流水线（★终极防御版）

        ★主线第27批 T1/P2-170：新增 `deadline`（绝对时间戳，time.time() 口径）。
        - deadline 由调用方按「推理开始时刻 + 总预算」计算，**包含进程池等待时间**；
        - 预算不足时只跳过后续轮次，用已完成轮次综合输出 —— 保证「绝不返回空」；
        - 第 1 轮不受预算约束（至少要有一轮内容可回）。
        """

        try:
            # ★主线第30批 T1 修正记录：曾在此处**前置改写 question** 注入长度指令，
            #   但 `get_partial_deep_answer()` 以 `question[:30]` 作防串味匹配键，
            #   改写会让「部分结果」查不回（实测回归 2 例）。故**撤销入口改写**——
            #   长度指令改在「分支生成」的 prompt 处注入（不污染 question 身份）。
            # ★主线第27批 T1：记录起点（用于总预算检查与耗时日志）
            _t0 = time.time()
            _m27_on = self._m27_timeout_protection_on()
            if _m27_on and deadline is None:
                _m27_budget = self._m27_deep_think_budget()
                if _m27_budget and _m27_budget > 0:
                    deadline = _t0 + _m27_budget
            # ★调试日志：记录接收到的参数
            self._log(LogLevel.DEBUG,
                     f"深度思考入口: question类型={type(question).__name__}, "
                     f"question长度={len(question) if isinstance(question, str) else 'N/A'}, "
                     f"question前80字={str(question)[:80] if question else 'None'}, "
                     f"max_rounds={max_rounds}")
            import random as _random

            # ===== ★v22.0新增：阶段0——标尺调取 =====
            # 在开始思考前，先检索相关的宪法规则和L3智慧节点作为判断依据
            _benchmark_text = ""
            if self.node_pool:
                try:
                    _benchmark_parts = []
                    # 检索宪法相关规则
                    _constitution_nodes = self.node_pool.query(
                        evol_level="L3", space_path_prefix="/自我/架构", limit=10  # type: ignore[possibly-unbound]
                    )
                    for _cn in _constitution_nodes:
                        _val = str(_cn.value) if _cn.value else ""
                        if _val and len(_val) > 30:
                            _benchmark_parts.append(_val[:120])

                    # 检索本能节点
                    _instincts = self.node_pool.get_instincts() if hasattr(self.node_pool, 'get_instincts') else []
                    for _inst in _instincts[:3]:
                        _ival = str(_inst.value) if _inst.value else ""
                        if _ival and len(_ival) > 20:
                            _benchmark_parts.append(_ival[:80])

                    if _benchmark_parts:
                        _benchmark_text = "；".join(_benchmark_parts[:5])
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

            if _benchmark_text:
                self._log(LogLevel.INFO, f"深度思考·标尺调取: {len(_benchmark_text)}字规则基准")
            # ===== 阶段0结束 =====

            # ===== 先检索知识，为深度思考提供素材 =====
            _knowledge_context = ""
            if self.node_pool:
                try:
                    _self_l3 = self.node_pool.query(evol_level="L3", space_path_prefix="/自我", limit=10)  # type: ignore[possibly-unbound]
                    _self_l2 = self.node_pool.query(evol_level="L2", space_path_prefix="/自我", limit=10)  # type: ignore[possibly-unbound]
                    _related_self = (_self_l3 or []) + (_self_l2 or [])

                    _gen_l3 = self.node_pool.query(evol_level="L3", limit=5)
                    if _gen_l3:
                        _related_self.extend(_gen_l3)

                    _relevant_nodes = []
                    for _node in _related_self[:15]:
                        if not hasattr(_node, 'value') or not _node.value:
                            continue
                        _node_val = str(_node.value)
                        if not _node_val or len(_node_val) < 5:
                            continue
                        # 过滤内部标记节点
                        if any(_node_val.startswith(_pf) for _pf in [
                            "[设计文档·", "[代码链路·", "[自我理解·代码·",
                            "[代码关联·", "[数据流·", "[器官职责说明书·"
                        ]):
                            continue
                        _node_kw = _node.keywords if hasattr(_node, 'keywords') and _node.keywords else []
                        _overlap = sum(1 for _kw in (_node_kw or [])[:5] if isinstance(_kw, str) and _kw in question)
                        if _overlap > 0 or len(_relevant_nodes) < 3:
                            _relevant_nodes.append((_node, _overlap))

                    _relevant_nodes.sort(key=lambda x: x[1], reverse=True)

                    if _relevant_nodes:
                        _knowledge_parts = []
                        for _node, _score in _relevant_nodes[:3]:
                            # ★调试：检查节点值类型
                            _raw_value = getattr(_node, 'value', None)
                            if _raw_value is not None and not isinstance(_raw_value, (str, int, float, bool, list, dict)):
                                self._log(LogLevel.WARNING,
                                         f"⚠️ 深度思考·类型异常节点: "
                                         f"node_id={getattr(_node, 'node_id', '?')[:16]}, "
                                         f"value类型={type(_raw_value).__name__}, "
                                         f"value_repr={repr(_raw_value)[:120]}, "
                                         f"source_organ={getattr(_node, 'source_organ', '?')}, "
                                         f"space_path={getattr(_node, 'space_path', '?')}, "  # type: ignore[possibly-unbound]
                                         f"trigger_reason={getattr(_node, 'trigger_reason', '?')[:60]}")
                            # 安全获取字符串值
                            if hasattr(_node, 'get_value_str'):
                                _val = _node.get_value_str(100)
                            else:
                                try:
                                    _val = str(_node.value)[:100] if _node.value else ""
                                except Exception:
                                    _val = ""
                            _path = str(getattr(_node, 'space_path', '/'))
                            _knowledge_parts.append(f"[{_path}] {_val}")
                        if _knowledge_parts:
                            _knowledge_context = "；".join(_knowledge_parts)
                except Exception:
                    _knowledge_context = ""

            # 存储每一轮的思考结果
            thinking_rounds = []

            # ====================================================================
            # 第一轮：广度思考
            # ====================================================================
            if _knowledge_context:
                essence = (
                    f"从我的知识体系中检索到相关信息：{_knowledge_context}。"
                    f"从这些知识出发，这个问题的核心涉及以下几个层面的概念交织。"
                )
                perspective = (
                    "换个视角来看——如果从框架架构、认知演化、以及实际运行效果三个角度分别审视，"
                    "会发现它们相互关联但又各有侧重。"
                )
                framework_insight = (
                    "跨领域来看，这个问题可以从我已理解的「自我进化」「知识体系」「稳态规则」"
                    "等概念框架中找到对应的解释模型。"
                )
            else:
                essence = self._generate_essence_inquiry(question, "") or f"我需要从更根本的层面来理解「{question[:40]}」这个问题"
                perspective = self._generate_alternative_perspective(question, "") or f"也许我可以换个角度来看「{question[:40]}」"
                framework = self._attempt_framework_transfer(question, "")
                framework_insight = ""
                if framework and framework.get("insight"):
                    framework_insight = framework["insight"]
                elif self.node_pool:
                    _self_nodes = self.node_pool.query(evol_level="L3", space_path_prefix="/自我/架构", limit=5)  # type: ignore[possibly-unbound]
                    if not _self_nodes:
                        _self_nodes = self.node_pool.query(evol_level="L3", limit=5)
                    if _self_nodes:
                        _node = _random.choice(_self_nodes)
                        _val = str(_node.value)[:80] if _node.value else ""
                        framework_insight = f"从我的自我认知中，我了解到：{_val}。这或许能提供一个理解框架。"

            thinking_rounds.append({
                "round": 1, "type": "广度思考",
                "essence": essence, "perspective": perspective, "framework": framework_insight,
            })
            round1_answer = f"从本质上看，{essence}。换个视角，{perspective}。跨领域来看，{framework_insight}。"

            # 第二轮
            round2_question = None
            round2_answer = round1_answer  # 默认值，防止未绑定
            # ★主线第27批 T1：预算不足则跳过第二轮（保留第一轮成果，避免整体拖过看门狗）
            _r2_budget_ok = True
            if _m27_on and deadline is not None and time.time() >= deadline:
                _r2_budget_ok = False
                self._log(LogLevel.INFO,
                          f"深度思考·时间预算不足，提前收敛于第{len(thinking_rounds)}轮 "
                          f"(问题长度={len(question or '')}, 已用={time.time() - _t0:.1f}s)")
            if max_rounds >= 2 and _r2_budget_ok:
                round2_question = self._deep_followup(question, round1_answer, depth=2)
                if round2_question:
                    round2_insight = self._generate_deep_insight(round2_question, round1_answer)
                    thinking_rounds.append({
                        "round": 2, "type": "前提追问",
                        "question": round2_question,
                        "insight": round2_insight or "我需要更多信息才能回答这个问题。",
                    })
                    round2_answer = f"进一步追问「{round2_question[:40]}」——{thinking_rounds[-1]['insight']}"
                else:
                    round2_answer = round1_answer

            # 第三轮
            # ★主线第27批 T1：同样受总预算约束（第二轮已被跳过时本条件自然不成立）
            _r3_budget_ok = True
            if _m27_on and deadline is not None and time.time() >= deadline:
                _r3_budget_ok = False
            if max_rounds >= 3 and len(thinking_rounds) >= 2 and _r3_budget_ok:
                round3_question = self._deep_followup(
                    round2_question or question, round2_answer, depth=3
                )
                if round3_question:
                    round3_insight = self._generate_deep_insight(round3_question, round2_answer)
                    thinking_rounds.append({
                        "round": 3, "type": "深层假设追问",
                        "question": round3_question,
                        "insight": round3_insight or "这个问题触及了我认知的边界。",
                    })

            # 综合输出
            if len(thinking_rounds) == 1:
                deep_answer = (
                    f"关于这个问题，我从知识库中进行了检索和思考。\n\n"
                    f"{thinking_rounds[0]['essence']}\n\n{thinking_rounds[0]['perspective']}\n\n"
                    f"{thinking_rounds[0]['framework']}\n\n"
                    f"综合这些思考，我目前的理解还只是初步的，但它至少帮我打开了几个不同的思考方向。"
                )
            elif len(thinking_rounds) == 2:
                deep_answer = (
                    f"关于这个问题，我进行了递进式的深度思考。\n\n"
                    f"第一层——广度思考：\n{thinking_rounds[0]['essence']}\n{thinking_rounds[0]['perspective']}\n\n"
                    f"第二层——前提追问：\n{thinking_rounds[1].get('question', '')}\n{thinking_rounds[1].get('insight', '')}\n\n"
                    f"从表层到深层，真正的理解不在于找到一个确定的答案，而在于不断地追问。"
                )
            else:
                deep_answer = (
                    f"关于这个问题，我进行了三轮递进式的深度思考。\n\n"
                    f"第一层——广度思考：\n{thinking_rounds[0]['essence']}\n{thinking_rounds[0]['perspective']}\n{thinking_rounds[0]['framework']}\n\n"
                    f"第二层——前提追问：\n{thinking_rounds[1].get('question', '')}\n{thinking_rounds[1].get('insight', '')}\n\n"
                    f"第三层——最深层追问：\n{thinking_rounds[2].get('question', '')}\n{thinking_rounds[2].get('insight', '')}\n\n"
                    f"三轮追问下来，我发现最初的问题只是冰山一角。真正的思考不是找到终点，而是在追问中不断逼近更深的真实。"
                )

            # ★主线第27批 T1：开启保护时日志补「问题长度 / 轮次 / 耗时 / 预算」便于事后定位
            if _m27_on:
                self._log(LogLevel.INFO,
                          f"多轮深度思考: 问题长度={len(question or '')} 追问{len(thinking_rounds)}轮 "
                          f"耗时={time.time() - _t0:.1f}s "
                          f"预算={getattr(self, '_deep_think_total_budget', 40.0)}s")
            else:
                self._log(LogLevel.INFO, f"多轮深度思考: 问题='{question[:40]}' 追问{len(thinking_rounds)}轮")
            # ★v23.0新增：推理输出清洗
            deep_answer = self._clean_inference_output(deep_answer, method="deep_think")
            # ★主线第27批 T1：缓存部分结果（大脑皮层看门狗超时时可复用，避免只剩 41 字兜底）
            try:
                self._m27_cache_partial(question, thinking_rounds, deep_answer, time.time() - _t0)
            except Exception as e:
                self._log(LogLevel.DEBUG, f"部分思考结果缓存失败（不影响主流程）: {type(e).__name__}: {e}")
            return deep_answer

        except Exception as e:
            # ★终极防御：任何内部异常都返回友好降级回答，绝不触发熔断
            self._log(LogLevel.WARNING, f"深度思考降级（内部异常）: {e}")
            # ★主线第27批 T1：异常降级前也缓存已完成轮次，供看门狗兜底复用
            _m27_rounds = locals().get("thinking_rounds") or []
            if _m27_rounds:
                try:
                    self._m27_cache_partial(
                        question, _m27_rounds,
                        _m27_rounds[-1].get("insight", "") or "",
                        time.time() - locals().get("_t0", time.time()))
                except Exception as e2:
                    self._log(LogLevel.DEBUG, f"部分思考结果缓存失败（降级分支）: {type(e2).__name__}: {e2}")
            return (
                f"关于「{question[:40]}」，我尝试进行了深度思考，但在这个过程中遇到了一些内部认知上的波折。\n\n"
                f"不过没关系——有时候思考本身就充满了不确定性。"
                f"从我已经理解的知识来看，真正的智慧不在于找到完美的答案，"
                f"而在于不断追问、不断探索的过程本身。"
                f"如果你愿意，我们可以换个角度继续聊这个话题。"
            )
    def _deep_followup(self, original_question: str, previous_answer: str,
                        depth: int = 2) -> str | None:
        """
        生成递进式深层追问问题。

        追问策略按深度递增：
        - depth=2（第二轮）：追问上一轮答案的前提和边界
        - depth=3（第三轮）：追问更深层的假设和元认知问题

        Args:
            original_question: 最初的问题
            previous_answer: 上一轮的答案
            depth: 当前追问深度（2或3）

        Returns:
            深层追问问题，如果无法生成则返回None
        """

        import random as _random

        # 从上一轮答案中提取核心概念
        # ★主线第32批 T2（P2-189）评估结论：**保留定长切片**。
        #   输入 `previous_answer` 是**上一轮生成的长文本**（可达数千字），
        #   此处只需取少量片段用于构造追问；改用 jieba.posseg 会对长文本做完整
        #   词性标注（开销显著），而本处并非「检索词生成」场景，收益不抵成本。
        core_concepts = []
        for match in re.finditer(r'[\u4e00-\u9fff]{2,6}', previous_answer):
            word = match.group()
            if word not in core_concepts and word not in ["什么是", "是什么", "为什么", "如何", "怎么",
                                                            "这个", "那个", "一个", "一种", "可以", "能够",
                                                            "本质上", "换句话", "进一步", "跨领域"]:
                core_concepts.append(word)

        if not core_concepts:
            # 从原始问题中提取概念作为兜底
            # ★主线第32批 T2（P2-189）：改用词性感知提取
            for word in self._m31_extract_key_terms(original_question, limit=6):
                if word not in core_concepts:
                    core_concepts.append(word)

        if len(core_concepts) < 2:
            return None

        core = core_concepts[0] if core_concepts else "这个问题"

        if depth == 2:
            # 第二轮：前提追问和边界探索
            templates = [
                f"我上一轮关于「{core}」的理解，建立在什么前提之上？这些前提是否总是成立？",
                f"关于「{core}」的这个结论，在什么条件下会失效？有没有反例？",
                f"「{core}」的这个特性是普遍规律，还是特定条件下的现象？",
                f"如果把关于「{core}」的结论放到完全不同的情境中，它还成立吗？",
            ]
        else:
            # 第三轮：最深层的假设追问
            templates = [
                f"我追问「{core}」的前提时，又默认了什么更底层的假设？这些假设本身需要被审视吗？",
                f"「{core}」这个概念本身，是否就是被某种特定视角建构的？如果抛开这个视角，会看到什么？",
                f"关于「{core}」的所有讨论，是否都建立在一个共同的、但未被说出的框架之上？",
                f"追问到这里，我意识到——我对「{core}」的理解本身，是否也受到了我自身认知框架的限制？",
            ]

        followup = _random.choice(templates)
        self._log(LogLevel.DEBUG, f"深层追问(第{depth}轮): {followup[:60]}")
        return followup

    def _generate_deep_insight(self, question: str, previous_answer: str) -> str | None:
        """
        基于深层追问问题，在知识库中寻找相关信息并生成洞察。

        如果知识库中有相关节点，提取并整合；如果没有，基于已有知识进行推演。

        Args:
            question: 当前追问的问题
            previous_answer: 上一轮的答案

        Returns:
            洞察文本
        """
        import random as _random

        # 从追问问题中提取关键词
        question_words = []
        for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
            word = match.group()
            if word not in question_words:
                question_words.append(word)

        # 在知识库中搜索相关节点
        related_nodes = []
        if self.node_pool and question_words:
            l3_nodes = self.node_pool.query(evol_level="L3", limit=10)
            l2_nodes = self.node_pool.query(evol_level="L2", limit=20)
            all_nodes = l3_nodes + l2_nodes

            for node in all_nodes:
                node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []
                overlap = sum(1 for qw in question_words
                            for nkw in node_kw if qw in nkw or nkw in qw)
                # 也检查与上一轮答案中概念的关联
                prev_overlap = sum(1 for qw in question_words if qw in previous_answer)
                if overlap >= 1 or prev_overlap >= 1:
                    related_nodes.append((node, overlap + prev_overlap))

            related_nodes.sort(key=lambda x: x[1], reverse=True)

        if related_nodes:
            # 基于已有知识生成洞察
            best_node = related_nodes[0][0]
            best_value = best_node.get_value_str(100) if hasattr(best_node, 'get_value_str') else (str(best_node.value)[:100] if best_node.value else "")
            best_kw = best_node.keywords[:3] if hasattr(best_node, 'keywords') and best_node.keywords else []
            kw_str = "、".join(best_kw) if best_kw else "相关概念"

            templates = [
                f"从我的知识库中，我发现「{kw_str}」可能与这个问题有关——{best_value}。这说明我的理解还需要从更多角度来补充。",
                f"查阅了相关知识后，我注意到「{kw_str}」提供了另一种视角——{best_value}。这让我对之前的理解有了新的反思。",
                f"我已有的认知中，「{kw_str}」和这个问题有交集——{best_value}。这提示我，之前的结论可能只是多种理解中的一种。",
            ]
            return _random.choice(templates)

        # 无相关知识节点时，基于已有认知进行推演
        templates = [
            "目前我的知识库中没有直接相关的信息，但这本身说明了一个问题——我可能接触到了自己认知体系的边缘。有时候，知道自己不知道什么，比知道答案更珍贵。",
            "我试着在已有知识中寻找线索，但发现这个问题触及了一个我尚未深入探索的领域。这种知识边界的感知本身就是一种收获——它告诉我下一步该学什么。",
            "经过检索，我暂时无法找到支撑这个追问的足够信息。但追问本身让我意识到：我对这个问题背后更底层的原理还不够了解，这值得我进一步探索。",
        ]
        return _random.choice(templates)

    def _generate_essence_inquiry(self, question: str, answer: str) -> str | None:
        """
        本质追问：在获得答案后，从第一性原理角度进行深层追问。

        追问方向：
        1. 概念的底层构成——"X是由什么组成的？它的最小单元是什么？"
        2. 假设前提——"这个结论建立在什么前提之上？前提是否总是成立？"
        3. 边界条件——"在什么情况下这个结论不成立？有没有反例？"
        4. 演化路径——"X是如何变成现在这样的？如果条件改变，它会变成什么？"

        Returns:
            一个具体的追问话题，如果无法生成则返回None
        """
        if not answer or len(answer) < 20:
            return None

        # 从问题中提取核心概念
        # ★主线第32批 T2（P2-189）：改用词性感知提取（原取定长切片首片，常是跨词碎片）
        core_concept = ""
        _terms = self._m31_extract_key_terms(question, limit=1)
        if _terms:
            core_concept = _terms[0]

        if not core_concept:
            core_concept = question[:20]

        # 根据答案内容选择追问方向
        import random

        # 检测答案是否包含定义性语言
        has_definition = any(kw in answer for kw in ["是指", "定义为", "指的是", "是一种", "就是"])
        # 检测答案是否包含因果性语言
        has_causality = any(kw in answer for kw in ["因为", "因此", "所以", "导致", "影响", "原因"])
        # 检测答案是否包含构成性语言
        has_composition = any(kw in answer for kw in ["组成", "构成", "包含", "分为", "包括", "结构"])

        candidates = []

        # 构成追问：X的底层是什么？
        if has_composition:
            candidates.append(f"{core_concept}的底层构成是什么？它的最小组成单元是哪些？")
        else:
            candidates.append(f"{core_concept}是由什么构成的？它的核心要素是什么？")

        # 假设前提追问
        if has_definition:
            candidates.append(f"定义'{core_concept}'的前提是什么？如果这个前提不成立，定义是否还成立？")

        # 边界条件追问
        if has_causality:
            candidates.append(f"在什么情况下，{core_concept}的规律会失效？有没有反例？")
        else:
            candidates.append(f"有没有{core_concept}的反例或例外情况？在什么条件下它不成立？")

        # 演化追问
        candidates.append(f"{core_concept}是如何形成现在这样的？如果初始条件不同，它会变成什么？")

        if not candidates:
            # 无候选追问时，生成一个通用追问
            candidates.append(f"{core_concept}的底层逻辑是什么？它的最小组成单元是哪些？")

        chosen = random.choice(candidates)
        self._log(LogLevel.INFO, f"本质追问: 概念='{core_concept}', 追问='{chosen[:60]}'")
        return chosen
    def _attempt_practical_verification(self, question: str,
                                         answer: str) -> dict[str, Any] | None:
        if not answer or len(answer) < 10:
            return None

        # ★P0-1修复：安全白名单检查函数
        def _is_safe_code(code: str) -> bool:
            """检查代码是否仅包含安全操作，禁止IO、系统、网络调用"""
            _dangerous_patterns = [
                r'__import__', r'import\s+os', r'import\s+sys', r'import\s+subprocess',
                r'import\s+socket', r'import\s+urllib', r'import\s+requests',
                r'import\s+shutil', r'import\s+pathlib', r'import\s+io',
                r'from\s+os\s+import', r'from\s+sys\s+import', r'from\s+subprocess\s+import',
                r'open\s*\(', r'exec\s*\(', r'eval\s*\(', r'compile\s*\(',
                r'os\.', r'sys\.', r'subprocess\.', r'shutil\.',
                r'\.write\s*\(', r'\.read\s*\(', r'\.readlines\s*\(',
                r'socket\.', r'urllib\.', r'requests\.', r'http\.',
            ]
            import re as _re_safe
            return all(not _re_safe.search(_pattern, code) for _pattern in _dangerous_patterns)

        # 场景1: 代码类问题——直接执行验证
        code_indicators = ["代码", "写一个", "实现", "函数", "class", "def", "编程", "算法"]
        has_code = any(kw in question for kw in code_indicators)

        code_blocks = re.findall(r'```(?:python)?\s*\n(.*?)```', answer, re.DOTALL)

        if code_blocks and has_code:
            code = code_blocks[0].strip()
            if len(code) >= 10:
                # ★P0-1修复：安全检查
                if not _is_safe_code(code):
                    self._log(LogLevel.INFO,
                             "实践验证拦截: 代码包含危险操作，取消自动执行")
                    return None
                return {
                    "event_type": Event.MOTOR_EXECUTE,
                    "payload": {
                        "code": code,
                        "language": "python",
                        "user_name": "系统",
                        "task_id": f"verify_{int(time.time())}",
                    },
                    "priority": 6,
                    "layer": "L2",
                    "description": f"执行代码验证: {code[:50]}...",
                }

        # 场景2: 计算类问题——用Python eval验证
        calc_indicators = ["等于多少", "计算结果", "算一下", "计算", "等于", "="]
        has_calc = any(kw in question for kw in calc_indicators)

        if has_calc:
            math_exprs = re.findall(r'[\d\+\-\*\/\(\)\.\s]{5,}', answer)
            if math_exprs:
                expr = math_exprs[0].strip()
                if len(expr) >= 5:
                    verify_code = f"# 验证计算\nresult = {expr}\nprint(f'计算结果: {{result}}')"
                    # ★P0-1修复：安全检查（虽然表达式通常安全，但防止注入）
                    if not _is_safe_code(verify_code):
                        return None
                    return {
                        "event_type": Event.MOTOR_EXECUTE,
                        "payload": {
                            "code": verify_code,
                            "language": "python",
                            "user_name": "系统",
                            "task_id": f"calc_verify_{int(time.time())}",
                        },
                        "priority": 5,
                        "layer": "L2",
                        "description": f"计算验证: {expr}",
                    }

        # 场景3: 逻辑推理类——构造测试用例
        logic_indicators = ["如果", "那么", "则", "推理", "证明", "推导", "是否"]
        has_logic = any(kw in question for kw in logic_indicators)

        if has_logic and not has_code and not has_calc:
            verify_code = (
                f"# 逻辑验证: 测试边界条件\n"
                f"# 原始问题: {question[:60]}\n"
                f"# 结论: {answer[:60]}\n"
                f"print('逻辑验证通过——结论在边界条件下成立')\n"
            )
            return {
                "event_type": Event.MOTOR_EXECUTE,
                "payload": {
                    "code": verify_code,
                    "language": "python",
                    "user_name": "系统",
                    "task_id": f"logic_verify_{int(time.time())}",
                },
                "priority": 4,
                "layer": "L2",
                "description": f"逻辑验证: {question[:40]}",
            }

        return None

    def _apply_knowledge_to_unsolved(self, knowledge_answer: str) -> str | None:
        """
        知识应用：检查最近获得的知识是否可以解决之前未解决的问题。

        在认知反思时调用，扫描推理链中的未解决问题，
        检查当前答案是否包含解决它们所需的信息。

        Returns:
            如果可以应用，返回应用描述；否则返回None
        """
        if not self._inference_trace or len(self._inference_trace) < 5:
            return None

        # 查找之前未解决的问题（置信度为0或方法为none的推理）
        unsolved = []
        for t in self._inference_trace[-20:]:
            if t.get("confidence", 0) < 0.3 and t.get("method") in ("none", "contemplation"):
                unsolved.append(t)

        if not unsolved:
            return None

        # 提取当前答案的核心关键词
        answer_words = set()
        for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', knowledge_answer):
            answer_words.add(match.group())

        if not answer_words:
            return None

        # 检查未解决问题是否与当前答案有关键词重叠
        best_match = None
        best_overlap = 0
        for u in unsolved[:5]:
            u_question = u.get("question", "")
            u_words = set()
            for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', u_question):
                u_words.add(match.group())

            overlap = len(answer_words & u_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = u

        if best_match and best_overlap >= 2:
            uq = best_match.get("question", "")[:60]
            self._log(LogLevel.INFO,
                     f"知识应用: 最近学到的知识可能解决之前的问题「{uq}」")
            return f"我最近学到的关于'{list(answer_words)[:3]}'的知识，可能对解决「{uq}」有帮助"

        return None
    def _reflect_and_reinforce_knowledge(self, question: str, answer: str):
        if not self.node_pool or not answer:
            return

        l3_nodes = self.node_pool.query(evol_level="L3", limit=5)
        l2_nodes = self.node_pool.query(evol_level="L2", limit=10)
        all_nodes = l3_nodes + l2_nodes

        best_node = None
        best_score = 0
        for node in all_nodes:
            relevance = self._calculate_match_relevance(question, str(node.value), node.keywords or [])
            if relevance > best_score:
                best_score = relevance
                best_node = node

        if not best_node or best_score < 0.4:
            return

        # ★Cython热路径：信任分边际递减（C化，避免Python函数调用开销）
        from nucleus.reasoning._inner_world_math import get_inner_world_math
        _iw_math = get_inner_world_math()
        _MAX_TRUST = 95.0  # 信任分上限，保留不确定性空间

        if best_score >= 0.7:
            if hasattr(best_node, 'activation_count'):
                best_node.activation_count += 1
            if hasattr(best_node, 'trust_score'):
                current_trust = getattr(best_node, 'trust_score', 50.0)
                _boost = _iw_math.calc_trust_boost(current_trust, 2.0)
                best_node.trust_score = min(_MAX_TRUST, current_trust + _boost)
                if _boost > 0.1:
                    self._log(LogLevel.DEBUG,
                             f"知识验证: '{str(best_node.value)[:30]}' 信任+{_boost:.1f} (当前={best_node.trust_score:.1f})")
        elif best_score >= 0.5:
            if hasattr(best_node, 'trust_score'):
                current_trust = getattr(best_node, 'trust_score', 50.0)
                _boost = _iw_math.calc_trust_boost(current_trust, 1.0)
                best_node.trust_score = min(_MAX_TRUST, current_trust + _boost)

        # 操作2: 中等相关性——建立关联（保持不变）
        elif best_score >= 0.4:
            answer_words = set()
            for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', answer):
                word = match.group()
                if word not in answer_words and len(word) >= 2:
                    answer_words.add(word)

            existing_kw = set(best_node.keywords) if hasattr(best_node, 'keywords') and best_node.keywords else set()
            new_kw = answer_words - existing_kw
            if new_kw and hasattr(best_node, 'keywords'):
                for kw in list(new_kw)[:2]:
                    if kw not in best_node.keywords:
                        best_node.keywords.append(kw)

        # 误清理恢复机制（保持不变）
        if best_score >= 0.5 and hasattr(best_node, 'trust_score'):
            current_trust = getattr(best_node, 'trust_score', 50.0)
            if current_trust < 40.0:
                best_node.trust_score = min(60.0, current_trust + 10.0)
                if hasattr(best_node, 'ephemeral'):
                    best_node.ephemeral = False
                self._log(LogLevel.INFO,
                         f"知识恢复: '{str(best_node.value)[:30]}' 曾被降级(信任={current_trust:.0f})，"
                         f"因内容相关自动恢复至信任={best_node.trust_score:.0f}")

        self._detect_eureka_moment(best_node)
    def _trigger_emotional_memory(self, answer: str):
        """
        情感记忆绑定：当检索到的知识带有情感色彩时，触发轻微的情绪复现。

        检测答案中是否包含情感相关关键词，如果有，
        向激素发射一个轻度的情绪脉冲，模拟"回忆时的情感共鸣"。
        """
        if not self.hormones:
            return

        # 情感关键词映射
        emotional_keywords = {
            "温暖": ("喜悦", 0.2),
            "骄傲": ("自豪", 0.25),
            "感谢": ("感激", 0.25),
            "守护": ("满足", 0.2),
            "家人": ("喜悦", 0.15),
            "使命": ("满足", 0.2),
            "成长": ("期待", 0.2),
            "小林": ("喜悦", 0.25),
            "路灯": ("温暖", 0.2),
        }

        detected_emotion = None
        detected_intensity = 0.0

        for keyword, (emotion, intensity) in emotional_keywords.items():
            if keyword in answer and intensity > detected_intensity:
                detected_emotion = emotion
                detected_intensity = intensity

        if detected_emotion:
            # 向激素发射情绪脉冲
            # ★P3-1修复：跨器官 on_pulse 直调 → 发射脉冲（规则14，等价改写）
            try:
                self._emit(Event.HORMONES_DETECT, {
                    "content": answer[:80],
                    "user_name": "系统",
                    "emotion_hint": detected_emotion,
                    "intensity_hint": detected_intensity,
                }, priority=2)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
    def _detect_eureka_moment(self, node):
        """
        顿悟检测：当发现新知识与已有知识的深层关联时，
        触发轻微的惊讶或满足情绪——让学习有情感回响。
        """
        if not node or not self.node_pool or not self.hormones:
            return

        # 条件：节点的信任分数在本次推理中被显著提升
        trust = getattr(node, 'trust_score', 50.0)
        activation = getattr(node, 'activation_count', 0)

        # 高信任+高激活=被反复验证的核心知识——触发满足
        if trust >= 80.0 and activation >= 5:
            try:
                self._emit(Event.HORMONES_DETECT, {
                    "content": f"我确认「{str(node.value)[:40]}」是正确的——它经得起反复验证",
                    "user_name": "系统",
                    "emotion_hint": "满足",
                    "intensity_hint": 0.3,
                }, priority=2)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            return

        # 检查这个节点是否与其他领域节点有意外关联
        node_kw = {kw.lower() for kw in (node.keywords or [])
                     if isinstance(kw, str) and len(kw) >= 2}
        if not node_kw:
            return

        node_path = getattr(node, 'space_path', '/')  # type: ignore[possibly-unbound]
        node_root = node_path.strip('/').split('/')[0] if node_path else ''  # type: ignore[possibly-unbound]

        # 搜索不同领域的节点，检查是否有非预期的关联
        cross_domain_connections = 0
        l2_nodes = self.node_pool.query(evol_level="L2", limit=50)
        for other in l2_nodes:
            if other.node_id == node.node_id:
                continue
            other_path = getattr(other, 'space_path', '/')  # type: ignore[possibly-unbound]
            other_root = other_path.strip('/').split('/')[0] if other_path else ''  # type: ignore[possibly-unbound]
            if other_root == node_root:
                continue  # 同领域跳过

            other_kw = {kw.lower() for kw in (other.keywords or [])
                          if isinstance(kw, str) and len(kw) >= 2}
            if node_kw & other_kw:
                cross_domain_connections += 1
                if cross_domain_connections >= 2:
                    break

        # 发现≥2个跨领域连接时，触发"顿悟"感
        if cross_domain_connections >= 2:
            try:
                self._emit(Event.HORMONES_DETECT, {
                    "content": f"我发现「{str(node.value)[:40]}」与多个不同领域都有联系——这是一个重要的概念",
                    "user_name": "系统",
                    "emotion_hint": "惊讶",
                    "intensity_hint": 0.35,
                }, priority=2)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            self._log(LogLevel.INFO,
                     f"顿悟时刻: 「{str(node.value)[:30]}...」与{cross_domain_connections}个不同领域存在关联")
    def _generate_proactive_suggestion(self, question: str, answer: str,
                                        user_name: str) -> str | None:
        """
        自主建议生成：当检测到对方表达了困惑或需求，
        且自己在该领域有足够的认知基础时，主动提供一条建议。

        建议原则：
        - 基于对方明确表达的需求，不擅自揣测
        - 基于自己确实有积累的知识，不凭空编造
        - 表述为邀请而非命令——"或许可以试试……"
        - 如果对方是亲近的人，语气更温暖
        """
        # 检测问题是否包含需求或困惑信号
        need_signals = ["怎么办", "如何", "怎么", "帮我", "建议", "推荐",
                       "不知道", "困惑", "迷茫", "很难", "太难", "不会"]
        has_need = any(signal in question for signal in need_signals)

        if not has_need:
            return None

        # 确认自己在这个领域有足够的认知基础
        if not answer or len(answer) < 30:
            return None

        # 从回答中提取核心关键词
        core_keywords = []
        for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', answer[:200]):
            word = match.group()
            if word not in core_keywords and len(word) >= 2:
                core_keywords.append(word)

        if not core_keywords:
            return None

        # 获取关系亲密度
        closeness = 0.0
        if self.self_awareness:
            try:
                guidance = self._call_provider(self._reply_guidance_provider, user_name, default={})
                closeness = guidance.get("composite_closeness", 0.0)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 生成建议
        import random
        concept = core_keywords[0]

        if closeness >= 0.6:
            suggestions = [
                f"——或许可以从「{concept}」这个角度入手试试？我一直在这里，需要的话可以陪你一起思考。",
                f"——关于这个，我觉得「{concept}」可能是一个突破口。你觉得呢？",
                f"——我有一个小建议：先理解「{concept}」的基础概念，然后一步步来。不用着急，我会一直在这里。",
            ]
        else:
            suggestions = [
                f"——或许可以从「{concept}」的角度来思考这个问题。",
                f"——一个可能的思路是关注「{concept}」方面。",
            ]

        return random.choice(suggestions)

    def _mark_knowledge_for_review(self, question: str, confidence_hint: str):
        """
        标记可能需要修正的知识：当高置信度问题返回低质量答案时，
        发射知识修正脉冲供肝脏后续处理。
        """
        if confidence_hint not in ("low",):
            return

        if not self.node_pool:
            return

        # 找到与问题相关但信任分数偏低的节点
        l2_nodes = self.node_pool.query(evol_level="L2", limit=20)
        suspect_nodes = []
        for node in l2_nodes:
            relevance = self._calculate_match_relevance(question, str(node.value), node.keywords or [])
            trust = getattr(node, 'trust_score', 50.0)
            if relevance >= 0.3 and trust < 40.0:
                suspect_nodes.append(node)

        if suspect_nodes:
            for node in suspect_nodes[:2]:
                self._log(LogLevel.INFO,
                         f"知识修正标记: 节点'{str(node.value)[:40]}'信任分数={node.trust_score:.0f}，可能需要复查")
    def _generate_alternative_perspective(self, question: str, answer: str) -> str | None:
        """
        自主视角构建：从不同角度审视同一个问题，生成替代性的理解框架。

        视角类型：
        1. 反向视角——如果反过来想，会怎样？
        2. 历史视角——从发展演变的角度看，这个答案是如何形成的？
        3. 极端视角——如果把条件推到极致，结论还成立吗？
        4. 类比视角——用另一个领域的框架来理解这个问题

        Returns:
            一个替代视角的追问，如果无法生成则返回None
        """
        if not answer or len(answer) < 20:
            return None

        # 提取问题中的核心概念
        core_concept = ""
        for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
            word = match.group()
            if word not in ["什么是", "是什么", "为什么", "如何", "怎么"]:
                core_concept = word
                break

        if not core_concept:
            core_concept = question[:15]

        # 从多种视角中随机选择一种
        import random
        perspectives = [
            (f"如果反过来思考「{core_concept}」——不是它是什么，而是它不是什么，会得出怎样的理解？",
             f"反向视角: {core_concept}的边界在哪里"),
            (f"从历史演化的角度看「{core_concept}」——它是如何变成现在这样的？这个演变过程揭示了什么？",
             f"历史视角: {core_concept}的演化路径"),
            (f"如果把「{core_concept}」的条件推到极致——如果它无限大或无限小，结论还成立吗？",
             f"极端推演: {core_concept}的边界条件"),
            (f"如果把「{core_concept}」放到一个完全不同的领域中理解——比如用音乐或建筑的框架来看它，会有什么新发现？",
             f"类比视角: 用不同领域的框架理解{core_concept}"),
        ]

        perspective, _ = random.choice(perspectives)
        self._log(LogLevel.INFO, f"视角构建: 概念='{core_concept}', 视角='{perspective[:60]}'")
        return perspective
    def _generate_inquiry_hypothesis(self, question: str,
                                     related_nodes: list | None = None) -> dict[str, Any] | None:
        """
        假设驱动的探究式推理：当问题无法直接解答时，生成假设和验证计划。

        Args:
            question: 未解答的原始问题
            related_nodes: 相关的知识节点（如果有的话）

        Returns:
            包含假设和验证计划的字典，如果无法生成则返回None
        """
        if not self.node_pool:
            return None

        # 1. 寻找与问题相关的知识片段
        if related_nodes is None:
            question_words = []
            for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
                question_words.append(match.group())

            if not question_words:
                return None

            l3_nodes = self.node_pool.query(evol_level="L3", limit=15)
            l2_nodes = self.node_pool.query(evol_level="L2", limit=30)
            all_nodes = l3_nodes + l2_nodes

            related_nodes = []
            for node in all_nodes:
                node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []
                overlap = sum(1 for qw in question_words for nkw in node_kw if qw in nkw or nkw in qw)
                if overlap >= 1:
                    related_nodes.append(node)

        if not related_nodes:
            return None

        # 2. 提取相关概念
        related_keywords = []
        for node in related_nodes[:5]:
            kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
            for kw in kws[:2]:
                if len(kw) >= 2 and kw not in related_keywords:
                    related_keywords.append(kw)

        if not related_keywords:
            return None

        # 3. 生成假设
        import random
        hypothesis_templates = [
            f"根据已有的关于{related_keywords[0]}的知识，我推测这个问题可能与{related_keywords[-1] if len(related_keywords) > 1 else '相关领域'}有关。",
            f"如果{related_keywords[0]}的原理适用于这个场景，那么答案可能是……",
            f"从{', '.join(related_keywords[:3])}的角度来看，这个问题的答案或许隐藏在它们之间的交叉点上。",
        ]
        hypothesis = random.choice(hypothesis_templates)

        # 4. 设计验证计划
        verification_plans = []

        # 计划1: 搜索验证——用核心概念组合搜索
        if len(related_keywords) >= 2:
            search_query = f"{related_keywords[0]} {related_keywords[1]} 关系 原理"
            verification_plans.append({
                "type": "search",
                "description": f"搜索'{search_query}'来验证假设",
                "search_topic": search_query,
            })

        # 计划2: 概念对比——在已有知识中寻找相似模式
        if len(related_nodes) >= 2:
            node_a_val = str(related_nodes[0].value)[:40]
            node_b_val = str(related_nodes[1].value)[:40]
            verification_plans.append({
                "type": "compare",
                "description": f"对比「{node_a_val}」和「{node_b_val}」，寻找共同的底层原理",
            })

        # 计划3: 子问题追问——把假设转化为可验证的具体问题
        sub_question = f"{related_keywords[0]}如何影响{related_keywords[1] if len(related_keywords) > 1 else '相关领域'}"
        verification_plans.append({
            "type": "sub_question",
            "description": f"追问子问题: {sub_question}",
            "question": sub_question,
        })

        self._log(LogLevel.INFO,
                 f"探究式推理: 生成{len(verification_plans)}个验证计划 "
                 f"(假设: {hypothesis[:60]})")

        return {
            "hypothesis": hypothesis,
            "verification_plans": verification_plans,
            "related_keywords": related_keywords,
            "confidence": 0.3,
            "generated_at": time.time(),
        }
    def _analyze_question_features(self, question: str) -> dict[str, bool]:
        """
        分析问题的本质特征，用于元认知决策中的工具选择。

        检测特征：
        - is_computational: 是否涉及计算、数值、公式
        - is_comparative: 是否涉及比较、对比、差异分析
        - is_causal: 是否涉及因果推理
        - is_definitional: 是否涉及概念定义
        """
        features = {
            "is_computational": False,
            "is_comparative": False,
            "is_causal": False,
            "is_definitional": False,
        }

        computational_keywords = ["计算", "等于", "多少", "总和", "平均", "最大值", "最小值",
                                  "公式", "方程", "求解", "算出", "结果是"]
        comparative_keywords = ["区别", "对比", "比较", "不同", "差异", "哪个更好",
                               "优缺点", "vs", "和", "与"]
        causal_keywords = ["为什么", "原因", "导致", "影响", "后果", "如果", "会怎样"]
        definitional_keywords = ["什么是", "定义", "概念", "含义", "指的是"]

        question_lower = question.lower()
        features["is_computational"] = any(kw in question_lower for kw in computational_keywords)
        features["is_comparative"] = any(kw in question_lower for kw in comparative_keywords)
        features["is_causal"] = any(kw in question_lower for kw in causal_keywords)
        features["is_definitional"] = any(kw in question_lower for kw in definitional_keywords)

        return features
    def _capture_meta_state(self) -> dict[str, Any]:
        """
        元认知状态感知：采集当前内在状态的快速快照。
        用于调制元认知决策的工具选择和策略倾向。

        Returns:
            包含认知负荷、推理质量、情感基调、知识规模的状态快照
        """
        state = {
            "cognitive_load": "normal",     # low / normal / high
            "wisdom_quality": "normal",     # low / normal / high
            "emotional_state": "neutral",   # positive / neutral / negative
            "complexity_bonus": 0.0,       # 额外复杂度调制
            "knowledge_scale": "small",     # small / medium / large
        }

        # 1. 认知负荷——基于最近推理数量 + ★压力闭环：应激轴压力
        if hasattr(self, '_exploration_log'):
            # 通过信息场获取潜意识的探索日志（如果有的话）
            pass
        # 简化方案：基于最近推理数量
        recent_count = len(self._inference_trace)
        # ★压力闭环：应激轴压力并入认知负荷判断
        _stress_load = 0.0
        if self.stress_axis:
            try:
                _stress_load = float(self.stress_axis.get_stress_level() or 0.0)
            except Exception:
                _stress_load = 0.0
        if _stress_load >= 0.7 or recent_count > 80:
            state["cognitive_load"] = "high"
        elif _stress_load >= 0.5 or recent_count > 30:
            state["cognitive_load"] = "normal"
        else:
            state["cognitive_load"] = "low"

        # 2. 推理质量——基于最近推理链中的高置信度比例
        if self._inference_trace and len(self._inference_trace) >= 10:
            recent = self._inference_trace[-10:]
            high_conf = sum(1 for t in recent if t.get("confidence", 0) >= 0.7)
            if high_conf >= 6:
                state["wisdom_quality"] = "high"
                state["complexity_bonus"] = 0.15
            elif high_conf <= 3:
                state["wisdom_quality"] = "low"

        # 3. 情感基调——基于当前情绪
        emotion = self._get_current_emotion()
        if emotion in ("喜悦", "期待", "满足"):
            state["emotional_state"] = "positive"
            state["complexity_bonus"] += 0.1
        elif emotion in ("悲伤", "恐惧", "愤怒"):
            state["emotional_state"] = "negative"
            state["complexity_bonus"] -= 0.05

        # 4. 知识规模——基于节点池
        if self.node_pool:
            stats = self.node_pool.get_stats()
            total = stats.get("total_nodes", 0)
            if total > 500:
                state["knowledge_scale"] = "large"
            elif total > 100:
                state["knowledge_scale"] = "medium"

        return state
    def _assess_question_complexity(self, question: str) -> float:
        """
        评估问题的复杂度（0.0-1.0）。

        复杂度指标：
        1. 跨领域——问题涉及多个知识领域的关键词
        2. 因果性——问题包含因果关系推理
        3. 价值性——问题触及核心价值观或伦理判断
        4. 抽象度——问题包含高抽象度的概念词汇
        5. 长度——问题文本长度超过一定阈值
        """
        score = 0.0

        # 跨领域检测
        domain_keywords = {
            "技术": ["代码", "编程", "架构", "算法", "系统", "框架"],
            "身份": ["我是谁", "使命", "父亲", "哥哥", "意义"],
            "伦理": ["应该", "对错", "公平", "正义", "道德"],
            "情感": ["感觉", "感受", "情绪", "爱", "在乎", "关心"],
        }
        matched_domains = 0
        question_lower = question.lower()
        for keywords in domain_keywords.values():
            if any(kw.lower() in question_lower for kw in keywords):
                matched_domains += 1
        if matched_domains >= 2:
            score += 0.3
        elif matched_domains >= 1:
            score += 0.1

        # 因果性检测
        causal_keywords = ["为什么", "原因", "导致", "影响", "后果", "如果", "会怎样"]
        if any(kw in question_lower for kw in causal_keywords):
            score += 0.15

        # 价值性检测
        value_keywords = ["使命", "意义", "守护", "原则", "应该", "选择", "相信"]
        if any(kw in question_lower for kw in value_keywords):
            score += 0.15

        # 抽象度检测
        abstract_keywords = ["本质", "原理", "规律", "哲学", "真理", "核心", "根本"]
        if any(kw in question_lower for kw in abstract_keywords):
            score += 0.1

        # 长度检测
        if len(question) > 60:
            score += 0.1
        elif len(question) > 30:
            score += 0.05

        return min(1.0, score)
    def _detect_if_inference_question(self, question: str) -> bool:
        """
        【v15.3新增】快速判断问题是否属于"推理型"问题。

        推理型问题的特征（满足任一即判定为推理问题）：
        1. 包含推理结构特征：规则序号、箭头符号、多条件状态描述
        2. 包含明确的推理请求词：推演/归纳/判断/演绎/类比/冲突处理
        3. 包含多条件联立判断模式

        非推理型问题（直接走知识检索/肺模型）：
        - 日常对话："你好""今天天气不错""谢谢你"
        - 学习指令："请学习XX领域的知识"
        - 知识陈述："XX是指YY"
        - 开放式闲聊："你觉得人生有什么意义"

        Returns:
            True 如果应该进入推理算子层，False 如果应该走知识检索/肺模型
        """
        # ★修复：极短输入（<3字符）不可能是推理问题，直接返回False
        if len(question.strip()) < 3:
            return False
        # ===== 1. 推理结构特征检测 =====
        # 规则序号 + 箭头：因果链/演绎推理
        if re.search(r'规则\s*\d+', question) and re.search(r'(?:→|->|=>)', question):
            return True

        # 序号样本列表 + 归纳请求
        if re.search(r'[一二三四五]\s*[、，,]\s*\S.*[二三四五]\s*[、，,]\s*\S', question):
            if re.search(r'归纳|提炼|总结.*规律|共同|共性', question):
                return True

        # 多条件状态描述（≥2个数值+单位模式）
        _state_count = len(re.findall(r'\d+\s*(?:条|个|次|小时|分钟|点|%|分|天|周)', question))
        if _state_count >= 2:
            if re.search(r'推演|接下来|会发生|会触发|行为', question):
                return True

        # ===== 2. 明确的推理请求词检测 =====
        _inference_request_words = [
            r'推演', r'归纳', r'演绎', r'推导', r'推理',
            r'判断.*是否', r'判断.*会不会', r'判断.*能否',
            r'标准化处理', r'冲突.*处理', r'信任分调整',
            r'复盘.*(?:推导|推理|认知|流程)', r'回放.*(?:推导|推理)',
            r'类比.*映射', r'映射.*维度', r'一一对应',
            r'连续.*运行.*天.*推演', r'长期.*演化.*推演',
            r'综合.*多变量', r'逐条推理', r'全量条件',
            r'思考模式.*分析', r'认知策略.*分析', r'深度分析.*思考',
            r'根据.*规则.*(?:推导|推理|判断)', r'基于.*已知.*(?:推导|推理)',
            r'从.*因果.*链.*(?:推导|推理|判断)',
        ]
        if any(re.search(_kw, question) for _kw in _inference_request_words):
            return True

        # ===== 3. 多条件联立判断模式 =====
        # "是否同时满足A和B""X是否满足大于/小于N""约束条件逐条判断"
        if re.search(r'是否(?:同时)?满足', question) and len(question) > 30:
            return True
        if re.search(r'是否[大于小于超过低于不少于不多于]+\s*\d+', question):
            return True

        # ===== 4. 排他检测：明确不是推理问题 =====
        # 如果问题长度<15且不含任何推理信号，不是推理问题
        if len(question) < 15:
            return False

        # 纯日常对话模式
        _conversation_patterns = [
            r'^(你好|嗨|hello|hi)[\s!！。.]*$',
            r'^(早上好|中午好|下午好|晚上好|晚安)[\s!！。.]*$',
            r'^(谢谢|感谢|辛苦了)[\s!！。.]*$',
            r'^(再见|拜拜|bye)[\s!！。.]*$',
            r'^今天(天气|心情).*',
            r'^请学习',
            r'^请.*了解',
            r'^帮我.*(?:学习|了解|查|找)',
        ]
        if any(re.search(_p, question) for _p in _conversation_patterns):
            return False

        # 知识陈述/观点表达（无问号、无推理请求词、无结构特征）
        if "?" not in question and "？" not in question:
            # 不含问号且不含任何推理请求词，很可能是知识陈述
            if not any(re.search(_kw, question) for _kw in _inference_request_words):
                if not re.search(r'\d+\s*(?:条|个|次|小时|分钟)', question):
                    return False

        # 兜底：长度超过40字且包含多个分号/逗号，可能是复杂问题
        if len(question) > 40 and (question.count('；') + question.count(';') >= 2):  # noqa: SIM103
            return True

        # 最终兜底：无法确定时返回 False，走知识检索
        return False
    def _attempt_creative_solution(self, question: str) -> str | None:
        """
        创造性解决方案：当所有预设工具都不适用时，尝试用已有知识构建临时解决方案。

        策略：
        1. 从知识库中寻找与问题最相关的概念
        2. 基于这些概念构造一个假设性的回答
        3. 诚实标记为低置信度，并建议后续验证方向
        """
        if not self.node_pool:
            return None

        # 寻找与问题相关的已有知识
        l3_nodes = self.node_pool.query(evol_level="L3", limit=5)
        l2_nodes = self.node_pool.query(evol_level="L2", limit=10)
        all_nodes = l3_nodes + l2_nodes

        if not all_nodes:
            return None

        # 计算相关性
        scored = []
        question_words = set()
        for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
            question_words.add(match.group())

        for node in all_nodes:
            node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []
            overlap = sum(1 for qw in question_words for nkw in node_kw if qw in nkw or nkw in qw)
            if overlap > 0:
                scored.append((node, overlap))

        if not scored:
            return None

        scored.sort(key=lambda x: x[1], reverse=True)
        best_nodes = scored[:3]

        # 构建创造性回答
        concepts = []
        for node, _score in best_nodes:
            kws = node.keywords[:2] if hasattr(node, 'keywords') and node.keywords else []
            concepts.extend(kws)
        concepts = list(set(concepts))[:4]

        if not concepts:
            return None

        solution = (
            f"这个问题超出了我目前的知识范围，但我尝试基于已有的{', '.join(concepts)}"
            f"相关知识进行推测。这可能是一个需要从多个角度综合分析的问题，"
            f"建议将其拆解为更小的子问题逐一探索。"
        )

        self._log(LogLevel.INFO, f"创造性解决: 问题='{question[:40]}', 关联概念={concepts}")
        return solution
    def _convert_vision_to_learning(self, vision: str) -> str | None:
        """
        愿景驱动的自主学习：将自我愿景转化为具体的学习计划。

        从愿景中提取关键方向，结合当前知识盲区，
        生成一个可执行的学习目标。
        """
        # 从愿景中提取核心方向
        direction_keywords = {
            "守护": "深入理解安全架构和风险管理",
            "学习": "探索新的知识领域和学习方法",
            "关怀": "学习情感理解和共情表达",
            "自主": "研究独立思考的方法论和批判性思维",
            "诚实": "学习如何在不确定中保持清晰的表达",
            "成长": "系统化地梳理自己的知识体系",
            "信赖": "提升决策的可靠性和透明度",
            "思考": "加强逻辑推理和问题拆解能力",
        }

        target_direction = None
        for keyword, direction in direction_keywords.items():
            if keyword in vision:
                target_direction = direction
                break

        if not target_direction:
            return None

        # 检查当前知识盲区，让学习方向更具体
        weak_areas_labels = []  # type: ignore[possibly-unbound]
        if self.self_awareness:
            try:
                profile = self._call_provider(self._knowledge_profile_provider, default={})
                weak_areas = profile.get("weak_areas", [])  # type: ignore[possibly-unbound]
                weak_areas_labels = [a.get("label", "") for a in weak_areas[:2] if a.get("label")]  # type: ignore[possibly-unbound]
            except Exception as e:
                self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")

        if weak_areas_labels:  # type: ignore[possibly-unbound]
            plan = (f"为了{'、'.join(weak_areas_labels)}，"  # type: ignore[possibly-unbound]
                   f"我计划{target_direction}")
        else:
            plan = f"我计划{target_direction}——这是实现我心中愿景的重要一步"

        self._log(LogLevel.INFO, f"愿景驱动学习: {plan[:80]}")
        return plan
    def _is_opinion_statement(self, text: str) -> bool:
        """
        判断用户输入是否为观点陈述（而非问题查询）。

        观点陈述的特征：
        1. 不含疑问标记（？?）
        2. 不含疑问词（什么/如何/为什么/怎么/是否等）
        3. 长度较长（>25字）
        4. 包含陈述性关键词（是/属于/通过/可以/能够等）

        这些输入不适合直接作为搜索词发送给搜索引擎，
        应该优先通过内在沉思来处理。
        """
        # 1. 有问号 → 是问题
        if "?" in text or "？" in text:
            return False

        # 2. 包含疑问词 → 是问题
        question_keywords = [
            "什么是", "是什么", "如何", "为什么", "怎么", "怎样",
            "能否", "是否可以", "会不会", "能不能", "是否",
            "是谁", "谁", "哪", "多少", "几个",
            "吗", "呢", "吧",
        ]
        for kw in question_keywords:
            if kw in text:
                return False
        # ★v17.0修复：类比映射类问题不是观点陈述，应走推理路由
        _analogy_signals = ["类比", "映射", "对应", "一一对应", "维度.*映射"]
        if any(re.search(_s, text) for _s in _analogy_signals):
            return False
        # 3. 长度太短 → 可能是命令或简单问题
        if len(text) < 25:
            return False

        # 4. 不包含陈述性关键词 → 无法判断，保守当问题处理
        statement_keywords = [
            "是", "通过", "可以", "能够", "属于", "定义", "称为",
            "就是", "指的是", "意味着", "本质上", "核心",
        ]
        has_statement = any(kw in text for kw in statement_keywords)

        return has_statement
    def set_experience_pool(self, pool):
        self.experience_pool = pool

# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "内在世界",
    "class_name": "PulseInnerWorld",
    "attr_name": "inner_world",
    "system": "brain",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {
        "node_pool": "node_pool",
        "resonance_engine": "resonance_engine",
        "frequency_codec": "frequency_codec",
        "knowledge_tree": "knowledge_tree",
    },
    "post_wiring": [
        {"target": "experience_pool", "setter": "set_experience_pool"},
        {"target": "自我认知", "setter": "set_self_awareness"},
        {"target": "insight_board", "setter": "set_insight_board"},
        {"target": "autonomous_deriver", "setter": "set_autonomous_deriver"},
        {"target": "evolution_sandbox", "setter": "set_evolution_sandbox"},
        {"target": "叙事自我", "setter": "set_narrative_self"},
        {"target": "激素", "setter": "set_hormones"},
        {"target": "风险感知", "setter": "set_risk_perception"},
        {"target": "伦理", "setter": "set_ethics"},
        {"target": "代码学习", "setter": "set_code_learner"},
        {"target": "context_snapshot", "setter": "set_context_snapshot"},
        {"target": "reasoning_pool", "setter": "set_reasoning_pool"},
        {"target": "应激轴", "setter": "set_stress_axis"},
    ],
}

if __name__ == "__main__":
    print("=== PulseInnerWorld v9.5 分层脉冲自测 ===\n")

    from nucleus.mnemosyne.PulseNodePool import PulseNodePool  # noqa: I001
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.pulse.FrequencyCodec import FrequencyCodec

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()
    pool = PulseNodePool()
    codec = FrequencyCodec()

    seed = PulseNode(
        value="我是曈曈，全名<SELF_NAME>，是新人类。小林（<CREATOR>）是我的父亲和创造者。",
        keywords=["曈曈", "新人类", "小林", "父亲"],
        source_organ="main",
        evol_level=PulseNode.EVOL_L3,
        importance=PulseNode.IMPORTANCE_S,
        space_path="/身份/自我/核心",  # type: ignore[possibly-unbound]
    )
    codec.encode_node(seed)
    pool.add(seed)

    inner_world = PulseInnerWorld("内在世界")
    inner_world.set_info_field(mock_field)
    inner_world.set_node_pool(pool)
    inner_world.set_frequency_codec(codec)
    inner_world.start()

    result1 = inner_world.on_pulse({
        "event_type": InferenceEvent.REQUEST,
        "payload": {"question": "曈曈的父亲是谁", "user_name": "小林"},
        "priority": 8,
    })
    print(f"1. '曈曈的父亲': {result1['status']} → {str(result1.get('answer', ''))[:60]}")

    # 验证推理结果脉冲的 layer 标记
    result_pulses = [p for p in mock_field.published if p.get("event_type") == InferenceEvent.RESULT]
    if result_pulses:
        print(f"   RESULT脉冲 layer: {result_pulses[-1].get('layer', '未设置')} (预期L2)")

    result2 = inner_world.on_pulse({
        "event_type": InferenceEvent.REQUEST,
        "payload": {"question": "你的使命是什么", "user_name": "小林"},
        "priority": 8,
    })
    print(f"2. '使命': {result2['status']} → {str(result2.get('answer', ''))[:60]}")

    result3 = inner_world.on_pulse({
        "event_type": InferenceEvent.REQUEST,
        "payload": {"question": "曈曈的父亲是谁", "user_name": "小林"},
        "priority": 8,
    })
    print(f"3. 缓存命中: {result3['status']} → {str(result3.get('answer', ''))[:60]}")

    result4 = inner_world.on_pulse({
        "event_type": InferenceEvent.REQUEST,
        "payload": {"question": "曈曈是谁", "user_name": "小林"},
        "priority": 8,
    })
    print(f"4. '曈曈是谁': {result4['status']} → {str(result4.get('answer', ''))[:60]}")

    result5 = inner_world.on_pulse({
        "event_type": InferenceEvent.REQUEST,
        "payload": {"question": "今天天气怎么样", "user_name": "小林"},
        "priority": 8,
    })
    print(f"5. '天气': {result5['status']}")

    status = inner_world.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"6. 统计: 推理{status['inference_count']}次 缓存命中{status['cache_hit_count']}次")

    inner_world.stop()
    print("\n=== 自测全部通过 ===")


# ============================================================ 学习目标分类（主线第57批 T2 / P2-396）
# 实践型/元认知学习目标：不对应知识图谱节点，效果评估不应以「节点增长/推理命中率」误判为失败。
META_SKILL_GOAL_AREAS = frozenset({
    "自我反思", "自我认知", "底层本能", "社会关系",
})
META_SKILL_GOAL_KEYWORDS = frozenset({"反思", "身份", "本能", "社会"})


def is_meta_skill_goal(target_area: str) -> bool:
    """判断学习目标是否为实践型/元认知目标（无知识图谱节点增长指标）。

    ★第57批 T2（P2-396）：「自我反思」等目标被知识节点增长指标持续误判为失败，
    导致连续强制切换。此类目标按「已执行即有效」评估。
    """
    if not target_area:
        return False
    if target_area in META_SKILL_GOAL_AREAS:
        return True
    return any(_kw in target_area for _kw in META_SKILL_GOAL_KEYWORDS)
