# -*- coding: utf-8 -*-
"""371 刀4 · F1 `__main__` 自测块盘点并案定向单测（4 例，对应任务书 §五）。

① 扫描工具复跑成功，块数/高危/打印/无副作用四项与机读 JSON 一致
② 基线漂移对照表齐备（360 与 371 两组数值 + drift 判定）
③ 并发自检块在位性结论有 git 证据支撑（d63d1d69 删除行可检索）
④ 生产面零越界（organs/nucleus/main/config 零改动）
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SNAP = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "371",
                    "__main__自测块盘点并案_20261026.json")
SCAN = os.path.join(ROOT, "tools", "scan_main_blocks.py")
TARGET = os.path.join(ROOT, "base", "BasePulseOrgan.py")


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def _clean_env():
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "CODEBUDDY_TOOL_CALL_ID"):
        env.pop(k, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


class TestInventoryRerun(unittest.TestCase):

    def test_01_scan_rerun_matches_snapshot(self):
        """① 现场复跑扫描工具，四项计数必须与机读 JSON 一致。"""
        out = os.path.join(tempfile.mkdtemp(prefix="m371b4_"), "inv.md")
        p = subprocess.run([sys.executable, SCAN, "--out", out],
                           cwd=ROOT, env=_clean_env(), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, "扫描工具失败：%s" % (p.stderr or "")[-400:])
        self.assertTrue(os.path.exists(out), "未生成盘点件")
        md = _read(out)

        snap = json.loads(_read(SNAP))
        want = snap["rerun_371"]
        m_blocks = re.search(r"自测块总数：\*\*(\d+)\*\*", md)
        m_high = re.search(r"高危（[^）]*）：\*\*(\d+)\*\*", md)
        m_print = re.search(r"仅打印：\*\*(\d+)\*\*", md)
        m_none = re.search(r"无副作用：\*\*(\d+)\*\*", md)
        for name, m in (("blocks", m_blocks), ("high_risk", m_high),
                        ("print_only", m_print), ("no_side_effect", m_none)):
            self.assertIsNotNone(m, "盘点件缺字段：%s" % name)
        self.assertEqual(int(m_blocks.group(1)), want["blocks"])
        self.assertEqual(int(m_high.group(1)), want["high_risk"])
        self.assertEqual(int(m_print.group(1)), want["print_only"])
        self.assertEqual(int(m_none.group(1)), want["no_side_effect"])


class TestDriftTable(unittest.TestCase):

    def test_02_drift_all_zero_and_complete(self):
        """② 漂移对照：五项都出现，且 drift 判定为 0/零漂移。"""
        snap = json.loads(_read(SNAP))
        self.assertEqual(snap["baseline_360"], snap["rerun_371"], "360 与 371 两组数值应完全相同")
        drift = snap["drift"]
        for k in ("blocks", "high_risk", "print_only", "no_side_effect", "total_lines"):
            self.assertEqual(drift[k], 0, "漂移不为 0：%s" % k)
        self.assertIn("零漂移", drift["verdict"])


class TestConcurrentSelfcheckEvidence(unittest.TestCase):

    def test_03_git_evidence_present(self):
        """③ 在位性结论须有 git 证据：d63d1d69 删除行含并发自检；当前文件无 __main__。"""
        cur = _read(TARGET)
        self.assertNotIn('if __name__ == "__main__":', cur, "当前仍存在 __main__ 块")
        self.assertEqual(len(cur.splitlines()), 652, "行数与证据件记录不符（652）")

        p = subprocess.run(["git", "show", "d63d1d69", "--", "base/BasePulseOrgan.py"],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=_clean_env())
        self.assertEqual(p.returncode, 0, "取 git 证据失败")
        diff = p.stdout or ""
        for token in ('-if __name__ == "__main__":',
                      "并发安全验证",
                      "并发熔断验证"):
            self.assertIn(token, diff, "git 证据里找不到：%s" % token)

    def test_03b_scope_not_overclaimed(self):
        """③附：证据件必须明说「只核销块已不在生产文件」这一子项，不得声称验证能力已重建。"""
        snap = json.loads(_read(SNAP))
        verdict = snap["concurrent_selfcheck_inventory"]["verdict"]
        self.assertIn("static_verified", verdict)
        md = _read(os.path.join(os.path.dirname(SNAP), "__main__自测块盘点并案_20261026.md"))
        self.assertIn("没有做", md, "人读件必须写明「迁入 tests/ 保留验证价值」未做")
        self.assertIn("不在本刀结论内", md.replace("**", ""))


class TestNoProductionDiff(unittest.TestCase):

    def test_04_production_surface_clean(self):
        """④ 本刀零删除：生产面零改动。"""
        p = subprocess.run(
            ["git", "status", "--porcelain", "--", "organs", "nucleus", "main.py", "config.py"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=_clean_env())
        dirty = [ln for ln in (p.stdout or "").splitlines() if ln.strip()]
        self.assertEqual(dirty, [], "生产面出现改动（本刀不允许删除任何块）：%s" % dirty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
