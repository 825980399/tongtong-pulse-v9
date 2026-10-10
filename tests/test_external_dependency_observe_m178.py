# -*- coding: utf-8 -*-
"""178批 刀4：外部依赖度 OBSERVE 埋点隔离门控测试（只观察不接线，零行为变化）。

运行方式：独立隔离单跑（清 PYTHONPATH/PYTHONHOME/PYTHONSTARTUP + NOUSERSITE），
确保不污染全库测试面。门禁判据：本文件 4 passed。
"""
from __future__ import annotations

import os
import sys

# 从本文件推导项目根注入 sys.path（无硬编码绝对路径）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config as _cfg  # noqa: E402
from nucleus.field.external_dependency_observe import (  # noqa: E402
    observe_delegate_browser,
    observe_external_channel_call,
    observe_semantic_decision,
    reset,
    snapshot,
)


def setup_function(_fn):
    reset()


def test_external_channel_call_increments_when_enabled():
    _cfg.ENABLE_SEMANTIC_CACHE_OBSERVE = True
    observe_external_channel_call()
    observe_external_channel_call()
    assert snapshot()["external_channel_calls"] == 2


def test_semantic_decision_counts_llm_used_false_ratio():
    _cfg.ENABLE_SEMANTIC_CACHE_OBSERVE = True
    # 3 次判定：2 次无需 LLM、1 次需要
    observe_semantic_decision(False)
    observe_semantic_decision(False)
    observe_semantic_decision(True)
    _s = snapshot()
    assert _s["llm_total"] == 3
    assert _s["llm_used_false"] == 2
    assert abs(_s["llm_used_false_ratio"] - round(2 / 3, 4)) < 1e-9


def test_delegate_browser_increments():
    _cfg.ENABLE_SEMANTIC_CACHE_OBSERVE = True
    observe_delegate_browser()
    assert snapshot()["delegate_browser"] == 1


def test_observe_noop_when_disabled():
    _cfg.ENABLE_SEMANTIC_CACHE_OBSERVE = False
    observe_external_channel_call()
    observe_semantic_decision(False)
    observe_delegate_browser()
    _s = snapshot()
    assert _s["external_channel_calls"] == 0
    assert _s["llm_total"] == 0
    assert _s["delegate_browser"] == 0
