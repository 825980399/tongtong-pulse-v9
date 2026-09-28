# -*- coding: utf-8 -*-
"""第64批 T4 门控：队列深度纳入自适应缓存 TTL。

覆盖：RuntimeMetrics.get_queue_depth 读取瞬时深度、深度 >HIGH→延长 600s、
>CRITICAL→延长 900s、<LOW→恢复 300s。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.self_inspector import SelfInspector
from nucleus.runtime_metrics import RuntimeMetrics

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


class TestQueueDepthAdaptive(unittest.TestCase):
    def test_01_get_queue_depth_returns_instant(self):
        rm = RuntimeMetrics()
        rm._last_queue_depth = 123
        self.assertEqual(rm.get_queue_depth(), 123)

    def test_02_adaptive_queue_high_extends(self):
        inst = _fresh_inspector()
        with _CfgRestore(ENABLE_ORGAN_SCAN_ADAPTIVE_TTL=True):
            with mock.patch("psutil.cpu_percent", return_value=10.0):
                with mock.patch("nucleus.runtime_metrics.get_runtime_metrics",
                                return_value=_fake_metrics(6000)):
                    self.assertEqual(inst._get_adaptive_cache_ttl(), 600.0)

    def test_03_adaptive_queue_critical_extends(self):
        inst = _fresh_inspector()
        with _CfgRestore(ENABLE_ORGAN_SCAN_ADAPTIVE_TTL=True):
            with mock.patch("psutil.cpu_percent", return_value=10.0):
                with mock.patch("nucleus.runtime_metrics.get_runtime_metrics",
                                return_value=_fake_metrics(12000)):
                    self.assertEqual(inst._get_adaptive_cache_ttl(), 900.0)

    def test_04_adaptive_queue_low_restores(self):
        inst = _fresh_inspector()
        with _CfgRestore(ENABLE_ORGAN_SCAN_ADAPTIVE_TTL=True):
            with mock.patch("psutil.cpu_percent", return_value=10.0):
                with mock.patch("nucleus.runtime_metrics.get_runtime_metrics",
                                return_value=_fake_metrics(1000)):
                    self.assertEqual(inst._get_adaptive_cache_ttl(), 300.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
