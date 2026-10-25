# -*- coding: utf-8 -*-
"""365 刀A3 验收：闭环第六段键断链修补。

覆盖：带键回写 FIXED／键缺失留痕计数（不计派单失败）／verdict 三路径各一例／
issue_type 归一生效（dead_code_candidate ↔ dead_code）／入池条目键互填。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.audit_problem_pool as pool  # noqa: E402
import nucleus.evolution.patch_execution_dispatch as dispatch  # noqa: E402
import nucleus.evolution.verify_feedback as vf  # noqa: E402
import nucleus.self_inspector as si  # noqa: E402


class TestA3CloseLoop(unittest.TestCase):
    def setUp(self):
        dispatch._M365_CLOSE_LOOP_MISS.clear()

    # ① 带键 ⇒ 回写池 FIXED（走既有状态机，不重建）
    def test_01_with_key_writes_fixed(self):
        with mock.patch.object(pool, "set_status",
                               mock.MagicMock(return_value={
                                   "state": "ok", "changed": True})) as _m:
            r = dispatch._m356_close_loop_fixed({"dedup_key": "abc123", "id": "p1"})
        self.assertEqual(r.get("state"), "ok")
        _m.assert_called_once()
        self.assertEqual(_m.call_args[0][0], "abc123")
        self.assertEqual(_m.call_args[0][1], pool.STATUS_FIXED)

    # ② 键缺失 ⇒ 留痕计数 + 返回 None，且**不**把派单判失败（不调 set_status）
    def test_02_missing_key_traced(self):
        with mock.patch.object(pool, "set_status",
                               lambda *a, **k: self.fail("键缺失不应回写")):
            r = dispatch._m356_close_loop_fixed({"id": "p2", "file": "a.py",
                                                 "method": "f"})
        self.assertIsNone(r)
        self.assertEqual(dispatch.get_m365_close_loop_stats().get("no_dedup_key"), 1)

    # ③ 三条映射路径各一例
    def test_03_map_paths(self):
        entries = [
            {"dedup_key": "k1", "patch_id": "pid1", "file": "a/b.py",
             "issue_type": "dead_code"},
            {"dedup_key": "k2", "patch_id": "pid2", "file": "c/d.py",
             "issue_type": "long_method"},
        ]
        r1 = vf.map_verdict_to_entry({"patch_id": "pool_k1"}, entries)
        self.assertEqual((r1["state"], r1["by"]), ("ok", "pool_prefix"))
        r2 = vf.map_verdict_to_entry({"patch_id": "pid2"}, entries)
        self.assertEqual((r2["state"], r2["by"]), ("ok", "entry_patch_id"))
        r3 = vf.map_verdict_to_entry({"patch_id": "unknown", "file": "c/d.py",
                                      "issue_type": "long_method"}, entries)
        self.assertEqual((r3["state"], r3["by"]), ("ok", "file_type"))
        self.assertEqual(r3["entry"]["dedup_key"], "k2")

    # ④ issue_type 归一：dead_code_candidate 与 dead_code 视为同族（唯一实证别名）
    def test_04_issue_type_norm(self):
        self.assertEqual(vf._m365_norm_issue_type("dead_code_candidate"), "dead_code")
        self.assertEqual(vf._m365_norm_issue_type("dead_code"), "dead_code")
        # 既有单一真源仍生效
        self.assertEqual(vf._m365_norm_issue_type("print_debug"), "print_instead_of_log")
        entries = [{"dedup_key": "k3", "file": "a/b.py", "issue_type": "dead_code"}]
        r = vf.map_verdict_to_entry({"patch_id": "zzz", "file": "a/b.py",
                                     "issue_type": "dead_code_candidate"}, entries)
        self.assertEqual((r["state"], r["by"]), ("ok", "file_type"))

    # ⑤ 多条同 file+type 仍 ambiguous（归一不放宽"不猜"）
    def test_05_ambiguous_still_ambiguous(self):
        entries = [{"dedup_key": "k4", "file": "a/b.py", "issue_type": "dead_code"},
                   {"dedup_key": "k5", "file": "a/b.py", "issue_type": "dead_code"}]
        r = vf.map_verdict_to_entry({"patch_id": "zzz", "file": "a/b.py",
                                     "issue_type": "dead_code_candidate"}, entries)
        self.assertEqual(r["state"], "ambiguous")

    # ⑥ 入池条目键互填（dedup_key 用池侧标准类型 dead_code 计算）
    def test_06_intake_entry_has_keys(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = os.path.join(tmpdir.name, "pending_patches.json")
        cand = {"file": "nucleus/demo_mod.py", "method": "foo", "line": 12,
                "evidence_tier": "hard_strict", "probe_hits": []}
        with mock.patch.object(si, "_m335_o3_intake_candidates",
                               lambda items=None, limit=1: {"candidates": [cand],
                                                            "rejected": {}, "pool": 1,
                                                            "pool_strict": 1}), \
             mock.patch.object(si, "_m333_intake_rate",
                               lambda project_root=None, now=None:
                               {"count": 0, "limit": 1}), \
             mock.patch.object(si, "_m339_intake_whitelisted", lambda f: True):
            r = dispatch._m335_o3_consume(
                now=1760000000.0, project_root=None, pending_path=path,
                gate_report={"official_verdict": "OPEN", "official_reason": "ok",
                             "sample": {"rows": []}})
        self.assertEqual(r.get("status"), "intake", r)
        with open(path, encoding="utf-8") as f:
            e = json.load(f)[0]
        self.assertEqual(e.get("dedup_key"),
                         pool.make_dedup_key("nucleus/demo_mod.py", "foo", "dead_code"))
        self.assertTrue(str(e.get("patch_id") or "").startswith("o3_"))


    # ⑦ unmap 原因细分（裁决落地·事项1）：同文件不同类型 vs 池里无此文件
    def test_07_unmap_reason_kind(self):
        entries = [{"dedup_key": "k9", "file": "a/b.py", "issue_type": "dead_code"}]
        r1 = vf.map_verdict_to_entry({"patch_id": "zzz", "file": "a/b.py",
                                      "issue_type": "silent_exception"}, entries)
        self.assertEqual(r1["state"], "unmap")
        self.assertEqual(r1.get("reason_kind"), "type_mismatch")
        self.assertEqual(r1.get("same_file_types"), ["dead_code"])
        r2 = vf.map_verdict_to_entry({"patch_id": "zzz", "file": "x/y.py",
                                      "issue_type": "silent_exception"}, entries)
        self.assertEqual(r2["state"], "unmap")
        self.assertEqual(r2.get("reason_kind"), "file_absent")

if __name__ == "__main__":
    unittest.main()
