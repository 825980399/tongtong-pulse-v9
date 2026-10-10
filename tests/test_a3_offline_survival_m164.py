# -*- coding: utf-8 -*-
"""
test_a3_offline_survival_m164.py —— 164批 刀A3 断网生存协议 隔离单测

验收点：
    1. 外脑在线（有回复）→ survive_offline 不改写任何行为（used=False）。
    2. 外脑不可达（None / 空白）→ 走本地 Symbolic→Causal→Analogy 链，
       解出则标 [本地推理·待验证]，verified=False。
    3. 本地未解出 → 返回诚实「部分结果」而非空模板应答（display=None，调用方据此委派外脑）。
    4. 本地三腿（symbolic / causal / analogy）各自可用。
    5. 联网复核缓冲 enqueue / drain / count 正确。

隔离：仅依赖 nucleus.reasoning 本地推理三件套，不触框架、不落盘真实 data/。
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.reasoning.offline_survival_protocol import (  # noqa: E402
    OFFLINE_TAG,
    OfflineResult,
    drain_recheck,
    enqueue_recheck,
    pending_count,
    run_offline_chain,
    survive_offline,
)


class TestSurviveOfflineGateway(unittest.TestCase):
    """对外统一入口 survive_offline 的网关语义。"""

    def test_online_path_unchanged(self):
        used, res = survive_offline("今天天气如何", "今天晴")
        self.assertFalse(used)
        self.assertIsNone(res)

    def test_offline_triggers_local_chain(self):
        used, res = survive_offline("甲比乙高，丙比甲高，谁最高？", None)
        self.assertTrue(used)
        self.assertIsNotNone(res)
        self.assertTrue(res.solved)
        self.assertEqual(res.method, "symbolic")
        self.assertFalse(res.verified)                 # 离线结果恒待联网复核
        self.assertEqual(res.tag, OFFLINE_TAG)
        self.assertIn(OFFLINE_TAG, res.display)
        self.assertEqual(res.answer, "丙")

    def test_offline_blank_reply_treated_as_offline(self):
        used, res = survive_offline("甲比乙高，丙比甲高，谁最高？", "   ")
        self.assertTrue(used)
        self.assertTrue(res.solved)

    def test_plain_question_no_template_answer(self):
        used, res = survive_offline("讲个冷笑话", None)
        self.assertTrue(used)
        # 本地未解出 → 不返回空模板，display 为 None（调用方据此委派外脑）
        self.assertFalse(res.solved)
        self.assertIsNone(res.display)
        self.assertNotEqual(res.method, "none")        # 仍是 symbolic_partial（带本地尝试证据）

    def test_disabled_switch_falls_through(self):
        with mock.patch(
            "nucleus.reasoning.offline_survival_protocol.OFFLINE_SURVIVAL_ENABLED", False
        ):
            used, res = survive_offline("甲比乙高，丙比甲高，谁最高？", None)
        self.assertFalse(used)
        self.assertIsNone(res)


class TestOfflineChainLegs(unittest.TestCase):
    """本地推理链三腿各自可用。"""

    def test_symbolic_leg(self):
        r = run_offline_chain("甲比乙高，丙比甲高，谁最高？")
        self.assertTrue(r.solved)
        self.assertEqual(r.method, "symbolic")
        self.assertEqual(r.answer, "丙")

    def test_causal_leg(self):
        with mock.patch(
            "nucleus.reasoning.offline_survival_protocol.get_causal_inferrer"
        ) as m:
            inst = mock.MagicMock()
            inst.verify_chain.return_value = {
                "verified": True,
                "answer": "因→果成立",
                "confidence": 0.9,
            }
            m.return_value = inst
            r = run_offline_chain(
                "为什么",
                causal_pairs=[{"cause": "a", "effect": "b"}],
                node_pool=mock.MagicMock(),
            )
        self.assertTrue(r.solved)
        self.assertEqual(r.method, "causal")
        self.assertEqual(r.answer, "因→果成立")

    def test_analogy_leg(self):
        with mock.patch(
            "nucleus.reasoning.offline_survival_protocol.AnalogyEngine"
        ) as m:
            inst = mock.MagicMock()
            ares = mock.MagicMock()
            ares.is_cross_domain = True
            ares.migrated_hypothesis = "迁移假设"
            ares.confidence = 0.9
            ares.similarity = 0.8
            ares.shared_structure = "量高→量大"
            inst.compare.return_value = ares
            m.return_value = inst
            r = run_offline_chain(
                "新问题",
                analogy_texts=("电压越高电流越大", "水压越高水流越大"),
            )
        self.assertTrue(r.solved)
        self.assertEqual(r.method, "analogy")
        self.assertEqual(r.answer, "迁移假设")

    def test_all_legs_fail_returns_partial_not_template(self):
        r = run_offline_chain("讲个冷笑话")          # 本地符号无法解出
        self.assertFalse(r.solved)
        self.assertIn(r.method, ("symbolic_partial", "symbolic"))
        self.assertFalse(r.verified)
        self.assertEqual(r.tag, OFFLINE_TAG)
        # 关键：不返回空模板（answer 可为 None，但对象是诚实未解出的 OfflineResult）
        self.assertIsInstance(r, OfflineResult)


class TestRecheckBuffer(unittest.TestCase):
    """联网后外脑批量复核沉淀的缓冲。"""

    def setUp(self):
        drain_recheck()                              # 清空进程内缓冲

    def test_enqueue_drain_count(self):
        self.assertEqual(pending_count(), 0)
        enqueue_recheck({"question": "q1", "answer": "a1", "ts": 1.0})
        enqueue_recheck({"question": "q2", "answer": "a2", "ts": 2.0})
        self.assertEqual(pending_count(), 2)
        drained = drain_recheck()
        self.assertEqual(len(drained), 2)
        self.assertEqual(pending_count(), 0)
        self.assertEqual(drained[0]["answer"], "a1")

    def test_recheck_record_carries_tag(self):
        used, res = survive_offline("甲比乙高，丙比甲高，谁最高？", None)
        self.assertTrue(used)
        rec = {
            "question": "x",
            "answer": res.answer,
            "method": res.method,
            "tag": res.tag,
        }
        enqueue_recheck(rec)
        self.assertEqual(pending_count(), 1)
        self.assertEqual(drain_recheck()[0]["tag"], OFFLINE_TAG)


if __name__ == "__main__":
    unittest.main(verbosity=2)
