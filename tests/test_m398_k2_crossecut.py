# -*- coding: utf-8 -*-
"""398 刀2：4 组横切件收口的判据。

★本批的收口**只做了 2 组**，另 2 组实测"同名不同义"，收了会改行为 ——
测试里把它们钉成"禁止合并"的具名清单，防止下一批又当成重复来合。

覆盖：
  A. `nucleus/_env.py` 三个函数的行为（含异常分支的保守方向）
  B. 7 处 `_is_test_env` / `_in_test_env` 全部委托到 `_env`（AST 级，不是字符串匹配）
  C. 2 处 `_writable` 委托；`rule_engine` 的死函数已删
  D. `_atomic*` 5 种签名、`ledger_path` 5 个账本 —— 断言它们**仍然各自独立**
"""
from __future__ import annotations

import ast
import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus import _env  # noqa: E402

#: 7 处 `_is_test_env` / `_in_test_env`：(文件, 函数名, 是否在类内)
_IS_TEST_ENV_SITES = [
    ("nucleus/evolution/task_ledger.py", "_is_test_env", False),
    ("nucleus/self_awareness/maturity_ledger.py", "_is_test_env", False),
    ("nucleus/self_awareness/capability_ledger.py", "_is_test_env", False),
    ("nucleus/llm/semantic_cache.py", "_is_test_env", True),
    ("nucleus/self_awareness/DailyScheduler.py", "_is_test_env", True),
    ("nucleus/llm/model_self_updater.py", "_in_test_env", False),
]

#: 2 处 `_writable`（模块级同义；model_self_updater 的无参方法不算）
_WRITABLE_SITES = [
    "nucleus/evolution/task_ledger.py",
    "nucleus/self_awareness/maturity_ledger.py",
]

#: 禁止合并清单：这些`_atomic*` 各有不同签名/返回/守卫
_ATOMIC_DISTINCT = [
    ("nucleus/audit_problem_pool.py", "_atomic_write_json"),
    ("nucleus/evolution/patch_execution_dispatch.py", "_m335_atomic_write_json"),
    ("nucleus/evolution/task_ledger.py", "_atomic_save"),
    ("nucleus/evolution/verifiable_recompute.py", "_atomic_write"),
    ("nucleus/mnemosyne/PulseSnapshot.py", "_atomic_write_with_rotation"),
]

#: 5 个不同的账本，路径函数不能合
_LEDGER_MODULES = [
    "nucleus/audit_problem_pool.py",
    "nucleus/decision/ledger.py",
    "nucleus/evolution/task_ledger.py",
    "nucleus/self_awareness/maturity_ledger.py",
    "nucleus/world_model/expectation_ledger.py",
]


def _src(rel: str) -> str:
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8",
                   errors="replace").read()


def _find_func(rel: str, name: str):
    """在模块里找函数/方法定义节点（含类内）。"""
    _tree = ast.parse(_src(rel))
    for _node in ast.walk(_tree):
        if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _node.name == name:
            return _node
    return None


def _body_is_pure_delegate(node, target: str) -> bool:
    """函数体（去掉 docstring）是否只有一句 `return <target>(...)`。"""
    _stmts = [s for s in node.body
              if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                      and isinstance(s.value.value, str))]
    if len(_stmts) != 1:
        return False
    _s = _stmts[0]
    if not isinstance(_s, ast.Return) or not isinstance(_s.value, ast.Call):
        return False
    _f = _s.value.func
    return isinstance(_f, ast.Name) and _f.id == target


# ---------------------------------------------------------------- A. _env 行为
def test_a1_is_test_env_true_under_pytest():
    """pytest 下必须判为测试环境（本测试自己就在 pytest 里跑）。"""
    assert _env.is_test_env() is True


def test_a2_is_production_data_path_true():
    """生产 data/ 下 ⇒ True。"""
    assert _env.is_production_data_path(
        os.path.join(_ROOT, "data", "monitor", "health_snapshot.json")) is True


