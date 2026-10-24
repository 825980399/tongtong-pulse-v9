# -*- coding: utf-8 -*-
"""363 批 A 组验收（A1 O3 命令面 / A2 pending 分级进候选 / A3 消费口观测埋点）。

纪律：测试只守**不变量与判据语义**，不锁易变总行数、不写生产 data/。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution import patch_execution_dispatch as d  # noqa: E402
from nucleus.reasoning.PatchManager import PatchManager  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_ROW_SEQ = [0]


def _row(**kw) -> dict:
    _ROW_SEQ[0] += 1
    base = {"id": "patch_test_%d" % _ROW_SEQ[0],
            "file": "organs/motor/PulseController.py",
            "method": "refresh_runtime_params",
            "status": "runtime_verified", "risk_level": "低",
            "trust_score": 60.0, "source": "llm",
            "issue_type": "silent_exception"}
    base.update(kw)
    return base


class TestA1O3CommandFace(unittest.TestCase):
    """A1：O3 专用命令面接线（四项不齐不放行，不替人工判断）。"""

    def test_01_o3_branch_precedes_generic(self):
        """命令分发表里 O3 分支必须排在普通 approve 之前，否则被 startswith 吃掉。"""
        src = open(os.path.join(_ROOT, "functions", "chat", "chat_service.py"),
                   encoding="utf-8").read()
        i_o3 = src.find('cmd.startswith(("approve_o3"')
        i_gen = src.find('cmd.startswith(("approve", "批准"))')
        self.assertGreater(i_o3, 0, "O3 命令分支必须存在")
        self.assertGreater(i_gen, 0)
        self.assertLess(i_o3, i_gen, "O3 分支必须排在普通 approve 之前")

    def test_02_basis_from_nested_basis(self):
        from functions.chat.chat_service import ChatService
        p = {"basis": {"items": {"evidence_tier": "A", "probe_out_of_graph": False,
                                 "misjudge_sample": "x.py:1", "god_file": False}}}
        items = ChatService._m363_o3_basis_of(p)["items"]
        self.assertEqual(items["evidence_tier"], "A")
        self.assertFalse(items["god_file"])

    def test_03_basis_from_top_level_fallback(self):
        from functions.chat.chat_service import ChatService
        p = {"evidence_tier": "B", "probe_out_of_graph": False,
             "misjudge_sample": "y.py:2", "god_file": False}
        items = ChatService._m363_o3_basis_of(p)["items"]
        self.assertEqual(items["evidence_tier"], "B")

    def test_04_basis_missing_is_none_not_default(self):
        """★不替人工判断：缺项必须是 None，不许填 False 蒙混过关。"""
        from functions.chat.chat_service import ChatService
        items = ChatService._m363_o3_basis_of({})["items"]
        self.assertTrue(all(v is None for v in items.values()))
        _ok, _why, _miss = PatchManager._m339_review_items_ok({"items": items})
        self.assertFalse(_ok)
        self.assertEqual(len(_miss), 4)

    def test_05_god_file_blocked(self):
        """上帝文件只加严不放松。"""
        _ok, _why, _miss = PatchManager._m339_review_items_ok(
            {"items": {"evidence_tier": "A", "probe_out_of_graph": False,
                       "misjudge_sample": "z.py:3", "god_file": True}})
        self.assertFalse(_ok)
        self.assertIn("god_file", _miss)

    def test_06_non_o3_rejected_by_special_port(self):
        """普通（llm）条目走 O3 口必须被拒（llm 永不自动语义不动）。"""
        self.assertFalse(PatchManager._m339_is_o3(
            {"source": "llm", "issue_type": "silent_exception"}))
        self.assertTrue(PatchManager._m339_is_o3(
            {"source": "o3_open_gate", "issue_type": "dead_code_candidate"}))


class TestA2PendingGraded(unittest.TestCase):
    """A2：门槛 = risk低 + trust≥40 + blast≤1 + 已复核通过；O3/高风险强制人工。"""

    def setUp(self):
        self._orig_blast = d.blast_radius_of
        d._M363_PENDING_REJ.clear()

    def tearDown(self):
        d.blast_radius_of = self._orig_blast
        d._M363_PENDING_REJ.clear()

    def test_01_qualified_row_passes(self):
        d.blast_radius_of = lambda *a, **k: 0
        sel = d.select_pending_candidates([_row()], limit=1, pilot_only=True)
        self.assertEqual(len(sel), 1, "达标条目（白名单内）必须进候选")

    def test_02_o3_forced_manual(self):
        d.blast_radius_of = lambda *a, **k: 0
        sel = d.select_pending_candidates(
            [_row(source="o3_open_gate", issue_type="dead_code_candidate")],
            limit=1, pilot_only=True)
        self.assertEqual(sel, [], "O3 强制人工，不得进自动候选")
        self.assertGreaterEqual(d.get_m363_pending_rej().get("o3", 0), 1)

    def test_03_high_risk_rejected(self):
        d.blast_radius_of = lambda *a, **k: 0
        sel = d.select_pending_candidates([_row(risk_level="高")], limit=1)
        self.assertEqual(sel, [])

    def test_04_trust_below_threshold_rejected(self):
        d.blast_radius_of = lambda *a, **k: 0
        sel = d.select_pending_candidates([_row(trust_score=39.9)], limit=1)
        self.assertEqual(sel, [])
        self.assertGreaterEqual(d.get_m363_pending_rej().get("trust", 0), 1)

    def test_05_unverified_status_rejected(self):
        """未复核（status=pending）不进——「已复核通过」用 runtime_verified 实证。"""
        d.blast_radius_of = lambda *a, **k: 0
        sel = d.select_pending_candidates([_row(status="pending")], limit=1)
        self.assertEqual(sel, [])

    def test_06_blast_unknown_is_manual_not_zero(self):
        """★牵连不可判定（None）必须转人工，禁止当 0 放行。"""
        d.blast_radius_of = lambda *a, **k: None
        sel = d.select_pending_candidates([_row()], limit=1)
        self.assertEqual(sel, [])
        self.assertGreaterEqual(d.get_m363_pending_rej().get("blast_unknown", 0), 1)

    def test_07_blast_over_limit_rejected(self):
        d.blast_radius_of = lambda *a, **k: 2
        sel = d.select_pending_candidates([_row()], limit=1)
        self.assertEqual(sel, [])
        self.assertGreaterEqual(d.get_m363_pending_rej().get("blast_over", 0), 1)

    def test_08_pilot_whitelist_guard_default_on(self):
        """非白名单条目：守闸时（默认）不进候选；显式关闭才可进。"""
        d.blast_radius_of = lambda *a, **k: 0
        row = _row(method="some_other_method")
        self.assertEqual(d.select_pending_candidates([row], pilot_only=True), [])
        self.assertEqual(len(d.select_pending_candidates([row], pilot_only=False)), 1)

    def test_09_switch_defaults(self):
        self.assertTrue(d._m363_pending_graded_on())
        self.assertTrue(d._m363_pending_pilot_only(),
                        "默认不得绕过 306刀4 受控试点白名单")

    def test_10_dispatch_has_pending_fallback(self):
        """候选补充必须挂在 history 两级之后，且不改动 history 选择逻辑。"""
        src = open(os.path.join(_ROOT, "nucleus", "evolution",
                                "patch_execution_dispatch.py"), encoding="utf-8").read()
        self.assertIn('if not _selected and _m363_pending_graded_on():', src)
        self.assertIn('select_pending_candidates(', src)
        # auto_triage 仍是对 history 的第二级（未被替换）
        self.assertIn('_selected = select_for_auto_triage(_hist, limit=1)', src)


class TestA3ConsumeObservability(unittest.TestCase):
    """A3：只加日志与字段，不改控制流；读不出来是 None 不是 0。"""

    def test_01_heartbeat_exposes_three_fields(self):
        import nucleus.audit_problem_pool_consumer as c
        orig_pool, orig_fb = c.consume_pool, None
        try:
            c.consume_pool = lambda **kw: {"state": "ok", "consumed": 2,
                                           "claimed": 2, "open_before": 717}
            import nucleus.evolution.verify_feedback as vf
            orig_fb = vf.feedback_once
            vf.feedback_once = lambda **kw: {"state": "ok", "stats": {},
                                             "persisted": True, "verdict_mtime": 0}
            r = c.consume_heartbeat(project_root=_ROOT, enable=True)
        finally:
            c.consume_pool = orig_pool
            if orig_fb is not None:
                import nucleus.evolution.verify_feedback as vf
                vf.feedback_once = orig_fb
        self.assertIn("m363_consume_view", r)
        v = r["m363_consume_view"]
        self.assertEqual(v["consume_state"], "ok")
        self.assertEqual(v["candidate_count"], 717)
        self.assertEqual(v["dispatch_selected"], 2)

    def test_02_unavailable_candidate_count_is_none(self):
        """读不出来时必须是 None，不许伪装 0（铁律156）。"""
        import nucleus.audit_problem_pool_consumer as c
        orig = c.consume_pool
        try:
            c.consume_pool = lambda **kw: {"state": "unavailable", "consumed": 0,
                                           "open_before": None}
            r = c.consume_heartbeat(project_root=_ROOT, enable=True)
        finally:
            c.consume_pool = orig
        self.assertIsNone(r["m363_consume_view"]["candidate_count"])

    def test_03_main_uses_return_value(self):
        src = open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()
        self.assertIn("_m363_cv = consume_heartbeat(", src,
                      "main 必须接住返回值（原先丢弃 ⇒ 消费口在日志里不可见）")
        self.assertIn("[363A3] 消费口 consume_state=", src)

    def test_04_silent_branches_have_logs(self):
        """三个静默分支（disabled / empty / unavailable）必须有留痕。"""
        src = open(os.path.join(_ROOT, "nucleus", "audit_problem_pool_consumer.py"),
                   encoding="utf-8").read()
        self.assertIn("本拍未消费：灰度开关关闭", src)
        self.assertIn("本拍未消费：池快照为空", src)
        self.assertIn("本拍未消费：池读不出来", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
