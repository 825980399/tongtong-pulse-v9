# -*- coding: utf-8 -*-
"""主线第57批 T3（P2-397）：轻量级矛盾解决机制门控测试。

覆盖：
  - classify_contradiction：按类型/证据分类（auto / human）
  - auto_resolve：自动可解决矛盾的解决记录
  - apply_aging：7天降优先级（resolving+low）、30天归档（archived）
  - resolve_batch：分类→自动/人工路由→老化→状态跟踪 的端到端工作流
  - 向后兼容：既有 reduce_tracking 策略消解不受影响
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.reasoning import ContradictionResolver as R  # noqa: E402


def _entry(node_a, node_b, ctype, age_days=0.0, detail_extra=None, **extra):
    detail = {"type": ctype}
    if detail_extra:
        detail.update(detail_extra)
    e = {
        "node_a_id": node_a,
        "node_b_id": node_b,
        "detail": detail,
        "status": R.CONTRADICTION_STATUS_UNRESOLVED,  # 真实跟踪项均带 status 字段
        "detected_at": time.time() - age_days * 86400.0,
    }
    e.update(extra)
    return e


class TestClassifyContradiction(unittest.TestCase):
    def test_numeric_auto(self):
        self.assertEqual(R.classify_contradiction({"type": "numeric"}), R.CONTRADICTION_RESOLVE_AUTO)

    def test_semantic_human(self):
        self.assertEqual(R.classify_contradiction({"type": "semantic"}), R.CONTRADICTION_RESOLVE_HUMAN)

    def test_path_human(self):
        self.assertEqual(R.classify_contradiction({"type": "path"}), R.CONTRADICTION_RESOLVE_HUMAN)

    def test_evidence_keyword_overrides_to_auto(self):
        # 即使非 numeric，证据含「配置/文档/代码」关键词也归自动可解决
        self.assertEqual(
            R.classify_contradiction({"type": "semantic", "evidence": "配置值不一致"}),
            R.CONTRADICTION_RESOLVE_AUTO)
        self.assertEqual(
            R.classify_contradiction({"type": "path", "reason": "文档与代码不符"}),
            R.CONTRADICTION_RESOLVE_AUTO)

    def test_non_dict_human(self):
        self.assertEqual(R.classify_contradiction(None), R.CONTRADICTION_RESOLVE_HUMAN)
        self.assertEqual(R.classify_contradiction("x"), R.CONTRADICTION_RESOLVE_HUMAN)


class TestAutoResolve(unittest.TestCase):
    def test_auto_resolve_record(self):
        rec = R.auto_resolve({"type": "numeric"})
        self.assertTrue(rec["resolved"])
        self.assertEqual(rec["status"], R.CONTRADICTION_STATUS_RESOLVED)
        self.assertEqual(rec["action"], "pending_fix")
        self.assertIn("自动可解决", rec["reason"])


class TestApplyAging(unittest.TestCase):
    def test_fresh_unresolved_untouched(self):
        t = [_entry("a", "b", "semantic", age_days=0.001)]
        self.assertEqual(R.apply_aging(t, now=time.time()), 0)
        self.assertEqual(t[0].get("status"), R.CONTRADICTION_STATUS_UNRESOLVED)
        self.assertNotIn("priority", t[0])

    def test_old_unresolved_demoted(self):
        t = [_entry("a", "b", "semantic", age_days=10)]
        self.assertEqual(R.apply_aging(t, now=time.time()), 1)
        self.assertEqual(t[0].get("status"), R.CONTRADICTION_STATUS_RESOLVING)
        self.assertEqual(t[0].get("priority"), "low")

    def test_ancient_unresolved_archived(self):
        t = [_entry("a", "b", "semantic", age_days=40)]
        self.assertEqual(R.apply_aging(t, now=time.time()), 1)
        self.assertEqual(t[0].get("status"), R.CONTRADICTION_STATUS_ARCHIVED)

    def test_resolved_untouched_by_aging(self):
        t = [_entry("a", "b", "semantic", age_days=40, status=R.CONTRADICTION_STATUS_RESOLVED)]
        self.assertEqual(R.apply_aging(t, now=time.time()), 0)

    def test_already_routed_old_item_gets_low_priority(self):
        # ★修复验证：resolve_batch 已置 resolving 的旧项，老化仍应降优先级
        t = [_entry("a", "b", "semantic", age_days=20, status=R.CONTRADICTION_STATUS_RESOLVING)]
        self.assertEqual(R.apply_aging(t, now=time.time()), 1)
        self.assertEqual(t[0].get("priority"), "low")


class TestResolveBatch(unittest.TestCase):
    def test_mixed_split_and_status(self):
        now = time.time()
        tracking = [
            _entry("a1", "b1", "numeric", age_days=0.001),
            _entry("a2", "b2", "semantic", age_days=0.001),
            _entry("a3", "b3", "semantic", age_days=0.001, detail_extra={"evidence": "配置不一致"}),
        ]
        logs = []
        res = R.resolve_batch(tracking, now=now, logger=logs.append)
        self.assertEqual(res["auto_resolved"], 2)   # numeric + evidence-override
        self.assertEqual(res["human_routed"], 1)    # semantic
        self.assertEqual(len(res["adjudications"]), 1)
        self.assertEqual(tracking[0].get("status"), R.CONTRADICTION_STATUS_RESOLVED)
        self.assertEqual(tracking[2].get("status"), R.CONTRADICTION_STATUS_RESOLVED)
        self.assertEqual(tracking[1].get("status"), R.CONTRADICTION_STATUS_RESOLVING)
        self.assertTrue(any("矛盾待裁决" in m for m in logs))

    def test_aging_applied_within_batch(self):
        now = time.time()
        tracking = [
            _entry("a4", "b4", "semantic", age_days=20),   # 旧→路由后老化降优先级
            _entry("a5", "b5", "semantic", age_days=40),   # 极旧→归档
        ]
        res = R.resolve_batch(tracking, now=now)
        self.assertGreaterEqual(res["aged"], 1)
        self.assertEqual(tracking[0].get("priority"), "low")
        self.assertEqual(tracking[1].get("status"), R.CONTRADICTION_STATUS_ARCHIVED)

    def test_can_classify_existing_40_pairs_shape(self):
        # 验收：现有 40 对矛盾至少能自动分类（auto vs human）
        now = time.time()
        pairs = [
            _entry(f"n{i}", f"m{i}", "numeric", age_days=0.001) for i in range(10)
        ] + [
            _entry(f"s{i}", f"t{i}", "semantic", age_days=0.001) for i in range(30)
        ]
        res = R.resolve_batch(pairs, now=now)
        self.assertEqual(res["auto_resolved"], 10)
        self.assertEqual(res["human_routed"], 30)
        self.assertEqual(res["auto_resolved"] + res["human_routed"], 40)


class TestBackwardCompat(unittest.TestCase):
    def test_reduce_tracking_still_works(self):
        # 既有策略消解入口不受影响（detector + resolver 未被改动）
        from nucleus.reasoning.ContradictionDetector import ContradictionDetector

        class _Node:
            def __init__(self, value, ts):
                self.value = value
                self.timestamp = ts
                self.node_id = value

        a = _Node("正确", 100.0)
        b = _Node("错误", 200.0)
        self.assertIsNotNone(ContradictionDetector.detect_semantic(a.value, b.value))
        tracking = [{"node_a_id": "正确", "node_b_id": "错误", "resolved": False}]
        got = R.ContradictionResolver.reduce_tracking(
            tracking, lambda nid: a if nid == "正确" else b, strategy="time")
        self.assertEqual(got, 1)
        self.assertTrue(tracking[0]["resolved"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
