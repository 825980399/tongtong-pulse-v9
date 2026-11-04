# -*- coding: utf-8 -*-
"""446 刀2：四个基线字段随 patch 落 pending / 带入 history。"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.reasoning.PatchManager import PatchManager as PM

_FIELDS = ("baseline_errors", "baseline_source", "baseline_window_sec", "baseline_shadow")


def test_ensure_fills_missing_fields():
    _p = {"id": "p1", "file": "organ/x.py", "method": "m"}
    _r = PM._m446_ensure_baseline_fields(_p)
    assert sorted(_r["filled"]) == sorted(_FIELDS), _r
    for _k in _FIELDS:
        assert _k in _p and _p[_k] is None, _p


def test_ensure_does_not_overwrite_values():
    _p = {"baseline_errors": 3, "baseline_source": "code_repro",
          "baseline_window_sec": 86400.0, "baseline_shadow": True}
    _r = PM._m446_ensure_baseline_fields(_p)
    assert _r["filled"] == [], _r
    assert _p["baseline_errors"] == 3 and _p["baseline_source"] == "code_repro"


def test_ensure_partial_fill():
    """只缺一个键时只补那个键 —— 不把已有的 True/数值改写成 None。"""
    _p = {"baseline_errors": None, "baseline_source": "unavailable",
          "baseline_shadow": False}
    _r = PM._m446_ensure_baseline_fields(_p)
    assert _r["filled"] == ["baseline_window_sec"], _r
    assert _p["baseline_source"] == "unavailable"


def test_ensure_handles_non_dict():
    assert PM._m446_ensure_baseline_fields(None)["filled"] == []
    assert PM._m446_ensure_baseline_fields("x")["filled"] == []


def test_carry_sets_timestamp_when_source_present():
    _p = {"id": "p2", "baseline_errors": 2, "baseline_source": "code_repro"}
    _r = PM._m446_carry_baseline_to_history(_p)
    assert _r["carried"] is True and _r["baseline_source"] == "code_repro", _r
    assert isinstance(_p.get("baseline_carried_at"), float), _p
    assert abs(_p["baseline_carried_at"] - time.time()) < 60, _p


def test_carry_no_timestamp_when_source_missing():
    _p = {"id": "p3"}
    _r = PM._m446_carry_baseline_to_history(_p)
    assert _r["carried"] is False and _r["baseline_source"] is None, _r
    assert "baseline_carried_at" not in _p, _p
    # 四字段仍然被补齐（history 行从此带上键）
    for _k in _FIELDS:
        assert _k in _p, _p


def test_carry_is_idempotent():
    _p = {"id": "p4", "baseline_errors": 1, "baseline_source": "code_repro"}
    PM._m446_carry_baseline_to_history(_p)
    _first = _p["baseline_carried_at"]
    _r2 = PM._m446_carry_baseline_to_history(_p)
    assert _r2["carried"] is False and _p["baseline_carried_at"] == _first, _p


def test_baseline_fields_tuple_matches_contract():
    """字段清单与任务书逐字对齐（少一个就等于少带一份基线）。"""
    assert tuple(PM._M446_BASELINE_FIELDS) == _FIELDS
