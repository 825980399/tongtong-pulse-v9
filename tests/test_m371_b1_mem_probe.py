# -*- coding: utf-8 -*-
"""371 刀1 · N1 内存归属只读取证探针定向单测（4 例，对应任务书 §二「定向单测」）。

① 探针独立可跑、零生产 import 依赖
② 分器计数与机读 JSON 一致、脚本幂等复跑（同一 pid 连跑两次，结构字段恒等）
③ 生产面零 diff 校验（除只读工具件外，本刀不得改 organs/nucleus/main/config）
④ 探针异常分支不抛出到主流程（不存在的 pid / 不存在的快照 / 坏日志路径）
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TOOL = os.path.join(ROOT, "tools", "ci", "probe_memory_attribution_371.py")


def _load():
    spec = importlib.util.spec_from_file_location("m371_b1_probe", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load()


class TestProbeStandalone(unittest.TestCase):

    def test_01_no_production_import(self):
        """① 探针不得 import 任何生产模块（nucleus/ organs/ config/ main）。"""
        with open(TOOL, encoding="utf-8") as f:
            src = f.read()
        prod_imports = [
            ln.strip() for ln in src.split("\n")
            if re.match(r"^\s*(from|import)\s+", ln)
            and re.search(r"\b(nucleus|organs|config|main)\b", ln)
        ]
        self.assertEqual(prod_imports, [], "探针出现生产模块 import：%s" % prod_imports)
        # 且必须是标准库口径
        self.assertIn("import argparse", src)
        self.assertIn("import logging", src)

    def test_02_cli_runs_and_json_consistent(self):
        """② CLI 可跑，机读 JSON 与内部函数口径一致（同一 pid 连跑两次结构恒等）。"""
        out1 = os.path.join(tempfile.mkdtemp(prefix="m371b1_"), "a.json")
        out2 = os.path.join(tempfile.mkdtemp(prefix="m371b1_"), "b.json")
        env = dict(os.environ)
        for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "CODEBUDDY_TOOL_CALL_ID"):
            env.pop(k, None)
        env["PYTHONNOUSERSITE"] = "1"
        for out in (out1, out2):
            p = subprocess.run([sys.executable, TOOL, "--self", "--out-json", out],
                               cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, "探针退出码非 0：%s" % (p.stderr or "")[-400:])
            self.assertTrue(os.path.exists(out))
        a = json.load(open(out1, encoding="utf-8"))
        b = json.load(open(out2, encoding="utf-8"))
        # 幂等：去掉时间/波动字段后结构必须一致
        for d in (a, b):
            d.pop("generated_ts", None)
            d.get("inproc", {}).pop("ts", None)
            d.get("inproc", {}).pop("gc_count", None)
            d.get("inproc", {}).pop("gc_tracked_objects", None)
            d.get("inproc", {}).pop("top_types_by_count", None)
            d.get("inproc", {}).pop("top_types_by_bytes", None)
        self.assertEqual(sorted(a), sorted(b), "两次复跑顶层键不一致")
        self.assertEqual(a["probe_version"], M.PROBE_VERSION)
        # 与内部函数口径一致
        inproc = M.sample_inproc(top_n=3)
        self.assertEqual(inproc["mode"], "self")
        self.assertEqual(inproc["pid"], os.getpid())

    def test_03_production_surface_clean(self):
        """③ 生产面零 diff：本刀只加只读工具，不得改 organs/nucleus/main/config。"""
        env = dict(os.environ)
        for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
            env.pop(k, None)
        p = subprocess.run(
            ["git", "status", "--porcelain", "--", "organs", "nucleus", "main.py", "config.py"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        dirty = [ln for ln in (p.stdout or "").splitlines() if ln.strip()]
        self.assertEqual(dirty, [], "生产面出现改动（本刀不允许）：%s" % dirty)

    def test_04_error_branches_do_not_raise(self):
        """④ 异常分支不抛出：坏 pid / 坏快照 / 坏日志路径都要返回结构化 err。"""
        bad_pid = M.sample_outside(999_999_999)          # 不存在的进程
        self.assertFalse(bad_pid["access_ok"])
        self.assertIsInstance(bad_pid.get("err"), str)
        self.assertIsNone(bad_pid.get("rss_mb"), "取不到时必须 None，不得用 0 冒充")

        snap = M.read_health_snapshot(os.path.join(ROOT, "no_such_snapshot_371.json"))
        self.assertIn("err", snap)

        rows = M.read_liver_rows(os.path.join(ROOT, "no_such_log_371.log"))
        self.assertEqual(rows, [])

        # 空/坏 JSON 快照也不得抛
        tmp = tempfile.mkdtemp(prefix="m371b1_s_")
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIn("err", M.read_health_snapshot(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
