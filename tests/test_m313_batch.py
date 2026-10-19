# -*- coding: utf-8 -*-
"""第313批 交付自测（三刀各一组，全部 tmp_path 隔离，**不读也不写生产目录**）。

刀1：日报输出全局认知**实际值**（status / gaps / 三源计数）；
刀2：写侧图污染守卫（合成图不落生产目录 / 存量合成图隔离 / 写盘守卫接线）；
刀3：per-item 路径归一一次性生效（file 与 dedup_key 不可能再分叉）。
"""

import json
import os

from nucleus.exploration_audit import (
    _m312_build_items,
    _m313_has_drive_path,
    _m313_scan_drive_paths,
    _m313_selfcheck_items,
)
from nucleus.self_awareness import daily_reader
from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
from nucleus.data.call_graph_guard import quarantine_invalid_graphs


# ------------------------------------------------------------------ 刀1

class _FakeInfoField:
    def __init__(self, snap):
        self._snap = snap

    def get_global_awareness(self):
        return dict(self._snap)


def _patch_info_field(monkeypatch, snap):
    import nucleus.field.InfoField as _if_mod
    monkeypatch.setattr(_if_mod, "get_info_field", lambda: _FakeInfoField(snap))


def test_m313_global_awareness_section_prints_status_and_gaps(tmp_path, monkeypatch):
    _patch_info_field(monkeypatch, {
        "status": "fresh",
        "source": "self_awareness.integrate_global_awareness",
        "as_of": 1_700_000_000.0,
        "stale_sec": 12.5,
        "learning_goal": {"goal": "搞懂调用图"},
        "exploration_clues": [{"a": 1}, {"b": 2}],
        "narrative_clues": ["因为x所以y"],
        "gaps": [],
    })
    _rp = tmp_path / "report_test.txt"
    _rp.write_text("", encoding="utf-8")
    _res = daily_reader.append_global_awareness_section(str(_rp))
    _txt = _rp.read_text(encoding="utf-8")

    assert "status=fresh" in _txt
    assert "gaps=[]（0 条）" in _txt
    assert "learning_goal=1 条 | exploration_clues=2 条 | narrative_clues=1 条" in _txt
    assert _res["status"] == "fresh" and _res["gaps_count"] == 0
    assert _res["learning_goal_count"] == 1


def test_m313_global_awareness_section_reports_gaps_values(tmp_path, monkeypatch):
    _patch_info_field(monkeypatch, {
        "status": "stale", "source": "x", "as_of": 1.0, "stale_sec": 7200.0,
        "learning_goal": None, "exploration_clues": [], "narrative_clues": [],
        "gaps": ["inner_world:missing"],
    })
    _rp = tmp_path / "r.txt"
    _rp.write_text("", encoding="utf-8")
    _res = daily_reader.append_global_awareness_section(str(_rp))
    _txt = _rp.read_text(encoding="utf-8")

    assert "status=stale" in _txt
    assert "inner_world:missing" in _txt and "（1 条）" in _txt
    assert _res["gaps_count"] == 1


def test_m313_global_awareness_section_unavailable_is_honest(tmp_path, monkeypatch):
    _patch_info_field(monkeypatch, {})
    _rp = tmp_path / "r2.txt"
    _rp.write_text("", encoding="utf-8")
    _res = daily_reader.append_global_awareness_section(str(_rp))
    _txt = _rp.read_text(encoding="utf-8")

    assert "status=unavailable" in _txt
    assert "gaps=unavailable" in _txt
    assert _res["status"] == "unavailable"


# ------------------------------------------------------------------ 刀2

def _synthetic_graph():
    return {"nodes": [{"id": "pkg.a.f%d" % _i, "file": "pkg/a.py"} for _i in range(12)],
            "edges": [], "stats": {"total_files": 4}}


def _valid_graph():
    _nodes = [{"id": "n%d" % _i, "file": "nucleus/m%d.py" % (_i % 40)} for _i in range(120)]
    return {"nodes": _nodes, "edges": [], "stats": {"total_files": 40}}


