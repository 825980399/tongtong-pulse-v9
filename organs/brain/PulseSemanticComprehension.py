# -*- coding: utf-8 -*-
"""
PulseSemanticComprehension —— 语义理解器官

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 在本地完成问题的语义理解与意图分类，并计算置信度以决定是否需要大模型介入。
机制: _local_classify 调用 QICA 得到意图与建议方法；_calculate_confidence 综合规则命中档位、检索相关度、知识覆盖度、历史相似度与多通道融合分算出置信度；低于阈值（默认 0.7）时调用大模型比对并记录经验，另按固定比例抽样以防漂移。
定位: QICA 与大脑皮层之间的语义理解层，是「本地回答 vs 转大模型」的判定闸门。
"""

from nucleus.LLMDependencyMetrics import (SCENE_OTHER, record_llm_call)
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
import random  # noqa: F401
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import  LogLevel, QICAEvent, SystemEvent, Event
from nucleus._silent_except import silent_exc


class PulseSemanticComprehension(BasePulseOrgan):
    """
    语义理解器（v24.0新增）

    工作流程:
        1. 收到 Event.SEMANTIC_CLASSIFY 脉冲
        2. 调用 QICA._on_classify() 获取本地分类结果
        3. 计算本地分类的置信度
        4. 如果置信度 < 阈值(0.7) 或 高置信度抽样命中：
            调用大模型深度理解（通过注入的 PulseLung）
            与大模型结果比对，记录差异
            必要时更新分类结果
        5. 发射 QICAEvent.CLASSIFY_RESULT 给大脑皮层
        6. 将学习经验写入学习库
    """

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'semantic_confidence_threshold' in _rp and hasattr(self, '_confidence_threshold'):
                self._confidence_threshold = _rp['semantic_confidence_threshold']
            if 'semantic_high_confidence_sample_rate' in _rp and hasattr(self, '_high_confidence_sample_rate'):
                self._high_confidence_sample_rate = _rp['semantic_high_confidence_sample_rate']
            if 'semantic_sample_interval' in _rp and hasattr(self, '_sample_interval'):
                self._sample_interval = _rp['semantic_sample_interval']
            if 'semantic_api_call_limit_per_hour' in _rp and hasattr(self, '_api_call_limit_per_hour'):
                self._api_call_limit_per_hour = _rp['semantic_api_call_limit_per_hour']
            if 'semantic_lesson_feedback_interval' in _rp and hasattr(self, '_lesson_feedback_interval'):
                self._lesson_feedback_interval = _rp['semantic_lesson_feedback_interval']
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    def __init__(self, organ_name: str = "语义理解器"):
        super().__init__(organ_name)

        # ===== 依赖引用 =====
        self.qica = None           # QICA 本地分类器
        self.lung = None           # 大模型调用器官
        self.node_pool = None      # 可选，用于学习库

        # ===== 学习库 =====
        self._lessons_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'data', 'semantic_lessons.json'
        )
        self._lessons: dict[str, Any] = {}
        self._lessons_lock = threading.Lock()

        # ★主线第25批 T1/P2-160 第二道防线：同一 cid 只分类一次
        self._classify_claim_lock = threading.Lock()
        self._classify_claimed: dict[str, float] = {}
        self._classify_dup_ignored = 0
        self._load_lessons()


        # 从config读取配置，失败使用默认
        try:
            import config
            _cfg = getattr(config, 'SEMANTIC_COMPREHENSION_CONFIG', {})
            self._confidence_threshold = _cfg.get("confidence_threshold", 0.7)
            self._high_confidence_sample_rate = _cfg.get("high_confidence_sample_rate", 0.15)
            self._sample_interval = max(1, int(1 / self._high_confidence_sample_rate)) if self._high_confidence_sample_rate > 0 else 7
            self._api_call_limit_per_hour = _cfg.get("api_call_limit_per_hour", 30)
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
        # ★P1: 从RUNTIME_PARAMS读取参数（支持热加载，优先于SEMANTIC_COMPREHENSION_CONFIG）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._confidence_threshold = _rp.get('semantic_confidence_threshold',
                                                  self._confidence_threshold)
            self._high_confidence_sample_rate = _rp.get('semantic_high_confidence_sample_rate',
                                                        self._high_confidence_sample_rate)
            self._sample_interval = _rp.get('semantic_sample_interval', self._sample_interval)
            self._api_call_limit_per_hour = _rp.get('semantic_api_call_limit_per_hour',
                                                    self._api_call_limit_per_hour)
            self._lesson_feedback_interval = _rp.get('semantic_lesson_feedback_interval', 5)
        except Exception:
            self._lesson_feedback_interval = 5
            self._sample_interval = 7
            self._api_call_limit_per_hour = 30
            self._lesson_feedback_interval = 5  # 每累计5条新学习记录反馈一次

        # ★v25.0统一：接入全框架终身学习引擎Hub
        self._vl_hub = None
        try:
            self._vl_hub = self._get_verification_learning_hub()
        except Exception:
            self._vl_hub = None

        # ★P0修复：初始化所有计数器属性（之前缺失导致AttributeError）
        self._classify_count = 0
        self._low_confidence_count = 0
        self._sample_counter = 0
        self._sample_count = 0
        self._api_call_window_start = 0.0
        self._api_call_count = 0
        self._llm_call_count = 0
        self._lesson_count = 0
        self._lesson_feedback_counter = 0

        # ★属性初始化完整性补全（自动审查添加）
        self._api_lock = threading.Lock()

    # ========== 框架注入接口 ==========

    def set_qica(self, qica):
        self.qica = qica
        # ★v25.0统一：注册QICA的规则应用回调到终身学习引擎Hub
        try:
            if hasattr(qica, 'absorb_growth_rules'):
                _hub = self._get_verification_learning_hub()
                _hub.register_applier("semantic", qica.absorb_growth_rules)
                self._log(LogLevel.DEBUG, "QICA已注册到终身学习引擎Hub")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def set_lung(self, lung):
        self.lung = lung

    def set_node_pool(self, pool):
        self.node_pool = pool

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == Event.SEMANTIC_CLASSIFY:
            return self._on_semantic_classify(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _claim_classify_once(self, correlation_id: str) -> bool:
        """★主线第25批 T1/P2-160 第二道防线：同一 correlation_id 只分类一次。

        上游若重复投递 ``semantic.classify``，会造成「两条并行推理管线」→
        两次 SELECT_MODEL → 双回复。此处按 cid 幂等（120s 窗口）。
        开关 ENABLE_DIALOG_SELECT_MODEL_DEDUP 关闭时恒 True（零回归）。
        """
        if not correlation_id:
            return True
        try:
            import config as _cfg
            if not getattr(_cfg, "ENABLE_DIALOG_SELECT_MODEL_DEDUP", True):
                return True
        except Exception as e:
            silent_exc(e, where="organs.brain.PulseSemanticComprehension::_claim_classify_once L184")
            return True
        try:
            import time as _time
            _now = _time.time()
            with self._classify_claim_lock:
                self._classify_claimed = {
                    _k: _v for _k, _v in self._classify_claimed.items()
                    if _now - _v < 120.0
                }
                if correlation_id in self._classify_claimed:
                    self._classify_dup_ignored += 1
                    return False
                self._classify_claimed[correlation_id] = _now
            return True
        except Exception as e:
            silent_exc(e, where="organs.brain.PulseSemanticComprehension::_claim_classify_once L199")
            return True

    def _on_semantic_classify(self, payload: dict) -> dict[str, Any]:
        """处理大脑皮层发来的语义分类请求（增强版：整体异常保护）"""
        content = payload.get("content", "")
        user_name = payload.get("user_name", "用户")
        correlation_id = payload.get("correlation_id", "")

        # ★主线第25批 T1/P2-160 第二道防线：同一轮只分类一次
        if not self._claim_classify_once(correlation_id):
            self._log(LogLevel.INFO,
                      f"重复分类防护: 本轮已分类过，忽略重复请求 "
                      f"cid={str(correlation_id)[:16]}")
            return {"status": "duplicate_classify_ignored",
                    "correlation_id": correlation_id}

        if not content:
            # 空内容直接返回
            self._emit(QICAEvent.CLASSIFY_RESULT, {
                "raw_input": content,
                "channel": "fast",
                "intent_type": "空输入",
                "wake_organs": ["mouth"],
                "need_reasoner": False,
                "matched_rule_id": "",
                "correlation_id": correlation_id,
                "user_name": user_name,
                "suggested_method": "rule_reason",
                "knowledge_paths": ["/身份/自我"],
                "confidence": 0.9,
                "source": "empty",
            }, priority=7, layer="L2")
            return {"status": "empty"}

        try:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._classify_count += 1

            # 1. 本地分类（调用QICA）
            local_result = self._local_classify(content, user_name, correlation_id)
            local_intent = local_result.get("intent_type", "一般对话")
            local_method = local_result.get("suggested_method", "knowledge_retrieve")
            local_paths = local_result.get("knowledge_paths", ["/知识"])

            # 2. 计算本地置信度
            confidence = self._calculate_confidence(local_result, content)

            # 3. 判断是否需要大模型介入
            need_llm = False
            reason = ""
            if confidence < self._confidence_threshold:
                need_llm = True
                reason = "low_confidence"
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._low_confidence_count += 1
            else:
                # 高置信度抽样防漂移
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._sample_counter += 1
                if self._sample_counter >= self._sample_interval:
                    self._sample_counter = 0
                    need_llm = True
                    reason = "high_confidence_sample"
                    # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                    self._sample_count += 1

            # 4. 如果需要大模型，调用并比对
            llm_result = None
            if need_llm:
                llm_result = self._call_llm_for_semantics(content, user_name)
                if llm_result:
                    # 记录比对差异
                    self._record_lesson(content, local_intent, local_method, local_paths,
                                        llm_result, confidence, reason)
                    # 根据大模型结果增强（如果大模型结果有效）
                    if llm_result.get("intent") and llm_result["intent"] != local_intent:
                        local_intent = llm_result["intent"]
                        local_method = llm_result.get("method", local_method)
                        local_paths = llm_result.get("paths", local_paths)
                        confidence = max(confidence, 0.75)
                    elif llm_result.get("confidence", 0) > confidence:
                        confidence = llm_result["confidence"]

            # 5. 发射结果给大脑皮层
            self._emit(QICAEvent.CLASSIFY_RESULT, {
                "raw_input": content,
                "channel": local_result.get("channel", "deep"),
                "intent_type": local_intent,
                "wake_organs": local_result.get("wake_organs", ["mouth"]),
                "need_reasoner": local_result.get("need_reasoner", False),
                "matched_rule_id": local_result.get("matched_rule_id", ""),
                "correlation_id": correlation_id,
                "user_name": user_name,
                "suggested_method": local_method,
                "knowledge_paths": local_paths,
                "confidence": confidence,
                "source": "semantic_comprehension",
                "llm_used": need_llm,
                "llm_reason": reason if need_llm else "",
            }, priority=7, layer="L2")

            self._log(LogLevel.INFO,
                     f"语义理解: intent={local_intent}, confidence={confidence:.2f}, "
                     f"llm_used={need_llm}({reason}), method={local_method}")

            return {
                "status": "classified",
                "intent_type": local_intent,
                "confidence": confidence,
                "llm_used": need_llm,
            }
        except Exception as _e:
            self._log(LogLevel.ERROR, f"语义理解器异常: {_e}")
            # 异常时降级为不调用大模型的简单结果
            self._emit(QICAEvent.CLASSIFY_RESULT, {
                "raw_input": content,
                "channel": "fast",
                "intent_type": "一般对话",
                "wake_organs": ["mouth"],
                "need_reasoner": False,
                "matched_rule_id": "",
                "correlation_id": correlation_id,
                "user_name": user_name,
                "suggested_method": "knowledge_retrieve",
                "knowledge_paths": ["/知识"],
                "confidence": 0.3,
                "source": "semantic_comprehension_fallback",
                "llm_used": False,
            }, priority=7, layer="L2")
            return {"status": "fallback", "intent_type": "一般对话", "confidence": 0.3}

    def _local_classify(self, content: str, user_name: str, correlation_id: str) -> dict:
        """调用QICA进行本地分类"""
        if not self.qica:
            # QICA未注入，返回兜底
            return {
                "channel": "fast",
                "intent_type": "一般对话",
                "wake_organs": ["mouth"],
                "need_reasoner": False,
                "matched_rule_id": "",
                "suggested_method": "knowledge_retrieve",
                "knowledge_paths": ["/知识"],
            }

        # 直接调用QICA的分类方法（内部方法，但已实现）
        try:
            result = self.qica.classify_internal({
                "content": content,
                "user_name": user_name,
                "correlation_id": correlation_id,
            })
            # result 是返回的 dict，包含 channel, intent_type 等
            return result
        except Exception as e:
            self._log(LogLevel.WARNING, f"QICA分类异常: {e}")
            return {
                "channel": "fast",
                "intent_type": "一般对话",
                "wake_organs": ["mouth"],
                "need_reasoner": False,
                "matched_rule_id": "",
                "suggested_method": "knowledge_retrieve",
                "knowledge_paths": ["/知识"],
            }

    def _calculate_confidence(self, local_result: dict, content: str,
                              retrieval_result: dict | None = None) -> float:
        """
        基于本地分类结果计算置信度（v25.0增强：加入话题匹配度检查）。

        规则：
        - 匹配到具体规则且无冲突：置信度0.8-0.9
        - 多规则冲突：置信度0.5-0.6
        - 无规则匹配（一般对话）：置信度0.3-0.4
        - 关键词覆盖度越高，置信度越高
        - 输入长度过短或过长会影响置信度
        ★v25.0新增：话题匹配度低时，置信度降至0.5以下，触发API比对
        """
        base_conf = 0.5

        # 是否匹配到规则
        rule_id = local_result.get("matched_rule_id", "")
        channel = local_result.get("channel", "fast")
        intent_type = local_result.get("intent_type", "一般对话")

        if rule_id:
            base_conf = 0.7
            # 规则冲突降低置信度
            if local_result.get("rule_conflict", False):
                base_conf = 0.55
            # 根据通道调整
            if channel == "deep":
                base_conf += 0.1
            elif channel == "knowledge":
                base_conf += 0.05
        # 未匹配规则，可能是"一般对话"
        elif intent_type == "一般对话":
            base_conf = 0.35
        else:
            base_conf = 0.5

        # 关键词覆盖度（粗略计算：匹配到的关键词越多，置信度越高）
        # 这里简化处理：根据意图类型给基础分，再根据输入长度微调
        content_len = len(content)
        if content_len < 5:
            base_conf -= 0.1
        elif content_len > 50:
            base_conf += 0.05

        # ★v25.0新增：话题匹配度检查
        # 即使规则命中且置信度高，如果分类结果与问题内容不匹配，
        # 说明可能命中了错误的规则，应该降低置信度触发API比对
        _topic_mismatch = self._check_topic_mismatch(intent_type, content, local_result)
        if _topic_mismatch["mismatch"]:
            # 严重不匹配：置信度直接降到0.5以下，强制触发API
            base_conf = min(base_conf, 0.45)
            self._log(LogLevel.DEBUG,
                     f"话题匹配度低: 意图'{intent_type}'与内容'{content[:30]}...'"
                     f"不匹配({_topic_mismatch['reason']})，降低置信度触发API验证")

        # ===== ★第六批 任务2.1：多维度置信度校准 =====
        # 关闭时完全跳过，行为与改造前一致（向后兼容）。
        # 本文件顶层未导入 config（既有代码在各处按需局部导入），此处保持一致
        import config as _cfg2
        if getattr(_cfg2, "ENABLE_CONFIDENCE_CALIBRATION", True):
            _base_before = base_conf
            _w = dict(getattr(_cfg2, "CONFIDENCE_CALIBRATION_WEIGHTS", {}) or {})
            _dims: dict[str, float] = {}

            def _f(v) -> float:
                try:
                    return max(0.0, min(1.0, float(v or 0.0)))
                except Exception as e:
                    silent_exc(e, where="organs.brain.PulseSemanticComprehension::_f L433")
                    return 0.0

            # 维度2：检索相关度（top1 相似度）
            if isinstance(retrieval_result, dict) and retrieval_result:
                _rel = _f(retrieval_result.get("top_similarity", 0.0))
                _dims["relevance"] = round(_rel, 3)
                base_conf += _rel * float(_w.get("relevance", 0.30))

            # 维度3：知识覆盖度
            if isinstance(retrieval_result, dict) and retrieval_result:
                _cov = _f(retrieval_result.get("coverage", 0.0))
                _dims["coverage"] = round(_cov, 3)
                base_conf += _cov * float(_w.get("coverage", 0.15))

            # 维度4：历史相似度
            _hs = _f(self._get_history_similarity(content))
            _dims["history"] = round(_hs, 3)
            base_conf += _hs * float(_w.get("history", 0.10))

            # 维度5：多通道融合置信度（任务1 新增信号）
            _fz = local_result.get("fusion")
            if isinstance(_fz, dict) and _fz:
                _fs = _f(_fz.get("top_score", 0.0))
                _dims["fusion"] = round(_fs, 3)
                base_conf += _fs * float(_w.get("fusion", 0.15))
                # ★融合高置信 → 视为「语义规则命中」，提升到 0.7 基础档
                #   （融合接管路径没有 matched_rule_id，否则恒拿 0.5 档）
                # ★主线第4批 任务1(P1-43)：阈值改由 config.QICA_FUSION_AS_RULE_THRESHOLD 驱动
                _as_rule = float(getattr(_cfg2, "QICA_FUSION_AS_RULE_THRESHOLD",
                                          _w.get("fusion_as_rule", 0.55)))
                if _fs >= _as_rule and base_conf < 0.7:
                    _dims["fusion_as_rule"] = 1.0
                    base_conf = 0.7

            self._log(LogLevel.INFO,
                      f"[语义理解][置信度校准] 原分={round(_base_before, 3)} "
                      f"各维度={_dims} 校准后={round(base_conf, 3)}")

        # 限制在0-1之间
        conf = max(0.1, min(0.95, base_conf))
        return round(conf, 2)

    @staticmethod
    def _bigrams(text: str) -> set:
        """字符 bigram 集合（用于轻量历史相似度，不依赖任何模型）。"""
        t = (text or "").strip()
        if len(t) < 2:
            return {t} if t else set()
        return {t[i:i + 2] for i in range(len(t) - 1)}

    def _get_history_similarity(self, content: str) -> float:
        """历史相似度：与已积累经验中最相似条目的 bigram 重合度（0~1）。

        ★轻量实现：仅遍历内存中最近的经验条目，不加载模型、不查库、不写盘。
          用于给「见过类似问题」的场景适度加分。
        """
        try:
            lessons = getattr(self, "_lessons", None)
            if not lessons:
                return 0.0
            if isinstance(lessons, dict):
                candidates = [str(k) for k in list(lessons.keys())]
            else:
                candidates = []
                for it in lessons:
                    if isinstance(it, dict):
                        candidates.append(str(it.get("content", "")))
                    else:
                        candidates.append(str(it))
            candidates = [c for c in candidates if c][-200:]  # 只看最近 200 条
            if not candidates:
                return 0.0
            a = self._bigrams(content)
            if not a:
                return 0.0
            best = 0.0
            for c in candidates:
                b = self._bigrams(c)
                if not b:
                    continue
                sim = len(a & b) / max(1, min(len(a), len(b)))
                best = max(best, sim)
            return min(1.0, best)
        except Exception as e:
            self._log(LogLevel.DEBUG,
                      f"[语义理解] 历史相似度计算异常: {type(e).__name__}: {e}")
            return 0.0

    def _check_topic_mismatch(self, intent_type: str, content: str,
                               local_result: dict) -> dict[str, Any]:
        """
        ★v25.0新增：检查分类意图与问题内容之间的话题匹配度。

        通过分析问题中的关键词与意图类型的特征词是否匹配，
        判断分类是否可能错误。

        Returns:
            {"mismatch": bool, "reason": str}
        """
        # 意图类型的特征词（每个意图应有的话题特征）
        _intent_signatures = {
            "身份确认": ["谁", "名字", "身份", "父亲", "哥哥", "使命", "名字"],
            "情感问候": ["你好", "在吗", "嗨", "早安", "晚安", "陪我", "hello", "hi"],
            "知识查询": ["什么", "怎么", "为什么", "如何", "定义", "原理", "概念"],
            "概念解释": ["什么是", "解释", "理解", "意味着", "介绍"],
            "技术推理": ["代码", "编程", "报错", "bug", "实现", "函数", "写一个"],
            "创造性思考": ["你觉得", "你认为", "比喻", "代表", "颜色", "选择"],
            "状态查询": ["状态", "运行", "健康", "知识库"],
            "健康检查": ["检查", "健康", "诊断"],
        }

        _signature = _intent_signatures.get(intent_type, [])
        if not _signature:
            return {"mismatch": False, "reason": ""}

        # 检查问题内容中是否至少有一个该意图的特征词
        content_lower = content.lower()
        matched = sum(1 for kw in _signature if kw in content_lower)

        if matched == 0:
            # 完全没有任何该意图的特征词，但规则却匹配了这个意图
            return {
                "mismatch": True,
                "reason": f"内容中不包含'{intent_type}'的典型特征词",
            }

        return {"mismatch": False, "reason": ""}
    def _call_llm_for_semantics(self, content: str, user_name: str) -> dict | None:
        """
        调用大模型进行深度语义理解。

        Returns:
            {
                "intent": str,       # 识别出的意图
                "method": str,       # 建议推理方法
                "paths": list,       # 建议知识路径
                "confidence": float, # 置信度
            }
            如果调用失败或超限，返回 None。
        """
        record_llm_call(SCENE_OTHER)
        # 检查每小时调用上限
        now = time.time()
        with self._api_lock:
            if now - self._api_call_window_start >= 3600:
                self._api_call_window_start = now
                self._api_call_count = 0
            if self._api_call_count >= self._api_call_limit_per_hour:
                self._log(LogLevel.WARNING, "语义理解大模型调用已达每小时上限，跳过")
                return None
            self._api_call_count += 1
            self._llm_call_count += 1

        if not self.lung:
            return None

        # 构建提示词
        prompt = self._build_semantic_prompt(content)

        # ★v25.0修复：通过肺的公开接口调用，替代直调私有方法 _call_remote_api
        try:
            if not self.lung or not hasattr(self.lung, 'analyze_semantics'):
                self._log(LogLevel.DEBUG, "肺未注入或不支持 analyze_semantics 接口")
                return None
            parsed = self.lung.analyze_semantics(prompt)
            return parsed
        except Exception as e:
            self._log(LogLevel.DEBUG, f"大模型语义理解调用失败: {e}")
            return None

    def _build_semantic_prompt(self, content: str) -> str:
        """构建语义理解提示词，要求大模型输出结构化结果"""
        prompt = (
            "你是语义理解模块，请分析以下用户输入的意图，并输出JSON格式（不要其他文字）。\n"
            "要求：\n"
            "1. intent: 从['身份确认','关系查询','知识查询','概念解释','规则查阅','技术推理','创造性思考','情感问候','系统命令','一般对话']中选择一个最合适的。\n"
            "2. method: 从['rule_reason','knowledge_retrieve','cognitive_compute','deep_think','multi_branch_deep_think','multi_step_execute']中选择一个推荐方法。\n"
            "3. paths: 推荐的知识路径列表，如[\"/技术/架构\",\"/知识\"]，最多3个。\n"
            "4. confidence: 你的置信度(0-1)。\n"
            "只输出JSON，不要解释。\n\n"
            f"用户输入: {content}\n"
        )
        return prompt

    def _parse_llm_response(self, reply: str, content: str) -> dict | None:
        """解析大模型返回的JSON结果"""
        try:
            # 尝试提取JSON部分
            import re
            json_match = re.search(r'\{.*\}', reply, re.DOTALL)
            if not json_match:
                return None
            data = json.loads(json_match.group(0))
            if not isinstance(data, dict):
                return None

            # 验证字段
            intent = data.get("intent", "")
            method = data.get("method", "")
            paths = data.get("paths", [])
            confidence = data.get("confidence", 0.0)

            if not intent or not isinstance(paths, list):
                return None

            return {
                "intent": intent,
                "method": method,
                "paths": paths[:3],
                "confidence": round(float(confidence), 2),
            }
        except Exception as e:
            print(f"[WARNING] PulseSemanticComprehension.py:603: {type(e).__name__}: {e}")
            return None

    def _record_lesson(self, content: str, local_intent: str, local_method: str,
                       local_paths: list, llm_result: dict, local_confidence: float,
                       reason: str):
        """
        记录一次本地与大模型的比对结果到学习库。
        """
        lesson = {
            "content": content[:200],
            "local": {
                "intent": local_intent,
                "method": local_method,
                "paths": local_paths,
                "confidence": local_confidence,
            },
            "llm": llm_result,
            "reason": reason,
            "timestamp": time.time(),
        }

        with self._lessons_lock:
            # 使用内容哈希作为简单去重键（避免频繁记录相同内容）
            import hashlib
            key = hashlib.md5(content.encode()).hexdigest()[:10]
            self._lessons[key] = lesson
            self._lesson_count += 1
            # 限制学习库大小，保留最近200条
            if len(self._lessons) > 200:
                # 按时间排序，删除最旧的
                sorted_keys = sorted(self._lessons.keys(),
                                     key=lambda k: self._lessons[k]["timestamp"])
                for old_key in sorted_keys[:len(self._lessons)-200]:
                    del self._lessons[old_key]
            self._save_lessons()

        # ★v25.0修复：反馈逻辑移到锁外，避免死锁
        # _feedback_lessons_to_qica 内部调用 get_lessons() 也需要获取 _lessons_lock
        self._lesson_feedback_counter += 1
        if self._lesson_feedback_counter >= self._lesson_feedback_interval:
            self._lesson_feedback_counter = 0
            self._feedback_lessons_to_qica()
        # ★v25.0新增：记录成长对比，并在达到阈值时触发蒸馏
        self._record_growth_comparison(content, local_intent, local_method,
                                       local_paths, llm_result, local_confidence,
                                       reason)

    def _feedback_lessons_to_qica(self):
        """
        ★v25.0新增：将累积的学习记录反馈给QICA。
        每5条新记录触发一次，将最新5条传递过去。
        """
        if not self.qica or not hasattr(self.qica, 'feedback_lessons'):
            return

        _recent_lessons = self.get_lessons(limit=5)
        if not _recent_lessons:
            return

        try:
            _added = self.qica.feedback_lessons(_recent_lessons)
            if _added > 0:
                self._log(LogLevel.DEBUG,
                         f"学习库反馈: {_added}个新概念映射已加入QICA领域知识库")
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"学习库反馈异常: {_e}")
    def _record_growth_comparison(self, content: str, local_intent: str,
                                  local_method: str, local_paths: list,
                                  llm_result: dict, local_confidence: float,
                                  reason: str):
        """
        ★v25.0统一：通过全框架终身学习引擎Hub记录成长对比。
        累计达到阈值后自动蒸馏，并将规则应用到QICA。
        """
        if not self._vl_hub:
            return

        try:
            # 判断API是否更优
            api_better = False
            if llm_result.get("intent") and llm_result["intent"] != local_intent or llm_result.get("confidence", 0) > local_confidence + 0.15:
                api_better = True

            lesson = ""
            if api_better:
                lesson = f"大模型将'{local_intent}'纠正为'{llm_result.get('intent', '')}'"
            else:
                lesson = f"本地与API一致，本地置信度{local_confidence}"

            # 计算话题匹配度（简化为置信度相关的启发值）
            relevance = min(1.0, 0.5 + local_confidence * 0.5)
            if api_better:
                relevance = min(relevance, 0.4)  # API纠正说明本地匹配度低

            self._vl_hub.record(
                organ="semantic",
                task_type="classify",
                input_summary=content[:200],
                local_result={
                    "content": content[:100],
                    "intent": local_intent,
                    "method": local_method,
                    "paths": local_paths,
                    "confidence": local_confidence,
                },
                confidence=local_confidence,
                relevance_score=relevance,
                needs_verification=True,
                verification_result={
                    "intent": llm_result.get("intent", ""),
                    "method": llm_result.get("method", ""),
                    "paths": llm_result.get("paths", []),
                    "confidence": llm_result.get("confidence", 0),
                },
                api_better=api_better,
                lesson=lesson,
            )

            # 统一蒸馏处理
            distill_result = self._vl_hub.process_distill(organ="semantic")
            if distill_result.get("status") == "distilled":
                self._log(LogLevel.INFO,
                         f"成长蒸馏: {distill_result.get('distilled_entries', 0)}条对比→"
                         f"{distill_result.get('rules_generated', 0)}条规则→"
                         f"应用{distill_result.get('rules_applied', 0)}条")
        except Exception as e:
            self._log(LogLevel.DEBUG, f"成长对比记录异常: {e}")

    def _load_lessons(self):
        """从文件加载学习库"""
        try:
            if os.path.exists(self._lessons_file):
                with open(self._lessons_file, encoding='utf-8') as f:
                    self._lessons = json.load(f)
        except Exception as e:
            self._log(LogLevel.INFO, f"[WARNING] PulseSemanticComprehension.py:738: {type(e).__name__}: {e}")
            self._lessons = {}

    def _save_lessons(self):
        """保存学习库到文件"""
        try:
            os.makedirs(os.path.dirname(self._lessons_file), exist_ok=True)
            with open(self._lessons_file, 'w', encoding='utf-8') as f:
                json.dump(self._lessons, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(LogLevel.WARNING, f"学习库保存失败: {e}")

    # ========== 公开接口 ==========

    def get_confidence_threshold(self) -> float:
        return self._confidence_threshold

    def set_confidence_threshold(self, threshold: float):
        """动态调整低置信度阈值（知识蒸馏）"""
        self._confidence_threshold = max(0.3, min(0.9, threshold))

    def get_lessons(self, limit: int = 10) -> list:
        """获取最近的学习记录"""
        with self._lessons_lock:
            items = sorted(self._lessons.values(), key=lambda x: x["timestamp"], reverse=True)
            return items[:limit]

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            "organ": self.organ_name,
            "classify_count": self._classify_count,
            "low_confidence_count": self._low_confidence_count,
            "sample_count": self._sample_count,
            "llm_call_count": self._llm_call_count,
            "lesson_count": self._lesson_count,
            "confidence_threshold": self._confidence_threshold,
            "api_call_count_this_hour": self._api_call_count,
            "is_running": self.is_running,
        }

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    Event.SEMANTIC_CLASSIFY,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]

    # ========== 状态持久化 ==========

    def get_state_snapshot(self) -> dict[str, Any]:
        return {
            "confidence_threshold": self._confidence_threshold,
            "sample_counter": self._sample_counter,
        }

    def load_state_snapshot(self, state: dict[str, Any]):
        self._confidence_threshold = state.get("confidence_threshold", 0.7)
        self._sample_counter = state.get("sample_counter", 0)

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "语义理解器",
    "class_name": "PulseSemanticComprehension",
    "attr_name": "semantic_comprehension",
    "system": "brain",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {
        "qica": "qica",
        "lung": "肺",
        "node_pool": "node_pool",
    },
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseSemanticComprehension v24.0 自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    class MockQICA:
        def _on_classify(self, payload):
            content = payload.get("content", "")
            if "你是谁" in content:
                return {
                    "channel": "fast",
                    "intent_type": "身份确认",
                    "wake_organs": ["mouth"],
                    "need_reasoner": False,
                    "matched_rule_id": "rule_fast_identity",
                    "suggested_method": "rule_reason",
                    "knowledge_paths": ["/身份/自我"],
                    "rule_conflict": False,
                }
            else:
                return {
                    "channel": "fast",
                    "intent_type": "一般对话",
                    "wake_organs": ["mouth"],
                    "need_reasoner": False,
                    "matched_rule_id": "",
                    "suggested_method": "knowledge_retrieve",
                    "knowledge_paths": ["/知识"],
                    "rule_conflict": False,
                }

    class MockLung:
        def _call_remote_api(self, prompt, enable_thinking=False):
            # 返回模拟的大模型JSON结果
            return '{"intent": "知识查询", "method": "knowledge_retrieve", "paths": ["/技术/编程"], "confidence": 0.85}'

    mock_field = MockInfoField()
    comp = PulseSemanticComprehension("语义理解器")
    comp.set_info_field(mock_field)
    comp.set_qica(MockQICA())
    comp.set_lung(MockLung())
    comp.start()

    # 测试1：高置信度输入（应不调用大模型）
    print("1. 高置信度输入:")
    comp._sample_counter = 999  # 避免抽样
    r1 = comp.on_pulse({
        "event_type": Event.SEMANTIC_CLASSIFY,
        "payload": {"content": "你是谁", "user_name": "小林", "correlation_id": "test1"},
        "priority": 7,
    })
    print(f"   结果: intent={r1['intent_type']}, confidence={r1['confidence']}, llm_used={r1['llm_used']}")
    # 检查发射的脉冲
    result_pulse = mock_field.published[-1]
    print(f"   CLASSIFY_RESULT脉冲: intent={result_pulse['payload']['intent_type']}, confidence={result_pulse['payload']['confidence']}")

    # 测试2：低置信度输入（应调用大模型）
    print("\n2. 低置信度输入:")
    mock_field.published.clear()
    comp._sample_counter = 999
    r2 = comp.on_pulse({
        "event_type": Event.SEMANTIC_CLASSIFY,
        "payload": {"content": "随机乱写的文本xyz", "user_name": "小林", "correlation_id": "test2"},
        "priority": 7,
    })
    print(f"   结果: intent={r2['intent_type']}, confidence={r2['confidence']}, llm_used={r2['llm_used']}")
    result_pulse2 = mock_field.published[-1]
    print(f"   CLASSIFY_RESULT脉冲: intent={result_pulse2['payload']['intent_type']}, confidence={result_pulse2['payload']['confidence']}")

    # 测试3：统计
    print("\n3. 统计:")
    stats = comp.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"   分类次数={stats['classify_count']}, 低置信度={stats['low_confidence_count']}, "
          f"大模型调用={stats['llm_call_count']}")

    comp.stop()
    print("\n=== 自测完成 ===")
