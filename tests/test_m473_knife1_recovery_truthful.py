# -*- coding: utf-8 -*-
"""第473批 刀1 门控单测：恢复尝试结果真实性（台账 C3）。

根因
----
`organs/core/PulseEmergencyHandler.py::_attempt_recovery` 原实现：

    recovery_strategies = {...}          # 四个策略，纯字符串
    strategy = recovery_strategies.get(alarm_type, "通用恢复策略")
    self._log(LogLevel.INFO, f"尝试恢复: {strategy}")
    return True                          # ← 只打日志，一条动作都没有

后果：
  * `_successful_recoveries` 恒等于 `_recovery_attempts` ⇒ 台账上
    「恢复成功率 100%」是彻底的假账；
  * `RECOVERY_ATTEMPT.success` 永远 true ⇒ 下游看到的「恢复有效」全是虚信号；
  * `organ_failure`（重启失败器官）**从来没有实现过**，却一直在报成功。

修复口径
--------
返回值改按真实执行结果：
  * energy_critical / memory_high ⇒ 强制 GC + 降并行度，两者都成功才 True
  * cpu_high                      ⇒ 降并行度成功才 True
  * organ_failure                 ⇒ 无动作（缺 orchestrator 句柄）⇒ 恒 False
  * 未知类型                       ⇒ 恒 False
灰度：`config.RECOVERY_TRUTHFUL_RESULT = False` 回到旧的恒 True。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from organs.core.PulseEmergencyHandler import PulseEmergencyHandler

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeInfoField:
    """最小信息场替身：只记录 set_load_level 收到的档位。"""

    def __init__(self):
        self.levels = []
        self.published = []

    def set_load_level(self, level):
        self.levels.append(level)

    def publish(self, pulse):
        self.published.append(pulse)


def _make_handler(with_info_field=True):
    h = PulseEmergencyHandler("紧急处理")
    if with_info_field:
        h.set_info_field(_FakeInfoField())
    return h


class TestA_ReturnValueReflectsReality(unittest.TestCase):
    """判据：返回值必须能区分「真做了」与「根本没这动作」。"""

    def test_01_organ_failure_is_false(self):
        """organ_failure 从来就没有实现（缺 orchestrator 句柄）⇒ 必须记失败。"""
        h = _make_handler()
        self.assertFalse(h._attempt_recovery("organ_failure"),
                         "organ_failure 没有对应动作却返回 True —— C3 未修")

    def test_02_unknown_type_is_false(self):
        h = _make_handler()
        self.assertFalse(h._attempt_recovery("完全不存在的告警类型"),
                         "未知告警类型返回 True —— 等于承认「什么都能救」")

    def test_03_energy_critical_true_when_actions_work(self):
        h = _make_handler()
        self.assertTrue(h._attempt_recovery("energy_critical"))
        # 动作必须真的发生过：降级 level 落到了信息场
        self.assertEqual(h.info_field.levels, ["critical"],
                         f"energy_critical 应把并行度降到 critical，实得 {h.info_field.levels!r}")

    def test_04_memory_high_downgrades_to_heavy(self):
        h = _make_handler()
        self.assertTrue(h._attempt_recovery("memory_high"))
        self.assertEqual(h.info_field.levels, ["heavy"])

    def test_05_cpu_high_downgrades_to_heavy(self):
        h = _make_handler()
        self.assertTrue(h._attempt_recovery("cpu_high"))
        self.assertEqual(h.info_field.levels, ["heavy"])

    def test_06_no_info_field_is_false(self):
        """缺依赖必须记失败，不能因为「我尽力了」就算成功。"""
        h = _make_handler(with_info_field=False)
        self.assertFalse(h._attempt_recovery("cpu_high"),
                         "info_field 未注入却返回 True —— 动作根本没下达")

    def test_07_info_field_without_setter_is_false(self):
        class _NoSetter:
            pass

        h = PulseEmergencyHandler("紧急处理")
        h.set_info_field(_NoSetter())
        self.assertFalse(h._attempt_recovery("memory_high"))


class TestB_CountersNoLongerFake(unittest.TestCase):
    """判据：成功数不得再恒等于尝试数（这是台账假账的直接来源）。"""

    def _alarm(self, h, alarm_type, level="L3"):
        return h.on_pulse({
            "event_type": "system.alarm",
            "payload": {"level": level, "type": alarm_type},
            "priority": 9,
        })

    def test_01_success_rate_not_always_100pct(self):
        h = _make_handler()
        self._alarm(h, "organ_failure")   # 必失败
        self._alarm(h, "energy_critical")  # 必成功
        self.assertEqual(h._recovery_attempts, 2)
        self.assertEqual(h._successful_recoveries, 1,
                         f"成功数应只记 1 条（organ_failure 是无动作的），"
                         f"实得 attempts={h._recovery_attempts} success={h._successful_recoveries}")

    def test_02_all_fail_case_yields_zero(self):
        h = _make_handler()
        for _t in ("organ_failure", "unknown_x", "unknown_y"):
            self._alarm(h, _t)
        self.assertEqual(h._recovery_attempts, 3)
        self.assertEqual(h._successful_recoveries, 0,
                         "三条都无动作，成功数必须为 0（旧实现这里会是 3）")

    def test_03_event_payload_success_matches(self):
        """RECOVERY_ATTEMPT 事件里的 success 必须与真实结果一致。"""
        h = _make_handler()
        self._alarm(h, "organ_failure")
        _pulses = [p for p in h.info_field.published
                   if p.get("event_type") == "system.recovery_attempt"]
        self.assertTrue(_pulses, "没有发出 RECOVERY_ATTEMPT 留痕")
        self.assertIs(_pulses[-1]["payload"]["success"], False,
                      f"事件里 success 应为 False，实得 {_pulses[-1]['payload']['success']!r}")


class TestC_GraySwitchRollback(unittest.TestCase):
    """判据：关掉开关必须完整回到旧行为（恒 True），用于应急回滚。"""

    def test_01_switch_off_restores_always_true(self):
        h = _make_handler()
        with mock.patch.object(config, "RECOVERY_TRUTHFUL_RESULT", False):
            self.assertTrue(h._attempt_recovery("organ_failure"),
                            "灰度关闭后应返回 True（旧行为）")
            self.assertTrue(h._attempt_recovery("unknown_zzz"))

    def test_02_switch_off_keeps_counter_equal(self):
        h = _make_handler()
        with mock.patch.object(config, "RECOVERY_TRUTHFUL_RESULT", False):
            for _t in ("organ_failure", "unknown_x"):
                h.on_pulse({"event_type": "system.alarm",
                            "payload": {"level": "L3", "type": _t}, "priority": 9})
        self.assertEqual(h._recovery_attempts, h._successful_recoveries,
                         "灰度关闭时应完整复现旧行为（成功数 == 尝试数）")

    def test_03_switch_default_is_on(self):
        self.assertTrue(getattr(config, "RECOVERY_TRUTHFUL_RESULT", None) is True,
                        "config.RECOVERY_TRUTHFUL_RESULT 默认必须是 True")


class TestD_SourceLevelGuards(unittest.TestCase):
    """源码级：防后人把 `return True` 又接回方法尾部。"""

    def _src(self):
        with open(os.path.join(_ROOT, "organs", "core", "PulseEmergencyHandler.py"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_01_no_log_then_return_true(self):
        src = self._src()
        self.assertNotIn('self._log(LogLevel.INFO, f"尝试恢复: {strategy}")\n        return True',
                         src.replace("\r\n", "\n"),
                         "方法尾部又变回「打一行日志就 return True」—— C3 回归")

    def test_02_executor_exists(self):
        self.assertIn("_m473_execute_recovery", self._src())

    def test_03_organ_failure_explicitly_false(self):
        src = self._src()
        _i = src.find('if alarm_type == "organ_failure":')
        self.assertGreater(_i, 0, "organ_failure 分支不见了")
        self.assertIn("return False", src[_i:_i + 400],
                      "organ_failure 分支必须显式 return False")


if __name__ == "__main__":
    unittest.main(verbosity=2)
