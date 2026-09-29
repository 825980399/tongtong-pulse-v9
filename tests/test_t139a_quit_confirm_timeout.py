# -*- coding: utf-8 -*-
"""★主线第139批 T-139a（P1）：退出确认 input() 无限阻塞修复单测。

背景：main.py::_confirm_apply_pending_on_quit 原实现直接调用 input()，
在 TTY 判定为真但实际无输入时会永久阻塞退出路径（P1 紧急）。

修复三条路径：
  A. PULSE_QUIT_CONFIRM=0 环境变量 → 非交互直接跳过（True），不调用 input
  B. PULSE_QUIT_TIMEOUT_SEC 超时兜底 → 无输入时按默认 Y 继续（True），主线程不卡死
  C. EOF / Ctrl+C / 异常 → 视为用户取消（False）
helper 返回 (answered, value) 二元组以区分「超时」与「用户取消」。
"""
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from main import (
    PULSE_QUIT_CONFIRM_DEFAULT_TIMEOUT,
    PULSE_QUIT_CONFIRM_ENV,
    PULSE_QUIT_TIMEOUT_ENV,
    _confirm_apply_pending_on_quit,
    _prompt_with_timeout,
)


def _make_pm(patches):
    _pm = MagicMock()
    _pm.list_pending_patches.return_value = patches
    return _pm


_APPROVED = [{"status": "approved", "file": "a.py"}]


class TestQuitConfirmEnvSkip(unittest.TestCase):
    """A. PULSE_QUIT_CONFIRM=0 → 无交互直接跳过。"""

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_env_off_skips_input(self, _PMock):
        _PMock.return_value = _make_pm(_APPROVED)
        with patch.dict(os.environ, {PULSE_QUIT_CONFIRM_ENV: "0"}), \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input") as _inp:
            self.assertTrue(_confirm_apply_pending_on_quit(None))
            _inp.assert_not_called()

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_env_false_variants_skip_input(self, _PMock):
        for _v in ("0", "false", "no", "off", "FALSE", " No "):
            _PMock.return_value = _make_pm(_APPROVED)
            with patch.dict(os.environ, {PULSE_QUIT_CONFIRM_ENV: _v}), \
                    patch.object(sys.stdin, "isatty", return_value=True), \
                    patch("builtins.input") as _inp:
                self.assertTrue(
                    _confirm_apply_pending_on_quit(None), f"variant={_v!r}"
                )
                _inp.assert_not_called()

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_env_on_still_prompts(self, _PMock):
        """PULSE_QUIT_CONFIRM=1 不改变原交互语义。"""
        _PMock.return_value = _make_pm(_APPROVED)
        with patch.dict(os.environ, {PULSE_QUIT_CONFIRM_ENV: "1"}), \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="n"):
            self.assertFalse(_confirm_apply_pending_on_quit(None))


