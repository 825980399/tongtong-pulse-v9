# -*- coding: utf-8 -*-
"""169批 C5（T-依赖度指标-1 + T-进化费用追踪-1 合刀前半）门控测试。

锁定：
  1. 本地决策率 = 1 - llm_dependency_ratio，**★禁自算**（必须复用唯一口径方法，
     不得用 local/(llm+local) 另算）—— 用源码级守卫锁死；
  2. 依赖度「阶段目标线」落进唯一口径件（本模块常量 + 读数方法）；
  3. 快照/小时日志均带本地决策率。

★费用维度（credits/cost）实测 call_recorder 0 命中 -> 本刀**不伪造**任何字段
  （不写 credits/cost 代码），只在交付报告立结论票。
"""
import inspect
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.LLMDependencyMetrics as m

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestLocalDecisionRate(unittest.TestCase):
    """① 本地决策率 = 1 - 依赖度（同源、禁自算）。"""

    def setUp(self):
        self.x = m.LLMDependencyMetrics(base_dir=tempfile.mkdtemp(
            prefix="m169_c5_"), auto_hourly_log=False)

    def tearDown(self):
        shutil.rmtree(self.x._base_dir, ignore_errors=True)

    def test_10_complements_ratio(self):
        with mock.patch.object(self.x, "llm_dependency_ratio", return_value=0.75):
            self.assertEqual(self.x.local_decision_rate(), 0.25)

    def test_11_zero_and_one_edges(self):
        with mock.patch.object(self.x, "llm_dependency_ratio", return_value=1.0):
            self.assertEqual(self.x.local_decision_rate(), 0.0)
        with mock.patch.object(self.x, "llm_dependency_ratio", return_value=0.0):
            self.assertEqual(self.x.local_decision_rate(), 1.0)

    def test_12_no_self_computation(self):
        """★禁自算守卫：实现必须复用 llm_dependency_ratio()，不得另算分母。"""
        _src = inspect.getsource(m.LLMDependencyMetrics.local_decision_rate)
        self.assertIn("self.llm_dependency_ratio()", _src,
                      "本地决策率必须复用唯一口径，禁止自算: {}".format(_src))
        # 不得出现 local_total / (llm_total + local_total) 形式的自算
        self.assertNotIn("self.local_total()", _src)
        self.assertNotIn("self.llm_total()", _src)

    def test_13_matches_live_data(self):
        """实测数据口径：1 - 0.9694 = 0.0306。"""
        with mock.patch.object(self.x, "llm_dependency_ratio",
                               return_value=0.9694):
            self.assertEqual(self.x.local_decision_rate(), 0.0306)


class TestDependencyTarget(unittest.TestCase):
    """② 阶段目标线（唯一口径件 = 本模块）。"""

    def setUp(self):
        self.x = m.LLMDependencyMetrics(base_dir=tempfile.mkdtemp(
            prefix="m169_c5_"), auto_hourly_log=False)

    def tearDown(self):
        shutil.rmtree(self.x._base_dir, ignore_errors=True)

    def test_20_baseline_and_targets_in_module(self):
        """★验收①：目标线落进唯一口径件（模块常量），不是散在 config。"""
        self.assertEqual(m.LLM_DEPENDENCY_BASELINE, 0.9694)
        self.assertTrue(len(m.LLM_DEPENDENCY_TARGETS) >= 1)
        for _stage, _t in m.LLM_DEPENDENCY_TARGETS:
            self.assertIsInstance(_stage, str)
            self.assertLess(_t, m.LLM_DEPENDENCY_BASELINE, "目标须低于基线（下降目标）")

    def test_21_first_unmet_stage(self):
        with mock.patch.object(self.x, "llm_dependency_ratio",
                               return_value=0.9694):
            _t = self.x.dependency_target()
        self.assertEqual(_t["stage"], "阶段一")
        self.assertEqual(_t["target"], 0.90)
        self.assertAlmostEqual(_t["gap"], 0.0694, places=4)
        self.assertFalse(_t["reached"])

    def test_22_progresses_to_next_stage(self):
        """降到 0.85 → 阶段一已达标，目标推进到阶段二。"""
        with mock.patch.object(self.x, "llm_dependency_ratio",
                               return_value=0.85):
            _t = self.x.dependency_target()
        self.assertEqual(_t["stage"], "阶段二")
        self.assertEqual(_t["target"], 0.80)
        self.assertFalse(_t["reached"])

    def test_23_all_met(self):
        with mock.patch.object(self.x, "llm_dependency_ratio",
                               return_value=0.65):
            _t = self.x.dependency_target()
        self.assertTrue(_t["reached"])
        self.assertLessEqual(_t["gap"], 0.0)


class TestSnapshotAndHourlyLog(unittest.TestCase):
    """③ 快照与日报（小时日志）带本地决策率。"""

    def setUp(self):
        self.x = m.LLMDependencyMetrics(base_dir=tempfile.mkdtemp(
            prefix="m169_c5_"), auto_hourly_log=False)

    def tearDown(self):
        shutil.rmtree(self.x._base_dir, ignore_errors=True)

    def test_30_snapshot_has_new_fields(self):
        _d = self.x.get_snapshot()["derived"]
        self.assertIn("local_decision_rate", _d)
        self.assertIn("dependency_target", _d)
        self.assertAlmostEqual(
            _d["local_decision_rate"] + _d["llm_dependency_ratio"], 1.0,
            places=4, msg="本地决策率与依赖度必须恒等互补")

    def test_31_hourly_log_mentions_local_decision(self):
        _rec = []

        class _L:
            def info(self, msg, *a, **k):
                _rec.append(str(msg))

            warning = debug = error = info

        with mock.patch.object(m, "_logger", _L()):
            self.x.log_hourly()
        self.assertTrue(any("本地决策率" in x for x in _rec),
                        "小时日志未出现本地决策率: {}".format(_rec))


if __name__ == "__main__":
    unittest.main()
