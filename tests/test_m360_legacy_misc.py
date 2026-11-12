# -*- coding: utf-8 -*-
"""360 遗留闭合验收（刀14 tool 通道显性化 / 刀15 共振诊断 + 零吸收归因）。

冷档 gzip 部分见 ``tests/test_m360_legacy_gzip.py``。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.qica.QICA import QICA  # noqa: E402
from nucleus.synapsys.ResonanceEngine import ResonanceEngine  # noqa: E402
from nucleus.tooling_runner import ToolingRunner  # noqa: E402


def _mk_qica() -> QICA:
    """裸构造（不走 __init__ 的重依赖），只给本测试要用的字段。"""
    q = QICA.__new__(QICA)
    q._domain_knowledge = {}
    q._domain_knowledge_lock = threading.RLock()
    q._intent_to_method = {}
    q._node_pool = None
    q._knowledge_tree = None  # 493.17：路径探针需要（None → 走默认分支，如实记「未变」）
    # 493.17：absorb 落盘隔离到临时目录，避免污染生产 absorb_trace 真实规则账
    q._m49317_absorb_trace_path = os.path.join(
        tempfile.gettempdir(), "m360_legacy_absorb_trace_test.jsonl")
    q._m360_absorb_reasons = {}
    q._m360_absorb_last = {}
    q._log = lambda *a, **k: None
    return q


class TestM360LegacyToolChannel(unittest.TestCase):
    """刀14：compileall / mypy 这类「走正则」的通道，失败必须记账、不得与「零问题」同形。"""

    def test_01_exec_failure_is_recorded(self):
        r = ToolingRunner(".")
        r._run_cmd_split(["python_nonexistent_cmd_xyz_360", "--v"], tool="probe360")
        flag = r.m360_tool_flags().get("probe360")
        self.assertIsNotNone(flag, "执行失败必须写 _tool_flags（此前 compileall/mypy 无记账）")
        self.assertTrue(flag["degraded"])
        self.assertEqual(flag["reason"], "exec_failed")
        self.assertFalse(flag["timeout"])

    def test_02_timeout_is_separated_from_exec_error(self):
        r = ToolingRunner(".")
        r._run_cmd_split(["python", "-c", "import time; time.sleep(3)"],
                         timeout=1, tool="slow360")
        flag = r.m360_tool_flags().get("slow360")
        self.assertIsNotNone(flag)
        self.assertTrue(flag["timeout"], "超时必须单列（与 exec_failed 处置动作不同）")
        self.assertEqual(flag["reason"], "timeout")

    def test_03_success_writes_no_flag(self):
        r = ToolingRunner(".")
        r._run_cmd_split(["python", "-c", "print(1)"], tool="ok360")
        self.assertIsNone(r.m360_tool_flags().get("ok360"),
                          "成功执行不得记账（记账只代表通道不可用）")

    def test_04_run_cmd_keeps_failure_prefix_and_records(self):
        r = ToolingRunner(".")
        out = r._run_cmd(["python_nonexistent_cmd_xyz_360"], tool="wrap360")
        self.assertTrue(out.startswith("[工具执行失败]"), "失败串语义必须保持（旧调用方依赖）")
        self.assertTrue(r.m360_tool_flags().get("wrap360", {}).get("degraded"))

    def test_05_tool_json_keeps_timeout(self):
        r = ToolingRunner(".")
        # 真实形态：超时时 err 非空（_run_cmd_split 会填），_tool_json 判不可用；
        # 关键是 flag 上的 reason 必须是 timeout，而不是被一律归成 exec_failed。
        rec = {"cmd": [], "stdout": "", "stderr": "", "rc": None, "ok": False,
               "timeout": True, "err": "TimeoutExpired(180s)"}
        data = r._tool_json("ruff360", rec, ["x.py"], "explicit")
        self.assertIsNone(data, "执行失败必须判通道不可用（不得读成 0 条问题）")
        flag = r.m360_tool_flags()["ruff360"]
        self.assertTrue(flag["timeout"], "_tool_json 重建 flag 时不得丢掉 timeout")
        self.assertEqual(flag["reason"], "timeout", "超时不得被归成 exec_failed")


class TestM360LegacyResonanceDiag(unittest.TestCase):
    """刀15A：语义通道计数此前有自增、无出口 ⇒ 命中率类结论无法自证。"""

    def test_01_diag_exists_and_no_sample_is_none(self):
        e = ResonanceEngine()
        d = e.m360_channel_diag()
        self.assertIn("semantic", d)
        self.assertIsNone(d["semantic"]["hit_rate"],
                          "无样本时命中率必须是 None（不是 0）")

    def test_02_diag_rate_matches_candle_micro_number(self):
        """烛微口径 7/2000=0.35% 必须能被本出口复现（分子分母同源）。"""
        e = ResonanceEngine()
        e._total_queries = 2000
        e._semantic_calls = 7
        d = e.m360_channel_diag()
        self.assertAlmostEqual(d["semantic"]["hit_rate"], 0.0035, places=6)

    def test_03_get_stats_exports_semantic(self):
        e = ResonanceEngine()
        e._total_queries = 100
        e._semantic_calls = 25
        s = e.get_stats()
        self.assertEqual(s["semantic_calls"], 25)
        self.assertAlmostEqual(s["semantic_hit_rate"], 0.25, places=6)
        self.assertIn("semantic_fallbacks", s)

    def test_04_hint_on_disabled_channel(self):
        """通道没开必须被提示出来，避免把「未启用」误读成「效果差」。"""
        e = ResonanceEngine()
        d = e.m360_channel_diag()
        self.assertIsInstance(d["hints"], list)
        if not d["semantic"]["enabled"]:
            self.assertTrue(any("enable_semantic_kernel" in h for h in d["hints"]))


class TestM360LegacyAbsorbAttribution(unittest.TestCase):
    """刀15B：吸收 0 条必须有归因（此前只有 >0 才留痕）。"""

    def test_01_empty_rules_attributed(self):
        q = _mk_qica()
        self.assertEqual(q.absorb_growth_rules([]), 0)
        self.assertEqual(q.m360_absorb_stats()["reasons"].get("empty_rules"), 1)

    def test_02_unknown_type_attributed(self):
        q = _mk_qica()
        self.assertEqual(q.absorb_growth_rules([{"type": "whatever", "concept": "x"}]), 0)
        self.assertEqual(q.m360_absorb_stats()["reasons"].get("unknown_type"), 1)

    def test_03_missing_fields_attributed(self):
        q = _mk_qica()
        self.assertEqual(q.absorb_growth_rules(
            [{"type": "domain_knowledge", "concept": "脉冲引擎"}]), 0)
        self.assertEqual(q.m360_absorb_stats()["reasons"].get("missing_fields"), 1)

    def test_04_quality_gate_attributed(self):
        q = _mk_qica()
        self.assertEqual(q.absorb_growth_rules(
            [{"type": "domain_knowledge", "concept": "曈曈", "path": "/身份/自我"}]), 0)
        self.assertEqual(q.m360_absorb_stats()["reasons"].get("blocked_quality_gate"), 1)

    def test_05_normal_absorption_still_works(self):
        """已知正例：正常规则必须仍能被吸收（归因改造不得改变成功路径）。"""
        q = _mk_qica()
        n = q.absorb_growth_rules(
            [{"type": "domain_knowledge", "concept": "脉冲引擎", "path": "/技术/框架"}])
        self.assertEqual(n, 1)
        self.assertEqual(q._domain_knowledge.get("脉冲引擎"), "/技术/框架")


if __name__ == "__main__":
    unittest.main(verbosity=2)
