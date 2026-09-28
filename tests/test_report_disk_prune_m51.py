# -*- coding: utf-8 -*-
"""第51批 T5（P2-357）门控测试：data/reports 磁盘回收机制。

覆盖：
* `ReportBus._prune_disk` 保留最新 N 份 / 重要类型阈值 ×2
* 开关关闭零回归 / 显式注入目录不回收 / 归档目录（_ 前缀）跳过
* 永不抛异常 / `get_disk_stats` 统计
* `tools/cleanup_reports.py` 的 build_plan / apply_plan / restore_archive
* 源码接线（config 开关、publish 调用）
"""
import importlib.util
import pytest
import io
import os
import shutil
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.reporting.report_bus import (  # noqa: E402
    MAX_REPORTS_ON_DISK, ReportBus)

# 加载 tools/cleanup_reports.py（tools 非包）
_spec = importlib.util.spec_from_file_location(
    "cleanup_reports_m51", os.path.join(_ROOT, "tools", "cleanup_reports.py"))
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()


class _BusBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m51_t5_")
        self._base = os.path.join(self._tmp, "reports")
        self._orig_prune = getattr(config, "ENABLE_REPORT_DISK_PRUNE", True)

    def tearDown(self):
        config.ENABLE_REPORT_DISK_PRUNE = self._orig_prune
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _bus(self, limit=3):
        _b = ReportBus(base_dir=self._base, max_reports=1000,
                       max_reports_on_disk=limit)
        # 沙箱目录属「显式注入」→ 默认不回收；测试里显式打开回收通道
        _b._explicit_base_dir = False
        return _b

    def _pub(self, t, n):
        _b = self._bus()
        for _i in range(n):
            _b.publish_simple(t, "ut", content={"i": _i})
        return _b


@pytest.mark.production_data
class TestPruneDisk(_BusBase):
    def test_01_keeps_newest_n(self):
        _b = self._bus(limit=3)
        for _i in range(6):
            _b.publish_simple("runtime", "ut", content={"i": _i})
        # ★publish 内部已自动回收（self._prune_disk()）→ 直接断言磁盘结果
        self.assertEqual(_b.get_disk_stats()["by_type"]["runtime"]["files"], 3)
        self.assertGreater(_b.get_stats()["disk_pruned_total"], 0)
        # 再次手动回收应为空（已在上限内）
        self.assertEqual(_b._prune_disk()["pruned"], 0)

    def test_02_keeps_exactly_limit_no_prune(self):
        _b = self._bus(limit=5)
        for _i in range(5):
            _b.publish_simple("runtime", "ut", content={"i": _i})
        _r = _b._prune_disk()
        self.assertEqual(_r["pruned"], 0)
        self.assertEqual(_b.get_disk_stats()["by_type"]["runtime"]["files"], 5)

    def test_03_important_type_double_limit(self):
        """★重要类型（health/pollution）阈值 ×2。"""
        _b = self._bus(limit=3)
        for _i in range(9):
            _b.publish_simple("health", "ut", content={"i": _i})
        _b._prune_disk()
        # 阈值 3 × 2 = 6
        self.assertEqual(_b.get_disk_stats()["by_type"]["health"]["files"], 6)

    def test_04_switch_off_no_prune(self):
        """零回归：开关关闭 → 不回收。"""
        config.ENABLE_REPORT_DISK_PRUNE = False
        _b = self._bus(limit=2)
        for _i in range(6):
            _b.publish_simple("runtime", "ut", content={"i": _i})
        _r = _b._prune_disk()
        self.assertEqual(_r["pruned"], 0)
        self.assertEqual(_b.get_disk_stats()["by_type"]["runtime"]["files"], 6)

    def test_05_explicit_dir_not_pruned(self):
        """显式注入目录（测试沙箱语义）→ 不回收。"""
        _b = ReportBus(base_dir=self._base, max_reports_on_disk=2)
        self.assertTrue(_b._explicit_base_dir)
        self.assertFalse(_b._disk_prune_enabled())

    def test_06_archive_dir_skipped(self):
        """归档目录（``_`` 前缀）不被计入、不被删除。"""
        _b = self._bus(limit=2)
        for _i in range(4):
            _b.publish_simple("runtime", "ut", content={"i": _i})
        _arch = os.path.join(self._base, "_archive_x", "runtime")
        os.makedirs(_arch, exist_ok=True)
        _sentinel = os.path.join(_arch, "keep_me.json")
        io.open(_sentinel, "w", encoding="utf-8").write("{}")
        _b._prune_disk()
        self.assertTrue(os.path.exists(_sentinel), "归档目录内的文件不得被删")
        self.assertEqual(_b.get_disk_stats()["by_type"]["runtime"]["files"], 2)

    def test_07_never_raises(self):
        _b = self._bus(limit=1)
        _b._base_dir = os.path.join(self._tmp, "nonexistent_deep", "x")
        self.assertIsInstance(_b._prune_disk(), dict)

    def test_08_non_json_untouched(self):
        _b = self._bus(limit=1)
        for _i in range(3):
            _b.publish_simple("runtime", "ut", content={"i": _i})
        _keep = os.path.join(self._base, "runtime", "notes.txt")
        io.open(_keep, "w", encoding="utf-8").write("x")
        _b._prune_disk()
        self.assertTrue(os.path.exists(_keep))

    def test_09_disk_stats_shape(self):
        _b = self._bus(limit=10)
        _b.publish_simple("health", "ut")
        _b.publish_simple("runtime", "ut")
        _s = _b.get_disk_stats()
        self.assertEqual(_s["total_files"], 2)
        self.assertIn("health", _s["by_type"])
        self.assertIn("bytes", _s["by_type"]["health"])

    def test_10_prune_counter_in_stats(self):
        _b = self._bus(limit=1)
        for _i in range(3):
            _b.publish_simple("runtime", "ut", content={"i": _i})
        self.assertGreater(_b.get_stats()["disk_pruned_total"], 0)


