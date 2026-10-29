# -*- coding: utf-8 -*-
"""397 刀3 验收：@deprecated 注释纠错（注释与代码实际依赖必须一致）。

票面（任务书刀3）：
  · `HybridParallelScheduler.py:13-14` 标着"@deprecated，禁止新代码 import"，
    但门面 `parallel_scheduler.py` 在模块级 import 它、`submit_cpu_bound` 100% 委托；
  · 注释与代码相反时，**被删的一定是代码** —— 这是最容易引发误删事故的一处。

本文件守两件事：
  ① 注释不再自相矛盾（不再写 @deprecated / 禁止 import，且必须给出正确指引）；
  ② ★注释里提到的**每一个 .py 调用点都必须真实存在** ——
     旧注释写"仅遗留调用点（main.py / self_inspector.py）使用"，而 self_inspector
     全仓根本没有调用点。把"注释里的调用点"变成可验证判据，下次再写错立刻变红。
"""
from __future__ import annotations

import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_BACKENDS = {
    "nucleus/HybridParallelScheduler.py": {
        "symbols": ("TaskType", "get_hybrid_scheduler"),
        "delegators": ("submit_cpu_bound",),
    },
    "nucleus/StructuredParallelScheduler.py": {
        "symbols": ("SubTask", "get_structured_parallel_scheduler"),
        "delegators": ("run_group",),
    },
}
_FACADE = "nucleus/parallel_scheduler.py"
_WRONG_PHRASES = ("@deprecated", "禁止新代码 import", "禁止新代码import")


def _read(rel: str) -> str:
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as _f:
        return _f.read()


def _header(rel: str) -> str:
    """取文件头 docstring（模块级第一个三引号块）。"""
    _src = _read(rel)
    _m = re.search(r'"""(.*?)"""', _src, re.S)
    return _m.group(1) if _m else ""


# ---------------------------------------------------------------- ① 注释不再自相矛盾
@pytest.mark.parametrize("rel", list(_BACKENDS))
def test_01_no_deprecated_wording(rel):
    for _p in _WRONG_PHRASES:
        assert _p not in _header(rel), f"{rel} 头部仍有「{_p}」"


@pytest.mark.parametrize("rel", list(_BACKENDS))
def test_02_points_at_the_facade(rel):
    """必须给出正确指引：新代码走 parallel_scheduler 门面。"""
    _h = _header(rel)
    assert "parallel_scheduler" in _h, f"{rel} 头部没有指向门面"
    assert "门面" in _h, f"{rel} 头部没说明门面关系"


@pytest.mark.parametrize("rel", list(_BACKENDS))
def test_03_says_implementation_is_kept(rel):
    """必须写明"实现保留、不要删"——否则读者仍可能按弃用处理。"""
    _h = _header(rel)
    assert "保留" in _h and "不要删" in _h, f"{rel} 头部未声明实现保留：{_h[:200]}"


# ---------------------------------------------------------------- ② 注释里的调用点必须真实
@pytest.mark.parametrize("rel", list(_BACKENDS))
def test_04_declared_callers_really_import_it(rel):
    """★最有价值的一条：注释里提到的每个 .py 都必须在源码里真的 import 本模块。

    旧注释写"仅遗留调用点（main.py / self_inspector.py）使用"，而 self_inspector 全仓
    无引用 —— 注释一旦被人当真，就可能出现"按注释删、结果删掉在用代码"的事故。
    """
    _mod = os.path.basename(rel)[:-3]
    _h = _header(rel)
    _mentioned = set(re.findall(r"([A-Za-z_][\w/]*\.py)", _h))
    _mentioned.discard(os.path.basename(rel))
    assert _mentioned, f"{rel} 头部没提任何 .py 调用点，判据无从验证"
    for _c in sorted(_mentioned):
        _p = os.path.join(_ROOT, _c)
        assert os.path.exists(_p), f"{rel} 注释提到的 {_c} 根本不存在"
        assert _mod in _read(_c), \
            f"{rel} 注释称 {_c} 是调用点，但 {_c} 里没有出现 {_mod}"


def test_05_wrong_third_caller_is_documented_as_absent():
    """旧注释错列的 `self_inspector` 调用点：全仓确实没有引用（把"查证过"固化下来）。"""
    for _rel, _meta in _BACKENDS.items():
        _mod = os.path.basename(_rel)[:-3]
        for _d in ("nucleus", "organs", "base", "functions", "somatics", "utils"):
            for _dp, _dns, _fns in os.walk(os.path.join(_ROOT, _d)):
                _dns[:] = [d for d in _dns if not d.startswith(".bak") and d != "__pycache__"]
                for _f in _fns:
                    if not _f.endswith(".py") or "self_inspector" not in _f:
                        continue
                    with open(os.path.join(_dp, _f), encoding="utf-8",
                              errors="replace") as _fh:
                        assert _mod not in _fh.read(), (
                            f"{os.path.join(_dp, _f)} 竟然引用了 {_mod} —— "
                            f"注释已改，本条判据的前提失效，需重核")


# ---------------------------------------------------------------- ③ 代码侧事实（注释的依据）
@pytest.mark.parametrize("rel,meta", list(_BACKENDS.items()))
def test_06_facade_imports_it_at_module_level(rel, meta):
    """注释说"门面模块级 import + 委托"，代码侧必须真的是这样。"""
    _mod = os.path.basename(rel)[:-3]
    _src = _read(_FACADE)
    for _sym in meta["symbols"]:
        assert re.search(rf"^from nucleus\.{_mod} import .*\b{_sym}\b", _src, re.M), \
            f"{_FACADE} 未模块级 import {_mod}.{_sym}"
    for _fn in meta["delegators"]:
        assert f"def {_fn}" in _src, f"{_FACADE} 没有 {_fn}"
        _seg = _src[_src.index(f"def {_fn}"): _src.index(f"def {_fn}") + 900]
        assert _mod in _seg, f"{_FACADE}.{_fn} 没有委托到 {_mod}"


def test_07_facade_has_no_deprecated_marker_either():
    """门面自己也不该标弃用（它是 P2-64 收敛后的唯一入口）。"""
    _h = _header(_FACADE)
    for _p in _WRONG_PHRASES:
        assert _p not in _h, f"{_FACADE} 头部有「{_p}」"
