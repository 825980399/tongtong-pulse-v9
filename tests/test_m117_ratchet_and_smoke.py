# -*- coding: utf-8 -*-
"""往期批次 相关任务（断5 棘轮自愈）+ 相关任务②（冒烟隔离规矩）门禁单测。

设计原则：离线、隔离、不读生产账本；SEE 用 importlib 独立加载，
并且**先把模块日志器换成 smoke 日志器**，避免合成指纹污染 pulse.log（B22）。
"""
from __future__ import annotations

import importlib.util
import logging
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEE_SRC = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_LOGGER_SRC = os.path.join(ROOT, "nucleus", "logger.py")
CALL = "self._m114a_clear_ratchet("


def _load_see():
    _spec = importlib.util.spec_from_file_location("SEE_m117_test", _SEE_SRC)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    # ★相关任务②：合成指纹一律走 smoke 日志器，不污染 pulse.log
    try:
        from nucleus.logger import get_smoke_logger
        _mod._module_logger = get_smoke_logger("m117_ratchet")
    except Exception:
        pass
    return _mod


class T117bRatchetSelfHeal(unittest.TestCase):
    """N1：成功侧出清 —— 棘轮必须可逆。"""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_see()
        cls.SEE = cls.mod.SafeEvolutionExecutor

    def _fake(self, rounds=None):
        """造一个「isinstance(self, SafeEvolutionExecutor) 为真」但不落盘的替身。"""
        SEE = self.SEE

        class _FakeSEE(SEE):
            def __init__(self):
                self._no_fix_cooldown = {}
                self._no_fix_cooldown_rounds = dict(rounds or {})
                self.saved = 0

            def _m114a_save_cooldown(self):
                self.saved += 1

            def _m114a_cooldown_path(self):
                return ""

        return _FakeSEE()

    def test_01_three_failures_then_skip(self):
        """基线：>=3 连败 → 跳过（断5 硬闸生效）。"""
        _iss = {"file": "x.py", "method": "m", "type": "T"}
        _fp = self.SEE._cooldown_key(_iss)
        _f = self._fake({_fp: 3})
        self.assertTrue(self.SEE._m114a_should_skip_ask(_f, _iss),
                        "3 连败应触发跳过")

    def test_02_success_clears_ratchet(self):
        """★核心：成功后连败清零 → 不再跳过（棘轮自愈，不再是单向死闸）。"""
        _iss = {"file": "x.py", "method": "m", "type": "T"}
        _fp = self.SEE._cooldown_key(_iss)
        _f = self._fake({_fp: 3})
        self.assertTrue(self.SEE._m114a_should_skip_ask(_f, _iss))

        _f._m114a_clear_ratchet(_fp, reason="单测模拟成功")

        self.assertNotIn(_fp, _f._no_fix_cooldown_rounds, "成功后连败计数应被清零")
        self.assertFalse(self.SEE._m114a_should_skip_ask(_f, _iss),
                         "清零后不得再跳过（棘轮必须可逆）")
        self.assertGreaterEqual(_f.saved, 1, "出清后应触发一次冷却落盘")

    def test_03_clear_is_noop_without_record(self):
        """无连败记录时短路：不抛异常、不落盘、不产生噪音。"""
        _f = self._fake({})
        _before = _f.saved
        _f._m114a_clear_ratchet("no/such/fp", reason="无记录")
        self.assertEqual(_before, _f.saved, "无记录时不应触发落盘")
        self.assertEqual({}, _f._no_fix_cooldown_rounds)

    def test_04_dummy_placeholder_early_return(self):
        """占位/Dummy 实例早退（相关任务 防御），不落盘。"""
        class _Dummy:
            pass

        _d = _Dummy()
        self.assertIsNone(self.SEE._m114a_clear_ratchet(_d, "fp"),
                          "Dummy 实例应早退返回 None")

    def test_05_two_success_sites_wired(self):
        """静态契约：本地修复成功 + 补丁验证成功，两处都必须接线。"""
        with open(_SEE_SRC, "r", encoding="utf-8") as f:
            _src = f.read()
        _n_calls = _src.count(CALL)
        self.assertEqual(
            2, _n_calls,
            f"应有 2 处成功侧调用（本地修复 + 补丁验证），实际 {_n_calls}")
        self.assertIn('reason="本地修复验证通过"', _src)
        self.assertIn('reason="补丁验证通过"', _src)

    def test_06_register_and_clear_are_symmetric(self):
        """登记与出清必须对称（同一指纹键、同一本账）。"""
        _iss = {"file": "y.py", "method": "n", "type": "T"}
        _fp = self.SEE._cooldown_key(_iss)
        _f = self._fake({})
        # 连续 3 次失败登记
        for _ in range(3):
            _f._m114a_register_verify_failure(_fp, detail="单测")
        self.assertEqual(3, _f._no_fix_cooldown_rounds.get(_fp))
        self.assertTrue(self.SEE._m114a_should_skip_ask(_f, _iss))
        # 一次成功 -> 归零
        _f._m114a_clear_ratchet(_fp, reason="单测")
        self.assertFalse(self.SEE._m114a_should_skip_ask(_f, _iss))


