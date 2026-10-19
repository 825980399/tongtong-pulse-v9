# -*- coding: utf-8 -*-
"""调用图消费守卫（主线第312批 刀1，P1）

问题
----
测试/临时验证会调用 `CallGraphAnalyzer.dump_graph_snapshot()` 落盘，而落盘目录是
**生产目录** `data/call_graph/`。312 批前置分析实测：目录里出现过两份 2,781B 的合成图
（`nodes 12 / edges 7 / stats.total_files=4`，`node.file` 全是 `pkg/a.py` 之类）。

消费侧原有实现一律 `sorted(glob)[-1]`（取最新一份，不看内容），只要合成图排在最后：
  * `self_inspector._m305_load_graph_index` ⇒ dead_code 读一张 4 文件的图，
    覆盖率与豁免集全部失真（**判据不可用却看不出来**）；
  * `patch_execution_dispatch.load_call_graph_cache` ⇒ 牵连闸起点定位不到，
    整池 `blast=None` 转人工（fail-closed 反而把闭环掐死）。

解法
----
本模块给出**唯一判据** :func:`judge_graph` 与**唯一选择器**
:func:`select_latest_graph`：从新到旧挑第一份「合格」的图，被拒的逐份留痕
（原因码 + 节点数 + 文件数）。

语义边界：守卫只回答「这份图能不能当判据用」，**不改图、不删图、不写盘**；
也不替代「测试不得写生产目录」这条纪律（那是写侧的事，本批不动写侧）。
"""
from __future__ import annotations

import glob
import json
import os

from nucleus._silent_except import silent_exc

#: 合格图的最小节点数（生产实测 6700+；合成夹具 12）
MIN_NODES = 100
#: 合格图的最小覆盖文件数（生产实测 300+；合成夹具 4）
MIN_FILES = 10
#: 合成夹具的包路径前缀（`pkg/a.py` / `pkg/b.py` …）
SYNTHETIC_FILE_PREFIXES = ("pkg/",)
#: 文件名里出现这些词即视为测试/夹具产物（`graph_test_*.json` / `graph_fixture*.json`）
_TEST_NAME_MARKERS = ("test", "fixture", "sample", "dummy")


def _norm(p: str) -> str:
    return str(p or "").replace("\\", "/").strip()


def judge_graph(graph: dict, path: str = "") -> tuple[bool, str]:
    """判定一份落盘调用图能否当**判据图**用。

    Args:
        graph: `data/call_graph/graph_*.json` 的内容（dict）。
        path: 该图的路径（仅用于判文件名标记 / 留痕；可空）。

    Returns:
        `(可用, 原因码)`。可用时原因码为 `"ok"`，否则形如
        `"too_few_nodes(12<100)"` / `"too_few_files(4<10)"` / `"synthetic_pkg"` /
        `"synthetic_name"` / `"no_nodes"` / `"not_dict"`。
    """
    if not isinstance(graph, dict):
        return False, "not_dict"
    _name = os.path.basename(str(path or "")).lower()
    for _mk in _TEST_NAME_MARKERS:
        if _mk in _name:
            return False, "synthetic_name(%s)" % _mk
    _nodes = graph.get("nodes") or []
    if not isinstance(_nodes, list) or not _nodes:
        return False, "no_nodes"
    _n = len(_nodes)
    if _n < MIN_NODES:
        return False, "too_few_nodes(%d<%d)" % (_n, MIN_NODES)
    _stats = graph.get("stats") or {}
    # 不用 try/except：这里只是读一个计数，非 int 就按 0（未知）处理——
    # 不留痕的 except 会被 cw2 判成「新增静默 handler」（铁律150）。
    _tf_raw = _stats.get("total_files", 0) if isinstance(_stats, dict) else 0
    _tf = _tf_raw if isinstance(_tf_raw, int) and not isinstance(_tf_raw, bool) else 0
    if _tf and _tf < MIN_FILES:
        return False, "too_few_files(%d<%d)" % (_tf, MIN_FILES)
    _files = set()
    for _nd in _nodes:
        if isinstance(_nd, dict):
            _f = _norm(_nd.get("file"))
            if _f:
                _files.add(_f)
    if _files and all(_f.startswith(SYNTHETIC_FILE_PREFIXES) for _f in _files):
        return False, "synthetic_pkg(%d files)" % len(_files)
    return True, "ok"


