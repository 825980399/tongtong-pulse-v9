# -*- coding: utf-8 -*-
"""第40批 T3 门控测试：evolution 统计口径统一（单一真相源）。

验证：
  · 纯函数分桶 / 互斥 / 均值口径；
  · 引擎侧（回退路径）与 executor 公共接口**结果完全一致**；
  · 灰度开关关闭 → 回退修复前口径（零回归）；
  · 边界（None / 非 list / 空 / 字段缺失）。
"""
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.evolution.evolution_stats import (  # noqa: E402
    EMPTY_STATS, stats_from_patch_history)
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor  # noqa: E402

# ★包 __init__ 导出同名类 → 取模块用 importlib
_eng = importlib.import_module("nucleus.self_awareness.SelfAwarenessEngine")


class _FakePM:
    def __init__(self, hist):
        self._h = hist

    def load_json(self, path, default=None):
        return self._h

    def get_history_file(self):
        return "fake_history.json"


class _FakeExec:
    """最小执行器：只提供统计路径必需的只读接口。"""

    def __init__(self, hist):
        self._patch_manager = _FakePM(hist)

    def get_stats(self):
        return {"total_patches_generated": 999}

    def get_crash_stats(self):
        return {"total": 3}

    def get_strategy_learning_stats(self):
        return {"strategies": {"a": 1}}


def _make_real_exec(hist):
    _e = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
    _e._patch_manager = _FakePM(hist)
    _e.get_stats = lambda: {"total_patches_generated": 999}
    _e.get_crash_stats = lambda: {"total": 3}
    _e.get_strategy_learning_stats = lambda: {"strategies": {"a": 1}}
    return _e


class TestPureFunction(unittest.TestCase):
    def test_01_buckets_and_mutex(self):
        _h = [
            {"applied": True, "runtime_verified": True,
             "runtime_verify_result": {"effectiveness": 0.8}},
            {"applied": True, "runtime_verified": True,
             "runtime_verify_result": {"effectiveness": 0.6}},
            {"applied": True, "runtime_verify_result": {"verified": False}},
            {"applied": True, "rolled_back": True},
            {"applied": True, "status": "rolled_back"},
            {"applied": True, "needs_runtime_verify": True},
            {"applied": False},
            "not-a-dict",
        ]
        _s = stats_from_patch_history(_h)
        # ★契约变更（第43批 T4 / P1-280）：`total` 现在是**去重后的有效记录数**
        #   （本例 7 条 dict + 1 条非法项 → 7），原始条数移到 `raw_total`。
        self.assertEqual(_s["total"], 7)
        self.assertEqual(_s["raw_total"], 8)
        self.assertEqual(_s["duplicates_removed"], 0)
        self.assertEqual(_s["applied"], 6)
        self.assertEqual(_s["successful"], 2)
        self.assertEqual(_s["failed"], 1)
        self.assertEqual(_s["rolled_back"], 2)
        self.assertEqual(_s["pending_verify"], 1)
        # 互斥：分桶之和 == applied（除"无标记"条目，本例无）
        self.assertEqual(_s["successful"] + _s["failed"] + _s["rolled_back"]
                         + _s["pending_verify"], _s["applied"])
        self.assertEqual(_s["avg_effectiveness"], 0.7)

    def test_02_empty(self):
        self.assertEqual(stats_from_patch_history([]), EMPTY_STATS)

    def test_03_non_list(self):
        for _bad in (None, {}, "x", 123):
            self.assertEqual(stats_from_patch_history(_bad), EMPTY_STATS)

    def test_04_effectiveness_from_verify_result_only(self):
        # ★顶层 effectiveness 不再计入（第40批统一口径）
        _h = [{"applied": True, "runtime_verified": True,
               "effectiveness": 0.99,
               "runtime_verify_result": {"effectiveness": 0.5}}]
        self.assertEqual(stats_from_patch_history(_h)["avg_effectiveness"], 0.5)

    def test_05_bool_not_counted_as_number(self):
        _h = [{"applied": True, "runtime_verified": True,
               "runtime_verify_result": {"effectiveness": True}}]
        self.assertEqual(stats_from_patch_history(_h)["avg_effectiveness"], 0.0)

    def test_06_no_effectiveness_samples(self):
        _h = [{"applied": True, "runtime_verified": True}]
        self.assertEqual(stats_from_patch_history(_h)["avg_effectiveness"], 0.0)


