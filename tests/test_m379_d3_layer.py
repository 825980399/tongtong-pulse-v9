# -*- coding: utf-8 -*-
"""★379 刀3 · 脉冲分层误判修复（DJ4-02，P0）

票面：L0+L1 仅 2.3%，L3 占 87.6%，`_auto_layer` 疑似把生命线事件误判为 L3。

T0 实测（`data/monitor/pulse_pairs_20261028.jsonl`，120,588 emit）：
    L3 89.32% / L2 7.78% / L1 2.67% / L0 0.23%    ← 票面数字复现
但**不是 `_infer_layer` 误判**：它默认返回 L1，`_DEFAULT_LAYER_MAP` 只有 20 条精确匹配，
唯一映射到 L3 的是 `purge.check`。真正的来源是**发射点显式写了 `layer="L3"`**
（全库 117 处显式 L3），其中 `eyes.stream_frame` 一家占 74.8%。
⇒ 修在 emit 收口（唯一真源），而不是逐发射点改。

★`eyes.stream_frame` 刻意**不降层**：L2/L3 在 InfoField 里带 TTL + 30%/50% 随机降载
（InfoField:634/642），L0/L1 免降载；它占 74.8% 流量，挪进 L1 等于拆掉泄压阀。
相机帧属"外部 IO"，按任务书规则本就该留 L3。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus._silent_except import silent_exc  # noqa: E402

from nucleus.pulse.PulseCore import PulseCore  # noqa: E402

_PAIRS = os.path.join(ROOT, "data", "monitor", "pulse_pairs_20261028.jsonl")


def _core():
    """不跑 `__init__`（避免拉起执行后端/线程），只取纯判定方法。"""
    return PulseCore.__new__(PulseCore)


@pytest.mark.parametrize("event_type,was,want", [
    ("heart.beat", "L3", "L1"),
    ("heart.alive", "L3", "L1"),
    ("energy.metabolism_snapshot", "L3", "L1"),
    ("touch.hardware_snapshot", "L3", "L1"),
    ("spinal.inspection_report", "L3", "L1"),
    ("hormones.detect", "L3", "L1"),
    ("metrics.snapshot", "L3", "L1"),
    ("curiosity.tick", "L3", "L1"),
    # 反控①：已知的"真"L3 —— 外部 IO / 传感流，必须原样留 L3
    ("eyes.stream_frame", "L3", "L3"),
    ("lungs.select_model", "L3", "L3"),
    # 反控②：L2 且非生命线 ⇒ 不动（知识消化是自主推理面）
    ("digest.knowledge", "L2", "L2"),
    # 反控③：已经是 L0/L1 ⇒ 不动（只纠正"高报"）
    ("organ.silent", "L0", "L0"),
    ("chat.initiative", "L1", "L1"),
])
def test_m379_d3_resolve_layer(event_type, was, want):
    _c = _core()
    assert PulseCore._m379_resolve_layer(_c, event_type, was) == want


def test_m379_d3_switch_off_restores_old_layers(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M379_LIFELINE_LAYER", False, raising=False)
    _c = _core()
    assert PulseCore._m379_resolve_layer(_c, "heart.beat", "L3") == "L3"
    # 关掉后 `_infer_layer` 也回到旧口径（心拍走 _DEFAULT_LAYER_MAP → L0）
    assert PulseCore._infer_layer(_c, "heart.beat") == "L0"


def test_m379_d3_infer_layer_covers_lifeline_when_layer_omitted():
    """没传 layer 的路径同样要归对（否则只修了显式那一半）。"""
    _c = _core()
    assert PulseCore._infer_layer(_c, "heart.alive") == "L1"
    assert PulseCore._infer_layer(_c, "touch.hardware_snapshot") == "L1"
    assert PulseCore._infer_layer(_c, "purge.check") == "L3"       # 既有映射不得丢
    assert PulseCore._infer_layer(_c, "some.unknown.event") == "L1"


def test_m379_d3_downgrade_logged_once_per_type():
    """热路径：同类只记一次（9 万次 emit 不能逐条打 INFO）。"""
    _c = _core()
    PulseCore._m379_resolve_layer(_c, "heart.beat", "L3")
    PulseCore._m379_resolve_layer(_c, "heart.beat", "L3")
    _seen = _c.__dict__.get("_m379_downgrades") or set()
    assert ("heart.beat", "L3") in _seen
    assert len([k for k in _seen if k[0] == "heart.beat"]) == 1


@pytest.mark.skipif(not os.path.isfile(_PAIRS), reason="当日 pulse_pairs 落盘缺失")
def test_m379_d3_replay_real_day_meets_15pct():
    """★验收：用**当日全量真实 emit** 重放，L0+L1 占比必须 ≥ 15%。"""
    _c = _core()
    _tot = _low = 0
    with open(_PAIRS, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception as _je:
                silent_exc(_je, where="test_m379_d3.replay json")
                continue
            if d.get("type", "emit") != "emit":
                continue
            _tot += 1
            if PulseCore._m379_resolve_layer(_c, d.get("event_type") or "", d.get("layer") or "?") in ("L0", "L1"):
                _low += 1
    assert _tot > 10000, "样本太少（%d），不构成验收" % _tot
    _pct = _low * 100.0 / _tot
    assert _pct >= 15.0, "L0+L1=%.2f%% < 15%%（total=%d）" % (_pct, _tot)
