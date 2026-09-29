"""vision_tracker —— 躯体视觉追踪模块

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

from collections.abc import Callable

import cv2
import numpy as np


class RobotVisionTracker:
    """躯体视觉追踪模块"""
    
    def __init__(self):
        self._face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        self._tracking_enabled = True
        
        # 画面参数
        self._frame_width = 640
        self._frame_height = 480
        
        # 云台死区（像素偏移在此范围内不动作）
        self._dead_zone_x = 30
        self._dead_zone_y = 20
        
        # 角度映射参数
        self._pan_per_pixel = 0.1   # 水平每像素对应度数
        self._tilt_per_pixel = 0.1  # 垂直每像素对应度数
        self._max_pan_angle = 90
        self._max_tilt_angle = 30
        
        # 当前人脸位置
        self._current_face_x: int | None = None
        self._current_face_y: int | None = None
        self._face_detected = False
        
        # 回调
        self._track_callback: Callable[[int, int], None] | None = None
        
        # 统计
        self._frames_processed = 0
        self._faces_detected = 0
    
    def set_track_callback(self, callback: Callable[[int, int], None]):
        """注册追踪偏移回调"""
        self._track_callback = callback
    
    def set_tracking_enabled(self, enabled: bool):
        self._tracking_enabled = enabled
    
    def process_frame(self, jpeg_data: bytes) -> tuple[int, int] | None:
        """
        处理单帧JPEG图像。
        返回云台偏移指令(offset_x, offset_y)，无需动作时返回None。
        """
        if not self._tracking_enabled:
            return None
        
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._frames_processed += 1
        
        # JPEG解码
        np_arr = np.frombuffer(jpeg_data, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if frame is None:
            return None
        
        self._frame_height, self._frame_width = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # 人脸检测
        faces = self._face_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
        )
        
        if len(faces) > 0:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._faces_detected += 1
            self._face_detected = True
            
            # 取最大人脸
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            center_x = x + w // 2
            center_y = y + h // 2
            self._current_face_x = center_x
            self._current_face_y = center_y
            
            # 计算偏移
            frame_center_x = self._frame_width // 2
            frame_center_y = self._frame_height // 2
            offset_x = center_x - frame_center_x
            offset_y = center_y - frame_center_y
            
            # 死区过滤
            if abs(offset_x) < self._dead_zone_x:
                offset_x = 0
            if abs(offset_y) < self._dead_zone_y:
                offset_y = 0
            
            # 转换为角度
            pan_angle = int(offset_x * self._pan_per_pixel)
            tilt_angle = int(offset_y * self._tilt_per_pixel)
            
            # 限位
            pan_angle = max(-self._max_pan_angle, min(self._max_pan_angle, pan_angle))
            tilt_angle = max(-self._max_tilt_angle, min(self._max_tilt_angle, tilt_angle))
            
            if pan_angle != 0 or tilt_angle != 0:
                if self._track_callback:
                    self._track_callback(pan_angle, tilt_angle)
                return (pan_angle, tilt_angle)
        else:
            self._face_detected = False
        
        return None
    
    def is_face_detected(self) -> bool:
        return self._face_detected
    
    def get_frames_processed(self) -> int:
        """公开只读访问已处理帧数（规则14）"""
        return self._frames_processed

    def get_stats(self) -> dict:
        return {
            "frames_processed": self._frames_processed,
            "faces_detected": self._faces_detected,
            "tracking_enabled": self._tracking_enabled,
            "face_detected": self._face_detected,
            "current_face": (self._current_face_x, self._current_face_y),
        }