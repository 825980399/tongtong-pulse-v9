# -*- coding: utf-8 -*-
"""InfoField L3 背压测试（≥5 例）：动态扩缩容、背压水位警告、拒绝分发、突发检测。

轻量范式：InfoField.__new__() 跳过重型 __init__（含看门狗线程），手动挂载所需属性与 fake pool。
"""
import os
import sys
import time
import unittest
from unittest.mock import MagicMock

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from nucleus.field.InfoField import _PULSE_LAYER_L3, InfoField


class _FakeQueue:
    def __init__(self, depth):
        self._d = depth

    def qsize(self):
        return self._d


class _FakePool:
    def __init__(self, depth, max_workers=2):
        self._work_queue = _FakeQueue(depth)
        self._max_workers = max_workers


def _make(depth, max_workers=2, burst=True, up_cd=0.0, down_cd=0.0):
    inst = InfoField.__new__(InfoField)
    inst._layer_pools = {_PULSE_LAYER_L3: _FakePool(depth, max_workers)}
    inst._layer_queue_limits = {_PULSE_LAYER_L3: 100}
    inst._layer_rejected_count = {_PULSE_LAYER_L3: 0}
    inst._layer_overflow_allowed = {_PULSE_LAYER_L3: 0}
    inst._overflow_priority_allow = 3
    inst._l3_dynamic_enabled = True
    inst._l3_last_scale_ts = 0.0
    inst._l3_scale_cooldown = 0.0
    inst._l3_scale_up_cooldown = up_cd
    inst._l3_scale_down_cooldown = down_cd
    inst._l3_base_workers = 2
    inst._l3_max_dynamic_workers = 5
    inst._l3_scale_up_thresholds = [0.70, 0.85, 0.95]
    inst._l3_burst_detection_enabled = burst
    inst._l3_burst_growth_threshold = 0.50  # ★主线第7批 P1-67：默认提高到 0.50
    inst._l3_min_workers = 3                  # ★主线第7批 P1-67：最小 worker
    inst._l3_scale_down_buffer_sec = 30.0     # ★主线第7批 P1-67：缩容延迟缓冲
    # ★主线第75批 T1：新增属性的轻量挂载（与 InfoField.__init__ 保持一致）
    inst._l3_scale_step = 1                   # 单次调整 worker 数（平滑±1）
    inst._l3_hysteresis_down_ratio = 0.50     # 滞回下阈值
    inst._l3_hysteresis_enabled = True        # 滞回保持带开关
    inst._l3_burst_window = []
    inst._l3_below_half_since = 0.0
    inst._l3_scale_up_count = 0
    inst._l3_scale_down_count = 0
    inst._l3_last_scale_reason = ""
    inst._l3_demand_start_ts = 0.0
    inst._l3_response_times = []
    inst._l3_depth_sample_ts = 0.0
    inst._l3_depth_history = []
    inst._l3_producer_counts = {}
    inst._l3_completion_window = []
    inst._module_logger = MagicMock()
    inst._resize_layer_pool = MagicMock()
    inst._record_phase18_l3 = MagicMock()
    return inst


