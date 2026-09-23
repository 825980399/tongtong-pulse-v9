# -*- coding: utf-8 -*-
"""主线第13批 T4 / P2-87：InfoField 池重建并发计数泄漏 门控单测。

轻量范式（沿用 test_info_field.py）：InfoField.__new__() 跳过重型 __init__。

覆盖：
  1) _track_layer_inflight 增减与归零清理
  2) _check_concurrency_consistency：一致 / 不一致检测
  3) 池重建（开关开启）：按在途数精确递减，不影响其他层
  4) 池重建（开关关闭）：退回 clear 全局归零
  5) 池重建后计数不为负（下限保护）
"""
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from nucleus.field.InfoField import _PULSE_LAYER_L1, _PULSE_LAYER_L2, _PULSE_LAYER_L3, InfoField


class _FakePool:
    def __init__(self, max_workers=2):
        self._max_workers = max_workers
        self._shutdown_args = None

    def shutdown(self, wait=False, cancel_futures=False):
        self._shutdown_args = (wait, cancel_futures)


def _make(pools=None, recovery=True):
    inst = InfoField.__new__(InfoField)
    inst._layer_pools = dict(pools) if pools else {
        _PULSE_LAYER_L1: _FakePool(4),
        _PULSE_LAYER_L2: _FakePool(2),
        _PULSE_LAYER_L3: _FakePool(2),
    }
    inst._organ_concurrent_count = {}
    inst._organ_concurrent_lock = threading.Lock()
    inst._organ_max_concurrent = 0
    inst._layer_organ_inflight = {}
    inst._layer_disabled = {}
    inst._module_logger = MagicMock()
    inst._concurrency_recovery_enabled = lambda: recovery
    return inst


class TestTrackInflight(unittest.TestCase):
    def test_增减与归零清理(self):
        inst = _make()
        inst._track_layer_inflight(_PULSE_LAYER_L3, "血管", +1)
        inst._track_layer_inflight(_PULSE_LAYER_L3, "血管", +1)
        inst._track_layer_inflight(_PULSE_LAYER_L3, "视觉皮层", +1)
        self.assertEqual(inst._layer_organ_inflight[_PULSE_LAYER_L3]["血管"], 2)
        inst._track_layer_inflight(_PULSE_LAYER_L3, "血管", -1)
        self.assertEqual(inst._layer_organ_inflight[_PULSE_LAYER_L3]["血管"], 1)
        inst._track_layer_inflight(_PULSE_LAYER_L3, "血管", -1)
        self.assertNotIn("血管", inst._layer_organ_inflight[_PULSE_LAYER_L3])
        inst._track_layer_inflight(_PULSE_LAYER_L3, "视觉皮层", -1)
        self.assertEqual(inst._layer_organ_inflight.get(_PULSE_LAYER_L3), {})

    def test_不为负(self):
        inst = _make()
        inst._track_layer_inflight(_PULSE_LAYER_L3, "血管", -1)
        self.assertEqual(inst._layer_organ_inflight.get(_PULSE_LAYER_L3, {}), {})


class TestConsistencyCheck(unittest.TestCase):
    def test_一致时consistent为True(self):
        inst = _make()
        inst._organ_concurrent_count = {"血管": 2, "心脏": 1}
        inst._track_layer_inflight(_PULSE_LAYER_L3, "血管", +2)
        inst._track_layer_inflight(_PULSE_LAYER_L3, "心脏", +1)
        _r = inst._check_concurrency_consistency()
        self.assertTrue(_r["consistent"])
        self.assertEqual(_r["diff"], 0)
        self.assertEqual(_r["organ_count_total"], 3)

    def test_不一致时diff非零(self):
        inst = _make()
        inst._organ_concurrent_count = {"血管": 5}
        inst._track_layer_inflight(_PULSE_LAYER_L3, "血管", +2)
        _r = inst._check_concurrency_consistency()
        self.assertFalse(_r["consistent"])
        self.assertEqual(_r["diff"], 3)

    def test_按层校验(self):
        inst = _make()
        inst._organ_concurrent_count = {"血管": 2, "心脏": 1}
        inst._track_layer_inflight(_PULSE_LAYER_L3, "血管", +2)
        inst._track_layer_inflight(_PULSE_LAYER_L1, "心脏", +1)
        _r = inst._check_concurrency_consistency(_PULSE_LAYER_L3)
        self.assertEqual(_r["inflight_total"], 2)


class TestResizePoolRecovery(unittest.TestCase):
    def _setup_counts(self, inst):
        # L3 层有 2 个血管在途 + 1 个视觉皮层；L1 层另有 1 个心脏在途
        inst._organ_concurrent_count = {"血管": 2, "视觉皮层": 1, "心脏": 1}
        inst._track_layer_inflight(_PULSE_LAYER_L3, "血管", +2)
        inst._track_layer_inflight(_PULSE_LAYER_L3, "视觉皮层", +1)
        inst._track_layer_inflight(_PULSE_LAYER_L1, "心脏", +1)

    def test_开启时精确递减(self):
        inst = _make(recovery=True)
        self._setup_counts(inst)
        inst._resize_layer_pool(_PULSE_LAYER_L3, 1, force=True)
        # L3 作废的 血管2+视觉皮层1 → 递减；L1 的心脏1 不受影响
        self.assertEqual(inst._organ_concurrent_count.get("血管", 0), 0)
        self.assertEqual(inst._organ_concurrent_count.get("视觉皮层", 0), 0)
        self.assertEqual(inst._organ_concurrent_count.get("心脏", 0), 1)

    def test_关闭时clear全局(self):
        inst = _make(recovery=False)
        self._setup_counts(inst)
        inst._resize_layer_pool(_PULSE_LAYER_L3, 1, force=True)
        # 旧行为：全局 clear
        self.assertEqual(inst._organ_concurrent_count, {})

    def test_计数不为负(self):
        inst = _make(recovery=True)
        inst._organ_concurrent_count = {"血管": 1}
        inst._track_layer_inflight(_PULSE_LAYER_L3, "血管", +3)  # 在途 > 计数（异常场景）
        inst._resize_layer_pool(_PULSE_LAYER_L3, 1, force=True)
        self.assertGreaterEqual(inst._organ_concurrent_count.get("血管", 0), 0)

    def test_旧池shutdown参数正确(self):
        inst = _make(recovery=True)
        _old = inst._layer_pools[_PULSE_LAYER_L3]
        inst._resize_layer_pool(_PULSE_LAYER_L3, 1, force=True)
        self.assertEqual(_old._shutdown_args, (False, True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
