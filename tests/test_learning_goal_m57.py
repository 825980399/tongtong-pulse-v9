# -*- coding: utf-8 -*-
"""主线第57批 T2（P2-396）：学习目标「自我反思」连续失败根因修复门控测试。

覆盖：
  - is_meta_skill_goal 分类逻辑（实践型/元认知目标 vs 知识型目标）
  - _evaluate_learning_effectiveness 对元认知目标的豁免：
      不计入连续失败计数（可重置预置失败）、不触发强制切换、返回「已执行即有效」
  - 一般知识型目标在 growth<=0 & 成功率<0.5 时仍正常累加失败计数（证明豁免仅限元认知目标）
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from organs.brain.PulseInnerWorld import (  # noqa: E402
    PulseInnerWorld,
    is_meta_skill_goal,
)


class _FakePool:
    """最小 node_pool：返回空路径分布（元认知目标无对应知识节点）。"""

    def get_path_distribution(self):
        return {}


def _mk_iw(target_area, pre_fail=0):
    """构造最小 PulseInnerWorld 实例（不调用 __init__），装备评估所需属性。"""
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._logs = []
    iw._log = lambda level, msg: iw._logs.append((str(level), str(msg)))
    iw._emit = lambda *a, **kw: None
    iw._inference_trace = []
    iw.node_pool = _FakePool()
    iw._get_path_label_for_plan = lambda x: None
    iw._learning_goal_queue = []
    iw._active_learning_goal = target_area
    iw._consecutive_fail_counts = {}
    if pre_fail:
        iw._consecutive_fail_counts[target_area] = pre_fail
    iw._learning_history = [{
        "evaluated": False,
        "target_area": target_area,
        "started_at": time.time() - 1000,  # 已运行足够久，满足评估窗口
        "nodes_before": 0,
    }]
    return iw


class TestIsMetaSkillGoal(unittest.TestCase):
    def test_meta_areas_true(self):
        for area in ("自我反思", "自我认知", "底层本能", "社会关系"):
            self.assertTrue(is_meta_skill_goal(area), area)

    def test_keyword_substring_true(self):
        self.assertTrue(is_meta_skill_goal("反思能力"))
        self.assertTrue(is_meta_skill_goal("社会关系学"))

    def test_knowledge_areas_false(self):
        for area in ("技术架构", "知识体系", "微服务架构", ""):
            self.assertFalse(is_meta_skill_goal(area), repr(area))

    def test_none_false(self):
        self.assertFalse(is_meta_skill_goal(None))


class TestMetaGoalExemption(unittest.TestCase):
    def test_meta_goal_resets_preset_fail_count(self):
        # 预置失败计数，验证元认知目标评估会将其重置为 0（不累积）
        iw = _mk_iw("自我反思", pre_fail=5)
        out = PulseInnerWorld._evaluate_learning_effectiveness.__get__(iw)()
        self.assertEqual(iw._consecutive_fail_counts.get("自我反思"), 0)
        self.assertIsInstance(out, str)
        self.assertIn("自我反思", out)
        # 未触发强制切换
        self.assertNotIn("学习目标强制切换", "\n".join(m for _, m in iw._logs))

    def test_meta_goal_logs_info_not_warning(self):
        iw = _mk_iw("自我反思")
        PulseInnerWorld._evaluate_learning_effectiveness.__get__(iw)()
        self.assertTrue(any("已执行评估为有效" in m for _, m in iw._logs))
        self.assertFalse(any("学习目标强制切换" in m for _, m in iw._logs))


class TestKnowledgeGoalStillFails(unittest.TestCase):
    def test_knowledge_goal_accumulates_fail(self):
        # 一般知识型目标在 growth<=0 & 成功率<0.5 时仍累加失败计数（豁免是特例）
        iw = _mk_iw("技术架构")
        out = PulseInnerWorld._evaluate_learning_effectiveness.__get__(iw)()
        self.assertEqual(iw._consecutive_fail_counts.get("技术架构"), 1)
        self.assertIsInstance(out, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
