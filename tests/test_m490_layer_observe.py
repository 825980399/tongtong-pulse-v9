# -*- coding: utf-8 -*-
"""490-3 阶段A 默认层迁移候选单测：只观测，绝不切默认值。"""
import inspect

import pytest

import config
import nucleus.m490_diag as diag
from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import ChatEvent

_MODE_KEY = "M490_LAYER_DEFAULT_MODE"


@pytest.fixture
def cfg(monkeypatch):
    _saved = getattr(config, _MODE_KEY, None)
    yield config
    if _saved is None:
        if hasattr(config, _MODE_KEY):
            delattr(config, _MODE_KEY)
    else:
        setattr(config, _MODE_KEY, _saved)


@pytest.fixture
def captured(monkeypatch):
    rows = []

    def _cap(prefix, row):
        rows.append((prefix, dict(row)))
        return row

    monkeypatch.setattr(diag, "append_jsonl", _cap)
    monkeypatch.setattr(diag, "_layer_counts", {})
    monkeypatch.setattr(diag, "_layer_last_flush", 0.0)
    yield rows


def test_4903_default_layer_unchanged():
    """阶段A 验收：_emit/_send 的默认 layer 逐字仍是 L1，绝不切换。"""
    assert inspect.signature(BasePulseOrgan._emit).parameters["layer"].default == "L1"
    assert inspect.signature(BasePulseOrgan._send).parameters["layer"].default == "L1"


def test_4903_log_mode_records_only_mismatch(cfg, captured):
    cfg.M490_LAYER_DEFAULT_MODE = "log"
    # 注册表推 L1 与现层一致 ⇒ 不记
    assert diag.observe_layer("org", ChatEvent.MESSAGE, "L1") is None
    assert captured == []
    # 未登记事件推 L3 ⇒ 记候选（organ/event/现层/推层/次数）
    row = diag.observe_layer("org", "totally.unknown.event", "L1")
    assert row is not None
    assert row["current_layer"] == "L1"
    assert row["inferred_layer"] == "L3"
    assert row["count"] >= 1


def test_4903_off_mode_no_observation(cfg, captured):
    cfg.M490_LAYER_DEFAULT_MODE = "off"
    assert diag.observe_layer("org", "totally.unknown.event", "L1") is None
    assert captured == []
    assert diag._layer_counts == {}


def test_4903_infer_matches_registry(cfg):
    """推断口径与 PulseCore._infer_layer 一致（注册表开启时）。"""
    from nucleus.event_layers import UNKNOWN_LAYER
    assert diag.infer_layer_quiet(ChatEvent.MESSAGE) == "L1"
    assert diag.infer_layer_quiet("totally.unknown.event") == UNKNOWN_LAYER


def test_4903_flush_writes_layer_candidate_file(cfg, captured):
    """累计到 flush 步长时落 layer_migration_candidates.jsonl，且字段齐全。"""
    cfg.M490_LAYER_DEFAULT_MODE = "log"
    for _ in range(diag._LAYER_FLUSH_STEP):
        diag.observe_layer("organX", "totally.unknown.event", "L1")
    assert captured
    _prefix, row = captured[-1]
    assert _prefix == "layer_migration_candidates"
    assert {"organ", "event", "current_layer", "inferred_layer", "count"} <= set(row)
    assert row["count"] >= diag._LAYER_FLUSH_STEP
