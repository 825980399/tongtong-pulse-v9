# -*- coding: utf-8 -*-
"""442 批单测：O3 独立节拍 / degraded 告警出口 / InfluxDB 接线。"""
import inspect
import os
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

import config
import nucleus.evolution.patch_execution_dispatch as PED
import organs.core.PulseMetricsCollector as PMC


# ---------------------------------------------------------------- 刀1
def test_o3_gate_interval_default_300(monkeypatch):
    monkeypatch.delattr(config, "PATCH_O3_GATE_INTERVAL_SEC", raising=False)
    assert PED._o3_gate_interval_sec() == 300.0


def test_o3_gate_interval_reads_config(monkeypatch):
    monkeypatch.setattr(config, "PATCH_O3_GATE_INTERVAL_SEC", 120, raising=True)
    assert PED._o3_gate_interval_sec() == 120.0


def test_o3_gate_interval_floor_60(monkeypatch):
    """下限 60s：防把 60s 轮询打成每拍重入。"""
    monkeypatch.setattr(config, "PATCH_O3_GATE_INTERVAL_SEC", 5, raising=True)
    assert PED._o3_gate_interval_sec() == 60.0


def test_o3_gate_runs_before_armed_early_return():
    """★核心：O3 独立节拍必须写在 `if _last_dispatch_ts is None` **之前**。

    否则派单间隔 1h + 频繁重启 ⇒ 永远停在 armed，O3 判定一次都跑不到
    （441 实测 5.6 万行日志 0 条 `[340判死]`）。
    """
    _src = inspect.getsource(PED.dispatch_on_tick)
    _gate = _src.find("_m335_o3_consume(now=_now)")
    _armed = _src.find("if _last_dispatch_ts is None:")
    assert _gate != -1, "dispatch_on_tick 里没有 O3 独立节拍调用"
    assert _armed != -1
    assert _gate < _armed, "O3 判定排在了 armed 早退之后 ⇒ 重启即饿死"


def test_o3_gate_timestamp_exists():
    assert hasattr(PED, "_last_o3_gate_ts")


# ---------------------------------------------------------------- 刀2
def _mk_root(rows, pending):
    import json
    _td = tempfile.mkdtemp()
    _dir = os.path.join(_td, "data", "patches")
    os.makedirs(_dir, exist_ok=True)
    for _n, _d in (("patch_history.json", rows), ("pending_patches.json", pending)):
        with open(os.path.join(_dir, _n), "w", encoding="utf-8") as _f:
            json.dump(_d, _f, ensure_ascii=False)
    return _td


def test_degraded_raises_warning(monkeypatch):
    """apply=0 且 queue>0 ⇒ 必须触发告警分支（时间戳被置位）。"""
    monkeypatch.setattr(PMC, "_M442_DEGRADED_WARN_AT", 0.0, raising=True)
    _td = _mk_root([{"id": "h", "generated_at": time.time(), "applied_at": None}],
                   [{"id": "p1"}])
    _d = PMC._m421_patch_chain_read(_td)
    assert _d.get("state") == "degraded", _d
    assert PMC._M442_DEGRADED_WARN_AT > 0, "降级了但告警没触发"


def test_not_degraded_no_warning(monkeypatch):
    """队列空 ⇒ 不降级、也不该告警。"""
    monkeypatch.setattr(PMC, "_M442_DEGRADED_WARN_AT", 0.0, raising=True)
    _td = _mk_root([{"id": "h", "generated_at": time.time(), "applied_at": None}], [])
    _d = PMC._m421_patch_chain_read(_td)
    assert _d.get("state") != "degraded", _d
    assert PMC._M442_DEGRADED_WARN_AT == 0.0


def test_warning_is_throttled(monkeypatch):
    """10 分钟内重复调用不再刷屏（时间戳不被更新）。"""
    _td = _mk_root([{"id": "h", "generated_at": time.time(), "applied_at": None}],
                   [{"id": "p1"}])
    monkeypatch.setattr(PMC, "_M442_DEGRADED_WARN_AT", 0.0, raising=True)
    PMC._m421_patch_chain_read(_td)
    _first = PMC._M442_DEGRADED_WARN_AT
    PMC._m421_patch_chain_read(_td)
    assert PMC._M442_DEGRADED_WARN_AT == _first, "节流失效，每拍都会刷一条 WARNING"


# ---------------------------------------------------------------- 刀3
def test_influx_autoconnect_switch_on():
    """442 刀3 的核心改动：AUTO_CONNECT 必须为 True。"""
    assert getattr(config, "ENABLE_INFLUXDB_AUTO_CONNECT", False) is True


def test_influx_store_connects():
    """真连一次；环境无 InfluxDB 则跳过（不把外部依赖做成必红）。"""
    from nucleus.timeseries_store.influxdb_store import get_influxdb_store
    _st = get_influxdb_store()
    if not _st.connect():
        pytest.skip("InfluxDB 不可达（环境未起服务）")
    assert _st.is_available() is True
    # 只验「入缓冲成功」； 在 pytest 进程里会撞客户端线程池已关的竞态
    # （"cannot schedule new futures after interpreter shutdown"），那是测试进程的
    # 生命周期问题，不是产品问题 —— 真实落库证据见交付报告 §3（独立进程 flush=True + 回查 3 条）。
    assert _st.write_point("m442_selftest", tags={"src": "test"}, fields={"v": 1.0}) is True