class TestL3DynamicScaling(unittest.TestCase):
    def test_深度70扩容到3(self):
        inst = _make(70, max_workers=2)
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_args[0][1], 3)

    def test_深度95扩容平滑到3(self):
        # ★主线第75批 T1：深度 0.95 仍只 +1（cur=2→3），不再 +3 跳变
        inst = _make(95, max_workers=2)
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_args[0][1], 3)

    def test_突发增长平滑扩到3(self):
        # ★主线第75批 T1：突发平滑，单次只 +1（2→3），不再 +2 跳变
        inst = _make(50, max_workers=2)
        now = time.time()
        inst._l3_burst_window = [(now - 8, 10), (now - 4, 30)]
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_args[0][1], 3)
        self.assertEqual(inst._l3_last_scale_reason, "burst")

    def test_突发增长直扩上限(self):
        # 历史用例保留：阈值 0.50 仍可被大增长触发，但★主线第75批 T1 平滑只 +1（cur=3→4）
        inst = _make(50, max_workers=3)
        now = time.time()
        inst._l3_burst_window = [(now - 8, 10), (now - 4, 30)]
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_args[0][1], 4)
        self.assertEqual(inst._l3_last_scale_reason, "burst")


    def test_最小worker不低于3(self):
        # ★主线第7批 P1-67：即便队列长期低位，worker 也不跌破最小数 3
        inst = _make(10, max_workers=3)  # ratio=0.10
        inst._l3_min_workers = 3
        inst._l3_scale_down_buffer_sec = 30.0
        # 模拟已持续低于 0.5 达 60s
        inst._l3_below_half_since = time.time() - 60.0
        inst._l3_last_scale_ts = 0.0
        inst._auto_scale_l3_by_depth()
        # cur=3, 目标 max(3-1,3)=3 == cur → 不调用 resize
        inst._resize_layer_pool.assert_not_called()

    def test_缩容延迟缓冲生效(self):
        # ★主线第7批 P1-67：低于 0.5 但不足 30s 缓冲 → 不缩容；足 30s → 缩 1
        inst = _make(30, max_workers=5)  # ratio=0.30
        inst._l3_min_workers = 3
        inst._l3_scale_down_buffer_sec = 30.0

        inst._l3_below_half_since = time.time() - 5.0  # 仅 5s
        inst._l3_last_scale_ts = 0.0
        inst._auto_scale_l3_by_depth()
        inst._resize_layer_pool.assert_not_called()

        inst._l3_below_half_since = time.time() - 40.0  # 已 40s
        inst._l3_last_scale_ts = 0.0
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_args[0][1], 4)  # 5→4

    def test_深度85_二级扩容到4(self):
        # ★主线第75批 T1：深度跨过第二档阈值 0.85，但平滑只 +1（cur=2→3）
        inst = _make(85, max_workers=2)
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_args[0][1], 3)

    def test_扩容冷却抑制重复扩容(self):
        # ★主线第7批 P1-67：扩容冷却 15s，短时间内重复触发只扩容一次
        inst = _make(95, max_workers=2)
        inst._l3_scale_up_cooldown = 15.0
        inst._auto_scale_l3_by_depth()        # 第一次：2→5
        self.assertEqual(inst._resize_layer_pool.call_count, 1)
        inst._auto_scale_l3_by_depth()        # 冷却期内：被抑制
        inst._auto_scale_l3_by_depth()        # 冷却期内：被抑制
        self.assertEqual(inst._resize_layer_pool.call_count, 1)
        # 冷却结束后可再次触发（模拟时间推进 16s）
        inst._l3_last_scale_ts = time.time() - 16.0
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_count, 2)

    def test_缩容目标不低于最小worker(self):
        # ★主线第7批 P1-67：cur=4 低于半载且稳定 40s → 缩到 3（触及最小 worker 地板，不会到 2）
        inst = _make(30, max_workers=4)  # ratio=0.30
        inst._l3_min_workers = 3
        inst._l3_scale_down_buffer_sec = 30.0
        inst._l3_below_half_since = time.time() - 40.0
        inst._l3_last_scale_ts = 0.0
        inst._auto_scale_l3_by_depth()
        self.assertEqual(inst._resize_layer_pool.call_args[0][1], 3)

    def test_队列震荡不频繁扩缩(self):
        # ★主线第7批 P1-67：模拟剧烈震荡输入，扩缩容次数应被有效抑制（≤3）
        inst = _make(18, max_workers=3)  # cur=3, ratio=0.18
        inst._l3_min_workers = 3
        inst._l3_scale_down_buffer_sec = 30.0
        inst._l3_burst_growth_threshold = 0.50
        inst._l3_scale_up_cooldown = 0.0
        inst._l3_scale_down_cooldown = 0.0
        depths = [18, 21, 44, 20, 18, 40, 22, 19, 45, 20]
        now = time.time()
        for i, d in enumerate(depths):
            pool = inst._layer_pools[_PULSE_LAYER_L3]
            pool._work_queue._d = d
            inst._l3_last_scale_ts = 0.0      # 关闭冷却，只测逻辑
            inst._l3_below_half_since = 0.0   # 每步重置，永远攒不满 30s 缓冲
            inst._l3_burst_window = [(now + i, d)]  # 单点窗口，无法计算增长→不触发突发
            inst._auto_scale_l3_by_depth()
        total = inst._l3_scale_up_count + inst._l3_scale_down_count
        self.assertLessEqual(total, 3)


class TestL3Backpressure(unittest.TestCase):
    def test_满队列低优先级拒绝(self):
        inst = _make(100, max_workers=2)
        self.assertFalse(inst._admit_layer_task(_PULSE_LAYER_L3, inst._layer_pools[_PULSE_LAYER_L3], "本体感知", 8))
        self.assertEqual(inst._layer_rejected_count[_PULSE_LAYER_L3], 1)

    def test_满队列高优先级降级放行(self):
        inst = _make(100, max_workers=2)
        self.assertTrue(inst._admit_layer_task(_PULSE_LAYER_L3, inst._layer_pools[_PULSE_LAYER_L3], "血管", 3))
        self.assertEqual(inst._layer_overflow_allowed[_PULSE_LAYER_L3], 1)


class TestL3Telemetry(unittest.TestCase):
    def test_telemetry结构完整(self):
        inst = _make(42, max_workers=3)
        inst._layer_rejected_count[_PULSE_LAYER_L3] = 7
        inst._l3_depth_history = [(1000.0, 80, 100), (1060.0, 90, 100)]
        inst._l3_producer_counts = {"人格内核": 5, "控制器": 10}
        inst._l3_completion_window = [time.time()] * 30
        tel = inst.get_l3_telemetry()
        self.assertEqual(tel["current_depth"], 42)
        self.assertEqual(tel["rejected"], 7)
        self.assertEqual(len(tel["depth_history"]), 2)
        self.assertEqual(tel["producer_top5"][0]["organ"], "控制器")

    def test_scaling_stats初始(self):
        inst = _make(85, max_workers=2)
        s = inst.get_l3_scaling_stats()
        self.assertEqual(s["scale_up_count"], 0)
        self.assertEqual(s["up_cooldown"], 0.0)
        self.assertTrue(s["burst_enabled"])
        # ★主线第7批 P1-67：新增字段
        self.assertEqual(s["min_workers"], 3)
        self.assertEqual(s["scale_down_buffer_sec"], 30.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
