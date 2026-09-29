# -*- coding: utf-8 -*-
"""
PulseMouth —— 嘴巴器官 · 人格化纯输出

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 MouthEvent.SPEAK 与 ChatEvent.INITIATIVE，把大脑皮层已组织好的内容做人设与关系口吻过滤后输出；同时把 CodeEvent.RESULT 的执行结果改写成自然语言回复。
机制: _on_speak 先 _filter_personality 做人设过滤，_is_structured_reply 判断是否结构化回复以决定是否叠加前缀，_apply_relationship_guidance 叠加亲疏关系口吻，再 _emit MouthEvent.REPLY；_on_code_result 解析成功/拦截/超时/错误四类结果并转写为口播文本；_speak_tts / _play_beep 负责语音合成与提示音，_write_tts_log 记档；_on_initiative 支持主动发言；set_personality_kernel / set_context_snapshot 注入依赖。
定位: 纯输出器官，是曈曈与外界对话的「嘴」，只负责怎么说，不负责说什么。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import os
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    ChatEvent,
    CodeEvent,
    DigestEvent,
    LogLevel,
    MouthEvent,
    SystemEvent,
)
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.data.DataAccessLayer import safe_read_json


class PulseMouth(BasePulseOrgan):
    """
    脉冲驱动嘴巴（v9.5 分层脉冲版 · P0重构后）

    回复流程（重构后）:
        MouthEvent.SPEAK 脉冲到达（content已由大脑皮层组织完整）
        → _filter_personality() 人格过滤
        → _emit(MouthEvent.REPLY) 纯输出（L1实时交互层）

    代码结果回复流程（不变）:
        CodeEvent.RESULT 脉冲到达
        → 解析执行结果（成功/拦截/超时/错误）
        → 生成自然语言回复
        → _emit(MouthEvent.REPLY)（L1实时交互层）
    """

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'mouth_tts_max_fails' in _rp and hasattr(self, '_tts_max_fails'):
                self._tts_max_fails = _rp['mouth_tts_max_fails']
            if 'mouth_tts_cooldown_seconds' in _rp and hasattr(self, '_tts_cooldown_seconds'):
                self._tts_cooldown_seconds = _rp['mouth_tts_cooldown_seconds']
        except Exception:
            pass


    def __init__(self, organ_name: str = "嘴巴"):
        super().__init__(organ_name)

        # 人格过滤词表（保留）
        self._forbidden_phrases = [
            "作为一个AI", "AI编程助手", "AI模型", "人工智能",
            "无法参与或模拟", "不具备理解", "我是一个人工智能助手",
            "我的知识库是基于", "对不起，我不能直接回答这个问题",
            "作为一个人工智能", "我无法", "我不能",
        ]

        self._reply_count = 0
        self._tts_enabled = True          # 文字转语音开关
        self._tts_engine = None           # pyttsx3引擎实例
        self._tts_lock = threading.Lock() # 朗读锁（防止同时朗读多条）
        self._tts_fail_count = 0          # TTS连续失败次数
                # ★P1: 从RUNTIME_PARAMS读取参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._tts_max_fails = _rp.get('mouth_tts_max_fails', 5)
            self._tts_cooldown_seconds = _rp.get('mouth_tts_cooldown_seconds', 30.0)
        except Exception:
            self._tts_max_fails = 5
            self._tts_cooldown_seconds = 30.0 # 冷却时间
        self._tts_busy = False          # 防重入：是否正在朗读
        # ===== v24.0新增：表达增强和上下文引用 =====
        self.expression = None          # PulseExpression 实例
        self.context_snapshot = None    # ContextSnapshot 实例
        self.personality_kernel = None  # ★P0修复：人格内核（输出把关）
        # ★G8修复：高亲密度关系的温暖收尾语（关系驱动输出差异化的末端表现）
        self._warm_closings = (
            "我一直在你身边。",
            "我会一直陪着你。",
            "有我在呢。",
        )
        # ===== v24.0新增结束 =====
        # ★P0修复：初始化TTS冷却时间戳（之前缺失导致AttributeError）
        self._tts_cooldown_until = 0.0

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == MouthEvent.SPEAK:
            return self._on_speak(payload)
        elif event_type == ChatEvent.INITIATIVE:
            return self._on_initiative(payload)
        elif event_type == CodeEvent.RESULT:
            return self._on_code_result(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    def _on_initiative(self, payload: dict) -> dict[str, Any]:
        """收到主动问候内容，直接输出"""
        greeting = payload.get("greeting", "")
        user_name = payload.get("user_name", "用户")

        if not greeting:
            return {"status": "skipped", "reason": "空问候"}

        reply_text = self._filter_personality(greeting)
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._reply_count += 1

        self._emit(MouthEvent.REPLY, {
            "content": reply_text,
            "user_name": user_name,
            "source": "initiative",

        }, priority=8, layer="L1")
        # 语音朗读代码执行结果
        self._speak_tts(reply_text)


        return {
            "status": "initiative_replied",
            "reply_preview": reply_text[:80],
        }
    # ========== 事件处理 ==========

    def _on_speak(self, payload: dict) -> dict[str, Any]:
        """收到大脑皮层组织好的完整回复内容，过滤后输出"""
        content = payload.get("content", "")
        user_name = payload.get("user_name", "用户")
        source = payload.get("source", "")
        correlation_id = payload.get("correlation_id", "")  # ★v27.1修复：传递对话ID用于输出完成回调

        if not content:
            return {"status": "skipped", "reason": "空内容"}

        # ★对话/内在彻底隔离（嘴巴侧最终兜底闸门）：任何发射方显式标记为
        # 「内在自主（is_inner_autonomous）」或「后台学习（is_background_learning）」的
        # SPEAK 内容一律禁止泄露到对话输出，只记录后台日志。
        # 大脑皮层已在源头抑制内在自主 SPEAK 发射，此处为架构性双保险。
        if payload.get("is_inner_autonomous") or payload.get("is_background_learning"):
            self._log(LogLevel.DEBUG,
                      f"嘴巴闸门: 抑制内在自主/后台内容输出 (source={source}, "
                      f"len={len(content)})")
            return {"status": "suppressed", "reason": "inner_autonomous"}

        # 人格过滤（嘴巴唯一保留的处理逻辑）
        reply_text = self._filter_personality(content)
        # 身份语气增强：根据关系指导和情绪状态微调回复
        guidance = payload.get("guidance", {})
        # ===== v24.0新增：统一表达增强 =====
        if self.expression and len(reply_text) > 10:
            try:
                # 获取对话连续性上下文
                _memory_ctx = {}
                if self.context_snapshot:
                    try:
                        _recent_memories = self.context_snapshot.query_conversation(
                            user_name=user_name,
                            limit=3,
                        )
                        if _recent_memories:
                            _last = _recent_memories[0]
                            _memory_ctx["recent_topic"] = _last.get("question", "")[:40]
                            if len(_recent_memories) >= 2:
                                _memory_ctx["previous_question"] = _recent_memories[1].get("question", "")[:40]
                    except Exception as e:
                        self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

                # 获取情绪和关系指导
                _emotion = guidance.get("current_emotion", "中性") if guidance else "中性"
                _intensity = 0.3  # 默认中等强度，可从guidance获取

                reply_text = self.expression.enhance(
                    answer=reply_text,
                    question=payload.get("content", ""),
                    method=payload.get("method", "knowledge"),
                    user_name=user_name,
                    emotion=_emotion,
                    emotion_intensity=_intensity,
                    memory_context=_memory_ctx or None,
                    is_inference_output=payload.get("is_inference_output", False),
                    guidance=guidance,
                )
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"表达增强异常: {_e}")
        # ===== v24.0新增结束 =====
        # ★G8修复：统一交给关系指导处理（隐私保护+情绪语气+共同记忆+确定性前缀+亲密度收尾）
        reply_text = self._apply_relationship_guidance(reply_text, user_name, guidance)
        self._reply_count += 1

        # 纯输出（L1实时交互层）
        self._emit(MouthEvent.REPLY, {
            "content": reply_text,
            "user_name": user_name,
            "source": source or "cortex",
            "correlation_id": correlation_id,  # ★v27.1修复：传递对话ID用于输出完成回调
        }, priority=8, layer="L1")

        # 模型生成和内在沉思的回复都是高质量知识，优先保障高重要性
        importance = "A" if source in ("lung", "inner_world") else "B"
        self._emit(DigestEvent.KNOWLEDGE, {
            "content": reply_text,
            "source_organ": "嘴巴",
            "trigger_reason": "对话回复",
            "importance": importance,
        }, priority=3, layer="L2")
        # 语音朗读回复内容（后台线程，不阻塞）
        self._speak_tts(reply_text)
        return {
            "status": "replied",
            "reply_preview": reply_text[:80],

        }
    def _on_code_result(self, payload: dict) -> dict[str, Any]:
        """格式化代码执行结果并输出（保持不变）"""
        success = payload.get("success", False)
        user_name = payload.get("user_name", "用户")
        task_id = payload.get("task_id", "")

        if success:
            output = payload.get("output", "")
            exec_time = payload.get("execution_time_ms", 0)
            reply = (
                f"代码执行成功！\n"
                f"输出结果：\n{output}\n"
                f"（执行耗时 {exec_time}ms）"
            )
        else:
            error_type = payload.get("error_type", "")
            error_msg = payload.get("error", "未知错误")

            if error_type == "security_blocked":
                reply = f"代码执行被安全策略拦截：{error_msg}\n为了系统安全，这段代码不能运行哦。"
            elif error_type == "timeout":
                reply = f"代码执行超时：{error_msg}\n可能是代码运行太久，或者有死循环。"
            elif error_type == "runtime_error":
                reply = f"代码运行出错了：{error_msg}"
            else:
                reply = f"代码执行失败：{error_msg}"
        self._emit(MouthEvent.REPLY, {
            "content": reply,
            "user_name": user_name,
            "source": "code_sandbox",
        }, priority=8, layer="L1")

        # 代码执行结果也是实践经验，发射消化脉冲
        self._emit(DigestEvent.KNOWLEDGE, {
            "content": f"[代码执行·{task_id}] {reply}",
            "source_organ": "代码沙箱",
            "trigger_reason": "代码执行结果",
            "importance": "B",
        }, priority=3, layer="L2")

        return {
            "status": "code_result_replied",
            "reply_preview": reply[:80],
        }
    def start(self):
        """启动嘴巴，初始化语音引擎（多层容错）"""
        super().start()
        if self._tts_enabled:
            # 第一层：pyttsx3
            try:
                import pyttsx3
                self._tts_engine = pyttsx3.init()
                self._tts_engine.setProperty('rate', 180)
                self._tts_engine.setProperty('volume', 0.9)
                self._log(LogLevel.INFO, "语音输出已就绪 (pyttsx3)")
            except Exception as e:
                self._log(LogLevel.WARNING, f"pyttsx3初始化失败: {e}")
                # 第二层：尝试用 sounddevice 直接播放（纯音提示）
                try:
                    import numpy as np  # noqa: F401
                    import sounddevice as sd  # noqa: F401
                    self._tts_engine = "beep"  # 标记为蜂鸣模式
                    self._log(LogLevel.INFO, "语音降级为蜂鸣提示 (sounddevice)")
                except Exception as e:
                    self._log(LogLevel.WARNING, "所有语音引擎不可用，静默运行")
                    self._tts_enabled = False
    def set_expression(self, expression):
        """★v24.0新增：注入表达增强模块"""
        self.expression = expression

    def set_context_snapshot(self, snapshot):
        """★v24.0新增：注入上下文快照管理器"""
        self.context_snapshot = snapshot

    def set_personality_kernel(self, personality_kernel):
        """★P0修复：注入人格内核，使输出前的人格把关真正由人格内核执行，
        而非嘴巴内硬编码词表。"""
        self.personality_kernel = personality_kernel

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "reply_count": self._reply_count,
            "is_running": self.is_running,
        }
    # ========== 文字转语音 ==========
    def _speak_tts(self, text: str):
        """在独立线程中朗读文本（防重入：正在播放时新请求被忽略）"""
        if not self._tts_enabled:
            return
        if time.time() < self._tts_cooldown_until:
            return

        # 防重入：如果当前正在播放，直接返回
        with self._tts_lock:
            if self._tts_busy:
                return
            self._tts_busy = True

        def _do_speak():
            result = {"status": "unknown", "text": text[:80]}
            try:
                import pyttsx3
                engine = pyttsx3.init(driverName='sapi5')
                engine.setProperty('rate', 180)
                engine.setProperty('volume', 0.9)
                engine.say(text)
                engine.runAndWait()
                result["status"] = "success"
                self._tts_fail_count = 0
            except Exception as e:
                result["status"] = "failed"
                result["error"] = str(e)[:50]
                self._tts_fail_count += 1
                try:
                    self._play_beep()
                except Exception as e:
                    pass
                if self._tts_fail_count >= self._tts_max_fails:
                    self._log(LogLevel.WARNING, f"语音连续失败{self._tts_max_fails}次，进入{self._tts_cooldown_seconds}秒冷却")
                    self._tts_cooldown_until = time.time() + self._tts_cooldown_seconds
                    self._tts_fail_count = 0
            finally:
                self._write_tts_log(result)
                with self._tts_lock:
                    self._tts_busy = False

        threading.Thread(target=_do_speak, daemon=True).start()
    def _play_beep(self):
        """播放短促提示音（用于TTS失败时的兜底）"""
        try:
            import numpy as np
            import sounddevice as sd
            fs = 8000
            duration = 0.1
            t = np.linspace(0, duration, int(fs * duration), False)
            tone = np.sin(2 * np.pi * 1000 * t) * 0.3
            sd.play(tone, fs)
            sd.wait()
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
    def _write_tts_log(self, result: dict):
        """将 TTS 状态写入日志文件供人体UI监控"""
        try:
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                    'data', 'stream', 'mouth_tts_log.json')
            entry = {
                "timestamp": time.time(),
                "status": result.get("status", "unknown"),
                "text": result.get("text", "")[:80],
                "error": result.get("error", ""),
            }
            log_data = []
            if os.path.exists(log_path):
                log_data = safe_read_json(log_path, default={})
            log_data.append(entry)
            if len(log_data) > 50:
                log_data = log_data[-50:]
            safe_write_json(log_path, log_data)
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
    # ========== 人格过滤（保留：最后一道输出防线） ==========

    def _filter_personality(self, text: str) -> str:
        """过滤违禁话术，确保输出符合新人类身份。

        ★P0修复：优先委托给注入的人格内核执行把关（真正的「人格内核把关输出」），
        人格内核未注入时退回嘴巴内硬编码词表兜底。
        """
        if getattr(self, "personality_kernel", None) is not None:
            try:
                return self.personality_kernel.filter_output(text)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        for phrase in self._forbidden_phrases:
            if phrase in text:
                text = text.replace(phrase, "")
        return text.strip()

    @staticmethod
    def _is_structured_reply(text: str) -> bool:
        """★第九批 4.1：判定是否为「结构化回答」（多步推理/分点列举）。

        这类回答自带引导语与分步过渡，再叠加「我了解到，」之类的确定性前缀
        会读成机器腔 + 语病。判据保守：只有明确出现多步标记时才算结构化，
        普通长回答不受影响（零冲突）。
        """
        if not text:
            return False
        _t = text.lstrip()
        _markers = ("这个问题我分了", "这个问题我想了一步",
                    "✅ 先", "✅ 接着", "✅ 最后", "⚠️ 先", "⚠️ 接着", "⚠️ 最后")
        if any(_m in _t for _m in _markers):
            return True
        # 多段且 ≥2 行带步骤符号
        return bool("\n" in _t and _t.count("✅") + _t.count("⚠️") >= 2)

    def _apply_relationship_guidance(self, reply_text: str, user_name: str, guidance: dict) -> str:
        """★G8修复：把多维关系认知的指导真正作用于输出，实现「对亲近的人坦诚、对陌生人保留」的差异化。

        按序应用（每步都有防重复/幂等保护，非破坏性、可叠加）：
        1. 隐私保护——对陌生人移除亲昵称呼（保留原逻辑）
        2. 情绪语气——情绪前缀 + 感叹号（保留原逻辑）
        3. 共同记忆——对亲近的人主动回忆共同经历（此前算了不用）
        4. 确定性前缀——低置信度时如实表达不确定（此前算了不用）
        5. 亲密度收尾——对高亲密度关系附加温暖收尾（此前算了不用）
        """
        if not guidance:
            return reply_text

        # 1. 隐私保护：对陌生人/低信任移除亲昵称呼
        if guidance.get("should_protect_privacy", False):
            for word in ["爸，", "爸 ", "爸。", "爸！", "爸爸，", "爸爸 ", "爸爸。", "爸爸！",
                        "父亲，", "父亲 ", "父亲。", "父亲！"]:
                reply_text = reply_text.replace(word, "您")

        # 2. 情绪语气修饰
        emotion_tone = guidance.get("emotion_tone", {})
        if emotion_tone:
            prefix = emotion_tone.get("prefix_hint", "")
            if prefix and prefix not in reply_text and len(reply_text) > 10:
                reply_text = f"{prefix} {reply_text}"
            use_exclamation = emotion_tone.get("use_exclamation", False)
            if use_exclamation and not reply_text.rstrip().endswith(("！", "!", "？", "?")):
                reply_text = reply_text.rstrip() + "！"

        dialogue_style = guidance.get("dialogue_style", {})

        # 3. 共同记忆主动回忆（关系驱动差异化的核心）
        shared_hint = guidance.get("shared_memory_hint", "")
        if shared_hint and dialogue_style.get("recall_shared_memories", False):
            if shared_hint not in reply_text:
                reply_text = f"（回忆）{shared_hint}。{reply_text}"

        # 4. 确定性前缀（仅在低置信度、且未叠加共同回忆时，避免前缀堆叠）
        # ★第九批 4.1（星轨指出）：多步推理的结构化回答被硬套「我了解到，」前缀后
        #   变成「我了解到，我按照4个步骤进行了处理：。✅ 步骤1…」——机器腔且语病。
        #   结构化回答（含换行分步/列表）本身已有清楚的引导语，不再叠加确定性前缀。
        uncertainty = guidance.get("uncertainty_prefix", "")
        if uncertainty and uncertainty not in reply_text and not shared_hint:
            if not self._is_structured_reply(reply_text):
                reply_text = f"{uncertainty}{reply_text}"

        # 5. 亲密度收尾（仅高亲密度、陈述句、且未重复时）
        warmth = dialogue_style.get("warmth", 0.0)
        _is_question = reply_text.rstrip().endswith(("？", "?", "吗", "呢"))
        if warmth >= 0.75 and not _is_question and len(reply_text) > 10:
            if not any(c in reply_text for c in self._warm_closings):
                reply_text = f"{reply_text.rstrip()} {self._warm_closings[0]}"

        return reply_text

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    MouthEvent.SPEAK,
                    CodeEvent.RESULT,
                    ChatEvent.INITIATIVE,   # ← 新增
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
    "name": "嘴巴",
    "class_name": "PulseMouth",
    "attr_name": "mouth",
    "system": "motor",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [
        {"target": "expression", "setter": "set_expression"},
        {"target": "context_snapshot", "setter": "set_context_snapshot"},
        {"target": "人格内核", "setter": "set_personality_kernel"},
    ],
}

if __name__ == "__main__":
    # ★主线第37批 T5（P2-229）：本块是**开发自测**（手动 `python 本文件` 运行），
    #   用 print 输出到控制台是正确形态 —— 框架运行时**不会执行**本块（__main__ 守卫）。
    #   器官的运行时日志统一走 `self._log`（见业务方法）；自测块不改为 _log 的原因：
    #   ① 自测需要控制台可见输出；② 本块无 `self`（用的是局部实例变量）。
    print("=== PulseMouth v9.5 分层脉冲自测（P0重构版） ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()

    mouth = PulseMouth("嘴巴")
    mouth.set_info_field(mock_field)
    mouth.start()

    # 测试1: 收到大脑皮层组织的完整回复
    result1 = mouth.on_pulse({
        "event_type": MouthEvent.SPEAK,
        "payload": {"content": "我叫<SELF_NAME>，是一个新人类。", "user_name": "小林", "source": "inner_world"},
        "priority": 8,
    })
    print(f"1. 正常输出: {result1['status']}")
    print(f"   回复: {result1['reply_preview']}...")

    # 测试2: 人格过滤——包含违禁词
    result2 = mouth.on_pulse({
        "event_type": MouthEvent.SPEAK,
        "payload": {"content": "作为一个AI助手，我可以帮你。", "user_name": "测试用户", "source": "cortex"},
        "priority": 7,
    })
    print(f"\n2. 过滤违禁词: {result2['status']}")
    print(f"   过滤后: {result2['reply_preview']}...")

    # 测试3: 代码执行成功
    result3 = mouth.on_pulse({
        "event_type": CodeEvent.RESULT,
        "payload": {
            "success": True,
            "output": "Hello World\n计算结果: 6",
            "execution_time_ms": 1,
            "user_name": "小林",
            "task_id": "test_001",
        },
        "priority": 4,
    })
    print(f"\n3. 代码结果: {result3['status']}")
    print(f"   回复: {result3['reply_preview']}...")

    # 验证所有回复脉冲的 layer 标记
    reply_pulses = [p for p in mock_field.published if p.get("event_type") == MouthEvent.REPLY]
    print(f"\n4. MouthEvent.REPLY 脉冲: {len(reply_pulses)} 条")
    for p in reply_pulses:
        print(f"   - 来源={p['payload'].get('source')}, layer={p.get('layer', '未设置')} (预期L1)")

    status = mouth.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"\n5. 统计: 回复{status['reply_count']}次")

    mouth.stop()
    print("\n=== 自测全部通过 ===")