def select_latest_graph(graph_dir: str, pattern: str = "graph_*.json") -> dict:
    """从新到旧挑**第一份合格图**（被拒的逐份记录，供调用方留痕）。

    Args:
        graph_dir: 图目录（通常 `<project_root>/data/call_graph`）。
        pattern: 文件名通配。

    Returns:
        `{"ok": bool, "path": str, "graph": dict|None, "rejected": [{"file","reason"}]}`。
        目录不存在 / 无图 / 全部被拒 ⇒ `ok=False`（调用方按「判据不可用」处理，不得猜）。
    """
    _out: dict = {"ok": False, "path": "", "graph": None, "rejected": []}
    try:
        _files = sorted(glob.glob(os.path.join(str(graph_dir or ""), pattern)))
        if not _files:
            return _out
        for _p in reversed(_files):          # 新 → 旧
            try:
                with open(_p, encoding="utf-8") as _f:
                    _gd = json.load(_f)
            except (OSError, ValueError) as e:
                silent_exc(e, where="nucleus.data.call_graph_guard::select_latest_graph:load")
                _out["rejected"].append({"file": os.path.basename(_p), "reason": "bad_json"})
                continue
            _ok, _why = judge_graph(_gd, _p)
            if _ok:
                _out.update({"ok": True, "path": _p, "graph": _gd})
                return _out
            _out["rejected"].append({"file": os.path.basename(_p), "reason": _why})
        return _out
    except Exception as e:
        silent_exc(e, where="nucleus.data.call_graph_guard::select_latest_graph")
        return _out


def format_rejected(rejected: list) -> str:
    """把被拒清单压成一行日志文本（空清单返回空串）。"""
    if not rejected:
        return ""
    return ", ".join("%s[%s]" % (str(_r.get("file", "")), str(_r.get("reason", "")))
                     for _r in rejected if isinstance(_r, dict))

# ---------------------------------------------------------------- 313批刀2 写侧

#: 隔离目录名（位于图目录下）。`select_latest_graph` 的 `graph_*.json` 通配**不进子目录**，
#: 因此被隔离的图不会被下游误当判据；同时也不再占「滚动 7 份」的配额。
QUARANTINE_DIRNAME = "_quarantine"


def quarantine_invalid_graphs(graph_dir: str, logger=None) -> dict:
    """★第313批 刀2（写侧）：把目录里**不合格的图**移入 `_quarantine/`（移动，不删除）。

    背景：312 批只补了读侧守卫（`select_latest_graph` 不取合成图），写侧仍脏 ——
    实测 `pkg/a.py` 类合成图三次进生产目录，还盘踞在滚动 7 份里，
    于是一份 4 文件的假图会把一份 6700 节点的真图挤出窗口。

    语义边界：**只移动、不删除**（留证据，可人工复核）；判据仍由 :func:`judge_graph`
    单点提供，本函数不另立标准。移动失败只留痕并置 `ok=False`，绝不抛。

    Args:
        graph_dir: 图目录（通常 `<project_root>/data/call_graph`）。
        logger: 保留参数（本函数不自行打日志，由调用方聚合输出一行）。

    Returns:
        ``{"ok": bool, "checked": n, "quarantined": [{"file","reason"}], "quarantine_dir": str}``
    """
    _out: dict = {"ok": True, "checked": 0, "quarantined": [], "quarantine_dir": ""}
    try:
        if not graph_dir or not os.path.isdir(str(graph_dir)):
            return _out
        _dir = str(graph_dir)
        _qdir = os.path.join(_dir, QUARANTINE_DIRNAME)
        _out["quarantine_dir"] = _qdir
        for _p in sorted(glob.glob(os.path.join(_dir, "graph_*.json"))):
            _out["checked"] += 1
            _gd = None
            _why = ""
            try:
                with open(_p, encoding="utf-8") as _f:
                    _gd = json.load(_f)
            except (OSError, ValueError) as e:
                silent_exc(
                    e, where="nucleus.data.call_graph_guard::quarantine_invalid_graphs:load")
                _why = "bad_json"
            if not _why:
                _ok, _why = judge_graph(_gd, _p)
                if _ok:
                    continue
            try:
                if not os.path.isdir(_qdir):
                    os.makedirs(_qdir, exist_ok=True)
                _dst = os.path.join(_qdir, os.path.basename(_p))
                if os.path.exists(_dst):
                    _dst = _dst[:-5] + "_" + str(int(os.path.getmtime(_p))) + ".json"
                os.replace(_p, _dst)
                _out["quarantined"].append({"file": os.path.basename(_p), "reason": _why})
            except OSError as e:
                silent_exc(
                    e, where="nucleus.data.call_graph_guard::quarantine_invalid_graphs:move")
                _out["ok"] = False
    except Exception as e:
        silent_exc(e, where="nucleus.data.call_graph_guard::quarantine_invalid_graphs")
        _out["ok"] = False
    return _out

