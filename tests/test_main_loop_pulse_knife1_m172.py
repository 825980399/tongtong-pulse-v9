# -*- coding: utf-8 -*-
"""172刀1 门控单测：周期任务/探针环节拍脉冲化（安全集）。

- 原语 wait_heartbeat_pulse 行为回归（唤醒/自动复位/超时返回 False 不挂死）。
- 三处周期/探针节拍调用点已转换为 wait_heartbeat_pulse：
    探针环 _probe_interval 保留为 timeout 实参（键名/默认值零变更）。
- 一次性重启/停机 sleep 未被误改（安全集约束）。
- DailyScheduler 未被改动（其循环用 self._stop.wait，非 time.sleep）。
- 三处转换均使用类方法内可达引用（self.controller / _lv_fw.controller），
    不使用类方法作用域不可达的 framework 引用。
"""
import os
import unittest

from organs.motor.PulseController import PulseController

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN = os.path.join(_REPO, "main.py")
_DS = os.path.join(_REPO, "nucleus", "self_awareness", "DailyScheduler.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class TestMainLoopPulseKnife1(unittest.TestCase):
    def test_primitive_wait_heartbeat_pulse(self):
        ctrl = PulseController("控制器")
        # 未置位 → 短超时返回 False（不挂死）
        self.assertFalse(ctrl.wait_heartbeat_pulse(0.05))
        # 收到心跳 → 置位唤醒并自动复位
        ctrl._on_heartbeat_pulse({"beat": 1})
        self.assertTrue(ctrl.wait_heartbeat_pulse(0.5))
        self.assertFalse(ctrl.wait_heartbeat_pulse(0.02))

    def test_call_sites_converted(self):
        src = _read(_MAIN)
        # 三处周期/探针节拍已转脉冲
        self.assertIn("wait_heartbeat_pulse(_probe_interval)", src)
        self.assertIn("wait_heartbeat_pulse(_m61_evo_next)", src)
        self.assertIn("wait_heartbeat_pulse(10)", src)
        # 三处均使用类方法内可达引用
        self.assertEqual(src.count("self.controller if self is not None else None"), 2)
        self.assertEqual(src.count('getattr(_lv_fw, "controller", None)'), 1)
        # 主循环 4027 既有 framework 引用保留 1 处（未被本刀误改/未误删）
        self.assertEqual(
            src.count("framework.controller if framework is not None else None"), 1
        )

    def test_one_shot_sleeps_not_converted(self):
        src = _read(_MAIN)
        # 一次性重启等待（3s）保留为普通 sleep，不被脉冲化
        self.assertIn("time.sleep(3)", src)
        # 停机释放 socket（0.3s）保留
        self.assertIn("time.sleep(0.3)", src)
        self.assertIn("time.sleep(0.2)", src)
        # DailyScheduler 未改（循环用 self._stop.wait，非 time.sleep）
        ds = _read(_DS)
        self.assertNotIn("wait_heartbeat_pulse", ds)
        self.assertIn("self._stop.wait", ds)


if __name__ == "__main__":
    unittest.main()
