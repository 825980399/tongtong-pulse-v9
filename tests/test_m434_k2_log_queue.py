# -*- coding: utf-8 -*-
"""第434批刀2 单测：日志队列化（QueueHandler + QueueListener）。

守三件事：
  ① root logger 挂的是 QueueHandler（不是 SafeRotatingFileHandler 直挂）——
     业务线程只入队、不抢文件锁；
  ② 真正的文件 handler 存模块级 _FILE_HANDLER，QueueListener 后台落盘；
  ③ flush_queue_logs 能把队列里的日志落盘（停机不丢日志），且幂等。
"""
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import nucleus.logger as LG


def _reset_logger_state(monkeypatch, tmp_path):
    """重置 logger 模块状态 + 隔离日志文件（不污染生产 pulse.log）。"""
    _logfile = str(tmp_path / "test_pulse.log")
    monkeypatch.setenv("PULSE_LOG_FILE", _logfile)
    monkeypatch.setattr(LG, "_log_initialized", False)
    monkeypatch.setattr(LG, "_FILE_HANDLER", None)
    monkeypatch.setattr(LG, "_QUEUE_LISTENER", None)
    monkeypatch.setattr(LG, "_QUEUE_HANDLER", None)
    # 清空 root logger handlers，避免复用其他测试挂的 handler
    _root = logging.getLogger("pulse")
    _root.handlers = []
    return _logfile


def test_root_logger_uses_queue_handler(monkeypatch, tmp_path):
    _reset_logger_state(monkeypatch, tmp_path)
    LG._init_root_logger()
    _root = logging.getLogger("pulse")
    _qh = [h for h in _root.handlers if isinstance(h, logging.handlers.QueueHandler)]
    assert _qh, "root logger 没挂 QueueHandler"
    _sfh = [h for h in _root.handlers if isinstance(h, LG.SafeRotatingFileHandler)]
    assert not _sfh, "root logger 仍直挂 SafeRotatingFileHandler（应改为入队）"
    assert isinstance(LG._FILE_HANDLER, LG.SafeRotatingFileHandler), \
        "_FILE_HANDLER 不是 SafeRotatingFileHandler"
    assert LG._QUEUE_LISTENER is not None, "QueueListener 没启动"
    assert LG.flush_queue_logs() is True


def test_log_flush_writes_to_file(monkeypatch, tmp_path):
    _logfile = _reset_logger_state(monkeypatch, tmp_path)
    LG._init_root_logger()
    _root = logging.getLogger("pulse")
    _root.info("434刀2测试日志入队")
    assert LG.flush_queue_logs() is True, "flush 队列失败"
    with open(_logfile, encoding="utf-8") as _f:
        _txt = _f.read()
    assert "434刀2测试日志入队" in _txt, "日志没通过 QueueListener 落盘"


def test_flush_queue_logs_idempotent(monkeypatch, tmp_path):
    _reset_logger_state(monkeypatch, tmp_path)
    # 未初始化时 _QUEUE_LISTENER 是 None，flush 返回 True 且不抛异常
    assert LG.flush_queue_logs() is True
    assert LG.flush_queue_logs() is True
