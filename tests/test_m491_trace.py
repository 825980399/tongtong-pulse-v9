# -*- coding: utf-8 -*-
"""491-5 Trace 补全单测（开关 on/off）。"""
import json
import os

import pytest

import config
from nucleus import dialogue_process_trace as tr


@pytest.fixture
def trace_root(tmp_path, monkeypatch):
    _d = tmp_path / "trace_root"
    _d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("M426_TRACE_ROOT", str(_d))
    monkeypatch.setattr(config, "ENABLE_M426_DIALOGUE_TRACE", True, raising=False)
    tr._pending.clear()
    yield _d
    tr._pending.clear()


def _lines():
    _p = tr.out_path()
    if not os.path.exists(_p):
        return []
    with open(_p, encoding="utf-8") as _f:
        return [json.loads(x) for x in _f if x.strip()]


def test_4915_derive_step_count_on(trace_root, monkeypatch):
    """on：step_results 非空 ⇒ step_count = len，且不写 reason。"""
    monkeypatch.setattr(config, "ENABLE_M491_TRACE_STEP_COUNT", True, raising=False)
    tr.begin("c1", "q")
    tr.finish("c1", step_results=[{"s": 1}, {"s": 2}, {"s": 3}])
    _r = _lines()[-1]
    assert _r["step_count"] == 3
    assert _r.get("reason") is None


def test_4915_empty_keeps_none_with_reason(trace_root, monkeypatch):
    """on：两者都空 ⇒ 保持 None + reason，绝不填假 0。"""
    monkeypatch.setattr(config, "ENABLE_M491_TRACE_STEP_COUNT", True, raising=False)
    tr.begin("c2", "q")
    tr.finish("c2")
    _r = _lines()[-1]
    assert _r["step_count"] is None
    assert _r.get("reason") == "no_multi_step"


def test_4915_explicit_step_count_not_overridden(trace_root, monkeypatch):
    """显式传入的 step_count 优先，不被推导覆盖。"""
    monkeypatch.setattr(config, "ENABLE_M491_TRACE_STEP_COUNT", True, raising=False)
    tr.begin("c3", "q")
    tr.finish("c3", step_count=7, step_results=[{"s": 1}])
    assert _lines()[-1]["step_count"] == 7


def test_4915_stats_two_fields(trace_root, monkeypatch):
    """stats 返回 step_count_none_ratio 与 step_count_nonzero。"""
    monkeypatch.setattr(config, "ENABLE_M491_TRACE_STEP_COUNT", True, raising=False)
    tr.begin("a", "q")
    tr.finish("a", step_results=[{"s": 1}])
    tr.begin("b", "q")
    tr.finish("b")
    _s = tr.stats()
    assert _s["step_count_nonzero"] == 1
    assert _s["step_count_none_ratio"] == 0.5


def test_4915_off_verbatim(trace_root, monkeypatch):
    """off：不推导、不写 reason、stats 不含新字段（逐字回改前）。"""
    monkeypatch.setattr(config, "ENABLE_M491_TRACE_STEP_COUNT", False, raising=False)
    tr.begin("c4", "q")
    tr.finish("c4", step_results=[{"s": 1}, {"s": 2}])
    _r = _lines()[-1]
    assert _r["step_count"] is None
    assert _r.get("reason") is None
    _s = tr.stats()
    assert "step_count_none_ratio" not in _s
    assert "step_count_nonzero" not in _s
