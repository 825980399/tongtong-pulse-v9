# -*- coding: utf-8 -*-
"""主线第39批 T1+T2 门控：.log 真问题豁免 + 运行态目录白名单。

覆盖：
  T1  扩展名白名单**豁免**有源码产出方的路径（ext_whitelist_exempted）/
      无产出方仍排除（ext_whitelist）/ **目录**白名单同样豁免（dir_whitelist_exempted）/
      豁免条目计入 no_consumer 而非 excluded / summary 与报告统计
  T2  运行态目录默认白名单（data/context·knowledge·evolution·stream·runtime_trajectory
      + nucleus/data 等）/ 不影响 data/self_awareness 单独机制 / 配置可扩展
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

import config
from nucleus.self_awareness.ProductionConsumptionMatcher import (
    ProductionConsumptionMatcher,
    _exclude_dirs,
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


#: 写 data/a.log（有产出方）与 data/stream/s.json（有产出方）
_W = '''# -*- coding: utf-8 -*-
import json


def write_log():
    with open("data/a.log", "w", encoding="utf-8") as f:
        f.write("crash\\n")


def write_stream():
    with open("data/stream/s.json", "w", encoding="utf-8") as f:
        json.dump({}, f)
'''

_FILES = {
    "src/w.py": _W,
    "data/a.log": "crash\n",                # 有产出方 → 豁免
    "data/b.log": "noise\n",                # 无产出方 → 排除
    "data/stream/s.json": "{}",             # 有产出方 → 目录豁免
    "data/stream/t.json": "{}",             # 无产出方 → 目录排除
    "data/context/c.json": "{}",
    "data/knowledge/k.json": "{}",
    "data/evolution/e.json": "{}",
    "nucleus/data/n.json": "{}",
    "docs/d.md": "# doc",
}


def _mkproj():
    d = tempfile.mkdtemp(prefix="m39pc_")
    for rel, txt in _FILES.items():
        p = os.path.join(d, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(txt)
    return d


def _scan(d):
    m = ProductionConsumptionMatcher(project_root=d, scan_dirs=["src"])
    return m.scan()


class TestExcludeReasonSignature(unittest.TestCase):
    """T1：`exclude_reason` 的 `has_producer` 参数与判定。"""

    def test_01_default_has_producer_false_backward_compatible(self):
        # 不传 has_producer → 与第38批行为一致（排除）
        self.assertEqual(exclude_reason("data/x.log"), "ext_whitelist")
        self.assertEqual(exclude_reason("data/probe/p.json"), "dir_whitelist")

    def test_02_ext_whitelist_exempted(self):
        self.assertEqual(exclude_reason("data/x.log", has_producer=True),
                         "ext_whitelist_exempted")

    def test_03_dir_whitelist_exempted(self):
        self.assertEqual(exclude_reason("data/stream/x.json", has_producer=True),
                         "dir_whitelist_exempted")

    def test_04_self_observation_never_exempted(self):
        """★自我观察排除**不豁免** —— 否则 T4 会失效（引擎产物也有源码产出方）。"""
        self.assertEqual(
            exclude_reason("data/self_awareness/p.json", has_producer=True),
            "self_observation")

    def test_05_non_whitelisted_returns_empty(self):
        self.assertEqual(exclude_reason("src/w.py", has_producer=True), "")


class TestRuntimeDirsDefault(unittest.TestCase):
    """T2：运行态目录默认白名单。"""

    def test_10_task_book_dirs_present(self):
        dirs = _exclude_dirs()
        for d in ("data/context", "data/knowledge", "data/evolution",
                  "data/stream", "data/runtime_trajectory"):
            self.assertIn(d, dirs, d)

    def test_11_nucleus_data_correction(self):
        """★实测校正：真实路径是 nucleus/data/runtime_trajectory（非 data/...）。"""
        self.assertIn("nucleus/data", _exclude_dirs())

    def test_12_self_awareness_not_in_dirs(self):
        """data/self_awareness 不在**目录白名单**中（由自我观察机制单独处理）。"""
        self.assertNotIn("data/self_awareness", _exclude_dirs())

    def test_13_original_dirs_kept(self):
        dirs = _exclude_dirs()
        for d in ("data/evolution/test_runs", "data/probe", "docs", "tmp"):
            self.assertIn(d, dirs, d)


class TestExemptionIntegration(unittest.TestCase):
    """T1：端到端（临时迷你工程）。"""

    @classmethod
    def setUpClass(cls):
        cls.proj = _mkproj()
        cls.r = _scan(cls.proj)
        cls.f = cls.r["files"]
        cls.s = cls.r["summary"]

    def test_20_ext_exempted_kept_as_no_consumer(self):
        e = self.f["data/a.log"]
        self.assertEqual(e["category"], "no_consumer")
        self.assertEqual(e["exclude_reason"], "ext_whitelist_exempted")
        self.assertTrue(e["producers"])

    def test_21_ext_not_exempted_still_excluded(self):
        e = self.f["data/b.log"]
        self.assertEqual(e["category"], "excluded")
        self.assertEqual(e["exclude_reason"], "ext_whitelist")

    def test_22_dir_exempted_kept_as_no_consumer(self):
        e = self.f["data/stream/s.json"]
        self.assertEqual(e["category"], "no_consumer")
        self.assertEqual(e["exclude_reason"], "dir_whitelist_exempted")

    def test_23_dir_not_exempted_still_excluded(self):
        for p in ("data/stream/t.json", "data/context/c.json",
                  "data/knowledge/k.json", "data/evolution/e.json",
                  "nucleus/data/n.json", "docs/d.md"):
            self.assertEqual(self.f[p]["category"], "excluded", p)

    def test_24_exempted_not_counted_as_excluded(self):
        ex_paths = {p for p, v in self.f.items()
                    if v["category"] == "excluded"}
        self.assertNotIn("data/a.log", ex_paths)
        self.assertNotIn("data/stream/s.json", ex_paths)

    def test_25_summary_counters(self):
        self.assertEqual(self.s["ext_whitelist_exempted"], 1)
        self.assertEqual(self.s["dir_whitelist_exempted"], 1)
        self.assertEqual(self.s["whitelist_exempted_total"], 2)
        self.assertEqual(
            self.s["exempted_by_reason"],
            {"dir_whitelist_exempted": 1, "ext_whitelist_exempted": 1})
        self.assertGreaterEqual(self.s["excluded"], 6)

    def test_26_report_shows_exemption(self):
        m = ProductionConsumptionMatcher(project_root=self.proj, scan_dirs=["src"])
        txt = m.build_report(self.r)
        self.assertIn("【白名单豁免】", txt)
        self.assertIn("共豁免 2 个真问题", txt)
        self.assertIn("dir_whitelist_exempted", txt)

    def test_27_analyze_entry_transparent(self):
        import importlib
        _m = importlib.import_module(
            "nucleus.self_awareness.ProductionConsumptionMatcher")
        _old = _m.ProductionConsumptionMatcher.__init__
        _proj = _mkproj()

        def _patched(self, project_root=None, scan_dirs=None):
            _old(self, project_root=_proj, scan_dirs=["src"])

        try:
            _m.ProductionConsumptionMatcher.__init__ = _patched
            r = _m.analyze_production_consumption()
        finally:
            _m.ProductionConsumptionMatcher.__init__ = _old
        self.assertEqual(r["ext_whitelist_exempted"], 1)


class TestSwitchAndConfig(unittest.TestCase):
    """T1/T2：开关与配置可扩展。"""

    def test_30_switch_off_zero_exclusion(self):
        proj = _mkproj()
        with _Switch(ENABLE_PRODUCTION_CONSUMPTION_EXCLUDE=False):
            r = _scan(proj)
        self.assertEqual(r["summary"]["excluded"], 0)
        self.assertEqual(r["summary"]["whitelist_exempted_total"], 0)
        self.assertEqual(self_state(r), 0)

    def test_31_config_extensible(self):
        """★配置可扩展：自定义目录白名单**替换**默认值。

        ★注意：断言必须在 ``with`` 块**内** —— 块退出后 config 已还原
        （首版把断言写在块外，测的是默认值，属测试自身缺陷）。
        """
        proj = _mkproj()
        with _Switch(PRODUCTION_CONSUMPTION_EXCLUDE_DIRS=["src"]):
            self.assertEqual(_exclude_dirs(), ("src",))
            r = _scan(proj)
        # 自定义白名单后 data/context 不再被目录白名单排除
        self.assertNotEqual(
            r["files"]["data/context/c.json"]["category"], "excluded")


def self_state(r):
    """辅助：统计当前 excluded 条目数（供开关关闭断言）。"""
    return len([1 for v in r["files"].values() if v["category"] == "excluded"])


if __name__ == "__main__":
    unittest.main()
