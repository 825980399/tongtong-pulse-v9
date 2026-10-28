# -*- coding: utf-8 -*-
"""376 刀3 单测：线程落差快照埋点（观察票 · P2 · 票 T-376-线程落差栈-1）。

【票面要什么】跨夜 131 个连续采样点只报「落差 94-97、滞留快照 0 条」——没说**是谁**。
要求在 watchdog 埋点处补 OS 线程名快照：落差出现时 dump `threading.enumerate()`
的 name/ident，找出没被 Python 登记的那批线程是谁起的。

【本件钉七件事】
① Python 侧清单字段齐（name/ident/daemon/dummy），且 `_DummyThread` 认得出来；
② OS 侧 tid 集合差 = 没被登记那批的**确切 tid**；**拿不到 ⇒ None + unavailable，
   不是 0**（铁律276：拿不到与真是 0 必须分开）；
③ 落差快照 schema／`native_only≈gap` 的**域自检位**（不同源时标出来，不猜）；
④ 限流三触发（首告警／再涨≥5／每 12 拍）与「其余不抓」，且 131 拍不会刷屏；
⑤ 快照有界、摘要在抓之前是 None（不是 0）、复位可清；
⑥ `python_side_threads` **不许持 `_LOCK`** 调 enumerate（观测面不能变成新卡点）；
⑦ **只在已判 warn 的分支调用**（不改判据、不改采样间隔），且 374 的
   `next_gap_state` 语义一字未动（行为级回归，不钉历史快照）。
"""
import ast
import os
import threading
import time

import pytest

import nucleus.thread_guard as tg

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GUARD = os.path.join(_ROOT, "nucleus", "thread_guard.py")
_LIVER = os.path.join(_ROOT, "organs", "body", "PulseLiver.py")


@pytest.fixture(autouse=True)
def _clean():
    tg.reset_gap_snapshots()
    yield
    tg.reset_gap_snapshots()


