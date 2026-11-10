# -*- coding: utf-8 -*-
"""test_m489_idle_detect.py — 489-3 空转判定（先红后绿，门控）。

空转判定双触发条件：
  A. 连续 ≥20 次 <1ms 且返回 None；
  B. n≥20 且 (lt1ms 占比>90% 且 返回None 占比>90%)。
命中即 WARNING 并进入 _idle_suspects，get_idle_subscribers 暴露 top5。
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
    cfg.ENABLE_M488_EVENT_LEDGER = False
    cfg.ENABLE_M488_FANOUT_STAT = False
    return InfoField(max_history=50)


def test_idle_off_returns_empty(cfg):
    """★489-3：开关关时，即便喂满空转样本，get_idle_subscribers 也返回 []。"""
    f = _make_field(cfg)
    cfg.ENABLE_M489_IDLE_DETECT = False
    for _ in range(30):
        f._m489_note_handler_stat("o", "e", 0.2, None)
    assert f.get_idle_subscribers() == []


def test_idle_flagged_after_streak(cfg):
    """★489-3 条件 A：连续 22 次 <1ms 且 None → 判 ghost。"""
    f = _make_field(cfg)
    cfg.ENABLE_M489_IDLE_DETECT = True
    for _ in range(22):
        f._m489_note_handler_stat("ghost_org", "ghost_evt", 0.2, None)
    subs = f.get_idle_subscribers()
    assert len(subs) == 1
    assert subs[0]["organ"] == "ghost_org"
    assert subs[0]["event"] == "ghost_evt"
    assert subs[0]["verdict"] == "ghost"


def test_idle_high_ratio_flagged(cfg):
    """★489-3 条件 B：n≥20 且 lt1ms/None 占比均 >90% → 判 idle。"""
    f = _make_field(cfg)
    cfg.ENABLE_M489_IDLE_DETECT = True
    for i in range(20):
        if i == 0:
            f._m489_note_handler_stat("r_org", "r_evt", 50.0, {"v": 1})
        else:
            f._m489_note_handler_stat("r_org", "r_evt", 0.2, None)
    subs = f.get_idle_subscribers()
    assert len(subs) == 1
    assert subs[0]["organ"] == "r_org"


def test_idle_not_flagged_normal(cfg):
    """★489-3：正常 handler（>1ms 且有返回值）不应被判空转。"""
    f = _make_field(cfg)
    cfg.ENABLE_M489_IDLE_DETECT = True
    for _ in range(20):
        f._m489_note_handler_stat("n_org", "n_evt", 30.0, {"real": True})
    assert f.get_idle_subscribers() == []
