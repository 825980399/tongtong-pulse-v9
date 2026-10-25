# -*- coding: utf-8 -*-
"""191刀2：semantic_cache L2 遥测 INFO 日志输出 隔离单测。

覆盖：
  - telemetry() 追加 INFO 日志（命中率/污染率·按场景分布）
  - 日志含汇总字段（scenes / hits / misses / neg / stores / hit_rate / pollution_rate）与逐场景明细
  - telemetry() 返回结构不变（仅新增日志，不改计算/存储）

★369刀1 夹具更新（E9 日志瘦身第一批 F2 族）：本族由「每场景一行、每拍必打」
改为「**读数变化才打** ＋ 整次一行汇总」。实证：40.9h / 6034 条，读数恒 0 仍按
场景刷一整套，是本窗最大单族噪声源。断言随之改判汇总行，但**覆盖不降**：
四个场景仍需在明细段逐一可见，命中率/污染率仍需在汇总行可见。
"""
import logging

from nucleus.llm.semantic_cache import SemanticCacheL2
from nucleus.log_state import reset


def test_telemetry_info_log_emitted(caplog):
    """调用 telemetry() 应 emit 一行 INFO 汇总，按场景分布覆盖 overall/code/chat/reasoning。"""
    # 369刀1：聚合判据是进程级记忆（读数不变则不打），单测必须先重置才拿得到基线行
    reset("semantic_cache_l2.telemetry")
    cache = SemanticCacheL2()
    with caplog.at_level(logging.INFO, logger="semantic_cache_l2"):
        _t = cache.telemetry()
    msgs = [rec.getMessage() for rec in caplog.records]
    agg = [m for m in msgs if "[semantic_cache_l2] telemetry 汇总" in m]
    assert agg, msgs
    line = agg[0]
    for field in ("scenes=", "hits=", "misses=", "neg=", "stores=",
                  "hit_rate=", "pollution_rate="):
        assert field in line, line
    # 覆盖不降：四个场景在明细段逐一可见
    for scene in ("overall", "code", "chat", "reasoning"):
        assert scene + "(h=" in line, line
    # 返回结构不变
    assert set(_t.keys()) == {"overall", "code", "chat", "reasoning"}


def test_telemetry_suppresses_unchanged_reading(caplog):
    """369刀1 新增：读数无变化时不重复落 INFO（本刀核心降噪行为）。"""
    reset("semantic_cache_l2.telemetry")
    cache = SemanticCacheL2()
    with caplog.at_level(logging.INFO, logger="semantic_cache_l2"):
        cache.telemetry()
    n1 = len([r for r in caplog.records if "telemetry 汇总" in r.getMessage()])
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="semantic_cache_l2"):
        cache.telemetry()
        cache.telemetry()
    n2 = len([r for r in caplog.records if "telemetry 汇总" in r.getMessage()])
    assert n1 == 1, "首次应留一条基线汇总"
    assert n2 == 0, "读数无变化仍重复落 INFO ⇒ 降噪未生效"


def test_telemetry_return_unchanged():
    """telemetry() 返回仍含 hit_rate / pollution_rate（零行为变化）。"""
    cache = SemanticCacheL2()
    _t = cache.telemetry()
    for _v in _t.values():
        assert "hit_rate" in _v
        assert "pollution_rate" in _v
        assert "hits" in _v and "misses" in _v and "neg" in _v
