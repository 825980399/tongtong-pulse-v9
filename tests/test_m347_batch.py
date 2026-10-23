# -*- coding: utf-8 -*-
"""347 批回归：刀1 需求驱动占比口径 / 刀2 D3 去线程不变式。

全部自包含：不读生产 data/、不依赖现场读数、不改任何全局状态。
"""
import ast
import os
import sys

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _read(rel: str) -> str:
    return open(os.path.join(_REPO, rel.replace("/", os.sep)), encoding="utf-8").read()


def _tree(rel: str):
    return ast.parse(_read(rel))


def _find_func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


@pytest.fixture(scope="module")
def evo():
    import nucleus.evolution.EvolutionLoop  # noqa: F401
    return sys.modules["nucleus.evolution.EvolutionLoop"]


# ==================== 刀1 需求驱动占比 ====================


def test_d1_ratio_is_defined_once(evo):
    assert callable(evo._m347_growth_ratio)
    tree = _tree("nucleus/evolution/EvolutionLoop.py")
    defs = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_m347_growth_ratio"]
    assert len(defs) == 1, "占比口径必须只有一个定义（多定义=多真相）"


def test_d1_ratio_math(evo):
    assert evo._m347_growth_ratio({"growth_need": 3, "defect": 7})["ratio"] == pytest.approx(0.3)
    assert evo._m347_growth_ratio({"growth_need": 0, "defect": 5})["ratio"] == 0.0


@pytest.mark.parametrize("tc", [{}, None, {"growth_need": 0, "defect": 0}])
def test_d1_zero_denominator_is_none_not_zero(evo, tc):
    """分母 0 ⇒ None（不填 0.0）：『本拍无料』与『占比为 0』必须可区分。"""
    r = evo._m347_growth_ratio(tc)
    assert r["all"] == 0
    assert r["ratio"] is None


def test_d1_emit_writes_greppable_line(evo, caplog):
    with caplog.at_level("INFO", logger="EvolutionLoop"):
        r = evo._m347_emit_intake_ratio({"growth_need": 1, "defect": 3}, source="ut")
    txt = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "[347需求]" in txt
    assert "源=ut" in txt
    assert "占比=0.2500" in txt
    assert r["ratio"] == pytest.approx(0.25)


def test_d1_emit_says_unavailable_when_no_material(evo, caplog):
    with caplog.at_level("INFO", logger="EvolutionLoop"):
        evo._m347_emit_intake_ratio({}, source="ut")
    txt = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "占比=unavailable" in txt
    assert "占比=0.0000" not in txt


def test_d1_trigger_counts_computed_once():
    """同一事实不得有三份独立真相：_m347_tc 恰好 1 次赋值 + 3 次使用。"""
    tree = _tree("nucleus/evolution/EvolutionLoop.py")
    stores = loads = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id == "_m347_tc":
            if isinstance(n.ctx, ast.Store):
                stores += 1
            else:
                loads += 1
    assert stores == 1, "trigger_counts 必须只算一次，实得 %d" % stores
    # 3 个出口（台账/入料快照/返回值）+ 1 次留痕调用 = 4 次读
    assert loads == 4, "三出口 + 留痕都引用同一份，实得 %d" % loads


def test_d1_no_leftover_duplicate_literal():
    src = _read("nucleus/evolution/EvolutionLoop.py")
    assert src.count('"trigger_counts": {') == 0, "仍有重复的 trigger_counts 字面量"


