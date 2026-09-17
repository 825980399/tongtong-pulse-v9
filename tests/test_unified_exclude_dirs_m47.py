# -*- coding: utf-8 -*-
"""第47批 T4 门控测试：统一排除目录列表（P2-310）

★核心目标：**防止「新建副本/归档/临时目录被全库扫描计入」的事故复发**。

第46批连续三次踩中：
  1. ``.tmp_backup/`` → 死 noqa 误报 19 处
  2. ``.pytest_tmp/`` → 同类风险
  3. ``.release-tmp/`` → Event 零引用 24→0，**审计彻底失效**

根因：每个测试各自维护排除列表，新增目录必然遗漏。

本测试确保：
  * 统一列表 ``nucleus/data/exclude_dirs.py`` 覆盖全部副本/归档/临时目录
  * 全库扫描类测试/工具**引用**该列表（而非各自硬编码）
  * Event 常量零引用 = 24 且不回退
  * ``iter_python_files`` 不会走进被排除目录
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.data import exclude_dirs as _ed  # noqa: E402

#: 必须被排除的目录（第46批三次事故的主角 + 既有同类）
MUST_EXCLUDE = (
    ".release-tmp",      # 源码副本 —— 零引用审计失效的元凶
    ".tmp_backup",       # 第46批 tmp 快照（旧落点）
    ".bak_tmp",          # 第47批 tmp 快照（新落点）
    ".pytest_tmp",       # 测试隔离目录
    "code_backups",
    "__pycache__", ".git", ".pytest_cache", ".ruff_cache",
    "node_modules", ".venv", "venv",
)


# ==================== 列表完整性 ====================

class TestListCompleteness(unittest.TestCase):
    def test_01_all_must_exclude_covered(self):
        for _d in MUST_EXCLUDE:
            self.assertTrue(_ed.is_excluded(_d),
                            "★未排除: %s（会导致全库扫描误计）" % _d)

    def test_02_bak_prefix_rule(self):
        """.bak* 前缀（.bak_batch44 / .bak_batch47 ...）一律排除。"""
        for _d in (".bak_batch44", ".bak_batch47", ".bak_anything"):
            self.assertTrue(_ed.is_excluded(_d))

    def test_03_normal_dirs_not_excluded(self):
        """正常源码目录**不得**被误排除。"""
        for _d in ("nucleus", "organs", "tools", "tests", "functions"):
            self.assertFalse(_ed.is_excluded(_d))

    def test_04_data_dirs_toggle(self):
        self.assertTrue(_ed.is_excluded("data"))
        self.assertTrue(_ed.is_excluded("logs"))
        self.assertTrue(_ed.is_excluded("tmp"))
        # include_data=False 时不排除（某些工具需要扫 data）
        self.assertFalse(_ed.is_excluded("data", include_data=False))

    def test_05_prune_helper(self):
        _in = ["nucleus", "__pycache__", ".git", ".release-tmp", "tools"]
        self.assertEqual(_ed.prune(_in), ["nucleus", "tools"])

    def test_06_describe_shape(self):
        _d = _ed.describe()
        for _k in ("vcs", "cache", "copy", "data", "backup_prefixes"):
            self.assertIn(_k, _d)
        self.assertIn(".release-tmp", _d["copy"])


# ==================== iter_python_files 行为 ====================

class TestIterPythonFiles(unittest.TestCase):
    def setUp(self):
        self._sand = tempfile.mkdtemp(prefix="m47exc_")
        for _sub in ("src", ".release-tmp", ".bak_tmp", "__pycache__",
                     ".pytest_tmp"):
            _d = os.path.join(self._sand, _sub)
            os.makedirs(_d, exist_ok=True)
            with io.open(os.path.join(_d, "a.py"), "w", encoding="utf-8") as _f:
                _f.write("# %s\n" % _sub)

    def tearDown(self):
        shutil.rmtree(self._sand, ignore_errors=True)

    def test_10_only_source_dir(self):
        _files = list(_ed.iter_python_files(self._sand))
        _dirs = {os.path.basename(os.path.dirname(f)) for f in _files}
        self.assertEqual(_dirs, {"src"})

    def test_11_no_copy_dirs(self):
        _files = list(_ed.iter_python_files(self._sand))
        for _f in _files:
            for _bad in (".release-tmp", ".bak_tmp", "__pycache__",
                         ".pytest_tmp"):
                self.assertNotIn(_bad, _f)

    def test_12_extra_skip(self):
        _files = list(_ed.iter_python_files(self._sand, extra_skip={"src"}))
        self.assertEqual(_files, [])


# ==================== 全库扫描类测试/工具已接入 ====================

class TestAdoption(unittest.TestCase):
    def _read(self, rel):
        _p = os.path.join(_ROOT, rel)
        if not os.path.isfile(_p):
            return None
        return io.open(_p, encoding="utf-8", errors="replace").read()

    def test_20_entry_probe_m35_uses_unified(self):
        _t = self._read(os.path.join("tests", "test_entry_probe_m35.py"))
        if _t is None:
            self.skipTest("文件不存在")
        self.assertIn("exclude_dirs", _t,
                      "test_entry_probe_m35 仍用本地硬编码排除列表")

    def test_21_scan_orphan_uses_unified(self):
        _t = self._read(os.path.join("tmp", "scan_orphan_event_m10.py"))
        if _t is None:
            self.skipTest("文件不存在")
        self.assertIn("exclude_dirs", _t,
                      "scan_orphan_event_m10 仍用本地硬编码排除列表")

    def test_22_scan_orphan_fallback_safe(self):
        """即便统一列表不可导入，也必须有 fallback 含 .release-tmp。"""
        _t = self._read(os.path.join("tmp", "scan_orphan_event_m10.py"))
        if _t is None:
            self.skipTest("文件不存在")
        self.assertIn(".release-tmp", _t)

    def test_23_no_hardcoded_tmp_backup_in_m35(self):
        """m35 不应再硬编码 .tmp_backup（应由统一列表覆盖）。"""
        _t = self._read(os.path.join("tests", "test_entry_probe_m35.py"))
        if _t is None:
            self.skipTest("文件不存在")
        # 允许出现在注释里，但不应出现在排除判断的元组中
        _bad = re.search(r'\(\s*"tmp".*?\.tmp_backup.*?\)', _t, re.S)
        self.assertIsNone(_bad, "m35 仍有硬编码的 .tmp_backup 排除元组")


# ==================== Event 零引用 = 24（不回退） ====================

class TestEventOrphanAudit(unittest.TestCase):
    """★第46批修复的核心指标：Event 常量零引用必须保持 24。"""

    def test_30_report_says_24(self):
        _p = os.path.join(_ROOT, "tmp", "orphan_event_m10.json")
        if not os.path.isfile(_p):
            self.skipTest("审计报告不存在（需先跑 scan_orphan_event_m10.py）")
        _d = json.load(io.open(_p, encoding="utf-8"))
        _all = _d.get("all", [])
        if not _all:
            self.skipTest("报告为空")
        _zero = sum(1 for r in _all if r.get("zero_ref"))
        self.assertEqual(_zero, 24,
                         "★零引用数回退：期望 24，实得 %d（副本目录可能又被计入）"
                         % _zero)

    def test_31_summary_says_24(self):
        _py = os.path.join(_ROOT, "tmp", "scan_summarize_orphan_m10.py")
        if not os.path.isfile(_py):
            self.skipTest("汇总脚本不存在")
        _r = subprocess.run([sys.executable, _py], cwd=_ROOT,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=300)
        _out = (_r.stdout or "") + (_r.stderr or "")
        self.assertIn("零引用候选: 24", _out,
                      "★零引用数回退。实际输出: %s" % _out[:200])


# ==================== 防复发：新增目录的兜底 ====================

class TestFutureProof(unittest.TestCase):
    def test_40_scan_project_root_for_unknown_copy_dirs(self):
        """扫描项目根：若出现疑似副本目录却未被排除 → 提前告警。"""
        _suspect = []
        for _n in os.listdir(_ROOT):
            _p = os.path.join(_ROOT, _n)
            if not os.path.isdir(_p) or not _n.startswith("."):
                continue
            if _ed.is_excluded(_n):
                continue
            # 疑似副本：含大量 .py 且与源码目录同名
            try:
                _sub = set(os.listdir(_p))
            except OSError:
                continue
            if {"nucleus", "organs"} & _sub:
                _suspect.append(_n)
        self.assertEqual(_suspect, [],
                         "发现疑似源码副本目录但未排除: %s" % _suspect)


if __name__ == "__main__":
    unittest.main(verbosity=2)
