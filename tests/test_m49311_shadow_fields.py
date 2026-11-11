# -*- coding: utf-8 -*-
"""493.11 刀5-6：影子日志字段补齐 + 断网路径落影子 + call 边行号补齐。"""
import json
import os

import pytest

import config as _cfg
import nucleus.self_index.fastpath as _fp
import nucleus.self_index.module_graph as _mg
from nucleus.self_index.fastpath import (
    record_self_query_shadow, _guard_reason, _load_module_graph_cached,
    self_query_fastpath_explain)
from nucleus.self_index.offline_local import offline_layered_answer


@pytest.fixture(autouse=True)
def _fields_on(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49311_SHADOW_FIELDS", True, raising=False)
    monkeypatch.setattr(_cfg, "ENABLE_M49311_CALL_LINES", True, raising=False)
    monkeypatch.setattr(_cfg, "M4939_FORCE_OFFLINE_LOCAL", True, raising=False)


NEW_FIELDS = ["session_id", "proc_gen", "source_path", "guard_reason",
              "error_reason", "expect_organ", "labeled_correct"]


# ───────────── 刀5 影子字段 ─────────────
def test_shadow_new_fields(tmp_path, monkeypatch):
    _p = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(_fp, "_SHADOW_PATH_OVERRIDE", str(_p), raising=False)
    # 用真实路径 + 备份还原（record 用模块常量路径）
    _real = os.path.join("data", "monitor", "self_query_shadow.jsonl")
    _bak = open(_real, encoding="utf-8").read() if os.path.isfile(_real) else None
    if os.path.isfile(_real):
        os.remove(_real)
    try:
        record_self_query_shadow(
            "胃在哪",
            {"answer": "胃 在 X", "action_id": "organ.location",
             "organ": "胃", "confidence": 0.9},
            session_id="S1", source_path="cortex")
        _row = json.loads(open(_real, encoding="utf-8").read().strip().splitlines()[-1])
        for _k in NEW_FIELDS:
            assert _k in _row, _k
        assert _row["session_id"] == "S1"
        assert _row["source_path"] == "cortex"
        assert isinstance(_row["proc_gen"], (int, float))
        assert _row["expect_organ"] is None and _row["labeled_correct"] is None
    finally:
        if _bak is None:
            if os.path.isfile(_real):
                os.remove(_real)
        else:
            open(_real, "w", encoding="utf-8").write(_bak)


def test_shadow_no_answer_must_have_reason():
    """would_answer=null 的行必须带 guard_reason 或 error_reason，不准静默空。"""
    _real = os.path.join("data", "monitor", "self_query_shadow.jsonl")
    _bak = open(_real, encoding="utf-8").read() if os.path.isfile(_real) else None
    if os.path.isfile(_real):
        os.remove(_real)
    try:
        record_self_query_shadow(
            "帮我看看这个报错", {"answer": None, "skipped_by_guard": True},
            session_id="S1", source_path="cortex", guard_reason="m4937_guard")
        record_self_query_shadow(
            "随便一句无自我指称", {"answer": None},
            session_id="S2", source_path="test")
        _rows = [json.loads(x) for x in open(_real, encoding="utf-8").read().strip().splitlines()]
        for _r in _rows:
            assert _r["would_answer"] is None
            assert _r.get("guard_reason") or _r.get("error_reason"), _r
        assert _rows[0]["guard_reason"] == "m4937_guard"
        assert _rows[1]["error_reason"] == "fastpath_no_match"
    finally:
        if _bak is None:
            if os.path.isfile(_real):
                os.remove(_real)
        else:
            open(_real, "w", encoding="utf-8").write(_bak)


def test_guard_reason_detection():
    assert _guard_reason("x" * 100) == "long_text_or_newline"
    assert _guard_reason("比较一下A和B的区别") == "causal_or_judgment_word"
    assert _guard_reason("https://example.com") == "code_or_url_or_timestamp"
    assert _guard_reason("胃在哪") is None


def test_offline_path_writes_shadow():
    """断网『胃在哪』必须产生 source_path=offline_local 的影子行。"""
    _real = os.path.join("data", "monitor", "self_query_shadow.jsonl")
    _bak = open(_real, encoding="utf-8").read() if os.path.isfile(_real) else None
    if os.path.isfile(_real):
        os.remove(_real)
    try:
        _r = offline_layered_answer("胃在哪")
        assert _r.get("layer") == "L0" and _r.get("answer")
        assert os.path.isfile(_real)
        _rows = [json.loads(x) for x in open(_real, encoding="utf-8").read().strip().splitlines()]
        assert any(x.get("source_path") == "offline_local" for x in _rows)
    finally:
        if _bak is None:
            if os.path.isfile(_real):
                os.remove(_real)
        else:
            open(_real, "w", encoding="utf-8").write(_bak)


def test_shadow_fields_off_reverts(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49311_SHADOW_FIELDS", False, raising=False)
    _real = os.path.join("data", "monitor", "self_query_shadow.jsonl")
    _bak = open(_real, encoding="utf-8").read() if os.path.isfile(_real) else None
    if os.path.isfile(_real):
        os.remove(_real)
    try:
        record_self_query_shadow("胃在哪", {"answer": "x"})
        _row = json.loads(open(_real, encoding="utf-8").read().strip())
        assert "session_id" not in _row and "proc_gen" not in _row
    finally:
        if _bak is None:
            if os.path.isfile(_real):
                os.remove(_real)
        else:
            open(_real, "w", encoding="utf-8").write(_bak)


# ───────────── 刀6 call 边行号 ─────────────
def test_inject_map_paths_fixed():
    """6 条错路径修正后，模块名对应的文件必须真实存在。"""
    for _attr, _mod in _mg.INJECT_MAP.items():
        _f = _mod.replace(".", "/") + ".py"
        assert os.path.isfile(_f), f"{_attr} -> {_f} 不存在"


def test_call_edges_to_line_enriched():
    _g = _load_module_graph_cached()
    _calls = [e for e in _g["edges"] if e.get("kind") == "call"]
    _null = [e for e in _calls if e.get("to_line") is None]
    assert len(_calls) == 172
    assert len(_null) <= 12, f"to_line 未解析仍 {len(_null)} 条"
    assert all(e.get("reason") == "no_call_graph_node" for e in _null)
    assert all(e.get("method") for e in _calls if e.get("to_line") is not None)


def test_import_inject_not_enriched():
    """import / inject 边绝不补 to_line（模块级/装配级无语义）。"""
    _g = _load_module_graph_cached()
    for _kind in ("import", "inject"):
        _es = [e for e in _g["edges"] if e.get("kind") == _kind]
        assert _es
        assert all(e.get("to_line") is None for e in _es), _kind


def test_enrich_is_honest_not_fabricated(monkeypatch):
    """索引为空时绝不编行号，全部写 reason。"""
    monkeypatch.setattr(_mg, "_load_call_graph_index", lambda: {})
    _edges = [{"kind": "call", "from": "A", "to": "nucleus.pulse.PulseCore",
               "via": "pulse_core", "method": "emit",
               "from_file": "x.py", "from_line": 1,
               "to_file": None, "to_line": None}]
    _s = _mg.enrich_call_edges(_edges)
    assert _s["resolved"] == 0 and _s["unresolved"] == 1
    assert _edges[0]["to_line"] is None
    assert _edges[0]["reason"] == "no_call_graph_node"


def test_call_lines_off_reverts(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49311_CALL_LINES", False, raising=False)
    _edges = [{"kind": "call", "to": "nucleus.pulse.PulseCore",
               "method": "emit", "to_line": None}]
    _s = _mg.enrich_call_edges(_edges)
    assert _s["call"] == 0
    assert "reason" not in _edges[0]


def test_render_call_edge_shows_real_line():
    """回答里 call 边显示真实行号；inject 边仍明说未解析。"""
    _ex = self_query_fastpath_explain("InfoField 和 PulseCore 怎么连")
    _a = str(_ex.get("answer"))
    assert "PulseCore.py:" in _a
    assert "目标行未解析" in _a       # inject 边
