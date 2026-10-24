# -*- coding: utf-8 -*-
"""第204批 刀3：D#2封存解封 + weight_map 接入实际脉冲流 的隔离验证。

断言：
1. ★363刀J1：~~默认 False~~ 改为**两态语义**——开关关闭 ⇒ 权重保持 1.0（零行为）；
   开关开启 ⇒ 累加到 2.0。不再把默认值钉成 False（见 test_switch_two_state_semantics）。
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
    def test_switch_two_state_semantics(self):
        """★363刀J1（裁决四·建议乙）：不再把默认值钉成 False。

        207 批之后该开关**有意开启**：留痕见 13b1545e「RESONANCE闸开闸(False→True)：
        经验闸前门已满足(Δ非空=500≥20, 填充率100%)」；活体 `[pulse.resonance]` 映射
        分布每约 3 分钟一条，是 358 批 C5 共振诊断的数据来源 —— **在用观测链**。

        把断言钉在「默认 False」，等于逼人在「保住采样链」与「测试绿」之间二选一。
        真正要守的不变量是**两态行为**：关闭 ⇒ 零行为（这才是「默认关闭」的本意），
        开启 ⇒ 累加。两态行为测试（test_emit_updates_weight_when_enabled /
        test_emit_noop_when_disabled）保留不动。
        """
        _pulse = {"source_organ": "Heart", "event_type": "heart.beat"}

        _on = ResonanceDetector()
        with mock.patch.object(rd_mod, "get_detector", return_value=_on):
            with mock.patch.dict(config.__dict__,
                                 {"RESONANCE_WEIGHT_MAP_ENABLED": True}):
                PulseCore.__new__(PulseCore)._resonance_weight_update(_pulse)
        self.assertEqual(_on.get_weight("Heart", "heart.beat")[0], 2.0,
                         "开启 ⇒ 累加（与默认值无关）")

        _off = ResonanceDetector()
        with mock.patch.object(rd_mod, "get_detector", return_value=_off):
            with mock.patch.dict(config.__dict__,
                                 {"RESONANCE_WEIGHT_MAP_ENABLED": False}):
                PulseCore.__new__(PulseCore)._resonance_weight_update(_pulse)
        self.assertEqual(_off.get_weight("Heart", "heart.beat")[0], 1.0,
                         "关闭 ⇒ 零行为（这才是「默认关闭」要保证的事）")

    def test_switch_value_documented(self):
        """★363刀J1：开关当前值必须是**布尔**，且生产读取点存在（防整条链被悄悄摘掉）。"""
        self.assertIsInstance(config.RESONANCE_WEIGHT_MAP_ENABLED, bool)
        _core_src = open(os.path.join(_ROOT, "nucleus", "pulse", "PulseCore.py"),
                         encoding="utf-8").read()
        self.assertIn("RESONANCE_WEIGHT_MAP_ENABLED", _core_src,
                      "生产读取点必须还在（nucleus/pulse/PulseCore.py）")

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
