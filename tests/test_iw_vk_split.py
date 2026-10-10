# -*- coding: utf-8 -*-
"""
第151批 T151-1 · _validate_knowledge_consistency 特征化测试（拆分前行为锁定）

目的：在将 God 方法 `_validate_knowledge_consistency`（L11433-11789）纯结构拆分成编排器+helper 之前，
锁定其「当前」可观测行为：两道 L2 阈值早退、维度1 多源确认（首次确认 emit 顿悟 pri=3 L3）、
维度0 矛盾跟踪复查消解（信任差≥30 且 review_count≥2 → emit 顿悟 pri=3 L3 + 标记 resolved）。

策略：
- 用 FakeNode/FakeNodePool 模拟节点池（L2/L1 query + get），避免真实 DB。
- override `self._emit` 捕获为 (event_type, priority, layer)。
- `_insight_board=None`（所有 board.post 均被 `if self._insight_board:` 守卫跳过）。

共 4 例；全绿后才允许下刀（T151-4）。重点锁定「emit 2 次 pri=3 守恒」现状。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault(
    "PULSE_LOG_FILE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "test_iw_vk.log"),
)

from nucleus.const import Event  # noqa: E402
from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402


class FakeNode:
    def __init__(self, node_id, keywords, value, trust_score=50.0, source_organ="",
                 last_activated=0, activation_count=0):
        self.node_id = node_id
        self.keywords = keywords
        self.value = value
        self.trust_score = trust_score
        self.source_organ = source_organ
        self.last_activated = last_activated
        self.activation_count = activation_count
        self.verification_history = []


class FakeNodePool:
    def __init__(self, l2, l1=None, get_map=None):
        self._l2 = l2
        self._l1 = l1 or []
        self._get_map = get_map or {}

    def query(self, evol_level="L2", limit=200):
        if evol_level == "L2":
            return self._l2
        return self._l1

    def get(self, node_id):
        return self._get_map.get(node_id)


class TestValidateKnowledgeConsistencySplit(unittest.TestCase):
    def setUp(self):
        self.iw = PulseInnerWorld("内在世界")
        self.iw._insight_board = None
        self.iw._contradiction_tracking = []
        self.iw._max_contradiction_tracking = 20
        self._emits = []
        self.iw._emit = lambda event_type, payload=None, priority=5, ttl_ns=5_000_000_000, layer="L1": self._emits.append(  # noqa: E731
            (event_type, priority, layer)
        )

    # ---- 早期返回 ----
    def test_01_no_node_pool_returns_none(self):
        self.iw.node_pool = None
        self.assertIsNone(self.iw._validate_knowledge_consistency())
        self.assertEqual(self._emits, [])

    def test_02_few_l2_nodes_returns_none(self):
        # 第一道阈值：len(L2) < 10 → 直接返回
        self.iw.node_pool = FakeNodePool(
            l2=[FakeNode("n%012d" % i, ["k", "w"], "v") for i in range(3)]
        )
        self.assertIsNone(self.iw._validate_knowledge_consistency())
        self.assertEqual(self._emits, [])

    # ---- 维度1：多源确认 + 首次确认 emit（eureka_confirmation, pri=3, L3） ----
    def test_03_multi_source_confirmation_emits_once(self):
        # 10 个 L2 节点：na(器官A, 高重叠 kw) + 8 个同源填充(器官A, 低重叠) + nb(器官B, 高重叠)
        # 同源对全部跳过；仅 na↔nb 跨源且重叠率≥0.6 且非矛盾 → 恰好 1 次确认。
        na = FakeNode("a" * 12, ["太阳", "升起", "东方", "早晨"], "地球绕着太阳转",
                      trust_score=50.0, source_organ="器官A")
        nb = FakeNode("b" * 12, ["太阳", "升起", "东方", "清晨"], "地球绕着太阳转",
                      trust_score=50.0, source_organ="器官B")
        fillers = [
            FakeNode("f%012d" % i, ["填充%d" % i, "无关"], "v", source_organ="器官A")
            for i in range(8)
        ]
        self.iw.node_pool = FakeNodePool(l2=[na, nb] + fillers, l1=[])
        self.iw._validate_knowledge_consistency()

        # 恰好 1 次 emit，且为 eureka_confirmation（Event.EXPRESS_URGE, pri=3, L3）
        self.assertEqual(len(self._emits), 1)
        _et, _pr, _ly = self._emits[0]
        self.assertEqual(_et, Event.EXPRESS_URGE)
        self.assertEqual(_pr, 3)
        self.assertEqual(_ly, "L3")
        # 确认提升信任（boost = min(8, 3+0.75*5)=6.75）
        self.assertGreater(na.trust_score, 50.0)
        self.assertGreater(nb.trust_score, 50.0)
        # 验证历史被记录
        self.assertTrue(len(na.verification_history) >= 1)
        self.assertTrue(len(nb.verification_history) >= 1)

    # ---- 维度0：矛盾跟踪复查消解 + emit（eureka_resolution, pri=3, L3）+ 清空 ----
    def test_04_contradiction_tracking_resolution_emits_once(self):
        # 10 个同源 L2 节点（维度1 跳过、维度2 信任<70 不衰减、维度3 L1 空）
        pool_nodes = [
            FakeNode("p%012d" % i, ["x", "y"], "v", trust_score=50.0, source_organ="same")
            for i in range(10)
        ]
        # 跟踪的一对节点：信任差 60（≥30），review_count 预置 1 → 复查后变 2 → 消解
        node_a = FakeNode("A" * 12, ["a", "b"], "观点甲", trust_score=20.0, source_organ="same")
        node_b = FakeNode("B" * 12, ["a", "b"], "观点乙", trust_score=80.0, source_organ="same")
        self.iw.node_pool = FakeNodePool(
            l2=pool_nodes,
            l1=[],
            get_map={"A" * 12: node_a, "B" * 12: node_b},
        )
        self.iw._contradiction_tracking = [
            {"node_a_id": "A" * 12, "node_b_id": "B" * 12, "review_count": 1, "resolved": False}
        ]
        self.iw._validate_knowledge_consistency()

        # 恰好 1 次 emit，且为 eureka_resolution（Event.EXPRESS_URGE, pri=3, L3）
        self.assertEqual(len(self._emits), 1)
        _et, _pr, _ly = self._emits[0]
        self.assertEqual(_et, Event.EXPRESS_URGE)
        self.assertEqual(_pr, 3)
        self.assertEqual(_ly, "L3")
        # 跟踪条目已标记 resolved 并从活跃列表移除
        self.assertEqual(self.iw._contradiction_tracking, [])


if __name__ == "__main__":
    unittest.main()
