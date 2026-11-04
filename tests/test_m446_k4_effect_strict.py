# -*- coding: utf-8 -*-
"""446 刀2：`passed=True` 只允许 `problem_gone=True`（判定 + 留痕 + 存量归一）。"""
import itertools
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.evolution.EvolutionEffectVerifier import (
    EvolutionEffectVerifier,
    normalize_strict_passed,
)


def _verifier(tmp_path):
    return EvolutionEffectVerifier(base_dir=str(tmp_path))


def test_passed_true_implies_problem_gone_true(tmp_path, monkeypatch):
    """★穷举三维 27 种组合：`passed=True` 必须蕴含 `problem_gone=True`。"""
    monkeypatch.setattr(config, "M446_EFFECT_VERIFY_TRACE_PATH",
                        str(tmp_path / "effect_verify_trace.jsonl"), raising=True)
    _v = _verifier(tmp_path)
    _bad = []
    for _pg, _fn, _perf in itertools.product((None, True, False),
                                             (None, True, False),
                                             (None, 0.0, 9000.0)):
        _r = _v.verify({"id": f"p_{_pg}", "file": "f.py", "method": "m"},
                       {"type": "silent_exception"},
                       problem_gone=_pg, function_ok=_fn, perf_delta_ms=_perf)
        if _r.get("passed") is True and _r["dims"]["problem_gone"] is not True:
            _bad.append(_r)
    assert _bad == [], _bad


def test_verify_records_trace_row(tmp_path, monkeypatch):
    _p = tmp_path / "effect_verify_trace.jsonl"
    monkeypatch.setattr(config, "M446_EFFECT_VERIFY_TRACE_PATH", str(_p), raising=True)
    monkeypatch.setattr(config, "ENABLE_M446_EFFECT_VERIFY_TRACE", True, raising=True)
    _v = _verifier(tmp_path)
    _r = _v.verify({"id": "patch_446_t1", "file": "organs/x.py", "method": "m",
                    "baseline_errors": 2, "baseline_source": "code_repro"},
                   {"type": "silent_exception"},
                   problem_gone=True, function_ok=True, perf_delta_ms=1.0)
    assert _r["passed"] is True, _r
    _rows = [json.loads(x) for x in _p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(_rows) == 1, _rows
    _row = _rows[0]
    assert _row["patch_id"] == "patch_446_t1", _row
    assert _row["verdict"] is True and _row["problem_gone"] is True, _row
    assert _row["baseline_errors"] == 2, _row
    assert _row["baseline_source"] == "code_repro", _row
    assert _row["reason"], _row


def test_verify_trace_records_undecidable_too(tmp_path, monkeypatch):
    """未判定的也要记 —— "没判出来"本身就是读数。"""
    _p = tmp_path / "effect_verify_trace.jsonl"
    monkeypatch.setattr(config, "M446_EFFECT_VERIFY_TRACE_PATH", str(_p), raising=True)
    _v = _verifier(tmp_path)
    _v.verify({"id": "patch_446_t2", "file": "f.py"}, None,
              problem_gone=None, function_ok=None, perf_delta_ms=None)
    _rows = [json.loads(x) for x in _p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(_rows) == 1 and _rows[0]["verdict"] is None, _rows


def test_verify_trace_switch_off(tmp_path, monkeypatch):
    _p = tmp_path / "effect_verify_trace.jsonl"
    monkeypatch.setattr(config, "M446_EFFECT_VERIFY_TRACE_PATH", str(_p), raising=True)
    monkeypatch.setattr(config, "ENABLE_M446_EFFECT_VERIFY_TRACE", False, raising=True)
    _verifier(tmp_path).verify({"id": "p", "file": "f.py"}, None,
                               problem_gone=True, function_ok=True, perf_delta_ms=0.0)
    assert not _p.exists(), "开关关了还写盘"


def test_normalize_demotes_legacy_self_certified():
    """存量 20 条的样子：passed=True 但 problem_gone=None ⇒ 降 None 且留原值。"""
    _vs = {
        "a": {"passed": True, "dims": {"problem_gone": None}, "reason": "旧口径"},
        "b": {"passed": True, "dims": {"problem_gone": True}, "reason": "ok"},
        "c": {"passed": None, "dims": {"problem_gone": None}, "reason": "x"},
    }
    _r = normalize_strict_passed(_vs)
    assert _r == {"total": 3, "normalized": 1, "kept": 2, "skipped": 0}, _r
    assert _vs["a"]["passed"] is None and _vs["a"]["passed_raw"] is True, _vs["a"]
    assert _vs["a"]["legacy_self_certified"] is True, _vs["a"]
    assert "旧口径" in _vs["a"]["reason"], _vs["a"]
    assert _vs["b"]["passed"] is True and "passed_raw" not in _vs["b"], _vs["b"]


def test_normalize_is_idempotent():
    _vs = {"a": {"passed": True, "dims": {"problem_gone": None}}}
    normalize_strict_passed(_vs)
    _r2 = normalize_strict_passed(_vs)
    assert _r2["normalized"] == 0, _r2
    assert _vs["a"]["passed_raw"] is True, _vs["a"]


def test_normalize_skips_non_dict():
    _vs = {"a": "oops"}
    assert normalize_strict_passed(_vs)["skipped"] == 1
