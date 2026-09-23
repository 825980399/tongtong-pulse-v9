# -*- coding: utf-8 -*-
"""主线第79批 T1（P0 安全审计）门控测试。

覆盖：
1. _is_core_file 正确识别核心文件(main/config/base/nucleus/*)；
2. _core_auto_apply_allowed 读取 EVOLUTION_CONFIG.allow_core_auto_apply（默认 False）；
3. _resolve_core_auto_apply_status 为核心/非核心 + 开关两种状态给出正确 status：
   - 核心文件 + 开关关(默认) → 'verified'（强制人工审批，安全红线）
   - 核心文件 + 开关开 → 'approved'（灰度回退旧行为）
   - 非核心文件 → 恒 'approved'
4. 真实调用路径 _verify_and_save_patch 接入上述封装：核心文件默认 verified，
   开关开才 approved；非核心恒 approved。execute() 路径经结构校验确认同样接入封装。

★mock 隔离全部外部依赖，**不写生产数据**。
"""
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor  # noqa: E402


class TestCoreFileDetection(unittest.TestCase):
    def setUp(self):
        self.exe = SafeEvolutionExecutor()

    def test_01_core_markers(self):
        for _f in ("main.py", "config.py", "nucleus/pulse/foo.py",
                   "nucleus/field/bar.py", "nucleus/mnemosyne/x.py",
                   "nucleus/reasoning/y.py", "organs/base/z.py",
                   # ★主线第80批 T7-3 补全：nucleus/security/、nucleus/evolution/、安全/宪法器官
                   "nucleus/security/SandboxCore.py",
                   "nucleus/evolution/EvolutionDriver.py",
                   "organs/identity/PulseSpiritConstitution.py",
                   "organs/identity/PulsePersonalityKernel.py",
                   "organs/identity/PulseEthics.py"):
            self.assertTrue(self.exe._is_core_file(_f), f"应判为核心: {_f}")

    def test_02_non_core(self):
        # ★T7-3：nucleus/evolution/ 已升为核心目录，故从非核心清单移除
        #   （AestheticJudge 等 evolution 子模块现在走核心人工审批红线）。
        for _f in ("organs/body/PulseLiver.py", "tools/verify.py", "data/x.json"):
            self.assertFalse(self.exe._is_core_file(_f), f"不应判为核心: {_f}")


class TestCoreAutoApplyAllowedSwitch(unittest.TestCase):
    def setUp(self):
        self.exe = SafeEvolutionExecutor()

    def test_10_default_false(self):
        with mock.patch.object(config, "EVOLUTION_CONFIG", {"allow_core_auto_apply": False}):
            self.assertFalse(self.exe._core_auto_apply_allowed())
        with mock.patch.object(config, "EVOLUTION_CONFIG", {}):
            self.assertFalse(self.exe._core_auto_apply_allowed())

    def test_11_switch_on_true(self):
        with mock.patch.object(config, "EVOLUTION_CONFIG", {"allow_core_auto_apply": True}):
            self.assertTrue(self.exe._core_auto_apply_allowed())


class TestResolveCoreStatus(unittest.TestCase):
    """核心决策封装：核心文件默认 verified，仅开关开才 approved；非核心恒 approved。"""
    def setUp(self):
        self.exe = SafeEvolutionExecutor()

    def _with_switch(self, allowed):
        return mock.patch.object(self.exe, "_core_auto_apply_allowed",
                                 return_value=allowed)

    def test_20_core_switch_off_verified(self):
        with self._with_switch(False):
            self.assertEqual(self.exe._resolve_core_auto_apply_status(True), "verified")

    def test_21_core_switch_on_approved(self):
        with self._with_switch(True):
            self.assertEqual(self.exe._resolve_core_auto_apply_status(True), "approved")

    def test_22_non_core_always_approved(self):
        for _allowed in (False, True):
            with self._with_switch(_allowed):
                self.assertEqual(self.exe._resolve_core_auto_apply_status(False), "approved")

    def test_23_integration_config_default(self):
        with mock.patch.object(config, "EVOLUTION_CONFIG", {}):
            self.assertEqual(self.exe._resolve_core_auto_apply_status(True), "verified")

    def test_24_integration_config_on(self):
        with mock.patch.object(config, "EVOLUTION_CONFIG", {"allow_core_auto_apply": True}):
            self.assertEqual(self.exe._resolve_core_auto_apply_status(True), "approved")


class TestVerifySavePatchCore(unittest.TestCase):
    """真实 _verify_and_save_patch 路径：核心文件默认 verified，开关开才 approved。"""
    def setUp(self):
        self.exe = SafeEvolutionExecutor()
        self.exe._patch_manager = mock.MagicMock()
        self.exe._patch_manager.verify_in_copy.return_value = {"passed": True}
        self.exe._patch_manager.save_pending_patch.return_value = True
        # 美化评分恒返回 A 级，确保走到核心决策分支而非被 D 级拦截
        _judge = mock.MagicMock()
        _judge.score.return_value = {"grade": "A"}
        self._judge_patch = mock.patch(
            "nucleus.evolution.AestheticJudge.get_aesthetic_judge",
            return_value=_judge)

    def _make_patch(self, file):
        return {"id": "pid1234567890abc", "file": file, "modified_code": "x=1",
                "diff_summary": "x", "repair_source": "rule", "trust_score": 80,
                "issue_type": "t"}

    def test_40_core_default_verified(self):
        with self._judge_patch, mock.patch.object(
                config, "EVOLUTION_CONFIG",
                {"auto_apply_enabled": True, "allow_core_auto_apply": False}):
            _p = self._make_patch("nucleus/pulse/foo.py")
            self.assertTrue(self.exe._verify_and_save_patch(_p))
            self.assertEqual(_p["status"], "verified")
            self.assertTrue(_p.get("is_core_file"))

    def test_41_core_switch_on_approved(self):
        with self._judge_patch, mock.patch.object(
                config, "EVOLUTION_CONFIG",
                {"auto_apply_enabled": True, "allow_core_auto_apply": True}):
            _p = self._make_patch("nucleus/pulse/foo.py")
            self.assertTrue(self.exe._verify_and_save_patch(_p))
            self.assertEqual(_p["status"], "approved")

    def test_42_non_core_approved(self):
        with self._judge_patch, mock.patch.object(
                config, "EVOLUTION_CONFIG",
                {"auto_apply_enabled": True, "allow_core_auto_apply": False}):
            _p = self._make_patch("organs/body/PulseLiver.py")
            self.assertTrue(self.exe._verify_and_save_patch(_p))
            self.assertEqual(_p["status"], "approved")
            self.assertFalse(_p.get("is_core_file", False))


class TestBothPathsUseHelper(unittest.TestCase):
    """结构校验：execute 与 _verify_and_save_patch 都接入同一决策封装，
    防止后续重构把核心文件又硬编码回 approved。"""
    def test_30_execute_references_helper(self):
        import inspect
        self.assertIn("_resolve_core_auto_apply_status",
                      inspect.getsource(SafeEvolutionExecutor.execute))

    def test_31_verify_references_helper(self):
        import inspect
        self.assertIn("_resolve_core_auto_apply_status",
                      inspect.getsource(SafeEvolutionExecutor._verify_and_save_patch))


if __name__ == "__main__":
    unittest.main()
