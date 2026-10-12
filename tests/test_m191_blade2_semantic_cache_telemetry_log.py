# -*- coding: utf-8 -*-
"""191刀2：semantic_cache L2 遥测 INFO 日志输出 隔离单测。

覆盖：
  - telemetry() 追加 INFO 日志（命中率/污染率·按场景分布）
  - 日志格式含 scene / hit_rate / pollution_rate
  - telemetry() 返回结构不变（仅新增日志，不改计算/存储）
"""
import logging

from nucleus.llm.semantic_cache import SemanticCacheL2


def test_telemetry_info_log_emitted(caplog):
    """调用 telemetry() 应 emit INFO 日志，按场景分布覆盖 overall/code/chat/reasoning。"""
    cache = SemanticCacheL2()
    with caplog.at_level(logging.INFO, logger="semantic_cache_l2"):
        _t = cache.telemetry()
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("telemetry scene=" in m for m in msgs), msgs
    assert any("hit_rate=" in m and "pollution_rate=" in m for m in msgs), msgs
    scenes = [m for m in msgs if "telemetry scene=" in m]
    assert any("scene=overall" in m for m in scenes)
    assert any("scene=code" in m for m in scenes)
    assert any("scene=chat" in m for m in scenes)
    assert any("scene=reasoning" in m for m in scenes)
    # 返回结构不变
    assert set(_t.keys()) == {"overall", "code", "chat", "reasoning"}


def test_telemetry_return_unchanged():
    """telemetry() 返回仍含 hit_rate / pollution_rate（零行为变化）。"""
    cache = SemanticCacheL2()
    _t = cache.telemetry()
    for _v in _t.values():
        assert "hit_rate" in _v
        assert "pollution_rate" in _v
        assert "hits" in _v and "misses" in _v and "neg" in _v
