"""421 刀7：TestGenerator 往返脚本写入失败不再记 passed=True。

被守护的四条：
  1) ★写入失败 ⇒ `passed=False` + detail 带异常类名与路径（原实现记 True）；
  2) 「不适用所以跳过」的三条路径**保持 True**（非存储类 / 无模板 / 子进程异常）——
     跳过 ≠ 失败，本刀不扩权；
  3) 写入成功 + 子进程 rc=0 ⇒ True；rc!=0 ⇒ False（既有语义不回归）；
  4) 脚本确实落在 `<project_root>/data/evolution/tests/` 下（隔离断言）。

★`TestGenerator(project_root=<tmp>)` 构造即 `os.makedirs(<tmp>/data/evolution/tests)`
  —— 传 tmp 就完全隔离，不碰生产 `data/evolution/tests/`。
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution.TestGenerator import TestGenerator

_SNAP = "organs/body/PulseSnapshot.py"


class TestK7WriteFailure(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.tg = TestGenerator(self.tmp)
        self.assertTrue(self.tg._test_dir.startswith(self.tmp), '测试目录未隔离')

    def tearDown(self):
        self._td.cleanup()

    def test_01_write_failure_is_passed_false(self):
        """★核心：写入失败 = 这一关没测，不能记通过。"""
        _blocker = os.path.join(self.tmp, "blocker")
        with open(_blocker, "w", encoding="utf-8") as f:
            f.write("x")
        # _test_dir 指到一个**文件** ⇒ os.path.join 后的写入必然失败
        self.tg._test_dir = _blocker
        r = self.tg._run_roundtrip_test({"id": "p1", "file": _SNAP})
        self.assertIs(r["passed"], False,
                      '写入失败仍记 passed=True —— 正是本刀要消灭的"没测算过"')
        self.assertIn("脚本写入失败", r["detail"])
        self.assertIn("blocker", r["detail"], 'detail 里应带出路径，便于排障')

    def test_02_write_failure_names_exception(self):
        _blocker = os.path.join(self.tmp, "blocker2")
        with open(_blocker, "w", encoding="utf-8") as f:
            f.write("x")
        self.tg._test_dir = _blocker
        r = self.tg._run_roundtrip_test({"id": "p2", "file": _SNAP})
        # 至少要把异常类名写出来（NotADirectoryError / PermissionError / OSError）
        self.assertRegex(r["detail"], r"(Error|OSError)")

    def test_03_non_storage_skip_still_passed(self):
        """跳过 ≠ 失败：非存储类变更不该因为本刀变红。"""
        r = self.tg._run_roundtrip_test({"id": "p3", "file": "nucleus/foo.py"})
        self.assertIs(r["passed"], True)
        self.assertIn("非存储类", r["detail"])

    def test_04_no_template_skip_still_passed(self):
        r = self.tg._run_roundtrip_test({"id": "p4", "file": "x_snapshot_store.py"})
        self.assertIs(r["passed"], True)
        self.assertIn("无法生成", r["detail"])

    def test_05_rc_zero_is_passed(self):
        _ok = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch('nucleus.evolution.TestGenerator.subprocess.run',
                        return_value=_ok):
            r = self.tg._run_roundtrip_test({"id": "p5", "file": _SNAP})
        self.assertIs(r["passed"], True)
        self.assertIn("通过", r["detail"])

    def test_06_rc_nonzero_is_failed(self):
        _bad = mock.Mock(returncode=1, stdout="boom", stderr="")
        with mock.patch('nucleus.evolution.TestGenerator.subprocess.run',
                        return_value=_bad):
            r = self.tg._run_roundtrip_test({"id": "p6", "file": _SNAP})
        self.assertIs(r["passed"], False)

    def test_07_script_lands_in_isolated_dir(self):
        _ok = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch('nucleus.evolution.TestGenerator.subprocess.run',
                        return_value=_ok):
            self.tg._run_roundtrip_test({"id": "p7", "file": _SNAP})
        _p = os.path.join(self.tg._test_dir, "p7_roundtrip.py")
        self.assertTrue(os.path.exists(_p), '脚本没落在隔离目录里')
        self.assertTrue(os.path.abspath(_p).startswith(os.path.abspath(self.tmp)))


if __name__ == '__main__':
    unittest.main()
