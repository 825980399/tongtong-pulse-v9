# -*- coding: utf-8 -*-
"""第51批 T3（P2-354）门控测试：写路径守卫的进程级判据。

覆盖：
* 进程类型识别（framework / pytest / test_mode / script）
* 拒写矩阵（pytest × framework × test_mode × strict × explicit）
* 子进程真实环境变量行为（★不经打桩，测真实 PULSE_FRAMEWORK / PULSE_TEST_MODE）
* 拒写留痕日志（含进程名 / 路径 / 原因）
* 源码接线（main.py 设标识、config 有开关）
* 零回归（严格开关关闭 → 退回第44批行为）

★纪律（skill §39.6）：任何「生产可写」用例一律在**谓词层**断言
（``reject_write(...) is False`` / ``guard_write(...) is True``），
绝不在 pytest 里真触发一次生产落盘。
"""
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.data import write_guard as wg  # noqa: E402

_PROD = os.path.join(_ROOT, "data", "reports", "_m51_never.json")
_OTHER = os.path.join(_ROOT, "tmp", "_m51_other.json")

_PY = sys.executable


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()


class TestWriterKind(unittest.TestCase):
    """进程类型识别。"""

    def test_01_pytest_env_is_test_env(self):
        # 本测试进程即 pytest
        self.assertTrue(wg.is_test_env())
        self.assertTrue(wg.is_test_like_env())

    def test_02_framework_flag_from_env(self):
        with mock.patch.dict(os.environ, {"PULSE_FRAMEWORK": "1"}):
            self.assertTrue(wg.is_framework_process())

    def test_03_framework_flag_absent(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PULSE_FRAMEWORK", None)
            with mock.patch.object(wg, "is_test_env", return_value=False):
                # __main__ 在 pytest 下不是 main.py
                self.assertFalse(wg.is_framework_process())

    def test_04_framework_main_fallback(self):
        """★兜底：未设环境变量但 __main__ 是 main.py → 仍识别为框架。"""
        os.environ.pop("PULSE_FRAMEWORK", None)
        _fake = type(sys)("__main__")
        _fake.__file__ = os.path.join(_ROOT, "main.py")
        with mock.patch.dict(sys.modules, {"__main__": _fake}):
            self.assertTrue(wg.is_framework_process())

    def test_05_test_mode_flag(self):
        with mock.patch.dict(os.environ, {"PULSE_TEST_MODE": "1"}):
            self.assertTrue(wg.is_test_mode())
            self.assertTrue(wg.is_test_like_env())

    def test_06_writer_kind_precedence(self):
        with mock.patch.object(wg, "is_framework_process", return_value=True):
            self.assertEqual(wg.writer_kind(), "framework")
        with mock.patch.object(wg, "is_framework_process", return_value=False), \
                mock.patch.object(wg, "is_test_env", return_value=True):
            self.assertEqual(wg.writer_kind(), "pytest")
        with mock.patch.object(wg, "is_framework_process", return_value=False), \
                mock.patch.object(wg, "is_test_env", return_value=False), \
                mock.patch.object(wg, "is_test_mode", return_value=True):
            self.assertEqual(wg.writer_kind(), "test_mode")
        with mock.patch.object(wg, "is_framework_process", return_value=False), \
                mock.patch.object(wg, "is_test_env", return_value=False), \
                mock.patch.object(wg, "is_test_mode", return_value=False):
            self.assertEqual(wg.writer_kind(), "script")


class TestRejectMatrix(unittest.TestCase):
    """拒写矩阵（谓词层，绝不真写生产）。"""

    def _rej(self, *, is_env, is_fw, is_mode, strict, explicit=False, path=_PROD):
        with mock.patch.object(wg, "is_test_env", return_value=is_env), \
                mock.patch.object(wg, "is_framework_process", return_value=is_fw), \
                mock.patch.object(wg, "is_test_mode", return_value=is_mode), \
                mock.patch.object(wg, "strict_enabled", return_value=strict), \
                mock.patch.object(wg, "guard_enabled", return_value=True):
            return wg.reject_write(path, explicit=explicit)

    def test_10_pytest_rejected(self):
        """pytest 环境 → 拒写生产。"""
        self.assertTrue(self._rej(is_env=True, is_fw=False, is_mode=False, strict=True))

    def test_11_framework_allowed(self):
        """框架主进程 → 允许写生产。"""
        self.assertFalse(self._rej(is_env=False, is_fw=True, is_mode=False, strict=True))

    def test_12_test_mode_rejected(self):
        """★PULSE_TEST_MODE=1 → 按测试环境处理（拒写生产）。"""
        self.assertTrue(self._rej(is_env=False, is_fw=False, is_mode=True, strict=True))

    def test_13_script_rejected_when_strict(self):
        """★普通脚本 + 严格开启 → 拒写（本批核心修复）。"""
        self.assertTrue(self._rej(is_env=False, is_fw=False, is_mode=False, strict=True))

    def test_14_script_allowed_when_not_strict(self):
        """零回归：严格关闭 → 普通脚本放行（= 第44批行为）。"""
        self.assertFalse(self._rej(is_env=False, is_fw=False, is_mode=False, strict=False))

    def test_15_pytest_rejected_even_when_not_strict(self):
        """零回归：即使严格关闭，pytest 仍拒写（第44批既有行为不变）。"""
        self.assertTrue(self._rej(is_env=True, is_fw=False, is_mode=False, strict=False))

    def test_16_explicit_bypasses(self):
        """explicit=True → 显式授权，绕过守卫。"""
        self.assertFalse(self._rej(is_env=False, is_fw=False, is_mode=False,
                                   strict=True, explicit=True))

    def test_17_non_production_path_always_allowed(self):
        """非生产 data/ 路径 → 一律放行（只在 data/ 前缀内生效）。"""
        self.assertFalse(self._rej(is_env=False, is_fw=False, is_mode=False,
                                   strict=True, path=_OTHER))
        self.assertFalse(self._rej(is_env=True, is_fw=False, is_mode=False,
                                   strict=True, path=_OTHER))

    def test_18_guard_disabled_means_allow(self):
        """守卫总开关关闭 → 一律放行。"""
        with mock.patch.object(wg, "guard_enabled", return_value=False):
            self.assertFalse(wg.reject_write(_PROD))

    def test_19_reject_reason_has_process_and_advice(self):
        with mock.patch.object(wg, "is_test_env", return_value=False), \
                mock.patch.object(wg, "is_framework_process", return_value=False), \
                mock.patch.object(wg, "is_test_mode", return_value=False), \
                mock.patch.object(wg, "strict_enabled", return_value=True), \
                mock.patch.object(wg, "guard_enabled", return_value=True):
            _r = wg.reject_reason(_PROD)
            self.assertIn("script", _r)
            self.assertIn("PULSE_FRAMEWORK", _r)


class TestLogging(unittest.TestCase):
    """拒写必须留痕（进程名 / 路径 / 原因）。"""

    def test_30_guard_write_logs_process_path_reason(self):
        wg.reset_warned()
        _captured = []

        class _L:
            def warning(self, msg, *a):
                _captured.append(msg % a if a else msg)

        with mock.patch.object(wg, "guard_enabled", return_value=True), \
                mock.patch.object(wg, "is_test_env", return_value=False), \
                mock.patch.object(wg, "is_framework_process", return_value=False), \
                mock.patch.object(wg, "is_test_mode", return_value=False), \
                mock.patch.object(wg, "strict_enabled", return_value=True), \
                mock.patch("nucleus.logger.get_module_logger", return_value=_L()):
            _ok = wg.guard_write(_PROD, component="UT_M51")
        self.assertFalse(_ok, "普通脚本应被拒写")
        self.assertTrue(_captured, "拒写必须留痕")
        _msg = _captured[0]
        for _must in ("UT_M51", "script", "_m51_never.json", "原因"):
            self.assertIn(_must, _msg, "日志缺少 {}: {}".format(_must, _msg))

    def test_31_never_raises(self):
        """守卫永不抛出（故障 → 放行）。"""
        with mock.patch.object(wg, "is_production_data_path",
                               side_effect=RuntimeError("boom")):
            # ★两个断言都必须在 patch 生效期内（故障注入下守卫应放行）
            self.assertFalse(wg.reject_write(_PROD))
            self.assertTrue(wg.guard_write(_PROD, component="UT_M51_nr"))


class TestSubprocessEnv(unittest.TestCase):
    """★真实环境变量行为（不经打桩）。"""

    _PROBE = (
        "import json, os, sys;"
        "sys.path.insert(0, r'{}');"
        "from nucleus.data import write_guard as wg;"
        "P=os.path.join(r'{}','data','reports','_m51_sp.json');"
        "print(json.dumps({{'kind':wg.writer_kind(),"
        "'reject':wg.reject_write(P),'guard':wg.guard_write(P,component='sp')}}))".format(_ROOT, _ROOT)
    )

    def _run(self, env_extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PULSE_FRAMEWORK", "PULSE_TEST_MODE",
                            "PYTEST_CURRENT_TEST")}
        env.update(env_extra)
        r = subprocess.run([_PY, "-c", self._PROBE], env=env, cwd=_ROOT,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        lines = [x for x in (r.stdout or "").splitlines() if x.strip()]
        return json.loads(lines[-1]) if lines else {"ERR": r.stdout + r.stderr}

    def test_40_plain_script_rejected(self):
        d = self._run({})
        self.assertEqual(d.get("kind"), "script")
        self.assertTrue(d.get("reject"))
        self.assertFalse(d.get("guard"))

    def test_41_framework_allowed(self):
        d = self._run({"PULSE_FRAMEWORK": "1"})
        self.assertEqual(d.get("kind"), "framework")
        self.assertFalse(d.get("reject"))
        self.assertTrue(d.get("guard"))

    def test_42_test_mode_rejected(self):
        d = self._run({"PULSE_TEST_MODE": "1"})
        self.assertEqual(d.get("kind"), "test_mode")
        self.assertTrue(d.get("reject"))
        self.assertFalse(d.get("guard"))


class TestSourceWiring(unittest.TestCase):
    """源码接线检查（AST/文本双通道）。"""

    def test_50_main_sets_framework_flag(self):
        _m = _src("main.py")
        self.assertIn('os.environ.setdefault("PULSE_FRAMEWORK", "1")', _m)
        # 必须在 import config 之前（任何写盘之前）
        _i_flag = _m.find('setdefault("PULSE_FRAMEWORK"')
        _i_cfg = _m.find("\nimport config")
        self.assertGreater(_i_flag, 0)
        self.assertGreater(_i_cfg, 0)
        self.assertLess(_i_flag, _i_cfg, "标识必须早于 import config")

    def test_51_config_has_strict_switch(self):
        _c = _src("config.py")
        self.assertIn("ENABLE_STRICT_WRITE_GUARD", _c)

    def test_52_write_guard_has_new_api(self):
        _w = _src("nucleus/data/write_guard.py")
        for _fn in ("def is_framework_process", "def is_test_mode",
                    "def is_test_like_env", "def is_trusted_writer",
                    "def writer_kind", "def strict_enabled",
                    "def reject_reason"):
            self.assertIn(_fn, _w, "缺少 {}".format(_fn))

    def test_53_no_bare_except_pass_in_reject(self):
        """守卫内不得有裸 except:pass（留痕失败要退化到 stderr）。"""
        import re
        _w = _src("nucleus/data/write_guard.py")
        self.assertEqual(
            len(re.findall(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)", _w)), 0)

    def test_54_signature_unchanged(self):
        """★签名兼容：guard_write / reject_write 的既有形参不得变更。"""
        import inspect
        self.assertEqual(
            list(inspect.signature(wg.guard_write).parameters),
            ["path", "explicit", "component"])
        self.assertEqual(
            list(inspect.signature(wg.reject_write).parameters),
            ["path", "explicit", "component"])


if __name__ == "__main__":
    unittest.main()
