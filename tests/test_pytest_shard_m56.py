# -*- coding: utf-8 -*-
"""★主线第56批 T4/P2-392：分片结果解析缺陷修复门控单测。

不依赖框架重启；全部为纯函数/ mock 测试，不真正跑全量 pytest。
"""
import os
import sys
import unittest
from unittest import mock

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
import pytest_shard as S  # noqa: E402


class TestClassifyExitCode(unittest.TestCase):
    def test_map(self):
        self.assertEqual(S.classify_exit_code(0), "pass")
        self.assertEqual(S.classify_exit_code(1), "fail")
        self.assertEqual(S.classify_exit_code(2), "interrupt")
        self.assertEqual(S.classify_exit_code(3), "internal_error")
        self.assertEqual(S.classify_exit_code(4), "cmdline_error")
        self.assertEqual(S.classify_exit_code(5), "no_tests_collected")

    def test_unknown(self):
        self.assertEqual(S.classify_exit_code(99), "unknown")


class TestParseSummary(unittest.TestCase):
    def test_normal(self):
        """★正常输出能正确解析 passed。"""
        _out = "...\n114 passed in 3.21s\n"
        _s = S.parse_summary(_out)
        self.assertEqual(_s["passed"], 114)
        self.assertEqual(_s["failed"], 0)
        self.assertFalse(_s["error"])

    def test_collection_error(self):
        """★collection 错误：pytest 不打印 passed，passed/failed 均为 0 且 error=True。"""
        _out = ("ImportError while importing test module.\n"
                "ERROR collecting tests/test_x.py\n"
                "pytest: error: ...\n")
        _s = S.parse_summary(_out)
        self.assertEqual(_s["passed"], 0)
        self.assertEqual(_s["failed"], 0)
        self.assertTrue(_s["error"])

    def test_failed_count(self):
        _out = "3 failed, 114 passed in 5.00s\n"
        _s = S.parse_summary(_out)
        self.assertEqual(_s["failed"], 3)
        self.assertEqual(_s["passed"], 114)


class TestEvaluateShard(unittest.TestCase):
    def test_normal_pass(self):
        """★正常通过：exit 0 + passed>0 → pass，不报警。"""
        _r = S.evaluate_shard("114 passed in 3s", 0, 8)
        self.assertEqual(_r["status"], "pass")
        self.assertFalse(_r["alarm"])

    def test_alarm_on_zero(self):
        """★核心修复：passed+failed==0 且文件数>0 → 显式报警（suspicious）。"""
        _r = S.evaluate_shard("", 0, 8)
        self.assertTrue(_r["alarm"])
        self.assertEqual(_r["status"], "suspicious")
        self.assertIn("passed+failed==0", _r["reason"])

    def test_fail(self):
        _r = S.evaluate_shard("3 failed, 114 passed", 1, 8)
        self.assertEqual(_r["status"], "fail")
        self.assertFalse(_r["alarm"])

    def test_collection_error_status(self):
        """★collection 错误即便 exit 0 也被判为 error（关键词兜底）。"""
        _out = "ERROR collecting tests/test_x.py\n"
        _r = S.evaluate_shard(_out, 0, 1)
        self.assertEqual(_r["status"], "error")
        self.assertTrue(_r["alarm"])

    def test_internal_error_exit(self):
        _r = S.evaluate_shard("INTERNALERROR> Traceback", 3, 8)
        self.assertEqual(_r["status"], "error")

    def test_no_tests_collected(self):
        _r = S.evaluate_shard("", 5, 8)
        self.assertEqual(_r["status"], "no_tests_collected")
        self.assertTrue(_r["alarm"])

    def test_timeout(self):
        _r = S.evaluate_shard("TIMEOUT", -1, 1)
        self.assertEqual(_r["status"], "timeout")
        self.assertTrue(_r["alarm"])


class TestRunShard(unittest.TestCase):
    def test_empty_files(self):
        _r = S.run_shard("pytest", [], cwd="D:/x")
        self.assertEqual(_r["status"], "pass")
        self.assertEqual(_r["file_count"], 0)

    def test_timeout_path(self):
        """★run_shard 捕获 TimeoutExpired → status=timeout。"""
        with mock.patch.object(S.subprocess, "run",
                                side_effect=S.subprocess.TimeoutExpired(
                                    cmd=["pytest"], timeout=1)):
            _r = S.run_shard("pytest", ["tests/x.py"], cwd="D:/x")
        self.assertEqual(_r["status"], "timeout")
        self.assertTrue(_r["alarm"])

    def test_real_run_via_mock(self):
        """★mock subprocess.run 返回 114 passed → status=pass。"""
        class _P:
            stdout = "114 passed in 3.00s\n"
            stderr = ""
            returncode = 0
        with mock.patch.object(S.subprocess, "run", return_value=_P()):
            _r = S.run_shard("pytest", ["tests/a.py", "tests/b.py"], cwd="D:/x")
        self.assertEqual(_r["status"], "pass")
        self.assertEqual(_r["passed"], 114)
        self.assertEqual(_r["file_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
