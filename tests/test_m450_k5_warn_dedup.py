# -*- coding: utf-8 -*-
"""450 刀5：指标采集器 / 问题池 告警降噪（内容不变不重报，数字一变立刻报）。"""
import json
import logging
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import nucleus._warn_throttle as WT
import nucleus.log_state as LS
from nucleus import alert_dedup as AD


@pytest.fixture(autouse=True)
def _reset():
    AD.reset_all()
    LS.reset(None)
    WT._LAST.clear()
    yield
    AD.reset_all()
    LS.reset(None)
    WT._LAST.clear()


# ---------------------------------------------------------------- 语义
def test_同内容不重报_内容一变立刻报():
    _got = []
    for _c in ("A", "A", "A", "B", "B"):
        _e, _s = AD.should_warn_content("k", _c, renotify_sec=3600.0)
        _got.append((_c, _e))
    assert _got[0] == ("A", True), _got
    assert _got[1][1] is False and _got[2][1] is False, _got
    assert _got[3] == ("B", True), "内容变了必须立刻报"
    assert _got[4][1] is False, _got


def test_窗口到了即使内容没变也重报():
    assert AD.should_warn_content("k2", "X", renotify_sec=0.0)[0] is True
    assert AD.should_warn_content("k2", "X", renotify_sec=0.0)[0] is True


def test_被压掉的条数可读():
    AD.should_warn_content("k3", "X", renotify_sec=3600.0)
    AD.should_warn_content("k3", "X", renotify_sec=3600.0)
    _e, _sup = AD.should_warn_content("k3", "X", renotify_sec=3600.0)
    assert _e is False and _sup >= 1, (_e, _sup)


def test_总开关关掉一律放行(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M450_ALERT_DEDUP", False, raising=False)
    _got = [AD.should_warn_content("k4", "X", renotify_sec=3600.0)[0] for _ in range(3)]
    assert _got == [True, True, True], _got


# ---------------------------------------------------------------- 指标采集器接线
def _iso_root(tmp_path):
    root = str(tmp_path)
    ts = time.time() - 60
    for _rel, _obj in (("data/patches/patch_history.json", []),
                       ("data/patches/pending_patches.json",
                        [{"id": "p1", "generated_at": ts, "status": "pending"}]),
                       ("data/evolution/verify_feedback_ledger.json", {"applied": {}})):
        _p = os.path.join(root, _rel)
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(_obj, f, ensure_ascii=False)
    return root


def test_降级告警同内容只报一次(tmp_path, monkeypatch):
    """★442 的 600s 时间窗在"10 分钟采样"现场等于没节流；换成内容去重。"""
    PC = pytest.importorskip("organs.core.PulseMetricsCollector")
    monkeypatch.setattr(config, "M450_METRIC_WARN_RENOTIFY_SEC", 3600.0, raising=False)
    monkeypatch.setattr(PC, "_m442_degraded_warn_at_ignored", 0.0, raising=False)
    root = _iso_root(tmp_path)

    _calls = []
    _lg = logging.getLogger("pulse.module.PulseMetricsCollector")
    _orig = _lg.warning
    monkeypatch.setattr(_lg, "warning",
                        lambda msg, *a, **kw: (_calls.append(msg % a if a else msg),
                                               _orig(msg, *a, **kw))[0])
    for _ in range(3):
        PC._m421_patch_chain_read(root=root)
    assert len(_calls) == 1, _calls
    assert "作废=" in _calls[0], _calls
    assert "瓶颈=" in _calls[0], _calls


def test_降级告警内容变化会再报(tmp_path, monkeypatch):
    PC = pytest.importorskip("organs.core.PulseMetricsCollector")
    monkeypatch.setattr(config, "M450_METRIC_WARN_RENOTIFY_SEC", 3600.0, raising=False)
    root = _iso_root(tmp_path)
    _calls = []
    _lg = logging.getLogger("pulse.module.PulseMetricsCollector")
    _orig = _lg.warning
    monkeypatch.setattr(_lg, "warning",
                        lambda msg, *a, **kw: (_calls.append(msg % a if a else msg),
                                               _orig(msg, *a, **kw))[0])
    PC._m421_patch_chain_read(root=root)
    # 队列里再多一件 ⇒ 状态串变了 ⇒ 必须再报
    _p = os.path.join(root, "data", "patches", "pending_patches.json")
    with open(_p, "w", encoding="utf-8", newline="\n") as f:
        json.dump([{"id": "p1", "generated_at": time.time() - 60, "status": "pending"},
                   {"id": "p2", "generated_at": time.time() - 60, "status": "pending"}], f)
    PC._m421_patch_chain_read(root=root)
    assert len(_calls) == 2, _calls


def test_指标采集器接线未退化():
    _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "organs", "core", "PulseMetricsCollector.py")
    with open(_p, encoding="utf-8", errors="replace") as _f:
        _src = _f.read()
    assert 'should_warn_content(\n                        "metrics.patch_chain_degraded"' in _src


# ---------------------------------------------------------------- 问题池接线
def test_问题池接线未退化():
    _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "nucleus", "audit_problem_pool.py")
    with open(_p, encoding="utf-8", errors="replace") as _f:
        _src = _f.read()
    assert '"problem_pool.low_cap"' in _src, "低危限额必须走去重口"
    assert "M450_POOL_WARN_RENOTIFY_SEC" in _src, "重报间隔必须可配"


def test_问题池内容签名只随数字变():
    """签名 = cap|超出数|保留数：数字不变则压掉，一变就报。"""
    _keys = []
    for _sig in ("600|395|600", "600|395|600", "600|396|600"):
        _e, _ = AD.should_warn_content("problem_pool.low_cap", _sig, renotify_sec=21600.0)
        _keys.append(_e)
    assert _keys == [True, False, True], _keys
