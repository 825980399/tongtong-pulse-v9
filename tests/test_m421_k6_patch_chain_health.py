"""421 刀6：patch_chain 四字段进 health_snapshot。

被守护的五条：
  1) 四字段口径正确（本地日切的 [00:00, 次日00:00)）；
  2) ★「0 条」与「读不到」分开：读不到写 `None` + `state`，绝不写 0 冒充；
  3) 60s TTL 缓存生效，且命中/过期可区分（`cache` 标记）；
  4) 缓存对象是**副本**，调用方改返回值不会污染缓存；
  5) 边界的昨天 23:59:59 不算今天。

★全部用 `root=<tmp>` 隔离，不读生产 `data/`。
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

from organs.core import PulseMetricsCollector as pmc


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        for _d in (('data', 'patches'), ('data', 'evolution')):
            os.makedirs(os.path.join(self.tmp, *_d), exist_ok=True)
        pmc._m421_pc_reset_cache()
        self.lo, self.hi = pmc._m421_today_bounds()

    def tearDown(self):
        pmc._m421_pc_reset_cache()
        self._td.cleanup()

    def _write(self, rel, obj):
        p = os.path.join(self.tmp, *rel.split('/'))
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)

    def _read(self):
        return pmc._m421_patch_chain_read(root=self.tmp)


class TestK6Fields(_Base):
    def test_01_counts_today(self):
        self._write('data/patches/patch_history.json', [
            {"id": "a", "generated_at": self.lo + 10, "applied_at": self.lo + 20},
            {"id": "b", "generated_at": self.lo + 100},
            {"id": "c", "generated_at": self.lo - 1},        # 昨天最后一秒
            {"id": "d", "generated_at": self.hi + 1},        # 明天
            {"id": "e"},                                      # 两字段都缺
            {"id": "f", "generated_at": "not-a-number"},     # 类型不对
        ])
        self._write('data/patches/pending_patches.json', [{"id": "p1"}, {"id": "p2"}])
        self._write('data/evolution/verify_feedback_ledger.json',
                    {"schema": "x", "applied": {
                        "a": self.lo + 5,
                        "b": {"at": self.lo + 6},
                        "c": self.lo - 5,
                    }})
        d = self._read()
        self.assertEqual(d["gen"], 2, '今天生成应为 2（a、b）')
        self.assertEqual(d["apply"], 1, '今天应用应为 1（a）')
        self.assertEqual(d["queue"], 2)
        self.assertEqual(d["ledger"], 2, '今天落账应为 2（a、b）')
        self.assertEqual(d["state"], "ok")
        self.assertEqual(d["day"], time.strftime("%Y-%m-%d", time.localtime(self.lo)))

    def test_02_last_ts_is_max(self):
        self._write('data/patches/patch_history.json', [
            {"id": "a", "generated_at": 100.0, "applied_at": 50.0},
            {"id": "b", "generated_at": 300.0, "applied_at": 70.0},
        ])
        d = self._read()
        self.assertEqual(d["last_gen_at"], 300.0)
        self.assertEqual(d["last_apply_at"], 70.0)

    def test_03_missing_files_are_none_not_zero(self):
        """★核心：文件不在 ⇒ None + unavailable，不是 0。"""
        d = self._read()
        for _f in pmc._M421_PC_FIELDS:
            self.assertIsNone(
                d[_f],
                f'{_f} 在源缺失时应为 None（写 0 会伪装成"真的没有"）')
        self.assertEqual(d["state"], "unavailable")
        self.assertIn("patch_history", d["errors"])

    def test_04_partial_state(self):
        self._write('data/patches/pending_patches.json', [{"id": "p"}])
        d = self._read()
        self.assertEqual(d["queue"], 1)
        self.assertIsNone(d["gen"])
        self.assertEqual(d["state"], "partial")

    def test_05_bad_json_is_none_not_zero(self):
        p = os.path.join(self.tmp, 'data', 'patches', 'patch_history.json')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('{ broken json ')
        d = self._read()
        self.assertIsNone(d["gen"])


class TestK6Cache(_Base):
    def test_06_cache_hit_after_first(self):
        self._write('data/patches/pending_patches.json', [{"id": "p"}])
        a = pmc._m421_patch_chain_snapshot(root=self.tmp)
        b = pmc._m421_patch_chain_snapshot(root=self.tmp)
        self.assertEqual(a["cache"], "miss")
        self.assertEqual(b["cache"], "hit")
        self.assertEqual(a["queue"], b["queue"])

    def test_07_cache_expires_with_ttl_zero(self):
        self._write('data/patches/pending_patches.json', [{"id": "p"}])
        pmc._m421_patch_chain_snapshot(root=self.tmp)
        # ttl=0 ⇒ 每次都是 miss
        c = pmc._m421_patch_chain_snapshot(root=self.tmp, ttl=0.0)
        self.assertEqual(c["cache"], "miss")

    def test_08_cache_picks_up_change_after_expiry(self):
        self._write('data/patches/pending_patches.json', [{"id": "p"}])
        a = pmc._m421_patch_chain_snapshot(root=self.tmp)
        self.assertEqual(a["queue"], 1)
        # 改文件但缓存还在
        self._write('data/patches/pending_patches.json', [{"id": "p"}, {"id": "q"}])
        b = pmc._m421_patch_chain_snapshot(root=self.tmp)
        self.assertEqual(b["queue"], 1, 'TTL 内应仍读缓存')
        # 过期后应看到新值
        c = pmc._m421_patch_chain_snapshot(root=self.tmp, ttl=0.0)
        self.assertEqual(c["queue"], 2, '过期后必须看到新值')

    def test_09_returned_dict_is_copy(self):
        """改返回值不能污染缓存。"""
        self._write('data/patches/pending_patches.json', [{"id": "p"}])
        a = pmc._m421_patch_chain_snapshot(root=self.tmp)
        a["queue"] = 999
        b = pmc._m421_patch_chain_snapshot(root=self.tmp)
        self.assertEqual(b["queue"], 1, '缓存被调用方改掉了')


class TestK6Bounds(unittest.TestCase):
    def test_10_today_bounds_is_local_day(self):
        lo, hi = pmc._m421_today_bounds()
        self.assertAlmostEqual(hi - lo, 86400.0, places=3)
        _tm = time.localtime(lo)
        self.assertEqual((_tm.tm_hour, _tm.tm_min, _tm.tm_sec), (0, 0, 0))

    def test_11_count_excludes_upper_bound(self):
        lo, hi = pmc._m421_today_bounds()
        rows = [{"t": lo}, {"t": hi - 0.001}, {"t": hi}]
        self.assertEqual(pmc._m421_count_today(rows, "t", lo, hi), 2,
                         '右界应是开区间（否则会和明天重复计数）')


if __name__ == '__main__':
    unittest.main()
