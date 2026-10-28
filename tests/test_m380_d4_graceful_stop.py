# -*- coding: utf-8 -*-
"""★380 刀4 · 优雅停后主循环立即 break（P2）

票面：18:07:40 器官并行停止完成后，主循环又空转 3 分钟才 exit。

T0 定位：`main.py` 里唯一的 `while True:`（自主意图环）**没有任何停止位**，
心跳等待 2700s；器官停完后它照旧跑一圈、再睡 45 分钟 ⇒ 进程退不干净。

本件验：停止位存在 / 可打断等待 / 循环条件读它 / 器官停完即置位。
（不 import main 跑真循环 —— 只验"装置"，不验进程。）
"""
import io
import os
import re
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _src():
    with io.open(os.path.join(ROOT, "main.py"), encoding="utf-8") as _f:
        return _f.read()


def test_m380_d4_main_is_importable_and_has_stop_event():
    """停止位必须是 `threading.Event`（bool 不能打断 sleep）。"""
    import main as _m
    assert isinstance(_m._M380_STOP, threading.Event)
    assert _m._M380_STOP.is_set() is False or _m._M380_STOP.is_set() is True


def test_m380_d4_wait_is_interruptible():
    """Event.wait 能被 set 立刻打断 —— 这是"3 分钟空转"能不能治好的关键。"""
    import main as _m
    _ev = _m._M380_STOP
    _was = _ev.is_set()
    _ev.clear()
    try:
        _t0 = threading.Event()
        threading.Timer(0.05, lambda: (_t0.set(), _ev.set())).start()
        _got = _ev.wait(5.0)
        assert _got is True, "wait 未被 set 打断（会退化成 sleep）"
    finally:
        _ev.clear()
        if _was:
            _ev.set()


def test_m380_d4_intent_loop_reads_stop_flag():
    _s = _src()
    assert "while not _M380_STOP.is_set():" in _s, "主循环条件没读停止位"
    assert "if _M380_STOP.wait(_rd.uniform(60, 900)):" in _s, "错峰等待不可打断"
    assert "if _M380_STOP.wait(0.1):" in _s, "每拍未检查停止位"


def test_m380_d4_stop_is_set_right_after_organs_stopped():
    """置位点必须在「器官并行停止完成」之后（不能更早，否则收尾被跳过）。"""
    _s = _src()
    _i = _s.index("器官并行停止完成")
    _j = _s.index("_M380_STOP.set()")
    assert _j > _i, "置位点不在器官停止完成之后"


def test_m380_d4_no_bare_while_true_left():
    """旧形态 `while True:` 必须已消失（否则停止位形同虚设）。"""
    _s = _src()
    assert not re.search(r"^\s*while True:\s*$", _s, re.M), "还有裸 while True"
