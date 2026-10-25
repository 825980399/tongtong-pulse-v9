# -*- coding: utf-8 -*-
"""364 刀 B4 第二刀验收：器官 AST 扫描缓存（开灯前置）。

只守不变量：结果一致性 / 缓存命中加速 / 失效键生效 / 清单口径不变。
不锁绝对耗时（机器差异大），只锁「缓存后应比冷启动快一个数量级」的宽松界。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.cognitive.organ_inventory as _oi  # noqa: E402

_SAMPLE = '''# -*- coding: utf-8 -*-
from base.BasePulseOrgan import BasePulseOrgan


class PulseFakeX(BasePulseOrgan):
    def __init__(self, organ_name="假器官X"):
        self.organ_name = organ_name

    def a(self):
        return 1
'''

_SAMPLE2 = _SAMPLE + '''
    def b(self):
        return 2
'''


class TestB4ScanCache(unittest.TestCase):
    def test_01_result_stable_across_calls(self):
        _oi.invalidate_organ_scan_cache()
        r1 = _oi.scan_organ_classes()
        r2 = _oi.scan_organ_classes()
        self.assertEqual(r1, r2)
        self.assertTrue(r1, "器官清单不得为空（判别力自证）")

    def test_02_cache_makes_second_call_fast(self):
        _oi.invalidate_organ_scan_cache()
        _t0 = time.perf_counter()
        _oi.scan_organ_classes()
        cold = time.perf_counter() - _t0
        _t1 = time.perf_counter()
        _oi.scan_organ_classes()
        warm = time.perf_counter() - _t1
        # 宽松界：缓存命中应比冷启动快一个数量级（实测 2.09s -> 0.011s）
        self.assertLess(warm, max(0.5, cold / 10.0),
                        f"缓存未生效：冷启动 {cold:.3f}s / 缓存后 {warm:.3f}s")
        self.assertGreater(_oi.organ_scan_cache_size(), 0)

    def test_03_invalidate_then_rescan_consistent(self):
        _oi.scan_organ_classes()
        _n = _oi.invalidate_organ_scan_cache()
        self.assertGreater(_n, 0)
        self.assertEqual(_oi.organ_scan_cache_size(), 0)
        r = _oi.scan_organ_classes()
        self.assertTrue(r)

    def test_04_cache_invalidates_on_file_change(self):
        tmp = tempfile.mkdtemp(prefix="m364_b4_")
        try:
            _p = os.path.join(tmp, "PulseFakeX.py")
            with open(_p, "w", encoding="utf-8") as f:
                f.write(_SAMPLE)
            _orig = _oi._organs_root
            _oi._organs_root = lambda: tmp
            try:
                r1 = _oi.scan_organ_classes()
                self.assertEqual(len(r1), 1)
                self.assertEqual(r1[0]["method_count"], 2)  # __init__ + a
                # 改写文件（内容变 ⇒ size 变 ⇒ 缓存键失效）
                with open(_p, "w", encoding="utf-8") as f:
                    f.write(_SAMPLE2)
                r2 = _oi.scan_organ_classes()
                self.assertEqual(len(r2), 1)
                self.assertEqual(r2[0]["method_count"], 3, "文件改动后缓存未失效")
            finally:
                _oi._organs_root = _orig
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_05_organ_names_all_unchanged(self):
        """清单口径守门：缓存不得改变 organ_names_all 的输出。"""
        _oi.invalidate_organ_scan_cache()
        a = _oi.organ_names_all()
        b = _oi.organ_names_all()
        self.assertEqual(a, b)
        self.assertIn("代码学习", a)
        self.assertGreaterEqual(len(a), 50)


if __name__ == "__main__":
    unittest.main()
