# -*- coding: utf-8 -*-
"""450 刀6：449 内存告警回归修复（稳态窗覆盖度护栏 + 持续态重报间隔）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import nucleus.runtime_metrics as RM


def _mk(uptime=1800.0, base_offset=1080.0, rss0=4000.0, step=60.0, n=31):
    """造一个受控 RuntimeMetrics：`_history` 自己铺，不碰采样线程。"""
    _m = RM.RuntimeMetrics()
    _now = time.time()
    _m._created_at = _now - float(uptime)
    _m._m427_rss_at_10min = float(rss0)
    _m._m427_rss_at_10min_ts = _now - float(base_offset)
    _m._history.clear()
    for _i in range(n):
        _m._history.append({"ts": _now - float(uptime) + _i * (float(uptime) / max(1, n - 1)),
                            "mem_rss_mb": float(rss0) + _i * float(step)})
    return _m, _now


# ---------------------------------------------------------------- 覆盖度护栏
def test_稳态窗覆盖不足不判泄漏():
    """★复现 11-04 20:09~20:28 的假泄漏：基点只覆盖 18 分钟 < 30×0.9，瞬时却是 0。"""
    _m, _now = _mk(base_offset=1080.0)
    _v = _m._m449_growth_verdict(now=_now)
    assert _v["steady_cover_ok"] is False, _v
    assert _v["steady_window_sec_read"] == 1080.0, _v
    assert _v["mode"] == "", _v
    assert _v["alarm"] is False, _v
    assert "覆盖" in _v["why"], _v["why"]


def test_稳态窗覆盖充足照常判泄漏():
    _m, _now = _mk(base_offset=1800.0)
    _v = _m._m449_growth_verdict(now=_now)
    assert _v["steady_cover_ok"] is True, _v
    assert _v["steady_window_sec_read"] == 1800.0, _v
    assert _v["mode"] == "steady_leak", _v


def test_覆盖比例可配(monkeypatch):
    monkeypatch.setattr(config, "M450_MEM_STEADY_COVER_RATIO", 0.5, raising=False)
    _m, _now = _mk(base_offset=1080.0)      # 1080 / 1800 = 0.6 ≥ 0.5
    _v = _m._m449_growth_verdict(now=_now)
    assert _v["steady_cover_ok"] is True, _v


def test_没基线时也算覆盖不足():
    """★uptime 还没过 ramp ⇒ 根本没有"ramp 后第一个样本"，稳态读数留 None。"""
    _m, _now = _mk(uptime=300.0, base_offset=0.0, n=11)
    _m._m427_rss_at_10min = None
    _m._m427_rss_at_10min_ts = None
    _v = _m._m449_growth_verdict(now=_now)
    assert _v["steady_cover_ok"] is False, _v
    assert _v["steady_window_sec_read"] is None, _v


def test_开关关掉时不判():
    _m, _now = _mk()
    _c = config.ENABLE_M449_MEM_STEADY_ALARM
    config.ENABLE_M449_MEM_STEADY_ALARM = False
    try:
        _v = _m._m449_growth_verdict(now=_now)
        assert _v["mode"] == "" and _v["alarm"] is False, _v
    finally:
        config.ENABLE_M449_MEM_STEADY_ALARM = _c


# ---------------------------------------------------------------- 重报间隔
class _FakeLogger:
    def __init__(self):
        self.warn = []
        self.info = []

    def warning(self, msg):
        self.warn.append(str(msg))

    def info(self, msg):
        self.info.append(str(msg))


class _FakeTime:
    def __init__(self, t):
        self._t = float(t)

    def time(self):
        return self._t

    def set(self, t):
        self._t = float(t)


def test_状态机支持自定义重报间隔(monkeypatch):
    """★30s 节流对 40s 采样等于没有；600s 才压得住（实测 26 条/29 分钟）。"""
    _ft = _FakeTime(10000.0)
    monkeypatch.setattr(RM, "time", _ft)
    RM._M403_ALARM_STATE.clear()
    _lg = _FakeLogger()
    _m = RM.RuntimeMetrics()
    _m._m403_alarm_transition(_lg, "k", True, "A", "R", renotify_sec=600.0)
    assert len(_lg.warn) == 1, _lg.warn
    for _i in range(26):
        _ft.set(10040.0 + _i * 40.0)
        _m._m403_alarm_transition(_lg, "k", True, "A", "R", renotify_sec=600.0)
    # 26 拍跨 1040s ⇒ 只应再报 1 次（约 10 分钟一次），而不是 26 次
    assert len(_lg.warn) == 2, len(_lg.warn)


def test_缺省仍走30s节流(monkeypatch):
    """★既有调用方一字未改 ⇒ 不传 renotify 时必须保持 403 的 30s 语义。"""
    _ft = _FakeTime(20000.0)
    monkeypatch.setattr(RM, "time", _ft)
    RM._M403_ALARM_STATE.clear()
    _lg = _FakeLogger()
    _m = RM.RuntimeMetrics()
    _m._m403_alarm_transition(_lg, "k2", True, "A", "R")
    _ft.set(20031.0)
    _m._m403_alarm_transition(_lg, "k2", True, "A", "R")
    assert len(_lg.warn) == 2, _lg.warn


def test_内存告警把重报间隔传下去(monkeypatch):
    """接线护栏：内存告警必须显式传 M450_MEM_RENOTIFY_SEC（否则又回到 30s）。"""
    _m = RM.RuntimeMetrics()
    _got = {}

    def _spy(logger, key, triggered, alarm_msg, recover_msg, renotify_sec=None):
        _got["key"] = key
        _got["ren"] = renotify_sec

    monkeypatch.setattr(_m, "_m403_alarm_transition", _spy)
    monkeypatch.setattr(_m, "_m449_growth_verdict",
                        lambda now=None: {"mode": "steady_leak", "alarm": True,
                                          "instant": 0.0, "steady": 60.0,
                                          "step_delta": 0.0, "held_sec": 0.0,
                                          "why": "x"})
    monkeypatch.setattr(_m, "_m449_note", lambda v: None)
    _m.check_memory_alarm_detailed(threshold_percent=80.0, auto_gc=False)
    assert _got.get("ren") == float(config.M450_MEM_RENOTIFY_SEC), _got


def test_返回字典带覆盖率字段():
    _m, _now = _mk()
    _r = _m.check_memory_alarm_detailed(threshold_percent=99.9, auto_gc=False)
    assert "m449_steady_cover_ok" in _r, sorted(_r)
    assert "m449_steady_window_sec_read" in _r, sorted(_r)
