# -*- coding: utf-8 -*-
"""第64批 T3 门控：自适应缓存 TTL（CPU 负载维度）。

覆盖：灰度关闭返回基础 TTL、CPU>80%→600s、>60%→450s、正常→300s。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.self_inspector import SelfInspector

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_inspector():
    inst = SelfInspector.__new__(SelfInspector)
    inst._scan_cache_ttl = 300.0
    inst._project_root = _ROOT
    return inst


class _CfgRestore:
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


def _fake_metrics(depth: int) -> mock.Mock:
    _m = mock.Mock()
    _m.get_queue_depth.return_value = depth
    return _m


class TestAdaptiveTTL(unittest.TestCase):
    def test_01_adaptive_off_returns_base(self):
        inst = _fresh_inspector()
        inst._scan_cache_ttl = 300.0
        with _CfgRestore(ENABLE_ORGAN_SCAN_ADAPTIVE_TTL=False):
            self.assertEqual(inst._get_adaptive_cache_ttl(), 300.0)

    def test_02_cpu_high_returns_600(self):
        inst = _fresh_inspector()
        with _CfgRestore(ENABLE_ORGAN_SCAN_ADAPTIVE_TTL=True):
            # 队列深度取中性区间 [2000,5000]，避免队列分支覆盖 CPU 维度
            with mock.patch("psutil.cpu_percent", return_value=85.0):
                with mock.patch("nucleus.runtime_metrics.get_runtime_metrics",
                                return_value=_fake_metrics(3000)):
                    self.assertEqual(inst._get_adaptive_cache_ttl(), 600.0)

    def test_03_cpu_mid_returns_450(self):
        inst = _fresh_inspector()
        with _CfgRestore(ENABLE_ORGAN_SCAN_ADAPTIVE_TTL=True):
            with mock.patch("psutil.cpu_percent", return_value=70.0):
                with mock.patch("nucleus.runtime_metrics.get_runtime_metrics",
                                return_value=_fake_metrics(3000)):
                    self.assertEqual(inst._get_adaptive_cache_ttl(), 450.0)

    def test_04_cpu_normal_returns_300(self):
        inst = _fresh_inspector()
        with _CfgRestore(ENABLE_ORGAN_SCAN_ADAPTIVE_TTL=True):
            # CPU 正常且队列深度为 0（< LOW 阈值）→ 基础 300s
            with mock.patch("psutil.cpu_percent", return_value=30.0):
                _fake = mock.Mock()
                _fake.get_queue_depth.return_value = 0
                with mock.patch("nucleus.runtime_metrics.get_runtime_metrics", return_value=_fake):
                    self.assertEqual(inst._get_adaptive_cache_ttl(), 300.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