def test_a3_is_production_data_path_false():
    """源码路径 ⇒ False（不能把代码当数据）。"""
    assert _env.is_production_data_path(
        os.path.join(_ROOT, "nucleus", "_env.py")) is False


def test_a4_is_production_data_path_false_for_prefix_lookalike():
    """`data_other/` 不能被误判成 `data/`（前缀边界）。"""
    assert _env.is_production_data_path(
        os.path.join(_ROOT, "data_other", "x.json")) is False


def test_a5_should_refuse_write_true_in_test_env(tmp_path):
    """测试环境 + 生产 data/ ⇒ 拒写。"""
    _p = os.path.join(_ROOT, "data", "tmp_never_created", "x.json")
    assert _env.should_refuse_write(_p, tag="t") is True


def test_a6_should_refuse_write_false_for_tmp(tmp_path):
    """测试环境但目标在 tmp ⇒ 不拒（允许测试写自己的临时区）。"""
    assert _env.should_refuse_write(str(tmp_path / "x.json")) is False


def test_a7_is_production_data_path_conservative_on_error(monkeypatch):
    """★异常方向：判不了时返回 True（宁可拒写也不污染）。

    原 `maturity_ledger._is_production_data_path` / `model_self_updater._is_production_path`
    的异常分支就是 `return True`，收口后必须保持一致。
    """
    def _boom(*_a, **_k):
        raise OSError("simulated")

    monkeypatch.setattr(_env.os.path, "abspath", _boom)
    assert _env.is_production_data_path("/whatever") is True


# ---------------------------------------------------------------- B. 7 处委托
@pytest.mark.parametrize("rel,name,in_class", _IS_TEST_ENV_SITES)
def test_b1_site_delegates_to_env(rel, name, in_class):
    """每处 `_is_test_env` 的函数体只剩 `return is_test_env()`（实现唯一）。"""
    _node = _find_func(rel, name)
    assert _node is not None, f"{rel} 里找不到 {name}"
    assert _body_is_pure_delegate(_node, "is_test_env"), \
        f"{rel}::{name} 没有委托到 nucleus._env.is_test_env（函数体应只剩一句 return）"


def _code_nodes_only(node) -> list:
    """函数体里的**代码**节点：排除 docstring。

    ★不能对源码做字符串匹配 —— 本批 398 自己刚踩过：注释里写"先看 `sys.modules`"
    就会被扫成"仍保留自带判据"（铁律 227：docstring 误命中 ⇒ 一律走 AST）。
    """
    _out: list = []
    for _s in node.body:
        if (isinstance(_s, ast.Expr) and isinstance(_s.value, ast.Constant)
                and isinstance(_s.value.value, str)):
            continue
        _out.extend(ast.walk(_s))
    return _out


@pytest.mark.parametrize("rel,name,in_class", _IS_TEST_ENV_SITES)
def test_b2_site_does_not_keep_own_logic(rel, name, in_class):
    """★反向断言（AST 级）：函数**代码**里不能再出现自带判据。"""
    _node = _find_func(rel, name)
    _nodes = _code_nodes_only(_node)
    _has_modules = any(isinstance(n, ast.Attribute) and n.attr == "modules"
                       for n in _nodes)
    _has_env = any(isinstance(n, ast.Constant) and n.value == "PYTEST_CURRENT_TEST"
                   for n in _nodes)
    assert not _has_modules, f"{rel}::{name} 代码里仍读 sys.modules"
    assert not _has_env, f"{rel}::{name} 代码里仍读 PYTEST_CURRENT_TEST"


def test_b3_rule_engine_dead_func_removed():
    """`rule_engine._is_test_env` 零调用点 ⇒ 本批已删（不留死代码）。"""
    assert _find_func("nucleus/self_awareness/rule_engine.py", "_is_test_env") is None


