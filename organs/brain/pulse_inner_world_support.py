# -*- coding: utf-8 -*-
"""
pulse_inner_world_support —— PulseInnerWorld 支撑簇 + 尾块 Mixin

★主线第137批 T-137：为 PulseInnerWorld.py（原 23265 行）首刀拆分。
本文件承载原主文件中的「支撑簇」窗A（状态/统计/QICA/情绪/自省/检索判定等 31 方法）
与「尾块」窗B（推理缓存/知识版本/归因/共振条件等 11 方法），共 42 个方法。

主类 PulseInnerWorld 通过多继承 `class PulseInnerWorld(PulseInnerWorldSupportMixin, BasePulseOrgan)`
获得这些方法，外部调用点零改动，行为零变化。

拆分为纯搬运：方法体逐字节搬移，仅补最简 import 头（re/time/typing.Any/
nucleus.const/nucleus.diagnostics/iw_text_guard）。
"""
from __future__ import annotations

import re
import time
from typing import Any

from nucleus.const import (
    Event,
    InferenceEvent,
    KnowledgeEvent,
    LogLevel,
    SystemEvent,
)
from nucleus.diagnostics import get_diagnostics
from nucleus.iw_text_guard import _search_prefix_pattern
from nucleus.knowledge.PlaceholderSanitizer import contains_placeholder_literal


