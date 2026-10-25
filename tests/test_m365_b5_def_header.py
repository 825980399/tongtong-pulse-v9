# -*- coding: utf-8 -*-
"""365 刀B5 验收：片段形态门禁（方法级 original 缺 def 头即拒收重生成）。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution.patch_generator import check_original_def_header  # noqa: E402

_GOOD = "    def foo(self, x):\n        return x + 1\n"
_FRAG = "    return x + 1\n"


class TestB5OriginalDefHeader(unittest.TestCase):
    # ① 方法级 + 缺 def 头（片段）⇒ 拒收重生成
    def test_01_method_mode_fragment_rejected(self):
        r = check_original_def_header({"method": "foo", "original_code": _FRAG},
                                      mode="method")
        self.assertFalse(r["ok"], r)
        self.assertIn("def", r["why"])

    # ② 方法级 + 有 def 头但方法名不符 ⇒ 拒
    def test_02_method_mode_name_mismatch(self):
        r = check_original_def_header({"method": "bar", "original_code": _GOOD},
                                      mode="method")
        self.assertFalse(r["ok"], r)
        self.assertIn("不一致", r["why"])

    # ③ 方法级 + 完整 def ⇒ 放行
    def test_03_method_mode_ok(self):
        r = check_original_def_header({"method": "foo", "original_code": _GOOD},
                                      mode="method")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["has_def"])

    # ④ 锚点级草稿天然无 def 头 ⇒ 跳过（不得把所有草稿一刀切拒掉）
    def test_04_anchor_mode_skipped(self):
        r = check_original_def_header({"method": "foo", "original_code": _FRAG},
                                      mode="anchor")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r.get("skipped"))

    # ⑤ 方法级但 original 为空 ⇒ 拒（无法定位原方法）
    def test_05_empty_original_rejected(self):
        r = check_original_def_header({"method": "foo", "original_code": ""},
                                      mode="method")
        self.assertFalse(r["ok"], r)

    # ⑥ 语法坏 ⇒ 视为片段拒收（不让"解析不了"混成通过）
    def test_06_unparsable_rejected(self):
        r = check_original_def_header({"method": "foo", "original_code": "def foo(:"},
                                      mode="method")
        self.assertFalse(r["ok"], r)
        self.assertFalse(r["has_def"])


if __name__ == "__main__":
    unittest.main()
