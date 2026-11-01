# -*- coding: utf-8 -*-
"""417 刀5（补）：**补丁完整性** —— 防「打补丁把 def 行吃掉」。

为什么必须加这一支（417 施工实测，教训写死在这里）：
    刀1 的补丁脚本以 `def _m377_tm_ensure() -> dict:` / 
    `def _build_observability_snapshot(...)` 这两行作为**插入锚点**，
    替换时把 `def` 行本身一并吃掉 —— 函数体被并进上一个函数，
    **语法照样合法**，于是：
      · `py_compile` 通过；
      · 96 条刀1~刀4 单测全绿 ← 因为它们统统把 `_build_observability_snapshot`
        换成了假件（sleep 0.4s），真函数根本没人调；
      · 只有 `ruff --select F821` 抓到「`skip_slow` 未定义」。
    ⇒ 真路径被 `__init__` 里的 `_m377_tm_ensure()` 直接引爆，而判据一条没响。

本支的判据（与既有单测互补，不重复）：
  ① **悬空 self 调用**：类里出现 `self.X(...)` 但 `dir(类)` 里没有 X ⇒ 必红。
     这一条对「def 被吃掉」是**直接命中**：函数体并进了上一个方法，
     里面所有 `self.xxx()` 还在，但 `xxx` 的定义没了。
  ② 两个被吃过的方法必须存在且签名正确（参数名/默认值钉住 —— 光有名字不够，
     本次就是「有函数体、没 def ⇒ 连名字都没了」）。
  ③ 刀1/刀2 新增的模块级函数与实例闸门必须都在（`__new__` 手搓实例的兼容路径）。
  ④ 刀3 删的 14 条方法在 AST 里**不得复活**（与 test_m417_k3 的 DELETED 同源）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: (文件, 类名) —— 本批动过方法体的两个类
PATCHED = [
    ("organs/core/PulseMetricsCollector.py", "PulseMetricsCollector"),
    ("nucleus/self_inspector.py", "SelfInspector"),
]


def _tree(rel: str):
    return ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="replace"), filename=rel)


def _class_node(tree, cls: str):
    for _n in ast.walk(tree):
        if isinstance(_n, ast.ClassDef) and _n.name == cls:
            return _n
    return None


def _attr_assigned(cnode) -> set:
    """类里被当属性赋过值的名字（可能是回调/partial，静态分不清，放行）。"""
    _out = set()
    for _n in ast.walk(cnode):
        if isinstance(_n, ast.Assign):
            for _t in _n.targets:
                if isinstance(_t, ast.Attribute) and isinstance(_t.value, ast.Name) \
                        and _t.value.id in ("self", "cls"):
                    _out.add(_t.attr)
    return _out


def _dynamic(cnode) -> bool:
    _m = {x.name for x in cnode.body if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if _m & {"__getattr__", "__getattribute__"}:
        return True
    return any(isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name)
               and _n.func.id == "setattr" for _n in ast.walk(cnode))


# ============================================================ ① 悬空 self 调用
@pytest.mark.parametrize("rel,cls", PATCHED)
def test_10_no_dangling_self_call(rel, cls):
    """类里每个 `self.X(...)` 的 X 必须在 `dir(类)` 里（含继承来的）。"""
    import importlib
    _ast_tree = _tree(rel)
    _cnode = _class_node(_ast_tree, cls)
    assert _cnode is not None, "%s 里没有类 %s" % (rel, cls)
    if _dynamic(_cnode):
        pytest.skip("%s 有 setattr/__getattr__，静态判不了" % cls)

    _klass = getattr(importlib.import_module(rel[:-3].replace("/", ".")), cls)
    _known = set(dir(_klass)) | _attr_assigned(_cnode)

    _bad = []
    for _n in ast.walk(_cnode):
        if not isinstance(_n, ast.Call):
            continue
        _f = _n.func
        # ★只看 `self`：本仓库里 `cls` 常被当作循环变量（`for cls in classes` 拿到 dict），
        #   `cls.get(...)` 会被误报。而「def 被吃掉」这个 bug 的症状一定是 `self.X`。
        if isinstance(_f, ast.Attribute) and isinstance(_f.value, ast.Name) \
                and _f.value.id == "self" and _f.attr not in _known:
            _bad.append("L%d self.%s" % (_n.lineno, _f.attr))
    assert not _bad, \
        "%s.%s 有悬空 self 调用（典型症状：打补丁把 def 行吃掉，函数体并进了上一个方法）：%r" \
        % (rel, cls, _bad[:10])


# ============================================================ ② 被吃过的方法必须回来
def test_20_build_observability_snapshot_signature():
    import organs.core.PulseMetricsCollector as _pmc
    _f = getattr(_pmc.PulseMetricsCollector, "_build_observability_snapshot", None)
    assert _f is not None, "`_build_observability_snapshot` 不见了（417 被吃过一次）"
    _a = list(_f.__code__.co_varnames[:_f.__code__.co_argcount])
    assert _a[:2] == ["self", "skip_slow"], "参数表被改了：%r" % (_a,)
    _d = _f.__defaults__ or ()
    assert _d and _d[0] is False, "`skip_slow` 默认值必须是 False（保住既有调用方行为）：%r" % (_d,)


def test_21_m377_tm_ensure_exists():
    import organs.core.PulseMetricsCollector as _pmc
    assert hasattr(_pmc, "_m377_tm_ensure"), \
        "模块级 `_m377_tm_ensure` 不见了（`__init__` 第一句就调它）"
    assert callable(_pmc._m377_tm_ensure)
    _r = _pmc._m377_tm_ensure()
    assert isinstance(_r, dict) and "mode" in _r and "tracing" in _r, _r


# ============================================================ ③ 417 新增件齐全
def test_30_module_level_symbols_present():
    import organs.core.PulseMetricsCollector as _pmc
    for _n in ("m417_async_switch_on", "m417_stats", "_m417_reset_state",
               "m417_cfg", "_M417_SWITCH_ATTR", "_M417_JOB_SNAPSHOT", "_M417_JOB_TICKS"):
        assert hasattr(_pmc, _n), "PulseMetricsCollector 缺 %s" % _n
    import nucleus.self_inspector as _si
    for _n in ("m417_yield_on", "m417_yield_stats", "_m417_scan_tick",
               "_m417_reset_yield_state", "_m348_tests_corpus", "_m348_reset_tests_corpus",
               "_M417_YIELD_EVERY", "_M417_YIELD_SEC"):
        assert hasattr(_si, _n), "self_inspector 缺 %s" % _n


def test_31_switch_defaults_on():
    import organs.core.PulseMetricsCollector as _pmc
    import nucleus.self_inspector as _si
    assert _pmc.m417_async_switch_on() is True, "刀1 开关缺省必须 True"
    assert _si.m417_yield_on() is True, "刀2 开关缺省必须 True"


# ============================================================ ④ 刀3 删除项没复活
def test_40_deleted_methods_not_resurrected():
    from tests.test_m417_k3_delete_regression import DELETED
    for _rel, _cls, _name in DELETED:
        _cnode = _class_node(_tree(_rel), _cls)
        assert _cnode is not None, "%s 里没有 %s" % (_rel, _cls)
        _names = {x.name for x in _cnode.body
                  if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert _name not in _names, "%s.%s 又回来了（刀3 白名单失效？）" % (_cls, _name)
