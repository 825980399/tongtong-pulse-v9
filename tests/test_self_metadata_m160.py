# -*- coding: utf-8 -*-
"""★第160批 上A 刀3（票1④·元数据不入库）防回归单测。

验证三件事：
1. 灰度开关 KNOWLEDGE_EXCLUDE_METADATA_FROM_RETRIEVAL 存在且默认 True；
   关闭后自检/元数据节点会重新进通用检索池（应急回滚旋钮）。
2. PulseNode.is_metadata 字段默认 False，且经 to_dict/from_dict 往返保持；
   这是「自检内容不得入知识库」的结构化落点。
3. _is_internal_knowledge_node 对 legacy "📊 知识库健康检查报告" 节点返回 True
   （根治 trust 90.3 万能答案；普通知识节点不受影响）。

标签：@pytest.mark.m160
"""
import importlib

import pytest


@pytest.mark.m160
class TestKn160Knife3Config:
    def test_exclude_switch_exists_default(self):
        import config
        importlib.reload(config)
        assert hasattr(config, "KNOWLEDGE_EXCLUDE_METADATA_FROM_RETRIEVAL")
        assert config.KNOWLEDGE_EXCLUDE_METADATA_FROM_RETRIEVAL is True


@pytest.mark.m160
class TestPulseNodeIsMetadata:
    def _make_node(self, value):
        from nucleus.mnemosyne.PulseNode import PulseNode
        return PulseNode(value=value)

    def test_default_false(self):
        n = self._make_node("普通知识节点")
        assert n.is_metadata is False

    def test_roundtrip_true(self):
        from nucleus.mnemosyne.PulseNode import PulseNode
        n = self._make_node("自我状态快照")
        n.is_metadata = True
        d = n.to_dict()
        assert d.get("is_metadata") is True
        n2 = PulseNode.from_dict(d)
        assert n2.is_metadata is True

    def test_roundtrip_false(self):
        from nucleus.mnemosyne.PulseNode import PulseNode
        n = self._make_node("另一个普通节点")
        d = n.to_dict()
        assert d.get("is_metadata") is False
        n2 = PulseNode.from_dict(d)
        assert n2.is_metadata is False


class _Stub:
    def _log(self, *a, **k):
        return None


@pytest.mark.m160
class TestInternalFilterCatchesHealthReport:
    def test_legacy_health_report_excluded(self):
        from organs.brain.pulse_inner_world_knowledge import PulseInnerWorldKnowledgeMixin
        val = "📊 知识库健康检查报告\n\n当前知识库状态：\n  · 总节点: 1234个\n"
        assert PulseInnerWorldKnowledgeMixin._is_internal_knowledge_node(_Stub(), val) is True

    def test_normal_knowledge_not_excluded(self):
        from organs.brain.pulse_inner_world_knowledge import PulseInnerWorldKnowledgeMixin
        val = "递归是一种函数调用自身的编程技巧，常用于树的深度优先遍历。"
        assert PulseInnerWorldKnowledgeMixin._is_internal_knowledge_node(_Stub(), val) is False
