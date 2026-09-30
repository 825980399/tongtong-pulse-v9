"""PulseEars —— PulseEars 相关实现

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""
from config import TIMEOUT_CONFIG

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

"""
PulseEars —— 脉冲驱动耳朵（意图识别器官 · v9.5 分层脉冲版）
版本: v9.5 PulseNet
设计: 路灯、小林、星轨  
日期: 2026年6月9日
更新: 2026年6月13日（P0-2+P0-5: 五合一全面改造——事件枚举+统一日志+命名规范+get_stats+自测同步）
更新: 2026年6月14日（v9.5: 意图/代码/消化脉冲分层标记，适配分层异步调度）

职责:
    1. 接收 ChatEvent.MESSAGE 脉冲 → 意图识别
    2. 意图分类：身份/状态/代码/知识
    3. 指代消解："你"→曈曈，"他"→上下文推断
    4. 多轮上下文：保留最近5轮对话
    5. 发射 EarEvent.INTENT_DETECTED 脉冲（L1实时交互层）
    6. 代码块检测：识别```包裹的代码，发射 MotorEvent.EXECUTE 脉冲（L1实时交互层）
    7. 对话内容消化：发射 DigestEvent.KNOWLEDGE 脉冲给胃（L2认知思考层）
"""

import os
import re
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    ChatEvent,
    DigestEvent,
    EarEvent,
    LogLevel,
    MotorEvent,
    PersonaEvent,
    SystemEvent,
)
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc


class PulseEars(BasePulseOrgan):
    """脉冲驱动耳朵（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'ears_max_context' in _rp and hasattr(self, '_max_context'):
                self._max_context = _rp['ears_max_context']
        except Exception as e:
            silent_exc(e, where="organs.senses.PulseEars::refresh_runtime_params L62")


    def __init__(self, organ_name: str = "耳朵"):
        super().__init__(organ_name)

        self._context: list[dict[str, str]] = []
                # ★P1: 从RUNTIME_PARAMS读取参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._max_context = _rp.get('ears_max_context', 5)
        except Exception:
            self._max_context = 5

        self._identity_keywords = [
            "你是谁", "你叫什么", "你的名字", "你的身份",
            "我是谁", "我叫什么", "我是什么",
            "你的父亲", "你的哥哥", "你的创造者", "你的使命",
            "曈曈是谁", "路灯是谁", "小林是谁",
            "你是什么", "你是什么人", "你的生日",
        ]
        self._status_keywords = [
            "状态", "status", "运行", "心跳", "知识数",
            "节点", "器官", "硬件", "cpu", "内存",
        ]
        self._code_keywords = [
            "写一个", "实现", "函数", "编程", "代码",
            "def ", "class ", "import ", "print",
        ]

        self._listen_count = 0
        self._code_block_count = 0
        self._voice_enabled = True        # 语音监听开关
        self._voice_thread = None         # 语音监听后台线程
        self._voice_running = False       # 线程运行标记
        self._current_user_name = "访客"  # 当前用户（从SWITCHED脉冲更新）
        self._recognizer = None           # SpeechRecognition实例
        self._microphone = None           # 麦克风实例
        self._intent_distribution = {"身份": 0, "状态": 0, "代码": 0, "知识": 0}

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == ChatEvent.MESSAGE:
            return self._on_chat_message(payload)
        elif event_type == PersonaEvent.SWITCHED:
            return self._on_persona_switched(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _on_chat_message(self, payload: dict) -> dict[str, Any]:
        content = payload.get("content", "")
        user_name = payload.get("user_name", "用户")

        if not content:
            return {"status": "skipped", "reason": "空消息"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._listen_count += 1

        if self._is_code_block(content):
            code_blocks = self._extract_code_blocks(content)
            if code_blocks:
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._code_block_count += 1
                return self._handle_code_blocks(code_blocks, content, user_name)

        cleaned = self._preprocess(content)
        resolved = self._resolve_references(cleaned)
        intent = self._detect_intent(resolved)
        self._intent_distribution[intent] = self._intent_distribution.get(intent, 0) + 1

        self._update_context(content, user_name, intent)

        # P4修复: 将非代码文本内容发射给胃消化为知识节点（v9.5: L2认知思考层）
        if len(content.strip()) >= 5:
            self._emit(DigestEvent.KNOWLEDGE, {
                "content": content,
                "source_organ": "耳朵",
                "trigger_reason": "chat.conversation",
                "user_name": user_name,
            }, priority=3, layer="L2")

        return {
            "status": "detected",
            "intent": intent,
            "content_preview": content[:50],
        }
    def _on_persona_switched(self, payload: dict) -> dict[str, Any]:
        """收到身份切换脉冲，更新当前用户"""
        self._current_user_name = payload.get("current_user", "访客")  # ★T-118a 未知用户默认访客
        return {"status": "ok", "user": self._current_user_name}
    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()
    # ========== 生命周期 ==========
    
    def start(self):
        """启动耳朵，开启语音监听后台线程"""
        super().start()
        if self._voice_enabled:
            self._voice_running = True
            self._voice_thread = threading.Thread(target=self._voice_listen_loop, daemon=True)
            self._voice_thread.start()
            self._log(LogLevel.INFO, "语音监听已启动")
    
    def stop(self):
        """停止耳朵"""
        self._voice_running = False
        # 等待语音监听线程结束（最多2秒）
        if self._voice_thread and self._voice_thread.is_alive():
            self._voice_thread.join(timeout=TIMEOUT_CONFIG['thread_join'])
        super().stop()
        self._log(LogLevel.INFO, f"已停止，监听{self._listen_count}次")
    
    # ========== 语音监听循环 ==========
    def _voice_listen_loop(self):
        """后台线程：用sounddevice录音→Vosk离线识别→发射消息（主动结束版）"""
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            self._log(LogLevel.WARNING, "sounddevice未安装，语音监听不可用")
            return

        try:
            import json

            import vosk
        except ImportError:
            self._log(LogLevel.WARNING, "vosk未安装，语音监听不可用")
            return

        # 加载Vosk模型
        model_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'models', 'vosk-model-small-cn-0.22'
        )
        if not os.path.exists(model_path):
            self._log(LogLevel.WARNING, f"Vosk模型不存在: {model_path}")
            return

        try:
            model = vosk.Model(model_path)
            recognizer = vosk.KaldiRecognizer(model, 16000)
            recognizer.SetWords(False)  # 不需要逐词时间戳，加快速度
            self._log(LogLevel.INFO, "Vosk离线语音引擎已加载")
        except Exception as e:
            self._log(LogLevel.ERROR, f"Vosk模型加载失败: {e}")
            return

        sample_rate = 16000
        silence_threshold = 0.025
        silence_count = 0
        max_silence = 6          # 连续静音3次（6秒）认为说话结束
        last_partial_text = ""    # 最后一次有效的partial文本
        has_speech = False        # 当前语音段是否有过声音

        while self._voice_running and self.is_running:
            try:
                chunk_duration = 1.5
                recording = sd.rec(
                    int(chunk_duration * sample_rate),
                    samplerate=sample_rate,
                    channels=1,
                    dtype='float32'
                )
                sd.wait()

                rms = float(np.sqrt(np.mean(recording ** 2)))
                audio_int16 = (recording * 32767).astype(np.int16).tobytes()

                if rms >= silence_threshold:
                    # 有声音
                    has_speech = True
                    silence_count = 0
                    recognizer.AcceptWaveform(audio_int16)
                    
                    # 获取实时partial结果
                    partial = json.loads(recognizer.PartialResult())
                    partial_text = partial.get("partial", "").strip()
                    if partial_text:
                        last_partial_text = partial_text
                        self._write_ear_log(rms, True, f"(识别中) {partial_text}")
                    else:
                        self._write_ear_log(rms, True, "监听中")
                else:
                    # 静音
                    silence_count += 1
                    
                    if has_speech and silence_count >= max_silence and last_partial_text:
                        # 连续静音达到阈值且有有效文本，立即结束
                        if self._is_valid_speech(last_partial_text):
                            self._log(LogLevel.INFO, f"语音识别: {last_partial_text}")
                            self._write_ear_log(rms, False, f"识别成功: {last_partial_text}")
                            self._emit_voice_message(last_partial_text)
                        else:
                            self._write_ear_log(rms, False, f"无效语音(已过滤): {last_partial_text}")
                        
                        # 重置状态
                        recognizer = vosk.KaldiRecognizer(model, 16000)
                        recognizer.SetWords(False)
                        has_speech = False
                        last_partial_text = ""
                        silence_count = 0
                    # 正常送入静音帧（让Vosk自己判断）
                    elif recognizer.AcceptWaveform(audio_int16):
                        # Vosk自己判断语音结束
                        result = json.loads(recognizer.Result())
                        text = result.get("text", "").strip()
                        if text and self._is_valid_speech(text):
                            self._log(LogLevel.INFO, f"语音识别: {text}")
                            self._write_ear_log(rms, False, f"识别成功: {text}")
                            self._emit_voice_message(text)
                        elif text:
                            self._write_ear_log(rms, False, f"无效语音(已过滤): {text}")
                        # 重置识别器
                        recognizer = vosk.KaldiRecognizer(model, 16000)
                        recognizer.SetWords(False)
                        has_speech = False
                        last_partial_text = ""
                        silence_count = 0
                    else:
                        self._write_ear_log(rms, False, "静默")

            except Exception as e:
                self._log(LogLevel.DEBUG, f"语音监听异常: {e}")
                try:
                    recognizer = vosk.KaldiRecognizer(model, 16000)
                    recognizer.SetWords(False)
                except Exception as e:
                    pass
                has_speech = False
                last_partial_text = ""
                silence_count = 0
                time.sleep(0.5)
    def _is_valid_speech(self, text: str) -> bool:
        """
        校验识别结果是否为有效语音，过滤噪音和误识别。
        
        规则：
        1. 文本长度 >= 2 个字符
        2. 至少包含一个中文字符（排除纯英文/数字噪音）
        3. 不能是单一的常见误识别词（如单独的"我"、"你"、"的"等）
        """
        if not text or len(text.strip()) < 2:
            return False
        
        text_stripped = text.strip()
        
        # 必须包含至少一个中文字符
        import re
        if not re.search(r'[\u4e00-\u9fff]', text_stripped):
            return False
        
        # 过滤掉纯单字（Vosk 最常见的噪音误识别）
        common_noise = {"我", "你", "的", "了", "是", "在", "有", "他", "她", "它", "这", "那", "吗", "呢", "吧", "啊", "哦", "嗯", "哼", "哈"}
        return text_stripped not in common_noise                
    def _emit_voice_message(self, text: str):
        """发射语音识别结果到信息场"""
        if not text or not self.info_field or not self.pulse_core:
            return
        voice_pulse = self.pulse_core.emit(
            source_organ=self.organ_name,
            event_type=ChatEvent.MESSAGE,
            payload={
                "content": text,
                "original_input": text,
                "user_name": self._current_user_name,
                "is_complex": len(text) > 50,
                "code_blocks": [],
                "file_paths": [],
            },
            priority=5,
            layer="L1"
        )
        self.info_field.publish(voice_pulse)
        self._listen_count += 1

    def _write_ear_log(self, rms: float, active: bool, status: str = ""):
        """写入耳朵监听日志文件"""
        try:
            ear_log_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 
                'data', 'stream', 'ear_stream_log.json'
            )
            entry = {
                "timestamp": time.time(),
                "rms": round(rms, 6),
                "active": active,
                "status": status,
            }
            log_data = []
            if os.path.exists(ear_log_path):
                try:
                    log_data = safe_read_json(ear_log_path, default=[])
                except (ValueError, OSError) as e:
                    self._log(LogLevel.INFO, f"[WARNING] PulseEars.py:367: {type(e).__name__}: {e}")
                    log_data = []
                # ★第81批 T6：历史脏文件自愈（流日志本应是 list，若读到 dict 不得对其调 append）
                if isinstance(log_data, dict):
                    self._log(LogLevel.WARNING,
                              "[第81批 T6] 耳朵流日志为 dict（历史脏文件），自愈为 list")
                    log_data = []
            log_data.append(entry)
            if len(log_data) > 50:
                log_data = log_data[-50:]
            safe_write_json(ear_log_path, log_data)
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
    def _update_ear_log(self, text: str | None = None, status: str = ""):
        """更新耳朵监听日志的最新条目"""
        try:
            ear_log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 
                                         'data', 'stream', 'ear_stream_log.json')
            if os.path.exists(ear_log_path):
                log_data = safe_read_json(ear_log_path, default=[])
                if isinstance(log_data, dict):
                    self._log(LogLevel.WARNING,
                              "[第81批 T6] 耳朵流日志为 dict（历史脏文件），自愈为 list")
                    log_data = []
                if log_data:
                    if text:
                        log_data[-1]["recognized_text"] = text
                    log_data[-1]["status"] = status
            safe_write_json(ear_log_path, log_data)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    # ========== 代码块检测与执行 ==========

    def _is_code_block(self, text: str) -> bool:
        code_block_pattern = r'```[a-zA-Z]*\s*\n.*?\n```'
        if re.search(code_block_pattern, text, re.DOTALL):
            return True
        return bool(text.strip().startswith('```') and text.strip().endswith('```'))

    def _extract_code_blocks(self, text: str) -> list[dict[str, str]]:
        code_blocks = []
        pattern = r'```([a-zA-Z]*)\s*\n(.*?)\n```'
        matches = re.findall(pattern, text, re.DOTALL)
        for language, code in matches:
            code_blocks.append({
                "language": language.strip() or "python",
                "code": code.strip(),
            })
        if not code_blocks and text.strip().startswith('```') and text.strip().endswith('```'):
            inner = text.strip()[3:-3].strip()
            lines = inner.split('\n', 1)
            if lines[0].strip() in ["python", "javascript", "java", "go", "rust", "sql", "sh", "bash"]:
                code_blocks.append({
                    "language": lines[0].strip(),
                    "code": lines[1].strip() if len(lines) > 1 else "",
                })
            else:
                code_blocks.append({
                    "language": "python",
                    "code": inner,
                })
        return code_blocks

    def _handle_code_blocks(self, code_blocks: list[dict[str, str]], 
                             original_content: str, user_name: str) -> dict[str, Any]:
        # v9.5: 代码执行请求标记为L1实时交互层
        for i, block in enumerate(code_blocks):
            self._emit(MotorEvent.EXECUTE, {
                "code": block["code"],
                "language": block["language"],
                "user_name": user_name,
                "task_id": f"ears_code_block_{self._code_block_count}_{i}",
            }, priority=7, layer="L1")

        return {
            "status": "code_block_detected",
            "block_count": len(code_blocks),
            "languages": [block["language"] for block in code_blocks],
            "content_preview": original_content[:50],
        }

    # ========== 预处理 ==========

    def _preprocess(self, text: str) -> str:
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    # ========== 指代消解 ==========

    def _resolve_references(self, text: str) -> str:
        text = text.replace("你的", "曈曈的")
        text = text.replace("你", "曈曈")

        if "他" in text or "她" in text:
            last_person = self._find_last_person_in_context()
            if last_person:
                text = text.replace("他的", f"{last_person}的")
                text = text.replace("她的", f"{last_person}的")
                text = text.replace("他", last_person)
                text = text.replace("她", last_person)

        return text

    def _find_last_person_in_context(self) -> str | None:
        known_persons = ["小林", "路灯", "<CREATOR_DAUGHTER>"]
        for round_data in reversed(self._context):
            content = round_data.get("content", "")
            for person in known_persons:
                if person in content:
                    return person
        return None

    # ========== 意图分类 ==========

    def _detect_intent(self, text: str) -> str:
        text_lower = text.lower()

        for kw in self._identity_keywords:
            if kw in text_lower:
                return "身份"

        for kw in self._status_keywords:
            if kw in text_lower:
                return "状态"

        for kw in self._code_keywords:
            if kw in text_lower:
                return "代码"

        statement_markers = ["是", "有", "包含", "由", "属于", "定义", "称为"]
        question_marks = ["？", "?", "吗", "呢", "什么", "怎么", "如何", "为什么", "是谁", "是什么"]
        is_question = any(marker in text_lower for marker in question_marks)
        if not is_question and len(text) > 10:
            for marker in statement_markers:
                if marker in text_lower:
                    return "知识"

        return "知识"

    # ========== 多轮上下文 ==========

    def _update_context(self, content: str, user_name: str, intent: str):
        self._context.append({
            "content": content,
            "user_name": user_name,
            "intent": intent,
        })
        while len(self._context) > self._max_context:
            self._context.pop(0)

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "listen_count": self._listen_count,
            "code_block_count": self._code_block_count,
            "intent_distribution": self._intent_distribution,
            "context_rounds": len(self._context),
            "is_running": self.is_running,
        }

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    ChatEvent.MESSAGE,
                    SystemEvent.STATUS_REQUEST,
                    ChatEvent.MESSAGE,
                    PersonaEvent.SWITCHED,
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
    "name": "耳朵",
    "class_name": "PulseEars",
    "attr_name": "ears",
    "system": "senses",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    # ★主线第37批 T5（P2-229）：本块是**开发自测**（手动 `python 本文件` 运行），
    #   用 print 输出到控制台是正确形态 —— 框架运行时**不会执行**本块（__main__ 守卫）。
    #   器官的运行时日志统一走 `self._log`（见业务方法）；自测块不改为 _log 的原因：
    #   ① 自测需要控制台可见输出；② 本块无 `self`（用的是局部实例变量）。
    print("=== PulseEars v9.5 分层脉冲自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()

    ears = PulseEars("耳朵")
    ears.set_info_field(mock_field)
    ears.start()

    result1 = ears.on_pulse({
        "event_type": ChatEvent.MESSAGE,
        "payload": {"content": "你是谁", "user_name": "小林"},
        "priority": 8,
    })
    print(f"1. '你是谁': {result1['intent']}")

    # 验证意图检测脉冲的 layer 标记
    intent_pulses = [p for p in mock_field.published if p.get("event_type") == EarEvent.INTENT_DETECTED]
    if intent_pulses:
        print(f"   INTENT_DETECTED脉冲 layer: {intent_pulses[-1].get('layer', '未设置')} (预期L1)")

    # 验证消化脉冲的 layer 标记
    digest_pulses_l2 = [p for p in mock_field.published if p.get("event_type") == DigestEvent.KNOWLEDGE]
    if digest_pulses_l2:
        print(f"   KNOWLEDGE脉冲 layer: {digest_pulses_l2[-1].get('layer', '未设置')} (预期L2)")

    print("\n2. 代码块检测 - Python代码:")
    result2 = ears.on_pulse({
        "event_type": ChatEvent.MESSAGE,
        "payload": {
            "content": "```python\nprint('Hello World')\nx = 1 + 2\nprint(x)\n```",
            "user_name": "小林"
        },
        "priority": 8,
    })
    print(f"   状态: {result2['status']}")
    print(f"   代码块数: {result2['block_count']}")
    print(f"   语言: {result2['languages']}")

    # 验证代码执行脉冲的 layer 标记
    execute_pulses = [p for p in mock_field.published if p.get("event_type") == MotorEvent.EXECUTE]
    if execute_pulses:
        print(f"   EXECUTE脉冲 layer: {execute_pulses[-1].get('layer', '未设置')} (预期L1)")

    print("\n3. 代码块检测 - 无语言标记:")
    result3 = ears.on_pulse({
        "event_type": ChatEvent.MESSAGE,
        "payload": {
            "content": "```\nfor i in range(5):\n    print(i)\n```",
            "user_name": "小林"
        },
        "priority": 8,
    })
    print(f"   状态: {result3['status']}")
    print(f"   代码块数: {result3['block_count']}")

    print("\n4. 危险代码检测（耳朵只识别，代码沙箱拦截）:")
    result4 = ears.on_pulse({
        "event_type": ChatEvent.MESSAGE,
        "payload": {
            "content": "```python\nimport os\nos.system('del /f *.*')\n```",
            "user_name": "测试用户"
        },
        "priority": 8,
    })
    print(f"   状态: {result4['status']}")
    print("   说明: 耳朵只识别代码块，危险拦截由代码沙箱处理")

    status = ears.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"\n5. 统计: 监听{status['listen_count']}次, "
          f"代码块{status['code_block_count']}次, "
          f"分布={status['intent_distribution']}")

    print(f"\n6. motor.execute 脉冲: {len(execute_pulses)} 条")
    for p in execute_pulses[:2]:
        payload = p["payload"]
        print(f"   - 语言={payload['language']}, 代码长度={len(payload['code'])}, layer={p.get('layer', '未设置')}")

    ears.stop()
    print("\n=== 自测全部通过 ===")