# -*- coding: utf-8 -*-
"""365 刀A2 验收：派单侧沙箱预筛接两通道分类（沙箱判据零改动）。

覆盖：惯用法不预拦／真实危险预拦／字面量不预拦／AST 失败 fail-closed／
设计内危险仍预拦／开关关闭复现旧口径。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.evolution.patch_execution_dispatch as d  # noqa: E402


def _patch(code):
    return {"modified_code": code}


class TestA2SandboxPrefilter(unittest.TestCase):
    def setUp(self):
        d._M365_PREJ.clear()

    def test_01_idiom_not_preblocked(self):
        """本仓惯用法（getattr 热加载）⇒ 不预拦，交沙箱终判。"""
        code = "def f(cfg):\n    return getattr(cfg, 'X', None)\n"
        scan = d.scan_dangerous_keywords(code)
        self.assertIn(scan.get("class"), ("idiom", "literal"))
        self.assertIs(d.is_sandbox_incompatible(_patch(code)), False)
        self.assertIn("repo_idiom", d.get_m365_sandbox_prefilter_stats())

    def test_02_real_danger_still_preblocked(self):
        """非惯用法真实调用（subprocess）⇒ 预拦转人工。"""
        code = "def f():\n    import subprocess\n    return subprocess.run(['ls'])\n"
        self.assertEqual(d.scan_dangerous_keywords(code).get("class"), "real")
        self.assertIs(d.is_sandbox_incompatible(_patch(code)), True)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("real"), 1)

    def test_03_literal_not_preblocked(self):
        """关键字只出现在字符串字面量 ⇒ 不预拦。"""
        code = "def f():\n    _names = 'getattr('\n    return _names\n"
        self.assertEqual(d.scan_dangerous_keywords(code).get("class"), "literal")
        self.assertIs(d.is_sandbox_incompatible(_patch(code)), False)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("literal"), 1)

    def test_04_unparsed_fail_closed(self):
        """AST 解析失败 ⇒ 预拦（fail-closed，不因"解析不了"放行）。"""
        code = "def f(:\n    return getattr(x, 'y')\n"
        self.assertEqual(d.scan_dangerous_keywords(code).get("class"), "unparsed")
        self.assertIs(d.is_sandbox_incompatible(_patch(code)), True)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("unparsed"), 1)

    def test_05_design_internal_still_preblocked(self):
        """eval/compile 属设计内危险 ⇒ 维持预拦（只加严）。"""
        code = "def f(expr):\n    return eval(expr)\n"
        scan = d.scan_dangerous_keywords(code)
        self.assertEqual(scan.get("idiom_scope"), "design_internal")
        self.assertIs(d.is_sandbox_incompatible(_patch(code)), True)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("design_internal"), 1)

    def test_06_switch_off_restores_legacy(self):
        """开关关闭 ⇒ 复现旧口径（子串命中即预拦）。"""
        code = "def f(cfg):\n    return getattr(cfg, 'X', None)\n"
        with mock.patch("config.ENABLE_SANDBOX_PREFILTER_TWO_CHANNEL", False):
            self.assertIs(d.is_sandbox_incompatible(_patch(code)), True)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("legacy:would_block"), 1)

    def test_07_no_hit_clean(self):
        code = "def f(a, b):\n    return a + b\n"
        self.assertIs(d.is_sandbox_incompatible(_patch(code)), False)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("clean"), 1)


if __name__ == "__main__":
    unittest.main()
