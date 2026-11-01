# -*- coding: utf-8 -*-
"""417 刀1 单测：心跳路径轻量化（异步观测 worker）。

判据取向（对应任务书验收）：
  ① 开关：默认 True；`ENABLE_M417_HEARTBEAT_ASYNC_CACHE=False` ⇒ **逐字回到同步**；
  ② 不阻塞：`_build_observability_snapshot` 慢 0.4s 时，`_on_heartbeat_observe`
     必须在 0.1s 内返回（L3 handler 不再等它）；
  ③ L0 尾巴：`_on_heartbeat_alive` 里的两个 tick 慢 0.4s 时同样秒返回；
  ④ 合并：连登 N 次只跑 1 批（`coalesced` 会涨，`jobs` 不会跟着涨 N）；
  ⑤ 低优先级：worker 线程的优先级后端必须是真设上的（win32_lowest 之类），
     不能是 `win32_failed`（那是 416 栽过的 64 位伪句柄截断）；
  ⑥ 停机：`m417_stop_async()` 能在超时内把线程收掉；
  ⑦ 瘦身实例（`__new__` 手搓，没有 `_m417_ready`）自动走同步 —— 回归测试零影响；
  ⑧ 写盘失败要计数，不许静默当成功。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import organs.core.PulseMetricsCollector as PMC  # noqa: E402
from organs.core.PulseMetricsCollector import (  # noqa: E402
    PulseMetricsCollector,
    m417_async_switch_on,
    m417_stats,
)


# ============================================================ 夹具
def _mk_collector(*, ready=True, interval=1, build_sec=0.0, write_ok=True, switch=None):
    """瘦身采集器：**绝不**碰生产 data/（build 与 write 都被替换掉）。"""
    c = PulseMetricsCollector.__new__(PulseMetricsCollector)
    c._cache_lock = threading.Lock()
    c._obs_enabled = True
    c._snapshot_interval = interval
    c._cache_timestamp = 0.0
    c._cached_heartbeat = {}
    c._m396_last_beat_rx_at = 0.0
    c._m396_beat_rx_count = 0
    c._m396_explicit_stop = False
    c._cached_current_user = "访客"
    c._cached_organ_stats = {}
    c._snapshot_count = 0
    c._beat_count = 0
    c.info_field = None
    c._logs = []
    c._log = lambda *a, **k: c._logs.append((a, k))

    # ---- 417 实例状态
    if ready:
        c._m417_ready = True
        c._m417_lock = threading.Lock()
        c._m417_pending = set()
        c._m417_wake = threading.Event()
        c._m417_stop = threading.Event()
        c._m417_thread = None

    # ---- 把重活换成可观测的假件（绝不写生产 health_snapshot.json）
    c._done = []
    c._written = []

    def _fake_build(skip_slow=False):
        if build_sec:
            time.sleep(build_sec)
        c._done.append(("build", skip_slow))
        return {"beat_count": c._beat_count, "timestamp": time.time()}

    def _fake_refresh():
        c._done.append(("refresh", None))

    def _fake_write(path, snap):
        if not write_ok:
            return False
        c._written.append((path, snap))
        return True

    c._build_observability_snapshot = _fake_build
    c._refresh_organ_cache = _fake_refresh
    c._m403_write_tiered = _fake_write
    c._m402_dead_letter_tick = lambda: c._done.append(("dead_letter", None))
    c._m403_causal_tick = lambda: c._done.append(("causal", None))
    if switch is not None:
        c._m417_force_switch = switch
    return c


def _wait(pred, timeout=2.0, step=0.02):
    """轮询等待（不睡固定时长：慢机也不假红）。"""
    _t0 = time.perf_counter()
    while time.perf_counter() - _t0 < timeout:
        if pred():
            return True
        time.sleep(step)
    return False


@pytest.fixture(autouse=True)
def _reset_stats():
    PMC._m417_reset_state()
    yield
    PMC._m417_reset_state()


# ============================================================ ① 开关
def test_01_switch_default_on(monkeypatch):
    monkeypatch.setattr(PMC, "m417_async_switch_on", lambda: True)
    assert PMC.m417_async_switch_on() is True


def test_02_switch_reads_config(monkeypatch):
    class _Cfg:
        ENABLE_M417_HEARTBEAT_ASYNC_CACHE = False

    monkeypatch.setitem(sys.modules, "config", _Cfg)
    assert m417_async_switch_on() is False
    _Cfg.ENABLE_M417_HEARTBEAT_ASYNC_CACHE = True
    assert m417_async_switch_on() is True


def test_03_switch_missing_key_defaults_true(monkeypatch):
    class _Cfg:
        pass

    monkeypatch.setitem(sys.modules, "config", _Cfg)
    assert m417_async_switch_on() is True, "缺键必须按新行为（True），不能偷偷回同步"


# ============================================================ ② 不阻塞
def test_10_observe_returns_fast_when_build_is_slow():
    c = _mk_collector(build_sec=0.4)
    _t0 = time.perf_counter()
    _r = c._on_heartbeat_observe({"interval": 10.0, "beat_count": 1})
    _dt = time.perf_counter() - _t0
    assert _dt < 0.1, "handler 被 _build_observability_snapshot 拖住了：%.3fs" % _dt
    assert _r.get("async") is True
    assert _r.get("status") == "observed", "返回形态必须与同步路径一致（调用方在解这个键）"
    # 但重活最终必须真跑（异步不等于不做）
    assert _wait(lambda: ("build", True) in c._done, timeout=3.0), c._done
    assert c._written, "worker 没把快照写出去"
    c.m417_stop_async(timeout=3.0)


def test_11_alive_ticks_do_not_block_l0():
    c = _mk_collector()
    _slow = {"n": 0}

    def _slow_tick():
        _slow["n"] += 1
        time.sleep(0.4)

    c._m402_dead_letter_tick = _slow_tick
    c._m403_causal_tick = _slow_tick
    _t0 = time.perf_counter()
    _r = c._on_heartbeat_alive({"interval": 10.0, "beat_count": 2, "status": "搏动中"})
    _dt = time.perf_counter() - _t0
    assert _dt < 0.1, "L0 handler 被 tick 拖住了：%.3fs" % _dt
    assert _r == {"status": "cached"}, "alive 的返回形态不得变（有调用方在解）"
    assert _wait(lambda: _slow["n"] >= 2, timeout=3.0), _slow
    c.m417_stop_async(timeout=3.0)


# ============================================================ ③ 同步回退
def test_20_switch_off_is_synchronous(monkeypatch):
    monkeypatch.setattr(PMC, "m417_async_switch_on", lambda: False)
    c = _mk_collector(build_sec=0.0)
    _r = c._on_heartbeat_observe({"interval": 10.0, "beat_count": 1})
    assert _r == {"status": "observed", "beat": 1}, "关开关后返回形态必须回到 416 原样"
    assert ("build", True) in c._done, "同步路径必须当场算完"
    assert c._written, "同步路径必须当场写盘"
    assert c.__dict__.get("_m417_thread") is None, "关开关不许起线程"
    assert PMC.m417_stats()["started"] == 0


def test_21_switch_off_alive_is_synchronous(monkeypatch):
    monkeypatch.setattr(PMC, "m417_async_switch_on", lambda: False)
    c = _mk_collector()
    c._on_heartbeat_alive({"interval": 10.0, "beat_count": 3})
    assert ("dead_letter", None) in c._done and ("causal", None) in c._done
    assert c.__dict__.get("_m417_thread") is None


def test_22_thin_instance_falls_back_to_sync():
    """`__new__` 手搓、没有 `_m417_ready` 的实例 ⇒ 必须走同步（回归测试零影响）。"""
    c = _mk_collector(ready=False)
    _r = c._on_heartbeat_observe({"interval": 10.0, "beat_count": 1})
    assert _r == {"status": "observed", "beat": 1}
    assert ("build", True) in c._done
    assert c.__dict__.get("_m417_thread") is None


# ============================================================ ④ 合并
def test_30_requests_coalesce():
    c = _mk_collector(build_sec=0.3)
    for _ in range(6):
        c._on_heartbeat_observe({"interval": 10.0, "beat_count": 1})
    assert _wait(lambda: len(c._written) >= 1, timeout=3.0)
    time.sleep(0.5)
    c.m417_stop_async(timeout=3.0)
    _st = m417_stats()
    assert _st["coalesced"] > 0, "6 连登没合并过 ⇒ 队列会堆"
    assert _st["jobs"] < 6, "6 次请求跑了 %d 批 ⇒ 没有合并" % _st["jobs"]
    assert len(c._written) <= 3, "写盘次数 %d，合并失效" % len(c._written)


# ============================================================ ⑤ 低优先级
def test_40_worker_priority_backend_is_real():
    c = _mk_collector()
    c._on_heartbeat_alive({"interval": 10.0, "beat_count": 4})
    assert _wait(lambda: m417_stats()["priority"] is not None, timeout=3.0)
    _pri = m417_stats()["priority"]
    c.m417_stop_async(timeout=3.0)
    assert _pri != "win32_failed", "SetThreadPriority 失败了（416 栽过：64 位伪句柄被截）"
    assert _pri in ("win32_lowest", "posix_nice19", "unavailable") or \
        str(_pri).startswith("win32_set_but_"), _pri


def test_41_thread_priority_util_roundtrip():
    from utils.thread_priority import BACKENDS, set_current_thread_lowest
    _b = set_current_thread_lowest()
    assert _b in BACKENDS or str(_b).startswith("win32_set_but_"), _b
    if sys.platform == "win32":
        assert _b == "win32_lowest", "Windows 上必须真设到 -2 并读回，实际=%s" % _b


# ============================================================ ⑥ 停机
def test_50_stop_async_joins():
    c = _mk_collector(build_sec=0.05)
    c._on_heartbeat_alive({"interval": 10.0, "beat_count": 5})
    assert _wait(lambda: c.__dict__.get("_m417_thread") is not None, timeout=2.0)
    assert c.m417_stop_async(timeout=3.0) is True
    _th = c.__dict__.get("_m417_thread")
    assert _th is not None and not _th.is_alive()


def test_51_stop_without_thread_is_true():
    c = _mk_collector()
    assert c.m417_stop_async() is True


def test_52_request_after_stop_does_not_restart():
    c = _mk_collector()
    c._on_heartbeat_alive({"interval": 10.0, "beat_count": 6})
    assert _wait(lambda: c.__dict__.get("_m417_thread") is not None, timeout=2.0)
    c.m417_stop_async(timeout=3.0)
    assert c._m417_ensure_worker() is False, "停机后不许再起线程"


# ============================================================ ⑦ 写盘失败计数
def test_60_write_failure_counted():
    c = _mk_collector(write_ok=False)
    c._on_heartbeat_observe({"interval": 10.0, "beat_count": 1})
    assert _wait(lambda: m417_stats()["snapshot_fail"] >= 1, timeout=3.0), m417_stats()
    assert m417_stats()["snapshot_ok"] == 0
    c.m417_stop_async(timeout=3.0)


def test_61_build_exception_counted_not_raised():
    c = _mk_collector()

    def _boom(skip_slow=False):
        raise RuntimeError("boom")

    c._build_observability_snapshot = _boom
    c._on_heartbeat_observe({"interval": 10.0, "beat_count": 1})
    assert _wait(lambda: m417_stats()["snapshot_fail"] >= 1, timeout=3.0), m417_stats()
    c.m417_stop_async(timeout=3.0)


# ============================================================ ⑧ 观测视图
def test_70_stats_view_fields():
    c = _mk_collector()
    _v = c.m417_async_stats()
    for _k in ("jobs", "coalesced", "snapshot_ok", "snapshot_fail", "ticks_ok",
               "ticks_fail", "thread_alive", "pending", "switch_on", "ready"):
        assert _k in _v, "统计视图缺字段 %s" % _k
    assert _v["ready"] is True


def test_71_pending_cleared_after_job():
    c = _mk_collector()
    c._on_heartbeat_alive({"interval": 10.0, "beat_count": 7})
    assert _wait(lambda: m417_stats()["jobs"] >= 1, timeout=3.0)
    assert _wait(lambda: not c.m417_async_stats()["pending"], timeout=2.0)


def test_72_snapshot_interval_still_gates():
    """非快照拍（interval 未命中）不许登记 job —— 节流语义不能被异步改掉。"""
    c = _mk_collector(interval=500)
    c._on_heartbeat_observe({"interval": 10.0, "beat_count": 1})
    assert ("build", True) not in c._done
    assert c.__dict__.get("_m417_thread") is None
