# -*- coding: utf-8 -*-
"""第167批 C3｜T-内在世界检索万能复用-1 ＋ T-占位符空槽泄漏-1 验证测试

★背景（T0偏差核实）：
  任务书称「召回侧无过滤：pulse_inner_world_knowledge.py 内 PlaceholderSanitizer/
  placeholder_alias 0 命中」。实测：检索/排序层（VectorStore / PulseNodePool /
  pulse_inner_world_support / PulseInnerWorld 出口）早已调用 contains_placeholder*
  总入口；融合侧 KNOWLEDGE_FUSION_SKIP_PLACEHOLDER=True 也已封堵融合输入。
  真正缺口仅在 _knowledge_retrieve 两处直出分支（单节点 / 关键词兜底）未过滤，
  故本批补「召回侧占位符过滤」统一出口判据 _recall_node_is_skippable，与出口净化
  DIALOG_SANITIZE_LEVEL="literal" 双保险，满足验收「空槽零「」/零截断」「同批节点复用≤1」。

本测试直测统一出口判据：正例（占位符字面量 / 空槽「」/ 截断 p...）必跳过；
反例（正常知识节点）必返回（豁免）；灰度开关关闭时占位符不再被召回侧拦截。
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from organs.brain.pulse_inner_world_knowledge import (  # noqa: E402
    PulseInnerWorldKnowledgeMixin,
)


class TestRecallPlaceholderSkip(unittest.TestCase):
    """验证 _recall_node_is_skippable 召回侧占位符过滤（正/反/豁免 + 开关反向）。"""

    def setUp(self):
        self.m = PulseInnerWorldKnowledgeMixin()

    def test_internal_marker_skipped(self):
        """既有内部标记节点过滤行为保持。"""
        self.assertTrue(self.m._recall_node_is_skippable("[曈曈] 内部标记内容"))

    def test_placeholder_literal_skipped(self):
        """正例：占位符字面量 [器官别名] ⇒ 必跳过。"""
        self.assertTrue(self.m._recall_node_is_skippable("[器官别名] 人格内核"))

    def test_empty_slot_skipped(self):
        """正例：空槽「」⇒ 必跳过（验收「空槽零泄漏」的召回侧堵源）。"""
        self.assertTrue(self.m._recall_node_is_skippable("你对进化的理解是「」"))

    def test_truncated_skipped(self):
        """正例：截断占位 p... ⇒ 必跳过。"""
        self.assertTrue(self.m._recall_node_is_skippable("这是 p... 的截断占位"))

    def test_normal_returned(self):
        """反/豁免例：正常知识节点 ⇒ 不跳过（应返回，不误伤）。"""
        self.assertFalse(
            self.m._recall_node_is_skippable("正常的中文知识回答，描述某个核心概念"))

    def test_gate_off_keeps_placeholder(self):
        """灰度开关关闭时，占位符节点不再被召回侧拦截（退回出口净化兜底）。"""
        import config as _cfg
        _orig = getattr(_cfg, "KNOWLEDGE_RECALL_SKIP_PLACEHOLDER", True)
        try:
            with mock.patch.object(_cfg, "KNOWLEDGE_RECALL_SKIP_PLACEHOLDER", False):
                self.assertFalse(
                    self.m._recall_node_is_skippable("[器官别名] 人格内核"))
        finally:
            _cfg.KNOWLEDGE_RECALL_SKIP_PLACEHOLDER = _orig


if __name__ == "__main__":
    unittest.main()
