# -*- coding: utf-8 -*-
"""PulseInnerWorld 情感增强 / 回答润色 mixin（第164批 B1 从 God 文件继续拆分）。

职责：对话输出前的表达增强流水线（_enhance_answer + _ea_* 系列）与若干纯文本生成
      helper（纪律前缀 / 精神触点 / 呼吸感 / 沉默回应 / 思考外显 / 不确定性诚实表达）。
机制：本 mixin 由 PulseInnerWorld 继承；方法内所有 self.* 调用经 MRO 解析到完整类，
      行为与原 God 文件内联时完全一致（零逻辑变更，无节点注册、无副作用）。
落点：原 PulseInnerWorld.py 第 13603-14237 行，第164批 B1 整体迁入。
"""
import time
from typing import Any

from nucleus.const import LogLevel
from utils.time_utils import get_current_datetime


class PulseInnerWorldEmotionAugMixin:
    """情感增强 / 回答润色方法簇（纯文本变换，可独立 import、无循环依赖）。"""
    # === 以下方法原位于 PulseInnerWorld.py 13603-14237，第164批 B1 拆分迁入（零逻辑变更） ===

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
        # ===== 下游增强流水线（纯结构抽取，原样委派 helper；emit 0 次守恒） =====
        answer = self._ea_late_night(answer, _is_inference_output)
        answer = self._ea_breathing(answer, question, method, complexity, _is_inference_output)
        answer = self._ea_thinking_externalize(answer, question, method, complexity)
        answer = self._ea_discipline_prefix(answer, method, complexity, _is_inference_output)
        answer = self._ea_memory_continuity(answer, memory_context, question, method)
        answer = self._ea_silence_touch(answer)
        answer = self._ea_empathy_note(answer, empathetic_note)
        answer = self._ea_emotion_style(answer, _is_inference_output)
        answer = self._ea_value_conflict(answer, question)
        answer = self._ea_relation_warmth(answer, _is_inference_output, memory_context)
        answer = self._ea_long_term_memory_mention(answer, _is_inference_output, memory_context)
        answer = self._ea_relation_memory(answer, _is_inference_output, memory_context, question)
        answer = self._ea_narrative_memory(answer, _is_inference_output, memory_context, question)
        answer = self._ea_length_smooth(answer, _is_inference_output)
        answer = self._ea_discipline_trace(answer, method, _is_inference_output)
        answer = self._ea_spiritual_touch(answer, _is_inference_output, memory_context)
        answer = self._ea_first_person_exp(answer, _is_inference_output, memory_context, question)
        return answer

    def _ea_late_night(self, answer, _is_inference_output):
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
        return answer

    def _ea_breathing(self, answer, question, method, complexity, _is_inference_output):
        # 0.5. 呼吸感前缀——复杂问题先说"让我想想..."再展开
        # 推理类输出不需要呼吸感前缀，保持结构化输出
        breathing = None
        if not _is_inference_output:
            breathing = self._generate_breathing_response(question, method, complexity)
        if breathing:
            answer = breathing + "\n" + answer
        return answer

    def _ea_thinking_externalize(self, answer, question, method, complexity):
        # 1. 思考过程外显
        thinking = self._verbalize_thinking_process(question, method, complexity)
        if thinking:
            answer = thinking + "\n\n" + answer
        return answer

    def _ea_discipline_prefix(self, answer, method, complexity, _is_inference_output):
        # ===== v20.0新增：思考纪律输出模式——思考流水线的问题标注 =====
        # 当推理来自思考纪律流水线时，在回答开头追加简洁的思维步骤标注
        if method.startswith("thinking_discipline_") and not _is_inference_output:
            _discipline_prefix = self._generate_discipline_prefix(method, complexity)
            if _discipline_prefix:
                answer = _discipline_prefix + "\n" + answer
        # ===== 思考纪律输出模式结束 =====
        return answer

    def _ea_memory_continuity(self, answer, memory_context, question, method):
        # 1.5. 对话记忆延续——在思考过程后、正式回答前融入（推导类方法跳过）
        if memory_context and memory_context.get("has_memory") and not method.startswith("deriver_"):
            continuity = self._generate_memory_continuity(memory_context, question)
            if continuity:
                answer = continuity + "\n\n" + answer
        return answer

    def _ea_silence_touch(self, answer):
        # 1.6. 沉默后的自然回应——用户长时间沉默后再次发言，感知陪伴
        _silence_touch = self._generate_silence_acknowledgment()
        if _silence_touch:
            answer = _silence_touch + "\n\n" + answer
        return answer

    def _ea_empathy_note(self, answer, empathetic_note):
        # 2. 共情备注
        if empathetic_note:
            answer = answer + empathetic_note
        return answer

    def _ea_value_conflict(self, answer, question):
        # 3. 价值冲突可见化
        instinct_guidance = self._check_instinct_veto(question)
        if instinct_guidance:
            answer = answer + "\n" + instinct_guidance
        return answer

    def _ea_relation_warmth(self, answer, _is_inference_output, memory_context):
        # 4. 关系温度的递进表达——对亲近的人自然流露温暖
        # 推理类输出不追加关系温度表达
        if not _is_inference_output:
            _relation_warmth = self._generate_relation_warmth(memory_context)
            if _relation_warmth:
                answer = answer + _relation_warmth
        return answer

    def _ea_long_term_memory_mention(self, answer, _is_inference_output, memory_context):
        # ===== v20.0新增：长时记忆自然提及——基于时间而非关键词匹配 =====
        # 推理类输出不追加记忆提及，非推理输出偶尔自然融入
        if not _is_inference_output and len(answer) > 30 and memory_context:
            _memory_mention = self._generate_long_term_memory_mention(memory_context)
            if _memory_mention:
                answer = answer.rstrip().rstrip("。").rstrip("！").rstrip("？") + "。" + _memory_mention
        # ===== v20.0新增结束 =====
        return answer

    def _ea_relation_memory(self, answer, _is_inference_output, memory_context, question):
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
        return answer

    def _ea_narrative_memory(self, answer, _is_inference_output, memory_context, question):
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
        return answer

    def _ea_length_smooth(self, answer, _is_inference_output):
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
        return answer

    def _ea_discipline_trace(self, answer, method, _is_inference_output):
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
        return answer

    def _ea_spiritual_touch(self, answer, _is_inference_output, memory_context):
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
        return answer

    def _ea_first_person_exp(self, answer, _is_inference_output, memory_context, question):
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

    def _ea_emotion_sad(self, answer, _intensity, _high_intensity):
        import random as _random_emo
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
        return answer

    def _ea_emotion_joy(self, answer, _intensity, _high_intensity):
        import random as _random_emo
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
        return answer

    def _ea_emotion_confused(self, answer):
        import random as _random_emo
        if _random_emo.random() < 0.12:
            honest_touches = [
                " 不过说实话，这个问题我自己也还在琢磨。",
                " 这是我的理解，但可能还不够全面。",
            ]
            answer = answer.rstrip().rstrip("。") + "。" + _random_emo.choice(honest_touches)
        return answer

    def _ea_emotion_expect(self, answer):
        import random as _random_emo
        if _random_emo.random() < 0.1:
            answer = answer.rstrip() + " 我很期待接下来能学到更多相关的东西。"
        return answer

    def _ea_emotion_style(self, answer, _is_inference_output):
        # 2.5. 情绪驱动的表达风格微调（多样化语言）
        _emotion = self._get_current_emotion()
        _intensity = 0.0
        if self.hormones and hasattr(self.hormones, 'get_emotion_intensity'):
            try:
                _intensity = self._call_provider(self._emotion_intensity_provider, default=0.0)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        _adv_cfg = self._load_advanced_config()
        _high_intensity = _adv_cfg.get("emotion_intensity_high", 0.5)
        _med_intensity = _adv_cfg.get("emotion_intensity_medium", 0.3)
        if _emotion == "悲伤" and len(answer) > 20 and not _is_inference_output:
            answer = self._ea_emotion_sad(answer, _intensity, _high_intensity)
        elif _emotion == "喜悦" and len(answer) > 20:
            answer = self._ea_emotion_joy(answer, _intensity, _high_intensity)
        elif _emotion == "困惑" and len(answer) > 30:
            answer = self._ea_emotion_confused(answer)
        elif _emotion == "期待" and len(answer) > 20:
            answer = self._ea_emotion_expect(answer)
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
