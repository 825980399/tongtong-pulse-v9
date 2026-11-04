# -*- coding: utf-8 -*-
"""451 刀5：自检视「不可判红色告警」同内容只报一次。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus._warn_throttle as WT
import nucleus.log_state as LS
from nucleus import alert_dedup as AD


@pytest.fixture(autouse=True)
def _reset():
    AD.reset_all()
    LS.reset(None)
    WT._LAST.clear()
    yield
    AD.reset_all()
    LS.reset(None)
    WT._LAST.clear()


def test_同一批条目只报一次():
    """★实测：5 分钟一轮，2.5 小时 29 条 ERROR，items 全是「⑦ K1三证＋对表」。"""
    _items = "⑦ K1三证＋对表"
    _got = [AD.should_warn_content("self_inspector.unjudged_red", _items,
                                   renotify_sec=3600.0)[0] for _ in range(6)]
    assert _got[0] is True, _got
    assert all(_g is False for _g in _got[1:]), _got


def test_条目集合变了立刻重报():
    AD.should_warn_content("self_inspector.unjudged_red", "⑦ K1三证＋对表",
                           renotify_sec=3600.0)
    _e, _ = AD.should_warn_content("self_inspector.unjudged_red",
                                   "⑦ K1三证＋对表,⑧ 另一条判据", renotify_sec=3600.0)
    assert _e is True


def test_计数与判定未被改动():
    """★去重只压日志：`note_unjudged` 仍逐条登记、越线仍触发审计。"""
    from nucleus.evolution import criteria_window as CW
    CW.reset_unjudged()
    for _ in range(3):
        CW.note_unjudged("⑦ K1三证＋对表", "probe")
    _al = CW.check_unjudged_alarm()
    assert _al["count"] == 3, _al
    assert _al["alarm"] is True, _al
    assert _al["verdict"] == "RED_ALARM", _al
    CW.reset_unjudged()


def test_源码里接的是内容去重():
    _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "nucleus", "self_inspector.py")
    with open(_p, encoding="utf-8", errors="replace") as f:
        _src = f.read()
    assert '"self_inspector.unjudged_red"' in _src
    assert "M451_UNJUDGED_RENOTIFY_SEC" in _src
    # 读数仍写进报表（去重不吞信息）
    assert '_rep["unjudged_alarm"] = _al' in _src
