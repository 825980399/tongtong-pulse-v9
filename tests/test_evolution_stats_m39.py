# -*- coding: utf-8 -*-
"""主线第39批 T3 门控：SafeEvolutionExecutor 公共统计接口 get_evolution_stats()。

覆盖：8 项主指标齐全 / 计数与比率正确（应用·成功·失败·回滚）/ 崩溃数 /
策略学习统计 / 空历史降级 / ★只读（不触发 verify·rollback·apply）/
引擎经公共接口取数 / 公共接口缺失时回退私有路径。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
from nucleus.self_awareness.SelfAwarenessEngine import (
    SelfAwarenessEngine,
    _evolution_stats_via_public,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_KEYS8 = ("total_patches", "successful_patches", "failed_patches",
          "rolled_back_patches", "success_rate", "rollback_rate",
          "crash_count", "strategy_learning_stats")


class _FakePM:
    """假补丁管理器（只读接口）。"""

    def __init__(self, history):
        self._h = history

    def load_json(self, path, default):
        return self._h

    def get_history_file(self):
        return "<mem>"


def _mk_executor(history=None, patches_ok=0, crash=0, strategies=None,
                 has_public=True):
    """轻量构造执行器（`__new__` 绕过 `__init__`，毫秒级零副作用）。"""
    ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
    ex._patch_log = [{} for _ in range(patches_ok)]
    ex._crash_stats = ({} if not crash
                       else {"repair": {"count": crash, "reasons": {}}})
    ex._strategy_stats = {}
    ex._patch_manager = _FakePM(history if history is not None else [])
    if not has_public:
        # 模拟旧版执行器：删除公共接口
        ex.__class__ = type("OldExecutor", (SafeEvolutionExecutor,), {
            "get_evolution_stats": property(lambda self: None)})
    return ex


class TestPublicStats(unittest.TestCase):
    """T3：公共接口返回契约。"""

    def setUp(self):
        self.hist = [
            # applied + 验证通过
            {"applied": True, "runtime_verified": True, "needs_runtime_verify": True,
             "runtime_verify_result": {"verified": True, "effectiveness": 1.0}},
            {"applied": True, "runtime_verified": True, "needs_runtime_verify": True,
             "runtime_verify_result": {"verified": True, "effectiveness": 0.5}},
            # applied + 验证失败
            {"applied": True, "runtime_verify_result": {"verified": False}},
            # applied + 已回滚
            {"applied": True, "rolled_back": True},
            # 未应用（仅待审批）
            {"applied": False, "status": "approved"},
        ]
        self.ex = _mk_executor(history=self.hist, patches_ok=3, crash=2)

    def test_10_keys_present(self):
        st = self.ex.get_evolution_stats()
        for k in _KEYS8:
            self.assertIn(k, st, k)

    def test_11_counts(self):
        st = self.ex.get_evolution_stats()
        self.assertEqual(st["total_patches"], 5)
        self.assertEqual(st["applied_patches"], 4)
        self.assertEqual(st["successful_patches"], 2)
        self.assertEqual(st["failed_patches"], 1)
        self.assertEqual(st["rolled_back_patches"], 1)

    def test_12_rates(self):
        st = self.ex.get_evolution_stats()
        # 分母 = applied_patches = 4
        self.assertEqual(st["success_rate"], 50.0)
        self.assertEqual(st["rollback_rate"], 25.0)

    def test_13_crash_and_strategy(self):
        st = self.ex.get_evolution_stats()
        self.assertEqual(st["crash_count"], 2)
        self.assertIsInstance(st["strategy_learning_stats"], dict)

    def test_14_avg_effectiveness(self):
        st = self.ex.get_evolution_stats()
        self.assertAlmostEqual(st["avg_effectiveness"], 0.75, places=4)

    def test_15_empty_history_all_zero(self):
        ex = _mk_executor(history=[])
        st = ex.get_evolution_stats()
        self.assertEqual(st["total_patches"], 0)
        self.assertEqual(st["applied_patches"], 0)
        self.assertEqual(st["success_rate"], 0.0)
        self.assertEqual(st["rollback_rate"], 0.0)
        for k in _KEYS8:
            self.assertIn(k, st, k)

    def test_16_readonly_no_state_changing_calls(self):
        """★只读红线：把会改状态的方法替换为断言，跑通即证明未调用。"""
        ex = self.ex

        def _boom(*a, **k):        # pragma: no cover
            raise AssertionError("get_evolution_stats 不得调用会改状态的接口")

        ex.verify_applied_patches = _boom
        ex.verify_submitted_patches = _boom
        ex.run_runtime_guided_review = _boom
        ex.execute = _boom
        ex.repair_with_distillation = _boom
        st = ex.get_evolution_stats()
        self.assertEqual(st["total_patches"], 5)

    def test_17_no_mutation_of_history(self):
        ex = self.ex
        _before = [dict(x) for x in ex._patch_manager._h]
        ex.get_evolution_stats()
        self.assertEqual(ex._patch_manager._h, _before)


class TestEngineUsesPublicAPI(unittest.TestCase):
    """T3：引擎改用公共接口 + 回退路径。"""

    def test_20_via_public(self):
        ex = _mk_executor(history=[
            {"applied": True, "runtime_verified": True},
            {"applied": True, "runtime_verified": True}])
        raw = _evolution_stats_via_public(ex)
        self.assertIsNotNone(raw)
        self.assertEqual(raw["history_total"], 2)
        self.assertEqual(raw["applied"], 2)
        self.assertEqual(raw["verified"], 2)
        self.assertIn("public_stats", raw)

    def test_21_missing_public_returns_none(self):
        class _Old:
            def get_stats(self):
                return {"total_patches_generated": 0}

        self.assertIsNone(_evolution_stats_via_public(_Old()))

    def test_22_public_raises_returns_none(self):
        class _Bad:
            def get_evolution_stats(self):
                raise RuntimeError("boom")

        self.assertIsNone(_evolution_stats_via_public(_Bad()))

    def test_23_public_non_dict_returns_none(self):
        class _Bad:
            def get_evolution_stats(self):
                return ["nope"]

        self.assertIsNone(_evolution_stats_via_public(_Bad()))

    def test_24_engine_integration(self):
        ex = _mk_executor(history=[
            {"applied": True, "runtime_verified": True},
            {"applied": True, "runtime_verified": True}])
        e = SelfAwarenessEngine()
        r = e.integrate_evolution_health(executor=ex)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["success_rate"], 100.0)
        self.assertEqual(r["score"], 100.0)

    def test_25_fallback_path_on_old_executor(self):
        """旧执行器（无公共接口）→ 走私有访问回退，仍产出结果。"""
        ex = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        ex._patch_log = []
        ex._crash_stats = {}
        ex._patch_manager = _FakePM([
            {"applied": True, "runtime_verified": True}])

        class _OldExec:
            """旧版执行器：有统计接口但**没有** get_evolution_stats。

            ★注意：**不能**用 `__getattr__` 委托 inner —— 那会让公共接口
            通过委托被"意外找到"，测不出回退路径（首版即此缺陷）。
            """

            def __init__(self, inner):
                self._patch_manager = inner._patch_manager

            def get_stats(self):
                return {"total_patches_generated": 0}

            def get_crash_stats(self):
                return {"total": 0}

            def get_strategy_learning_stats(self):
                return {"status": "no_data", "strategies": {}}

        e = SelfAwarenessEngine()
        r = e.integrate_evolution_health(executor=_OldExec(ex))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["success_rate"], 100.0)


if __name__ == "__main__":
    unittest.main()
