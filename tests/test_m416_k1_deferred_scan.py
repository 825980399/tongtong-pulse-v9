# -*- coding: utf-8 -*-
"""第416批刀1 单测：self_inspector 启动期全库扫方法 → 独立低优先级延迟线程。

覆盖任务书验收三条：
  * 延迟启动（不会立刻扫、主线程不被阻塞）
  * 低优先级（线程体内确实把优先级降到最低档）
  * 让步（每 10 个方法体让 1ms；上下文外零开销）
另含：开关关逐字回退、单飞、启动窗口、停机打断。
"""
import sys
import time

import pytest

import nucleus.self_inspector as SI


@pytest.fixture()
def stub_inspector():
    """造一个只跑桩、不真扫全库的 SelfInspector。"""
    si = SI.SelfInspector()
    si._m416_scan_thread = None
    si._m416_scan_started = False
    si._m416_scan_runs = 0
    si._m416_scan_last = {}
    si._m416_priority_backend = ""
    si._scan_all_organs = lambda: {"PulseA": {}, "PulseB": {}}
    si.detect_code_issues = lambda **kw: [{"id": "i1"}, {"id": "i2"}]
    return si


# --------------------------------------------------------------- 开关
def test_switch_off_returns_switch_off(stub_inspector, monkeypatch):
    monkeypatch.setattr(SI, "m416_scan_switch_on", lambda: False)
    out = stub_inspector.submit_deferred_scan(reason="t", delay_sec=0.01)
    assert out["started"] is False
    assert out["why"].startswith("switch_off")
    assert stub_inspector.m416_scan_state()["running"] is False


def test_switch_default_true():
    """默认值必须是 True（任务书：ENABLE_SELF_INSPECTOR_STARTUP_SCAN = True）。"""
    import config
    assert getattr(config, "ENABLE_SELF_INSPECTOR_STARTUP_SCAN", None) is True


# --------------------------------------------------------------- 延迟 + 不阻塞
def test_deferred_does_not_block_caller(stub_inspector):
    t0 = time.time()
    out = stub_inspector.submit_deferred_scan(reason="t", delay_sec=0.5)
    elapsed = time.time() - t0
    assert out["started"] is True
    assert elapsed < 0.2, "调用方被阻塞了 %.3fs（应 < 0.2s）"
    # 还没到点，扫描不该发生
    assert stub_inspector.m416_scan_state()["runs"] == 0
    assert stub_inspector._m416_scan_thread.is_alive() is True


def test_deferred_runs_after_delay(stub_inspector):
    out = stub_inspector.submit_deferred_scan(reason="t", delay_sec=0.05)
    assert out["started"] is True
    _th = stub_inspector._m416_scan_thread
    _th.join(5.0)
    assert not _th.is_alive()
    st = stub_inspector.m416_scan_state()
    assert st["runs"] == 1
    assert st["last"]["organs"] == 2
    assert st["last"]["issues"] == 2
    assert st["last"]["reason"] == "t"
    # 延迟确实等过（不小于设定值）
    assert st["last"]["at"] > 0
    assert st["wait_sec"] >= 0.04


# --------------------------------------------------------------- 低优先级
def test_priority_backend_is_lowest(stub_inspector):
    out = stub_inspector.submit_deferred_scan(reason="t", delay_sec=0.01)
    assert out["started"] is True
    stub_inspector._m416_scan_thread.join(5.0)
    backend = stub_inspector.m416_scan_state()["priority_backend"]
    assert backend, "线程体没记录优先级后端"
    assert backend != "win32_failed"
    if sys.platform == "win32":
        assert backend == "win32_lowest"


def test_set_thread_lowest_returns_named_backend():
    backend = SI._m416_set_thread_lowest()
    assert backend in ("win32_lowest", "win32_failed", "posix_nice19",
                       "unavailable")


# --------------------------------------------------------------- 让步
def test_coop_tick_noop_outside_thread(monkeypatch):
    _sleeps = []
    monkeypatch.setattr(SI.time, "sleep", lambda s: _sleeps.append(s))
    SI._m416_set_coop(False)
    before = SI.m416_coop_stats()["ticks"]
    for _ in range(50):
        SI._m416_coop_tick()
    assert SI.m416_coop_stats()["ticks"] == before, "上下文外不该计数"
    assert _sleeps == [], "上下文外不该让出"


def test_coop_tick_yields_every_10(monkeypatch):
    _sleeps = []
    monkeypatch.setattr(SI.time, "sleep", lambda s: _sleeps.append(s))
    SI._m416_set_coop(True)
    try:
        for _ in range(35):
            SI._m416_coop_tick()
    finally:
        SI._m416_set_coop(False)
    assert len(_sleeps) == 3, "35 次应让出 3 次（10/20/30），实际 %d" % len(_sleeps)
    assert all(abs(s - 0.001) < 1e-9 for s in _sleeps)


def test_coop_reset_after_run(stub_inspector):
    stub_inspector.submit_deferred_scan(reason="t", delay_sec=0.01)
    stub_inspector._m416_scan_thread.join(5.0)
    assert SI.m416_coop_stats()["on"] is False, "线程结束后让步上下文必须复位"
    assert SI.m416_coop_stats()["ticks"] > 0, "线程内应至少计过数"


# --------------------------------------------------------------- 单飞
def test_single_flight(stub_inspector):
    out1 = stub_inspector.submit_deferred_scan(reason="a", delay_sec=0.4)
    out2 = stub_inspector.submit_deferred_scan(reason="b", delay_sec=0.01)
    assert out1["started"] is True
    assert out2["started"] is False
    assert out2["why"] == "already_running"
    stub_inspector.stop_deferred_scan(timeout=3.0)


# --------------------------------------------------------------- 启动窗口
def test_outside_startup_window(stub_inspector):
    stub_inspector._boot_ts = time.time() - 10_000.0
    out = stub_inspector.submit_deferred_scan(reason="t", delay_sec=0.01)
    assert out["started"] is False
    assert out["why"] == "outside_startup_window"


def test_force_bypasses_window(stub_inspector):
    stub_inspector._boot_ts = time.time() - 10_000.0
    out = stub_inspector.submit_deferred_scan(reason="t", delay_sec=0.01,
                                             force=True)
    assert out["started"] is True
    stub_inspector._m416_scan_thread.join(5.0)


def test_in_startup_window_inside(stub_inspector):
    stub_inspector._boot_ts = time.time()
    assert stub_inspector.in_startup_window(180.0) is True
    assert stub_inspector.in_startup_window(0.0) in (True, False)


# --------------------------------------------------------------- 停机打断
def test_stop_interrupts_long_delay(stub_inspector):
    stub_inspector.submit_deferred_scan(reason="t", delay_sec=30.0)
    t0 = time.time()
    ok = stub_inspector.stop_deferred_scan(timeout=3.0)
    assert ok is True
    assert time.time() - t0 < 3.0, "停机没有在 3s 内打断 30s 延迟"
    assert stub_inspector.m416_scan_state()["running"] is False


def test_stop_without_thread_is_true(stub_inspector):
    assert stub_inspector.stop_deferred_scan() is True


# --------------------------------------------------------------- 状态面
def test_state_shape(stub_inspector):
    st = stub_inspector.m416_scan_state()
    for k in ("switch", "started", "running", "thread_name", "priority_backend",
              "delay_sec", "wait_sec", "runs", "last", "coop"):
        assert k in st, "缺字段 %s" % k
    assert isinstance(st["coop"], dict)
    assert st["coop"]["yield_every"] == 10
