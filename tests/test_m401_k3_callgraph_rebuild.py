# -*- coding: utf-8 -*-
"""★401批刀3（P2 / L1）：调用图常驻副本 + 未解析全量清单（判据单测）。

票面背景（烛微401 §L1）：排除表已由 397刀2 修好，但图快照仍停在旧时刻没重建；
`keep=7` 按小时轮转会冲掉历史对比基线。本文件守四件事：

  ① 覆盖数是**下限**判据（>= 409），不钉会随新增文件变化的精确值；
  ② `graph_latest.json` 是**常驻副本**：落盘必写、不占滚动 7 份配额、
     且**不被任何"按文件名取最新"的选择器误选**（'l' 排在时间戳之后是硬事实）；
  ③ 未解析清单有 `file:line:call_expr` 三元组，当日一份、按自然日保留 7 天；
  ④ 「是否被截断」写在首行 meta 里 —— 不许把"样本"当"全量"用。

★不测"活体上覆盖是否真的涨了"：那是重建工具跑一次 + 重启后核验项
（`python tools/m401_rebuild_call_graph.py` 的 PASS 行）。
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import nucleus.self_awareness.CallGraphAnalyzer  # noqa: E402,F401
import nucleus.self_awareness.unresolved_samples as _us  # noqa: E402
from nucleus.data.call_graph_guard import (  # noqa: E402
    quarantine_invalid_graphs,
    select_latest_graph,
)

_cga = sys.modules["nucleus.self_awareness.CallGraphAnalyzer"]
assert hasattr(_cga, "_iter_py_files"), "拿到的不是 CallGraphAnalyzer 模块（是类）"

_EXPECT_TOTAL = 409
_LATEST = "graph_latest.json"


def _graph(tag="v1", n_nodes=120, n_files=12) -> dict:
    """一份**合格**的合成图（judge_graph 要求 >=100 节点 / >=10 文件 / 非 pkg/ 包）。"""
    _nodes, _edges = [], []
    for _i in range(n_nodes):
        _f = "nucleus/m%02d.py" % (_i % n_files)
        _nodes.append({"id": "nucleus.m%02d:C.m%d" % (_i % n_files, _i),
                       "file": _f, "cls": "C", "name": "m%d" % _i,
                       "kind": "method", "line": _i + 1})
    for _i in range(n_nodes - 1):
        _edges.append({"src": _nodes[_i]["id"], "dst": _nodes[_i + 1]["id"],
                       "kind": "static", "line": _i + 1})
    return {"nodes": _nodes, "edges": _edges,
            "stats": {"total_files": n_files, "total_functions": n_nodes},
            "tag": tag, "dumped_at": time.time()}


def _write_graph(path, g) -> str:
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as _f:
        json.dump(g, _f, ensure_ascii=False)
    return str(path)


def _snapshots(d: str) -> list:
    """目录里的**时间戳快照**（不含常驻副本）。"""
    return sorted(f for f in os.listdir(d)
                  if f.startswith("graph_") and f.endswith(".json") and f != _LATEST)


# ------------------------------------------------------------ ① 覆盖下限
def test_01_coverage_floor():
    """票面验收：调用图覆盖 **>= 409** 个 .py（397刀2 交付读数，用下限不用等号）。"""
    _files = _cga._iter_py_files(_cga._project_root(), _cga._scan_dirs())
    assert len(_files) >= _EXPECT_TOTAL, \
        "覆盖 %d < %d（盲区没补齐）" % (len(_files), _EXPECT_TOTAL)


# ------------------------------------------------- ② 常驻副本：写了、不算配额
def test_02_dump_writes_snapshot_and_latest(tmp_path, monkeypatch):
    """落盘必写快照 + 常驻副本，两者**同内容同刻**（同一份 payload）。"""
    monkeypatch.setattr(_cga, "_project_root", lambda: str(tmp_path))
    _cg = _cga.CallGraphAnalyzer(project_root=str(tmp_path))

    _p = _cg.dump_graph_snapshot(_graph("v2"), keep_latest=True)

    assert _p and os.path.isfile(_p), "快照没落盘（写盘守卫拒写？）"
    _lp = os.path.join(str(tmp_path), "data", "call_graph", _LATEST)
    assert os.path.isfile(_lp), "常驻副本 graph_latest.json 没写"
    with open(_p, encoding="utf-8") as _f:
        _a = json.load(_f)
    with open(_lp, encoding="utf-8") as _f:
        _b = json.load(_f)
    assert _a.get("dumped_at") == _b.get("dumped_at"), "副本与快照不同源（不是同一份 payload）"
    assert _a.get("tag") == "v2"


def test_03_latest_not_in_rotation_quota(tmp_path, monkeypatch):
    """常驻副本**不占**滚动配额：keep=2 时目录里仍是 2 份时间戳快照 + 1 份副本。"""
    monkeypatch.setattr(_cga, "_project_root", lambda: str(tmp_path))
    _d = os.path.join(str(tmp_path), "data", "call_graph")
    os.makedirs(_d, exist_ok=True)
    for _i in range(5):
        _write_graph(os.path.join(_d, "graph_20200101_00000%d.json" % _i), _graph("old%d" % _i))

    _cg = _cga.CallGraphAnalyzer(project_root=str(tmp_path))
    _cg.dump_graph_snapshot(_graph("new"), keep=2, keep_latest=True)

    _left = _snapshots(_d)
    assert len(_left) == 2, "滚动份数不对：%s" % _left
    assert os.path.isfile(os.path.join(_d, _LATEST)), "常驻副本被滚动清掉了"


def test_04_no_latest_when_disabled(tmp_path, monkeypatch):
    """显式关闭 ⇒ 不写副本（灰度可退，不影响快照本体）。"""
    monkeypatch.setattr(_cga, "_project_root", lambda: str(tmp_path))
    _cg = _cga.CallGraphAnalyzer(project_root=str(tmp_path))

    _p = _cg.dump_graph_snapshot(_graph("v3"), keep_latest=False)

    assert _p and os.path.isfile(_p)
    assert not os.path.isfile(os.path.join(str(tmp_path), "data", "call_graph", _LATEST))


# ------------------------------------------- ③ 选择器不许误选派生副本（核心）
def test_05_selector_skips_derived_copy(tmp_path):
    """`select_latest_graph` 按**文件名**排序：'graph_latest' 排在所有时间戳之后，
    若不过滤就永远选中副本 —— 副本一旦落后就是"看不见的旧图"。"""
    _d = os.path.join(str(tmp_path), "cg")
    os.makedirs(_d, exist_ok=True)
    _write_graph(os.path.join(_d, "graph_20200101_000000.json"), _graph("old"))
    _write_graph(os.path.join(_d, _LATEST), _graph("copy"))

    _pick = select_latest_graph(_d)

    assert _pick["ok"] is True, "挑不到合格图：%s" % (_pick.get("rejected"),)
    assert os.path.basename(_pick["path"]) == "graph_20200101_000000.json", \
        "选中了派生副本：%s" % os.path.basename(_pick["path"])
    assert _pick["graph"]["tag"] == "old"


def test_06_quarantine_skips_derived_copy(tmp_path):
    """存量清扫也不碰副本（副本是派生物，由下一次落盘整体覆盖）。"""
    _d = os.path.join(str(tmp_path), "cg")
    os.makedirs(_d, exist_ok=True)
    _write_graph(os.path.join(_d, "graph_20200101_000000.json"), _graph("ok"))
    _write_graph(os.path.join(_d, _LATEST), {})          # 坏副本

    _out = quarantine_invalid_graphs(_d)

    assert _out["checked"] == 1, "把副本也算进候选了：checked=%d" % _out["checked"]
    assert _out["quarantined"] == [], "副本被隔离了（应由落盘覆盖，不该动它）"
    assert os.path.isfile(os.path.join(_d, _LATEST))


# --------------------------------------- ④ 未解析清单：三元组 / 当日 / 7 天
def test_07_full_list_has_triple(tmp_path):
    """`file:line:call_expr` 三个维度齐；`line` 缺失给 None，`call_expr` 由 parts 派生。"""
    _rows = [
        {"cat": "external", "parts": ["threading", "Lock"], "module": "m",
         "file": "a.py", "line": 12, "in_class": "C", "caller": "C.f"},
        {"cat": "external", "parts": [], "module": "m",
         "file": "b.py", "in_class": "", "caller": "g"},
    ]
    _p = _us.dump_samples(_rows, str(tmp_path), cap=300000)

    assert _p and os.path.isfile(_p), "小时快照没落盘（318 兼容口径）"
    _d = _us._dir_of(str(tmp_path))
    _full = [f for f in os.listdir(_d) if f.startswith(_us._FULL_PREFIX)]
    assert len(_full) == 1, "当日全量清单没生成：%s" % _full

    _back = _us.latest_samples(str(tmp_path))
    assert len(_back) == 2, "meta 行被当成样本了（应为 2 条）"
    assert _back[0]["call_expr"] == "threading.Lock"
    assert _back[0]["line"] == 12
    assert _back[1]["line"] is None, "缺行号填了 0（没测到 ≠ 第 0 行）"
    assert _back[1]["call_expr"].startswith("<dynamic:"), "动态派发没留标记"

    _meta = _us.latest_meta(str(tmp_path))
    assert _meta.get("rows") == 2
    assert _meta.get("truncated") is False
    assert _meta.get("_schema") == _us._SCHEMA


def test_08_truncated_flag_is_honest(tmp_path):
    """条数触到采集上限 ⇒ meta 必须标 truncated（"全量"这句话要能被证伪）。"""
    _rows = [{"cat": "external", "parts": ["x"], "file": "a.py", "line": 1} for _ in range(2)]
    _us.dump_samples(_rows, str(tmp_path), cap=2)
    assert _us.latest_meta(str(tmp_path)).get("truncated") is True


def test_09_roll_days_keeps_seven_days(tmp_path):
    """按自然日保留 7 天：第 8 天及更早的清单删掉；小时快照不受日滚动影响。"""
    _d = _us._dir_of(str(tmp_path))
    os.makedirs(_d, exist_ok=True)
    _now = time.time()
    _made = {}
    for _back in (0, 3, 6, 7, 30):
        _day = time.strftime("%Y%m%d", time.localtime(_now - _back * 86400))
        _name = "%s%s.jsonl" % (_us._FULL_PREFIX, _day)
        with open(os.path.join(_d, _name), "w", encoding="utf-8", newline="\n") as _f:
            _f.write("{}\n")
        _made[_back] = _name
    _snap = os.path.join(_d, "%s20200101_000000.jsonl" % _us._PREFIX)
    with open(_snap, "w", encoding="utf-8", newline="\n") as _f:
        _f.write("{}\n")

    _removed = _us._roll_days(_d)

    _left = {f for f in os.listdir(_d) if f.startswith(_us._FULL_PREFIX)}
    assert _left == {_made[_b] for _b in (0, 3, 6)}, "保留窗口不对：%s" % sorted(_left)
    assert set(_removed) == {_made[7], _made[30]}, "该删的没删：%s" % _removed
    assert os.path.isfile(_snap), "日滚动误删了小时快照"


def test_10_retention_constants_declared():
    """两套保留语义都在，且不互相顶替：快照 7 份（318 规格）/ 清单 7 天（401）。"""
    assert _us._KEEP == 7, "小时快照滚动份数被改动（318 规格）"
    assert _us._KEEP_DAYS == 7, "全量清单保留天数不是 7"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
