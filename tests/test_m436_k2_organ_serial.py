# -*- coding: utf-8 -*-
"""第436批刀2 单测：同器官串行、跨器官并行（每器官 RLock）。

守三件事：
  ① 同器官拿到**同一把**锁、跨器官拿到**不同**锁；
  ② `_dispatch_handler` 下同器官的两条脉冲**不重叠执行**（串行）；
  ③ `ENABLE_M436_ORGAN_SERIAL=False` ⇒ 逐字回到「同器官可并发」。
"""
import os
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import nucleus.field.InfoField as IF


def _bare_field():
    """不经 __init__ 的裸实例（只测并发锁，不起器官总线）。"""
    _f = object.__new__(IF.InfoField)
    _f._organ_exec_locks = {}
    _f._organ_exec_locks_guard = threading.Lock()
    _f._l0_inflight = {}
    _f._handled_lock = threading.Lock()
    _f._total_handled = 0
    _f._task_count_lock = threading.Lock()
    _f._inflight_dispatch_count = 0
    _f._organ_last_active = {}
    _f.pulse_core = None
    _f._layer_rejected_count = {}
    _f._layer_inflight = {}
    _f._organ_concurrent_lock = threading.Lock()
    _f._organ_concurrent_count = {}
    return _f


def test_same_organ_same_lock():
    _f = _bare_field()
    _a = _f._m436_organ_lock("心脏")
    _b = _f._m436_organ_lock("心脏")
    _c = _f._m436_organ_lock("肝脏")
    assert _a is _b, "同器官应返回同一把锁"
    assert _a is not _c, "不同器官应是不同锁（跨器官并行）"


def test_same_organ_serialized():
    """同器官两条脉冲不得重叠：用「进入计数峰值」判定。"""
    _f = _bare_field()
    _f._m436_organ_serial_on = lambda: True
    _inside = [0]
    _peak = [0]
    _lock = threading.Lock()

    def _handler(_pulse):
        with _lock:
            _inside[0] += 1
            _peak[0] = max(_peak[0], _inside[0])
        time.sleep(0.15)
        with _lock:
            _inside[0] -= 1

    _ts = [threading.Thread(target=_f._dispatch_handler,
                            args=({"pulse_id": "p%d" % i}, _handler, "心脏", "parallel"))
           for i in range(3)]
    for _t in _ts:
        _t.start()
    for _t in _ts:
        _t.join(5.0)
    assert _peak[0] == 1, f"同器官并发峰值={_peak[0]}（应为 1，串行）"


def test_cross_organ_parallel():
    """跨器官应能并行：两个器官各 0.15s，总耗时应 < 0.28s。"""
    _f = _bare_field()
    _f._m436_organ_serial_on = lambda: True
    _inside = [0]
    _peak = [0]
    _lock = threading.Lock()

    def _handler(_pulse):
        with _lock:
            _inside[0] += 1
            _peak[0] = max(_peak[0], _inside[0])
        time.sleep(0.15)
        with _lock:
            _inside[0] -= 1

    _t0 = time.time()
    _ts = [threading.Thread(target=_f._dispatch_handler,
                            args=({"pulse_id": "c%d" % i}, _handler, _org, "parallel"))
           for i, _org in enumerate(("心脏", "肝脏"))]
    for _t in _ts:
        _t.start()
    for _t in _ts:
        _t.join(5.0)
    _sec = time.time() - _t0
    assert _peak[0] >= 2, f"跨器官并发峰值={_peak[0]}（应 ≥2，并行）"
    assert _sec < 0.28, f"跨器官总耗时 {_sec:.3f}s（应 <0.28s，说明真并行）"


def test_switch_off_allows_concurrent():
    """关灰度 ⇒ 同器官可并发（峰值应 >1）。"""
    _f = _bare_field()
    _f._m436_organ_serial_on = lambda: False
    _inside = [0]
    _peak = [0]
    _lock = threading.Lock()

    def _handler(_pulse):
        with _lock:
            _inside[0] += 1
            _peak[0] = max(_peak[0], _inside[0])
        time.sleep(0.12)
        with _lock:
            _inside[0] -= 1

    _ts = [threading.Thread(target=_f._dispatch_handler,
                            args=({"pulse_id": "o%d" % i}, _handler, "心脏", "parallel"))
           for i in range(3)]
    for _t in _ts:
        _t.start()
    for _t in _ts:
        _t.join(5.0)
    assert _peak[0] > 1, f"关开关后同器官应可并发，峰值={_peak[0]}"
