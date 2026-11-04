# -*- coding: utf-8 -*-
"""449 刀1：内存增长告警改「稳态窗 + 阶跃 + 持续」（同数据新旧口径 A/B）。

★为什么不直接造 `RuntimeMetrics` 的 `_history`：它的采样线程会往同一个 deque 里
  持续追加真实 RSS（实测一次 pytest 里 fixture 只有 372 个点、跑起来却读到 672 个），
  用它做断言等于拿生产噪声当基准（铁律 30）。这里改成**把增长率喂进去**，
  只测判定逻辑本身；端到端那两条则把 `get_memory_growth_report` 一起钉住。
"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.runtime_metrics import RuntimeMetrics


class _Fake:
    """只带判定所需三件：growth_report 桩、_history、spike 计时。"""

    def __init__(self, instant, steady, phase="steady", points=None):
        self._history = list(points or [])
        self._report = {"growth_mb_per_min": instant, "phase": phase,
                        "steady_growth_mb_per_min": steady,
                        "steady_window_sec": 1800.0, "window_sec": 300.0,
                        "window_samples": 60, "is_leak_signal": steady >= 50.0,
                        "rss_now": 4700.0, "rss_at_10min": 4700.0,
                        "uptime_sec": 3600.0}

    def get_memory_growth_report(self, window_samples=None, window_sec=None):
        # 瞬时窗(≤300s)给 instant，稳态窗给 steady —— 一个桩同时服务两次取数，
        # 否则两份读数相等，steady_leak 会盖住 spike（第一版就栽在这）。
        _r = dict(self._report)
        _w = float(window_sec or 0.0)
        _r["growth_mb_per_min"] = (self._report["growth_mb_per_min"]
                                   if _w and _w <= 300.0
                                   else self._report.get("steady_growth_mb_per_min", 0.0))
        return _r

    def _m449_growth_verdict(self, now=None):
        return RuntimeMetrics._m449_growth_verdict(self, now=now)

    def _m449_note(self, v):
        return RuntimeMetrics._m449_note(self, v)


def _pts(now, first, last, rss_first, rss_last):
    """造 2 个点：足够算 step_delta（rss_last - rss_first）。"""
    return [{"ts": now - first, "mem_rss_mb": rss_first},
            {"ts": now - last, "mem_rss_mb": rss_last}]


def _wire(tmp_path, monkeypatch, on=True):
    monkeypatch.setattr(config, "M449_MEM_TRACE_PATH",
                        str(tmp_path / "mem_trace.jsonl"), raising=True)
    monkeypatch.setattr(config, "ENABLE_M449_MEM_TRACE", True, raising=True)
    monkeypatch.setattr(config, "ENABLE_M449_MEM_STEADY_ALARM", on, raising=True)


def test_noise_does_not_alarm_immediately(tmp_path, monkeypatch):
    """★核心：瞬时 192MB/min、稳态 0 —— 旧口径当场告警，新口径当场不告警。"""
    _wire(tmp_path, monkeypatch)
    _now = time.time()
    _f = _Fake(192.0, 0.0, points=_pts(_now, 290.0, 10.0, 3850.0, 4700.0))
    _v = _f._m449_growth_verdict(now=_now)
    assert _v["instant"] >= 50.0 and _v["steady"] < 5.0, _v
    assert _v["alarm"] is False, _v
    assert _v["held_sec"] == 0.0, _v


def test_spike_alarms_after_hold(tmp_path, monkeypatch):
    """同样的抖动若真的持续 5 分钟 ⇒ 仍然告警（不把真异常也滤掉）。"""
    _wire(tmp_path, monkeypatch)
    _now = time.time()
    _f = _Fake(192.0, 0.0, points=_pts(_now, 290.0, 10.0, 3850.0, 4700.0))
    _f._m449_growth_verdict(now=_now)
    _v = _f._m449_growth_verdict(now=_now + 301.0)
    assert _v["mode"] == "spike" and _v["alarm"] is True, _v
    assert _v["held_sec"] >= 300.0, _v


def test_spike_below_ratio_does_not_alarm(tmp_path, monkeypatch):
    """瞬时只比稳态高一点点（比值 <2）且稳态未越线 ⇒ 两种模式都不算。

    ★稳态取 40（< 阈值 50），否则它自己就构成 steady_leak，测不到"比值不够"这条。
    """
    _wire(tmp_path, monkeypatch)
    _now = time.time()
    _f = _Fake(70.0, 40.0, points=_pts(_now, 290.0, 10.0, 4700.0, 4700.0))
    _f._m449_growth_verdict(now=_now)
    _v = _f._m449_growth_verdict(now=_now + 400.0)
    assert _v["ratio"] < 2.0, _v
    assert _v["mode"] == "" and _v["alarm"] is False, _v


def test_steady_leak_still_alarms(tmp_path, monkeypatch):
    """★真泄漏：稳态 60MB/min ≥ 阈值 ⇒ 立刻告警（不看比值，比值≈1 也抓）。"""
    _wire(tmp_path, monkeypatch)
    _now = time.time()
    _v = _Fake(60.0, 60.0, points=_pts(_now, 290.0, 10.0, 4000.0, 4700.0)) \
        ._m449_growth_verdict(now=_now)
    assert _v["mode"] == "steady_leak" and _v["alarm"] is True, _v
    assert _v["steady"] >= 50.0, _v


def test_step_is_marked_separately(tmp_path, monkeypatch):
    """+1200MB 的加载阶跃单独标 step，不冒充泄漏。"""
    _wire(tmp_path, monkeypatch)
    _now = time.time()
    _v = _Fake(240.0, 40.0, points=_pts(_now, 290.0, 10.0, 4700.0, 5900.0)) \
        ._m449_growth_verdict(now=_now)
    assert _v["mode"] == "step" and _v["alarm"] is True, _v
    assert _v["step_delta"] >= 1024.0, _v


def test_ramp_phase_suppressed(tmp_path, monkeypatch):
    """启动 10 分钟内的高斜率不是泄漏（427 口径，本批不重复计）。"""
    _wire(tmp_path, monkeypatch)
    _now = time.time()
    _v = _Fake(600.0, 600.0, phase="ramp",
               points=_pts(_now, 290.0, 10.0, 3000.0, 3800.0))._m449_growth_verdict(now=_now)
    assert _v["phase"] == "ramp" and _v["alarm"] is False, _v


def test_switch_off_restores_old_behaviour(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, on=False)
    _v = _Fake(60.0, 60.0)._m449_growth_verdict()
    assert _v["alarm"] is False and _v["mode"] == "", _v


def test_alarm_detailed_no_longer_triggers_on_noise(tmp_path, monkeypatch):
    """端到端：同一条噪声读数，`check_memory_alarm_detailed` 不再报 triggered。"""
    _wire(tmp_path, monkeypatch)
    _m = RuntimeMetrics()
    _m.get_memory_usage = lambda: {"percent": 9.7, "rss_mb": 4770.0}
    def _stub_noise(window_samples=None, window_sec=None, **_kw):
        _w = float(window_sec or 0.0)
        return {"growth_mb_per_min": 192.0 if _w <= 300.0 else 0.0,
                "phase": "steady", "steady_window_sec": 1800.0,
                "steady_growth_mb_per_min": 0.0, "window_sec": 300.0,
                "window_samples": 60, "is_leak_signal": False,
                "rss_now": 4770.0, "rss_at_10min": 4700.0, "uptime_sec": 3600.0}

    _m.get_memory_growth_report = _stub_noise
    _d = _m.check_memory_alarm_detailed(auto_gc=False)
    assert _d["triggered"] is False, _d
    assert "growth" not in (_d["triggers"] or []), _d


def test_alarm_detailed_carries_m449_fields(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _m = RuntimeMetrics()
    _m.get_memory_usage = lambda: {"percent": 9.7, "rss_mb": 4800.0}
    def _stub_leak(window_samples=None, window_sec=None, **_kw):
        return {"growth_mb_per_min": 60.0, "phase": "steady",
                "steady_window_sec": 1800.0, "steady_growth_mb_per_min": 60.0,
                "window_sec": 300.0, "window_samples": 60, "is_leak_signal": True,
                "rss_now": 4800.0, "rss_at_10min": 4000.0, "uptime_sec": 3600.0}

    _m.get_memory_growth_report = _stub_leak
    _d = _m.check_memory_alarm_detailed(auto_gc=False)
    assert _d["triggered"] is True and "growth" in _d["triggers"], _d
    assert _d["m449_mode"] == "steady_leak", _d
    for _k in ("m449_steady_mb_per_min", "m449_instant_mb_per_min",
               "m449_held_sec", "m449_step_delta_mb"):
        assert _k in _d, (_k, _d)


def test_trace_only_on_mode_change(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _f = _Fake(60.0, 60.0)
    _f._m449_note(_f._m449_growth_verdict())
    _f._m449_note(_f._m449_growth_verdict())      # 同模式 ⇒ 不重复写
    _p = tmp_path / "mem_trace.jsonl"
    _rows = [json.loads(x) for x in _p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(_rows) == 1, _rows
    assert _rows[0]["mode"] == "steady_leak", _rows[0]
