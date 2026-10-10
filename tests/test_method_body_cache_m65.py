# -*- coding: utf-8 -*-
"""主线第65批 T3/P2：get_method_body 文件级缓存 门控单测（4例）。

覆盖：put→get 命中返回独立副本 / 文件 mtime 变化→失效（miss）/ 缓存总开关关闭→旁路 /
      统计字段暴露 + LRU(>1000 FIFO 淘汰)。

隔离：用 __new__ 构造轻量实例并手动注入缓存相关属性，避免触发全项目扫描。
"""
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.self_inspector import SelfInspector  # noqa: E402


def _make_si():
    _si = SelfInspector.__new__(SelfInspector)
    _si._scan_cache_enabled = True
    _si._method_body_cache = {}
    _si._method_body_hits = 0
    _si._method_body_misses = 0
    # get_scan_cache_stats 依赖的 L2 扫描缓存统计字段（轻量补齐，避免 AttributeError）
    _si._scan_cache_hits = 0
    _si._scan_cache_misses = 0
    _si._scan_cache_invalidations = 0
    _si._scan_cache = {}
    _si._scan_cache_time = 0.0
    _si._scan_cache_l2_hits = 0
    _si._scan_cache_l2_misses = 0
    return _si


class TestMethodBodyCache(unittest.TestCase):
    def setUp(self):
        self.si = _make_si()
        self.f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8")
        self.f.write("def foo():\n    return 1\n")
        self.f.close()

    def tearDown(self):
        if os.path.isfile(self.f.name):
            os.remove(self.f.name)

    def test_10_put_then_get_hit_returns_copy(self):
        _res = {"body": "x", "name": "foo"}
        self.si._put_method_body_cached(self.f.name, "foo", _res)
        _got = self.si._get_method_body_cached(self.f.name, "foo")
        self.assertIsNotNone(_got)
        self.assertEqual(_got["body"], "x")
        # 返回独立副本：改脏不影响缓存
        _got["body"] = "mutated"
        _again = self.si._get_method_body_cached(self.f.name, "foo")
        self.assertEqual(_again["body"], "x")
        self.assertEqual(self.si._method_body_hits, 2)

    def test_11_mtime_change_invalidates(self):
        self.si._put_method_body_cached(self.f.name, "foo", {"body": "x"})
        # 命中一次
        self.assertIsNotNone(self.si._get_method_body_cached(self.f.name, "foo"))
        # 改变文件 mtime → 缓存失效
        _old = os.stat(self.f.name).st_mtime
        os.utime(self.f.name, (_old + 10.0, _old + 10.0))
        self.assertIsNone(self.si._get_method_body_cached(self.f.name, "foo"))
        self.assertEqual(self.si._method_body_misses, 1, "mtime 变化后应记为 1 次 miss")
        self.assertEqual(self.si._method_body_hits, 1)

    def test_12_scan_cache_disabled_bypasses(self):
        self.si._scan_cache_enabled = False
        self.si._put_method_body_cached(self.f.name, "foo", {"body": "x"})
        self.assertEqual(len(self.si._method_body_cache), 0, "关闭开关不得写入缓存")
        self.assertIsNone(self.si._get_method_body_cached(self.f.name, "foo"))

    def test_13_stats_and_lru_cap(self):
        # LRU 上限 1000：超出后 FIFO 淘汰，size 恒 ≤ 1000
        _n = 1001
        for i in range(_n):
            self.si._put_method_body_cached(self.f.name, "m%d" % i, {"i": i})
        self.assertLessEqual(len(self.si._method_body_cache), 1000)
        # 统计字段存在且类型正确
        _stats = self.si.get_scan_cache_stats()
        self.assertIn("method_body_hits", _stats)
        self.assertIn("method_body_misses", _stats)
        self.assertIn("method_body_cache_size", _stats)
        self.assertEqual(_stats["method_body_cache_size"], len(self.si._method_body_cache))


if __name__ == "__main__":
    unittest.main(verbosity=2)
