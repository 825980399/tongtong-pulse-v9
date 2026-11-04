# -*- coding: utf-8 -*-
"""448 刀2：problem_gone 带到补丁顶层（台账 → 静态复现）+ 判定留痕。"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.reasoning.PatchManager import PatchManager as PM

_BUG = ("        try:\n"
        "            return 1 / x\n"
        "        except Exception:\n"
        "            pass\n")
_FIX = ("def tick(self, x):\n"
        "    try:\n"
        "        return 1 / x\n"
        "    except Exception as e:\n"
        "        self._log('err', e)\n"
        "    return 0\n")


def _wire(tmp_path, monkeypatch, on=True):
    monkeypatch.setattr(config, "M448_PROBLEM_GONE_TRACE_PATH",
                        str(tmp_path / "problem_gone_trace.jsonl"), raising=True)
    monkeypatch.setattr(config, "ENABLE_M448_PROBLEM_GONE_SYNC", on, raising=True)


def _rows(tmp_path):
    _p = tmp_path / "problem_gone_trace.jsonl"
    if not os.path.exists(str(_p)):
        return []
    with open(str(_p), encoding="utf-8") as _f:
        return [json.loads(x) for x in _f if x.strip()]


def _pm(tmp_path):
    os.makedirs(str(tmp_path / "data" / "patches"), exist_ok=True)
    return PM(str(tmp_path))


def _patch(**kw):
    _p = {"id": "p448_pg_1", "file": "organs/x.py", "method": "tick",
          "issue_type": "silent_exception",
          "original_code": _BUG, "modified_code": _FIX, "baseline_errors": 1}
    _p.update(kw)
    return _p


def test_reprobe_true_pass_sets_true(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _p = _patch()
    _r = _pm(tmp_path)._m448_sync_problem_gone(_p)
    assert _r["problem_gone"] is True and _r["source"] == "reprobe", _r
    assert _r["verdict"] == "true_pass", _r
    assert _p["problem_gone"] is True, _p
    assert _p["problem_gone_source"] == "reprobe", _p
    assert (_p.get("reprobe_baseline_hits") or 0) >= 1, _p


def test_reprobe_false_pass_stays_none(tmp_path, monkeypatch):
    """复现不出 ≠ 修好了：保持 None，八闸⑤继续拒。"""
    _wire(tmp_path, monkeypatch)
    _p = _patch(original_code=_FIX, modified_code=_FIX)
    _r = _pm(tmp_path)._m448_sync_problem_gone(_p)
    assert _r["problem_gone"] is None and _r["verdict"] == "false_pass", _r
    assert "problem_gone" not in _p or _p["problem_gone"] is None, _p


def test_existing_bool_not_overwritten(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _p = _patch(problem_gone=False)
    _r = _pm(tmp_path)._m448_sync_problem_gone(_p)
    assert _r["source"] == "existing" and _r["problem_gone"] is False, _r
    assert _p["problem_gone"] is False, _p


def test_ledger_wins_over_reprobe(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _pmv = PM(str(tmp_path))
    os.makedirs(str(tmp_path / "data" / "evolution"), exist_ok=True)
    with open(str(tmp_path / "data" / "evolution" / "effect_verify.json"),
              "w", encoding="utf-8") as _f:
        json.dump({"verdicts": {"p448_pg_1": {
            "passed": True, "reason": "台账理由",
            "dims": {"problem_gone": True}}}}, _f)
    _p = _patch()
    _r = _pmv._m448_sync_problem_gone(_p)
    assert _r["source"] == "effect_verify" and _r["problem_gone"] is True, _r
    assert "台账理由" in _r["reason"], _r


def test_ledger_false_also_honored(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _pmv = PM(str(tmp_path))
    os.makedirs(str(tmp_path / "data" / "evolution"), exist_ok=True)
    with open(str(tmp_path / "data" / "evolution" / "effect_verify.json"),
              "w", encoding="utf-8") as _f:
        json.dump({"verdicts": {"p448_pg_1": {
            "passed": False, "reason": "还在",
            "dims": {"problem_gone": False}}}}, _f)
    _p = _patch()
    _r = _pmv._m448_sync_problem_gone(_p)
    assert _r["problem_gone"] is False and _p["problem_gone"] is False, _r


def test_missing_modified_code_is_none(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _p = _patch(modified_code="")
    _r = _pm(tmp_path)._m448_sync_problem_gone(_p)
    assert _r["problem_gone"] is None, _r


def test_trace_row_fields(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _pm(tmp_path)._m448_sync_problem_gone(_patch())
    _r = _rows(tmp_path)[0]
    for _k in ("patch_id", "problem_gone", "source", "reason", "reprobe_verdict"):
        assert _k in _r, (_k, _r)
    assert _r["patch_id"] == "p448_pg_1" and _r["source"] == "reprobe", _r
    assert _r["reprobe_verdict"] == "true_pass", _r


def test_switch_off(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, on=False)
    _p = _patch()
    _r = _pm(tmp_path)._m448_sync_problem_gone(_p)
    assert _r["changed"] is False and _r["problem_gone"] is None, _r
    assert _rows(tmp_path) == [], "开关关了还写留痕"


def test_bad_inputs_safe(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _pmv = _pm(tmp_path)
    assert _pmv._m448_sync_problem_gone(None)["changed"] is False
    assert _pmv._m448_sync_problem_gone({})["changed"] is False


def test_decision_reads_top_level_true(tmp_path, monkeypatch):
    """八闸⑤读的就是顶层字段：同步后必须从 FAIL 变 PASS。"""
    from nucleus.evolution import approval_bridge as AB
    _wire(tmp_path, monkeypatch)
    _p = _patch(risk_level="低", trust_score=80, confidence="high")
    _before = AB.decision(_p)["gates"]["problem_gone_true"]["ok"]
    _pm(tmp_path)._m448_sync_problem_gone(_p)
    _after = AB.decision(_p)["gates"]["problem_gone_true"]["ok"]
    assert _before is False and _after is True, (_before, _after)