def test_d1_surface_has_no_constant_empty_assignment():
    """需求源面『恒空赋值』0 残留（规则与 tmp/p347_root/d1_req_source.py 同源）。

    判别：局部名首次赋常量空值、此后无非空赋值、无就地写入、且参与
    return/f-string/拼接 或 出现在 if/while 条件里。
    """
    files = ["nucleus/evolution/EvolutionLoop.py",
             "nucleus/mnemosyne/verification_learning_hub.py",
             "nucleus/self_awareness/CallGraphAnalyzer.py",
             "organs/brain/PulseSubconscious.py"]
    tokens = ("growth_intake", "_on_growth_need", "intake_summary", "intake_journal",
              "_m316_", "_m325_", "_m326_", "a5_growth", "m347_")
    empty_vals = ("", b"", None, 0, 0.0, False)
    mutators = {"append", "appendleft", "extend", "extendleft", "update", "add",
                "setdefault", "insert", "pop", "discard", "clear", "popitem",
                "write", "writelines"}

    def _is_empty(node):
        if isinstance(node, ast.Constant):
            return node.value in empty_vals
        if isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
            return len(getattr(node, "keys", getattr(node, "elts", []))) == 0
        return False

    def _names(node):
        out = []

        def _w(t):
            if isinstance(t, ast.Name):
                out.append(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                for e in t.elts:
                    _w(e)

        if isinstance(node, ast.Assign):
            for t in node.targets:
                _w(t)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _w(node.target)
        return out

    hits = []
    for rel in files:
        tree = _tree(rel)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(t in fn.name for t in tokens):
                continue
            seq = {}
            for stmt in ast.walk(fn):
                if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
                    for nm in _names(stmt):
                        seq.setdefault(nm, []).append((stmt.lineno, _is_empty(stmt.value)))
            for nm, s in seq.items():
                if not s[0][1] or any(not e for _, e in s):
                    continue
                inplace = False
                for n in ast.walk(fn):
                    if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) \
                            and n.value.id == nm and isinstance(n.ctx, (ast.Store, ast.Del)):
                        inplace = True
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                            and isinstance(n.func.value, ast.Name) \
                            and n.func.value.id == nm and n.func.attr in mutators:
                        inplace = True
                if inplace:
                    continue
                used = False
                for n in ast.walk(fn):
                    if isinstance(n, ast.Return) and n.value is not None \
                            and any(isinstance(x, ast.Name) and x.id == nm for x in ast.walk(n.value)):
                        used = True
                    if isinstance(n, ast.JoinedStr) and any(
                            isinstance(x, ast.Name) and x.id == nm for x in ast.walk(n)):
                        used = True
                    if isinstance(n, ast.BinOp) and any(
                            isinstance(x, ast.Name) and x.id == nm for x in ast.walk(n)):
                        used = True
                    if isinstance(n, (ast.If, ast.While)) and any(
                            isinstance(x, ast.Name) and x.id == nm for x in ast.walk(n.test)):
                        used = True
                if used:
                    hits.append("%s:%d %s::%s" % (rel, s[0][0], fn.name, nm))
    assert hits == [], "恒空赋值残留: %s" % hits


# ==================== 刀2 D3 去线程 ====================


def test_d2_keepalive_thread_removed():
    tree = _tree("organs/motor/PulseController.py")
    fn = _find_func(tree, "_start_keepalive_timer")
    assert fn is not None
    for n in ast.walk(fn):
        assert not (isinstance(n, ast.Attribute) and n.attr == "Thread"), \
            "_start_keepalive_timer 内不得再创建线程"
    src = _read("organs/motor/PulseController.py")
    assert "_keepalive_thread" not in src
    assert "threading.Thread(target=_keepalive_loop" not in src


def test_d2_keepalive_tick_exists_and_is_driven_by_heartbeat():
    tree = _tree("organs/motor/PulseController.py")
    tick = _find_func(tree, "_keepalive_tick")
    assert tick is not None
    hb = _find_func(tree, "_on_heartbeat_pulse")
    assert hb is not None
    calls = [n for n in ast.walk(hb) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "_keepalive_tick"]
    assert calls, "心跳回调必须驱动保活节拍"


def test_d2_keepalive_tick_throttles():
    import organs.motor.PulseController as _mod  # noqa: F401
    mod = sys.modules["organs.motor.PulseController"]
    obj = mod.PulseController.__new__(mod.PulseController)
    obj.is_running = True
    obj._headless_browser = None
    obj._headless_page = None
    obj._log = lambda *a, **k: None
    obj._get_headless_config = lambda *a, **k: 0
    # 刚跑过（时间戳=现在）⇒ 节流期内不应推进时间戳
    import time as _t
    _now = _t.time()
    obj._keepalive_last_ts = _now
    obj._keepalive_tick()
    assert obj._keepalive_last_ts == _now, "节流期内不得重复执行"
    # 时间戳归零 ⇒ 应当执行并推进
    obj._keepalive_last_ts = 0.0
    obj._keepalive_tick()
    assert obj._keepalive_last_ts > 0.0, "超过 60s 应执行并推进时间戳"


def test_d2_keepalive_tick_noop_when_not_running():
    import organs.motor.PulseController as _mod  # noqa: F401
    mod = sys.modules["organs.motor.PulseController"]
    obj = mod.PulseController.__new__(mod.PulseController)
    obj.is_running = False
    obj._log = lambda *a, **k: None
    obj._keepalive_last_ts = 0.0
    obj._keepalive_tick()
    assert obj._keepalive_last_ts == 0.0
