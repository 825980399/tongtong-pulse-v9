# -*- coding: utf-8 -*-
"""
test_spike_wiring_m203.py —— 第203批 刀1 隔离单测

刀1：执行后端接入实际脉冲流（P1 · 类脑改造）
    PulseCore.emit 在 SPIKE_BACKEND_ENABLED=True 时调用 PythonSNNBackend.emit；
    PulseCore.on_spike 薄转发到后端；默认关闭（SPIKE_BACKEND_ENABLED=False）零行为。

验收要点：
    1. SPIKE_BACKEND_ENABLED 默认 False（零行为变化）。
    2. 默认关闭时 emit 不调用任何后端（_get_spike_backend 返回 None）。
    3. 开启后 emit 调用后端 emit（传入 pulse）。
    4. on_spike 薄转发到后端 on_spike（开启时）。
    5. 不违反 202 批「未授权接入点」口径：接入点仅 PulseCore。
"""
import config
import pytest

from nucleus.pulse.PulseCore import PulseCore
from nucleus.synapsys.SpikeExecutionBackend import PythonSNNBackend


class _RecordingBackend(PythonSNNBackend):
    """记录调用次数的测试替身。"""

    def __init__(self):
        super().__init__()
        self.emit_calls = []
        self.on_spike_calls = []

    def emit(self, spike):
        self.emit_calls.append(spike)

    def on_spike(self, spike):
        self.on_spike_calls.append(spike)


@pytest.fixture
def core():
    return PulseCore()


def test_enabled_default_false():
    assert getattr(config, "SPIKE_BACKEND_ENABLED", False) is False


def test_gate_disabled_returns_none(core, monkeypatch):
    monkeypatch.setattr(config, "SPIKE_BACKEND_ENABLED", False)
    assert core._get_spike_backend() is None


def test_gate_enabled_returns_backend(core, monkeypatch):
    monkeypatch.setattr(config, "SPIKE_BACKEND_ENABLED", True)
    b = core._get_spike_backend()
    assert isinstance(b, PythonSNNBackend)


def test_emit_wires_to_backend_when_enabled(core, monkeypatch):
    monkeypatch.setattr(config, "SPIKE_BACKEND_ENABLED", True)
    fake = _RecordingBackend()
    monkeypatch.setattr(core, "_get_spike_backend", lambda: fake)
    pulse = core.emit("测试器官", "test.event", priority=5)
    assert len(fake.emit_calls) == 1
    assert fake.emit_calls[0] is pulse


def test_on_spike_wires_to_backend_when_enabled(core, monkeypatch):
    monkeypatch.setattr(config, "SPIKE_BACKEND_ENABLED", True)
    fake = _RecordingBackend()
    monkeypatch.setattr(core, "_get_spike_backend", lambda: fake)
    spike = {"pulse_id": "s1"}
    core.on_spike(spike)
    assert len(fake.on_spike_calls) == 1
    assert fake.on_spike_calls[0] is spike


def test_on_spike_noop_when_disabled(core, monkeypatch):
    # 关开关 + 替身后端：门控应在调用后端前短路返回 None，替身不被触发
    monkeypatch.setattr(config, "SPIKE_BACKEND_ENABLED", False)
    fake = _RecordingBackend()
    monkeypatch.setattr(
        "nucleus.synapsys.SpikeExecutionBackend.get_spike_backend", lambda *a, **k: fake
    )
    core.on_spike({"pulse_id": "s2"})
    assert fake.on_spike_calls == []
