# -*- coding: utf-8 -*-
"""444 刀3：经验库去重工具（只删「内容完全相同」的重复，保留首条）。"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

sys.path.insert(0, os.path.join(_ROOT, "tools"))
import exp_dedup_444 as dd


def _write(path, items):
    with open(path, "w", encoding="utf-8", newline="\n") as _f:
        json.dump({"experiences": items}, _f, ensure_ascii=False)


def _read(path):
    with open(path, encoding="utf-8") as _f:
        return json.load(_f)


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    _p = tmp_path / "exp.json"
    _write(str(_p), [{"summary": "A", "example_question": "q1"},
                     {"summary": "A", "example_question": "q1"},
                     {"summary": "B", "example_question": "q2"}])
    _before = _p.read_bytes()
    monkeypatch.setattr(dd, "_ROOT", str(tmp_path), raising=True)
    _r = dd.dedup_file("exp.json", apply=False)
    assert _r["before"] == 3 and _r["after"] == 2, _r
    assert _r["dup_removed"] == 1, _r
    assert _p.read_bytes() == _before, "dry-run 竟然写了盘"


def test_apply_keeps_first_and_backs_up(tmp_path, monkeypatch):
    _p = tmp_path / "exp.json"
    _write(str(_p), [{"summary": "A", "example_question": "q1", "tag": "keep"},
                     {"summary": "A", "example_question": "q1", "tag": "dup"},
                     {"summary": "B", "example_question": "q2"}])
    monkeypatch.setattr(dd, "_ROOT", str(tmp_path), raising=True)
    _r = dd.dedup_file("exp.json", apply=True)
    assert _r["written"] is True, _r
    _d = _read(str(_p))
    _items = _d["experiences"]
    assert len(_items) == 2, _items
    assert _items[0]["tag"] == "keep", "留下的不是第一条"
    assert os.path.exists(str(_p) + ".bak_m444_dedup"), "没留备份"


def test_drop_test_keywords_removes_placeholder(tmp_path, monkeypatch):
    _p = tmp_path / "exp.json"
    _write(str(_p), [{"summary": "这是一条测试数据", "example_question": "q"},
                     {"summary": "真实经验", "example_question": "q2"}])
    monkeypatch.setattr(dd, "_ROOT", str(tmp_path), raising=True)
    _r = dd.dedup_file("exp.json", apply=True, drop_test_keywords=True)
    assert _r["test_removed"] == 1, _r
    assert len(_read(str(_p))["experiences"]) == 1


def test_missing_file_is_reported_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "_ROOT", str(tmp_path), raising=True)
    _r = dd.dedup_file("nope.json", apply=True)
    assert _r["exists"] is False and _r["written"] is False
