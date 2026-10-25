# -*- coding: utf-8 -*-
"""365 刀A1 验收：O3 入池四件套字段透传（只落字段，门槛一字不改）。

覆盖：四件套齐备放行／无抽验样本 ⇒ misjudge_sample 如实 None 且专用口拒（缺项拒）／
god_file=True 不入池／probe_hits 空与非空各一例。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.evolution.patch_execution_dispatch as dispatch  # noqa: E402
import nucleus.self_inspector as si  # noqa: E402
from nucleus.reasoning.PatchManager import PatchManager  # noqa: E402


def _gate(rows=None):
    return {"official_verdict": "OPEN", "official_reason": "gate open",
            "sample": {"rows": list(rows or [])}}


def _cand(file="nucleus/demo_mod.py", method="foo", line=12, hits=None):
    return {"file": file, "method": method, "line": line,
            "evidence_tier": "hard_strict",
            "probe_hits": list(hits if hits is not None else [])}


class TestA1O3ReviewItems(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pending = os.path.join(self._tmp.name, "pending_patches.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _consume(self, cand, gate):
        with mock.patch.object(si, "_m335_o3_intake_candidates",
                               lambda items=None, limit=1: {"candidates": [cand],
                                                            "rejected": {}, "pool": 1,
                                                            "pool_strict": 1}), \
             mock.patch.object(si, "_m333_intake_rate",
                               lambda project_root=None, now=None:
                               {"count": 0, "limit": 1}), \
             mock.patch.object(si, "_m339_intake_whitelisted", lambda f: True):
            return dispatch._m335_o3_consume(now=1760000000.0, project_root=None,
                                             pending_path=self.pending,
                                             gate_report=gate)

    def _rows(self):
        if not os.path.exists(self.pending):
            return []
        with open(self.pending, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []

    # ① 四件套齐备 ⇒ 放行，且专用口预检通过
    def test_01_all_four_items_pass(self):
        cand = _cand()
        gate = _gate([{"file": "nucleus/demo_mod.py", "method": "foo", "line": 12,
                       "probe_hits": [], "judgement": "维持判死（图外亦无证据）"}])
        r = self._consume(cand, gate)
        self.assertEqual(r.get("status"), "intake", r)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        e = rows[0]
        self.assertIsInstance(e.get("probe_out_of_graph"), dict)
        self.assertEqual(e.get("misjudge_sample"), ["nucleus/demo_mod.py:12"])
        self.assertIs(e.get("god_file"), False)
        ok, why, miss = PatchManager._m339_review_items_ok(
            {"items": {k: e.get(k) for k in PatchManager._M339_REVIEW_ITEMS}})
        self.assertTrue(ok, "%s missing=%s" % (why, miss))

    # ② 无抽验样本 ⇒ misjudge_sample 如实 None，专用口按缺项拒（不编造、不填默认）
    def test_02_no_sample_is_none_and_gate_rejects(self):
        cand = _cand(method="bar", line=33)
        r = self._consume(cand, _gate([]))
        self.assertEqual(r.get("status"), "intake", r)
        e = self._rows()[0]
        self.assertIsNone(e.get("misjudge_sample"))
        ok, _why, miss = PatchManager._m339_review_items_ok(
            {"items": {k: e.get(k) for k in PatchManager._M339_REVIEW_ITEMS}})
        self.assertFalse(ok)
        self.assertEqual(miss, ["misjudge_sample"])

    # ③ god_file=True ⇒ 不入池（只加严）
    def test_03_god_file_blocked(self):
        cand = _cand(file="organs/brain/PulseInnerWorld.py", method="baz")
        r = self._consume(cand, _gate([]))
        self.assertEqual(r.get("status"), "no_candidate", r)
        self.assertEqual(r.get("blocked_at"), "god_file")
        self.assertEqual(self._rows(), [])

    # ④ probe_hits 空 → 四类计数全 0；非空 → 逐类计数（归一，不新造判据）
    def test_04_probe_hits_empty_vs_nonempty(self):
        empty = dispatch._m365_o3_review_items(_cand(hits=[]), _gate([]))
        self.assertEqual(empty.get("probe_out_of_graph"),
                         {"string_ref": 0, "protocol_name": 0,
                          "test_ref": 0, "attr_bound": 0})
        full = dispatch._m365_o3_review_items(
            _cand(hits=["string_ref", "test_ref", "string_ref"]), _gate([]))
        self.assertEqual(full.get("probe_out_of_graph"),
                         {"string_ref": 2, "protocol_name": 0,
                          "test_ref": 1, "attr_bound": 0})
        # 未知 kind 不入表（不虚增四类之外的键）
        other = dispatch._m365_o3_review_items(_cand(hits=["no_name"]), _gate([]))
        self.assertEqual(sorted(other.get("probe_out_of_graph")),
                         ["attr_bound", "protocol_name", "string_ref", "test_ref"])

    # ⑤ 四件套字段落在条目顶层（专用口只认条目字段）
    def test_05_fields_at_top_level(self):
        self._consume(_cand(), _gate([]))
        e = self._rows()[0]
        for k in ("evidence_tier", "probe_out_of_graph", "misjudge_sample", "god_file"):
            self.assertIn(k, e)


if __name__ == "__main__":
    unittest.main()
