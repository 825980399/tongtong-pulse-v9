# -*- coding: utf-8 -*-
"""194刀1：telemetry() 外部调用方接线隔离单测。

验收：心跳低频拍加一处 telemetry() 读取（外部调用方存在）；隔离单测含外部调用方存在断言。
"""
import os


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_source(path: str) -> str:
    with open(os.path.join(_REPO, path), "r", encoding="utf-8") as f:
        return f.read()


def test_telemetry_external_caller_exists_in_source():
    """静态断言：SurvivalOrchestrator 已接入 telemetry() 外部调用方（修复有出口无扳机）。"""
    src = _load_source("nucleus/SurvivalOrchestrator.py")
    assert "from nucleus.llm.semantic_cache import get_semantic_cache_l2" in src
    assert "get_semantic_cache_l2().telemetry()" in src


def test_on_heartbeat_triggers_telemetry(monkeypatch):
    """启用 orchestrator 后，每 DETECT_EVERY_BEATS 拍的 on_heartbeat 触发 telemetry() 读取。"""
    import unittest.mock as mock

    import nucleus.SurvivalOrchestrator as so
    from nucleus.SurvivalOrchestrator import SurvivalOrchestrator

    monkeypatch.setattr(so, "_orchestrator_enabled", lambda: True)
    mock_l2 = mock.MagicMock()
    monkeypatch.setattr(so, "get_semantic_cache_l2", lambda: mock_l2)

    orch = SurvivalOrchestrator()
    orch.tick = lambda: None  # 隔离真实感知闭环，仅验证 telemetry 接线
    orch.on_heartbeat(5)  # 第 5 拍（DETECT_EVERY_BEATS=5）进入检测点
    assert mock_l2.telemetry.called, "on_heartbeat(5) 未触发 telemetry()"


def test_on_heartbeat_no_telemetry_when_disabled(monkeypatch):
    """默认关闭（_orchestrator_enabled 为假）时，on_heartbeat 不触发 telemetry（零行为变化）。"""
    import unittest.mock as mock

    import nucleus.SurvivalOrchestrator as so
    from nucleus.SurvivalOrchestrator import SurvivalOrchestrator

    monkeypatch.setattr(so, "_orchestrator_enabled", lambda: False)
    mock_l2 = mock.MagicMock()
    monkeypatch.setattr(so, "get_semantic_cache_l2", lambda: mock_l2)

    orch = SurvivalOrchestrator()
    orch.on_heartbeat(5)
    assert not mock_l2.telemetry.called, "关闭时不应触发 telemetry()"
