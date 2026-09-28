# -*- coding: utf-8 -*-
"""主线第38批 T5 门控：配对器信噪比优化（目录/扩展名排除白名单）。

覆盖：
  目录白名单（data/evolution/test_runs、data/probe、docs、tmp）/
  扩展名白名单（.md/.txt/.log）/**数据扩展名不排除**（.json/.parquet）/
  自我观察排除优先级 / excluded 不计入 no_consumer /
  三类口径（有效无消费 · 磁盘独有已排除 · 磁盘独有未排除）/ 信噪比与噪声过滤率 /
  配置可扩展 / 开关关闭零回归 / 报告三分类 / 分析器入口透传
"""
import os
import sys
import tempfile

# ★注意：sys.path.insert(...) 是调用语句（不关闭 ruff import 区）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

import config
from nucleus.self_awareness.ProductionConsumptionMatcher import (
    ProductionConsumptionMatcher,
    _exclude_dirs,
    _exclude_extensions,
    _match_prefix,
    analyze_production_consumption,
    exclude_reason,
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


#: 写 data/a.json（真实产出方）
_W = '''# -*- coding: utf-8 -*-
import json


def write_a():
    with open("data/a.json", "w", encoding="utf-8") as f:
        json.dump({"x": 1}, f)
'''

_FILES = {
    "src/w.py": _W,
    "data/a.json": '{"x": 1}',
    "data/keep.json": '{"keep": 1}',
    "data/readme.txt": "hello",
    "data/probe/p.json": "{}",
    "data/self_awareness/s.json": "{}",
    "docs/d.md": "# doc",
    "docs/sub/e.md": "# doc2",
}


def _mkproj():
    d = tempfile.mkdtemp(prefix="m38pc_")
    for rel, txt in _FILES.items():
        p = os.path.join(d, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(txt)
    return d


def _scan(d):
    m = ProductionConsumptionMatcher(project_root=d, scan_dirs=["src"])
    return m.scan()


class TestExcludeReasonFn(unittest.TestCase):
    """T5：排除判据单元。"""

    def test_10_match_prefix_boundaries(self):
        pre = ("data/probe",)
        self.assertTrue(_match_prefix("data/probe/p.json", pre))
        self.assertTrue(_match_prefix("data/probe", pre))
        self.assertTrue(_match_prefix("./data/probe/x", pre))
        self.assertFalse(_match_prefix("data/probe2/x", pre))
        self.assertFalse(_match_prefix("data/x.json", pre))

    def test_11_reasons(self):
        self.assertEqual(exclude_reason("data/probe/x.json"), "dir_whitelist")
        self.assertEqual(exclude_reason("docs/a/b.md"), "dir_whitelist")
        self.assertEqual(exclude_reason("data/self_awareness/p.json"),
                         "self_observation")
        self.assertEqual(exclude_reason("data/readme.txt"), "ext_whitelist")
        self.assertEqual(exclude_reason("data/notes.log"), "ext_whitelist")
        self.assertEqual(exclude_reason("data/a.json"), "")
        self.assertEqual(exclude_reason("data/v.npz"), "")
        self.assertEqual(exclude_reason("data/k.parquet"), "")

    def test_12_defaults(self):
        self.assertIn("docs", _exclude_dirs())
        self.assertIn("data/probe", _exclude_dirs())
        self.assertIn("data/evolution/test_runs", _exclude_dirs())
        self.assertIn("tmp", _exclude_dirs())
        exts = _exclude_extensions()
        self.assertIn(".md", exts)
        self.assertIn(".txt", exts)
        self.assertIn(".log", exts)
        # ★数据文件扩展名**不**在排除列表
        self.assertNotIn(".json", exts)
        self.assertNotIn(".parquet", exts)
        self.assertNotIn(".npz", exts)


class TestExcludeIntegration(unittest.TestCase):
    """T5：端到端（临时迷你工程）。"""

    @classmethod
    def setUpClass(cls):
        cls.proj = _mkproj()
        cls.r = _scan(cls.proj)
        cls.files = cls.r["files"]
        cls.s = cls.r["summary"]

    def _cat(self, rel):
        return self.files[rel]["category"]

    def test_20_dir_whitelist(self):
        self.assertEqual(self._cat("data/probe/p.json"), "excluded")
        self.assertEqual(self.files["data/probe/p.json"]["exclude_reason"],
                         "dir_whitelist")
        self.assertEqual(self._cat("docs/d.md"), "excluded")
        self.assertEqual(self._cat("docs/sub/e.md"), "excluded")

    def test_21_ext_whitelist(self):
        self.assertEqual(self._cat("data/readme.txt"), "excluded")
        self.assertEqual(self.files["data/readme.txt"]["exclude_reason"],
                         "ext_whitelist")

    def test_22_self_observation(self):
        self.assertEqual(self._cat("data/self_awareness/s.json"), "excluded")
        self.assertEqual(self.files["data/self_awareness/s.json"]["exclude_reason"],
                         "self_observation")

    def test_23_data_files_not_excluded(self):
        self.assertNotEqual(self._cat("data/a.json"), "excluded")
        self.assertNotEqual(self._cat("data/keep.json"), "excluded")

    def test_24_excluded_not_counted_as_no_consumer(self):
        nc = {e["path"] for e in self.files.values()
              if e["category"] == "no_consumer"}
        for p in ("data/probe/p.json", "docs/d.md", "data/readme.txt",
                  "data/self_awareness/s.json"):
            self.assertNotIn(p, nc)

    def test_25_three_categories(self):
        # 有效无消费：源码有产出方
        self.assertIn("data/a.json", self.files)
        self.assertEqual(self._cat("data/a.json"), "no_consumer")
        self.assertTrue(self.files["data/a.json"]["producers"])
        # 磁盘独有未排除
        self.assertEqual(self._cat("data/keep.json"), "no_consumer")
        self.assertFalse(self.files["data/keep.json"]["producers"])
        # 统计口径
        self.assertGreaterEqual(self.s["effective_no_consumer"], 1)
        self.assertGreaterEqual(self.s["disk_only_unexcluded"], 1)
        self.assertGreaterEqual(self.s["excluded"], 4)
        self.assertEqual(self.s["excluded_by_reason"].get("self_observation"), 1)

    def test_26_signal_and_noise_ratio(self):
        self.assertIsNotNone(self.s["signal_ratio"])
        self.assertIsNotNone(self.s["noise_filtered_ratio"])
        self.assertGreater(self.s["noise_filtered_ratio"], 0.5)   # 噪声过滤率 > 50%

    def test_27_report_three_categories(self):
        m = ProductionConsumptionMatcher(project_root=self.proj, scan_dirs=["src"])
        txt = m.build_report(self.r)
        self.assertIn("【信噪比】", txt)
        self.assertIn("真问题占比", txt)
        self.assertIn("噪声过滤率", txt)
        self.assertIn("已排除（噪声白名单", txt)
        self.assertIn("【排除明细】", txt)

    def test_28_excluded_keeps_exclude_reason(self):
        for e in self.files.values():
            if e["category"] == "excluded":
                self.assertTrue(e["exclude_reason"])
                self.assertTrue(e["suggestion"])


class TestSwitchAndConfig(unittest.TestCase):
    """T5：开关与配置可扩展。"""

    def test_30_switch_off_zero_regression(self):
        proj = _mkproj()
        with _Switch(ENABLE_PRODUCTION_CONSUMPTION_EXCLUDE=False):
            r = _scan(proj)
        self.assertEqual(r["summary"]["excluded"], 0)
        cats = {e["category"] for e in r["files"].values()}
        self.assertNotIn("excluded", cats)

    def test_31_config_extensible(self):
        """★第39批 T1（P2-238）：目录白名单**同样豁免源码产出方命中的路径** ——
        `data/a.json`（src/w.py 有产出方）保留为 no_consumer，
        仅无产出方的 `data/keep.json` 等被排除。"""
        proj = _mkproj()
        with _Switch(PRODUCTION_CONSUMPTION_EXCLUDE_DIRS=["data"]):
            r = _scan(proj)
        self.assertEqual(r["files"]["data/a.json"]["category"], "no_consumer")
        self.assertEqual(r["files"]["data/a.json"]["exclude_reason"],
                         "dir_whitelist_exempted")
        self.assertEqual(r["files"]["data/keep.json"]["category"], "excluded")
        self.assertGreaterEqual(r["summary"]["excluded"], 3)

    def test_32_ext_config_extensible(self):
        """★第39批 T1（P2-238）：扩展名白名单豁免**有产出方**的路径
        （`data/a.json`），对**无产出方**的同扩展名文件（`data/keep.json`）仍生效。"""
        proj = _mkproj()
        with _Switch(PRODUCTION_CONSUMPTION_EXCLUDE_EXTENSIONS=[".json"]):
            r = _scan(proj)
        self.assertEqual(r["files"]["data/keep.json"]["category"], "excluded")
        self.assertEqual(r["files"]["data/keep.json"]["exclude_reason"],
                         "ext_whitelist")
        self.assertEqual(r["files"]["data/a.json"]["category"], "no_consumer")
        self.assertEqual(r["files"]["data/a.json"]["exclude_reason"],
                         "ext_whitelist_exempted")

    def test_33_analyze_entry_transparent(self):
        # ★陷阱：包 __init__ 导出了同名类 → `import ... as _m` 拿到的是**类**，
        #   必须用 importlib.import_module 才拿到模块本身（项目铁律）。
        import importlib
        _m = importlib.import_module(
            "nucleus.self_awareness.ProductionConsumptionMatcher")
        _old = _m.ProductionConsumptionMatcher.__init__
        _proj = _mkproj()

        def _patched(self, project_root=None, scan_dirs=None):
            _old(self, project_root=_proj, scan_dirs=["src"])

        try:
            _m.ProductionConsumptionMatcher.__init__ = _patched
            r = analyze_production_consumption()
        finally:
            _m.ProductionConsumptionMatcher.__init__ = _old
        self.assertIn("excluded_count", r)
        self.assertIn("effective_no_consumer", r)
        self.assertIn("signal_ratio", r)
        self.assertGreaterEqual(r["excluded_count"], 4)


if __name__ == "__main__":
    unittest.main()
