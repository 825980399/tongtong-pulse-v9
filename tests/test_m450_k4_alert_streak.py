# -*- coding: utf-8 -*-
"""450 刀4：控制器超时 / 百科 403 告警聚合（连续 3 次才 WARNING）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import alert_dedup as AD


@pytest.fixture(autouse=True)
def _reset():
    AD.reset_all()
    import nucleus.log_state as LS
    LS.reset(None)
    yield
    AD.reset_all()
    LS.reset(None)


# ---------------------------------------------------------------- 连续计数语义
def test_连续三次才放行_第四次起按时间窗():
    _got = []
    for i in range(1, 7):
        _e, _n = AD.should_warn_streak("k", threshold=3, renotify_sec=600.0, now=1000.0 + i)
        _got.append((i, _e))
    assert _got[0] == (1, False), _got
    assert _got[1] == (2, False), _got
    assert _got[2] == (3, True), _got      # 第 3 次才是"真挂了"
    assert all(_e is False for _i, _e in _got[3:]), _got


def test_到重报点再报一次():
    AD.should_warn_streak("k", threshold=3, renotify_sec=600.0, now=1000.0)
    AD.should_warn_streak("k", threshold=3, renotify_sec=600.0, now=1000.0)
    _e, _ = AD.should_warn_streak("k", threshold=3, renotify_sec=600.0, now=1000.0)
    assert _e is True
    _e2, _ = AD.should_warn_streak("k", threshold=3, renotify_sec=600.0, now=1300.0)
    assert _e2 is False, "未到 600s 不应重报"
    _e3, _ = AD.should_warn_streak("k", threshold=3, renotify_sec=600.0, now=1601.0)
    assert _e3 is True, "过了 600s 必须再报（渠道一直挂着还得能看见）"


def test_reset后重新计():
    for _ in range(3):
        AD.should_warn_streak("k2", threshold=3, renotify_sec=600.0, now=1000.0)
    assert AD.streak_of("k2") == 3
    AD.reset_streak("k2")
    assert AD.streak_of("k2") == 0
    _e, _n = AD.should_warn_streak("k2", threshold=3, renotify_sec=600.0, now=2000.0)
    assert _e is False and _n == 1, (_e, _n)


def test_间隔为零表示每次都报():
    """★`getattr(cfg,X,d) or d` 会把 0 吞掉；`cfg_num` 必须把 0 当有效值。"""
    assert AD.cfg_num("M450_CTRL_EXC_RENOTIFY_SEC", 600.0) == 600.0
    _got = [AD.should_warn_streak("k3", threshold=3, renotify_sec=0.0, now=1000.0 + i)[0]
            for i in range(1, 5)]
    assert _got == [False, False, True, True], _got


def test_总开关关掉一律放行(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M450_ALERT_DEDUP", False, raising=False)
    _got = [AD.should_warn_streak("k4", threshold=3, renotify_sec=600.0,
                                  now=1000.0 + i)[0] for i in range(1, 5)]
    assert _got == [True, True, True, True], _got


# ---------------------------------------------------------------- 控制器
def test_超时族识别():

    import organs.motor.PulseController as PC

    assert PC._m450_is_timeout(TimeoutError("x")) is True
    assert PC._m450_is_timeout(TimeoutError("x")) is True
    assert PC._m450_is_timeout(Exception("The read operation timed out")) is True
    assert PC._m450_is_timeout(ValueError("bad value")) is False
    assert PC._m450_is_timeout(KeyError("k")) is False


class _FakeOrgan:
    def __init__(self):
        self.logs = []

    def _log(self, level, msg):
        self.logs.append((str(level), msg))


def test_控制器超时前两次降DEBUG第三次WARNING():
    import organs.motor.PulseController as PC

    _o = _FakeOrgan()
    _e = TimeoutError("The read operation timed out")
    for _ in range(3):
        PC._m450_log_exc(_o, _e, "L999")
    _warn = [m for lv, m in _o.logs if "WARNING" in lv]
    _dbg = [m for lv, m in _o.logs if "DEBUG" in lv]
    assert len(_warn) == 1, _o.logs
    assert len(_dbg) == 2, _o.logs
    assert "连续第 3 次" in _warn[0], _warn


def test_控制器非超时异常立即WARNING():
    """★聚合只对超时族生效：真 bug 不能被藏起来。"""
    import organs.motor.PulseController as PC

    _o = _FakeOrgan()
    PC._m450_log_exc(_o, ValueError("boom"), "L998")
    assert len(_o.logs) == 1, _o.logs
    assert "WARNING" in _o.logs[0][0], _o.logs
    assert "ValueError" in _o.logs[0][1], _o.logs


# ---------------------------------------------------------------- 百科 403
class _MockLogger:
    def __init__(self):
        self.warn = []
        self.dbg = []

    def warning(self, msg):
        self.warn.append(str(msg))

    def debug(self, msg):
        self.dbg.append(str(msg))


def test_百科403连续三次才WARNING且证据不丢(monkeypatch):
    import nucleus.knowledge.WikiQuerier as WQ

    _lg = _MockLogger()
    monkeypatch.setattr(WQ, "_logger", _lg)
    for _ in range(3):
        WQ._m450_note_403("http://baike.baidu.com/item/x", "<html>验证</html>",
                          {"Retry-After": "5"})
    assert len(_lg.warn) == 1, _lg.warn
    assert len(_lg.dbg) == 2, _lg.dbg
    _all = "\n".join(_lg.warn + _lg.dbg)
    assert "403 反爬命中" in _all, _all
    assert "验证" in _all, "响应体证据不能因为降级而丢"
    assert "Retry-After" in _all, _all
    assert "连续第 3 次" in _lg.warn[0], _lg.warn


def test_百科403不同站点分别计数(monkeypatch):
    import nucleus.knowledge.WikiQuerier as WQ

    _lg = _MockLogger()
    monkeypatch.setattr(WQ, "_logger", _lg)
    for _h in ("http://a.com/x", "http://b.com/x", "http://c.com/x"):
        WQ._m450_note_403(_h, "b", {})
    assert _lg.warn == [], "三个不同站点各一次 ⇒ 都不该 WARNING"
    assert len(_lg.dbg) == 3, _lg.dbg
    WQ._m450_note_403("http://a.com/y", "b", {})
    WQ._m450_note_403("http://a.com/z", "b", {})
    assert len(_lg.warn) == 1, _lg.warn


# ---------------------------------------------------------------- 窗口去重（另两类控制器告警）
def test_窗口去重同类消息一条(monkeypatch):
    import organs.motor.PulseController as PC
    monkeypatch.setattr(config, "M450_CTRL_SEMANTIC_RENOTIFY_SEC", 1800.0, raising=False)
    _got = [PC._m450_windowed("ctrl.semantic_zero_overlap",
                              "M450_CTRL_SEMANTIC_RENOTIFY_SEC", 1800.0)[0]
            for _ in range(3)]
    assert _got == [True, False, False], _got


def test_窗口去重键不同互不影响(monkeypatch):
    import organs.motor.PulseController as PC
    monkeypatch.setattr(config, "M450_CTRL_PW_TIMEOUT_RENOTIFY_SEC", 600.0, raising=False)
    _a = PC._m450_windowed("ctrl.playwright_search_timeout",
                           "M450_CTRL_PW_TIMEOUT_RENOTIFY_SEC", 600.0)[0]
    _b = PC._m450_windowed("ctrl.semantic_zero_overlap",
                           "M450_CTRL_PW_TIMEOUT_RENOTIFY_SEC", 600.0)[0]
    assert (_a, _b) == (True, True), (_a, _b)


# ---------------------------------------------------------------- 接线护栏
def test_控制器九处告警都已接线():
    """源码级护栏：9 处「异常已忽略（需关注）」全部换成聚合入口，防被改回去。"""
    _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "organs", "motor", "PulseController.py")
    with open(_p, encoding="utf-8", errors="replace") as _f:
        _src = _f.read()
    assert _src.count("_m450_log_exc(self, e,") == 9, _src.count("_m450_log_exc(self, e,")
    assert '_log(LogLevel.WARNING, f"异常已忽略（需关注）' not in _src


def test_百科403不在调用点直写warning():
    _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "nucleus", "knowledge", "WikiQuerier.py")
    with open(_p, encoding="utf-8", errors="replace") as _f:
        _src = _f.read()
    assert "_m450_note_403(url, _body512, _ev_hdr)" in _src
    assert '_logger.warning(\n                        f"[WikiQuerier] 403 反爬命中' not in _src
