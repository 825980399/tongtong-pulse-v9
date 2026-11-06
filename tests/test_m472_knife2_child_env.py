# -*- coding: utf-8 -*-
"""第472批 刀2 门控单测：补丁验证子进程的环境白名单（台账 S6 / F1）。

被测对象：`nucleus/reasoning/PatchManager.py`
  · `_m472_build_child_env()`
  · `_verify_import_in_subprocess` 的调用参数（源码级）

判据来源：`nucleus/data/write_guard.py` 用 `PULSE_FRAMEWORK` 判定「可信写入者」，
而 `execute_code_in_subprocess(child_env=None)` ⇒ `Popen(env=None)` ⇒ 继承父环境。
所以「被验证的补丁代码能写 data/」这件事是可以直接构造出来的：
把 `PULSE_FRAMEWORK=1` 放进 `os.environ`，再调构造函数，看它出不出来。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.reasoning import PatchManager as PM
from nucleus.review.ScriptExecutor import ScriptExecutor


class TestA_NoPulseVars(unittest.TestCase):
    """核心判据：任何 PULSE_* 都不得进入子进程环境。"""

    def test_01_pulse_framework_stripped(self):
        with mock.patch.dict(os.environ, {"PULSE_FRAMEWORK": "1"}, clear=False):
            _env = PM._m472_build_child_env()
        self.assertNotIn("PULSE_FRAMEWORK", _env,
                         "PULSE_FRAMEWORK 是 write_guard 的身份凭据，绝不能透传")

    def test_02_all_pulse_prefix_stripped(self):
        with mock.patch.dict(os.environ, {
            "PULSE_FRAMEWORK": "1",
            "PULSE_TEST_FRAMEWORK_RUNNING": "1",
            "PULSE_PATCH_RESTART_COOLDOWN_HOURS": "3",
            "PULSE_SOMETHING_NEW": "x",
        }, clear=False):
            _env = PM._m472_build_child_env()
        _left = [k for k in _env if k.upper().startswith("PULSE_")]
        self.assertEqual(_left, [], f"PULSE_* 必须全剥掉，实剩 {_left}")

    def test_03_strip_wins_even_if_allowlisted(self):
        """兜底语义：即使有人把 PULSE_X 写进白名单，也必须被剥掉。"""
        _patched = tuple(PM._M472_CHILD_ENV_ALLOWLIST) + ("PULSE_FRAMEWORK",)
        with mock.patch.object(PM, "_M472_CHILD_ENV_ALLOWLIST", _patched), \
                mock.patch.dict(os.environ, {"PULSE_FRAMEWORK": "1"}, clear=False):
            _env = PM._m472_build_child_env()
        self.assertNotIn("PULSE_FRAMEWORK", _env,
                         "白名单不是免死金牌：PULSE_* 必须在最后一道被剥掉")


class TestB_KeepsWhatIsNeeded(unittest.TestCase):
    """反向判据：剥太狠会让子进程起不来（那就变成 fail-closed 的另一种故障）。"""

    def test_04_path_kept(self):
        _env = PM._m472_build_child_env()
        self.assertTrue(_env.get("PATH"), "没有 PATH 子进程无法启动")

    def test_05_path_filled_when_missing(self):
        _clean = {k: v for k, v in os.environ.items() if k.upper() != "PATH"}
        with mock.patch.dict(os.environ, _clean, clear=True):
            _env = PM._m472_build_child_env()
        self.assertTrue(_env.get("PATH"), "PATH 缺失时必须补一个兜底值")

    def test_06_io_encoding_forced(self):
        _env = PM._m472_build_child_env()
        self.assertEqual(_env.get("PYTHONIOENCODING"), "utf-8",
                         "Windows GBK 下补丁代码 print 非 ASCII 会崩，必须强制 utf-8")

    def test_07_unsafe_var_not_passed(self):
        """反例：任意非白名单变量（这里用凭证名）不得透传。"""
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret"}, clear=False):
            _env = PM._m472_build_child_env()
        self.assertNotIn("OPENAI_API_KEY", _env, "凭证类变量必须默认 deny")


class TestC_NoDriftWithScriptExecutor(unittest.TestCase):
    """★F1 的根治判据：两份白名单必须逐项一致，否则又是一次「修一半」。"""

    def test_08_allowlist_matches_script_executor(self):
        _a = {k.upper() for k in PM._M472_CHILD_ENV_ALLOWLIST}
        _b = {k.upper() for k in ScriptExecutor.ENV_ALLOWLIST}
        _only_pm = sorted(_a - _b)
        _only_se = sorted(_b - _a)
        self.assertEqual(
            _a, _b,
            f"两处白名单漂移了。仅PatchManager={_only_pm} 仅ScriptExecutor={_only_se}")


class TestD_Wiring(unittest.TestCase):
    """源码级：调用点必须真的把 child_env 传下去。"""

    @classmethod
    def setUpClass(cls):
        _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "nucleus", "reasoning", "PatchManager.py")
        with open(_p, encoding="utf-8") as _fh:
            cls._src = _fh.read()

    def test_09_child_env_passed_at_import_check(self):
        _i = self._src.index("def _verify_import_in_subprocess")
        _seg = self._src[_i:_i + 6000]
        self.assertIn("child_env=_m472_build_child_env()", _seg,
                      "补丁验证子进程必须传白名单环境")

    def test_10_child_env_inside_the_call(self):
        """不能只出现字符串——必须在 execute_code_in_subprocess(...) 的参数里。

        锚点取 `_res = execute_code_in_subprocess(`：docstring 里也有一句
        「execute_code_in_subprocess() 执行」，直接 index 会命中注释而不是实参。
        """
        _i = self._src.index("def _verify_import_in_subprocess")
        _j = self._src.index("_res = execute_code_in_subprocess(", _i)
        _call = self._src[_j:_j + 900]
        self.assertIn("child_env=", _call, "child_env 必须落在该次调用的实参里")


if __name__ == "__main__":
    unittest.main()
