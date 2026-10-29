# -*- coding: utf-8 -*-
"""398 刀4：`on_field_oscillation` 64 个声明点的预埋标注。

本批**只标注不接线**（任务书红线：这是场共振阶段的事，现在接会分散精力）。

判据：
  * 64 个 def 上方紧邻必须有一行含 `398刀4` 的注释；
  * 全仓 `398刀4` 出现次数 == def 数（**不许误标到别的函数**）；
  * 标注文案必须写明"预埋未接"与"0 调用点"（理由要能自证）；
  * 调用点数**只报告不断言** —— 将来接线了，更新标注即可，测试不该因此变红
    （铁律 268：判据断言历史快照必然变红 ⇒ 跟随最新证据）。
"""
from __future__ import annotations

import ast
import io
import os
import sys

import pytest

from nucleus._silent_except import silent_exc

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

TARGET = "on_field_oscillation"
MARK = "398刀4"


def _iter_prod():
    for _dp, _dns, _fns in os.walk(_ROOT):
        _dns[:] = [d for d in _dns if d not in
                   {".git", "__pycache__", ".venv", "node_modules", "build", "tmp"}
                   and not d.startswith(".bak")]
        _rel = os.path.relpath(_dp, _ROOT).replace("\\", "/")
        _top = _rel.split("/")[0] if _rel != "." else ""
        for _fn in _fns:
            if not _fn.endswith(".py"):
                continue
            if _top in ("nucleus", "organs", "functions", "base", "utils",
                        "somatics", "hardware"):
                yield os.path.join(_dp, _fn)
            elif _top == "" and _fn in ("main.py", "config.py", "pulse_doctor.py"):
                yield os.path.join(_dp, _fn)


def _mark_block(lines, lineno_1based, max_back=4):
    """取 def 上方**连续注释块**（不含空行），返回该块的行列表。

    标注是两行注释，紧邻 def 的那行是第二行（不含 `398刀4`），
    所以必须回看整个连续注释块，不能只看上一行。
    「紧邻」的定义：def 上一行必须已是注释（中间有代码/空行即不算）。
    """
    i = lineno_1based - 2                 # def 上一行的 0-based 索引
    block = []
    while i >= 0 and len(block) < max_back:
        _s = lines[i].strip()
        if not _s.startswith("#"):
            break
        block.append(_s)
        i -= 1
    return block[::-1]                    # 恢复自上而下的顺序


def _collect():
    """返回 (声明点列表, 全仓 MARK 出现次数)。"""
    sites = []
    mark_lines = 0
    for p in _iter_prod():
        rel = os.path.relpath(p, _ROOT).replace("\\", "/")
        try:
            src = io.open(p, encoding="utf-8", errors="replace").read()
            tree = ast.parse(src)
        except SyntaxError as _e:
            silent_exc(_e, where="m398_k4::_collect parse", level="debug")
            continue
        _lines = src.splitlines()
        for _ln in _lines:
            if MARK in _ln:
                mark_lines += 1
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == TARGET:
                _blk = _mark_block(_lines, node.lineno)
                sites.append({"rel": rel, "lineno": node.lineno,
                              "block": _blk,
                              "text": " ".join(_blk),
                              "marked": any(MARK in x for x in _blk)})
    return sites, mark_lines


_SITES, _MARKS = _collect()


def test_01_site_count_is_64():
    """声明点必须仍是 64 个（票面与实际一致；变了说明有人加/删了这个钩子）。"""
    assert len(_SITES) == 64, f"实际 {len(_SITES)} 个声明点"


def test_02_every_site_is_marked():
    """64 个点**全部**在紧上一行带标注（任务书验收硬要求）。"""
    _miss = [f"{s['rel']}:{s['lineno']}" for s in _SITES if not s["marked"]]
    assert not _miss, f"{len(_miss)} 个声明点没标注：{_miss[:8]}"


def test_03_no_stray_marks():
    """★不许误标：全仓 `398刀4` 行数必须等于声明点数。

    多出来说明标注被贴到了别的函数/别的地方（那样"64 个都有标注"就成了假象）。
    """
    assert _MARKS == len(_SITES), \
        f"标注行 {_MARKS} ≠ 声明点 {len(_SITES)}，有误标或漏标"


def test_04_mark_text_states_reason():
    """标注必须写明「预埋未接」+「0 调用点」—— 理由要能自证，不能只贴个编号。"""
    _bad = []
    for s in _SITES:
        if "预埋未接" not in s["text"] or "0 调用点" not in s["text"]:
            _bad.append(f"{s['rel']}:{s['lineno']}")
    assert not _bad, f"这些点的标注缺关键理由：{_bad[:6]}"


def test_05_mark_is_adjacent_not_far():
    """标注必须**紧邻** def：def 上一行就是注释块（中间不许夹空行或代码）。"""
    _far = []
    for s in _SITES:
        _lines = io.open(os.path.join(_ROOT, s["rel"]), encoding="utf-8",
                         errors="replace").read().splitlines()
        if s["lineno"] - 2 < 0:
            _far.append(f"{s['rel']}:{s['lineno']}")
            continue
        if not _lines[s["lineno"] - 2].strip().startswith("#"):
            _far.append(f"{s['rel']}:{s['lineno']}（上一行不是注释）")
    assert not _far, f"标注没有紧贴 def：{_far[:6]}"


@pytest.mark.parametrize("rel", sorted({s["rel"] for s in _SITES})[:6])
def test_06_sample_files_still_import(rel):
    """抽样：带标注的文件必须仍可 import（注释不该改坏语法）。"""
    import importlib

    _mod = rel[:-3].replace("/", ".")
    assert importlib.import_module(_mod) is not None


def test_07_call_sites_report_only():
    """调用点数**只报告**：仍为 0 说明标注措辞成立；接线了就该改措辞，不是判红。"""
    _refs = 0
    for p in _iter_prod():
        try:
            _tree = ast.parse(io.open(p, encoding="utf-8", errors="replace").read())
        except SyntaxError as _e:
            silent_exc(_e, where="m398_k4::test_07 parse", level="debug")
            continue
        for node in ast.walk(_tree):
            if isinstance(node, ast.Attribute) and node.attr == TARGET \
                    and isinstance(node.ctx, ast.Load):
                _refs += 1
            elif isinstance(node, ast.Name) and node.id == TARGET \
                    and isinstance(node.ctx, ast.Load):
                _refs += 1
    if _refs:
        pytest.skip(f"已有 {_refs} 个调用点 —— 标注该从「预埋未接」改成「已接线」")
    assert _refs == 0