# ---------------------------------------------------------------- C. _writable
@pytest.mark.parametrize("rel", _WRITABLE_SITES)
def test_c1_writable_delegates(rel):
    """模块级 `_writable` 委托到 `should_refuse_write`（取反）。"""
    _node = _find_func(rel, "_writable")
    assert _node is not None, f"{rel} 里找不到 _writable"
    _nodes = _code_nodes_only(_node)          # 只看代码，不看 docstring
    _calls = {n.func.id for n in _nodes
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "should_refuse_write" in _calls, f"{rel}::_writable 未委托"
    _has_startswith = any(isinstance(n, ast.Attribute) and n.attr == "startswith"
                          for n in _nodes)
    assert not _has_startswith, f"{rel}::_writable 代码里仍自带路径前缀判断"


def test_c2_model_self_updater_writable_method_kept():
    """`model_self_updater._writable(self)` 保留为方法（无参判自身 base_dir，语义不同）。"""
    _node = _find_func("nucleus/llm/model_self_updater.py", "_writable")
    assert _node is not None
    assert [a.arg for a in _node.args.args] == ["self"], \
        "这个 _writable 必须仍然是无参方法（判 self.base_dir），不能改成接 path"


# ---------------------------------------------------------------- D. 禁止合并
@pytest.mark.parametrize("rel,name", _ATOMIC_DISTINCT)
def test_d1_atomic_variants_still_distinct(rel, name):
    """★5 种 `_atomic*` 必须仍然**各自存在** —— 它们签名/返回/守卫都不同。

    合并会改行为（例如 `audit_problem_pool` 那份带 `guard_write` 守卫，
    `patch_execution_dispatch` 那份没有）。这条测试是**反合并护栏**：
    将来若有人把它们并成一个，这里会红，提醒他先读 `nucleus/_env.py` 的说明。
    """
    assert _find_func(rel, name) is not None, \
        f"{rel}::{name} 不见了 —— 这 5 种 atomic 写入器签名不同，不应合并"


def test_d2_atomic_guard_difference_is_real():
    """守卫差异要有实证：audit 那份调 guard_write，dispatch 那份不调（AST 判，不看注释）。"""
    _audit_node = _find_func("nucleus/audit_problem_pool.py", "_atomic_write_json")
    assert _audit_node is not None
    _a_calls = {n.func.id for n in _code_nodes_only(_audit_node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    _a_attrs = {n.attr for n in _code_nodes_only(_audit_node)
                if isinstance(n, ast.Attribute)}
    assert "guard_write" in _a_calls or "guard_write" in _a_attrs, \
        "audit 那份的 guard_write 守卫不见了（这是两份不能合并的关键差别）"

    _d_node = _find_func("nucleus/evolution/patch_execution_dispatch.py",
                         "_m335_atomic_write_json")
    assert _d_node is not None
    _d_nodes = _code_nodes_only(_d_node)
    _d_calls = {n.func.id for n in _d_nodes
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "guard_write" not in _d_calls, \
        "dispatch 那份若加了 guard_write，就与 audit 同构了 —— 那时才可以考虑合并"


@pytest.mark.parametrize("rel", _LEDGER_MODULES)
def test_d3_ledger_path_still_per_module(rel):
    """★5 个账本各自的路径函数必须各自存在（不同源账本不能共用路径函数）。"""
    assert _find_func(rel, "ledger_path") is not None, \
        f"{rel}::ledger_path 不见了 —— 5 处指 5 个不同账本，不应合并"


def test_d4_ledger_paths_point_to_different_files():
    """实证：这 5 个账本解析出的路径互不相同（不同物，故不能合并）。"""
    from nucleus.decision import ledger as _dl
    from nucleus.evolution import task_ledger as _tl
    from nucleus.self_awareness import maturity_ledger as _ml
    from nucleus.world_model import expectation_ledger as _el
    from nucleus import audit_problem_pool as _ap

    _paths = {
        "decision": _dl.ledger_path(),
        "task": _tl.ledger_path(),
        "maturity": _ml.ledger_path(),
        "expectation": _el.ledger_path(),
        "audit": _ap.ledger_path(),
    }
    _norm = {k: os.path.normcase(os.path.abspath(v)) for k, v in _paths.items()}
    assert len(set(_norm.values())) == 5, f"5 个账本路径有重复，对账不清：{_norm}"
