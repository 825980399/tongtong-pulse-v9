# -*- coding: utf-8 -*-
"""
第151批 T151-1 · _cognitive_reflection 特征化测试（拆分前行为锁定）

目的：在将 God 方法 `_cognitive_reflection`（L4873-5226）纯结构拆分成编排器+helper 之前，
用特征化测试锁定其「当前」可观测行为，保证后续拆分（零逻辑修改）后跑同套测试语义不变。

策略：
- 实例化 PulseInnerWorld('内在世界')（node_pool=None 无外部 I/O，安全）。
- override `self._emit` 捕获所有脉冲事件为 (event_type, priority, layer)。
- 将所有「洞察生成」类 helper 统一 mock 为返回 None，隔离编排逻辑；
  这样只有早期比例触发与失败域分支会产生可观测输出/emit。
- 不调用任何外部 LLM（knowledge/节点池默认 None，helper 全 mock）。

共 4 例；全绿后才允许下刀（T151-3）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault(
    "PULSE_LOG_FILE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "test_iw_cr.log"),
)

from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402
from nucleus.const import GrowthEvent  # noqa: E402

# 在默认 node_pool=None 场景下、且 r=1 时会被无条件调用的洞察生成 helper；
# 统一 mock 为 None 以隔离编排逻辑。r%N 门控项也一并 mock（实例属性遮蔽，无害）。
_CR_HELPERS = [
    "_verify_knowledge_relevance",
    "_assess_knowledge_health",
    "_scan_knowledge_blind_spots",
    "_scan_knowledge_precipitation",
    "_analyze_specific_cases",
    "_abstract_thinking_pattern",
    "_attempt_knowledge_repair",
    "_integrate_knowledge_insights",
    "_evaluate_learning_effectiveness",
    "_generate_self_vision",
    "_decompose_vision_into_milestones",
    "_set_active_learning_goal",
    "_convert_vision_to_learning",
    "_review_cognitive_tensions",
    "_attempt_knowledge_innovation",
    "_generate_self_directed_learning_plan",
    "_generate_learning_pathway",
    "_explore_cognitive_boundary",
    "_manage_active_projects",
    "_generate_holographic_self_assessment",
    "_apply_adaptive_regulation",
    "_integrate_worldview",
    "_synthesize_meta_insight",
    "_generate_growth_sharing",
    "_extract_weak_areas_from_reflection",
    "_check_learning_goal_progress",
    "_analyze_experience_quality",
]


class TestCognitiveReflectionSplit(unittest.TestCase):
    def setUp(self):
        self.iw = PulseInnerWorld("内在世界")
        # 关闭外部依赖
        self.iw.node_pool = None
        self.iw._insight_board = None
        # 重置可变状态，保证用例间独立
        self.iw._inference_trace = []
        self.iw._reflection_round = 0
        self.iw._last_reflection_insights = []
        self.iw._failed_domain_records = {}
        self.iw._cognitive_tensions = []
        self.iw._active_learning_goal = None
        self.iw._learning_goal_queue = []
        self.iw._max_goal_queue = 5
        # 捕获 emit
        self._emits = []
        self.iw._emit = lambda event_type, payload=None, priority=5, ttl_ns=5_000_000_000, layer="L1": self._emits.append(  # noqa: E731
            (event_type, priority, layer)
        )
        # 屏蔽所有洞察生成 helper
        for _name in _CR_HELPERS:
            setattr(self.iw, _name, lambda *a, **k: None)

    def _trace(self, n, method="deriver_step", confidence=0.9):
        return [
            {"method": method, "confidence": confidence, "question": "q", "answer": "a"}
            for _ in range(n)
        ]

    # ---- 早期返回 ----
    def test_01_empty_trace_returns_none(self):
        self.iw._inference_trace = []
        self.assertIsNone(self.iw._cognitive_reflection())
        self.assertEqual(self.iw._reflection_round, 0)

    def test_02_short_trace_returns_none(self):
        self.iw._inference_trace = self._trace(9)
        self.assertIsNone(self.iw._cognitive_reflection())
        self.assertEqual(self.iw._reflection_round, 0)

    # ---- 正常生成 + 状态写入 ----
    def test_03_balanced_trace_falls_back_to_equilibrium_insight(self):
        # 全部 deriver_* 方法：rule/knowledge/contemplation/cache 比例均为 0，
        # 高置信度 → 无比例触发 insight；helper 全 None → insights 空 → 触发均衡兜底。
        self.iw._inference_trace = self._trace(15)
        out = self.iw._cognitive_reflection()
        self.assertIsNotNone(out)
        self.assertIn("我的思考模式比较均衡", out)
        # 状态写入：轮次自增、洞察落盘
        self.assertEqual(self.iw._reflection_round, 1)
        self.assertTrue(len(self.iw._last_reflection_insights) >= 1)
        self.assertTrue(self.iw._last_reflection_insights[-1].endswith("。"))
        # 均衡兜底分支不应产生任何 emit
        self.assertEqual(self._emits, [])

    # ---- 失败域分支：emit 守恒（GrowthEvent.NEED_DETECTED, pri=5, L3）+ 记录清空 ----
    def test_04_failed_domain_records_emit_and_clear(self):
        self.iw._inference_trace = self._trace(15)
        self.iw._failed_domain_records = {"边界A": 5}
        out = self.iw._cognitive_reflection()
        self.assertIsNotNone(out)
        self.assertIn("边界A", out)
        # 仅失败域分支产生 1 次发射
        self.assertEqual(len(self._emits), 1)
        _et, _pr, _ly = self._emits[0]
        self.assertEqual(_et, GrowthEvent.NEED_DETECTED)
        self.assertEqual(_pr, 5)
        self.assertEqual(_ly, "L3")
        # 分析后清空跟踪记录
        self.assertEqual(self.iw._failed_domain_records, {})


if __name__ == "__main__":
    unittest.main()
