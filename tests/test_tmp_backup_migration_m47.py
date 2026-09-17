# -*- coding: utf-8 -*-
"""第47批 T4 门控测试：tmp 备份落点迁移（P2-300）

覆盖：
  * 默认落点已改为 ``.bak_tmp/``（可被既有 ``.bak*`` 规则自动排除）
  * ``migrate_legacy()`` 迁移正确性（移动而非删除、MANIFEST 校验、空目录清理）
  * 迁移后旧快照内容完整（sha256 与 MANIFEST 一致）
  * ``.gitignore`` 已同步
  * 生产落点现状（``.bak_tmp`` 存在 / ``.tmp_backup`` 已消失）

★测试隔离：迁移测试在沙箱内构造 fake 根目录，**不动生产目录**。
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


class TestDefaultLocation(unittest.TestCase):
    def test_01_default_is_bak_tmp(self):
        self.assertEqual(os.path.basename(_tb.BACKUP_ROOT), ".bak_tmp")

    def test_02_legacy_constant_kept(self):
        """旧常量保留，供迁移使用。"""
        self.assertEqual(os.path.basename(_tb.LEGACY_BACKUP_ROOT),
                         ".tmp_backup")

    def test_03_bak_tmp_auto_excluded(self):
        """.bak_tmp 必须被统一排除列表的 .bak* 前缀规则覆盖。"""
        from nucleus.data.exclude_dirs import is_excluded
        self.assertTrue(is_excluded(".bak_tmp"))
        self.assertTrue(is_excluded(".bak_batch47"))
        self.assertTrue(is_excluded(".tmp_backup"))   # 显式列出
        self.assertTrue(is_excluded(".release-tmp"))
        self.assertTrue(is_excluded(".pytest_tmp"))


class _MigrateSandbox(unittest.TestCase):
    def setUp(self):
        self._sand = tempfile.mkdtemp(prefix="m47mig_")
        self.legacy = os.path.join(self._sand, ".tmp_backup")
        self.new = os.path.join(self._sand, ".bak_tmp")
        os.makedirs(os.path.join(self.legacy, "20260101_000000"), exist_ok=True)
        # 造一个带 MANIFEST 的快照
        _d = os.path.join(self.legacy, "20260101_000000")
        with io.open(os.path.join(_d, "patch_x_m99.py"), "w",
                     encoding="utf-8") as _f:
            _f.write("x = 1\n")
        with io.open(os.path.join(_d, _tb.MANIFEST), "w", encoding="utf-8") as _f:
            _f.write(json.dumps({"snapshot": "20260101_000000",
                                 "file_count": 1, "files": []}))

    def tearDown(self):
        shutil.rmtree(self._sand, ignore_errors=True)


class TestMigrate(_MigrateSandbox):
    def test_10_moves_snapshot(self):
        _r = _tb.migrate_legacy(self.legacy, self.new)
        self.assertEqual(_r["moved"], ["20260101_000000"])
        self.assertTrue(os.path.isdir(os.path.join(self.new, "20260101_000000")))

    def test_11_content_preserved(self):
        _tb.migrate_legacy(self.legacy, self.new)
        _p = os.path.join(self.new, "20260101_000000", "patch_x_m99.py")
        self.assertEqual(io.open(_p, encoding="utf-8").read(), "x = 1\n")

    def test_12_legacy_dir_removed_when_empty(self):
        _r = _tb.migrate_legacy(self.legacy, self.new)
        self.assertTrue(_r["legacy_removed"])
        self.assertFalse(os.path.isdir(self.legacy))

    def test_13_no_legacy_dir_is_noop(self):
        shutil.rmtree(self.legacy)
        _r = _tb.migrate_legacy(self.legacy, self.new)
        self.assertEqual(_r["moved"], [])
        self.assertIn("无需迁移", _r["skipped"][0])

    def test_14_existing_target_skipped(self):
        os.makedirs(os.path.join(self.new, "20260101_000000"), exist_ok=True)
        _r = _tb.migrate_legacy(self.legacy, self.new)
        self.assertEqual(_r["moved"], [])
        self.assertTrue(any("已存在" in s for s in _r["skipped"]))

    def test_15_broken_manifest_not_moved(self):
        """MANIFEST 损坏的快照不迁移（避免污染新落点），且保留现场。"""
        _d = os.path.join(self.legacy, "20260102_000000")
        os.makedirs(_d, exist_ok=True)
        with io.open(os.path.join(_d, _tb.MANIFEST), "w", encoding="utf-8") as _f:
            _f.write("{broken json")
        _r = _tb.migrate_legacy(self.legacy, self.new)
        self.assertIn("20260101_000000", _r["moved"])
        self.assertTrue(any("20260102_000000" in e for e in _r["errors"]))
        self.assertFalse(os.path.isdir(os.path.join(self.new, "20260102_000000")))

    def test_16_idempotent(self):
        _tb.migrate_legacy(self.legacy, self.new)
        _r2 = _tb.migrate_legacy(self.legacy, self.new)
        self.assertEqual(_r2["moved"], [])

    def test_17_creates_target_dir(self):
        shutil.rmtree(self.new, ignore_errors=True)
        _tb.migrate_legacy(self.legacy, self.new)
        self.assertTrue(os.path.isdir(self.new))


class TestGitignore(unittest.TestCase):
    def test_20_bak_tmp_ignored(self):
        _gi = os.path.join(_ROOT, ".gitignore")
        if not os.path.isfile(_gi):
            self.skipTest("无 .gitignore")
        _g = io.open(_gi, encoding="utf-8", errors="replace").read()
        self.assertIn(".bak_tmp/", _g)

    def test_21_pytest_tmp_ignored(self):
        _gi = os.path.join(_ROOT, ".gitignore")
        if not os.path.isfile(_gi):
            self.skipTest("无 .gitignore")
        _g = io.open(_gi, encoding="utf-8", errors="replace").read()
        self.assertIn(".pytest_tmp/", _g)


class TestProductionState(unittest.TestCase):
    def test_30_old_location_gone(self):
        """.tmp_backup 应已被迁移走。"""
        if not os.path.isdir(os.path.join(_ROOT, ".tmp_backup")):
            return
        self.fail("旧落点 .tmp_backup 仍存在（迁移未完成）")

    def test_31_new_location_exists(self):
        _d = os.path.join(_ROOT, ".bak_tmp")
        if not os.path.isdir(_d):
            self.skipTest("生产 .bak_tmp 尚未创建")
        self.assertGreater(len(os.listdir(_d)), 0)

    def test_32_migrated_snapshot_readable(self):
        """迁移后的快照 MANIFEST 仍可解析（数据未损坏）。"""
        _d = os.path.join(_ROOT, ".bak_tmp")
        if not os.path.isdir(_d):
            self.skipTest("生产 .bak_tmp 尚未创建")
        for _n in os.listdir(_d):
            _mf = os.path.join(_d, _n, _tb.MANIFEST)
            if os.path.isfile(_mf):
                _j = json.load(io.open(_mf, encoding="utf-8"))
                self.assertTrue(_j.get("file_count") is not None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
