# -*- coding: utf-8 -*-
"""451 刀4：安全演化执行 LLM 修复失败告警聚合（连续 3 次才 WARNING）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import nucleus.reasoning.SafeEvolutionExecutor as SE
from nucleus import alert_dedup as AD


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    AD.reset_all()
    monkeypatch.setattr(config, "M451_LLM_REPAIR_STREAK_THRESHOLD", 3, raising=False)
    monkeypatch.setattr(config, "M451_LLM_REPAIR_RENOTIFY_SEC", 600.0, raising=False)
    yield
    AD.reset_all()


def test_连续三次才WARNING():
    _got = [SE._m451_llm_repair_should_warn("no_result")[0] for _ in range(5)]
    assert _got == [False, False, True, False, False], _got


def test_两类失败分别计数():
    """`no_result` 与 `empty_content` 是两类，不能互相把对方的连续计数顶起来。"""
    assert SE._m451_llm_repair_should_warn("no_result")[1] == 1
    assert SE._m451_llm_repair_should_warn("empty_content")[1] == 1
    assert SE._m451_llm_repair_should_warn("no_result")[1] == 2


def test_阈值可配(monkeypatch):
    monkeypatch.setattr(config, "M451_LLM_REPAIR_STREAK_THRESHOLD", 1, raising=False)
    assert SE._m451_llm_repair_should_warn("k1")[0] is True


def test_过阈值后按时间窗重报():
    """渠道真挂了必须还能看见：过阈值后每 renotify 秒再报一次。"""
    AD.reset_all()
    for _ in range(3):
        AD.should_warn_streak("llm_repair:x", threshold=3, renotify_sec=600.0,
                              now=1000.0)
    assert AD.should_warn_streak("llm_repair:x", threshold=3, renotify_sec=600.0,
                                 now=1300.0)[0] is False
    assert AD.should_warn_streak("llm_repair:x", threshold=3, renotify_sec=600.0,
                                 now=1601.0)[0] is True


def test_两处告警都接线了():
    _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "nucleus", "reasoning", "SafeEvolutionExecutor.py")
    with open(_p, encoding="utf-8", errors="replace") as f:
        _src = f.read()
    assert _src.count("_m451_llm_repair_should_warn(") == 3   # 定义 1 + 调用 2
    assert "连续第 %d 次" in _src
