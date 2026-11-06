# -*- coding: utf-8 -*-
"""第472批 刀4 门控单测：紧急处理链两处断点（台账 C1 + C2）。

C1 `organs/core/PulseHealthMonitor.py`
    能量唯一写入方 `PulseEnergyMetabolism._update_energy` 用 `max(0.1, 1-load)`，
    值恒 >= 0.1；`el < 0.1` 恒 False ⇒ L3「危急」永远不触发。改 `<=` 后触底即危急。

C2 `organs/senses/PulseTouch.py`
    hardware_critical 的 payload 缺 `level`，消费者的 `payload.get("level","L1")`
    拿到 L1 ⇒ `not in ("L3","L4")` 早退 ⇒ priority=10 的告警只剩一行日志。
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc
from organs.core.PulseEmergencyHandler import PulseEmergencyHandler
from organs.core.PulseHealthMonitor import PulseHealthMonitor

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestA_EnergyCriticalReachable(unittest.TestCase):
    """判据：能量触底（0.1）必须判为危急；0.11 不该误报。"""

    def setUp(self):
        self._h = PulseHealthMonitor()
        self._h._latest_hw = {}

    def test_01_floor_energy_is_critical(self):
        self._h._latest_energy = {"energy_level": 0.1}
        _r = self._h._on_check({})
        self.assertEqual(_r["status"], "critical",
                         "能量已经触底（0.1）却判 healthy —— C1 未修")
        self.assertGreaterEqual(_r["alarms"], 1)

    def test_02_above_floor_not_critical(self):
        """反向判据：`<=` 不能扩成把整个 L2 档也判危急。0.11 应落 L2（warning）。"""
        self._h._latest_energy = {"energy_level": 0.11}
        _r = self._h._on_check({})
        self.assertEqual(_r["status"], "warning", f"0.11 应落 L2 偏低档，实得 {_r!r}")

    def test_02b_healthy_band(self):
        self._h._latest_energy = {"energy_level": 0.5}
        _r = self._h._on_check({})
        self.assertEqual(_r["status"], "healthy", f"0.5 应健康，实得 {_r!r}")

    def test_03_low_band_still_l2(self):
        self._h._latest_energy = {"energy_level": 0.2}
        _r = self._h._on_check({})
        self.assertEqual(_r["status"], "warning", f"0.2 应落在 L2 偏低档，实得 {_r!r}")

    def test_04_source_uses_le(self):
        """源码级：判定必须是 `<=`（防后人改回 `<` 又变成恒 False）。"""
        _p = os.path.join(_ROOT, "organs", "core", "PulseHealthMonitor.py")
        with open(_p, encoding="utf-8") as _fh:
            _src = _fh.read()
        self.assertIn('if el <= self._thresholds["energy_critical"]:', _src)
        self.assertNotIn('if el < self._thresholds["energy_critical"]:', _src)


def _hardware_payload_dicts(src_path):
    """从源码里捞出所有含 type=hardware_critical 的字典字面量。"""
    with open(src_path, encoding="utf-8") as _fh:
        _tree = ast.parse(_fh.read())
    _found = []
    for _node in ast.walk(_tree):
        if not isinstance(_node, ast.Dict):
            continue
        _kv = {}
        _ok = True
        for _k, _v in zip(_node.keys, _node.values):
            if not isinstance(_k, ast.Constant):
                _ok = False
                break
            try:
                _kv[_k.value] = ast.literal_eval(_v)
            except Exception as _e:
                # 非字面量（变量/调用表达式）取不到值，标记占位即可 —— 但要留痕，
                # 不做静默吞（铁律 4：except 里赋值同样算静默）。
                silent_exc(_e, where="tests/test_m472_knife4:literal_eval", level="debug")
                _kv[_k.value] = "<expr>"
        if _ok and _kv.get("type") == "hardware_critical":
            _found.append(_kv)
    return _found


class TestB_HardwareCriticalCarriesLevel(unittest.TestCase):
    """判据：PulseTouch 发出的 hardware_critical 必须自带 level="L3"。"""

    def setUp(self):
        self._p = os.path.join(_ROOT, "organs", "senses", "PulseTouch.py")
        self._dicts = _hardware_payload_dicts(self._p)

    def test_05_payload_found(self):
        self.assertTrue(self._dicts, "没找到 hardware_critical 的 payload —— 判据定位失效")

    def test_06_every_payload_has_level_l3(self):
        for _d in self._dicts:
            self.assertIn("level", _d, f"payload 缺 level ⇒ 消费方按 L1 早退: {_d}")
            self.assertEqual(_d["level"], "L3", f"level 必须是 L3: {_d}")

    def test_07_design_intent_matches_selftest(self):
        """交叉验证：EmergencyHandler 文末自测用的就是 level="L3"（设计意图锚点）。"""
        _p = os.path.join(_ROOT, "organs", "core", "PulseEmergencyHandler.py")
        with open(_p, encoding="utf-8") as _fh:
            _src = _fh.read()
        self.assertIn('"level": "L3", "type": "hardware_critical"', _src)


class TestC_ConsumerBehavesOnLevel(unittest.TestCase):
    """行为判据：level 是这个链路的开关（证明 C2 的修法确实接通）。"""

    def setUp(self):
        self._eh = PulseEmergencyHandler()

    def test_08_l3_enters_recovery(self):
        _r = self._eh._on_alarm({"type": "hardware_critical", "level": "L3"})
        self.assertEqual(_r["status"], "recovery_attempted",
                         f"L3 必须进恢复链，实得 {_r!r}")

    def test_09_missing_level_falls_back_to_logged(self):
        """旧行为复现：这正是 C2 描述的那条早退路径。"""
        _r = self._eh._on_alarm({"type": "hardware_critical"})
        self.assertEqual(_r["status"], "logged", f"缺 level 应早退，实得 {_r!r}")

    def test_10_l4_enters_safe_mode(self):
        """边界守卫：L4 才进安全模式，hardware_critical 用 L3 不会误触。"""
        _r = self._eh._on_alarm({"type": "kernel_panic", "level": "L4"})
        self.assertEqual(_r["status"], "safe_mode_activated")


if __name__ == "__main__":
    unittest.main()
