# -*- coding: utf-8 -*-
"""474 刀23：PATH 切 3.14t 后定期测试假超时的门控单测。

判据纪律：
  · 反证/判别力（铁律 35）：`wait_encoder_ready` 对 disabled 与 loading
    两种输入的 sleep 次数必须**显著不同**，否则「早退」逻辑在旧实现上也成立。
  · 独立基准（铁律 30）：期望值来自手工构造的假编码器与假时钟，
    不拿被测函数的输出反推。
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config
from nucleus.evolution.PeriodicTestScheduler import _periodic_test_python

_VSK = os.path.join(_ROOT, "tools", "verify_semantic_kernel.py")
_spec = importlib.util.spec_from_file_location("_vsk_m474_k23", _VSK)
_vsk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_vsk)
wait_encoder_ready = _vsk.wait_encoder_ready


class _Clock:
    """假时钟：每次读 +1 秒，保证循环有限且可数。"""

    def __init__(self):
        self.t = 0.0
        self.sleeps = 0

    def now(self):
        self.t += 1.0
        return self.t

    def sleep(self, _sec):
        self.sleeps += 1


class _Encoder:
    def __init__(self, state: str, available: bool = False):
        self.state = state
        self.available = available

    def is_available(self):
        return self.available

    def status(self):
        return {"state": self.state}


class TestEncoderWait(unittest.TestCase):
    """disabled 早退：修的是「空等 120s 被 60s 杀掉」这个假症状。"""

    def test_01_disabled_returns_immediately_without_sleeping(self):
        _c = _Clock()
        _ok, _waited, _naps = wait_encoder_ready(
            _Encoder("disabled"), clock=_c.now, sleeper=_c.sleep)
        self.assertFalse(_ok)
        self.assertEqual(0, _c.sleeps)
        self.assertEqual(0, _naps)

    def test_02_counterproof_loading_must_sleep_more_than_disabled(self):
        """★反证：若 loading 与 disabled 的 sleep 次数一样，说明早退判据无判别力。"""
        _c_dis = _Clock()
        wait_encoder_ready(_Encoder("disabled"), timeout=5,
                           clock=_c_dis.now, sleeper=_c_dis.sleep)
        _c_load = _Clock()
        wait_encoder_ready(_Encoder("loading"), timeout=5,
                           clock=_c_load.now, sleeper=_c_load.sleep)
        self.assertGreater(_c_load.sleeps, _c_dis.sleeps)
        self.assertEqual(0, _c_dis.sleeps)

    def test_03_loading_respects_timeout_no_infinite_loop(self):
        _c = _Clock()
        _ok, _waited, _naps = wait_encoder_ready(
            _Encoder("loading"), timeout=5, clock=_c.now, sleeper=_c.sleep)
        self.assertFalse(_ok)
        self.assertLessEqual(_naps, 6)

    def test_04_ready_returns_true_immediately(self):
        _c = _Clock()
        _ok, _waited, _naps = wait_encoder_ready(
            _Encoder("ready", available=True), clock=_c.now, sleeper=_c.sleep)
        self.assertTrue(_ok)
        self.assertEqual(0, _naps)

    def test_05_default_clock_does_not_break_on_ready(self):
        _ok, _waited, _naps = wait_encoder_ready(_Encoder("ready", available=True))
        self.assertTrue(_ok)
        self.assertGreaterEqual(_waited, 0)

    def test_06_source_has_disabled_early_exit(self):
        with open(_VSK, encoding="utf-8") as _fh:
            _tree = ast.parse(_fh.read())
        _fn = next(_n for _n in ast.walk(_tree)
                   if isinstance(_n, ast.FunctionDef)
                   and _n.name == "wait_encoder_ready")
        _consts = [_n.value for _n in ast.walk(_fn)
                   if isinstance(_n, ast.Constant) and isinstance(_n.value, str)]
        self.assertIn("disabled", _consts)
        self.assertIn("break", ast.unparse(_fn))


class TestPeriodicTestPython(unittest.TestCase):
    """解释器选择：不跟 PATH 漂移，可配置、可回滚。"""

    def test_07_configured_value_wins(self):
        self.assertEqual(config.PERIODIC_TEST_PYTHON_EXE, _periodic_test_python())

    def test_08_unset_falls_back_to_sys_executable(self):
        with mock.patch.object(config, "PERIODIC_TEST_PYTHON_EXE", None, create=True):
            self.assertEqual(sys.executable, _periodic_test_python())

    def test_09_empty_string_falls_back(self):
        with mock.patch.object(config, "PERIODIC_TEST_PYTHON_EXE", "", create=True):
            self.assertEqual(sys.executable, _periodic_test_python())

    def test_10_import_failure_falls_back(self):
        with mock.patch.dict(sys.modules, {"config": None}):
            self.assertEqual(sys.executable, _periodic_test_python())

    def test_11_no_hardcoded_python_left_in_scheduler(self):
        _p = os.path.join(_ROOT, "nucleus", "evolution", "PeriodicTestScheduler.py")
        with open(_p, encoding="utf-8") as _fh:
            _src = _fh.read()
        self.assertNotIn('["python", _script]', _src)
        self.assertIn("[_periodic_test_python(), _script]", _src)

    def test_12_config_documents_exit_condition(self):
        with open(os.path.join(_ROOT, "config.py"), encoding="utf-8") as _fh:
            _src = _fh.read()
        self.assertIn("PERIODIC_TEST_PYTHON_EXE = ", _src)
        self.assertIn("退出条件", _src)
        self.assertIn("sys.executable", _src)

    def test_13_configured_interpreter_has_local_vector_backend(self):
        """真实实证：桥接目标必须真的具备 fastembed，否则修了也白修。"""
        _exe = config.PERIODIC_TEST_PYTHON_EXE
        if not os.path.isfile(_exe):
            self.skipTest("桥接解释器不在本机，跳过实证")
        import subprocess
        _r = subprocess.run(
            [_exe, "-c", "import importlib.util as u; print(bool(u.find_spec('fastembed')))"],
            capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual("True", (_r.stdout or "").strip(),
                         "桥接解释器必须能 import fastembed")


if __name__ == "__main__":
    unittest.main()
