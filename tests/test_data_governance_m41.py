# -*- coding: utf-8 -*-
"""第41批 T2 门控测试：数据治理（.corrupted 清理 + 知识备份轮转）。

★测试隔离：模块级路径常量全部改指 ``tempfile.mkdtemp()`` —— **绝不碰生产 data/**。
"""
import gzip
import importlib
import io
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

_gov = importlib.import_module("tools.data_governance_m41")


def _pytest_tmp_root():
    """★主线第46批 T4（P2-301/307）：测试隔离目录改用【项目根/.pytest_tmp/】。

    背景：旧实现 ``mkdtemp(prefix=..., dir=<项目>/tmp)`` 会在项目 ``tmp/`` 下
    持续留下隔离目录（``m41_t2_*`` / ``m43dq_*`` / ``_m27_*`` ...），tearDown
    清不干净时就变成"历史残留"，并与 tmp 清理工具互相打架。

    现改为项目根下的 ``.pytest_tmp/``：
      * **与项目同盘** —— Windows 跨盘 ``shutil.move`` 会 copy+unlink，
        触发沙箱删除配额（m41 实测教训），故不能用 ``tempfile.gettempdir()``
      * 不在 ``tmp/`` 下 → tmp 清理判据无需再为隔离目录开特例
      * 已纳入 ``.gitignore``
      * 可用环境变量 ``PULSE_TEST_TMP_ROOT`` 覆盖（调试/隔离用）
    """
    _env = os.environ.get("PULSE_TEST_TMP_ROOT")
    if _env:
        _d = _env
    else:
        _d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".pytest_tmp")
    os.makedirs(_d, exist_ok=True)
    return _d


class _Base(unittest.TestCase):
    def setUp(self):
        # ★与项目同盘（Windows 跨盘 shutil.move 会 copy+unlink → 触发沙箱删除配额）
        self._root = tempfile.mkdtemp(
            prefix="m41_t2_", dir=_pytest_tmp_root())
        self._data = os.path.join(self._root, "data")
        os.makedirs(os.path.join(self._data, "corrupted"), exist_ok=True)
        os.makedirs(os.path.join(self._data, "knowledge"), exist_ok=True)
        # 重定向模块级常量
        self._saved = (_gov._DATA, _gov._QUAR, _gov._ARCH, _gov._MANIFEST)
        _gov._DATA = self._data
        _gov._QUAR = os.path.join(self._data, "_quarantine", "corrupted")
        _gov._ARCH = os.path.join(self._data, "_archive", "knowledge_backups")
        _gov._MANIFEST = os.path.join(self._data, "_quarantine", "manifest.json")
        self._orig_keep = getattr(config, "KNOWLEDGE_BACKUP_KEEP", 3)
        self._orig_rot = getattr(config, "ENABLE_KNOWLEDGE_BACKUP_ROTATION", True)

    def tearDown(self):
        (_gov._DATA, _gov._QUAR, _gov._ARCH, _gov._MANIFEST) = self._saved
        config.KNOWLEDGE_BACKUP_KEEP = self._orig_keep
        config.ENABLE_KNOWLEDGE_BACKUP_ROTATION = self._orig_rot
        shutil.rmtree(self._root, ignore_errors=True)

    def _mk_corrupted(self, name, payload="x", origin_file=None):
        _cd = os.path.join(self._data, "corrupted")
        with io.open(os.path.join(_cd, name), "w", encoding="utf-8") as f:
            f.write(payload)
        if origin_file:
            with io.open(os.path.join(_cd, origin_file), "w", encoding="utf-8") as f:
                f.write("intact")

    def _mk_backup(self, name, size=200, age=0.0):
        _kd = os.path.join(self._data, "knowledge")
        _p = os.path.join(_kd, name)
        with io.open(_p, "w", encoding="utf-8") as f:
            f.write("a" * size)
        _t = time.time() - age
        os.utime(_p, (_t, _t))
        return _p


class TestOriginName(unittest.TestCase):
    def test_01_parses_timestamp(self):
        self.assertEqual(
            _gov.origin_name("bad2.json.20260910_174016.corrupted"),
            "bad2.json")

    def test_02_no_timestamp(self):
        self.assertEqual(_gov.origin_name("plain.json.corrupted"), "plain.json")

    def test_03_unknown_form(self):
        self.assertEqual(_gov.origin_name("weird.corrupted"), "weird")


