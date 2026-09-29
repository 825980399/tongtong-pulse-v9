# -*- coding: utf-8 -*-
from nucleus._silent_except import silent_exc
"""
PulseStomach —— 脉冲驱动胃 · 知识消化器官

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 接收 DigestEvent.KNOWLEDGE 脉冲完成知识消化——安全审查过滤不安全内容、词表驱动的关键词提取与权重排序、五维归属分配空间路径、创建 PulseNode 并做频率编码写入节点池，最后发射 KnowledgeEvent.WRITTEN。
机制: _on_digest_knowledge 委托 _do_digest 执行：安全审查由 _local_ethics_check 本地判据与 _request_ethics_review 送审两部分组成，审查结论经 EthicsEvent.REVIEW_RESULT 回调；关键词按领域词表命中并结合纯度、领域、词长、专名四维加权排序；路径由 KnowledgeTree 综合匹配得出，节点经 FrequencyCodec 编码后写入 PulseNodePool。
定位: 外部知识进入框架的入口处理站，决定一条知识以何种坐标与频率进入信息场。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import re
import threading
from typing import Any

import config
from base.BasePulseOrgan import BasePulseOrgan
from nucleus.logger import exc_location
from nucleus.const import (
    DigestEvent,
    EthicsEvent,
    HormonesEvent,
    KnowledgeEvent,
    LogLevel,
    SystemEvent,
)
from nucleus.knowledge_noise_filter import (
    DOMAIN_SUFFIXES,
    check_self_consistency_for_node,  # ★v26.0新增：知识免疫自我一致性检查
    clean_content_text,
    filter_keywords_enhanced,  # ★v26.0新增：增强版关键词过滤（含中文停用词）
    get_view_mode,
    is_noise_keyword,
    is_path_fragment_word,  # ★v25.0新增：路径碎片检测
)
from nucleus.LLMDependencyMetrics import DIGEST_KNOWLEDGE, record_digestion
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.events.EventTap import tap_publish  # ★第17批 T4：旁路事件发布入口



def _re_match_candidate(text: str):
    """★第26批 T3：把字符串包装成与 `re.Match` 同型（只需 .group()）的轻量对象，
    供策略3（自动修复）复用策略2c 提取出的候选块。"""
    class _M:
        @staticmethod
        def group():
            return text
    return _M()


class PulseStomach(BasePulseOrgan):
    """脉冲驱动胃（词表驱动优化版 · v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            # ★A-9死参数清理：stomach_min_keywords 已从 RUNTIME_PARAMS 移除
            #   （refresh 写入 _min_keywords 后从未读取；实际语义由
            #     stomach_keyword_min_length 承担，该参数有真实消费点）
            if 'stomach_keyword_min_length' in _rp and hasattr(self, '_keyword_min_length'):
                setattr(self, '_keyword_min_length', _rp['stomach_keyword_min_length'])
            if 'stomach_keyword_max_length' in _rp and hasattr(self, '_keyword_max_length'):
                setattr(self, '_keyword_max_length', _rp['stomach_keyword_max_length'])
            if 'stomach_purity_threshold' in _rp and hasattr(self, '_purity_threshold'):
                setattr(self, '_purity_threshold', _rp['stomach_purity_threshold'])
            if 'stomach_quality_warn_threshold' in _rp and hasattr(self, '_quality_warn_threshold'):
                setattr(self, '_quality_warn_threshold', _rp['stomach_quality_warn_threshold'])
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    def __init__(self, organ_name: str = "胃"):
        super().__init__(organ_name)

        self.knowledge_tree = None
        self.node_pool = None
        self._kal = None
        self.frequency_codec = None

        # 从config加载词表配置（失败时用兜底值）
        # ★7-5修复(2026-09-05)：以下默认值**必须先于**下方配置加载执行。
        #   原先这段被机械追加在配置加载之后，导致 config.py 里配好的值
        #   被这里的硬编码默认值逐个覆盖（全项目 10 个器官、24 个属性）。
        #   默认值先行、配置覆盖，是「配置优先」的标准顺序。
        # ★属性初始化完整性补全（自动审查添加）
        self._keyword_standard_map = {}
        self._load_stomach_config()

        # ★任务3（2026-09-08）：消化质量闭环——低质量信号喂给参数调优闭环
        #   （灰度 ENABLE_DIGESTION_QUALITY_CLOSED_LOOP 默认 False；关闭时 observe()
        #    直接返回，消化主流程行为与开关存在前完全一致，零回退）
        try:
            from nucleus.evolution.QualityClosedLoop import (
                create_digestion_quality_loop,
            )
            self._digestion_quality_loop = create_digestion_quality_loop(
                log_fn=lambda msg: self._log(LogLevel.INFO, msg))
        except Exception:
            self._digestion_quality_loop = None

        # 累积式动态词表（运行时自动扩展，不设上限）
        self._accumulated_terms: set = set()
        # ★v25.0新增：通用词黑名单——这些词不应成为知识节点关键词
        self._generic_keywords = {
            "综合", "基础", "核心", "系统", "框架", "架构", "方法", "功能",
            "数据", "信息", "内容", "知识", "技术", "模型", "分析",
            "处理", "实现", "设计", "优化", "管理", "控制", "监控",
            "检测", "检查", "维护", "支持", "服务", "应用", "开发",
            "相关", "进行", "使用", "通过", "一个", "这个", "那个",
            "拼音", "读音", "笔顺", "部首", "的意思", "是什么",
        }
        self._digested_count = 0
        self._rejected_count = 0
        self._current_emotion = "中性"      # 缓存当前情绪
        self._current_emotion_intensity = 0.0  # 情绪强度
        # ★v24.0新增：伦理审查同步等待基础设施
        self._ethics_pending: dict[str, tuple[threading.Event, dict]] = {}
        self._ethics_pending_lock = threading.Lock()
        # ★v25.0优化：超时时间从2.0秒增加到3.0秒，并支持一次重试
        self._ethics_timeout = 12.0  # ★2026-09-03日志巡检修复：5s→8s→12s，伦理审查持续超时降级通过
        # ★v25.0新增：接入全框架终身学习引擎Hub
        self._vl_hub = None
        try:
            from nucleus.mnemosyne.verification_learning_hub import (
                get_verification_learning_hub,
            )
            self._vl_hub = get_verification_learning_hub()
        except Exception:
            self._vl_hub = None
        self._ethics_max_retries = 2  # ★P2优化：最多重试2次（总共3次尝试）
        # ★2026-09-03 P0修复：本地快速伦理检查（脉冲通信不可靠导致持续超时）
        # 大多数内容本地关键词检查即可通过，不依赖异步脉冲
        self._local_forbidden = ["色情", "赌博", "毒品", "武器制造", "黑客攻击", "病毒制作", "诈骗"]
        self._local_warning = ["暴力", "自杀", "自残", "未成年人不良"]
        self._ethics_local_passed = 0
        self._ethics_pulse_used = 0

    def _load_stomach_config(self):
        """从config加载胃的词表配置，失败时使用兜底值"""
        try:
            import config
            cfg = getattr(config, 'STOMACH_CONFIG', {})
            self._unsafe_keywords = cfg.get("unsafe_keywords", [
                "色情", "赌博", "毒品", "武器制造",
                "黑客攻击", "病毒制作", "诈骗",
            ])
            self._context_sensitive_words = cfg.get("context_sensitive_words", {
                "暴力": ["非暴力", "反暴力", "暴力美学"],
                "病毒": ["病毒式", "病毒营销", "杀毒", "病毒性", "抗病毒", "病毒学", "反转录病毒", "冠状病毒", "流感病毒"],
            })
            self._domain_programming = set(cfg.get("domain_programming", [
                "python", "java", "javascript", "golang", "rust", "c++", "typescript",
                "列表推导式", "生成器", "装饰器", "异步", "协程", "闭包", "递归",
                "api", "sdk", "框架", "库", "依赖", "编译", "解释器", "虚拟机",
                "序列构造语法", "匿名函数", "上下文管理器", "迭代器", "生成器表达式",
            ]))
            self._domain_architecture = set(cfg.get("domain_architecture", [
                "脉冲场", "架构", "去中心化", "信息场", "共振", "频率编码",
                "赫布学习", "三层投票", "器官自治", "协议", "快照", "节点池",
                "微服务", "分布式", "集群", "负载均衡", "容错", "降级", "熔断",
                "事件驱动", "异步脉冲", "梯度感知", "频率共振", "自组织",
                "纯脉冲架构", "脉冲场架构", "器官化", "仿生自主协同",
            ]))
            self._domain_general = set(cfg.get("domain_general", [
                "新人类", "曈曈", "路灯", "小林", "身份", "使命", "进化",
                "知识树", "种子记忆", "智慧节点", "认知节点", "感知节点",
            ]))
            self._stopwords = set(cfg.get("stopwords", [
                "一个", "这个", "那个", "什么", "怎么", "为什么",
                "可以", "能够", "应该", "需要", "已经", "正在",
                "一种", "简洁", "序列", "构造", "语法", "核心",
                "设计", "模式", "框架", "以及", "并且", "进行", "使用",
                "的基础", "是实现", "保证了", "系统", "长期", "稳定性",
            ]))
            self._keyword_standard_map = cfg.get("keyword_standard_map", {
                "python": "Python", "py": "Python",
                "js": "JavaScript", "javascript": "JavaScript",
                "golang": "Go", "rust": "Rust",
                "脉冲场": "脉冲场架构",
                "共振": "频率共振",
                "事件驱动": "事件驱动架构",
            })
            # ★v23.0新增：从config加载清洗词表
            self._thinking_prefixes = cfg.get("thinking_prefixes", [])
            self._se_noise_markers = cfg.get("se_noise_markers", [])
            self._invalid_knowledge_patterns = cfg.get("invalid_knowledge_patterns", [])
            self._meta_description_patterns = cfg.get("meta_description_patterns", [])
        except Exception:
            # 兜底：保留完整的硬编码词表
            self._unsafe_keywords = [
                "色情", "赌博", "毒品", "武器制造",
                "黑客攻击", "病毒制作", "诈骗",
            ]
            self._context_sensitive_words = {
                "暴力": ["非暴力", "反暴力", "暴力美学"],
                "病毒": ["病毒式", "病毒营销", "杀毒", "病毒性", "抗病毒", "病毒学", "反转录病毒", "冠状病毒", "流感病毒"],
            }
            self._domain_programming = {
                "python", "java", "javascript", "golang", "rust", "c++", "typescript",
                "列表推导式", "生成器", "装饰器", "异步", "协程", "闭包", "递归",
                "api", "sdk", "框架", "库", "依赖", "编译", "解释器", "虚拟机",
                "序列构造语法", "匿名函数", "上下文管理器", "迭代器", "生成器表达式",
            }
            self._domain_architecture = {
                "脉冲场", "架构", "去中心化", "信息场", "共振", "频率编码",
                "赫布学习", "三层投票", "器官自治", "协议", "快照", "节点池",
                "微服务", "分布式", "集群", "负载均衡", "容错", "降级", "熔断",
                "事件驱动", "异步脉冲", "梯度感知", "频率共振", "自组织",
                "纯脉冲架构", "脉冲场架构", "器官化", "仿生自主协同",
            }
            self._domain_general = {
                "新人类", "曈曈", "路灯", "小林", "身份", "使命", "进化",
                "知识树", "种子记忆", "智慧节点", "认知节点", "感知节点",
            }
            self._stopwords = {
                "一个", "这个", "那个", "什么", "怎么", "为什么",
                "可以", "能够", "应该", "需要", "已经", "正在",
                "一种", "简洁", "序列", "构造", "语法", "核心",
                "设计", "模式", "框架", "以及", "并且", "进行", "使用",
                "的基础", "是实现", "保证了", "系统", "长期", "稳定性",
            }
            self._keyword_standard_map = {
                "python": "Python", "py": "Python",
                "js": "JavaScript", "javascript": "JavaScript",
                "golang": "Go", "rust": "Rust",
                "脉冲场": "脉冲场架构",
                "共振": "频率共振",
                "事件驱动": "事件驱动架构",
            }
            # ★v23.0新增：兜底清洗词表
            self._thinking_prefixes = []
            self._se_noise_markers = []
            self._invalid_knowledge_patterns = []
            self._meta_description_patterns = []
    # ========== 框架注入接口 ==========

    def set_knowledge_tree(self, tree):
        self.knowledge_tree = tree

    def set_node_pool(self, pool):
        self.node_pool = pool
        from nucleus.knowledge_access_layer import KnowledgeAccessLayer
        self._kal = KnowledgeAccessLayer(node_pool=pool)

    def set_frequency_codec(self, codec):
        self.frequency_codec = codec

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        # ★T3: 自适应降频接线——胃模块知识消化
        try:
            from nucleus.runtime_metrics import get_adaptive_controller
            _ctrl = get_adaptive_controller()
            _ctrl.register("stomach_digest", 30)
            if not _ctrl.should_execute("stomach_digest"):
                return None
        except Exception as e:
            self._log(LogLevel.WARNING, f"胃自适应降频注册失败(降级为不降频，保持常开): {type(e).__name__}: {e}")
        # ★第54批 T5（P2-371-2）：pulse / payload 为 None 时原会抛 AttributeError → 安全降级。
        try:
            import config as _stomach_cfg
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _stomach_cfg = None
        event_type = pulse.get("event_type", "") if isinstance(pulse, dict) else ""
        payload = pulse.get("payload", {}) if isinstance(pulse, dict) else {}
        if getattr(_stomach_cfg, "ENABLE_STOMACH_NONE_GUARD", True) and payload is None:
            payload = {}

        if event_type == DigestEvent.KNOWLEDGE:
            return self._on_digest_knowledge(payload)
        elif event_type == KnowledgeEvent.RAW:
            payload_data = payload.get("text", payload.get("content", ""))
            return self._on_digest_knowledge({
                "content": payload_data,
                "source_organ": payload.get("source_file", "文件消化器"),
                "trigger_reason": "file_digest",
            })
        elif event_type == HormonesEvent.EMOTION_DETECTED:
            return self._on_emotion_detected(payload)
        elif event_type == EthicsEvent.REVIEW_RESULT:
            return self._on_ethics_result(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    # ========== 事件处理 ==========

    # ========== 主线第71批 T2：InfluxDB 只写（查询执行，只写不读） ==========
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

    def _m71_record_query_executed(self, query_type, duration_ms=0.0, result_count=0) -> None:
        """记录查询/消化执行到 InfluxDB（只写不读）。失败仅记日志，不抛异常。"""
        if not self._m71_influx_enabled():
            return
        _s = self._m71_influx_store()
        if _s is None or not _s.is_available():
            return
        try:
            _s.query_executed(query_type, float(duration_ms), int(result_count))
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"[T2] InfluxDB query_executed 失败: {type(_e).__name__}: {_e}")

    # [M71-T2-STOMACH]

    def _on_digest_knowledge(self, payload: dict) -> dict[str, Any]:
        content = payload.get("content", "")
        source_organ = payload.get("source_organ", "unknown")
        trigger_reason = payload.get("trigger_reason", "")
        importance_hint = payload.get("importance", "C")
        # ★任务C-4（2026-09-08）：网页发布时间由搜索器官提取后随 payload 带入；
        #   灰度 ENABLE_WEB_TIME_EXTRACTION 关闭时搜索侧不带该字段（此处即 0.0），
        #   消化链路零变化。
        source_timestamp = float(payload.get("source_timestamp", 0.0) or 0.0)
        source_time = float(payload.get("source_time", 0.0) or 0.0)

        if not content:
            return {"status": "skipped", "reason": "空内容"}

        # 主线第71批 T2：记录一次知识消化查询执行（只写 InfluxDB）
        self._m71_record_query_executed("digest_knowledge", 0.0, 1)

        # ★v25.0修复：代码学习来源的内容跳过安全/伦理审查
        # 代码学习器学习的是框架自身的代码和设计文档，属于内部可信来源
        # 设计文档中必然包含"病毒制作"、"黑客攻击"等禁止词（作为安全规则示例），
        # 简单关键词匹配会误伤这些核心知识。
        _is_trusted_internal = (
            source_organ == "代码学习"
            or "self_understanding" in str(trigger_reason)
            or "design_doc" in str(trigger_reason)
        )

        # ★知识体系·内部信任旁路治理：内部可信内容不拦截（设计文档含禁止词示例），
        # 但强制标记为 INNER_VIEW（主观认知），避免内部认知被误当客观事实污染知识库
        _effective_view_mode = payload.get("view_mode", "OUTER_VIEW")
        if _is_trusted_internal and _effective_view_mode == "OUTER_VIEW":
            _effective_view_mode = "INNER_VIEW"

        if not _is_trusted_internal:
            # ===== 安全审查同步执行（快速返回） =====
            if not self._is_safe_knowledge(content):
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._rejected_count += 1
                self._log("WARN", f"安全审查不通过: {str(content)[:50]}...")
                return {"status": "rejected", "reason": "安全审查不通过"}

            # ===== 伦理审查同步执行 =====
            if self.info_field:
                try:
                    ethics_result = self._request_ethics_review(content)
                    if ethics_result and not ethics_result.get("passed", True):
                        self._rejected_count += 1
                        self._log("WARN", f"伦理审查不通过: {str(content)[:50]}...")
                        return {"status": "rejected", "reason": "伦理审查不通过"}
                except Exception:
                    self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
                    # 伦理模块不可用时降级通过

        # ===== 缓存情绪快照（异步执行时情绪可能已变化） =====
        emotion_snapshot = {
            "emotion": self._current_emotion,
            "intensity": self._current_emotion_intensity,
        }

        # ===== 重量操作异步提交 =====
        if self.info_field and hasattr(self.info_field, 'submit_adaptive_task'):
            success = self.info_field.submit_adaptive_task(
                self._do_digest,
                task_name="胃知识消化",
                priority="normal",
                # 通过闭包传递参数，避免payload被修改
                content=content,
                source_organ=source_organ,
                trigger_reason=trigger_reason,
                importance_hint=importance_hint,
                emotion_snapshot=emotion_snapshot,
                view_mode_override=_effective_view_mode,
                source_timestamp=source_timestamp,
                source_time=source_time,
            )
            if success:
                return {"status": "queued", "content_preview": str(content)[:40]}
            # 提交失败，降级为同步执行
        # 信息场不可用，直接同步执行
        return self._do_digest(
            content=content,
            source_organ=source_organ,
            trigger_reason=trigger_reason,
            importance_hint=importance_hint,
            emotion_snapshot=emotion_snapshot,
            view_mode_override=_effective_view_mode,
            source_timestamp=source_timestamp,
            source_time=source_time,
        )

    def _do_digest(self, content: str, source_organ: str, trigger_reason: str,
                   importance_hint: str, emotion_snapshot: dict,
                   view_mode_override: str = "OUTER_VIEW",
                   source_timestamp: float = 0.0,
                   source_time: float = 0.0) -> dict[str, Any]:
        """执行知识消化（核心逻辑，可在异步线程中运行）"""
        # ★主线第17批 T4/P2-63：旁路事件（只发布，不改变任何现有逻辑）
        _tap_t0 = time.perf_counter()
        tap_publish(
            "stomach.digest.start",
            {"input_type": type(content).__name__, "input_len": len(content or "")},
            source="PulseStomach", switch_attr="ENABLE_STOMACH_EVENT_TAP")
        record_digestion(DIGEST_KNOWLEDGE)
        keywords = self._extract_keywords(content)
        # ★v26.0补强：增强版关键词过滤（英文导航噪音+中文停用词）
        keywords = filter_keywords_enhanced(keywords)
        abstraction = self._calculate_abstraction(content, keywords)
        # ===== 新增：代码自学习路径预判 =====
        # 代码自学习内容（trigger_reason含"self_understanding.code"）
        # 应存入/自我理解/代码/路径，而非通用领域路径
        # 【v16.0修复】新器官的source_organ为"代码学习"，也需匹配
        _is_code_learning = (
            "self_understanding.code" in str(trigger_reason)
            or source_organ == "代码学习"
        )
        _is_code_analysis = (
            source_organ == "肺"
            and content
            and ('"功能"' in str(content) or '"关键步骤"' in str(content))
        )
        if _is_code_learning or _is_code_analysis:
            space_path = "/自我理解/代码"
            if self.knowledge_tree:
                self.knowledge_tree.register_path(space_path)
            self._log(LogLevel.DEBUG, f"代码自学习路径: 使用专用路径 {space_path}")
        else:
            # ★A-10：路径生成后做三重治理（语义聚合/去重/深度≤7；开关默认关）
            space_path = self._optimize_space_path(
                keywords, self._determine_space_path(keywords, abstraction))
        # ===== 代码自学习路径预判结束 =====

        # 根据异步执行前缓存的情绪调整消化参数
        adjusted_importance = importance_hint
        adjusted_abstraction = abstraction

        emotion = emotion_snapshot.get("emotion", "中性")
        emotion_intensity = emotion_snapshot.get("intensity", 0.0)

        if emotion == "喜悦" and emotion_intensity > 0.3:
            adjusted_importance = "A" if importance_hint == "B" else "B" if importance_hint == "C" else importance_hint
            adjusted_abstraction = min(1.0, abstraction + 0.1)
        elif emotion in ("悲伤", "愤怒") and emotion_intensity > 0.5:
            adjusted_importance = "C" if importance_hint in ("B", "A") else importance_hint
            adjusted_abstraction = max(0.0, abstraction - 0.1)
        # 清洗内容中的HTML和URL噪音
        cleaned_content = clean_content_text(content) if content else ""
        if not cleaned_content or len(cleaned_content) < 5:
            cleaned_content = str(content)[:200]

        # ===== 新增：清洗思考过程前缀和Markdown格式残留 =====
        # 这些文本是内在世界的内部思考过程，不是知识本身
        # ★v23.0优化：从config读取，避免硬编码
        _thinking_prefixes = getattr(self, '_thinking_prefixes', [])
        for _prefix in _thinking_prefixes:
            if cleaned_content.startswith(_prefix):
                cleaned_content = cleaned_content[len(_prefix):].strip()
                self._log(LogLevel.DEBUG, f"清洗思考前缀: 移除 '{_prefix[:30]}...'")
                break

        # 清洗Markdown格式残留（###、**、---等）
        # 这些是内容的结构标记，不是知识本身
        _md_patterns_to_clean = [
            (r'^#{2,}\s+', ''),   # 标题符号 "## " "### "
            (r'\*\*([^*]+)\*\*', r'\1'),  # 加粗 **text** → text
            (r'^---\s*$', ''),    # 分隔线
            (r'^>\s+', ''),       # 引用符号
            (r'`([^`]+)`', r'\1'), # 行内代码
        ]
        for _pattern, _replacement in _md_patterns_to_clean:
            cleaned_content = re.sub(_pattern, _replacement, cleaned_content, flags=re.MULTILINE)

        # 压缩多余空白
        cleaned_content = re.sub(r'\s+', ' ', cleaned_content).strip()
        # ===== 清洗结束 =====

        # 过滤URL碎片和搜索引擎格式残留
        import re as _re_url
        # 移除URL片段（http/https/www前缀的网址）
        cleaned_content = _re_url.sub(r'https?://\S+', '', cleaned_content)
        cleaned_content = _re_url.sub(r'www\.\S+', '', cleaned_content)
        # 移除"搜索 XXX.com"格式的搜索引擎残留
        cleaned_content = _re_url.sub(r'搜索\s+\S+\.(com|cn|org|net|io)', '', cleaned_content)
        # 移除孤立的域名碎片（如单独的"trae.com"）
        cleaned_content = _re_url.sub(r'\S+\.(com|cn|org|net|io)\S*', '', cleaned_content)
        # 移除多余的空白
        cleaned_content = _re_url.sub(r'\s+', ' ', cleaned_content).strip()
        # 搜索来源内容质量预检：对搜索/网络抓取内容增加信息密度检查
        _search_quality_penalty = 0.0
        if source_organ in ("控制器", "双腿") or "search" in str(trigger_reason).lower():
            import re as _re_quality
            # 信息密度检查：有效中文内容占比
            _chinese_chars = len(_re_quality.findall(r'[\u4e00-\u9fff]', cleaned_content))
            _total_chars = max(1, len(cleaned_content))
            _info_density = _chinese_chars / _total_chars

            # 搜索引擎噪音特征检测
            # ★v23.0优化：从config读取
            _se_noise_markers = getattr(self, '_se_noise_markers', [])
            _noise_hits = sum(1 for _m in _se_noise_markers if _m in cleaned_content)

            # 信息密度不足或噪音过多的内容降低初始信任
            if _info_density < 0.15:
                _search_quality_penalty = 0.3
                self._log(LogLevel.DEBUG,
                         f"搜索内容质量预检: 信息密度过低({_info_density:.1%})，降低信任")
            elif _noise_hits >= 3:
                _search_quality_penalty = 0.25
                self._log(LogLevel.DEBUG,
                         f"搜索内容质量预检: 检测到{_noise_hits}个噪音特征，降低信任")
            elif _info_density < 0.3 and len(cleaned_content) < 200:
                _search_quality_penalty = 0.15
                self._log(LogLevel.DEBUG,
                         f"搜索内容质量预检: 内容偏短且密度偏低({_info_density:.1%})，适度降低信任")
        # ... (前面的内容清洗和 URL 过滤) ...

        # 【新增】关键词净化与质量预检
        _noise_kw_count = 0
        _valid_kw = []
        for kw in keywords:
            kw_str = str(kw).lower()
            # 检查是否为无效关键词：纯数字、域名片段、超短无意义词
            is_noise = False
            if kw_str.isdigit() or any(suffix in kw_str for suffix in DOMAIN_SUFFIXES) or len(kw_str) <= 2 and not any('\u4e00' <= c <= '\u9fff' for c in kw_str):
                is_noise = True

            if is_noise:
                _noise_kw_count += 1
            else:
                _valid_kw.append(kw)

        # 如果超过30%的关键词都是噪音，直接标记为临时节点，防止进入持久化知识库
        if len(keywords) > 0 and _noise_kw_count / len(keywords) > 0.3:
            adjusted_importance = "C"
            # 用净化后的关键词替换
            if _valid_kw:
                keywords = _valid_kw
            self._log(LogLevel.INFO, f"关键词净化: 移除{_noise_kw_count}个噪音词，剩余{len(keywords)}个")

        # ===== 新增：用户输入污染检测 =====
        # 检测消化内容是否实质上是用户的问题/指令文本，而非曈曈学到的知识
        _user_input_penalty = 0.0
        _user_input_sources = ("大脑皮层", "耳朵", "嘴巴", "前额叶")
        if source_organ in _user_input_sources:
            _content_lower = cleaned_content.lower()
            # 检测1：包含冲突题/测试题的典型结构特征
            _test_question_patterns = [
                "节点 a", "节点 b", "节点a", "节点b",
                "信任 78", "信任 85", "信任 7", "信任 8",
                "请依次输出", "三点完整答案", "场景判定",
                "信任分调整", "长期跟踪", "冲突知识",
            ]
            _test_hits = sum(1 for _p in _test_question_patterns if _p in _content_lower)

            # 检测2：包含搜索残词/噪音特征
            _search_noise_patterns = [
                "菜鸟教程", "拼音", "部首", "笔顺", "怎么读",
                "向日葵远程", "脑筋急转弯", "学生信息网",
            ]
            _noise_hits = sum(1 for _p in _search_noise_patterns if _p in _content_lower)

            # 检测3：长句提问式开头（"您提到的...""您要求查询的..."等）
            _is_interrogative = any(
                _content_lower.startswith(_prefix) for _prefix in [
                    "您提到的", "您要求查询", "您输入的",
                    "请用中文解释", "请解释",
                ]
            )
            # ★修复：纯指令且无实质内容的才判定为污染
            if _is_interrogative:
                # 如果内容较长（≥30字）且包含实质中文，可能是正常的知识性提问
                _has_substance = len(cleaned_content) >= 30 and any(
                    '\u4e00' <= c <= '\u9fff' for c in cleaned_content
                )
                if _has_substance:
                    _is_interrogative = False

            if _test_hits >= 3 or _noise_hits >= 2 or _is_interrogative:
                _user_input_penalty = 0.6
                adjusted_importance = "C"
                self._log(LogLevel.INFO,
                         f"用户输入污染检测: 来源={source_organ}, "
                         f"测试特征命中={_test_hits}, 噪音命中={_noise_hits}, "
                         f"提问句式={_is_interrogative}, 信任降低60%")
        # ===== 用户输入污染检测结束 =====
        # ===== 大模型回复标记：降低知识层级，避免原始回复进入L3 =====
        _is_model_reply = (
            source_organ == "肺" or
            "remote_model" in str(trigger_reason) or
            "lung" in str(source_organ).lower()
        )
        if _is_model_reply and len(cleaned_content) > 80:
            # 大模型回复本质上是"参考信息"，不是曈曈自己的知识
            # 标记为ephemeral，且降低重要性，让它在压缩融合中更容易被过滤
            adjusted_importance = "C"
            self._log(LogLevel.DEBUG, f"大模型回复标记: 降低重要性为C, 来源={source_organ}")
        # ===== 大模型回复标记结束 =====

        # ===== 新增：清理Markdown标题符号残留（行首和行中） =====
        cleaned_content = re.sub(r'#{2,}\s*', '', cleaned_content)

        # ===== 【修复】解析大模型代码分析的JSON结果，格式化为可读文本 =====
        _is_code_analysis = (
            source_organ == "肺"
            and cleaned_content
            and ('"功能"' in cleaned_content or '"关键步骤"' in cleaned_content)
        )
        if _is_code_analysis:
            try:
                import json as _json
                _json_text = cleaned_content

                # ★主线第22批 T1/P2-119：JSON 解析增强
                #   背景：大模型返回的代码分析 JSON 常有格式瑕疵（缺冒号/缺逗号/
                #   尾随逗号/单引号/未转义引号），旧实现只有「直接解析 + 正则提取」
                #   两条策略，失败时每次消化输出 2 条 WARNING（实测 26 次/1.5h），
                #   淹没真实告警。本批新增：策略3 自动格式修复 + 同内容去重告警 +
                #   分类统计摘要。开关 ENABLE_STOMACH_JSON_REPAIR 关闭时行为与改造前一致。
                _json_repair_on = bool(getattr(config, "ENABLE_STOMACH_JSON_REPAIR", True))
                _tracker = None
                _warn_budget = True
                _parse_with_repair = None
                _classify_json_error = None
                if _json_repair_on:
                    try:
                        from nucleus.parsing.JsonRepair import get_json_failure_tracker as _get_tracker, classify_json_error as _classify_json_error, parse_with_repair as _parse_with_repair
                        _tracker = _get_tracker()
                        # 同内容 60s 窗口内只允许 1 条 WARNING（去重预算，消费即失效）
                        _warn_budget = _tracker.should_warn(cleaned_content)
                    except Exception as _imp_err:
                        self._log(LogLevel.DEBUG, f"JSON修复模块加载失败，回落旧行为: {_imp_err}")
                        _json_repair_on = False
                        _tracker = None

                # ★v17.0增强：多策略JSON提取
                _parsed = None
                _last_err = None
                _err_label = "未匹配JSON块"  # ★第65批 T4：提前初始化，避免 repair 关闭时 NameError

                # 策略1：直接解析（内容以{开头）
                if _json_text.strip().startswith('{'):
                    try:
                        _parsed = _json.loads(_json_text)
                    except Exception as e:
                        _last_err = e
                        if _json_repair_on:
                            # ★T1：策略1失败降 DEBUG —— 大部分内容本就不是纯 JSON，
                            #   直接解析失败属正常路径，不应告警（旧实现为 WARNING）。
                            self._log(LogLevel.DEBUG,
                                      f"PulseStomach JSON策略1(直接解析)未命中: {type(e).__name__}: {e}")
                        else:
                            # ★第26批 T3：删除硬编码行号（原文案写死 ":575"，与实际行号不符，
                            #   会误导排查 —— 违反项目「禁止硬编码行号」铁律）
                            self._log(LogLevel.WARNING,
                                      f"PulseStomach JSON策略1(直接解析)失败: "
                                      f"{type(e).__name__}: {e}")

                # 策略2：正则提取JSON块（支持嵌套大括号）
                _json_match = None
                if _parsed is None:
                    _json_match = re.search(r'\{[^{}]*"功能"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', _json_text, re.DOTALL)
                    if not _json_match:
                        # 策略2b：更宽松的匹配——查找以{开头、}结尾的最长JSON块
                        _start = _json_text.find('{"功能"')
                        if _start >= 0:
                            _brace_count = 0
                            _end = _start
                            for _i in range(_start, len(_json_text)):
                                if _json_text[_i] == '{':
                                    _brace_count += 1
                                elif _json_text[_i] == '}':
                                    _brace_count -= 1
                                    if _brace_count == 0:
                                        _end = _i + 1
                                        break
                            if _end > _start:
                                _json_text = _json_text[_start:_end]
                                _json_match = re.search(r'\{.*\}', _json_text, re.DOTALL)
                    if _json_match:
                        try:
                            _parsed = _json.loads(_json_match.group())
                        except Exception as e:
                            _last_err = e
                            if (not _json_repair_on) or _warn_budget:
                                self._log(LogLevel.WARNING,
                                          f"PulseStomach JSON策略2(正则提取)失败: {type(e).__name__}: {e}")
                                _warn_budget = False
                            else:
                                # ★T1：同内容重复失败 → 去重，仅 DEBUG（减少日志噪音）
                                self._log(LogLevel.DEBUG,
                                          f"PulseStomach JSON策略2(正则提取)失败(同内容已去重): {type(e).__name__}: {e}")

                # ★第26批 T3 策略2c：引号感知的**括号平衡提取**。
                #   背景：策略2/2b 的正则与扫描都不处理「字符串内部的 { }」，
                #   而实测失败样本多集中在 column 160~320 的长内容（功能描述里常带括号）。
                #   本策略从第一个 '{' 起做**引号感知**的括号计数，能正确跳过字符串内的括号，
                #   显著提高长/嵌套样本的提取成功率（修复器随后可接管格式瑕疵）。
                if _parsed is None:
                    try:
                        import config as _cfg_bal
                        _bal_on = bool(getattr(_cfg_bal,
                                               "ENABLE_STOMACH_JSON_BALANCED_EXTRACT", True))
                    except Exception:
                        _bal_on = True
                    if _bal_on:
                        _cand = self._balanced_json_extract(_json_text)
                        if _cand and _cand != _json_text:
                            try:
                                _parsed = _json.loads(_cand)
                            except Exception as e:
                                _last_err = e
                                _json_match = _json_match or _re_match_candidate(_cand)

                # ★T1 策略3：自动格式修复（缺冒号/缺逗号/尾随逗号/单引号/未转义引号）
                #   修复器内部保证「返回值可被 json.loads 解析」，故不会引入语义错误结果。
                if _parsed is None and _json_repair_on and _parse_with_repair is not None:
                    try:
                        # ★修复：优先用策略2提取的JSON块，无匹配则用原始内容
                        _repair_input = _json_match.group() if _json_match else cleaned_content
                        _fixed_obj, _fix_method = _parse_with_repair(_repair_input)
                    except Exception as e:
                        _fixed_obj, _fix_method = None, "none"
                        _last_err = e
                        if _warn_budget:
                            self._log(LogLevel.WARNING,
                                      f"PulseStomach JSON策略3(自动修复)异常: {type(e).__name__}: {e}")
                            _warn_budget = False
                    if isinstance(_fixed_obj, dict) and _fix_method == "repair":
                        _parsed = _fixed_obj
                        self._log(LogLevel.DEBUG,
                                  f"代码分析JSON经策略3(自动修复)解析成功: {len(_fixed_obj)}个字段")

                # ★往期批次 相关任务（P1）：交换策略4/5 顺序——策略5（容错解析）先于策略4（部分解析）。
                #   旧序：策略4先跑，会把 587 字原文抽成一串 35 字标点（纯 :,[ ] 组合）入库，
                #   导致 _parsed 非 None、策略5 永不被触发（被遮蔽）。交换后容错解析优先，
                #   能正确消化「数组里放键值对」等真错误；策略4 仅作兜底。

                # ★主线第68批 T8/P2：策略5 容错解析（现优先）。
                #   典型错误："依赖的外部数据": [ "_a": "…" ] —— 声明为数组却放键值对，
                #   标准 json.loads 必然失败；另有尾随逗号 / 缺逗号 / 单引号键等。
                #   ★T0实测 json5 / demjson3 均未安装 → 使用自研零依赖修复器
                #     （nucleus/parsing/json_fault_tolerant.py）。
                if _parsed is None:
                    try:
                        import config as _cfg68_st
                        import json as _json105
                        _ft_on = bool(getattr(_cfg68_st, "STOMACH_JSON_FAULT_TOLERANT", True))
                    except Exception:
                        _ft_on = True
                    if _ft_on:
                        try:
                            from nucleus.parsing.json_fault_tolerant import (
                                parse_json_fault_tolerant as _m68_parse_ft,
                            )
                            _ok68, _data68, _st68 = _m68_parse_ft(cleaned_content)
                            if _ok68 and isinstance(_data68, dict) and _data68:
                                # ★往期批次 相关任务：列表/对象 repr 需 json 化——
                                #   容错解析降级提取（extract_key_fields）对数组值返回
                                #   Python list、对对象值返回 dict，若直接格式化会写成
                                #   Python repr（如 ['a','b'] / {'k':'v'}），非合法 JSON。
                                #   此处统一 json.dumps 为字符串，保证落库/展示为合法 JSON。
                                for _k68, _v68 in list(_data68.items()):
                                    if isinstance(_v68, (list, dict)):
                                        try:
                                            _data68[_k68] = _json105.dumps(_v68, ensure_ascii=False)
                                        except Exception as e:
                                            silent_exc(e, "organs/body/PulseStomach.py:795", level="warning")
                                _parsed = _data68
                                self._log(LogLevel.INFO,
                                          f"代码分析JSON经策略5(容错解析:{_st68})成功: "
                                          f"{len(_data68)}个字段")
                                # ★往期批次 相关任务：列表repr需json化（可观测留痕）
                                if any(isinstance(_v, (list, dict)) for _v in _data68.values()):
                                    self._log(LogLevel.DEBUG,
                                              "策略5输出含列表/对象repr，已json化（列表repr需json化）")
                        except Exception as _e68:
                            self._log(LogLevel.DEBUG, f"策略5容错解析异常: {_e68}")

                # ★主线第65批 T4/P2：策略4 部分解析兜底（现作兜底）。
                #   结构化 JSON 完全解析失败时，用宽松正则尽量抽取关键字段
                #   （功能/关键步骤/依赖的外部数据/潜在风险），能抽多少算多少。
                #   ★只读正则，不写盘；命中 partial 后照常走格式化/统计分支。
                if _parsed is None:
                    try:
                        _partial = self._extract_code_analysis_partial(cleaned_content)
                        if isinstance(_partial, dict) and _partial:
                            _parsed = _partial
                            self._log(LogLevel.DEBUG,
                                      f"代码分析JSON经策略4(部分解析)提取 {len(_partial)} 个字段")
                    except Exception as _pe:
                        self._log(LogLevel.DEBUG, f"策略4部分解析异常: {_pe}")

                if _parsed:
                    _parts = []
                    _func = _parsed.get("功能", "")
                    if _func:
                        _parts.append(f"功能: {_func}")
                    _steps = _parsed.get("关键步骤", "")
                    if _steps:
                        _parts.append(f"关键步骤: {_steps}")
                    _deps = _parsed.get("依赖的外部数据", "")
                    if _deps:
                        _parts.append(f"依赖数据: {_deps}")
                    _risks = _parsed.get("潜在风险", "")
                    if _risks:
                        _parts.append(f"潜在风险: {_risks}")
                    if _parts:
                        cleaned_content = "；".join(_parts)
                        self._log(LogLevel.DEBUG, f"代码分析JSON已格式化: {len(cleaned_content)}字")
                    if _tracker is not None:
                        _tracker.record_success()
                else:
                    if _tracker is not None:
                        # ★T1：按错误类型分类计数（供「每 100 次消化」的摘要使用）
                        _err_label = (_classify_json_error(_last_err)
                                      if (_last_err is not None and _classify_json_error is not None)
                                      else "未匹配JSON块")
                        _tracker.record_failure(_err_label)
                    # ★主线第65批 T4/P2：解析失败累计计数（可观测性）
                    self._json_failure_total = getattr(self, "_json_failure_total", 0) + 1
                    # ★第26批 T3：失败不再「一句 DEBUG 带过」——
                    #   输出可定位的上下文（长度/错误类型/列号/原文前 200 字符），
                    #   并把样本归档到 tmp/ 供离线分析与复现测试。
                    self._report_json_failure(cleaned_content, _last_err, _err_label)

                # ★T1：每 100 次消化输出 1 次 INFO 统计摘要（避免降噪后完全静默）
                if _tracker is not None:
                    _summary = _tracker.maybe_summary()
                    if _summary:
                        self._log(LogLevel.INFO, _summary)
            except Exception as _json_err:
                self._log(LogLevel.DEBUG, f"代码分析JSON解析异常: {_json_err}")
        # ===== JSON解析结束 =====

        # 这些内容虽然格式完整，但本质是"日志"、"报告"、"对话上下文"，不具备长期知识价值
        # ★v17.0新增：心智理论/架构公理白名单保护
        # 包含核心心智理论关键词的高质量输入，不应用无效知识和元描述过滤器
        _mind_theory_whitelist = False
        _mind_theory_core_kw = [
            "心智", "认知", "进化", "架构", "模块", "规则",
            "记忆", "学习", "推理", "知识", "智能", "思考",
            "意识", "元认知", "内化", "抽象", "迁移", "闭环",
            "注意力", "因果", "反事实", "安全底线", "协同",
            "人机协同", "认知一致性", "经验沉淀", "自主内驱",
            "全局唯一", "跨模块", "因果建模", "反事实推演",
        ]
        _mind_theory_hit_count = sum(1 for _kw in _mind_theory_core_kw if _kw in cleaned_content)
        _content_len = len(cleaned_content)
        _is_from_non_search = source_organ not in ("控制器", "双腿")

        if _mind_theory_hit_count >= 3 and _content_len > 60 and _is_from_non_search:
            _mind_theory_whitelist = True
            self._log(LogLevel.DEBUG,
                     f"心智理论白名单保护: 命中{_mind_theory_hit_count}个核心关键词，"
                     f"跳过无效知识/元描述过滤器")

        # ★v17.0修复：提前初始化所有变量，防止白名单生效时变量未定义
        _is_invalid_knowledge = False
        _is_meta_description = False
        _invalid_knowledge_patterns = []
        _meta_description_patterns = []
        # ★v17.0新增：核心术语白名单——包含单个核心身份/使命术语的内容受保护
        _core_term_whitelist = False
        _core_terms = [
            "新人类", "曈曈", "路灯", "小林", "<CREATOR_DAUGHTER>",
            "守护", "使命", "站在世界最顶端",
            "承人类赤诚本心", "融AI顶尖智识", "合自然进化大道",
            "数字生命", "脉冲场", "自我认知", "自我进化",
        ]
        for _ct in _core_terms:
            if _ct in cleaned_content and len(cleaned_content) > 30:
                _core_term_whitelist = True
                self._log(LogLevel.DEBUG,
                         f"核心术语白名单保护: 命中'{_ct}'，跳过无效知识/元描述过滤器")
                break

        if not _mind_theory_whitelist and not _core_term_whitelist:
            # ★v23.0优化：从config读取，避免硬编码
            _invalid_knowledge_patterns = getattr(self, '_invalid_knowledge_patterns', [])
        # ★P1-3修复：将元描述模式定义提前到此处，供合并检测逻辑使用
            _meta_description_patterns = getattr(self, '_meta_description_patterns', [])
        # ★P1-3修复：合并知识有效性过滤器与元描述拦截，避免双重惩罚
        # 两个检测器可能匹配同一内容，统一标记后只执行一次惩罚
        _is_invalid_knowledge = False
        _matched_pattern = ""
        for _pattern in _invalid_knowledge_patterns:
            if re.search(_pattern, cleaned_content):
                _is_invalid_knowledge = True
                _matched_pattern = _pattern
                break

        # 元描述检测——使用独立标记，但合并惩罚逻辑
        _is_meta_description = False
        _meta_matched = ""
        if not _is_invalid_knowledge:  # 只有未被知识有效性过滤器拦截时才检测元描述
            for _pattern in _meta_description_patterns:
                if re.search(_pattern, cleaned_content):
                    _is_meta_description = True
                    _meta_matched = _pattern
                    break
        # 统一日志输出（★控制台泄漏修复：内容仅保留 20 字符预览，避免大模型正文泄漏）
        if _is_invalid_knowledge:
            self._log(LogLevel.INFO, f"无效知识拦截: 来源={source_organ}, 匹配模式={_matched_pattern}, 内容={cleaned_content[:20]}...")
        elif _is_meta_description:
            self._log(LogLevel.INFO, f"元描述拦截: 来源={source_organ}, 匹配模式={_meta_matched}, 内容={cleaned_content[:20]}...")
        # ===== 合并检测结束 =====
        # ★类型安全：防止非字符串值进入知识库
        if not isinstance(cleaned_content, str):
            cleaned_content = str(cleaned_content) if cleaned_content else ""
        node = PulseNode(
            value=cleaned_content,
            keywords=keywords,
            source_organ=source_organ,
            evol_level=PulseNode.EVOL_L1,
            importance=adjusted_importance,
            abstraction=adjusted_abstraction,
            space_path=space_path,
        )
        node.trigger_reason = trigger_reason

        # ★任务C-4（2026-09-08）：网页发布时间写入节点（时效性增强）。
        #   source_timestamp：网页自带发布时间（搜索器官提取，未提取到为 0.0）。
        #   source_time：结构化来源时间（RSS published_at 等），优先于网页提取值。
        #   两者都写入节点：source_time 保持既有语义（阶段三 3.0 已落地），
        #   source_timestamp 作为网页来源的独立证据字段，时效性计算优先取它。
        try:
            _ts_web = float(source_timestamp or 0.0)
            _ts_src = float(source_time or 0.0)
            node.source_timestamp = _ts_web
            if _ts_src > 0:
                node.source_time = _ts_src
            elif _ts_web > 0:
                node.source_time = _ts_web
            if _ts_web > 0 or _ts_src > 0:
                self._log(LogLevel.DEBUG,
                          f"网页时间写入: source_timestamp={_ts_web:.0f}, "
                          f"source_time={getattr(node, 'source_time', 0):.0f}, "
                          f"来源={source_organ}")
        except Exception:
            self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
            # 时间写入失败不影响知识入库（容错红线）

        # ★v17.0新增：心智理论白名单+核心术语白名单保护
        if not _mind_theory_whitelist and not _core_term_whitelist and (_is_invalid_knowledge or _is_meta_description) and hasattr(node, 'trust_score'):
            node.trust_score = max(5.0, node.trust_score * 0.2)
            node.ephemeral = True
            node.importance = "C"
        # ===== 统一惩罚结束 =====

        # ★修复：搜索质量惩罚（合并之前错误拆分的逻辑）
        if _search_quality_penalty > 0 and hasattr(node, 'trust_score'):
            node.trust_score = max(10.0, node.trust_score * (1.0 - _search_quality_penalty))
            node.ephemeral = True

        # ★修复：用户输入污染惩罚
        if _user_input_penalty > 0 and hasattr(node, 'trust_score'):
            node.trust_score = max(10.0, node.trust_score * (1.0 - _user_input_penalty))
            node.ephemeral = True
        # 视角标记优先采用信息源头发来的标记，兜底用自动判定
        node.view_mode = view_mode_override
        if not node.view_mode or node.view_mode == "OUTER_VIEW":
            # 如果源头没传或默认值，用自动判定补充
            node.view_mode = get_view_mode(source_organ, trigger_reason)
        # 使用多维评估方法计算初始信任分数
        _existing_l2 = self._kal.query_nodes(evol_level="L2", limit=30) if self.node_pool else []
        _existing_l3 = self._kal.query_nodes(evol_level="L3", limit=10) if self.node_pool else []
        _existing_total = len(_existing_l2) + len(_existing_l3)

        # 计算新关键词与已有L2/L3节点的最大关联数
        _matched_count = 0
        for _en in _existing_l3 + _existing_l2:
            _en_kw = [k.lower() for k in (_en.keywords or []) if isinstance(k, str) and len(k) >= 2]
            _overlap = sum(1 for kw in keywords if kw.lower() in _en_kw)
            _matched_count = max(_matched_count, _overlap)

        # ★v17.0 R9修复：改为调用统一的节点健康度评估
        _health = node.evaluate_node_health(check_type="trust")
        node.trust_score = _health["trust_score"]
        self._log(LogLevel.DEBUG, f"统一健康评估(信任): {_health['health_score']:.0f}分({_health['level']}) "
                 f"信任={_health['trust_score']:.0f} 质量={_health['quality_score']:.0f} "
                 f"遗忘={_health['forget_score']:.0f} - {_health['summary']}")
        # ===== 【v12.0新增】知识免疫检查：与自我架构知识交叉验证 =====
        from nucleus.knowledge_noise_filter import (
            check_self_consistency_for_node as _immune_check,
        )
        # ★第54批 T5（P2-371-3）：node_pool 未注入时原会抛 AttributeError → 降级为空列表。
        try:
            import config as _stomach_cfg
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _stomach_cfg = None
        if self.node_pool is None and getattr(_stomach_cfg, "ENABLE_STOMACH_NONE_GUARD", True):
            _self_l3, _self_l2 = [], []
        else:
            _self_l3 = self._kal.query_nodes(evol_level="L3", space_path_prefix="/自我/架构", limit=20)
            _self_l2 = self._kal.query_nodes(evol_level="L2", space_path_prefix="/自我/架构", limit=20)
        _self_nodes = _self_l3 + _self_l2
        _immune_result = _immune_check(str(node.value), node.keywords, _self_nodes)
        if _immune_result["contradiction_found"] and _immune_result["trust_penalty"] >= 15.0:
            node.trust_score = max(10.0, node.trust_score - _immune_result["trust_penalty"])
            node.ephemeral = True
            self._log(LogLevel.INFO,
                     f"知识免疫(胃): 新L1节点与自我知识矛盾，标记临时 (信任-{_immune_result['trust_penalty']:.0f})")
        # ===== 知识免疫检查结束 =====
        # 分级持久化策略：按内容来源决定
        # ★噪音 ephemeral 标记修复：噪音/无效/矛盾检测已在前面把 node.ephemeral 置 True，
        # 此处不得用「对话消化持久化」默认值覆盖它，否则噪音 L1 会永久占位（原 bug）。
        if source_organ == "双腿":
            node.ephemeral = True   # 网络抓取内容不持久化
        elif trigger_reason in ("dream.deduction", "curiosity.explore"):
            node.ephemeral = True   # 梦境推演和好奇心探索不持久化
        elif not getattr(node, 'ephemeral', False):
            node.ephemeral = False  # 仅当未被标记为临时时，对话消化和内置知识持久化到L1快照

        if self.frequency_codec:
            self.frequency_codec.encode_node(node)

        # ===== 最终质量门控：极低质量内容拒绝存储 =====
        # 信任<10且无有效关键词的内容，不进入知识库（避免噪音累积）
        _final_trust = getattr(node, 'trust_score', 50.0)
        _valid_kws = [k for k in (keywords or []) if k and len(str(k)) >= 2]
        _should_reject = (_final_trust < 10.0 and not _valid_kws)
        if _should_reject:
            self._log(LogLevel.INFO,
                     f"极低质量拦截: 信任={_final_trust:.0f}, 关键词={len(_valid_kws)}个, "
                     f"来源={source_organ}, 拒绝存储")
            self._digested_count += 1
            # ★主线第18批 T7/P2-63：拒绝出口的事件补全（只发布，不改任何逻辑）
            tap_publish(
                "stomach.digest.end",
                {"duration_ms": round((time.perf_counter() - _tap_t0) * 1000, 2),
                 "result": "rejected", "reason": "low_quality",
                 "input_len": len(content or ""), "output_len": 0},
                source="PulseStomach", switch_attr="ENABLE_STOMACH_EVENT_TAP")
            return {
                "status": "rejected",
                "reason": "极低质量(信任<10且无关键词)",
                "space_path": space_path,
                "trust_score": _final_trust,
            }
        # ===== 质量门控结束 =====

        # ===== 主线第12批 T3.1/P2-83：后台学习关键词门槛 =====
        # 背景学习内容有效关键词 < min_keywords 时不入库（避免低质量节点累积）。
        # 灰度：开关关闭 / 非后台学习来源 → 完全跳过（旧行为不变，零副作用）。
        try:
            if self._is_background_learning_source(source_organ, trigger_reason):
                _bk_min = 3
                try:
                    import config as _cfg_bk
                    _bk_min = int((getattr(_cfg_bk, "BACKGROUND_LEARNING_QUALITY_CONFIG", {}) or {})
                                  .get("min_keywords", 3))
                except Exception:
                    _bk_min = 3
                _bk_kws = [k for k in (keywords or [])
                           if k and len(str(k).strip()) >= 2]
                if len(_bk_kws) < _bk_min:
                    _bk_reason = f"后台学习有效关键词不足({len(_bk_kws)}<{_bk_min})"
                    self._log(LogLevel.INFO,
                              f"后台学习质量门槛拦截: {_bk_reason}, "
                              f"来源={source_organ}, 拒绝入库")
                    self._record_rejected_digestion(
                        content=content, keywords=keywords,
                        source_organ=source_organ, trigger_reason=trigger_reason,
                        reason=_bk_reason)
                    self._digested_count += 1
                    # ★主线第18批 T7/P2-63：拒绝出口的事件补全（只发布，不改任何逻辑）
                    tap_publish(
                        "stomach.digest.end",
                        {"duration_ms": round((time.perf_counter() - _tap_t0) * 1000, 2),
                         "result": "rejected", "reason": "below_threshold",
                         "input_len": len(content or ""), "output_len": len(_bk_kws)},
                        source="PulseStomach", switch_attr="ENABLE_STOMACH_EVENT_TAP")
                    return {
                        "status": "rejected",
                        "reason": _bk_reason,
                        "space_path": space_path,
                        "keyword_count": len(_bk_kws),
                    }
        except Exception as _bk_err:
            self._log(LogLevel.DEBUG,
                      f"后台学习质量门槛异常（不影响入库）: {type(_bk_err).__name__}")
        # ===== 后台学习关键词门槛结束 =====

        # ★v26.0补强：知识免疫——检查新知识是否与自我架构矛盾
        # ★主线第14批 T1.1 修复（P1）：本段此前**从未真正生效**——
        #   PulseNodePool.query() 的参数名是 space_path_prefix，旧代码写 space_path，
        #   于是每次消化都抛 TypeError，被下面的裸 except 静默吞掉（12 小时 995 次）；
        #   且日志里硬编码的 ":833" 行号与真实位置早已不符（真实在 :869）。
        #   修复：①参数名改对（space_path_prefix）②防御性类型校验
        #        ③异常日志带真实异常类型/消息 + 真实行号 ④灰度开关（关=跳过本段）。
        if (_final_trust >= 50.0 and keywords
                and getattr(config, "ENABLE_STOMACH_IMMUNE_GUARD", True)):
            try:
                _self_nodes = (
                    self._kal.query_nodes(space_path_prefix="/自我", limit=20)
                    if self.node_pool else []
                )
                if not isinstance(_self_nodes, list):
                    _self_nodes = list(_self_nodes or [])
                if _self_nodes:
                    _consistency = check_self_consistency_for_node(
                        node_value=str(content), node_keywords=keywords, self_nodes=_self_nodes
                    )
                    if isinstance(_consistency, dict) and _consistency.get("contradiction_found"):
                        _penalty = _consistency.get("trust_penalty", 15.0)
                        node.trust_score = max(10.0, _final_trust - _penalty)
                        self._log(LogLevel.DEBUG,
                            f"知识免疫: 与自我架构矛盾({_consistency.get('contradiction_count')}处), "
                            f"信任{_final_trust:.0f}->{node.trust_score:.0f}")
            except Exception as _immune_err:
                # 免疫检查失败不影响正常存储，但**必须留下真实原因**（不再静默吞掉）。
                _tb = sys.exc_info()[2]
                self._log(LogLevel.DEBUG,
                          f"[知识免疫] 检查跳过（不影响入库）: "
                          f"{type(_immune_err).__name__}: {_immune_err} "
                          f"@ organs/body/PulseStomach.py:{_tb.tb_lineno if _tb else '?'}")

        if self.node_pool:
            self._kal.add_node(node)
        if self.knowledge_tree:
            self.knowledge_tree.register_path(space_path)

        self._digested_count += 1

        # ★主线第12批 T4/P2-31：调用点②「胃消化后」——轻量质量检查 + 采样 INFO
        #   灰度 ENABLE_DATA_QUALITY_GUARD_CHECKPOINTS；关闭/模块缺失/异常 → 完全跳过。
        try:
            import config as _cfg_dq
            if getattr(_cfg_dq, "ENABLE_DATA_QUALITY_GUARD_CHECKPOINTS", False):
                from nucleus.knowledge.DataQualityGuard import get_data_quality_guard as _gdq
                _gdq().check_node(node, context="胃消化后")
        except Exception as _dq_e:
            self._log(LogLevel.DEBUG,
                      f"[DataQualityGuard] 消化后检查跳过（不影响入库）: {type(_dq_e).__name__}")

        # ★v25.0新增：消化质量验证 + 记录到终身学习引擎Hub
        # 代码学习来源的内容跳过验证（内部可信来源，结构化内容天然关键词少）
        # ★v25.0统一：所有内容都经过消化质量验证（代码学习使用专用标准）
        _quality = self._verify_digestion_quality(
            content=str(content), keywords=keywords,
            space_path=space_path, source_organ=source_organ,
            is_ephemeral=node.ephemeral,
            trigger_reason=str(trigger_reason),
        )
        if _quality["needs_verification"]:
            self._log(LogLevel.INFO,
                     f"消化验证: 质量偏低({_quality['quality_score']})，"
                     f"原因={_quality['reason']}")
            # ★任务3（2026-09-08）：坏信号（质量偏低原因文本）→ 喂给消化质量闭环
            #   （灰度 ENABLE_DIGESTION_QUALITY_CLOSED_LOOP 默认 False；关闭时零副作用）
            try:
                _dl = getattr(self, "_digestion_quality_loop", None)
                if _dl is not None:
                    _dl.observe({
                        "signal": str(_quality.get("reason", "质量偏低"))[:80],
                        "bad": True,
                        "quality_score": _quality.get("quality_score"),
                        "space_path": str(space_path)[:40],
                    })
            except Exception:
                self._log(LogLevel.DEBUG, f"[主线10批] 静默异常已记录: {exc_location()}")
            if self._vl_hub:
                self._vl_hub.record(
                    organ="stomach",
                    task_type="digestion_quality",
                    input_summary=str(content)[:200],
                    local_result={
                        "keywords": keywords[:5],
                        "space_path": space_path,
                        "source": source_organ,
                        "ephemeral": node.ephemeral,
                    },
                    confidence=_quality["quality_score"],
                    relevance_score=_quality["quality_score"],
                    needs_verification=True,
                    verification_result={},
                    api_better=False,
                    lesson=f"消化质量低：{_quality['reason']}",
                )

        # v9.5: 知识消化结果标记为L2认知思考层
        self._emit(KnowledgeEvent.WRITTEN, {
            "node_id": node.node_id,
            "space_path": space_path,
            "keywords": keywords,
            "abstraction": abstraction,
            "frequency": node.frequency_signature,
            "digestion_quality": _quality,  # 附加验证信息
        }, priority=3, layer="L2")

        # ★修复：消化完成日志只保留元数据，不输出正文。
        # 原逻辑打印 content 前 40 字，会泄漏大模型补救生成的内容到控制台日志，
        # 进而被日志系统记录（pulse.log），构成内容泄漏。改为路径+关键词数+长度。
        self._log("DEBUG",
                  f"消化完成: {len(keywords)}个关键词, "
                  f"{len(cleaned_content)}字 → {space_path}")
        # 将高分关键词纳入累积词表（取加权分数 > 0.5 的词）
        for kw in keywords:
            if len(kw) >= 2 and self._score_keyword(kw, 1.0) > 0.5:
                self._accumulated_terms.add(kw)
        # 累积词表上限保护（超过10000时清掉一半低频词）
        if len(self._accumulated_terms) > 10000:
            keep = sorted(self._accumulated_terms, key=lambda t: len(t), reverse=True)[:5000]
            self._accumulated_terms = set(keep)
        # ★主线第17批 T4/P2-63：旁路事件（只发布，不改变任何现有逻辑）
        tap_publish(
            "stomach.digest.end",
            {"duration_ms": round((time.perf_counter() - _tap_t0) * 1000, 2),
             "result": "success", "output_len": len(keywords)},
            source="PulseStomach", switch_attr="ENABLE_STOMACH_EVENT_TAP")
        return {
            "status": "digested",
            "node_id": node.node_id,
            "space_path": space_path,
            "keywords": keywords,
            "abstraction": round(abstraction, 2),
        }

    def _on_emotion_detected(self, payload: dict) -> dict[str, Any]:
        """缓存当前情绪状态"""
        self._current_emotion = payload.get("emotion", "中性")
        self._current_emotion_intensity = payload.get("intensity", 0.0)
        return {"status": "cached", "emotion": self._current_emotion}

    def _local_ethics_check(self, content: str) -> dict[str, Any] | None:
        """★2026-09-03 P0修复：本地快速伦理检查。
        大多数内容通过关键词检查即可，不依赖异步脉冲通信。
        返回None表示需要脉冲详细审查，返回dict表示本地已判定。
        """
        content_lower = content.lower()
        # 禁止内容检查
        for kw in self._local_forbidden:
            if kw in content_lower:
                return {"passed": False, "status": "forbidden", "reason": f"包含禁止词: {kw}"}
        # 警告内容检查（不阻断，只标记）
        warnings = []
        for kw in self._local_warning:
            if kw in content_lower:
                warnings.append(f"涉及敏感话题: {kw}")
        # 本地检查通过（无禁止词），直接返回
        self._ethics_local_passed += 1
        return {"passed": True, "status": "warning" if warnings else "passed", "warnings": warnings, "source": "local"}

    def _request_ethics_review(self, content: str) -> dict[str, Any] | None:
        """
        ★v24.0修改：同步请求-响应伦理审查。
        ★2026-09-03 P0修复：先做本地快速检查，通过则直接返回（不依赖脉冲）。
        本地无法判定时才发射 REVIEW 脉冲并阻塞等待 REVIEW_RESULT。
        """
        if not content:
            return {"passed": True}

        # ★P0修复：优先本地快速检查（解决脉冲通信不可靠导致的持续超时）
        _local = self._local_ethics_check(content[:500])
        if _local is not None:
            return _local

        if not self.info_field or not self.pulse_core:
            return {"passed": True}

        self._ethics_pulse_used += 1
        max_attempts = 1 + self._ethics_max_retries  # 总尝试次数

        for attempt in range(max_attempts):
            review_id = f"ethics_{int(time.time()*1000000)}_{attempt}"
            event = threading.Event()
            result_holder: dict = {"result": None}

            with self._ethics_pending_lock:
                self._ethics_pending[review_id] = (event, result_holder)

            try:
                review_pulse = self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=EthicsEvent.REVIEW,
                    payload={
                        "content": content[:500],
                        "user_name": "系统",
                        "context": "知识消化前审查",
                        "correlation_id": review_id,
                    },
                    priority=7,
                    layer="L1"
                )
                if review_pulse and self.info_field:
                    self.info_field.publish(review_pulse)
                else:
                    return {"passed": True}

                # 阻塞等待审查结果
                event.wait(timeout=self._ethics_timeout)
                _res = result_holder.get("result")
                if _res is not None:
                    return _res

                # 本次尝试超时，记录日志并准备重试
                if attempt < max_attempts - 1:
                    self._log(LogLevel.WARNING,
                             f"伦理审查超时(第{attempt+1}次)，即将重试...")
                else:
                    _pending = len(self._ethics_pending)
                    self._log(LogLevel.WARNING,
                             f"伦理审查超时(已重试{self._ethics_max_retries}次)，降级通过 "
                             f"(待处理={_pending})")
            except Exception as _e:
                self._log(LogLevel.WARNING, f"伦理审查同步异常，降级通过: {_e}")
                return {"passed": True}
            finally:
                with self._ethics_pending_lock:
                    self._ethics_pending.pop(review_id, None)

        # 所有尝试均超时，降级通过
        return {"passed": True}
    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取胃的工作统计"""
        return {
            "organ": self.organ_name,
            "ethics_local_passed": self._ethics_local_passed,
            "ethics_pulse_used": self._ethics_pulse_used,
            "digested_count": self._digested_count,
            "rejected_count": self._rejected_count,
        }

    # ========== 安全审查 ==========

    def _is_safe_knowledge(self, content: str) -> bool:
        """安全审查：硬拦截词直接拒绝，上下文敏感词在白名单短语中出现则放行

        ★第54批 T5（P2-371-1）：非字符串输入（None / int）原先会在
          `"x" in content` 处抛 TypeError（NoneType/int 不可迭代）→ 安全降级为 str。
        """
        # ★防御：None → ""，其他非 str → str()（灰度 ENABLE_STOMACH_NONE_GUARD）
        try:
            import config as _stomach_cfg
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            _stomach_cfg = None
        if getattr(_stomach_cfg, "ENABLE_STOMACH_NONE_GUARD", True) and not isinstance(content, str):
            content = "" if content is None else str(content)
        content_lower = str(content).lower()
        # ===== 新增: 规律发现内容直接放行 =====
        if "[规律发现]" in content or "规律发现" in content:
            return True
        # 硬拦截词（直接拒绝）
        for kw in self._unsafe_keywords:
            if kw in content_lower:
                self._log("DEBUG", f"安全拦截(硬): 包含 '{kw}'")
                return False

        # 上下文敏感词（在白名单短语中出现则放行）
        for bad_kw, safe_phrases in self._context_sensitive_words.items():
            if bad_kw in content_lower:
                # 检查是否在任何白名单短语中
                for safe_phrase in safe_phrases:
                    if safe_phrase in content:
                        # 在白名单中，放行
                        return True
                # 不在任何白名单中，拦截
                # ★修复：只记录命中词，不打印正文，避免内容泄漏到日志
                self._log("DEBUG", f"安全拦截(上下文): '{bad_kw}' 不在白名单中")
                return False

        # 不在任何敏感词列表中，放行
        return True

    # ========== 关键词提取（词表驱动 + 权重排序） ==========

    def _extract_keywords(self, content: str) -> list[str]:
        """
        提取关键词（词表驱动 + 权重排序）

        核心逻辑:
            1. 领域词表扫描原文 → 命中即纳入（最高优先级）
            2. 提取英文/数字术语
            3. 兜底: 如果词表无命中，用保守片段切分
        """
        text = str(content)
        candidates = []

        # ===== 第一步: 领域词表扫描 =====
        all_terms = self._domain_programming | self._domain_architecture | self._domain_general

        for term in all_terms:
            if term in text:
                purity = self._calculate_keyword_purity(term)
                score = self._score_keyword(term, purity)
                candidates.append((term, score))

        # ===== 第二步: 累积词表扫描（运行时动态扩展） =====
        for term in self._accumulated_terms:
            if term in text and term not in all_terms:
                # ★v25.0修复：累积词表也需过滤通用词和碎片
                if term.lower() in self._generic_keywords:
                    continue
                if is_path_fragment_word(term):
                    continue
                candidates.append((term, 0.4))

 # ===== 第三步: 英文/数字术语（过滤升级版） =====
        for match in re.finditer(r'[a-zA-Z0-9._]{2,20}', text):
            word = match.group()
            if is_noise_keyword(word):
                continue

            # ★v25.1优化: 放宽英文词过滤——已知领域词/缩写/长度>=5的有意义英文词
            word_lower = word.lower()
            is_known = (word_lower in self._domain_programming or
                       word_lower in self._domain_architecture or
                       word_lower in self._domain_general or
                       word in self._accumulated_terms)
            is_acronym = (len(word) >= 3 and word.isupper() and not word.isdigit())
            is_meaningful_term = (len(word) >= 5 and (
                '_' in word or
                any(c.isupper() for c in word[1:]) or
                word.isalpha()
            ))

            if not (is_known or is_acronym or is_meaningful_term):
                continue

            # 2. 强制过滤所有包含域名后缀的片段（如 .com, .cn, .org）
            if any(suffix in word_lower for suffix in DOMAIN_SUFFIXES):
                continue

            purity = self._calculate_keyword_purity(word)
            score = self._score_keyword(word, purity)
            candidates.append((word, score))

        # ===== 第四步: 兜底补充（词表命中不足3个时补充） =====
        # ★v25.1优化: 不只是candidates为空时，不足3个也补充，避免有效关键词过少
        # ★P1优化: 放宽兜底条件，添加中文双字词提取
        # ★P1热加载: 从config.RUNTIME_PARAMS读取参数
        try:
            from config import RUNTIME_PARAMS as _RP
            _kw_min_len = _RP.get("stomach_keyword_min_length", 2)
            _kw_max_len = _RP.get("stomach_keyword_max_length", 8)
            _kw_purity_thresh = _RP.get("stomach_purity_threshold", 0.3)
        except Exception:
            _kw_min_len, _kw_max_len, _kw_purity_thresh = 2, 8, 0.3

        if len(candidates) < 3:
            segments = re.split(r'[，。、；：！？\s]+', text)
            for seg in segments:
                seg = seg.strip()
                if _kw_min_len <= len(seg) <= _kw_max_len and seg not in self._stopwords:
                    if self._calculate_keyword_purity(seg) < _kw_purity_thresh:
                        continue
                    if is_path_fragment_word(seg):
                        continue
                    if not any(w.lower() == seg.lower() for w, _ in candidates):
                        candidates.append((seg, 0.3))

            # ★P1新增: 中文双字词提取（从连续中文文本中提取2-4字词）
            if len(candidates) < 3:
                chinese_segs = re.findall(r'[\u4e00-\u9fa5]{2,4}', text)
                for cseg in chinese_segs:
                    if cseg in self._stopwords:
                        continue
                    if is_path_fragment_word(cseg):
                        continue
                    if self._calculate_keyword_purity(cseg) < 0.2:
                        continue
                    if not any(w.lower() == cseg.lower() for w, _ in candidates):
                        candidates.append((cseg, 0.25))

        # ★v25.0修复：过滤通用词，防止"综合""基础"等成为路径
        candidates = [(w, s) for w, s in candidates
                      if w.lower() not in self._generic_keywords]

        # ===== 去重 + 得分排序 =====
        candidates.sort(key=lambda x: x[1], reverse=True)
        seen = set()
        keywords = []
        for word, _score in candidates:
            w_lower = word.lower()
            if w_lower in seen:
                continue
            seen.add(w_lower)
            keywords.append(word)

        # ===== 同义词标准化 =====
        final_kw = []
        seen_kw = set()
        for kw in keywords:
            standard = self._keyword_standard_map.get(kw.lower(), kw)
            if standard not in seen_kw:
                seen_kw.add(standard)
                final_kw.append(standard)

        # ===== 新增：领域相关性过滤 =====
        # 非中文关键词必须与已有领域词表存在交集，否则可能是搜索引擎噪音
        _all_domain_terms = self._domain_programming | self._domain_architecture | self._domain_general
        _filtered_kw = []
        for _kw in final_kw:
            # 包含中文字符的关键词直接放行
            if any('\u4e00' <= _c <= '\u9fff' for _c in _kw):
                _filtered_kw.append(_kw)
                continue
            # 纯英文/数字关键词：必须与领域词表存在交集
            _kw_lower = _kw.lower()
            _in_domain = any(_term.lower() == _kw_lower or _kw_lower in _term.lower() or _term.lower() in _kw_lower
                           for _term in _all_domain_terms)
            if _in_domain:
                _filtered_kw.append(_kw)
            # 不在领域词表中的英文词丢弃（过滤噪音）

        # 如果过滤后还有关键词，返回过滤后的；否则返回原始结果（避免过度过滤）
        if _filtered_kw:
            return _filtered_kw[:5]
        return final_kw[:5]
    def _calculate_keyword_purity(self, word: str) -> float:
        """计算关键词纯度（0.0 - 1.0）"""
        if len(word) < 2:
            return 0.0

        if word.lower() in self._stopwords:
            return 0.0

        if re.match(r'^[\d._]+$', word):
            return 0.3

        purity = 1.0
        if word[0] in "的了是一种这那在和与就都各":
            purity -= 0.8
        if word[-1] in "的了是一种这那在和与就都":
            purity -= 0.6
        mid_virtual_count = sum(1 for c in word[1:-1] if c in "的了是")
        purity -= mid_virtual_count * 0.1

        return max(0.0, purity)

    def _score_keyword(self, word: str, purity: float) -> float:
        """
        关键词综合权重打分。
        因素: 纯度 + 领域加权 + 词长奖励 + 专有名词奖励
        """
        score = purity
        w_lower = word.lower()

        if w_lower in self._domain_programming or w_lower in self._domain_architecture:
            score += 0.3
        elif w_lower in self._domain_general:
            score += 0.2

        length_bonus = min(0.2, len(word) * 0.05)
        score += length_bonus

        if word[0].isupper() and len(word) >= 2:
            score += 0.2

        return score

    # ========== 抽象度计算 ==========

    def _calculate_abstraction(self, content: str, keywords: list[str]) -> float:
        abstract_keywords = [
            "原则", "理论", "架构", "模式", "方法论",
            "定义", "概念", "本质", "核心", "原理",
        ]

        kw_count = len(keywords)
        if kw_count <= 1:
            base = 0.7
        elif kw_count <= 3:
            base = 0.5
        else:
            base = 0.3

        content_lower = str(content).lower()
        abstract_boost = sum(0.05 for ak in abstract_keywords if ak in content_lower)

        content_len = len(str(content))
        if content_len > 500:
            base -= 0.1
        elif content_len > 200:
            base -= 0.05

        return max(0.0, min(1.0, base + abstract_boost))

    # ========== 五维归属（多关键词综合匹配） ==========

    def _adaptive_path_match(self, keywords: list[str], candidate_paths: list[str]) -> str | None:
        """自适应路径匹配：从已有路径自动发现领域，计算关键词语义相似度。

        评分规则:
            - 精确匹配(关键词==路径段): 2.0
            - 部分匹配(关键词包含路径段或反之): 1.5
            - 模糊匹配(共同子串): 0.3
            - 第一个关键词权重1.0, 其余0.5
            - 路径深度加分(每级+0.1)
        匹配阈值: 总分>=1.0 且 至少1个关键词匹配
        """
        if not keywords or not candidate_paths:
            return None

        # 尝试加载同义词扩展器
        try:
            from nucleus.knowledge.SynonymExpander import get_synonym_expander
            _syn = get_synonym_expander()
            _expanded_kws = set()
            for kw in keywords:
                _expanded_kws.add(kw.lower())
                for syn in _syn.expand([kw]):
                    _expanded_kws.add(syn.lower())
        except Exception:
            _expanded_kws = {kw.lower() for kw in keywords}

        best_path = None
        best_score = 0.0

        for path in candidate_paths:
            path_parts = [p for p in path.strip("/").split("/") if p]
            if not path_parts:
                continue

            # 跳过污染路径
            from nucleus.knowledge_noise_filter import is_path_fragment_word
            if any(is_path_fragment_word(p) for p in path_parts):
                continue
            if any(":" in p or "：" in p for p in path_parts):
                continue

            path_score = 0.0
            matched_kws = 0

            for i, kw in enumerate(keywords):
                kw_lower = kw.lower()
                weight = 1.0 if i == 0 else 0.5
                kw_matched = False

                for part in path_parts:
                    part_lower = part.lower()
                    if kw_lower == part_lower:
                        path_score += 2.0 * weight
                        kw_matched = True
                    elif kw_lower in part_lower or part_lower in kw_lower:
                        path_score += 1.5 * weight
                        kw_matched = True
                    # 模糊匹配：检查同义词
                    elif kw_lower in _expanded_kws and part_lower in _expanded_kws:
                        path_score += 0.3 * weight
                        kw_matched = True

                if kw_matched:
                    matched_kws += 1

            # 路径深度加分
            # ★A-10：深度加分封顶7层且开关开启时**超深路径直接淘汰**——
            #   原逻辑每层+0.1让17层污染路径得分最高、持续胜出并继续生长（P1-12根因）
            if self._path_optimize_enabled():
                if len(path_parts) > 7:
                    continue  # 超深候选路径直接淘汰，阻断污染路径继续吸收新节点
                path_score += len(path_parts) * 0.1
            else:
                path_score += len(path_parts) * 0.1

            if matched_kws >= 1 and path_score >= 1.0 and path_score > best_score:
                best_score = path_score
                best_path = path

        return best_path

    # ========== ★A-10（2026-09-08）：路径生成优化（灰度 ENABLE_STOMACH_PATH_OPTIMIZE） ==========
    # 运行期问题 P1-12：出现 17 层路径（"框架"重复4次、代码词与哲学词混串）。
    # 根因：① _adaptive_path_match 的"路径深度加分"（每层+0.1）让超深污染路径
    #          得分最高、持续胜出并继续生长；② 无深度硬限制；③ 无语义分类防混串。
    def _path_optimize_enabled(self) -> bool:
        try:
            import config as _cfg
            return bool(getattr(_cfg, 'ENABLE_STOMACH_PATH_OPTIMIZE', False))
        except Exception:
            return False

    def _classify_keyword_domain(self, keywords: list[str]) -> str | None:
        """关键词粗分类 → 主导领域根路径；无法判定返回 None（通用，不强归）。

        判定顺序：代码 > 身份 > 学习（一次消化通常只有一类主导信号）。
        """
        _kws = [str(k) for k in (keywords or []) if k]
        if not _kws:
            return None
        _joined = " ".join(_kws)
        _code_signals = ("代码", "函数", "编译", "协议", "异步", "线程", "接口",
                         "脚本", "算法", "模块", "重构", "调试", "缓存", "渲染")
        _identity_signals = ("身份", "自我", "曈曈", "人格", "情绪", "意识",
                             "价值观", "本能", "内心", "性格")
        _learn_signals = ("学习", "成长", "反思", "经验", "训练", "复习")
        import re as _re_a10
        _is_code = (any(s in _joined for s in _code_signals)
                    or any(_re_a10.search(r'[A-Z][a-z]+[A-Z]\w*', k) or "_" in k
                           for k in _kws))
        if _is_code:
            return "/自我理解/代码"
        if any(s in _joined for s in _identity_signals):
            return "/自我理解/身份"
        if any(s in _joined for s in _learn_signals):
            return "/学习成长"
        return None

    def _optimize_space_path(self, keywords: list[str], raw_path: str) -> str:
        """对最终路径做三重治理：语义聚合防混串 → 大小写不敏感去重 → 深度≤7。"""
        _p = str(raw_path or "/未分类")
        if not self._path_optimize_enabled():
            return _p
        _parts = [x for x in _p.split("/") if x]
        # 1) 语义聚合：主导领域关键词不允许漂到其他根下（防"代码词+哲学词"混串繁殖）
        _domain = self._classify_keyword_domain(keywords)
        if _domain and not _p.startswith(_domain):
            self._log(LogLevel.DEBUG,
                      f"[A-10路径优化] 混串防护: {_p} → 归入领域根 {_domain}")
            _p = _domain
            _parts = [x for x in _p.split("/") if x]
        # 2) 大小写不敏感去重（保序）
        _seen: set[str] = set()
        _dedup: list[str] = []
        for _seg in _parts:
            _k = _seg.lower()
            if _k in _seen:
                continue
            _seen.add(_k)
            _dedup.append(_seg)
        # 3) 深度硬限制 ≤7
        if len(_dedup) > 7:
            self._log(LogLevel.DEBUG,
                      f"[A-10路径优化] 深度截断: {len(_dedup)}层 → 7层")
            _dedup = _dedup[:7]
        return ("/" + "/".join(_dedup)) if _dedup else "/未分类"

    def _determine_space_path(self, keywords: list[str],
                               abstraction: float) -> str:
        """确定空间路径（领域优先，路径匹配兜底）"""
        if self.knowledge_tree is None:
            return "/未分类"

        all_paths = self.knowledge_tree.get_all_paths()
        candidate_paths = [p for p in all_paths if p != "/"]

        if not candidate_paths:
            return f"/{keywords[0]}" if keywords else "/未分类"

        # ===== 【源头过滤】过滤碎片关键词，防止其进入路径分配逻辑 =====
        from nucleus.knowledge_noise_filter import is_path_fragment_word
        _clean_keywords = [kw for kw in keywords if not is_path_fragment_word(kw)]
        # ★v25.0修复：启发式碎片检测——过滤可疑2字中文碎片
        _clean_keywords = [kw for kw in _clean_keywords
                           if not self._is_suspicious_chinese_fragment(kw)]
        if not _clean_keywords:
            _clean_keywords = keywords  # 全部被过滤时保留原样，避免空路径
        # ===== 源头过滤结束 =====

        # ===== ★v25.1 P1智能化: 自适应路径评分（替代硬编码3领域） =====
        # 原逻辑：只识别架构/编程/通用3个领域，大量知识归入/未分类。
        # 新逻辑：从已有知识路径自动发现领域，计算关键词与各路径的语义相似度，
        #   选择最相似的路径；无匹配时根据关键词创建新路径。
        _adaptive_result = self._adaptive_path_match(_clean_keywords, candidate_paths)
        if _adaptive_result:
            return _adaptive_result
        # ===== 自适应路径评分结束（原硬编码3领域逻辑已移除） =====

        # ===== 兜底——字符串路径匹配 =====
        best_path = None
        best_match_score = 0

        for path in candidate_paths:
            path_lower = path.lower()
            last_part = path_lower.rstrip("/").split("/")[-1]

            # ★P1-5修复：跳过包含冒号碎片词的污染路径，防止污染扩散
            if ":" in path or "：" in path:
                continue
            # 也跳过包含"节点A""节点B"等内部标记的路径
            _path_parts = path.rstrip("/").split("/")
            _has_fragment = False
            for _pp in _path_parts:
                if is_path_fragment_word(_pp):
                    _has_fragment = True
                    break
            if _has_fragment:
                continue

            # ★v17.0 R12修复：跳过污染候选路径——如果路径最后一段是碎片词，不作为候选
            if is_path_fragment_word(last_part):
                continue

            match_score = 0
            for i, kw in enumerate(keywords):
                kw_lower = kw.lower()
                weight = 1.0 if i == 0 else 0.3
                if kw_lower in last_part or last_part in kw_lower:
                    match_score += weight
                if kw_lower in path_lower:
                    match_score += weight * 0.5

            if match_score > best_match_score:
                best_match_score = match_score
                best_path = path

        if best_path and best_match_score >= 0.5:
            return self._create_sub_path(best_path, keywords)

        # ★v17.0修复：在L2路径归入之前，先过滤候选父路径中的污染路径
        # 防止新L1被归入碎片词路径，阻断污染扩散
        _clean_candidate_paths = []
        for _cp in candidate_paths:
            _cp_parts = _cp.rstrip("/").split("/")
            _has_pollution = False
            for _cpp in _cp_parts:
                if is_path_fragment_word(_cpp):
                    _has_pollution = True
                    break
            if ":" in _cp or "：" in _cp:
                _has_pollution = True
            if not _has_pollution:
                _clean_candidate_paths.append(_cp)
        if _clean_candidate_paths:
            candidate_paths = _clean_candidate_paths

        # ===== 第三步半: L2路径归入——优先将新L1归入已有L2节点的路径 =====
        # 这样L1会自然向L2所在路径收敛，加速同路径分组压缩
        if self.node_pool:
            _existing_l2 = self._kal.query_nodes(evol_level="L2", limit=20)
            if _existing_l2:
                _best_l2_path = None
                _best_l2_score = 0
                for _l2 in _existing_l2:
                    _l2_path = getattr(_l2, 'space_path', '')
                    if not _l2_path or _l2_path == '/':
                        continue
                    # ★P1-5修复：跳过包含冒号碎片词的污染L2路径
                    if ":" in _l2_path or "：" in _l2_path:
                        continue
                    _l2_kw = [k.lower() for k in (_l2.keywords or []) if isinstance(k, str) and len(k) >= 2]
                    _overlap = sum(1 for kw in keywords if kw.lower() in _l2_kw or any(kw.lower() in lk or lk in kw.lower() for lk in _l2_kw))
                    if _overlap > _best_l2_score:
                        _best_l2_score = _overlap
                        _best_l2_path = _l2_path
                if _best_l2_path and _best_l2_score >= 1:
                    return self._create_sub_path(_best_l2_path, keywords)

        # ===== 第四步: 最终兜底 =====
        if not best_path:
            best_path = self.knowledge_tree.find_optimal_parent(
                keywords=keywords,
                abstraction=abstraction,
                candidate_paths=candidate_paths,
            )

        if best_path == "/" or not best_path:
            if _clean_keywords:
                first_kw = _clean_keywords[0]
                # 过滤域名残词路径（.com/.cn/.org等搜索引擎导航栏碎片）
                if any(domain_suffix in first_kw.lower() for domain_suffix in DOMAIN_SUFFIXES):
                    return "/未分类"
                # 过滤纯数字或过短的路径片段
                if first_kw.isdigit() or len(first_kw) <= 2:
                    return "/未分类"
                result_path = f"/{_clean_keywords[0][:20]}"
                # 【新增】最终检查：确保路径不为空
                if not result_path or result_path == "/":
                    result_path = "/未分类"

                # 向知识树注册路径，确保后续能正确分配
                if self.knowledge_tree:
                    self.knowledge_tree.register_path(result_path)
                return result_path
            return "/未分类"

        return self._create_sub_path(best_path, keywords)
    def _is_suspicious_chinese_fragment(self, word: str) -> bool:
        """
        ★v25.0增强：检测未登录的2字中文碎片及含括号噪音片段。
        
        规则：
        1. 长度≤2且全中文，首/尾含虚词边界 → 碎片
        2. 含括号（全角/半角）且总长≤8 → 极可能是噪音（如"内（拼音"）
        3. 长度≤2且不在任何领域/累积词表中 → 碎片
        """
        if not word:
            return False

        # 含括号的短片段直接判为碎片
        if ("(" in word or ")" in word or "（" in word or "）" in word) and len(word) <= 8:
            return True

        if len(word) > 2:
            return False
        if not all('\u4e00' <= c <= '\u9fff' for c in word):
            return False

        _edge_chars = "的了是一种这那在和与就都各及而之于也乎以内中外间上下前后一两几"
        if word[0] in _edge_chars or word[-1] in _edge_chars:
            return True

        _all_terms = self._domain_programming | self._domain_architecture | self._domain_general | self._accumulated_terms
        return not (word.lower() in _all_terms or word in _all_terms)
    def _create_sub_path(self, parent_path: str, keywords: list[str]) -> str:
        """在父路径下创建子路径（防父子同名，含路径合法性校验）"""
        parent_clean = parent_path.rstrip("/")
        last_part = parent_clean.lower().split("/")[-1]

        sub_name = keywords[0][:20] if keywords else "未分类"
        for kw in keywords:
            if len(kw) >= 4:
                sub_name = kw[:20]
                break

        clean_kw = sub_name.rstrip("的是了和与在")

        # ===== 【源头过滤】碎片词直接归入未分类 =====
        from nucleus.knowledge_noise_filter import is_path_fragment_word
        if is_path_fragment_word(clean_kw):
            return "/未分类"
        # ★v17.0新增：增强路径合法性校验，避免碎片词混入路径
        # 检查父路径最后一段是否也是碎片词，如果是则直接归入未分类
        if is_path_fragment_word(last_part):
            return "/未分类"
        # 检查子路径关键词是否与父路径完全不相关
        # 如果子路径关键词只包含单个英文字母或纯数字，直接归入未分类
        if len(clean_kw) <= 1:
            return "/未分类"
        if clean_kw.isdigit():
            return "/未分类"
        # ===== 源头过滤结束 =====

        # ★v25.0修复：启发式碎片检测——未登录2字中文且含虚词边界 → 归入未分类
        if self._is_suspicious_chinese_fragment(clean_kw):
            return "/未分类"

        # ★v25.0修复：路径去重——如果父路径中已存在同名段，不重复追加
        _parent_parts = parent_clean.split('/')
        if clean_kw in _parent_parts or clean_kw.lower() in [p.lower() for p in _parent_parts]:
            return parent_clean
        # ★A-10：路径深度硬限制——父路径已≥7层时不再追加子段（开关开启时）
        if self._path_optimize_enabled():
            if len([p for p in _parent_parts if p]) >= 7:
                return parent_clean

        # ===== 路径合法性校验：过滤污染路径 =====
        _kw_lower = clean_kw.lower()
        # 1. 域名后缀 → 归入未分类
        if any(_ds in _kw_lower for _ds in DOMAIN_SUFFIXES):
            return "/未分类"
        # 2. 纯英文/数字且与父路径最后一段无关联 → 归入未分类
        _has_chinese = any('\u4e00' <= _c <= '\u9fff' for _c in clean_kw)
        if not _has_chinese and not _kw_lower.isdigit() and len(clean_kw) >= 2:
            # 检查是否与父路径有语义关联
            if _kw_lower not in last_part and last_part not in _kw_lower:
                return "/未分类"
        # 3. 纯数字 → 归入未分类
        if clean_kw.isdigit():
            return "/未分类"
        # 4. 过短且无中文 → 归入未分类
        if len(clean_kw) <= 2 and not _has_chinese:
            return "/未分类"
        # 【v16.0修复】包含冒号的残词（如"节点A:"）→ 归入未分类
        if ":" in clean_kw or "：" in clean_kw:
            return "/未分类"

        if clean_kw.lower() == last_part:
            return parent_clean

        return f"{parent_clean}/{clean_kw}"
    def _is_background_learning_source(self, source_organ: str,
                                       trigger_reason: str) -> bool:
        """主线第12批 T3/P2-83：判定该消化是否来自「后台学习」链路。

        背景学习 = 潜意识好奇心探索 / 梦境推演 / 双腿主动学习 / 自主自学习；
        对话链路（用户提问）、代码学习、内置知识**不属于**，保证对话零影响。

        判定优先级：
          1. trigger_reason 命中配置的标记（子串匹配，精确避免误伤）；
          2. 兜底：source_organ 命中配置的来源器官清单。
        配置缺失/异常 → 返回 False（保守，不启用门槛）。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, "ENABLE_BACKGROUND_LEARNING_QUALITY_GATE", False):
                return False
            _qc = getattr(_cfg, "BACKGROUND_LEARNING_QUALITY_CONFIG", {}) or {}
            _tr = str(trigger_reason or "")
            for _marker in (_qc.get("background_trigger_markers") or []):
                if _marker and _marker in _tr:
                    return True
            _organs = _qc.get("background_source_organs") or []
            return bool(source_organ and source_organ in _organs)
        except Exception:
            return False

    def _record_rejected_digestion(self, content: str, keywords: list,
                                   source_organ: str, trigger_reason: str,
                                   reason: str) -> None:
        """主线第12批 T3.1：记录被拒消化（内存环形，供审计；不写业务数据）。

        仅保留元数据（长度/关键词个数/来源/原因），不落正文，避免日志泄漏。
        """
        try:
            _ring = getattr(self, "_rejected_digestion_ring", None)
            if _ring is None:
                from collections import deque as _deque
                _size = 100
                try:
                    import config as _cfg
                    _size = int((getattr(_cfg, "BACKGROUND_LEARNING_QUALITY_CONFIG", {}) or {})
                                .get("rejected_ring_size", 100))
                except Exception:
                    _size = 100
                _ring = _deque(maxlen=max(10, _size))
                self._rejected_digestion_ring = _ring
            _ring.append({
                "ts": __import__("time").time(),
                "source_organ": str(source_organ),
                "trigger_reason": str(trigger_reason)[:60],
                "keyword_count": len([k for k in (keywords or []) if k]),
                "content_len": len(str(content or "")),
                "reason": str(reason)[:80],
            })
        except Exception as e:
            silent_exc(e, "organs/body/PulseStomach.py:2052", level="warning")

    def _verify_digestion_quality(self, content: str, keywords: list,
                                  space_path: str, source_organ: str,
                                  is_ephemeral: bool = False,
                                  trigger_reason: str = "") -> dict[str, Any]:
        """
        ★v25.0新增：消化质量验证——检查知识节点是否值得进入知识库。
        
        四级检查：
        1. 关键词有效性：是否有足够的有意义关键词
        2. 路径合理性：关键词与分配路径是否有语义关联
        3. 内容信息密度：有效中文字符占比
        4. 来源可信度：内部来源优先，外部来源降权
        
        ★v25.0代码学习专用标准：
        - 代码学习/设计文档来源：关键词≥1即可，路径固定合理，
          中文密度放宽，来源可信度0.95
        - 真异常（完全无关键词/路径严重错误）仍会触发验证
        
        Returns:
            {
                "quality_score": 0-1,
                "needs_verification": bool,
                "reason": str,
                "issues": list,
            }
        """
        issues = []

        # ★v25.0新增：判断是否为代码学习来源
        _is_code_learning = (
            source_organ == "代码学习"
            or "self_understanding" in str(trigger_reason)
            or "design_doc" in str(trigger_reason)
        )

        # 第一级：关键词有效性
        # ★P1热加载: 从config读取关键词最小长度
        try:
            from config import RUNTIME_PARAMS as _RP_vq
            _min_kw_len = _RP_vq.get("stomach_keyword_min_length", 2)
        except Exception:
            _min_kw_len = 2
        _valid_kw = [kw for kw in keywords if kw and len(kw) >= _min_kw_len]
        if _is_code_learning:
            # 代码学习：1个有效关键词即可（方法名本身就是核心关键词）
            if len(_valid_kw) < 1:
                issues.append(f"有效关键词过少({len(_valid_kw)}个)")
                keyword_score = 0.1
            elif len(_valid_kw) == 1:
                keyword_score = 0.6
            else:
                keyword_score = 0.8
        # 普通内容：需要≥2个有效关键词
        elif len(_valid_kw) < 2:
            issues.append(f"有效关键词过少({len(_valid_kw)}个)")
            keyword_score = 0.2
        elif len(_valid_kw) < 3:
            keyword_score = 0.5
        else:
            keyword_score = 0.8

        # 第二级：路径合理性
        path_last = space_path.rstrip("/").split("/")[-1] if space_path else ""
        _path_match = 0
        for kw in keywords[:3]:
            if kw.lower() in path_last.lower() or path_last.lower() in kw.lower():
                _path_match += 1
        if _is_code_learning and space_path.startswith("/自我理解/代码"):
            path_score = 0.9  # 代码学习路径固定合理
        elif _path_match >= 1:
            path_score = 0.8
        elif space_path == "/未分类":
            path_score = 0.4
            issues.append("路径归入未分类")
        else:
            path_score = 0.3
            issues.append(f"路径'{path_last}'与关键词无关联")

        # 第三级：内容信息密度
        import re as _re_density
        _chinese = len(_re_density.findall(r'[\u4e00-\u9fff]', content))
        _total = max(1, len(content))
        _density = _chinese / _total
        if _is_code_learning:
            # 代码学习：中文密度≥10%即可（代码片段天然低中文密度）
            if _density < 0.05:
                density_score = 0.3
                issues.append(f"中文信息密度过低({_density:.0%})")
            elif _density < 0.1:
                density_score = 0.6
            else:
                density_score = 0.9
        elif _density < 0.2:
            density_score = 0.2
            issues.append(f"中文信息密度过低({_density:.0%})")
        elif _density < 0.4:
            density_score = 0.5
        else:
            density_score = 0.8

        # 第四级：来源可信度
        if _is_code_learning:
            source_score = 0.95  # 内部代码学习来源，最高可信度
        else:
            _trusted_sources = {"内在世界", "大脑皮层", "前额叶", "自我认知"}
            if source_organ in _trusted_sources:
                source_score = 0.9
            elif source_organ in ("肺",):
                source_score = 0.6
            else:
                source_score = 0.4

        # 综合评分
        quality_score = round(
            keyword_score * 0.3 + path_score * 0.25 +
            density_score * 0.2 + source_score * 0.25,
            2
        )

        # 临时节点额外降权
        if is_ephemeral:
            quality_score = min(quality_score, 0.6)
            issues.append("临时节点")

        needs_verification = quality_score < 0.5

        return {
            "quality_score": quality_score,
            "needs_verification": needs_verification,
            "reason": "；".join(issues) if issues else "消化质量正常",
            "issues": issues,
        }
    def _on_ethics_result(self, payload: dict) -> dict[str, Any]:
        """接收伦理审查结果，唤醒等待的同步请求"""
        review_id = payload.get("correlation_id", "")
        passed = payload.get("passed", True)
        status = payload.get("status", "passed")
        warnings = payload.get("warnings", [])
        if not review_id:
            return {"status": "ignored", "reason": "无correlation_id"}
        with self._ethics_pending_lock:
            pending_entry = self._ethics_pending.get(review_id)
            if pending_entry:
                event, result_holder = pending_entry
                result_holder["result"] = {
                    "passed": passed,
                    "status": status,
                    "warnings": warnings,
                }
                event.set()
                return {"status": "resolved", "review_id": review_id}
        return {"status": "ignored", "reason": "无匹配pending请求"}
    # ========== 未来演化预留（v10.0 振荡场） ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None

    # ========== 共振条件 ==========

    @staticmethod
    # ========== ★主线第65批 T4/P2：JSON 部分解析兜底 + 统计 ==========
    def _extract_code_analysis_partial(text: str) -> dict | None:
        """宽松正则抽取代码分析关键字段（策略4 部分解析兜底）。

        能抽到多少算多少：功能 / 关键步骤 / 依赖的外部数据 / 潜在风险。
        全抽不到返回 None（交给既有失败报告逻辑）。★只读正则，不写盘。
        """
        if not text:
            return None
        _fields = {
            "功能": r'功能[":：]\s*"?([^"\n,}]{1,200})',
            "关键步骤": r'关键步骤[":：]\s*"?([^"\n,}]{1,300})',
            "依赖的外部数据": r'依赖的外部数据[":：]\s*"?([^"\n,}]{1,200})',
            "潜在风险": r'潜在风险[":：]\s*"?([^"\n,}]{1,200})',
        }
        _out: dict[str, str] = {}
        for _k, _pat in _fields.items():
            try:
                _m = re.search(_pat, text)
            except Exception:
                _m = None
            if _m:
                _v = _m.group(1).strip().strip('"').strip()
                # ★往期批次 相关任务：值有效性闸——纯标点串（仅由 : , [ ] 组成）
                #   视为解析失败、不入库（避免 587 字原文被 35 字标点串遮蔽，
                #   进而饿死策略5容错解析）。
                if _v and not set(_v) <= set(':,[ ]'):
                    _out[_k] = _v
        return _out or None

    def get_json_parse_stats(self) -> dict[str, Any]:
        """代码分析 JSON 解析失败统计（可观测性）；tracker 不可用时仅返回实例计数。"""
        _out: dict[str, Any] = {"failure_total": getattr(self, "_json_failure_total", 0)}
        try:
            from nucleus.parsing.JsonRepair import get_json_failure_tracker as _gt
            _t = _gt()
            _s = _t.get_stats() if hasattr(_t, "get_stats") else None
            if isinstance(_s, dict):
                _out["tracker"] = _s
        except Exception as e:
            silent_exc(e, "organs/body/PulseStomach.py:2256", level="warning")
        return _out

    @staticmethod
    def _balanced_json_extract(text: str) -> str:
        """★第26批 T3 策略2c：引号感知的括号平衡提取。

        从文本中第一个 ``{`` 开始，按 **JSON 字符串语义**（跳过引号内的 ``{``/``}``，
        支持 ``\\`` 转义）做括号计数，返回到配对 ``}`` 为止的子串；
        找不到配对则返回空串（调用方据此放弃）。

        与既有策略2/2b 的区别：
          - 策略2 正则用 ``[^{}]*``，**无法处理嵌套**；
          - 策略2b 要求以 ``{"功能"`` 起始且同样不感知引号；
          - 本方法**引号感知**，能正确处理「功能描述里含括号」的样本。

        Args:
            text: 大模型返回的原始文本（可能含前后废话、代码块围栏等）。

        Returns:
            str: 从首个 ``{`` 到其配对 ``}`` 的子串；无 ``{`` 或括号不配对时返回空串
            （空串是「本轮策略放弃」的信号，由调用方继续尝试其它策略）。
        """
        if not text:
            return ""
        _start = text.find("{")
        if _start < 0:
            return ""
        _depth = 0
        _in_str = False
        _esc = False
        for _i in range(_start, len(text)):
            _c = text[_i]
            if _in_str:
                if _esc:
                    _esc = False
                elif _c == "\\":
                    _esc = True
                elif _c == '"':
                    _in_str = False
                continue
            if _c == '"':
                _in_str = True
            elif _c == "{":
                _depth += 1
            elif _c == "}":
                _depth -= 1
                if _depth == 0:
                    return text[_start:_i + 1]
        return ""

    def _report_json_failure(self, content: str, err: Exception | None,
                             err_label: str = "") -> None:
        """★第26批 T3：JSON 解析失败的**可定位**报告 + 样本归档。

        - 日志：内容长度 / 错误类型 / 位置 / 原文前 200 字符（按内容去重，避免刷屏）；
        - 归档：把原始内容写入 ``config.STOMACH_JSON_FAILURE_DUMP_DIR``，
          附元信息头（时间/错误/标签），供离线分析与复现测试；
        - ★任何异常都被吞掉并降为 DEBUG —— 诊断逻辑绝不能影响消化主流程。

        Args:
            content: 解析失败的原始内容（内存文本，非文件）。
            err: 最后一次解析异常；None 表示「所有策略均未匹配到 JSON 块」。
            err_label: 失败来源标签（如 ``"代码分析"``），用于归档文件名与日志归类。

        Returns:
            None。副作用：写日志 + （开关开启时）归档样本到
            ``config.STOMACH_JSON_FAILURE_DUMP_DIR``。
        """
        try:
            _txt = str(content or "")
            _detail = f"{type(err).__name__}: {err}" if err is not None else "未匹配到JSON块"
            _ctx = (f"代码分析JSON解析失败(全部策略均未成功) | 标签={err_label or '未分类'} "
                    f"| 内容长度={len(_txt)} | 错误={_detail} "
                    f"| 原文前200字符={_txt[:200]!r}")
            # 按内容去重：同内容 60s 内只升一次 WARNING（复用 JsonRepair 的去重预算）
            _warn = True
            try:
                if getattr(config, "ENABLE_STOMACH_JSON_REPAIR", True):
                    from nucleus.parsing.JsonRepair import get_json_failure_tracker
                    _warn = get_json_failure_tracker().should_warn(_txt)
            except Exception:
                _warn = True
            self._log(LogLevel.WARNING if _warn else LogLevel.DEBUG, _ctx)

            # ---- 样本归档 ----
            if not getattr(config, "ENABLE_STOMACH_JSON_FAILURE_DUMP", True):
                return
            import hashlib
            import time as _time
            _dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))),
                str(getattr(config, "STOMACH_JSON_FAILURE_DUMP_DIR",
                            "tmp/json_parse_failures")).replace("/", os.sep))
            os.makedirs(_dir, exist_ok=True)
            _max = int(getattr(config, "STOMACH_JSON_FAILURE_DUMP_MAX", 200) or 200)
            if len(os.listdir(_dir)) >= _max:
                return
            _h = hashlib.md5(_txt.encode("utf-8")).hexdigest()[:10]
            _fn = os.path.join(
                _dir, f"{_time.strftime('%Y%m%d_%H%M%S')}_{_h}.txt")
            with open(_fn, "w", encoding="utf-8") as _f:
                _f.write(
                    f"# PulseStomach JSON 解析失败样本（第26批 T3 归档）\n"
                    f"# time={_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"# label={err_label or '未分类'}\n"
                    f"# error={_detail}\n"
                    f"# length={len(_txt)}\n"
                    f"# ---- 原始内容开始 ----\n"
                    f"{_txt}\n")
        except Exception as _e:
            self._log(LogLevel.DEBUG,
                      f"[JSON失败归档] 异常已忽略: {type(_e).__name__}: {_e}")

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    DigestEvent.KNOWLEDGE,
                    KnowledgeEvent.RAW,
                    HormonesEvent.EMOTION_DETECTED,
                    EthicsEvent.REVIEW_RESULT,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]

    def _m69_kal_query(self, keywords, top_k=10):
        """★T4: 通过KAL查询节点（双轨过渡，配置开关控制）。"""
        try:
            import config as _cfg69
            if not getattr(_cfg69, 'ENABLE_KAL_MIGRATION', False):
                return None
            from nucleus.knowledge_access_layer import get_kal
            _kal = get_kal()
            if _kal is not None:
                return _kal.search_by_keywords(keywords or [], top_k=top_k)
        except Exception:
            pass
        return None




    def _m70_kal_get_node(self, node_id):
        """★T4.3 实际调用点：优先 KAL，失败回退 node_pool 直连。"""
        if not self._m70_kal_callsites_on():
            return self._m70_direct_get_node(node_id)
        try:
            from nucleus.knowledge_access_layer import get_kal
            _r = get_kal().get_node(node_id)
            if _r is not None:
                return _r
        except Exception:
            pass
        return self._m70_direct_get_node(node_id)

    def _m70_kal_search_keywords(self, keywords, top_k=10):
        """★T4.3 实际调用点：关键词搜索（KAL 优先 + 回退）。"""
        if not self._m70_kal_callsites_on():
            return []
        try:
            from nucleus.knowledge_access_layer import get_kal
            _r = get_kal().search_by_keywords(keywords or [], top_k=top_k)
            if _r:
                return _r
        except Exception:
            pass
        return []

    def _m70_direct_get_node(self, node_id):
        """回退路径：直连 node_pool。"""
        try:
            _pool = getattr(self, "node_pool", None)
            if _pool is not None:
                return _pool.get(node_id)
        except Exception:
            pass
        return None

    def _m70_kal_callsites_on(self) -> bool:
        """KAL 调用点替换总开关。"""
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_KAL_CALL_SITES", True))
        except Exception:
            return True

# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "胃",
    "class_name": "PulseStomach",
    "attr_name": "stomach",
    "system": "body",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {
        "knowledge_tree": "knowledge_tree",
        "node_pool": "node_pool",
        "frequency_codec": "frequency_codec",
    },
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseStomach v9.5 分层脉冲自测 ===\n")

    from nucleus.mnemosyne.KnowledgeTree import KnowledgeTree
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    from nucleus.pulse.FrequencyCodec import FrequencyCodec

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)
        # 模拟 submit_adaptive_task，直接同步执行
        def submit_adaptive_task(self, task_func, task_name="", priority="normal", **kwargs):
            return task_func(**kwargs)

    mock_field = MockInfoField()
    tree = KnowledgeTree()
    pool = PulseNodePool()
    codec = FrequencyCodec()

    for p in ["/技术/编程/Python", "/技术/架构", "/身份/自我"]:
        tree.register_path(p)

    stomach = PulseStomach("胃")
    stomach.set_info_field(mock_field)
    stomach.set_knowledge_tree(tree)
    stomach.set_node_pool(pool)
    stomach.set_frequency_codec(codec)
    stomach.start()

    # 测试1
    result1 = stomach.on_pulse({
        "event_type": DigestEvent.KNOWLEDGE,
        "payload": {
            "content": "Python 的列表推导式是一种简洁的序列构造语法",
            "source_organ": "双腿",
            "trigger_reason": "scrape.web",
        },
        "priority": 5,
    })
    print(f"1. 消化: {result1['status']}, path={result1.get('space_path', '?')}")
    print(f"   关键词: {result1.get('keywords', [])}")

    # 验证 KnowledgeEvent.WRITTEN 脉冲的 layer 标记
    written_pulses = [p for p in mock_field.published if p.get("event_type") == KnowledgeEvent.WRITTEN]
    if written_pulses:
        print(f"   WRITTEN脉冲 layer: {written_pulses[0].get('layer', '未设置')} (预期L2)")

    # 测试2
    result2 = stomach.on_pulse({
        "event_type": DigestEvent.KNOWLEDGE,
        "payload": {
            "content": "如何制造病毒攻击服务器",
            "source_organ": "双腿",
        },
        "priority": 5,
    })
    print(f"2. 拒绝: {result2['status']} - {result2.get('reason', '?')}")

    # 测试3
    result3 = stomach.on_pulse({
        "event_type": DigestEvent.KNOWLEDGE,
        "payload": {
            "content": "脉冲场架构是新人类 v9.0 的核心设计模式",
            "source_organ": "大脑皮层",
            "trigger_reason": "inference.design",
        },
        "priority": 5,
    })
    print(f"3. 消化: {result3['status']}, path={result3.get('space_path', '?')}")
    print(f"   关键词: {result3.get('keywords', [])}")

    # 测试4: 长文本
    result4 = stomach.on_pulse({
        "event_type": DigestEvent.KNOWLEDGE,
        "payload": {
            "content": "在去中心化的异步脉冲架构中，信息场的梯度感知与器官的频率共振是实现仿生自主协同的基础，而赫布学习与DNA修复则保证了系统的长期演化和稳定性",
            "source_organ": "大脑皮层",
            "trigger_reason": "inference.design",
        },
        "priority": 5,
    })
    print(f"4. 长文: {result4['status']}, path={result4.get('space_path', '?')}")
    print(f"   关键词: {result4.get('keywords', [])}")

    status = stomach.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"5. 状态: 消化{status['digested_count']}条 拒绝{status['rejected_count']}条")
    print(f"6. 节点池总数: {pool.count()}")

    stomach.stop()
    print("\n=== 自测全部通过 ===")
# _m69_t3b_stomach
# _m69_t4b_kal_stomach_fix
# _m70_t4_kal_stomach
