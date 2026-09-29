"""mediapipe_engine —— 高精度人脸检测 (适配 0.10.x 新版 API)

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日
"""
import cv2

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底


class MediaPipeEngine(SilentLogMixin):
    """MediaPipe人脸检测器 (适配 0.10.35+)"""

    def __init__(self):
        self._available = False
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_tasks
            from mediapipe.tasks.python import vision

            # 检查版本和API兼容性
            if hasattr(mp, 'tasks'):
                self._mp = mp
                self._mp_tasks = mp_tasks
                self._vision = vision
                self._available = True
        except ImportError:
            pass

    def is_available(self) -> bool:
        return self._available

    def detect(self, frame) -> tuple:
        """返回 (是否检测到人脸, 置信度 0.0-1.0)"""
        if not self._available:
            return False, 0.0
        try:
            # 配置检测选项
            base_options = self._mp_tasks.BaseOptions(model_asset_path=None)
            options = self._vision.FaceDetectorOptions(
                base_options=base_options,
                min_detection_confidence=0.5
            )
            detector = self._vision.FaceDetector.create_from_options(options)

            # 转换为 MediaPipe Image
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)

            result = detector.detect(mp_image)
            detector.close()

            if result.detections:
                best = max(result.detections, key=lambda d: d.score)
                return True, round(best.score, 2)
        except Exception as e:
            self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
        return False, 0.0