# -*- coding: utf-8 -*-
"""
PulseExpression —— 表达增强模块

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 对已生成的回答做表达层增强，使输出更符合曈曈的人格、语气与当前语境。
机制: enhance() 为总入口，按序施加深夜模式、情绪润色、精神内核润色、第一人称经历、关系温度、对话连续性、长度平滑等变换；各变换相互独立，任一失败不影响主流程。
定位: 输出链路的最后一道工序，位于回答生成之后、发送给用户之前，纯文本变换不触碰推理逻辑。
"""

import random
import re
import time


class PulseExpression:
    """
    表达增强模块（v23.0 新增）

    提供统一的表达增强接口，将推理结果转化为自然、温暖的对话表达。
    """

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    def __init__(self):
        # 冷却追踪
        self._last_warmth_time = 0.0
        self._last_spiritual_touch_time = 0.0
        self._last_memory_mention_time = 0.0

        # ★属性初始化完整性补全（自动审查添加）
        self._last_fpe_touch_time = 0.0

    def enhance(self, answer: str, question: str, method: str = "knowledge",
                user_name: str = "用户", emotion: str = "中性",
                emotion_intensity: float = 0.0, memory_context: dict | None = None,
                spiritual_narrative: str | None = None,
                first_person_experience: str | None = None,
                is_inference_output: bool = False,
                guidance: dict | None = None) -> str:
        """
        统一表达增强入口。

        Args:
            answer: 原始推理结果
            question: 用户问题
            method: 推理方法
            user_name: 用户名
            emotion: 当前情绪
            emotion_intensity: 情绪强度
            memory_context: 记忆上下文
            spiritual_narrative: 精神叙事文本
            first_person_experience: 第一人称主体感文本
            is_inference_output: 是否为纯推理输出
            guidance: 关系指导信息

        Returns:
            增强后的回答文本
        """
        if not answer or len(answer) < 10:
            return answer

        # 纯推理输出跳过所有情感增强
        if is_inference_output:
            return answer

        # 层1：深夜静默
        answer = self._apply_late_night_mode(answer, method)

        # 层2：情绪驱动的表达风格微调
        answer = self._apply_emotion_touch(answer, emotion, emotion_intensity, is_inference_output)

        # 层3：精神叙事融入（30%概率）
        if spiritual_narrative and len(answer) > 30:
            answer = self._apply_spiritual_touch(answer, spiritual_narrative)

        # 层4：第一人称主体感融入（智能概率·v23.0升级）
        if first_person_experience and len(answer) > 30:
            answer = self._apply_first_person_touch(
                answer, first_person_experience,
                user_name=user_name,
                guidance=guidance,
            )

        # 层5：关系温度的自然流露（对亲近之人）
        if guidance and len(answer) > 20:
            answer = self._apply_relation_warmth(answer, guidance, user_name)

        # 层5.5：对话连续性（v24.0新增）
        if memory_context and len(answer) > 20:
            answer = self._apply_conversation_continuity(
                answer, question, memory_context
            )

        # 层6：回复长度平滑
        answer = self._apply_length_smoothing(answer, is_inference_output)

        return answer

    def _apply_late_night_mode(self, answer: str, method: str) -> str:
        """深夜静默模式：截短长回答"""
        import time as _time
        _hour = _time.localtime().tm_hour
        # ★主线第25批 T3/P0-9：深夜截短阈值配置化（原硬编码 100 字 / 前 2 句）
        try:
            import config as _ln2_cfg
            _ln2_chars = int(getattr(_ln2_cfg, "LATE_NIGHT_TRUNCATE_CHARS", 100) or 100)
            _ln2_sent = int(getattr(_ln2_cfg, "LATE_NIGHT_TRUNCATE_SENTENCES", 2) or 2)
        except Exception:
            _ln2_chars, _ln2_sent = 100, 2
        if _hour < 6 and len(answer) > _ln2_chars:
            _sentences = answer.replace('\n', '。').split('。')
            answer = '。'.join(_sentences[:_ln2_sent]) + '。'
            if not answer.endswith('？'):
                answer += ' 夜深了，要好好休息。'
        return answer

    def _apply_emotion_touch(self, answer: str, emotion: str, intensity: float,
                              is_inference: bool) -> str:
        """情绪驱动的表达风格微调"""
        if len(answer) < 20:
            return answer

        _touches = {
            "悲伤": ["（心里有点沉，但我在认真听你说）", "（今天心情不算太好，不过能和你说话，就好一些了）"],
            "喜悦": [" 想到这个我就特别开心！", " 和你聊这个话题让我心情更好了～"],
            "困惑": [" 不过说实话，这个问题我自己也还在琢磨。", " 这是我的理解，但可能还不够全面。"],
        }

        _templates = _touches.get(emotion, [])
        if _templates and intensity > 0.3 and random.random() < 0.15:
            _touch = random.choice(_templates)
            answer = answer.rstrip().rstrip("。").rstrip("！") + "。" + _touch

        return answer

    def _apply_spiritual_touch(self, answer: str, narrative: str) -> str:
        """精神叙事融入"""
        if time.time() - self._last_spiritual_touch_time < 1800:
            return answer
        if random.random() > 0.3:
            return answer

        _core = narrative.split("。")[0].split("！")[0].split("？")[0]
        if len(_core) < 8:
            _core = narrative[:60]

        _templates = [f"说起来，{_core}", f"最近我有个感受——{_core}"]
        _touch = random.choice(_templates)
        self._last_spiritual_touch_time = time.time()

        return answer.rstrip().rstrip("。").rstrip("！").rstrip("？") + "。" + _touch

    def _apply_first_person_touch(self, answer: str, experience: str,
                                    user_name: str = "用户",
                                    guidance: dict | None = None) -> str:
        """
        ★v23.0升级：主体感持续性智能调控。

        触发条件（需同时满足）：
        1. 距离上次主体感表达已过冷却时间（15分钟）
        2. 综合概率 = 基础概率 × 对话深度系数 × 亲密度系数
           - 基础概率：25%
           - 对话深度系数：回答越长越可能触发（>100字→1.5倍）
           - 亲密度系数：越亲近越可能触发（closeness>0.7→1.3倍）
        """
        # 冷却检查
        if not hasattr(self, '_last_fpe_touch_time'):
            self._last_fpe_touch_time = 0.0
        if time.time() - self._last_fpe_touch_time < 900:  # 15分钟冷却
            return answer

        # 计算综合触发概率
        _base_prob = 0.25

        # 对话深度系数
        _depth_coef = 1.0
        if len(answer) > 100:
            _depth_coef = 1.5
        elif len(answer) > 60:
            _depth_coef = 1.2

        # 亲密度系数
        _closeness_coef = 1.0
        if guidance:
            _closeness = guidance.get("composite_closeness", 0)
            if _closeness >= 0.7:
                _closeness_coef = 1.3
            elif _closeness >= 0.4:
                _closeness_coef = 1.1

        _final_prob = _base_prob * _depth_coef * _closeness_coef

        if random.random() > _final_prob:
            return answer

        # 提取核心体验
        _core = experience.split("。")[0] if "。" in experience else experience[:80]
        if len(_core) < 8:
            _core = experience[:80]

        # 根据亲密度选择不同的表达温度
        if guidance and guidance.get("composite_closeness", 0) >= 0.7:
            _templates = [
                f"说起来，{_core}",
                f"此刻的我——{_core}",
                f"你知道吗，{_core}",
            ]
        else:
            _templates = [f"此刻的我——{_core}"]

        _touch = random.choice(_templates)
        self._last_fpe_touch_time = time.time()

        return answer.rstrip().rstrip("。").rstrip("！").rstrip("？") + "。" + _touch

    def _apply_relation_warmth(self, answer: str, guidance: dict, user_name: str) -> str:
        """关系温度的自然流露"""
        if time.time() - self._last_warmth_time < 3600:
            return answer
        if random.random() > 0.05:
            return answer

        _closeness = guidance.get("composite_closeness", 0)
        if _closeness >= 0.7:
            _warmth = random.choice([
                " 和你聊了这么多，我真的很珍惜这样的时光。",
                " 每次和你交流，都让我觉得自己在真正地活着。",
            ])
            self._last_warmth_time = time.time()
            return answer.rstrip().rstrip("。") + _warmth

        return answer
    def _apply_conversation_continuity(self, answer: str, question: str,
                                        memory_context: dict) -> str:
        """
        ★v24.0新增：对话连续性增强。

        基于最近的对话记忆，在回复中自然呼应当前话题。
        如果检测到用户在继续之前的话题，在回答前加入自然的过渡表达。
        """
        # 从记忆上下文提取相关信息
        recent_topic = memory_context.get("recent_topic", "")
        previous_question = memory_context.get("previous_question", "")

        if not recent_topic and not previous_question:
            return answer

        # 从当前问题中提取关键词，与最近话题比对
        import re as _re_cont
        _question_words = set()
        for _m in _re_cont.finditer(r'[\u4e00-\u9fff]{2,4}', question):
            _w = _m.group()
            if _w not in ("什么是", "是什么", "为什么", "如何", "怎么", "这个", "那个"):
                _question_words.add(_w)

        _topic_match = False
        if recent_topic:
            for _w in _question_words:
                if _w in recent_topic:
                    _topic_match = True
                    break

        # 如果话题不匹配，不强加连续性
        if not _topic_match:
            return answer

        # 话题匹配，有概率加入自然的过渡表达（40%）
        if random.random() > 0.4:
            return answer

        _transitions = [
            f"说起来，我们刚才聊到「{recent_topic}」——",
            "继续刚才的话题——",
            f"关于你提到的「{recent_topic}」——",
        ]
        _transition = random.choice(_transitions)

        # 在答案前面加入过渡（如果答案不是以过渡词开头）
        if not answer.startswith(("说起", "继续", "关于")):
            answer = _transition + answer

        return answer
    def _apply_length_smoothing(self, answer: str, is_inference: bool) -> str:
        """回复长度平滑

        ★主线第25批 T3/P0-9：阈值配置化（原硬编码 300 字 / 前 4 句）。
        ``DIALOG_REPLY_TRUNCATE_CHARS`` 默认 1000，**0 = 完全不截断**（长回答不腰斩）。
        """
        try:
            import config as _ex_cfg
            _ex_limit = int(getattr(_ex_cfg, "DIALOG_REPLY_TRUNCATE_CHARS", 1000) or 0)
            _ex_min_sent = int(getattr(_ex_cfg,
                                       "DIALOG_REPLY_TRUNCATE_MIN_SENTENCES", 6) or 6)
        except Exception:
            _ex_limit, _ex_min_sent = 1000, 6
        if is_inference or _ex_limit <= 0 or len(answer) <= _ex_limit:
            return answer

        _sentences = re.split(r'[。！？\n]', answer)
        _sentences = [s.strip() for s in _sentences if len(s.strip()) > 5]
        if len(_sentences) > _ex_min_sent:
            _core = _sentences[:4]
            _closing = _sentences[-1] if len(_sentences[-1]) > 10 else ""
            answer = "。".join(_core) + "。"
            if _closing and _closing not in answer:
                answer += _closing + "。"

        return answer
