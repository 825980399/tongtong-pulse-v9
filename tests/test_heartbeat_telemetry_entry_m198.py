# -*- coding: utf-8 -*-
"""198刀1：例①½定案修正 —— on_heartbeat 入口 + telemetry() 入口 一次性 INFO 隔离单测。

验收：隔离单测含入口 INFO 断言（一次性，断点收窄至「分支未及 / 异常吞」两候选）。
不做：不改 telemetry() 本体、不翻生产开关、不删 silent_exc 兜底(:194)。

注：本环境 logger 有效级别为 WARNING，INFO 被过滤；测试直接记录方法调用
（_log / _L.info），不依赖 handler 派发，保证确定性。
"""
import os
from unittest import mock

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_source(path: str) -> str:
    with open(os.path.join(_REPO, path), "r", encoding="utf-8") as f:
        return f.read()


def test_on_heartbeat_entry_info_marker_in_source():
    """静态断言：on_heartbeat 检测分支入口存在「目标读数出现」一次性 INFO 标记。"""
    src = _load_source("nucleus/SurvivalOrchestrator.py")
    assert "目标读数出现：on_heartbeat 进入低频遥测扳机分支" in src
    assert "_HB_TELEMETRY_LOGGED" in src


def test_telemetry_entry_info_marker_in_source():
    """静态断言：telemetry() 入口存在一次性 INFO 标记。"""
    src = _load_source("nucleus/llm/semantic_cache.py")
    assert "telemetry 入口被调用（目标读数采集启动）" in src
    assert "_TELEMETRY_ENTRY_LOGGED" in src


def test_on_heartbeat_entry_info_once(monkeypatch):
    """启用 orchestrator 后，on_heartbeat 进入检测分支仅首拍记录「目标读数出现」一次性 INFO。"""
    import nucleus.SurvivalOrchestrator as so
    from nucleus.SurvivalOrchestrator import SurvivalOrchestrator

    # 复位进程级一次性标记，保证本测试自洽（不依赖测试执行顺序）
    monkeypatch.setattr(so.SurvivalOrchestrator, "_HB_TELEMETRY_LOGGED", False)
    monkeypatch.setattr(so, "_orchestrator_enabled", lambda: True)
    mock_l2 = mock.MagicMock()
    monkeypatch.setattr(so, "get_semantic_cache_l2", lambda: mock_l2)

    # 直接记录 _log 调用（绕过 logger 级别过滤，确定性）
    calls: list = []

    def _rec_log(self, level, message=None, **kwargs):
        if message is None:
            level, message = "INFO", level
        calls.append((str(level), str(message)))

    monkeypatch.setattr(so.SurvivalOrchestrator, "_log", _rec_log)

    orch = SurvivalOrchestrator()
    orch.tick = lambda: None  # 隔离真实感知闭环，仅验证入口 INFO 接线

    orch.on_heartbeat(5)   # 第 5 拍（DETECT_EVERY_BEATS=5）进入检测点
    orch.on_heartbeat(10)  # 再跑一拍，验证「一次性」（仅首拍记录）

    target = [m for (_lvl, m) in calls if "目标读数出现" in m]
    assert len(target) == 1, f"「目标读数出现」应恰好记录一次，实际 {len(target)} 次：{calls}"


def test_telemetry_entry_info_emitted(monkeypatch):
    """直接调用真实 telemetry()，验证入口一次性 INFO 被发出（首次调用，绕过级别过滤）。"""
    import nucleus.llm.semantic_cache as sc
    from nucleus.llm.semantic_cache import SemanticCacheL2

    monkeypatch.setattr(sc, "_TELEMETRY_ENTRY_LOGGED", False)

    msgs: list = []
    monkeypatch.setattr(sc._L, "info", lambda *a, **k: msgs.append(" ".join(str(x) for x in a)))

    cache = SemanticCacheL2()
    cache.telemetry()

    assert any("telemetry 入口被调用" in m for m in msgs), f"telemetry 入口 INFO 未发出：{msgs}"
