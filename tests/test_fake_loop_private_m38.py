# -*- coding: utf-8 -*-
"""主线第38批 T3+T4 门控：虚假闭环检测器私有命名识别 + 自我观察噪声排除。

覆盖：
  T3  私有精确变体（_save/_load/_persist）被识别 / _save_xxx 仍不识别（防误报）/
      开关关闭零回归 / summary 公开·私有命中拆分 / 报告区分 / 候选结构字段
  T4  自我观察目录排除（可配置）/ 不影响其他目录 / _is_self_observe 边界 /
      排除计数口径 / 开关关闭回退
"""
import ast
import os
import sys
import tempfile

# ★注意：sys.path.insert(...) 是调用语句（不关闭 ruff import 区）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

import config
from nucleus.self_awareness.FakeLoopDetector import (
    FakeLoopDetector,
    _is_load_name,
    _is_save_name,
    _is_self_observe,
    _self_observe_excludes,
    analyze_fake_loops,
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


#: 含「公开 save + 私有 _load」的模块（T3 目标）
_PRIV = '''# -*- coding: utf-8 -*-
import json


class Store:
    def save(self):
        with open("data/s.json", "w", encoding="utf-8") as f:
            json.dump({}, f)

    def _load(self):
        return None
'''

#: 含「公开 save + _read_flag」—— `_read_flag` 非精确变体，**不得**被识别
_FLAG = '''# -*- coding: utf-8 -*-
import json


class Flags:
    def __init__(self):
        self._flag = False

    def save(self):
        with open("data/f.json", "w", encoding="utf-8") as f:
            json.dump({}, f)

    def _read_flag(self):
        return self._flag
'''

#: 含『私有 _save + 私有 _load』
_PSAVE = '''# -*- coding: utf-8 -*-
import json


class Cache:
    def _save(self):
        with open("data/c.json", "w", encoding="utf-8") as f:
            json.dump({}, f)

    def _load(self):
        return None
'''


def _mkproj(files):
    d = tempfile.mkdtemp(prefix="m38fl_")
    for rel, txt in files.items():
        p = os.path.join(d, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(txt)
    return d


class TestPrivateNameMatching(unittest.TestCase):
    """T3：方法名判据（精确私有变体）。"""

    def test_10_exact_private_matches_when_enabled(self):
        self.assertTrue(_is_save_name("_save", True))
        self.assertTrue(_is_save_name("_write", True))
        self.assertTrue(_is_save_name("_dump", True))
        self.assertTrue(_is_save_name("_persist", True))
        self.assertTrue(_is_load_name("_load", True))
        self.assertTrue(_is_load_name("_read", True))

    def test_11_private_ignored_when_disabled(self):
        self.assertFalse(_is_save_name("_save", False))
        self.assertFalse(_is_load_name("_load", False))
        self.assertFalse(_is_save_name("_save"))          # 默认关闭

    def test_12_suffixed_private_not_matched(self):
        """★保守策略：`_save_xxx` / `_read_flag` 形式**不**识别（实测误报源）。"""
        self.assertFalse(_is_save_name("_save_user_rules", True))
        self.assertFalse(_is_load_name("_read_flag", True))
        self.assertFalse(_is_load_name("_read_method_body", True))
        self.assertFalse(_is_load_name("_load_config", True))

    def test_13_public_still_works(self):
        self.assertTrue(_is_save_name("save"))
        self.assertTrue(_is_save_name("save_all"))
        self.assertTrue(_is_load_name("load"))
        self.assertTrue(_is_load_name("load_async"))
        self.assertFalse(_is_load_name("loads"))          # 无下划线则非族成员


class TestPrivateScan(unittest.TestCase):
    """T3：端到端（临时迷你工程）。"""

    def setUp(self):
        self.proj = _mkproj({"pkg/store.py": _PRIV, "pkg/flag.py": _FLAG})

    def test_20_private_hit_detected(self):
        d = FakeLoopDetector(project_root=self.proj, scan_dirs=["pkg"])
        r = d.scan()
        self.assertTrue(r["private_match"])
        classes = {c["class"] for c in r["candidates"]}
        self.assertIn("Store", classes)          # _load 私有命中
        st = next(c for c in r["candidates"] if c["class"] == "Store")
        self.assertTrue(st["has_private_hit"])
        self.assertEqual(st["private_load_methods"], ["_load"])
        self.assertEqual(st["private_save_methods"], [])
        self.assertIn("private", st["problems"][0])

    def test_21_flag_not_matched(self):
        """`_read_flag` 非精确变体 → Flags 不成为候选（只剩 save，无 load）。"""
        d = FakeLoopDetector(project_root=self.proj, scan_dirs=["pkg"])
        classes = {c["class"] for c in d.scan()["candidates"]}
        self.assertNotIn("Flags", classes)

    def test_22_both_private(self):
        proj = _mkproj({"pkg/cache.py": _PSAVE})
        d = FakeLoopDetector(project_root=proj, scan_dirs=["pkg"])
        c = next(x for x in d.scan()["candidates"] if x["class"] == "Cache")
        self.assertEqual(c["private_save_methods"], ["_save"])
        self.assertEqual(c["private_load_methods"], ["_load"])

    def test_23_switch_off_zero_regression(self):
        with _Switch(ENABLE_FAKE_LOOP_PRIVATE_MATCH=False):
            d = FakeLoopDetector(project_root=self.proj, scan_dirs=["pkg"])
            r = d.scan()
        self.assertFalse(r["private_match"])
        self.assertEqual(r["candidates"], [])      # _load 不被识别 → 无候选

    def test_24_summary_split(self):
        d = FakeLoopDetector(project_root=self.proj, scan_dirs=["pkg"])
        s = d.scan()["summary"]
        self.assertEqual(s["private_hit_candidates"], 1)
        self.assertEqual(s["public_hit_candidates"], 0)
        self.assertGreater(s["private_hit_problems"], 0)

    def test_25_report_distinguishes_visibility(self):
        d = FakeLoopDetector(project_root=self.proj, scan_dirs=["pkg"])
        r = d.scan()
        txt = d.build_report(r)
        self.assertIn("★私有命中", txt)
        self.assertIn("私有方法命中", txt)
        self.assertIn("命中分布", txt)

    def test_26_analyze_entry_transparent(self):
        """入口透传：私有命中 / 自我观察排除均出现在返回值里。"""
        _proj = self.proj
        _old = FakeLoopDetector.__init__

        def _patched(self, project_root=None, scan_dirs=None):
            _old(self, project_root=_proj, scan_dirs=["pkg"])

        try:
            FakeLoopDetector.__init__ = _patched
            r = analyze_fake_loops()
        finally:
            FakeLoopDetector.__init__ = _old
        self.assertIn("private_hit_candidates", r)
        self.assertIn("excluded_self_observation", r)
        self.assertGreaterEqual(r["private_hit_candidates"], 1)


class TestSelfObserveExclude(unittest.TestCase):
    """T4：自我观察噪声排除。"""

    def test_30_default_prefix(self):
        self.assertIn("data/self_awareness", _self_observe_excludes())

    def test_31_is_self_observe_boundaries(self):
        pre = ("data/self_awareness",)
        self.assertTrue(_is_self_observe("data/self_awareness/x.json", pre))
        self.assertTrue(_is_self_observe("data/self_awareness", pre))
        self.assertTrue(_is_self_observe("./data/self_awareness/a/b.json", pre))
        self.assertFalse(_is_self_observe("data/self_awareness2/x.json", pre))
        self.assertFalse(_is_self_observe("data/probe/x.json", pre))
        self.assertFalse(_is_self_observe("", pre))

    def test_32_scan_excludes_self_observation(self):
        proj = _mkproj({"src/gen/a.py": _PRIV, "src/keep/b.py": _PRIV})
        with _Switch(SELF_AWARENESS_EXCLUDE_DIRS=["src/gen"],
                     ENABLE_SELF_AWARENESS_EXCLUDE=True):
            d = FakeLoopDetector(project_root=proj, scan_dirs=["src"])
            r = d.scan()
        self.assertEqual(r["excluded_self_observation"], 1)
        files = {c["file"] for c in r["candidates"]}
        self.assertIn("src/keep/b.py", files)
        self.assertNotIn("src/gen/a.py", files)

    def test_33_switch_off_no_exclude(self):
        proj = _mkproj({"src/gen/a.py": _PRIV, "src/keep/b.py": _PRIV})
        with _Switch(ENABLE_SELF_AWARENESS_EXCLUDE=False):
            d = FakeLoopDetector(project_root=proj, scan_dirs=["src"])
            r = d.scan()
        self.assertEqual(r["excluded_self_observation"], 0)
        files = {c["file"] for c in r["candidates"]}
        self.assertIn("src/gen/a.py", files)

    def test_34_report_shows_exclusion(self):
        proj = _mkproj({"src/gen/a.py": _PRIV, "src/keep/b.py": _PRIV})
        with _Switch(SELF_AWARENESS_EXCLUDE_DIRS=["src/gen"]):
            d = FakeLoopDetector(project_root=proj, scan_dirs=["src"])
            txt = d.build_report(d.scan())
        self.assertIn("已排除自我观察文件 1 个", txt)


class TestSyntaxSanity(unittest.TestCase):
    """T3：判据改动不破坏 AST 解析（回归护栏）。"""

    def test_40_module_parses(self):
        with open(os.path.join(
                _ROOT, "nucleus", "self_awareness", "FakeLoopDetector.py"),
                encoding="utf-8") as f:
            ast.parse(f.read())


if __name__ == "__main__":
    unittest.main()