class T117dSmokeIsolation(unittest.TestCase):
    """B22：冒烟/合成指纹日志不得落进 pulse.log。"""

    def test_01_smoke_logger_isolated_and_prefixed(self):
        from nucleus.logger import SMOKE_TAG, get_smoke_logger
        _lg = get_smoke_logger("m117_case")
        self.assertFalse(_lg.propagate, "smoke 日志器必须 propagate=False（不冒泡到 pulse）")
        _hs = [h for h in _lg.handlers if getattr(h, "_pulse_smoke", False)]
        self.assertTrue(_hs, "smoke 日志器应有独立文件 handler")
        self.assertTrue(_hs[0].baseFilename.endswith("smoke.log"),
                        f"smoke 日志必须写独立文件，实际 {_hs[0].baseFilename}")
        _rec = logging.LogRecord("pulse.smoke.m117_case", logging.INFO,
                                 __file__, 1, "hello", None, None)
        self.assertIn(SMOKE_TAG, _hs[0].formatter.format(_rec),
                      "每条 smoke 日志必须带 [SMOKE] 前缀")

    def test_02_smoke_handler_not_shared_with_pulse(self):
        from nucleus.logger import get_smoke_logger
        _lg = get_smoke_logger("m117_case2")
        _pulse = logging.getLogger("pulse")
        _overlap = [h for h in _lg.handlers if h in _pulse.handlers]
        self.assertEqual([], _overlap,
                         "smoke handler 不得与 pulse 根日志器共享")

    def test_03_synthetic_fingerprint_never_touches_pulse_log(self):
        """行为验证：合成指纹驱动的硬闸日志不写 pulse.log。"""
        from nucleus.logger import get_smoke_logger
        _mod = _load_see()
        _lg = get_smoke_logger("m117_case3")
        _mod._module_logger = _lg
        self.assertIs(_lg, _mod._module_logger)

        _pulse_log = os.path.join(ROOT, "logs", "pulse.log")
        _before = os.path.getsize(_pulse_log) if os.path.exists(_pulse_log) else None
        if _before is None:
            self.skipTest("pulse.log 不存在，跳过体积对照")

        _iss = {"file": "a.py", "method": "m", "type": "silent_exception"}
        _fp = _mod.SafeEvolutionExecutor._cooldown_key(_iss)

        class _F(_mod.SafeEvolutionExecutor):
            def __init__(self):
                self._no_fix_cooldown = {}
                self._no_fix_cooldown_rounds = {_fp: 5}

            def _m114a_save_cooldown(self):
                return None

            def _m114a_cooldown_path(self):
                return ""

        self.assertTrue(_mod.SafeEvolutionExecutor._m114a_should_skip_ask(_F(), _iss))
        _after = os.path.getsize(_pulse_log)
        self.assertEqual(
            _before, _after,
            f"合成指纹冒烟后 pulse.log 体积不得变化（B22）：{_before} -> {_after}")


if __name__ == "__main__":
    unittest.main()
