# -*- coding: utf-8 -*-
"""418 刀1 单测：_m410 初始化块必须真的在 `__init__` 里。

为什么单独一批：411刀6 把这块**留在了 `@staticmethod` 体内、`return` 之后**，
`py_compile` 过、`ast.parse` 过、静态扫描也不报（它不是语法错误，是控制流死区）。
唯一能一眼看穿的是「构造一个实例，问它有没有这个属性」。
"""
import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SRC = os.path.join(ROOT, "nucleus", "genesis", "StreamMiner.py")

#: ★418 修好后实例上必须存在的 9 个 410 状态（一个都不能少）
M410_ATTRS = (
    "_m410_tp", "_m410_prof", "_m410_prof_at", "_m410_windows", "_m410_hist",
    "_m410_win_preds", "_m410_verdicts", "_m410_stat", "_m410_noted",
)


def _tree():
    return ast.parse(open(SRC, encoding="utf-8").read())


def _cls():
    return [n for n in _tree().body
            if isinstance(n, ast.ClassDef) and n.name == "StreamMiner"][0]


def _fn(name):
    for n in _cls().body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("StreamMiner 里没有 %s" % name)


# ============================================================ ① 结构：在 __init__ 里
def test_10_attrs_declared_in_init():
    """9 个 _m410* 属性的赋值必须出现在 `__init__` 体内。"""
    _src = ast.unparse(_fn("__init__"))
    _missing = [a for a in M410_ATTRS if ("self.%s" % a) not in _src]
    assert not _missing, "这些属性没在 __init__ 里初始化：%s" % _missing


def test_11_last_persist_at_in_init():
    """`_last_persist_at` 必须初始化为 0.0（否则首次 consume 就 AttributeError）。"""
    _src = ast.unparse(_fn("__init__"))
    assert "self._last_persist_at = 0.0" in _src


def test_12_auto_load_really_read():
    """`auto_load` 形参此前被埋在死代码里 ⇒ 传 True 等于没传。必须在 __init__ 被读。"""
    _src = ast.unparse(_fn("__init__"))
    assert "if auto_load:" in _src


def test_13_staticmethod_has_no_self():
    """`_m411_periodicity_keep` 是 staticmethod：函数体里**不许**出现 `self.`。

    这正是 411 那处补丁的病灶 —— 一个没有 `self` 的方法里写着 `self._m410_tp = None`，
    只要有人删掉上面的 `return` 就会 NameError。用结构判据把它钉死。
    """
    _n = _fn("_m411_periodicity_keep")
    assert not [a for a in _n.args.args], "它不该有位置参数"
    assert "self." not in ast.unparse(_n), "staticmethod 里出现 self.（死代码或真 bug）"


def test_14_no_dead_code_after_return():
    """函数体里 `return` 之后不许再有语句（本仓已栽过一次）。"""
    for _name in ("_m411_periodicity_keep", "__init__"):
        _body = _fn(_name).body
        _seen_ret = False
        for _st in _body:
            if isinstance(_st, ast.Return):
                _seen_ret = True
                continue
            assert not _seen_ret, ("%s 的 `return` 之后还有语句（L%d）"
                                   % (_name, getattr(_st, "lineno", 0)))


# ============================================================ ② 实例：真的有属性
def test_20_instance_has_all_attrs():
    from nucleus.genesis.StreamMiner import StreamMiner
    _m = StreamMiner()
    _missing = [a for a in M410_ATTRS if not hasattr(_m, a)]
    assert not _missing, "实例上缺属性：%s" % _missing
    assert _m._last_persist_at == 0.0
    assert _m._m410_tp is None
    assert _m._m410_stat == {}
    assert _m._m410_win_preds == []


def test_21_methods_callable():
    """属性有了还不够 —— 读它们的方法也必须能跑（原来在这里抛 AttributeError）。"""
    from nucleus.genesis.StreamMiner import StreamMiner
    _m = StreamMiner()
    _st = _m.get_typed_prediction_stats()
    assert isinstance(_st, dict)
    assert "by_event" in _st and "enabled" in _st
    # 无覆盖样本 ⇒ by_event 为空，但**不许**抛
    assert _st["by_event"] == {}


def test_22_consume_does_not_raise(tmp_path):
    """`consume()` 是生产入口（InfoField 调），此前首次调用会 AttributeError。

    ★418 收尾：`consume()` 内部会走到 `save_patterns()` 无参 ⇒ 用 `self._storage_path`。
    不隔离就等于**每跑一次测试覆盖一次生产 patterns.json**（本轮实测：saved_at 变、
    sha256 变、内容被写成空数据）。所以构造后第一件事必须改路径。
    """
    from nucleus.genesis.StreamMiner import StreamMiner
    _m = StreamMiner()
    _m._storage_path = str(tmp_path / "patterns.json")     # ★必须先改路径再调生产方法
    _r = _m.consume()
    assert isinstance(_r, dict)
    assert "prewarm" in _r and "quality" in _r
    assert os.path.exists(_m._storage_path), "consume 落盘落到了别处（路径没生效）"


def test_23_consumption_stats_readable():
    """`get_consumption_stats()` 里读 `_last_persist_at` —— 同一条链上的第二个爆点。"""
    from nucleus.genesis.StreamMiner import StreamMiner
    _m = StreamMiner()
    _s = _m.get_consumption_stats()
    assert _s["last_persist_at"] == 0.0
    assert "storage" in _s


