# -*- coding: utf-8 -*-
"""第98批 T-98b（P1）门控测试：肝订阅并消费 digest.knowledge，消除孤儿脉冲。

根因：代码学习器官长期发射 DigestEvent.KNOWLEDGE，但无器官接收，
pulse_orphans.json 持续积累 digest.knowledge 孤儿。
修复：肝（知识代谢中枢）在 get_resonance_conditions 增加 DigestEvent.KNOWLEDGE，
并在 on_pulse 中分流到 _on_digest_knowledge 做知识沉淀（异常静默降级）。
本测试验证：(1) 订阅契约成立；(2) 消费逻辑正确沉淀节点；(3) 空内容安全跳过。
"""
import os
import sys
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.const import DigestEvent  # noqa: E402
from organs.body.PulseLiver import PulseLiver  # noqa: E402


class TestOrphanConsumerM98:
    def test_liver_subscribes_digest_knowledge(self):
        # 核心契约：肝订阅 digest.knowledge → 发射后必有器官接收，孤儿消除
        o = PulseLiver(organ_name="肝")
        types = []
        for cond in o.get_resonance_conditions():
            types.extend(cond.get("event_types", []))
        assert DigestEvent.KNOWLEDGE in types

    def test_on_digest_knowledge_adds_node(self):
        o = PulseLiver(organ_name="肝")
        o.is_running = True
        fake_pool = mock.MagicMock()
        o.node_pool = fake_pool  # 满足生产「node_pool is not None」前置守卫
        kal = mock.MagicMock()
        # ★B156-3 T-A09 接线点：生产已改走 self._kal.add_node（KAL 集成，8d9b95f 债务清偿既定形态）
        o._kal = kal
        payload = {"content": "示例消化知识点", "source_organ": "代码学习"}
        o._on_digest_knowledge(payload)
        # 替身过时：原断言 fake_pool.add 已非当前接线点
        kal.add_node.assert_called_once()
        node = kal.add_node.call_args.args[0]
        assert "digest.knowledge" in node.keywords
        assert node.source_organ == "肝"

    def test_on_digest_knowledge_empty_content_noop(self):
        o = PulseLiver(organ_name="肝")
        o.is_running = True
        fake_pool = mock.MagicMock()
        o.node_pool = fake_pool
        o._on_digest_knowledge({})
        fake_pool.add.assert_not_called()

    def test_on_digest_knowledge_fault_tolerant(self):
        # 知识沉淀异常必须静默降级，绝不抛出、绝不影响脉冲消费
        o = PulseLiver(organ_name="肝")
        o.is_running = True
        bad_pool = mock.MagicMock()
        bad_pool.add.side_effect = RuntimeError("boom")
        o.node_pool = bad_pool
        try:
            o._on_digest_knowledge({"content": "x"})
        except Exception as e:  # pragma: no cover
            raise AssertionError(f"消费逻辑不应抛异常: {e}")
