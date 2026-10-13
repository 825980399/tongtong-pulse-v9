# -*- coding: utf-8 -*-
"""第204批 刀3：D#2封存解封 + weight_map 接入实际脉冲流 的隔离验证。

断言：
1. config.RESONANCE_WEIGHT_MAP_ENABLED 默认 False（零行为）。
2. ResonanceDetector 顶部 157-D C-6 封存注释已移除。
3. get_detector() 模块级单例（跨调用同实例）。
4. PulseCore.emit 开启开关时调用 detector.update_weight（pre=source_organ, post=event_type, delta=1.0）。
5. 关闭开关时 _resonance_weight_update 为空操作（权重保持默认值）。
"""
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.synapsys.ResonanceDetector import ResonanceDetector, get_detector  # noqa: E402
from nucleus.pulse.PulseCore import PulseCore  # noqa: E402
import nucleus.synapsys.ResonanceDetector as rd_mod  # noqa: E402


class TestResonanceWiringM204(unittest.TestCase):
    def test_default_disabled(self):
        self.assertFalse(config.RESONANCE_WEIGHT_MAP_ENABLED,
                          "RESONANCE_WEIGHT_MAP_ENABLED 默认应关闭")

    def test_seal_comment_removed(self):
        src = open("nucleus/synapsys/ResonanceDetector.py", encoding="utf-8").read()
        self.assertNotIn("157-D C-6", src, "封存标注应已移除")
        self.assertNotIn("禁止新代码 import 本模块", src, "封存标注应已移除")

    def test_get_detector_singleton(self):
        d1 = get_detector()
        d2 = get_detector()
        self.assertIs(d1, d2, "get_detector 应为模块级单例")
        self.assertIsInstance(d1, ResonanceDetector)

    def test_emit_updates_weight_when_enabled(self):
        rec = ResonanceDetector()
        pulse = {"source_organ": "Heart", "event_type": "heart.beat"}
        with mock.patch.object(rd_mod, "get_detector", return_value=rec):
            with mock.patch.dict(config.__dict__, {"RESONANCE_WEIGHT_MAP_ENABLED": True}):
                core = PulseCore.__new__(PulseCore)  # 跳过重 init
                core._resonance_weight_update(pulse)
        # 默认权重 1.0 + 增量 1.0 = 2.0
        self.assertEqual(rec.get_weight("Heart", "heart.beat")[0], 2.0,
                         "开启时应对 (source_organ, event_type) 累加权重")

    def test_emit_noop_when_disabled(self):
        rec = ResonanceDetector()
        pulse = {"source_organ": "Heart", "event_type": "heart.beat"}
        with mock.patch.object(rd_mod, "get_detector", return_value=rec):
            with mock.patch.dict(config.__dict__, {"RESONANCE_WEIGHT_MAP_ENABLED": False}):
                core = PulseCore.__new__(PulseCore)
                core._resonance_weight_update(pulse)
        # 关闭时无更新，保持默认权重 1.0
        self.assertEqual(rec.get_weight("Heart", "heart.beat")[0], 1.0,
                         "关闭时应为空操作")


if __name__ == "__main__":
    unittest.main()
