# -*- coding: utf-8 -*-
"""第46批 T4 门控测试：tmp 备份机制（P2-300）

覆盖 ``tools/tmp_backup.py``：
  * 快照创建 / MANIFEST / sha256
  * 列表 / 保留策略 prune / 恢复 restore（含校验）
  * 边界：空目录、不存在目录、非 .py 文件

★测试隔离：全部使用 ``tempfile.mkdtemp()`` 构造的独立目录，
  **绝不碰生产 ``tmp/`` 或 ``.tmp_backup/``**。
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools import tmp_backup as _tb  # noqa: E402


class _Sandbox(unittest.TestCase):
    """每个用例一套独立沙箱：fake tmp + fake backup root。"""

    def setUp(self):
        self._sand = tempfile.mkdtemp(prefix="m46tbb_")
        self.tmp = os.path.join(self._sand, "tmp")
        self.bak = os.path.join(self._sand, ".bak_tmp")
        os.makedirs(self.tmp, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._sand, ignore_errors=True)

    def _put(self, name, content="x = 1\n"):
        _p = os.path.join(self.tmp, name)
        with io.open(_p, "w", encoding="utf-8") as _f:
            _f.write(content)
        return _p


# ==================== 快照 ====================

class TestSnapshot(_Sandbox):
    def test_01_snapshot_creates_dir(self):
        self._put("scan_probe_m99.py")
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        self.assertTrue(os.path.isdir(os.path.join(self.bak, _r["snapshot"])))
        self.assertEqual(_r["file_count"], 1)

    def test_02_snapshot_copies_py_files(self):
        self._put("patch_probe_m99.py", "print(1)\n")
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        _d = os.path.join(self.bak, _r["snapshot"], "patch_probe_m99.py")
        self.assertTrue(os.path.isfile(_d))
        self.assertEqual(io.open(_d, encoding="utf-8").read(), "print(1)\n")

    def test_03_manifest_written_and_valid(self):
        self._put("check_probe_m99.py", "y = 2\n")
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        _mf = os.path.join(self.bak, _r["snapshot"], _tb.MANIFEST)
        self.assertTrue(os.path.isfile(_mf))
        _j = json.load(io.open(_mf, encoding="utf-8"))
        self.assertEqual(_j["file_count"], 1)
        self.assertEqual(_j["files"][0]["name"], "check_probe_m99.py")
        self.assertTrue(_j["files"][0]["sha256"])

    def test_04_sha256_matches_source(self):
        _p = self._put("verify_probe_m99.py", "z = 3\n")
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        self.assertEqual(_r["files"][0]["sha256"], _tb._sha256(_p))

    def test_05_empty_tmp_still_snapshots(self):
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        self.assertEqual(_r["file_count"], 0)
        self.assertTrue(os.path.isdir(os.path.join(self.bak, _r["snapshot"])))

    def test_06_missing_tmp_dir_no_crash(self):
        _r = _tb.snapshot(tmp_dir=os.path.join(self._sand, "nope"),
                          backup_root=self.bak)
        self.assertEqual(_r["file_count"], 0)

    def test_07_snapshot_records_label(self):
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak,
                          label="第46批")
        self.assertEqual(_r["label"], "第46批")

    def test_08_dirs_are_observed_not_copied(self):
        os.makedirs(os.path.join(self.tmp, "some_iso_dir"), exist_ok=True)
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        self.assertIn("some_iso_dir", _r["dirs_observed"])
        # 目录本身不被复制（避免把隔离目录整个搬走）
        self.assertFalse(os.path.isdir(
            os.path.join(self.bak, _r["snapshot"], "some_iso_dir")))


# ==================== 列表 ====================

class TestList(_Sandbox):
    def test_10_list_empty(self):
        self.assertEqual(_tb.list_backups(self.bak), [])

    def test_11_list_sorted_desc(self):
        for _i in range(3):
            self._put("scan_a_m99_%d.py" % _i)
            _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
            os.remove(os.path.join(self.tmp, "scan_a_m99_%d.py" % _i))
        _l = _tb.list_backups(self.bak)
        self.assertEqual(len(_l), 3)
        _names = [x["snapshot"] for x in _l]
        self.assertEqual(_names, sorted(_names, reverse=True))

    def test_12_list_has_manifest_info(self):
        self._put("record_b_m99.py")
        _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        _l = _tb.list_backups(self.bak)
        self.assertEqual(_l[0]["file_count"], 1)
        self.assertTrue(_l[0]["created_str"])


# ==================== 保留策略 ====================

class TestPrune(_Sandbox):
    def _make(self, n):
        for _i in range(n):
            self._put("scan_c_m99.py", "# %d\n" % _i)
            _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)

    def test_20_prune_keeps_last_n(self):
        self._make(7)
        _r = _tb.prune(keep=5, backup_root=self.bak)
        self.assertEqual(len(_r["remaining"]), 5)
        self.assertEqual(len(_r["removed"]), 2)

    def test_21_prune_removes_older_dirs(self):
        self._make(7)
        _r = _tb.prune(keep=5, backup_root=self.bak)
        for _n in _r["removed"]:
            self.assertFalse(os.path.isdir(os.path.join(self.bak, _n)))

    def test_22_prune_noop_when_under_limit(self):
        self._make(3)
        _r = _tb.prune(keep=5, backup_root=self.bak)
        self.assertEqual(_r["removed"], [])
        self.assertEqual(len(_r["remaining"]), 3)

    def test_23_prune_idempotent(self):
        self._make(7)
        _tb.prune(keep=5, backup_root=self.bak)
        _r2 = _tb.prune(keep=5, backup_root=self.bak)
        self.assertEqual(_r2["removed"], [])


# ==================== 恢复 ====================

class TestRestore(_Sandbox):
    def test_30_restore_copies_back(self):
        self._put("patch_d_m99.py", "q = 9\n")
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        os.remove(os.path.join(self.tmp, "patch_d_m99.py"))
        _r2 = _tb.restore(_r["snapshot"], tmp_dir=self.tmp, backup_root=self.bak)
        self.assertTrue(_r2["ok"])
        self.assertEqual(_r2["restored_count"], 1)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "patch_d_m99.py")))

    def test_31_restore_content_identical(self):
        self._put("check_d_m99.py", "w = 8\n")
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        os.remove(os.path.join(self.tmp, "check_d_m99.py"))
        _tb.restore(_r["snapshot"], tmp_dir=self.tmp, backup_root=self.bak)
        self.assertEqual(
            io.open(os.path.join(self.tmp, "check_d_m99.py"), encoding="utf-8").read(),
            "w = 8\n")

    def test_32_restore_detects_sha_mismatch(self):
        self._put("scan_d_m99.py", "orig\n")
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        # 篡改备份内容
        _bp = os.path.join(self.bak, _r["snapshot"], "scan_d_m99.py")
        with io.open(_bp, "w", encoding="utf-8") as _f:
            _f.write("tampered\n")
        _r2 = _tb.restore(_r["snapshot"], tmp_dir=self.tmp, backup_root=self.bak)
        self.assertIn("scan_d_m99.py", _r2["sha_mismatch"])
        self.assertFalse(_r2["ok"])

    def test_33_restore_missing_snapshot(self):
        _r = _tb.restore("99999999_000000", tmp_dir=self.tmp, backup_root=self.bak)
        self.assertFalse(_r["ok"])
        self.assertIn("备份不存在", _r["error"])

    def test_34_restore_no_overwrite_skips(self):
        self._put("update_d_m99.py", "old\n")
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        with io.open(os.path.join(self.tmp, "update_d_m99.py"), "w",
                     encoding="utf-8") as _f:
            _f.write("new\n")
        _r2 = _tb.restore(_r["snapshot"], tmp_dir=self.tmp, backup_root=self.bak,
                          overwrite=False)
        self.assertIn("update_d_m99.py", _r2["skipped"])
        self.assertEqual(
            io.open(os.path.join(self.tmp, "update_d_m99.py"), encoding="utf-8").read(),
            "new\n")

    def test_35_restore_creates_tmp_if_missing(self):
        self._put("run_d_m99.py")
        _r = _tb.snapshot(tmp_dir=self.tmp, backup_root=self.bak)
        _new = os.path.join(self._sand, "restored_tmp")
        _r2 = _tb.restore(_r["snapshot"], tmp_dir=_new, backup_root=self.bak)
        self.assertTrue(os.path.isdir(_new))
        self.assertEqual(_r2["restored_count"], 1)


# ==================== 不碰生产目录（安全断言） ====================

class TestNoProductionTouch(unittest.TestCase):
    def test_40_production_backup_root_untouched_by_sandbox(self):
        """本测试文件全程用沙箱，不应改动生产 .tmp_backup 的既有备份数。"""
        _prod = os.path.join(_ROOT, ".bak_tmp")
        if not os.path.isdir(_prod):
            self.skipTest("生产 .tmp_backup 尚未创建")
        _before = len(os.listdir(_prod))
        _sand = tempfile.mkdtemp(prefix="m46safe_")
        try:
            _tb.snapshot(tmp_dir=_sand,
                         backup_root=os.path.join(_sand, "bk"))
        finally:
            shutil.rmtree(_sand, ignore_errors=True)
        self.assertEqual(len(os.listdir(_prod)), _before)

    def test_41_should_keep_rules(self):
        self.assertTrue(_tb._should_keep("patch_x_m99.py"))
        self.assertTrue(_tb._should_keep("scan_orphan_event_m10.py"))
        self.assertFalse(_tb._should_keep("_m45_collect.txt"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
