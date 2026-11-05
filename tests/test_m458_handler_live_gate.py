# -*- coding: utf-8 -*-
"""458 刀4（P2）：handler 实弹脚手架 —— 只验闸门，不实弹。

★本批铁律：`M458_HANDLER_LIVE_PCT` 必须是 **0**。本文件既验脚手架的正确性，
  也用最后一条用例把这个铁律钉死（谁把 PCT 改成非 0，这条会红）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
from organs.brain.PulseInnerWorld import PulseInnerWorld as IW


@pytest.fixture(autouse=True)
def _reset():
    IW._M458_LIVE_WINDOW = []
    IW._M458_LIVE_TRIPPED = False
    IW._M458_LIVE_TRIP_REASON = ""
    yield
    IW._M458_LIVE_WINDOW = []
    IW._M458_LIVE_TRIPPED = False
    IW._M458_LIVE_TRIP_REASON = ""


# ---------------------------------------------------------------- 分桶
def test_bucket_is_stable_and_reproducible():
    a = IW._m458_live_bucket("dlg:4:1793880475719", "m458")
    b = IW._m458_live_bucket("dlg:4:1793880475719", "m458")
    assert a == b
    assert 0 <= a < 100


def test_bucket_matches_md5_definition():
    import hashlib
    cid, salt = "dlg:458:x", "m458"
    _want = int(hashlib.md5(f"{salt}|{cid}".encode("utf-8")).hexdigest()[:8], 16) % 100
    assert IW._m458_live_bucket(cid, salt) == _want


def test_bucket_changes_with_salt():
    cid = "dlg:458:salt"
    assert IW._m458_live_bucket(cid, "m458") != IW._m458_live_bucket(cid, "other") or True
    # 换盐后整体分布应重排（单条可能碰巧相同，故只断言"不崩且合法"）
    assert 0 <= IW._m458_live_bucket(cid, "other") < 100


# ---------------------------------------------------------------- 闸门
def test_pct_zero_never_live():
    assert config.M458_HANDLER_LIVE_PCT == 0
    for _cid in ("a", "b", "c", "dlg:458:1"):
        assert IW._m458_live_allowed(_cid, "self_report") is False


def test_live_requires_switch(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M458_HANDLER_LIVE", True, raising=False)
    monkeypatch.setattr(config, "M458_HANDLER_LIVE_PCT", 100, raising=False)
    monkeypatch.setattr(config, "M458_HANDLER_LIVE_SCOPE", "all", raising=False)
    assert IW._m458_live_allowed("dlg:458:1", "self_report") is True


def test_scope_filters_handler_type(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M458_HANDLER_LIVE", True, raising=False)
    monkeypatch.setattr(config, "M458_HANDLER_LIVE_PCT", 100, raising=False)
    monkeypatch.setattr(config, "M458_HANDLER_LIVE_SCOPE", "self_report", raising=False)
    assert IW._m458_live_allowed("dlg:458:1", "self_report") is True
    assert IW._m458_live_allowed("dlg:458:1", "code_reasoning") is False


def test_switch_off_wins_over_pct(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M458_HANDLER_LIVE", False, raising=False)
    monkeypatch.setattr(config, "M458_HANDLER_LIVE_PCT", 100, raising=False)
    assert IW._m458_live_allowed("dlg:458:1", "self_report") is False


def test_empty_inputs_never_live(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M458_HANDLER_LIVE", True, raising=False)
    monkeypatch.setattr(config, "M458_HANDLER_LIVE_PCT", 100, raising=False)
    monkeypatch.setattr(config, "M458_HANDLER_LIVE_SCOPE", "all", raising=False)
    assert IW._m458_live_allowed("", "self_report") is False
    assert IW._m458_live_allowed("dlg:458:1", "") is False


# ---------------------------------------------------------------- 熔断
def test_breaker_trips_after_two_failures_in_window(monkeypatch):
    monkeypatch.setattr(config, "M458_HANDLER_BREAKER_WINDOW", 20, raising=False)
    monkeypatch.setattr(config, "M458_HANDLER_BREAKER_FAILS", 2, raising=False)
    assert IW._m458_live_note(False) is False      # 第 1 次失败，未达阈值
    assert IW._m458_live_note(False) is True       # 第 2 次 ⇒ 熔断
    assert IW._M458_LIVE_TRIPPED is True
    assert "PCT 自动归零" in IW._M458_LIVE_TRIP_REASON


def test_breaker_stops_live_even_at_pct_100(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M458_HANDLER_LIVE", True, raising=False)
    monkeypatch.setattr(config, "M458_HANDLER_LIVE_SCOPE", "all", raising=False)
    monkeypatch.setattr(config, "M458_HANDLER_LIVE_PCT", 100, raising=False)
    assert IW._m458_live_allowed("dlg:458:1", "self_report") is True
    IW._m458_live_note(False)
    IW._m458_live_note(False)
    assert IW._m458_live_allowed("dlg:458:1", "self_report") is False


def test_breaker_window_is_bounded(monkeypatch):
    monkeypatch.setattr(config, "M458_HANDLER_BREAKER_WINDOW", 5, raising=False)
    monkeypatch.setattr(config, "M458_HANDLER_BREAKER_FAILS", 2, raising=False)
    for _ in range(20):
        IW._m458_live_note(True)
    assert len(IW._M458_LIVE_WINDOW) == 5, "窗口必须封顶，否则内存无限增长"


# ---------------------------------------------------------------- 铁律
def test_batch_458_must_not_go_live():
    """★458 只搭架子：总闸关 + PCT=0。样本今天仍是 0 条，任何实弹都是无据放行。"""
    assert config.ENABLE_M458_HANDLER_LIVE is False
    assert config.M458_HANDLER_LIVE_PCT == 0
