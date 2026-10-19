# -*- coding: utf-8 -*-
"""第312批 回归测试：图污染守卫 / per-item 相对路径 / 留痕守卫。

全部走**临时目录**与**纯函数**，不读生产 `data/`、不写任何落盘产物（铁律151 隔离）。
"""
import json
import os

from nucleus.data.call_graph_guard import (MIN_FILES, MIN_NODES, judge_graph,
                                           select_latest_graph)
from nucleus.exploration_audit import _m312_build_items, _m312_rel_file
from nucleus.self_awareness.DailyScheduler import _m312_as_dict


def _big_graph():
    return {"nodes": [{"id": "m%d:f%d" % (i, i), "name": "f%d" % i,
                       "file": "nucleus/m%d.py" % (i % 20), "line": i + 1}
                      for i in range(MIN_NODES + 20)],
            "edges": [], "stats": {"total_files": MIN_FILES + 10}}


def _synth_graph():
    return {"nodes": [{"id": "pkg.a:a%d" % i, "name": "a%d" % i,
                       "file": "pkg/a.py", "line": i + 1} for i in range(12)],
            "edges": [{"caller": "pkg.a:a0", "callee": "pkg.a:a1"}],
            "stats": {"total_files": 4}}


def test_judge_graph_rejects_too_few_nodes():
    ok, why = judge_graph(_synth_graph(), "graph_20260101_000000.json")
    assert ok is False
    assert why.startswith("too_few_nodes")


def test_judge_graph_accepts_production_shape():
    ok, why = judge_graph(_big_graph(), "graph_20260101_000000.json")
    assert ok is True and why == "ok"


def test_select_latest_skips_synthetic_when_newest(tmp_path):
    d = tmp_path / "call_graph"
    d.mkdir()
    (d / "graph_20260101_000000.json").write_text(
        json.dumps(_big_graph(), ensure_ascii=False), encoding="utf-8")
    (d / "graph_20991231_235959.json").write_text(
        json.dumps(_synth_graph(), ensure_ascii=False), encoding="utf-8")
    pick = select_latest_graph(str(d))
    assert pick["ok"] is True
    assert os.path.basename(pick["path"]) == "graph_20260101_000000.json"
    assert pick["rejected"] and pick["rejected"][0]["reason"].startswith("too_few_nodes")


def test_select_latest_none_when_all_rejected(tmp_path):
    d = tmp_path / "call_graph"
    d.mkdir()
    (d / "graph_20260101_000000.json").write_text(
        json.dumps(_synth_graph()), encoding="utf-8")
    pick = select_latest_graph(str(d))
    assert pick["ok"] is False and pick["graph"] is None


def test_per_item_file_is_relative(tmp_path):
    root = str(tmp_path)
    items = _m312_build_items([
        {"type": "t", "severity": "low", "file": os.path.join(root, "a", "b.py"), "line": 3},
        {"type": "t2", "severity": "low", "file": "C:\\Windows\\Temp\\x.py", "line": 4},
    ], [], root)
    assert items[0]["file"] == "a/b.py"
    assert items[1]["file"] == "x.py"
    assert all(":" not in i["file"] for i in items)
    assert items[0]["dedup_key"] == "a/b.py:3:t"


def test_rel_file_keeps_relative_input():
    assert _m312_rel_file("organs/body/PulseLiver.py", "X:/root") == "organs/body/PulseLiver.py"
    assert _m312_rel_file("", "X:/root") == ""


def test_as_dict_guards_none():
    assert _m312_as_dict(None, "回扫") == {}
    assert _m312_as_dict({"changed": 1}, "回扫") == {"changed": 1}
    # 留痕行不得自抛：按原写法读两个键必须可读
    _r = _m312_as_dict(None, "回扫")
    assert (_r.get("changed"), _r.get("total")) == (None, None)
