# -*- coding: utf-8 -*-
"""364 批 A3 验收（知识矛盾门控：跨空间不配对 / 低重叠不报矛盾 / 跟踪条目来源标记）。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _N:
    def __init__(self, sp="", nid="n1"):
        self.space_path = sp
        self.node_id = nid


class TestA3ContradictionGate(unittest.TestCase):
    def test_01_overlap_threshold_is_60pct(self):
        """原注释写「重叠率 ≥ 60%」但代码没这道门 —— 现在落到代码里。"""
        self.assertAlmostEqual(PulseInnerWorld._M364_MIN_OVERLAP, 0.60, places=6)

    def test_02_same_space(self):
        self.assertTrue(PulseInnerWorld._m364_same_space(
            _N("/知识/技术"), _N("/知识/科学")))

    def test_03_cross_space_blocked(self):
        self.assertFalse(PulseInnerWorld._m364_same_space(
            _N("/知识/技术"), _N("/自我/情绪")))

    def test_04_dream_space_blocked(self):
        self.assertFalse(PulseInnerWorld._m364_same_space(
            _N("/梦境/叙事"), _N("/知识/技术")),
            "虚构梦境节点不得与标准知识空间配对判矛盾")

    def test_05_missing_space_not_blocked(self):
        """★缺字段不能把全部矛盾判定废掉：两边都取不到空间 ⇒ 按同空间处理（既有行为）。"""
        self.assertTrue(PulseInnerWorld._m364_same_space(_N(""), _N("")))
        self.assertTrue(PulseInnerWorld._m364_same_space(_N(""), _N("/知识/技术")))

    def test_06_perspective_counter(self):
        p = PulseInnerWorld.__new__(PulseInnerWorld)
        p._log = lambda *a, **k: None
        p._m364_note_perspective_diff(_N("/知识/技术"), _N("/自我/情绪"), 0.3,
                                      reason="cross_space")
        p._m364_note_perspective_diff(_N("/知识/技术"), _N("/知识/科学"), 0.25,
                                      reason="low_overlap")
        s = p.m364_perspective_stats()
        self.assertEqual(s.get("cross_space"), 1)
        self.assertEqual(s.get("low_overlap"), 1)

    def test_07_gate_wired_in_call_site(self):
        src = open(os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py"),
                   encoding="utf-8").read()
        self.assertIn("if overlap_ratio < 0.60:", src)
        self.assertIn('reason="low_overlap"', src)
        self.assertIn('reason="cross_space"', src)
        self.assertIn("self._m364_same_space(node_a, node_b)", src)

    def test_08_track_entry_marked(self):
        """跟踪条目必须带来源/空间/种子素材标记，下游才能过滤。"""
        src = open(os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py"),
                   encoding="utf-8").read()
        for k in ('"origin": "m364_cross_source_contradiction"',
                  '"in_seed_material": False',
                  '"space_a":', '"space_b":'):
            self.assertIn(k, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
