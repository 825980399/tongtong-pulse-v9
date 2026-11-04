# -*- coding: utf-8 -*-
"""451 刀6：问题池低危条目上限 600 → 2000。"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import nucleus.audit_problem_pool as AP


def _low(n, start=0):
    return [{"id": f"l{i}", "severity": "low", "status": "open",
             "first_seen": 1_700_000_000 + i} for i in range(start, start + n)]


def test_默认上限是2000():
    assert AP.MAX_LOW_ENTRIES == 2000, AP.MAX_LOW_ENTRIES


def test_上限仍低于总闸():
    """★扩容不是取消上限：低危额度必须明显小于 MAX_POOL_ENTRIES，否则等于没闸。"""
    assert AP.MAX_LOW_ENTRIES < AP.MAX_POOL_ENTRIES, (AP.MAX_LOW_ENTRIES,
                                                       AP.MAX_POOL_ENTRIES)


def test_995条不再被截():
    """现场实测 995 条（600 额度外溢 395 条）—— 这就是本刀要治的那一幕。"""
    _e = _low(995)
    _kept, _dropped = AP.cap_low_severity(_e)
    assert len(_kept) == 995, len(_kept)
    assert _dropped == [], len(_dropped)


def test_超过2000才截():
    _e = _low(2005)
    _kept, _dropped = AP.cap_low_severity(_e)
    assert len(_kept) == 2000, len(_kept)
    assert len(_dropped) == 5, len(_dropped)


def test_medium_high永不被截():
    _e = _low(2001) + [{"id": "m1", "severity": "medium", "status": "open"},
                       {"id": "h1", "severity": "high", "status": "open"}]
    _kept, _dropped = AP.cap_low_severity(_e)
    _ids = {x["id"] for x in _kept}
    assert {"m1", "h1"} <= _ids, "限额只砍 low，绝不挤掉可修项"
    assert len(_dropped) == 1, len(_dropped)


def test_显式cap仍然生效():
    """`cap=` 参数是既有契约（单测/报表按它取样本），不能被全局常量顶掉。"""
    _kept, _dropped = AP.cap_low_severity(_low(10), cap=3)
    assert len(_kept) == 3 and len(_dropped) == 7


def test_配置可覆盖(monkeypatch):
    monkeypatch.setattr(config, "M451_MAX_LOW_ENTRIES", 5, raising=False)
    _m = importlib.reload(AP)
    try:
        assert _m.MAX_LOW_ENTRIES == 5, _m.MAX_LOW_ENTRIES
    finally:
        monkeypatch.undo()
        importlib.reload(_m)
