# -*- coding: utf-8 -*-
"""163批 刀7 门控单测：ParamPatchManager.verify_effect 灰度真实验证。

- 开关关闭（默认）→ 行为与原「应用即待验证」桩完全一致（零回归）。
- 开关开启 → 执行真实效果验证（复用 worker 链路），effect_verified 反映真实结果。
- 开关开启且验证返回错误 → 维持待验证态（不谎报为已验证）。
"""
import time
import unittest

import config as _cfg
import nucleus.reasoning.ReasoningWorkerPool as RWP
from nucleus.evolution.ParamPatchManager import ParamPatchManager


class _FakeFuture:
    def __init__(self, vres):
        self._vres = vres

    def result(self, timeout=30):
        return self._vres


class _FakePool:
    def __init__(self, vres):
        self._vres = vres

    def submit(self, name, info, log_file):
        return _FakeFuture(self._vres)


class TestParamPatchEffectKnife7(unittest.TestCase):
    def setUp(self):
        # 避免离线加载磁盘历史
        self._orig_load = ParamPatchManager._load_history
        ParamPatchManager._load_history = lambda self: None
        self._orig_switch = getattr(_cfg, "PARAM_PATCH_EFFECT_VERIFY_ENABLED", None)
        self._orig_get = RWP.get_reasoning_pool

    def tearDown(self):
        ParamPatchManager._load_history = self._orig_load
        if self._orig_switch is None:
            if hasattr(_cfg, "PARAM_PATCH_EFFECT_VERIFY_ENABLED"):
                del _cfg.PARAM_PATCH_EFFECT_VERIFY_ENABLED
        else:
            _cfg.PARAM_PATCH_EFFECT_VERIFY_ENABLED = self._orig_switch
        RWP.get_reasoning_pool = self._orig_get

    @staticmethod
    def _make_patch():
        return {"id": "p1", "param": "x.y", "applied": True, "applied_at": time.time()}

    def test_off_default_no_regression(self):
        _cfg.PARAM_PATCH_EFFECT_VERIFY_ENABLED = False
        pm = ParamPatchManager()
        res = pm.verify_effect(self._make_patch(), {}, wait_seconds=0)
        self.assertIsNone(res.get("error"))
        self.assertFalse(res["effect_verified"])
        self.assertEqual(res["recommended"], "observe")
        self.assertIn("将在", res["reason"])

    def test_on_performs_real_verification(self):
        _cfg.PARAM_PATCH_EFFECT_VERIFY_ENABLED = True
        RWP.get_reasoning_pool = lambda: _FakePool({
            "effect": "improved", "recommendation": "keep",
            "before_metrics": {}, "after_metrics": {"errors": 0}, "delta": {},
        })
        pm = ParamPatchManager()
        patch = self._make_patch()
        res = pm.verify_effect(patch, {}, wait_seconds=0)
        self.assertTrue(res["effect_verified"])
        self.assertEqual(res["recommended"], "keep")
        self.assertTrue(patch["effect_verified"])
        self.assertEqual(patch["effect_result"], "improved")

    def test_on_error_keeps_unverified(self):
        _cfg.PARAM_PATCH_EFFECT_VERIFY_ENABLED = True
        RWP.get_reasoning_pool = lambda: _FakePool({"error": "log missing"})
        pm = ParamPatchManager()
        patch = self._make_patch()
        res = pm.verify_effect(patch, {}, wait_seconds=0)
        self.assertFalse(res["effect_verified"])
        self.assertFalse(patch["effect_verified"])
        self.assertEqual(res["recommended"], "observe")


if __name__ == "__main__":
    unittest.main()