@pytest.mark.production_data
class TestCleanupTool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m51_t5t_")
        self._base = os.path.join(self._tmp, "reports")
        for _t, _n in (("runtime", 5), ("health", 3)):
            _d = os.path.join(self._base, _t)
            os.makedirs(_d, exist_ok=True)
            for _i in range(_n):
                _p = os.path.join(_d, "%s_%02d.json" % (_t, _i))
                io.open(_p, "w", encoding="utf-8").write("{}")
                os.utime(_p, (time.time() - 100 + _i, time.time() - 100 + _i))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_20_build_plan_counts_all(self):
        _p = cr.build_plan(self._base)
        self.assertEqual(_p["total"], 8)
        self.assertEqual(_p["by_type"], {"runtime": 5, "health": 3})

    def test_21_build_plan_keep_newest(self):
        _p = cr.build_plan(self._base, keep=2)
        # 每类型保留最新 2 份 → 目标 = (5-2)+(3-2) = 4
        self.assertEqual(_p["total"], 4)

    def test_22_build_plan_before_filter(self):
        _p = cr.build_plan(self._base, before=time.time() - 50)
        self.assertEqual(_p["total"], 8)          # 全部早于 -50s

    def test_23_apply_archives_not_deletes(self):
        _p = cr.build_plan(self._base)
        _r = cr.apply_plan(_p, base=self._base, label="ut")
        self.assertEqual(_r["moved"], 8)
        self.assertEqual(len(_r["errors"]), 0)
        _arch = _r["dest"]
        _n = sum(len(f) for _d, _sd, f in os.walk(_arch))
        self.assertEqual(_n, 8, "归档应保留全部内容（不删除）")
        # 活动区应为空
        self.assertEqual(cr.build_plan(self._base)["total"], 0)

    def test_24_restore_roundtrip(self):
        _p = cr.build_plan(self._base)
        cr.apply_plan(_p, base=self._base, label="ut")
        _r = cr.restore_archive(label="ut", base=self._base)
        self.assertEqual(_r["restored"], 8)
        self.assertEqual(cr.build_plan(self._base)["total"], 8)

    def test_25_archive_dir_ignored_by_plan(self):
        """归档目录自身不得被再次纳入清理预案。"""
        _p = cr.build_plan(self._base)
        cr.apply_plan(_p, base=self._base, label="ut")
        self.assertEqual(cr.build_plan(self._base)["total"], 0)


class TestSourceWiring(unittest.TestCase):
    def test_30_config_has_switches(self):
        _c = _src("config.py")
        for _k in ("ENABLE_REPORT_DISK_PRUNE", "MAX_REPORTS_ON_DISK"):
            self.assertIn(_k, _c)

    def test_31_publish_calls_prune(self):
        _s = _src("nucleus/reporting/report_bus.py")
        self.assertIn("self._prune_disk()", _s)

    def test_32_no_bare_except_pass(self):
        import re
        _s = _src("nucleus/reporting/report_bus.py")
        self.assertEqual(
            len(re.findall(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)", _s)), 0)

    def test_33_default_limit_matches_config(self):
        self.assertEqual(config.MAX_REPORTS_ON_DISK, MAX_REPORTS_ON_DISK)


if __name__ == "__main__":
    unittest.main()
