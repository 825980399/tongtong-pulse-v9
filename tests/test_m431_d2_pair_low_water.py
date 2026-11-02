# -*- coding: utf-8 -*-
"""第431批 刀2：pairs 裁切加**低水位线**。

背景（T0 实测，推翻任务书「裁切没生效」）：`logs/pulse.log` 当日 trim
**1349 次**，21/22/23 点各 360/445/442 次（≈7.4 次/分钟）。每次
`_trim_pair_file` 要 readlines(148MB)+备份(148MB)+写 tmp(148MB)+replace
⇒ 约 450MB IO／次，稳态 65 GB/小时。文件本身一直卡在 50 万行，
**裁切是生效的**，问题是"刚好超几行就整体重写"这个判据太紧。

被守护的七条：
  1) 超上限 ⇒ 裁到 **cap × ratio**（默认 45 万），不是裁回 cap；
  2) 未超上限 ⇒ 一行都不动（不重写、不备份）；
  3) 裁掉的是**最老的**（保留最后 N 行，首行 seq 可验证）；
  4) 备份在裁前生成且**内容等于裁前全量**；
  5) ratio 非法（bool/0/负/>1/缺失）⇒ 回落 0.9；
  6) cap 很小时低水位仍**严格小于 cap**（否则裁完还超，等于没修）；
  7) `pulse_pair_trim_low_ratio` 真从 `RUNTIME_PARAMS` 取。
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from utils import pulse_tracer as pt


def _write_pairs(path, n):
    with open(path, "w", encoding="utf-8") as _f:
        _f.writelines(json.dumps({"seq": _i, "organ": "x"}) + "\n" for _i in range(n))


def _lines(path):
    with open(path, "r", encoding="utf-8") as _f:
        return _f.read().splitlines()


class TestM431D2LowWater(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.p = os.path.join(self.tmp, "pulse_pairs_20260101.jsonl")
        self._rp = dict(config.RUNTIME_PARAMS)
        config.RUNTIME_PARAMS["pulse_pair_file_cap"] = 100
        config.RUNTIME_PARAMS["pulse_pair_trim_low_ratio"] = 0.9
        pt._PAIR_LINE_STATE["path"] = None
        pt._PAIR_LINE_STATE["lines"] = 0

    def tearDown(self):
        config.RUNTIME_PARAMS.clear()
        config.RUNTIME_PARAMS.update(self._rp)
        pt._PAIR_LINE_STATE["path"] = None
        pt._PAIR_LINE_STATE["lines"] = 0
        self._td.cleanup()

    # ---- 1) 超上限裁到低水位 ----
    def test_01_trims_to_low_water_not_cap(self):
        _write_pairs(self.p, 130)
        pt._trim_pair_file(self.p)
        self.assertEqual(len(_lines(self.p)), 90,
                         "130 行应裁到 cap×0.9=90，不是裁回 cap=100")

    # ---- 2) 未超上限不动 ----
    def test_02_no_trim_when_under_cap(self):
        _write_pairs(self.p, 100)
        _before = os.stat(self.p).st_mtime_ns
        pt._trim_pair_file(self.p)
        self.assertEqual(len(_lines(self.p)), 100)
        self.assertEqual(os.stat(self.p).st_mtime_ns, _before, "未超限不得重写文件")
        self.assertFalse(os.path.exists(self.p + ".trim.bak"), "未超限不得生成备份")

    # ---- 3) 裁掉最老的 ----
    def test_03_drops_oldest(self):
        _write_pairs(self.p, 130)
        pt._trim_pair_file(self.p)
        _kept = [json.loads(x)["seq"] for x in _lines(self.p)]
        self.assertEqual(_kept[0], 40, "130 行裁到 90 ⇒ 首行应是 seq=40")
        self.assertEqual(_kept[-1], 129, "末行必须是最新的 seq=129")

    # ---- 4) 备份 = 裁前全量 ----
    def test_04_backup_equals_pre_trim(self):
        _write_pairs(self.p, 130)
        _pre = _lines(self.p)
        pt._trim_pair_file(self.p)
        _bk = self.p + ".trim.bak"
        self.assertTrue(os.path.exists(_bk), "裁前必须留备份")
        self.assertEqual(_lines(_bk), _pre, "备份内容必须等于裁前全量")

    # ---- 5) ratio 非法回落 ----
    def test_05_ratio_invalid_falls_back(self):
        for _bad in (0, -1, 1.5, True, "0.5", None):
            config.RUNTIME_PARAMS["pulse_pair_trim_low_ratio"] = _bad
            self.assertAlmostEqual(pt._pair_trim_low_ratio(), 0.9, places=6,
                                   msg=f"非法 ratio={_bad!r} 必须回落 0.9")
        config.RUNTIME_PARAMS.pop("pulse_pair_trim_low_ratio", None)
        self.assertAlmostEqual(pt._pair_trim_low_ratio(), 0.9, places=6,
                               msg="缺键必须回落 0.9")

    # ---- 6) 小 cap 下低水位严格小于 cap ----
    def test_06_low_water_strictly_below_cap(self):
        for _cap in (2, 3, 5, 10):
            config.RUNTIME_PARAMS["pulse_pair_file_cap"] = _cap
            _low = pt._pair_trim_low_water()
            self.assertLess(_low, _cap, f"cap={_cap} 时低水位必须 < cap（否则下一轮又裁）")
            self.assertGreaterEqual(_low, 1, "低水位至少 1 行")
        config.RUNTIME_PARAMS["pulse_pair_file_cap"] = 1
        self.assertEqual(pt._pair_trim_low_water(), 1, "cap=1 无法再降，直接返回 1")

    # ---- 7) 真从 RUNTIME_PARAMS 取 ----
    def test_07_reads_runtime_params(self):
        config.RUNTIME_PARAMS["pulse_pair_file_cap"] = 200
        config.RUNTIME_PARAMS["pulse_pair_trim_low_ratio"] = 0.5
        self.assertEqual(pt._pair_file_cap(), 200)
        self.assertEqual(pt._pair_trim_low_water(), 100)
        _write_pairs(self.p, 250)
        pt._trim_pair_file(self.p)
        self.assertEqual(len(_lines(self.p)), 100)


if __name__ == "__main__":
    unittest.main()
