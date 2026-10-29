# -*- coding: utf-8 -*-
"""398 刀5：`_generate_multiple_solutions` 整层死代码的标注判据。

本批**只标注不接线**（任务书红线：接上会改补丁生成主路径，风险不适合 P2 批次）。

判据：
  * 该层仍零调用（AST 级）；
  * 它的唯一内部依赖 `_generate_rule_patch` 也只在死链上被调（这是"整层从未执行"的实证）；
  * 标注写明三件事：未接线 / 为什么留着 / 为什么不现在接；
  * `_generate_patch` 的 docstring **不再**声称"先做多方案评估"（原注释与代码相反，
    397 刀3 修过同型问题：注释标弃用但代码硬依赖）。
"""
from __future__ import annotations

import ast
import io
import os
import sys

from nucleus._silent_except import silent_exc

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_TARGET_FILE = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_TARGET = "_generate_multiple_solutions"
_MARK = "398刀5"


def _src() -> str:
    return io.open(_TARGET_FILE, encoding="utf-8", errors="replace").read()


def _tree():
    return ast.parse(_src())


def _func(name):
    for n in ast.walk(_tree()):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def test_01_layer_exists():
    """整层仍在（本批只标注，不删）。"""
    n = _func(_TARGET)
    assert n is not None, f"{_TARGET} 不见了 —— 本批不该删它"
    _span = n.end_lineno - n.lineno + 1
    assert _span >= 50, f"该层只剩 {_span} 行，不像是完整实现"


def test_02_still_zero_calls():
    """★零调用（这是标注措辞的事实基础）。接线了就该改措辞，不是删这段测试。"""
    _refs = 0
    for p in _iter_prod():
        try:
            t = ast.parse(io.open(p, encoding="utf-8", errors="replace").read())
        except SyntaxError as _e:
            silent_exc(_e, where="m398_k5::test_02 parse", level="debug")
            continue
        for node in ast.walk(t):
            if isinstance(node, ast.Attribute) and node.attr == _TARGET \
                    and isinstance(node.ctx, ast.Load):
                _refs += 1
            elif isinstance(node, ast.Name) and node.id == _TARGET \
                    and isinstance(node.ctx, ast.Load):
                _refs += 1
    assert _refs == 0, f"已有 {_refs} 个调用点 —— 标注该从「未接线」改成「已接线」"


def _iter_prod():
    for dp, dns, fns in os.walk(_ROOT):
        dns[:] = [d for d in dns if d not in
                  {".git", "__pycache__", ".venv", "node_modules", "build", "tmp"}
                  and not d.startswith(".bak")]
        rel = os.path.relpath(dp, _ROOT).replace("\\", "/")
        top = rel.split("/")[0] if rel != "." else ""
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            if top in ("nucleus", "organs", "functions", "base", "utils",
                       "somatics", "hardware"):
                yield os.path.join(dp, fn)
            elif top == "" and fn in ("main.py", "config.py", "pulse_doctor.py"):
                yield os.path.join(dp, fn)


def test_03_rule_patch_only_called_from_dead_layer():
    """★实证：`_generate_rule_patch` 的**唯一**调用点落在死层内部。

    这条比"有没有标注"更重要 —— 它说明"三方案择优"整层（含它依赖的规则生成）
    从未执行过。若哪天有人在活路径上调用了 `_generate_rule_patch`，
    这条会红，提醒更新标注。
    """
    _f = os.path.basename(_TARGET_FILE)
    _calls = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            _fn = node.func
            if isinstance(_fn, ast.Attribute) and _fn.attr == "_generate_rule_patch":
                _calls.append(node.lineno)
    assert _calls, "找不到任何 _generate_rule_patch 调用"
    _dead = _func(_TARGET)
    _inside = [ln for ln in _calls if _dead.lineno <= ln <= _dead.end_lineno]
    assert len(_inside) == len(_calls), (
        f"{_f} 里 _generate_rule_patch 有 {len(_calls) - len(_inside)} 处调用"
        f"落在死层之外（行号 {_calls}）—— 说明它已经活了，标注要跟着改")


def test_04_mark_states_three_things():
    """标注必须写明：未接线 / 为什么留着 / 为什么不现在接。"""
    _node = _func(_TARGET)
    _doc = ast.get_docstring(_node) or ""
    for _kw in (_MARK, "零调用", "为什么留着", "为什么不现在接"):
        assert _kw in _doc, f"标注缺「{_kw}」"


def test_05_generate_patch_doc_no_longer_overclaims():
    """★`_generate_patch` 的 docstring 不能再**承诺**多方案评估。

    原文是一句独立的「★第三阶段：先进行多方案评估，选择最优方案后生成补丁」，
    而代码从没调用过 `_generate_multiple_solutions` —— 注释与代码相反
    （397 刀3 同型：注释标 deprecated 但代码硬依赖）。

    判据不钉字面：纠错说明本身**必须引用原话**，所以凡是含该短语的行，
    只要求它同时带 398刀5 标记（说明这句是被引用来否定掉的，不是承诺）。
    这样以后改纠错措辞不会把判据顶红（铁律 268：别钉历史快照）。
    """
    _node = _func("_generate_patch")
    assert _node is not None
    _doc = ast.get_docstring(_node) or ""
    _bad = [ln.strip() for ln in _doc.splitlines()
            if "先进行多方案评估" in ln and _MARK not in ln]
    assert not _bad, (
        "以下行仍把多方案评估当作既有能力在承诺（不带 398刀5 纠错标记）：\n  "
        + "\n  ".join(_bad))
    assert _MARK in _doc, "纠错说明缺失（应标注这是 398 刀5 的更正）"


def test_06_module_still_imports():
    """标注改的是 docstring，模块必须仍可导入。"""
    import importlib

    m = importlib.import_module("nucleus.reasoning.SafeEvolutionExecutor")
    assert m is not None
