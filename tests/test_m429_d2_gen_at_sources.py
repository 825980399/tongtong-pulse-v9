# -*- coding: utf-8 -*-
"""第429批 刀2：`last_gen_at` 取「history ∪ pending」两源 max。

被守护的四条：
  1) 只有 history 有值时，`last_gen_at` 仍等于 history 的值（**旧契约不破**）；
  2) 只有 pending 有值时（本批实测的生产形态），`last_gen_at` 能反映 pending 的新时间
     —— 这是「批处理链停摆」错觉的修复点；
  3) 两源都有值时取较大者，且 `gen_seen_source` 指明是谁；
  4) 两源都读不到 ⇒ `None`，**不填 0**（铁律 156）。

★全部用 `root=<tmp>` 隔离，不读生产 `data/`。
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from organs.core import PulseMetricsCollector as pmc


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        for _d in (("data", "patches"), ("data", "evolution")):
            os.makedirs(os.path.join(self.tmp, *_d), exist_ok=True)
        pmc._m421_pc_reset_cache()

    def tearDown(self):
        pmc._m421_pc_reset_cache()
        self._td.cleanup()

    def _write(self, rel, obj):
        p = os.path.join(self.tmp, *rel.split("/"))
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def _read(self):
        return pmc._m421_patch_chain_read(root=self.tmp)


class TestM429D2GenAtSources(_Base):
    def test_01_history_only_keeps_old_contract(self):
        """旧契约：没有 pending 文件时，`last_gen_at` 就是 history 的 max。"""
        self._write("data/patches/patch_history.json", [
            {"id": "a", "generated_at": 100.0},
            {"id": "b", "generated_at": 300.0},
        ])
        d = self._read()
        self.assertEqual(d["last_gen_at"], 300.0)
        self.assertEqual(d["last_gen_at_history"], 300.0)
        self.assertIsNone(d["last_gen_at_pending"])
        self.assertEqual(d["gen_seen_source"], "history")

    def test_02_pending_wins_when_newer(self):
        """★核心：补丁只进了 pending、没进 history 时，必须能看见它的时间。

        这正是 2026-11-02 的生产形态：history 停在 10-22，pending 有 11-02 的补丁。
        改前 `last_gen_at` 读成 10-22 ⇒ 误判「两周没生成」。
        """
        self._write("data/patches/patch_history.json", [
            {"id": "old", "generated_at": 100.0},
        ])
        self._write("data/patches/pending_patches.json", [
            {"id": "n1", "generated_at": 50.0},
            {"id": "n2", "generated_at": 900.0},
        ])
        d = self._read()
        self.assertEqual(d["last_gen_at"], 900.0, "应取 pending 的较新时间")
        self.assertEqual(d["last_gen_at_pending"], 900.0)
        self.assertEqual(d["gen_seen_source"], "pending")
        self.assertEqual(d["queue"], 2)

    def test_03_pending_falls_back_to_created_saved(self):
        """pending 条目没有 generated_at 时，按 created_at → saved_at 回退。"""
        self._write("data/patches/patch_history.json", [])
        self._write("data/patches/pending_patches.json", [
            {"id": "n1", "saved_at": 20.0},
            {"id": "n2", "created_at": 777.0},
        ])
        d = self._read()
        self.assertEqual(d["last_gen_at"], 777.0)
        self.assertEqual(d["gen_seen_source"], "pending")

    def test_04_both_missing_is_none_not_zero(self):
        """两源都读不到 ⇒ None（不是 0，0 会伪装成「真的没生成过」）。"""
        d = self._read()
        self.assertIsNone(d["last_gen_at"])
        self.assertIsNone(d["gen_seen_source"])
        self.assertEqual(d["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
