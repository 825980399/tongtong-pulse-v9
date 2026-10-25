# -*- coding: utf-8 -*-
"""365 刀D组 验收：config 开关标注（D1 共窗翻回留痕 / D2 死开关归档 / D3 反向语义）。

只守不变量：标注在位、且 13 个开关取值**零变化**（本刀只改注释，不改行为）。
"""
from __future__ import annotations

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

D1_SWITCHES = ("ENABLE_ROUTING_LAYERED", "ENABLE_INNER_WORLD_QUALITY_ESCALATION",
               "ENABLE_COGNITIVE_TEMPERATURE_OBSERVE", "ENABLE_MEMORY_STRENGTH_SHADOW",
               "ENABLE_HEBBIAN_COLLISION_SCAN", "ENABLE_HEBBIAN_APPLY_DECAY")
D2_SWITCHES = ("QICA_USE_TUNED_WEIGHTS", "STOMACH_USE_JSON5",
               "ENABLE_OA3_SKILL_MANIFEST", "ENABLE_SPIKE_BACKEND_STATS")
D3_SWITCHES = ("COLD_DISABLE_STARTUP_COMPACT", "ENABLE_EYES_STREAM_STATS_INFO")


def _src():
    with open(os.path.join(_ROOT, "config.py"), encoding="utf-8") as f:
        return f.read()


class TestDAnnotations(unittest.TestCase):
    def test_01_d1_all_false_and_annotated(self):
        _s = _src()
        self.assertEqual(_s.count("D1留痕[L397]"), len(D1_SWITCHES))
        for _n in D1_SWITCHES:
            self.assertIs(getattr(config, _n), False, "%s 状态必须维持 False" % _n)

    def test_02_d2_archived(self):
        _s = _src()
        self.assertEqual(_s.count("D2归档[L398]"), len(D2_SWITCHES))
        for _n in D2_SWITCHES:
            self.assertIs(getattr(config, _n), False, "%s 不得被开" % _n)
        # OB1 走独立标注（未实装，防被当成"提权已受控"）
        self.assertIn("D2[S] 仅 gate 表/配置面引用", _s)

    def test_03_d3_reverse_semantics(self):
        _s = _src()
        self.assertEqual(_s.count("D3反向[L399]"), len(D3_SWITCHES))
        for _n in D3_SWITCHES:
            self.assertIs(getattr(config, _n), False, "%s 语义反向但取值不动" % _n)
        self.assertIn("False=新行为", _s)
        self.assertIn("False=降噪", _s)

    def test_04_no_value_drift(self):
        """13 个开关在 config 源码里仍为 False（防手滑写成 True）。"""
        _s = _src()
        for _n in D1_SWITCHES + D2_SWITCHES + D3_SWITCHES:
            _m = re.search(r"^%s\s*=\s*(\S+)" % _n, _s, re.M)
            self.assertIsNotNone(_m, _n)
            self.assertEqual(_m.group(1).rstrip(","), "False", _n)


if __name__ == "__main__":
    unittest.main()