def _src(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


# ==================================================== ① Python 侧清单
def test_01_python_side_rows_have_required_fields():
    rows = tg.python_side_threads()
    assert isinstance(rows, list) and rows, "enumerate() 至少应含当前线程"
    for r in rows:
        assert set(r) == {"name", "ident", "daemon", "dummy"}
        assert isinstance(r["name"], str)
        assert isinstance(r["daemon"], bool) and isinstance(r["dummy"], bool)
    _me = [r for r in rows if r["ident"] == threading.get_ident()]
    assert _me, "当前线程必须出现在清单里"


def test_02_dummy_thread_is_recognised():
    """裸 `_thread.start_new_thread` 起的线程进入 Python 后登记为 `_DummyThread`。"""
    import _thread

    _box = {}
    _go = threading.Event()

    def _worker():
        _box["t"] = threading.current_thread()   # 触发 _DummyThread 登记
        _go.wait(3.0)

    _thread.start_new_thread(_worker, ())
    _deadline = time.time() + 3.0
    _hit = []
    while time.time() < _deadline:
        _hit = [r for r in tg.python_side_threads() if r["dummy"]]
        if _hit:
            break
        time.sleep(0.02)
    _go.set()
    assert _hit, "裸线程没被认成 _DummyThread"
    assert isinstance(_box.get("t"), threading.Thread)
    assert tg.name_histogram(tg.python_side_threads()).get(
        _hit[0]["name"], 0) >= 1


def test_03_name_histogram_counts_and_empty():
    rows = [{"name": "a"}, {"name": "a"}, {"name": "b"}, {"name": ""}, {}]
    assert tg.name_histogram(rows) == {"a": 2, "b": 1, "": 2}
    assert tg.name_histogram([]) == {}
    assert tg.name_histogram(None) == {}


# ==================================================== ② OS 侧集合差
def test_04_native_only_unavailable_is_not_zero():
    r = tg.native_only_threads(None, [1, 2, 3])
    assert r["state"] == "unavailable"
    assert r["count"] is None, "拿不到必须 None，不许填 0"
    assert r["tids"] == [] and r["rows"] == []
    assert r["os_total"] is None and r["py_total"] is None


def test_05_native_only_is_exact_set_difference():
    os_tids = [{"id": 11, "user": 0.0, "sys": 0.0},
               {"id": 12, "user": 0.5, "sys": 0.5},
               {"id": 13, "user": 1.0, "sys": 0.0}]
    r = tg.native_only_threads(os_tids, [12, 13, None, "x"])
    assert r["state"] == "ok"
    assert r["tids"] == [11], "应只差出 11"
    assert r["count"] == 1
    assert r["os_total"] == 3 and r["py_total"] == 2, "非 int 的 ident 不计入分母"


def test_06_native_only_rows_sorted_by_cpu_and_bounded():
    os_tids = [{"id": i, "user": i * 0.01, "sys": 0.0} for i in range(40)]
    r = tg.native_only_threads(os_tids, [])
    assert r["count"] == 40
    assert len(r["rows"]) == 32, "rows 必须有界（快照不能无限大）"
    assert r["rows"][0]["id"] == 39, "按 CPU 时间降序 ⇒ 最忙的排最前"


def test_07_os_tids_none_proc_returns_none():
    assert tg._os_tids(None) is None, "没有 proc 就是取不到，不许造空列表"


# ==================================================== ③ 快照与域自检
def test_08_build_snapshot_schema_and_boundary():
    snap = tg.build_gap_snapshot(7, 100, [{"id": 1, "user": 0.0, "sys": 0.0}],
                                 py_threads=[{"name": "主", "ident": 9,
                                              "daemon": False, "dummy": False}],
                                 now=1234.5)
    assert snap["schema"] == tg.GAP_SNAP_SCHEMA
    assert snap["gap"] == 7 and snap["os_threads"] == 100
    assert snap["at"] == 1234.5
    assert snap["py_names"] == {"主": 1}
    assert snap["py_dummy"] == 0
    assert snap["native_only"]["tids"] == [1]
    assert "不编造" in snap["boundary"], "能力边界必须随数据一起给出"


def test_09_new_tids_diff_only_against_prev():
    _tids = [{"id": 1, "user": 0.0, "sys": 0.0},
             {"id": 2, "user": 0.0, "sys": 0.0},
             {"id": 3, "user": 0.0, "sys": 0.0}]
    _py = [{"name": "x", "ident": 1, "daemon": False, "dummy": False}]
    a = tg.build_gap_snapshot(2, 4, _tids, py_threads=_py, prev_tids=[2])
    assert a["new_tids"] == [3], "相对上一拍只新增 3"
    b = tg.build_gap_snapshot(2, 4, _tids, py_threads=_py, prev_tids=None)
    assert b["new_tids"] == [], "无上一拍 ⇒ 不算新增（不假装全是新的）"


def test_10_ident_domain_selfcheck_flags_mismatch():
    """`native_only` 应≈gap；明显偏大 ⇒ 标出「ident 与 OS tid 不同源」。"""
    os_tids = [{"id": i, "user": 0.0, "sys": 0.0} for i in range(10)]
    bad = tg.build_gap_snapshot(3, 20, os_tids, py_threads=[])
    assert bad["native_only"]["count"] == 10
    assert bad["ident_domain_mismatch"] is True
    good = tg.build_gap_snapshot(10, 20, os_tids, py_threads=[])
    assert good["ident_domain_mismatch"] is False


def test_11_snapshot_with_unavailable_tids_keeps_none():
    snap = tg.build_gap_snapshot(5, 50, None, py_threads=[])
    assert snap["native_only"]["state"] == "unavailable"
    assert snap["native_only"]["count"] is None
    assert snap["ident_domain_mismatch"] is False, "count=None 时不得据它判错位"


# ==================================================== ④ 限流
def test_12_should_snapshot_three_triggers_and_silence():
    assert tg.should_snapshot(None, 90, 0) == (True, "first_warn")
    _prev = {"gap": 90}
    assert tg.should_snapshot(_prev, 95, 1) == (True, "jump_+5")
    assert tg.should_snapshot(_prev, 94, 3) == (False, ""), "涨 4 不触发"
    assert tg.should_snapshot(_prev, 90, tg.GAP_SNAP_EVERY_N)[0] is True
    assert tg.should_snapshot(_prev, 90, tg.GAP_SNAP_EVERY_N - 1) == (False, "")
    assert tg.should_snapshot(_prev, None, 0) == (False, ""), "gap 不可判不抓"


def test_13_first_warn_takes_snapshot_with_trigger():
    snap = tg.maybe_gap_snapshot(90, 190)
    assert snap is not None and snap["trigger"] == "first_warn"
    assert snap["gap"] == 90
    assert len(tg.last_gap_snapshots()) == 1


def test_14_same_gap_is_throttled_then_periodic():
    assert tg.maybe_gap_snapshot(90, 190) is not None
    _nones = 0
    for _ in range(tg.GAP_SNAP_EVERY_N):
        if tg.maybe_gap_snapshot(90, 190) is None:
            _nones += 1
    assert _nones == tg.GAP_SNAP_EVERY_N, "12 拍内一次都不该再抓"
    _snap = tg.maybe_gap_snapshot(90, 190)
    assert _snap is not None and _snap["trigger"].startswith("periodic")
    assert len(tg.last_gap_snapshots()) == 2


def test_15_jump_triggers_without_waiting_period():
    tg.maybe_gap_snapshot(90, 190)
    snap = tg.maybe_gap_snapshot(96, 196)
    assert snap is not None and snap["trigger"] == "jump_+6"
    assert len(tg.last_gap_snapshots()) == 2


def test_16_snapshots_are_bounded():
    _gap = 10
    for _ in range(tg.MAX_GAP_SNAPSHOTS + 4):
        _gap += tg.GAP_SNAP_MIN_JUMP
        tg.maybe_gap_snapshot(_gap, _gap + 100)
    _all = tg.last_gap_snapshots()
    assert len(_all) == tg.MAX_GAP_SNAPSHOTS, "快照必须滚动有界"
    assert max(x["gap"] for x in _all) == _gap, "保留的应是最新那批"


# ==================================================== ⑤ 摘要与复位
def test_17_summary_none_before_any_then_digest():
    assert tg.gap_snapshot_summary() is None, "没抓过必须是 None，不是 0/空 dict"
    tg.maybe_gap_snapshot(30, 130)
    _s = tg.gap_snapshot_summary()
    assert _s is not None
    assert _s["gap"] == 30 and _s["trigger"] == "first_warn"
    assert "native_state" in _s, "拿不到与真 0 必须两个字段分开"
    assert isinstance(_s["py_names_top"], list) and _s["py_names_top"]


def test_18_reset_clears_snapshots_and_counter():
    tg.maybe_gap_snapshot(90, 190)
    assert tg.last_gap_snapshots()
    tg.reset_gap_snapshots()
    assert tg.last_gap_snapshots() == []
    assert tg.gap_snapshot_summary() is None
    _snap = tg.maybe_gap_snapshot(90, 190)
    assert _snap["trigger"] == "first_warn", "复位后应重新从首告警起算"


# ==================================================== ⑥ 不持锁 enumerate
def test_19_python_side_does_not_hold_lock():
    """`python_side_threads` 里不许出现 `_LOCK`：enumerate 自己要拿 threading 锁。"""
    _tree = ast.parse(_src(_GUARD))
    _fn = next(n for n in ast.walk(_tree)
               if isinstance(n, ast.FunctionDef) and n.name == "python_side_threads")
    _names = {n.id for n in ast.walk(_fn) if isinstance(n, ast.Name)}
    _attrs = {n.attr for n in ast.walk(_fn) if isinstance(n, ast.Attribute)}
    assert "_LOCK" not in _names and "_LOCK" not in _attrs, (
        "观测面持锁调 enumerate ⇒ 与新建线程互等，自己变成卡点")


# ==================================================== ⑦ 埋点位置与回归
def _method_block(path, func_name):
    """按缩进切出某个方法体（返回行列表），够用且不依赖 AST 行号漂移。"""
    _lines = _src(path).replace("\r\n", "\n").split("\n")
    _i = next(i for i, x in enumerate(_lines) if x.startswith("    def " + func_name + "("))
    _body = []
    for _x in _lines[_i + 1:]:
        if _x.strip() and not _x.startswith("        "):
            break
        _body.append(_x)
    return _body


def test_20_snapshot_called_only_under_warn_branch():
    """不改判据：调用点必须落在 `if self._thread_gap_state.get("warn"):` 块内。"""
    _body = _method_block(_LIVER, "_sample_resource_usage")
    _i = next(i for i, x in enumerate(_body)
              if 'if self._thread_gap_state.get("warn")' in x)
    _indent = len(_body[_i]) - len(_body[_i].lstrip())
    _blk = []
    for _x in _body[_i + 1:]:
        if _x.strip() and (len(_x) - len(_x.lstrip())) <= _indent:
            break
        _blk.append(_x)
    _txt = "\n".join(_blk)
    assert "maybe_gap_snapshot(" in _txt, "快照没挂在 warn 分支里"
    _whole = "\n".join(_body)
    assert _whole.count("maybe_gap_snapshot(") == 1, (
        "调用点不止一处 ⇒ 可能被挪到无条件路径（就变成改判据了）")


def test_21_stats_exposes_snapshot_summary():
    _body = "\n".join(_method_block(_LIVER, "_resource_stats"))
    assert '"thread_gap_snapshot"' in _body, "面板拿不到快照"
    assert "gap_snapshot_summary()" in _body


def test_22_next_gap_state_semantics_unchanged():
    """374 刀9 的落差状态机语义（行为级回归，不钉字面快照）。"""
    assert tg.next_gap_state({}, 79) == {"base": 79, "persist": 0,
                                         "delta": 0, "warn": False}
    _s1 = tg.next_gap_state({"base": 79, "persist": 0}, 80)
    assert _s1["warn"] is False, "单点突增不告警"
    _s2 = tg.next_gap_state(_s1, 81)
    assert _s2["warn"] is True, "连续两点才告警"
    _drop = tg.next_gap_state(_s2, 79)
    assert _drop["persist"] == 0 and _drop["warn"] is False
    assert _drop["base"] == 79, "回落不改参照基线"
    _none = tg.next_gap_state(_s2, None)
    assert _none["delta"] is None and _none["warn"] is False
