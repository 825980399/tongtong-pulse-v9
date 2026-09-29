# -*- coding: utf-8 -*-
"""第117批 T-117d①（N4）：问题身份键的 file 形制归一（normcase + relpath）。

病：账内同一目标文件三种形制并存（反斜杠相对 / 正斜杠相对 / 绝对），
    被算成 2~3 个不同键 ⇒ 冷却计数稀释、去重失效、分组错位。
"""
from __future__ import annotations

import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEE_SRC = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")


def _load_see():
    _spec = importlib.util.spec_from_file_location("SEE_m117_path", _SEE_SRC)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    try:
        from nucleus.logger import get_smoke_logger
        _mod._module_logger = get_smoke_logger("m117_path_norm")
    except Exception:
        pass
    return _mod


class T117dPathNormalization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_see()
        cls.SEE = cls.mod.SafeEvolutionExecutor

    def _key(self, file_val):
        return self.SEE._cooldown_key(
            {"file": file_val, "method": "m", "type": "T"})

    def test_01_three_forms_converge(self):
        """反斜杠相对 / 正斜杠相对 / 绝对 —— 必须收敛成同一个键。"""
        _forms = [
            "organs/brain/PulseSubconscious.py",
            "organs\\brain\\PulseSubconscious.py",
            os.path.join(ROOT, "organs", "brain", "PulseSubconscious.py"),
            "./organs/brain/PulseSubconscious.py",
        ]
        _keys = {self._key(f) for f in _forms}
        self.assertEqual(1, len(_keys),
                         f"四种形制应收敛为 1 个键，实际 {len(_keys)}：{_keys}")

    def test_02_case_insensitive_on_windows(self):
        """normcase：Windows 下大小写不同也应视为同一目标。"""
        if os.name != "nt":
            self.skipTest("非 Windows，normcase 为空操作")
        self.assertEqual(
            self._key("organs/BRAIN/PulseSubconscious.PY"),
            self._key("organs/brain/pulsesubconscious.py"))

    def test_03_different_files_stay_distinct(self):
        """归一不得过度合并 —— 不同文件仍应是不同键。"""
        self.assertNotEqual(
            self._key("organs/brain/PulseSubconscious.py"),
            self._key("organs/brain/PulseCortex.py"))

    def test_04_no_file_falls_back_to_organ_desc(self):
        """回归：file/method 皆空时仍退化为 organ|type|描述摘要（D3 语义不变）。"""
        _k = self.SEE._cooldown_key(
            {"organ": "心脏", "type": "E", "description": "d"})
        self.assertTrue(_k.startswith("organ:心脏|type:E|"), _k)

    def test_05_empty_file_is_empty_string(self):
        self.assertEqual("", self.mod._normalize_file_key(""))
        self.assertEqual("", self.mod._normalize_file_key(None))


if __name__ == "__main__":
    unittest.main()
