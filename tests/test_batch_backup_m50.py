# -*- coding: utf-8 -*-
"""第50批 T4 门控测试：P2-334 备份脚本 SKIP_DIRS 修复

核心缺陷：裸目录名匹配 → `nucleus/data/` 与 `tmp/` 被误跳过 → 无基线。
本测试同时守「判据正确」与「端到端真的备份到」。
"""
import io
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools import batch_backup as _bb  # noqa: E402


# ============================================================ 判据
class TestShouldSkipDir(unittest.TestCase):
    def test_01_root_data_and_logs_skipped(self):
        self.assertTrue(_bb.should_skip_dir("data"))
        self.assertTrue(_bb.should_skip_dir("data/evolution"))
        self.assertTrue(_bb.should_skip_dir("logs"))

    def test_02_nested_data_not_skipped(self):
        """★核心：`nucleus/data/` 不得被跳过（P2-334 缺陷本体）。"""
        self.assertFalse(_bb.should_skip_dir("nucleus/data"))
        self.assertFalse(_bb.should_skip_dir("nucleus/data/sub"))
        self.assertFalse(_bb.should_skip_dir("organs/data"))

    def test_03_root_tmp_not_skipped(self):
        """★核心：根层 `tmp/` 纳入备份（保住 test_isolation.py）。"""
        self.assertFalse(_bb.should_skip_dir("tmp"))
        self.assertFalse(_bb.should_skip_dir("tmp/sub"))

    def test_04_nested_tmp_not_skipped(self):
        self.assertFalse(_bb.should_skip_dir("nucleus/data/tmp"))
        self.assertFalse(_bb.should_skip_dir("organs/x/tmp"))

    def test_05_cache_dirs_skipped_any_depth(self):
        for d in ("__pycache__", "nucleus/__pycache__",
                  "nucleus/mnemosyne/__pycache__", ".pytest_cache",
                  "node_modules", ".venv", "venv", "htmlcov"):
            self.assertTrue(_bb.should_skip_dir(d), d)

    def test_06_backup_dirs_skipped(self):
        for d in (".bak_batch49", ".bak_tmp", ".tmp_backup", ".pytest_tmp",
                  ".release-tmp", "code_backups", "nucleus/data/.bak_x"):
            self.assertTrue(_bb.should_skip_dir(d), d)

    def test_07_vcs_skipped(self):
        self.assertTrue(_bb.should_skip_dir(".git"))
        self.assertTrue(_bb.should_skip_dir("sub/.git"))

    def test_08_root_itself_not_skipped(self):
        self.assertFalse(_bb.should_skip_dir(""))
        self.assertFalse(_bb.should_skip_dir("."))
        self.assertFalse(_bb.should_skip_dir(None))

    def test_09_windows_separator_normalized(self):
        self.assertTrue(_bb.should_skip_dir("data\\evolution"))
        self.assertFalse(_bb.should_skip_dir("nucleus\\data"))

    def test_10_leading_trailing_slash_tolerated(self):
        self.assertTrue(_bb.should_skip_dir("/data/"))
        self.assertFalse(_bb.should_skip_dir("/nucleus/data/"))


# ============================================================ 防回归
class TestAntiRegression(unittest.TestCase):
    def test_20_bare_data_tmp_absent_from_name_set(self):
        """★防回归：`data` / `tmp` 不得回到「裸目录名」集合里。"""
        self.assertNotIn("data", _bb.SKIP_DIR_NAMES)
        self.assertNotIn("tmp", _bb.SKIP_DIR_NAMES)
        self.assertNotIn("logs", _bb.SKIP_DIR_NAMES)

    def test_21_path_prefixes_are_paths_not_names(self):
        self.assertTrue(all(p.endswith("/") for p in _bb.SKIP_PATH_PREFIXES))
        for p in _bb.SKIP_PATH_PREFIXES:
            self.assertNotIn(p, _bb.SKIP_DIR_NAMES)