def test_m313_quarantine_invalid_graphs_moves_only_bad(tmp_path):
    _d = tmp_path / "call_graph"
    _d.mkdir()
    (_d / "graph_20260101_000000.json").write_text(
        json.dumps(_synthetic_graph()), encoding="utf-8")
    (_d / "graph_20260101_000001.json").write_text(
        json.dumps(_valid_graph()), encoding="utf-8")

    _out = quarantine_invalid_graphs(str(_d))

    assert _out["ok"] is True and _out["checked"] == 2
    assert [x["file"] for x in _out["quarantined"]] == ["graph_20260101_000000.json"]
    assert (_d / "_quarantine" / "graph_20260101_000000.json").is_file()
    assert (_d / "graph_20260101_000001.json").is_file()


def test_m313_dump_graph_snapshot_rejects_synthetic(tmp_path, monkeypatch):
    import sys
    _cg_mod = sys.modules["nucleus.self_awareness.CallGraphAnalyzer"]
    _root = tmp_path / "proj"
    _root.mkdir()
    monkeypatch.setattr(_cg_mod, "_project_root", lambda: str(_root))
    monkeypatch.setattr(_cg_mod, "_M313_PRUNED_DIRS", set())

    _cg = CallGraphAnalyzer(project_root=str(_root))
    _path = _cg.dump_graph_snapshot(_synthetic_graph())

    _d = _root / "data" / "call_graph"
    assert _path == ""
    assert not list(_d.glob("graph_*.json"))
    assert len(list((_d / "_quarantine").glob("graph_quarantined_*.json"))) == 1


def test_m313_dump_graph_snapshot_writes_valid_and_sweeps_history(tmp_path, monkeypatch):
    import sys
    _cg_mod = sys.modules["nucleus.self_awareness.CallGraphAnalyzer"]
    _root = tmp_path / "proj2"
    (_root / "data" / "call_graph").mkdir(parents=True)
    _hist = _root / "data" / "call_graph" / "graph_20251231_235959.json"
    _hist.write_text(json.dumps(_synthetic_graph()), encoding="utf-8")
    monkeypatch.setattr(_cg_mod, "_project_root", lambda: str(_root))
    monkeypatch.setattr(_cg_mod, "_M313_PRUNED_DIRS", set())

    _cg = CallGraphAnalyzer(project_root=str(_root))
    _path = _cg.dump_graph_snapshot(_valid_graph())

    _d = _root / "data" / "call_graph"
    assert _path != "" and os.path.isfile(_path)
    assert not _hist.exists()
    assert (_d / "_quarantine" / "graph_20251231_235959.json").is_file()
    assert len(list(_d.glob("graph_*.json"))) == 1


# ------------------------------------------------------------------ 刀3

def _mixed_items():
    return [
        {"type": "long_method", "severity": "medium",
         "file": "D:\\proj\\organs\\body\\PulseLiver.py", "line": 10,
         "description": "x"},
        {"type": "print_debug", "severity": "low",
         "file": "config.py", "line": 3, "description": "y"},
        {"type": "non_atomic_write", "severity": "medium",
         "file": "E:/other/place/deep/z.py", "line": 7, "description": "z"},
    ]


def test_m313_build_items_still_normalizes_file(tmp_path):
    _items = _m312_build_items(_mixed_items(), [], str(tmp_path))
    for _it in _items:
        assert not _m313_has_drive_path(_it["file"]), _it


def test_m313_selfcheck_rebuilds_dedup_key_without_mixing(tmp_path):
    _items = _m312_build_items(_mixed_items(), [], str(tmp_path))
    # 模拟 312 的遗留口径：file 相对、dedup_key 仍是绝对路径拼出来的
    _items[0]["dedup_key"] = "D:\\proj\\organs\\body\\PulseLiver.py:10:long_method"
    _items, _leaks = _m313_selfcheck_items(_items, str(tmp_path))

    assert _leaks == []
    for _it in _items:
        assert not _m313_has_drive_path(_it["file"])
        assert not _m313_has_drive_path(_it["dedup_key"])
        assert _it["dedup_key"].split(":")[0] == _it["file"] or ":" not in _it["file"]


def test_m313_scan_drive_paths_flags_residual(tmp_path):
    _payload = {"items": [{"file": "ok.py", "desc": "见 D:/proj/x.py"}]}
    _leaks = _m313_scan_drive_paths(_payload, "payload")
    assert len(_leaks) == 1 and "desc" in _leaks[0]["where"]


def test_m313_has_drive_path_ignores_url_scheme():
    assert _m313_has_drive_path("http://example.com/a") is False
    assert _m313_has_drive_path("D:/proj/a.py") is True
    assert _m313_has_drive_path("nucleus/data/a.py") is False
