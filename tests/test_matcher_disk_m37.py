# -*- coding: utf-8 -*-
"""第37批 T2+T3 门控：配对器磁盘枚举通道 + 路径解析与误报收敛。

覆盖：磁盘枚举正确性/排除规则/并集来源标记/四类分类判定/
suggestion 区分/汇总口径/开关关闭回退。
"""
import os
import sys

# ★注意：sys.path.insert(...) 是调用语句（不关闭 ruff import 区）；
#   `_ROOT = ...` 这类赋值会关闭 → 其后所有 import 报 E402。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io
import tempfile
import unittest

import config
from nucleus.self_awareness.ProductionConsumptionMatcher import (
    ProductionConsumptionMatcher,
    _blank_entry,
    _disk_extensions,
    _disk_roots,
    _iter_disk_data_files,
    _scan_disk_enabled,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Switch:
    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(config, k)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(config, k, v)
        return False


_PROD = '''# -*- coding: utf-8 -*-
import json
import os


def write_a():
    with open("data/a.json", "w", encoding="utf-8") as f:
        json.dump({"x": 1}, f)


def write_dynamic():
    _ts = "20260913"
    with open("data/dyn_%s.json" % _ts, "w", encoding="utf-8") as f:
        json.dump({}, f)
'''

_CONS = '''# -*- coding: utf-8 -*-
import json


def read_b():
    with open("data/b.json", encoding="utf-8") as f:
        return json.load(f)
'''


def _mk_proj():
    """造迷你工程：pkg/ 源码 + data/ 数据（含应排除的子目录）。"""
    d = tempfile.mkdtemp(prefix="m37mt_")
    os.makedirs(os.path.join(d, "pkg"))
    os.makedirs(os.path.join(d, "data"))
    os.makedirs(os.path.join(d, "data", "tmp"))
    os.makedirs(os.path.join(d, "data", "__pycache__"))
    os.makedirs(os.path.join(d, ".bak_old"))
    _w(os.path.join(d, "pkg", "prod.py"), _PROD)
    _w(os.path.join(d, "pkg", "cons.py"), _CONS)
    _w(os.path.join(d, "data", "a.json"), "{}")
    _w(os.path.join(d, "data", "b.json"), "{}")
    _w(os.path.join(d, "data", "orphan.json"), "{}")
    _w(os.path.join(d, "data", "tmp", "x.json"), "{}")
    _w(os.path.join(d, "data", "__pycache__", "y.json"), "{}")
    _w(os.path.join(d, ".bak_old", "z.json"), "{}")
    _w(os.path.join(d, "data", "note.md"), "# doc")
    return d


def _w(p, s):
    io.open(p, "w", encoding="utf-8").write(s)


class TestDiskEnumeration(unittest.TestCase):
    """1) 磁盘枚举通道。"""

    @classmethod
    def setUpClass(cls):
        cls.bak = {"PRODUCTION_CONSUMPTION_DISK_ROOTS":
                   getattr(config, "PRODUCTION_CONSUMPTION_DISK_ROOTS", ["."])}

    def setUp(self):
        self.proj = _mk_proj()

    def _enum(self, ext=None, depth=0):
        return _iter_disk_data_files(self.proj, ["data"],
                                     ext or _disk_extensions(), depth)

    def test_10_enumerates_data_files(self):
        got = self._enum()
        self.assertIn("data/a.json", got)
        self.assertIn("data/b.json", got)
        self.assertIn("data/orphan.json", got)

    def test_11_excludes_tmp_and_pycache(self):
        got = self._enum()
        self.assertNotIn("data/tmp/x.json", got)
        self.assertNotIn("data/__pycache__/y.json", got)

    def test_12_excludes_bak_dirs(self):
        got = _iter_disk_data_files(self.proj, ["."], _disk_extensions())
        self.assertFalse([p for p in got if ".bak" in p])

    def test_13_extension_whitelist(self):
        got = self._enum()
        self.assertIn("data/note.md", got, "md 在默认白名单内")
        got2 = self._enum(ext=(".json",))
        self.assertNotIn("data/note.md", got2)

    def test_14_max_depth_limits(self):
        os.makedirs(os.path.join(self.proj, "data", "d1", "d2"), exist_ok=True)
        _w(os.path.join(self.proj, "data", "d1", "d2", "deep.json"), "{}")
        self.assertIn("data/d1/d2/deep.json", self._enum(depth=0))
        self.assertNotIn("data/d1/d2/deep.json", self._enum(depth=1))

    def test_15_returns_posix_rel_paths_sorted(self):
        got = self._enum()
        self.assertEqual(got, sorted(got))
        self.assertTrue(all("/" in p or not p.startswith(".") for p in got))
        self.assertFalse([p for p in got if "\\" in p], "应为 POSIX 分隔符")


class TestFinalizeClassification(unittest.TestCase):
    """2) 四类分类判定（T3 误报收敛核心）。"""

    def setUp(self):
        self.proj = _mk_proj()
        self.m = ProductionConsumptionMatcher(project_root=self.proj,
                                              scan_dirs=["pkg"])

    def _fin(self, path, producers=None, consumers=None, sources=None):
        e = _blank_entry(path)
        e["producers"] = list(producers or [])
        e["consumers"] = list(consumers or [])
        e["sources"] = list(sources or [])
        self.m._finalize(path, e)
        return e

    def test_20_dynamic_template_is_dynamic_path(self):
        e = self._fin("data/dyn_{ts}.json", sources=["source"])
        self.assertEqual(e["category"], "dynamic_path")
        self.assertIsNone(e["size"])

    def test_21_percent_template_is_dynamic_path(self):
        e = self._fin("data/x_%s.json", sources=["source"])
        self.assertEqual(e["category"], "dynamic_path")

    def test_22_missing_file_is_path_not_found(self):
        e = self._fin("data/nope.json", sources=["source"])
        self.assertEqual(e["category"], "path_not_found")
        self.assertIsNone(e["size"])
        self.assertEqual(e["path_kind"], "missing")
        self.assertNotIn("无消费", e["suggestion"])

    def test_23_existing_no_consumer_is_no_consumer(self):
        e = self._fin("data/orphan.json", sources=["disk"])
        self.assertEqual(e["category"], "no_consumer")
        self.assertEqual(e["path_kind"], "existing")

    def test_24_existing_with_producer_and_consumer_is_normal(self):
        e = self._fin("data/b.json", producers=[{"line": 1}],
                      consumers=[{"line": 1}], sources=["disk"])
        self.assertEqual(e["category"], "normal")

    def test_24b_disk_with_consumer_but_no_source_producer(self):
        """★磁盘存在 + 有消费方 + 源码**无**产出调用 → no_producer（幽灵依赖）。

        磁盘枚举只能证明「文件存在」（确有产出），**不能**证明"代码写了它"；
        「读了没人写」的判定必须只认源码里的产出调用，否则该类别永不出现。
        """
        e = self._fin("data/b.json", consumers=[{"line": 1}], sources=["disk"])
        self.assertEqual(e["category"], "no_producer")

    def test_25_consumer_without_producer_is_no_producer(self):
        e = self._fin("data/b.json", consumers=[{"line": 1}], sources=["source"])
        self.assertEqual(e["category"], "no_producer")

    def test_26_disk_enumeration_counts_as_producer(self):
        """★磁盘枚举命中 = 运行期确已写过 → 视为有产出。"""
        e = self._fin("data/a.json", sources=["disk"])
        self.assertEqual(e["category"], "no_consumer")

    def test_27_suggestion_differs_by_producer(self):
        _with = self._fin("data/a.json", producers=[{"line": 1}],
                          sources=["source"])
        _disk = self._fin("data/orphan.json", sources=["disk"])
        self.assertIn("确认是否废弃", _with["suggestion"])
        self.assertIn("磁盘存在但源码未引用", _disk["suggestion"])

    def test_28_abs_path_recorded(self):
        e = self._fin("data/a.json", sources=["disk"])
        self.assertTrue(os.path.isabs(e["abs_path"]))
        self.assertTrue(e["abs_path"].endswith("a.json"))

    def test_29_false_positive_rate_is_zero_for_existing(self):
        """「无消费」类别里不得出现解析后不存在的路径（误报收敛的硬判据）。"""
        e = self._fin("data/orphan.json", sources=["disk"])
        self.assertEqual(e["category"], "no_consumer")
        self.assertIsNotNone(e["size"], "有效无消费必须 size 非空")


class TestScanUnionAndSummary(unittest.TestCase):
    """3) 并集 / 来源标记 / 汇总口径 / 开关回退。"""

    def setUp(self):
        self.proj = _mk_proj()

    def _scan(self, roots=("data",)):
        with _Switch(PRODUCTION_CONSUMPTION_DISK_ROOTS=list(roots)):
            return ProductionConsumptionMatcher(
                project_root=self.proj, scan_dirs=["pkg"]).scan()

    def test_30_union_merges_both_channels(self):
        _r = self._scan()
        _files = _r["files"]
        self.assertIn("data/a.json", _files)
        self.assertIn("data/orphan.json", _files, "仅磁盘有、源码未引用")

    def test_31_sources_marked(self):
        _r = self._scan()
        self.assertIn("source", _r["files"]["data/a.json"]["sources"])
        self.assertIn("disk", _r["files"]["data/a.json"]["sources"])
        self.assertEqual(_r["files"]["data/orphan.json"]["sources"], ["disk"])

    def test_32_summary_fields(self):
        _s = self._scan()["summary"]
        for _k in ("source_paths", "disk_files", "union_files", "coverage_ratio",
                   "no_consumer", "path_not_found", "dynamic_path",
                   "false_positive_rate", "no_consumer_with_producer",
                   "no_consumer_disk_only"):
            self.assertIn(_k, _s, _k)

    def test_33_coverage_ratio_high(self):
        _s = self._scan()["summary"]
        self.assertIsNotNone(_s["coverage_ratio"])
        self.assertGreater(_s["coverage_ratio"], 0.8)

    def test_34_switch_off_falls_back_to_source_channel(self):
        with _Switch(PRODUCTION_CONSUMPTION_SCAN_DISK=False):
            _r = ProductionConsumptionMatcher(
                project_root=self.proj, scan_dirs=["pkg"]).scan()
        _s = _r["summary"]
        self.assertEqual(_s["disk_files"], 0)
        self.assertEqual(_s["union_files"], _s["source_paths"])
        self.assertIsNone(_s["coverage_ratio"])

    def test_35_path_not_found_not_in_no_consumer(self):
        """★误报收敛：不存在路径必须单列，不得落在 no_consumer。"""
        _r = self._scan()
        _nc = [e for e in _r["files"].values() if e["category"] == "no_consumer"]
        self.assertTrue(all(e["size"] is not None for e in _nc),
                        f"no_consumer 中出现 size=None: "
                        f"{[e['path'] for e in _nc if e['size'] is None]}")

    def test_36_report_is_readable_with_source_section(self):
        _m = ProductionConsumptionMatcher(project_root=self.proj,
                                          scan_dirs=["pkg"])
        with _Switch(PRODUCTION_CONSUMPTION_DISK_ROOTS=["data"]):
            _txt = _m.build_report(_m.scan())
        self.assertIn("【扫描渠道】", _txt)
        self.assertIn("覆盖率", _txt)
        self.assertIn("【路径解析】", _txt)


class TestConfigAndHelpers(unittest.TestCase):
    """4) 配置项与辅助函数。"""

    def test_40_config_items(self):
        self.assertTrue(hasattr(config, "PRODUCTION_CONSUMPTION_SCAN_DISK"))
        self.assertTrue(config.PRODUCTION_CONSUMPTION_SCAN_DISK)
        self.assertTrue(hasattr(config, "PRODUCTION_CONSUMPTION_DISK_ROOTS"))
        self.assertTrue(hasattr(config, "PRODUCTION_CONSUMPTION_SCAN_EXTENSIONS"))
        self.assertTrue(hasattr(config, "PRODUCTION_CONSUMPTION_DISK_MAX_DEPTH"))

    def test_41_helpers_read_config(self):
        self.assertTrue(_scan_disk_enabled())
        self.assertIsInstance(_disk_roots(), list)
        self.assertIsInstance(_disk_extensions(), tuple)

    def test_42_disk_excludes_do_not_contain_data(self):
        """★磁盘通道必须包含 data/（源码通道排除它，磁盘通道正是要枚举它）。"""
        from nucleus.self_awareness.ProductionConsumptionMatcher import (
            _DISK_EXCLUDE_DIRS,
        )
        self.assertNotIn("data", _DISK_EXCLUDE_DIRS)
        self.assertNotIn("logs", _DISK_EXCLUDE_DIRS)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestFileWhitelistP258(unittest.TestCase):
    """★P2-258：data根下运行态文件的文件级白名单。"""

    def test_01_data_root_runtime_file_excluded(self):
        """data根下的运行态文件（无产出方）应被排除为噪声。"""
        from nucleus.self_awareness.ProductionConsumptionMatcher import exclude_reason
        _reason = exclude_reason("data/channel_quota_usage.json", has_producer=False)
        self.assertEqual(_reason, "file_whitelist",
                         f"期望 file_whitelist，实际 {_reason!r}")

    def test_02_data_root_runtime_file_with_producer_exempted(self):
        """data根下的运行态文件有源码产出方时应豁免（保留为真问题）。"""
        from nucleus.self_awareness.ProductionConsumptionMatcher import exclude_reason
        _reason = exclude_reason("data/runtime_state.json", has_producer=True)
        self.assertEqual(_reason, "file_whitelist_exempted",
                         f"期望 file_whitelist_exempted，实际 {_reason!r}")

    def test_03_non_whitelist_file_not_excluded(self):
        """非白名单文件不应被文件级白名单排除。"""
        from nucleus.self_awareness.ProductionConsumptionMatcher import exclude_reason
        _reason = exclude_reason("data/my_custom_data.json", has_producer=False)
        self.assertNotIn(_reason, ("file_whitelist", "file_whitelist_exempted"),
                         f"非白名单文件不应被文件级排除，实际 {_reason!r}")

    def test_04_all_default_whitelist_files_excluded(self):
        """默认白名单中的所有文件都应被排除（无产出方时）。"""
        from nucleus.self_awareness.ProductionConsumptionMatcher import _EXCLUDE_FILES_DEFAULT, exclude_reason
        for _f in _EXCLUDE_FILES_DEFAULT:
            _reason = exclude_reason(_f, has_producer=False)
            self.assertEqual(_reason, "file_whitelist",
                             f"{_f} 期望 file_whitelist，实际 {_reason!r}")

    def test_05_config_override_works(self):
        """配置项 PRODUCTION_CONSUMPTION_EXCLUDE_FILES 可覆盖默认值。"""
        from nucleus.self_awareness.ProductionConsumptionMatcher import _exclude_files, exclude_reason
        with _Switch(PRODUCTION_CONSUMPTION_EXCLUDE_FILES=["data/custom_file.json"]):
            _files = _exclude_files()
            self.assertIn("data/custom_file.json", _files)
            self.assertNotIn("data/channel_quota_usage.json", _files,
                             "配置覆盖后默认值不应生效")
            # 配置中的文件应被排除
            _reason = exclude_reason("data/custom_file.json", has_producer=False)
            self.assertEqual(_reason, "file_whitelist")
            # 默认白名单中的文件不再被排除
            _reason2 = exclude_reason("data/channel_quota_usage.json", has_producer=False)
            self.assertNotEqual(_reason2, "file_whitelist")

    def test_06_suggestion_text_exists(self):
        """file_whitelist 的建议文案应存在。"""
        from nucleus.self_awareness.ProductionConsumptionMatcher import _EXCLUDE_SUGGEST
        self.assertIn("file_whitelist", _EXCLUDE_SUGGEST)
        self.assertIn("file_whitelist_exempted", _EXCLUDE_SUGGEST)