class TestTwoPathsConsistent(unittest.TestCase):
    _HIST = [
        {"applied": True, "runtime_verified": True,
         "runtime_verify_result": {"effectiveness": 0.9}},
        {"applied": True, "runtime_verify_result": {"verified": False}},
        {"applied": True, "rolled_back": True},
        {"applied": True, "needs_runtime_verify": True},
        {"applied": False},
    ]

    def test_20_engine_equals_pure(self):
        _raw = _eng._evolution_raw_stats(_FakeExec(self._HIST))
        _st = stats_from_patch_history(self._HIST)
        self.assertEqual(_raw["history_total"], _st["total"])
        self.assertEqual(_raw["applied"], _st["applied"])
        self.assertEqual(_raw["verified"], _st["successful"])
        self.assertEqual(_raw["failed"], _st["failed"])
        self.assertEqual(_raw["rolled_back"], _st["rolled_back"])
        self.assertEqual(_raw["pending_verify"], _st["pending_verify"])
        self.assertEqual(_raw["avg_effectiveness"], _st["avg_effectiveness"])

    def test_21_public_equals_pure(self):
        _pub = _make_real_exec(self._HIST).get_evolution_stats()
        _st = stats_from_patch_history(self._HIST)
        self.assertEqual(_pub["total_patches"], _st["total"])
        self.assertEqual(_pub["applied_patches"], _st["applied"])
        self.assertEqual(_pub["successful_patches"], _st["successful"])
        self.assertEqual(_pub["failed_patches"], _st["failed"])
        self.assertEqual(_pub["rolled_back_patches"], _st["rolled_back"])
        self.assertEqual(_pub["pending_verify_patches"], _st["pending_verify"])
        self.assertEqual(_pub["avg_effectiveness"], _st["avg_effectiveness"])

    def test_22_engine_equals_public(self):
        _raw = _eng._evolution_raw_stats(_FakeExec(self._HIST))
        _pub = _make_real_exec(self._HIST).get_evolution_stats()
        self.assertEqual(_raw["history_total"], _pub["total_patches"])
        self.assertEqual(_raw["applied"], _pub["applied_patches"])
        self.assertEqual(_raw["verified"], _pub["successful_patches"])
        self.assertEqual(_raw["failed"], _pub["failed_patches"])
        self.assertEqual(_raw["rolled_back"], _pub["rolled_back_patches"])
        self.assertEqual(_raw["pending_verify"], _pub["pending_verify_patches"])
        self.assertEqual(_raw["avg_effectiveness"], _pub["avg_effectiveness"])


class TestUnifiedOnly(unittest.TestCase):
    """★第41批 T4（P2-268）：**回退分支已删除** —— 统一口径成为唯一路径。

    原 ``TestSwitchRegression``（3 例）守护的是"开关关闭 → 回退旧口径"，
    该分支已按 P2-268 删除 → 本类同步改写（缺口表征反向同步，铁律 26）。
    """

    _HIST = [{"applied": True, "runtime_verified": True,
              "runtime_verify_result": {"effectiveness": 0.9}}]

    def test_30_unified_path_matches_pure(self):
        """无开关：``_evolution_raw_stats`` 直接走单一真相源。"""
        _raw = _eng._evolution_raw_stats(_FakeExec(self._HIST))
        _st = stats_from_patch_history(self._HIST)
        self.assertEqual(_raw["avg_effectiveness"], _st["avg_effectiveness"])
        self.assertEqual(_raw["verified"], _st["successful"])
        self.assertEqual(_raw["history_total"], _st["total"])

    def test_31_switch_removed_from_config(self):
        """★开关已删除（config 不再暴露）。"""
        self.assertFalse(hasattr(config, "ENABLE_EVOLUTION_STATS_UNIFIED"))

    def test_32_no_legacy_branch(self):
        """★旧口径（读顶层 ``effectiveness``）已彻底移除。"""
        _hist = [{"applied": True, "runtime_verified": True,
                  "effectiveness": 0.99,
                  "runtime_verify_result": {"effectiveness": 0.5}}]
        _raw = _eng._evolution_raw_stats(_FakeExec(_hist))
        self.assertEqual(_raw["avg_effectiveness"], 0.5)   # 不取顶层 0.99

    def test_33_old_executor_no_public_api(self):
        """无 get_evolution_stats() 的旧执行器 → 回退入口仍正常（不抛）。"""

        class _Old:
            def get_stats(self):
                return {}

            def get_crash_stats(self):
                return {}

            def get_strategy_learning_stats(self):
                return {}

        _raw = _eng._evolution_raw_stats(_Old())
        self.assertEqual(_raw["history_total"], 0)

    def test_34_integrate_uses_public_then_fallback(self):
        _exec = _FakeExec(self._HIST)
        _res = _eng.SelfAwarenessEngine().integrate_evolution_health(executor=_exec)
        self.assertIsInstance(_res, dict)

    def test_35_helper_function_removed(self):
        """``_evolution_stats_unified`` 辅助函数已删除。"""
        self.assertFalse(hasattr(_eng, "_evolution_stats_unified"))

if __name__ == "__main__":
    unittest.main()
