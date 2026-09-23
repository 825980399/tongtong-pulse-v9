# -*- coding: utf-8 -*-
"""第63批 T2 门控测试：冷存 compaction 的 parquet 缺失容错与删旧目录重试/兜底。

验证点（任务书 T2 / P1）：
1. 单个 parquet 损坏时优雅跳过，其余文件仍合并，并返回 skipped_files 计数
2. 全部 parquet 损坏时 _read_cold_partition_tolerant 返回 (None, skipped)
3. compact_cold_storage 在「部分文件损坏」场景下 success=True 且透明上报 skipped_files
4. 生产源码包含可配重试参数 + 强制删除兜底分支（静态确认改动落地）
5. 删旧目录遇 WinError 145（rmtree 反复失败）时走 ignore_errors 强制删除兜底，compaction 仍成功
6. 灰度开关 ENABLE_COLD_COMPACT_WINDOWS_RETRY 关闭时回到单次 rmtree（零回归路径）
"""
import io
import os
import shutil
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from nucleus.mnemosyne.PulseNodePool import PulseNodePool

SRC = io.open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "nucleus/mnemosyne/PulseNodePool.py"),
    encoding="utf-8", errors="replace").read()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rmtree_robust(path):
    """铁律 #54：Windows 下 shutil.rmtree(ignore_errors=True) 会静默失败。

    这里用逐文件删除兜底，确保测试临时目录（同盘）被真正清理。
    """
    if not os.path.isdir(path):
        return
    for _ in range(3):
        try:
            for _root, _dirs, _files in os.walk(path, topdown=False):
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
            os.rmdir(path)
            return
        except OSError:
            pass


def _mk_pool(cold_dir):
    """轻量 PulseNodePool 实例（绕过 __init__，仅喂冷存相关字段）。"""
    obj = PulseNodePool.__new__(PulseNodePool)
    obj._cold_storage_enabled = True
    obj._cold_dir = cold_dir
    obj._cold_compact_cooldown_until = 0.0
    obj._cold_compact_fail_streak = 0
    return obj


def _write_good_parquet(pdir, fname, node_id):
    t = pa.table({
        "node_id": pa.array([node_id]),
        "evol_level": pa.array(["L1"]),
        "payload": pa.array(["x"]),
    })
    pq.write_table(t, os.path.join(pdir, fname))


def _write_corrupt_parquet(pdir, fname):
    with io.open(os.path.join(pdir, fname), "wb") as _f:
        _f.write(b"this is not a valid parquet file\x00\x00")


