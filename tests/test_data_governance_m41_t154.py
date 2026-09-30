# -*- coding: utf-8 -*-
"""T154-7 / M41 接线单元测试：govern_quarantine_expiry 按龄清理隔离件。

验证点：
1. 超过 config.CORRUPTED_QUARANTINE_DAYS（默认 30）天的隔离件被删除并移出 manifest；
2. 未到期隔离件保留；
3. dry-run 不改动 manifest 与磁盘；
4. 函数经由 _cfg 读取 CORRUPTED_QUARANTINE_DAYS（死配置 A6 案修复的闭环证据）。
"""
import json
import os
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import tools.data_governance_m41 as gov  # noqa: E402


class TestQuarantineExpiry(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m41_q_")
        self._quar = os.path.join(self._tmp, "_quarantine", "corrupted")
        os.makedirs(self._quar, exist_ok=True)
        self._manifest = os.path.join(self._tmp, "manifest.json")
        self._saved = (gov._QUAR, gov._MANIFEST)
        gov._QUAR = self._quar
        gov._MANIFEST = self._manifest

    def tearDown(self):
        gov._QUAR, gov._MANIFEST = self._saved
        for _root, _dirs, _files in os.walk(self._tmp, topdown=False):
            for _f in _files:
                try:
                    os.remove(os.path.join(_root, _f))
                except OSError:
                    pass
            for _d in _dirs:
                try:
                    os.rmdir(os.path.join(_root, _d))
                except OSError:
                    pass
        try:
            os.rmdir(self._tmp)
        except OSError:
            pass

    def _write_manifest(self, entries):
        with open(self._manifest, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)

    def test_expired_purged_recent_kept(self):
        _old = os.path.join(self._quar, "old.json.corrupted")
        with open(_old, "w", encoding="utf-8") as f:
            f.write("stale")
        _new = os.path.join(self._quar, "new.json.corrupted")
        with open(_new, "w", encoding="utf-8") as f:
            f.write("fresh")
        self._write_manifest([
            {"file": "old.json.corrupted", "origin": "old.json",
             "size": 5, "at": time.time() - 40 * 86400.0},
            {"file": "new.json.corrupted", "origin": "new.json",
             "size": 5, "at": time.time() - 1 * 86400.0},
        ])
        _r = gov.govern_quarantine_expiry(dry=False)
        self.assertEqual(_r["purged"], 1, "仅过期 1 件应被清理")
        self.assertFalse(os.path.isfile(_old), "过期隔离件应被删除")
        self.assertTrue(os.path.isfile(_new), "未到期隔离件应保留")
        _entries = json.load(open(self._manifest, encoding="utf-8"))
        self.assertEqual(len(_entries), 1)
        self.assertEqual(_entries[0]["file"], "new.json.corrupted")

    def test_stale_manifest_entry_without_file_purged(self):
        # A6 案真实形态：manifest 1,274 条账 vs 实际 0 件 → 过期账应被清掉
        self._write_manifest([
            {"file": "ghost.json.corrupted", "origin": "ghost.json",
             "size": 0, "at": time.time() - 100 * 86400.0},
        ])
        _r = gov.govern_quarantine_expiry(dry=False)
        self.assertEqual(_r["purged"], 1)
        self.assertEqual(json.load(open(self._manifest, encoding="utf-8")), [],
                         "无对应文件的过期账应从 manifest 移除")

    def test_dry_run_no_change(self):
        _old = os.path.join(self._quar, "old.json.corrupted")
        with open(_old, "w", encoding="utf-8") as f:
            f.write("stale")
        _manifest_before = [
            {"file": "old.json.corrupted", "origin": "old.json",
             "size": 5, "at": time.time() - 40 * 86400.0},
        ]
        self._write_manifest(_manifest_before)
        _r = gov.govern_quarantine_expiry(dry=True)
        self.assertEqual(_r["purged"], 1, "dry-run 仍应统计到期件数")
        self.assertTrue(os.path.isfile(_old), "dry-run 不得删除磁盘文件")
        self.assertEqual(
            json.load(open(self._manifest, encoding="utf-8")),
            _manifest_before, "dry-run 不得改写 manifest")


if __name__ == "__main__":
    unittest.main(verbosity=2)
