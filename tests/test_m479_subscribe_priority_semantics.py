# -*- coding: utf-8 -*-
"""★479 收尾④：订阅优先级判据的**语义固化**（核实结论：不是 bug，不许翻转）。

## 为什么专门写这个文件

交接单里长期挂着一项「R6：`priority < min_priority` 语义反转（真 bug）」。
实测核实后结论相反 —— **方向是对的**，翻转才会真的把系统搞坏：

`config.PULSE_PRIORITY` 的定义是「**数字越大越重要**」：
    LIGHT_SPEED=10 / CRITICAL=9 / HIGH=7 / NORMAL=5 / LOW=3 / BACKGROUND=1
而 `min_priority` 是「订阅者要求的最低重要性档」。

因此 `priority < min_priority ⇒ 拒绝` = **拒绝重要性不够的脉冲**，语义正确：
    · min_priority=1（BACKGROUND）⇒ 拒绝 priority=-1/0（低于 BACKGROUND 的）
    · min_priority=7（HIGH）      ⇒ 拒绝 NORMAL(5)/LOW(3)/BACKGROUND(1)

★若按「真 bug」去翻转成 `priority > min_priority`：
    · min_priority=1 会拒绝 **NORMAL(5)/HIGH(7)/CRITICAL(9) 全部**；
    · 而 `register_condition(min_priority=1)` 是 main.py 里的**主流传值**；
    ⇒ 等于一次瘫痪全部订阅准入。

★误判来源（写下来防复发）：渠道池 `REMOTE_API_CHANNELS` 的 `priority` 是
  **排序序号**（1 最先选），与脉冲的 `PULSE_PRIORITY`（数值越大越重要）是
  **两套不同语义**。把前者的直觉套到后者，就会得出「反转」的错误结论。

本文件的用例在「翻转判据」时**必然全红**，这就是它存在的意义。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus.field.InfoField import InfoField
from nucleus.synapsys.ResonanceEngine import ResonanceEngine


class TestPulsePriorityDirection(unittest.TestCase):
    """先钉死「数值方向」这件事本身 —— 后面所有判据都以此为前提。"""

    def test_01_higher_number_means_more_important(self):
        _p = config.PULSE_PRIORITY
        self.assertGreater(_p["LIGHT_SPEED"], _p["CRITICAL"])
        self.assertGreater(_p["CRITICAL"], _p["HIGH"])
        self.assertGreater(_p["HIGH"], _p["NORMAL"])
        self.assertGreater(_p["NORMAL"], _p["LOW"])
        self.assertGreater(_p["LOW"], _p["BACKGROUND"])

    def test_02_background_is_lowest_positive(self):
        self.assertEqual(config.PULSE_PRIORITY["BACKGROUND"], 1)
        self.assertEqual(config.PULSE_PRIORITY["NORMAL"], 5)

    def test_03_channel_priority_is_a_different_concept(self):
        """反证：渠道池的 priority 是「排序序号」，越小越先选。

        这条断言的意义：把两套语义混为一谈是「R6 是 bug」这个误判的根源。
        """
        _act = config.get_active_channels()
        if len(_act) >= 2:
            # 渠道池按 priority 升序排列 ⇒ 第 1 条的 priority 应 <= 第 2 条
            self.assertLessEqual(int(_act[0].get("priority") or 0),
                                 int(_act[1].get("priority") or 0))


class TestMatchConditionSemantics(unittest.TestCase):
    """`InfoField._match_condition` 的优先级闸（3179 行）。"""

    def _m(self, priority, min_priority, event_types=None):
        _o = InfoField.__new__(InfoField)
        return _o._match_condition(
            {"event_type": "x.y", "priority": priority},
            {"min_priority": min_priority,
             "event_types": event_types if event_types is not None else []})

    def test_10_normal_passes_background_gate(self):
        self.assertTrue(self._m(config.PULSE_PRIORITY["NORMAL"], 1))

    def test_11_below_gate_rejected(self):
        """priority=-1 低于 BACKGROUND(1) ⇒ 拒绝（PulseLung 就发 -1）。"""
        self.assertFalse(self._m(-1, 1))
        self.assertFalse(self._m(0, 1))

    def test_12_boundary_equal_passes(self):
        """等于门槛应放行（`<` 不是 `<=`）。"""
        self.assertTrue(self._m(1, 1))

    def test_13_high_gate_rejects_normal(self):
        """min_priority=HIGH(7) ⇒ NORMAL(5) 不够，必须拒。"""
        self.assertFalse(self._m(config.PULSE_PRIORITY["NORMAL"],
                                 config.PULSE_PRIORITY["HIGH"]))
        self.assertTrue(self._m(config.PULSE_PRIORITY["HIGH"],
                                config.PULSE_PRIORITY["HIGH"]))

    def test_14_default_min_priority_zero(self):
        """未声明 min_priority ⇒ 默认 0 ⇒ priority=-1 才被拒。"""
        _o = InfoField.__new__(InfoField)
        self.assertTrue(_o._match_condition({"event_type": "x", "priority": 0}, {}))
        self.assertFalse(_o._match_condition({"event_type": "x", "priority": -1}, {}))

    def test_15_pulse_missing_priority_defaults_5(self):
        _o = InfoField.__new__(InfoField)
        self.assertTrue(_o._match_condition({"event_type": "x"}, {"min_priority": 1}))


class TestResonanceEngineSameSemantics(unittest.TestCase):
    """`ResonanceEngine.match_condition_advanced` 必须与 InfoField 同口径。

    两处判据分居两个模块，任何一处被「修正」成 `>` 都会造成不对称 ——
    这类不一致在运行期极难发现（一边放行一边拒绝）。
    """

    def _m(self, priority, min_priority):
        _e = ResonanceEngine.__new__(ResonanceEngine)
        return _e.match_condition_advanced(
            {"event_type": "x.y", "priority": priority},
            {"min_priority": min_priority, "event_types": []})

    def test_20_same_table_as_infofield(self):
        for _p, _mp, _expect in (
            (config.PULSE_PRIORITY["NORMAL"], 1, True),
            (-1, 1, False),
            (0, 1, False),
            (1, 1, True),
            (config.PULSE_PRIORITY["NORMAL"], 7, False),
            (config.PULSE_PRIORITY["HIGH"], 7, True),
        ):
            self.assertEqual(self._m(_p, _mp), _expect,
                             f"priority={_p} min_priority={_mp}")

    def test_21_guard_against_flip(self):
        """★核心防回潮：翻转判据后这组断言必然失败。

        若有人把 `priority < min_priority` 改成 `>`，则
        NORMAL(5) 会被 min_priority=1 拒掉（本条断言 False != True 直接红）。
        """
        self.assertTrue(self._m(config.PULSE_PRIORITY["NORMAL"], 1),
                        "NORMAL 被 min_priority=1 拒了 ⇒ 判据被翻转了")

    def test_22_source_uses_less_than(self):
        _p = os.path.join(ROOT, "nucleus", "synapsys", "ResonanceEngine.py")
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        self.assertIn('pulse.get("priority", 5) < condition.get("min_priority", 0)',
                      _src, "判据方向被改了 —— 翻转会让全部订阅瘫痪")

    def test_23_infofield_source_uses_less_than(self):
        _p = os.path.join(ROOT, "nucleus", "field", "InfoField.py")
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        self.assertIn('if priority < condition.get("min_priority", 0):', _src,
                      "InfoField 侧判据方向被改了")


if __name__ == "__main__":
    unittest.main()
