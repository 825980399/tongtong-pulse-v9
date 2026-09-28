# -*- coding: utf-8 -*-
"""第147批 刀2 槽位一致性单测：InferenceContext.__slots__ 必须覆盖所有 ctx 字段写入点。

背景：拆分 _on_inference_request 时，跨切口变量通过 InferenceContext（__slots__ 限定）承载。
若某处写入 `ctx.xxx = ...` 但 xxx 未列入 __slots__，运行时抛 AttributeError（__slots__
不允许动态新增属性）。本测试用 AST 自动扫描所有 ctx/_ctx 属性写入点，断言全部落槽，
防止漏槽。

扫描范围：
1. 写入点（Assign / AugAssign / AnnAssign 目标是 ctx.attr 或 _ctx.attr）
2. 读取点（Attribute(value=ctx/_ctx) 的 Load）—— 一并断言，读写都必须在槽内
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import unittest


_SRC = os.path.join(ROOT, "organs", "brain", "PulseInnerWorld.py")


def _load_tree():
    with open(_SRC, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    return ast.parse(src)


def _slots(tree):
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == "InferenceContext":
            for st in n.body:
                if isinstance(st, ast.Assign):
                    for t in st.targets:
                        if isinstance(t, ast.Name) and t.id == "__slots__":
                            if isinstance(st.value, ast.Tuple):
                                return {e.value for e in st.value.elts
                                        if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    raise AssertionError("未找到 InferenceContext.__slots__")


def _ctx_attr_writes(tree):
    """返回 (写入字段集, 读取字段集)。"""
    writes, reads = set(), set()
    for n in ast.walk(tree):
        # 写入：目标为 ctx.xxx / _ctx.xxx
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                        and t.value.id in ("ctx", "_ctx"):
                    writes.add(t.attr)
        # 读取：Attribute(value=ctx/_ctx)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
                and n.value.id in ("ctx", "_ctx") and isinstance(n.ctx, ast.Load):
            reads.add(n.attr)
    return writes, reads


class TestIrSlotsT147(unittest.TestCase):
    """第147批刀2：ctx 槽位一致性（防漏槽 AttributeError）。"""

    @classmethod
    def setUpClass(cls):
        cls._tree = _load_tree()
        cls._slots = _slots(cls._tree)
        cls._writes, cls._reads = _ctx_attr_writes(cls._tree)

    def test_01_slots_cover_all_ctx_writes(self):
        """所有 ctx.xxx = 写入点字段必须都在 __slots__ 中。"""
        missing = sorted(self._writes - self._slots)
        self.assertEqual([], missing, f"写入点字段未落槽: {missing}")

    def test_02_slots_cover_all_ctx_reads(self):
        """所有 ctx.xxx 读取字段必须都在 __slots__ 中。"""
        missing = sorted(self._reads - self._slots)
        self.assertEqual([], missing, f"读取字段未落槽: {missing}")

    def test_03_slots_non_empty(self):
        self.assertGreater(len(self._slots), 15)

    def test_04_build_context_returns_ctx(self):
        """_ir_build_context 应 return ctx（非 None 路径）。"""
        for n in ast.walk(self._tree):
            if isinstance(n, ast.FunctionDef) and n.name == "_ir_build_context":
                has_return_ctx = any(
                    isinstance(s, ast.Return) and isinstance(s.value, ast.Name)
                    for s in ast.walk(n)
                )
                self.assertTrue(has_return_ctx, "_ir_build_context 缺少 return _ctx")
                return
        self.fail("未找到 _ir_build_context")


if __name__ == "__main__":
    unittest.main()
