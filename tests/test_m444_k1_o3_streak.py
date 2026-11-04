# -*- coding: utf-8 -*-
"""444 刀1：判死池后台刷新 + streak 的 `as_of` 语义。

钉住两件事：
  ① `streak` **只在 `as_of` 变化时 +1**（同一轮扫描重复调用不虚增）——
     这正是「重启后永远 0/3」的机制根因，也是本刀要绕开的点；
  ② 没有独立刷新拍时，`as_of` 只由数小时一轮的自检视更新 ⇒ 必须由 O3 节拍
     主动触发后台重扫（本刀新增）。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import nucleus.self_inspector as si


def test_streak_only_counts_new_scan_rounds():
    """同一 tick_id 重复调用不虚增；换 tick 才 +1（340 刀3 的幂等设计）。"""
    _st = {"streak": 0, "last_tick": None, "last_open": False}
    si._M340_JUDGE_ACTION_STATE.clear()
    si._M340_JUDGE_ACTION_STATE.update(_st)
    _kw = {"mode": "review", "sample": "PASS", "misjudge": 0.0}
    si._m340_judge_action_open(tick_id=100, **_kw)
    _s1 = si._M340_JUDGE_ACTION_STATE["streak"]
    si._m340_judge_action_open(tick_id=100, **_kw)      # 同一轮，重复调用
    _s2 = si._M340_JUDGE_ACTION_STATE["streak"]
    si._m340_judge_action_open(tick_id=101, **_kw)      # 新一轮
    _s3 = si._M340_JUDGE_ACTION_STATE["streak"]
    assert _s1 == 1, _s1
    assert _s2 == 1, "同一 as_of 重复调用不该虚增 streak"
    assert _s3 == 2, _s3


def test_refresh_disabled_switch(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENABLE_M444_DEAD_CODE_REFRESH", False, raising=True)
    _r = si.refresh_dead_code_review_async(max_age_sec=1.0)
    assert _r["status"] == "disabled", _r


def test_refresh_fresh_when_as_of_recent(monkeypatch):
    monkeypatch.setitem(si._DEAD_CODE_REVIEW_STATS, "as_of", time.time())
    _r = si.refresh_dead_code_review_async(max_age_sec=1800.0)
    assert _r["status"] == "fresh", _r


def test_refresh_started_when_stale(monkeypatch):
    """as_of 很旧 ⇒ 必须起后台线程（不阻塞调用方）。"""
    monkeypatch.setitem(si._DEAD_CODE_REVIEW_STATS, "as_of", time.time() - 7200)
    monkeypatch.setattr(si, "_M444_REFRESH_RUNNING", False, raising=True)
    _called = []
    monkeypatch.setattr(si, "get_self_inspector",
                        lambda: type("X", (), {"_detect_dead_code": lambda self: _called.append(1)})(),
                        raising=True)
    _r = si.refresh_dead_code_review_async(max_age_sec=1800.0)
    assert _r["status"] == "started", _r
    for _ in range(50):
        if _called:
            break
        time.sleep(0.02)
    assert _called == [1], "后台线程没有真的跑扫描"


def test_refresh_skipped_in_test_mode():
    """测试模式不传 max_age_sec ⇒ 不起线程（避免单测里冒出 25s 扫描）。"""
    _r = si.refresh_dead_code_review_async()
    assert _r["status"] == "skipped_test", _r


def test_o3_consume_calls_refresh_before_gate():
    """源码级：刷新必须在取闸门结论**之前**（否则本轮仍用旧池）。"""
    _p = os.path.join(_ROOT, "nucleus", "evolution", "patch_execution_dispatch.py")
    with open(_p, encoding="utf-8") as _f:
        _src = _f.read()
    _i_rf = _src.index("refresh_dead_code_review_async()")
    _i_gate = _src.index("o3_open_gate_report(", _i_rf)
    assert _i_rf < _i_gate, "刷新被挪到取闸门之后 ⇒ 白跑一轮"
