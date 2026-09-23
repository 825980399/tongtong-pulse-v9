# -*- coding: utf-8 -*-
"""★主线第56批 T5/P2-395：代码学习已检测问题记忆（方案B 持久化）门控单测。

不依赖框架重启；全部使用临时存储路径，不触碰生产 data/。
"""
import os
import sys
import tempfile
import time
import unittest

ROOT = "D:/xinrenlei/tongtong-pulse-v9"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from nucleus.code_learning_memory import CheckedIssueMemory  # noqa: E402


class TestCheckedIssueMemory(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.mkdtemp(prefix="m56_clm_")
        self._store = os.path.join(self._d, "checked_issues.json")
        self._mem = CheckedIssueMemory(storage_path=self._store, expiry_days=7)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._d, ignore_errors=True)

    def test_new_issue_not_skipped(self):
        """★新问题（未记录）→ should_skip 返回 False。"""
        self.assertFalse(self._mem.should_skip("x.py", 10, "bare_except"))

    def test_record_then_skip(self):
        """★记录后同键（文件未改+未过期）→ 跳过。"""
        self._mem.record("x.py", 10, "bare_except")
        self.assertTrue(self._mem.should_skip("x.py", 10, "bare_except"))

    def test_skipped_count(self):
        """★skipped_count 随跳过递增。"""
        self._mem.record("a.py", 1, "t1")
        self._mem.record("b.py", 2, "t2")
        self._mem.should_skip("a.py", 1, "t1")
        self._mem.should_skip("b.py", 2, "t2")
        self._mem.should_skip("a.py", 1, "t1")
        self.assertEqual(self._mem.skipped_count, 3)

    def test_file_modified_resets(self):
        """★文件被修改（mtime 变化）→ 重新检测（不跳过）。"""
        _src = os.path.join(self._d, "src.py")
        with open(_src, "w", encoding="utf-8") as f:
            f.write("def f():\n    pass\n")
        self._mem.record(_src, 1, "bare_except")
        self.assertTrue(self._mem.should_skip(_src, 1, "bare_except"))
        # 修改文件（更新 mtime 到未来）
        _new = time.time() + 1000
        os.utime(_src, (_new, _new))
        self.assertFalse(self._mem.should_skip(_src, 1, "bare_except"))

    def test_expiry_resets(self):
        """★超过过期天数 → 重新检测（不跳过）。"""
        self._mem.record("x.py", 5, "print_debug")
        _key = self._mem._key("x.py", 5, "print_debug")
        self._mem._data["issues"][_key]["checked_at"] = time.time() - 30 * 86400
        self.assertFalse(self._mem.should_skip("x.py", 5, "print_debug"))

    def test_persistence_across_instances(self):
        """★持久化：新实例从磁盘加载 → 仍跳过已记录问题。"""
        self._mem.record("x.py", 7, "hardcoded_abs_path")
        _mem2 = CheckedIssueMemory(storage_path=self._store, expiry_days=7)
        self.assertTrue(_mem2.should_skip("x.py", 7, "hardcoded_abs_path"))

    def test_recorded_count(self):
        self._mem.record("a.py", 1, "t1")
        self._mem.record("b.py", 2, "t2")
        self.assertEqual(self._mem.recorded_count(), 2)

    def test_clear(self):
        self._mem.record("a.py", 1, "t1")
        self._mem.clear()
        self.assertEqual(self._mem.recorded_count(), 0)
        self.assertFalse(self._mem.should_skip("a.py", 1, "t1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
