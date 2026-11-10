# -*- coding: utf-8 -*-
"""491-6 死锁等待图单测（默认关，绝不自动重启）。"""
import pytest

import config
from nucleus import m491_deadlock as dl
from nucleus.field.InfoField import InfoField
from nucleus.const import SystemEvent


@pytest.fixture
def cfg():
    _saved = getattr(config, "ENABLE_M491_DEADLOCK_DETECT", None)
    dl.reset_for_tests()
    yield config
    if _saved is None:
        if hasattr(config, "ENABLE_M491_DEADLOCK_DETECT"):
            delattr(config, "ENABLE_M491_DEADLOCK_DETECT")
    else:
        setattr(config, "ENABLE_M491_DEADLOCK_DETECT", _saved)
    dl.reset_for_tests()


def test_4916_off_is_noop(cfg):
    """默认关：snapshot 空、不记边、不建字典、不 dump。"""
    cfg.ENABLE_M491_DEADLOCK_DETECT = False
    assert dl.on() is False
    assert dl.snapshot() == {}
    assert dl.note_wait_start("organ", 123) is None
    assert dl.note_wait_end("organ", 99.0) is None
    assert dl.note_wait("organ", 123, 99.0) is None
    assert dl.owner_of("organ") is None


def test_4916_on_records_wait_edges(cfg):
    cfg.ENABLE_M491_DEADLOCK_DETECT = True
    # 用「别的线程」作为 owner：等自己的边会被模块刻意跳过（避免自环误报）
    _owner_tid = 987654321
    with dl._LOCK:
        dl._OWNERS["A"] = _owner_tid
    _r = dl.note_wait_start("B", _owner_tid)
    assert _r is not None
    assert _r["ring"] is None
    assert len(dl.snapshot()["waits"]) == 1


def test_4916_cycle_detection(cfg):
    """三元环能被检出；无环返回 None。"""
    cfg.ENABLE_M491_DEADLOCK_DETECT = True
    assert dl._find_ring({1: 2, 2: 3}) is None
    _ring = dl._find_ring({1: 2, 2: 3, 3: 1})
    assert _ring is not None
    assert _ring[0] == _ring[-1]


def test_4916_note_wait_end_clears_edge(cfg):
    cfg.ENABLE_M491_DEADLOCK_DETECT = True
    dl.note_acquired("A")
    dl.note_wait_start("B", 999)
    _r = dl.note_wait_end("B", 5.0)
    assert _r["recorded"] is True
    assert _r["slow_handler"] is False
    assert dl.snapshot()["waits"] == {}


def test_4916_slow_handler_threshold(cfg):
    cfg.ENABLE_M491_DEADLOCK_DETECT = True
    _r = dl.note_wait_end("A", 31000.0)
    assert _r["slow_handler"] is True


def test_4916_alarm_uses_const_event(cfg):
    """告警脉冲走 SystemEvent.ALARM 常量，不写裸事件名。"""
    cfg.ENABLE_M491_DEADLOCK_DETECT = True
    _f = InfoField.__new__(InfoField)
    _seen = {}
    _f.publish = lambda pulse: _seen.update(pulse) or {}
    _f._m491_emit_ring_alarm([1, 2, 1])
    assert _seen["event_type"] == SystemEvent.ALARM
    assert _seen["payload"]["type"] == "deadlock_wait_graph_ring"
    assert _seen["layer"] == "L0"
    assert "ts_ns" in _seen["payload"]


def test_4916_no_restart_symbols(cfg):
    """模块内绝不出现自动重启/重建池语义。"""
    import inspect
    _src = inspect.getsource(dl)
    for _bad in ("_resize_layer_pool", "os._exit", "sys.exit",
                 "dump_traceback_later"):
        assert _bad not in _src
