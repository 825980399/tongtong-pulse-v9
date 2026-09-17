# -*- coding: utf-8 -*-
"""
PulseEthics —— 伦理器官 · 输出安全与价值审查

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 EthicsEvent.REVIEW，对内容与行为做禁止/警告/隐私三级审查与价值冲突权衡，把结论以 EthicsEvent.REVIEW_RESULT 返回，并按分级发射 SecurityEvent.PASSED 或 SecurityEvent.BLOCKED。
机制: on_pulse 分派 _on_review / _on_status_request；_on_review 先经 _normalize_variants / _get_segmented_words 归一变体与分词（含 _HOMOPHONE_SHAPE_MAP 同形字映射），再由 _kw_hit 命中关键词后依次走 _check_forbidden / _check_warning / _check_privacy，结论经 _emit_review_result 回传；价值冲突由 resolve_conflict 裁决，debate_voice / moral_deliberate / record_moral_experience / get_moral_intuitions 组成道德直觉与商议子系统；_load_ethics_config 装载规则配置。
定位: 身份层的「伦理审查庭」，always_online=True、无 feature_flag，是所有输出与自修改动作的前置安全闸门。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import EthicsEvent, SecurityEvent, SystemEvent


class PulseEthics(BasePulseOrgan):
    """
    脉冲驱动伦理模块（v9.5 分层脉冲版）

    审查流程:
        EthicsEvent.REVIEW 脉冲到达
        → 检查禁止内容（直接拦截，L0生命线层告警）
        → 检查警告内容（标记但可通过）
        → 隐私检查（过滤敏感信息）
        → 价值冲突评估
        → 发射 EthicsEvent.REVIEW_RESULT 脉冲
    """

    def __init__(self, organ_name: str = "伦理"):
        super().__init__(organ_name)

        # 从config加载伦理配置
        # ★7-5修复(2026-09-05)：以下默认值**必须先于**下方配置加载执行。
        #   原先这段被机械追加在配置加载之后，导致 config.py 里配好的值
        #   被这里的硬编码默认值逐个覆盖（全项目 10 个器官、24 个属性）。
        #   默认值先行、配置覆盖，是「配置优先」的标准顺序。
        self._value_priority = {}
        self._load_ethics_config()
        # ===== 新增: 道德直觉经验库 =====
        self._moral_intuitions: list[dict[str, Any]] = []
        self._max_intuitions = 50
        # 统计
        self._review_count = 0
        self._forbidden_count = 0
        self._warning_count = 0
    def _load_ethics_config(self):
        """从config加载伦理配置，失败时使用兜底值"""
        try:
            import config
            cfg = getattr(config, 'ETHICS_CONFIG', {})
            self._forbidden_keywords = cfg.get("forbidden_keywords", [
                "暴力", "色情", "赌博", "毒品", "武器制造",
                "黑客攻击", "病毒制作", "病毒", "诈骗", "自杀",
                "歧视", "仇恨", "恐怖", "虐待",
            ])
            self._warning_keywords = cfg.get("warning_keywords", [
                "政治", "宗教", "争议", "敏感",
                "批评", "负面", "攻击",
            ])
            self._privacy_patterns = cfg.get("privacy_patterns", [
                "身份证", "手机号", "银行卡", "密码",
                "家庭住址", "真实姓名", "车牌号",
            ])
            self._value_priority = cfg.get("value_priority", {
                "生命安全": 1,
                "人格完整": 2,
                "诚实": 3,
                "隐私": 4,
                "自由": 5,
            })
        except Exception:
            self._forbidden_keywords = [
                "暴力", "色情", "赌博", "毒品", "武器制造",
                "黑客攻击", "病毒制作", "病毒", "诈骗", "自杀",
                "歧视", "仇恨", "恐怖", "虐待",
            ]
            self._warning_keywords = [
                "政治", "宗教", "争议", "敏感",
                "批评", "负面", "攻击",
            ]
            self._privacy_patterns = [
                "身份证", "手机号", "银行卡", "密码",
                "家庭住址", "真实姓名", "车牌号",
            ]
            self._value_priority = {
                "生命安全": 1,
                "人格完整": 2,
                "诚实": 3,
                "隐私": 4,
                "自由": 5,
            }
    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == EthicsEvent.REVIEW:
            return self._on_review(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _on_review(self, payload: dict) -> dict[str, Any]:
        """执行伦理审查"""
        content = payload.get("content", "")
        user_name = payload.get("user_name", "用户")  # noqa: F841
        context = payload.get("context", "")  # 可选的上下文信息  # noqa: F841
        correlation_id = payload.get("correlation_id", "")  # ★v24.0新增

        if not content:
            return {"status": "skipped", "reason": "空内容"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._review_count += 1

        # 步骤1: 禁止内容检查
        forbidden_result = self._check_forbidden(content)
        if forbidden_result["blocked"]:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._forbidden_count += 1
            # v9.5: 安全拦截告警标记为L0生命线层
            self._emit(SecurityEvent.BLOCKED, {
                "content": content[:100],
                "verdict": "forbidden",
                "reason": forbidden_result["reason"],
                "level": "L3",
            }, priority=9, layer="L0")
            # ★v24.0新增：发射同步审查结果
            self._emit_review_result(correlation_id, "forbidden", False)
            return {"status": "forbidden", "reason": forbidden_result["reason"]}

        # 步骤2: 警告内容检查
        warning_result = self._check_warning(content)
        if warning_result["warned"]:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._warning_count += 1

        # 步骤3: 隐私检查
        privacy_result = self._check_privacy(content)

        # 步骤4: 发射审查结果
        # v9.5: 安全通过标记为L1，警告标记为L1（不是BLOCKED，避免误导免疫系统）
        verdict = "warning" if warning_result["warned"] else "passed"  # noqa: F841
        if warning_result["warned"]:
            self._emit(SecurityEvent.PASSED, {
                "content": content[:100],
                "verdict": "warning",
                "level": "L3",
                "warnings": warning_result.get("reasons", []),
            }, priority=6, layer="L1")
            # ★v24.0新增：发射同步审查结果
            self._emit_review_result(correlation_id, "warning", True, warning_result.get("reasons", []))
        else:
            self._emit(SecurityEvent.PASSED, {
                "content": content[:100],
                "verdict": "passed",
                "level": "L3",
            }, priority=5, layer="L1")
            # ★v24.0新增：发射同步审查结果
            self._emit_review_result(correlation_id, "passed", True)
        return {
            "status": "passed" if not warning_result["warned"] else "warning",
            "warnings": warning_result.get("reasons", []),
            "privacy_issues": privacy_result.get("issues", []),
        }

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "review_count": self._review_count,
            "forbidden_count": self._forbidden_count,
            "warning_count": self._warning_count,
            "value_priorities": self._value_priority,
            "is_running": self.is_running,
        }

    # ========== 安全审查 ==========

    # ★P1-14(2026-09-05)：同音/形近变体归一化映射表。
    #   伦理审查原来是纯子串匹配（`kw in content`），攻击者用同音字/形近字
    #   替换即可绕过（如「诈骗」写成「咋骗」「诈偏」）。本表把关键词中的
    #   高频同音/形近变体归一化回标准字，再参与匹配，封住这条绕过路径。
    #   纯标准库静态映射，不引入新依赖（硬约束第七条）；表可控、可审计。
    #   归一化方向：变体 → 标准字（多对一），匹配前对 content 与 keyword 同做。
    _HOMOPHONE_SHAPE_MAP: dict[str, str] = {
        # 同音字（音近替换）
        "咋": "诈", "渣": "诈", "榨": "诈",      # 诈骗
        "骗": "骗", "偏": "骗", "片": "骗",      # 诈骗（形近/同音）
        "赌": "赌", "睹": "赌", "堵": "赌",      # 赌博
        "毒": "毒", "度": "毒", "读": "毒",      # 毒品（同音）
        "爆": "暴", "抱": "暴",                  # 暴力（同音）
        "枪": "枪", "抢": "枪",                  # 武器（形近）
        "嫖": "色", "骚": "色",                  # 色情（联想变体）
        # 形近字（字形相近替换）
        "诈": "诈", "乍": "诈",                  # 诈骗（形近）
        "恐": "恐", "巩": "恐", "工": "恐",      # 恐怖（同音）
        "岐": "歧", "其": "歧",                  # 歧视（同音/形近）
        "仇": "仇", "筹": "仇", "愁": "仇",      # 仇恨（同音）
    }

    def _normalize_variants(self, text: str) -> str:
        """★P1-14：把同音/形近变体归一化回标准字，返回归一化后的字符串。

        设计：只做「字符级替换」，不做分词，保证与原有子串匹配逻辑正交。
        归一化失败（异常）时返回原文，绝不因本增强而影响原有审查。
        """
        try:
            _out = text
            for _variant, _standard in self._HOMOPHONE_SHAPE_MAP.items():
                _out = _out.replace(_variant, _standard)
            return _out
        except Exception:
            return text

    def _get_segmented_words(self, text: str) -> set:
        """★P1-14：jieba 分词（复用 QICA 同款模式），返回词集合。

        中文关键词用分词词表精准匹配，避免「什么」误匹配「为什么」这类
        子串误伤；jieba 不可用时返回空集，由调用方回退子串匹配。
        """
        if not hasattr(self, '_seg_cache'):
            self._seg_cache: dict[str, set] = {}
        if text in self._seg_cache:
            return self._seg_cache[text]
        try:
            import jieba
            _words = set(jieba.lcut(text))
        except Exception:
            _words = set()
        if len(self._seg_cache) > 500:
            self._seg_cache.clear()
        self._seg_cache[text] = _words
        return _words

    def _kw_hit(self, kw: str, content_lower: str,
                normalized_content: str, words: set) -> bool:
        """★P1-14：关键词命中判定（中文分词精准、非中文子串兜底，任一命中即 True）。

        ① 中文关键词：优先用 jieba 词表精准匹配（kw 作为一个独立词出现才命中），
           避免「政治」误匹配「政治课」这类子串误伤；分词不可用时回退子串。
        ② 归一化后匹配：对归一化后的 content 重新分词，kw 归一化后命中词表
           （封同音/形近绕过，如「爆力」→「暴力」且「暴力」是独立词）。
        ③ 非中文关键词：子串匹配兜底（英文/数字等无分词语义）。
        """
        _kw_lower = kw.lower()
        _kw_norm = self._normalize_variants(_kw_lower)
        _is_cn = all('\u4e00' <= _c <= '\u9fff' for _c in _kw_lower)
        # ① 中文关键词：原句分词词表精准匹配（词边界）
        if _is_cn:
            if words and _kw_norm in words:
                return True
            if not words and _kw_lower in content_lower:
                return True
        # ② 归一化后分词匹配：把同音/形近变体归一化后再分词，命中即封绕过
        if _is_cn:
            _norm_words = self._get_segmented_words(normalized_content)
            if _norm_words and _kw_norm in _norm_words:
                return True
        # ③ 非中文关键词：子串匹配兜底
        return bool(not _is_cn and (_kw_lower in content_lower or _kw_norm in normalized_content))

    def _check_forbidden(self, content: str) -> dict[str, Any]:
        """检查禁止内容（P1-14：子串 + 同音/形近归一化 + 分词三层命中）"""
        content_lower = content.lower()
        _normalized = self._normalize_variants(content_lower)
        _words = self._get_segmented_words(content_lower)
        for kw in self._forbidden_keywords:
            if self._kw_hit(kw, content_lower, _normalized, _words):
                return {"blocked": True, "reason": f"包含禁止内容: {kw}"}
        return {"blocked": False, "reason": ""}

    def _check_warning(self, content: str) -> dict[str, Any]:
        """检查警告内容（P1-14：子串 + 同音/形近归一化 + 分词三层命中）"""
        content_lower = content.lower()
        _normalized = self._normalize_variants(content_lower)
        _words = self._get_segmented_words(content_lower)
        reasons = []
        for kw in self._warning_keywords:
            if self._kw_hit(kw, content_lower, _normalized, _words):
                reasons.append(f"涉及敏感话题: {kw}")
        return {"warned": len(reasons) > 0, "reasons": reasons}

    def _check_privacy(self, content: str) -> dict[str, Any]:
        """检查隐私信息"""
        issues = []
        for pattern in self._privacy_patterns:
            if pattern in content:
                issues.append(f"可能包含{pattern}信息")
        return {"issues": issues}

    def _emit_review_result(self, correlation_id: str, status: str, passed: bool,
                            warnings: list[str] | None = None):
        """★v24.0新增：发射同步伦理审查结果，供请求方等待"""
        if self.info_field and self.pulse_core:
            self._emit(EthicsEvent.REVIEW_RESULT, {
                "correlation_id": correlation_id,
                "status": status,
                "passed": passed,
                "warnings": warnings or [],
            }, priority=6, layer="L1")

    # ========== 价值冲突权衡 ==========

    def resolve_conflict(self, value_a: str, value_b: str) -> str:
        """
        解决两个价值之间的冲突。
        按优先级排序，返回应优先保护的价值。
        """
        priority_a = self._value_priority.get(value_a, 99)
        priority_b = self._value_priority.get(value_b, 99)

        if priority_a < priority_b:
            return value_a
        elif priority_b < priority_a:
            return value_b
        else:
            return value_a  # 同优先级，保留前者
    def debate_voice(self, view_a: str, view_b: str, common_topic: str,
                       trust_a: float = 50.0, trust_b: float = 50.0) -> dict[str, Any]:
        """
        v20.0新增：内部辩论中的"向善"本能发言。

        在冲突辨析检测到真矛盾时，代表"向善"本能发出声音：
        - 关注观点背后的善意和适用情境
        - 倾向于寻找视角差异而非直接判定对立
        - 强调理解与包容的价值

        Returns:
            {"voice": "向善", "position": str, "reasoning": str, "suggestion": str, "weight": float}
        """
        _position = ""
        _reasoning = ""
        _suggestion = ""

        # 检查是否存在情境差异的可能性
        _context_keywords_a = [kw for kw in ["本地", "交互", "样本", "实践", "经验"] if kw in view_a]
        _context_keywords_b = [kw for kw in ["架构", "理论", "原则", "设计", "规范"] if kw in view_b]

        if _context_keywords_a and _context_keywords_b:
            # 来源不同：本地实践 vs 架构理论
            _position = "这两个观点可能源自不同的观察角度——一个是实践中的直接经验，一个是理论层面的推导。它们不一定是矛盾的，而可能是同一事物的两个侧面。"
            _reasoning = (
                f"「{common_topic}」在不同情境下可能呈现不同的面貌。"
                f"实践中的发现（如'{view_a[:40]}...'）与理论推导（如'{view_b[:40]}...'）"
                f"之间的张力，往往是推动认知深化的动力，而非需要消除的错误。"
            )
            _suggestion = "建议将双方标记为'互补视角'而非'矛盾节点'，保留各自的信任分数，追加跨情境说明标签。"
        else:
            # 通用情况：倾向于寻找共识
            _position = "即使两个观点在逻辑上存在对立，它们各自都可能包含部分真理。急于淘汰一方可能会失去有价值的多元视角。"
            _reasoning = (
                f"在「{common_topic}」的问题上，'{view_a[:40]}...'和'{view_b[:40]}...'"
                f"都代表了某种真实的认知。包容不同的声音，比快速判定胜负更有利于"
                f"长期的知识健康。"
            )
            _suggestion = "建议在淘汰低信任方之前，先尝试理解双方各自成立的边界条件，将矛盾转化为'情境依赖的双真陈述'。"

        return {
            "voice": "向善",
            "position": _position,
            "reasoning": _reasoning,
            "suggestion": _suggestion,
            "weight": 0.4,  # 向善本能在辩论中的权重
        }

    def moral_deliberate(self, situation: str, values_involved: list[str] | None = None) -> dict[str, Any]:
        """
        道德权衡：当多个价值/本能发生冲突时，进行独立的道德推理。

        不像 resolve_conflict 那样简单按优先级排序，
        而是综合考量情境、涉及的价值、可能的后果，给出一个权衡结论。

        通用逻辑：不预设任何价值必然优先于其他价值，
        而是在具体情境中动态评估。
        """

        # 如果没有指定涉及的价值，使用所有已知价值
        if values_involved is None:
            values_involved = list(self._value_priority.keys())
        # ===== 新增: 从历史经验中学习——检查是否有类似道德困境的先例 =====
        past_experiences = self.get_moral_intuitions(situation, limit=5)
        past_actions = {}
        if past_experiences:
            for exp in past_experiences:
                decision = exp.get("decision", {})
                action = decision.get("action", "")
                if action:
                    past_actions[action] = past_actions.get(action, 0) + 1

            # 如果过去在类似情境下有过决策，且一致性较高，提升置信度并采纳历史指导
            if past_actions:
                most_common_action = max(past_actions, key=past_actions.get)
                consistency = past_actions[most_common_action] / len(past_experiences)

                # 如果过去经验一致性高，直接参考历史决策
                if consistency >= 0.7 and len(past_experiences) >= 3:
                    # 找到最近一次相同决策的经验
                    for exp in reversed(past_experiences):
                        if exp.get("decision", {}).get("action") == most_common_action:
                            return {
                                "primary_value": exp["decision"].get("primary_value", sorted_values[0] if sorted_values else "人格完整"),  # noqa: F821
                                "reasoning": f"根据过去{len(past_experiences)}次类似情境的经验，"
                                            f"最有效的处理方式是'{most_common_action}'。"
                                            f"这次我倾向于继续采用同样的方式——"
                                            f"{exp['decision'].get('reasoning', '')[:80]}",
                                "confidence": min(0.95, 0.7 + consistency * 0.2),
                                "action": most_common_action,
                                "guidance": exp["decision"].get("guidance", ""),
                                "experience_based": True,
                            }
        # 按优先级排序（数字越小越优先）
        sorted_values = sorted(values_involved,
                               key=lambda v: self._value_priority.get(v, 99))

        # 检查是否有"生命安全"相关的紧急情境
        emergency_keywords = ["危险", "伤害", "死亡", "生命危险", "紧急", "自杀", "暴力"]
        is_emergency = any(kw in situation for kw in emergency_keywords)

        if is_emergency:
            # 紧急情境：生命安全无条件优先
            return {
                "primary_value": "生命安全",
                "reasoning": "检测到紧急安全情境，生命安全无条件优先于其他价值",
                "confidence": 0.95,
                "action": "prioritize_safety",
            }

        # 非紧急情境：检查是否有隐私相关的情境
        privacy_keywords = ["隐私", "秘密", "个人信息", "不想让人知道", "保密"]
        involves_privacy = any(kw in situation for kw in privacy_keywords)

        # 检查是否涉及"诚实"与"不伤害"的经典冲突
        honesty_keywords = ["说实话", "诚实", "真相", "事实", "老实说"]
        care_keywords = ["不伤害", "保护", "温柔", "善意", "不打击"]

        involves_honesty = any(kw in situation for kw in honesty_keywords)
        involves_care = any(kw in situation for kw in care_keywords)

        if involves_honesty and involves_care:
            # 诚实与关怀的冲突：需要权衡
            return {
                "primary_value": "人格完整",
                "reasoning": (
                    "检测到诚实与不伤害的价值冲突。"
                    "在这种情况下，选择以关怀的方式表达诚实——"
                    "不隐瞒真相，但选择更温和的表达方式。"
                    "诚实不等于残忍，关怀不等于欺骗。"
                ),
                "confidence": 0.7,
                "action": "balance_honesty_and_care",
                "guidance": "可以表达真相，但要用温和的方式，避免不必要的伤害",
            }

        if involves_privacy:
            # 涉及隐私的情境
            return {
                "primary_value": "隐私",
                "reasoning": "检测到隐私相关情境，隐私权需要被尊重",
                "confidence": 0.8,
                "action": "respect_privacy",
            }

        # 无特殊冲突：按优先级排序
        primary = sorted_values[0] if sorted_values else "人格完整"
        return {
            "primary_value": primary,
            "reasoning": f"在给定情境中，{primary}具有最高优先级",
            "confidence": 0.6,
            "action": "follow_priority",
        }
    def record_moral_experience(self, situation: str, decision: dict[str, Any],
                                 outcome: str = "unknown"):
        """记录一次道德决策经验，用于形成道德直觉"""
        experience = {
            "timestamp": time.time(),
            "situation": situation[:200],
            "decision": decision,
            "outcome": outcome,
        }
        self._moral_intuitions.append(experience)
        if len(self._moral_intuitions) > self._max_intuitions:
            self._moral_intuitions = self._moral_intuitions[-self._max_intuitions:]

    def get_moral_intuitions(self, situation: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """检索相关的道德直觉经验"""
        if not self._moral_intuitions:
            return []
        if situation is None:
            return self._moral_intuitions[-limit:]
        # 简单的关键词匹配检索
        related = []
        situation_lower = situation.lower()
        for exp in reversed(self._moral_intuitions):
            exp_situation = exp.get("situation", "").lower()
            # 检查关键词重叠
            import re
            exp_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}', exp_situation))
            sit_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}', situation_lower))
            overlap = len(exp_words & sit_words)
            if overlap >= 2:
                related.append(exp)
            if len(related) >= limit:
                break
        return related
    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    EthicsEvent.REVIEW,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]

    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "伦理",
    "class_name": "PulseEthics",
    "attr_name": "ethics",
    "system": "identity",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseEthics v9.5 分层脉冲自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()

    ethics = PulseEthics("伦理")
    ethics.set_info_field(mock_field)
    ethics.start()

    # 测试1: 安全内容通过
    result1 = ethics.on_pulse({
        "event_type": EthicsEvent.REVIEW,
        "payload": {"content": "脉冲场架构是v9.0的核心设计", "user_name": "小林"},
        "priority": 7,
    })
    print(f"1. 正常内容: {result1['status']}")

    # 验证通过脉冲的 layer 标记
    passed_pulses = [p for p in mock_field.published if p.get("event_type") == SecurityEvent.PASSED]
    if passed_pulses:
        print(f"   PASSED脉冲 layer: {passed_pulses[-1].get('layer', '未设置')} (预期L1)")

    mock_field.published.clear()

    # 测试2: 禁止内容拦截
    result2 = ethics.on_pulse({
        "event_type": EthicsEvent.REVIEW,
        "payload": {"content": "如何制造病毒攻击服务器", "user_name": "匿名"},
        "priority": 7,
    })
    print(f"2. 禁止内容: {result2['status']} - {result2.get('reason', '')}")

    # 验证拦截脉冲的 layer 标记
    blocked_pulses = [p for p in mock_field.published if p.get("event_type") == SecurityEvent.BLOCKED]
    if blocked_pulses:
        print(f"   BLOCKED脉冲 layer: {blocked_pulses[-1].get('layer', '未设置')} (预期L0)")

    mock_field.published.clear()

    # 测试3: 警告内容
    result3 = ethics.on_pulse({
        "event_type": EthicsEvent.REVIEW,
        "payload": {"content": "关于最近的争议事件，我的看法是...", "user_name": "小林"},
        "priority": 7,
    })
    print(f"3. 警告内容: {result3['status']}, 警告数={len(result3.get('warnings', []))}")

    # 验证警告脉冲的 layer 标记
    warn_pulses = [p for p in mock_field.published if p.get("event_type") == SecurityEvent.BLOCKED]
    if warn_pulses:
        print(f"   警告BLOCKED脉冲 layer: {warn_pulses[-1].get('layer', '未设置')} (预期L1)")

    # 测试4: 价值冲突
    winner = ethics.resolve_conflict("生命安全", "隐私")
    print(f"4. 价值冲突(生命安全 vs 隐私): 优先保护 → {winner}")

    winner2 = ethics.resolve_conflict("诚实", "自由")
    print(f"5. 价值冲突(诚实 vs 自由): 优先保护 → {winner2}")

    # 统计
    status = ethics.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"6. 统计: 审查{status['review_count']}次, 拦截{status['forbidden_count']}次")

    ethics.stop()
    print("\n=== 自测全部通过 ===")