class TestColdCompactionFileMissingM63(unittest.TestCase):
    def _tmp_cold(self):
        _base = tempfile.mkdtemp(prefix="m63_cold_", dir=os.path.join(ROOT, "tmp"))
        _pdir = os.path.join(_base, "evol_level=L1")
        os.makedirs(_pdir, exist_ok=True)
        return _base, _pdir

    def test_01_skip_corrupt_parquet_report_count(self):
        _base, _pdir = self._tmp_cold()
        try:
            _write_good_parquet(_pdir, "good1.parquet", "n1")
            _write_good_parquet(_pdir, "good2.parquet", "n2")
            _write_corrupt_parquet(_pdir, "bad1.parquet")
            obj = _mk_pool(_base)
            _table, _skipped = obj._read_cold_partition_tolerant()
            self.assertIsNotNone(_table, "存在可读文件时不应返回 None")
            self.assertEqual(_skipped, 1, "应跳过 1 个损坏文件")
            _rows = _table.to_pylist()
            self.assertEqual(len(_rows), 2, "合并后行数应等于可读文件行数")
            _ids = {_r.get("node_id") for _r in _rows}
            self.assertEqual(_ids, {"n1", "n2"})
        finally:
            _rmtree_robust(_base)

    def test_02_all_corrupt_returns_none(self):
        _base, _pdir = self._tmp_cold()
        try:
            _write_corrupt_parquet(_pdir, "bad1.parquet")
            _write_corrupt_parquet(_pdir, "bad2.parquet")
            obj = _mk_pool(_base)
            _table, _skipped = obj._read_cold_partition_tolerant()
            self.assertIsNone(_table, "全部损坏时应返回 None")
            self.assertEqual(_skipped, 2, "应跳过全部 2 个损坏文件")
        finally:
            _rmtree_robust(_base)

    def test_03_compact_skips_and_reports_skipped_files(self):
        _base, _pdir = self._tmp_cold()
        try:
            _write_good_parquet(_pdir, "good1.parquet", "n1")
            _write_good_parquet(_pdir, "good2.parquet", "n2")
            _write_corrupt_parquet(_pdir, "bad1.parquet")
            obj = _mk_pool(_base)
            _res = obj.compact_cold_storage()
            self.assertTrue(_res.get("success"), f"compaction 应成功: {_res}")
            self.assertIn("skipped_files", _res, "返回应含 skipped_files 透明计数")
            self.assertEqual(_res["skipped_files"], 1, "应报告跳过 1 个损坏文件")
            self.assertLessEqual(_res["after_files"], 1,
                                 "compaction 后小文件应被合并为 1 个")
        finally:
            _rmtree_robust(_base)

    def test_04_source_contains_retry_and_force_delete(self):
        self.assertIn("COLD_COMPACT_DELETE_RETRY_COUNT",
                      SRC, "源码应引入可配重试次数")
        self.assertIn("COLD_COMPACT_DELETE_RETRY_INTERVAL",
                      SRC, "源码应引入可配重试间隔")
        self.assertIn("shutil.rmtree(self._cold_dir, ignore_errors=True)",
                      SRC, "源码应含强制删除兜底分支（ignore_errors）")
        self.assertIn('"skipped_files": _skipped',
                      SRC, "compact 返回应透传 skipped_files")

    def test_05_force_delete_fallback_on_rmtree_failure(self):
        """删旧目录反复失败（模拟 WinError 145）→ 强制删除兜底，compaction 仍成功。"""
        import config as _cfg
        _base, _pdir = self._tmp_cold()
        _orig_n = getattr(_cfg, "COLD_COMPACT_DELETE_RETRY_COUNT", 10)
        _orig_i = getattr(_cfg, "COLD_COMPACT_DELETE_RETRY_INTERVAL", 2.0)
        try:
            _cfg.COLD_COMPACT_DELETE_RETRY_COUNT = 2
            _cfg.COLD_COMPACT_DELETE_RETRY_INTERVAL = 0.01
            _write_good_parquet(_pdir, "good1.parquet", "n1")
            _write_good_parquet(_pdir, "good2.parquet", "n2")
            obj = _mk_pool(_base)
            _real_rmtree = shutil.rmtree

            def _fake_rmtree(path, ignore_errors=False, *a, **k):
                if ignore_errors:
                    # 强制删除分支：真正删掉（模拟 ignore_errors 吞掉 WinError 145）
                    return _real_rmtree(path, ignore_errors=True)
                raise PermissionError("WinError 145: 目录非空（模拟文件锁）")

            from unittest import mock
            with mock.patch("shutil.rmtree", side_effect=_fake_rmtree):
                _res = obj.compact_cold_storage()
            self.assertTrue(_res.get("success"),
                            f"强制删除兜底后 compaction 应成功: {_res}")
            self.assertLessEqual(_res["after_files"], 1)
        finally:
            _cfg.COLD_COMPACT_DELETE_RETRY_COUNT = _orig_n
            _cfg.COLD_COMPACT_DELETE_RETRY_INTERVAL = _orig_i
            _rmtree_robust(_base)

    def test_06_gray_off_single_rmtree(self):
        """灰度关闭 ENABLE_COLD_COMPACT_WINDOWS_RETRY → 单次 rmtree（零回归路径）。"""
        import config as _cfg
        _base, _pdir = self._tmp_cold()
        _orig = getattr(_cfg, "ENABLE_COLD_COMPACT_WINDOWS_RETRY", True)
        try:
            _cfg.ENABLE_COLD_COMPACT_WINDOWS_RETRY = False
            _write_good_parquet(_pdir, "good1.parquet", "n1")
            _write_good_parquet(_pdir, "good2.parquet", "n2")
            obj = _mk_pool(_base)
            _calls = []
            _real_rmtree = shutil.rmtree

            def _spy_rmtree(path, ignore_errors=False, *a, **k):
                _calls.append(ignore_errors)
                return _real_rmtree(path, ignore_errors=ignore_errors)

            from unittest import mock
            with mock.patch("shutil.rmtree", side_effect=_spy_rmtree):
                _res = obj.compact_cold_storage()
            self.assertTrue(_res.get("success"), f"灰度关闭路径应成功: {_res}")
            # 单次 rmtree，且不应出现 ignore_errors=True 的强制删除分支
            self.assertEqual(_calls, [False],
                             f"灰度关闭应仅单次 rmtree，无强制删除兜底: {_calls}")
        finally:
            _cfg.ENABLE_COLD_COMPACT_WINDOWS_RETRY = _orig
            _rmtree_robust(_base)


if __name__ == "__main__":
    unittest.main()
