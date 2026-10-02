# ⚠️ @deprecated (157-D C-4 躯体/多机封存 / Q157-2 已裁):
#   自制硬件躯体·robot_body_driver：依赖实体 TCP 设备，现网无设备即无触发路径；③封存。
#   复活须待 PHASE19（具身化/多机）专门批；禁止新代码 import 本模块（若仍在用请先接线）。
"""robot_body_driver —— 自制躯体总驱动

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import threading
import time

from nucleus._silent_except import silent_exc


class RobotBodyDriver:
    """自制躯体总驱动"""
    
    def __init__(self, esp32_host: str = "192.168.4.1"):
        # 三个核心模块
        from hardware.robot_body.tcp_client import RobotBodyTCP
        from hardware.robot_body.vision_tracker import RobotVisionTracker
        from hardware.robot_body.voice_handler import RobotVoiceHandler
        
        self._tcp = RobotBodyTCP(host=esp32_host)
        self._vision = RobotVisionTracker()
        self._voice = RobotVoiceHandler()
        
        # 回调绑定
        self._tcp.set_image_callback(self._on_image_received)
        self._tcp.set_audio_callback(self._on_audio_received)
        self._vision.set_track_callback(self._on_track_offset)
        self._voice.set_speech_callback(self._on_speech_recognized)
        self._voice.set_action_callback(self._on_action_triggered)
        
        # 运行状态
        self._running = False
        self._track_thread: threading.Thread | None = None
        self._track_interval = 0.033  # 30fps
        self._last_track_time = 0.0
        
        # PulseNet引用（启动后注入）
        self.info_field = None
        self.pulse_core = None
    
    def set_info_field(self, info_field):
        self.info_field = info_field
    
    def set_pulse_core(self, pulse_core):
        self.pulse_core = pulse_core
    
    def start(self, vosk_model_path: str = "models/vosk-model-small-cn-0.22") -> bool:
        """启动躯体驱动"""
        if not self._tcp.connect():
            return False
        
        self._voice.load_model(vosk_model_path)
        self._running = True
        
        print("[RobotBodyDriver] 躯体驱动已启动")
        return True
    
    def stop(self):
        """停止躯体驱动"""
        self._running = False
        self._vision.set_tracking_enabled(False)
        self._tcp.disconnect()
        print("[RobotBodyDriver] 躯体驱动已停止")
    
    def _on_image_received(self, jpeg_data: bytes):
        """收到图像帧：送视觉追踪"""
        # 负载降级：高负载时跳过部分帧
        if self._is_high_load():
            return
        
        now = time.time()
        if now - self._last_track_time < self._track_interval:
            return
        self._last_track_time = now
        
        self._vision.process_frame(jpeg_data)
        
        # 发射视觉帧脉冲到PulseNet（OUTER_VIEW标记）
        if self.pulse_core and self.info_field:
            frame_pulse = self.pulse_core.emit(
                source_organ="robot_body",
                event_type="eyes.stream_frame",
                payload={
                    "raw_data": jpeg_data,
                    "frame_seq": self._vision.get_frames_processed(),
                    "timestamp": now,
                    "source": "robot_body",
                    "view_mode": "OUTER_VIEW",
                },
                priority=3,
                layer="L3"
            )
            if frame_pulse:
                self.info_field.publish(frame_pulse)
    
    def _on_audio_received(self, pcm_data: bytes):
        """收到音频流：送语音识别"""
        self._voice.feed_audio(pcm_data)
    
    def _on_track_offset(self, offset_x: int, offset_y: int):
        """追踪偏移：下发云台指令"""
        self._tcp.send_track_offset(offset_x, offset_y)
    
    def _on_speech_recognized(self, text: str):
        """语音识别结果：发射到PulseNet耳朵"""
        print(f"[RobotBodyDriver] 语音识别: {text}")
        if self.pulse_core and self.info_field:
            speech_pulse = self.pulse_core.emit(
                source_organ="robot_body",
                event_type="ears.heard",
                payload={
                    "content": text,
                    "user_name": "用户",
                    "view_mode": "OUTER_VIEW",
                },
                priority=5,
                layer="L1"
            )
            if speech_pulse:
                self.info_field.publish(speech_pulse)
    
    def _on_action_triggered(self, action_info: dict):
        """动作触发：下发舵机指令"""
        action = action_info.get("action", "")
        if action == "wave":
            # 挥手动作序列
            self._tcp.send_servo_command(0, 0)
            time.sleep(0.3)
            self._tcp.send_servo_command(0, 90)
            time.sleep(0.3)
            self._tcp.send_servo_command(0, 0)
        elif action in ("nod", "shake"):
            channel = action_info.get("servo_channel", 1)
            angle = action_info.get("angle", 30)
            self._tcp.send_servo_command(channel, angle)
        elif action == "look_at":
            self._vision.set_tracking_enabled(True)
    
    def say(self, text: str):
        """让躯体扬声器播报文本"""
        self._tcp.send_speech_text(text)
    
    def set_tracking_enabled(self, enabled: bool):
        self._vision.set_tracking_enabled(enabled)
    
    def _is_high_load(self) -> bool:
        """检查系统负载"""
        try:
            if self.info_field and hasattr(self.info_field, 'get_load_level'):
                return self.info_field.get_load_level() in ("heavy", "critical")
        except Exception as e:
            silent_exc(e, where="robot_body_driver._is_high_load:152")
        return False
    
    def get_stats(self) -> dict:
        return {
            "tcp": self._tcp.get_stats() if self._tcp else {},
            "vision": self._vision.get_stats() if self._vision else {},
            "voice": self._voice.get_stats() if self._voice else {},
            "running": self._running,
        }