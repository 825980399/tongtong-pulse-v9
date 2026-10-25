#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T-H组评审medium 收口：non_atomic_write 2 处的回归用例（367 后续）。

票面：`T-H组评审medium23条收口-1` —— non_atomic_write 2
（`PulseCodeLearner` 参数优化建议、`PulseSemanticComprehension._save_lessons`）。

★顺带修的真 bug：`safe_read_json(..., default={})` 返回 dict 时，
  紧接着 `_existing.append(...)` 抛 AttributeError 被 except 吞掉 ⇒ 写入静默失败。
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from organs.brain.PulseSemanticComprehension import PulseSemanticComprehension  # noqa: E402

_P1 = os.path.join(_ROOT, "organs", "brain", "PulseCodeLearner.py")
_P2 = os.path.join(_ROOT, "organs", "brain", "PulseSemanticComprehension.py")


def _src(p):
    return io.open(p, encoding="utf-8").read()


def _make_sem(lessons_file):
    """不走 __init__ 的轻量对象（只测 _save_lessons）。"""
    o = PulseSemanticComprehension.__new__(PulseSemanticComprehension)
    o._lessons_file = lessons_file
    o._lessons = {"k1": {"confidence": 0.8}, "k2": {"confidence": 0.4}}
    o._logs = []
    o._log = lambda level, msg, **kw: o._logs.append((level, msg, kw))
    return o


def _bare_write_linenos(src):
    """返回源码里裸 open(..., 'w'/'a') 的行号集合（AST 判，排注释）。"""
    out = set()
    for n in ast.walk(ast.parse(src)):
        if not (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"):
            continue
        mode = ""
        if len(n.args) >= 2 and isinstance(n.args[1], ast.Constant):
            mode = str(n.args[1].value)
        for kw in n.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = str(kw.value.value)
        if "w" in mode or "a" in mode:
            out.add(n.lineno)
    return out


class TestSemanticLessonsAtomic:
    def test_01_writes_atomically(self, tmp_path):
        """正常写：内容可读回，且**不留 .tmp 半成品**（backup 是设计内）。"""
        p = str(tmp_path / "lessons.json")
        o = _make_sem(p)
        o._save_lessons()
        assert json.load(io.open(p, encoding="utf-8")) == o._lessons
        left = [n for n in os.listdir(str(tmp_path)) if n != "lessons.json"]
        assert not [n for n in left if n.endswith(".tmp")], "不该留 .tmp：%s" % left

    def test_02_creates_parent_dir(self, tmp_path):
        """父目录不存在时自建（原实现已有，防回退）。"""
        p = str(tmp_path / "deep" / "dir" / "lessons.json")
        o = _make_sem(p)
        o._save_lessons()
        assert os.path.isfile(p)

    def test_03_no_bare_open_write_in_save_lessons(self):
        """`_save_lessons` 内不得再有裸写。

        ★用 AST 判、不用文本包含：本函数上方的修复注释里就写着裸 open 写法，
        文本断言会被自己的注释命中（铁律 227 同款坑）。
        """
        seg = _src(_P2)
        start = seg.index("def _save_lessons")
        end = seg.index("def get_confidence_threshold")
        assert "safe_write_json" in seg[start:end]
        lo = seg[:start].count("\n") + 1
        hi = seg[:end].count("\n") + 1
        bad = [ln for ln in _bare_write_linenos(seg) if lo <= ln <= hi]
        assert not bad, "_save_lessons 内仍有裸写：行 %s" % bad

    def test_04_failure_is_logged(self):
        """原子写返回 False 时必须留痕（不静默）。"""
        seg = _src(_P2)
        i = seg.index("def _save_lessons")
        assert "原子写返回 False" in seg[i:i + 1200]


class TestCodeLearnerSuggestion:
    def test_05_existing_normalized_to_list(self):
        """`_existing` 必须做 list 归一（dict ⇒ append 会 AttributeError ⇒ 静默失败）。"""
        seg = _src(_P1)
        i = seg.index("_existing = safe_read_json(_suggestion_file, default={})")
        assert "isinstance(_existing, list)" in seg[i:i + 900], "缺少 list 归一保护"

    def test_06_uses_atomic_write(self):
        """参数优化建议改为原子写（不留半截 JSON）。"""
        seg = _src(_P1)
        assert "safe_write_json(_suggestion_file" in seg
        # 该段里不该再有裸写（AST 判）
        i = seg.index("_suggestion_file = ")
        lo = seg[:i].count("\n") + 1
        hi = lo + 40
        bad = [ln for ln in _bare_write_linenos(seg) if lo <= ln <= hi]
        assert not bad, "建议文件写入段仍有裸写：行 %s" % bad

    def test_07_write_failure_logged(self):
        seg = _src(_P1)
        i = seg.index("safe_write_json(_suggestion_file")
        assert "写入失败" in seg[i:i + 300]

    def test_08_import_present(self):
        """safe_write_json 必须真被 import（否则 F821）。"""
        for p in (_P1, _P2):
            head = _src(p).split("class ")[0]
            assert "safe_write_json" in head, p


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
