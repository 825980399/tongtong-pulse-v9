# ⚠️ @deprecated (157-D C-4 躯体/多机封存 / Q157-2 已裁):
#   自制硬件躯体·voice_handler：依赖实体音频设备，现网无触发路径；③封存。
#   复活须待 PHASE19（具身化/多机）专门批；禁止新代码 import 本模块（若仍在用请先接线）。
"""voice_handler —— 躯体语音处理模块

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import json
from collections.abc import Callable
from typing import Any


class RobotVoiceHandler:
    """躯体语音处理模块"""
    
    def __init__(self):
        self._vosk_model = None
        self._recognizer = None
        self._audio_buffer = bytearray()
        
        # 关键词→动作映射
        self._keyword_actions: dict[str, dict[str, Any]] = {
            "挥手": {"action": "wave", "servo_channel": 0, "angle": 90},
            "点头": {"action": "nod", "servo_channel": 1, "angle": 30},
            "摇头": {"action": "shake", "servo_channel": 1, "angle": -30},
            "看着我": {"action": "look_at", "reset_tracking": True},
        }
        
        # 回调
        self._action_callback: Callable[[dict[str, Any]], None] | None = None
        self._speech_callback: Callable[[str], None] | None = None
        
        # 统计
        self._phrases_recognized = 0
        self._actions_triggered = 0
    
    def set_action_callback(self, callback: Callable[[dict[str, Any]], None]):
        """注册动作触发回调"""
        self._action_callback = callback
    
    def set_speech_callback(self, callback: Callable[[str], None]):
        """注册语音识别结果回调"""
        self._speech_callback = callback
    
    def load_model(self, model_path: str) -> bool:
        """加载Vosk离线模型"""
        try:
            import vosk
            self._vosk_model = vosk.Model(model_path)
            self._recognizer = vosk.KaldiRecognizer(self._vosk_model, 16000)
            print(f"[RobotVoiceHandler] Vosk模型已加载: {model_path}")
            return True
        except Exception as e:
            print(f"[RobotVoiceHandler] 模型加载失败: {e}")
            return False
    
    def feed_audio(self, pcm_data: bytes):
        """送入PCM音频数据"""
        if not self._recognizer:
            return
        
        if self._recognizer.AcceptWaveform(pcm_data):
            result = json.loads(self._recognizer.Result())
            text = result.get("text", "").strip()
            if text:
                self._phrases_recognized += 1
                if self._speech_callback:
                    self._speech_callback(text)
                self._match_keywords(text)
    
    def _match_keywords(self, text: str):
        """关键词匹配，触发对应动作"""
        for keyword, action_info in self._keyword_actions.items():
            if keyword in text:
                self._actions_triggered += 1
                if self._action_callback:
                    self._action_callback(action_info)
                print(f"[RobotVoiceHandler] 关键词触发: '{keyword}' → {action_info['action']}")
                break
    
    def add_keyword_action(self, keyword: str, action_info: dict[str, Any]):
        """动态添加关键词动作映射"""
        self._keyword_actions[keyword] = action_info
    
    def get_stats(self) -> dict:
        return {
            "model_loaded": self._vosk_model is not None,
            "phrases_recognized": self._phrases_recognized,
            "actions_triggered": self._actions_triggered,
            "keywords_count": len(self._keyword_actions),
        }