# -*- coding: utf-8 -*-
"""
aibot_logger.py —— AI机器人日志器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 统一日志格式与级别管理，支持器官级日志标签
机制: 基于AibotSilentLogger类实现，包含7个核心方法
定位: 日志基础设施层
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底


class AibotSilentLogger(SilentLogMixin):
    """AiBotSDK 日志适配器：DEBUG/INFO 静默，WARN/ERROR 转框架日志。"""

    def __init__(self, prefix: str = "AiBotSDK") -> None:
        self._prefix = prefix
        self._logger: logging.Logger | None = None
        self._lock = threading.Lock()

    def _get_logger(self) -> logging.Logger | None:
        """懒初始化框架日志器（避免 import 时创建循环依赖）。"""
        if self._logger is None:
            with self._lock:
                if self._logger is None:
                    try:
                        from nucleus.logger import get_module_logger
                        self._logger = get_module_logger(self._prefix)
                    except Exception:
                        self._logger = logging.getLogger(self._prefix)
        return self._logger

    # ========== aibot Logger Protocol ==========

    def debug(self, message: str, *args: Any) -> None:
        # 静默：压制心跳/ack 等高频 DEBUG 噪音
        pass

    def info(self, message: str, *args: Any) -> None:
        # 静默：INFO 级别（连接建立等）控制台已不打印，文件 DEBUG 已覆盖
        pass

    def warn(self, message: str, *args: Any) -> None:
        _lg = self._get_logger()
        if _lg is not None:
            try:
                _lg.warning("%s", message)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def error(self, message: str, *args: Any) -> None:
        _lg = self._get_logger()
        if _lg is not None:
            try:
                _lg.error("%s", message)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")


# 全局共享实例（两处 WSClient 注入同一实例，单一来源）
_shared_logger: AibotSilentLogger | None = None
_shared_lock = threading.Lock()


def get_aibot_logger() -> AibotSilentLogger:
    """获取全局共享的 AiBotSDK 压制日志器（单例）。"""
    global _shared_logger
    if _shared_logger is None:
        with _shared_lock:
            if _shared_logger is None:
                _shared_logger = AibotSilentLogger()
    return _shared_logger
