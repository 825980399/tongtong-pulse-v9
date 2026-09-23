# -*- coding: utf-8 -*-
"""第64批 T1/T2 门控：器官扫描 TTL 缓存 + 二级缓存。

覆盖：TTL 命中/未命中、TTL 过期、mtime 变化失效、手动失效、灰度关闭零回归、
浅拷贝独立性、统计字段、二级缓存版本戳联动失效。
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.self_inspector import SelfInspector  # noqa: F401  (验证可导入)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_inspector():
    """轻量实例化（绕开重型 __init__），手动注入缓存相关属性。"""
    inst = SelfInspector.__new__(SelfInspector)
    inst._scan_cache = {}
    inst._scan_cache_time = 0.0
    inst._scan_cache_ttl = 300.0
    inst._scan_cache_file_mtimes = {}
    inst._scan_cache_hits = 0
    inst._scan_cache_misses = 0
    inst._scan_cache_invalidations = 0
    inst._scan_cache_enabled = True
    inst._organ_file_cache = {}
    inst._structure_cache = {}
    inst._method_info_cache = {}
    inst._l2_hits = 0
    inst._l2_misses = 0
    inst._scan_stats_last_log = 0.0
    # ★主线第90批（顺手修存量）：第65批 T3/P2 在 __init__ 里新增了
    #   get_method_body 文件级缓存的三个统计属性，而本用例走 __new__ 绕开
    #   __init__ ⇒ get_scan_cache_stats() 抛 AttributeError，自第65批起一直红。
    #   此处补齐（纯测试侧注入，无语义改动）。
    inst._method_body_cache = {}
    inst._method_body_hits = 0
    inst._method_body_misses = 0
    inst._project_root = _ROOT
    return inst


class _CfgRestore:
    """上下文管理器：临时改 config 属性，退出还原。"""
    def __init__(self, **kw):
        self._kw = kw
        self._saved = {}
    def __enter__(self):
        for k, v in self._kw.items():
            self._saved[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self
    def __exit__(self, *a):
        for k, v in self._saved.items():
            if v is None:
                delattr(config, k)
            else:
                setattr(config, k, v)


class TestScanCacheTTL(unittest.TestCase):
    """T1：TTL 缓存命中/未命中/过期/失效。"""

    def _fake_organs(self):
        return {"PulseHeart": {"file_path": "organs/core/PulseHeart.py", "classes": [], "methods": []}}

    def test_01_cache_hit_returns_cached_and_counts(self):
        inst = _fresh_inspector()
        inst._scan_cache = self._fake_organs()
        inst._scan_cache_time = time.time()
        calls = {"n": 0}
        inst._do_scan_all_organs = lambda: (calls.__setitem__("n", calls["n"] + 1) or self._fake_organs())
        inst._update_file_mtimes = lambda: None
        result = inst._scan_all_organs()
        self.assertEqual(calls["n"], 0, "命中不应触发扫描")
        self.assertEqual(inst._scan_cache_hits, 1)
        self.assertEqual(inst._scan_cache_misses, 0)
        # T1 设计为浅拷贝返回：内容相等但非同一对象（防止调用方替换顶层键污染缓存）
        self.assertEqual(result, inst._scan_cache)
        self.assertIsNot(result, inst._scan_cache)

    def test_02_cache_miss_calls_do_scan_and_stores(self):
        inst = _fresh_inspector()
        calls = {"n": 0}
        inst._do_scan_all_organs = lambda: (calls.__setitem__("n", calls["n"] + 1) or self._fake_organs())
        inst._update_file_mtimes = lambda: None
        result = inst._scan_all_organs()
        self.assertEqual(calls["n"], 1)
        self.assertEqual(inst._scan_cache_misses, 1)
        self.assertEqual(inst._scan_cache_hits, 0)
        self.assertEqual(result, inst._scan_cache)

    def test_03_cache_expiry_by_ttl(self):
        inst = _fresh_inspector()
        inst._scan_cache = self._fake_organs()
        inst._scan_cache_time = time.time() - 1000.0  # 已过期
        inst._scan_cache_ttl = 300.0
        calls = {"n": 0}
        inst._do_scan_all_organs = lambda: (calls.__setitem__("n", calls["n"] + 1) or self._fake_organs())
        inst._update_file_mtimes = lambda: None
        inst._scan_all_organs()
        self.assertEqual(calls["n"], 1, "过期应重新扫描")

    def test_04_mtime_change_invalidates(self):
        inst = _fresh_inspector()
        inst._scan_cache = self._fake_organs()
        inst._scan_cache_file_mtimes = {"/fake/organ.py": 1000.0}
        inst._scan_cache_time = time.time()
        calls = {"n": 0}
        inst._do_scan_all_organs = lambda: (calls.__setitem__("n", calls["n"] + 1) or self._fake_organs())
        inst._update_file_mtimes = lambda: None
        # 文件未变 → 命中（mock 必须在调用期间生效，否则真实缺失文件触发 OSError 被判变化）
        with mock.patch("os.path.getmtime", return_value=1000.0):
            inst._scan_all_organs()
        self.assertEqual(calls["n"], 0)
        # 文件 mtime 变化 → 失效并重扫
        with mock.patch("os.path.getmtime", return_value=2000.0):
            inst._scan_all_organs()
        self.assertEqual(calls["n"], 1)
        self.assertEqual(inst._scan_cache_invalidations, 1)

    def test_05_manual_invalidate(self):
        inst = _fresh_inspector()
        inst._scan_cache = self._fake_organs()
        inst._scan_cache_time = time.time()
        calls = {"n": 0}
        inst._do_scan_all_organs = lambda: (calls.__setitem__("n", calls["n"] + 1) or self._fake_organs())
        inst._update_file_mtimes = lambda: None
        inst._scan_all_organs()
        self.assertEqual(calls["n"], 0)
        inst.invalidate_scan_cache()
        self.assertEqual(inst._scan_cache, {})
        self.assertEqual(inst._scan_cache_invalidations, 1)
        inst._scan_all_organs()
        self.assertEqual(calls["n"], 1, "失效后下次应重扫")

    def test_06_gray_off_returns_fresh_each_time(self):
        inst = _fresh_inspector()
        calls = {"n": 0}
        inst._do_scan_all_organs = lambda: (calls.__setitem__("n", calls["n"] + 1) or self._fake_organs())
        inst._update_file_mtimes = lambda: None
        with _CfgRestore(ENABLE_ORGAN_SCAN_CACHE=False):
            inst._scan_all_organs()
            inst._scan_all_organs()
        self.assertEqual(calls["n"], 2, "关闭缓存时每次都应扫描（零回归）")

    def test_07_shallow_copy_return_is_independent(self):
        inst = _fresh_inspector()
        inst._scan_cache = self._fake_organs()
        inst._scan_cache_time = time.time()
        inst._do_scan_all_organs = lambda: self._fake_organs()
        inst._update_file_mtimes = lambda: None
        result = inst._scan_all_organs()
        # 浅拷贝：顶层是新字典，调用方替换顶层键不会污染缓存
        self.assertIsNot(result, inst._scan_cache)
        result["__probe__"] = 1
        self.assertNotIn("__probe__", inst._scan_cache)
        # 注：嵌套对象为浅引用（T1 设计如此），只读消费方不改嵌套结构，安全

    def test_08_stats_fields_present(self):
        inst = _fresh_inspector()
        inst._scan_cache = self._fake_organs()
        inst._scan_cache_time = time.time()
        inst._do_scan_all_organs = lambda: self._fake_organs()
        inst._update_file_mtimes = lambda: None
        inst._scan_all_organs()
        s = inst.get_scan_cache_stats()
        for k in ("hits", "misses", "invalidations", "hit_rate", "cache_size",
                  "cache_age_seconds", "l2_hits", "l2_misses", "l2_hit_rate"):
            self.assertIn(k, s, f"统计缺少字段 {k}")
        self.assertEqual(s["hits"], 1)
        self.assertTrue(s["hit_rate"].endswith("%"))


class TestL2CacheVersionStamp(unittest.TestCase):
    """T2：二级缓存与扫描缓存版本戳联动失效。"""

    def _fake_organs(self):
        return {
            "PulseHeart": {
                "file_path": "organs/core/PulseHeart.py",
                "classes": [{"name": "PulseHeart"}],
                "methods": [{"name": "_beat", "line_number": 10}],
                "method_count": 1,
            }
        }

    def _inst_with_scan(self):
        inst = _fresh_inspector()
        inst._scan_cache = self._fake_organs()
        inst._scan_cache_time = time.time()
        inst._do_scan_all_organs = lambda: self._fake_organs()
        inst._update_file_mtimes = lambda: None
        return inst

    def test_09_l2_resolve_organ_file_cache_hit(self):
        inst = self._inst_with_scan()
        r1 = inst.resolve_organ_file("PulseHeart")
        r2 = inst.resolve_organ_file("PulseHeart")
        self.assertEqual(r1, r2)
        self.assertEqual(inst._l2_hits, 1)  # 第二次走二级缓存命中
        self.assertEqual(inst._l2_misses, 1)  # 第一次未命中触发扫描

    def test_10_l2_invalidated_on_scan_refresh(self):
        inst = self._inst_with_scan()
        inst.resolve_organ_file("PulseHeart")
        self.assertEqual(inst._l2_hits, 0)
        # 扫描缓存刷新（版本戳变化）→ 二级缓存应失效
        inst._scan_cache_time = time.time() + 1.0
        r = inst.resolve_organ_file("PulseHeart")
        self.assertIsNotNone(r)
        self.assertEqual(inst._l2_misses, 2, "版本戳变化后应再次未命中")


if __name__ == "__main__":
    unittest.main(verbosity=2)