# ============================================================ 端到端（合成树）
class TestIterPyFilesSynthetic(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m50_t4_")
        # 应当被备份
        for rel in ("nucleus/data/exclude_dirs.py", "nucleus/data/path_utils.py",
                    "tmp/test_isolation.py", "tools/a.py", "config.py",
                    "nucleus/mnemosyne/pool.py"):
            p = os.path.join(self.d, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            io.open(p, "w", encoding="utf-8").write("# {}\n".format(rel))
        # 应当被跳过
        for rel in ("data/x.py", "logs/y.py", "__pycache__/z.py",
                    "nucleus/__pycache__/w.py", ".bak_batch49/old.py",
                    ".pytest_tmp/q.py", "node_modules/n.py"):
            p = os.path.join(self.d, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            io.open(p, "w", encoding="utf-8").write("# skip\n")
        # 非 .py 不参与
        p = os.path.join(self.d, "tmp", "note.txt")
        io.open(p, "w", encoding="utf-8").write("x")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_30_included_and_excluded(self):
        got = sorted(_bb.iter_py_files(self.d))
        print("     实际:", got)
        for want in ("nucleus/data/exclude_dirs.py", "nucleus/data/path_utils.py",
                     "tmp/test_isolation.py", "tools/a.py", "config.py"):
            self.assertIn(want, got, "应备份但缺失: {}".format(want))
        for bad in ("data/x.py", "logs/y.py", "__pycache__/z.py",
                    "nucleus/__pycache__/w.py", ".bak_batch49/old.py",
                    ".pytest_tmp/q.py", "node_modules/n.py", "tmp/note.txt"):
            self.assertNotIn(bad, got, "不应备份但出现了: {}".format(bad))

    def test_31_backup_writes_relative_structure(self):
        r = _bb.backup(99, root=self.d)
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["nucleus_data_included"], 2)
        self.assertEqual(r["tmp_included"], 1)
        dst = os.path.join(self.d, ".bak_batch99")
        for rel in ("nucleus/data/exclude_dirs.py", "tmp/test_isolation.py",
                    "tools/a.py", "config.py"):
            self.assertTrue(os.path.isfile(os.path.join(dst, rel.replace("/", os.sep))),
                            "备份缺失: {}".format(rel))
        # 内容一致（可回滚）
        a = io.open(os.path.join(self.d, "tmp/test_isolation.py"), encoding="utf-8").read()
        b = io.open(os.path.join(dst, "tmp", "test_isolation.py"), encoding="utf-8").read()
        self.assertEqual(a, b)
        shutil.rmtree(dst, ignore_errors=True)

    def test_32_dry_run_writes_nothing(self):
        r = _bb.backup(98, root=self.d, dry_run=True)
        self.assertGreater(r["copied"], 0)
        self.assertFalse(os.path.isdir(os.path.join(self.d, ".bak_batch98")))

    def test_33_plan_is_read_only(self):
        before = set(os.listdir(self.d))
        p = _bb.plan(self.d)
        self.assertGreaterEqual(p["count"], 6)
        self.assertEqual(set(os.listdir(self.d)), before)


# ============================================================ 真实仓库自检
class TestRealRepo(unittest.TestCase):
    def test_40_real_plan_includes_p234_targets(self):
        """★在真实仓库上验证 P2-334 修复生效。"""
        p = _bb.plan(_ROOT)
        self.assertGreater(p["skipped_nucleus_data"], 0,
                           "nucleus/data/ 下应有 .py 被纳入备份")
        self.assertGreater(p["skipped_root_tmp"], 0,
                           "tmp/ 下应有 .py 被纳入备份")
        self.assertTrue(any(f == "nucleus/data/exclude_dirs.py"
                            for f in p["files"]))

    def test_41_docstring_documents_p234(self):
        src = io.open(os.path.join(_ROOT, "tools/batch_backup.py"),
                      encoding="utf-8", errors="replace").read()
        self.assertIn("P2-334", src)
        self.assertIn("SKIP_PATH_PREFIXES", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
