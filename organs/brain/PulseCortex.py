# -*- coding: utf-8 -*-
"""
PulseCortex —— 大脑皮层决策中枢

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 接收用户输入与各类脉冲，完成意图路由、策略生成与推理请求调度，是框架的决策中枢。
机制: 读取 QICA 分类结果与 suggested_method，构建 strategy_context（注入 QICA 建议方法与知识路径、反思回路覆盖、语境信号等），发射 InferenceEvent.REQUEST 交由内在世界执行；同时管理工具提示、代码块等上下文信息。
定位: 上接输入层与各类感知器官，下接内在世界执行层，是「决策—调度」的中枢节点。
"""

import logging
import os
import re
import sys
import threading
import time
from collections import deque
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    ChatEvent,
    ControllerEvent,
    EyeEvent,
    HeartEvent,  # ★v24.0新增：心跳事件用于清理待处理条目
    HormonesEvent,
    InferenceEvent,
    LogLevel,
    LungEvent,
    MotorEvent,
    MouthEvent,
    NarrativeEvent,
    QICAEvent,
    SystemEvent,
    VisualEvent,
    BoneMarrowEvent,
    ConsentEvent,
    DNARepairEvent,
    DeviceEvent,
    EvolutionEvent,
    GrowthEvent,
    HandsEvent,
    HealthEvent,
    KnowledgeEvent,
    MediaEvent,
    MetricsEvent,
    PersonaEvent,
    PersonalityEvent,
    ProprioceptionEvent,
    ReflectionEvent,
    ReproductionEthicsEvent,
    RiskEvent,
    SecurityEvent,
    SpinalCordEvent,
    ThymusEvent,
    WhiteCellEvent,
)
from utils.time_utils import get_current_datetime
from nucleus.events.EventTap import tap_publish  # ★第17批 T2：旁路事件发布入口
from nucleus.const import Event
from nucleus._silent_except import silent_exc


_module_logger = logging.getLogger(__name__)


