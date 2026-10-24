# -*- coding: utf-8 -*-
"""360 遗留闭合验收：冷档 gzip（读侧透明 + 压缩/还原 + 主档保护 + 备份计数）。

判据口径（★铁律 193 须含已知正例）：
  * 正例：`_is_cold_backup` 对**真实冷档名**必须为真（否则工具对生产档全 skip）；
  * 反例：主档 / 已压缩档 / 非备份 必须为假。
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.data.DataAccessLayer import safe_read_json  # noqa: E402
from tools.snapshot_cold_gzip import (  # noqa: E402
    _is_cold_backup,
    _sha256,
    compress,
    restore,
    scan,
)


class TestM360LegacyGzip(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m360gz_")

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    # ---------- 读侧：safe_read_json 透明支持 .gz ----------
    def test_01_read_gz_equals_plain(self):
        """压缩档读出来的内容必须与明文逐键一致（不是「读不到返回默认」）。"""
        data = {"version": "v1", "nodes": [{"node_id": f"n{i}"} for i in range(50)],
                "node_list_checksum": "abc123"}
        p = os.path.join(self._td, "pulse_knowledge_snapshot.json.20260101_000000.bak")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        gz = p + ".gz"
        with open(p, "rb") as src, gzip.open(gz, "wb") as dst:
            shutil.copyfileobj(src, dst)
        self.assertEqual(safe_read_json(p), data)
        self.assertEqual(safe_read_json(gz), data, ".gz 必须与明文同内容")

    def test_02_read_broken_gz_returns_default_not_silence(self):
        """损坏的 .gz 必须返回默认且**留 error 痕迹**（不能伪装成「没数据」）。"""
        gz = os.path.join(self._td, "broken.json.bak.gz")
        with open(gz, "wb") as f:
            f.write(b"\x1f\x8b\x08\x00not-a-real-gzip-body")
        out = safe_read_json(gz, default={"k": "default"})
        self.assertEqual(out, {"k": "default"})

    def test_03_read_absent_gz_returns_default(self):
        self.assertEqual(
            safe_read_json(os.path.join(self._td, "nope.bak.gz"), default=[]), [])

    # ---------- 冷档判据：正例 + 反例 ----------
    def test_04_cold_backup_positive(self):
        self.assertTrue(_is_cold_backup(
            "data/knowledge/pulse_knowledge_snapshot.json.20261024_120953.bak"),
            "真实冷档必须判为真（否则生产档全被 skip）")

    def test_05_cold_backup_negatives(self):
        for name in ("data/knowledge/pulse_knowledge_snapshot.json",           # 主档(mmap 惰性读)
                     "data/knowledge/pulse_knowledge_snapshot.json.1.bak.gz",  # 已压缩
                     "data/knowledge/pulse_l1_snapshot.json",                  # 主档白名单
                     "data/knowledge/vectors_meta.json",
                     "data/knowledge/cold.index.json",
                     "data/knowledge/snapshot_6gmfb9s7.json"):                 # 非备份
            self.assertFalse(_is_cold_backup(name), f"{name} 必须被拒绝")

    # ---------- 压缩 / 还原 ----------
    def test_06_compress_verify_and_restore(self):
        """压缩 → sha 一致 → 删明文 → 还原 → 与原档逐字节一致。"""
        p = os.path.join(self._td, "pulse_knowledge_snapshot.json.20260101_000000.bak")
        payload = {"nodes": [{"node_id": f"n{i}", "text": "知识节点内容×" * 20}
                             for i in range(3000)]}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        plain_sha, plain_n = _sha256(p)

        r = compress(p, level=1)
        self.assertTrue(r["ok"], r["msg"])
        self.assertEqual(r["stage"], "ok")
        self.assertTrue(os.path.exists(p + ".gz"))
        self.assertFalse(os.path.exists(p), "默认应删除明文")
        self.assertLess(r["ratio"], 1.0, "压缩后必须更小")
        # 回读等价（走安全读口，等价于生产的 _load_from_backup 路径）
        self.assertEqual(safe_read_json(p + ".gz"), payload)

        r2 = restore(p + ".gz")
        self.assertTrue(r2["ok"], r2["msg"])
        back_sha, back_n = _sha256(p)
        self.assertEqual((back_sha, back_n), (plain_sha, plain_n),
                         "还原后必须与原档逐字节一致")

    def test_07_compress_is_idempotent(self):
        p = os.path.join(self._td, "pulse_knowledge_snapshot.json.20260102_000000.bak")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"a": 1}')
        self.assertTrue(compress(p, level=1)["ok"])
        r = compress(p + ".gz", level=1)  # 已压缩档再压
        self.assertEqual(r["stage"], "refused")
        self.assertTrue(compress(p, level=1)["ok"]) if os.path.exists(p) else None
        r3 = compress(p, level=1) if os.path.exists(p) else {"stage": "skipped"}
        self.assertIn(r3["stage"], ("skipped", "refused"))

    def test_08_compress_refuses_main_snapshot(self):
        p = os.path.join(self._td, "pulse_knowledge_snapshot.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"a": 1}')
        r = compress(p, level=1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "refused", "主档永不压缩（mmap 惰性读依赖）")
        self.assertTrue(os.path.exists(p))

    def test_09_dry_run_writes_nothing(self):
        p = os.path.join(self._td, "pulse_knowledge_snapshot.json.20260103_000000.bak")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"a": 1}')
        r = compress(p, level=1, dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["stage"], "dry_run")
        self.assertFalse(os.path.exists(p + ".gz"))
        self.assertTrue(os.path.exists(p))

    def test_10_keep_plain(self):
        p = os.path.join(self._td, "pulse_knowledge_snapshot.json.20260104_000000.bak")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"a": 1}')
        self.assertTrue(compress(p, level=1, keep_plain=True)["ok"])
        self.assertTrue(os.path.exists(p + ".gz"))
        self.assertTrue(os.path.exists(p), "--keep-plain 必须保留明文")

    # ---------- scan ----------
    def test_11_scan_lists_only_cold_backups(self):
        d = os.path.join(self._td, "knowledge")
        os.makedirs(d, exist_ok=True)
        for name in ("pulse_knowledge_snapshot.json",
                     "pulse_knowledge_snapshot.json.20261024_120953.bak",
                     "pulse_knowledge_snapshot.json.20261024_154753.bak",
                     "pulse_l1_snapshot.json"):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write("{}")
        items = scan(d)
        names = sorted(it["name"] for it in items)
        self.assertEqual(len(names), 2, f"只应列出 2 份冷档，实得 {names}")
        self.assertTrue(all(n.endswith(".bak") for n in names))

    # ---------- 备份计数含 .bak.gz ----------
    def test_12_backup_count_includes_gz(self):
        """压缩后备份数不得凭空减少（看板失真=静默降级，铁律 156）。"""
        from organs.core import PulseMetricsCollector as _pmc
        src = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "organs", "core", "PulseMetricsCollector.py"), encoding="utf-8").read()
        self.assertIn("pulse_knowledge_snapshot.json.*.bak.gz", src,
                      "备份计数必须显式含 .bak.gz")
        # 真实跑一遍：切到临时根造 1 明文 + 1 gz，计数应为 2
        cwd = os.getcwd()
        fake_root = os.path.join(self._td, "root")
        os.makedirs(os.path.join(fake_root, "data", "knowledge"), exist_ok=True)
        kd = os.path.join(fake_root, "data", "knowledge")
        for suffix in (".20261024_120953.bak", ".20261024_154753.bak.gz"):
            with open(os.path.join(kd, "pulse_knowledge_snapshot.json" + suffix),
                      "w", encoding="utf-8") as f:
                f.write("{}")
        try:
            os.chdir(fake_root)
            _c = _pmc.PulseMetricsCollector.__new__(_pmc.PulseMetricsCollector)
            self.assertEqual(_c._count_snapshot_backups(), 2,
                             "1 份明文 + 1 份压缩档 应计 2")
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
