# -*- coding: utf-8 -*-
"""365 刀A2 验收：派单侧沙箱预筛接两通道分类（沙箱判据零改动）。

★口径（366 收口轮）：开关 **默认 True（小林裁决甲）**，故：
  * "新口径"用例显式 patch True（不依赖默认态，防止默认值再变动时整族失效）；
  * 有一例断言「默认就是开的」——防日后被悄悄翻回 False；
  * 旧口径用例显式 patch False 验灰度回退口。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import nucleus.evolution.patch_execution_dispatch as d  # noqa: E402

_ON = "config.ENABLE_SANDBOX_PREFILTER_TWO_CHANNEL"


def _patch(code):
    return {"modified_code": code}


class TestA2SandboxPrefilter(unittest.TestCase):
    def setUp(self):
        d._M365_PREJ.clear()

    # ⓿ 默认必须开（小林 366 裁决甲；防日后被悄悄翻回 False）
    def test_00_default_on(self):
        self.assertIs(config.ENABLE_SANDBOX_PREFILTER_TWO_CHANNEL, True)
        self.assertIs(d._m365_two_channel_on(), True)

    # ① 本仓惯用法（getattr 热加载）⇒ 新口径下不预拦，交沙箱终判
    def test_01_idiom_not_preblocked(self):
        code = "def f(cfg):\n    return getattr(cfg, 'X', None)\n"
        scan = d.scan_dangerous_keywords(code)
        self.assertIn(scan.get("class"), ("idiom", "literal"))
        with mock.patch(_ON, True):
            self.assertIs(d.is_sandbox_incompatible(_patch(code)), False)
        self.assertIn("repo_idiom", d.get_m365_sandbox_prefilter_stats())

    # ② 非惯用法真实调用（subprocess）⇒ 仍预拦转人工
    def test_02_real_danger_still_preblocked(self):
        code = "def f():\n    import subprocess\n    return subprocess.run(['ls'])\n"
        self.assertEqual(d.scan_dangerous_keywords(code).get("class"), "real")
        with mock.patch(_ON, True):
            self.assertIs(d.is_sandbox_incompatible(_patch(code)), True)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("real"), 1)

    # ③ 关键字只出现在字符串字面量 ⇒ 新口径不预拦
    def test_03_literal_not_preblocked(self):
        code = "def f():\n    _names = 'getattr('\n    return _names\n"
        self.assertEqual(d.scan_dangerous_keywords(code).get("class"), "literal")
        with mock.patch(_ON, True):
            self.assertIs(d.is_sandbox_incompatible(_patch(code)), False)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("literal"), 1)

    # ④ AST 解析失败 ⇒ 预拦（fail-closed，两态都拦）
    def test_04_unparsed_fail_closed(self):
        code = "def f(:\n    return getattr(x, 'y')\n"
        self.assertEqual(d.scan_dangerous_keywords(code).get("class"), "unparsed")
        with mock.patch(_ON, True):
            self.assertIs(d.is_sandbox_incompatible(_patch(code)), True)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("unparsed"), 1)

    # ⑤ eval/compile 属设计内危险 ⇒ 维持预拦（只加严）
    def test_05_design_internal_still_preblocked(self):
        code = "def f(expr):\n    return eval(expr)\n"
        scan = d.scan_dangerous_keywords(code)
        self.assertEqual(scan.get("idiom_scope"), "design_internal")
        with mock.patch(_ON, True):
            self.assertIs(d.is_sandbox_incompatible(_patch(code)), True)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("design_internal"), 1)

    # ⑥ 灰度回退口：显式关掉 ⇒ 复现旧口径（子串命中即预拦），不再靠默认态验
    def test_06_switch_off_restores_legacy(self):
        code = "def f(cfg):\n    return getattr(cfg, 'X', None)\n"
        with mock.patch(_ON, False):
            self.assertIs(d.is_sandbox_incompatible(_patch(code)), True)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("legacy:would_block"), 1)

    # ⑦ 无命中 ⇒ 不预拦（两态一致）
    def test_07_no_hit_clean(self):
        code = "def f(a, b):\n    return a + b\n"
        self.assertIs(d.is_sandbox_incompatible(_patch(code)), False)
        self.assertEqual(d.get_m365_sandbox_prefilter_stats().get("clean"), 1)


if __name__ == "__main__":
    unittest.main()
