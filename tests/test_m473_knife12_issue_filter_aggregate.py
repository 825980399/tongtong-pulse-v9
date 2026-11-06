# -*- coding: utf-8 -*-
"""第473批 刀12 门控单测：`[问题过滤]` 逐条 WARNING → 按 (file, type) 聚合。

病灶
----
近两天 `[问题过滤] organ和method均为空且无法推断，跳过: …` 共 28 条，其中：

    2026-11-06 19:55:19  pid=13048   同秒 10 条
    2026-11-06 20:22:39  pid=52140   同秒 10 条

全是 `tests/test_m470_align_window.py (code_review.SIM115)` —— 该文件有 10 处
SIM115（`open` 未用 `with`），演化系统对每一处各打一条 WARNING。
一次扫描刷 10 行，而真正的信息量只有一句：「这个文件有 10 处 SIM115，
且定位不到 organ/method，自动修不了」。

判据
----
* 同一 `(file, type)` 只出现一次，带 `xN` 计数；
* 多组时按「次数降序、名字升序」排（刷得最多的先看到）；
* 超过 6 组折成「…共 N 组」，不把日志行撑爆；
* 级别仍是 WARNING —— 这是**能力边界**（这些 lint 问题永远不会被自动修），
  必须可见；要治的是「一条问题一行」，不是「看不见」。
"""

from __future__ import annotations

import ast
import os

import nucleus.reasoning.SafeEvolutionExecutor as SEE

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")

_F = "tests/test_m470_align_window.py"
_T = "code_review.SIM115"


# ==================== 聚合结果 ====================

def test_same_file_same_type_collapses():
    """10 处同文件同规则 ⇒ 一条，带 x10。"""
    _txt, _groups = SEE._m473_format_skipped_om([(_F, _T)] * 10)
    assert _groups == 1, _groups
    assert f"{_F}({_T})x10" == _txt, _txt


def test_groups_sorted_by_count_desc_then_name():
    _detail = [("b.py", "R2"), ("a.py", "R1"), ("a.py", "R1"), ("c.py", "R3")] * 1
    _txt, _groups = SEE._m473_format_skipped_om(_detail)
    assert _groups == 3
    # a.py 出现 2 次排最前；其余各 1 次按名字升序 ⇒ b.py 再 c.py
    assert _txt == "a.py(R1)x2, b.py(R2)x1, c.py(R3)x1", _txt


def test_truncates_beyond_six_groups():
    _detail = [(f"f{i}.py", "R") for i in range(9)]
    _txt, _groups = SEE._m473_format_skipped_om(_detail)
    assert _groups == 9
    assert "…共9组" in _txt, _txt
    assert _txt.count(")x") == 6, _txt


def test_empty_and_none_are_safe():
    for _v in ([], None):
        _txt, _groups = SEE._m473_format_skipped_om(_v)
        assert _groups == 0
        assert _txt == ""


def test_malformed_items_skipped():
    """非二元组 / 长度不足的脏项直接跳过，不抛。"""
    _txt, _groups = SEE._m473_format_skipped_om(
        ["bad", ("only_one",), {"x": 1}, (_F, _T)])
    assert _groups == 1
    assert _txt == f"{_F}({_T})x1", _txt


def test_missing_fields_become_placeholder():
    """file/type 为 None ⇒ 占位 `?`，不产生 "None(None)"。"""
    _txt, _groups = SEE._m473_format_skipped_om([(None, None), ("", "")])
    assert _groups == 1
    assert _txt == "?(?)x2", _txt


# ==================== 接线（AST） ====================

def _src():
    with open(_MOD, "r", encoding="utf-8") as fh:
        return fh.read()


def test_per_issue_warning_removed():
    """旧的「逐条 WARNING」必须消失（否则聚合被绕过、日志照旧翻倍）。"""
    _s = _src()
    assert "organ和method均为空且无法推断，跳过:" not in _s, "逐条告警仍在"
    assert "[问题过滤] organ/method 空且无法推断，跳过 %d 个问题（%d 组）：%s" in _s


def test_helper_called_in_repair_path():
    _tree = ast.parse(_src())
    _calls = [n for n in ast.walk(_tree) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name)
              and n.func.id == "_m473_format_skipped_om"]
    assert len(_calls) == 1, f"应恰好 1 处调用，实际 {len(_calls)}"
    _defs = {n.name: n.lineno for n in _tree.body if isinstance(n, ast.FunctionDef)}
    assert "_m473_format_skipped_om" in _defs
    assert _defs["_m473_format_skipped_om"] < _calls[0].lineno, "调用在定义之前"


def test_still_warning_level():
    """聚合后仍是 WARNING：能力边界必须可见，不许顺手降成 INFO。"""
    _s = _src()
    _i = _s.find('"[问题过滤] organ/method 空且无法推断，跳过 %d 个问题（%d 组）：%s"')
    assert _i > 0
    _head = _s[max(0, _i - 200):_i]
    assert "_module_logger.warning(" in _head, _head[-120:]
