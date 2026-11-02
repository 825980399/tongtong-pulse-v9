# -*- coding: utf-8 -*-
"""428 批刀1 单测：生成侧截断治理。

钉语义（不钉措辞）：
  · 值内 markdown 围栏必须剥掉，且**保留结尾换行**（original_code 会被当精确锚点）；
  · 喂给模型的原文必须是**语法自洽**前缀（不能切在语句中间）；
  · 解析出的补丁要带 `m428_gen_truncated` 观测位。
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

pg = importlib.import_module("nucleus.evolution.LLMEvolutionEngine")


def test_01_strip_fence_keeps_trailing_newline():
    _in = "```python\nX = 1\n```\n"
    assert pg._m428_strip_fence_lines(_in) == "X = 1\n"
    assert pg._m428_strip_fence_lines("X = 1\n") == "X = 1\n", "无围栏必须原样"
    assert pg._m428_strip_fence_lines("```\nA = 1\nB = 2\n```") == "A = 1\nB = 2"


def test_02_self_contained_prefix_backtracks_out_of_half_try():
    # 3000 字硬切会切在这里：try 开了一半，except 还没出现
    _src = "def f():\n    a = 1\n    try:\n        b = 2\n"
    _cut, _truncated = pg._m428_self_contained_prefix(_src, 40)
    assert _truncated is True
    ast.parse(_cut)                       # 关键判据：回退到语法自洽
    assert "try:" not in _cut, "回退必须退出未闭合的 try 块"
    assert "a = 1" in _cut


def test_03_parse_strips_in_value_fence_and_marks_truncation():
    _resp = json.dumps({"patches": [{
        "file": "x.py", "method": "m",
        "original_code": "```python\ndef m():\n    return 1\n```\n",
        "modified_code": "```python\ndef m():\n    return 2\n```\n",
    }]}, ensure_ascii=False)
    _e = pg.LLMEvolutionEngine(_ROOT)
    _e._m428_truncated = True           # 模拟"本次回复被截断"
    _rows = _e._parse_multi_file_patch(_resp, {"type": "t"})
    assert len(_rows) == 1
    _p = _rows[0]
    assert "```" not in _p["original_code"], "值内围栏必须剥掉"
    assert "```" not in _p["modified_code"]
    assert _p["original_code"].startswith("def m():")
    assert _p["m428_gen_truncated"] is True
