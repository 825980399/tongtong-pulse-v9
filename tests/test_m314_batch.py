# -*- coding: utf-8 -*-
"""第314批 交付自测：掌握率唯一取数口（刀1）+ get_current 语义注释（刀2）。

全部 tmp_path / 内存桩隔离：不读日志、不写生产 data/、不启器官。
"""

from pathlib import Path

import nucleus.code_learning_stats as cls
from nucleus.code_learning_stats import get_code_learning_stats
from nucleus.self_awareness import daily_reader


class _PulseInfoField:
    """`InfoField.get_current` 的桩：返回**脉冲记录**（不是 stats 本体）。"""

    def __init__(self, pulse):
        self._pulse = pulse

    def get_current(self, key):
        return self._pulse


class _EmptyInfoField:
    def get_current(self, key):
        return None


class _FakeLearner:
    def __init__(self, understood, total):
        self._u = understood
        self._t = total

    def get_stats(self):
        return {"understood": self._u, "total_methods": self._t,
                "organ": "代码学习", "is_running": True}


# ------------------------------------------------------------------ 刀1

def test_m314_reads_understood_from_pulse_payload():
    """兼容口径：脉冲记录里嵌 payload（get_current 的真实返回形状）。"""
    _if = _PulseInfoField({"event_type": "code_learner.stats",
                           "payload": {"understood": 6677, "total_methods": 8000}})
    _r = get_code_learning_stats(info_field=_if, organs={})
    assert _r["source"] == "info_field.code_learner.stats"
    assert _r["understood"] == 6677 and _r["total_methods"] == 8000
    assert _r["mastery_pct"] == 83.5 and _r["gap"] is None


def test_m314_falls_back_to_learner_get_stats():
    """取不到就直调真实生产者 PulseCodeLearner.get_stats()。"""
    _r = get_code_learning_stats(info_field=_EmptyInfoField(),
                                 organs={"代码学习": _FakeLearner(6677, 1418)})
    assert _r["source"] == "code_learner.get_stats"
    assert _r["learner_found"] is True
    assert _r["understood"] == 6677 and _r["mastery_pct"] == 100.0  # 钳到 100


def test_m314_no_producer_reports_gap_not_zero():
    """两路都取不到 ⇒ 结构缺口（understood=None），绝不折算成 0。"""
    _r = get_code_learning_stats(info_field=_EmptyInfoField(), organs={})
    assert _r["source"] == "unavailable"
    assert _r["understood"] is None
    assert _r["gap"] and "no_producer" in _r["gap"]


def test_m314_accepts_object_organs_by_attr_name():
    class _Organs:
        code_learner = _FakeLearner(10, 20)

    _r = get_code_learning_stats(info_field=_EmptyInfoField(), organs=_Organs())
    assert _r["source"] == "code_learner.get_stats"
    assert _r["mastery_pct"] == 50.0


def test_m314_mastery_section_prints_numerator_source(tmp_path, monkeypatch):
    import nucleus.self_inspector as _si

    class _Insp:
        def get_organ_code_structure(self):
            return {"PulseLiver": {"method_count": 1418}}

    monkeypatch.setattr(_si, "get_self_inspector", lambda: _Insp())
    monkeypatch.setattr(cls, "get_code_learning_stats", lambda **kw: {
        "understood": 123, "total_methods": 1418, "mastery_pct": 8.7,
        "source": "code_learner.get_stats", "gap": None, "learner_found": True})

    _rp = tmp_path / "report.txt"
    _rp.write_text("", encoding="utf-8")
    _res = daily_reader.append_mastery_section(str(_rp))
    _txt = _rp.read_text(encoding="utf-8")

    assert "已理解 123/1418 个方法（掌握率 8.7%）" in _txt
    assert "numerator_source=code_learner.get_stats" in _txt
    assert _res["understood"] == 123 and _res["source"] == "code_learner.get_stats"


def test_m314_mastery_section_marks_structural_gap(tmp_path, monkeypatch):
    import nucleus.self_inspector as _si

    class _Insp:
        def get_organ_code_structure(self):
            return {"PulseLiver": {"method_count": 1418}}

    monkeypatch.setattr(_si, "get_self_inspector", lambda: _Insp())
    monkeypatch.setattr(cls, "get_code_learning_stats", lambda **kw: {
        "understood": None, "total_methods": 1418, "mastery_pct": None,
        "source": "unavailable", "gap": "no_producer(x)", "learner_found": False})

    _rp = tmp_path / "r2.txt"
    _rp.write_text("", encoding="utf-8")
    _res = daily_reader.append_mastery_section(str(_rp))
    _txt = _rp.read_text(encoding="utf-8")

    assert "结构性缺口" in _txt and "no_producer" in _txt
    assert "掌握率 0.0%" not in _txt
    assert _res["numerator_source"] == "unavailable"


# ------------------------------------------------------------------ 刀2

def test_m314_get_current_docstring_declares_history_semantics():
    from nucleus.field.InfoField import InfoField
    _doc = InfoField.get_current.__doc__ or ""
    assert "脉冲历史" in _doc
    assert "5_000_000_000" in _doc or "5 秒" in _doc
    assert "payload" in _doc
    assert "不是" in _doc


def test_m314_313_knives_still_present():
    """314 刀3/刀4/刀5 = 313 三刀收编：确认源码在位（防回退）。"""
    _root = Path(__file__).resolve().parent.parent
    assert "append_global_awareness_section" in (
        _root / "nucleus/self_awareness/daily_reader.py").read_text(encoding="utf-8")
    assert "quarantine_invalid_graphs" in (
        _root / "nucleus/data/call_graph_guard.py").read_text(encoding="utf-8")
    assert "_m313_selfcheck_items" in (
        _root / "nucleus/exploration_audit.py").read_text(encoding="utf-8")
