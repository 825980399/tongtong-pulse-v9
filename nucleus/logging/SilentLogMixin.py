# -*- coding: utf-8 -*-
"""
SilentLogMixin.py —— 静默日志混入

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 支持静默模式的日志混入类
机制: 基于SilentLogMixin类实现，包含2个核心方法
定位: 日志基础设施层
"""

from __future__ import annotations

import logging


__all__ = ["SilentLogMixin", "coerce_log_level"]

_DEFAULT_LEVEL = logging.INFO

# 覆盖项目内出现过的全部等级写法；key 一律大写
_LEVEL_MAP = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "EXCEPTION": logging.ERROR,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "TRACE": logging.DEBUG,
}


def coerce_log_level(level) -> int:
    """把任意形态的日志等级转成 `logging` 的整数等级。

    无法识别时一律退回 INFO —— 宁可等级不准，也不能因为等级解析失败
    而再次抛异常（那正是本次要修复的病根）。
    """
    if isinstance(level, bool):          # bool 是 int 子类，先排除
        return _DEFAULT_LEVEL
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        return _LEVEL_MAP.get(level.strip().upper(), _DEFAULT_LEVEL)
    # LogLevel 之类的枚举：优先 .name（字符串），其次 .value（整数）
    _name = getattr(level, "name", None)
    if isinstance(_name, str):
        return _LEVEL_MAP.get(_name.strip().upper(), _DEFAULT_LEVEL)
    _value = getattr(level, "value", None)
    if isinstance(_value, int) and not isinstance(_value, bool):
        return _value
    return _DEFAULT_LEVEL


class SilentLogMixin:
    """为缺少 `_log` 方法的类提供安全的日志兜底。

    用法（基类列表末尾，让真 `_log` 优先）：
        class InfoField(SilentLogMixin):
            ...
    """

    def _log(self, level, message=None, **kwargs) -> None:
        """安全日志兜底 —— 不抛异常，兼容单参/双参/带关键字三种调用形态。"""
        try:
            # 单参形态 self._log("消息")：把唯一参数当作消息，等级按 INFO 处理
            if message is None:
                level, message = "INFO", level
            _text = str(message)
            # 关键字参数（如 error_code）拼进消息尾部，保证信息不丢
            if kwargs:
                _extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
                _text = f"{_text} [{_extra}]"
            _logger = logging.getLogger(
                f"{type(self).__module__}.{type(self).__name__}")
            _logger.log(coerce_log_level(level), _text)
        except Exception:
            # 日志本身失败绝不能影响主流程 —— 静默到底
            pass
