# -*- coding: utf-8 -*-
"""★172刀4 编排补丁闸门单测。

覆盖 _quit_patch_gate_allow 纯函数真值表，以及 _confirm_apply_pending_on_quit
在非 TTY 场景下的闸门行为：
  - 通道已注册（NIGHT_ORCH_ENABLED=True，默认）+ 非 TTY → 允许默认放行（True）
  - 通道未注册（NIGHT_ORCH_ENABLED=False）+ 非 TTY → 不自重启（False）
  - TTY 在场 → 仍走交互提示（门禁不受影响）
放行权来自通道注册，不来自超时（任务书刀4 纪律）。
"""
import sys
import unittest
from unittest.mock import MagicMock, patch

import config as _cfg
from main import (
    _confirm_apply_pending_on_quit,
    _quit_patch_gate_allow,
)


def _make_pm(patches):
    _pm = MagicMock()
    _pm.list_pending_patches.return_value = patches
    return _pm


_APPROVED = [{"status": "approved", "file": "a.py"}]


class TestQuitPatchGateHelper(unittest.TestCase):
    """纯函数真值表。"""

    def test_truth_table(self):
        self.assertTrue(_quit_patch_gate_allow(True, True))
        self.assertFalse(_quit_patch_gate_allow(False, True))
        self.assertFalse(_quit_patch_gate_allow(True, False))
        self.assertFalse(_quit_patch_gate_allow(False, False))


class TestQuitPatchGateBehavior(unittest.TestCase):
    """_confirm_apply_pending_on_quit 闸门行为。"""

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_non_tty_channel_registered_returns_true(self, _PMock):
        """默认 config（通道已注册）+ 非 TTY → 允许默认放行，不弹交互。"""
        _PMock.return_value = _make_pm(_APPROVED)
        with patch.object(_cfg, "NIGHT_ORCH_ENABLED", True),                 patch.object(sys.stdin, "isatty", return_value=False),                 patch("builtins.input") as _inp:
            self.assertTrue(_confirm_apply_pending_on_quit(None))
            _inp.assert_not_called()

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_non_tty_no_channel_returns_false(self, _PMock):
        """★刀4 验收①：非 TTY + 无通道 → 不自重启（False），不弹交互。"""
        _PMock.return_value = _make_pm(_APPROVED)
        with patch.object(_cfg, "NIGHT_ORCH_ENABLED", False),                 patch.object(sys.stdin, "isatty", return_value=False),                 patch("builtins.input") as _inp:
            self.assertFalse(_confirm_apply_pending_on_quit(None))
            _inp.assert_not_called()

    @patch("nucleus.reasoning.PatchManager.PatchManager")
    def test_tty_still_prompts_no_default_y(self, _PMock):
        """TTY 在场 → 走交互提示（门禁不受影响），输入 n 即拒绝。"""
        _PMock.return_value = _make_pm(_APPROVED)
        with patch.object(_cfg, "NIGHT_ORCH_ENABLED", True),                 patch.object(sys.stdin, "isatty", return_value=True),                 patch("builtins.input", return_value="n"):
            self.assertFalse(_confirm_apply_pending_on_quit(None))


class TestQuitPatchGateSourceWiring(unittest.TestCase):
    """源码级接线断言（防回退为无条件 return True）。"""

    def setUp(self):
        import main as _m
        with open(_m.__file__, encoding="utf-8") as _f:
            self._src = _f.read()

    def test_helper_called_in_confirm(self):
        self.assertIn("return _quit_patch_gate_allow(_channel_registered, _non_tty)", self._src)

    def test_gate_helper_defined(self):
        self.assertIn("def _quit_patch_gate_allow(", self._src)

    def test_channel_registration_read_from_config(self):
        self.assertIn('_channel_registered = bool(getattr(config, "NIGHT_ORCH_ENABLED", True))', self._src)


if __name__ == "__main__":
    unittest.main()
