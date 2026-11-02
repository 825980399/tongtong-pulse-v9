# -*- coding: utf-8 -*-
"""第430批 刀2：pending 池「长期未裁决」超期归档（**不硬删**、可回退、幂等）。

被守护的七条：
  1) 超过阈值（默认 7 天）且 `status=runtime_verified` ⇒ 搬出 pending；
  2) 未超阈值的新件 ⇒ **一条都不动**（不得误伤刚生成的补丁）；
  3) `runtime_failed` / `pending` 等其它状态 ⇒ 完全不碰（`runtime_failed` 有 331刀4 自己的出口）；
  4) `dry_run=True` ⇒ **零写盘**（归档文件不建、pending 不变）；
  5) 归档是"搬"不是"删"：归档行含原行全部字段 + `archived_at` / `archive_reason`；
  6) 幂等：同一 id 重跑不会追加第二行、也不会从 pending 再删一次；
  7) 写盘前留 `*.bak_430_archive_*` 备份（回退只需 copy 回来）。

★全部注入隔离路径（pending / archive / ledger 三路），**绝不碰生产 `data/patches/`**
  —— 331刀4 首版就是漏了 archive 路径注入，把隔离数据写进了生产归档。
"""
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution import patch_execution_dispatch as ped

_DAY = 86400.0


def _row(pid, status="runtime_verified", age_days=10.0, **kw):
    d = {"id": pid, "status": status, "risk_level": "低",
         "created_at": time.time() - age_days * _DAY,
         "file": "organs/body/Demo.py", "method": "m"}
    d.update(kw)
    return d


class TestM430D2StaleArchive(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.pend = os.path.join(self.tmp, "pending_patches.json")
        self.arch = os.path.join(self.tmp, "pending_verified_archive_430.jsonl")
        self.ledger = os.path.join(self.tmp, "fail_ledger.json")

    def tearDown(self):
        self._td.cleanup()

    def _seed(self, rows):
        with open(self.pend, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        return rows

    def _read_pend(self):
        with open(self.pend, encoding="utf-8") as f:
            return json.load(f)

    def _read_arch(self):
        if not os.path.isfile(self.arch):
            return []
        out = []
        with open(self.arch, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    out.append(json.loads(ln))
        return out

    def _run(self, **kw):
        return ped.archive_stale_pending(
            pending_path=self.pend, archive_path=self.arch,
            ledger_path=self.ledger, ttl_sec=7 * _DAY, **kw)

    def test_01_over_ttl_is_archived(self):
        """核心：超过 7 天 ⇒ 搬出 pending 并落进归档。"""
        self._seed([_row("old1", age_days=9.5)])
        r = self._run(dry_run=False)
        self.assertEqual(r["scanned"], 1)
        self.assertEqual(r["aged_out"], 1)
        self.assertEqual(r["archived_ids"], ["old1"])
        self.assertEqual(self._read_pend(), [], "超期件应从 pending 移除")
        self.assertEqual([x["id"] for x in self._read_arch()], ["old1"])

    def test_02_under_ttl_is_kept(self):
        """核心：未超阈值 ⇒ 一条都不动（不得误伤刚生成的补丁）。"""
        self._seed([_row("fresh", age_days=0.9)])
        r = self._run(dry_run=False)
        self.assertEqual(r["aged_out"], 0)
        self.assertEqual(r["keep_ids"], ["fresh"])
        self.assertEqual(len(self._read_pend()), 1)
        self.assertEqual(self._read_arch(), [])

    def test_03_other_statuses_untouched(self):
        """`runtime_failed` / `pending` / `approved` 一概不碰（331刀4 有自己出口）。"""
        rows = [_row("rf", status="runtime_failed", age_days=30),
                _row("pd", status="pending", age_days=30),
                _row("ap", status="approved", age_days=30),
                _row("old", status="runtime_verified", age_days=30)]
        self._seed(rows)
        r = self._run(dry_run=False)
        self.assertEqual(r["scanned"], 1, "只有 runtime_verified 进扫描面")
        self.assertEqual(r["archived_ids"], ["old"])
        _left = {x["id"] for x in self._read_pend()}
        self.assertEqual(_left, {"rf", "pd", "ap"})

    def test_04_dry_run_writes_nothing(self):
        """默认 dry_run ⇒ 零写盘（生产调用前先出结论）。"""
        rows = self._seed([_row("old", age_days=20)])
        r = self._run(dry_run=True)
        self.assertEqual(r["aged_out"], 1, "dry_run 也要报数")
        self.assertFalse(r["written"])
        self.assertFalse(os.path.isfile(self.arch), "dry_run 不得建归档文件")
        self.assertEqual(self._read_pend(), rows, "dry_run 不得改 pending")

    def test_05_archive_row_is_full_copy(self):
        """归档是"搬"：原行字段全留 + 归档三字段。"""
        self._seed([_row("old", age_days=10, trust_score=75)])
        self._run(dry_run=False)
        _a = self._read_arch()[0]
        for k in ("id", "status", "risk_level", "file", "method", "created_at",
                  "trust_score"):
            self.assertIn(k, _a, f"归档行缺原字段 {k}")
        self.assertEqual(_a["trust_score"], 75)
        self.assertIn("archived_at", _a)
        self.assertIn("archived_by", _a)
        self.assertIn("archive_reason", _a)

    def test_06_idempotent_second_run(self):
        """幂等：同一条重跑不追加第二行、不报第二次归档。"""
        self._seed([_row("old", age_days=10)])
        self._run(dry_run=False)
        r2 = self._run(dry_run=False)
        self.assertEqual(r2["aged_out"], 0)
        self.assertEqual(len(self._read_arch()), 1, "不得追加重复归档行")

    def test_07_backup_file_created(self):
        """写盘前留备份（回退只需 copy 回来）。"""
        self._seed([_row("old", age_days=10)])
        r = self._run(dry_run=False)
        _b = r.get("backup") or ""
        self.assertTrue(_b.startswith(self.pend), f"备份路径异常: {_b}")
        self.assertTrue(os.path.isfile(_b), "备份文件必须真的存在")
        with open(_b, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 1, "备份应是归档前的内容")

    def test_08_no_timestamp_is_conservative(self):
        """没有时间戳 ⇒ 不可判定 ⇒ **保留不归档**（宁留勿删）。"""
        self._seed([{"id": "nots", "status": "runtime_verified"}])
        r = self._run(dry_run=False)
        self.assertEqual(r["aged_out"], 0)
        self.assertEqual(r["keep_ids"], ["nots"])
        self.assertEqual(len(self._read_pend()), 1)

    def test_09_ttl_from_config_default_7_days(self):
        """阈值来自 config（默认 7 天），不是硬编码。"""
        self.assertAlmostEqual(ped._m430_archive_ttl_sec() / _DAY, 7.0, places=3)

    def test_10_switch_default_on(self):
        """开关默认开（任务书刀2 裁决"开"）。"""
        self.assertTrue(ped._m430_archive_enabled())


if __name__ == "__main__":
    unittest.main()
