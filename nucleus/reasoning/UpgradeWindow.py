# -*- coding: utf-8 -*-
"""
UpgradeWindow.py —— 升级窗口

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架升级的安全窗口管理
机制: 基于UpgradeWindow类实现，包含4个核心方法
定位: 进化治理层
"""

import time

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底


class UpgradeWindow(SilentLogMixin):
    """自动升级时间窗口控制器"""

    def __init__(self, start_hour: int = 3, end_hour: int = 4,
                 framework=None):
        self._start_hour = start_hour
        self._end_hour = end_hour
        self._framework = framework

    def is_in_window(self) -> bool:
        """当前是否在维护窗口内"""
        _hour = time.localtime().tm_hour
        if self._start_hour <= self._end_hour:
            return self._start_hour <= _hour < self._end_hour
        else:
            # 跨天窗口（如 23:00 - 01:00）
            return _hour >= self._start_hour or _hour < self._end_hour

    def is_user_present(self) -> bool:
        """检查用户是否在场——有人在场时禁止重启"""
        try:
            if self._framework and hasattr(self._framework, 'organs'):
                _visual = self._framework.organs.get("视觉皮层")
                if _visual and hasattr(_visual, 'is_face_detected'):
                    return _visual.is_face_detected()
                _subcon = self._framework.organs.get("潜意识")
                if _subcon and hasattr(_subcon, 'is_user_present'):
                    return _subcon.is_user_present()
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return False

    def should_trigger(self, pending_count: int) -> bool:
        """
        是否应该触发自动升级。
        条件：
        1. 有补丁待应用
        2. 在维护窗口内
        3. 用户不在场
        """
        return pending_count > 0 and self.is_in_window() and not self.is_user_present()