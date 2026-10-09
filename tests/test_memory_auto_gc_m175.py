# -*- coding: utf-8 -*-
"""175刀1 门控单测：PulseMetricsCollector 内存压力采集链接入 RSS 上限自动回收。

验收判据：
  1) ENABLE_MEMORY_AUTO_GC=True 且 process_rss_mb > MEMORY_AUTO_GC_RSS_MB → gc.collect 触发、
     gc_triggered 递增、gc_last_rss_mb 记录；
  2) ENABLE_MEMORY_AUTO_GC=False → 不触发、gc_triggered=0、gc.collect 不被调用（默认关=零回归）；
  3) 启用但 process_rss_mb 未越阈 → 不触发；
  4) 瞬时峰值留痕字段存在（rss_peak_mb / rss_peak_ts / instant_drop_mb）。

注：配置读取走 sys.modules["config"]（与产品代码一致），测试以 stub 注入，
不依赖真实 config 热路径 import。
"""


def _make_collector(info_field):
    from organs.core.PulseMetricsCollector import PulseMetricsCollector
    _c = PulseMetricsCollector.__new__(PulseMetricsCollector)
    _c.info_field = info_field
    return _c


class _FakeFieldHigh:
    def _m169_memory_pressure(self):
        return {"ok": True, "process_rss_mb": 9000.0, "system_percent": 50.0,
                "breached": True, "reason": ""}


class _FakeFieldLow:
    def _m169_memory_pressure(self):
        return {"ok": True, "process_rss_mb": 1000.0, "system_percent": 30.0,
                "breached": False, "reason": ""}


import sys
import types
from unittest.mock import patch


def test_gc_triggered_when_enabled_and_over():
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_GC=True, MEMORY_AUTO_GC_RSS_MB=1000.0)
    with patch.dict(sys.modules, {"config": _stub}):
        _c = _make_collector(_FakeFieldHigh())
        with patch("gc.collect") as _mock:
            _r = _c._collect_memory_pressure()
    assert _r["ok"] is True
    assert _r["gc_triggered"] == 1, _r
    assert _r["gc_last_rss_mb"] == 9000.0
    _mock.assert_called_once()


def test_no_gc_when_disabled():
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_GC=False, MEMORY_AUTO_GC_RSS_MB=1000.0)
    with patch.dict(sys.modules, {"config": _stub}):
        _c = _make_collector(_FakeFieldHigh())
        with patch("gc.collect") as _mock:
            _r = _c._collect_memory_pressure()
    assert _r["gc_triggered"] == 0
    _mock.assert_not_called()


def test_no_gc_when_under_threshold():
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_GC=True, MEMORY_AUTO_GC_RSS_MB=16384.0)
    with patch.dict(sys.modules, {"config": _stub}):
        _c = _make_collector(_FakeFieldHigh())
        with patch("gc.collect") as _mock:
            _r = _c._collect_memory_pressure()
    assert _r["gc_triggered"] == 0
    _mock.assert_not_called()


def test_peak_fields_present():
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_GC=False, MEMORY_AUTO_GC_RSS_MB=8192.0)
    with patch.dict(sys.modules, {"config": _stub}):
        _c = _make_collector(_FakeFieldHigh())
        _r = _c._collect_memory_pressure()
    assert "rss_peak_mb" in _r
    assert "rss_peak_ts" in _r
    assert "instant_drop_mb" in _r
    assert _r["rss_peak_mb"] == 9000.0


def test_field_none_still_safe():
    _stub = types.SimpleNamespace(ENABLE_MEMORY_AUTO_GC=True, MEMORY_AUTO_GC_RSS_MB=1.0)
    with patch.dict(sys.modules, {"config": _stub}):
        _c = _make_collector(None)
        with patch("gc.collect") as _mock:
            _r = _c._collect_memory_pressure()
    assert _r["ok"] is False
    assert _r["gc_triggered"] == 0
    _mock.assert_not_called()
