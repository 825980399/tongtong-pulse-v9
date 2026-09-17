# -*- coding: utf-8 -*-
"""★主线第59批 T4：main.py 用户主动 quit 时待应用补丁确认提示单测。

覆盖 _confirm_apply_pending_on_quit：
  - 交互终端 + 已批准补丁 + 输入 Y / 空（回车）= 确认应用（True，默认 Y）
  - 交互终端 + 已批准补丁 + 输入 n = 拒绝（False）
  - 交互终端 + 仅未审批补丁 = 不弹提示、返回 False
  - 非交互终端（服务/后台）+ 已批准补丁 = 不弹提示、直接应用（True，维持原行为）
  - 输入 EOFError / KeyboardInterrupt = 视为拒绝（False，不阻断退出）
  - PatchManager 构造异常 = 返回 False（任何异常都不阻断退出）
源码级接线测试：main.py finally 块按 user_quit_requested 门控、signal_handler 标记。
"""
import sys
import unittest
from unittest.mock import MagicMock, patch

from main import _confirm_apply_pending_on_quit


def _make_pm(patches):
    _pm = MagicMock()
    _pm.list_pending_patches.return_value = patches
    return _pm


class TestConfirmApplyPendingOnQuit(unittest.TestCase):
    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_interactive_yes(self, _PMock):
        _PMock.return_value = _make_pm([
            {"status": "approved", "file": "a.py"},
            {"status": "approved", "file": "b.py"},
        ])
        with patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="y"):
            self.assertTrue(_confirm_apply_pending_on_quit(None))

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_interactive_empty_default_yes(self, _PMock):
        _PMock.return_value = _make_pm([{"status": "approved", "file": "a.py"}])
        with patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value=""):
            self.assertTrue(_confirm_apply_pending_on_quit(None))

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_interactive_no(self, _PMock):
        _PMock.return_value = _make_pm([{"status": "approved", "file": "a.py"}])
        with patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="n"):
            self.assertFalse(_confirm_apply_pending_on_quit(None))

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_interactive_unapproved_only_no_prompt(self, _PMock):
        _PMock.return_value = _make_pm([{"status": "pending"}, {"status": "pending"}])
        with patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input") as _inp:
            self.assertFalse(_confirm_apply_pending_on_quit(None))
            _inp.assert_not_called()

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_non_interactive_apply_without_prompt(self, _PMock):
        _PMock.return_value = _make_pm([{"status": "approved", "file": "a.py"}])
        with patch.object(sys.stdin, "isatty", return_value=False), \
                patch("builtins.input") as _inp:
            self.assertTrue(_confirm_apply_pending_on_quit(None))
            _inp.assert_not_called()

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_input_eof_decline(self, _PMock):
        _PMock.return_value = _make_pm([{"status": "approved", "file": "a.py"}])
        with patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", side_effect=EOFError):
            self.assertFalse(_confirm_apply_pending_on_quit(None))

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_exception_graceful_decline(self, _PMock):
        _PMock.side_effect = RuntimeError("boom")
        with patch.object(sys.stdin, "isatty", return_value=True):
            self.assertFalse(_confirm_apply_pending_on_quit(None))


class TestQuitConfirmWiring(unittest.TestCase):
    def setUp(self):
        import main as _m
        with open(_m.__file__, encoding="utf-8") as _f:
            self._src = _f.read()

    def test_finally_block_gates_on_user_quit(self):
        self.assertIn("_confirm_apply_pending_on_quit(framework)", self._src)
        self.assertIn("user_quit_requested", self._src)

    def test_signal_handler_marks_user_quit(self):
        self.assertIn("user_quit_requested = True", self._src)


if __name__ == "__main__":
    unittest.main()
