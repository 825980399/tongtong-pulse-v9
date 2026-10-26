# -*- coding: utf-8 -*-
"""371 刀3 · 破零三闸现状核查定向单测（4 例，对应任务书 §四「定向单测」）。

① 三闸锚点仍在（函数/常量/读侧入口存在且签名未变）——对照表的锚点可复现
② 机读 JSON 与对照表读数一致（关键值逐项对表）
③ 生产面零 diff（本刀只读）
④ 候选子刀清单风险分级字段齐备（风险/停机/动什么 不缺项）
"""
import ast
import io
import json
import os
import re
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SNAP = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "371",
                    "破零三闸现状核查对照表_20261026.json")
MD = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "371",
                  "破零三闸现状核查对照表_20261026.md")


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


class TestGateAnchors(unittest.TestCase):

    def test_01_anchors_present(self):
        """① 三闸锚点：闸①/闸② 函数、闸① 两常量、闸③ 三函数与读侧入口均在。"""
        si = _read(os.path.join(ROOT, "nucleus", "self_inspector.py"))
        self.assertIn("def _m340_judge_action_open(", si, "闸① 函数缺失")
        self.assertIn("def o3_open_gate_report(", si, "闸② 函数缺失")
        self.assertRegex(si, r"_O3_MISJUDGE_LINE\s*=\s*0\.10", "闸① 阈值常量漂移")
        self.assertRegex(si, r"_M340_JUDGE_ACTION_MIN_TICKS\s*=\s*3", "闸① 连续 tick 常量漂移")

        lg = _read(os.path.join(ROOT, "nucleus", "mnemosyne", "live_gate.py"))
        for fn in ("_cond_recall", "_cond_cold", "live_gate_ok"):
            self.assertIn("def %s(" % fn, lg, "闸③ %s 缺失" % fn)

        pmc = _read(os.path.join(ROOT, "organs", "core", "PulseMetricsCollector.py"))
        self.assertIn("live_gate_ok(", pmc, "闸③ 读侧调用点缺失")
        self.assertIn("m350_reload_stats_path", pmc, "闸③ 读侧取路径入口缺失")

        pool = _read(os.path.join(ROOT, "nucleus", "mnemosyne", "PulseNodePool.py"))
        for fn in ("_m349_reload_stats_path", "_m349_persist_reload_stats", "reload_hit_rate"):
            self.assertIn("def %s(" % fn, pool, "闸③ 写侧 %s 缺失" % fn)


class TestSnapshotConsistency(unittest.TestCase):

    def setUp(self):
        self.snap = json.loads(_read(SNAP))
        self.md = _read(MD)

    def test_02_json_fields_and_md_agree(self):
        """② 机读 JSON 字段齐备，且关键读数能在人读件里对上。"""
        g = self.snap["gates"]
        self.assertEqual(set(g), {"gate1_action_surface", "gate2_o3_open", "gate3_reload_chain"})

        g1 = g["gate1_action_surface"]
        self.assertFalse(g1["reading"]["judge_action_open"])
        self.assertEqual(g1["reading"]["verdict"], "CLOSED")
        self.assertIn("三取一", g1["finding_note"].replace("「", "").replace("」", ""))

        g2 = g["gate2_o3_open"]["reading"]
        self.assertEqual(g2["official"], "NOT_RUN")
        self.assertEqual(g2["hard_total"], 357)
        self.assertEqual(g2["target"], 100)
        self.assertFalse(g2["capped"])

        g3 = g["gate3_reload_chain"]
        self.assertIsNone(g3["reading"]["reload"])
        self.assertEqual(g3["reading"]["cold_index"], 5531)
        self.assertEqual(g3["break_location"], "write_side_never_persisted")

        # 人读件必须写出同样的关键值（防两处分叉）
        for token in ("NOT_RUN", "357", "644", "5531", "recall_hit_rate_ge_95",
                      "1738.7", "reload_stats.json"):
            self.assertIn(token, self.md, "人读件缺关键读数：%s" % token)

    def test_02b_reload_stats_absent_is_real(self):
        """②附：断点结论必须与磁盘现状一致（data/ 下确实没有 reload_stats.json）。"""
        hits = []
        for dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, "data")):
            for fn in filenames:
                if fn == "reload_stats.json":
                    hits.append(os.path.join(dirpath, fn))
        self.assertEqual(hits, [],
                         "data/ 下出现了 reload_stats.json ⇒ 断点结论已过期，需重核：%s" % hits)


class TestNoProductionDiff(unittest.TestCase):

    def test_03_production_surface_clean(self):
        """③ 本刀只读：organs/nucleus/main.py/config.py 零改动。"""
        env = dict(os.environ)
        for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
            env.pop(k, None)
        p = subprocess.run(
            ["git", "status", "--porcelain", "--", "organs", "nucleus", "main.py", "config.py"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        dirty = [ln for ln in (p.stdout or "").splitlines() if ln.strip()]
        self.assertEqual(dirty, [], "生产面出现改动（本刀不允许）：%s" % dirty)


class TestSubknifeList(unittest.TestCase):

    def test_04_candidates_have_risk_fields(self):
        """④ 候选子刀清单：每条必须有 id/title/surface/risk/needs_downtime/touches。"""
        snap = json.loads(_read(SNAP))
        cands = snap["subknife_candidates"]
        self.assertGreaterEqual(len(cands), 3)
        need = {"id", "title", "surface", "risk", "needs_downtime", "touches"}
        for c in cands:
            self.assertTrue(need.issubset(set(c)), "候选 %s 缺字段 %s" % (c.get("id"), sorted(need - set(c))))
            self.assertIn(c["risk"], {"低", "中", "中高", "高"}, "风险分级取值非法：%s" % c["risk"])
            self.assertIsInstance(c["needs_downtime"], bool)
        self.assertTrue(snap["explicitly_not_doing"], "必须显式列出「明确不做」项")


if __name__ == "__main__":
    unittest.main(verbosity=2)
