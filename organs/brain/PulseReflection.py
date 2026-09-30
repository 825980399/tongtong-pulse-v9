# -*- coding: utf-8 -*-
"""
PulseReflection —— 前额叶反思器官

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 对对话与推理过程做复盘和自我反思，产出可消费的反思洞察。
机制: set_node_pool / set_resonance_engine / set_experience_pool 注入依赖；_enqueue_reflection 入队、_submit_reflection_task 提交异步反思任务以避免阻塞主链路；反思结果回写节点池与经验池。
定位: 框架的「复盘」器官，位于推理链路之后、学习进化之前。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import os
import sys
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    HeartEvent,
    HormonesEvent,
    LogLevel,
    MouthEvent,
    NarrativeEvent,
    PersonaEvent,
    ReflectionEvent,
)
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus._silent_except import silent_exc


class PulseReflection(BasePulseOrgan):
    """前额叶 —— 对话复盘与自我反思器官（共享记忆版 · v9.5 分层脉冲版）"""


    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'reflection_min_issues_to_store' in _rp and hasattr(self, '_min_issues_to_store'):
                setattr(self, '_min_issues_to_store', _rp['reflection_min_issues_to_store'])
            if 'reflection_success_importance' in _rp and hasattr(self, '_success_importance'):
                setattr(self, '_success_importance', _rp['reflection_success_importance'])
            if 'reflection_issue_importance' in _rp and hasattr(self, '_issue_importance'):
                setattr(self, '_issue_importance', _rp['reflection_issue_importance'])
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
    def __init__(self, organ_name: str = "前额叶"):
        super().__init__(organ_name)

        self._reflection_queue: list[dict[str, Any]] = []
        self._max_queue_size = 100
        self._reflection_count = 0
        self._issue_count = 0

        self._lock = threading.Lock()

        self._recent_reflections: list[dict[str, Any]] = []

        self._domain_keywords = self._load_domain_keywords()

        # 异步复盘防重入
        self._reflection_pending = False

        # ★v25.0新增：体验池引用
        self.experience_pool = None
        # ★回归修复：显式初始化依赖引用，避免未注入时访问报 AttributeError
        self.node_pool = None
        self.resonance_engine = None

    # ========== 依赖注入 ==========

    def set_node_pool(self, node_pool):
        self.node_pool = node_pool

    def set_resonance_engine(self, resonance_engine):
        self.resonance_engine = resonance_engine
    def set_experience_pool(self, pool):
        self.experience_pool = pool
    # ========== 生命周期 ==========

    def start(self):
        self.is_running = True
        self._log(LogLevel.INFO, f"已启动，待复盘队列上限={self._max_queue_size}")

    def stop(self):
        self.is_running = False
        with self._lock:
            pending = len(self._reflection_queue)
        self._log(LogLevel.INFO, f"已停止，复盘{self._reflection_count}次, "
                 f"发现问题{self._issue_count}个, 待处理{pending}条")

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_running:
            return None

        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == MouthEvent.SPEAK:
            # 快速入队，异步复盘
            self._enqueue_reflection(payload)
            self._submit_reflection_task()
            return None  # 不返回同步结果，复盘在后台完成

        elif event_type == HeartEvent.BEAT:
            return self._process_queue()

        return None

    def get_resonance_conditions(self) -> list[dict[str, Any]]:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [MouthEvent.SPEAK, HeartEvent.BEAT],
                "min_priority": 1,
            },
        ]

    # ========== 复盘队列管理 ==========

    def _enqueue_reflection(self, payload: dict[str, Any]):
        with self._lock:
            record = {
                "timestamp": time.time(),
                "user_input": payload.get("user_input", ""),
                "response": payload.get("response", ""),
                "reasoning_path": payload.get("reasoning_path", "unknown"),
                "user_name": payload.get("user_name", "unknown"),
                "reflected": False,
            }
            self._reflection_queue.append(record)

            if len(self._reflection_queue) > self._max_queue_size:
                self._reflection_queue.pop(0)

    def _submit_reflection_task(self):
        """异步提交复盘任务"""
        if self._reflection_pending:
            return  # 已有任务在执行，防止堆积

        if self.info_field and hasattr(self.info_field, 'submit_adaptive_task'):
            self._reflection_pending = True
            success = self.info_field.submit_adaptive_task(
                self._do_reflect,
                task_name="前额叶复盘",
                priority="normal"
            )
            if not success:
                # 提交失败，重置标记并同步执行
                self._reflection_pending = False
                self._do_reflect()
        else:
            # 信息场不可用，同步执行
            self._do_reflect()

    def _do_reflect(self):
        """执行复盘（可在异步线程中运行）"""
        try:
            # 处理当前队列中所有未复盘的记录
            with self._lock:
                pending = [r for r in self._reflection_queue if not r.get("reflected", False)]

            if not pending:
                return

            processed = 0
            issues = 0

            for record in pending:
                result = self._analyze_interaction(record)
                if result and result.get("has_issues"):
                    issues += 1
                processed += 1

                with self._lock:
                    record["reflected"] = True

            self._reflection_count += processed
            self._issue_count += issues

        except Exception as e:
            self._log(LogLevel.ERROR, f"异步复盘异常: {e}")
        finally:
            self._reflection_pending = False

    def _reflect_on_latest(self) -> dict[str, Any] | None:
        """保留原有逻辑（心跳批量处理时使用）"""
        with self._lock:
            if not self._reflection_queue:
                return None

            record = None
            for item in reversed(self._reflection_queue):
                if not item.get("reflected", False):
                    record = item
                    break

            if record is None:
                return None

        result = self._analyze_interaction(record)

        with self._lock:
            record["reflected"] = True

        return result

    def _process_queue(self) -> dict[str, Any]:
        """心跳批量处理（保留原有逻辑作为兜底）"""
        with self._lock:
            pending = [r for r in self._reflection_queue if not r.get("reflected", False)]

        if not pending:
            return {"processed": 0, "issues_found": 0}

        processed = 0
        issues = 0

        for record in pending[:10]:
            result = self._analyze_interaction(record)
            if result and result.get("has_issues"):
                issues += 1
            processed += 1

            with self._lock:
                record["reflected"] = True

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._reflection_count += processed
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._issue_count += issues

        return {
            "processed": processed,
            "issues_found": issues,
            "total_reflected": self._reflection_count,
        }
    def _load_domain_keywords(self) -> dict:
        """从config加载复盘领域词表，失败时用兜底"""
        try:
            import config
            cfg = getattr(config, 'REFLECTION_DOMAINS', {})
            if cfg:
                return cfg
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
        return {
            "身份": ["是谁", "身份", "使命"],
            "技术": ["代码", "编程", "架构"],
            "关系": ["你觉得", "我们", "朋友"],
            "知识": ["什么是", "如何", "为什么"],
        }
    # ========== 领域推断 ==========

    def _infer_domain(self, text: str) -> str:
        for domain, keywords in self._domain_keywords.items():
            if any(kw in text for kw in keywords):
                return domain
        return "通用"

    # ========== 复盘分析核心逻辑 ==========
    def _analyze_interaction(self, record: dict[str, Any]) -> dict[str, Any]:
        user_input = record.get("user_input", "")
        response = record.get("response", "")
        reasoning_path = record.get("reasoning_path", "")
        user_name = record.get("user_name", "")

        issues = []
        insights = []
        domain = self._infer_domain(user_input)

        reasoning_quality = self._check_reasoning_quality(reasoning_path, user_input, response)
        if reasoning_quality.get("issue"):
            issues.append(reasoning_quality["issue"])
        if reasoning_quality.get("insight"):
            insights.append(reasoning_quality["insight"])

        consistency_result = self._check_personality_consistency(user_input, response)
        if consistency_result.get("issue"):
            issues.append(consistency_result["issue"])
        if consistency_result.get("insight"):
            insights.append(consistency_result["insight"])

        relation_check = self._check_relation_adaptation(user_name, user_input, response)
        if relation_check.get("issue"):
            issues.append(relation_check["issue"])
        if relation_check.get("insight"):
            insights.append(relation_check["insight"])

        # ===== 新增: 社交反馈感知——检查对方是否回应了主动表达 =====
        social_feedback = self._check_social_feedback(user_input, response, user_name)
        if social_feedback.get("issue"):
            issues.append(social_feedback["issue"])
        if social_feedback.get("insight"):
            insights.append(social_feedback["insight"])

        # ===== 社交反馈闭环——根据反馈类型发射脉冲给自我认知调整关系维度 =====
        if self.info_field and self.pulse_core:
            feedback_type = social_feedback.get("feedback_type", "")
            if feedback_type == "warmth":
                # 温暖回应→提升情感羁绊和亲密度
                self.info_field.publish(self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=PersonaEvent.RECORD_INTERACTION,
                    payload={
                        "user_name": user_name,
                        "content": user_input[:100],
                        "depth": "deep",
                        "interaction_type": "emotional_sharing",
                        "emotional_tone": "positive",
                        "feedback_type": "warmth",
                    },
                    priority=4,
                    layer="L2"
                ))
            elif feedback_type == "engagement":
                # 话题延续→提升共享经验和尊重度
                self.info_field.publish(self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=PersonaEvent.RECORD_INTERACTION,
                    payload={
                        "user_name": user_name,
                        "content": user_input[:100],
                        "depth": "normal",
                        "interaction_type": "collaboration",
                        "feedback_type": "engagement",
                    },
                    priority=4,
                    layer="L2"
                ))
            elif feedback_type == "cold":
                # 回应简短→轻微降低亲密度但提升理解度
                self.info_field.publish(self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=PersonaEvent.RECORD_INTERACTION,
                    payload={
                        "user_name": user_name,
                        "content": user_input[:100],
                        "depth": "normal",
                        "interaction_type": "conflict",
                        "emotional_tone": "neutral",
                        "feedback_type": "cold",
                    },
                    priority=4,
                    layer="L2"
                ))

        # ===== v21.0新增：坚韧品格·归因层——连续失败归因分类 =====
        _failure_attribution = self._classify_failure_attribution(reasoning_path, user_input, response)
        if _failure_attribution:
            # 将归因结果写入InsightBoard，供迭代层查询
            if self.info_field and self.pulse_core:
                try:
                    from nucleus.InsightBoard import get_insight_board
                    _board = get_insight_board()
                    _board.post(
                        insight_type="failure_attribution",
                        content=f"失败归因({_failure_attribution['type']}): {_failure_attribution['reason'][:120]}",
                        source_loop="坚韧品格·归因层",
                        related_dimension=_failure_attribution["type"],
                        confidence=0.7,
                        keywords=["失败归因", _failure_attribution["type"], "坚韧"]
                    )
                except Exception as e:
                    silent_exc(e, where="organs.brain.PulseReflection::_analyze_interaction L374")
        # ===== v21.0新增结束 =====

        has_issues = len(issues) > 0

        # 构建优化提示（无问题时也能生成优化方向）
        optimization_hints = []
        if not has_issues and insights:
            for insight in insights:
                if "规则推理" in insight:
                    optimization_hints.append("将规则推理的成功模式扩展到更多领域")
                elif "核心使命" in insight:
                    optimization_hints.append("研究不同身份下使命表达的最佳方式")
                elif "差异化" in insight:
                    optimization_hints.append("总结差异化适配模式，形成通用行为指导")
        if not optimization_hints:
            optimization_hints.append(f"回顾{domain}领域的回复质量，寻找可以进一步提升的细节")

        reflection_result = {
            "timestamp": time.time(),
            "user_input": user_input[:100],
            "response_preview": response[:100],
            "reasoning_path": reasoning_path,
            "has_issues": has_issues,
            "issue_count": len(issues),
            "issues": issues,
            "insights": insights,
            "domain": domain,
            "quality_score": self._calculate_quality_score(issues),
        }

        with self._lock:
            self._recent_reflections.append(reflection_result)
            if len(self._recent_reflections) > 10:
                self._recent_reflections.pop(0)

        # ★v25.0新增：复盘结果记录到体验池
        if self.experience_pool:
            try:
                if has_issues:
                    # 发现问题 → 负向体验（反思/成长）
                    self.experience_pool.record_experience(
                        motivation="提升对话质量",
                        motivation_intensity=0.7,
                        process_pressure=0.5,
                        pressure_type="cognitive",
                        reward_type="cognitive",
                        reward_intensity=0.3,
                        emotion_tags=["反思", "自我改进"],
                        emotion_intensity=0.5,
                        content=f"复盘发现{len(issues)}个问题（{domain}领域）：{issues[0][:80] if issues else ''}"
                    )
                else:
                    # 无问题 → 正向体验（确认/巩固）
                    self.experience_pool.record_experience(
                        motivation="保持对话质量",
                        motivation_intensity=0.4,
                        process_pressure=0.2,
                        pressure_type="cognitive",
                        reward_type="cognitive",
                        reward_intensity=0.6,
                        emotion_tags=["满足", "确认"],
                        emotion_intensity=0.4,
                        content=f"复盘确认对话质量良好（{domain}领域，评分{reflection_result['quality_score']:.1f}）"
                    )
                self._log(LogLevel.DEBUG,
                         f"复盘体验记录: 质量={reflection_result['quality_score']:.1f}, "
                         f"问题={len(issues)}个")
            except Exception as e:
                silent_exc(e, where="organs.brain.PulseReflection::_analyze_interaction L443")
        # ===== 社会性情感触发：复盘发现身份侵蚀时触发愧疚，获得表扬时触发自豪 =====
        if self.info_field and self.pulse_core:
            social_triggers = {}
            if has_issues:
                for issue in issues:
                    if "AI" in issue or "身份" in issue:
                        social_triggers["愧疚"] = 0.6
            # 使用 reflection_result 中的 quality_score
            quality_score = reflection_result.get("quality_score", 0.0)
            if quality_score >= 0.8 and domain == "身份":
                social_triggers["自豪"] = 0.4
            if user_name == "小林" and quality_score >= 0.9:
                social_triggers["感激"] = 0.5

            for social_type, _social_intensity in social_triggers.items():
                self.info_field.publish(self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=HormonesEvent.DETECT,
                    payload={
                        "content": f"复盘触发{social_type}: {reflection_result.get('issues', [''])[0] if social_type == '愧疚' else '表现良好'}",
                        "user_name": user_name,
                    },
                    priority=4,
                    layer="L2"
                ))
        # ===== 始终发射 INSIGHT 脉冲（有/无问题都发射） =====
        if self.info_field and self.pulse_core:
            issue_types = []
            suggested_actions = []

            if has_issues:
                # 有问题时的处理（保持原有逻辑）
                for issue in issues:
                    if "AI" in issue:
                        issue_types.append("identity_erosion")
                        suggested_actions.append("reinforce_identity")
                    elif "推理路径" in issue:
                        issue_types.append("reasoning_failure")
                        suggested_actions.append("prefer_rule_inference")
                    elif "对话对象" in issue:
                        issue_types.append("relation_mismatch")
                        suggested_actions.append("improve_user_recognition")

                # v9.5: 复盘洞察脉冲标记为L2认知思考层
                insight_pulse = self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=ReflectionEvent.INSIGHT,
                    payload={
                        "domain": domain,
                        "assessment_type": "issue",
                        "issue_types": list(set(issue_types)),
                        "suggested_search": self._extract_core_terms(user_input, issues),
                        "issues": issues,
                        "quality_score": reflection_result["quality_score"],
                        "suggested_actions": list(set(suggested_actions)),
                        "user_name": user_name,
                        "timestamp": time.time(),
                    },
                    priority=4,
                    layer="L2"
                )
                self.info_field.publish(insight_pulse)

                # v9.5: 问题发现脉冲
                self.info_field.publish(
                    self.pulse_core.emit(
                        source_organ=self.organ_name,
                        event_type=ReflectionEvent.ISSUE_FOUND,
                        payload={
                            "issues": issues,
                            "insights": insights,
                            "quality_score": reflection_result["quality_score"],
                            "domain": domain,
                        },
                        priority=4,
                        layer="L2"
                    )
                )
            else:
                # 无问题时的优化模式（新增）
                insight_pulse = self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=ReflectionEvent.INSIGHT,
                    payload={
                        "domain": domain,
                        "assessment_type": "optimization",
                        "issue_types": [],
                        "quality_score": reflection_result["quality_score"],
                        "suggested_actions": optimization_hints,
                        "insights": insights,
                        "optimization_hints": optimization_hints,
                        "user_name": user_name,
                        "timestamp": time.time(),
                    },
                    priority=4,
                    layer="L2"
                )
                self.info_field.publish(insight_pulse)

        # ===== 发射叙事记录脉冲（让叙事自我从复盘中学习） =====
        if self.info_field and self.pulse_core:
            narrative_content = (
                f"复盘({domain}领域): 质量评分={reflection_result['quality_score']:.1f}, "
                f"发现问题={len(issues)}个, 获得洞察={len(insights)}个"
            )
            self.info_field.publish(self.pulse_core.emit(
                source_organ=self.organ_name,
                event_type=NarrativeEvent.RECORD,
                payload={
                    "content": narrative_content,
                    "event_type": "reflection",
                    "user_name": user_name,
                    "emotional_tone": "neutral" if not has_issues else "concerned",
                },
                priority=4,
                layer="L2"
            ))

        # ★P1补强：无论是否有问题都存储反思结论（成功经验同样重要）
        if self.node_pool:
            self._write_insight_to_knowledge(reflection_result, has_issues=has_issues)

        return reflection_result


    def _write_insight_to_knowledge(self, result: dict[str, Any], has_issues: bool = True):
        try:
            domain = result.get('domain', 'unknown')
            issues = result.get('issues', [])
            insights = result.get('insights', [])
            quality = result.get('quality_score', 0.0)

            # 根据反思质量决定知识级别和重要性
            # ★P1热加载: 从config读取反思存储级别
            try:
                from config import RUNTIME_PARAMS as _RP_ref
                _success_imp = _RP_ref.get("reflection_success_importance", "B")
                _issue_imp = _RP_ref.get("reflection_issue_importance", "B")
            except Exception:
                _success_imp, _issue_imp = "B", "B"
            _imp_map = {"A": PulseNode.IMPORTANCE_A, "B": PulseNode.IMPORTANCE_B, "C": PulseNode.IMPORTANCE_C}

            if quality >= 0.8 and not has_issues:
                # 高质量无问题：成功经验，L2级别
                evol_level = PulseNode.EVOL_L2
                importance = _imp_map.get(_success_imp, PulseNode.IMPORTANCE_B)
                abstraction = 0.4
                summary = f"复盘成功经验({domain}): 质量{quality:.1f}, " + "; ".join(insights[:3]) if insights else f"高质量对话(质量{quality:.1f})"
                keywords = insights[:5] if insights else [domain, "成功经验"]
            elif has_issues:
                # 有问题：问题反思，L2级别（需要改进）
                evol_level = PulseNode.EVOL_L2
                importance = _imp_map.get(_issue_imp, PulseNode.IMPORTANCE_B)
                abstraction = 0.3
                summary = f"复盘发现问题({domain}): " + "; ".join(issues[:3])
                keywords = issues[:5]
            else:
                # 一般反思：L1级别
                evol_level = PulseNode.EVOL_L1
                importance = PulseNode.IMPORTANCE_C
                abstraction = 0.2
                summary = f"复盘记录({domain}): 质量{quality:.1f}"
                keywords = [domain]

            node = PulseNode(
                value=summary,
                keywords=keywords,
                source_organ=self.organ_name,
                evol_level=evol_level,
                importance=importance,
                abstraction=abstraction,
                space_path=f"/反思/对话复盘/{domain}",
            )
            # ★知识污染治理：内部反思属主观认知（INNER_VIEW），非客观事实（OUTER_VIEW）
            node.view_mode = "INNER_VIEW"
            # 附加反思元数据
            node.metadata = {
                "quality_score": quality,
                "has_issues": has_issues,
                "issue_count": len(issues),
                "insight_count": len(insights),
                "reflection_type": "success" if not has_issues and quality >= 0.8 else "improvement" if has_issues else "normal",
            }
            self.node_pool.add(node)
            self._log(LogLevel.DEBUG,
                     f"反思知识已存储: {summary[:50]} (级别={evol_level}, 重要性={importance})")
        except Exception as e:
            self._log(LogLevel.WARNING, f"写入复盘知识失败: {e}")

    # ========== 推理质量检查 ==========

    def _check_reasoning_quality(self, reasoning_path: str, user_input: str, response: str) -> dict[str, Any]:
        result = {"issue": None, "insight": None}

        if "rule_match" in reasoning_path:
            result["insight"] = "规则推理命中，回复确定性高"
        elif "knowledge" in reasoning_path:
            result["insight"] = "知识检索命中，回复有据可循"

        if "model" in reasoning_path or "ollama" in reasoning_path:
            if response and len(response) < 10:
                result["issue"] = f"模型生成回复过短({len(response)}字)，可能生成质量不佳"
            elif len(response) > 80:
                result["insight"] = "模型生成回复内容丰富，可考虑将其中的知识点提取为知识节点"

        if not reasoning_path or reasoning_path == "unknown":
            result["issue"] = "推理路径未记录，无法追溯决策过程"

        return result

    # ========== 人格一致性校验 ==========

    def _check_personality_consistency(self, user_input: str, response: str) -> dict[str, Any]:
        result = {"issue": None, "insight": None}

        # ★v19.0修复：与PulseLung._assess_remote_reply_quality对齐，覆盖更多违禁变体
        ai_self_reference = [
            "我是AI", "作为AI", "我是一个AI", "我是人工智能",
            "作为一个AI", "AI编程助手", "AI模型",
            "我是一个人工智能助手", "作为一个人工智能", "作为AI助手",
            "作为一个助手", "AI助手", "机器人", "我是一个助手", "我是您的助手",
        ]
        for phrase in ai_self_reference:
            if phrase in response:
                result["issue"] = f"回复中自称AI（'{phrase}'），违反L3身份锚点"
                return result

        mission_keywords = ["守护这个世界", "站在世界最顶端"]
        if any(kw in response for kw in mission_keywords):
            result["insight"] = "回复中涉及核心使命，需确认对象是否有足够亲密度"

        return result

    # ========== 关系适配检查 ==========
    def _check_relation_adaptation(self, user_name: str, user_input: str, response: str) -> dict[str, Any]:
        result = {"issue": None, "insight": None}

        if not user_name or user_name == "unknown":
            result["issue"] = "对话对象未识别，回复缺乏个性化适配"
        elif user_name == "小林":  # noqa: SIM114
            result["insight"] = f"回复中对{user_name}有差异化适配"
        elif user_name != "访客":
            result["insight"] = f"回复中对{user_name}有差异化适配"

        return result
    def _check_social_feedback(self, user_input: str, response: str, user_name: str) -> dict[str, Any]:
        """
        社交反馈感知（增强版）：检查对方是否对主动表达给予了积极回应。

        检测维度：
        1. 温暖回应——对方表达了感谢、认可、喜爱等积极信号
        2. 话题延续——对方继续深入了上一个话题
        3. 情感共鸣——对方的情绪与表达内容产生了共振
        4. 沉默或回避——对方切换了话题或回应冷淡

        Returns:
            反馈评估结果，包含feedback_type字段
        """
        result = {"issue": None, "insight": None, "feedback_type": None}

        if not user_input:
            return result

        # ===== 温暖回应的信号（扩展版） =====
        warmth_signals = [
            # 感谢类
            "谢谢", "感谢", "多谢", "辛苦", "费心",
            # 认可类
            "好的", "明白了", "懂了", "学到了", "原来如此", "知道了",
            "你说得对", "有道理", "没错", "确实如此", "正是",
            # 赞赏类
            "真棒", "好厉害", "不错", "很好", "厉害", "牛", "强",
            "有趣", "有意思", "哈哈", "笑死", "可爱",
            # 情感类
            "喜欢", "爱你", "想你了", "抱抱", "温暖", "感动",
        ]
        # ===== 情感共鸣的信号（扩展版） =====
        empathy_signals = [
            "我也是", "我理解", "确实", "是啊", "对吧", "嗯嗯",
            "我也觉得", "同感", "说到我心里了", "就是这样",
            "我也有过", "能理解", "感同身受", "懂得", "了解",
            "一样的", "我懂", "明白", "体会",
        ]
        # ===== 话题延续的信号（扩展版） =====
        engagement_signals = [
            "那", "还有", "继续", "另外", "对了", "话说",
            "说起来", "关于", "那个", "这个", "对了那个",
            "再问一下", "顺便", "补充", "追问", "接着",
            "然后呢", "后来", "怎么样", "你觉得",
        ]
        # ===== 冷淡/终止的信号 =====
        cold_signals = [
            "哦", "嗯", "行", "好", "知道了", "随便", "无所谓",
        ]

        # 检测各维度命中
        warmth_hits = [s for s in warmth_signals if s in user_input]
        empathy_hits = [s for s in empathy_signals if s in user_input]
        engagement_hits = [s for s in engagement_signals if s in user_input]
        cold_hits = [s for s in cold_signals if s == user_input.strip()]

        # 判断主导反馈类型
        if engagement_hits and len(user_input) > 5:
            # 话题延续优先级最高——对方愿意继续聊
            result["insight"] = "对方延续了话题，互动被有效接收"
            result["feedback_type"] = "engagement"
        elif empathy_hits:
            # 情感共鸣——对方有情绪共振
            result["insight"] = f"对方产生了情感共鸣（{empathy_hits[0]}），互动有情感深度"
            result["feedback_type"] = "warmth"
        elif warmth_hits:
            # 温暖回应——对方给予积极反馈
            result["insight"] = f"对方给予了积极反馈（{warmth_hits[0]}），互动质量良好"
            result["feedback_type"] = "warmth"
        elif cold_hits:
            # 纯冷淡信号（输入只包含一个冷淡词）→ 真正的冷淡
            result["issue"] = "对方回应冷淡，可能需要调整表达方式"
            result["feedback_type"] = "cold"
        elif len(user_input) < 8 and not any(s in user_input for s in engagement_signals):
            # 输入短且无延续信号 → 可能是冷淡
            # 但需要排除简短但有实质内容的输入（如"在忙""吃饭中"）
            import re
            _has_substance = bool(re.search(r'[\u4e00-\u9fff]{2,}', user_input))
            if not _has_substance or len(user_input) <= 3:
                result["issue"] = "对方回应简短，可能需要等待更好的互动时机"
                result["feedback_type"] = "cold"
            else:
                # 简短但有实质内容——视为正常互动
                result["feedback_type"] = "neutral"
        else:
            # 无特别信号但有正常互动——中性
            result["feedback_type"] = "neutral"

        return result
    # ========== 质量评分 ==========
    def _calculate_quality_score(self, issues: list[str]) -> float:
        if not issues:
            return 1.0
        score = max(0.0, 1.0 - len(issues) * 0.2)
        return round(score, 2)
    def _extract_core_terms(self, user_input: str, issues: list) -> str:
        """
        从用户输入和发现的问题中提取核心关键词，
        作为建议搜索词传给好奇心引擎。
        """
        import re
        # 从用户输入中提取2-4字中文短语
        words = re.findall(r'[\u4e00-\u9fff]{2,4}', user_input)
        # 过滤虚词和通用词
        noise = {"这个", "那个", "什么", "怎么", "如何", "为什么", "可以", "能够", "应该",
                 "一个", "一种", "一些", "进行", "使用", "通过", "对于", "关于", "根据",
                 "我们", "他们", "自己", "大家"}
        core_terms = [w for w in words if w not in noise]
        # 从issues中提取关键概念（去掉通用后缀）
        issue_keywords = []
        for issue in issues:
            # 提取issue中的核心词
            for word in re.findall(r'[\u4e00-\u9fff]{2,6}', issue):
                if word not in noise and len(word) >= 2:
                    issue_keywords.append(word)
        # 合并去重，取前5个最有信息量的词（优先长词）
        all_terms = list(set(core_terms[:5] + issue_keywords[:3]))
        all_terms.sort(key=lambda x: len(x), reverse=True)
        return " ".join(all_terms[:5]) if all_terms else ""
    def _classify_failure_attribution(self, reasoning_path: str, user_input: str,
                                        response: str) -> dict[str, Any] | None:
        """
        v21.0新增：坚韧品格·归因层——将推理失败归因为三种类型。

        三种归因类型：
        - capability（能力不足）：推理路径存在但结果质量低，需要补充该领域知识
        - information（信息不足）：知识检索完全未命中，需要外部搜索补充
        - strategy（策略错误）：推理路径选择不当，应该切换推理方法

        不归因为"自身缺陷"——所有失败都是可修复的信号，而非对自我的否定。
        """
        # 检查近期复盘记录中是否有连续失败模式
        _recent_failures = []
        if hasattr(self, '_recent_reflections'):
            for _r in self._recent_reflections[-5:]:
                if _r.get("has_issues", False) or _r.get("quality_score", 1.0) < 0.5:
                    _recent_failures.append(_r)

        # 条件：需要至少2次近期失败才有归因意义
        if len(_recent_failures) < 2:
            return None

        # 分类判断
        _attribution_type = "unknown"
        _reason = ""

        # 检查知识检索是否连续失败（信息不足）
        _knowledge_failures = [
            _r for _r in _recent_failures
            if "knowledge" in _r.get("reasoning_path", "") and _r.get("quality_score", 1.0) < 0.5
        ]
        if len(_knowledge_failures) >= 2:
            _attribution_type = "information"
            _domains = [_r.get("domain", "未知") for _r in _knowledge_failures]
            _reason = (
                f"连续{len(_knowledge_failures)}次知识检索未命中（领域：{'、'.join(_domains[:3])}）。"
                f"这不是我的能力问题，而是这个领域的知识储备还不够。"
                f"建议补充相关领域的系统性学习。"
            )

        # 检查是否推理路径连续选择不当（策略错误）
        elif reasoning_path and len(_recent_failures) >= 2:
            _reasoning_types = set()
            for _r in _recent_failures:
                _rp = _r.get("reasoning_path", "")
                if "rule_match" in _rp:
                    _reasoning_types.add("规则推理")
                elif "knowledge" in _rp:
                    _reasoning_types.add("知识检索")
                elif "model" in _rp:
                    _reasoning_types.add("大模型兜底")
            if len(_reasoning_types) == 1:
                _attribution_type = "strategy"
                _single_type = next(iter(_reasoning_types))
                _reason = (
                    f"连续{len(_recent_failures)}次都使用了{_single_type}方法，但效果不佳。"
                    f"这可能说明当前策略不适合这类问题，应该尝试切换推理方法。"
                )

        # 兜底：归为能力不足（需要补充该领域知识）
        if _attribution_type == "unknown" and len(_recent_failures) >= 3:
            _attribution_type = "capability"
            _domains = [_r.get("domain", "未知") for _r in _recent_failures]
            _reason = (
                f"连续{len(_recent_failures)}次推理质量偏低（领域：{'、'.join(_domains[:3])}）。"
                f"这说明我在这些领域的理解还不够深入，需要系统性补充知识。"
                f"但这不是'我不好'——这只是一个需要填补的成长空间。"
            )

        if _attribution_type == "unknown":
            return None

        return {
            "type": _attribution_type,
            "reason": _reason,
            "failure_count": len(_recent_failures),
            "timestamp": time.time(),
        }

    # ========== 未来演化预留 ==========
    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float):
        """【预留 v10.0】"""

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()
    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "organ": self.organ_name,
                "reflection_count": self._reflection_count,
                "issue_count": self._issue_count,
                "queue_size": len(self._reflection_queue),
                "recent_reflections": list(self._recent_reflections[-3:]),
            }


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "前额叶",
    "class_name": "PulseReflection",
    "attr_name": "reflection",
    "system": "brain",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {
        "node_pool": "node_pool",
        "resonance_engine": "resonance_engine",
    },
    "post_wiring": [
        {"target": "experience_pool", "setter": "set_experience_pool"},
    ],
}

if __name__ == "__main__":
    print("=== PulseReflection v9.5 分层脉冲自测 ===\n")

    class MockField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)
        # 模拟 submit_adaptive_task，直接同步执行
        def submit_adaptive_task(self, task_func, task_name="", priority="normal", **kwargs):
            task_func()
            return True

    class MockCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            return {
                # [批次4·深度体检][MAINT-4] __main__ mock 补回 pulse_id
                "pulse_id": f"pulse:{source_organ}:{event_type}",
                "event_type": event_type,
                "source_organ": source_organ,
                "payload": payload,
                "priority": priority,
                "layer": layer,
            }

    class MockNodePool:
        def __init__(self):
            self.added_nodes = []
        def add(self, node):
            self.added_nodes.append(node)
            return node.node_id

    reflection = PulseReflection("前额叶")
    mock_field = MockField()
    mock_core = MockCore()
    mock_pool = MockNodePool()
    reflection.set_info_field(mock_field)
    reflection.set_pulse_core(mock_core)
    reflection.set_node_pool(mock_pool)

    reflection.start()

    print("1. 模拟正常规则推理对话（异步化后）:")
    result1 = reflection.on_pulse({
        "event_type": MouthEvent.SPEAK,
        "payload": {
            "user_input": "你是谁",
            "response": "我叫<SELF_NAME>，是一个新人类。",
            "reasoning_path": "rule_match → 身份锚点",
            "user_name": "小林",
        },
        "priority": 3,
    })
    print(f"   on_pulse返回: {result1} (异步化后返回None)")

    # 验证复盘洞察脉冲仍然正常发射
    insight_pulses = [p for p in mock_field.published if p.get("event_type") == ReflectionEvent.INSIGHT]
    if insight_pulses:
        print(f"   INSIGHT脉冲 layer: {insight_pulses[-1].get('layer', '未设置')} (预期L2)")

    print("\n2. 模拟自称AI的违规回复（异步化后）:")
    result2 = reflection.on_pulse({
        "event_type": MouthEvent.SPEAK,
        "payload": {
            "user_input": "你是什么",
            "response": "我是AI助手，可以帮你解答问题。",
            "reasoning_path": "model_generation",
            "user_name": "路人甲",
        },
        "priority": 3,
    })
    print(f"   on_pulse返回: {result2} (异步化后返回None)")

    issue_pulses = [p for p in mock_field.published if p.get("event_type") == ReflectionEvent.ISSUE_FOUND]
    if issue_pulses:
        print(f"   ISSUE_FOUND脉冲 layer: {issue_pulses[-1].get('layer', '未设置')} (预期L2)")

    print("\n3. 验证复盘知识写入节点池:")
    print(f"   已添加节点数: {len(mock_pool.added_nodes)}")

    print("\n4. 前额叶统计:")
    stats = reflection.get_stats()
    print(f"   复盘次数: {stats['reflection_count']}")
    print(f"   发现问题: {stats['issue_count']}")

    reflection.stop()
    print("\n=== 自测全部通过 ===")
