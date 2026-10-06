# -*- coding: utf-8 -*-
"""163批 刀8（P0★）门控单测：主循环/保活脉冲化网关（灰度 OFF 默认零行为变化）。

- 开关关闭（默认）→ _should_use_pulse_gateway()=False；wait_heartbeat_pulse 超时返回 False（无挂死）。
- on_pulse 收到 heart.beat → _on_heartbeat_pulse 置位事件 → wait_heartbeat_pulse 被唤醒且自动复位。
- 开关开启 → _should_use_pulse_gateway()=True（由 config.MAIN_LOOP_PULSE_ENABLED 控制）。
"""
import unittest

import config as _cfg

from organs.motor.PulseController import PulseController


class TestMainLoopPulseKnife8(unittest.TestCase):
    def setUp(self):
        self._orig_switch = getattr(_cfg, "MAIN_LOOP_PULSE_ENABLED", None)
        self._ctrl = PulseController("控制器")

    def tearDown(self):
        if self._orig_switch is None:
            if hasattr(_cfg, "MAIN_LOOP_PULSE_ENABLED"):
                del _cfg.MAIN_LOOP_PULSE_ENABLED
        else:
            _cfg.MAIN_LOOP_PULSE_ENABLED = self._orig_switch

    def test_default_off_zero_regression(self):
        # 默认开关关闭 → 网关不启用（循环走原 time.sleep，零行为变化）
        self.assertFalse(self._ctrl._should_use_pulse_gateway())
        # 事件未置位 → 短超时返回 False（不挂死）
        self.assertFalse(self._ctrl.wait_heartbeat_pulse(0.05))

    def test_gateway_reflects_config_on(self):
        _cfg.MAIN_LOOP_PULSE_ENABLED = True
        self.assertTrue(self._ctrl._should_use_pulse_gateway())
        _cfg.MAIN_LOOP_PULSE_ENABLED = False
        self.assertFalse(self._ctrl._should_use_pulse_gateway())

    def test_on_heartbeat_pulse_sets_and_wakes(self):
        # 初始事件未置位
        self.assertFalse(self._ctrl.wait_heartbeat_pulse(0.02))
        # heart.beat 到达 → 置位并返回 acknowledged
        r = self._ctrl._on_heartbeat_pulse({"beat": 1})
        self.assertEqual(r.get("status"), "acknowledged")
        # 阻塞等待被脉冲唤醒 → 返回 True
        self.assertTrue(self._ctrl.wait_heartbeat_pulse(0.5))
        # 唤醒后自动复位 → 再次短等超时返回 False
        self.assertFalse(self._ctrl.wait_heartbeat_pulse(0.02))

    def test_on_pulse_routes_heart_beat(self):
        # on_pulse 仅在器官运行时收脉冲（生产态守卫）
        self._ctrl.is_running = True
        # on_pulse 对 heart.beat 事件返回 acknowledged（不报错、不误入其他分支）
        r = self._ctrl.on_pulse({"event_type": "heart.beat", "payload": {}})
        self.assertEqual(r.get("status"), "acknowledged")


if __name__ == "__main__":
    unittest.main()
