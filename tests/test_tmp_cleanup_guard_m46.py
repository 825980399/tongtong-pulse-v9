# -*- coding: utf-8 -*-
"""第46批 T4 门控测试：tmp 清理判据增强（P2-307）

★动机（第45批真实回归）：
  tmp 清理时误删 3 个**被测试依赖**的文件，导致 2 个既有测试失败：
    * ``tmp/patch_t2_m9.py``              —— 被 ``test_except_refine_m9.py``  **import**
    * ``tmp/scan_orphan_event_m10.py``    —— 被 ``test_orphan_event_audit_m10.py`` **subprocess 执行**
    * ``tmp/scan_summarize_orphan_m10.py``—— 同上
  根因：清理判据**未扫描"tmp 是否被测试依赖"**。

本测试确保 ``tools/tmp_backup.py::scan_test_dependencies`` 能识别三类依赖，
且这些文件在清理时被自动排除。

★测试隔离：使用独立沙箱构造 fake tests/tmp，**不依赖生产 tmp 的实时内容**。
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

from tools import tmp_backup as _tb  # noqa: E402

#: 第45批被误删的 3 个文件（真实存在，是判据的硬底线）
CRITICAL = ("patch_t2_m9.py", "scan_orphan_event_m10.py",
            "scan_summarize_orphan_m10.py")


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self._sand = tempfile.mkdtemp(prefix="m46guard_")
        self.tests = os.path.join(self._sand, "tests")
        self.tmp = os.path.join(self._sand, "tmp")
        os.makedirs(self.tests, exist_ok=True)
        os.makedirs(self.tmp, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._sand, ignore_errors=True)

    def _tmpfile(self, name, content="# stub\n"):
        with io.open(os.path.join(self.tmp, name), "w", encoding="utf-8") as _f:
            _f.write(content)

    def _testfile(self, name, content):
        with io.open(os.path.join(self.tests, name), "w", encoding="utf-8") as _f:
            _f.write(content)


# ==================== 三类依赖识别 ====================

class TestDependencyDetection(_Sandbox):
    def test_01_import_dependency_detected(self):
        self._tmpfile("patch_zz_m99.py")
        self._testfile(
            "test_zz_m99.py",
            'import os, sys\n'
            '_TMP = os.path.join("x", "tmp")\n'
            'sys.path.insert(0, _TMP)\n'
            'import patch_zz_m99\n')
        _r = _tb.scan_test_dependencies(self.tests, self.tmp)
        self.assertIn("patch_zz_m99.py", _r["protected"])

    def test_02_subprocess_dependency_detected(self):
        self._tmpfile("scan_zz_m99.py")
        self._testfile(
            "test_zz_m99.py",
            'import subprocess, os\n'
            'subprocess.run(["python", os.path.join("tmp", "scan_zz_m99.py")])\n')
        _r = _tb.scan_test_dependencies(self.tests, self.tmp)
        self.assertIn("scan_zz_m99.py", _r["protected"])

    def test_03_open_dependency_detected(self):
        self._tmpfile("check_zz_m99.py")
        self._testfile(
            "test_zz_m99.py",
            'import io\n'
            'io.open("tmp/check_zz_m99.py", encoding="utf-8").read()\n')
        _r = _tb.scan_test_dependencies(self.tests, self.tmp)
        self.assertIn("check_zz_m99.py", _r["protected"])

    def test_04_non_tmp_py_not_flagged(self):
        """tmp 里不存在的文件不应被列为受保护。"""
        self._testfile("test_zz_m99.py",
                       'import subprocess\n'
                       'subprocess.run(["python", "tmp/nonexistent_zz.py"])\n')
        _r = _tb.scan_test_dependencies(self.tests, self.tmp)
        self.assertNotIn("nonexistent_zz.py", _r["protected"])

    def test_05_project_module_not_flagged(self):
        """与项目内模块同名的文件不应被误判（避免把真模块当 tmp 脚本）。"""
        self._tmpfile("config.py")
        self._testfile("test_zz_m99.py",
                       'import subprocess\n'
                       'subprocess.run(["python", "tmp/config.py"])\n')
        _r = _tb.scan_test_dependencies(self.tests, self.tmp)
        self.assertNotIn("config.py", _r["protected"])

    def test_06_empty_sandbox_no_protected(self):
        _r = _tb.scan_test_dependencies(self.tests, self.tmp)
        self.assertEqual(_r["protected"], [])

    def test_07_report_shape(self):
        _r = _tb.scan_test_dependencies(self.tests, self.tmp)
        for _k in ("by_import", "by_subprocess", "by_open", "protected",
                   "protected_count"):
            self.assertIn(_k, _r)

    def test_08_is_protected_helper(self):
        self._tmpfile("verify_zz_m99.py")
        self._testfile("test_zz_m99.py",
                       'import subprocess, os\n'
                       'subprocess.run(["python", os.path.join("tmp", "verify_zz_m99.py")])\n')
        self.assertTrue(_tb.is_protected("verify_zz_m99.py",
                                         _tb.scan_test_dependencies(self.tests, self.tmp)))
        self.assertFalse(_tb.is_protected("totally_unrelated_m99.py",
                                          _tb.scan_test_dependencies(self.tests, self.tmp)))


# ==================== 生产环境硬底线（★防复发核心） ====================

class TestCriticalFilesProtected(unittest.TestCase):
    """★这 3 个文件第45批被误删过 —— 判据必须永远把它们识别为受保护。"""

    @classmethod
    def setUpClass(cls):
        _tmp = os.path.join(_ROOT, "tmp")
        for _n in CRITICAL:
            if not os.path.isfile(os.path.join(_tmp, _n)):
                raise unittest.SkipTest(
                    "生产 tmp 缺少 %s（可能已被清理）→ 无法验证" % _n)
        cls.deps = _tb.scan_test_dependencies(
            os.path.join(_ROOT, "tests"), _tmp)

    def test_20_patch_t2_m9_protected(self):
        self.assertIn("patch_t2_m9.py", self.deps["protected"])

    def test_21_scan_orphan_m10_protected(self):
        self.assertIn("scan_orphan_event_m10.py", self.deps["protected"])

    def test_22_scan_summarize_m10_protected(self):
        self.assertIn("scan_summarize_orphan_m10.py", self.deps["protected"])

    def test_23_all_three_still_on_disk(self):
        for _n in CRITICAL:
            self.assertTrue(os.path.isfile(os.path.join(_ROOT, "tmp", _n)),
                            "生产 tmp 缺少 %s" % _n)

    def test_24_import_and_subprocess_both_covered(self):
        """三类依赖里，import 与 subprocess 这两类必须都有命中（第45批就是栽在这两类）。"""
        self.assertTrue(self.deps["by_import"], "import 类依赖应至少命中 1 处")
        self.assertTrue(self.deps["by_subprocess"],
                        "subprocess 类依赖应至少命中 1 处")


# ==================== 与第45批守卫测试的兼容性 ====================

class TestCompatWithM45Guard(unittest.TestCase):
    def test_30_test_isolation_still_present(self):
        """157-T-基础-1：verify 硬依赖已迁入受控路径 tools/test_isolation_shim.py（git 跟踪）。
        B156-1 T-A03：tmp/test_isolation.py / test_log_isolation.py 为 git-ignored 易失辅助模块，
        缺失时降级 skip（与 m18/m19/m22 一致）；受控件 tools/test_isolation_shim.py 始终存在。"""
        # 受控硬依赖（git 跟踪，必存在）
        _shim = os.path.join(_ROOT, "tools", "test_isolation_shim.py")
        self.assertTrue(os.path.isfile(_shim), "缺少受控隔离件 %s" % _shim)
        # 历史 tmp/ 易失件：缺失则降级 skip
        for _n in ("test_isolation.py", "test_log_isolation.py"):
            _p = os.path.join(_ROOT, "tmp", _n)
            if not os.path.isfile(_p):
                self.skipTest("生产 tmp 缺少 %s（git-ignored 易失件）→ 跳过" % _n)

    def test_31_deps_superset_of_m45_guard(self):
        """本判据识别出的受保护文件，应是 m45 守卫所要求集合的超集。"""
        _tmp = os.path.join(_ROOT, "tmp")
        _d = _tb.scan_test_dependencies(os.path.join(_ROOT, "tests"), _tmp)
        for _n in ("test_isolation.py", "test_log_isolation.py"):
            if os.path.isfile(os.path.join(_tmp, _n)):
                self.assertIn(_n, _d["protected"],
                              "%s 未被识别为受保护" % _n)


# ==================== tmp 隔离目录迁移（T4-A/B 成果） ====================

class TestIsolationDirMigrated(unittest.TestCase):
    """★T4-A/B：测试隔离目录不再落在项目 tmp/ 下。"""

    def test_40_helper_defined_in_migrated_tests(self):
        _files = ["test_call_pattern_m43.py", "test_data_quality_m43.py",
                  "test_experience_retriever_m42.py", "test_log_retention_m42.py",
                  "test_model_self_updater_m43.py", "test_patch_quality_m42.py",
                  "test_data_governance_m41.py", "test_semantic_cache_m41.py"]
        for _f in _files:
            _p = os.path.join(_ROOT, "tests", _f)
            if not os.path.isfile(_p):
                continue
            _t = io.open(_p, encoding="utf-8", errors="replace").read()
            self.assertIn("_pytest_tmp_root", _t,
                          "%s 未改用 _pytest_tmp_root()" % _f)

    def test_41_helper_points_outside_project_tmp(self):
        """辅助函数不得再指向项目 tmp/（否则残留必然复现）。"""
        _p = os.path.join(_ROOT, "tests", "test_call_pattern_m43.py")
        if not os.path.isfile(_p):
            self.skipTest("文件不存在")
        _t = io.open(_p, encoding="utf-8", errors="replace").read()
        self.assertIn(".pytest_tmp", _t)
        # 不应再出现 'dir=_TMP_ROOT' 之外的项目 tmp 直连
        self.assertNotIn('_TMP_ROOT = os.path.join(_ROOT, "tmp")', _t)

    def test_42_pytest_tmp_gitignored(self):
        """★第47批 T4：tmp 快照落点由 ``.tmp_backup/`` 迁移为 ``.bak_tmp/``
        （可被既有 ``.bak*`` 规则自动排除），断言同步更新。"""
        _gi = os.path.join(_ROOT, ".gitignore")
        if not os.path.isfile(_gi):
            self.skipTest("无 .gitignore")
        _g = io.open(_gi, encoding="utf-8", errors="replace").read()
        for _n in (".pytest_tmp/", ".bak_tmp/"):
            self.assertIn(_n, _g, ".gitignore 缺少 %s" % _n)

    def test_43_helper_same_disk_as_project(self):
        """落点必须与项目同盘（Windows 跨盘 move 触发沙箱删除配额）。"""
        _p = os.path.join(_ROOT, "tests", "test_call_pattern_m43.py")
        if not os.path.isfile(_p):
            self.skipTest("文件不存在")
        _t = io.open(_p, encoding="utf-8", errors="replace").read()
        self.assertIn("os.path.dirname(os.path.dirname(os.path.abspath(__file__)))",
                      _t, "落点应基于项目根计算（保证同盘）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
