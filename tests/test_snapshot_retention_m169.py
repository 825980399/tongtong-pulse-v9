# -*- coding: utf-8 -*-
"""169批 C6（T-快照 retention 核查）门控测试。

锁定：
  1. 受管（顶层，判据与 _cleanup_old_backups 同源）/ 未受管（子目录）分离计数；
  2. **只读**：审计前后文件一个不少（删除动作归停窗段）；
  3. 「5 > 3」归因正确：受管未超时，超额来自子目录副本（清理不递归）。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.snapshot_retention_audit as ra


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m169_c6_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, rel, size_bytes=1024, age_days=0.0):
        _p = os.path.join(self.tmp, rel)
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
        with open(_p, "wb") as _f:
            _f.write(b"x" * size_bytes)
        if age_days:
            _mt = time.time() - age_days * 86400.0
            os.utime(_p, (_mt, _mt))
        return _p

    def _count_files(self):
        _n = 0
        for _dp, _dns, _fns in os.walk(self.tmp):
            _n += len(_fns)
        return _n


class TestRetentionScan(_Tmp):
    """① 受管 / 未受管分离计数。"""

    def test_10_managed_vs_unmanaged(self):
        for _i in range(3):
            self._mk("pulse_knowledge_snapshot.json.2026100%d.bak" % _i, 2048,
                     age_days=float(_i))
        self._mk("other.tmp", 512)                       # 不匹配基名 -> 不计
        self._mk("sub/a.bak", 4096)                      # 子目录 -> 未受管
        self._mk("sub/pulse_knowledge_snapshot.json.20260923_01.bak", 8192)
        _r = ra.scan_retention(directory=self.tmp, keep=3, root=self.tmp)
        self.assertEqual(_r["managed_count"], 3)
        self.assertEqual(_r["unmanaged_count"], 2)
        self.assertTrue(_r["over_limit"])

    def test_11_predicate_mirrors_cleanup(self):
        """判据同源：顶层 .bak 必须以基名为前缀才进受管集。"""
        self._mk("pulse_knowledge_snapshot.json.1.bak", 1024)
        self._mk("unrelated.bak", 1024)                  # 含 .bak 但前缀不符
        _r = ra.scan_retention(directory=self.tmp, keep=3, root=self.tmp)
        self.assertEqual(_r["managed_count"], 1)
        self.assertTrue(all(x["name"].startswith(ra.DEFAULT_BASE_NAME)
                            for x in _r["managed"]))

    def test_12_clean_state_no_over(self):
        self._mk("pulse_knowledge_snapshot.json.1.bak", 1024, age_days=1.0)
        self._mk("pulse_knowledge_snapshot.json.2.bak", 1024, age_days=2.0)
        _r = ra.scan_retention(directory=self.tmp, keep=3, root=self.tmp)
        self.assertEqual(_r["managed_count"], 2)
        self.assertEqual(_r["unmanaged_count"], 0)
        self.assertFalse(_r["over_limit"])
        self.assertEqual(_r["over_by"], 0)

    def test_13_managed_overflow(self):
        """受管 5 > 上限 3 -> over_by=2。"""
        for _i in range(5):
            self._mk("pulse_knowledge_snapshot.json.%d.bak" % _i, 1024,
                     age_days=float(_i))
        _r = ra.scan_retention(directory=self.tmp, keep=3, root=self.tmp)
        self.assertEqual(_r["over_by"], 2)
        self.assertTrue(_r["over_limit"])


class TestReadOnly(_Tmp):
    """② 只读：审计不删任何文件。"""

    def test_20_no_deletion(self):
        for _i in range(3):
            self._mk("pulse_knowledge_snapshot.json.%d.bak" % _i, 1024)
        self._mk("sub/x.bak", 2048)
        _before = self._count_files()
        ra.scan_retention(directory=self.tmp, keep=3, root=self.tmp)
        # 连 main 也跑一遍（含 --json 落盘），确认仍不删快照
        _out = os.path.join(self.tmp, "out.json")
        ra.main(["--dir", self.tmp, "--keep", "3", "--json", _out])
        self.assertEqual(self._count_files(), _before + 1,
                         "只允许多出 1 个 --json 产物，快照不得被删")


class TestCause(_Tmp):
    """③ 归因文案正确（供停窗段清理决策）。"""

    def test_30_cause_subdir(self):
        for _i in range(3):
            self._mk("pulse_knowledge_snapshot.json.%d.bak" % _i, 1024,
                     age_days=float(_i))
        self._mk("sub/pulse_knowledge_snapshot.json.old.bak", 2048)
        _r = ra.scan_retention(directory=self.tmp, keep=3, root=self.tmp)
        self.assertEqual(_r["over_by"], 0)
        self.assertIn("子目录", _r["cause"])

    def test_31_cause_managed_overflow(self):
        for _i in range(5):
            self._mk("pulse_knowledge_snapshot.json.%d.bak" % _i, 1024,
                     age_days=float(_i))
        _r = ra.scan_retention(directory=self.tmp, keep=3, root=self.tmp)
        self.assertIn("清理", _r["cause"])


if __name__ == "__main__":
    unittest.main()
