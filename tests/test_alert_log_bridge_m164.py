# -*- coding: utf-8 -*-
"""刀A5 门控单测：日志→告警落盘桥（去重 / 限流 / 分级门槛 / 挂载纪律）。

测真实源码逻辑（AlertBridgePolicy / AlertJsonlHandler / install），不依赖
生产文件系统：通过注入 ``sink`` 捕获落盘内容，避免触碰 write_guard / data/。
"""
import logging
import time

from nucleus.reporting.alert_log_bridge import (
    ALERT_LOG_BRIDGE_ENABLED,
    AlertBridgePolicy,
    AlertJsonlHandler,
    install_alert_log_bridge,
    reset_install_state,
)


class _Sink:
    def __init__(self):
        self.records = []

    def __call__(self, doc):
        self.records.append(doc)


def _rec(level, msg, name="pulse.module.x", lineno=1):
    return logging.LogRecord(name, level, __file__, lineno, msg, None, None)


def test_policy_severity_gate():
    p = AlertBridgePolicy(min_levelno=logging.WARNING)
    assert p.should_emit("x", logging.DEBUG, "m") is False
    assert p.should_emit("x", logging.INFO, "m") is False
    assert p.should_emit("x", logging.WARNING, "m") is True
    assert p.should_emit("x", logging.ERROR, "m") is True


def test_policy_dedup_and_rate_cap():
    # 窗口内未超上限：多次允许
    p = AlertBridgePolicy(dedup_window=600, max_per_key=3,
                          min_levelno=logging.WARNING)
    assert p.should_emit("n", logging.ERROR, "same") is True
    assert p.should_emit("n", logging.ERROR, "same") is True
    assert p.should_emit("n", logging.ERROR, "same") is True
    assert p.should_emit("n", logging.ERROR, "same") is False  # 触达上限→抑制

    # 不同消息互不干扰（独立键）
    assert p.should_emit("n", logging.ERROR, "other") is True


def test_policy_window_expiry():
    p = AlertBridgePolicy(dedup_window=0.05, max_per_key=1,
                          min_levelno=logging.WARNING)
    assert p.should_emit("n", logging.ERROR, "w") is True
    assert p.should_emit("n", logging.ERROR, "w") is False  # 同窗口且达上限
    time.sleep(0.08)
    assert p.should_emit("n", logging.ERROR, "w") is True  # 窗口过期→重置


def test_handler_below_threshold_not_sunk():
    sink = _Sink()
    h = AlertJsonlHandler(
        policy=AlertBridgePolicy(min_levelno=logging.WARNING), sink=sink)
    h.emit(_rec(logging.INFO, "info-only"))
    assert sink.records == []


def test_handler_above_threshold_sunk_with_fields():
    sink = _Sink()
    h = AlertJsonlHandler(
        policy=AlertBridgePolicy(min_levelno=logging.WARNING), sink=sink)
    h.emit(_rec(logging.ERROR, "boom %d", lineno=42))
    assert len(sink.records) == 1
    d = sink.records[0]
    assert d["level"] == "ERROR"
    assert d["logger"] == "pulse.module.x"
    assert d["lineno"] == 42
    assert d["source"] == "log_bridge"
    assert "boom" in d["message"]


def test_handler_dedup_suppresses_repeats():
    sink = _Sink()
    h = AlertJsonlHandler(
        policy=AlertBridgePolicy(dedup_window=600, max_per_key=1,
                                 min_levelno=logging.WARNING),
        sink=sink)
    for _ in range(5):
        h.emit(_rec(logging.ERROR, "repeat"))
    assert len(sink.records) == 1  # 去重→只入档一条（无重复膨胀）


def test_handler_disabled_no_sink():
    sink = _Sink()
    h = AlertJsonlHandler(enabled=False, sink=sink)
    h.emit(_rec(logging.ERROR, "x"))
    assert sink.records == []


def test_install_skips_in_test_env():
    # 当前 pytest 环境 is_test_like_env 为真 → 不挂载、返回 False
    reset_install_state()
    lg = logging.getLogger("pulse")
    before = len([h for h in lg.handlers
                  if getattr(h, "_alert_log_bridge", False)])
    res = install_alert_log_bridge(lg)
    after = len([h for h in lg.handlers
                 if getattr(h, "_alert_log_bridge", False)])
    assert res is False
    assert after == before


def test_install_attaches_when_enabled_and_not_test(monkeypatch):
    import nucleus.data.write_guard as _wg
    reset_install_state()
    monkeypatch.setattr(_wg, "is_test_like_env", lambda: False)
    lg = logging.getLogger("pulse.test_bridge_install")
    for h in list(lg.handlers):
        if getattr(h, "_alert_log_bridge", False):
            lg.removeHandler(h)
    try:
        res = install_alert_log_bridge(lg)
        assert res is True
        assert any(getattr(h, "_alert_log_bridge", False)
                   for h in lg.handlers)
    finally:
        reset_install_state()
        for h in list(lg.handlers):
            if getattr(h, "_alert_log_bridge", False):
                lg.removeHandler(h)


def test_default_switch_is_enabled():
    # 验收口径：默认开关使修复生效（ERROR/WARNING 可入档）
    assert ALERT_LOG_BRIDGE_ENABLED is True
