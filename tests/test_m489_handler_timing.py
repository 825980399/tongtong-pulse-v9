# -*- coding: utf-8 -*-
"""test_m489_handler_timing.py — 489-1 handler 耗时埋点 + 返回值带出（先红后绿，门控）。

所有断言均建立在「开关默认关、逐字回滚态」之上：关掉即没有任何计数/计时行为变化。
"""
import pytest


@pytest.fixture
def cfg():
    import config as _c
    _saved = {}
    for k in ("ENABLE_M489_HANDLER_TIMING", "ENABLE_M489_IDLE_DETECT",
             "ENABLE_M488_EVENT_LEDGER", "ENABLE_M488_FANOUT_STAT"):
        _saved[k] = getattr(_c, k, None)
    yield _c
    for k, v in _saved.items():
        if v is None:
            if hasattr(_c, k):
                delattr(_c, k)
        else:
            setattr(_c, k, v)


def _make_field(cfg):
    from nucleus.field.InfoField import InfoField
    # 仅隔离 488 定时器副作用；489 开关由各用例在构造后显式设置
    cfg.ENABLE_M488_EVENT_LEDGER = False
    cfg.ENABLE_M488_FANOUT_STAT = False
    return InfoField(max_history=50)


def test_return_value_passthrough(cfg):
    """★489-1：handler 的返回值被原样带出（即便开关全关）。"""
    f = _make_field(cfg)
    captured = {}

    def h(p):
        captured["p"] = p
        return {"ok": 1}

    res = f._call_handler_safe({"event_type": "x", "payload": 9}, h)
    assert res == {"ok": 1}
    assert captured["p"]["payload"] == 9


def test_timing_off_no_stats(cfg):
    """开关全关：_handler_stats 与 get_handler_stats 都必须为空。"""
    f = _make_field(cfg)
    cfg.ENABLE_M489_HANDLER_TIMING = False

    def h(p):
        return 42

    f._call_handler_safe({"event_type": "x"}, h, "org", "evt")
    assert f._handler_stats == {}
    assert f.get_handler_stats() == {}


def test_timing_on_records_stats(cfg):
    """★489-1：开计时后，一次调用应进入 _handler_stats（7 桶 + 计数）。"""
    f = _make_field(cfg)
    cfg.ENABLE_M489_HANDLER_TIMING = True
    f._call_handler_safe({"event_type": "heart.beat"}, lambda p: "done",
                         "t_org", "heart.beat")
    stats = f.get_handler_stats()
    assert "t_org/heart.beat" in stats
    row = stats["t_org/heart.beat"]
    assert row["n"] == 1
    assert sum(row["buckets"]) == 1
    assert row["max_ms"] >= 0.0
    assert "p95_ms" in row


def test_error_count_incremented(cfg):
    """★489-1：handler 抛异常被静默保护，返回值 None，error_count 累加。"""
    f = _make_field(cfg)
    cfg.ENABLE_M489_HANDLER_TIMING = True

    def boom(p):
        raise RuntimeError("boom")

    res = f._call_handler_safe({"event_type": "e"}, boom, "o", "e")
    assert res is None
    assert f._handler_stats[("o", "e")]["error_count"] >= 1