# ============================================================ ③ 落盘：patterns.json 真写
def test_30_save_patterns_writes_file(tmp_path):
    """`save_patterns()` 必须真写盘并推进 `_last_persist_at`（不再锁在旧 mtime）。"""
    import json
    import time
    from nucleus.genesis.StreamMiner import StreamMiner
    _m = StreamMiner()
    _p = str(tmp_path / "patterns.json")
    assert _m.save_patterns(_p) is True
    assert os.path.exists(_p)
    _payload = json.load(open(_p, encoding="utf-8"))
    assert _payload["version"] == 1
    assert _payload["saved_at"] > 0
    assert _m._last_persist_at > 0
    assert _m._last_persist_at <= time.time() + 1


def test_31_persist_interval_triggers_after_init(tmp_path):
    """`_last_persist_at` 初值 0.0 ⇒ 首次 consume（interval>0）就该落盘。

    这正是「patterns.json 8 小时不推进」的判据：改前属性根本不存在，
    `consume()` 在比较那一行就抛 AttributeError，落盘永远轮不到。
    """
    from nucleus.genesis.StreamMiner import StreamMiner
    _m = StreamMiner()
    _m._storage_path = str(tmp_path / "patterns.json")
    _m.consume()
    assert os.path.exists(_m._storage_path), "首次 consume 没落盘"
    assert _m._last_persist_at > 0


def test_32_auto_load_reads_back(tmp_path, monkeypatch):
    """`auto_load=True` 必须真的读回（此前被死代码吞掉）。

    ★418 收尾：`auto_load=True` 在 `__init__` 里就 load，那时还来不及改
    `_storage_path` ⇒ 必须先把**类属性** `_DEFAULT_STORAGE` 指到 tmp，
    否则构造期会去读生产文件（只读，但同样不干净）。
    """
    import json
    from nucleus.genesis.StreamMiner import StreamMiner
    _p = str(tmp_path / "patterns.json")
    json.dump({"version": 1, "patterns": {"k": {"v": 1}},
               "periodicity": {"e": [1.0, 2.0]}, "quality": {}},
              open(_p, "w", encoding="utf-8"))
    monkeypatch.setattr(StreamMiner, "_DEFAULT_STORAGE", _p, raising=False)
    _m = StreamMiner(auto_load=True)
    assert "k" in _m._discovered_patterns, "auto_load 没在构造期读回（开关仍是死的）"
    assert _m._storage_path == _p


# ============================================================ ④ 回归：开关/语义不变
def test_40_periodicity_keep_still_works():
    """`_m411_periodicity_keep()` 本身的行为不许被这刀改坏。"""
    from nucleus.genesis.StreamMiner import StreamMiner
    _v = StreamMiner._m411_periodicity_keep()
    assert isinstance(_v, int) and _v > 0
    _m = StreamMiner()
    assert _m._M411_PERIODICITY_KEEP == _v


def test_41_periodicity_keep_invalid_falls_back(monkeypatch):
    """非法值（0 / 负数 / 非整数）一律回落 100 —— 原语义逐字保留。"""
    import config
    from nucleus.genesis.StreamMiner import StreamMiner
    for _bad in (0, -5, "abc"):
        monkeypatch.setattr(config, "M411_PERIODICITY_KEEP", _bad, raising=False)
        assert StreamMiner._m411_periodicity_keep() == 100, _bad


def test_42_eol_preserved():
    """行尾必须仍是**纯 CRLF**（本仓铁律 1）。"""
    _b = open(SRC, "rb").read()
    assert _b.count(b"\r\n") == _b.count(b"\n") == _b.count(b"\r"), \
        "crlf=%d lf=%d cr=%d" % (_b.count(b"\r\n"), _b.count(b"\n"), _b.count(b"\r"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# ============================================================ ⑤ 守门：测试不得污染生产
def test_90_no_test_touches_production_storage():
    """★418 收尾守门：本文件里每个 `StreamMiner(...)` 构造后都必须隔离路径。

    判据（AST，不看措辞）：对每个 `StreamMiner(...)` 赋值语句，其后 40 行内必须出现
    `_storage_path` 或 `_DEFAULT_STORAGE`，否则判红 —— 因为 `consume()` /
    `save_patterns()` / `auto_load` 三条路都会摸到 `_storage_path`。

    为什么必须静态查：本轮就是「断言写得很漂亮、但测试本身在写生产」——
    单测全绿，生产文件被静默覆盖。
    """
    import ast
    _src = open(os.path.abspath(__file__), encoding="utf-8").read()
    _tree = ast.parse(_src)
    _lines = _src.splitlines()
    _bad = []
    for _node in ast.walk(_tree):
        if not isinstance(_node, ast.Assign):
            continue
        if not isinstance(_node.value, ast.Call):
            continue
        _fn = _node.value.func
        _name = getattr(_fn, "id", None) or getattr(_fn, "attr", None)
        if _name != "StreamMiner":
            continue
        _lo = _node.lineno
        _hi = min(len(_lines), _lo + 40)
        _win = "\n".join(_lines[_lo:_hi])
        if "_storage_path" not in _win and "_DEFAULT_STORAGE" not in _win:
            _bad.append(_lo)
    assert not _bad, (
        "★这些行构造了 StreamMiner 却没隔离存储路径（会写生产 patterns.json）：%s"
        % _bad)
