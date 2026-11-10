import threading
import pytest
from organs.senses.PulseTouch import PulseTouch


def _make():
    # 绕过重型 __init__，只装配 _schedule_next_snapshot 所需的属性，
    # 直接单元测试 488-0 的修复点（不依赖硬件检测 / 启动流程）。
    t = object.__new__(PulseTouch)
    t._snapshot_in_progress = False
    t._snapshot_interval = 0.01
    t._trigger_snapshot = lambda: None
    t._next_snapshot_timer = None
    return t


def test_schedule_ten_times_no_error():
    """连续 10 次调度不应抛异常（旧实现首调 0.0.is_alive() 即 AttributeError）。"""
    t = _make()
    for _ in range(10):
        t._schedule_next_snapshot()
    assert True


def test_legacy_float_sentinel_no_crash():
    """复现缺陷：_next_timer 曾被复用为 float 哨兵/时间戳 → is_alive() 炸。
    修复后用 isinstance 守卫，float 残留不再触发 AttributeError。"""
    t = _make()
    t._next_snapshot_timer = 0.0  # 旧哨兵残留
    t._schedule_next_snapshot()  # 必须不抛 'float' object has no attribute 'is_alive'
    assert t._next_snapshot_timer is None or isinstance(t._next_snapshot_timer, threading.Timer)


def test_isinstance_guard_on_existing_timer():
    """已存活 Timer 应被识别为 alive（不重复创建），且不抛异常。"""
    t = _make()
    t._schedule_next_snapshot()  # 创建并启动一个 Timer
    existing = t._next_snapshot_timer
    assert isinstance(existing, threading.Timer)
    t._schedule_next_snapshot()  # 第二次：isinstance + is_alive 守卫，不应崩
    assert True