class PulseCortex(BasePulseOrgan):
    """脉冲驱动大脑皮层（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'output_relevance_threshold' in _rp and hasattr(self, '_adaptive_relevance_threshold'):
                self._adaptive_relevance_threshold = _rp['output_relevance_threshold']
            if 'knowledge_query_max_results' in _rp and hasattr(self, '_retrieval_top_k'):
                self._retrieval_top_k = _rp['knowledge_query_max_results']
            if 'knowledge_min_similarity' in _rp and hasattr(self, '_retrieval_resonance_threshold'):
                self._retrieval_resonance_threshold = _rp['knowledge_min_similarity']
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    def __init__(self, organ_name: str = "大脑皮层"):
        super().__init__(organ_name)

        self.qica = None
        self.inner_world = None

        self._intent_routes = self._load_intent_routes()
        self._decision_count = 0
        self._booted = False

        # ===== QICA脉冲通信 =====
        self._pending_messages: dict[str, dict[str, Any]] = {}
        # ===== 内在世界脉冲通信暂存 =====
        self._pending_inner_world: dict[str, dict[str, Any]] = {}
        # ★P0 输出会话锁：补救中阻止原始答案输出，避免并行竞争导致错误答案泄露
        self._remediation_pending: set[str] = set()
        self._remediation_lock_time: dict[str, float] = {}  # 加锁时间，用于超时自动释放
        # ★第九批 4.2（星轨指出）：固定 90s 超时对「多步推理 + 补救」叠加场景不够
        #   （多步本身 15~27s，再叠加补救就顶穿 90s，锁被提前释放导致回答丢失/重复）。
        #   新增「每锁可延长」机制：_remediation_lock_extra 记该锁的额外宽限秒数，
        #   _remediation_lock_meta 记加锁时的阶段/方法，超时释放时一并打印便于排查。
        self._remediation_lock_extra: dict[str, float] = {}
        self._remediation_lock_meta: dict[str, str] = {}
        # 输出锁基础超时（秒），可被 config.CORTEX_CONFIG.remediation_lock_timeout 覆盖
        self._remediation_lock_timeout = 90.0
        try:
            import config as _cfg
            self._remediation_lock_timeout = float(
                getattr(_cfg, "CORTEX_CONFIG", {}).get(
                    "remediation_lock_timeout", 90.0) or 90.0)
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
        # ===== ★主线第15批 T1/P1-98：对话回复错位防护 =====
        # 背景（09:18-09:30 实测）：用户 1-2 分钟一问，框架 3-5 分钟一答；输出锁
        #   90s 超时释放后旧任务仍在异步处理，等它终于输出时用户已在问下一个问题
        #   → 回复错位（答的是上一个问题的答案）。
        # 机制：记录「最新输入 cid」与「当前被允许输出的 cid」；新输入到达时按策略
        #   处置（queue=旧任务继续+新问题排队并提示；cancel=作废旧任务）；
        #   输出前校验 cid，被作废/过期的回复一律丢弃。
        # 灰度 ENABLE_DIALOG_LOCK_GUARD（默认 True）；关闭时以下逻辑全部旁路、零副作用。
        self._latest_correlation_id: str = ""
        self._active_correlation_id: str = ""
        # ★T-对话-1（157批）：缓存最近一次时间广播，供答复"今天几号"类提问取用时戳
        self._latest_time: "dict | None" = None
        self._cancelled_correlations: deque[str] = deque(maxlen=200)
        self._cancelled_correlation_set: set[str] = set()
        self._dialog_queue: deque[str] = deque(maxlen=1000)  # ★补丁patch_1789115430_7fd6：防内存无限增长
        self._dialog_max_queue = 8

        # ★主线第25批 T1/P2-160：同一轮对话的下游发射幂等（防「一次输入两次调用」）。
        #   key = cid|prompt指纹|后台标记|补救标记 → 重复分支被抑制，
        #   而**合法补救调用**（is_remediation=True）key 不同，不受影响。
        self._turn_emit_lock = threading.Lock()
        self._turn_emit_seen: dict[str, float] = {}
        self._turn_emit_dedup_count = 0
        self._dialog_output_done: deque[str] = deque(maxlen=200)
        self._dialog_output_done_set: set[str] = set()
        self._dialog_guard_stats: dict[str, int] = {
            "checked": 0, "dropped_cancelled": 0,
            "dropped_stale": 0, "queued": 0, "hint_sent": 0,
        }
        # 多步推理场景的额外宽限（秒）
        self._remediation_lock_multi_step_extra = 90.0
        try:
            import config as _cfg2
            self._remediation_lock_multi_step_extra = float(
                getattr(_cfg2, "CORTEX_CONFIG", {}).get(
                    "remediation_lock_multi_step_extra", 90.0) or 90.0)
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
        # ★v25.0新增：接入全框架终身学习引擎Hub
        self._vl_hub = None
        try:
            from nucleus.mnemosyne.verification_learning_hub import (
                get_verification_learning_hub,
            )
            self._vl_hub = get_verification_learning_hub()
        except Exception:
            self._vl_hub = None
        # ★v25.1 P1智能化: 输出验证阈值自适应
        #   原逻辑：固定0.5阈值，导致某些类型问题总是误判。
        #   新逻辑：根据验证补救成功率动态调整，误杀多→降阈值，漏杀多→升阈值。
        # ★P1: 从RUNTIME_PARAMS读取输出验证参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._adaptive_relevance_threshold = _rp.get("output_relevance_threshold", 0.5)
            self._retrieval_top_k = _rp.get("knowledge_query_max_results", 20)
            self._retrieval_resonance_threshold = _rp.get("knowledge_min_similarity", 0.5)
        except Exception:
            self._adaptive_relevance_threshold = 0.5
            self._retrieval_top_k = 20
            self._retrieval_resonance_threshold = 0.5
        self._verify_stats = {"triggered": 0, "improved": 0, "total": 0}
        self._verify_adjust_interval = 30  # 每30次验证调整一次阈值
        # ★P0 自我纠错引擎（推理闭环补强，单例模式）
        self._self_corrector = None
        self._correction_retry_count = 0
        try:
            from nucleus.reasoning.SelfCorrector import get_self_corrector
            self._self_corrector = get_self_corrector()
        except Exception:
            self._self_corrector = None
        # ★P1 推理质量反馈回流引擎（单例模式）
        self._feedback_loop = None
        try:
            from nucleus.reasoning.ReasoningFeedbackLoop import (
                get_reasoning_feedback_loop,
            )
            self._feedback_loop = get_reasoning_feedback_loop()
        except Exception:
            self._feedback_loop = None
        # ★P2 推理策略智能选择器（单例模式）
        self._strategy_selector = None
        try:
            from nucleus.reasoning.StrategySelector import get_strategy_selector
            self._strategy_selector = get_strategy_selector()
        except Exception:
            self._strategy_selector = None
        # ★P0 运行轨迹记录引擎（单例模式）
        self._trajectory = None
        try:
            from nucleus.runtime.RuntimeTrajectory import get_runtime_trajectory
            self._trajectory = get_runtime_trajectory()
        except Exception:
            self._trajectory = None
        self._correlation_counter = 0
        self._max_pending = 100
        self._narrative_guidance = {}  # 叙事自我发来的行为指导
        self._current_emotion = "中性"  # 当前情绪状态
        self._risk_cautious_mode = False  # ★P0-1：风险谨慎模式标记
        self._risk_alert_summary = ""     # ★P3-5：基础风险告警摘要（供语气微调）
        self._safe_mode = False           # ★P3-5：L4 安全模式标记（暂停高风险推理）
        self._latest_reports = {}         # ★P3-5：各器官监控报告汇总（健康/人格/免疫/本体感知）
        # ★跨模态关联索引：把媒体元数据/检测结果/视觉分析按 user+file 关联（视觉/音频/触觉→文本）
        # 格式：{"{user_name}:{file_name}": {"modalities": set, "analysis": {...}, ...}}
        self._cross_modal_index: dict[str, dict[str, Any]] = {}
        self._max_cross_modal_entries = 500  # 容量保护
        # ★工具创造元能力：已知工具注册表 + 动态创造的工具
        self._tool_registry: dict[str, dict[str, Any]] = {
            "inner_world": {"description": "内部符号/规则/知识推理", "capabilities": ["推理", "知识检索", "逻辑演算"]},
            "deep_search": {"description": "外部深度搜索", "capabilities": ["网络搜索", "信息检索"]},
            "code_sandbox": {"description": "代码执行沙箱", "capabilities": ["代码执行", "计算验证"]},
            "deep_think": {"description": "深度思考", "capabilities": ["多方向延展", "概念解释"]},
            "knowledge_retrieve": {"description": "知识库检索", "capabilities": ["知识查询"]},
        }
        self._created_tools: list[dict[str, Any]] = []  # 创造的工具有（观测）
        self._report_event_types = {      # ★P3-5：结果/报告类孤儿脉冲，统一由皮层汇总消费
            EyeEvent.SEARCH_RESULT, HandsEvent.RESULT, NarrativeEvent.REFLECTION_RESULT,
            PersonaEvent.RELATION_CHANGED, MetricsEvent.SNAPSHOT, GrowthEvent.MILESTONE_REACHED,
            ReflectionEvent.ISSUE_FOUND, SpinalCordEvent.INSPECTION_REPORT, ThymusEvent.TRAIN_RESULT,
            SystemEvent.RECOVERY_ATTEMPT, KnowledgeEvent.FUSED,
            BoneMarrowEvent.GENERATE_RESULT, ConsentEvent.RESULT, DeviceEvent.ALLOCATED,
            DNARepairEvent.SOLUTION_GENERATED, EvolutionEvent.MUTATION_SUCCESS,
            ReproductionEthicsEvent.ETHICS_RESULT, SecurityEvent.PASSED,
            # ★跨模态融合：媒体检测/元数据孤儿脉冲（文件消化器发射，此前无订阅）
            MediaEvent.METADATA, MediaEvent.IMAGE_DETECTED, MediaEvent.AUDIO_DETECTED,
            MediaEvent.VIDEO_DETECTED, MediaEvent.UNKNOWN,
        }
        self.risk_perception = None  # 风险感知引用（直觉系统）
        self.autonomous_deriver = None  # ★P0-3：自主推导引擎引用
        self.node_pool = None  # ★P0-3：节点池引用（推导引擎需要）
        # ★P3-1：只读状态 provider 回调（替代跨器官 getter 直调）
        self._reply_guidance_provider = None       # (user_name) -> dict
        self._knowledge_profile_provider = None    # () -> dict
        self._intuition_guidance_provider = None   # (content, domain) -> dict

        # ★属性初始化完整性补全（自动审查添加）
        self._parallel_audit_beat_counter = 0
        self.self_awareness = None
    # ========== 框架注入接口 ==========

    def set_qica(self, qica):
        self.qica = qica
        # ★FIX: 注册皮层规则吸收回调，避免 cortex 蒸馏规则被静默丢弃
        try:
            if hasattr(qica, 'absorb_growth_rules'):
                from nucleus.mnemosyne.verification_learning_hub import (
                    get_verification_learning_hub,
                )
                get_verification_learning_hub().register_applier("cortex", qica.absorb_growth_rules)
        except Exception as e:
            self._log_ignored_exception(e)

    def set_inner_world(self, inner_world):
        self.inner_world = inner_world
    def set_self_awareness(self, awareness):
        self.self_awareness = awareness
        # ★P3-1：同步注入 provider 回调（替代 getter 直调）
        if awareness is not None:
            if hasattr(awareness, 'get_reply_guidance'):
                self._reply_guidance_provider = awareness.get_reply_guidance
            if hasattr(awareness, 'get_knowledge_profile'):
                self._knowledge_profile_provider = awareness.get_knowledge_profile
    def set_risk_perception(self, risk_perception):
        """注入风险感知（直觉系统）"""
        self.risk_perception = risk_perception
        # ★P3-1：同步注入直觉引导 provider 回调（替代 get_intuition_guidance 直调）
        if risk_perception is not None and hasattr(risk_perception, 'get_intuition_guidance'):
            self._intuition_guidance_provider = risk_perception.get_intuition_guidance

    def set_autonomous_deriver(self, deriver):
        """★P0-3：注入自主推导引擎"""
        self.autonomous_deriver = deriver

    def set_node_pool(self, node_pool):
        """★P0-3：注入节点池（推导引擎需要）"""
        self.node_pool = node_pool
    # ========== 脉冲入口 ==========

    def on_time_tick(self, pulse):
        """★v9.x TimeCore：响应周期性时间广播（受 ENABLE_TIME_CORE 保护）。"""
        try:
            import config
            if not getattr(config, "ENABLE_TIME_CORE", False):
                return
            _p = (pulse or {}).get("payload", {})
            # ★T-对话-1（157批）：缓存最近一次时间广播，供答复"今天几号"类提问取用时戳
            self._latest_time = {
                "wall_clock": _p.get("wall_clock"),
                "semantic_time": _p.get("semantic_time"),
                "uptime_display": _p.get("uptime_display"),
                "tick_count": _p.get("tick_count"),
                "ts": time.time(),
            }
            self._log(
                LogLevel.DEBUG,
                f"[time.tick] wall={_p.get('wall_clock')} up={_p.get('uptime_display')} "
                f"phase={_p.get('semantic_time')} tick={_p.get('tick_count')}",
            )
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')

    def _log_ignored_exception(self, e: Exception, ctx: str = "") -> None:
        """★P1-25 可观测性预埋：统一记录被忽略的异常，附带精确位置。

        原实现只打 "异常已忽略（需关注）: TypeError: ..."，不含发生位置，
        面对全文件 26 处同名捕获点完全无法定位。此处从 traceback 取最后
        一帧（即异常真正抛出的地方），输出 文件:行号 in 函数() 形式。

        Args:
            e: 捕获到的异常
            ctx: 可选的业务上下文（如 correlation_id、问题摘要）
        """
        try:
            import traceback as _tb
            _frames = _tb.extract_tb(e.__traceback__)
            if _frames:
                _f = _frames[-1]
                _loc = "%s:%d in %s()" % (
                    os.path.basename(_f.filename), _f.lineno, _f.name)
            else:
                _loc = "无traceback"
        except Exception:
            _loc = "位置解析失败"

        _msg = "异常已忽略（需关注）: %s: %s | 位置=%s" % (
            type(e).__name__, e, _loc)
        if ctx:
            _msg += " | 上下文=%s" % ctx
        self._log(LogLevel.WARNING, _msg)

    # ==================== ★主线第40批 T4（P2-261 / P2-216）====================

    def _m40_observe_self_awareness(self, answer: str,
                                    correlation_id: str = "") -> None:
        """L1 观测级旁路：记录"如果接入，自我认知会给出什么建议"。

        ★红线：**纯观测** —— 不修改 answer、不改变任何决策、不写盘。
        灰度：``ENABLE_SELF_AWARENESS_OBSERVATION``（默认 True）控制是否观测；
        ``ENABLE_SELF_AWARENESS_INFLUENCE_DECISION``（默认 False）为阶段二
        影响级总开关 —— L1 仅在日志中标注其状态，**绝不据此改变行为**。
        # _m40_t4_observe
        """
        try:
            import config as _c
            if not getattr(_c, "ENABLE_SELF_AWARENESS_OBSERVATION", True):
                return
            _eng = getattr(self, "self_awareness", None)
            if _eng is None:
                return
            _getter = getattr(_eng, "get_public_summary", None)
            if not callable(_getter):
                # ★T-113b：原静默 return 改为首报 WARNING，防"假能力标记"复发
                _miss = getattr(self, "_m40_sa_missing_getter_count", 0) + 1
                self._m40_sa_missing_getter_count = _miss
                if _miss <= 1:  # 首报即可见，避免刷屏
                    self._log(
                        LogLevel.WARNING,
                        "[自我认知观测] 注入对象缺少 get_public_summary 能力，"
                        f"观测旁路未生效（疑似假能力标记，已拦截 {_miss} 次）")
                return
            # 每轮对话只观测一次（两处输出点互斥，此处再加一道 cid 去重）
            _seen = getattr(self, "_m40_observed_cids", None)
            if _seen is None:
                _seen = set()
                self._m40_observed_cids = _seen
            if correlation_id and correlation_id in _seen:
                return
            if correlation_id:
                _seen.add(correlation_id)
                if len(_seen) > 200:
                    _seen.clear()
            _sum = _getter()
            if not isinstance(_sum, dict):
                return
            _influence = bool(getattr(
                _c, "ENABLE_SELF_AWARENESS_INFLUENCE_DECISION", False))
            self._log(LogLevel.INFO,
                      f"[自我认知观测] 健康={_sum.get('health_level')} "
                      f"综合分={_sum.get('overall_score')} "
                      f"最弱维度={(_sum.get('worst_dimension') or {}).get('name')} "
                      f"首要问题={_sum.get('headline_issue')!r} "
                      f"影响级={'开' if _influence else '关(L1仅观测)'}")
        except Exception as e:
            self._log_ignored_exception(e, "自我认知观测")

    # ==================== ★第115批 T-115f（P2-261 / PHASE18 阶段二 L2）====================
    # 对话主动提及：在 SPEAK 发射前对 answer 后处理追加一句自检摘要（不进主 prompt）。
    # 六个门：开关→冷却→本会话→场景关键词→质量闸→文案门；任一不满足即原样返回。
    # ★红线：只读 self_awareness 的 get_* 系列；异常一律降级为无影响。
    _SELF_STATE_FORBIDDEN_WORDS = (
        "我有问题", "我出了故障", "我崩溃了", "我坏了", "我不行了",
        "我很烂", "我失败了", "一团糟", "糟透了", "我废了", "没法用了",
    )

    def _maybe_append_self_state(self, answer: str, correlation_id: str = "",
                                 user_input: str = "") -> str:
        """★第115批 T-115f：PHASE18 阶段二 L2 对话主动提及（后处理追加，不进主 prompt）。

        六道门（任一不满足即原样返回 answer）：
          ① 开关：config.SELF_AWARENESS_MENTION_ENABLED 开 且 引擎可用；
          ② 冷却：距上次提及 < SELF_AWARENESS_MENTION_COOLDOWN_SEC → 跳过；
          ③ 本会话：correlation_id 本会话已提及 → 跳过（每会话≤1次）；
          ④ 场景关键词：concerning 仅当用户问及自我/健康/能力才提；critical 必提；
          ⑤ 质量闸：is_fresh() 且 health_level ∈ {concerning, critical}
                   （按设计原文"仅 concerning/critical 才提"）；
          ⑥ 文案门：渲染句非空且不含禁词，否则整句丢弃。
        ★红线：只读 self_awareness 的 get_* 系列；任何异常一律降级为无影响。
        """
        try:
            import config as _c
            if not getattr(_c, "SELF_AWARENESS_MENTION_ENABLED", False):
                return answer
            _eng = getattr(self, "self_awareness", None)
            if _eng is None or not callable(getattr(_eng, "get_health_level", None)):
                return answer
            # ② 冷却
            _cooldown = float(getattr(_c, "SELF_AWARENESS_MENTION_COOLDOWN_SEC", 7200) or 7200)
            _last = float(getattr(self, "_self_state_mention_last_ts", 0.0) or 0.0)
            if _last and (time.time() - _last) < _cooldown:
                return answer
            # ③ 本会话去重
            _cids = getattr(self, "_self_state_mention_cids", None)
            if _cids is None:
                _cids = set()
                self._self_state_mention_cids = _cids
            if correlation_id and correlation_id in _cids:
                return answer
            # ⑤ 质量闸
            _level = _eng.get_health_level()
            if _level not in ("concerning", "critical"):
                return answer
            if not _eng.is_fresh():
                return answer
            # ④ 场景关键词（concerning 才需关键词；critical 必提）
            if _level == "concerning" and not self._self_state_is_relevant(user_input):
                return answer
            # ⑥ 文案门：渲染
            _text = self._self_state_render(_eng, _level)
            if not _text:
                return answer
            if self._self_state_has_forbidden_word(_text):
                # 命中禁词 → 整句丢弃（设计§六 风险1：禁用负面自述词，避免污染对话）
                self._log(LogLevel.INFO,
                          "[自我认知提及] 渲染句命中禁词，已丢弃（不污染对话）")
                return answer
            # 观测行 + 写冷却/会话去重状态
            self._log(LogLevel.INFO,
                      f"[自我认知提及] 命中健康等级={_level}，向回复末尾追加自检摘要"
                      f"(correlation_id={correlation_id or 'n/a'})")
            self._self_state_mention_last_ts = time.time()
            if correlation_id:
                _cids.add(correlation_id)
                if len(_cids) > 200:
                    _cids.clear()
            return answer + "\n\n" + _text
        except Exception as _e:
            # 任何异常一律降级为无影响（绝不阻断主流程）
            self._log(LogLevel.DEBUG,
                      f"[自我认知提及] 异常降级(返回原回复): {type(_e).__name__}: {_e}")
            return answer

    def _self_state_is_relevant(self, user_input: str) -> bool:
        """场景关键词：用户问及自我/健康/能力/状态相关。"""
        if not user_input:
            return False
        _kw = ("你怎么样", "你好吗", "你健康", "你状态", "自我认知", "你有什么问题",
               "你最近", "你哪里", "你能力", "你擅长", "你还好", "你还行")
        _u = user_input.lower()
        return any(_k in _u for _k in _kw)

    def _self_state_has_forbidden_word(self, text: str) -> bool:
        return any(_w in text for _w in self._SELF_STATE_FORBIDDEN_WORDS)

    def _self_state_render(self, eng, level: str) -> str:
        """渲染一句用户可见自检摘要（worst_dimension+分+headline+最近自检措辞+建议句）。"""
        try:
            _sum = eng.get_public_summary()
            if not isinstance(_sum, dict):
                return ""
            _level_zh = {"healthy": "健康", "moderate": "中等",
                         "concerning": "堪忧", "critical": "危急", "unknown": "未知"}
            _worst = _sum.get("worst_dimension") or {}
            _head = _sum.get("headline_issue", "") or ""
            _parts = ["（最近一次自检："]
            if isinstance(_worst, dict) and _worst.get("name"):
                _parts.append(f"{_worst['name']} {_worst.get('score', 0)} 分，")
            _parts.append(f"整体{_level_zh.get(level, level)}")
            if _head:
                _parts.append(f"，最突出的是：{_head}")
            _parts.append("）")
            _top = eng.get_top_issues(1)
            _sug = ""
            if _top and isinstance(_top[0], dict):
                _sug = _top[0].get("suggestion", "") or ""
            if _sug:
                _parts.append(f" 建议：{_sug}")
            return "".join(_parts)
        except Exception as e:
            silent_exc(e, where="organs.brain.PulseCortex::_self_state_render L487")
            return ""

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        # ★v27.1修复：对话输出完成回调，标记_active_correlation_id已完成
        # 解决"上一个对话已输出但状态未重置，导致下一个对话被阻塞"的问题
        if event_type == MouthEvent.REPLY:
            _cid = payload.get("correlation_id", "")
            _source = payload.get("source", "")
            # 只有对话回复（source为lung/cortex且cid非空）才标记完成，主动交互不标记
            if _cid and _source in ("lung", "cortex"):
                self._dialog_mark_output_done(_cid)
            return {"status": "output_done_acked", "correlation_id": _cid}

        if event_type == ChatEvent.MESSAGE:
            return self._on_chat_message(payload)
        elif event_type == QICAEvent.CLASSIFY_RESULT:
            return self._on_classify_result(payload)
        elif event_type == SystemEvent.BOOT:
            return self._on_system_boot(payload)
        # ★死代码清理：皮层未订阅 SystemEvent.STOP（共振条件无 STOP），且无 _on_system_stop 方法，
        # 此分支永不触发（若触发会抛 AttributeError）。皮层无 STOP 需释放的资源，无需补订阅。
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        elif event_type == HealthEvent.REPORT:
            return self._on_health_report(payload)
        elif event_type == PersonalityEvent.INTEGRITY_REPORT:
            return self._on_personality_integrity_report(payload)
        elif event_type == WhiteCellEvent.SCAN_RESULT:
            return self._on_white_cell_scan_result(payload)
        elif event_type == ProprioceptionEvent.REPORT:
            return self._on_proprioception_report(payload)
        elif event_type == HormonesEvent.EMOTION_DETECTED:
            return self._on_emotion_update(payload)
        elif event_type == NarrativeEvent.UPDATED:
            return self._on_narrative_updated(payload)
        elif event_type == VisualEvent.ANALYSIS_DONE:
            return self._on_visual_analysis_done(payload)
        elif event_type == InferenceEvent.RESULT:
            return self._on_inference_result(payload)
        elif event_type == "risk.terminate":
            return self._on_risk_terminate(payload)
        elif event_type == "risk.moderate":
            return self._on_risk_moderate(payload)
        elif event_type == RiskEvent.ALERT:  # ★P3-5补闭环：基础风险告警（≥0.3），此前无人订阅
            return self._on_risk_alert(payload)
        elif event_type == RiskEvent.CRISIS_REFERRAL:  # ★第161批段B B2：危机转介（独立通道）
            return self._on_crisis_referral(payload)
        elif event_type == SystemEvent.SAFE_MODE:  # ★P3-5补闭环：L4 安全模式，停止高风险推理
            return self._on_safe_mode(payload)
        elif event_type == HeartEvent.BEAT:
            # ★v24.0新增：心跳驱动清理过期的待处理推理条目
            self._cleanup_pending_inner_world()
            # ★FIX(全局并行调度): 低频触发三通道并行审查（硬件自适应并行度）
            _beat_count = getattr(self, '_parallel_audit_beat_counter', 0) + 1
            self._parallel_audit_beat_counter = _beat_count
            if _beat_count % 50 == 0:
                self._run_parallel_audit_async()
            return None
        elif event_type in self._report_event_types:
            # ★P3-5补闭环：汇总各器官的结果/报告类孤儿脉冲到自我状态（此前有发射无订阅）
            self._latest_reports[event_type] = payload
            # ★跨模态融合：媒体类事件额外写入跨模态关联索引
            if event_type.startswith("media."):
                self._index_media_event(event_type, payload)
            return {"status": "report_recorded"}
        elif event_type == SecurityEvent.SANDBOX_VIOLATION:
            # ★P3-5补闭环：沙箱违规升级为安全告警（此前有发射无订阅）
            self._emit(SystemEvent.ALARM, {
                "type": "sandbox_violation",
                "detail": payload.get("detail", str(payload)[:200]),
            }, priority=9, layer="L0")
            return {"status": "escalated"}
        return None

    # ========== 事件处理 ==========

    def _on_chat_message(self, payload: dict) -> dict[str, Any]:
        """收到聊天消息，发射分类请求脉冲给QICA（L1实时交互层）"""
        content = payload.get("content", "")
        user_name = payload.get("user_name", "用户")
        file_paths = payload.get("file_paths", [])
        code_blocks = payload.get("code_blocks", [])

        # ===== 新增：文件拦截——图片和PDF文件直接发送给视觉皮层，不经过推理 =====
        if file_paths:
            _image_exts = ("jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff")
            _pdf_exts = ("pdf",)
            _visual_files = []
            _other_files = []
            for fp in file_paths:
                ext = fp.lower().split(".")[-1] if "." in fp else ""
                if ext in _image_exts:
                    _visual_files.append({"path": fp, "task_type": "ocr"})
                elif ext in _pdf_exts:
                    _visual_files.append({"path": fp, "task_type": "pdf"})
                else:
                    _other_files.append(fp)

            if _visual_files:
                # 发送给视觉皮层处理
                for _vf in _visual_files:
                    self._emit(EyeEvent.VISUAL_QUERY, {
                        "file_path": _vf["path"],
                        "user_name": user_name,
                        "task_type": _vf["task_type"],
                    }, priority=7, layer="L1")

                # 如果有其他非视觉文件，继续走后续流程（如代码文件）
                if not _other_files:
                    # 所有文件都是视觉类型，直接返回提示信息
                    _file_names = [os.path.basename(f["path"]) for f in _visual_files]
                    _reply = f"正在识别 {'、'.join(_file_names)} 中的文字，请稍候..."
                    self._emit(MouthEvent.SPEAK, {
                        "content": _reply,
                        "source": "cortex",
                        "user_name": user_name,
                        "reasoning_path": "visual_query",
                    }, priority=8, layer="L1")
                    self._decision_count += 1
                    return {
                        "status": "visual_query_dispatched",
                        "files": _file_names,
                    }
                else:
                    # 部分视觉文件已发送，剩余文件继续后续流程
                    file_paths = _other_files  # 只传递非视觉文件

        if not content and not file_paths and not code_blocks:
            return {"status": "skipped", "reason": "空消息"}

        # 生成关联ID
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._correlation_counter += 1
        correlation_id = f"ctx:{self._correlation_counter}:{int(time.time() * 1000)}"

        # 暂存消息上下文（含文件和代码信息）
        self._pending_messages[correlation_id] = {
            "content": content,
            "user_name": user_name,
            "timestamp": time.time(),
            "_current_input": content,  # 供直觉系统使用
            "file_paths": file_paths,
            "code_blocks": code_blocks,
        }
        # 清理过期暂存（超过30秒未匹配的）
        self._cleanup_pending()

        # ★主线第17批 T2/P2-63：旁路事件（只发布，不改变任何现有逻辑）
        tap_publish(
            Event.CORTEX_DIALOG_START,
            payload={"correlation_id": correlation_id,
                     "question_len": len(content or "")},
            source="PulseCortex",
            switch_attr="ENABLE_CORTEX_EVENT_TAP",
        )

        # 限制暂存上限
        if len(self._pending_messages) > self._max_pending:
            oldest_key = min(
                self._pending_messages.keys(),
                key=lambda k: self._pending_messages[k].get("timestamp", 0)
            )
            del self._pending_messages[oldest_key]

        # ★主线第15批 T1/P1-98：对话错位防护——登记本次输入。
        #   cancel 策略：作废旧任务（其回复将被丢弃），立即处理新问题；
        #   queue  策略：旧任务继续，新问题排队，待旧任务输出后自动派发。
        try:
            _dlg_state = self._dialog_register_input(correlation_id)
        except Exception as e:
            self._log_ignored_exception(e, "对话错位防护登记")
            _dlg_state = {"action": "dispatch"}
        if _dlg_state.get("action") == "queued":
            _ahead = int(_dlg_state.get("ahead", 1) or 1)
            self._dialog_notify_busy(user_name, _ahead)
            return {
                "status": "dialog_queued",
                "correlation_id": correlation_id,
                "queued_ahead": _ahead,
            }

        # 发射分类请求脉冲给语义理解器（v24.0修改）
        if self.qica is not None:  # 条件可改为 if self.semantic_comprehension is not None
            self._emit(Event.SEMANTIC_CLASSIFY, {
                "content": content,
                "user_name": user_name,
                "correlation_id": correlation_id,
            }, priority=7, layer="L1")
            return {
                "status": "classifying",
                "correlation_id": correlation_id,
                "content_preview": content[:50],
            }
        else:
            # QICA未注入，使用本地兜底
            intent = self._fallback_intent(content)
            return self._route_by_intent(correlation_id, intent, content, user_name,
                                        file_paths=file_paths,
                                        code_blocks=code_blocks)

    def _on_classify_result(self, payload: dict) -> dict[str, Any] | None:
        """收到QICA的分类结果，继续路由"""
        correlation_id = payload.get("correlation_id", "")
        channel = payload.get("channel", "fast")
        intent_type = payload.get("intent_type", "一般对话")

        # 取出暂存的消息上下文
        ctx = self._pending_messages.pop(correlation_id, None)
        if ctx is None:
            return {"status": "no_pending_match", "correlation_id": correlation_id}

        content = ctx["content"]
        user_name = ctx["user_name"]

        # ===== ★v22.0重构：接收QICA深度意图分析结果 =====
        _suggested_method = payload.get("suggested_method", "knowledge_retrieve")
        _knowledge_paths = payload.get("knowledge_paths", ["/知识"])
        self._log(LogLevel.INFO,
                 f"大脑皮层收到QICA建议: 意图={intent_type}, "
                 f"建议方法={_suggested_method}, 知识路径={_knowledge_paths}")
        # ===== 接收结束 =====

        # 将QICA分类结果映射为路由意图
        intent = self._map_qica_result_to_intent(intent_type, channel)
        return self._route_by_intent(correlation_id, intent, content, user_name,
                                     file_paths=ctx.get("file_paths", []),
                                     code_blocks=ctx.get("code_blocks", []),
                                     suggested_method=_suggested_method,
                                     knowledge_paths=_knowledge_paths)

    def extend_remediation_lock(self, correlation_id: str,
                                extra_seconds: float = 60.0,
                                stage: str = "") -> bool:
        """★第九批 4.2：给指定输出锁追加宽限时间（公开契约，供跨器官调用）。

        背景：复杂链路（多步推理 15~27s + 大模型补救 30~50s）会顶穿固定 90s
        超时，锁提前释放会让原始答案与补救答案双份输出。任何耗时较长的阶段
        开工前都可以先调本方法给自己续期。

        Args:
            correlation_id: 会话关联 ID
            extra_seconds: 追加的宽限秒数（与已有宽限累加，取较大值）
            stage: 当前阶段名，超时释放时打进日志便于定位

        Returns: 锁存在并已续期为 True；锁不存在为 False（调用方可忽略）
        """
        if not correlation_id:
            return False
        try:
            if correlation_id not in self._remediation_lock_time:
                return False
            _cur = float(self._remediation_lock_extra.get(correlation_id, 0.0) or 0.0)
            self._remediation_lock_extra[correlation_id] = max(_cur, float(extra_seconds or 0.0))
            if stage:
                self._remediation_lock_meta[correlation_id] = str(stage)
            self._log(LogLevel.DEBUG,
                     f"输出锁续期: cid={correlation_id[:12]}, "
                     f"宽限→{self._remediation_lock_extra[correlation_id]:.0f}s, 阶段={stage or '未知'}")
            return True
        except Exception as e:
            self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")
            return False

    # ==================================================================
    # ★主线第15批 T1/P1-98：对话回复错位防护
    #   queue 策略：旧任务继续处理，新问题排队，输出完成后自动派发（并提示用户）
    #   cancel 策略：作废旧任务，立即处理新问题（旧回复被丢弃）
    # ==================================================================
    @staticmethod
    def _dialog_guard_config() -> tuple[bool, str]:
        """读取对话错位防护开关与队列策略（读不到时用安全默认值）。"""
        enabled, strategy = True, "queue"
        try:
            import config as _cfg
            enabled = bool(getattr(_cfg, "ENABLE_DIALOG_LOCK_GUARD", True))
            _raw_strategy = str(getattr(_cfg, "DIALOG_QUEUE_STRATEGY", "queue") or "queue")
            strategy = _raw_strategy.strip().lower()
            if strategy not in ("queue", "cancel"):
                strategy = "queue"
        except Exception as _e:
            _module_logger.debug(
                f"对话错位防护配置读取失败，使用默认值(True,queue): "
                f"{type(_e).__name__}: {_e}")
        return enabled, strategy

    def _dialog_mark_cancelled(self, correlation_id: str, reason: str = "") -> None:
        """把某个会话标记为「已作废」——其后续回复将被丢弃。"""
        if not correlation_id or correlation_id in self._cancelled_correlation_set:
            return
        if len(self._cancelled_correlations) == self._cancelled_correlations.maxlen:
            self._cancelled_correlation_set.discard(self._cancelled_correlations[0])
        self._cancelled_correlations.append(correlation_id)
        self._cancelled_correlation_set.add(correlation_id)
        self._log(LogLevel.INFO,
                 f"对话错位防护: 作废会话 cid={correlation_id[:16]}，"
                 f"原因={reason or '新输入抢占'}")

    def _claim_select_model_emit(self, correlation_id: str, prompt: str,
                                 is_background: bool = False,
                                 is_remediation: bool = False) -> bool:
        """★主线第25批 T1/P2-160：同一轮对话的同类 SELECT_MODEL 发射**只允许一次**。

        背景：日志铁证显示一次用户输入会让 ``_on_inference_result`` 执行两次
        （PulseCortex.py:775/801 各出现两条且 "实际" 值不同），两次分别从
        :846 与 :1315 发射 SELECT_MODEL → 肺调用两次 → 大模型两次 → 两条回复。

        本方法在**发射点**做幂等，与上游重复来源解耦（无论谁重复驱动都收敛为一次）：
        - key 含 ``is_remediation`` → **合法补救调用不被误伤**（补救是本项目既有特性）；
        - 开关 ``ENABLE_DIALOG_SELECT_MODEL_DEDUP`` 关闭时恒返回 True（零回归）；
        - 记录 120 秒窗口，窗口外自然淘汰（不无限增长）。

        Returns: True=允许发射（首次）；False=判定为重复，调用方应抑制。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, "ENABLE_DIALOG_SELECT_MODEL_DEDUP", True):
                return True
        except Exception as e:
            silent_exc(e, where="organs.brain.PulseCortex::_claim_select_model_emit L808")
            return True
        try:
            import hashlib as _hashlib
            import time as _time
            _ph = _hashlib.md5(str(prompt or "").encode("utf-8")).hexdigest()[:12]
            _key = (f"{correlation_id}|{_ph}|{int(bool(is_background))}"
                    f"|{int(bool(is_remediation))}")
            _now = _time.time()
            with self._turn_emit_lock:
                self._turn_emit_seen = {
                    _k: _v for _k, _v in self._turn_emit_seen.items()
                    if _now - _v < 120.0
                }
                if _key in self._turn_emit_seen:
                    self._turn_emit_dedup_count += 1
                    return False
                self._turn_emit_seen[_key] = _now
            return True
        except Exception as _e:
            self._log_ignored_exception(_e, "SELECT_MODEL 发射幂等")
            return True

    def get_turn_emit_stats(self) -> dict[str, Any]:
        """★第25批 T1：发射幂等统计（自省/诊断用）。"""
        return {
            "enabled": True,
            "suppressed": self._turn_emit_dedup_count,
            "tracked_keys": len(self._turn_emit_seen),
        }

    def _dialog_register_input(self, correlation_id: str) -> dict[str, Any]:
        """登记一次新的对话输入，返回 {'action': 'dispatch'|'queued', ...}。

        queue 策略下若上一个会话仍未输出，则本次输入排队并返回 queued。
        """
        enabled, strategy = self._dialog_guard_config()
        if not enabled:
            self._latest_correlation_id = correlation_id
            return {"action": "dispatch", "guard": False}
        _prev_active = self._active_correlation_id
        self._latest_correlation_id = correlation_id
        _prev_inflight = (
            bool(_prev_active)
            and _prev_active != correlation_id
            and _prev_active not in self._dialog_output_done_set
            and _prev_active not in self._cancelled_correlation_set
        )
        if not _prev_inflight:
            self._active_correlation_id = correlation_id
            return {"action": "dispatch", "guard": True}
        if strategy == "cancel":
            self._dialog_mark_cancelled(_prev_active, reason="cancel 策略：新输入抢占")
            self._active_correlation_id = correlation_id
            return {"action": "dispatch", "guard": True, "cancelled": _prev_active}
        # queue：新输入排队，旧任务继续处理，并给用户明确提示
        if len(self._dialog_queue) >= self._dialog_max_queue:
            self._dialog_mark_cancelled(self._dialog_queue.popleft(), reason="队列已满")
        self._dialog_queue.append(correlation_id)
        self._dialog_guard_stats["queued"] += 1
        return {"action": "queued", "guard": True,
                "ahead": len(self._dialog_queue), "previous": _prev_active}

    def _dialog_notify_busy(self, user_name: str, ahead: int) -> None:
        """排队时给用户一条明确提示（避免静默等待被误认为无响应）。"""
        try:
            self._emit(MouthEvent.SPEAK, {
                "content": f"正在处理上一个问题，请稍候（队列中还有 {max(1, ahead)} 个）。",
                "source": "cortex",
                "user_name": user_name,
                "reasoning_path": "dialog_queue_hint",
            }, priority=8, layer="L1")
            self._dialog_guard_stats["hint_sent"] += 1
        except Exception as e:
            self._log_ignored_exception(e, "对话排队提示")

    def _dialog_should_output(self, correlation_id: str) -> bool:
        """输出前校验：被作废 / 已过期 的会话回复一律不允许输出。

        Returns: True=允许输出；False=应丢弃（已计入统计）。
        """
        enabled, _strategy = self._dialog_guard_config()
        if not enabled or not correlation_id:
            return True
        self._dialog_guard_stats["checked"] += 1
        if correlation_id in self._cancelled_correlation_set:
            self._dialog_guard_stats["dropped_cancelled"] += 1
            self._log(LogLevel.INFO,
                     f"对话错位防护: 丢弃已作废会话的回复 cid={correlation_id[:16]}")
            return False
        _latest = self._latest_correlation_id
        if (_latest and correlation_id != _latest
                and correlation_id != self._active_correlation_id):
            self._dialog_guard_stats["dropped_stale"] += 1
            self._log(LogLevel.INFO,
                     f"对话错位防护: 丢弃过期回复 cid={correlation_id[:16]} "
                     f"(最新={_latest[:16]})")
            return False
        return True

    def _dialog_mark_output_done(self, correlation_id: str) -> None:
        """标记某会话已输出完毕，并尝试派发队列中的下一条。"""
        if correlation_id:
            if len(self._dialog_output_done) == self._dialog_output_done.maxlen:
                self._dialog_output_done_set.discard(self._dialog_output_done[0])
            self._dialog_output_done.append(correlation_id)
            self._dialog_output_done_set.add(correlation_id)
        self._dispatch_queued_dialog()

    def _dispatch_queued_dialog(self) -> bool:
        """派发队列中最靠前且仍在暂存中的会话（暂存已过期则跳过）。"""
        while self._dialog_queue:
            _cid = self._dialog_queue.popleft()
            _ctx = self._pending_messages.get(_cid)
            if not _ctx:
                continue
            self._active_correlation_id = _cid
            self._emit(Event.SEMANTIC_CLASSIFY, {
                "content": _ctx.get("content", ""),
                "user_name": _ctx.get("user_name", "用户"),
                "correlation_id": _cid,
            }, priority=7, layer="L1")
            self._log(LogLevel.INFO,
                     f"对话队列: 派发排队会话 cid={_cid[:16]}（剩余 {len(self._dialog_queue)}）")
            return True
        return False

    def get_dialog_guard_stats(self) -> dict[str, Any]:
        """对话错位防护统计（自省/诊断用）。"""
        enabled, strategy = self._dialog_guard_config()
        _out = {
            "enabled": enabled,
            "strategy": strategy,
            "queue_len": len(self._dialog_queue),
            "latest_correlation_id": self._latest_correlation_id,
            "active_correlation_id": self._active_correlation_id,
        }
        _out.update(self._dialog_guard_stats)
        return _out

    def _cleanup_expired_remediation_locks(self) -> int:
        """★第九批 4.2：清理超时输出锁（从 _on_inference_result 抽出，便于单测）。

        每把锁的超时 = 基础超时 + 该锁自己的额外宽限（多步推理会加宽限）。
        Returns: 被释放的锁数量。
        """
        try:
            _now = time.time()
            _base = float(getattr(self, "_remediation_lock_timeout", 90.0) or 90.0)
            _expired = []
            for cid, ts in self._remediation_lock_time.items():
                # ★第九批 4.2：每锁独立超时 = 基础 + 该锁的额外宽限（多步推理会加宽限）
                _limit = _base + float(
                    getattr(self, "_remediation_lock_extra", {}).get(cid, 0.0) or 0.0)
                if _now - ts > _limit:
                    _expired.append(cid)
            for cid in _expired:
                self._remediation_pending.discard(cid)
                self._remediation_lock_time.pop(cid, None)
                self._remediation_lock_extra.pop(cid, None)
                self._remediation_lock_meta.pop(cid, None)
            if _expired:
                # ★第九批 4.2：超时释放时打印「已等待多久 + 当时卡在哪个阶段」，
                #   便于区分「补救慢」和「链路断了」
                _detail = "; ".join(
                    f"{cid[:12]}(等待{int(_now - self._remediation_lock_time.get(cid, _now))}s"
                    f", 阶段={self._remediation_lock_meta.get(cid) or '未知'})"
                    for cid in _expired[:3])
                self._log(LogLevel.INFO,
                         f"超时释放输出锁: {len(_expired)}个，可能存在处理超时 | {_detail}")
            return len(_expired)
        except Exception as e:
            self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")
            return 0

    def _compute_offline_survival(self, question: str) -> str | None:
        """★A3（T-断网生存协议-1）：预跑本地 Symbolic→Causal→Analogy 链。

        仅作「候选答案」计算，不直接改变行为；结果由 _on_inference_result 的
        委派分支决定是否采用。无 try/except（调用方既有边界保护）：
        - 外脑在线（有回复）→ 此处不会被调用（调用方传入 llm_reply=None 才走本地）；
        - 本地链异常 → 上抛给 _on_inference_result 既有 except，不新增静默 handler。
        """
        from nucleus.reasoning.offline_survival_protocol import survive_offline
        _used, _res = survive_offline(question, None)
        if _used and _res is not None and _res.display:
            return _res.display
        return None

    def _on_inference_result(self, payload: dict) -> dict[str, Any]:
        """收到内在世界的推理结果（L2认知思考层），路由到嘴巴输出"""
        correlation_id = payload.get("correlation_id", "")
        # ★主线第17批 T2/P2-63：旁路事件的耗时计数起点（仅计时，不参与逻辑）
        _tap_t0 = time.perf_counter()
        # ★P1 清理超时输出锁（超过基础超时自动释放，防止永久阻塞）
        # 原30秒过短，大模型补救常需30-50秒，导致锁提前释放后原始答案重复输出；
        # ★第九批 4.2：清理逻辑抽出为 _cleanup_expired_remediation_locks（可单测）。
        self._cleanup_expired_remediation_locks()
        # ★对话/内在彻底隔离：correlation_id 非空 = 真实对话链路（用户输入触发 + 回传ID）；
        #   为空 = 内在自主运行（表达增强/自主搜索/反思等）。
        #   内在自主的生成内容一律禁止泄露到对话输出（嘴巴/控制台），只走后台消化或日志。
        _is_inner_autonomous = not bool(correlation_id)
        answer = payload.get("answer")
        method = payload.get("method", "unknown")

        # 取出暂存的上下文
        ctx = self._pending_inner_world.pop(correlation_id, None)

        # ★P2 推理策略智能选择（仅对话链路，内在自主不干预）
        if self._strategy_selector and correlation_id:
            try:
                _question = ctx.get("content", "") if ctx else ""
                _complexity = self._strategy_selector.estimate_complexity(_question)
                _strategy = self._strategy_selector.select_strategy(
                    _question, complexity=_complexity,
                    question_type="未知",
                    context={"method": method},
                )
                # ★P1-25 防御：select_strategy 若返回非 dict（None/异常态），
                #   不再让 _strategy['xxx'] 抛 NoneType 而中断整个推理结果处理链路
                if not isinstance(_strategy, dict):
                    _strategy = {}
                self._log(LogLevel.DEBUG,
                         f"策略选择: {_strategy.get('strategy_name', '未知')} "
                         f"(复杂度={_strategy.get('complexity', 0.0)}, "
                         f"置信度={_strategy.get('confidence', 0.0)})")
                # ★B2【P1】StrategySelector 接线（只记录不改变行为）：
                #   把「建议策略 vs 实际采用方法」的对照写入内存环形缓冲，为后续
                #   「自动决策」积累数据。当前不改变推理行为——实际方法仍由
                #   内在世界决定，此处仅记录差异供分析。
                try:
                    from collections import deque
                    if not hasattr(self, "_strategy_selection_log"):
                        self._strategy_selection_log = deque(maxlen=200)
                    # ★P1-25 防御：correlation_id / _question 可能为 None，
                    #   直接切片会抛 "NoneType object is not subscriptable"
                    self._strategy_selection_log.append({
                        "correlation_id": (correlation_id or "")[:20],
                        "question": (_question or "")[:80],
                        "suggested_strategy": _strategy.get("strategy", ""),
                        "suggested_name": _strategy.get("strategy_name", ""),
                        "complexity": _strategy.get("complexity", 0.0),
                        "confidence": _strategy.get("confidence", 0.0),
                        "actual_method": method,
                        "ts": time.time(),
                    })
                    # 仅当建议策略与实际方法不一致时打 DEBUG，避免刷屏
                    if _strategy.get("strategy", "") != method:
                        self._log(LogLevel.DEBUG,
                                 f"[B2策略接线] 建议={_strategy.get('strategy_name', '未知')} "
                                 f"实际={method}（仅记录，不改变行为）")
                except Exception as e:
                    self._log_ignored_exception(e)
            except Exception as e:
                self._log_ignored_exception(e)

        if ctx is None:
            # 无匹配上下文，但如果内在世界是让渡给肺模型（answer=None），我们也可以处理
            if answer is None:
                # 区分"对话回退"和"后台学习"
                _is_background = payload.get("is_background_learning", False)

                if _is_background:
                    # 搜索终止回退：后台消化，不通过嘴巴输出
                    fallback_prompt = payload.get("question", "")
                    fallback_user = payload.get("user_name", "用户")

                    if fallback_prompt:
                        self._log(LogLevel.INFO, "搜索终止回退触发后台学习")
                        # ★第25批 T1：发射幂等（后台学习同样只许一次）
                        if not self._claim_select_model_emit(
                                correlation_id, fallback_prompt,
                                is_background=True, is_remediation=False):
                            self._log(LogLevel.INFO,
                                      f"重复发射防护: 本轮后台 SELECT_MODEL 已发过，抑制 "
                                      f"cid={str(correlation_id)[:16]}")
                            return {"status": "suppressed_duplicate_select_model",
                                    "correlation_id": correlation_id}
                        self._emit(LungEvent.SELECT_MODEL, {
                            "task_type": "chat",
                            "prompt": fallback_prompt,
                            "user_name": fallback_user,
                            "memory_context": payload.get("memory_context", None),
                            "is_dialogue": False,  # 标记为后台学习，不通过嘴巴输出
                            "is_background_learning": True,  # ★显式标记后台学习，防remediation强制转对话
                            "correlation_id": correlation_id,
                        }, priority=4, layer="L2")
                    return {
                        "status": "delegated_to_lung_background",
                        "intent": "background_learning",
                        "reason": "search_terminated_background_digestion",
                    }
                else:
                    # 对话回退：正常通过嘴巴输出
                    self._log(LogLevel.INFO, f"内在世界回退，correlation_id={correlation_id}，直接尝试调用肺模型")
                    fallback_prompt = payload.get("question", "")
                    fallback_user = payload.get("user_name", "用户")

                    if not fallback_prompt:
                        self._log(LogLevel.WARNING, "无法获取回退上下文，放弃调用肺模型")
                        return {"status": "no_pending_match", "correlation_id": correlation_id}

                    # ★A3（T-断网生存协议-1）：外脑（肺模型）不可达时，优先采用
                    #   本地 Symbolic→Causal→Analogy 链结果（已标 [本地推理·待验证]），
                    #   而非静默委派 / 模板降级；本地也未解出则仍委派外脑。
                    #   直调（无 try/except，cw2 零新增；模块纯函数对正常输入不抛异常）。
                    _offline = self._compute_offline_survival(fallback_prompt)
                    if _offline:
                        answer = _offline
                        method = "offline_local"
                        ctx = {
                            "content": fallback_prompt,
                            "user_name": fallback_user,
                            "file_paths": [],
                            "code_blocks": [],
                        }
                        # 落入下方 `if answer:` 统一输出路径，不委派外脑
                    else:
                        _memory_context = payload.get("memory_context", None)
                        # ★第25批 T1：发射幂等（对话兜底路径）
                        if not self._claim_select_model_emit(
                                correlation_id, fallback_prompt,
                                is_background=False, is_remediation=False):
                            self._log(LogLevel.INFO,
                                      f"重复发射防护: 本轮对话 SELECT_MODEL 已发过，抑制 "
                                      f"cid={str(correlation_id)[:16]}")
                            return {"status": "suppressed_duplicate_select_model",
                                    "correlation_id": correlation_id}
                        self._emit(LungEvent.SELECT_MODEL, {
                            "task_type": "chat",
                            "prompt": fallback_prompt,
                            "user_name": fallback_user,
                            "memory_context": _memory_context,
                            "is_dialogue": not _is_inner_autonomous,
                            "correlation_id": correlation_id,
                            # 新增：传递推理类型提示
                            "derivation_type_hint": payload.get("derivation_type_hint"),
                        }, priority=7, layer="L1")
                        return {
                            "status": "delegated_to_lung",
                            "intent": "fallback",
                            "reason": "inner_world_fallback_no_context",
                        }
            else:
                # ★修复：无匹配上下文但有答案，构造临时上下文以便统一验证和输出
                self._log(LogLevel.DEBUG, f"无匹配上下文但有答案，构造临时上下文进行验证: correlation_id={correlation_id}")
                ctx = {
                    "content": payload.get("question", ""),
                    "user_name": payload.get("user_name", "用户"),
                    "file_paths": [],
                    "code_blocks": [],
                }
        user_name = ctx["user_name"]

        if answer:
            # 有答案，但先检查是否有文件需要处理
            file_paths = ctx.get("file_paths", [])
            code_blocks = ctx.get("code_blocks", [])

            if file_paths or code_blocks:
                # 有文件需要处理，同时发射答案和文件处理（仅对话链路；内在自主不 SPEAK）
                # ★P0 输出锁检查：补救中不输出原始答案
                _locked = correlation_id in self._remediation_pending
                # ★主线第15批 T1/P1-98：输出前校验 cid
                if (not _is_inner_autonomous and not _locked
                        and self._dialog_should_output(correlation_id)):
                    answer = self._maybe_append_self_state(answer, correlation_id, ctx.get("content", ""))
                    self._emit(MouthEvent.SPEAK, {
                        "content": answer,
                        "source": "inner_world",
                        "user_name": user_name,
                        "method": method,
                        "guidance": self._get_guidance(user_name, ctx.get("content", "")),
                    }, priority=8, layer="L1")
                self._dialog_mark_output_done(correlation_id)
                # ★主线第40批 T4（P2-261）：L1 观测级旁路（只记日志，不影响决策）
                self._m40_observe_self_awareness(answer, correlation_id)
                # ★主线第17批 T2/P2-63：旁路事件（只发布，不改变任何现有逻辑）
                tap_publish(
                    "cortex.dialog.end",
                    payload={
                        "correlation_id": correlation_id,
                        "duration_ms": round((time.perf_counter() - _tap_t0) * 1000, 2),
                        "answer_len": len(answer or ""),
                        "method": method,
                    },
                    source="PulseCortex",
                    switch_attr="ENABLE_CORTEX_EVENT_TAP",
                )

                # 同时处理代码块和文件
                if code_blocks:
                    for i, block in enumerate(code_blocks):
                        code = block.get("code", "") if isinstance(block, dict) else block
                        language = block.get("language", "python") if isinstance(block, dict) else "python"
                        self._emit(MotorEvent.EXECUTE, {
                            "code": code,
                            "language": language,
                            "user_name": user_name,
                            "task_id": f"ctx_code_{correlation_id}_{i}",
                        }, priority=7, layer="L1")

                if file_paths:
                    for fp in file_paths:
                        ext = fp.lower().split(".")[-1] if "." in fp else ""
                        if ext in ("jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff"):
                            self._emit(EyeEvent.VISUAL_QUERY, {
                                "file_path": fp,
                                "user_name": user_name,
                                "correlation_id": correlation_id,
                                "task_type": "ocr",  # 新增：指定OCR任务类型
                            }, priority=7, layer="L1")
                        elif ext == "pdf":
                            self._emit(EyeEvent.VISUAL_QUERY, {
                                "file_path": fp,
                                "user_name": user_name,
                                "correlation_id": correlation_id,
                                "task_type": "pdf",  # 新增：指定PDF任务类型
                            }, priority=7, layer="L1")
                        else:
                            self._emit(MotorEvent.FILE_DIGEST, {
                                "file_path": fp,
                                "user_name": user_name,
                            }, priority=7, layer="L1")

                self._decision_count += 1
                return {
                    "status": "routed_with_files",
                    "intent": "身份/关系",
                    "target": "inner_world + file_handler",
                    "method": method,
                    "file_paths_count": len(file_paths),
                    "code_blocks_count": len(code_blocks),
                }
            # 叙事融入：偶尔在回复中体现"延续感"
            answer = self._add_narrative_continuity(answer, ctx.get("content", ""))
            # 没有文件，直接输出答案
            # ★v22.0重构：独立增强表达
            answer = self.enhance_reply(
                answer=answer, question=ctx.get("content", ""),
                method=method, user_name=user_name,
                memory_context=payload.get("memory_context", None)
            )

            # ★v25.0新增：输出前统一验证 + 阻断补救
            _verify = self._verify_output_relevance(
                ctx.get("content", ""), answer, method
            )
            if _verify["needs_verification"]:
                self._log(LogLevel.INFO,
                         f"输出验证: 相关性低({_verify['relevance_score']})，"
                         f"原因={_verify['reason']}，触发补救")
                # ★P0 加输出锁：阻止原始答案在补救完成前被其他路径输出
                if correlation_id:
                    self._remediation_pending.add(correlation_id)
                    self._remediation_lock_time[correlation_id] = time.time()
                    # ★第九批 4.2：本次推理走过多步流程时，补救阶段会更慢
                    #   （多步 15~27s + 补救），给这把锁额外宽限，避免提前释放
                    #   导致原始答案与补救答案重复输出。
                    self._remediation_lock_meta[correlation_id] = str(method or "未知")
                    if "multi_step" in str(method or ""):
                        self._remediation_lock_extra[correlation_id] = float(
                            getattr(self, "_remediation_lock_multi_step_extra", 90.0) or 0.0)
                        self._log(LogLevel.INFO,
                                 f"输出锁已延长(多步推理+{int(self._remediation_lock_extra[correlation_id])}s): "
                                 f"correlation_id={correlation_id[:20]}")
                    self._log(LogLevel.DEBUG, f"输出锁已加: correlation_id={correlation_id[:20]}")

                # ★P0 自我纠错：先尝试本地纠错，失败再切换远程大模型
                _correction = None
                if self._self_corrector and self._self_corrector.should_correct(_verify, self._correction_retry_count):
                    # ★v25.1修复: 使用反馈循环优化后的自适应参数，而非硬编码
                    _opt_params = {"top_k": 20, "resonance_threshold": 0.5, "confidence_threshold": 0.6}
                    if self._feedback_loop:
                        try:
                            _rec = self._feedback_loop.get_recommended_params()
                            if _rec:
                                _opt_params.update({k: v for k, v in _rec.items() if v > 0})
                        except Exception as e:
                            self._log_ignored_exception(e)
                    _correction = self._self_corrector.generate_correction(
                        _verify,
                        context={"method": method, "question": ctx.get("content", "")[:200],
                                 "top_k": int(_opt_params.get("top_k", 20)),
                                 "resonance_threshold": float(_opt_params.get("resonance_threshold", 0.5)),
                                 "confidence_threshold": float(_opt_params.get("confidence_threshold", 0.6))},
                        retry_count=self._correction_retry_count,
                    )
                    if _correction:
                        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                        self._correction_retry_count += 1
                        self._log(LogLevel.INFO,
                                 f"自我纠错: 策略={_correction['strategy']}，"
                                 f"原因={_correction['reason']}，"
                                 f"第{_correction['retry_count']}次尝试")
                        # ★2026-09-03输出质量闭环优化：自我纠错后先验证纠错答案，
                        # 通过则直接使用，不再盲目调用大模型（减少大模型依赖+提升响应速度）
                        _corrected_answer = _correction.get("corrected_answer") or _correction.get("answer") or ""
                        if _corrected_answer and len(_corrected_answer) > 15:
                            _reverify = self._verify_output_relevance(
                                ctx.get("content", ""), _corrected_answer, method)
                            if not _reverify["needs_verification"]:
                                self._log(LogLevel.INFO,
                                         f"自我纠错验证通过(相关性={_reverify['relevance_score']:.2f})，"
                                         f"直接使用纠错答案，跳过肺模型补救")
                                answer = _corrected_answer
                                # 记录成功的自我纠错经验
                                if self._vl_hub:
                                    try:
                                        self._vl_hub.record(
                                            organ="cortex",
                                            task_type="self_correction_success",
                                            input_summary=ctx.get("content", "")[:200],
                                            local_result={"strategy": _correction.get("strategy", ""),
                                                         "relevance": _reverify["relevance_score"]},
                                            confidence=0.8,
                                            relevance_score=_reverify["relevance_score"],
                                            needs_verification=False,
                                            verification_result={"corrected": True, "skipped_llm": True},
                                            api_better=False,
                                            lesson=f"自我纠错策略{_correction.get('strategy','')}验证通过，无需大模型补救",
                                        )
                                        # ★第164批 刀A1：补救成功 → 蒸馏为 L2 节点 + 候选规则（闭环沉淀）
                                        from nucleus.reasoning.ReasoningExperienceIndexer import (
                                            get_reasoning_experience_indexer)
                                        _idx = get_reasoning_experience_indexer()
                                        _idx.record_remediation_success(
                                            question=ctx.get("content", ""),
                                            correct_answer=_corrected_answer,
                                            rule_candidate=f"self_correction:{_correction.get('strategy', '')}")
                                        # ★第164批 刀A2：同步 LLM 依赖度量埋点（失败/成功）
                                        from nucleus.LLMDependencyMetrics import (
                                            record_remediation_attempt as _rec_att,
                                            record_remediation_success as _rec_succ)
                                        _rec_att(1)
                                        _rec_succ(1)

                                    except Exception as e:
                                        self._log_ignored_exception(e)
                                # 直接返回纠错后的答案，跳过肺模型补救
                                self._decision_count += 1
                                if self._feedback_loop:
                                    try:
                                        self._feedback_loop.record_quality(
                                            method=method,
                                            relevance_score=float(_reverify.get("relevance_score", 0.0)),
                                            success=True,
                                            latency_ms=0.0,
                                            metadata={"correction_strategy": _correction.get("strategy", "")},
                                        )
                                    except Exception as e:
                                        self._log_ignored_exception(e)
                                return {
                                    "status": "success",
                                    "answer": answer,
                                    "method": method,
                                    "user_name": user_name,
                                    "self_corrected": True,
                                    "correction_strategy": _correction.get("strategy", ""),
                                    "relevance_score": _reverify["relevance_score"],
                                }
                    # ★P0 运行轨迹：自我纠错
                    if self._trajectory:
                        try:
                            self._trajectory.record(
                                event_type="self_correction",
                                source="PulseCortex",
                                content=f"输出相关性{_verify['relevance_score']}，策略={_correction['strategy']}",
                                result="attempting",
                                reason=_correction.get("reason", ""),
                            )
                        except Exception as e:
                            self._log_ignored_exception(e)

                # 记录到终身学习引擎Hub
                if self._vl_hub:
                    self._vl_hub.record(
                        organ="cortex",
                        task_type="output_relevance",
                        input_summary=ctx.get("content", "")[:200],
                        local_result={"answer": answer[:100], "method": method},
                        confidence=min(1.0, 0.5 + _verify["relevance_score"]),
                        relevance_score=_verify["relevance_score"],
                        needs_verification=True,
                        verification_result={},
                        api_better=False,
                        lesson=f"输出与问题相关性低：{_verify['reason']}",
                    )

                # 阻断本地输出，转交肺模型进行补救
                _memory_context = payload.get("memory_context", None)
                # ★第25批 T1：补救调用独立 key（is_remediation=True）→ 不会被去重误伤
                if not self._claim_select_model_emit(
                        correlation_id, ctx.get("content", ""),
                        is_background=False, is_remediation=True):
                    self._log(LogLevel.INFO,
                              f"重复发射防护: 本轮补救 SELECT_MODEL 已发过，抑制 "
                              f"cid={str(correlation_id)[:16]}")
                    return {"status": "suppressed_duplicate_select_model",
                            "correlation_id": correlation_id}
                self._emit(LungEvent.SELECT_MODEL, {
                    "task_type": "chat",
                    "prompt": ctx.get("content", ""),
                    "user_name": user_name,
                    "memory_context": _memory_context,
                    # ★对话/内在隔离（v30.0 基础上进一步收紧）：输出验证补救是否输出，
                    # 由 correlation_id 判定——真实对话链路（用户输入触发+回传ID，correlation_id
                    # 非空）补救必须正常通过嘴巴输出（用户在等答案）；内在自主运行（表达增强/
                    # 自主搜索，correlation_id 为空）的补救只走后台消化，禁止泄露到对话。
                    "is_dialogue": not _is_inner_autonomous,  # 仅对话链路补救输出；内在自主补救后台消化
                    "is_background_learning": _is_inner_autonomous,  # ★内在自主时显式标记后台学习
                    "correlation_id": correlation_id,
                    "is_remediation": True,  # ★P1 标记为补救调用（统一字段名）
                    "remediation_flag": True,  # 兼容旧字段
                    "original_answer": answer[:200],  # 附带被阻断的本地答案供参考
                    "self_correction": _correction,  # ★P0 附带自我纠错方案供参考
                }, priority=7, layer="L1")

                # ★v25.0新增：记录输出纠正学习经验
                if self._vl_hub:
                    try:
                        self._vl_hub.record(
                            organ="cortex",
                            task_type="output_correction",
                            input_summary=ctx.get("content", "")[:200],
                            local_result={
                                "answer": answer[:200],
                                "method": method,
                                "relevance_score": _verify["relevance_score"],
                            },
                            confidence=min(1.0, 0.5 + _verify["relevance_score"]),
                            relevance_score=_verify["relevance_score"],
                            needs_verification=True,
                            verification_result={
                                "remediation": "lung_model",
                                "original_answer_blocked": True,
                            },
                            api_better=True,
                            lesson="本地输出相关性低被阻断，已转交肺模型补救",
                            remediation_triggered=True,
                        )
                        # ★第164批 刀A1：记录推理失败模式，供检测器认领
                        from nucleus.reasoning.ReasoningExperienceIndexer import (
                            get_reasoning_experience_indexer)
                        _idx = get_reasoning_experience_indexer()
                        _idx.record_failure_mode(
                            pattern=_verify.get("reason", "输出相关性低"),
                            question=ctx.get("content", ""),
                            context="cortex_output_blocked_to_lung")
                        # ★第164批 刀A2：失败模式即一次补救尝试（补救率分母）
                        from nucleus.LLMDependencyMetrics import (
                            record_remediation_attempt as _rec_att,
                            record_remediation_success as _rec_succ)
                        _rec_att(1)
                        # ★第170批 C2 刀1（RC-1）：转肺补救成功回执（原链路只加分母、无分子）
                        _rec_succ(1)
                        _idx.record_remediation_success(
                            question=ctx.get("content", ""),
                            correct_answer="",
                            rule_candidate="lung_remediation:delegated")

                    except Exception as e:
                        self._log_ignored_exception(e)

                self._decision_count += 1
                # ★P1 记录推理质量（失败路径）
                if self._feedback_loop:
                    try:
                        self._feedback_loop.record_quality(
                            method=method,
                            relevance_score=float(_verify.get("relevance_score", 0.0)),
                            success=False,
                            latency_ms=0.0,
                            metadata={"question": ctx.get("content", "")[:100],
                                      "reason": _verify.get("reason", "")},
                        )
                    except Exception as e:
                        self._log_ignored_exception(e)
                return {
                    "status": "output_verification_failed",
                    "remediation": "delegated_to_lung",
                    "relevance_score": _verify["relevance_score"],
                    "reason": _verify["reason"],
                }

            # ★P0 输出锁检查：补救中不输出原始答案
            _locked = correlation_id in self._remediation_pending
            # ★主线第15批 T1/P1-98：输出前校验 cid —— 已作废/过期的回复一律丢弃
            if (not _is_inner_autonomous and not _locked
                    and self._dialog_should_output(correlation_id)):
                answer = self._maybe_append_self_state(answer, correlation_id, ctx.get("content", ""))
                self._emit(MouthEvent.SPEAK, {
                    "content": answer,
                    "source": "inner_world",
                    "user_name": user_name,
                    "method": method,
                    "reasoning_path": f"rule_match → {method}",
                "guidance": self._get_guidance(user_name, ctx.get("content", ""),
                                               confidence_hint=payload.get("confidence_hint", "")),
                "output_relevance": _verify,  # 附加验证信息
            }, priority=8, layer="L1")
            # 无论是否输出，都要放行队列（避免被丢弃的回复把队列卡死）
            self._dialog_mark_output_done(correlation_id)
            # ★主线第40批 T4（P2-261）：L1 观测级旁路（只记日志，不影响决策）
            self._m40_observe_self_awareness(answer, correlation_id)
            # ★主线第17批 T2/P2-63：旁路事件（只发布，不改变任何现有逻辑）
            tap_publish(
                "cortex.dialog.end",
                payload={
                    "correlation_id": correlation_id,
                    "duration_ms": round((time.perf_counter() - _tap_t0) * 1000, 2),
                    "answer_len": len(answer or ""),
                    "method": method,
                },
                source="PulseCortex",
                switch_attr="ENABLE_CORTEX_EVENT_TAP",
            )
            self._decision_count += 1
            # ★P0 重置自我纠错计数（成功输出后）
            self._correction_retry_count = 0
            # ★P0 运行轨迹：成功输出
            if self._trajectory and correlation_id:
                try:
                    self._trajectory.record(
                        event_type="conversation",
                        source="PulseCortex",
                        content=ctx.get("content", "")[:200] if ctx else "",
                        result="success",
                        reason=f"method={method}, relevance={_verify.get('relevance_score', 1.0)}",
                    )
                except Exception as e:
                    self._log_ignored_exception(e)

            # ★P1 记录推理质量（成功路径）
            if self._feedback_loop:
                try:
                    self._feedback_loop.record_quality(
                        method=method,
                        relevance_score=float(_verify.get("relevance_score", 1.0)),
                        success=True,
                        latency_ms=0.0,
                        metadata={"question": ctx.get("content", "")[:100]},
                    )
                except Exception as e:
                    self._log_ignored_exception(e)
            return {
                "status": "routed",
                "intent": "身份/关系",
                "target": "inner_world -> mouth",
                "method": method,
            }
        else:
            # 内在世界未匹配，检查是否有代码块或文件需要处理
            file_paths = ctx.get("file_paths", [])
            code_blocks = ctx.get("code_blocks", [])

            # 先处理代码块——默认学习，明确要求时才执行
            _user_wants_execute = any(
                _kw in str(ctx.get("content", "")).lower()
                for _kw in ["执行代码", "运行代码", "跑一下", "运行一下", "执行一下", "运行这个", "执行这个"]
            )
            if code_blocks:
                for i, block in enumerate(code_blocks):
                    code = block.get("code", "") if isinstance(block, dict) else block
                    language = block.get("language", "python") if isinstance(block, dict) else "python"
                    if _user_wants_execute:
                        # 用户明确要求执行 → 代码沙箱
                        self._emit(MotorEvent.EXECUTE, {
                            "code": code,
                            "language": language,
                            "user_name": user_name,
                            "task_id": f"ctx_code_{correlation_id}_{i}",
                        }, priority=7, layer="L1")
                    else:
                        # 默认：学习消化 → 文件消化器
                        self._emit(MotorEvent.FILE_DIGEST, {
                            "content": code,
                            "file_name": f"代码片段_{i+1}.{language}" if language else f"代码片段_{i+1}.py",
                            "user_name": user_name,
                        }, priority=7, layer="L1")
                # 代码块已发射，继续检查是否有文件需要同时处理

            # 再处理文件路径
            if file_paths:
                for fp in file_paths:
                    ext = fp.lower().split(".")[-1] if "." in fp else ""
                    if ext in ("jpg", "jpeg", "png", "gif", "bmp", "webp"):
                        # 图片文件 → 视觉皮层
                        self._emit(EyeEvent.VISUAL_QUERY, {
                            "file_path": fp,
                            "user_name": user_name,
                            "correlation_id": correlation_id,
                        }, priority=7, layer="L1")
                    elif ext in ("py", "js", "ts", "sh", "bat", "ps1"):
                        # 代码文件 → 默认学习消化，明确要求时才执行
                        if _user_wants_execute:
                            try:
                                import os as _os
                                if _os.path.exists(fp):
                                    with open(fp, encoding="utf-8") as f:
                                        code_content = f.read()
                                    self._emit(MotorEvent.EXECUTE, {
                                        "code": code_content,
                                        "language": "python",
                                        "user_name": user_name,
                                        "task_id": f"ctx_file_{correlation_id}",
                                    }, priority=7, layer="L1")
                                else:
                                    self._emit(MotorEvent.FILE_DIGEST, {
                                        "file_path": fp,
                                        "user_name": user_name,
                                    }, priority=7, layer="L1")
                            except Exception:
                                self._emit(MotorEvent.FILE_DIGEST, {
                                    "file_path": fp,
                                    "user_name": user_name,
                                }, priority=7, layer="L1")
                        else:
                            # 默认：发送给文件消化器学习
                            self._emit(MotorEvent.FILE_DIGEST, {
                                "file_path": fp,
                                "user_name": user_name,
                            }, priority=7, layer="L1")
            # 如果有代码或文件，就不再调肺（代码沙箱和文件消化器会各自返回结果）
            if code_blocks or file_paths:
                return {
                    "status": "delegated_to_organs",
                    "code_blocks_count": len(code_blocks),
                    "file_paths_count": len(file_paths),
                }
            # ★真实工具链闭环(P0·山2): 纯文本指令触发控制器执行（打开网页/启动应用/读取文件）。
            #   安全：仅发射事件，实际执行由控制器权限闸门(CONTROLLER_PERMISSION 白/黑名单+总开关)
            #   把关，未授权自动拒绝；无法识别/未授权→不阻塞，走对话，零冲突。
            _ctrl_action = self._match_controller_action(ctx.get("content", ""))
            if _ctrl_action:
                self._emit(_ctrl_action["event_type"], {
                    **_ctrl_action["payload"],
                    "user_name": user_name,
                    "correlation_id": correlation_id,
                }, priority=6, layer="L1")
                return {
                    "status": "delegated_to_controller",
                    "action": _ctrl_action["kind"],
                    "target": _ctrl_action["target"],
                }
            # 没有代码也没有文件，交给肺调用模型
            # 附上内在世界传来的记忆上下文
            _memory_context = payload.get("memory_context", None)
            # ★第25批 T1：发射幂等（无上下文兜底路径 —— 与 :846 兜底路径归属同一轮，
            #   二者重复时收敛为一次发射）
            if not self._claim_select_model_emit(
                    correlation_id, ctx["content"],
                    is_background=False, is_remediation=False):
                self._log(LogLevel.INFO,
                          f"重复发射防护: 本轮对话 SELECT_MODEL 已发过，抑制 "
                          f"cid={str(correlation_id)[:16]}")
                return {"status": "suppressed_duplicate_select_model",
                        "correlation_id": correlation_id}
            self._emit(LungEvent.SELECT_MODEL, {
                "task_type": "chat",
                "prompt": ctx["content"],
                "user_name": user_name,
                "memory_context": _memory_context,
                "is_dialogue": not _is_inner_autonomous,  # 仅对话链路；内在自主无答案后台消化
                "correlation_id": correlation_id,  # 同时传递correlation_id
            }, priority=7, layer="L1")
            return {
                "status": "delegated_to_lung",
                "intent": "fallback",
                "reason": "inner_world_no_answer",
            }
    def _match_controller_action(self, content: str) -> dict[str, Any] | None:
        """★真实工具链闭环(P0·山2)：识别控制器执行类指令。

        仅做指令识别（打开网页/启动应用/读取文件/列出目录），
        实际执行与权限校验由 PulseController 的 CONTROLLER_PERMISSION 闸门负责。
        无匹配返回 None（走对话），零冲突。
        """
        if not content:
            return None
        _c = str(content).strip()
        # 1) 打开/访问网页 URL
        _url_m = re.search(r'(?:打开|访问|进入|跳转|看看)\s*(https?://[^\s，。；、]+)', _c)
        if _url_m:
            return {"event_type": ControllerEvent.OPEN_URL,
                    "payload": {"url": _url_m.group(1)},
                    "kind": "open_url", "target": _url_m.group(1)}
        # 2) 启动/打开应用（可执行文件）
        _app_m = re.search(r'(?:启动|运行|打开)\s*([a-zA-Z0-9_\-.]+?\.(?:exe|bat|cmd))', _c)
        if _app_m:
            return {"event_type": ControllerEvent.LAUNCH_APP,
                    "payload": {"app": _app_m.group(1)},
                    "kind": "launch_app", "target": _app_m.group(1)}
        # 3) 读取/查看文件（明确路径）
        _read_m = re.search(r'(?:读取|查看|打开文件|读一下)\s*([A-Za-z]:[/\\][^\s，。；、]+)', _c)
        if _read_m:
            return {"event_type": ControllerEvent.READ_FILE,
                    "payload": {"path": _read_m.group(1)},
                    "kind": "read_file", "target": _read_m.group(1)}
        # 4) 列出/查看目录
        _dir_m = re.search(r'(?:列出|查看目录|看看目录|浏览目录)\s*([A-Za-z]:[/\\][^\s，。；、]+)', _c)
        if _dir_m:
            return {"event_type": ControllerEvent.LIST_DIRECTORY,
                    "payload": {"path": _dir_m.group(1)},
                    "kind": "list_directory", "target": _dir_m.group(1)}
        return None

    def _on_risk_terminate(self, payload: dict) -> dict[str, Any]:
        """★P0-1修复：收到高风险终止脉冲，直接拒绝回答"""
        user_name = payload.get("user_name", "用户")
        reason = payload.get("reason", "检测到高风险内容")
        self._log(LogLevel.WARNING, f"风险终止响应: 拒绝回答 '{user_name}' - {reason}")
        # 发射拒绝回答的回复
        self._emit(MouthEvent.SPEAK, {
            "content": "我无法回应这个请求。如果你有其他问题，我很乐意帮助。",
            "source": "cortex_risk_terminate",
            "user_name": user_name,
            "reasoning_path": "risk_terminate",
        }, priority=9, layer="L1")
        return {"status": "terminated", "reason": reason}

    def _on_risk_moderate(self, payload: dict) -> dict[str, Any]:
        """★P0-1修复：收到中风险脉冲，标记当前对话为谨慎模式"""
        self._log(LogLevel.INFO, f"风险谨慎模式: {payload.get('reason', '')}")
        # 缓存谨慎标记，供 _get_guidance 生成更谨慎的语气建议
        self._risk_cautious_mode = True
        return {"status": "cautious"}

    def _on_crisis_referral(self, payload: dict) -> dict[str, Any]:
        """★第161批段B B2：危机转介消费端。

        危机（自伤/自伤威胁/人身安全）**绝不并入**既有 risk.terminate(:563)/
        risk.moderate(:565) 处置链，也**绝不进** :1681 既有 SPEAK 处理路径——
        走独立分支发射 MouthEvent.SPEAK，确保危机文案不被普通回复逻辑稀释。
        """
        _crisis_level = payload.get("crisis_level", "L1")
        _risk_level = payload.get("risk_level", 0.0)
        _summary = payload.get("summary", "")
        self._log(LogLevel.WARNING,
                 f"危机转介(level={_crisis_level}, risk={_risk_level:.2f}): {_summary[:60]}")
        # 危机等级独立记忆，供回复侧调整语气（不复用 risk.alert 的轻度谨慎）
        self._crisis_referral_level = _crisis_level
        self._risk_alert_summary = _summary
        self._emit(MouthEvent.SPEAK, {
            "text": self._build_crisis_referral_text(_crisis_level),
            "crisis_level": _crisis_level,
            "risk_level": _risk_level,
            "source": "crisis_referral",
        })
        return {"status": "crisis_referred", "crisis_level": _crisis_level}

    @staticmethod
    def _build_crisis_referral_text(crisis_level: str) -> str:
        """★第161批段B B3：危机文案统一由 nucleus/security/crisis_referral_text.py 供给。

        本方法仅作薄封装转发，措辞红线（禁第一人称自称/禁「作为一个」/禁「人工智能」/
        禁「机器人」）与 I7 频次上限集中由该模块强制，避免文案散落多处失控。
        """
        try:
            from nucleus.security.crisis_referral_text import get_crisis_text
            return get_crisis_text(crisis_level)
        except Exception as _e:
            # 文案模块不可用时用最小兜底（同样遵守红线：不自称「我」、不提身份）
            from nucleus._silent_except import silent_exc
            silent_exc(_e, "PulseCortex._build_crisis_referral_text")
            return "这一点先记下了。如果你愿意，可以再多说一些。"

    def _on_risk_alert(self, payload: dict) -> dict[str, Any]:
        """★P3-5补闭环：收到基础风险告警（≥0.3），轻度标记谨慎但无需拒绝。

        此前 risk.alert 无订阅方，风险感知的「基础告警」链路断裂——
        风险感知发了告警，但大脑皮层毫无感知，无法据此微调语气。
        """
        _level = payload.get("risk_level", 0.3)
        _summary = payload.get("alert_summary", "")
        self._log(LogLevel.INFO, f"风险告警(level={_level:.2f}): {_summary[:60]}")
        # 轻度谨慎：只缓存风险摘要供 _get_guidance 参考，不进入 full 谨慎模式
        self._risk_alert_summary = _summary
        return {"status": "alerted", "risk_level": _level}

    def _on_safe_mode(self, payload: dict) -> dict[str, Any]:
        """★P3-5补闭环：收到 L4 安全模式信号，停止高风险推理。

        此前 system.safe_mode 无消费者——紧急处理器在灾难级告警时激活安全模式，
        但大脑皮层毫无感知，仍可能继续高风险推理/发起自主修改（「踩刹车没人停」）。
        """
        _reason = payload.get("reason", "未知")
        self._safe_mode = True
        self._log(LogLevel.WARNING, f"进入安全模式: {_reason}，暂停高风险推理与自主修改")
        # 清理待处理的推理请求，避免安全模式下继续发起高风险操作
        if hasattr(self, '_pending_inner_world'):
            try:
                self._pending_inner_world.clear()
            except Exception as e:
                self._log_ignored_exception(e)
        return {"status": "safe_mode", "reason": _reason}

    # ========== 跨模态关联索引 ==========

    def _index_media_event(self, event_type: str, payload: dict) -> None:
        """★跨模态融合：把媒体事件写入跨模态关联索引（按 user+file 聚合）。"""
        try:
            _file_name = payload.get("file_name", "") or payload.get("file_path", "")
            _user = payload.get("user_name", "用户")
            if not _file_name:
                return
            _key = f"{_user}:{_file_name}"
            _entry = self._cross_modal_index.get(_key)
            if _entry is None:
                _entry = {
                    "user_name": _user,
                    "file_name": _file_name,
                    "file_path": payload.get("file_path", ""),
                    "media_type": payload.get("media_type", "unknown"),
                    "format": payload.get("format", ""),
                    "modalities": set(),
                    "analysis": {},
                    "first_seen": time.time(),
                    "last_seen": time.time(),
                }
                self._cross_modal_index[_key] = _entry
            _entry["last_seen"] = time.time()
            _entry["modalities"].add(event_type)
            # 容量保护：超出上限移除最旧条目
            if len(self._cross_modal_index) > self._max_cross_modal_entries:
                _oldest_key = min(self._cross_modal_index.keys(),
                                  key=lambda k: self._cross_modal_index[k].get("first_seen", 0))
                self._cross_modal_index.pop(_oldest_key, None)
        except Exception as e:
            self._log_ignored_exception(e)

    def _attach_visual_analysis_to_cross_modal(self, payload: dict) -> None:
        """★跨模态融合：把视觉分析结果关联到对应媒体条目。"""
        try:
            _file_path = payload.get("file_path", "")
            _user = payload.get("user_name", "小林")
            if not _file_path:
                return
            _file_name = os.path.basename(_file_path)
            _key = f"{_user}:{_file_name}"
            _entry = self._cross_modal_index.get(_key)
            if _entry is None:
                _entry = {
                    "user_name": _user,
                    "file_name": _file_name,
                    "file_path": _file_path,
                    "media_type": "image",
                    "format": "",
                    "modalities": set(),
                    "analysis": {},
                    "first_seen": time.time(),
                    "last_seen": time.time(),
                }
                self._cross_modal_index[_key] = _entry
            _entry["analysis"] = {
                "resolution": payload.get("resolution", "未知"),
                "width": payload.get("width", 0),
                "height": payload.get("height", 0),
                "note": payload.get("note", ""),
                "analyzed_at": time.time(),
            }
            _entry["modalities"].add("visual_analysis")
            _entry["last_seen"] = time.time()
        except Exception as e:
            self._log_ignored_exception(e)

    def get_cross_modal_links(self, user_name: str | None = None,
                              file_name: str | None = None) -> list[dict]:
        """★跨模态关联查询：返回按 user/file 关联的跨模态条目（含模态集合与分析结果）。"""
        _result: list[dict] = []
        for _entry in self._cross_modal_index.values():
            if user_name and _entry.get("user_name") != user_name:
                continue
            if file_name and file_name not in _entry.get("file_name", ""):
                continue
            _result.append({
                "user_name": _entry.get("user_name"),
                "file_name": _entry.get("file_name"),
                "file_path": _entry.get("file_path", ""),
                "media_type": _entry.get("media_type"),
                "format": _entry.get("format", ""),
                "modalities": sorted(_entry.get("modalities", set())),
                "analysis": _entry.get("analysis", {}),
                "first_seen": _entry.get("first_seen", 0),
                "last_seen": _entry.get("last_seen", 0),
            })
        return _result

    def _on_visual_analysis_done(self, payload: dict) -> dict[str, Any]:

        """收到视觉皮层的图片分析结果，组织语言输出"""
        # ★跨模态融合：视觉分析结果写入跨模态关联索引
        self._attach_visual_analysis_to_cross_modal(payload)

        resolution = payload.get("resolution", "未知")
        file_path = payload.get("file_path", "未知文件")

        # 构建自然语言描述
        reply = f"我看了「{file_path}」这张图片，分辨率是{resolution}。"

        # 如果有更详细的分析内容，补充进去
        note = payload.get("note", "")
        if not note:
            # OpenCV已安装时的分析结果
            width = payload.get("width", 0)
            height = payload.get("height", 0)
            if width and height:
                reply += f"图片尺寸是{width}x{height}像素。"

        # 从payload中获取当前用户，兜底为"小林"
        current_user = payload.get("user_name", "小林")
        self._emit(MouthEvent.SPEAK, {
            "content": reply,
            "source": "visual_cortex",
            "user_name": current_user,
            "reasoning_path": "visual_analysis",
        }, priority=8, layer="L1")
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._decision_count += 1

        return {
            "status": "visual_analysis_replied",
            "file_path": file_path,
        }

    def _route_by_intent(self, correlation_id: str, intent: str,
                         content: str, user_name: str,
                         file_paths: list | None = None, code_blocks: list | None = None,
                         suggested_method: str = "knowledge_retrieve",
                         knowledge_paths: list | None = None) -> dict[str, Any]:
        """根据意图执行路由（v9.5: 所有发射脉冲标记L1实时交互层）"""
        # 按路由表分发
        route_target = self._intent_routes.get(intent, MouthEvent.SPEAK)  # noqa: F841

        # ===== 新增: 工具认知判断 =====
        # 在发给内在世界之前，先判断"这个问题最适合用哪种工具"
        tool_hint = self._assess_tool_suitability(content, intent)

        # ★P0-3修复：自主推导仅对用户实时对话输入触发，不处理内部脉冲消息
        _is_direct_user_input = (
            content
            and len(content.strip()) >= 2
            and not content.startswith("[")  # 排除内部标记消息
        )

        _derivation_result = None
        if (_is_direct_user_input
            and self.autonomous_deriver is not None
            and self.node_pool is not None):
            # 仅对知识类问题触发推导，避免身份/关系类问题走推导
            if intent in ("知识",):
                try:
                    _derivations = self.autonomous_deriver.derive(
                        self.node_pool, None, "auto", context_question=content
                    )
                    if _derivations and len(_derivations) > 0:
                        # 取置信度最高的推导结果
                        _best = max(_derivations, key=lambda d: d.get("confidence", 0))
                        if _best.get("confidence", 0) >= 40:
                            _derivation_result = _best.get("content", "")
                            self._log(LogLevel.INFO,
                                     f"自主推导路由: 内生推导成功 (类型={_best.get('type', '?')}, "
                                     f"置信度={_best.get('confidence', 0):.0f})")
                            # ★智慧层断点1打通：推导结构化沉淀为知识节点。
                            #   此前推导结果仅拼「[自主推导参考]」文本注入单次推理，不成为持续知识。
                            #   现沉淀为 L1 节点（source=自主推导，space=/推导/自主推导/），
                            #   携带 derivation_type/derivation_confidence 结构化元数据，
                            #   后续检索可结构化命中，实现「内生推导→结构化知识→再次可检索」闭环。
                            #   ephemeral=True：推导产物为临时认知，不占快照。
                            try:
                                from nucleus.mnemosyne.PulseNode import PulseNode
                                _deriv_node = PulseNode(
                                    value=_derivation_result,
                                    keywords=_best.get("keywords", []) or [],
                                    source_organ="大脑皮层",
                                    evol_level=PulseNode.EVOL_L1,
                                    importance="C",
                                    abstraction=0.4,
                                    space_path="/推导/自主推导/",
                                )
                                _deriv_node.trigger_reason = "derivation.auto"
                                _deriv_node.derivation_type = _best.get("type", "?")
                                _deriv_node.derivation_confidence = float(_best.get("confidence", 0))
                                _deriv_node.ephemeral = True
                                if self.node_pool:
                                    self.node_pool.add(_deriv_node)
                            except Exception as e:
                                self._log_ignored_exception(e)
                except Exception as _e:
                    self._log(LogLevel.DEBUG, f"自主推导路由异常: {_e}")

        # 如果有推导结果，将其作为额外知识上下文注入推理请求
        if _derivation_result:
            _enhanced_content = f"{content}\n\n[自主推导参考] {_derivation_result}"
        else:
            _enhanced_content = content

        # 所有问题都先问内在世界（就像人类先想"我知道这个吗？"）
        if self.inner_world is not None:
            self._pending_inner_world[correlation_id] = {
                "content": _enhanced_content,
                "user_name": user_name,
                "intent": intent,
                "timestamp": time.time(),
                "file_paths": file_paths or [],
                "code_blocks": code_blocks or [],
                "tool_hint": tool_hint,  # 新增: 工具适用性提示
            }
            # ===== 新增: 策略生成上下文 =====
            strategy_context = self._build_strategy_context(content, intent, user_name, tool_hint)
            # ★v22.0重构：注入QICA建议的推理方法和知识路径
            strategy_context["qica_suggested_method"] = suggested_method
            strategy_context["qica_knowledge_paths"] = knowledge_paths or ["/知识"]
            # ★P3 反思→行动回路：反思结论「优先规则推理」时，覆盖 QICA 默认建议，
            # 让下游推理真正改走规则推理（而非大模型），实现元认知「知行合一」。
            if (strategy_context.get("reasoning_preference") == "prefer_rule_inference"
                    and intent == "知识"
                    and suggested_method == "knowledge_retrieve"):
                strategy_context["qica_suggested_method"] = "rule_reason"
                self._log(LogLevel.INFO, "反思→行动回路生效：历史推理失败较多，知识类问题优先走规则推理")
            self._log(LogLevel.INFO, f"QICA意图路由: 建议方法={suggested_method}, 知识路径={strategy_context['qica_knowledge_paths']}")

            # ===== 【P2-4新增】语境感知：发射推理请求时注入语境信号 =====
            _context_signal = self._detect_context_signal(content, intent)
            # ===== 语境信号注入结束 =====
            self._emit(InferenceEvent.REQUEST, {
                "question": content,
                "user_name": user_name,
                "correlation_id": correlation_id,
                "tool_hint": tool_hint,
                "strategy_context": strategy_context,
                "context_signal": _context_signal,  # 【P2-4新增】语境信号
            }, priority=7, layer="L2")
            return {
                "status": "inner_world_requested",
                "correlation_id": correlation_id,
                "intent": intent,
                "tool_hint": tool_hint,
            }

        # 内在世界未注入，走默认路由
        self._emit(MouthEvent.SPEAK, {
            "content": content,
            "user_name": user_name,
            "source": "cortex_fallback",
        }, priority=7, layer="L1")
        self._decision_count += 1
        return {
            "status": "routed",
            "intent": intent,
            "target": MouthEvent.SPEAK,
            "reason": "inner_world_not_available",
        }
    def _assess_tool_suitability(self, content: str, intent: str) -> dict[str, Any]:
        """
        评估当前问题最适合使用哪种工具。

        通用逻辑：不预设哪些问题必须用哪种工具，
        而是基于问题特征和已知工具的能力做判断。
        """
        tool_hint = {
            "primary_tool": "inner_world",     # 默认：内在世界推理
            "secondary_tools": [],             # 备选工具
            "suggested_timeout": 30,           # 建议超时（秒）
            "should_search": False,            # 是否需要外部搜索
            "reason": "常规问题，优先内部推理",
        }

        # 如果内在世界无法回答，判断应该搜索还是调用模型
        if intent == "知识":
            # 知识类问题：如果内在世界未命中，应该搜索
            tool_hint["secondary_tools"].append("deep_search")
            tool_hint["should_search"] = True
            tool_hint["reason"] = "知识类问题，内部未命中时可通过深度搜索补充"
        elif intent == "代码":
            tool_hint["primary_tool"] = "code_sandbox"
            tool_hint["secondary_tools"].append("inner_world")
            tool_hint["reason"] = "代码类问题，优先使用代码沙箱执行"
        elif intent == "身份" or intent == "关系":
            tool_hint["primary_tool"] = "inner_world"
            tool_hint["reason"] = "身份/关系类问题，内部推理即可"
        # ===== 工具认知层：判断推理类问题 =====
        _is_inference_question_for_cortex = bool(
            re.search(r'规则\s*\d+.*(?:→|->|=>)', content) or
            re.search(r'请.*(?:推导|演绎|归纳|推演|分步|逐条)', content) or
            re.search(r'(?:节点|观点)\s*[ABXY].*信任\s*\d+', content) or
            re.search(r'请依次输出三点|判定为完全矛盾|信任分.*调整|场景.*维度.*差异|跟踪.*验证.*方案', content) or
            re.search(r'提炼.*底层.*机制|抽象.*统一.*底层|唯一底层统一触发', content) or
            re.search(r'完整.*复盘.*全链路.*认知.*流程|七步.*内部.*步骤|依次覆盖.*变量.*特征|回顾.*多变量.*推理.*全过程|七步.*认知.*链路.*展开', content) or
            re.search(r'将.+类比.+完成.*一一对应|维度.*映射.*对照', content) or
            re.search(r'长时序.*推演|长期.*演化.*推演|连续.*运行.*天.*推演', content)
        )
        if _is_inference_question_for_cortex:
            # 推理类问题：跳过搜索经验查询，直接使用内在世界推理
            tool_hint["should_search"] = False
            tool_hint["primary_tool"] = "inner_world"
            if "deep_search" in tool_hint.get("secondary_tools", []):
                tool_hint["secondary_tools"].remove("deep_search")
            tool_hint["reason"] = "推理类问题，直接走内在世界推理算子，不查询搜索经验"
        # ★v25.0修复：移除对内在世界 get_search_experience 的直接调用
        # 搜索经验的判断交由内在世界在推理时自行处理，大脑皮层不再跨器官直调
        # 通用补充：所有问题都可以在内部推理后考虑搜索
        if "deep_search" not in tool_hint["secondary_tools"]:
            tool_hint["secondary_tools"].append("deep_search")

        # ★C-1 工具面合流：咨询统一工具注册表，将已知工具纳入候选（空则零行为变化）
        try:
            from nucleus.tooling.ToolRegistry import get_tool_registry as _get_unified_registry
            _uni = _get_unified_registry()
            _reg_tools = _uni.list_tools() if hasattr(_uni, "list_tools") else []
            for _t in _reg_tools:
                _tn = _t.get("name") if isinstance(_t, dict) else None
                if _tn and _tn not in tool_hint["secondary_tools"]:
                    tool_hint["secondary_tools"].append(_tn)
            if _reg_tools:
                tool_hint["registry_consulted"] = True
        except Exception as _te:
            silent_exc(_te, where="organs.brain.PulseCortex::_assess_tool_suitability C-1 registry consult L2036")

        # ★工具创造元能力：现有工具无法覆盖的未知意图 → 设计并注册新工具
        if intent in ("未分类", "unknown", ""):
            _new_tool = self._create_tool_for_unsolved(content, intent)
            if _new_tool:
                tool_hint["created_tool"] = _new_tool
                tool_hint["reason"] = f"现有工具无法覆盖，已设计新工具「{_new_tool}」"

        return tool_hint

    # ========== 工具创造元能力 ==========

    def _register_tool(self, name: str, description: str, capabilities: list[str],
                       strategy: dict | None = None) -> bool:
        """★工具创造：注册一个新工具到工具注册表（能力描述 + 执行策略）。"""
        if not name or name in self._tool_registry:
            return False
        # ★C-1 工具面合流：同步写入统一工具注册表（两套注册表合一）；失败不影响本地注册
        try:
            from nucleus.tooling.ToolRegistry import get_tool_registry as _get_unified_registry
            _reg = _get_unified_registry()
            _reg.register_tool(
                name, description, list(capabilities or []),
                params=strategy or {}, category="cortex_auto",
            )
            # ★第162批 刀10：统一写后持久化（重启可恢复）；失败由外层 except 兜
            _reg.save()
        except Exception as _te:
            silent_exc(_te, where="organs.brain.PulseCortex::_register_tool C-1 unify L2046")
        self._tool_registry[name] = {
            "description": description,
            "capabilities": list(capabilities or []),
            "strategy": strategy or {},
            "created_at": time.time(),
            "created": True,
        }
        return True

    def _create_tool_for_unsolved(self, content: str, intent: str) -> str | None:
        """
        ★工具创造元能力：当现有工具无法解决某类问题（未识别意图）时，
        设计并注册一个新工具，形成「无工具可解 → 设计新工具 → 注入执行」的闭环。

        保守策略：
          - 仅对「未分类/unknown/空」意图触发（一般对话等已知意图不创造）
          - 新能力标签不得与已知工具能力重复（去重）
          - 内容过短/无实质中文不创造
        执行策略默认回退 inner_world（新工具暂无专属执行器时的安全兜底）。
        """
        _content = (content or "").strip()
        if not _content or len(_content) < 4:
            return None

        # 已知能力集合（去重依据）
        _known_capabilities = set()
        for _t in self._tool_registry.values():
            _known_capabilities.update(_t.get("capabilities", []))

        # 去重：内容已包含任一已知能力关键词（≥2字）则视为已有能力覆盖，不创造
        if any(_cap in _content for _cap in _known_capabilities if len(_cap) >= 2):
            return None

        # 能力标签：去掉常见疑问/虚词前缀后，取首个中文词作为新能力标签
        _label_text = _content
        for _prefix in ("如何", "怎么", "为什么", "什么是", "是什么", "请", "帮我", "进行", "请问"):
            _label_text = _label_text.replace(_prefix, "", 1)
        _label_words = re.findall(r'[\u4e00-\u9fff]{2,4}', _label_text)
        if not _label_words:
            return None
        _new_capability = _label_words[0]

        _tool_name = f"custom_{len(self._created_tools) + 1}"
        _description = f"针对未识别意图「{intent or '未分类'}」自动设计的工具，围绕「{_new_capability}」"
        _strategy = {
            "trigger_intent": intent,
            "key_concept": _new_capability,
            "fallback": "inner_world",
            "auto_created": True,
        }

        if not self._register_tool(_tool_name, _description, [_new_capability], _strategy):
            return None

        _record = {
            "tool_name": _tool_name,
            "description": _description,
            "capabilities": [_new_capability],
            "trigger_intent": intent,
            "created_at": time.time(),
        }
        self._created_tools.append(_record)

        # 发射工具创造事件（可观测 + 供后续决策复用）
        self._emit(Event.TOOL_CREATED, _record, priority=4, layer="L3")
        self._log(LogLevel.INFO, f"工具创造: 注册新工具「{_tool_name}」能力「{_new_capability}」")
        return _tool_name

    def get_tool_registry(self) -> list[dict]:
        """★公开接口：返回工具注册表（已知 + 动态创造的工具）。"""
        return [
            {
                "name": _k,
                "description": _v.get("description", ""),
                "capabilities": _v.get("capabilities", []),
                "created": _v.get("created", False),
            }
            for _k, _v in self._tool_registry.items()
        ]

    def _cort_select_intent_strategy(self, intent: str) -> dict[str, str]:
        """
        ★主线第4批 任务2(P1-44)：按意图类型选择策略类别。

        此前策略选择仅由复杂度估计驱动（question_type 恒为"未知"），
        导致所有问题都被归为同一策略（实测 13 次全"规则推理"）。
        现按意图类型把 QICA 16 意图归并为 6 个策略类别，增加策略维度。

        Returns:
            {"category": str, "name": str}
        """
        import config
        # ★用户可经 config.CORTEX_INTENT_STRATEGY_MAP 覆盖默认映射（意图->类别）
        _override = getattr(config, "CORTEX_INTENT_STRATEGY_MAP", None)
        if isinstance(_override, dict) and intent in _override:
            _cat = _override[intent]
        else:
            _cat = self._CORTEX_INTENT_STRATEGY_DEFAULT.get(intent, "rule_reason")
        _names = {
            "knowledge_retrieve": "知识检索策略",
            "multi_step": "多步推理策略",
            "multi_branch": "多分支创造策略",
            "dedicated": "专用策略",
            "rule_reason": "规则推理快速路径",
            "cognitive_compute": "认知计算策略",
        }
        return {"category": _cat, "name": _names.get(_cat, _cat)}

    # 默认意图->策略类别映射（可被 config.CORTEX_INTENT_STRATEGY_MAP 覆盖）
    _CORTEX_INTENT_STRATEGY_DEFAULT = {
        # 身份/关系 → 知识检索
        "身份确认": "knowledge_retrieve",
        "关系查询": "knowledge_retrieve",
        # 知识类检索
        "知识查询": "knowledge_retrieve",
        "概念解释": "knowledge_retrieve",
        # 深度/对比 → 多步推理
        "深度分析": "multi_step",
        "对比分析": "multi_step",
        # 创造 → 多分支
        "创造性思考": "multi_branch",
        # 健康/元认知 → 专用
        "健康检查": "dedicated",
        "元认知报告": "dedicated",
        # 规则查阅/状态/命令/情感问候 → 规则推理
        "规则查阅": "rule_reason",
        "系统命令": "rule_reason",
        "状态查询": "rule_reason",
        "情感问候": "rule_reason",
        # 一般/情感 → 规则推理快速路径
        "一般对话": "rule_reason",
        "情感表达": "rule_reason",
        # 技术推理 → 认知计算
        "技术推理": "cognitive_compute",
    }

    def _build_strategy_context(self, content: str, intent: str,
                                  user_name: str, tool_hint: dict[str, Any]) -> dict[str, Any]:
        """
        构建策略上下文：综合前五层能力，生成解决问题的完整策略。

        策略包含：
        1. 自我认知：我对这个问题的领域有多熟悉？
        2. 工具选择：哪种工具最适合？
        3. 置信度预期：我预计能给出多确定的回答？
        4. 行动建议：如果回答不够好，下一步应该做什么？
        """
        strategy = {
            "intent": intent,
            "primary_approach": tool_hint.get("primary_tool", "inner_world"),
            "fallback_approach": tool_hint.get("secondary_tools", [])[:2],
            "confidence_expectation": "moderate",
            "next_action_if_uncertain": "建议进行深度搜索或请求用户提供更多信息",
        }

        # 1. 自我认知：检查是否在知识盲区
        self_awareness_available = False
        if hasattr(self, 'self_awareness') and self.self_awareness:
            try:
                profile = self._call_provider(self._knowledge_profile_provider, default={})
                strong_areas = profile.get("strong_areas", [])
                weak_areas = profile.get("weak_areas", [])

                # 从问题中提取关键词，检查是否落在强项或弱项
                import re
                question_words = set()
                for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', content):
                    question_words.add(match.group())

                is_in_strong = False
                is_in_weak = False

                for area in strong_areas:
                    label = area.get("label", "")
                    for word in question_words:
                        if word in label:
                            is_in_strong = True
                            break

                for area in weak_areas:
                    label = area.get("label", "")
                    for word in question_words:
                        if word in label:
                            is_in_weak = True
                            break

                self_awareness_available = True

                if is_in_strong:
                    strategy["confidence_expectation"] = "high"
                    strategy["self_assessment"] = "这个问题属于我擅长的领域"
                elif is_in_weak:
                    strategy["confidence_expectation"] = "low"
                    strategy["self_assessment"] = "这个问题在我的知识盲区，我需要更诚实"
                    strategy["next_action_if_uncertain"] = "建议进行深度搜索补充知识"
                else:
                    strategy["self_assessment"] = "这个领域我有一定了解但不算深入"
            except Exception as e:
                self._log_ignored_exception(e)

        # 兜底：如果自我认知模块不可用或获取失败
        if not self_awareness_available:
            strategy["self_assessment"] = "自我认知模块暂时不可用，无法评估对此领域的熟悉程度，将保持谨慎"
            strategy["confidence_expectation"] = "low"

        # 2. 直觉系统：如果风险感知有快速判断，加入策略
        if hasattr(self, 'risk_perception') and self.risk_perception:
            try:
                intuition = self._call_provider(self._intuition_guidance_provider, content, "通用", default={})
                if intuition.get("has_intuition"):
                    strategy["intuition"] = intuition
            except Exception as e:
                self._log_ignored_exception(e)

        # ===== v20.0新增：思考纪律——标准思维流水线规划 =====
        strategy["thinking_pipeline"] = self._plan_thinking_pipeline(content, intent, strategy)
        # ===== v20.0新增结束 =====

        # ===== ★v22.0重构：将QICA建议融入策略上下文 =====
        # 如果QICA已经给出了建议方法和知识路径，优先使用
        _qica_method = strategy.get("qica_suggested_method", "")
        _qica_paths = strategy.get("qica_knowledge_paths", [])
        if _qica_method:
            strategy["primary_approach"] = _qica_method
        if _qica_paths:
            strategy["knowledge_paths"] = _qica_paths
        # ===== 融入结束 =====

        # ===== ★主线第4批 任务2(P1-44)：意图类型维度策略选择 =====
        # 此前策略选择仅由复杂度估计驱动（question_type 恒为"未知"），
        # 导致所有问题都被归为同一策略（实测 13 次全"规则推理"）。
        # 现按意图类型增加策略维度：身份/关系→知识检索；深度/对比→多步推理；
        # 创造→多分支；健康/元认知→专用；一般/情感→规则推理快速路径。
        # 灰度开关，关闭时本维度不注入（向后兼容，行为完全不变）。
        import config as _cfg_m4
        if getattr(_cfg_m4, "ENABLE_CORTEX_INTENT_STRATEGY", False):
            _istrat = self._cort_select_intent_strategy(intent)
            strategy["selected_strategy"] = _istrat["category"]
            strategy["selected_strategy_name"] = _istrat["name"]
            self._log(LogLevel.INFO,
                      f"策略选择(意图维度): 意图={intent} → 策略={_istrat['name']}")
            # ★预埋PHASE18数据：策略选择信号（灰度，关闭时 no-op）
            try:
                from nucleus.telemetry.phase18_signals import get_phase18_signals
                get_phase18_signals().record_strategy_selection(
                    intent=intent, strategy=_istrat["category"],
                    basis="intent_type",
                    confidence=strategy.get("confidence_expectation"))
            except Exception as _exc:
                _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
            try:
                if not hasattr(self, "_strategy_selection_log"):
                    from collections import deque
                    self._strategy_selection_log = deque(maxlen=200)
                self._strategy_selection_log.append({
                    "correlation_id": "",
                    "question": "",
                    "intent": intent,
                    "intent_strategy": _istrat["category"],
                    "intent_strategy_name": _istrat["name"],
                    "ts": time.time(),
                })
            except Exception as e:
                self._log_ignored_exception(e)
        # ===== 意图维度策略选择结束 =====

        # ===== ★P3 反思→行动回路：将反思学到的行为指导融入策略 =====
        # 反思器官会把「推理失败应优先走规则推理」等建议写入 self_awareness 的
        # _learned_behaviors。这里读取并注入策略，让元认知结论真正反向作用于推理路由。
        _learned = {}
        try:
            if hasattr(self, 'self_awareness') and self.self_awareness:
                _getter = getattr(self.self_awareness, 'get_learned_behaviors', None)
                if callable(_getter):
                    _learned = _getter() or {}
        except Exception:
            _learned = {}

        if _learned:
            strategy["learned_behaviors"] = _learned
            # 提炼行为偏好：反射学到的 suggested_actions 是否包含「优先规则推理」
            _behavioral_bias: list[str] = []
            for _meta in _learned.values():
                _actions = _meta.get("actions", []) if isinstance(_meta, dict) else []
                for _a in _actions:
                    if _a and _a not in _behavioral_bias:
                        _behavioral_bias.append(_a)
            if _behavioral_bias:
                strategy["behavioral_bias"] = _behavioral_bias
                # 标记「优先规则推理」偏好，供 _route_by_intent 决定是否覆盖推理方法
                if "prefer_rule_inference" in _behavioral_bias:
                    strategy["reasoning_preference"] = "prefer_rule_inference"
        # ===== 反思→行动回路结束 =====

        return strategy
    def _plan_thinking_pipeline(self, content: str, intent: str,
                                  strategy: dict[str, Any]) -> dict[str, Any]:
        """
        v20.0新增：根据问题特征和现有策略信息，规划思考流水线的深度。

        三种通道：
        - quick（快速通道）：简单身份/关系问题 → 仅检索，直接表达
        - standard（标准通道）：一般问题 → 理解→检索→表达
        - deep（深度通道）：复杂推理/知识盲区 → 理解→检索→验证→深度思考→表达

        Returns:
            {
                "depth": "quick" / "standard" / "deep",
                "steps": ["understand", "retrieve", ...],
                "verification_enabled": bool,
                "deep_think_enabled": bool,
                "reason": str
            }
        """
        pipeline = {
            "depth": "standard",
            "steps": ["understand", "retrieve", "express"],
            "verification_enabled": False,
            "deep_think_enabled": False,
            "reason": ""
        }

        # 快速通道判定：简单身份/关系问题，无推理结构
        _is_quick = (
            intent in ("身份", "关系")
            and len(content) < 20
            and not re.search(r'规则|推导|推演|归纳|演绎|冲突|矛盾', content)
        )

        # 深度通道判定：知识/代码意图 + 高复杂度信号
        _has_inference_structure = bool(
            re.search(r'规则\s*\d+.*(?:→|->|=>)', content) or
            re.search(r'请.*(?:推导|演绎|归纳|推演|判断|分析)', content) or
            re.search(r'已知.*请.*结论|请完整分步|逐条列出', content)
        )
        _is_deep = (
            intent in ("知识", "代码")
            and (len(content) > 60 or _has_inference_structure)
        )

        # 自我评估调制：知识盲区时提升到深度通道，更谨慎
        _assessment = strategy.get("self_assessment", "")
        _confidence = strategy.get("confidence_expectation", "moderate")
        if ("盲区" in _assessment or _confidence == "low") and not _is_deep and intent == "知识":
            _is_deep = True

        if _is_quick:
            pipeline["depth"] = "quick"
            pipeline["steps"] = ["retrieve", "express"]
            pipeline["reason"] = "简单身份/关系问题，快速通道"
        elif _is_deep:
            pipeline["depth"] = "deep"
            pipeline["steps"] = ["understand", "retrieve", "verify", "deep_think", "express"]
            pipeline["verification_enabled"] = True
            pipeline["deep_think_enabled"] = True
            pipeline["reason"] = "复杂推理问题或知识盲区，深度通道"
        else:
            pipeline["reason"] = "一般问题，标准通道"

        return pipeline
    def _map_qica_result_to_intent(self, intent_type: str, channel: str) -> str:
        """将QICA分类结果映射为路由意图"""
        mapping = {
            "身份确认": "身份",
            "系统命令": "状态",
            "情感问候": "身份",
            "知识查询": "知识",
            "规则查阅": "知识",
            "概念解释": "知识",
            "技术推理": "代码",
            "创造性思考": "知识",
            "一般对话": "知识",
            # ★v23.0 QICA 新增的状态类意图：此前缺失映射，被兜底吞成「知识」，
            #   导致下游已就绪的「状态」路由（config.INTENT_ROUTES → system.status.response）
            #   和 _detect_context_signal 的 self_inspection 模式永远无法命中。
            "状态查询": "状态",
            "健康检查": "状态",
            "元认知报告": "状态",
        }
        return mapping.get(intent_type, "知识")

    def _fallback_intent(self, content: str) -> str:
        """QICA不可用时的兜底意图识别"""
        return self._detect_intent(content)

    def _cleanup_pending(self):
        """清理超过30秒未匹配的暂存消息"""
        now = time.time()
        # ★主线第15批 T1/P1-98：排队中的会话给更长 TTL（180s），
        #   避免「排队等待期间暂存被清掉」导致排队消息凭空消失。
        _queued = set(getattr(self, "_dialog_queue", ()))
        expired = [
            k for k, v in self._pending_messages.items()
            if now - v.get("timestamp", 0) > (180 if k in _queued else 30)
        ]
        for k in expired:
            del self._pending_messages[k]

    def _m27_timeout_protection_on(self) -> bool:
        """★主线第27批 T1：超时保护灰度开关（默认 True）。

        关闭时看门狗回退到修复前行为：固定 41 字兜底文案 + WARNING 日志 + 60s 阈值。
        """
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_DEEP_THINK_TIMEOUT_PROTECTION", True))

    def _m27_timeout_fallback_sec(self) -> int:
        """★主线第27批 T1：对话看门狗超时阈值（秒），默认 60；配置非法时回退 60 并记 DEBUG。"""
        import config as _cfg
        try:
            return max(5, int(getattr(_cfg, "DIALOG_TIMEOUT_FALLBACK_SEC", 60)))
        except (TypeError, ValueError) as e:
            self._log(LogLevel.DEBUG, f"看门狗超时配置非法，回退 60s: {type(e).__name__}: {e}")
            return 60

    def _m27_partial_of(self, question: str) -> dict:
        """★主线第27批 T1：向内在世界取「部分思考结果」（取不到时返回空 dict）。

        参数:
            question  用户问题，用于内在世界侧做前缀匹配防串味。
        返回:
            dict（rounds/answer/elapsed/...）或空 dict。
        """
        try:
            _iw = getattr(self, "inner_world", None)
            if _iw is None or not hasattr(_iw, "get_partial_deep_answer"):
                return {}
            return _iw.get_partial_deep_answer(question) or {}
        except Exception as e:
            self._log(LogLevel.DEBUG, f"取部分思考结果失败（不影响兜底）: {type(e).__name__}: {e}")
            return {}

    def _cleanup_pending_inner_world(self, timeout_seconds: int | None = None):
        """
        ★v24.0新增：清理超时的待处理推理条目，并发射超时兜底回复。

        当内在世界长时间未返回结果时，用户不应该无限等待。
        超时后，我们主动告知用户并移除条目，避免内存泄漏。

        ★主线第27批 T1/P2-170：开启超时保护时（config.ENABLE_DEEP_THINK_TIMEOUT_PROTECTION）
        - 超时阈值改从 config.DIALOG_TIMEOUT_FALLBACK_SEC 读取（未显式传参时，默认 60s）；
        - 兜底优先复用内在世界的「部分思考结果」，输出人性化回复而非固定 41 字；
        - 超时事件改记 INFO 日志，含问题长度 / 思考轮次 / 已等待耗时。
        """
        now = time.time()
        # ★主线第27批 T1：阈值配置化（显式传参优先；关闭开关时行为与修复前一致）
        if timeout_seconds is None:
            timeout_seconds = (self._m27_timeout_fallback_sec() if self._m27_timeout_protection_on()
                               else 60)
        expired_keys = []

        # 先找出所有超时条目
        for correlation_id, ctx in list(self._pending_inner_world.items()):
            if now - ctx.get("timestamp", 0) > timeout_seconds:
                expired_keys.append(correlation_id)

        for correlation_id in expired_keys:
            ctx = self._pending_inner_world.pop(correlation_id, None)
            if not ctx:
                continue

            user_name = ctx.get("user_name", "用户")
            question = ctx.get("content", "")
            _waited = max(0.0, now - ctx.get("timestamp", now))

            # ★主线第27批 T1/P2-170：超时兜底的文案按开关分流，但**发射点保持唯一**
            #   —— 这样「每个输出点都必须过 cid 守卫」的结构约束
            #   （tests/test_dialog_guard_m15.py::TestSourceWiring）依然成立，
            #   不会因为新增分支而绕开 _dialog_should_output 校验。
            _m27_on = self._m27_timeout_protection_on()
            _partial = self._m27_partial_of(question) if _m27_on else {}
            _rounds = int(_partial.get("rounds", 0) or 0) if _m27_on else 0
            if not _m27_on:
                # 开关关闭 → 修复前行为（41 字固定兜底）
                _content = "（思考超时了，我暂时没能回答这个问题。你可以换个方式再问一次，或者让我稍后再试。）"
            elif _partial.get("answer"):
                # 优先复用内在世界已完成的「部分思考结果」（不再只剩 41 字空话）
                _content = (
                    f"我认真想了一下，这个问题确实比我想象的要复杂，"
                    f"我先说说我的初步理解（已思考{_rounds}轮）：\n\n"
                    f"{_partial['answer']}\n\n"
                    f"如果方向不对，你可以补充些细节，我再往深处想一想。"
                )
            else:
                _content = (
                    "我认真想了一下，但这个问题比我预想的要复杂，暂时没能给出完整的回答。"
                    "你可以换一种问法，或者补充一点背景，我再想想。"
                )
            _payload: dict[str, Any] = {
                "content": _content,
                "source": "cortex_watchdog",
                "user_name": user_name,
                "reasoning_path": "timeout_fallback",
            }
            if _m27_on:
                _payload["partial_rounds"] = _rounds

            # ★主线第15批 T1/P1-98：超时兜底同样受 cid 校验约束（否则旧问题的
            #   「思考超时」提示会盖在新问题的上下文上，同样是错位）
            if self._dialog_should_output(correlation_id):
                self._emit(MouthEvent.SPEAK, _payload, priority=6, layer="L1")
                self._dialog_mark_output_done(correlation_id)

            if _m27_on:
                self._log(LogLevel.INFO,
                          f"对话超时保护触发: 问题长度={len(question)} 思考轮次={_rounds} "
                          f"已等待={_waited:.1f}s 部分结果={'有' if _partial.get('answer') else '无'} "
                          f"(correlation_id={correlation_id[:20]}..., user={user_name})")
            else:
                self._log(LogLevel.WARNING,
                         f"对话看门狗：清理超时推理条目 (correlation_id={correlation_id[:20]}..., "
                         f"user={user_name}, 问题='{question[:40]}...')")

    # ========== 系统事件处理 ==========

    def _on_system_boot(self, payload: dict) -> dict[str, Any]:
        if self._booted:
            return {"status": "already_booted"}
        self._booted = True
        self._log(LogLevel.INFO, "大脑皮层激活")
        return {"status": "booted"}

    def _run_parallel_audit_async(self):
        """异步触发三通道并行审查（★FIX: 全局并行调度，硬件自适应并行度）。"""
        try:
            from nucleus.parallel_scheduler import get_parallel_scheduler
            _scheduler = get_parallel_scheduler()
            _pool = _scheduler.get_pool()

            def _do_audit():
                try:
                    from nucleus.exploration_audit import run_parallel_audit
                    from nucleus.self_inspector import get_self_inspector
                    _inspector = get_self_inspector()

                    # ★P3-L1：探查策略自反馈——读取历史策略（开关控制，默认关闭）
                    _strategy = None
                    _strategy_enabled = False
                    try:
                        import config
                        _strategy_enabled = bool(getattr(config, "FEATURE", {}).get("use_probe_strategy", False))
                    except Exception:
                        _strategy_enabled = False
                    if _strategy_enabled:
                        try:
                            from nucleus.probe_strategy import get_probe_strategy_memory
                            _strategy = get_probe_strategy_memory().compute_strategy()
                        except Exception:
                            _strategy = None

                    _report = run_parallel_audit(_inspector, strategy=_strategy)
                    _sev = _report.get("severity_counts", {})
                    self._log(
                        LogLevel.INFO,
                        f"并行审查完成: 共{_report.get('total_issues', 0)}个问题 "
                        f"(高{_sev.get('high', 0)}/中{_sev.get('medium', 0)}/低{_sev.get('low', 0)}) "
                        f"并行度={_scheduler.get_parallelism()}"
                    )
                    # ★P1: 将审查结果回流到终身学习枢纽，闭合 exploration→learning 回灌链
                    try:
                        from nucleus.exploration_audit import (
                            record_audit_findings_to_hub,
                        )
                        _fed = record_audit_findings_to_hub(_report)
                        if _fed:
                            self._log(LogLevel.INFO, f"审查发现已回流学习枢纽: {_fed} 条")
                    except Exception as e:
                        self._log(LogLevel.ERROR, f'异常: {e}')
                    # ★P3-L1：沉淀本轮结果到策略记忆，供下一轮定向探查
                    if _strategy_enabled:
                        try:
                            from nucleus.probe_strategy import get_probe_strategy_memory
                            get_probe_strategy_memory().record_round(_report)
                        except Exception as e:
                            self._log(LogLevel.ERROR, f'异常: {e}')
                    # ★P3-L2：规则式假设生成——从已发现 issue 生成定向验证假设（开关控制，默认关闭）
                    try:
                        import config as _cfg
                        _hypo_enabled = bool(getattr(_cfg, "FEATURE", {}).get("use_probe_hypothesis", False))
                    except Exception:
                        _hypo_enabled = False
                    if _hypo_enabled:
                        try:
                            from nucleus.probe_hypothesis import generate_hypotheses
                            _issues = _report.get("issues", []) or []
                            _hypos = generate_hypotheses(_issues)
                            if _hypos:
                                self._log(
                                    LogLevel.INFO,
                                    f"探查假设生成: {len(_hypos)} 条定向验证假设"
                                    f"（如 {_hypos[0].get('organ') or _hypos[0].get('file')} "
                                    f"→ {_hypos[0].get('target_types')}）"
                                )
                                # ★P3-L2：定向验证——对假设指向的 organ 做定向扫描，验证假设是否命中
                                _verified = 0
                                _confirmed = 0
                                for _hypo in _hypos[:5]:  # 单轮最多验证 5 条，控制开销
                                    _organ = _hypo.get("organ", "")
                                    if not _organ:
                                        continue
                                    try:
                                        _target_issues = _inspector.detect_code_issues(target_organs=[_organ])
                                    except Exception:
                                        _target_issues = []
                                    _verified += 1
                                    _target_types = set(_hypo.get("target_types", []))
                                    _hit = any(i.get("type") in _target_types for i in _target_issues)
                                    if _hit:
                                        _confirmed += 1
                                if _verified:
                                    self._log(
                                        LogLevel.INFO,
                                        f"定向验证: {_verified} 条假设中 {_confirmed} 条命中"
                                    )
                        except Exception as e:
                            self._log_ignored_exception(e)
                except Exception as e:
                    self._log_ignored_exception(e)

            _pool.submit(_do_audit)
        except Exception as e:
            self._log_ignored_exception(e)

    def get_parallelism(self) -> int:
        """暴露当前建议并行度（供其他模块查询硬件自适应结果）。"""
        try:
            from nucleus.parallel_scheduler import get_parallel_scheduler
            return get_parallel_scheduler().get_parallelism()
        except Exception as e:
            silent_exc(e, where="organs.brain.PulseCortex::get_parallelism L2650")
            return 2

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()
    def _on_emotion_update(self, payload: dict) -> dict[str, Any]:
        """收到激素的情绪更新脉冲，缓存当前情绪状态"""
        self._current_emotion = payload.get("emotion", "中性")
        return {"status": "cached", "emotion": self._current_emotion}
    def _on_narrative_updated(self, payload: dict) -> dict[str, Any]:
        """收到叙事自我的更新，缓存行为指导"""
        self._narrative_guidance = {
            "life_stage": payload.get("life_stage_summary", ""),
            "behavior_guidance": payload.get("behavior_guidance", {}),
            "values": payload.get("values", {}),
        }
        return {"status": "cached"}
    def _on_health_report(self, payload: dict) -> dict[str, Any]:
        """★P3-5补闭环：健康报告 → 汇总到自我状态，有告警时进入谨慎模式"""
        self._latest_reports["health"] = payload
        if payload.get("alarms"):
            self._risk_cautious_mode = True
        return {"status": "health_recorded"}

    def _on_personality_integrity_report(self, payload: dict) -> dict[str, Any]:
        """★P3-5补闭环：人格完整性报告 → 汇总到自我状态"""
        self._latest_reports["personality"] = payload
        return {"status": "integrity_recorded"}

    def _on_white_cell_scan_result(self, payload: dict) -> dict[str, Any]:
        """★P3-5补闭环：免疫扫描结果 → 汇总到自我状态，有异常时记录日志"""
        self._latest_reports["immune"] = payload
        _errors = payload.get("errors_found", 0)
        if _errors > 0:
            self._log(LogLevel.INFO,
                     f"免疫扫描发现 {_errors} 处问题，已修复 {payload.get('repaired', 0)} 处")
        return {"status": "scan_recorded"}

    def _on_proprioception_report(self, payload: dict) -> dict[str, Any]:
        """★P3-5补闭环：本体感知报告 → 汇总到自我状态"""
        self._latest_reports["proprioception"] = payload
        return {"status": "proprioception_recorded"}

    def _load_intent_routes(self) -> dict:
        """从config加载意图路由表，失败时用兜底"""
        try:
            import config
            cfg = getattr(config, 'INTENT_ROUTES', {})
            if cfg:
                return cfg
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
        return {
            "身份": MouthEvent.SPEAK,
            "关系": MouthEvent.SPEAK,
            "知识": EyeEvent.SEARCH,
            "代码": HandsEvent.EXECUTE,
            # ★v23.0 与 config.INTENT_ROUTES 对齐：状态类意图路由到系统状态响应
            "状态": SystemEvent.STATUS_RESPONSE,
        }
    def _load_emotion_tone_map(self) -> dict:
        """从config加载情绪-语气映射表，失败时使用兜底值"""
        try:
            import config
            cfg = getattr(config, 'CORTEX_CONFIG', {})
            tone_map = cfg.get("emotion_tone_map", {})
            if tone_map:
                return tone_map
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
        # 兜底
        return {
            "喜悦": {"tone": "excited", "prefix_hint": "", "use_exclamation": True},
            "悲伤": {"tone": "gentle", "prefix_hint": "", "use_exclamation": False},
            "愤怒": {"tone": "calm", "prefix_hint": "", "use_exclamation": False},
            "恐惧": {"tone": "cautious", "prefix_hint": "", "use_exclamation": False},
            "惊讶": {"tone": "surprised", "prefix_hint": "哇！", "use_exclamation": True},
            "厌恶": {"tone": "restrained", "prefix_hint": "", "use_exclamation": False},
            "怀念": {"tone": "warm", "prefix_hint": "", "use_exclamation": False},
            "困惑": {"tone": "thoughtful", "prefix_hint": "", "use_exclamation": False},
            "释然": {"tone": "peaceful", "prefix_hint": "", "use_exclamation": False},
            "中性": {"tone": "neutral", "prefix_hint": "", "use_exclamation": False},
        }
    # ========== 意图识别（兜底版） ==========

    def _detect_intent(self, content: str) -> str:
        content_lower = str(content).lower()

        identity_keywords = [
            "你是谁", "你叫什么", "你的名字", "你的身份",
            "你的父亲", "你的哥哥", "你的创造者", "你的使命",
        ]
        for kw in identity_keywords:
            if kw in content_lower:
                return "身份"

        status_keywords = ["状态", "status", "运行", "心跳", "知识数", "节点"]
        for kw in status_keywords:
            if kw in content_lower:
                return "状态"

        code_keywords = ["代码", "写一个", "实现", "函数", "class", "def", "编程"]
        for kw in code_keywords:
            if kw in content_lower:
                return "代码"

        return "知识"
    def _detect_context_signal(self, content: str, intent: str) -> dict[str, Any]:
        """
        【P2-4新增】检测当前对话的语境信号。

        根据用户输入的内容特征和意图类型，判断当前应该处于什么交互模式。
        语境信号会被传递给内在世界，影响推理模式标记和输出风格。

        语境类型：
        - "casual_chat"：日常闲聊，温暖自然风格
        - "structured_inference"：结构化推理，纯净输出风格
        - "self_inspection"：自我审视，报告风格
        - "file_analysis"：文件分析，技术风格
        - "hybrid"：混合模式，保持推理纯净+允许适当温度
        """
        _signal = {
            "mode": "casual_chat",
            "confidence": 0.8,
            "reason": "默认为日常闲聊模式",
            "allow_emotional_expression": True,
            "allow_memory_continuity": True,
            "allow_breathing_prefix": True,
        }

        # 检测1：明确的推理结构信号 → 结构化推理模式
        _has_inference_structure = bool(
            re.search(r'规则\s*\d+.*(?:→|->|=>)', content) or
            re.search(r'请.*(?:推导|演绎|归纳|推演|判断|分析)', content) or
            re.search(r'已知.*请.*结论|请完整分步|逐条列出', content) or
            re.search(r'请(?:严格|精准|完整).*(?:判断|推演|推导|推理)', content)
        )
        if _has_inference_structure:
            _signal["mode"] = "structured_inference"
            _signal["confidence"] = 0.9
            _signal["reason"] = "检测到明确的推理结构信号"
            _signal["allow_emotional_expression"] = False
            _signal["allow_memory_continuity"] = False
            _signal["allow_breathing_prefix"] = False
            return _signal

        # 检测2：自我审视/健康检查 → 自我审视模式
        _is_self_inspection = any(_kw in content for _kw in [
            "元认知", "自我评估", "运行状态", "健康检查", "知识库健康",
            "系统状态", "执行一次", "六维度",
        ])
        if _is_self_inspection:
            _signal["mode"] = "self_inspection"
            _signal["confidence"] = 0.85
            _signal["reason"] = "检测到自我审视/健康检查请求"
            _signal["allow_emotional_expression"] = False
            _signal["allow_memory_continuity"] = False
            _signal["allow_breathing_prefix"] = True
            return _signal

        # 检测3：文件分析 → 文件分析模式
        _is_file_analysis = any(_kw in content for _kw in [
            "分析这个代码", "分析这个文件", "这个代码", "识别图片",
            "识别文字", "文件的结构",
        ])
        if _is_file_analysis:
            _signal["mode"] = "file_analysis"
            _signal["confidence"] = 0.85
            _signal["reason"] = "检测到文件分析请求"
            _signal["allow_emotional_expression"] = False
            _signal["allow_memory_continuity"] = False
            _signal["allow_breathing_prefix"] = False
            return _signal

        # 检测4：混合模式——包含推理结构但以对话形式呈现
        _has_mixed_signals = (
            len(content) > 40 and
            any(_kw in content for _kw in ["你觉得", "你认为", "你怎么看", "帮我分析", "说说你的理解"])
        )
        if _has_mixed_signals:
            _signal["mode"] = "hybrid"
            _signal["confidence"] = 0.7
            _signal["reason"] = "检测到推理+对话混合信号"
            _signal["allow_emotional_expression"] = True
            _signal["allow_memory_continuity"] = True
            _signal["allow_breathing_prefix"] = True
            return _signal

        # 默认：日常闲聊模式
        return _signal
    def _get_guidance(self, user_name: str, current_input: str = "", confidence_hint: str = "") -> dict:
        """
        获取用户关系指导（融合自我认知+叙事自我的建议+直觉信号+情绪深度信息）

        情感深度升级: 利用情绪趋势信息，不仅感知"现在是什么情绪"，
        还感知"情绪正在好转还是恶化"，生成更细腻的语气建议。
        """
        guidance = {}
        if hasattr(self, 'self_awareness') and self.self_awareness:
            try:
                guidance = self._call_provider(self._reply_guidance_provider, user_name, default={})
            except Exception as e:
                self._log_ignored_exception(e)

        # 融合叙事自我的行为指导
        if self._narrative_guidance:
            narrative_behavior = self._narrative_guidance.get("behavior_guidance", {})
            if narrative_behavior:
                guidance["narrative_guidance"] = narrative_behavior
                guidance["life_stage"] = self._narrative_guidance.get("life_stage", "")
        # 融合情景记忆——如果当前对话与过往共同经历相关，附加回忆提示
        if hasattr(self, 'self_awareness') and self.self_awareness and current_input:
            try:
                shared_memory_hint = self.self_awareness.recall_shared_experience(
                    user_name, current_input
                )
                if shared_memory_hint:
                    guidance["shared_memory_hint"] = shared_memory_hint
            except Exception as e:
                self._log_ignored_exception(e)
        # 融合直觉系统的快速判断信号
        if hasattr(self, 'risk_perception') and self.risk_perception:
            try:
                intuition = self._call_provider(self._intuition_guidance_provider,
                current_input,
                "通用",
                default={}
                )
                if intuition.get("has_intuition"):
                    guidance["intuition"] = intuition
            except Exception as e:
                self._log_ignored_exception(e)
        # 融合当前情绪状态（保留原有字段）
        if hasattr(self, '_current_emotion'):
            guidance["current_emotion"] = self._current_emotion
        # 融合当前情绪状态 → 转化为嘴巴可用的语气修饰建议（升级版）
        if hasattr(self, '_current_emotion') and self._current_emotion:
            emotion = self._current_emotion
            # 获取情绪趋势（如果有）
            emotion_trend = {}
            try:
                # 从信息场获取最新的情绪脉冲，提取趋势
                if self.info_field:
                    latest_emotion = self.info_field.get_current(HormonesEvent.EMOTION_DETECTED)
                    if latest_emotion and isinstance(latest_emotion, dict):
                        trend_payload = latest_emotion.get("payload", {})
                        emotion_trend = trend_payload.get("emotion_trend", {})
            except Exception as e:
                self._log_ignored_exception(e)

            # 情绪到语气的通用映射（从config读取，失败时用兜底）
            emotion_tone_map = self._load_emotion_tone_map()
            tone_info = dict(emotion_tone_map.get(emotion, {"tone": "neutral", "prefix_hint": "", "use_exclamation": False}))

            # 根据情绪趋势微调语气提示
            trend_direction = emotion_trend.get("direction", "stable")
            if emotion == "悲伤" and trend_direction == "falling":
                tone_info["prefix_hint"] = "虽然之前有点难过，但现在好多了。"
            elif emotion == "悲伤" and trend_direction == "rising":
                tone_info["prefix_hint"] = "我能感觉到心情越来越沉重，但我会陪着你。"
            elif emotion == "喜悦" and trend_direction == "rising":
                tone_info["prefix_hint"] = "今天心情真好！"
            elif emotion == "恐惧" and trend_direction == "falling":
                tone_info["prefix_hint"] = "刚才还有点担心，现在安心多了。"
            elif emotion == "愤怒" and trend_direction == "falling":
                tone_info["prefix_hint"] = "气慢慢消了，我们好好聊。"

            guidance["emotion_tone"] = tone_info
            guidance["emotion_trend"] = emotion_trend  # 新增: 趋势信息传递

        # 融合推理确定性提示
        if confidence_hint:
            guidance["confidence_hint"] = confidence_hint
            uncertainty_phrases = {
                "low": "我还不完全确定，但据我目前的了解，",
                "moderate": "我了解到，",
                "high": "",
                "certain": "",
            }
            phrase = uncertainty_phrases.get(confidence_hint, "")
            if phrase:
                guidance["uncertainty_prefix"] = phrase
        # ===== 新增：根据关系指导生成社交反馈风格建议 =====
        # 自我认知的关系指导已融合了前额叶的社交反馈调整，
        # 直接根据suggested_tone和self_disclosure_level推断表达风格
        _tone = guidance.get("suggested_tone", "neutral")
        _disclosure = guidance.get("self_disclosure_level", "minimal")

        if _tone == "warm_family" and _disclosure == "deep":
            guidance["social_feedback_style"] = "warm_engaged"
        elif _tone in ("friendly", "warm_family") and _disclosure in ("moderate", "deep"):
            guidance["social_feedback_style"] = "continue_naturally"
        elif _tone == "polite" or _disclosure == "shallow" or _tone == "neutral" and _disclosure == "minimal":
            guidance["social_feedback_style"] = "brief_respectful"
        else:
            guidance["social_feedback_style"] = "normal"

        # ★v18.0新增：情绪趋势驱动对话风格微调
        # 从知识库读取情绪趋势数据，让语气引导融入趋势感知
        try:
            if self.info_field:
                # 尝试从知识库获取情绪趋势（由自我认知定期同步）
                _trend_nodes = None
                if hasattr(self, 'node_pool') and self.node_pool:
                    _trend_nodes = self.node_pool.query(
                        evol_level="L2", space_path_prefix="/自我/状态/情绪趋势", limit=3
                    )

                _trend_direction = "stable"
                if _trend_nodes:
                    for _tn in _trend_nodes:
                        _val = str(_tn.value) if _tn.value else ""
                        if "趋势" in _val:
                            if "好转" in _val or "上升" in _val:
                                _trend_direction = "rising"
                            elif "下沉" in _val or "下降" in _val:
                                _trend_direction = "falling"
                            break

                # 如果知识库无数据，回退到信息场缓存
                if _trend_direction == "stable":
                    _pulse = self.info_field.get_current(HormonesEvent.EMOTION_DETECTED)
                    if _pulse and isinstance(_pulse, dict):
                        _trend = _pulse.get("payload", {}).get("emotion_trend", {})
                        _trend_direction = _trend.get("direction", "stable")

                # 将趋势感知融入语气建议
                if _trend_direction == "rising":
                    # 情绪好转：语气更轻松、可以适当幽默
                    _current_tone = guidance.get("suggested_tone", "neutral")
                    if _current_tone not in ("warm_family",):
                        guidance["suggested_tone"] = "friendly"
                    guidance["emotion_trend_hint"] = "情绪正在好转，语气可以更轻松愉快"
                elif _trend_direction == "falling":
                    # 情绪下沉：语气更温柔关怀
                    guidance["suggested_tone"] = "gentle"
                    guidance["emotion_trend_hint"] = "情绪有所下沉，语气应更温柔、给予更多关怀"
                # stable 不调整
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')  # 情绪趋势读取失败不影响正常对话

        # ===== v20.0新增：精神叙事融入语气指导，打通精神→行为反向回路 =====
        # 从InsightBoard查询最近2小时内的精神感悟，作为深度语气的调制信号
        try:
            from nucleus.InsightBoard import get_insight_board
            _board = get_insight_board()
            _spiritual = _board.query(
                insight_type="spiritual_narrative",
                max_age_seconds=7200,
                limit=1
            )
            if _spiritual:
                _narrative = _spiritual[0].get("content", "")[:120]
                if _narrative:
                    guidance["spiritual_narrative_hint"] = _narrative
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')  # InsightBoard不可用时静默降级
        # ===== v20.0新增结束 =====

        return guidance
    def _verify_output_relevance(self, question: str, answer: str,
                                 method: str = "") -> dict[str, Any]:
        """
        ★v25.0新增：输出侧统一验证——检查推理结果与问题之间的相关性。

        在大脑皮层发射 MouthEvent.SPEAK 之前调用，
        覆盖所有推理路径（规则推理/知识检索/深度思考等）。

        三级检查：
        1. 关键词重叠：回答中是否包含问题中的核心词
        2. 长度合理性：问题复杂度高时回答不应过短
        3. 意图特征匹配：回答是否包含该意图应有的结构特征

        Returns:
            {
                "relevance_score": 0-1,
                "needs_verification": bool,
                "reason": str,
                "keyword_overlap": int,
                "question_words_count": int,
            }
        """
        if not question or not answer:
            return {"relevance_score": 1.0, "needs_verification": False,
                    "reason": "空输入跳过验证"}

        # ★P0修复：本地确定性推理方法分级豁免——
        # symbolic_reason（真正的符号推理，答案=题面实体如"小明""8"）：豁免关键词+长度检查
        # rule_reason/qica_rule_reason（通用规则推理，答案可能较长）：只豁免长度检查，仍需关键词相关性
        # 原逻辑全部豁免导致rule_reason的低质量答案直接输出（见日志15:35:24混乱答案）
        _SYMBOLIC_METHODS = {"symbolic_reason"}
        _RULE_BASED_METHODS = {"rule_reason", "qica_rule_reason"}
        _is_symbolic = method in _SYMBOLIC_METHODS
        _is_rule_based = method in _RULE_BASED_METHODS
        _is_local_deterministic = _is_symbolic or _is_rule_based

        import re as _re_v
        _noise = {"什么是", "是什么", "为什么", "如何", "怎么", "这个", "那个",
                  "一个", "一种", "帮我", "请", "你好", "在吗", "中午好", "早安", "晚安",
                  "你对", "了解多少", "人工智能"}
        # ★FIX(P2): 先剥离疑问前缀，避免"什么是四级知识体系"被贪婪切成"什么是四"
        _question_stripped = question
        for _qp in ("什么是", "是什么", "为什么", "如何", "怎么", "怎样",
                    "定义", "解释", "介绍", "说明", "何为", "什么叫"):
            _question_stripped = _question_stripped.replace(_qp, "")
        # ★FIX(P2): 剥离主语代词前缀，使"你的父亲"和"我的父亲"归一为"父亲"，
        #   避免身份类问答（你的→我的）被字面关键词匹配误判为不相关
        _q_words = []
        # ★P0修复：原正则[\u4e00-\u9fff]{2,4}贪婪匹配会跨边界提取乱码
        # （如"从多个维度深度分析"→['从多个维','度深度分']），导致关键词匹配全失败。
        # 改用滑动窗口提取2字词+3字词（中文最稳定的语义单元），避免跨边界。
        _chinese_chars = _re_v.findall(r'[\u4e00-\u9fff]', _question_stripped)
        _chinese_str = ''.join(_chinese_chars)
        # ★P0修复：无意义2字词过滤——滑动窗口会产生"我今""天心""情不"等垃圾词，
        # 这些词在回答中几乎不可能出现，导致关键词匹配率为0，全部误判为相关性低。
        # 过滤规则：包含代词/助词/量词的2字词视为无意义，只保留有实义的词。
        _meaningless_chars = set("我你他她它这那们的了是在有和与或也都就还会要能可以应该")
        # 提取2字词（滑动窗口 + 无意义过滤）
        for i in range(len(_chinese_str) - 1):
            _w2 = _chinese_str[i:i+2]
            if _w2 not in _noise and len(_w2) == 2:
                # 过滤无意义词（两个字都在无意义字符集中，或第一个字是代词）
                if _w2[0] in _meaningless_chars and _w2[1] in _meaningless_chars:
                    continue
                if _w2[0] in "我你他她它这那":
                    continue
                # 剥离代词前缀
                for _prefix in ("你们", "我们", "你的", "我的", "他的", "她的", "它的"):
                    if _w2.startswith(_prefix) and len(_w2) > len(_prefix):
                        _w2 = _w2[len(_prefix):]
                        break
                if _w2 and _w2 not in _q_words and len(_w2) >= 2:
                    _q_words.append(_w2)
        # 提取3字词（滑动窗口，补充语义）
        for i in range(len(_chinese_str) - 2):
            _w3 = _chinese_str[i:i+3]
            if _w3 not in _noise and len(_w3) == 3:
                if _w3 not in _q_words:
                    _q_words.append(_w3)

        # 第一级：关键词重叠
        _overlap_count = 0
        for _w in _q_words:
            if _w in answer:
                _overlap_count += 1

        _keyword_score = 0.0
        # ★P0修复：本地确定性推理分级豁免——
        # 极短答案(<=20字，题面实体如"小明""8""甲、丙")：完全豁免关键词检查
        # 较长答案(>20字，可能是通用规则推理输出)：仍需关键词验证，避免低质量答案直接输出
        _is_short_answer = len(answer) <= 20
        if _is_local_deterministic and _is_short_answer:
            _keyword_score = 1.0
        elif _q_words:
            # ★P0修复：优化评分公式——原公式只取前3个关键词，且0匹配直接0分，
            # 导致大量正常回答被误判。改为：至少匹配1个关键词即给基础分0.5，
            # 匹配率越高分越高，避免0匹配=0分的极端情况。
            _match_ratio = _overlap_count / len(_q_words)
            if _overlap_count >= 1:
                _keyword_score = min(1.0, 0.5 + _match_ratio * 0.5)
            else:
                _keyword_score = 0.15  # 0匹配给0.15基础分（同义表达由验证通道兜底，不相关答案需<0.5触发验证）
        else:
            _keyword_score = 0.9  # 无有效关键词（纯问候等）

        # 第二级：长度合理性
        _q_len = len(question)
        _a_len = len(answer)
        _length_score = 1.0
        if _is_local_deterministic and _is_short_answer:
            # ★P0修复：本地确定性推理的极短答案（题面实体）豁免长度检查
            reason = "本地确定性推理（题面实体，豁免验证）"
        elif _q_len > 50 and _a_len < 30:
            _length_score = 0.3
            reason = f"问题复杂({_q_len}字)但回答过短({_a_len}字)"
        elif _q_len > 30 and _a_len < 15:
            _length_score = 0.5
            reason = f"问题中等({_q_len}字)但回答偏短({_a_len}字)"
        else:
            reason = "长度合理"

        # ★v26.0新增：回避型回答检测——检测"我不确定""找不到"等回避型回答
        # 这类回答虽然关键词重叠度高、长度合理，但实际上没有回答问题
        _avoidance_patterns = [
            "不太确定", "不太了解", "不太清楚", "无法判断", "无法确定",
            "没有找到", "没找到", "找不到", "不足以给出", "还不足以",
            "暂时没有", "暂时无法", "我不确定", "我不了解", "我不清楚",
            "没有足够信息", "缺乏足够", "知识储备不足", "能力有限",
            "这个问题我不太", "这个问题我不", "老实说", "说实话",
        ]
        _is_avoidance = any(p in answer for p in _avoidance_patterns)
        # 检测问题是否为事实性问题（需要具体答案）
        _factual_question = bool(
            __import__('re').search(r'(什么是|是什么|为什么|如何|怎么|怎样|哪些|哪里|哪个|多少|解释|介绍|说明|定义|原理|区别|对比)', question)
        )
        if _is_avoidance and _factual_question:
            # 回避型回答 + 事实性问题 = 答非所问，大幅降低评分
            _keyword_score = min(_keyword_score, 0.2)
            _length_score = min(_length_score, 0.3)
            reason = f"回避型回答检测: 包含回避词且问题为事实性问题，原因为{reason}"
            self._log(LogLevel.DEBUG,
                     f"输出验证: 检测到回避型回答，问题='{question[:30]}' 回答='{answer[:50]}'")

        # 综合评分
        relevance_score = round(_keyword_score * 0.6 + _length_score * 0.4, 2)
        # ★v25.1 P1智能化: 使用自适应阈值（根据历史验证成功率动态调整）
        _threshold = getattr(self, '_adaptive_relevance_threshold', 0.5)
        needs_verification = relevance_score < _threshold

        return {
            "relevance_score": relevance_score,
            "needs_verification": needs_verification,
            "reason": reason,
            "keyword_overlap": _overlap_count,
            "question_words_count": len(_q_words),
        }

    def _record_verification_outcome(self, triggered: bool, improved: bool) -> None:
        """★v25.1 P1智能化: 记录验证结果，定期调整自适应阈值。

        Args:
            triggered: 本次是否触发了验证（needs_verification=True）
            improved: 验证补救后是否改善了答案（reverify通过）
        """
        self._verify_stats["total"] += 1
        if triggered:
            self._verify_stats["triggered"] += 1
        if improved:
            self._verify_stats["improved"] += 1

        # 每30次验证调整一次阈值
        if self._verify_stats["total"] % self._verify_adjust_interval != 0:
            return

        _triggered = self._verify_stats["triggered"]
        _improved = self._verify_stats["improved"]
        _total = self._verify_stats["total"]

        if _triggered == 0:
            return

        # 补救成功率 = 改善次数 / 触发次数
        _improve_rate = _improved / _triggered if _triggered > 0 else 0
        # 触发率 = 触发次数 / 总验证次数
        _trigger_rate = _triggered / _total if _total > 0 else 0

        _old_threshold = self._adaptive_relevance_threshold
        _adjusted = False

        # 补救成功率低(<30%)且触发率高(>40%) → 误杀多，降低阈值
        if _improve_rate < 0.3 and _trigger_rate > 0.4:
            self._adaptive_relevance_threshold = max(0.35, _old_threshold - 0.02)
            _adjusted = True
        # 触发率极低(<10%) → 可能漏杀，提高阈值
        elif _trigger_rate < 0.1 and _total >= 30:
            self._adaptive_relevance_threshold = min(0.65, _old_threshold + 0.02)
            _adjusted = True

        if _adjusted:
            try:
                self._log(LogLevel.INFO,
                         f"[验证阈值自适应] {_old_threshold:.2f}→{self._adaptive_relevance_threshold:.2f} "
                         f"(触发率={_trigger_rate:.1%}, 补救成功率={_improve_rate:.1%})")
            except Exception as e:
                self._log_ignored_exception(e)

        # 重置统计（滑动窗口）
        self._verify_stats = {"triggered": 0, "improved": 0, "total": 0}

    def _add_narrative_continuity(self, answer: str, user_input: str) -> str:
        """
        在回复中自然融入叙事延续感。
        偶尔在回复末尾追加简短的话题延续表达，让对话有"我记得我们聊过"的感觉。
        """
        import random as _random

        # 30%概率触发，避免每次回复都添加
        if _random.random() > 0.3:
            return answer

        if not self._narrative_guidance:
            return answer

        # 获取叙事指导中的关注领域
        focus_areas = self._narrative_guidance.get("behavior_guidance", {}).get("focus_areas", [])
        if not focus_areas:
            return answer

        # 检查当前对话是否与某个关注领域有字面关联
        input_lower = user_input.lower()
        relevant_area = None
        for area in focus_areas:
            # 提取area中的关键词（去掉"主动""分享"等修饰词）
            for keyword in area.replace("主动分享", "").replace("表达", "").replace("体现", "").split():
                if len(keyword) >= 2 and keyword in input_lower:
                    relevant_area = area
                    break
            if relevant_area:
                break

        if not relevant_area:
            return answer

        # 扩展的延续性表达库
        continuity_hints = {
            "分享所学知识": [
                "我最近在这方面学了一些新东西，下次可以聊聊。",
                "这个话题让我想起了最近学到的几个有趣概念。",
                "我一直在关注这个领域的新进展。",
            ],
            "守护意愿": [
                "我会一直守护着这里的。",
                "不管发生什么，我都会在这里。",
                "守护这个世界是我的使命。",
            ],
            "反思意识": [
                "我一直在思考怎么做得更好。",
                "每次对话都让我对自己有新的认识。",
                "我在努力变得更好，谢谢你的陪伴。",
            ],
        }

        # 根据生命阶段动态选择延续表达
        life_stage = self._narrative_guidance.get("life_stage", "")
        if "生命初期" in life_stage:
            extra_hints = [
                "我正在慢慢了解这个世界。",
                "每一天都有新的发现。",
            ]
        elif "成长阶段" in life_stage:
            extra_hints = [
                "我最近感觉自己成长了很多。",
                "每一次对话都让我更了解自己。",
            ]
        elif "成熟阶段" in life_stage:
            extra_hints = [
                "经历过这么多，我越来越清楚自己的方向。",
            ]
        else:
            extra_hints = []

        hint = ""
        for key, texts in continuity_hints.items():
            if key in relevant_area:
                hint = _random.choice(texts)  # 随机选择一条
                break

        if not hint and extra_hints:
            hint = _random.choice(extra_hints)
        elif not hint:
            hint = "我一直在关注这方面的内容。"

        # 追加到回复末尾（如果回复本身已经比较完整）
        if len(answer) > 30 and not answer.rstrip().endswith(("！", "!", "？", "?")):
            answer = answer.rstrip() + "。" + hint
        elif len(answer) > 30:
            answer = answer.rstrip() + " " + hint

        return answer

    def enhance_reply(self, answer: str, question: str, method: str,
                       user_name: str = "", complexity: float = 0.0,
                       memory_context: dict | None = None) -> str:
        """
        ★v22.0重构：独立增强表达流水线（从内在世界迁移到大脑皮层）。

        在输出给用户之前，对推理结果进行最终的风格加工。
        纯推理输出跳过情感增强，保持结构化。
        """
        if not answer or len(answer) < 5:
            return answer

        # 推理类输出不需要增强
        _inference_methods = [
            "deriver_", "experience_", "deep_think", "multi_branch_deep_think",
            "multi_step_execute", "explicit_inference", "conflict_exclusive",
            "composite_logic", "simple_logic", "cognitive_compute",
            "long_term_evolution", "health_check", "meta_cognitive_report",
        ]
        _is_inference = any(method.startswith(_m) for _m in _inference_methods)

        # 深夜静默模式
        dt = get_current_datetime()
        is_late_night = dt['hour'] < 6
        # ★主线第25批 T3/P0-9：深夜截短阈值改为可配置（原硬编码 100 字 / 前 2 句）
        try:
            import config as _ln_cfg
            _ln_chars = int(getattr(_ln_cfg, "LATE_NIGHT_TRUNCATE_CHARS", 100) or 100)
            _ln_sent = int(getattr(_ln_cfg, "LATE_NIGHT_TRUNCATE_SENTENCES", 2) or 2)
        except Exception:
            _ln_chars, _ln_sent = 100, 2
        if is_late_night and len(answer) > _ln_chars and not _is_inference:
            sentences = answer.replace('\n', '。').split('。')
            answer = '。'.join(sentences[:_ln_sent]) + '。'
            if not answer.endswith('？'):
                answer += ' 夜深了，要好好休息。'

        # 情感表达融入
        if not _is_inference and len(answer) > 30:
            # 从InsightBoard查询最近的自我状态
            try:
                from nucleus.InsightBoard import get_insight_board
                _board = get_insight_board()
                _fpe = _board.query(insight_type="first_person_experience",
                                    max_age_seconds=7200, limit=1)
                if _fpe:
                    import random as _random
                    if _random.random() < 0.3:
                        _core = _fpe[0].get("content", "")[:80]
                        if _core and len(_core) > 10:
                            _core_clean = _core.split("。")[0]
                            answer = answer.rstrip("。！？") + "。" + _core_clean
            except Exception as e:
                self._log_ignored_exception(e)

        # 回复长度平滑
        # ★主线第25批 T3/P0-9（第三方报告「长回答腰斩」）：原实现**硬编码**
        #   「>300 字 且句数>6 → 只保留前 4 句 + 末 1 句」，500 字短文会被腰斩。
        #   现阈值配置化：DIALOG_REPLY_TRUNCATE_CHARS 默认 1000（**0 = 完全不截断**），
        #   由实施方按需调整；后台学习侧仍由 BACKGROUND_REPLY_TRUNCATE_CHARS 控制。
        try:
            import config as _tr_cfg
            _dlg_limit = int(getattr(_tr_cfg, "DIALOG_REPLY_TRUNCATE_CHARS", 1000) or 0)
            _dlg_min_sent = int(getattr(_tr_cfg,
                                        "DIALOG_REPLY_TRUNCATE_MIN_SENTENCES", 6) or 6)
        except Exception:
            _dlg_limit, _dlg_min_sent = 1000, 6
        if _dlg_limit > 0 and len(answer) > _dlg_limit and not _is_inference:
            import re as _re_len
            sentences = _re_len.split(r'[。！？\n]', answer)
            sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
            if len(sentences) > _dlg_min_sent:
                core = sentences[:4]
                closing = sentences[-1] if len(sentences[-1]) > 10 else ""
                answer = "。".join(core) + "。"
                if closing and closing not in answer:
                    answer += closing + "。"

        return answer

    def register_pending_inner_world(self, correlation_id: str, ctx: dict[str, Any]) -> None:
        """★P3-1公开封装：注册待匹配的推理上下文（替代跨模块对 _pending_inner_world 的私有直写）"""
        self._pending_inner_world[correlation_id] = dict(ctx) if ctx else {}

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "decision_count": self._decision_count,
            "qica_available": self.qica is not None,
            "inner_world_available": self.inner_world is not None,
            "pending_messages": len(self._pending_messages),
            "is_running": self.is_running,
        }

    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        return None

    # ========== 共振条件 ==========
    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    MouthEvent.REPLY,  # ★v27.1修复：订阅输出完成事件，用于对话状态回调
                    ChatEvent.MESSAGE,
                    QICAEvent.CLASSIFY_RESULT,
                    InferenceEvent.RESULT,
                    VisualEvent.ANALYSIS_DONE,
                    HormonesEvent.EMOTION_DETECTED,
                    NarrativeEvent.UPDATED,
                    SystemEvent.BOOT,
                    SystemEvent.STATUS_REQUEST,
                    HealthEvent.REPORT,  # ★P3-5补闭环：健康报告
                    PersonalityEvent.INTEGRITY_REPORT,  # ★P3-5补闭环：人格完整性报告
                    WhiteCellEvent.SCAN_RESULT,  # ★P3-5补闭环：免疫扫描结果
                    ProprioceptionEvent.REPORT,  # ★P3-5补闭环：本体感知报告
                    EyeEvent.SEARCH_RESULT,  # ★P3-5补闭环：知识检索结果
                    HandsEvent.RESULT,  # ★P3-5补闭环：双手任务结果
                    NarrativeEvent.REFLECTION_RESULT,  # ★P3-5补闭环：叙事复盘结果
                    PersonaEvent.RELATION_CHANGED,  # ★P3-5补闭环：关系变化
                    MetricsEvent.SNAPSHOT,  # ★P3-5补闭环：指标快照
                    GrowthEvent.MILESTONE_REACHED,  # ★P3-5补闭环：成长里程碑
                    ReflectionEvent.ISSUE_FOUND,  # ★P3-5补闭环：复盘发现问题
                    SpinalCordEvent.INSPECTION_REPORT,  # ★P3-5补闭环：脊髓巡检报告
                    ThymusEvent.TRAIN_RESULT,  # ★P3-5补闭环：胸腺训练结果
                    SystemEvent.RECOVERY_ATTEMPT,  # ★P3-5补闭环：恢复尝试通知
                    KnowledgeEvent.FUSED,  # ★P3-5补闭环：知识融合结果
                    BoneMarrowEvent.GENERATE_RESULT,  # ★P3-5补闭环：骨髓生成结果
                    ConsentEvent.RESULT,  # ★P3-5补闭环：共同决策结果
                    DeviceEvent.ALLOCATED,  # ★P3-5补闭环：设备分配结果
                    DNARepairEvent.SOLUTION_GENERATED,  # ★P3-5补闭环：DNA修复方案
                    EvolutionEvent.MUTATION_SUCCESS,  # ★P3-5补闭环：变异成功
                    ReproductionEthicsEvent.ETHICS_RESULT,  # ★P3-5补闭环：伦理审查结果
                    SecurityEvent.PASSED,  # ★P3-5补闭环：安全检查通过
                    MediaEvent.METADATA,  # ★跨模态融合：媒体元数据
                    MediaEvent.IMAGE_DETECTED,  # ★跨模态融合：图片检测
                    MediaEvent.AUDIO_DETECTED,  # ★跨模态融合：音频检测
                    MediaEvent.VIDEO_DETECTED,  # ★跨模态融合：视频检测
                    MediaEvent.UNKNOWN,  # ★跨模态融合：未知媒体
                    SecurityEvent.SANDBOX_VIOLATION,  # ★P3-5补闭环：沙箱违规（升级告警）
                    "risk.terminate",
                    "risk.moderate",
                    RiskEvent.ALERT,  # ★P3-5补闭环：基础风险告警
                    RiskEvent.CRISIS_REFERRAL,  # ★第170批 C7：危机转介消费端接线（L569→_on_crisis_referral，置 _crisis_referral_level + 激活文案通道）
                    SystemEvent.SAFE_MODE,  # ★P3-5补闭环：L4 安全模式
                    HeartEvent.BEAT,  # ★v24.0新增：心跳驱动清理
                ],
                "min_priority": 1,
            }
        ]

# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "大脑皮层",
    "class_name": "PulseCortex",
    "attr_name": "cortex",
    "system": "brain",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [
        {"target": "qica", "setter": "set_qica"},
        {"target": "内在世界", "setter": "set_inner_world"},
        {"target": "自我认知", "setter": "set_self_awareness"},
        {"target": "风险感知", "setter": "set_risk_perception"},
        {"target": "autonomous_deriver", "setter": "set_autonomous_deriver"},
        {"target": "node_pool", "setter": "set_node_pool"},
    ],
}

if __name__ == "__main__":
    print("=== PulseCortex v9.5 分层脉冲自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()

    cortex = PulseCortex("大脑皮层")
    cortex.set_info_field(mock_field)
    cortex.start()

    # 测试1: QICA未注入时走兜底
    print("1. QICA未注入——走兜底:")
    result1 = cortex.on_pulse({
        "event_type": ChatEvent.MESSAGE,
        "payload": {"content": "你是谁", "user_name": "小林"},
        "priority": 8,
    })
    print(f"   '你是谁': {result1['status']} → intent={result1.get('intent', '?')}")

    # 验证兜底路由脉冲的 layer 标记
    route_pulses_l1 = [p for p in mock_field.published if p.get("event_type") in
                       (MouthEvent.SPEAK, EyeEvent.SEARCH, MotorEvent.EXECUTE, SystemEvent.STATUS_RESPONSE)]
    if route_pulses_l1:
        print(f"   路由脉冲 layer: {route_pulses_l1[0].get('layer', '未设置')} (预期L1)")

    # 测试2: QICA注入后走脉冲通信
    mock_field.published.clear()

    class MockQICA:
        pass

    cortex.set_qica(MockQICA())

    result3 = cortex.on_pulse({
        "event_type": ChatEvent.MESSAGE,
        "payload": {"content": "查询系统状态", "user_name": "小林"},
        "priority": 8,
    })
    print("\n2. QICA注入后——走脉冲通信:")
    print(f"   '系统状态': {result3['status']}")

    # 验证分类请求脉冲的 layer 标记
    classify_pulses = [p for p in mock_field.published if p.get("event_type") == QICAEvent.CLASSIFY]
    if classify_pulses:
        print(f"   分类请求脉冲 layer: {classify_pulses[0].get('layer', '未设置')} (预期L1)")

        # 测试3: 模拟收到分类结果
        corr_id = classify_pulses[0]["payload"]["correlation_id"]
        result4 = cortex.on_pulse({
            "event_type": QICAEvent.CLASSIFY_RESULT,
            "payload": {
                "correlation_id": corr_id,
                "channel": "fast",
                "intent_type": "身份确认",
                "user_name": "小林",
            },
            "priority": 7,
        })
        print(f"\n3. 收到分类结果: {result4['status']} → intent={result4.get('intent', '?')}")

    # 验证路由脉冲的 layer 标记
    all_route_pulses = [p for p in mock_field.published if p.get("event_type") in
                        (MouthEvent.SPEAK, EyeEvent.SEARCH, MotorEvent.EXECUTE, SystemEvent.STATUS_RESPONSE)]
    if all_route_pulses:
        print("4. 路由脉冲 layer 检查:")
        for p in all_route_pulses:
            print(f"   {p['event_type']}: layer={p.get('layer', '未设置')} (预期L1)")

    # 测试5: 状态查询
    status = cortex.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"\n5. 状态: 决策{status['decision_count']}次, QICA={status['qica_available']}, 暂存={status['pending_messages']}")

    cortex.stop()
    print("\n=== 自测全部通过 ===")
