# -*- coding: utf-8 -*-
"""★382 刀9 · 线程数/句柄数每拍埋点（P2）

票面：进程级线程数/句柄数无埋点。要求挂 `PulseMetricsCollector` 拍点、
超阈值（线程 > 50 / 句柄 > 5000）打 WARNING、阈值写 config、**纯埋点不新建定时器**。

T0 校正：`PulseMetricsCollector` **没有 `tick` 方法**（票面写的是 `tick`）；
拍级入口是 `_collect_memory_pressure` → `_m339_memory_probe` 这条**已活的采样链**。
故本刀挂在这条链上（不新开调度器），并在返回值里带 `resource_probe`。
"""
import logging
import os
import sys
import threading


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from organs.core.PulseMetricsCollector import PulseMetricsCollector  # noqa: E402


class _Stub(PulseMetricsCollector):
    """只装探针要用的两件：`_log` 与（可选的）config 读取。不跑 __init__。"""

    def __init__(self):
        self.logs = []

    def _log(self, level, msg):
        self.logs.append((level, msg))


def test_m382_d9_probe_returns_fields():
    _o = _Stub()._m382_resource_probe()
    assert set(_o) >= {"threads", "handles", "warned", "why"}
    assert _o["threads"] >= 1, "线程数至少 1（当前线程）"
    assert _o["warned"] is False or _o["why"]


def test_m382_d9_thread_threshold_triggers(monkeypatch):
    """★核心：线程数超阈值 ⇒ warned=True 且 why 写明数字。"""
    monkeypatch.setattr(config, "PROC_THREADS_WARN", 0, raising=False)
    monkeypatch.setattr(config, "PROC_HANDLES_WARN", 10 ** 9, raising=False)
    _s = _Stub()
    _o = _s._m382_resource_probe()
    assert _o["warned"] is True, _o
    assert "线程" in _o["why"] and ">" in _o["why"]


def test_m382_d9_handle_threshold_triggers(monkeypatch):
    monkeypatch.setattr(config, "PROC_THREADS_WARN", 10 ** 6, raising=False)
    monkeypatch.setattr(config, "PROC_HANDLES_WARN", 1, raising=False)
    _o = _Stub()._m382_resource_probe()
    assert _o["warned"] is True
    assert "句柄" in _o["why"]


def test_m382_d9_no_warn_below_threshold(monkeypatch):
    monkeypatch.setattr(config, "PROC_THREADS_WARN", 10 ** 6, raising=False)
    monkeypatch.setattr(config, "PROC_HANDLES_WARN", 10 ** 9, raising=False)
    assert _Stub()._m382_resource_probe()["warned"] is False


def test_m382_d9_warn_rate_limited(monkeypatch, caplog):
    """超限会持续存在 ⇒ 每 10 拍最多报一次（不刷屏）。"""
    monkeypatch.setattr(config, "PROC_THREADS_WARN", 0, raising=False)
    _s = _Stub()
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            _s._m382_resource_probe()
    _hits = [r for r in caplog.records if "382刀9" in r.getMessage()]
    assert len(_hits) <= 1, "5 拍报了 %d 次（应 ≤1）" % len(_hits)


def test_m382_d9_probe_does_not_spawn_threads():
    """反控：探针本身不许新建线程（票面要求"纯埋点，不新建后台定时器"）。"""
    _s = _Stub()
    _before = threading.active_count()
    for _ in range(3):
        _s._m382_resource_probe()
    assert threading.active_count() <= _before