class TestCorruptedGovernance(_Base):
    def test_10_scan(self):
        self._mk_corrupted("a.json.20260101_000000.corrupted")
        self._mk_corrupted("b.json.20260101_000000.corrupted")
        self.assertEqual(len(_gov.scan_corrupted(self._data)), 2)

    def test_11_no_counterpart_quarantined(self):
        self._mk_corrupted("a.json.20260101_000000.corrupted")
        _r = _gov.govern_corrupted()
        self.assertEqual(_r["total"], 1)
        self.assertEqual(_r["deleted"], 0)
        self.assertEqual(_r["quarantined"], 1)
        self.assertTrue(os.path.isfile(
            os.path.join(_gov._QUAR, "a.json.20260101_000000.corrupted")))
        self.assertTrue(os.path.isfile(_gov._MANIFEST))
        # 源目录已空
        self.assertEqual(os.listdir(os.path.join(self._data, "corrupted")), [])

    def test_12_with_counterpart_deleted(self):
        self._mk_corrupted("a.json.20260101_000000.corrupted",
                           origin_file="a.json")
        _r = _gov.govern_corrupted()
        self.assertEqual(_r["deleted"], 1)
        self.assertEqual(_r["quarantined"], 0)
        self.assertFalse(os.path.isfile(
            os.path.join(self._data, "corrupted", "a.json.20260101_000000.corrupted")))
        # 完好副本保留
        self.assertTrue(os.path.isfile(
            os.path.join(self._data, "corrupted", "a.json")))

    def test_13_dry_run_no_change(self):
        self._mk_corrupted("a.json.20260101_000000.corrupted")
        _r = _gov.govern_corrupted(dry=True)
        self.assertEqual(_r["quarantined"], 1)
        self.assertTrue(os.path.isfile(
            os.path.join(self._data, "corrupted",
                         "a.json.20260101_000000.corrupted")))
        self.assertFalse(os.path.isdir(_gov._QUAR))

    def test_14_idempotent(self):
        self._mk_corrupted("a.json.20260101_000000.corrupted")
        _gov.govern_corrupted()
        _r2 = _gov.govern_corrupted()          # 第二次：源已空
        self.assertEqual(_r2["total"], 0)
        self.assertEqual(_r2["quarantined"], 0)

    def test_15_manifest_accumulates(self):
        self._mk_corrupted("a.json.20260101_000000.corrupted")
        _gov.govern_corrupted()
        self._mk_corrupted("b.json.20260101_000000.corrupted")
        _gov.govern_corrupted()
        import json
        _m = json.load(io.open(_gov._MANIFEST, encoding="utf-8"))
        self.assertEqual(len(_m), 2)


class TestBackupRotation(_Base):
    def test_20_scan_sorted_desc(self):
        self._mk_backup("s.json.1.bak", age=300)
        self._mk_backup("s.json.2.bak", age=200)
        self._mk_backup("s.json.3.bak", age=100)
        _rows = _gov.scan_knowledge_backups(os.path.join(self._data, "knowledge"))
        self.assertEqual(len(_rows), 3)
        self.assertIn("s.json.3.bak", _rows[0][1])   # 最新在前

    def test_21_keep_n_archives_rest(self):
        config.KNOWLEDGE_BACKUP_KEEP = 2
        for _i, _age in enumerate((500, 400, 300, 200, 100)):
            self._mk_backup("s.json.%d.bak" % _i, age=_age)
        _r = _gov.govern_backups()
        self.assertEqual(_r["keep"], 2)
        self.assertEqual(_r["total"], 5)
        self.assertEqual(_r["archived"], 3)
        _kd = os.path.join(self._data, "knowledge")
        self.assertEqual(len([f for f in os.listdir(_kd) if f.endswith(".bak")]), 2)
        _gz = [f for f in os.listdir(_gov._ARCH) if f.endswith(".gz")]
        self.assertEqual(len(_gz), 3)

    def test_22_gz_content_recoverable(self):
        config.KNOWLEDGE_BACKUP_KEEP = 1
        self._mk_backup("old.bak", size=64, age=999)
        self._mk_backup("new.bak", age=1)
        _gov.govern_backups()
        _gz = os.path.join(_gov._ARCH, "old.bak.gz")
        self.assertTrue(os.path.isfile(_gz))
        with gzip.open(_gz, "rb") as f:
            _content = f.read()
        self.assertEqual(len(_content), 64)

    def test_23_idempotent_when_gz_exists(self):
        config.KNOWLEDGE_BACKUP_KEEP = 1
        self._mk_backup("a.bak", age=10)
        _r1 = _gov.govern_backups()          # 1 份备份，keep=1 → 不归档
        self.assertEqual(_r1["archived"], 0)
        self._mk_backup("b.bak", age=20)     # 新增更旧的一份 → 归档它
        _r2 = _gov.govern_backups()
        self.assertEqual(_r2["archived"], 1)
        # 同名再来一次 → 已有 .gz → 跳过（幂等）
        self._mk_backup("b.bak", age=20)
        _r3 = _gov.govern_backups()
        self.assertEqual(_r3["archived"], 0)

    def test_24_zero_falls_back_to_default(self):
        """★0/空 视为"未配置" → 用默认 3（而非删光）。"""
        config.KNOWLEDGE_BACKUP_KEEP = 0
        self._mk_backup("a.bak", age=10)
        _r = _gov.govern_backups()
        self.assertEqual(_r["keep"], 3)

    def test_25_negative_clamped_to_one(self):
        config.KNOWLEDGE_BACKUP_KEEP = -5
        self._mk_backup("a.bak", age=10)
        _r = _gov.govern_backups()
        self.assertGreaterEqual(_r["keep"], 1)


class TestDryRunMain(_Base):
    def test_30_dry_run_returns_zero(self):
        self._mk_corrupted("a.json.20260101_000000.corrupted")
        self._mk_backup("s.bak", age=10)
        self.assertEqual(_gov.main(["--dry-run"]), 0)
        self.assertFalse(os.path.isdir(_gov._QUAR))

    def test_31_module_importable(self):
        self.assertTrue(callable(_gov.main))
        self.assertTrue(callable(_gov.govern_corrupted))
        self.assertTrue(callable(_gov.govern_backups))


if __name__ == "__main__":
    unittest.main()