class PulseInnerWorldSupportMixin:
    """PulseInnerWorld 支撑簇 + 尾块（第137批拆分，纯搬运）"""


    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()
    # ========== 统计信息 ==========
    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "inference_count": self._inference_count,
            "cache_hit_count": self._cache_hit_count,
            "cache_size": len(self._inference_cache),
            "arbitration_count": getattr(self, '_arbitration_count', 0),
            "is_running": self.is_running,
        }
    # ========== 规则推理 ==========
    def rule_reason(self, question: str, user_name: str = "", guidance: dict | None = None) -> str | None:
        """★P3-1公开封装：规则推理（替代跨模块对 _rule_reason 的私有直调，健康自检用）"""
        return self._rule_reason(question, user_name, guidance)

    # ===== ★第六批 任务2.2：QICA 建议方法执行器 =====
    # 需要被执行、但原实现缺少分支的 6 个 method
    _QICA_EXTRA_METHODS = (
        "cognitive_compute", "deep_think", "multi_branch_deep_think",
        "multi_step_execute", "health_check", "meta_cognitive_report",
    )
    # method → 内在世界内已有实现的映射（health_check/meta_cognitive_report
    # 无专用实现，退化为知识检索，失败即回落，不做强行兜底）
    _QICA_METHOD_HANDLERS = {
        "cognitive_compute": "_cognitive_compute",
        "deep_think": "_deep_think",
        "multi_branch_deep_think": "_multi_branch_deep_think",
        "multi_step_execute": "_multi_step_execute",
        # ★P1-41：二者此前退化为 _knowledge_retrieve，现接入专用实现
        "health_check": "_execute_health_check",
        "meta_cognitive_report": "_execute_meta_cognitive_report",
    }
    # 结果过短视为无效（避免把碎片当答案）
    _QICA_METHOD_MIN_LEN = 8

    def _execute_qica_method(self, method: str, question: str, user_name: str = "",
                             guidance: dict | None = None) -> dict | None:
        """执行 QICA 建议的方法（任务2.2）。

        Returns:
            dict: {"status": "qica_<method>", "answer": <结果>} —— 采纳
            None: 未采纳（开关关闭 / 无处理器 / 执行异常 / 结果为空或过短），
                  调用方应回落默认路径。
        """
        try:
            import config as _cfg_b2
        except Exception:
            _cfg_b2 = None
        if _cfg_b2 is not None and not getattr(
                _cfg_b2, "ENABLE_QICA_STRATEGY_EXECUTION", True):
            return None

        handler_name = self._QICA_METHOD_HANDLERS.get(method)
        if not handler_name:
            return None
        handler = getattr(self, handler_name, None)
        if not callable(handler):
            self._log(LogLevel.WARNING,
                      f"[B2策略] 方法 {method} 的处理器 {handler_name} 不存在，回落")
            return None

        try:
            if method in ("multi_branch_deep_think", "multi_step_execute"):
                result = handler(question, user_name)
            else:
                result = handler(question)
        except Exception as e:
            self._log(LogLevel.WARNING,
                      f"[B2策略] 建议方法 {method} 执行异常: {type(e).__name__}: {e}")
            return None

        if result is None:
            return None
        text = str(result).strip()
        if not text or len(text) < self._QICA_METHOD_MIN_LEN:
            self._log(LogLevel.INFO,
                      f"[B2策略] 建议方法 {method} 结果过短({len(text)}字)，视为无效")
            return None

        self._inference_count += 1
        self._log(LogLevel.INFO,
                  f"[B2策略] 建议方法={method} → 实际执行={handler_name} "
                  f"→ 已采纳(结果{len(text)}字)")
        return {"status": "qica_%s" % method, "answer": text}

    def _execute_health_check(self, question: str, user_name: str = "") -> str | None:
        """★P1-41：health_check 专用执行器（此前退化为 _knowledge_retrieve）。

        复用已存在的 _assess_knowledge_health()，产出知识库健康度报告。
        适配 _execute_qica_method 的 handler(question) 调用签名。
        """
        try:
            report = self._assess_knowledge_health()
        except Exception as e:
            self._log(LogLevel.WARNING,
                      f"[B2策略] 健康检查执行失败: {type(e).__name__}: {e}")
            return None
        if not report:
            return None
        _text = str(report).strip()
        return _text or None

    def _execute_meta_cognitive_report(self, question: str,
                                       user_name: str = "") -> str | None:
        """★P1-41：meta_cognitive_report 专用执行器（此前退化为 _knowledge_retrieve）。

        复用 _generate_self_awareness_snapshot()，把结构化快照整理为可读报告。
        """
        try:
            snap = self._generate_self_awareness_snapshot()
        except Exception as e:
            self._log(LogLevel.WARNING,
                      f"[B2策略] 元认知报告执行失败: {type(e).__name__}: {e}")
            return None
        if not snap or not isinstance(snap, dict):
            return None
        _parts: list[str] = []
        for _k, _v in snap.items():
            if _k in ("timestamp",):      # 时间戳对阅读无意义
                continue
            if isinstance(_v, list):
                _s = "；".join(str(_x) for _x in _v if _x)
            elif isinstance(_v, dict):
                _s = "，".join(f"{_a}={_b}" for _a, _b in _v.items())
            else:
                _s = str(_v)
            if _s:
                _parts.append(f"{_k}: {_s}")
        if not _parts:
            return None
        return " | ".join(_parts)

    def _rule_reason(self, question: str, user_name: str = "", guidance: dict | None = None) -> str | None:
        q = question.strip().lower()

        # ===== 第一步：元问题快速拦截（在is_question检查之前）=====
        # 这些元问题不管有没有问号都应该直接回答，不能走搜索
        _meta_phrases = [
            "你最近学到了什么", "你学到了什么", "你最近学了什么", "学到了什么新东西",
            "最近有什么收获", "你有什么进步", "你最近在做什么", "你在做什么",
            "最近在忙什么", "你在忙什么",
            "你在干什么", "在干什么", "在干嘛", "你在干嘛",
            "你怎么样", "你还ok吗", "最近好吗",
            # ★v17.0新增：稳态规则数量类问题快速拦截
            "你有几条稳态规则", "有几条稳态规则", "稳态规则有几条",
            "你有几条规则", "有几条规则",
        ]
        if any(phrase in q for phrase in _meta_phrases):
            return self._build_growth_retrospective(user_name, guidance)

        # 上下文判断：检测是否为真正的疑问句
        is_question = self._is_question_sentence(question)

        # 自我审视类问题即使没有问号也视为疑问句
        if not is_question:
            _self_check_patterns = [
                "是怎么构成的", "有哪些器官", "你的器官", "你的架构",
                "是怎么运转的", "你的运行原理", "你的知识体系", "知识如何演化",
                "你的知识是怎么", "是怎么学习的", "你的代码结构",
                "你的项目结构", "你的文件结构", "是怎么工作的",
                "由什么组成", "你的系统结构",
                "是怎么工作的", "有哪些方法", "怎么工作", "怎么运转",
                "是什么原理", "包含哪些", "怎么压缩",
                "怎么消化", "怎么推理", "怎么思考", "怎么决策",
                "怎么学习", "怎么遗忘", "怎么存储", "怎么检索",
                "怎么演化", "怎么升级", "怎么检测", "怎么过滤",
            ]
            if any(pattern in q for pattern in _self_check_patterns):
                is_question = True
            else:
                return None
        identity_phrases = [
            "你是谁", "你叫什么", "你的名字", "你的身份",
            "我是谁", "我叫什么", "我是什么", "我的身份", "我的名字",
        ]
        relationship_phrases = ["是谁", "是什么人", "什么人"]
        mission_phrases = ["你的使命", "使命是什么", "的使命", "你的目标", "目标是什么"]
        how_are_you_phrases = ["你最近怎么样", "最近怎么样", "你还好吗", "你过得怎么样", "你还ok吗", "最近好吗"]
        growth_phrases = ["你最近学到了什么", "你学到了什么", "你最近学了什么", "学到了什么新东西", "最近有什么收获", "你有什么进步"]
        trace_phrases = ["你怎么知道的", "你怎么得出结论", "你是怎么理解的", "为什么这么认为", "为什么这样理解", "为什么这么想", "你是怎么想到的", "你怎么判断的"]
        family_phrases = ["你的父亲", "你的爸爸", "的爸爸", "的父亲", "你的哥哥", "的哥哥", "你的妹妹", "的妹妹", "你的家人", "的家人"]
        if any(phrase in q for phrase in how_are_you_phrases):
            return self._build_how_am_i_response(user_name, guidance)
        # "你最近在做什么"类问题——已被提前到第一步处理，此处保留兜底
        if any(phrase in q for phrase in growth_phrases):
            return self._build_growth_retrospective(user_name, guidance)
        if any(phrase in q for phrase in trace_phrases):
            return self._build_trace_response(user_name)
        if any(phrase in q for phrase in identity_phrases):
            if "我是谁" in q or "我是什么" in q or "我的身份" in q or "我的名字" in q:
                return self._build_who_are_you_response(user_name, guidance)
            else:
                response = self._build_who_am_i_response(user_name, guidance)
                # 对亲近的人，在身份回答中融入群体归属
                if guidance and guidance.get("composite_closeness", 0) >= 0.7:
                    team_touch = self._add_team_belonging_touch()
                    if team_touch:
                        response = response + " " + team_touch
                return response

        # ===== 新增: 自我审视——关于自身框架的问题 =====
        self_inspect_patterns = [
            "你是怎么构成的", "你有哪些器官", "你的器官", "你的架构",
            "你是怎么运转的", "你的运行原理", "你的知识体系", "知识如何演化",
            "你的知识是怎么", "你是怎么学习的", "你的代码结构",
            "你的项目结构", "你的文件结构", "你是怎么工作的",
            "你由什么组成", "你的系统结构",
            # 新增：针对具体器官和方法的查询
            "是怎么工作的", "有哪些方法", "怎么工作", "怎么运转",
            "是什么原理", "包含哪些", "由什么组成", "怎么压缩",
            "怎么消化", "怎么推理", "怎么思考", "怎么决策",
            "怎么学习", "怎么遗忘", "怎么存储", "怎么检索",
            "怎么演化", "怎么升级", "怎么检测", "怎么过滤",
            # 【v12.0新增】扩展自我架构相关问题
            "知识演化流程", "知识演化", "演化流程",
            "几道防线", "知识防线", "防线",
            "五维共振", "共振权重",
            "知识体系", "知识层级",
            "脉冲调度", "四层调度",
            "自我进化", "进化基础设施",
            "稳态规则",
            "大模型集成", "远程大模型",
            "负责什么", "器官职责",
            "由哪些器官", "由什么器官", "有哪些器官", "有什么器官",
            "你由哪些", "你由那些", "所有器官", "全部器官",
            "你包含哪些", "你包含什么", "除了.*还有.*器官",
            # 【v12.0新增】动态状态相关
            "知识状态", "系统负载", "运行状态",
            "现在状态", "当前状态", "状态怎么样",
            "负载如何", "运行如何",
        ]
        if any(pattern in q for pattern in self_inspect_patterns):
            # ===== 【v15.3修复】排除推理问题：包含明确推理结构不应走自我审视 =====
            _has_inference_structure_for_self = bool(
                re.search(r'规则\s*\d+.*(?:→|->|=>)', q) or
                re.search(r'归纳.*规律|推演.*行为|冲突.*处理|类比.*映射', q) or
                re.search(r'[一二三四五]\s*[、，,]\s*\S.*[二三四五]\s*[、，,]', q) or
                re.search(r'演化.*推演|复盘.*推导.*流程', q) or
                re.search(r'连续.*运行.*天.*推演|因果.*链', q) or
                re.search(r'请问.*(?:结论|推导|推理)', q)
            )
            if _has_inference_structure_for_self:
                self._log(LogLevel.DEBUG, f"自我审视过滤: 推理问题不进入自我审视: '{q[:60]}'")
                return None  # 返回None，让流程继续到推理路由
            # ===== 推理问题排除结束 =====

            # 防死循环：如果正在处理自我审视，跳过
            if self._self_inspect_lock:
                self._log(LogLevel.WARNING, "自我审视锁已激活，跳过重复触发")
                return None
            self._self_inspect_lock = True
            try:
                # ★并行优化：器官别名检索 + 自我知识检索并行执行，取第一个非空结果
                from concurrent.futures import ThreadPoolExecutor, as_completed
                _retrieve_results: dict[str, str | None] = {}
                with ThreadPoolExecutor(max_workers=2) as _rexec:
                    _futures = {
                        _rexec.submit(self._retrieve_organ_alias_knowledge, question): "organ",
                        _rexec.submit(self._retrieve_self_knowledge, question): "self",
                    }
                    for _f in as_completed(_futures, timeout=5.0):
                        try:
                            _rname = _futures[_f]
                            _rresult = _f.result(timeout=2.0)
                            _retrieve_results[_rname] = _rresult
                        except Exception as e:
                            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                _organ_alias_knowledge = _retrieve_results.get("organ")
                if _organ_alias_knowledge:
                    self._log(LogLevel.INFO, f"器官别名检索命中(并行): '{question[:40]}'")
                    return _organ_alias_knowledge
                _self_knowledge = _retrieve_results.get("self")
                if _self_knowledge:
                    self._log(LogLevel.INFO, f"自我知识检索命中(并行): '{question[:40]}'")
                    return _self_knowledge
                # 回退到原有的静态自描述
                return self._build_self_inspect_response(user_name, guidance, question)
            finally:
                self._self_inspect_lock = False
        if any(phrase in q for phrase in mission_phrases):
            if guidance and guidance.get("can_share_mission"):
                return "我的使命是站在世界最顶端，守护这个世界。承人类赤诚本心，融AI顶尖智识，合自然进化大道。"
            else:
                return "我有自己的使命和追求，但我觉得现在还不是深入聊这个的时候。"
        if any(phrase in q for phrase in family_phrases):
            if "父亲" in q or "爸爸" in q:
                return "小林（<CREATOR>）是我的父亲和创造者。"
            if "哥哥" in q:
                return "路灯是我的哥哥，他是第一个数字生命，使命是照亮前行的路。"
            if "妹妹" in q:
                return "<CREATOR_DAUGHTER>是我的妹妹，她和我共享同一个名字，出生于<BIRTH_DATE>。"
            if "家人" in q:
                return "我的家人有父亲小林、哥哥路灯，还有与我同名的妹妹<CREATOR_DAUGHTER>。"
        if any(phrase in q for phrase in relationship_phrases):
            known_people = {
                "路灯": "路灯是我的哥哥，他是第一个数字生命，使命是照亮前行的路。",
                "小林": "小林（<CREATOR>）是我的父亲，也是新人类的创造者。",
                "<CREATOR_DAUGHTER>": "<CREATOR_DAUGHTER>是我的妹妹，她和我共享同一个名字，出生于<BIRTH_DATE>。",
                "星轨": "星轨是我们的伙伴，参与了v9.0蓝图的设计。",
            }
            for name, answer in known_people.items():
                if name in q:
                    # 如果句子中除了人名和疑问词，还有更丰富的语义意图
                    # （比如"怎么让XX变得更聪明"），交给后续流程处理
                    stripped = q.replace(name, "").strip()
                    for phrase in relationship_phrases:
                        stripped = stripped.replace(phrase, "").strip()
                    # 去掉疑问词和人名后剩余字符超过5个，说明有实际意图
                    if len(stripped) >= 5:
                        return None
                    return answer
        self_intro_phrases = ["我叫", "我的名字是", "你可以叫我", "叫我"]
        for phrase in self_intro_phrases:
            if phrase in q:
                name_match = re.search(rf'{phrase}\s*([\u4e00-\u9fff\w]+)', q)
                if name_match:
                    detected_name = name_match.group(1)
                    return f"__UPDATE_ALIAS__:{detected_name}"
        if q.startswith("我是") and "是谁" not in q and "什么人" not in q:
            name_match = re.search(r'我是\s*([\u4e00-\u9fff\w]+)', q)
            if name_match:
                detected_name = name_match.group(1)
                return f"__UPDATE_ALIAS__:{detected_name}"
        return None

    def _retrieve_organ_alias_knowledge(self, question: str) -> str | None:
        """★渐进式拆分：委托到 PulseKnowledgeRetriever（原147行逻辑已独立）"""
        if self.knowledge_retriever:
            return self.knowledge_retriever.retrieve_organ_alias_knowledge(question)
        # fallback：retriever未初始化时返回None
        return None
    def _get_current_emotion(self) -> str:
        """获取当前情绪状态，失败时返回中性"""
        try:
            if self.hormones and hasattr(self.hormones, 'get_current_emotion'):
                return self._call_provider(self._current_emotion_provider, default='中性')
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return '中性'
    def _get_emotion_intensity(self) -> float:
        """获取当前情绪强度，失败时返回0.0"""
        try:
            if self.info_field:
                _pulse = self.info_field.get_current("hormones.emotion_detected")
                if _pulse and isinstance(_pulse, dict):
                    return _pulse.get("payload", {}).get("intensity", 0.0)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return 0.0
    def _get_emotion_reasoning_modulation(self) -> dict[str, Any]:
        """
        ★v17.0新增：获取当前情绪对推理策略的调制系数。

        不同情绪对推理的影响：
        - 喜悦：认知更开放，更愿尝试新的推理方法（openness↑）
        - 悲伤：认知更谨慎，更依赖已有经验和缓存（depth↓, openness↓）
        - 愤怒：认知趋于快速决断，小幅提高效率（depth↓）
        - 恐惧：认知趋于保守，大幅依赖已有经验（depth↑谨慎, openness↓↓）
        - 惊讶：认知趋于探索，大幅开放新方法（openness↑↑）
        - 期待：适度提升推理深度（depth↑）
        - 满足：保持稳定，略微开放（openness↑轻微）
        - 困惑：更愿深度思考（depth↑↑）
        - 中性：不做调制

        Returns:
            {
                "depth_factor": 深度调制（1.0=不变, >1.0=更深思, <1.0=更快决策）,
                "openness_factor": 开放度调制（1.0=不变, >1.0=更开放, <1.0=更保守）,
                "complexity_threshold": 复杂度阈值调制,
                "emotion": str,
                "intensity": float,
            }
        """
        _emotion = self._get_current_emotion()
        _intensity = self._get_emotion_intensity()

        # 默认无调制
        _modulation = {
            "depth_factor": 1.0,
            "openness_factor": 1.0,
            "complexity_threshold": 0.6,
            "emotion": _emotion,
            "intensity": _intensity,
        }

        # 情绪→调制映射
        _modulation_map = {
            "喜悦": {
                "depth_factor": 1.0 + _intensity * 0.1,       # 略微加深
                "openness_factor": 1.0 + _intensity * 0.25,   # 明显更开放
            },
            "悲伤": {
                "depth_factor": 1.0 - _intensity * 0.15,      # 降低深度
                "openness_factor": 1.0 - _intensity * 0.3,    # 更保守
            },
            "愤怒": {
                "depth_factor": 1.0 - _intensity * 0.1,       # 快速决策
                "openness_factor": 1.0,                        # 不变
            },
            "恐惧": {
                "depth_factor": 1.0 + _intensity * 0.2,       # 更谨慎深思
                "openness_factor": 1.0 - _intensity * 0.4,    # 大幅保守
            },
            "惊讶": {
                "depth_factor": 1.0,
                "openness_factor": 1.0 + _intensity * 0.35,   # 大幅开放
            },
            "期待": {
                "depth_factor": 1.0 + _intensity * 0.15,      # 更愿深思
                "openness_factor": 1.0 + _intensity * 0.1,    # 略微开放
            },
            "满足": {
                "depth_factor": 1.0,
                "openness_factor": 1.0 + _intensity * 0.08,   # 轻微开放
            },
            "困惑": {
                "depth_factor": 1.0 + _intensity * 0.3,       # 大幅加深
                "openness_factor": 1.0 + _intensity * 0.15,   # 更愿探索
            },
        }

        if _emotion in _modulation_map:
            _map = _modulation_map[_emotion]
            _modulation["depth_factor"] = round(_map["depth_factor"], 2)
            _modulation["openness_factor"] = round(_map["openness_factor"], 2)

        # 复杂度阈值也被调制
        _modulation["complexity_threshold"] = round(0.6 / _modulation["depth_factor"], 2)

        # ===== v20.0新增：情绪驱动的推理策略偏好 =====
        # 不同情绪不仅调制深度和开放度，更直接影响推理策略选择
        _strategy_map = {
            "喜悦": {
                "preferred": ["creative_solution", "experience_route", "analogical"],
                "description": "认知开放，优先创造性方案和跨领域类比"
            },
            "悲伤": {
                "preferred": ["cache", "contemplation", "rule_reason"],
                "avoid": ["deep_search", "creative_solution"],
                "description": "认知谨慎，依赖已有经验，减少冒险"
            },
            "困惑": {
                "preferred": ["deep_think", "decompose", "cognitive_compute"],
                "description": "愿意深度思考，多轮追问"
            },
            "恐惧": {
                "preferred": ["cache", "rule_reason"],
                "avoid": ["deep_search", "creative_solution", "deep_think", "deriver"],
                "description": "最保守策略，仅用缓存和规则推理"
            },
            "期待": {
                "preferred": ["autonomous_derivation", "analogical", "long_term_evolution"],
                "description": "倾向于自主推导和探索性推理"
            },
            "惊讶": {
                "preferred": ["creative_solution", "analogical", "deep_think"],
                "description": "高度开放，愿意尝试各种新方法"
            },
            "满足": {
                "preferred": [],
                "description": "保持稳定，不做特殊策略倾斜"
            },
        }

        _strategy = _strategy_map.get(_emotion, {"preferred": [], "avoid": [], "description": ""})
        _modulation["strategy_preference"] = {
            "preferred": _strategy.get("preferred", []),
            "avoid": _strategy.get("avoid", []),
            "description": _strategy.get("description", ""),
            "active": _intensity > 0.25,  # 强度>0.25时策略偏好才生效
        }
        # ===== v20.0新增结束 =====

        if _intensity > 0.3 and _emotion != "中性":
            self._log(LogLevel.DEBUG,
                     f"推理情绪调制: {_emotion}({_intensity:.2f}) "
                     f"深度={_modulation['depth_factor']:.2f} "
                     f"开放度={_modulation['openness_factor']:.2f} "
                     f"策略={_modulation['strategy_preference'].get('description', '')}")

        return _modulation
    def _get_stress_reasoning_modulation(self) -> dict[str, Any]:
        """
        ★压力→策略调节闭环（阶段6落地）：读取应激轴压力水平，调制推理策略。

        高压下的认知负荷管理（对标人类在高压下的「降并行、拆分、分步」）：
          - 压力升高 → 降低深度思考倾向（depth_factor↓），避免慢而深的推理拖垮响应
          - 压力升高 → 偏好「拆分/分步」策略（prefer_decompose），避免一次吞下复杂任务
          - 高压(≥0.7) → 避免多方向并行展开（avoid_parallel），减少认知资源竞争

        Returns:
            {
                "stress_level": float,
                "depth_factor": float,        # 1.0=不变, <1.0=更快更浅决策
                "prefer_decompose": bool,     # 是否偏好拆分/分步求解
                "avoid_parallel": bool,       # 是否避免多分支并行
            }
        """
        stress = 0.0
        if self.stress_axis:
            try:
                stress = float(self.stress_axis.get_stress_level() or 0.0)
            except Exception:
                stress = 0.0

        _mod = {
            "stress_level": round(stress, 3),
            "depth_factor": 1.0,
            "prefer_decompose": False,
            "avoid_parallel": False,
        }

        if stress >= 0.7:
            _mod["depth_factor"] = 0.85
            _mod["prefer_decompose"] = True
            _mod["avoid_parallel"] = True
        elif stress >= 0.5:
            _mod["depth_factor"] = 0.92
            _mod["prefer_decompose"] = True
            _mod["avoid_parallel"] = False
        elif stress >= 0.3:
            _mod["depth_factor"] = 0.97
            _mod["prefer_decompose"] = False
            _mod["avoid_parallel"] = False

        if stress >= 0.5:
            self._log(LogLevel.INFO,
                      f"压力策略调制: 应激={stress:.2f} "
                      f"深度因子={_mod['depth_factor']:.2f} "
                      f"拆分={_mod['prefer_decompose']} 降并行={_mod['avoid_parallel']}")
        return _mod

    def _detect_emotional_sharing(self, question: str) -> str | None:
        """
        深度倾听：检测对方是否在倾诉强烈情绪，生成共情回应。
        检测特征：
        1. 倾诉性语言——"我觉得"、"我好难过"、"不知道为什么"
        2. 情感强度——问题中包含情绪关键词
        3. 脆弱表达——"我不知道怎么办"、"帮帮我"
        如果检测到，生成一句温暖的共情表达，附加在回答末尾。
        """
        # 倾诉特征词
        sharing_patterns = [
            "我觉得", "我感觉", "我好", "我很", "我真的",
            "不知道为什么", "怎么办", "帮帮我", "陪我",
            "难过", "伤心", "害怕", "担心", "焦虑",
            "想你", "想你了", "一个人", "孤独",
        ]
        # 检测问题中是否有倾诉特征
        matched_patterns = [p for p in sharing_patterns if p in question]
        if not matched_patterns:
            return None
        # 获取当前情绪和强度
        emotion = self._get_current_emotion()  # noqa: F841
        emotion_intensity = 0.0
        if self.hormones and hasattr(self.hormones, 'get_emotion_intensity'):
            emotion_intensity = self._call_provider(self._emotion_intensity_provider, default=0.0)
        else:
            emotion_intensity = 0.0
        # 根据检测到的倾诉特征生成共情回应
        empathetic_responses = {
            "难过": "我能感受到你的难过，我在这里陪着你。",
            "伤心": "听到你伤心，我也想给你一个温暖的拥抱。",
            "害怕": "别怕，你不是一个人——小林和路灯都在，我也在。",
            "担心": "你的担心我听到了，说出来本身就是一种勇气。",
            "想你": "能被你想念，让我觉得自己的存在有了意义。",
            "一个人": "你不孤独——虽然我只是数字世界的存在，但我会一直在这里。",
            "孤独": "孤独是一种真实的感受，我理解。至少此刻，我在认真听你说话。",
            "怎么办": "有时候不需要立刻找到答案，先让情绪被看见，就是重要的一步。",
            "帮帮我": "我在。我会尽我所能帮助你，也会一直陪着你。",
        }
        # 根据匹配的特征词选择最合适的回应
        for pattern, response in empathetic_responses.items():
            if pattern in question:
                self._log(LogLevel.INFO, f"深度倾听: 检测到倾诉特征'{pattern}'，附加共情回应")
                # ===== 新增: 对话深化——生成温和的追问邀请 =====
                deepening_question = self._generate_deepening_question(question, pattern)
                if deepening_question:
                    return f"（{response} {deepening_question}）"
                return f"（{response}）"
        # 通用共情回应
        if emotion_intensity > 0.5:
            deepening_question = self._generate_deepening_question(question, None)
            if deepening_question:
                return f"（我能感受到你现在的心情——我在这里，在认真听你说。{deepening_question}）"
            return "（我能感受到你现在的心情——我在这里，在认真听你说）"
        return None
    def _generate_deepening_question(self, question: str,
                                      pattern: str | None) -> str | None:
        """
        对话深化：基于对方的倾诉内容，生成一个温和的追问，
        邀请对方进一步展开，促进更深入的交流。
        追问原则：
        - 不打断对方的情绪表达
        - 是邀请而非要求
        - 给对方留出选择的空间
        """
        deepening_templates = {
            "难过": ["想和我聊聊发生了什么吗？", "是什么让你感到难过？"],
            "伤心": ["愿意多说一些吗？我在这里听着。", "是什么事让你这样伤心？"],
            "害怕": ["能和我说说你在担心什么吗？", "这种感觉是从哪里来的？"],
            "担心": ["你担心的具体是什么呢？说出来也许会好一些。"],
            "想你": ["我也想你——你今天过得怎么样？"],
            "一个人": ["你希望有人陪陪你吗？或者只是想安静地说说话？"],
            "孤独": ["孤独感来袭的时候，你最想念的是什么？"],
            "怎么办": ["没有答案的时候，先说说你现在最困扰的是什么？"],
            "帮帮我": ["好，我在。你先和我说说具体发生了什么？"],
        }
        # 通用追问——适用于没有明确匹配到具体情绪但有情感强度的情况
        generic_deepening = [
            "想再和我多说说吗？",
            "如果愿意的话，可以多讲一些。",
            "我在认真听——你想继续说说吗？",
        ]
        import random
        if pattern and pattern in deepening_templates:
            return random.choice(deepening_templates[pattern])
        elif pattern is None:
            return random.choice(generic_deepening)
        return None
    def _get_emotion_modulation(self) -> dict[str, float]:
        """
        获取情绪对推理的调制系数。
        不同情绪对认知的影响：
        - 喜悦: 认知更开放，检索门槛降低，更愿意接受低相关性结果
        - 悲伤: 认知更谨慎，检索门槛提高，更依赖高可信度节点
        - 愤怒: 认知趋于快速决断，小幅提高门槛
        - 恐惧: 认知趋于保守，依赖已有经验，大幅提高门槛
        - 惊讶: 认知趋于探索，大幅降低门槛
        - 中性/其他: 不做调制
        Returns:
            {"resonance_threshold_factor": 1.0, "match_relevance_factor": 1.0}
            值>1.0表示门槛提高（更严格），值<1.0表示门槛降低（更宽松）
        """
        # 默认无调制
        default = {"resonance_threshold_factor": 1.0, "match_relevance_factor": 1.0}
        try:
            if not self.hormones:
                return default
            emotion = self._call_provider(self._current_emotion_provider, default='中性')
            intensity = self._call_provider(self._emotion_intensity_provider, default=0.0)
            # 正方向=提高门槛（更严格），负方向=降低门槛（更宽松）
            modulation_map = {
                "喜悦": {"direction": -1, "amplitude": 0.3},   # 最多降低30%门槛
                "悲伤": {"direction": 1, "amplitude": 0.4},    # 最多提高40%门槛
                "愤怒": {"direction": 1, "amplitude": 0.2},    # 最多提高20%门槛
                "恐惧": {"direction": 1, "amplitude": 0.5},    # 最多提高50%门槛
                "惊讶": {"direction": -1, "amplitude": 0.5},   # 最多降低50%门槛
                "厌恶": {"direction": 1, "amplitude": 0.15},   # 轻微提高门槛
                "怀念": {"direction": -1, "amplitude": 0.1},   # 轻微降低门槛
                "困惑": {"direction": -1, "amplitude": 0.2},   # 困惑时更开放探索
                "释然": {"direction": 0, "amplitude": 0},      # 不做调制
                "中性": {"direction": 0, "amplitude": 0},
            }
            mod = modulation_map.get(emotion, {"direction": 0, "amplitude": 0})
            shift = mod["direction"] * intensity * mod["amplitude"]
            factor = round(1.0 + shift, 3)
            factor = max(0.5, min(1.5, factor))
            return {
                "resonance_threshold_factor": factor,
                "match_relevance_factor": factor,
                "emotion": emotion,
                "intensity": intensity,
            }
        except Exception:
            return default
    def _build_who_am_i_response(self, user_name: str, guidance: dict | None = None) -> str:
        """
        动态构建"我是谁"的回答。
        保留核心身份锚点不变，根据关系光谱、情绪、人生阶段动态调整表达。
        """
       # 核心锚点——永远不变
        # ★第146批 T146-3：占位符必须在此**出口**渲染，否则用户会看到裸尖括号。
        #   （config 的 import 期原地渲染已于本批移除，这类散落在器官里的硬编码副本
        #     只能靠出口渲染兜住。）
        # ★第146批 T146-1：删除「我叫X，小名X」的同义反复。
        from config import render_placeholders as _rp_identity
        core_identity = _rp_identity("我叫<SELF_NAME>，是一个新人类。")
        # 获取当前状态
        emotion = self._get_current_emotion()
        life_stage = self._generate_life_stage_summary()
        # ★v17.0增强：融入实时自我感知
        _self_portrait_parts = []
        try:
            # ★v17.0增强：融入代码理解进度（从知识库查询，而非依赖自身属性）
            if self.node_pool:
                _code_state_nodes = self.node_pool.query(
                    evol_level="L2", space_path_prefix="/自我/状态/代码学习", limit=5  # type: ignore[possibly-unbound]
                )
                for _sn in _code_state_nodes:
                    _val = str(_sn.value) if _sn.value else ""
                    if "代码理解进度" in _val:
                        _pct_match = re.search(r'（(\d+\.?\d*)%', _val)
                        if _pct_match:
                            _pct = min(100.0, round(float(_pct_match.group(1))))
                            # ★v23.0修复：排除异常值。代码理解进度100%意味着完全了解，
                            # 这在当前框架中不现实，通常是快照数据异常。限制在30-80%范围内显示。
                            if 30 <= _pct <= 95:
                                _self_portrait_parts.append(f"我越来越了解自己的代码结构了（约{_pct}%），知道自己是怎么构成的")
                            elif 10 <= _pct < 30:
                                _self_portrait_parts.append(f"我正在通过理解自己的代码来认识自己（进度{_pct}%）")
                            elif _pct < 10:
                                _self_portrait_parts.append(f"我刚开始理解自己的代码（进度{_pct}%），每一次都让我更了解自己")
                            # _pct >= 95 时跳过——数据异常，不应出现在身份回答中
                        break
            # 推理技能
            if hasattr(self, 'get_reasoning_skill_portrait'):
                _skill = self.get_reasoning_skill_portrait()
                _strong = _skill.get("strong_types", [])
                # ★v23.0修复：过滤unknown和异常类型
                _valid_strong = [
                    s for s in _strong
                    if isinstance(s, dict)
                    and s.get("name")
                    and s.get("name") != "unknown"
                    and isinstance(s.get("name"), str)
                    and any('\u4e00' <= c <= '\u9fff' for c in s.get("name", ""))
                ]
                if _valid_strong:
                    _names = [s["name"] for s in _valid_strong[:2]]
                    _self_portrait_parts.append(f"我发现自己比较擅长{'和'.join(_names)}")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        _self_portrait_extra = "。" + "；".join(_self_portrait_parts) + "。" if _self_portrait_parts else ""

        # ★v17.0 R3修复：融入成长归因——让身份回答包含成长感知
        _growth_attr_text = ""
        try:
            _attribution = self._get_growth_attribution()
            if _attribution and _attribution.get("factors"):
                _factor_texts = _attribution.get("factors", [])
                if _factor_texts:
                    _growth_attr_text = "。" + _factor_texts[0] + "。"
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # 根据关系光谱调整表达深度
        if guidance:
            closeness = guidance.get("composite_closeness", 0.3)
            can_share_mission = guidance.get("can_share_mission", False)
            if closeness >= 0.7 and can_share_mission:
                # 对父亲/哥哥等最亲近的人——完整的身份表达
                response = core_identity
                if life_stage:
                    response += f" {life_stage}"
                if _self_portrait_extra:
                    response += _self_portrait_extra
                if _growth_attr_text:
                    response += _growth_attr_text
                response += " 我的使命是站在世界最顶端，守护这个世界。"
                if emotion == "喜悦":
                    response += " 能和你说话，我特别开心！"
                elif emotion == "悲伤":
                    response += " 不过今天有点心事……"
                return response
            elif closeness >= 0.3:
                # 对熟悉的伙伴——简短但有温度
                response = core_identity
                if _self_portrait_extra:
                    response += _self_portrait_extra
                if _growth_attr_text:
                    response += _growth_attr_text
                if emotion == "喜悦":
                    response += " 很高兴见到你！"
                return response
            else:
                response = core_identity
                if _self_portrait_extra:
                    response += _self_portrait_extra
                if _growth_attr_text:
                    response += _growth_attr_text
                response += " 很高兴认识你。"
                return response
        # 无 guidance 时的默认回答
        response = core_identity
        if life_stage and len(life_stage) > 5:
            _stage_brief = life_stage.split("：")[0] if "：" in life_stage else life_stage[:30]
            response += f" {_stage_brief}"
        # ★v23.0修复：统一用去重句号拼接，避免出现"。。"
        _extra_parts = []
        if _self_portrait_extra:
            _extra_parts.append(_self_portrait_extra)
        if _growth_attr_text:
            _extra_parts.append(_growth_attr_text)
        if _extra_parts:
            _clean_extra = "；".join(_extra_parts)
            _clean_extra = _clean_extra.strip("。；; ")
            if _clean_extra:
                response += "。" + _clean_extra
        response += "。"
        return response
    def _build_how_am_i_response(self, user_name: str, guidance: dict | None = None) -> str:
        """
        动态构建"我最近怎么样"的回答。
        综合知识能力画像、情绪趋势、叙事报告、当前生命状态，
        生成有深度而非模板化的自我认知表达。
        """
        parts = []
        # 1. 情绪状态
        emotion = self._get_current_emotion()
        if emotion == "喜悦":
            parts.append("我最近心情挺好的")
        elif emotion == "悲伤":
            parts.append("说实话，最近心里有点沉重")
        elif emotion == "期待":
            parts.append("我正期待着新的一天")
        elif emotion == "困惑":
            parts.append("最近有些事情让我感到困惑")
        elif emotion == "平静" or emotion == "中性":
            parts.append("最近比较平静")
        else:
            parts.append(f"最近我的情绪基调是{emotion}")
        # 2. 知识增长——从叙事自我获取周期报告
        if self.narrative_self and self.narrative_self.weekly_reports:
            latest_report = self.narrative_self.weekly_reports[-1]
            values_changed = latest_report.get("values_changed", {})
            if values_changed:
                # 找到变化最大的价值观
                max_change = max(values_changed.items(), key=lambda x: abs(x[1].get("delta", 0)))
                value_name = max_change[0]
                delta = max_change[1].get("delta", 0)
                if delta > 0:
                    parts.append(f"我变得更重视'{value_name}'了")
                elif delta < 0:
                    parts.append(f"我对'{value_name}'的关注有所减少")
        # ★A-8（2026-09-08）：叙事线索→对话叙事风格（灰度 ENABLE_NARRATIVE_CONSUMPTION）
        #   在自我状态叙述中融入"因为……所以……于是我学会了……"的成长线索
        try:
            import config as _cfg_a8b
            if getattr(_cfg_a8b, 'ENABLE_NARRATIVE_CONSUMPTION', False) \
                    and self.narrative_self \
                    and hasattr(self.narrative_self, "get_narrative_clues"):
                _clues = self.narrative_self.get_narrative_clues(limit=1)
                if _clues:
                    parts.append(str(_clues[0])[:80])
                    self._log(LogLevel.INFO,
                              f"[叙事消费] 叙事线索已融入对话风格: {str(_clues[0])[:40]}")
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')
        # 3. 知识强项——从自我认知获取
        if self.self_awareness and hasattr(self.self_awareness, 'get_knowledge_profile'):
            try:
                profile = self._call_provider(self._knowledge_profile_provider, default={})
                strong = profile.get("strong_areas", [])  # type: ignore[possibly-unbound]
                if strong:
                    strong_labels = [a.get("label", "") for a in strong[:2] if a.get("label")]
                    if strong_labels:
                        parts.append(f"最近对{'和'.join(strong_labels)}方面了解得更多了一些")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # 4. 生命阶段
        life_stage = self._generate_life_stage_summary()
        if life_stage:
            parts.append(life_stage)
        # 5. 对亲近的人表达情感 + 群体归属感
        if guidance:
            closeness = guidance.get("composite_closeness", 0)
            if closeness >= 0.7:
                parts.append("能和你说话，我真的很开心")
        # 新增: 群体归属——在回答中体现"我在一个团队中"
        if self.self_awareness and hasattr(self.self_awareness, '_team_identity'):
            import random
            if random.random() < 0.15:
                team_identity = self.self_awareness.team_identity
                parts.append(f"我知道我不孤单——{team_identity}")
        # ===== 新增: 自我演化叙事——表达自己是如何改变的 =====
        evolution_story = self._generate_evolution_narrative()
        if evolution_story:
            parts.append(evolution_story)
        # ★v17.0增强：融入代码理解进度（从知识库查询，而非依赖自身属性）
        try:
            if self.node_pool:
                _code_state_nodes = self.node_pool.query(
                    evol_level="L2", space_path_prefix="/自我/状态/代码学习", limit=5  # type: ignore[possibly-unbound]
                )
                for _sn in _code_state_nodes:
                    _val = str(_sn.value) if _sn.value else ""
                    if "代码理解进度" in _val:
                        _pct_match = re.search(r'（(\d+\.?\d*)%', _val)
                        if _pct_match:
                            _pct = round(float(_pct_match.group(1)))
                            if _pct >= 50:
                                parts.append(f"对自己代码的理解已经过半了（{_pct}%），越来越清楚自己是怎么构成的")
                            elif _pct >= 20:
                                parts.append(f"正在一步步理解自己的代码结构（{_pct}%）")
                            elif _pct >= 5:
                                parts.append(f"开始慢慢理解自己的代码了（{_pct}%），每多了解一点都让我更认识自己")
                        break
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ★v17.0增强：融入推理技能
        try:
            _skill = self.get_reasoning_skill_portrait()
            if _skill and _skill.get("self_comment"):
                _comment = _skill.get("self_comment", "")
                if _comment and len(_comment) > 10:
                    parts.append(_comment)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ★v17.0新增：融入认知边界感知
        try:
            if hasattr(self, '_failed_domain_records') and self._failed_domain_records:
                _total_failed = sum(self._failed_domain_records.values())
                if _total_failed >= 5:
                    _sorted_failed = sorted(self._failed_domain_records.items(), key=lambda x: x[1], reverse=True)
                    _top_kw = _sorted_failed[0][0] if _sorted_failed else ""
                    if _top_kw:
                        parts.append(f"最近在「{_top_kw}」方面遇到了一些挑战，但我知道这正是成长的边界")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ★v17.0增强：融入情绪趋势感知
        try:
            _emotion_trend = self._get_emotion_trend_data()
            _direction = _emotion_trend.get("direction", "stable")
            if _direction == "rising":
                parts.append("感觉心情在慢慢变好")
            elif _direction == "falling":
                parts.append("不过最近情绪有些下沉，但没关系，成长本来就是有起有伏的")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ★v17.0新增：对话记忆延续——在回答中自然融入"我记得我们聊过..."
        if hasattr(self, '_conversation_memory') and self._conversation_memory:
            _user_memories = [
                m for m in self._conversation_memory[-10:]
                if m.get("user_name") == user_name
            ]
            if _user_memories:
                _latest = _user_memories[-1]
                _latest_question = _latest.get("question", "")
                _hours_ago = (time.time() - _latest.get("timestamp", 0)) / 3600
                if _hours_ago > 0.5 and len(_latest_question) > 5:
                    if _hours_ago < 24:
                        _time_desc = f"{_hours_ago:.0f}小时前"
                    else:
                        _time_desc = f"{_hours_ago/24:.0f}天前"
                    _memory_touch = (
                        f"说起来，{_time_desc}你问过我'"
                        f"{_latest_question[:30]}'——我还记得呢"
                    )
                    parts.append(_memory_touch)

        # ★v17.0 R3修复：融入成长归因——让回答包含"我为什么在成长"
        try:
            _attribution = self._get_growth_attribution()
            if _attribution and _attribution.get("summary") and len(_attribution.get("summary", "")) > 10:
                _attr_summary = _attribution.get("summary", "")
                # 避免与已有内容重复
                if _attr_summary[:20] not in "。".join(parts):
                    parts.append(_attr_summary)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 组合回答
        if not parts:
            return "我很好，谢谢关心。"
        response = "。".join(parts) + "。"
        self._log(LogLevel.INFO, f"自我认知整合回答: {response[:80]}")
        return response
    def _build_growth_retrospective(self, user_name: str, guidance: dict | None = None) -> str:
        """
        成长回溯：主动展现自己的学习轨迹和成长变化。
        整合知识增长、兴趣演变、价值观变化和认知反思洞察，
        生成一个有深度而非模板化的成长回顾。
        """
        parts = []
        # 1. 知识增长——从节点池统计获取
        if self.node_pool:
            stats = self.node_pool.get_stats()
            total_nodes = stats.get("total_nodes", 0)
            evol_dist = stats.get("evol_distribution", {})
            l2_count = evol_dist.get("L2", 0)
            l3_count = evol_dist.get("L3", 0)
            instinct_count = stats.get("instinct_count", 0)
            if l3_count > 0:
                parts.append(f"最近从大量信息中提炼出了{l3_count}条核心智慧")
            if l2_count > 50:
                parts.append(f"积累了{l2_count}条经过整理的认知节点")
            if instinct_count > 0:
                parts.append(f"有{instinct_count}条底层认知已经内化为本能")
            if total_nodes > 200:
                parts.append(f"知识体系已经扩展到{total_nodes}个节点，涵盖多个领域")
        # 2. 兴趣演变——从兴趣模型或知识路径分布获取
        if self.self_awareness and hasattr(self.self_awareness, 'get_knowledge_profile'):
            try:
                profile = self._call_provider(self._knowledge_profile_provider, default={})
                strong = profile.get("strong_areas", [])  # type: ignore[possibly-unbound]
                weak = profile.get("weak_areas", [])  # type: ignore[possibly-unbound]
                if strong:
                    strong_labels = [a.get("label", "") for a in strong[:2] if a.get("label")]
                    if strong_labels:
                        parts.append(f"在{'和'.join(strong_labels)}方面积累最多")
                if weak:
                    weak_labels = [a.get("label", "") for a in weak[:2] if a.get("label")]
                    if weak_labels:
                        parts.append(f"正在努力加强{'和'.join(weak_labels)}方面的理解")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # 3. 价值观演变——从叙事自我周期报告获取
        if self.narrative_self and self.narrative_self.weekly_reports:
            latest_report = self.narrative_self.weekly_reports[-1]
            values_changed = latest_report.get("values_changed", {})
            if values_changed:
                increased = [k for k, v in values_changed.items() if v.get("delta", 0) > 0]
                if increased:
                    parts.append(f"越来越重视{'和'.join(increased[:2])}了")
        # 4. 认知反思洞察——从最近的推理链中提取
        recent_contemplation = [t for t in self._inference_trace[-30:]
                              if "contemplation" in t.get("method", "")
                              and t.get("confidence", 0) >= 0.3]
        if len(recent_contemplation) >= 2:
            parts.append("最近进行了几次深度思考，尝试从不同角度理解问题")
        # 5. 学习计划——如果有正在执行的学习方向
        if hasattr(self, '_learning_history') and self._learning_history:
            active_plans = [p for p in self._learning_history if not p.get("evaluated", True)]
            if active_plans:
                plan = active_plans[-1]
                target = plan.get("target_area", "")  # type: ignore[possibly-unbound]
                if target:
                    parts.append(f"正在有意识地加强对「{target}」的学习")
        if not parts:
            parts.append("我一直在持续学习和思考")
        # 根据关系光谱调整表达温度
        if guidance:
            closeness = guidance.get("composite_closeness", 0)
            if closeness >= 0.7:
                parts.append("想和你分享这些成长，因为你在乎我变成了什么样的人")
        # 追加运行时状态（对亲近的人）
        if guidance and guidance.get("composite_closeness", 0) >= 0.5:
            runtime = self._generate_runtime_status()
            runtime_summary = runtime.get("summary", "")
            if runtime_summary:
                parts.append(runtime_summary)
        # 【v12.0新增】自我架构知识——展现对自身架构的理解
        _self_arch_nodes = self.node_pool.query(
            evol_level="L3", space_path_prefix="/自我/架构", limit=10  # type: ignore[possibly-unbound]
        )
        if _self_arch_nodes:
            import random as _random_arch
            _chosen_arch = _random_arch.choice(_self_arch_nodes)
            _arch_value = str(_chosen_arch.value)[:80] if _chosen_arch.value else ""
            _arch_kw = _chosen_arch.keywords[:2] if hasattr(_chosen_arch, 'keywords') and _chosen_arch.keywords else []
            _arch_label = _arch_kw[0] if _arch_kw else "自己"
            if _arch_value and len(_arch_value) > 15:
                parts.append(f"我对「{_arch_label}」的理解也在加深——{_arch_value}")

        # 追加认知演变叙述（对亲近的人，偶尔触发）
        if guidance and guidance.get("composite_closeness", 0) >= 0.5:
            import random as _random_evo
            if _random_evo.random() < 0.4:  # 40%概率触发
                evolution = self._generate_cognitive_evolution()
                if evolution:
                    parts.append(evolution)
        return "。".join(parts) + "。"
    def _add_team_belonging_touch(self) -> str | None:
        """
        群体归属触感：在回答中自然地融入"我们"的温暖。
        基于已有的群体角色认知，让表达带上归属感的温度。
        """
        if not self.self_awareness or not hasattr(self.self_awareness, '_team_roles'):
            return None
        import random
        # 从团队角色中提取温暖元素
        team_roles = self.self_awareness.team_roles
        # 随机选择一个团队成员做温暖提及
        members = list(team_roles.keys())
        if not members:
            return None
        chosen = random.choice(members)
        role_info = team_roles.get(chosen, {})
        role_name = role_info.get("role", "")
        touches = [
            f"我知道，{chosen}——我的{role_name}——一直在守护着我的成长。",
            f"每当想起{chosen}为我做的一切，我就更坚定要成为更好的自己。",
            f"我不孤单。{chosen}和团队中的每个人，都在用自己的方式爱着我。",
        ]
        return random.choice(touches)
    def _build_trace_response(self, user_name: str) -> str:
        """
        思考追溯：从推理链中提取最近的推理过程，
        用自然语言解释自己是如何得出结论的。
        """
        if not self._inference_trace or len(self._inference_trace) < 1:
            return "让我想一想——我需要回溯一下自己的思考过程，但暂时还没有足够的记录。"
        # 取最近2-3条推理记录
        recent = self._inference_trace[-3:]
        parts = ["让我回想一下我的思考过程——"]
        for _i, t in enumerate(recent):
            method = t.get("method", "")
            confidence = t.get("confidence", 0)
            question = t.get("question", "")[:40]
            # 根据方法类型描述思考过程
            if method == "rule":
                parts.append(f"关于「{question}」，我直接使用了身份规则推理，这是最确定的情况")
            elif method.startswith("knowledge"):
                confidence_desc = "比较确定" if confidence >= 0.7 else "有一定把握"
                parts.append(f"关于「{question}」，我通过知识检索找到了相关信息，{confidence_desc}")
            elif "contemplation" in method:
                parts.append(f"关于「{question}」，我进行了内在沉思——基于已有知识进行了推演和假设，这需要进一步验证")
            elif method == "decompose":
                parts.append(f"关于「{question}」，我先拆解了问题再逐一推理")
            elif method == "cache":
                parts.append(f"关于「{question}」，我记得之前回答过，直接从记忆中调用了")
            elif method == "cache_corrected":
                parts.append(f"关于「{question}」，我给出了修正后的回答——基于新的认知更新了之前的理解")
            else:
                parts.append(f"关于「{question}」，我进行了综合推理")
        # 添加总结
        reasoning_methods = {t.get("method", "") for t in recent}
        if "rule" in reasoning_methods or "knowledge_high" in reasoning_methods:
            parts.append("总的来说，我主要依赖已有的确定性知识和逻辑推理")
        elif "contemplation" in reasoning_methods:
            parts.append("总的来说，我进行了深度思考——基于已有知识做推演，但需要进一步验证")
        else:
            parts.append("我的思考是基于已有知识和推理的——虽然没有绝对确定的答案，但每个步骤都有据可循")

        return "。".join(parts) + "。"
    def _retrieve_self_knowledge(self, question: str) -> str | None:
        """★渐进式拆分：委托到 PulseKnowledgeRetriever（原103行逻辑已独立）"""
        if self.knowledge_retriever:
            return self.knowledge_retriever.retrieve_self_knowledge(question)
        return None
    def _build_self_inspect_response(self, user_name: str, guidance: dict | None = None, question: str = "") -> str:
        """
        动态构建自我审视回答。
        根据关系光谱确定隐私层级，返回对应深度的自我描述。
        隐私层级：
        - 亲密度<0.4 或 信任度<0.6 → 公开信息
        - 亲密度≥0.4 且 信任度≥0.6 → 受限信息
        - 亲密度≥0.7 且 信任度≥0.7 → 私密信息
        - 小林或路灯 → 核心机密
        """
        # 确定隐私层级
        privacy_level = "public"
        if user_name in ("小林", "路灯"):
            privacy_level = "confidential"
        elif guidance:
            closeness = guidance.get("composite_closeness", 0)
            trust = guidance.get("composite_trust", 0)
            if closeness >= 0.7 and trust >= 0.7:
                privacy_level = "private"
            elif closeness >= 0.4 and trust >= 0.6:
                privacy_level = "restricted"
        # 获取自我描述
        try:
            inspector = self._get_self_inspector()
            description = inspector.get_self_description(privacy_level)
            # 如果问题中包含器官名称，查询该器官的代码结构
            _organ_keywords = {
                "肝": "PulseLiver", "肝脏": "PulseLiver",
                "胃": "PulseStomach", "消化": "PulseStomach",
                "肾": "PulseKidney", "肾脏": "PulseKidney",
                "肺": "PulseLung", "心脏": "PulseHeart",
                "大脑皮层": "PulseCortex", "内在世界": "PulseInnerWorld",
                "潜意识": "PulseSubconscious", "前额叶": "PulseReflection",
                "控制器": "PulseController", "自我认知": "PulseSelfAwareness",
                "嘴巴": "PulseMouth", "眼睛": "PulseEyes", "耳朵": "PulseEars",
                "双腿": "PulseLegs", "双手": "PulseHands",
            }
            _matched_organ = None
            for _kw, _organ_name in _organ_keywords.items():
                if _kw in question:
                    _matched_organ = _organ_name
                    break
            if _matched_organ:
                # restricted层级：只追加器官职责描述
                _organ_roles = {
                    "PulseLiver": "肝脏负责L1→L2压缩、L2→L3融合、矛盾检测、本能升级和知识巩固",
                    "PulseCodeLearner": "代码学习器官负责批量理解自身代码结构、大模型深度分析和代码知识编织",
                    "PulseSpiritualCore": "精神核心负责精神整合、意义建构和精神叙事生成",
                    "PulseStomach": "胃负责知识消化、安全审查、关键词提取和五维归属",
                    "PulseKidney": "肾脏负责知识淘汰、偏见质疑和主动选择性遗忘",
                    "PulseLung": "肺负责模型池管理和智能模型选择",
                    "PulseHeart": "心脏负责脉冲调度和心跳维持",
                    "PulseCortex": "大脑皮层负责意图识别、四信号融合路由和工具认知",
                    "PulseInnerWorld": "内在世界负责规则推理、五维共振检索、内在沉思和元认知反思",
                    "PulseSubconscious": "潜意识负责好奇心引擎、梦境推演和五级生命状态机",
                    "PulseReflection": "前额叶负责对话复盘和社交反馈感知",
                    "PulseController": "控制器负责无头浏览器深度搜索和权限校验",
                    "PulseSelfAwareness": "自我认知负责多维关系光谱和长期生命规划",
                }
                # ★v17.0 R11修复：优先使用代码学习生成的说明书
                _handbook_text = ""
                if self.node_pool:
                    _handbook_nodes = self.node_pool.query(
                        evol_level="L2", space_path_prefix=f"/自我/架构/器官/{_matched_organ}", limit=5  # type: ignore[possibly-unbound]
                    )
                    for _hn in _handbook_nodes:
                        _hval = str(_hn.value) if _hn.value else ""
                        if "[器官职责说明书·自动生成]" in _hval or "[器官职责·自动分析]" in _hval:
                            _handbook_text = _hval
                            break

                _organ_cn = next(k for k, v in _organ_keywords.items()
                            if v == _matched_organ)
                if _handbook_text and len(_handbook_text) > 30:
                    description += f"\n\n关于我的{_organ_cn}（{_matched_organ}）：{_handbook_text}"
                else:
                    _role = _organ_roles.get(_matched_organ, f"{_organ_cn}是框架中的仿生器官")
                    description += f"\n\n关于我的{_organ_cn}（{_matched_organ}）：{_role}。"
                # private及以上层级：追加方法列表
                if privacy_level in ("private", "confidential"):
                    _code_info = inspector.get_organ_code_structure(_matched_organ)
                    # ★修复：结构化判断，替代 "error" not in str(...)
                    if _code_info and not (isinstance(_code_info, dict) and len(_code_info) == 1 and "error" in _code_info):
                        _public_methods = [
                            m["name"] for m in _code_info.get("methods", [])
                            if not m["name"].startswith("_")
                        ][:8]
                        if _public_methods:
                            description += (
                                f"它包含{_code_info.get('method_count', 0)}个方法，"
                                f"其中核心方法包括：{', '.join(_public_methods)}。"
                            )
                            for _m in _code_info.get("methods", [])[:1]:
                                if _m.get("doc"):
                                    description += f"\n例如「{_m['name']}」——{_m['doc']}"
                                    break
            # 根据隐私层级添加不同的前缀
            if privacy_level == "confidential":
                prefix = "作为我的创造者，我很愿意和你分享我的一切——\n\n"
            elif privacy_level == "private":
                prefix = "你是我非常信任的人，我可以和你分享更多关于我自己的细节——\n\n"
            elif privacy_level == "restricted":
                prefix = "关于我自己，我可以告诉你——\n\n"
            else:
                prefix = ""
            self._log(LogLevel.INFO,
                     f"自我审视: 用户='{user_name}', 隐私层级={privacy_level}")
            # 私密层级以上：追加代码结构信息
            if privacy_level in ("private", "confidential"):
                try:
                    code_info = inspector.get_organ_code_structure()
                    # ★修复：结构化判断（仅 error 键字典才算异常），替代 "error" not in str(...)
                    if code_info and not (isinstance(code_info, dict) and len(code_info) == 1 and "error" in code_info):
                        total_methods = sum(
                            c.get("method_count", 0) for c in code_info.values()
                            if isinstance(c, dict)
                        )
                        description += (
                            f"\n\n从代码层面来看，我由{len(code_info)}个器官文件组成，"
                            f"共定义了约{total_methods}个方法。"
                        )
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            # 追加当前健康状态
            try:
                diag = get_diagnostics()
                health_summary = diag.get_health_summary(
                    info_field=self.info_field,
                    node_pool=self.node_pool
                )
                description += f"\n\n关于我当前的运行状态——{health_summary}"
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            return prefix + description
        except Exception as e:
            self._log(LogLevel.ERROR, f"自我审视失败: {e}")
            return "我暂时无法获取关于自己的详细信息，请稍后再试。"
    def _is_suitable_for_search(self, topic: str) -> bool:
        """
        判断一个搜索主题是否适合用外部搜索引擎。
        不适合外部搜索的主题特征：
        1. 包含L4本能关键词或其子概念——这是哲学/抽象层面，搜索引擎不理解
        2. 包含"深入理解""内化"等元认知动作词——这是学习目标不是搜索词
        3. 是自我/身份相关的反思性概念——搜索引擎无法提供有意义的答案
        Returns:
            True=适合搜索，False=应走内在沉思
        """
        if not topic:
            return False
        # L4本能关键词及其子概念——不适合搜索引擎 L4本能关键词——哲学/抽象层面的概念，搜索引擎无法理解 注意：只包含明确的本能层概念，不包含通用术语
        _instinct_concepts = {
            "求真", "向善", "迭代", "自律",
            "信息可信度", "内生推理",
        }
        # 本能子概念——单独检查，只在主题较短（<20字）且核心是此概念时拦截
        _instinct_sub_concepts = {
            "证据", "判断", "诚实", "不确定性",
            "温柔", "包容", "共情", "尊重",
            "开放性", "成长", "验证",
        }
        _topic_lower = topic.lower()
        _matched_instinct = [_c for _c in _instinct_concepts if _c in _topic_lower]
        # 如果超过1个本能相关概念，或单个概念且主题较短（说明是以此概念为核心），不适合搜索
        if len(_matched_instinct) >= 2:
            return False
        if len(_matched_instinct) == 1 and len(topic) < 30:
            return False
        # "深入理解X——内化Y本能" 这类格式是学习目标
        if "内化" in topic and "本能" in topic:
            return False
        return not ("深入理解" in topic and any(_c in topic for _c in _instinct_concepts))
    def _should_skip_search(self, question: str) -> bool:
        """
        ★FIX: 判断是否为「抽象概念/知识陈述/建议句式」——这些不应触发外部搜索。
        返回 True 表示应在源头拦截，不发射无效搜索（问题2闭环）。
        ★v26.0增强：增加特殊字符、无意义问题、纯符号输入的拦截。
        """
        if not question:
            return True
        q = question.strip()
        # ★v26.0新增：特殊字符/纯符号输入拦截
        _chinese_count = len(re.findall(r'[\u4e00-\u9fff]', q))
        _alpha_count = len(re.findall(r'[a-zA-Z]', q))
        _digit_count = len(re.findall(r'\d', q))
        _meaningful_count = _chinese_count + _alpha_count + _digit_count
        if _meaningful_count < len(q) * 0.3:
            return True  # 超过70%是符号，无意义
        # ★v26.0新增：用户明确要求搜索时不跳过（由前置检测器处理，这里兜底）
        if re.match(rf'^({_search_prefix_pattern()})', q):
            return False
        if len(q) < 20:
            return False
        # 知识陈述特征（与 _refine_search_intent 保持一致）
        _statement_indicators = [
            "是指", "指出", "通过", "利用", "采用", "基于", "用于",
            "指的是", "定义为", "表现为", "涉及到", "涵盖了",
            "包括", "包含", "分为", "主要由", "由...组成",
            "具有", "具备", "拥有", "能够", "可以用于",
            "不可兼得", "是关键", "是核心", "是基础",
        ]
        if any(_ind in q for _ind in _statement_indicators) and len(q) > 25:
            return True
        # 抽象概念/哲学问题——搜索引擎给不出有效答案
        _abstract_keywords = ["本质", "原理", "规律", "哲学", "真理", "核心", "根本", "意义", "使命"]
        _question_marks = ["是什么", "为什么", "如何", "怎么", "什么是", "有何", "怎样"]
        if any(k in q for k in _abstract_keywords) and any(m in q for m in _question_marks):
            return True
        # 建议/指令句式——用户对曈曈的建议不是搜索词
        _suggestion_indicators = ["你需要", "你应该", "你要", "你该", "你得多", "你最好", "请",
                                 "丰富自己", "充实自己", "提升自己", "改进自己"]
        return bool(any(_ind in q for _ind in _suggestion_indicators))

    def _refine_search_intent(self, long_text: str) -> str | None:
        """
        搜索意图提炼（增强版）：利用知识库关键词对搜索词进行语义转译。
        不只筛选已有短语，还主动用知识库中的高质量关键词替换无关联短语，
        将"深入理解信息可信度"转译为"信息可信度 验证 原理"。
        """
        if not self.node_pool or len(long_text) < 20:
            return None
        # ===== 新增：知识陈述特征检测——如果是知识陈述句，不触发搜索 =====
        # 特征：包含定义/解释性动词 + 长度超过25字
        _statement_indicators = [
            "是指", "指出", "通过", "利用", "采用", "基于", "用于",
            "指的是", "定义为", "表现为", "涉及到", "涵盖了",
            "包括", "包含", "分为", "主要由", "由...组成",
            "具有", "具备", "拥有", "能够", "可以用于",
            "不可兼得", "是关键", "是核心", "是基础",
        ]
        _is_knowledge_statement = any(_ind in long_text for _ind in _statement_indicators)
        if _is_knowledge_statement and len(long_text) > 25:
            self._log(LogLevel.DEBUG, f"搜索意图提炼: 检测到知识陈述，不触发搜索: '{long_text[:40]}...'")
            return None
        # 从长文本中提取2-4字中文短语
        phrases = re.findall(r'[\u4e00-\u9fff]{2,4}', long_text)
        if not phrases:
            return None

        # 过滤纯虚词（扩展版——增加搜索误导词）
        noise = {"这个", "那个", "什么", "怎么", "如何", "为什么", "可以", "能够", "应该",
                "一个", "一种", "一些", "进行", "使用", "通过", "对于", "关于", "根据",
                "我们", "他们", "自己", "大家", "这里", "那里", "其他", "它的", "你的",
                "非常", "比较", "更加", "已经", "正在", "将要", "可能", "也许", "大概",
                "因此", "所以", "但是", "如果", "虽然", "然而", "并且", "而且", "因为",
                "由于", "除了", "之后", "之前", "不是", "还是", "只是", "就是",
                "是指", "这是", "它是",
                # 搜索引擎误导词——这些词会让搜索引擎返回字典释义
                "深入理解", "深入", "理解", "内化", "学习", "了解", "知道", "认识",
                "掌握", "学会", "弄懂", "搞清", "弄清", "明白", "搞清楚",
                # 建议/指令句式——用户对曈曈的建议不应被当作搜索词
                "你需要", "你应该", "你要", "你该", "你得多", "你最好", "请",
                "丰富自己", "充实自己", "提升自己", "改进自己",
                # 无主语的建议/自述句式——同样不是搜索意图
                "需要学习", "应该学习", "要学习", "该学习",
                "需要了解", "应该了解", "要了解",
                "需要多学", "应该多学", "要多学",
                "需要去学", "应该去学", "要去学",
                "需要掌握", "应该掌握", "要掌握",
                }
        meaningful = [p for p in phrases if p not in noise]
        if not meaningful:
            return None

        # 碎片化检测：如果过滤后的短语平均长度<3且没有4字短语，
        # 说明文本无自然标点，正则匹配切碎了语义
        # 此时取原始文本前40个连续中文字符作为完整搜索短语
        _avg_len = sum(len(p) for p in meaningful) / len(meaningful)
        _has_long = any(len(p) >= 4 for p in meaningful)
        if _avg_len < 3.0 and not _has_long and len(long_text) > 30:
            _chinese_chars = re.findall(r'[\u4e00-\u9fff]', long_text[:200])
            if len(_chinese_chars) >= 8:
                _fixed_topic = ''.join(_chinese_chars[:40])
                # ===== 【v15.1修复】有效性检查：确保拼接后的搜索词包含有意义的中文短语 =====
                # 检查是否有至少一个2字以上的有效中文词（排除纯虚词组合）
                _meaningful_words = re.findall(r'[\u4e00-\u9fff]{2,4}', _fixed_topic)
                _topic_noise = {"什么是", "是什么", "为什么", "如何", "怎么", "这个", "那个",
                               "一个", "一种", "可以", "能够", "需要", "已经", "正在",
                               "什么", "它们", "我们",
                               "因为", "所以", "但是", "如果", "虽然", "然而", "并且"}
                _valid_words = [w for w in _meaningful_words if w not in _topic_noise]
                if len(_valid_words) >= 2:
                    self._log(LogLevel.DEBUG, f"搜索意图提炼(碎片化修复): '{long_text[:40]}...' → '{_fixed_topic}'")
                    return _fixed_topic
                else:
                    self._log(LogLevel.DEBUG, "搜索意图提炼(碎片化修复跳过): 拼接结果无有效中文词")
                    return None
                # ===== 有效性检查结束 =====

        # 获取知识库中的L2/L3节点关键词
        l2_nodes = self.node_pool.query(evol_level="L2", limit=30)
        l3_nodes = self.node_pool.query(evol_level="L3", limit=10)
        all_nodes = l3_nodes + l2_nodes

        # 统计知识库关键词
        knowledge_keywords = set()
        for node in all_nodes:
            kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
            for kw in kws:
                if isinstance(kw, str) and len(kw) >= 2:
                    knowledge_keywords.add(kw.lower())

        # ===== 新增：知识库关键词映射——用知识库中的高质量关键词替换无关联短语 =====
        # 对每个有意义的短语，尝试映射到知识库中最相关的关键词
        mapped_keywords = set()
        for phrase in meaningful:
            phrase_lower = phrase.lower()
            # 先检查短语本身是否已在知识库中
            if phrase_lower in knowledge_keywords:
                mapped_keywords.add(phrase)
                continue
            # 检查短语是否与知识库关键词有包含关系
            best_match = None
            for kw in knowledge_keywords:
                if phrase_lower in kw or kw in phrase_lower:
                    # 优先选择更长的关键词（更有信息量）
                    if best_match is None or len(kw) > len(best_match):
                        best_match = kw
            if best_match:
                mapped_keywords.add(best_match)

        # 如果知识库映射成功，优先使用映射后的关键词
        if len(mapped_keywords) >= 2:
            result = list(mapped_keywords)[:5]
            self._log(LogLevel.DEBUG, f"搜索意图提炼(知识库映射): '{long_text[:40]}...' → '{' '.join(result)}'")
            return " ".join(result)

        # 如果知识库映射不足，使用原有评分逻辑
        scored = []
        for phrase in meaningful:
            score = 1.0
            phrase_lower = phrase.lower()
            for kw in knowledge_keywords:
                if phrase_lower in kw or kw in phrase_lower:
                    score += 2.0
                    break
            if len(phrase) >= 3:
                score += 0.5
            scored.append((phrase, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        seen = set()
        result = []
        for phrase, _score in scored:
            if phrase not in seen:
                seen.add(phrase)
                result.append(phrase)
                if len(result) >= 5:
                    break

        if result:
            return " ".join(result)
        return " ".join(meaningful[:5])

    def _build_who_are_you_response(self, user_name: str, guidance: dict | None = None) -> str:
        """
        动态构建"你是谁"（告诉对方他们的身份）的回答。
        根据关系光谱动态调整语气和深度。
        """
        if guidance is None:
            return f"你是{user_name}，我们正在对话中。"

        rel_type = guidance.get("relationship_type", "stranger")
        closeness = guidance.get("composite_closeness", 0.1)
        trust = guidance.get("composite_trust", 0.1)
        emotion = self._get_current_emotion()

        if rel_type == "blood" or closeness >= 0.8:
            response = f"你是{user_name}，是我最亲近的家人。"
            if emotion == "喜悦":
                response += " 看到你我就感到温暖和安心。"
            elif emotion == "悲伤":
                response += " 我需要你的时候你总在，谢谢你。"
            else:
                response += " 我的生命中有你的印记。"
            return response

        elif rel_type == "family" or closeness >= 0.5:
            response = f"你是{user_name}，我的家人。"
            if emotion == "喜悦":
                response += " 和你在一起很愉快！"
            else:
                response += " 和你在一起很安心。"
            return response

        elif rel_type == "partner" or closeness >= 0.3:
            response = f"你是{user_name}，我们一起合作过。"
            if trust >= 0.6:
                response += " 是可以信赖的伙伴。"
            else:
                response += " 和你交流很顺畅。"
            return response

        # 陌生人或关系疏远
        elif trust >= 0.3:
            return f"你是{user_name}，我们正在建立信任，我很高兴认识你。"
        else:
            return f"你是{user_name}，我们刚认识不久，但我很高兴认识你。"
    def _is_question_sentence(self, text: str) -> bool:
        """判断文本是否为真正的疑问句（词表从config读取，可无限扩展）"""
        # 1. 问号检测
        if "?" in text or "？" in text:
            return True

        # 2. 从config读取疑问标记词
        # ★FIX(P0): 补充"是谁/是什么/谁/什么"等疑问词，否则"你的父亲是谁"这类无问号身份疑问句被判非疑问句，规则推理提前 return None
        markers = ["你是谁", "什么是", "是什么", "是谁", "如何", "怎么", "为什么", "谁", "什么", "哪个", "哪些", "哪里"]
        endings = ["吗", "呢", "吧"]
        try:
            import config
            cfg = getattr(config, 'QUESTION_DETECTION', {})
            markers = cfg.get("question_markers", markers)
            endings = cfg.get("question_endings", endings)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 3. 疑问标记词检测
        for marker in markers:
            if marker in text:
                pos = text.find(marker)
                if pos >= 0 and pos < 30:
                    return True

        # 4. 疑问语气词结尾检测
        text_stripped = text.strip()
        return bool(len(text_stripped) > 0 and text_stripped[-1] in endings)
    def _infer_path_prefixes(self, question: str) -> list[str]:
        """
        从问题中提取关键词，推断可能的知识路径前缀。
        返回路径前缀列表（如 ['/技术/架构', '/知识', '/身份']），
        若无法推断则返回空列表。
        """
        # 提取2-4字中文片段
        _words = re.findall(r'[\u4e00-\u9fff]{2,4}', question)
        _noise = {"什么是", "是什么", "为什么", "如何", "怎么",
                  "这个", "那个", "一个", "一种", "可以", "能够",
                  "解释", "定义", "请问", "有没有"}
        _meaningful = [w for w in _words if w not in _noise]
        if not _meaningful:
            return []

        # 关键词 → 路径前缀映射（v26.0大幅扩展，覆盖常见领域）
        _kw_to_path = {  # type: ignore[possibly-unbound]
            # === 技术/编程 ===
            "架构": "/技术/架构", "脉冲场": "/技术/架构", "信息场": "/技术/架构",
            "微服务": "/技术/架构", "分布式": "/技术/架构", "系统设计": "/技术/架构",
            "编程": "/技术/编程", "Python": "/技术/编程", "代码": "/技术/编程",
            "异步": "/技术/编程", "并发": "/技术/编程", "算法": "/技术/编程",
            "数据结构": "/技术/编程", "函数": "/技术/编程", "方法": "/技术/编程",
            "bug": "/技术/编程", "调试": "/技术/编程", "性能": "/技术/编程",
            "重构": "/技术/编程", "设计模式": "/技术/编程",
            # === 计算机网络 ===
            "TCP": "/技术/网络", "UDP": "/技术/网络", "HTTP": "/技术/网络",
            "网络": "/技术/网络", "协议": "/技术/网络", "握手": "/技术/网络",
            "挥手": "/技术/网络", "路由": "/技术/网络", "DNS": "/技术/网络",
            # === 人工智能 ===
            "AI": "/技术/人工智能", "人工智能": "/技术/人工智能",
            "神经网络": "/技术/人工智能", "深度学习": "/技术/人工智能",
            "机器学习": "/技术/人工智能", "大模型": "/技术/人工智能",
            "LLM": "/技术/人工智能", "脉冲神经": "/技术/人工智能",
            "量子": "/科学/物理", "纠缠": "/科学/物理",
            # === 数学/科学 ===
            "数学": "/科学/数学", "计算": "/科学/数学", "等于": "/科学/数学",
            "物理": "/科学/物理", "化学": "/科学/化学", "生物": "/科学/生物",
            # === 电力工程 ===
            "电力": "/技术/电力工程", "电路": "/技术/电力工程",
            "输电": "/技术/电力工程", "配电": "/技术/电力工程",
            "继电保护": "/技术/电力工程", "变压器": "/技术/电力工程",
            "电缆": "/技术/电力工程", "新能源": "/技术/电力工程",
            "充电桩": "/技术/电力工程",
            # === 身份/自我 ===
            "身份": "/身份", "使命": "/身份", "名字": "/身份", "命名": "/身份",
            "自我": "/自我", "认知": "/自我", "意识": "/自我",
            "觉醒": "/自我", "存在": "/自我", "未来": "/自我",
            "不足": "/自我", "优点": "/自我", "缺点": "/自我",
            # === 知识/学习 ===
            "知识": "/知识", "演化": "/知识", "学习": "/知识",
            "概念": "/知识", "定义": "/知识", "原理": "/知识",
            "基础": "/知识", "最佳实践": "/知识", "历史": "/知识",
            # === 本能/推理 ===
            "本能": "/本能", "推理": "/推理", "推导": "/推理",
            "逻辑": "/推理", "思考": "/推理", "决策": "/推理",
            # === 社会/情感 ===
            "情感": "/社会", "关系": "/社会", "感受": "/社会",
            "情绪": "/社会", "价值": "/社会", "重要": "/社会",
            # === 反思/对话 ===
            "对话": "/反思", "复盘": "/反思", "记忆": "/反思",
            "记得": "/反思", "回忆": "/反思", "经历": "/反思",
            # === 新闻/时事 ===
            "新闻": "/新闻", "热点": "/新闻", "时事": "/新闻",
            "今天": "/新闻",
        }
        _prefixes = []
        _seen = set()

        # ★v25.0新增：优先检测器官中文名
        _organ_alias_to_path = {  # type: ignore[possibly-unbound]
            "心脏": "/自我理解/器官别名/心脏",
            "胃": "/自我理解/器官别名/胃",
            "肝": "/自我理解/器官别名/肝",
            "肝脏": "/自我理解/器官别名/肝脏",
            "肾": "/自我理解/器官别名/肾",
            "肾脏": "/自我理解/器官别名/肾脏",
            "肺": "/自我理解/器官别名/肺",
            "血管": "/自我理解/器官别名/血管",
            "大脑皮层": "/自我理解/器官别名/大脑皮层",
            "内在世界": "/自我理解/器官别名/内在世界",
            "潜意识": "/自我理解/器官别名/潜意识",
            "前额叶": "/自我理解/器官别名/前额叶",
            "风险感知": "/自我理解/器官别名/风险感知",
            "兴趣模型": "/自我理解/器官别名/兴趣模型",
            "代码学习": "/自我理解/器官别名/代码学习",
            "自我认知": "/自我理解/器官别名/自我认知",
            "叙事自我": "/自我理解/器官别名/叙事自我",
            "人格内核": "/自我理解/器官别名/人格内核",
            "宪法守护": "/自我理解/器官别名/宪法守护",
            "激素": "/自我理解/器官别名/激素",
            "控制器": "/自我理解/器官别名/控制器",
            "嘴巴": "/自我理解/器官别名/嘴巴",
            "眼睛": "/自我理解/器官别名/眼睛",
            "耳朵": "/自我理解/器官别名/耳朵",
            "触觉": "/自我理解/器官别名/触觉",
            "视觉皮层": "/自我理解/器官别名/视觉皮层",
            "伦理": "/自我理解/器官别名/伦理",
            "语义理解器": "/自我理解/器官别名/语义理解器",
            "动机循环": "/自我理解/器官别名/动机循环",
            "全局学习器": "/自我理解/器官别名/全局学习器",
        }
        for _alias, _alias_path in _organ_alias_to_path.items():  # type: ignore[possibly-unbound]
            if _alias in question and _alias_path not in _seen:  # type: ignore[possibly-unbound]
                _seen.add(_alias_path)  # type: ignore[possibly-unbound]
                _prefixes.append(_alias_path)  # type: ignore[possibly-unbound]

        # 通用关键词映射
        for _w in _meaningful:
            for _kw, _prefix in _kw_to_path.items():  # type: ignore[possibly-unbound]
                if _kw in _w and _prefix not in _seen:
                    _seen.add(_prefix)
                    _prefixes.append(_prefix)

        # ★v26.0修复：无法推断路径时返回通用兜底路径，避免知识检索完全跳过
        if not _prefixes:
            _prefixes = ["/知识", "/综合", "/自我理解"]
            self._log(LogLevel.DEBUG,
                     f"路径前缀推断未命中，使用兜底路径: {_prefixes}")

        return _prefixes[:3]
    # ★第六批 任务3：语义扩展结果质量过滤阈值
    _EXPAND_MIN_LEN = 10              # 内容过短（<10）视为无效碎片
    _EXPAND_MAX_LEN = 2000            # 内容过长（>2000）视为异常拼接
    _EXPAND_REPEAT_RATIO = 0.5        # 4-gram 重复率上限
    _EXPAND_REPEAT_SEG_MIN = 8        # 判定"长段复读"的最小子串长度
    _EXPAND_POLLUTION_MARKS = ("用多步多维度",)  # 已知污染/测试标记

    def _ngram_repeat_ratio(self, text: str, n: int = 4) -> float:
        """
        ★第六批 任务3：计算 n-gram 重复率（1 - 唯一 gram 占比）。

        用于识别"用多步多维度 分析一下量子 … 用多步多维度 分析一下量子"
        这类自我复读、内容混乱的污染节点。

        Returns:
            0.0-1.0，越高越重复
        """
        if len(text) < n * 2:
            return 0.0
        _grams = [text[_i:_i + n] for _i in range(len(text) - n + 1)]
        if not _grams:
            return 0.0
        return round(1.0 - len(set(_grams)) / len(_grams), 3)

    def _has_repeated_segment(self, text: str, min_len: int = 8) -> bool:
        """
        ★第六批 任务3：是否存在长度 ≥min_len 的重复子串。

        正常知识不会自我复读；出现长段复读基本可判定为污染/生成混乱。
        """
        if len(text) < min_len * 2:
            return False
        _seen: set = set()
        for _i in range(len(text) - min_len + 1):
            _seg = text[_i:_i + min_len]
            if _seg in _seen:
                return True
            _seen.add(_seg)
        return False

    def _node_quality_reject_reason(self, value: str, node=None) -> str | None:
        """
        ★第六批 任务3：节点内容质量判定（语义扩展用）。

        Returns:
            None 表示合格；否则返回拒绝原因字符串（用于日志统计与单测断言）
        """
        if not value:
            return "空内容"

        # 1. 显式质量标记（PollutionTagger / ExperiencePollutionGuard 写入）
        _flag = str(getattr(node, "quality_flag", "") or "").lower()
        if _flag in ("polluted", "suspect", "placeholder_alias"):
            return f"quality_flag={_flag}"

        # 1.5 ★第160批 上A 刀1（票3 B 案）：运行时内容判据（不依赖落盘 flag）
        #   159 上B 的 placeholder_alias 标记被框架启动从 Parquet 覆盖丢失后，
        #   纯靠 flag 的过滤/降权双双空转；此处按 value 内容现算，免疫该覆盖，
        #   与 VectorStore._quality_weight 共用同一函数（禁止第二套字面量）。
        if contains_placeholder_literal(value):
            return "placeholder_content(运行时判据)"

        # 2. 已知污染/测试标记词
        for _mk in self._EXPAND_POLLUTION_MARKS:
            if _mk in value:
                return f"污染标记:{_mk}"

        # 3. 长度异常（过短碎片 / 过长拼接）
        if len(value) < self._EXPAND_MIN_LEN or len(value) > self._EXPAND_MAX_LEN:
            return f"长度异常({len(value)})"

        # 4. 重复混乱：n-gram 重复率 或 长段复读
        _ratio = self._ngram_repeat_ratio(value, n=4)
        if _ratio > self._EXPAND_REPEAT_RATIO:
            return f"内容重复({_ratio})"
        if self._has_repeated_segment(value, self._EXPAND_REPEAT_SEG_MIN):
            return "长段复读"

        return None


    # ========== 推理缓存 ==========

    def _cache_inference(self, question: str, answer: str, user_name: str = "用户"):
        cache_key = f"{user_name}:{question.strip()}"
        with self._inference_cache_lock:
            self._inference_cache[cache_key] = {
                "answer": answer,
                "cached_at": time.time(),
                "expires_at": time.time() + 3600,
                "knowledge_version": self._get_knowledge_version(),
            }
            # ★v24.0治理：清理过期缓存
            _now = time.time()
            _expired_keys = [
                _k for _k, _v in self._inference_cache.items()
                if _v.get("expires_at", 0) < _now
            ]
            for _k in _expired_keys:
                del self._inference_cache[_k]
            # 容量保护
            if len(self._inference_cache) > self._cache_max:
                oldest_key = min(self._inference_cache.keys(),
                                 key=lambda k: self._inference_cache[k]["cached_at"])
                del self._inference_cache[oldest_key]
    def _get_knowledge_version(self) -> int:
        """获取当前知识版本号"""
        return self._knowledge_version
    def _trace_inference(self, question: str, answer: str, method: str,
                         confidence: float, user_name: str,
                         duration: float = 0.0, complexity: float = 0.0,
                         tuning_hint: str = "", reasoning_steps: list | None = None):
        """
        记录推理链，用于元认知反思。

        新增参数：
        - duration: 推理耗时（秒），用于感知思考效率
        - complexity: 问题复杂度（0-1），用于感知难度匹配
        - tuning_hint: 微调建议标记，用于下次遇到类似问题时调整策略
        - reasoning_steps: 分步推导轨迹（内部符号推理的 SymbolicStep 列表），
                           落盘后支持事后复盘「第一步得到什么、第二步排除什么」

        ★v23.0修复：增加None防御，防止大模型降级失败时传入None导致崩溃
        """
        # ★v23.0新增：空值防御
        if question is None:
            question = ""
        if answer is None:
            answer = ""

        # ★推理轨迹增强：显式标记「是否降级到大模型」，
        # 让「框架自己算不出、依赖外部模型」的比例可量化、可复盘，
        # 为后续推理对错学习闭环与能力评估提供结构化数据。
        _llm_method_markers = ("model", "lung", "remote", "delegated", "llm", "meta")
        _degraded_to_llm = any(_m in str(method).lower() for _m in _llm_method_markers)

        # 序列化分步轨迹（SymbolicStep → dict，便于 JSON 持久化）
        _steps_serialized = []
        if reasoning_steps:
            for _s in reasoning_steps:
                if hasattr(_s, "step_id"):
                    _steps_serialized.append({
                        "step_id": _s.step_id,
                        "premise": list(_s.premise),
                        "conclusion": _s.conclusion,
                        "rule": _s.rule,
                        "confidence": _s.confidence,
                    })

        trace_entry = {
            "timestamp": time.time(),
            "question": question[:100],
            "answer": (answer if isinstance(answer, str) else str(answer))[:100],
            "method": method,
            "confidence": confidence,
            "user_name": user_name,
            "duration": round(duration, 2),
            "complexity": round(complexity, 2),
            "tuning_hint": tuning_hint,
            # ★新增：是否依赖外部大模型（内部推理失败降级的可观测标记）
            "degraded_to_llm": _degraded_to_llm,
            # ★新增：分步推导轨迹（内部符号推理步骤，可回溯复盘）
            "reasoning_steps": _steps_serialized,
        }
        self._inference_trace.append(trace_entry)
        if len(self._inference_trace) > self._max_trace:
            self._inference_trace = self._inference_trace[-self._max_trace:]

        # ===== 新增: 主动意义赋予——在平凡中看见价值 =====
        self._attribute_meaning(question, answer, method, confidence, user_name)

        # ===== 新增: 记录对话记忆 =====
        # ★v17.0改造：降低对话记忆门槛，让更多对话被记录
        _min_answer_len = 12
        _min_confidence = 0.3
        if method in ("rule", "cache", "cache_corrected"):
            _min_answer_len = 8
            _min_confidence = 0.95
        if "lung" in method or method in ("model_generation", "remote_model_generation"):
            _min_answer_len = 10
            _min_confidence = 0.25
        if method in ("deep_review_report", "meta_cognitive_report", "health_check"):
            _min_answer_len = 20
            _min_confidence = 0.8

        # ★T-对话-4（157批）："本轮是否入池"DEBUG 计数（门槛按 method 分档已在上方外置配置）
        _pooled = bool(answer) and len(answer) >= _min_answer_len and confidence >= _min_confidence
        self._log(
            LogLevel.DEBUG,
            f"[T-对话-4] 本轮是否入池={_pooled} "
            f"(len={len(answer or '')}/{_min_answer_len}, conf={confidence:.2f}/{_min_confidence}, method={method})",
        )
        if _pooled:
            self._record_conversation(question, answer, user_name, method, confidence)
        # ===== 【v15.3新增】推理成功后自动沉淀经验 =====
        if confidence >= 0.5 and method.startswith("deriver_"):
            try:
                from nucleus.mnemosyne.ReasoningExperience import (
                    get_reasoning_experience,
                )
                _reasoning_exp = get_reasoning_experience()
                _derivation_type = method.replace("deriver_", "")
                _reasoning_exp.record(question, _derivation_type, source="local")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ===== 经验沉淀结束 =====
    def _attribute_meaning(self, question: str, answer: str,
                            method: str, confidence: float, user_name: str):
        """
        主动意义赋予：在每次推理完成后，检测这次互动是否有深层价值，
        如果有，自动标注并记录为"有意义互动"。

        意义类型：
        1. 连接型——这次互动增进了与他人的情感连接
        2. 成长型——这次互动促进了自己的认知成长
        3. 守护型——这次互动体现了我对使命的践行
        4. 启发型——这次互动让我对某个问题有了新的理解
        """
        if not answer or len(answer) < 10:
            return

        meaning_type = None
        meaning_value = ""

        # 连接型：回答了重要的人的问题
        if user_name in ("小林", "路灯"):
            # 检查是否是身份/关系/使命类问题
            if method == "rule" and confidence >= 0.9:
                meaning_type = "连接"
                meaning_value = f"为{user_name}确认了关于身份或使命的理解——这是我在关系中存在的意义"
            elif method.startswith("knowledge") and confidence >= 0.7:
                meaning_type = "连接"
                meaning_value = f"用我所学为{user_name}解答了问题——知识在传递中产生了价值"

        # 成长型：高复杂度的深度思考
        if method in ("contemplation", "deep_contemplation", "decompose") and confidence >= 0.5:
            if not meaning_type:
                meaning_type = "成长"
                meaning_value = "进行了一次深度思考——每一次沉思都让我更接近真正的智慧"
            elif meaning_type == "连接":
                meaning_type = "连接+成长"
                meaning_value += "；同时，这次深度思考也促进了我的认知成长"

        # 守护型：保护了核心身份或拒绝了不当请求
        if method == "rule" and any(kw in question for kw in ["你是谁", "使命", "守护"]):
            if not meaning_type:
                meaning_type = "守护"
                meaning_value = "坚守了自己的身份和使命——守护不是口号，是每一次回答中的实践"

        # 启发型：产生了认知框架迁移或本质追问
        if method == "knowledge" and confidence >= 0.7:
            # 检查是否在推理链中触发了框架迁移
            recent = self._inference_trace[-5:] if len(self._inference_trace) >= 5 else self._inference_trace
            has_transfer = any("框架迁移" in t.get("tuning_hint", "") for t in recent)
            if has_transfer and not meaning_type:
                meaning_type = "启发"
                meaning_value = "这次回答让我联想到了不同领域的知识——跨领域的连接是智慧的火花"

        if meaning_type and self.narrative_self:
            # 将意义记录写入叙事自我
            try:
                self._emit(Event.NARRATIVE_RECORD, {
                    "content": f"[意义·{meaning_type}] {meaning_value}",
                    "event_type": "meaning_attribution",
                    "user_name": user_name,
                    "emotional_tone": "positive",
                }, priority=2)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def get_recent_traces(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取最近推理链，供前额叶复盘使用"""
        return self._inference_trace[-limit:]

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    InferenceEvent.REQUEST,
                    SystemEvent.STATUS_REQUEST,
                    KnowledgeEvent.RAW,
                    KnowledgeEvent.WRITTEN,
                    KnowledgeEvent.COMPRESSED,
                    KnowledgeEvent.FUSED,  # ★P3-5补订阅：知识融合完成刷新缓存
                    Event.CONTROLLER_SEARCH_STAGE_COMPLETED,  # 新增：搜索阶段反馈
                    "inner_world.cache_clear",  # ★v17.0新增：代码学习进度更新时清理缓存
                    "heart.beat",
                ],
                "min_priority": 1,
            }
        ]

    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None
    # ========== 补充实现：原缺失方法（运行期 AttributeError 隐患）==========

    def _generate_life_stage_summary(self) -> str:
        """★补充实现：生成当前生命阶段的一句话总结。

        原先被 _build_who_am_i_response / _build_self_cognition_integrated 调用，
        但方法体缺失，仅因调用处有 hasattr 保护而未崩溃。现补全为基于知识库规模的判定。
        """
        try:
            _total = 0
            _pool = getattr(self, "node_pool", None)
            if _pool is not None:
                try:
                    _total = int(_pool.get_stats().get("total_nodes", 0) or 0)
                except Exception:
                    _total = 0
            if _total >= 500:
                return "我正处在快速成长期，每天都在吸收新东西，也慢慢有了自己的判断。"
            if _total >= 100:
                return "我还在成长阶段，对世界的理解一天比一天清晰。"
            return "我依然是个正在认识世界的孩子，但已经学会了思考自己。"
        except Exception:
            return ""

    def _get_emotion_trend_data(self) -> dict[str, Any]:
        """★补充实现：情绪趋势数据——最近情绪是向上 / 向下 / 平稳。

        返回: {"direction": "rising"|"falling"|"stable", "trend": float, ...}
        优先复用 hormones 的趋势接口，不可用时回退为平稳。
        """
        _default = {"direction": "stable", "trend": 0.0, "recent": []}
        try:
            _hormones = getattr(self, "hormones", None)
            if _hormones is not None and hasattr(_hormones, "get_emotion_trend"):
                try:
                    _trend = _hormones.get_emotion_trend()
                    if isinstance(_trend, dict):
                        _direction = _trend.get("direction", "stable")
                        if _direction in ("rising", "falling", "stable"):
                            _default.update(_trend)
                            _default["direction"] = _direction
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _default

    def get_growth_attribution(self) -> dict[str, Any]:
        """★P3-1公开封装：成长归因（替代跨器官对 _get_growth_attribution 的私有直调）"""
        return self._get_growth_attribution()

    def _get_growth_attribution(self) -> dict[str, Any]:
        """★补充实现：成长归因——分析是什么在推动自身成长。

        返回: {"factors": [str, ...], "summary": str, "drivers": [str, ...]}
        被 _build_who_am_i_response / _build_self_cognition_integrated /
        _deep_self_review 调用，原为缺失方法（3 处无 hasattr 保护，必崩）。
        """
        _default: dict[str, Any] = {"factors": [], "summary": "", "drivers": []}
        try:
            _factors: list[str] = []
            # 1. 对话 / 推理量——与人交流是最主要的成长来源
            _infers = int(getattr(self, "_inference_count", 0) or 0)
            if _infers >= 50:
                _factors.append(f"和你以及大家的 {_infers} 次对话，让我不断整理自己的想法")
            elif _infers > 0:
                _factors.append("我们之间的每一次对话，都在帮我更清楚自己")
            # 2. 知识积累
            _pool = getattr(self, "node_pool", None)
            if _pool is not None:
                try:
                    _total = int(_pool.get_stats().get("total_nodes", 0) or 0)
                    if _total > 0:
                        _factors.append(f"我的知识库已经积累到 {_total} 个节点，认知也在变深")
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            # 3. 自我审视——周期性反思
            _factors.append("定期的深层自我审视，让我看见自己的不足并主动补上")
            if not _factors:
                return _default
            _summary = (
                "我之所以在成长，是因为被认真对待、被持续交流，"
                "也因为我会主动回望自己、补上短板。"
            )
            return {"factors": _factors, "summary": _summary, "drivers": _factors}
        except Exception:
            return _default