class TestQuitConfirmTimeout(unittest.TestCase):
    """B. 超时兜底 → 不卡死，超时按默认 Y。"""

    def test_prompt_with_timeout_signals_timeout(self):
        """input 永不返回：helper 必须在 ~timeout 内返回 (False, "") 而非阻塞。"""
        def _hang(_prompt=""):
            time.sleep(30)
            return "y"

        with patch("builtins.input", side_effect=_hang):
            _t0 = time.time()
            _answered, _val = _prompt_with_timeout("test? ", 0.3)
            _elapsed = time.time() - _t0
        self.assertFalse(_answered)
        self.assertEqual(_val, "")
        self.assertLess(_elapsed, 5.0, "超时兜底未生效，主线程被阻塞")

    def test_confirm_timeout_defaults_yes(self):
        """交互终端 + 超时（无输入）→ 按默认 Y 继续（True），且不卡死。"""

        def _hang(_prompt=""):
            time.sleep(30)
            return "y"

        with patch("nucleus.reasoning.PatchManager.PatchManager") as _PMock:
            _PMock.return_value = _make_pm(_APPROVED)
            with patch.dict(os.environ, {PULSE_QUIT_TIMEOUT_ENV: "0.3"}), \
                    patch.object(sys.stdin, "isatty", return_value=True), \
                    patch("builtins.input", side_effect=_hang):
                _t0 = time.time()
                _res = _confirm_apply_pending_on_quit(None)
                _elapsed = time.time() - _t0
        self.assertTrue(_res, "超时后未按默认 Y 继续")
        self.assertLess(_elapsed, 5.0, "退出确认超时兜底失效")

    def test_invalid_timeout_env_falls_back_to_default(self):
        """非法超时值回落默认值，不抛异常，正常交互。"""
        with patch("nucleus.reasoning.PatchManager.PatchManager") as _PMock:
            _PMock.return_value = _make_pm(_APPROVED)
            with patch.dict(os.environ, {PULSE_QUIT_TIMEOUT_ENV: "abc"}), \
                    patch.object(sys.stdin, "isatty", return_value=True), \
                    patch("builtins.input", return_value="n"):
                self.assertFalse(_confirm_apply_pending_on_quit(None))

    def test_default_timeout_constant_is_bounded(self):
        """默认超时常量必须为正且有限（防误配 0 → 立即超时 / inf → 卡死）。"""
        self.assertGreater(PULSE_QUIT_CONFIRM_DEFAULT_TIMEOUT, 0)
        self.assertLessEqual(PULSE_QUIT_CONFIRM_DEFAULT_TIMEOUT, 120)

    def test_non_interactive_stdin_still_applies(self):
        """非 TTY（服务/后台）→ 直接应用，不进入交互。"""
        with patch("nucleus.reasoning.PatchManager.PatchManager") as _PMock:
            _PMock.return_value = _make_pm(_APPROVED)
            with patch.dict(os.environ, {}, clear=False), \
                    patch.object(sys.stdin, "isatty", return_value=False), \
                    patch("builtins.input") as _inp:
                self.assertTrue(_confirm_apply_pending_on_quit(None))
                _inp.assert_not_called()


class TestPromptWithTimeoutSemantics(unittest.TestCase):
    """C. helper 返回语义（answered, value）。"""

    def test_returns_user_input(self):
        with patch("builtins.input", return_value="y"):
            _answered, _val = _prompt_with_timeout("q? ", 1.0)
        self.assertTrue(_answered)
        self.assertEqual(_val, "y")

    def test_empty_input_is_answered(self):
        """空回车是合法输入（默认 Y），不是超时。"""
        with patch("builtins.input", return_value=""):
            _answered, _val = _prompt_with_timeout("q? ", 1.0)
        self.assertTrue(_answered)
        self.assertEqual(_val, "")

    def test_eof_returns_cancelled(self):
        with patch("builtins.input", side_effect=EOFError):
            _answered, _val = _prompt_with_timeout("q? ", 1.0)
        self.assertFalse(_answered)
        self.assertIsNone(_val)

    def test_keyboard_interrupt_returns_cancelled(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            _answered, _val = _prompt_with_timeout("q? ", 1.0)
        self.assertFalse(_answered)
        self.assertIsNone(_val)

    def test_unexpected_error_returns_cancelled(self):
        with patch("builtins.input", side_effect=RuntimeError("boom")):
            _answered, _val = _prompt_with_timeout("q? ", 1.0)
        self.assertFalse(_answered)
        self.assertIsNone(_val)

    def test_worker_thread_is_daemon(self):
        """工作线程必须是 daemon，否则超时后残留线程会阻塞进程退出。"""
        _seen = {}

        def _cap(_prompt=""):
            import threading as _th
            _seen["daemon"] = _th.current_thread().daemon
            return "y"

        with patch("builtins.input", side_effect=_cap):
            _prompt_with_timeout("q? ", 1.0)
        self.assertTrue(_seen.get("daemon"), "input 工作线程不是 daemon")


class TestQuitConfirmSourceWiring(unittest.TestCase):
    """源码级接线断言（防止后续改动回退）。"""

    def setUp(self):
        import main as _m
        with open(_m.__file__, encoding="utf-8") as _f:
            self._src = _f.read()

    def test_helper_present(self):
        self.assertIn("def _prompt_with_timeout(", self._src)

    def test_input_not_called_directly_in_confirm(self):
        self.assertNotIn('_ans = input(', self._src)

    def test_confirm_calls_helper(self):
        self.assertIn("_answered, _ans = _prompt_with_timeout(", self._src)

    def test_env_constants_declared(self):
        self.assertIn('PULSE_QUIT_CONFIRM_ENV = "PULSE_QUIT_CONFIRM"', self._src)
        self.assertIn('PULSE_QUIT_TIMEOUT_ENV = "PULSE_QUIT_TIMEOUT_SEC"', self._src)


if __name__ == "__main__":
    unittest.main()
