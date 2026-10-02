# ⚠️ @deprecated (157-D C-4 躯体/多机封存 / Q157-2 已裁):
#   三壳·robot_body/__init__：纯包壳（37行），无独立语义；③弃用标注（原定②删除或③，为规避删除潜在引用断裂选③）。
#   复活须待 PHASE19（具身化/多机）专门批；禁止新代码 import 本模块（若仍在用请先接线）。
"""__init__ —— 模块化组件

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

# 导出全部躯体驱动类，项目全局一键导入
from .config_body import (
    CHANNEL_TO_NAME,
    ESP32_HOST,
    ESP32_TCP_PORT,
    SERVO_ANGLE_LIMIT,
    SERVO_CHANNEL_MAP,
    TRACK_INTERVAL,
    VOICE_ACTION_SERVO,
    VOSK_MODEL_PATH,
)
from .robot_body_driver import RobotBodyDriver
from .tcp_client import RobotBodyTCP
from .vision_tracker import RobotVisionTracker
from .voice_handler import RobotVoiceHandler

__all__ = [
    'CHANNEL_TO_NAME',
    'ESP32_HOST',
    'ESP32_TCP_PORT',
    'SERVO_ANGLE_LIMIT',
    'SERVO_CHANNEL_MAP',
    'TRACK_INTERVAL',
    'VOICE_ACTION_SERVO',
    'VOSK_MODEL_PATH',
    'RobotBodyDriver',
    'RobotBodyTCP',
    'RobotVisionTracker',
    'RobotVoiceHandler',
]