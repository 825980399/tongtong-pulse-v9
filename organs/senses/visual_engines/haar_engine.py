"""haar_engine —— 永远可用的最低存活方案

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日
"""
import os

import cv2

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底


class HaarEngine(SilentLogMixin):
    """OpenCV Haar级联分类器"""
    
    def __init__(self):
        self._cascade = None
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        if os.path.exists(cascade_path):
            self._cascade = cv2.CascadeClassifier(cascade_path)
    
    def is_available(self) -> bool:
        return self._cascade is not None
    
    def detect(self, frame) -> tuple:
        """返回 (是否检测到人脸, 置信度 0.0-1.0)"""
        if self._cascade is None:
            return False, 0.0
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)
            faces = self._cascade.detectMultiScale(gray, 1.1, 4, minSize=(40, 40))
            if len(faces) > 0:
                return True, 0.85
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return False, 0.0