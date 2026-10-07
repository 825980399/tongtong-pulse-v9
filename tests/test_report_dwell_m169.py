# -*- coding: utf-8 -*-
"""169批 C4（T-报告时限-1）门控测试。

锁定三件事：
  1. 停留时限读数：超限时进「逾期清单」，未超限不进；
  2. 汇报件**强制落盘断言**：清单真的写盘且可解析、内容与内存一致；
  3. 灰度开关关闭 → 不扫描、不落盘；`--strict` 有逾期即 exit 1。

★本刀只立口径，**不做任何删除**（清理归停窗段）—— 测试亦只写临时目录。
★跨盘：临时目录常在 C 盘、项目在 D 盘，工具须走 normalize_relpath（C2 统一入口）。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

import tools.report_dwell_audit as ra


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m169_c4_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk_dir(self):
        """建一个扫描用子目录。"""
        return tempfile.mkdtemp(prefix="d_", dir=self.tmp)

    def _mk_file(self, directory, name, age_days):
        """在指定目录内建一个 mtime 为 age_days 天前的文件。"""
        _p = os.path.join(directory, name)
        with open(_p, "w", encoding="utf-8") as _f:
            _f.write("x")
        _mt = time.time() - age_days * 86400.0
        os.utime(_p, (_mt, _mt))
        return _p


class TestDwellScan(_Tmp):
    """① 停留时限读数 + 逾期清单。"""

    def test_10_overdue_only(self):
        _d = self._mk_dir()
        self._mk_file(_d, "old.md", 5.0)
        self._mk_file(_d, "fresh.md", 0.1)
        _r = ra.scan_dwell(dirs=[_d], limit_days=2.0)
        self.assertEqual(_r["total"], 2)
        self.assertEqual(_r["overdue_count"], 1, "只有超限时才进清单")
        self.assertTrue(_r["overdue"][0]["path"].replace("\\", "/").endswith("old.md"),
                        _r["overdue"][0]["path"])

    def test_11_limit_respected(self):
        _d = self._mk_dir()
        self._mk_file(_d, "f.md", 3.0)
        self.assertEqual(ra.scan_dwell(dirs=[_d], limit_days=5.0)["overdue_count"], 0)
        self.assertEqual(ra.scan_dwell(dirs=[_d], limit_days=1.0)["overdue_count"], 1)

    def test_12_age_days_reported(self):
        _d = self._mk_dir()
        self._mk_file(_d, "f.md", 4.0)
        _r = ra.scan_dwell(dirs=[_d], limit_days=1.0)
        self.assertAlmostEqual(_r["overdue"][0]["age_days"], 4.0, delta=0.05)

    def test_13_limit_from_config(self):
        """未显式传参 → 读 config.REPORT_DWELL_LIMIT_DAYS（默认 2）。"""
        with mock.patch.object(config, "REPORT_DWELL_LIMIT_DAYS", 7.0,
                               create=True):
            self.assertEqual(ra._limit_days(None), 7.0)
        self.assertEqual(ra._limit_days(3.5), 3.5)


class TestForcedPersist(_Tmp):
    """② 汇报件强制落盘断言。"""

    def test_20_written_and_parseable(self):
        _d = self._mk_dir()
        self._mk_file(_d, "old.md", 9.0)
        _r = ra.scan_dwell(dirs=[_d], limit_days=2.0)
        _out = os.path.join(self.tmp, "overdue.json")
        _got = ra.write_overdue(_r, _out)
        self.assertTrue(os.path.isfile(_got), "★强制落盘：清单必须真的写盘")
        with open(_got, encoding="utf-8") as _f:
            _back = json.loads(_f.read())
        self.assertEqual(_back["overdue_count"], 1)
        self.assertEqual(len(_back["overdue"]), 1)

    def test_21_content_matches_memory(self):
        """落盘内容与内存结果一致（write_overdue 内含该断言）。"""
        _d = self._mk_dir()
        self._mk_file(_d, "old.md", 9.0)
        _r = ra.scan_dwell(dirs=[_d], limit_days=2.0)
        _out = os.path.join(self.tmp, "o2.json")
        ra.write_overdue(_r, _out)
        with open(_out, encoding="utf-8") as _f:
            _back = json.loads(_f.read())
        self.assertEqual(_back, _r)


class TestSwitchAndCli(_Tmp):
    """③ 灰度开关 + CLI 退出码。"""

    def test_30_switch_off_no_write(self):
        _d = self._mk_dir()
        self._mk_file(_d, "old.md", 9.0)
        _out = os.path.join(self.tmp, "off.json")
        with mock.patch.object(config, "ENABLE_REPORT_DWELL_AUDIT", False,
                               create=True):
            self.assertFalse(ra._m169_dwell_enabled())
            _rc = ra.main(["--dirs", _d, "--out", _out])
        self.assertEqual(_rc, 0)
        self.assertFalse(os.path.exists(_out), "开关关闭时不得落盘")

    def test_31_strict_exit_code(self):
        _d = self._mk_dir()
        self._mk_file(_d, "old.md", 9.0)
        _out = os.path.join(self.tmp, "s1.json")
        self.assertEqual(ra.main(["--dirs", _d, "--out", _out, "--strict"]), 1)

    def test_32_no_overdue_exit_zero(self):
        _d = self._mk_dir()
        self._mk_file(_d, "fresh.md", 0.1)
        _out = os.path.join(self.tmp, "s2.json")
        self.assertEqual(ra.main(["--dirs", _d, "--out", _out, "--strict"]), 0)
        self.assertTrue(os.path.isfile(_out), "无逾期也应落盘（读数留痕）")


if __name__ == "__main__":
    unittest.main()
