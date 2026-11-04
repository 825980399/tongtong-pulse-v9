# -*- coding: utf-8 -*-
"""448 刀1：trust 按证据加分（只升不降）+ 明细留痕。"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.reasoning.PatchManager import PatchManager as PM


def _wire(tmp_path, monkeypatch, on=True):
    monkeypatch.setattr(config, "M448_TRUST_TRACE_PATH",
                        str(tmp_path / "trust_calc_trace.jsonl"), raising=True)
    monkeypatch.setattr(config, "ENABLE_M448_TRUST_CALC", on, raising=True)


def _rows(tmp_path):
    _p = tmp_path / "trust_calc_trace.jsonl"
    if not os.path.exists(str(_p)):
        return []
    with open(str(_p), encoding="utf-8") as _f:
        return [json.loads(x) for x in _f if x.strip()]


def _good(**kw):
    _p = {"id": "p448_1", "file": "organs/body/PulseHeart.py", "method": "m",
          "risk_level": "低", "trust_score": 40, "baseline_errors": 3,
          "problem_gone": True}
    _p.update(kw)
    return _p


def test_all_signals_full_score(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _r = PM._m448_calc_trust(_good())
    assert _r["score"] == 70.0, _r
    assert _r["after"] == 70.0 and _r["applied"] is True, _r
    assert [i["rule"] for i in _r["items"]] == [
        "risk_low", "single_file", "blast_le_1", "baseline_positive",
        "problem_gone_true"], _r["items"]
    assert all(i["ok"] for i in _r["items"]), _r["items"]


def test_missing_signals_lower_score(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _r = PM._m448_calc_trust(_good(baseline_errors=0, problem_gone=None))
    assert _r["score"] == 40.0, _r          # 20 + 10 + 10
    assert _r["after"] == 40.0, _r          # 旧值 40，取 max 不动


def test_never_lowers_existing_trust(tmp_path, monkeypatch):
    """★只升不降：证据不全时绝不能把 80 压到 0（那会顺手关掉自动应用线）。"""
    _wire(tmp_path, monkeypatch)
    _r = PM._m448_calc_trust({"id": "x", "file": "a.py", "risk_level": "高",
                              "trust_score": 80, "problem_gone": None})
    assert _r["score"] == 20.0, _r        # 只剩 单文件+10 / blast+10
    assert _r["after"] == 80 and _r["applied"] is False, _r


def test_high_risk_gets_no_risk_bonus(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _r = PM._m448_calc_trust(_good(risk_level="高"))
    assert _r["score"] == 50.0, _r          # 70 - 20
    assert _r["applied"] is True and _r["after"] == 50.0, _r


def test_multi_file_loses_single_and_blast(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _r = PM._m448_calc_trust({"id": "x", "file": "a.py", "risk_level": "低",
                              "trust_score": 10, "baseline_errors": 1,
                              "problem_gone": True,
                              "files": ["a.py", "b.py"]})
    assert _r["score"] == 50.0, _r          # 20 + 10 + 20（单文件/ blast 不给分）


def test_score_capped_at_100(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _r = PM._m448_calc_trust(_good())
    assert _r["score"] <= 100.0, _r


def test_sets_aux_fields(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _p = _good(trust_score=None)
    PM._m448_calc_trust(_p)
    assert _p["trust_score"] == 70.0, _p
    assert _p["trust_calc_score"] == 70.0, _p
    assert isinstance(_p["trust_calc_at"], float), _p


def test_trace_has_items_detail(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    PM._m448_calc_trust(_good())
    _r = _rows(tmp_path)[0]
    assert _r["patch_id"] == "p448_1" and _r["applied"] is True, _r
    assert _r["before"] == 40 and _r["after"] == 70, _r
    assert len(_r["items"]) == 5, _r
    _by = {i["rule"]: i for i in _r["items"]}
    assert _by["problem_gone_true"]["delta"] == 20.0, _by
    assert "problem_gone=True" in _by["problem_gone_true"]["why"], _by


def test_switch_off_no_change(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, on=False)
    _p = _good()
    _r = PM._m448_calc_trust(_p)
    assert _r["applied"] is False and _p["trust_score"] == 40, _r
    assert _rows(tmp_path) == [], "开关关了还写留痕"


def test_bad_inputs_safe(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    assert PM._m448_calc_trust(None)["applied"] is False
    assert PM._m448_calc_trust({})["applied"] is False
    # 旧值非数值 ⇒ 按 0 处理，不抛
    assert PM._m448_calc_trust({"id": "x", "trust_score": "abc",
                                "file": "a.py"})["before"] == "abc"


def test_trust_thresholds_unchanged():
    """本批不得动任何既有信任门槛（只加算法，不改门）。"""
    assert config.EVOLUTION_CONFIG["auto_apply_min_trust"] == 40
    assert config.PATCH_AUTO_APPROVE_TRUST_THRESHOLD == 40
    assert config.M439_APPROVE_MIN_TRUST == 60.0
